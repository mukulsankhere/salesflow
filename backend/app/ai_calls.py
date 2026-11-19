"""AI Calling engine + automatic WhatsApp/SMS messaging (PRD ADD-01..ADD-05).

The voice agent ("Aarav") works the lead queue exactly like the human action queue:
score-first, SLA-aware, with retries, budget caps, calling window and instant human
handoff. Every call is simulated by a deterministic demo provider (in production the
same lifecycle is driven by a telephony provider webhook - Exotel/Twilio - through the
same call records), and every follow-up goes out as a real-looking WhatsApp/SMS message
through the same provider interface the marketing campaigns use (campaigns.py).

Collections:
- ai_state:    one document (_id 'shift') with the shift configuration + toggle
- ai_calls:    one document per call attempt (transcript, outcome, cost, sentiment)
- ai_messages: one document per automatic WhatsApp/SMS message (status lifecycle)
"""
import asyncio
import random
from datetime import datetime, timedelta, timezone

from . import capacity, config, db as dbm, priority, rules, sync
from .realtime import publish

CALL_GAP_MIN = 90          # retry gap per lead (BR: 3 attempts / 90 min)
MAX_CALLS_PER_LEAD_DAY = 2
LINE_SECONDS = 3.5         # real seconds between transcript lines (demo pacing)
TICK_SECONDS = 5

VOICE_NAME = "Aarav"
REPLY_POOL = ["Ok thanks, booked!", "Can you share the location?", "What is the price?",
              "I will come tomorrow", "Not interested now", "Yes, please confirm"]
NUDGE_REPLIES = ["Ok, I will call back", "Evening is better", "Tell me the price first"]


def _now():
    return datetime.now(timezone.utc)


def _ist():
    return _now() + timedelta(hours=5, minutes=30)


def _today():
    return _ist().strftime("%Y-%m-%d")


def _today_start():
    """Start of the IST day (ISO string compare works on stored ISO timestamps)."""
    d = _ist().replace(hour=0, minute=0, second=0, microsecond=0)
    return (d - timedelta(hours=5, minutes=30)).isoformat()


def _parse(ts):
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:  # client-supplied timestamps may lack an offset; assume UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ------------------------------------------------------------------ shift state
DEFAULTS = {
    "_id": "shift", "org_id": config.ORG_ID,
    "enabled": False, "voice": VOICE_NAME, "language": "Hinglish (auto-detect)",
    "window_start": 10, "window_end": 19,      # TRAI-safe promotional calling window
    "daily_budget": 600, "daily_lead_cap": 60,
    "retry_gap_min": CALL_GAP_MIN, "max_attempts": 3,
    "speed": "1.0x", "script": "Qualify v3",
    "updated_at": None,
}


async def get_state(db) -> dict:
    doc = await db.ai_state.find_one({"_id": "shift"})
    if not doc:
        doc = dict(DEFAULTS)
        await db.ai_state.insert_one(dict(doc))
    return doc


async def set_state(db, user, patch: dict) -> dict:
    await get_state(db)
    patch = {k: v for k, v in patch.items() if k in DEFAULTS and k not in ("_id", "org_id")}
    patch["updated_at"] = dbm.now_iso()
    await db.ai_state.update_one({"_id": "shift"}, {"$set": patch})
    return await get_state(db)


def in_window(state: dict) -> bool:
    h = _ist().hour
    return state["window_start"] <= h < state["window_end"]


# ------------------------------------------------------------------ messaging
async def send_message(db, person: dict, channel: str, kind: str, text: str,
                       call_id: str | None = None, when_note: str = "") -> dict:
    """One automatic WhatsApp/SMS message through the demo provider. Consent-first:
    WhatsApp only when the person opted in, otherwise SMS (BR-12/E4)."""
    consent = person.get("consent") or {}
    if channel == "auto":
        channel = "whatsapp" if consent.get("whatsapp") else "sms"
    msg = {
        "_id": dbm.new_id("aim"), "org_id": config.ORG_ID,
        "person_id": person["_id"], "person_name": person["name"],
        "person_kind": "customer" if str(person["_id"]).startswith("cus_") else "lead",
        "channel": channel, "kind": kind, "text": text,
        "status": "sent", "provider_id": f"demo_{random.randint(10**8, 10**9 - 1)}",
        "call_id": call_id, "when_note": when_note,
        "at": dbm.now_iso(), "sent_at": dbm.now_iso(),
    }
    await db.ai_messages.insert_one(msg)
    await db.interactions.insert_one({
        "_id": dbm.new_id("int"), "org_id": config.ORG_ID, "person_id": person["_id"],
        "person_name": person["name"], "channel": channel, "direction": "out",
        "template": f"AI {kind.replace('_', ' ')}", "content": text, "outcome": "automation",
        "status": "sent", "user_name": "Aarav (AI)", "at": dbm.now_iso()})
    await publish(db, "ai_message.sent", {
        "message_id": msg["_id"], "person": person["name"], "channel": channel,
        "kind": kind, "text": text}, store_id=person.get("store_id"))
    return msg


async def _advance_messages(db):
    """Demo provider lifecycle: sent -> delivered -> read -> (sometimes) reply."""
    now = _now()
    msgs = await db.ai_messages.find({"status": {"$in": ["sent", "delivered", "read"]}})
    for m in msgs:
        sent = _parse(m.get("sent_at")) or now
        age = (now - sent).total_seconds()
        if m["status"] == "sent" and age > 15:
            await db.ai_messages.update_one({"_id": m["_id"]},
                                            {"$set": {"status": "delivered", "delivered_at": dbm.now_iso()}})
        elif m["status"] == "delivered" and age > 35 and random.random() < 0.8:
            await db.ai_messages.update_one({"_id": m["_id"]},
                                            {"$set": {"status": "read", "read_at": dbm.now_iso()}})
        elif m["status"] == "read" and age > 55 and random.random() < 0.3:
            reply = random.choice(NUDGE_REPLIES if m["kind"] in ("nudge", "callback", "reminder") else REPLY_POOL)
            await db.ai_messages.update_one({"_id": m["_id"]},
                                            {"$set": {"status": "replied", "replied_at": dbm.now_iso(),
                                                      "reply_text": reply}})
            await db.interactions.insert_one({
                "_id": dbm.new_id("int"), "org_id": config.ORG_ID, "person_id": m["person_id"],
                "person_name": m["person_name"], "channel": m["channel"], "direction": "in",
                "content": reply, "outcome": "reply", "user_name": "Aarav (AI)", "at": dbm.now_iso()})
            await publish(db, "ai_message.reply", {
                "message_id": m["_id"], "person": m["person_name"], "text": reply})


async def _dispatch_reminders(db):
    """Appointment reminders for AI-booked visits: WhatsApp (or SMS) 2h before start."""
    now_iso = dbm.now_iso()
    appts = await db.appointments.find({"org_id": config.ORG_ID, "source": "ai_call",
                                        "status": "booked", "ai_reminder_sent": {"$ne": True}})
    for a in appts:
        due = a.get("ai_reminder_at")
        if not due or due > now_iso:
            continue
        person = None
        if a.get("person_type") == "lead":
            person = await db.leads.find_one({"_id": a.get("person_id") or a.get("lead_id")})
        person = person or (await db.customers.find_one({"_id": a.get("customer_id")}) if a.get("customer_id") else None)
        if not person:
            await db.appointments.update_one({"_id": a["_id"]}, {"$set": {"ai_reminder_sent": True}})
            continue
        when = _fmt_hour(a.get("start_hour", 11))
        text = (f"Reminder: your {a.get('service_name', 'session')} at "
                f"{a.get('store_name', 'our store')} is today at {when}. "
                f"Reply RESCHEDULE to change or STOP to opt out.")
        await send_message(db, person, "auto", "reminder", text)
        await db.appointments.update_one({"_id": a["_id"]}, {"$set": {"ai_reminder_sent": True}})


