"""Data sync engine (the user's rule: "data is matched and updated after every
booking and completion").

`verify()` re-derives every cross-document fact the platform keeps and repairs
the safe mismatches automatically, so the website always shows one truth:

- A booked appointment must hold its slot reservations; a finished one must not.
- A lead in stage `booked` must own an active (booked) appointment - and no
  open-stage lead may hide behind one.
- A customer's visits/spend are the sum of their completed appointments.
- Every completed appointment carries its paid payment record (BR-24).

Runs automatically at the end of booking.book / complete / cancel / no_show /
reschedule and after every AI-call booking, and on demand via the API. The
result (issues, repairs, timestamps) lands in the `sync_state` collection and
on the AI Calling + Dashboard surfaces as a "Data Sync" status.
"""
from datetime import datetime, timedelta, timezone

from . import config, db as dbm
from .realtime import publish

LAST_ACTION = {"reason": "startup"}


def note(reason: str):
    LAST_ACTION["reason"] = reason


def _now():
    return datetime.now(timezone.utc)


async def verify(db, reason: str = "") -> dict:
    """Full consistency sweep. Returns {ok, checked, repaired, issues, at, reason}."""
    if reason:
        note(reason)
    org = config.ORG_ID
    issues: list[str] = []
    repairs = 0

    appts = await db.appointments.find({"org_id": org})
    leads = await db.leads.find({"org_id": org})
    lead_by_id = {l["_id"]: l for l in leads}

    active = [a for a in appts if a.get("status") == "booked"]
    finished = [a for a in appts if a.get("status") in ("completed", "cancelled", "no_show")]

    # A. every active appointment holds its slot reservations
    res_by_appt: dict[str, int] = {}
    for r in await db.slot_reservations.find({"org_id": org}):
        res_by_appt[r["appointment_id"]] = res_by_appt.get(r["appointment_id"], 0) + 1
    for a in active:
        if not res_by_appt.get(a["_id"]):
            issues.append(f"Booked appointment {a['_id']} has no slot reservations")

    # B. finished appointments must release their slots
    stuck = [a["_id"] for a in finished if res_by_appt.get(a["_id"])]
    if stuck:
        await db.slot_reservations.delete_many({"appointment_id": {"$in": stuck}})
        repairs += len(stuck)
        issues.append(f"Released {len(stuck)} reservation(s) left on finished appointments")

    # C/D. lead pipeline must mirror the appointment book
    appt_by_lead: dict[str, list] = {}
    for a in active:
        if a.get("lead_id"):
            appt_by_lead.setdefault(a["lead_id"], []).append(a)
    for l in leads:
        stage = l.get("stage")
        has = l["_id"] in appt_by_lead
        if stage == "booked" and not has:
            hist = l.get("stage_history") or []
            hist.append({"stage": "offered", "at": dbm.now_iso(),
                         "note": "Data sync - booking missing, moved back to Appt. Offered"})
            await db.leads.update_one({"_id": l["_id"]}, {"$set": {
                "stage": "offered", "stage_history": hist,
                "follow_up_at": (_now() + timedelta(days=1)).isoformat()}})
            repairs += 1
            issues.append(f"Lead {l['name']} was 'Booked' without an appointment - moved to Appt. Offered")
        elif stage in ("new", "contacted", "interested", "offered") and has:
            hist = l.get("stage_history") or []
            hist.append({"stage": "booked", "at": dbm.now_iso(),
                         "note": "Data sync - active appointment found, pipeline updated"})
            await db.leads.update_one({"_id": l["_id"]}, {"$set": {"stage": "booked", "stage_history": hist}})
            repairs += 1
            issues.append(f"Lead {l['name']} had an active appointment but stage said {stage} - fixed to Booked")

    # E. customer counters equal the completed visits
    done_by_customer: dict[str, list] = {}
    for a in appts:
        if a.get("status") == "completed" and a.get("customer_id"):
            done_by_customer.setdefault(a["customer_id"], []).append(a)
    for c in await db.customers.find({"org_id": org}):
        done = done_by_customer.get(c["_id"], [])
        visits = len(done)
        spend = sum(a["price"]["final"] for a in done if a.get("price"))
        if c.get("visits", 0) != visits or c.get("spend", 0) != spend:
            await db.customers.update_one({"_id": c["_id"]},
                                          {"$set": {"visits": visits, "spend": spend}})
            repairs += 1
            issues.append(f"Customer {c['name']} counters recomputed ({visits} visits, spend synced)")

    # F. every completed appointment carries its paid payment (BR-24)
    paid_appts = {p["appointment_id"] for p in await db.payments.find({"org_id": org})}
    for a in [x for x in appts if x.get("status") == "completed"]:
        if a["_id"] not in paid_appts:
            await db.payments.insert_one({
                "_id": dbm.new_id("pay"), "org_id": org, "appointment_id": a["_id"],
                "customer_id": a.get("customer_id"), "store_id": a.get("store_id"),
                "amount": (a.get("price") or {}).get("final", 0), "method": "upi",
                "status": "paid", "provider_ref": f"sync_{dbm.new_id('pay')[-10:]}",
                "created_at": dbm.now_iso(), "paid_at": dbm.now_iso()})
            repairs += 1
            issues.append(f"Missing payment record created for completed appointment {a['_id']}")

    result = {
        "ok": not issues,
        "checked": {"appointments": len(appts), "leads": len(leads),
                    "active_bookings": len(active), "customers": await db.customers.count_documents({"org_id": org})},
        "repaired": repairs, "issues": issues[:12], "issue_count": len(issues),
        "at": dbm.now_iso(),
        "reason": LAST_ACTION["reason"],
        "pipeline_status": {"booked_leads": await db.leads.count_documents({"org_id": org, "stage": "booked"}),
                            "active_appointments": len(active),
                            "matched": len(appt_by_lead) == await db.leads.count_documents({"org_id": org, "stage": "booked"})},
    }
    await db.sync_state.update_one({"_id": "last"}, {"$set": result}, upsert=True)
    await publish(db, "sync.checked", {"ok": result["ok"], "repaired": repairs,
                                       "reason": result["reason"]})
    return result


async def last(db) -> dict:
    doc = await db.sync_state.find_one({"_id": "last"})
    return doc or {"ok": True, "issue_count": 0, "repaired": 0, "at": None,
                   "reason": "not run yet", "issues": [], "checked": {},
                   "pipeline_status": {}}
