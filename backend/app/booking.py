"""Booking and visit lifecycle (PRD 12.7 sync rules, BOOK/APPT modules, BR-16..BR-23)."""
import uuid
from datetime import datetime, timedelta, timezone

from . import capacity, config, db as dbm, rules, sync
from .audit import audit
from .realtime import publish


def _now():
    return datetime.now(timezone.utc)


async def _service(db, service_id):
    return await db.services.find_one({"org_id": config.ORG_ID, "_id": service_id})


async def _store(db, store_id):
    return await db.stores.find_one({"org_id": config.ORG_ID, "_id": store_id})


def _stage_push(lead, stage, note=""):
    hist = lead.get("stage_history") or []
    hist.append({"stage": stage, "at": _now().isoformat(), "note": note})
    return {"stage": stage, "stage_history": hist}


async def book(db, user, person_type: str, person_id: str, store_id: str, service_id: str,
               date: str, start_hour: int, renew: bool = False, extra_hours: int = 0,
               extra_staff: int = 0, employee_ids: list | None = None, src_campaign: str | None = None,
               discount_pct: int = 0, discount_reason: str | None = None,
               rerouted_from: str | None = None, apply_far_benefit: bool = True):
    """Atomic booking (PRD 9.3/BOOK-03): slot reservations first (unique index guards
    double booking), then appointment, then lead/customer sync, event + audit.
    discount_pct is the explicit cross-store overload offer shown on the AI call
    ("15% off"); rerouted_from records the store the customer was moved from."""
    org = await rules.org_doc(db)
    service = await _service(db, service_id)
    store = await _store(db, store_id)
    if not service or not store:
        raise BookingError("VALIDATION_ERROR", "Unknown service or store")
    if service["duration_hours"] + start_hour > 22:
        raise BookingError("VALIDATION_ERROR", "The session would end after closing time (10 PM)")
    max_ahead = rules.rule_value(org, "BOOK_AHEAD_DAYS", 60)
    today = datetime.now().date()
    d = datetime.fromisoformat(date).date()
    if d < today:
        raise BookingError("VALIDATION_ERROR", "Cannot book in the past")
    if (d - today).days > max_ahead:
        raise BookingError("VALIDATION_ERROR", f"Booking window is {max_ahead} days ahead")

    ctx = await capacity.load_day_ctx(db, [store_id], date)
    if not capacity.store_offers(ctx, store_id, service):  # BR-05
        raise BookingError("NOT_OFFERED", f"{store['name']} does not offer {service['name']}")
    assigned = capacity.place(ctx, store_id, service, start_hour)
    if employee_ids:
        free = {e["_id"] for e in capacity.free_employees(ctx, store_id, service, start_hour)}
        if not set(employee_ids) <= free:
            raise BookingError("SLOT_TAKEN", "Chosen employees are not free in that slot")
        assigned = [e for e in ctx["employees"] if e["_id"] in set(employee_ids)]
    if not assigned:
        raise BookingError("SLOT_TAKEN", "That slot is full - please pick another hour or store")
    if await _held_by_other(db, store_id, date, start_hour, user):
        raise BookingError("SLOT_HELD", "Another user is completing that booking right now")

    person, lead, customer = await _resolve_person(db, person_type, person_id)
    dists = await capacity.distances_from(db, store_id)
    km = None
    if lead and lead.get("store_id") and lead["store_id"] != store_id:
        km = dists.get(lead["store_id"])
    elif customer and customer.get("store_id") and customer["store_id"] != store_id:
        km = dists.get(customer["store_id"])
    # with an explicit cross-store offer the discount IS the price story - the
    # distance benefit would double-count it, so it only applies on its own
    price = rules.price_breakup(service, km if apply_far_benefit else None, org, extra_hours, extra_staff)
    if discount_pct:
        disc = int(price["final"] * discount_pct / 100)
        price["final"] -= disc
        price["offer_discount"] = {"pct": discount_pct, "amount": disc,
                                   "reason": discount_reason or "Cross-store overload offer"}

    async with db.tx():
        appt_id = dbm.new_id("appt")
        for e in assigned:
            for h in range(start_hour, start_hour + service["duration_hours"]):
                await db.slot_reservations.insert_one({
                    "_id": dbm.new_id("res"), "org_id": config.ORG_ID,
                    "appointment_id": appt_id, "store_id": store_id, "date": date,
                    "hour": h, "employee_id": e["_id"]})
        created_customer = False
        if person_type == "lead" and lead:
            if not lead.get("customer_id"):
                customer = await _create_customer_from_lead(db, lead, store, service, date, renew)
                created_customer = True
            else:
                customer = await db.customers.find_one({"_id": lead["customer_id"]})
        if renew and customer:
            # a renewal (re)starts the 30-day plan - for lead bookings and for
            # existing customers booking the renewal from Customer 360 alike
            start_d = date
            await db.customers.update_one(
                {"_id": customer["_id"]},
                {"$set": {"plan": {"start": start_d,
                                   "end": (datetime.fromisoformat(start_d) + timedelta(days=rules.rule_value(org, "BR-01", 30))).isoformat()},
                          "store_id": store_id, "last_service_id": service_id}})
        appointment = {
            "_id": appt_id, "org_id": config.ORG_ID,
            "person_type": person_type, "person_id": person_id,
            "customer_id": customer["_id"] if customer else None,
            "lead_id": lead["_id"] if lead else None,
            "person": {"name": person["name"], "phone": person.get("phone_display") or person.get("phone")},
            "store_id": store_id, "service_id": service_id,
            "service_name": service["name"], "store_name": store["name"],
            "employee_ids": [e["_id"] for e in assigned],
            "employee_names": [e["name"] for e in assigned],
            "date": date, "start_hour": start_hour, "duration": service["duration_hours"],
            "renew": renew, "price": price, "status": "booked",
            "rerouted_from": rerouted_from,
            "status_history": [{"status": "booked", "at": _now().isoformat(), "by": user["name"]}],
            "source_campaign_id": src_campaign, "created_customer": created_customer,
            "created_at": _now().isoformat(), "created_by": user["name"],
        }
        await db.appointments.insert_one(appointment)

        if lead:
            await db.leads.update_one({"_id": lead["_id"]}, {
                "$set": dict(_stage_push(lead, "booked", f"Booked at {store['name']}"),
                             customer_id=customer["_id"] if customer else None,
                             offered_at=lead.get("offered_at"))})
            await db.leads.update_one({"_id": lead["_id"]},
                                      {"$set": {"last_contact_at": _now().isoformat()}})
        await _release_hold(db, store_id, date, start_hour, user)
        await _consume_waitlist_offers(db, person, store_id, service_id, date, start_hour)
    await audit(db, user, "booking.create", "appointment", None,
                {"id": appt_id, "store": store["name"], "service": service["name"], "date": date,
                 "hour": start_hour, "final": price["final"]})
    await publish(db, "appointment.booked",
                  {"appointment_id": appt_id, "person": person["name"], "service": service["name"],
                   "store": store["name"], "date": date, "hour": start_hour,
                   "price_final": price["final"], "store_id": store_id,
                   "rerouted": bool(rerouted_from), "discount_pct": discount_pct})
    await sync.verify(db, reason=f"booking created at {store['name']}")
    return await db.appointments.find_one({"_id": appt_id})