# ------------------------------------------------------------------ call lifecycle
async def _pick_lead(db, state) -> dict | None:
    """The next lead to dial: score-first, SLA-aware, honouring DNC, retry gaps,
    per-day attempt caps and skips - the same priority rules the human queue uses."""
    leads = await db.leads.find({"org_id": config.ORG_ID, "archived": {"$ne": True},
                                 "stage": {"$nin": ["booked", "lost"]}})
    todays = await db.ai_calls.find({"org_id": config.ORG_ID, "date": _today()})
    per_lead: dict[str, int] = {}
    recent: dict[str, datetime] = {}
    for c in todays:
        per_lead[c["lead_id"]] = per_lead.get(c["lead_id"], 0) + 1
        st = _parse(c.get("started_at"))
        if st:
            recent[c["lead_id"]] = max(recent.get(c["lead_id"], st), st)
    now = _now()
    cands = []
    for l in leads:
        if l.get("ai_dnc") or (l.get("consent") or {}).get("call") is False:
            continue
        if per_lead.get(l["_id"], 0) >= MAX_CALLS_PER_LEAD_DAY:
            continue
        if per_lead.get(l["_id"], 0) >= state.get("max_attempts", 3):
            continue
        last = recent.get(l["_id"])
        if last and (now - last).total_seconds() < CALL_GAP_MIN * 60:
            continue
        skip = _parse(l.get("ai_skip_until"))
        if skip and skip > now:
            continue
        cands.append(l)
    if not cands:
        return None
    prios = await priority.compute_priority_many(db, cands)
    tier_rank = {"hot": 0, "warm": 1, "low": 2}
    # priority first (tier + score), then SLA breaches inside the tier; service and
    # location decide where the booking lands (overload plan), not who dials first
    cands.sort(key=lambda l: (tier_rank[prios[l["_id"]]["tier"]],
                              0 if _reason(l, prios[l["_id"]]).startswith("SLA breached") else 1,
                              -l.get("score", 0), l.get("created_at") or ""))
    return cands[0]


async def _context(db, lead):
    # leads carry service_id; customers (renewal calls) carry last_service_id
    service = await db.services.find_one(
        {"_id": lead.get("service_id") or lead.get("last_service_id")})
    store = await db.stores.find_one({"_id": lead.get("store_id")})
    return service or {}, store or {}


def _offer_pct(km: float | None, org: dict) -> int:
    """Cross-store overload discount (shown on the call): 10% for nearby stores,
    15% beyond the distance threshold, hard cap 20%."""
    far_km = rules.rule_value(org, "BR-07KM", 5)
    if km is not None and km > far_km:
        return min(20, rules.rule_value(org, "AI-OFFER-FAR", 15))
    return min(20, rules.rule_value(org, "AI-OFFER", 10))


def _placeable(ctx: dict, store_id: str, service: dict) -> bool:
    return any(capacity.place(ctx, store_id, service, h) for h in config.SLOT_HOURS)


async def _overload_plan(db, lead, service, store) -> dict:
    """Is the lead's preferred store overloaded for this service tomorrow - and
    which nearby stores can take the booking at a percentage off? The offer list
    (store, distance, % off, price after discount) is shown live on the call."""
    org = await rules.org_doc(db)
    if not service or not store or not store.get("_id"):
        return {"overloaded": False, "util": 0, "threshold": 90, "offers": [], "date": None}
    day = (_ist().date() + timedelta(days=1)).isoformat()
    threshold = rules.rule_value(org, "BR-06", 90)
    all_stores = await db.stores.find({"org_id": config.ORG_ID, "status": "active"})
    ctx = await capacity.load_day_ctx(db, [s["_id"] for s in all_stores], day)
    util = (await capacity.store_day_utilization(db, store["_id"], day))["utilization_pct"]
    offered = capacity.store_offers(ctx, store["_id"], service)
    has_slot = bool(offered and _placeable(ctx, store["_id"], service))
    overloaded = (not offered) or util >= threshold or not has_slot
    offers = []
    if overloaded:
        dists = await capacity.distances_from(db, store["_id"])
        rows = []
        for s in all_stores:
            if s["_id"] == store["_id"] or not capacity.store_offers(ctx, s["_id"], service):
                continue
            if not _placeable(ctx, s["_id"], service):
                continue
            rows.append((dists.get(s["_id"], 0), s))
        rows.sort(key=lambda r: (r[0], r[1]["name"]))
        for km, s in rows[:2]:
            pct = _offer_pct(km, org)
            base = service.get("base_price", 0)
            offers.append({"store_id": s["_id"], "store_name": s["name"], "area": s.get("area"),
                           "km": km, "pct": pct, "price_before": base,
                           "price_after": int(base * (100 - pct) / 100)})
    return {"overloaded": overloaded, "util": util, "threshold": threshold,
            "offers": offers, "date": day}


def _fmt_hour(h: int) -> str:
    hh = h % 12 or 12
    return f"{hh}:00 {'AM' if h < 12 else 'PM'}"


# ------------------------------------------------------------------ slot intent
def _daypart_of(h: int) -> str:
    """Which part of the day a start hour belongs to (slots quoted to the lead
    must sit inside the part they asked for)."""
    if h < 13:
        return "morning"
    if h < 17:
        return "afternoon"
    return "evening"


def _day_label(day_iso: str) -> str:
    d = datetime.fromisoformat(day_iso).date()
    today = _ist().date()
    if d == today + timedelta(days=1):
        return "tomorrow"
    if d == today + timedelta(days=2):
        return "the day after"
    return d.strftime("%A, %d %b")


async def _open_slots(db, store_id, service, pref: str | None = None, max_days: int = 3):
    """Real employee availability from the capacity engine (shifts, leaves, existing
    reservations - BR-03/04): every placeable (day, start_hour) for this service at
    this store over the next few days. Split into slots inside the lead's preferred
    daypart and everything else, both sorted soonest-first."""
    pref_slots: list[tuple[str, int]] = []
    any_slots: list[tuple[str, int]] = []
    if not service or not store_id:
        return pref_slots, any_slots
    for add in range(1, max_days + 1):
        day = (_ist().date() + timedelta(days=add)).isoformat()
        ctx = await capacity.load_day_ctx(db, [store_id], day)
        if not capacity.store_offers(ctx, store_id, service):
            continue
        for h in config.SLOT_HOURS:
            if capacity.place(ctx, store_id, service, h):
                any_slots.append((day, h))
                if pref and _daypart_of(h) == pref:
                    pref_slots.append((day, h))
    return pref_slots, any_slots


def _sla_min(source: str) -> int:
    v = config.RULES["BR-16"]["value"]
    return int(v.get(source, 15))


def _reason(lead: dict, prio: dict) -> str:
    created = _parse(lead.get("created_at"))
    if created:
        mins = int((_now() - created).total_seconds() / 60)
        sla = _sla_min(lead.get("source", "others"))
        if lead.get("stage") == "new" and mins > sla:
            return f"SLA breached {mins}m - never contacted"
    fu = _parse(lead.get("follow_up_at"))
    if fu and fu < _now():
        return "Follow-up due today"
    if prio["reasons"]:
        return prio["reasons"][0]
    return f"{config.SOURCE_LABELS.get(lead.get('source'), 'Portal')} lead, score {lead.get('score', 0)}"


def _roll_outcome(lead: dict) -> str:
    """Outcome weighted by the explainable score band (demo provider behaviour).
    A lead can carry ai_force_outcome (demo/debug hook: 'make the next call book')."""
    forced = lead.get("ai_force_outcome")
    if forced:
        return forced
    s = lead.get("score", 0)
    if s >= 80:
        return random.choices(["booked", "callback", "interested", "not_interested", "voicemail", "no_answer", "dnc"],
                              weights=[42, 16, 14, 8, 10, 9, 1])[0]
    if s >= 65:
        return random.choices(["booked", "callback", "interested", "not_interested", "voicemail", "no_answer", "dnc"],
                              weights=[24, 18, 16, 12, 15, 14, 1])[0]
    return random.choices(["booked", "callback", "interested", "not_interested", "voicemail", "no_answer", "dnc"],
                          weights=[10, 12, 12, 12, 20, 30, 4])[0]


def _roll_renewal_outcome(cust: dict) -> str:
    """Outcome for a renewal follow-up call. Expired customers are warm (they
    already paid before), so 'renewed' weighs higher than a cold lead booking.
    A customer can carry ai_force_outcome='renewed' (demo/debug hook)."""
    forced = cust.get("ai_force_outcome")
    if forced:
        return "renewed" if forced == "booked" else forced
    return random.choices(["renewed", "callback", "interested", "not_interested", "voicemail", "no_answer", "dnc"],
                          weights=[34, 18, 16, 10, 12, 9, 1])[0]


async def _renewal_candidates(db) -> list[dict]:
    """Expired-plan customers the AI should call for a renewal follow-up, most
    overdue first (loyal, high-visit customers break ties). Honours call consent,
    the per-customer retry gap and renewal skips - exactly like the lead rules."""
    today = _ist().date()
    now = _now()
    custs = await db.customers.find({"org_id": config.ORG_ID})
    svc_ids = {s["_id"] for s in await db.services.find({"org_id": config.ORG_ID})}
    store_ids = {s["_id"] for s in await db.stores.find({"org_id": config.ORG_ID})}
    out = []
    for c in custs:
        end = (c.get("plan") or {}).get("end")
        if not end or c.get("last_service_id") not in svc_ids or c.get("store_id") not in store_ids:
            continue
        try:
            end_d = datetime.fromisoformat(end).date()
        except ValueError:
            continue
        if end_d >= today:  # plan still active - not a renewal candidate
            continue
        if (c.get("consent") or {}).get("call") is False:
            continue
        skip = _parse(c.get("renewal_skip_until"))
        if skip and skip > now:
            continue
        last = _parse(c.get("renewal_last_call_at"))
        if last and (now - last).total_seconds() < CALL_GAP_MIN * 60:
            continue
        out.append({"customer": c, "expired_days": (today - end_d).days})
    out.sort(key=lambda r: (-min(r["expired_days"], 60), -r["customer"].get("visits", 0),
                            r["customer"].get("name", "")))
    return out


