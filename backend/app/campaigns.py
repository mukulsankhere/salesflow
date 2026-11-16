"""Marketing engine (PRD MKT-01..MKT-10, 12.4): segments, estimates, preview, launch,
consent/quiet-hours/frequency-cap enforcement (BR-12/13/21), automations, templates."""
import asyncio
import random
from datetime import datetime, timedelta, timezone

from . import config, db as dbm, rules
from .audit import audit
from .realtime import publish

QUIET_START, QUIET_END = 9, 21  # BR-12: 09:00-21:00


def ist_hour() -> int:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).hour


def in_quiet_hours() -> bool:
    return QUIET_START <= ist_hour() < QUIET_END


# ------------------------------------------------------------------ lead segments
LEAD_SEGMENT_DEFS = {
    "hot": "Hot Leads (Score 80+)",
    "interested": "Interested, Not Booked",
    "new": "New Leads (Last 7 Days)",
    "stale_offers": "Stale Offers (Offered 14+ Days Ago)",
    "overdue_followups": "Overdue Follow-Ups",
    "by_source": "By Source",
    "by_service": "By Service",
    "lost_by_reason": "Lost By Reason",
}


async def lead_segments(db, period: str, store_ids: list) -> dict:
    start, _ = rules.period_range(period, datetime.now(timezone.utc))
    q = {"org_id": config.ORG_ID, "stage": {"$nin": ["booked"]}}
    if store_ids:
        q["store_id"] = {"$in": store_ids}
    leads = await db.leads.find(q)
    now = datetime.now(timezone.utc)
    out = {}
    def within(l):
        if not start:
            return True
        c = l.get("created_at") or ""
        return bool(c) and c >= start.isoformat()
    scoped = [l for l in leads if within(l) or l.get("stage") in ("interested", "offered")]
    out["hot"] = [l for l in scoped if l.get("score", 0) >= 80]
    out["interested"] = [l for l in scoped if l.get("stage") == "interested"]
    out["new"] = [l for l in scoped if l.get("stage") == "new" and
                  l.get("created_at", "") >= (now - timedelta(days=7)).isoformat()]
    out["stale_offers"] = [l for l in scoped if l.get("stage") == "offered" and l.get("offered_at")
                           and l["offered_at"] < (now - timedelta(days=14)).isoformat()]
    out["overdue_followups"] = [l for l in scoped if l.get("follow_up_at")
                                and l["follow_up_at"] < now.isoformat()
                                and l.get("stage") not in ("booked", "lost")]
    out["by_source"] = scoped
    out["by_service"] = scoped
    out["lost_by_reason"] = [l for l in leads if l.get("stage") == "lost"]
    return out


async def customer_segments(db, store_ids: list) -> dict:
    q = {"org_id": config.ORG_ID}
    if store_ids:
        q["store_id"] = {"$in": store_ids}
    customers = await db.customers.find(q)
    now = datetime.now(timezone.utc)
    out: dict[str, list] = {}
    for c in customers:
        seg = rules.segment_for(c, now)
        out.setdefault(seg, []).append(c)
    return out


async def _price_for(db, service_id: str, offer_pct: int) -> int:
    svc = await db.services.find_one({"_id": service_id})
    base = svc["base_price"] if svc else 0
    return int(base * (100 - offer_pct) / 100)


async def _touch_count(db, person_id: str) -> int:
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    return await db.interactions.count_documents(
        {"person_id": person_id, "direction": "out",
         "channel": {"$in": ["whatsapp", "sms", "call"]},
         "outcome": {"$in": ["campaign", "promotional"]},
         "at": {"$gte": week_ago}})