async def _resolve_person(db, person_type, person_id):
    lead = customer = None
    if person_type == "lead":
        lead = await db.leads.find_one({"_id": person_id})
        if not lead:
            raise BookingError("VALIDATION_ERROR", "Lead not found")
        person = {"_id": lead["_id"], "name": lead["name"],
                  "phone_display": lead.get("phone_display"),
                  "phone_norm": lead["phone_norm"], "source": lead.get("source")}
    else:
        customer = await db.customers.find_one({"_id": person_id})
        if not customer:
            raise BookingError("VALIDATION_ERROR", "Customer not found")
        person = {"_id": customer["_id"], "name": customer["name"],
                  "phone_display": customer.get("phone_display"),
                  "phone_norm": customer["phone_norm"], "source": customer.get("source")}
    return person, lead, customer


async def _create_customer_from_lead(db, lead, store, service, date, renew):
    org = await rules.org_doc(db)
    plan = None
    if renew or True:  # every new customer joins a 30-day plan (BR-01)
        plan = {"start": date,
                "end": (datetime.fromisoformat(date) + timedelta(days=rules.rule_value(org, "BR-01", 30))).isoformat()}
    cid = dbm.new_id("cus")
    await db.customers.insert_one({
        "_id": cid, "org_id": config.ORG_ID, "name": lead["name"],
        "phone_norm": lead["phone_norm"], "phone_display": lead.get("phone_display"),
        "store_id": store["_id"], "last_service_id": service["_id"],
        "visits": 0, "spend": 0, "last_visit_at": None,
        "plan": plan, "segment": "new", "consent": lead.get("consent") or {},
        "lead_id": lead["_id"], "source": lead.get("source"), "tags": [],
        "no_shows": 0, "created_at": _now().isoformat()})
    await db.identities.update_one(
        {"org_id": config.ORG_ID, "phone_norm": lead["phone_norm"]},
        {"$set": {"customer_id": cid}}, upsert=True)
    return await db.customers.find_one({"_id": cid})