PREF_LEAD_LINES = {
    "morning": ["Morning is better for me - before 1 PM.", "Subah chalega, morning around 11?"],
    "afternoon": ["Afternoon works best for me - around 2 to 4.", "I can come in the afternoon."],
    "evening": ["Evening is better for me - after 6 PM.", "Shaam ko aa sakta hoon, evening please."],
}


def _script(lead, service, store, outcome, overload=False, offers=None,
            accept_alt=False, util=0, pref=None, pref_slots=None,
            any_slots=None, alt_slot=None, renewal=None) -> list[dict]:
    """Transcript lines consistent with the final outcome (who, text, stage tag).
    Pricing, duration, store location AND every slot quoted come from live data:
    the lead's stated daypart (morning/afternoon/evening) is matched against real
    employee availability from the capacity engine - the agent never quotes a
    price or a time that is not in the database. `renewal` (dict with expired_days
    and plan_end) turns the call into a plan-renewal follow-up for an expired
    customer - same slot NLU and live-availability rules apply."""
    svc = service.get("name", "the service")
    st = store.get("name", "our store")
    area = store.get("area") or st
    price = service.get("base_price", 149900)
    dur = service.get("duration_hours", 1)
    off = int(price * 0.8)
    price_l = f"₹{round(price / 100):,}" if price else "the current offer price"
    off_l = f"₹{round(off / 100):,}" if price else "the discounted price"
    pref = pref or "morning"
    pref_slots = pref_slots or []
    any_slots = any_slots or []
    is_renewal = bool(renewal)
    if is_renewal:
        end = (renewal or {}).get("plan_end") or "recently"
        days = (renewal or {}).get("expired_days")
        ago = f" {days} days ago" if days is not None else ""
        lines = [
            ("ai", f"Hello! Am I speaking with {lead['name']}? This is {VOICE_NAME} from {st} "
                   f"({area}). I see your {svc} plan with us ended{ago} - this is a quick "
                   f"follow-up to renew it.", "Greeting"),
            ("lead", random.choice(["Haan, yes - I remember the plan.",
                                    "Yes, speaking. It expired?",
                                    "Hello ji, yes."]), "Greeting"),
        ]
    else:
        lines = [
            ("ai", f"Hello! Am I speaking with {lead['name']}? This is {VOICE_NAME} from {st} "
                   f"({area}), about your {svc} enquiry.", "Greeting"),
            ("lead", random.choice(["Haan, yes - I filled the form on your website.",
                                    "Yes, speaking. What is this about?",
                                    "Hello ji, yes."]), "Greeting"),
        ]
    if outcome == "voicemail":
        if is_renewal:
            return [lines[0], ("ai", f"Your {svc} plan at {st} has ended - if you would like to "
                                     f"renew, reply RENEW and I will set it up. Thank you!",
                               "Voicemail message")]
        return [lines[0], ("ai", f"You can reach us anytime for {svc} at {st}. "
                                 f"I will send the details on WhatsApp. Thank you!", "Voicemail message")]
    if outcome == "no_answer":
        return lines[:1]
    if outcome == "dnc":
        return [lines[0],
                ("lead", "Look, stop calling me. Remove my number.", "Objection"),
                ("ai", "I am very sorry - I have removed your number from our calling list right away. "
                       "You will also get an SMS confirmation. Sorry once again.", "Opt-out")]
    if is_renewal:
        price_line = ("ai", f"Renewing is easy - {svc} is {price_l} for another 30-day plan of "
                            f"{dur}-hour sessions at {st}, same benefits as before. Would a morning "
                            f"or an evening slot suit you better?", "Handling price")
    else:
        price_line = ("ai", f"{svc} is {price_l} for a {dur}-hour session, and this week we have a "
                            f"20% first-visit offer - {off_l}. Would a morning or an evening slot "
                            f"suit you better?", "Handling price")
    if outcome == "not_interested":
        if is_renewal:
            lines += [("lead", random.choice(["Not right now, maybe later.", "I have switched elsewhere.",
                                              "Please don't call again for this."]), "Objection"),
                      ("ai", "Understood - I will note that for now and not disturb you about the "
                             f"renewal. You are always welcome at {st}. Have a good day!", "Polite close")]
            return lines
        lines += [("lead", random.choice(["Not interested, thank you.", "I have plans elsewhere.",
                                          "Please don't call again."]), "Objection"),
                  ("ai", "Understood - I will close the enquiry. If you change your mind, "
                         f"we are at {st} anytime. Have a good day!", "Polite close")]
        return lines

    def intent_answer():
        return ("lead", random.choice(PREF_LEAD_LINES.get(pref) or PREF_LEAD_LINES["morning"]),
                "Intent - Slot Preference")

    if overload and offers:
        # the preferred location is overloaded: offer the nearest alternatives with
        # the exact % off, a real slot at the offered store, and the price after
        # discount - all straight from store data; the lead's stated daypart is
        # honoured first when picking the offered slot
        o = offers[0]
        a_day, a_hour = alt_slot or ((_ist().date() + timedelta(days=1)).isoformat(), 17)
        slot_note = f"{_day_label(a_day)} at {_fmt_hour(a_hour)}"
        if _daypart_of(a_hour) == pref:
            slot_note += f" ({pref} slot, as you asked)"
        lines += [("lead", random.choice(["What is the price?", "Kitna charge karoge?",
                                          "Tell me the offer first."]), "Price question"),
                  ("ai", f"{svc} is {price_l} for a {dur}-hour session. Would a morning or an "
                         f"evening slot suit you better?", "Handling price"),
                  ("lead", random.choice(PREF_LEAD_LINES.get(pref) or PREF_LEAD_LINES["morning"]),
                   "Intent - Slot Preference"),
                  ("ai", f"Noted - {pref} it is. One thing though: {st} is almost fully booked "
                         f"tomorrow ({util}% loaded). To keep your slot this week I can move you to "
                         f"{o['store_name']} in {o['area']}, about {o['km']} km away, with "
                         f"{slot_note}, at {o['pct']}% off - ₹{round(o['price_after'] / 100):,} "
                         f"instead of ₹{round(o['price_before'] / 100):,}. Shall I book you there?",
                   "Overload - Cross-Store Offer"),
                  ("lead", "Theek hai, that works - please book it there." if accept_alt
                   else f"I would rather come to {st} only.", "Offer Answer")]
        if accept_alt:
            note = "" if _daypart_of(a_hour) == pref else " - the closest opening I have"
            lines += [("ai", f"Done! Your {svc} session at {o['store_name']} is set for "
                             f"{_day_label(a_day)} at {_fmt_hour(a_hour)}{note} with {o['pct']}% off - "
                             f"₹{round(o['price_after'] / 100):,}. The WhatsApp confirmation is on "
                             f"its way.", "Booking"),
                      ("lead", "Perfect, thank you!", "Confirm")]
        else:
            if pref_slots:
                d0, h0 = pref_slots[0]
                lines += [("ai", f"No problem - I will keep {st} and take the next open {pref} slot "
                                 f"there: {_day_label(d0)} at {_fmt_hour(h0)}. I will hold it for you.",
                          "Booking")]
            else:
                lines += [("ai", f"No problem - I will keep {st} and take the next open {pref} slot "
                                 f"there.", "Booking")]
        return lines
    lines += [("lead", random.choice(["What is the price?", "Kitna charge karoge?",
                                      "Tell me the offer first."]), "Price question"),
              price_line]
    if outcome in ("booked", "renewed"):
        renew_note = ""
        if is_renewal:
            renew_note = " and your 30-day plan restarts with that session"
        if pref_slots:
            day, hour = pref_slots[0]
            when = f"{_day_label(day)} at {_fmt_hour(hour)}"
            lines += [intent_answer(),
                      ("ai", f"Noted - {pref} it is. Let me check the calendar… I have {when} open "
                             f"for your {dur}-hour {svc} session at {st}, and the team is free then. "
                             f"I have booked that for you{renew_note} - the WhatsApp confirmation is "
                             f"on its way.",
                       "Booking"),
                      ("lead", "Theek hai, done. Thank you!", "Confirm")]
        elif any_slots:
            day, hour = any_slots[0]
            when = f"{_day_label(day)} at {_fmt_hour(hour)}"
            lines += [intent_answer(),
                      ("ai", f"I checked - there is no {pref} opening in the next few days. The "
                             f"earliest I have at {st} is {when} for your {dur}-hour {svc} session. "
                             f"Shall I hold that? Great - booked{renew_note}, confirmation on WhatsApp.",
                       "Booking"),
                      ("lead", "Theek hai, done. Thank you!", "Confirm")]
        else:
            lines += [intent_answer(),
                      ("ai", f"I checked the roster - the calendar at {st} is full for the next few "
                             f"days. I will call you back the moment a {pref} slot opens.",
                       "Booking")]
    elif outcome == "callback":
        if pref_slots:
            day, hour = pref_slots[0]
            lines += [intent_answer(),
                      ("ai", f"Noted. I do have {_day_label(day)} {_fmt_hour(hour)} open for the "
                             f"{dur}-hour {svc} session - think about it and I will call you back "
                             f"shortly to lock it in.", "Follow-up")]
        else:
            lines += [intent_answer(),
                      ("ai", f"Noted - I will call you back as soon as a {pref} slot opens at {st}. "
                             f"The {svc} details are on WhatsApp right now.", "Follow-up")]
    elif outcome == "interested":
        lines += [("lead", random.choice(["I need to check my schedule and tell you.",
                                          "Maybe weekend - I will confirm."]), "Soft yes"),
                  ("ai", "No problem - I will check back with you tomorrow, and I am sending "
                         f"the {svc} details on WhatsApp right now.", "Follow-up")]
    else:  # handed_over path uses connected lines; outcome applied by the admin action
        lines += [("lead", random.choice(["I want to talk to a manager about the package.",
                                          "I have a complaint about my last visit."]), "Escalation"),
                  ("ai", "Absolutely - I am connecting you to our team lead right now. "
                         f"One moment please, they will join this call.", "Handoff")]
    return lines