async def build_audience(db, kind: str, segment_key: str, sub_key: str | None,
                         period: str, store_ids: list, channel: str = "whatsapp") -> dict:
    """BR-21: audience limited to consented people inside the global filter.
    Returns audience docs + exclusion counts by reason (consent, frequency cap)."""
    if kind == "leads":
        segs = await lead_segments(db, period, store_ids)
        pool = segs.get(segment_key, [])
        if segment_key == "by_source" and sub_key:
            pool = [l for l in pool if l["source"] == sub_key]
        if segment_key == "by_service" and sub_key:
            pool = [l for l in pool if l["service_id"] == sub_key]
        if segment_key == "lost_by_reason" and sub_key:
            pool = [l for l in pool if l.get("lost_reason") == sub_key]
    else:
        segs = await customer_segments(db, store_ids)
        pool = segs.get(segment_key, [])
    audience, excluded_consent, excluded_cap = [], 0, 0
    for p in pool:
        consent = (p.get("consent") or {})
        if not consent.get(channel, False):  # BR-12/E4: no promo without recorded consent
            excluded_consent += 1
            continue
        if await _touch_count(db, p["_id"]) >= rules.rule_value(await rules.org_doc(db), "BR-13", 3):
            excluded_cap += 1
            continue
        audience.append(p)
    return {"audience": audience, "excluded_consent": excluded_consent,
            "excluded_frequency_cap": excluded_cap}


async def preview(db, kind, segment_key, sub_key, offer_pct, channel, template_body,
                  period, store_ids) -> dict:
    org = await rules.org_doc(db)
    aud = await build_audience(db, kind, segment_key, sub_key, period, store_ids, channel)
    audience = aud["audience"]
    stores = {s["_id"]: s for s in await db.stores.find({"org_id": config.ORG_ID})}
    services = {s["_id"]: s for s in await db.services.find({"org_id": config.ORG_ID})}
    exp_bookings, exp_revenue = 0.0, 0
    samples = []
    for p in audience:
        if kind == "leads":
            ch = rules.chance_to_book(p.get("stage", "new"), p.get("score", 0), offer_pct)
            price = await _price_for(db, p.get("service_id"), offer_pct)
        else:
            lo, hi = rules.SEGMENT_CONV.get(segment_key, (0.10, 0.18))
            ch = (lo + hi) / 2
            price = await _price_for(db, p.get("last_service_id"), offer_pct)
        exp_bookings += ch
        exp_revenue += ch * price
        svc = services.get(p.get("service_id") or p.get("last_service_id"))
        store = stores.get(p.get("store_id"))
        if len(samples) < 5:
            samples.append({"person": p["name"], "text": rules.render_template(
                template_body, p, svc, store, price_final=price, discount_pct=offer_pct)})
    exp_bookings = round(exp_bookings, 1)
    best_time = "11:00 AM" if ist_hour() < 12 else "7:00 PM"
    return {"audience_size": len(audience),
            "excluded_consent": aud["excluded_consent"],
            "excluded_frequency_cap": aud["excluded_frequency_cap"],
            "expected_bookings": exp_bookings,
            "expected_bookings_range": [round(exp_bookings * 0.8, 1), round(exp_bookings * 1.2, 1)],
            "expected_revenue": int(exp_revenue),
            "expected_revenue_range": [int(exp_revenue * 0.8), int(exp_revenue * 1.2)],
            "samples": samples, "best_send_time": best_time,
            "quiet_hours_ok": in_quiet_hours()}


