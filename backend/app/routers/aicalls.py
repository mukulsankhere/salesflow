"""AI Calling + automatic messaging endpoints (admin-only section).

- GET  /ai-calls/state      one polled payload: shift, KPIs, funnel, live call,
                            queue, recent calls/messages (the dashboard's data)
- POST /ai-calls/shift      toggle the auto-calling shift on/off
- POST /ai-calls/settings   guardrails: window, budget, caps, voice
- POST /ai-calls/calls/{id}/action    takeover | end | listen | whisper
- POST /ai-calls/queue/{lead_id}/action  skip | defer
- GET  /ai-calls/calls/{id} full transcript for the transcript modal
- GET  /ai-calls/messages   WhatsApp/SMS log with channel/kind filters
- POST /ai-calls/messages/test        send a test message (demo of the provider)
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import ai_calls, config, sync
from ..deps import err, require_section
from ..db import Database, get_db
from ..realtime import publish

router = APIRouter(prefix="/api/v1")


class ShiftIn(BaseModel):
    enabled: bool


class SettingsIn(BaseModel):
    window_start: int | None = None
    window_end: int | None = None
    daily_budget: int | None = None
    daily_lead_cap: int | None = None
    voice: str | None = None
    language: str | None = None
    speed: str | None = None
    script: str | None = None
    max_attempts: int | None = None


class CallActionIn(BaseModel):
    action: str  # takeover | end | listen | whisper


class QueueActionIn(BaseModel):
    action: str  # skip | defer


class TestMessageIn(BaseModel):
    channel: str  # whatsapp | sms | auto
    phone: str | None = None


@router.get("/ai-calls/state")
async def ai_state(flt: dict = Depends(require_section("aicalls")), db: Database = Depends(get_db)):
    return await ai_calls.overview(db)


@router.post("/ai-calls/shift")
async def ai_shift(body: ShiftIn, flt: dict = Depends(require_section("aicalls")),
                   db: Database = Depends(get_db)):
    state = await ai_calls.set_state(db, flt["user"], {"enabled": body.enabled})
    await publish(db, "ai_call.shift", {"enabled": state["enabled"], "by": flt["user"]["name"]})
    return {"shift": {k: state.get(k) for k in ("enabled", "window_start", "window_end")},
            "message": ("Auto-calling shift started - Aarav is on the queue"
                        if body.enabled else "Shift paused - the queue is frozen")}


@router.post("/ai-calls/settings")
async def ai_settings(body: SettingsIn, flt: dict = Depends(require_section("aicalls")),
                      db: Database = Depends(get_db)):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "window_start" in patch or "window_end" in patch:
        state = await ai_calls.get_state(db)
        ws = patch.get("window_start", state["window_start"])
        we = patch.get("window_end", state["window_end"])
        if not (0 <= ws < we <= 21):
            raise err("VALIDATION_ERROR", "Calling window must stay inside 00:00-21:00 (TRAI-safe)")
    if "daily_budget" in patch and not 50 <= patch["daily_budget"] <= 10000:
        raise err("VALIDATION_ERROR", "Budget must be between ₹50 and ₹10,000")
    if "daily_lead_cap" in patch and not 5 <= patch["daily_lead_cap"] <= 500:
        raise err("VALIDATION_ERROR", "Lead cap must be between 5 and 500")
    state = await ai_calls.set_state(db, flt["user"], patch)
    return {"shift": {k: state.get(k) for k in
                      ("enabled", "voice", "language", "window_start", "window_end",
                       "daily_budget", "daily_lead_cap", "speed", "script", "max_attempts")},
            "message": "Guardrails updated"}


@router.post("/ai-calls/calls/{call_id}/action")
async def ai_call_action(call_id: str, body: CallActionIn,
                         flt: dict = Depends(require_section("aicalls")),
                         db: Database = Depends(get_db)):
    try:
        return await ai_calls.call_action(db, flt["user"], call_id, body.action)
    except ValueError as e:
        raise err("NOT_FOUND" if "not found" in str(e) else "INVALID_ACTION", str(e), 404 if "not found" in str(e) else 400)


@router.post("/ai-calls/queue/{lead_id}/action")
async def ai_queue_action(lead_id: str, body: QueueActionIn,
                          flt: dict = Depends(require_section("aicalls")),
                          db: Database = Depends(get_db)):
    try:
        return await ai_calls.queue_action(db, flt["user"], lead_id, body.action)
    except ValueError as e:
        raise err("NOT_FOUND", str(e), 404)


@router.get("/ai-calls/calls/{call_id}")
async def ai_call_detail(call_id: str, flt: dict = Depends(require_section("aicalls")),
                         db: Database = Depends(get_db)):
    call = await db.ai_calls.find_one({"_id": call_id})
    if not call:
        raise err("NOT_FOUND", "Call not found", 404)
    return {"call": call}


@router.get("/ai-calls/messages")
async def ai_messages(channel: str | None = None, kind: str | None = None, limit: int = 50,
                      flt: dict = Depends(require_section("aicalls")), db: Database = Depends(get_db)):
    q = {"org_id": config.ORG_ID}
    if channel and channel != "all":
        q["channel"] = channel
    if kind and kind != "all":
        q["kind"] = kind
    msgs = await db.ai_messages.find(q, sort=[("at", -1)], limit=max(1, min(limit, 200)))
    return {"messages": msgs,
            "kinds": sorted({m.get("kind", "other") for m in msgs})}


@router.post("/ai-calls/sync/run")
async def sync_run(flt: dict = Depends(require_section("aicalls")), db: Database = Depends(get_db)):
    """Manual data-consistency sweep (also runs automatically after every
    booking / completion / cancellation)."""
    res = await sync.verify(db, reason="manual check")
    return {"sync": res,
            "message": f"Data verified - {res['checked'].get('appointments', 0)} appointments and "
                       f"{res['checked'].get('leads', 0)} leads match, {res['repaired']} auto-repair(s)"}


@router.post("/ai-calls/messages/test")
async def ai_test_message(body: TestMessageIn, flt: dict = Depends(require_section("aicalls")),
                          db: Database = Depends(get_db)):
    admin = flt["user"]
    person = {"_id": admin["_id"], "name": admin["name"],
              "consent": {"whatsapp": True, "call": True},
              "store_id": None}
    text = (f"SalesFlow test - this is how your automatic {body.channel.replace('auto', 'smart')} "
            f"message looks. Sent by Aarav (AI) at {ai_calls._ist().strftime('%H:%M')} IST.")
    msg = await ai_calls.send_message(db, person, body.channel or "auto", "test", text)
    return {"message": msg, "text": f"Test {msg['channel'].upper()} sent to {admin['name']}"}