async def _start_renewal_call(db) -> dict | None:
    """No dialable lead left in the portal: call an expired-plan customer and
    follow up on renewing their service plan (most overdue first, consent and
    retry-gap aware). The slot rules are identical to lead calls."""
    cands = await _renewal_candidates(db)
    if not cands:
        return None
    r = cands[0]
    c = r["customer"]
    service, store = await _context(db, c)  # last_service_id / store_id based
    score = min(99, 50 + c.get("visits", 0) * 3)
    call = {
        "_id": dbm.new_id("aic"), "org_id": config.ORG_ID, "date": _today(),
        "kind": "renewal", "lead_id": None, "customer_id": c["_id"],
        "person_name": c["name"],
        "phone": c.get("phone_display") or c.get("phone_norm"),
        "service": service.get("name"), "store": store.get("name"),
        "location": store.get("area"),
        "store_id": c.get("store_id"), "service_id": c.get("last_service_id"),
        "tier": "hot" if c.get("segment") == "vip" else "warm" if c.get("visits", 0) >= 5 else "low",
        "score": score, "attempt": 1, "outcome": "in_progress",
        "stage_tag": "Dialing", "sentiment": "neutral", "intents": [],
        "expired_days": r["expired_days"], "plan_end": (c.get("plan") or {}).get("end"),
        "store_load_pct": 0, "overload": False, "offers": [], "offer_accepted": False,
        "slot_pref": None, "slot_day": None, "slot_hour": None, "slot_candidates": [],
        "rerouted": False, "booked_store": None, "discount_pct": 0,
        "data_synced": False,
        "transcript": [], "cost": 0, "handed": False, "force_end": False,
        "started_at": dbm.now_iso(), "ended_at": None,
    }
    await db.ai_calls.insert_one(call)
    await publish(db, "ai_call.started", {"call_id": call["_id"], "person": c["name"],
                                          "score": call["score"], "store": call["store"],
                                          "renewal": True, "expired_days": r["expired_days"]},
                  store_id=call.get("store_id"))
    return call


async def start_call(db) -> dict | None:
    """Dial the next lead (engine loop calls this when idle). When no dialable
    lead is left in the portal, fall back to expired-plan customers and follow
    up on renewing their service plan."""
    state = await get_state(db)
    lead = await _pick_lead(db, state)
    if not lead:
        return await _start_renewal_call(db)
    service, store = await _context(db, lead)
    plan = await _overload_plan(db, lead, service, store)
    call = {
        "_id": dbm.new_id("aic"), "org_id": config.ORG_ID, "date": _today(),
        "kind": "lead", "lead_id": lead["_id"], "person_name": lead["name"],
        "phone": lead.get("phone_display") or lead.get("phone_norm"),
        "service": service.get("name"), "store": store.get("name"),
        "location": store.get("area"),
        "store_id": lead.get("store_id"), "service_id": lead.get("service_id"),
        "tier": "hot" if lead.get("score", 0) >= 80 else "warm" if lead.get("score", 0) >= 65 else "low",
        "score": lead.get("score", 0), "attempt": 1, "outcome": "in_progress",
        "stage_tag": "Dialing", "sentiment": "neutral", "intents": [],
        "store_load_pct": plan["util"], "overload": plan["overloaded"],
        "offers": plan["offers"], "offer_accepted": False,
        "slot_pref": None, "slot_day": None, "slot_hour": None, "slot_candidates": [],
        "rerouted": False, "booked_store": None, "discount_pct": 0,
        "data_synced": False,
        "transcript": [], "cost": 0, "handed": False, "force_end": False,
        "started_at": dbm.now_iso(), "ended_at": None,
    }
    await db.ai_calls.insert_one(call)
    await publish(db, "ai_call.started", {"call_id": call["_id"], "person": lead["name"],
                                          "score": call["score"], "store": call["store"],
                                          "overload": call["overload"], "load": call["store_load_pct"]},
                  store_id=lead.get("store_id"))
    return call


async def _finish_call(db, call: dict, outcome: str, sentiment: str, intents: list):
    ended = dbm.now_iso()
    cost = random.randint(11, 18)
    # only the first finisher wins (an admin end + the script loop can race)
    res = await db.ai_calls.update_one(
        {"_id": call["_id"], "outcome": "in_progress"},
        {"$set": {"outcome": outcome, "sentiment": sentiment,
                  "intents": intents, "cost": cost,
                  "stage_tag": outcome.replace("_", " ").title(),
                  "ended_at": ended}})
    if not res:
        return
    if call.get("kind") == "renewal":
        cust = await db.customers.find_one({"_id": call["customer_id"]})
        if not cust:
            return
        await db.customers.update_one({"_id": cust["_id"]},
                                      {"$set": {"renewal_last_call_at": dbm.now_iso()},
                                       "$unset": {"ai_force_outcome": ""}})
        service, store = await _context(db, cust)
        await _apply_outcome(db, cust, call, outcome, service, store)
        await db.interactions.insert_one({
            "_id": dbm.new_id("int"), "org_id": config.ORG_ID, "person_id": cust["_id"],
            "person_name": cust["name"], "channel": "call", "direction": "out",
            "template": f"AI renewal call ({VOICE_NAME})",
            "content": f"AI renewal call - outcome: {outcome.replace('_', ' ')}",
            "outcome": "ai_call", "status": "completed", "user_name": "Aarav (AI)",
            "call_id": call["_id"], "at": dbm.now_iso()})
        await publish(db, "ai_call.outcome", {"call_id": call["_id"], "person": cust["name"],
                                              "outcome": outcome, "store": call["store"],
                                              "renewal": True},
                      store_id=call.get("store_id"))
        return
    lead = await db.leads.find_one({"_id": call["lead_id"]})
    if not lead:
        return
    await db.leads.update_one({"_id": lead["_id"]},
                              {"$unset": {"ai_force_outcome": "", "ai_force_accept": ""}})
    service, store = await _context(db, lead)
    await _apply_outcome(db, lead, call, outcome, service, store)
    await db.interactions.insert_one({
        "_id": dbm.new_id("int"), "org_id": config.ORG_ID, "person_id": lead["_id"],
        "person_name": lead["name"], "channel": "call", "direction": "out",
        "template": f"AI call ({VOICE_NAME})", "content": f"AI call - outcome: {outcome.replace('_', ' ')}",
        "outcome": "ai_call", "status": "completed", "user_name": "Aarav (AI)",
        "call_id": call["_id"], "at": dbm.now_iso()})
    await db.leads.update_one({"_id": lead["_id"]}, {"$set": {"last_contact_at": dbm.now_iso()}})
    await publish(db, "ai_call.outcome", {"call_id": call["_id"], "person": lead["name"],
                                          "outcome": outcome, "store": call["store"]},
                  store_id=lead.get("store_id"))