async def _consume_waitlist_offers(db, person, store_id, service_id, date, start_hour):
    """A direct booking closes any open waitlist entries of the same person."""
    pid = person.get("_id")
    if pid:
        await db.waitlist.update_many(
            {"person_id": pid, "status": {"$in": ["waiting", "offered"]}},
            {"$set": {"status": "booked"}})


async def _held_by_other(db, store_id, date, hour, user) -> bool:
    for h in await db.slot_holds.find({"store_id": store_id, "date": date, "hour": hour}):
        if h["user_id"] != user["_id"] and h["expires_at"] > _now().isoformat():
            return True
    return False


async def _release_hold(db, store_id, date, hour, user):
    await db.slot_holds.delete_many({"store_id": store_id, "date": date,
                                     "hour": {"$gte": hour, "$lt": hour + 1}, "user_id": user["_id"]})


async def hold(db, user, store_id, date, start_hour, duration):
    exp = (_now() + timedelta(minutes=5)).isoformat()  # BOOK-09
    for h in range(start_hour, start_hour + duration):
        await db.slot_holds.delete_many({"store_id": store_id, "date": date, "hour": h, "user_id": user["_id"]})
        await db.slot_holds.insert_one({"_id": dbm.new_id("hold"), "org_id": config.ORG_ID,
                                        "store_id": store_id, "date": date, "hour": h,
                                        "user_id": user["_id"], "user_name": user["name"],
                                        "expires_at": exp})
    return exp


async def cancel(db, user, appt_id: str, reason: str = ""):
    appt = await db.appointments.find_one({"_id": appt_id})
    if not appt:
        raise BookingError("NOT_FOUND", "Appointment not found")
    if appt["status"] != "booked":
        raise BookingError("VALIDATION_ERROR", f"Cannot cancel an appointment that is {appt['status']}")
    async with db.tx():
        await db.slot_reservations.delete_many({"appointment_id": appt_id})
        hist = appt.get("status_history") or []
        hist.append({"status": "cancelled", "at": _now().isoformat(), "by": user["name"], "reason": reason})
        await db.appointments.update_one({"_id": appt_id}, {"$set": {"status": "cancelled", "status_history": hist}})
        lead = await db.leads.find_one({"_id": appt.get("lead_id")}) if appt.get("lead_id") else None
        if lead and lead["stage"] == "booked":  # BR-17: booking reversed
            await db.leads.update_one({"_id": lead["_id"]}, {
                "$set": dict(_stage_push(lead, "offered", "Booking cancelled"),
                             follow_up_at=(datetime.now() + timedelta(days=1)).isoformat())})
        if appt.get("created_customer") and appt.get("customer_id"):
            cus = await db.customers.find_one({"_id": appt["customer_id"]})
            if cus and (cus.get("visits") or 0) == 0:
                await db.customers.delete_one({"_id": cus["_id"]})
                await db.identities.update_one(
                    {"org_id": config.ORG_ID, "phone_norm": cus["phone_norm"]},
                    {"$unset": {"customer_id": ""}})
        await db.payments.update_many({"appointment_id": appt_id, "status": "pending"},
                                      {"$set": {"status": "cancelled"}})
    await audit(db, user, "appointment.cancel", "appointment", {"status": "booked"}, {"status": "cancelled"})
    await publish(db, "appointment.cancelled", {"appointment_id": appt_id, "store_id": appt["store_id"]})
    await waitlist_offer_for_slot(db, appt["store_id"], appt["service_id"], appt["date"], appt["start_hour"])
    await sync.verify(db, reason="booking cancelled")
    return await db.appointments.find_one({"_id": appt_id})


