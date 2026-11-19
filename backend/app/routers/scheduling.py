"""Scheduling: availability (BOOK-01/02), booking options + reroute (BOOK-04), earliest
(BOOK-05), appointments lifecycle (APPT-01..APPT-09), calendar, waitlist, slot holds."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import booking as booking_svc, capacity, config, db as dbm, rules
from ..deps import current_user, err, require_section, store_scope
from ..db import Database, DuplicateKeyError, get_db
from ..realtime import publish

router = APIRouter(prefix="/api/v1")


class BookIn(BaseModel):
    person_type: str = "lead"  # lead | customer
    person_id: str
    store_id: str
    service_id: str
    date: str
    start_hour: int
    renew: bool = False
    extra_hours: int = 0
    extra_staff: int = 0
    employee_ids: list | None = None
    src_campaign: str | None = None


class ApptActionIn(BaseModel):
    reason: str | None = None
    date: str | None = None
    start_hour: int | None = None
    store_id: str | None = None


class WaitlistIn(BaseModel):
    person_type: str = "lead"
    person_id: str
    store_id: str
    service_id: str
    windows: list  # [{"date": "...", "start_hour": 17}]
    renew: bool = False


@router.get("/availability")
async def availability(store_id: str, service_id: str, date: str,
                       db: Database = Depends(get_db), user: dict = Depends(current_user)):
    svc = await db.services.find_one({"_id": service_id})
    if not svc:
        raise err("NOT_FOUND", "Service not found", 404)
    grid = await capacity.availability_grid(db, store_id, svc, date)
    if grid.get("error"):
        raise err("NOT_FOUND", "Store not found", 404)
    # BOOK-09: subtract holds from other users
    for h in grid["hours"]:
        for hold in await db.slot_holds.find({"store_id": store_id, "date": date, "hour": h["hour"]}):
            if hold["expires_at"] > dbm.now_iso() and hold["user_id"] != user["_id"]:
                h["status"] = "held"
    return grid


@router.get("/availability/options")
async def availability_options(person_type: str = "lead", person_id: str | None = None,
                               store_id: str | None = None, service_id: str | None = None,
                               date: str | None = None, start_hour: int | None = None,
                               db: Database = Depends(get_db), user: dict = Depends(current_user)):
    """Preferred store first; reroute options with far-location benefit when at/above threshold."""
    org = await rules.org_doc(db)
    if person_id:
        if person_type == "lead":
            p = await db.leads.find_one({"_id": person_id})
        else:
            p = await db.customers.find_one({"_id": person_id})
        if not p:
            raise err("NOT_FOUND", "Person not found", 404)
        store_id = store_id or p.get("store_id")
        service_id = service_id or p.get("service_id") or p.get("last_service_id")
    if not store_id or not service_id:
        raise err("VALIDATION_ERROR", "store_id and service_id are required")
    svc = await db.services.find_one({"_id": service_id})
    if not svc:
        raise err("NOT_FOUND", "Service not found", 404)
    date = date or datetime.now().strftime("%Y-%m-%d")
    start_hour = start_hour if start_hour is not None else max(10, datetime.now().hour)
    opts = await capacity.reroute_options(db, svc, date, start_hour, store_id, org)
    return opts


@router.get("/availability/earliest")
async def earliest(service_id: str, db: Database = Depends(get_db),
                   user: dict = Depends(current_user)):
    svc = await db.services.find_one({"_id": service_id})
    if not svc:
        raise err("NOT_FOUND", "Service not found", 404)
    org = await rules.org_doc(db)
    return {"earliest": await capacity.earliest_available(db, svc, org)}


@router.post("/appointments/hold")
async def hold(body: dict, db: Database = Depends(get_db), user: dict = Depends(current_user)):
    svc = await db.services.find_one({"_id": body.get("service_id")})
    expires = await booking_svc.hold(db, user, body["store_id"], body["date"],
                                     body["start_hour"], (svc or {}).get("duration_hours", 1))
    return {"expires_at": expires}


@router.post("/appointments", status_code=201)
async def book(body: BookIn, db: Database = Depends(get_db),
               flt: dict = Depends(require_section("appointments")), user: dict = Depends(current_user)):
    try:
        appt = await booking_svc.book(db, user, body.person_type, body.person_id, body.store_id,
                                      body.service_id, body.date, body.start_hour, body.renew,
                                      body.extra_hours, body.extra_staff, body.employee_ids,
                                      body.src_campaign)
        if body.src_campaign:
            camp = await db.campaigns.find_one({"_id": body.src_campaign})
            if camp:
                stats = camp.get("stats") or {}
                stats["booked"] = stats.get("booked", 0) + 1
                stats["revenue"] = stats.get("revenue", 0) + appt["price"]["final"]
                await db.campaigns.update_one({"_id": body.src_campaign}, {"$set": {"stats": stats}})
        return appt
    except booking_svc.BookingError as e:
        if e.code == "SLOT_TAKEN":
            svc = await db.services.find_one({"_id": body.service_id})
            grid = await capacity.availability_grid(db, body.store_id, svc, body.date) if svc else {}
            raise HTTPException(409, {"error": {"code": "SLOT_TAKEN", "message": e.message,
                                                "details": {"availability": grid}}})
        raise err(e.code, e.message)
    except DuplicateKeyError:  # unique index guard (PRD 9.3-2)
        raise err("SLOT_TAKEN", "That employee-hour was just taken - refresh availability", 409)


@router.get("/appointments")
async def list_appointments(date: str | None = None, store_id: str | None = None,
                            status: str | None = None, daypart: str | None = None,
                            limit: int = 200, flt: dict = Depends(require_section("appointments")),
                            db: Database = Depends(get_db)):
    date = date or datetime.now().strftime("%Y-%m-%d")
    query = {"org_id": config.ORG_ID, "date": date}
    scope = flt["store_ids"]
    if store_id:
        query["store_id"] = store_id
    elif scope:
        query["store_id"] = {"$in": scope}
    if status:
        query["status"] = status
    appts = await db.appointments.find(query, sort=[("start_hour", 1)])
    if daypart and daypart != "all":
        rng = {"morning": (10, 14), "afternoon": (14, 18), "evening": (18, 22)}.get(daypart)
        if rng:
            appts = [a for a in appts if rng[0] <= a["start_hour"] < rng[1]]
    return {"appointments": appts[:min(limit, 500)], "date": date,
            "today": datetime.now().strftime("%Y-%m-%d")}


@router.get("/appointments/calendar")
async def calendar(date: str | None = None, store_id: str | None = None,
                   status: str | None = None, daypart: str | None = None,
                   flt: dict = Depends(require_section("appointments")),
                   db: Database = Depends(get_db)):
    """APPT-02: employees as rows, 12 hour slots as columns, per store.

    status/daypart mirror the list endpoint so the filters visibly apply to
    the calendar grid too: status picks which appointment statuses appear in
    cells (default booked+completed), daypart narrows the hour columns.
    """
    date = date or datetime.now().strftime("%Y-%m-%d")
    stores = await db.stores.find({"org_id": config.ORG_ID, "status": "active"})
    if flt["store_ids"]:
        stores = [s for s in stores if s["_id"] in flt["store_ids"]]
    if store_id:
        stores = [s for s in stores if s["_id"] == store_id]
    ctx = await capacity.load_day_ctx(db, [s["_id"] for s in stores], date)
    appt_query = {"org_id": config.ORG_ID, "date": date}
    if status:
        appt_query["status"] = status
    else:
        appt_query["status"] = {"$in": ["booked", "completed"]}
    appts = await db.appointments.find(appt_query)
    hours = list(config.SLOT_HOURS)
    if daypart and daypart != "all":
        rng = {"morning": (10, 14), "afternoon": (14, 18), "evening": (18, 22)}.get(daypart)
        if rng:
            hours = [h for h in hours if rng[0] <= h < rng[1]]
    out = []
    for s in stores:
        emps = [e for e in ctx["employees"] if e["store_id"] == s["_id"]]
        rows = []
        for e in emps:
            shift = capacity.effective_shift(e, date, ctx["shift_overrides"])
            on_leave = e["_id"] in ctx["on_leave"]
            cells = []
            for h in hours:
                covered = capacity.shift_covers(shift, h, 1) and not on_leave
                cells.append({"hour": h, "covered": covered, "appointment": None})
            rows.append({"employee_id": e["_id"], "employee_name": e["name"],
                         "role": e.get("role"), "shift": shift, "on_leave": on_leave, "cells": cells})
        emp_idx = {r["employee_id"]: r for r in rows}
        for a in appts:
            if a["store_id"] != s["_id"]:
                continue
            for eid in a["employee_ids"]:
                row = emp_idx.get(eid)
                if not row:
                    continue
                for i, cell in enumerate(row["cells"]):
                    if cell["hour"] in range(a["start_hour"], a["start_hour"] + a["duration"]):
                        if not cell["appointment"] or a["status"] == "booked":
                            cell["appointment"] = {"id": a["_id"], "person": a["person"]["name"],
                                                   "service": a["service_name"],
                                                   "status": a["status"],
                                                   "lead_id": a.get("lead_id"),
                                                   "customer_id": a.get("customer_id"),
                                                   "start_hour": a["start_hour"],
                                                   "duration": a["duration"],
                                                   "price_final": a["price"]["final"]}
        util = await capacity.store_day_utilization(db, s["_id"], date)
        out.append({"store": s, "rows": rows, "utilization": util})
    return {"date": date, "stores": out,
            "today": datetime.now().strftime("%Y-%m-%d"),
            "hours": [{"hour": h, "label": f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"} for h in hours]}


@router.post("/appointments/{appt_id}/complete")
async def complete(appt_id: str, db: Database = Depends(get_db),
                   flt: dict = Depends(require_section("appointments")), user: dict = Depends(current_user)):
    try:
        return await booking_svc.complete(db, user, appt_id)
    except booking_svc.BookingError as e:
        raise err(e.code, e.message)


@router.post("/appointments/{appt_id}/no-show")
async def no_show(appt_id: str, db: Database = Depends(get_db),
                  flt: dict = Depends(require_section("appointments")), user: dict = Depends(current_user)):
    try:
        return await booking_svc.no_show(db, user, appt_id)
    except booking_svc.BookingError as e:
        raise err(e.code, e.message)


@router.post("/appointments/{appt_id}/cancel")
async def cancel(appt_id: str, body: ApptActionIn | None = None,
                 db: Database = Depends(get_db), flt: dict = Depends(require_section("appointments")), user: dict = Depends(current_user)):
    try:
        return await booking_svc.cancel(db, user, appt_id, (body or {}).reason or "")
    except booking_svc.BookingError as e:
        raise err(e.code, e.message)


@router.post("/appointments/{appt_id}/reschedule")
async def reschedule(appt_id: str, body: ApptActionIn, db: Database = Depends(get_db),
                     flt: dict = Depends(require_section("appointments")), user: dict = Depends(current_user)):
    if not body.date or body.start_hour is None:
        raise err("VALIDATION_ERROR", "date and start_hour are required")
    try:
        return await booking_svc.reschedule(db, user, appt_id, body.date, body.start_hour, body.store_id)
    except booking_svc.BookingError as e:
        raise err(e.code, e.message)


@router.get("/appointments/{appt_id}")
async def get_appt(appt_id: str, db: Database = Depends(get_db), user: dict = Depends(current_user)):
    a = await db.appointments.find_one({"_id": appt_id})
    if not a:
        raise err("NOT_FOUND", "Appointment not found", 404)
    payments = await db.payments.find({"appointment_id": appt_id})
    return {**a, "payments": payments}


# ------------------------------------------------------------------ waitlist
@router.get("/waitlist")
async def list_waitlist(store_id: str | None = None, flt: dict = Depends(current_user),
                        db: Database = Depends(get_db)):
    await booking_svc.expire_offers(db)
    query = {"org_id": config.ORG_ID, "status": {"$in": ["waiting", "offered"]}}
    scope = store_scope(flt)
    if store_id:
        query["store_id"] = store_id
    elif scope:
        query["store_id"] = {"$in": scope}
    entries = await db.waitlist.find(query, sort=[("created_at", 1)])
    services = {s["_id"]: s for s in await db.services.find({"org_id": config.ORG_ID})}
    stores = {s["_id"]: s["name"] for s in await db.stores.find({"org_id": config.ORG_ID})}
    out = []
    for w in entries:
        svc = services.get(w.get("service_id"), {})
        out.append({**w, "service_name": svc.get("name"), "store_name": stores.get(w.get("store_id")),
                    "high_value": svc.get("base_price", 0)})
    return {"waitlist": out}


@router.post("/waitlist", status_code=201)
async def add_waitlist(body: WaitlistIn, db: Database = Depends(get_db), user: dict = Depends(current_user)):
    if body.person_type not in ("lead", "customer"):
        raise err("VALIDATION_ERROR", "person_type must be 'lead' or 'customer'", 422)
    person = await (db.leads if body.person_type == "lead" else db.customers).find_one({"_id": body.person_id})
    if not person:
        raise err("NOT_FOUND", "Person not found", 404)
    if not await db.stores.find_one({"org_id": config.ORG_ID, "_id": body.store_id}):
        raise err("VALIDATION_ERROR", "Unknown store", 422)
    if not await db.services.find_one({"org_id": config.ORG_ID, "_id": body.service_id}):
        raise err("VALIDATION_ERROR", "Unknown service", 422)
    doc = {"_id": dbm.new_id("wl"), "org_id": config.ORG_ID,
           "person_type": body.person_type, "person_id": body.person_id,
           "person_name": person["name"], "store_id": body.store_id,
           "service_id": body.service_id, "windows": body.windows,
           "renew": body.renew, "status": "waiting", "offer": None,
           "created_at": dbm.now_iso()}
    await db.waitlist.insert_one(doc)
    return doc


@router.post("/waitlist/{wl_id}/offer")
async def offer(wl_id: str, date: str, start_hour: int, db: Database = Depends(get_db),
                flt: dict = Depends(require_section("appointments")), user: dict = Depends(current_user)):
    org = await rules.org_doc(db)
    hold_min = rules.rule_value(org, "BR-08", 30)
    w = await db.waitlist.find_one({"_id": wl_id})
    if not w:
        raise err("NOT_FOUND", "Waitlist entry not found", 404)
    await db.waitlist.update_one({"_id": wl_id}, {"$set": {
        "status": "offered", "offer": {"date": date, "start_hour": start_hour,
                                       "expires_at": (datetime.now() + timedelta(minutes=hold_min)).isoformat()}}})
    return await db.waitlist.find_one({"_id": wl_id})


@router.post("/waitlist/{wl_id}/accept")
async def accept(wl_id: str, db: Database = Depends(get_db),
                 flt: dict = Depends(require_section("appointments")), user: dict = Depends(current_user)):
    w = await db.waitlist.find_one({"_id": wl_id})
    if not w or w.get("status") != "offered":
        raise err("VALIDATION_ERROR", "No active offer for this waitlist entry")
    try:
        appt = await booking_svc.book(db, user, w["person_type"], w["person_id"], w["store_id"],
                                      w["service_id"], w["offer"]["date"], w["offer"]["start_hour"],
                                      renew=w.get("renew", False))
    except booking_svc.BookingError as e:
        raise err(e.code, e.message)
    await db.waitlist.update_one({"_id": wl_id}, {"$set": {"status": "booked",
                                                           "appointment_id": appt["_id"]}})
    return appt


@router.delete("/waitlist/{wl_id}")
async def remove_waitlist(wl_id: str, db: Database = Depends(get_db), user: dict = Depends(current_user)):
    await db.waitlist.update_one({"_id": wl_id}, {"$set": {"status": "removed"}})
    return {"ok": True}


# ------------------------------------------------------------------ my schedule (staff)
@router.get("/me/schedule")
async def my_schedule(date: str | None = None, db: Database = Depends(get_db),
                      flt: dict = Depends(require_section("myschedule")), user: dict = Depends(current_user)):
    date = date or datetime.now().strftime("%Y-%m-%d")
    emp = await db.employees.find_one({"user_id": user["_id"]})
    if not emp:
        return {"appointments": [], "employee": None, "date": date}
    reservations = await db.slot_reservations.find({"employee_id": emp["_id"], "date": date})
    appt_ids = {r["appointment_id"] for r in reservations}
    appts = [a for a in await db.appointments.find({"date": date}) if a["_id"] in appt_ids]
    appts.sort(key=lambda a: a["start_hour"])
    stores = {s["_id"]: s["name"] for s in await db.stores.find({"org_id": config.ORG_ID})}
    services = {s["_id"]: s["name"] for s in await db.services.find({"org_id": config.ORG_ID})}
    for a in appts:
        a["store_name"] = stores.get(a["store_id"])
        a["service_name"] = services.get(a["service_id"])
    shift_doc = await db.shifts.find_one({"employee_id": emp["_id"], "date": date})
    shift = shift_doc["type"] if shift_doc else capacity.effective_shift(emp, date, {})
    return {"appointments": appts, "employee": emp, "date": date, "shift": shift}