async def _apply_outcome(db, lead: dict, call: dict, outcome: str, service: dict, store: dict):
    """Move the lead/pipeline forward exactly like a human action would (stage history,
    follow-ups, messages) and fire the automatic WhatsApp/SMS follow-ups."""
    now = _now()
    hist = lead.get("stage_history") or []

    def stage_to(stage, note):
        hist.append({"stage": stage, "at": dbm.now_iso(), "note": note})
        upd = {"stage": stage, "stage_history": hist}
        if stage == "offered":
            upd["offered_at"] = dbm.now_iso()
        return upd

    if call.get("kind") == "renewal":
        await _apply_renewal_outcome(db, lead, call, outcome, service, store)
        return

    if outcome == "booked":
        # only a real appointment counts as "booked" - and it must be the very slot
        # quoted on the call (that slot came from real employee availability, with
        # the lead's stated daypart first); further candidates are the fallback
        booked_appt = None
        offer = None
        admin = await db.users.find_one({"org_id": config.ORG_ID, "roles": "admin"})
        ai_user = admin or {"_id": "ai", "name": "Aarav (AI)", "roles": ["admin"]}
        from . import booking as booking_svc
        if call.get("offer_accepted") and call.get("offers"):
            quoted = [(call.get("slot_day"), int(call.get("slot_hour") or 17))]
            quoted += [tuple(c) for c in (call.get("slot_candidates") or [])]
            quoted.append(((_ist().date() + timedelta(days=1)).isoformat(), 17))
            seen: set = set()
            for o in call["offers"]:
                for day_s, hour in quoted:
                    if not day_s or (o["store_id"], day_s, hour) in seen:
                        continue
                    seen.add((o["store_id"], day_s, hour))
                    try:
                        booked_appt = await booking_svc.book(
                            db, ai_user, "lead", lead["_id"], o["store_id"], lead["service_id"],
                            day_s, hour, src_campaign="ai_call",
                            discount_pct=o["pct"],
                            discount_reason=f"Cross-store overload offer ({o['pct']}% off)",
                            rerouted_from=store.get("name"), apply_far_benefit=False)
                        offer = o
                        break
                    except Exception as e:  # offer store lost the slot mid-call
                        print(f"[ai_calls] cross-store booking {o['store_name']} {day_s} h{hour} "
                              f"failed: {type(e).__name__}: {e}")
                if booked_appt:
                    break
        if not booked_appt and not call.get("offer_accepted"):
            # book the slots quoted on the call, preference first; the safe defaults
            # stay as a last resort if every open slot vanished mid-call
            cands = [tuple(c) for c in (call.get("slot_candidates") or [])]
            cands += [((_ist().date() + timedelta(days=d)).isoformat(), h)
                      for d, h in ((1, 17), (1, 18), (1, 11), (2, 17), (2, 11), (3, 12))]
            seen: set = set()
            for day_s, hour in cands:
                if (day_s, hour) in seen:
                    continue
                seen.add((day_s, hour))
                try:
                    appt = await booking_svc.book(
                        db, ai_user, "lead", lead["_id"], lead["store_id"], lead["service_id"],
                        day_s, hour, src_campaign="ai_call")
                    booked_appt = appt
                    break
                except Exception as e:  # slot full / capacity guards
                    print(f"[ai_calls] booking slot {day_s} h{hour} failed: {type(e).__name__}: {e}")
        if booked_appt:
            hour = booked_appt["start_hour"]
            day = datetime.fromisoformat(booked_appt["date"])
            ist_tz = timezone(timedelta(hours=5, minutes=30))
            start_ist = datetime(day.year, day.month, day.day, hour, tzinfo=ist_tz)
            reminder_at = (start_ist - timedelta(hours=2)).astimezone(timezone.utc).isoformat()
            await db.appointments.update_one({"_id": booked_appt["_id"]}, {"$set": {
                "source": "ai_call", "created_by": "Aarav (AI)",
                "ai_reminder_at": reminder_at, "ai_reminder_sent": False}})
            when = f"on {day.strftime('%A, %d %b')} at {_fmt_hour(hour)}"
            final_r = round((booked_appt.get("price") or {}).get("final", 0) / 100)
            if offer:
                await send_message(db, lead, "auto", "booking_confirmation",
                                   f"Booked! Your {service.get('name', 'session')} at "
                                   f"{offer['store_name']} ({offer['area']}) is {when} - your "
                                   f"{offer['pct']}% off is applied: ₹{final_r:,} "
                                   f"(was ₹{round(offer['price_before'] / 100):,}). "
                                   f"Reply RESCHEDULE to change or STOP to opt out.",
                                   call_id=call["_id"])
            else:
                await send_message(db, lead, "auto", "booking_confirmation",
                                   f"Booked! Your {service.get('name', 'session')} at "
                                   f"{store.get('name', 'our store')} is {when}. "
                                   f"Reply RESCHEDULE to change or STOP to opt out.",
                                   call_id=call["_id"])
            # booking.book already re-verified the whole pipeline (sync.verify):
            # lead stage, appointment, slot reservations and customer counters agree
            await db.ai_calls.update_one({"_id": call["_id"]}, {"$set": {
                "booked_slot": f"{booked_appt['date']} {_fmt_hour(hour)}",
                "booked_store": booked_appt["store_name"],
                "discount_pct": (offer or {}).get("pct", 0),
                "rerouted": bool(offer), "data_synced": True}})
            fresh_lead = await db.leads.find_one({"_id": lead["_id"]})
            await publish(db, "ai_call.booked", {
                "call_id": call["_id"], "person": lead["name"],
                "pipeline_status": fresh_lead.get("stage"),
                "appointment_id": booked_appt["_id"], "store": booked_appt["store_name"],
                "rerouted": bool(offer), "discount_pct": (offer or {}).get("pct", 0),
                "data_synced": True}, store_id=lead.get("store_id"))
            return
        # no slot anywhere - the offer stays open with a follow-up instead; the call
        # record says "interested" so the Booked KPI only counts real appointments
        upd = stage_to("offered", f"AI call {call['_id']} - calendar full, slot offered")
        upd["follow_up_at"] = (now + timedelta(days=1)).isoformat()
        await db.leads.update_one({"_id": lead["_id"]}, {"$set": upd})
        await db.ai_calls.update_one({"_id": call["_id"]},
                                     {"$set": {"outcome": "interested", "stage_tag": "Slot Offered",
                                               "intents": (call.get("intents") or []) + ["Calendar full - follow-up scheduled"]}})
        return
    if outcome == "callback":
        fu = now + timedelta(minutes=90)
        upd = stage_to("interested" if lead["stage"] in ("new", "contacted") else lead["stage"],
                       "AI call - callback requested")
        upd["follow_up_at"] = fu.isoformat()
        await db.leads.update_one({"_id": lead["_id"]}, {"$set": upd})
        await send_message(db, lead, "auto", "callback",
                           f"Thanks for talking to {store.get('name', 'us')}! Our team will call you back "
                           f"around 5 PM today about {service.get('name', 'your enquiry')}. "
                           f"Reply STOP to opt out.", call_id=call["_id"])
    elif outcome == "interested":
        upd = stage_to("interested", "AI call - interested")
        upd["follow_up_at"] = (now + timedelta(days=1)).isoformat()
        await db.leads.update_one({"_id": lead["_id"]}, {"$set": upd})
    elif outcome in ("voicemail", "no_answer"):
        todays = await db.ai_calls.find({"org_id": config.ORG_ID, "lead_id": lead["_id"], "date": _today()})
        if len(todays) >= 2:
            await send_message(db, lead, "auto", "nudge",
                               f"We tried calling you about your {service.get('name', 'enquiry')} at "
                               f"{store.get('name', 'our store')}. Reply YES for a callback or STOP to opt out.",
                               call_id=call["_id"])
    elif outcome == "not_interested":
        upd = stage_to("lost", "AI call - not interested")
        upd["lost_reason"] = "No Response"
        await db.leads.update_one({"_id": lead["_id"]}, {"$set": upd})
    elif outcome == "dnc":
        consent = lead.get("consent") or {}
        consent["call"] = False
        consent["whatsapp"] = False
        await db.leads.update_one({"_id": lead["_id"]},
                                  {"$set": {"consent": consent, "ai_dnc": True}})
        await send_message(db, lead, "sms", "opt_out",
                           "As requested, we have removed your number from our calling list. "
                           "Sorry for the disturbance.", call_id=call["_id"])