async def complete(db, user, appt_id: str):
    appt = await db.appointments.find_one({"_id": appt_id})
    if not appt or appt["status"] != "booked":
        raise BookingError("VALIDATION_ERROR", "Only a booked appointment can be completed")
    async with db.tx():
        hist = appt.get("status_history") or []
        hist.append({"status": "completed", "at": _now().isoformat(), "by": user["name"]})
        await db.appointments.update_one({"_id": appt_id},
                                         {"$set": {"status": "completed", "status_history": hist,
                                                   "completed_at": _now().isoformat()}})
        await db.slot_reservations.delete_many({"appointment_id": appt_id})
        cus = await db.customers.find_one({"_id": appt.get("customer_id")}) if appt.get("customer_id") else None
        if cus:
            await db.customers.update_one({"_id": cus["_id"]}, {
                "$inc": {"visits": 1, "spend": appt["price"]["final"]},
                "$set": {"last_visit_at": _now().isoformat(), "last_service_id": appt["service_id"]}})
        await db.payments.update_many({"appointment_id": appt_id, "status": "pending"},
                                      {"$set": {"status": "paid", "paid_at": _now().isoformat()}})  # BR-24
        if not await db.payments.find_one({"appointment_id": appt_id}):
            await db.payments.insert_one({  # every completed visit carries its payment record
                "_id": dbm.new_id("pay"), "org_id": config.ORG_ID, "appointment_id": appt_id,
                "customer_id": appt.get("customer_id"), "store_id": appt["store_id"],
                "amount": appt["price"]["final"], "method": "upi", "status": "paid",
                "provider_ref": f"pay_{uuid.uuid4().int % 10**10}",
                "created_at": _now().isoformat(), "paid_at": _now().isoformat()})
    await audit(db, user, "appointment.complete", "appointment", {"status": "booked"}, {"status": "completed"})
    await publish(db, "visit.completed", {"appointment_id": appt_id, "person": appt["person"]["name"],
                                          "service": appt["service_name"], "store": appt["store_name"],
                                          "store_id": appt["store_id"], "revenue": appt["price"]["final"]})
    await sync.verify(db, reason=f"visit completed at {appt['store_name']}")
    return await db.appointments.find_one({"_id": appt_id})


async def no_show(db, user, appt_id: str):
    appt = await db.appointments.find_one({"_id": appt_id})
    if not appt or appt["status"] != "booked":
        raise BookingError("VALIDATION_ERROR", "Only a booked appointment can be marked a no-show")
    async with db.tx():
        await db.slot_reservations.delete_many({"appointment_id": appt_id})
        hist = appt.get("status_history") or []
        hist.append({"status": "no_show", "at": _now().isoformat(), "by": user["name"]})
        await db.appointments.update_one({"_id": appt_id}, {"$set": {"status": "no_show", "status_history": hist}})
        if appt.get("customer_id"):
            await db.customers.update_one({"_id": appt["customer_id"]}, {"$inc": {"no_shows": 1}})  # APPT-05
        if appt.get("lead_id"):
            await db.leads.update_one({"_id": appt["lead_id"]}, {
                "$set": {"follow_up_at": (datetime.now() + timedelta(days=1)).isoformat()}})
    await audit(db, user, "appointment.no_show", "appointment", {"status": "booked"}, {"status": "no_show"})
    await publish(db, "appointment.no_show", {"appointment_id": appt_id, "store_id": appt["store_id"]})
    await sync.verify(db, reason="no-show marked")
    return await db.appointments.find_one({"_id": appt_id})