async def launch(db, user, kind, name, segment_key, sub_key, offer_pct, channel,
                 template_body, schedule_at, period, store_ids):
    org = await rules.org_doc(db)
    aud = await build_audience(db, kind, segment_key, sub_key, period, store_ids, channel)
    status = "scheduled" if (schedule_at and schedule_at > dbm.now_iso()) else "live"
    cid = dbm.new_id("cmp")
    await db.campaigns.insert_one({
        "_id": cid, "org_id": config.ORG_ID, "name": name, "kind": kind,
        "segment_key": segment_key, "sub_key": sub_key,
        "filter_snapshot": {"period": period, "store_ids": store_ids},
        "channel": channel, "template_body": template_body, "offer_pct": offer_pct,
        "schedule": schedule_at, "status": status,
        "stats": {"audience": len(aud["audience"]), "sent": 0, "delivered": 0, "read": 0,
                  "replied": 0, "booked": 0, "revenue": 0},
        "created_by": user["name"], "created_at": dbm.now_iso()})
    for p in aud["audience"]:
        svc = await db.services.find_one({"_id": p.get("service_id") or p.get("last_service_id")})
        store = await db.stores.find_one({"_id": p.get("store_id")})
        price = int((svc["base_price"] if svc else 0) * (100 - offer_pct) / 100)
        text = rules.render_template(template_body, p, svc, store, price_final=price,
                                     discount_pct=offer_pct)
        try:
            await db.campaign_messages.insert_one({
                "_id": dbm.new_id("cmsg"), "campaign_id": cid, "person_id": p["_id"],
                "person_name": p["name"], "person_kind": kind,
                "channel": channel, "rendered_text": text, "status": "queued",
                "created_at": dbm.now_iso()})
        except dbm.DuplicateKeyError:
            continue
    await audit(db, user, "campaign.launch", "campaign", None,
                {"id": cid, "name": name, "audience": len(aud["audience"])})
    await publish(db, "campaign.status", {"campaign_id": cid, "name": name, "status": status})
    if status == "live":
        asyncio.get_event_loop().create_task(dispatch(db, cid))
    return await db.campaigns.find_one({"_id": cid})


async def dispatch(db, campaign_id: str):
    """P3/P4 worker: enqueue through the provider interface, enforce consent and quiet
    hours, record per-message status, advance stages (MKT-06/MKT-08)."""
    camp = await db.campaigns.find_one({"_id": campaign_id})
    if not camp:
        return
    msgs = await db.campaign_messages.find({"campaign_id": campaign_id, "status": "queued"})
    if not msgs:
        return
    quiet_ok = in_quiet_hours()
    sent = replied = 0
    for m in msgs:
        if not quiet_ok:
            break  # stay queued until the window opens (worker retries)
        consent_doc = (await db.leads.find_one({"_id": m["person_id"]}) if m["person_kind"] == "leads"
                       else await db.customers.find_one({"_id": m["person_id"]}))
        if not consent_doc or not (consent_doc.get("consent") or {}).get(camp["channel"], False):
            await db.campaign_messages.update_one({"_id": m["_id"]}, {"$set": {"status": "blocked"}})
            continue
        await db.campaign_messages.update_one(
            {"_id": m["_id"]},
            {"$set": {"status": "sent", "sent_at": dbm.now_iso(),
                      "provider_message_id": f"demo_{random.randint(10**8, 10**9 - 1)}"}})
        sent += 1
        await db.interactions.insert_one({
            "_id": dbm.new_id("int"), "org_id": config.ORG_ID, "person_id": m["person_id"],
            "person_name": m["person_name"], "channel": camp["channel"], "direction": "out",
            "template": camp["name"], "content": m["rendered_text"], "outcome": "campaign",
            "status": "sent", "campaign_id": campaign_id, "at": dbm.now_iso()})
        if m["person_kind"] == "leads":
            lead = consent_doc
            if lead["stage"] == "new":  # MKT-06: new leads move to Contacted + follow-up
                hist = lead.get("stage_history") or []
                hist.append({"stage": "contacted", "at": dbm.now_iso(), "note": f"Campaign {camp['name']}"})
                await db.leads.update_one({"_id": lead["_id"]}, {
                    "$set": {"stage": "contacted", "stage_history": hist,
                             "last_contact_at": dbm.now_iso(),
                             "follow_up_at": (datetime.now() + timedelta(days=1)).isoformat()}})
        # simulated reply for the demo provider (measured reply rate MKT-08)
        if random.random() < 0.2:
            replied += 1
            await db.campaign_messages.update_one(
                {"_id": m["_id"]},
                {"$set": {"status": "replied", "delivered_at": dbm.now_iso(),
                          "replied_at": dbm.now_iso(), "reply_text": random.choice(
                              ["Yes, what times are free?", "Share price please",
                               "Book me tomorrow evening", "Not interested now"])}})
            await db.interactions.insert_one({
                "_id": dbm.new_id("int"), "org_id": config.ORG_ID, "person_id": m["person_id"],
                "person_name": m["person_name"], "channel": camp["channel"], "direction": "in",
                "content": "Reply received", "outcome": "reply", "campaign_id": campaign_id,
                "at": dbm.now_iso()})
    stats = camp.get("stats") or {}
    stats["sent"] = stats.get("sent", 0) + sent
    stats["delivered"] = stats.get("delivered", 0) + sent
    stats["read"] = stats.get("read", 0) + int(sent * 0.7)
    stats["replied"] = stats.get("replied", 0) + replied
    remaining = await db.campaign_messages.count_documents(
        {"campaign_id": campaign_id, "status": "queued"})
    status = "live" if remaining else "completed"
    await db.campaigns.update_one({"_id": campaign_id}, {"$set": {"stats": stats, "status": status}})
    await publish(db, "campaign.status", {"campaign_id": campaign_id, "status": status})