async def _apply_renewal_outcome(db, cust: dict, call: dict, outcome: str,
                                 service: dict, store: dict):
    """Renewal follow-ups for expired-plan customers: 'renewed' books exactly the
    slot quoted on the call at the customer's home store (renew=True restarts the
    30-day plan, BR-01); every other outcome schedules the retry/follow-up on the
    customer with polite guardrails - no stage pipeline is touched."""
    now = _now()
    if outcome == "renewed":
        booked_appt = None
        admin = await db.users.find_one({"org_id": config.ORG_ID, "roles": "admin"})
        ai_user = admin or {"_id": "ai", "name": "Aarav (AI)", "roles": ["admin"]}
        from . import booking as booking_svc
        # the slots quoted on the call came from real availability - try them in
        # order (stated preference first), then a few safe defaults
        cands = [tuple(x) for x in (call.get("slot_candidates") or [])]
        cands += [((_ist().date() + timedelta(days=d)).isoformat(), h)
                  for d, h in ((1, 17), (1, 18), (1, 11), (2, 17), (2, 11), (3, 12))]
        seen: set = set()
        for day_s, hour in cands:
            if (day_s, hour) in seen:
                continue
            seen.add((day_s, hour))
            try:
                booked_appt = await booking_svc.book(
                    db, ai_user, "customer", cust["_id"], cust["store_id"],
                    cust["last_service_id"], day_s, hour, src_campaign="ai_call", renew=True)
                break
            except Exception as e:  # slot full / capacity guards
                print(f"[ai_calls] renewal booking {day_s} h{hour} failed: {type(e).__name__}: {e}")
        if booked_appt:
            hour = booked_appt["start_hour"]
            day = datetime.fromisoformat(booked_appt["date"])
            ist_tz = timezone(timedelta(hours=5, minutes=30))
            start_ist = datetime(day.year, day.month, day.day, hour, tzinfo=ist_tz)
            reminder_at = (start_ist - timedelta(hours=2)).astimezone(timezone.utc).isoformat()
            await db.appointments.update_one({"_id": booked_appt["_id"]}, {"$set": {
                "source": "ai_call", "created_by": "Aarav (AI)",
                "ai_reminder_at": reminder_at, "ai_reminder_sent": False}})
            when = f"on {day.strftime('%A, %d %b')} at {_fmt_hour(hour)}"
            final_r = round((booked_appt.get("price") or {}).get("final", 0) / 100)
            await send_message(db, cust, "auto", "booking_confirmation",
                               f"Renewed! Your {service.get('name', 'session')} plan at "
                               f"{store.get('name', 'our store')} restarts {when} - ₹{final_r:,} "
                               f"for the next 30 days. Reply RESCHEDULE to change or STOP to opt out.",
                               call_id=call["_id"])
            # booking.book (renew=True) already restarted the plan and re-verified
            # the pipeline (sync.verify); guard against repeat calls for a month
            await db.customers.update_one({"_id": cust["_id"]}, {"$set": {
                "renewal_skip_until": (now + timedelta(days=35)).isoformat(),
                "last_contact_at": dbm.now_iso()}})
            await db.ai_calls.update_one({"_id": call["_id"]}, {"$set": {
                "booked_slot": f"{booked_appt['date']} {_fmt_hour(hour)}",
                "booked_store": booked_appt["store_name"],
                "data_synced": True, "renewal_done": True}})
            fresh_cust = await db.customers.find_one({"_id": cust["_id"]})
            await publish(db, "ai_call.booked", {
                "call_id": call["_id"], "person": cust["name"], "renewal": True,
                "pipeline_status": "renewed", "appointment_id": booked_appt["_id"],
                "store": booked_appt["store_name"], "data_synced": True,
                "plan_end": (fresh_cust.get("plan") or {}).get("end")},
                store_id=cust.get("store_id"))
            return
        # calendar full - keep the renewal open with a follow-up, no booking made
        await db.customers.update_one({"_id": cust["_id"]}, {"$set": {
            "renewal_follow_up_at": (now + timedelta(days=1)).isoformat(),
            "renewal_skip_until": (now + timedelta(hours=6)).isoformat()}})
        await db.ai_calls.update_one({"_id": call["_id"]},
                                     {"$set": {"outcome": "interested", "stage_tag": "Renewal Offered",
                                               "intents": (call.get("intents") or [])
                                               + ["Calendar full - renewal follow-up scheduled"]}})
        return
    if outcome == "callback":
        fu = now + timedelta(minutes=90)
        await db.customers.update_one({"_id": cust["_id"]}, {"$set": {
            "renewal_follow_up_at": fu.isoformat(),
            "renewal_skip_until": fu.isoformat()}})
        await send_message(db, cust, "auto", "callback",
                           f"Thanks for talking to {store.get('name', 'us')}! We will call you back "
                           f"shortly about renewing your {service.get('name', 'service')} plan. "
                           f"Reply STOP to opt out.", call_id=call["_id"])
    elif outcome == "interested":
        await db.customers.update_one({"_id": cust["_id"]}, {"$set": {
            "renewal_follow_up_at": (now + timedelta(days=1)).isoformat(),
            "renewal_skip_until": (now + timedelta(days=1)).isoformat()}})
    elif outcome in ("voicemail", "no_answer"):
        await db.customers.update_one({"_id": cust["_id"]}, {"$set": {
            "renewal_skip_until": (now + timedelta(hours=6)).isoformat()}})
        await send_message(db, cust, "auto", "nudge",
                           f"Your {service.get('name', 'service')} plan at "
                           f"{store.get('name', 'our store')} has ended. Reply RENEW to restart "
                           f"it or STOP to opt out.", call_id=call["_id"])
    elif outcome == "not_interested":
        await db.customers.update_one({"_id": cust["_id"]}, {"$set": {
            "renewal_skip_until": (now + timedelta(days=30)).isoformat(),
            "renewal_declined_at": dbm.now_iso()}})
    elif outcome == "dnc":
        consent = cust.get("consent") or {}
        consent["call"] = False
        consent["whatsapp"] = False
        await db.customers.update_one({"_id": cust["_id"]},
                                      {"$set": {"consent": consent, "renewal_dnc": True}})
        await send_message(db, cust, "sms", "opt_out",
                           "As requested, we have removed your number from our calling list. "
                           "Sorry for the disturbance.", call_id=call["_id"])


async def _run_call(db, call: dict):
    """Drive one simulated call: transcript lines published live, then the outcome."""
    try:
        if call.get("kind") == "renewal":
            cust = await db.customers.find_one({"_id": call["customer_id"]})
            if not cust:
                await db.ai_calls.update_one({"_id": call["_id"]}, {"$set": {"outcome": "failed", "ended_at": dbm.now_iso()}})
                return
            lead = {**cust, "stage": "renewal", "score": call.get("score", 50)}
            outcome = _roll_renewal_outcome(cust)
        else:
            lead = await db.leads.find_one({"_id": call["lead_id"]})
            if not lead:
                await db.ai_calls.update_one({"_id": call["_id"]}, {"$set": {"outcome": "failed", "ended_at": dbm.now_iso()}})
                return
            outcome = _roll_outcome(lead)
        service, store = await _context(db, lead)
        offers = call.get("offers") or []
        # does the lead accept a discounted cross-store booking? (demo provider
        # behaviour: 80% take the offer; force_book demos always accept)
        accept_alt = bool(outcome == "booked" and call.get("overload") and offers
                          and (random.random() < 0.8 or lead.get("ai_force_accept")))
        if accept_alt:
            await db.ai_calls.update_one({"_id": call["_id"]}, {"$set": {"offer_accepted": True}})
        # intent understanding: the lead states a daypart on this call, and every
        # slot the AI quotes comes from REAL employee availability (capacity
        # engine: shifts, leaves, existing reservations) - preference first
        pref = random.choice(["morning", "morning", "evening", "evening", "afternoon"])
        pref_slots: list[tuple[str, int]] = []
        any_slots: list[tuple[str, int]] = []
        alt_slot = None
        if outcome == "booked" and accept_alt and offers:
            a_pref, a_any = await _open_slots(db, offers[0]["store_id"], service, pref, max_days=1)
            if a_pref or a_any:
                alt_slot = (a_pref or a_any)[0]
        elif outcome in ("booked", "renewed", "callback"):
            pref_slots, any_slots = await _open_slots(db, lead.get("store_id"), service, pref)
        # the slot the AI quotes is exactly the slot the booking will use
        chosen = alt_slot or (pref_slots or any_slots or [None])[0]
        candidates = list(pref_slots) + [s for s in any_slots if s not in pref_slots] \
            if not alt_slot else (list(a_pref) + [s for s in a_any if s not in a_pref])
        upd = {"slot_pref": pref}
        if chosen:
            upd.update({"slot_day": chosen[0], "slot_hour": chosen[1],
                        "slot_candidates": [list(s) for s in candidates[:6]]})
        await db.ai_calls.update_one({"_id": call["_id"]}, {"$set": upd})
        script = _script(lead, service, store, outcome, overload=bool(call.get("overload")),
                         offers=offers, accept_alt=accept_alt,
                         util=call.get("store_load_pct") or 0,
                         pref=pref, pref_slots=pref_slots, any_slots=any_slots,
                         alt_slot=alt_slot,
                         renewal={"expired_days": call.get("expired_days"),
                                  "plan_end": call.get("plan_end")}
                         if call.get("kind") == "renewal" else None)
        for who, text, tag in script:
            await asyncio.sleep(LINE_SECONDS)
            fresh = await db.ai_calls.find_one({"_id": call["_id"]})
            if not fresh or fresh.get("outcome") != "in_progress":
                return  # ended or already finished by an admin action
            if fresh.get("handed"):
                await _finish_call(db, fresh, "handed_over", "negative",
                                   ["Escalation detected", "Manager request"])
                return
            line = {"who": who, "text": text, "tag": tag,
                    "at": dbm.now_iso()}
            await db.ai_calls.update_one({"_id": call["_id"]},
                                         {"$push": {"transcript": line},
                                          "$set": {"stage_tag": tag}})
            await publish(db, "ai_call.line", {"call_id": call["_id"], "who": who,
                                               "text": text, "tag": tag},
                          store_id=call.get("store_id"))
        await asyncio.sleep(LINE_SECONDS)
        fresh = await db.ai_calls.find_one({"_id": call["_id"]})
        if not fresh or fresh.get("outcome") != "in_progress":
            return
        if fresh.get("handed"):
            intents = ["Escalation detected", "Manager request"]
            await _finish_call(db, fresh, "handed_over", "negative", intents)
            return
        intents = {
            "booked": ["Price enquiry - handled", "Appointment intent", "Slot confirmed"],
            "renewed": ["Renewal offer shared", "Appointment intent", "Slot confirmed"],
            "callback": ["Callback requested", "Timing concern"],
            "interested": ["Interest confirmed", "Price shared"],
            "not_interested": ["Not interested", "Polite close"],
            "voicemail": ["Voicemail detected"],
            "no_answer": ["No response"],
            "dnc": ["Opt-out request"],
        }[outcome]
        if call.get("kind") == "renewal":
            dp = (fresh.get("slot_pref") or "slot").title()
            base = [f"Plan expired {call.get('expired_days')}d - renewal follow-up",
                    "Renewal offer shared", f"{dp} preference honoured"]
            intents = base + {
                "renewed": ["Renewal booked from live availability", "30-day plan restarted"],
                "callback": ["Callback requested"],
                "interested": ["Will confirm later"],
                "not_interested": ["Renewal declined", "Polite close"],
                "voicemail": ["Voicemail - renewal nudge sent"],
                "no_answer": ["No response"],
                "dnc": ["Opt-out request"],
            }[outcome]
        elif outcome == "booked":
            dp = (fresh.get("slot_pref") or "slot").title()
            if fresh.get("offer_accepted") and offers:
                o = offers[0]
                intents = ["Price enquiry - handled",
                           f"Overload detected ({call.get('store_load_pct', 0)}% loaded)",
                           f"Cross-store offer accepted - {o['pct']}% off at {o['store_name']}",
                           f"{dp} preference honoured", "Appointment intent",
                           "Slot confirmed from live availability"]
            else:
                intents = ["Price enquiry - handled", f"{dp} slot preference understood",
                           "Appointment intent", "Slot confirmed from live availability"]
        sentiment = {"booked": "positive", "renewed": "positive", "callback": "positive",
                     "interested": "positive", "not_interested": "neutral",
                     "voicemail": "neutral", "no_answer": "neutral", "dnc": "negative"}[outcome]
        await _finish_call(db, fresh, outcome, sentiment, intents)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[ai_calls] call {call['_id']} crashed: {type(e).__name__}: {e}")
        await db.ai_calls.update_one({"_id": call["_id"]},
                                     {"$set": {"outcome": "failed", "ended_at": dbm.now_iso()}})