async def reschedule(db, user, appt_id: str, date: str, start_hour: int, store_id: str | None = None):
    appt = await db.appointments.find_one({"_id": appt_id})
    if not appt or appt["status"] != "booked":
        raise BookingError("VALIDATION_ERROR", "Only a booked appointment can be rescheduled")
    service = await _service(db, appt["service_id"])
    target_store = store_id or appt["store_id"]
    ctx = await capacity.load_day_ctx(db, [target_store], date)
    assigned = capacity.place(ctx, target_store, service, start_hour)
    if not assigned:
        raise BookingError("SLOT_TAKEN", "The new slot is not free - pick another hour")
    org = await rules.org_doc(db)
    dists = await capacity.distances_from(db, target_store)
    km = dists.get(appt["store_id"]) if target_store != appt["store_id"] else None
    price = rules.price_breakup(service, km, org)
    async with db.tx():
        await db.slot_reservations.delete_many({"appointment_id": appt_id})
        for e in assigned:
            for h in range(start_hour, start_hour + service["duration_hours"]):
                await db.slot_reservations.insert_one({
                    "_id": dbm.new_id("res"), "org_id": config.ORG_ID,
                    "appointment_id": appt_id, "store_id": target_store, "date": date,
                    "hour": h, "employee_id": e["_id"]})
        hist = appt.get("status_history") or []
        hist.append({"status": "rescheduled", "at": _now().isoformat(), "by": user["name"],
                     "to": f"{date} {start_hour}:00"})
        await db.appointments.update_one({"_id": appt_id}, {
            "$set": {"date": date, "start_hour": start_hour, "store_id": target_store,
                     "store_name": (await _store(db, target_store))["name"],
                     "employee_ids": [e["_id"] for e in assigned],
                     "employee_names": [e["name"] for e in assigned],
                     "price": price, "status_history": hist}})
    await audit(db, user, "appointment.reschedule", "appointment",
                {"date": appt["date"], "hour": appt["start_hour"]}, {"date": date, "hour": start_hour})
    await publish(db, "appointment.rescheduled", {"appointment_id": appt_id, "store_id": target_store})
    await waitlist_offer_for_slot(db, appt["store_id"], appt["service_id"], appt["date"], appt["start_hour"])
    await sync.verify(db, reason="appointment rescheduled")
    return await db.appointments.find_one({"_id": appt_id})


# ---------------------------------------------------------------------- waitlist
async def waitlist_offer_for_slot(db, store_id, service_id, date, start_hour):
    """BOOK-07: offer the freed slot to the first eligible entry (renewals first,
    then high-value services, then oldest), held for the hold time (BR-08)."""
    entries = await db.waitlist.find({"store_id": store_id, "service_id": service_id,
                                      "status": "waiting"})
    if not entries:
        return None
    org = await rules.org_doc(db)
    service = await _service(db, service_id)
    hold_min = rules.rule_value(org, "BR-08", 30)

    def eligible(e):
        for w in e.get("windows", []):
            if w.get("date") == date and w.get("start_hour") == start_hour:
                return True
        return False

    cands = [e for e in entries if eligible(e)]
    if not cands:
        # no exact window match: still offer to oldest waiting entry for store+service
        cands = entries
    def rank(e):
        renewal = 0 if e.get("renew") else 1
        return (renewal, -((service or {}).get("base_price", 0)), e.get("created_at", ""))
    cands.sort(key=rank)
    chosen = cands[0]
    offer = {"date": date, "start_hour": start_hour,
             "expires_at": (_now() + timedelta(minutes=hold_min)).isoformat()}
    await db.waitlist.update_one({"_id": chosen["_id"]}, {"$set": {"status": "offered", "offer": offer}})
    await publish(db, "waitlist.offered", {"waitlist_id": chosen["_id"], "store_id": store_id,
                                           "date": date, "hour": start_hour})
    return chosen


async def expire_offers(db):
    now = _now().isoformat()
    await db.waitlist.update_many({"status": "offered", "offer.expires_at": {"$lt": now}},
                                  {"$set": {"status": "waiting", "offer": None}})


class BookingError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)
