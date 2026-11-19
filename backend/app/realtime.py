"""Realtime gateway (PRD RT-01..RT-03): in-process event bus + SSE stream + demo simulator.

In production the outbox worker publishes to Redis pub/sub and this module forwards to
subscribers; the interface is identical (publish(db, type, payload)).
"""
import asyncio
import json
import random
from datetime import datetime, timezone

from . import config, db as dbm

_subs: dict[str, list[asyncio.Queue]] = {}
_sim_task = None


def subscribe(key: str) -> asyncio.Queue:
    q = asyncio.Queue(maxsize=200)
    _subs.setdefault(key, []).append(q)
    return q


def unsubscribe(key: str, q: asyncio.Queue):
    if key in _subs and q in _subs[key]:
        _subs[key].remove(q)


def _store_keys(store_ids):
    return ["all"] + [f"store:{s}" for s in (store_ids or [])]


async def publish(db, event_type: str, payload: dict, store_id: str | None = None):
    event = {"type": event_type, "payload": payload,
             "at": datetime.now(timezone.utc).isoformat()}
    try:
        await db.events.insert_one({"_id": dbm.new_id("evt"), "org_id": config.ORG_ID,
                                    "type": event_type, "payload": payload, "at": event["at"]})
    except Exception:
        pass
    for key in _store_keys([store_id] if store_id else []):
        for q in _subs.get(key, []):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


def format_sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"


# ------------------------------------------------------------------ demo simulator
FIRST = ["Riya", "Aman", "Neha", "Kabir", "Isha", "Arjun", "Priya", "Rahul", "Sneha", "Vikram",
         "Ananya", "Rohit", "Meera", "Dev", "Tara", "Yash", "Divya", "Karan", "Pooja", "Sameer"]
LAST = ["Sharma", "Verma", "Patel", "Joshi", "Rane", "Dixit", "Nair", "Kulkarni", "Bose", "Chauhan"]


async def start_simulator(db):
    """DEMO_REALTIME=1: one event every ~5 s (prototype behaviour). Sometimes it creates a
    real lead (visible in DB and pipeline); other events are feed-only."""
    global _sim_task
    if _sim_task is None:
        _sim_task = asyncio.create_task(_sim_loop(db))


async def _sim_loop(db):
    from . import capacity, rules, scoring
    org = await rules.org_doc(db)
    stores = await db.stores.find({"org_id": config.ORG_ID})
    services = await db.services.find({"org_id": config.ORG_ID})
    sources = list(config.SOURCES)
    n = random.randint(1, 9_000)
    while True:
        await asyncio.sleep(5)
        try:
            roll = random.random()
            if roll < 0.35 and stores and services:
                store = random.choice(stores)
                service = random.choice(services)
                name = f"{random.choice(FIRST)} {random.choice(LAST)}"
                phone = f"9{random.randint(100000000, 999999999)}"
                lead = {
                    "_id": dbm.new_id("led"), "org_id": config.ORG_ID, "name": name,
                    "phone_norm": phone, "phone_display": phone,
                    "age_group": random.choice(["18-25", "26-35", "36-45", "45+"]),
                    "gender": random.choice(["Female", "Male"]),
                    "store_id": store["_id"], "service_id": service["_id"],
                    "source": random.choice(sources), "stage": "new", "score": 0,
                    "score_factors": [], "follow_up_at": None, "offered_at": None,
                    "lost_reason": None, "owner_id": None, "consent": {"whatsapp": True},
                    "customer_id": None, "created_at": datetime.now(timezone.utc).isoformat(),
                    "last_contact_at": None, "notes": "Auto-ingested from portal (demo)",
                }
                score, factors = scoring.score_lead(db, lead, org)
                lead["score"], lead["score_factors"] = score, factors
                try:
                    await db.leads.insert_one(lead)
                    await db.interactions.insert_one({
                        "_id": dbm.new_id("int"), "org_id": config.ORG_ID,
                        "person_id": lead["_id"], "person_name": name, "channel": "system",
                        "direction": "in", "content": f"Lead received from {config.SOURCE_LABELS.get(lead['source'], lead['source'])}",
                        "outcome": "lead_created", "at": datetime.now(timezone.utc).isoformat()})
                    await publish(db, "lead.created",
                                  {"lead_id": lead["_id"], "name": name,
                                   "service": service["name"], "store": store["name"],
                                   "score": score, "source": config.SOURCE_LABELS.get(lead["source"], lead["source"]),
                                   "store_id": store["_id"]}, store_id=store["_id"])
                except dbm.DuplicateKeyError:
                    pass
            elif roll < 0.55:
                await publish(db, "reply.received", {"text": random.choice(
                    ["Yes, what times are free tomorrow?", "What is the price?",
                     "Can you share the location?", "Interested, please call."])}, store_id=None)
            elif roll < 0.7:
                appts = await db.appointments.find({"status": "booked"}, limit=5)
                if appts:
                    a = random.choice(appts)
                    await publish(db, "followup.due", {"lead_id": a.get("lead_id"),
                                                       "person": a["person"]["name"],
                                                       "store_id": a["store_id"]})
            elif roll < 0.85:
                wls = await db.waitlist.find({"status": "waiting"}, limit=3)
                if wls:
                    w = random.choice(wls)
                    await publish(db, "waitlist.slots_open", {"waitlist_id": w["_id"],
                                                              "store_id": w["store_id"]})
            else:
                await publish(db, "campaign.reply", {"campaign": "Hot Lead Offer",
                                                     "text": "Booked my slot, thanks!"})
        except Exception:
            pass


async def ensure_indexes_ok():
    pass