_tasks: set = set()


def _spawn(coro):
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


# ------------------------------------------------------------------ admin actions
async def call_action(db, user, call_id: str, action: str) -> dict:
    call = await db.ai_calls.find_one({"_id": call_id})
    if not call:
        raise ValueError("Call not found")
    if call.get("outcome") != "in_progress" and action in ("takeover", "end"):
        raise ValueError("This call has already finished")
    if action == "takeover":
        await db.ai_calls.update_one({"_id": call_id}, {"$set": {"handed": True}})
        lead = await db.leads.find_one({"_id": call["lead_id"]})
        if lead:
            hist = lead.get("stage_history") or []
            hist.append({"stage": lead["stage"], "at": dbm.now_iso(),
                         "note": "Human took over from AI mid-call"})
            await db.leads.update_one({"_id": lead["_id"]},
                                      {"$set": {"stage_history": hist,
                                                "owner_id": user.get("_id"),
                                                "last_contact_at": dbm.now_iso()}})
        await publish(db, "ai_call.outcome", {"call_id": call_id, "person": call["person_name"],
                                              "outcome": "taken_over", "store": call["store"]})
        return {"message": f"You are now on the call with {call['person_name']} - the AI stepped back"}
    if action == "end":
        fresh = await db.ai_calls.find_one({"_id": call_id})
        await _finish_call(db, fresh, "ended_by_admin", "neutral", ["Ended by admin"])
        return {"message": "Call ended"}
    # listen / whisper are monitor-only actions
    await publish(db, "ai_call.line", {"call_id": call_id, "who": "admin",
                                       "text": "(admin " + action + ")", "tag": "Monitor"},
                  store_id=call.get("store_id"))
    return {"message": {"listen": "You are listening in (silent)",
                        "whisper": "Tip whispered to the AI"}[action]}


async def queue_action(db, user, lead_id: str, action: str) -> dict:
    if lead_id == "all" and action == "skip":
        # push every dialable lead back 1 hour (queue housekeeping / demo hook);
        # when nothing is left dialable the engine moves on to renewal follow-ups
        leads = await db.leads.find({"org_id": config.ORG_ID, "archived": {"$ne": True},
                                     "stage": {"$nin": ["booked", "lost"]}})
        until = (_now() + timedelta(hours=1)).isoformat()
        n = 0
        for l in leads:
            if l.get("ai_dnc") or (l.get("consent") or {}).get("call") is False:
                continue
            await db.leads.update_one({"_id": l["_id"]}, {"$set": {"ai_skip_until": until}})
            n += 1
        return {"message": f"{n} dialable leads pushed back by 1 hour"}
    if lead_id.startswith("cus_"):
        cust = await db.customers.find_one({"_id": lead_id})
        if not cust:
            raise ValueError("Customer not found")
        if action == "skip":
            until = (_now() + timedelta(hours=1)).isoformat()
            await db.customers.update_one({"_id": lead_id},
                                          {"$set": {"renewal_skip_until": until}})
            return {"message": f"{cust['name']} renewal pushed back by 1 hour"}
        if action == "force_book":
            # demo provider hook: the next renewal call to this customer ends in a
            # confirmed renewal (30-day plan restarted at a real available slot)
            await db.customers.update_one({"_id": lead_id},
                                          {"$set": {"ai_force_outcome": "booked"},
                                           "$unset": {"renewal_skip_until": ""}})
            return {"message": f"{cust['name']} pinned - the next renewal call will renew the plan"}
        raise ValueError("Unknown action")
    lead = await db.leads.find_one({"_id": lead_id})
    if action == "skip":
        until = (_now() + timedelta(hours=1)).isoformat()
        await db.leads.update_one({"_id": lead_id}, {"$set": {"ai_skip_until": until}})
        return {"message": f"{lead['name']} pushed back by 1 hour"}
    if action == "defer":
        fu = (_now() + timedelta(days=1)).isoformat()
        await db.leads.update_one({"_id": lead_id}, {"$set": {"follow_up_at": fu}})
        return {"message": f"{lead['name']} deferred to tomorrow"}
    if action == "force_book":
        # demo provider hook: the next call to this lead ends in a confirmed
        # booking (and accepts a cross-store offer if one is on the table);
        # pinning also lifts any skip so the lead really is dialled next
        await db.leads.update_one({"_id": lead_id},
                                  {"$set": {"ai_force_outcome": "booked", "ai_force_accept": True},
                                   "$unset": {"ai_skip_until": ""}})
        return {"message": f"{lead['name']} pinned - the next call will end in a booking"}
    raise ValueError("Unknown action")


# ------------------------------------------------------------------ overview payload
CONNECT_OUTCOMES = ("booked", "renewed", "callback", "interested", "not_interested")


