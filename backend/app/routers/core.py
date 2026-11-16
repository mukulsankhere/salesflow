"""Core: auth (AUTH-01), org + rules editor (AUTH-04/ADM-01), stores, services,
pricing (PROD-02), audit (AUTH-05), integration health (ADM-03)."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .. import capacity, config, db as dbm, rules, security
from ..deps import audit, current_user, err, require_section
from ..db import Database, get_db

router = APIRouter(prefix="/api/v1")


class LoginIn(BaseModel):
    email_or_phone: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class RulesIn(BaseModel):
    rule_id: str
    value: object


@router.post("/auth/login")
async def login(body: LoginIn, db: Database = Depends(get_db)):
    key = body.email_or_phone.strip().lower()
    user = await db.users.find_one({"org_id": config.ORG_ID, "email": key})
    if not user:
        user = await db.users.find_one({"org_id": config.ORG_ID, "phone": key})
    if not user or not security.verify_password(body.password, user.get("password_hash", "")):
        raise err("INVALID_CREDENTIALS", "Wrong email/phone or password", 401)
    access = security.create_access_token(user)
    refresh = security.create_refresh_token(user)
    user.pop("password_hash", None)
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login": dbm.now_iso()}})
    return {"access_token": access, "refresh_token": refresh,
            "token_type": "bearer", "user": user}


@router.post("/auth/refresh")
async def refresh(body: RefreshIn, db: Database = Depends(get_db)):
    try:
        payload = security.decode_token(body.refresh_token)
        if payload.get("typ") != "refresh":
            raise ValueError
    except Exception:
        raise err("INVALID_TOKEN", "Refresh token invalid or expired", 401)
    user = await db.users.find_one({"_id": payload["sub"]})
    if not user:
        raise err("INVALID_TOKEN", "User not found", 401)
    return {"access_token": security.create_access_token(user),
            "refresh_token": security.create_refresh_token(user), "token_type": "bearer"}


@router.get("/auth/me")
async def me(user: dict = Depends(current_user), db: Database = Depends(get_db)):
    stores = await db.stores.find({"org_id": config.ORG_ID})
    user = dict(user)
    user.pop("password_hash", None)
    return {"user": user, "stores": stores,
            "permissions": [s for s, roles in config.PERMISSIONS.items() if user["roles"][0] in roles]}


@router.post("/auth/logout")
async def logout(user: dict = Depends(current_user)):
    return {"ok": True}


# ------------------------------------------------------------------ org + rules
@router.get("/org/settings")
async def org_settings(db: Database = Depends(get_db),
                       flt: dict = Depends(require_section("dashboard")), user: dict = Depends(current_user)):
    org = await rules.org_doc(db)
    return {"org": {k: v for k, v in org.items() if k != "rule_overrides"},
            "rules": rules.get_rules(org)}


@router.put("/org/rules")
async def put_rule(body: RulesIn, db: Database = Depends(get_db),
                   flt: dict = Depends(require_section("settings")), user: dict = Depends(current_user)):
    org = await rules.org_doc(db)
    if body.rule_id not in rules.get_rules(org):
        raise err("VALIDATION_ERROR", "Unknown rule id")
    before = (org.get("rule_overrides") or {}).get(body.rule_id)
    overrides = dict(org.get("rule_overrides") or {})
    overrides[body.rule_id] = body.value
    hist = org.get("rule_history") or []
    hist.append({"rule_id": body.rule_id, "value": body.value, "at": dbm.now_iso(),
                 "by": user["name"]})
    await db.organizations.update_one({"_id": org["_id"]},
                                      {"$set": {"rule_overrides": overrides, "rule_history": hist}})
    await audit(db, user, "rule.change", "organization", {body.rule_id: before},
                {body.rule_id: body.value}, entity_id=org["_id"])
    return {"ok": True, "rules": rules.get_rules(await rules.org_doc(db))}


@router.get("/org/rules/history")
async def rule_history(db: Database = Depends(get_db),
                       flt: dict = Depends(require_section("settings")), user: dict = Depends(current_user)):
    org = await rules.org_doc(db)
    return {"history": org.get("rule_history") or []}


# ------------------------------------------------------------------ stores & services
@router.get("/stores")
async def stores(db: Database = Depends(get_db), user: dict = Depends(current_user)):
    stores = await db.stores.find({"org_id": config.ORG_ID, "status": "active"})
    dists = await db.store_distances.find({"org_id": config.ORG_ID})
    today = dbm.now_iso()[:10]
    out = []
    for s in stores:
        u = await capacity.store_day_utilization(db, s["_id"], today)
        out.append({**s, "utilization_today": u})
    return {"stores": out, "distances": dists}


@router.get("/stores/distances")
async def store_distances(db: Database = Depends(get_db), user: dict = Depends(current_user)):
    return {"distances": await db.store_distances.find({"org_id": config.ORG_ID})}


@router.get("/services")
async def services(db: Database = Depends(get_db), user: dict = Depends(current_user)):
    services = await db.services.find({"org_id": config.ORG_ID})
    stores = await db.stores.find({"org_id": config.ORG_ID, "status": "active"})
    today = dbm.now_iso()[:10]
    out = []
    for svc in services:
        offered_at, trained = [], {}
        for s in stores:
            ctx = await capacity.load_day_ctx(db, [s["_id"]], today)
            n = capacity.trained_count(ctx, s["_id"], svc["_id"])
            trained[s["_id"]] = n
            if capacity.store_offers(ctx, s["_id"], svc):
                offered_at.append(s["_id"])
        out.append({**svc, "stores_offering": offered_at, "trained_count": trained,
                    "stores": [{"id": s["_id"], "name": s["name"]} for s in stores]})
    return {"services": out}


@router.get("/services/{service_id}/price")
async def service_price(service_id: str, store_id: str, far: bool = False,
                        db: Database = Depends(get_db), user: dict = Depends(current_user)):
    svc = await db.services.find_one({"_id": service_id})
    if not svc:
        raise err("NOT_FOUND", "Service not found", 404)
    dists = await capacity.distances_from(db, store_id)
    nearest_other = min([v for k, v in dists.items() if k != store_id] or [0])
    km = nearest_other if far else 0
    org = await rules.org_doc(db)
    return {"price": rules.price_breakup(svc, km if far else None, org)}


# ------------------------------------------------------------------ audit & integrations
@router.get("/audit")
async def audit_log(limit: int = 100, db: Database = Depends(get_db),
                    flt: dict = Depends(require_section("audit")), user: dict = Depends(current_user)):
    rows = await db.audit_log.find({"org_id": config.ORG_ID},
                                   sort=[("at", -1)], limit=min(limit, 300))
    return {"audit": rows}


@router.get("/integrations/health")
async def integrations(db: Database = Depends(get_db),
                       flt: dict = Depends(require_section("settings")), user: dict = Depends(current_user)):
    return {"integrations": [
        {"name": "WhatsApp Business Cloud API", "kind": "whatsapp", "status": "demo",
         "note": "Provider interface ready; set credentials in Settings to go live."},
        {"name": "SMS Gateway (DLT)", "kind": "sms", "status": "demo", "note": "DLT-registered template IDs pending."},
        {"name": "Voice / Click-to-call", "kind": "voice", "status": "demo", "note": "Telephony API pending."},
        {"name": "Payment Gateway (Razorpay)", "kind": "payments", "status": "demo", "note": "Payment links recorded; live keys pending."},
        {"name": "Lead Portals (Google/Meta/Justdial)", "kind": "portals", "status": "demo",
         "note": "Webhook endpoints under /api/v1/ingest/{portal}; pollers configured."},
        {"name": "MongoDB", "kind": "database", "status": "live" if db.is_mongo else "fallback",
         "note": "In-process document store active (set MONGO_URI for MongoDB)."},
        {"name": "Redis", "kind": "queue", "status": "demo", "note": "In-process queue active (set REDIS_URL in production)."},
    ]}


# ------------------------------------------------------------------ portal ingest
class IngestIn(BaseModel):
    name: str
    phone: str
    service: str | None = None
    area: str | None = None
    source_ref: str | None = None


@router.post("/ingest/{portal}")
async def ingest(portal: str, body: IngestIn, db: Database = Depends(get_db)):
    """LEAD-01: portal webhook intake - map to a standard lead, validate, dedupe (LEAD-02/03)."""
    if len(body.phone.strip().replace(" ", "").isdigit()) != 10:
        return {"accepted": False, "reason": "phone must be 10 digits"}
    phone_norm = body.phone.strip()[-10:]
    if await db.identities.find_one({"org_id": config.ORG_ID, "phone_norm": phone_norm}):
        return {"accepted": False, "reason": "duplicate phone (existing lead/customer)", "code": "PHONE_EXISTS"}
    service = None
    if body.service:
        service = await db.services.find_one({"org_id": config.ORG_ID,
                                              "name": {"$regex": body.service.strip(), "$options": "i"}})
    store = None
    if body.area:
        store = await db.stores.find_one({"org_id": config.ORG_ID, "area": {"$regex": body.area.strip(), "$options": "i"}})
    if not store:
        store = (await db.stores.find({"org_id": config.ORG_ID}))[0]
    src = portal.lower()
    if src not in config.SOURCES:
        src = "others"
    lead = {"_id": dbm.new_id("led"), "org_id": config.ORG_ID, "name": body.name.strip(),
            "phone_norm": phone_norm, "phone_display": phone_norm,
            "store_id": store["_id"], "service_id": service["_id"] if service else None,
            "source": src, "source_ref": body.source_ref, "stage": "new", "score": 0,
            "score_factors": [], "consent": {}, "customer_id": None,
            "created_at": dbm.now_iso()}
    org = await rules.org_doc(db)
    from ..scoring import score_lead
    score, factors = await score_lead(db, lead, org)
    lead["score"], lead["score_factors"] = score, factors
    await db.leads.insert_one(lead)
    await db.identities.insert_one({"org_id": config.ORG_ID, "phone_norm": phone_norm,
                                    "lead_id": lead["_id"]})
    return {"accepted": True, "lead_id": lead["_id"], "score": score}