AUTOMATION_DEFS = [
    {"name": "Birthday Offer", "trigger": "Birthday today", "kind": "customers", "segment_key": "vip",
     "template": "Happy Birthday {name}! Enjoy a free add-on with your next {service} at {store}."},
    {"name": "Win-Back at 60 Days", "trigger": "Customer expired 60+ days", "kind": "customers",
     "segment_key": "expired_22p", "template": "Hi {name}, we miss you at {store}! Come back for {service} at a special price."},
    {"name": "Post-Visit Review", "trigger": "Visit completed", "kind": "customers", "segment_key": "loyal",
     "template": "Hi {name}, how was your {service} at {store}? A quick reply helps us a lot!"},
    {"name": "Renewal Day 25", "trigger": "Plan ends in 5 days", "kind": "customers",
     "segment_key": "renewal_due", "template": "Hi {name}, your plan at {store} ends soon. Renew now and keep your preferred slot!"},
    {"name": "New-Lead Welcome", "trigger": "Lead created", "kind": "leads", "segment_key": "new",
     "template": "Hi {name}, thanks for your interest in {service} at {store}! When can we call you?"},
    {"name": "No-Show Follow-Up", "trigger": "Appointment no-show", "kind": "customers", "segment_key": "loyal",
     "template": "Hi {name}, we missed you today. Shall we rebook your {service} at {store}?"},
]


async def run_automation(db, user, name: str):
    org = await rules.org_doc(db)
    auto = next((a for a in AUTOMATION_DEFS if a["name"] == name), None)
    if not auto:
        return None
    aud = await build_audience(db, auto["kind"], auto["segment_key"], None, "all", [], "whatsapp")
    sent = 0
    for p in aud["audience"][:50]:
        svc = await db.services.find_one({"_id": p.get("service_id") or p.get("last_service_id")})
        store = await db.stores.find_one({"_id": p.get("store_id")})
        text = rules.render_template(auto["template"], p, svc, store)
        await db.interactions.insert_one({
            "_id": dbm.new_id("int"), "org_id": config.ORG_ID, "person_id": p["_id"],
            "person_name": p["name"], "channel": "whatsapp", "direction": "out",
            "template": name, "content": text, "outcome": "automation",
            "status": "sent", "at": dbm.now_iso()})
        sent += 1
    await audit(db, user, "automation.run", "automation", None, {"name": name, "sent": sent})
    return {"name": name, "sent": sent, "audience": len(aud["audience"]),
            "excluded_consent": aud["excluded_consent"]}
