"""Action queue (QUEUE-01..02), lead response SLA tracking (SLA-01..03) and
mobile quick actions (MOB-01..03).

- /queue/today: one prioritized list of overdue follow-ups, new leads at risk,
  today's upcoming appointments and renewals, each with one-click actions.
- /queue/action: executes a one-click action (log call, move stage, snooze,
  confirm appointment, cancel) with audit + realtime for every change.
- /sla/report: response-time targets from BR-16, leads at risk of going cold,
  store-to-store comparison.
- /quick/today: role-aware payload for the phone - my leads, my follow-ups,
  today's bookings and (staff) their own schedule.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import booking as booking_svc, config, db as dbm, priority, rules
from ..audit import audit
from ..deps import current_user, err, require_section, store_scope
from ..db import Database, get_db
from ..realtime import publish

router = APIRouter(prefix="/api/v1")


def _now():
    return datetime.now(timezone.utc)


class QueueActionIn(BaseModel):
    kind: str          # lead | appointment | renewal
    ref_id: str
    action: str        # log_call | mark_interested | snooze | complete | reschedule | mark_lost | confirm | cancel | offer
    note: str | None = None
    follow_up_at: str | None = None   # for reschedule
    lost_reason: str | None = None    # for mark_lost


async def _scoped_query(flt) -> dict:
    q = {"org_id": config.ORG_ID, "archived": {"$ne": True}}
    if flt["store_ids"]:
        q["store_id"] = {"$in": flt["store_ids"]}
    if flt.get("store_id"):
        q["store_id"] = flt["store_id"]
    return q


async def _store_names(db):
    return {s["_id"]: s["name"] for s in await db.stores.find({"org_id": config.ORG_ID})}


async def _log_call(db, user, person: dict, person_kind: str, content: str):
    """Shared one-click 'log call' for leads and customers."""
    await db.interactions.insert_one({
        "_id": dbm.new_id("int"), "org_id": config.ORG_ID, "person_id": person["_id"],
        "person_name": person["name"], "channel": "call", "direction": "out",
        "content": content, "outcome": "call_made",
        "user_id": user["_id"], "user_name": user["name"], "at": _now().isoformat()})


@router.get("/queue/today")
async def queue_today(flt: dict = Depends(require_section("queue")), db: Database = Depends(get_db)):
    now = _now()
    q = await _scoped_query(flt)
    stores = await _store_names(db)
    org = await rules.org_doc(db)
    items = []

    leads = await db.leads.find(q)
    open_leads = [l for l in leads if l.get("stage") not in ("booked", "lost")]
    prio = await priority.compute_priority_many(db, open_leads)

    # 1) overdue follow-ups (highest priority: a promise already broken)
    for l in open_leads:
        fu = l.get("follow_up_at")
        if not fu:
            continue
        fu_dt = rules.parse_dt(fu)
        if fu_dt and fu_dt < now:
            hours = round((now - fu_dt).total_seconds() / 3600, 1)
            items.append({
                "id": f"lead:{l['_id']}", "kind": "followup", "priority": 1,
                "tier": prio.get(l["_id"], {}).get("tier"),
                "next_step": "Call the lead, log the outcome, then set the next follow-up",
                "title": f"Follow-up overdue: {l['name']}",
                "subtitle": f"{l.get('service_name') or ''} · {stores.get(l.get('store_id'), '-')} · "
                            f"{hours:.0f}h overdue · score {l.get('score', 0)}",
                "store_id": l.get("store_id"), "store_name": stores.get(l.get("store_id")),
                "at": fu, "ref_id": l["_id"], "person_name": l["name"],
                "phone": l.get("phone_display"), "stage": l.get("stage"),
                "score": l.get("score", 0), "hours_overdue": hours,
                "actions": ["log_call", "mark_interested", "complete", "reschedule", "snooze"]})

    # 2) new leads at risk of going cold (SLA breached, or hot and untouched)
    for l in open_leads:
        if l.get("stage") != "new":
            continue
        created = rules.parse_dt(l.get("created_at"))
        if not created:
            continue
        sla = _sla_for(org, l.get("source", "others"))
        deadline = created + timedelta(minutes=sla)
        waited = (now - created).total_seconds() / 60
        if now > deadline:
            items.append({
                "id": f"lead:{l['_id']}", "kind": "sla_breach", "priority": 1,
                "tier": prio.get(l["_id"], {}).get("tier"),
                "next_step": f"First contact is overdue - call {l['name']} now and log the call",
                "title": f"SLA breached: {l['name']} never contacted",
                "subtitle": f"{config.SOURCE_LABELS.get(l.get('source'), l.get('source'))} · "
                            f"{stores.get(l.get('store_id'), '-')} · {waited:.0f} min waited "
                            f"(target {sla} min) · score {l.get('score', 0)}",
                "store_id": l.get("store_id"), "store_name": stores.get(l.get("store_id")),
                "at": l.get("created_at"), "ref_id": l["_id"], "person_name": l["name"],
                "phone": l.get("phone_display"), "stage": l.get("stage"),
                "score": l.get("score", 0), "waited_min": round(waited), "sla_min": sla,
                "actions": ["log_call", "mark_interested", "complete", "reschedule", "snooze"]})
        elif l.get("score", 0) >= 80 and waited >= sla:
            items.append({
                "id": f"lead:{l['_id']}", "kind": "hot_waiting", "priority": 2,
                "tier": prio.get(l["_id"], {}).get("tier"),
                "next_step": "Call while the intent is fresh - offer a slot today",
                "title": f"Hot lead waiting: {l['name']} (score {l.get('score', 0)})",
                "subtitle": f"{config.SOURCE_LABELS.get(l.get('source'), l.get('source'))} · "
                            f"{stores.get(l.get('store_id'), '-')} · {waited:.0f} min since capture",
                "store_id": l.get("store_id"), "store_name": stores.get(l.get("store_id")),
                "at": l.get("created_at"), "ref_id": l["_id"], "person_name": l["name"],
                "phone": l.get("phone_display"), "stage": l.get("stage"),
                "score": l.get("score", 0), "waited_min": round(waited),
                "actions": ["log_call", "mark_interested", "complete", "reschedule", "snooze"]})

    # 3) leads ready to book: the conversation is warm (interested) or a slot was
    #    offered and nobody confirmed yet. Scheduled-for-later follow-ups stay out.
    for l in open_leads:
        if l.get("stage") == "offered":
            pass
        elif l.get("stage") == "interested":
            fu = rules.parse_dt(l.get("follow_up_at"))
            if fu and fu > now:
                continue  # scheduled for later - not queue-worthy today
        else:
            continue
        items.append({
            "id": f"lead:{l['_id']}", "kind": "ready_to_book", "priority": 2,
            "tier": prio.get(l["_id"], {}).get("tier"),
            "next_step": "Offer a slot and book it - open the booking picker from this item",
            "title": f"Ready to book: {l['name']} - {l.get('service_name') or 'service'}",
            "subtitle": f"{config.SOURCE_LABELS.get(l.get('source'), l.get('source'))} · "
                        f"{stores.get(l.get('store_id'), '-')} · score {l.get('score', 0)}"
                        + (" · appointment offered" if l.get("stage") == "offered" else ""),
            "store_id": l.get("store_id"), "store_name": stores.get(l.get("store_id")),
            "at": l.get("offered_at") or l.get("last_contact_at") or l.get("created_at"),
            "ref_id": l["_id"], "person_name": l["name"], "phone": l.get("phone_display"),
            "stage": l.get("stage"), "score": l.get("score", 0),
            "service_id": l.get("service_id"),
            "actions": ["log_call", "book", "complete", "reschedule", "mark_lost"]})

    # 4) today's upcoming appointments that are not confirmed yet (next 4 hours)
    today = now.strftime("%Y-%m-%d")
    hour_now = now.hour
    appts = await db.appointments.find({**q, "date": today, "status": "booked"})
    appts.sort(key=lambda a: a["start_hour"])
    for a in appts:
        if a["start_hour"] < hour_now or a["start_hour"] > hour_now + 4:
            continue
        starts_in = (a["start_hour"] - hour_now) * 60
        prio_level = 1 if starts_in <= 90 else 2
        items.append({
            "id": f"appointment:{a['_id']}", "kind": "appointment", "priority": prio_level,
            "next_step": "Confirm the customer is coming, or rebook if they cannot make it",
            "title": f"{'Starting soon: ' if starts_in <= 90 else ''}{a['person']['name']} - {a['service_name']} "
                     f"at {fmt_hour(a['start_hour'])}",
            "subtitle": f"{a.get('store_name', stores.get(a['store_id'], '-'))} · "
                        f"{', '.join(a.get('employee_names') or [])} · "
                        f"{'unconfirmed' if not a.get('confirmed_at') else 'confirmed'}",
            "store_id": a.get("store_id"), "store_name": a.get("store_name") or stores.get(a["store_id"]),
            "at": f"{today}T{a['start_hour']:02d}:00:00", "ref_id": a["_id"],
            "person_name": a["person"]["name"], "phone": a["person"].get("phone"),
            "start_hour": a["start_hour"], "confirmed": bool(a.get("confirmed_at")),
            "actions": ["confirm", "cancel"]})

    # 4) renewals: BR-10 reminders start on day 25 (5 days before end); win-back for
    #    plans expired in the last 21 days
    customers = await db.customers.find(q)
    for c in customers:
        plan = c.get("plan") or {}
        end = rules.parse_dt(plan.get("end"))
        if not end:
            continue
        days = (end - now).days
        if days > 5 or days < -21:
            continue
        if days >= 0:
            title = f"Renewal due in {days} days: {c['name']}"
        else:
            title = f"Plan expired {-days}d ago: {c['name']}"
        items.append({
            "id": f"renewal:{c['_id']}", "kind": "renewal", "priority": 3,
            "next_step": "Call, offer the renewal and hold a preferred slot if they say yes",
            "title": title,
            "subtitle": f"{rules.fmt_money(c.get('spend', 0))} lifetime · {c.get('visits', 0)} visits · "
                        f"{stores.get(c.get('store_id'), '-')}",
            "store_id": c.get("store_id"), "store_name": stores.get(c.get("store_id")),
            "at": plan.get("end"), "ref_id": c["_id"], "person_name": c["name"],
            "phone": c.get("phone_display"), "plan_end": plan.get("end"), "days": days,
            "actions": ["log_call", "offer", "snooze"]})

    priority_sort = {i["id"]: (i["priority"], i.get("at") or "") for i in items}
    items.sort(key=lambda x: (x["priority"], x.get("at") or ""))
    counts = {}
    for i in items:
        counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    return {"items": items[:80], "counts": counts, "total": len(items)}


def _sla_for(org: dict, source: str) -> int:
    sla = rules.rule_value(org, "BR-16", {})
    if isinstance(sla, dict):
        return int(sla.get(source, 15))
    return 15


def fmt_hour(h: int) -> str:
    hh = h % 12 or 12
    return f"{hh}:00 {'AM' if h < 12 else 'PM'}"


@router.post("/queue/action")
async def queue_action(body: QueueActionIn, db: Database = Depends(get_db),
                       flt: dict = Depends(require_section("queue")),
                       user: dict = Depends(current_user)):
    now = _now()
    kind, action = body.kind, body.action
    if kind == "lead":
        lead = await db.leads.find_one({"_id": body.ref_id})
        if not lead:
            raise err("NOT_FOUND", "Lead not found", 404)
        if action == "log_call":
            await _log_call(db, user, lead, "lead", "Call logged from action queue")
            updates = {"last_contact_at": now.isoformat(), "follow_up_at": None}
            if lead.get("stage") == "new":
                hist = list(lead.get("stage_history") or [])
                hist.append({"stage": "contacted", "at": now.isoformat(), "note": "Contacted via action queue"})
                updates.update({"stage": "contacted", "stage_history": hist})
            await db.leads.update_one({"_id": lead["_id"]}, {"$set": updates})
            await audit(db, user, "lead.contact", "lead", {"stage": lead["stage"]},
                        {"stage": updates.get("stage", lead["stage"]), "channel": "call"}, entity_id=lead["_id"])
            await publish(db, "interaction.logged", {"lead_id": lead["_id"], "channel": "call",
                                                     "person": lead["name"]}, store_id=lead.get("store_id"))
            return {"ok": True, "message": f"Call logged for {lead['name']} - lead moved to Contacted"}
        if action == "mark_interested":
            if lead.get("stage") not in ("new", "contacted"):
                raise err("VALIDATION_ERROR", f"Lead is already {lead['stage']}")
            hist = list(lead.get("stage_history") or [])
            hist.append({"stage": "interested", "at": now.isoformat(), "note": "Marked from action queue"})
            await db.leads.update_one({"_id": lead["_id"]}, {"$set": {"stage": "interested", "stage_history": hist}})
            await audit(db, user, "lead.stage", "lead", {"stage": lead["stage"]},
                        {"stage": "interested"}, entity_id=lead["_id"])
            await publish(db, "lead.stage", {"lead_id": lead["_id"], "person": lead["name"],
                                             "stage": "interested"}, store_id=lead.get("store_id"))
            return {"ok": True, "message": f"{lead['name']} moved to Interested"}
        if action == "snooze":
            fu = (now + timedelta(days=1)).isoformat()
            await db.leads.update_one({"_id": lead["_id"]}, {"$set": {"follow_up_at": fu}})
            await audit(db, user, "lead.followup", "lead", {"follow_up_at": lead.get("follow_up_at")},
                        {"follow_up_at": fu, "note": "snoozed from action queue"}, entity_id=lead["_id"])
            return {"ok": True, "message": f"Follow-up for {lead['name']} snoozed to tomorrow"}
        if action == "reschedule":
            fu = body.follow_up_at
            if not fu or not rules.parse_dt(fu):
                raise err("VALIDATION_ERROR", "Pick a valid follow-up date and time", 422)
            await db.leads.update_one({"_id": lead["_id"]}, {"$set": {"follow_up_at": fu}})
            await audit(db, user, "lead.followup", "lead", {"follow_up_at": lead.get("follow_up_at")},
                        {"follow_up_at": fu, "note": "rescheduled from action queue"}, entity_id=lead["_id"])
            await publish(db, "lead.followup", {"lead_id": lead["_id"], "person": lead["name"],
                                                "follow_up_at": fu}, store_id=lead.get("store_id"))
            return {"ok": True, "message": f"Follow-up for {lead['name']} rescheduled to {fu[:16].replace('T', ' ')}"}
        if action == "mark_lost":
            if lead.get("stage") == "booked":
                raise err("VALIDATION_ERROR", "A booked lead cannot be lost until its appointment is cancelled", 422)
            reason = body.lost_reason or "other"
            if reason not in config.LOST_REASONS:
                raise err("VALIDATION_ERROR", f"Reason must be one of {config.LOST_REASONS}", 422)
            hist = list(lead.get("stage_history") or [])
            hist.append({"stage": "lost", "at": now.isoformat(), "note": f"From action queue: {reason}"})
            await db.leads.update_one({"_id": lead["_id"]},
                                      {"$set": {"stage": "lost", "lost_reason": reason,
                                                "stage_history": hist, "follow_up_at": None}})
            await audit(db, user, "lead.lost", "lead", {"stage": lead.get("stage")},
                        {"stage": "lost", "reason": reason, "source": "action queue"},
                        entity_id=lead["_id"])
            await publish(db, "lead.lost", {"lead_id": lead["_id"], "person": lead["name"],
                                            "reason": reason}, store_id=lead.get("store_id"))
            return {"ok": True, "message": f"{lead['name']} marked lost ({reason})"}
        if action == "complete":
            # the promise was kept: log an outcome to the activity history and clear the reminder
            content = body.note or "Follow-up completed from the action queue"
            await _log_call(db, user, lead, "lead", content)
            hist = list(lead.get("stage_history") or [])
            hist.append({"stage": lead.get("stage"), "at": now.isoformat(),
                         "note": "Follow-up completed"})
            await db.leads.update_one({"_id": lead["_id"]},
                                      {"$set": {"follow_up_at": None, "last_contact_at": now.isoformat()}})
            await audit(db, user, "lead.followup_done", "lead",
                        {"follow_up_at": lead.get("follow_up_at")},
                        {"completed": True, "note": content}, entity_id=lead["_id"])
            await publish(db, "interaction.logged", {"lead_id": lead["_id"], "channel": "call",
                                                     "person": lead["name"]}, store_id=lead.get("store_id"))
            return {"ok": True, "message": f"Follow-up for {lead['name']} marked complete - logged in the lead history"}
        raise err("VALIDATION_ERROR", f"Unknown lead action {action}")

    if kind == "appointment":
        appt = await db.appointments.find_one({"_id": body.ref_id})
        if not appt:
            raise err("NOT_FOUND", "Appointment not found", 404)
        if action == "confirm":
            if appt.get("status") != "booked":
                raise err("VALIDATION_ERROR", f"Appointment is {appt['status']}")
            await db.appointments.update_one({"_id": appt["_id"]},
                                             {"$set": {"confirmed_at": now.isoformat(),
                                                       "confirmed_by": user["name"]}})
            await audit(db, user, "appointment.confirm", "appointment", {"confirmed": False},
                        {"confirmed": True}, entity_id=appt["_id"])
            await publish(db, "appointment.confirmed",
                          {"person": appt["person"]["name"], "hour": appt["start_hour"],
                           "store": appt.get("store_name"), "service": appt.get("service_name")},
                          store_id=appt.get("store_id"))
            return {"ok": True, "message": f"{appt['person']['name']} confirmed for {fmt_hour(appt['start_hour'])}"}
        if action == "cancel":
            try:
                await booking_svc.cancel(db, user, appt["_id"], body.note or "Cancelled from action queue")
            except booking_svc.BookingError as e:
                raise err(e.code, e.message)
            return {"ok": True, "message": f"{appt['person']['name']}'s appointment cancelled"}
        raise err("VALIDATION_ERROR", f"Unknown appointment action {action}")

    if kind == "renewal":
        c = await db.customers.find_one({"_id": body.ref_id})
        if not c:
            raise err("NOT_FOUND", "Customer not found", 404)
        if action == "log_call":
            await _log_call(db, user, c, "customer", "Renewal call logged from action queue")
            await db.customers.update_one({"_id": c["_id"]},
                                          {"$set": {"last_contact_at": now.isoformat()}})
            await audit(db, user, "customer.renewal_call", "customer", None,
                        {"plan_end": (c.get("plan") or {}).get("end")}, entity_id=c["_id"])
            await publish(db, "interaction.logged", {"customer_id": c["_id"], "channel": "call",
                                                     "person": c["name"]}, store_id=c.get("store_id"))
            return {"ok": True, "message": f"Renewal call logged for {c['name']}"}
        if action == "offer":
            if not (c.get("consent") or {}).get("whatsapp"):
                raise err("CONSENT_MISSING", "Customer has not opted in to WhatsApp - log a call instead", 403)
            plan_end = (c.get("plan") or {}).get("end") or "-"
            content = f"Renewal reminder sent - plan ends {plan_end[:10]}"
            await db.interactions.insert_one({
                "_id": dbm.new_id("int"), "org_id": config.ORG_ID, "person_id": c["_id"],
                "person_name": c["name"], "channel": "whatsapp", "direction": "out",
                "content": content, "outcome": "renewal_offer_sent",
                "user_id": user["_id"], "user_name": user["name"], "at": now.isoformat()})
            await db.customers.update_one({"_id": c["_id"]},
                                          {"$set": {"last_contact_at": now.isoformat()}})
            await audit(db, user, "customer.renewal_offer", "customer", None,
                        {"channel": "whatsapp"}, entity_id=c["_id"])
            await publish(db, "interaction.logged", {"customer_id": c["_id"], "channel": "whatsapp",
                                                     "person": c["name"]}, store_id=c.get("store_id"))
            return {"ok": True, "message": f"Renewal reminder queued to {c['name']}"}
        if action == "snooze":
            touch = (now + timedelta(days=1)).isoformat()
            await db.customers.update_one({"_id": c["_id"]}, {"$set": {"next_touch": touch}})
            await audit(db, user, "customer.snooze", "customer", None, {"next_touch": touch},
                        entity_id=c["_id"])
            return {"ok": True, "message": f"{c['name']} snoozed until tomorrow"}
        raise err("VALIDATION_ERROR", f"Unknown renewal action {action}")

    raise err("VALIDATION_ERROR", f"Unknown kind {kind}")


@router.get("/sla/report")
async def sla_report(flt: dict = Depends(require_section("leads")), db: Database = Depends(get_db)):
    """SLA-01/02/03: response-time targets per BR-16, at-risk leads, store comparison."""
    now = _now()
    start, _ = rules.period_range(flt["period"], now)
    q = await _scoped_query(flt)
    leads = await db.leads.find(q)
    if start and flt["period"] != "all":
        leads = [l for l in leads if (l.get("created_at") or "") >= start.isoformat()]
    org = await rules.org_doc(db)
    stores = await _store_names(db)

    lead_ids = [l["_id"] for l in leads]
    first_out: dict[str, str] = {}
    if lead_ids:
        inters = await db.interactions.find({"org_id": config.ORG_ID, "person_id": {"$in": lead_ids},
                                             "direction": "out"})
        for i in inters:
            pid = i["person_id"]
            if pid not in first_out or (i.get("at") or "") < first_out[pid]:
                first_out[pid] = i.get("at")
    responded: dict[str, str] = dict(first_out)
    for l in leads:
        if l["_id"] not in responded and l.get("last_contact_at"):
            responded[l["_id"]] = l["last_contact_at"]

    per_store: dict[str, dict] = {}
    at_risk, responded_min_list = [], []
    open_breaches = 0
    within_sla_count = 0
    for l in leads:
        sla = _sla_for(org, l.get("source", "others"))
        created = rules.parse_dt(l.get("created_at"))
        if not created:
            continue
        resp = responded.get(l["_id"])
        resp_min = round((rules.parse_dt(resp) - created).total_seconds() / 60) if resp else None
        sid = l.get("store_id") or "-"
        d = per_store.setdefault(sid, {"store_id": sid, "store_name": stores.get(sid, "-"),
                                       "total": 0, "responded": 0, "within_sla": 0,
                                       "sum_response": 0, "breached": 0})
        d["total"] += 1
        open_breach = l.get("stage") == "new" and now > created + timedelta(minutes=sla)
        if open_breach:
            d["breached"] += 1
            open_breaches += 1
        if resp_min is not None:
            d["responded"] += 1
            d["sum_response"] += resp_min
            responded_min_list.append(resp_min)
            if resp_min <= sla:
                d["within_sla"] += 1
                within_sla_count += 1
        elif l.get("stage") == "new":
            waited = (now - created).total_seconds() / 60
            if waited >= sla * 0.5:
                at_risk.append({
                    "lead_id": l["_id"], "name": l["name"], "phone": l.get("phone_display"),
                    "store_id": l.get("store_id"), "store_name": stores.get(l.get("store_id"), "-"),
                    "source_label": config.SOURCE_LABELS.get(l.get("source"), l.get("source")),
                    "score": l.get("score", 0), "waited_min": round(waited), "sla_min": sla,
                    "urgency": min(200, round(100 * waited / sla))})
    for d in per_store.values():
        d["avg_response_min"] = round(d["sum_response"] / d["responded"]) if d["responded"] else None
        d["within_sla_pct"] = round(100 * d["within_sla"] / d["responded"]) if d["responded"] else None
        del d["sum_response"]
    at_risk.sort(key=lambda x: -x["urgency"])
    sla_map = rules.rule_value(org, "BR-16", {})
    return {"rule": sla_map,
            "summary": {"avg_response_min": round(sum(responded_min_list) / len(responded_min_list))
                        if responded_min_list else None,
                        "within_sla_pct": round(100 * within_sla_count / len(responded_min_list))
                        if responded_min_list else None,
                        "open_breaches": open_breaches, "at_risk_count": len(at_risk),
                        "leads_in_window": len(leads)},
            "stores": sorted(per_store.values(), key=lambda d: -d["total"]),
            "at_risk": at_risk[:20]}


@router.get("/quick/today")
async def quick_today(date: str | None = None, db: Database = Depends(get_db),
                      flt: dict = Depends(require_section("quick")), user: dict = Depends(current_user)):
    """MOB-01/02/03: role-aware payload for the phone."""
    role = user["roles"][0]
    today = (date or _now().strftime("%Y-%m-%d"))
    stores = await _store_names(db)
    scope = store_scope(user)
    out = {"role": role, "date": today, "user": user["name"], "stores": scope}

    # today's bookings across the user's store scope (staff see their own below)
    aq = {"org_id": config.ORG_ID, "date": today, "status": "booked"}
    if scope:
        aq["store_id"] = {"$in": scope}
    appts = await db.appointments.find(aq, sort=[("start_hour", 1)])
    out["todays_bookings"] = [{"id": a["_id"], "person": a["person"]["name"],
                               "phone": a["person"].get("phone"), "service": a.get("service_name"),
                               "store": a.get("store_name") or stores.get(a["store_id"]),
                               "start_hour": a["start_hour"], "duration": a["duration"],
                               "confirmed": bool(a.get("confirmed_at"))} for a in appts[:40]]
    out["unconfirmed"] = sum(1 for a in out["todays_bookings"] if not a["confirmed"])

    if role == "staff":
        emp = await db.employees.find_one({"user_id": user["_id"]})
        out["employee"] = emp["name"] if emp else None
        out["shift"] = None
        out["my_bookings"] = []
        if emp:
            res = await db.slot_reservations.find({"employee_id": emp["_id"], "date": today})
            ids = {r["appointment_id"] for r in res}
            mine = [a for a in await db.appointments.find({"date": today}) if a["_id"] in ids]
            mine.sort(key=lambda a: a["start_hour"])
            out["my_bookings"] = [{"id": a["_id"], "person": a["person"]["name"],
                                   "phone": a["person"].get("phone"), "service": a.get("service_name"),
                                   "store": a.get("store_name") or stores.get(a["store_id"]),
                                   "start_hour": a["start_hour"], "duration": a["duration"],
                                   "status": a["status"]} for a in mine]
            out["todays_bookings"] = out["my_bookings"]
            out["unconfirmed"] = 0
        shift_doc = await db.shifts.find_one({"employee_id": (emp or {}).get("_id", "-"), "date": today})
        out["shift"] = shift_doc["type"] if shift_doc else None
        out["my_leads"] = []
        out["followups_due"] = []
        return out

    # sales / manager / admin: my leads + follow-ups due
    lq = {"org_id": config.ORG_ID, "owner_id": user["_id"], "stage": {"$nin": ["booked", "lost"]}}
    if scope:
        lq["store_id"] = {"$in": scope}
    my = await db.leads.find(lq)
    services = {s["_id"]: s["name"] for s in await db.services.find({"org_id": config.ORG_ID})}
    now = _now()
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    my_leads, due = [], []
    for l in my:
        item = {"id": l["_id"], "name": l["name"], "phone": l.get("phone_display"),
                "stage": l["stage"], "score": l.get("score", 0),
                "service": services.get(l.get("service_id")),
                "store": stores.get(l.get("store_id")),
                "follow_up_at": l.get("follow_up_at")}
        my_leads.append(item)
        fu = rules.parse_dt(l.get("follow_up_at"))
        if fu and fu <= today_end:
            due.append(item)
    my_leads.sort(key=lambda x: (x["follow_up_at"] or "9999"))
    out["my_leads"] = my_leads[:25]
    out["followups_due"] = due[:15]
    out["counts"] = {"my_leads": len(my_leads), "followups_due": len(due),
                     "bookings_today": len(out["todays_bookings"]), "unconfirmed": out["unconfirmed"]}
    return out