async def overview(db) -> dict:
    state = await get_state(db)
    today = _today()
    todays = await db.ai_calls.find({"org_id": config.ORG_ID, "date": today})
    done = [c for c in todays if c["outcome"] != "in_progress"]
    live = next((c for c in todays if c["outcome"] == "in_progress"), None)
    attempted = len(todays)
    connects = len([c for c in done if c["outcome"] in CONNECT_OUTCOMES])
    booked = len([c for c in done if c["outcome"] == "booked"])
    handoffs = len([c for c in done if c["outcome"] == "handed_over"])
    spent = sum(c.get("cost", 0) for c in done)

    def pct(a, b):
        return round(100 * a / b) if b else 0

    # hot coverage: of all open hot leads in the pipeline, how many has the AI dialed
    # today (first contact included)? Pipeline-wide so the KPI is meaningful on day one.
    open_now = await db.leads.find({"org_id": config.ORG_ID, "archived": {"$ne": True},
                                    "stage": {"$nin": ["booked", "lost"]}})
    hot_total = len([l for l in open_now if l.get("score", 0) >= 80])
    hot_reached = len({c["lead_id"] for c in todays if c["tier"] == "hot"})
    voicemail = len([c for c in done if c["outcome"] == "voicemail"])
    no_answer = len([c for c in done if c["outcome"] == "no_answer"])
    dnc = len([c for c in done if c["outcome"] == "dnc"])
    not_int = len([c for c in done if c["outcome"] == "not_interested"])
    durations = [(_parse(c.get("ended_at")) and _parse(c.get("started_at"))
                  and (_parse(c["ended_at"]) - _parse(c["started_at"])).total_seconds()) or 0
                 for c in done]
    avg_dur = int(sum(durations) / len(durations)) if durations else 0

    others = len([c for c in done if c["outcome"] in ("handed_over", "ended_by_admin", "failed")])
    renewed = len([c for c in done if c["outcome"] == "renewed"])
    renewal_calls = len([c for c in done if c.get("kind") == "renewal"])
    kpis = {
        "attempted": attempted, "cap": state["daily_lead_cap"],
        "connect_rate": pct(connects, attempted), "connects": connects,
        "booked": booked, "renewed": renewed, "renewal_calls": renewal_calls,
        "hot_total": hot_total, "hot_reached": hot_reached,
        "hot_pct": pct(hot_reached, hot_total),
        "cost": spent, "budget": state["daily_budget"],
        "cost_per_connect": round(spent / connects, 1) if connects else 0,
        "handoffs": handoffs, "avg_duration": avg_dur,
        "rerouted": len([c for c in done if c.get("rerouted")]),
    }
    funnel = {"dialed": attempted, "connected": connects,
              "interested": len([c for c in done if c["outcome"] in ("booked", "renewed", "callback", "interested")]),
              "offered": booked + renewed, "booked": booked + renewed}

    # next-up queue (what the AI will dial, and why) - each entry carries the
    # lead's service, its store location and tomorrow's load so overload offers
    # are visible before the phone even rings
    leads = await db.leads.find({"org_id": config.ORG_ID, "archived": {"$ne": True},
                                 "stage": {"$nin": ["booked", "lost"]}})
    prios = await priority.compute_priority_many(db, leads)
    per_lead: dict[str, int] = {}
    recent: dict[str, datetime] = {}
    for c in todays:
        per_lead[c["lead_id"]] = per_lead.get(c["lead_id"], 0) + 1
        st0 = _parse(c.get("started_at"))
        if st0:
            recent[c["lead_id"]] = max(recent.get(c["lead_id"], st0), st0)
    tier_rank = {"hot": 0, "warm": 1, "low": 2}
    ranked = sorted(leads, key=lambda l: (tier_rank[prios[l["_id"]]["tier"]],
                                          0 if _reason(l, prios[l["_id"]]).startswith("SLA breached") else 1,
                                          -l.get("score", 0), l.get("created_at") or ""))
    stores_list = await db.stores.find({"org_id": config.ORG_ID})
    stores = {s["_id"]: s["name"] for s in stores_list}
    store_areas = {s["_id"]: s.get("area") for s in stores_list}
    svc_docs = await db.services.find({"org_id": config.ORG_ID})
    services = {s["_id"]: s["name"] for s in svc_docs}
    day = (_ist().date() + timedelta(days=1)).isoformat()
    org = await rules.org_doc(db)
    threshold = rules.rule_value(org, "BR-06", 90)
    util_by_store = {s["_id"]: (await capacity.store_day_utilization(db, s["_id"], day))["utilization_pct"]
                     for s in stores_list}
    # one capacity context for all stores tomorrow - overload flag matches the
    # call-time plan: load at/above threshold, service not offered, or no free slot
    ctx_day = await capacity.load_day_ctx(db, [s["_id"] for s in stores_list], day)
    queue = []
    for l in ranked[:12]:
        if l.get("ai_dnc") or (l.get("consent") or {}).get("call") is False:
            continue
        if per_lead.get(l["_id"], 0) >= MAX_CALLS_PER_LEAD_DAY:
            continue
        # mirror _pick_lead's retry rules so the queue never shows someone the
        # engine will not actually dial (skip pushed / called moments ago)
        skip = _parse(l.get("ai_skip_until"))
        if skip and skip > _now():
            continue
        last = recent.get(l["_id"])
        if last and (_now() - last).total_seconds() < CALL_GAP_MIN * 60:
            continue
        p = prios[l["_id"]]
        sid, svc_doc = l.get("store_id"), svc_docs and next((s for s in svc_docs if s["_id"] == l.get("service_id")), None)
        load = util_by_store.get(sid, 0)
        overload = False
        if sid and svc_doc:
            offered = capacity.store_offers(ctx_day, sid, svc_doc)
            overload = (not offered) or load >= threshold or not _placeable(ctx_day, sid, svc_doc)
        queue.append({
            "lead_id": l["_id"], "name": l["name"], "tier": p["tier"], "score": l.get("score", 0),
            "stage": l["stage"], "service": services.get(l.get("service_id"), "-"),
            "service_id": l.get("service_id"),
            "store": stores.get(sid, "-"), "store_id": sid,
            "location": store_areas.get(sid, "-"),
            "store_load_pct": load,
            "overload_expected": overload,
            "attempts_today": per_lead.get(l["_id"], 0),
            "reason": _reason(l, p),
        })
        if len(queue) >= 6:
            break

    # no dialable lead left: the engine falls back to renewal follow-ups for
    # expired-plan customers - show exactly who it will call and why
    if not queue:
        for r in (await _renewal_candidates(db))[:6]:
            c = r["customer"]
            sid = c.get("store_id")
            queue.append({
                "lead_id": c["_id"], "renewal": True, "name": c["name"],
                "tier": "hot" if c.get("segment") == "vip" else
                        "warm" if c.get("visits", 0) >= 5 else "low",
                "score": min(99, 50 + c.get("visits", 0) * 3), "stage": "renewal",
                "service": services.get(c.get("last_service_id"), "-"),
                "service_id": c.get("last_service_id"),
                "store": stores.get(sid, "-"), "store_id": sid,
                "location": store_areas.get(sid, "-"),
                "store_load_pct": util_by_store.get(sid, 0),
                "overload_expected": False, "attempts_today": 0,
                "reason": f"Plan expired {r['expired_days']}d ago - renewal follow-up",
            })

    recent = sorted(done, key=lambda c: c.get("started_at") or "", reverse=True)[:8]
    live_full = None
    if live:
        live_full = await db.ai_calls.find_one({"_id": live["_id"]})

    msgs = await db.ai_messages.find({}, sort=[("at", -1)], limit=14)
    msg_stats = {
        "whatsapp_today": await db.ai_messages.count_documents(
            {"channel": "whatsapp", "at": {"$gte": _today_start()}}),
        "sms_today": await db.ai_messages.count_documents(
            {"channel": "sms", "at": {"$gte": _today_start()}}),
        "replies_today": await db.ai_messages.count_documents(
            {"status": "replied", "at": {"$gte": _today_start()}}),
    }
    retry_waiting = len([l for l in leads if 0 < per_lead.get(l["_id"], 0) < MAX_CALLS_PER_LEAD_DAY])
    return {
        "shift": {k: state.get(k) for k in
                  ("enabled", "voice", "language", "window_start", "window_end",
                   "daily_budget", "daily_lead_cap", "speed", "script", "max_attempts")},
        "window_ok": in_window(state),
        "ist_time": _ist().strftime("%H:%M"),
        "kpis": kpis, "funnel": funnel,
        "breakdown": {"voicemail": voicemail, "no_answer": no_answer, "dnc": dnc,
                      "not_interested": not_int, "handoffs": handoffs, "other": others},
        "live": live_full, "queue": queue, "recent_calls": recent,
        "recent_messages": msgs, "msg_stats": msg_stats,
        "retry_waiting": retry_waiting,
        "sync": await sync.last(db),
    }


# ------------------------------------------------------------------ engine loop
async def engine_loop(db):
    """The AI shift worker: starts calls, advances message statuses, sends reminders."""
    while True:
        try:
            await asyncio.sleep(TICK_SECONDS)
            await _advance_messages(db)
            await _dispatch_reminders(db)
            state = await get_state(db)
            if not state["enabled"] or not in_window(state):
                continue
            todays = await db.ai_calls.count_documents({"org_id": config.ORG_ID, "date": _today()})
            if todays >= state["daily_lead_cap"]:
                continue
            done = await db.ai_calls.find({"org_id": config.ORG_ID, "date": _today()})
            spent = sum(c.get("cost", 0) for c in done if c["outcome"] != "in_progress")
            if spent >= state["daily_budget"]:
                continue
            live = await db.ai_calls.count_documents(
                {"org_id": config.ORG_ID, "date": _today(), "outcome": "in_progress"})
            if live:
                continue
            call = await start_call(db)
            if call:
                _spawn(_run_call(db, call))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
