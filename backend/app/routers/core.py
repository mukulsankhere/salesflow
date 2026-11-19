"""Core: auth (AUTH-01), org + rules editor (AUTH-04/ADM-01), stores, services,
pricing (PROD-02), audit (AUTH-05), integration health (ADM-03)."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .. import automations, capacity, config, db as dbm, rules, security
from ..deps import audit, current_user, err, require_section
from ..db import Database, get_db
from ..realtime import publish

router = APIRouter(prefix="/api/v1")


class LoginIn(BaseModel):
    email_or_phone: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class RulesIn(BaseModel):
    rule_id: str
    value: object


class StatusIn(BaseModel):
    status: str


class ResetPasswordIn(BaseModel):
    password: str


@router.post("/auth/login")
async def login(body: LoginIn, db: Database = Depends(get_db)):
    key = body.email_or_phone.strip().lower()
    user = await db.users.find_one({"org_id": config.ORG_ID, "email": key})
    if not user:
        user = await db.users.find_one({"org_id": config.ORG_ID, "phone": key})
    if not user or not security.verify_password(body.password, user.get("password_hash") or ""):
        raise err("INVALID_CREDENTIALS", "Wrong email/phone or password", 401)
    if user.get("status") != "active":
        raise err("ACCOUNT_DISABLED", "This account has been deactivated - contact your admin", 403)
    if "admin" not in (user.get("roles") or []):
        # Single-login deployment: only the Admin account can sign in. Employees stay
        # in Store & Team as staff records (assignees, roster) but have no sign-in.
        raise err("EMPLOYEE_LOGIN_DISABLED",
                  "Employee sign-in is disabled - only the Admin account can sign in", 403)
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


VALID_ROLES = ("admin", "manager", "sales", "staff", "marketing", "readonly")
USER_FIELDS = ("_id", "name", "email", "phone", "roles", "store_ids", "status", "created_at", "last_login")


class UserIn(BaseModel):
    name: str
    email: str
    phone: str = ""
    password: str
    roles: list[str]
    store_ids: list[str] = []


def _require_admin(user: dict):
    if "admin" not in (user.get("roles") or []):
        raise err("FORBIDDEN", "Only admins can manage users", 403)


@router.get("/auth/users")
async def list_users(user: dict = Depends(current_user), db: Database = Depends(get_db)):
    """ADM-02: admin-only user directory for Settings > Users & Scope."""
    _require_admin(user)
    users = await db.users.find({"org_id": config.ORG_ID}, sort=[("created_at", 1)])
    return {"users": [{k: u.get(k) for k in USER_FIELDS} for u in users]}


@router.post("/auth/users", status_code=201)
async def create_user(body: UserIn, user: dict = Depends(current_user), db: Database = Depends(get_db)):
    """ADM-02: create a new login with roles and store scope; the account can sign in immediately."""
    _require_admin(user)
    name = body.name.strip()
    email = body.email.strip().lower()
    phone = body.phone.strip()
    if not name:
        raise err("VALIDATION", "Name is required", 422)
    if "@" not in email or "." not in email.split("@")[-1] or " " in email:
        raise err("VALIDATION", "Enter a valid email address", 422)
    if phone and (not phone.isdigit() or len(phone) != 10):
        raise err("VALIDATION", "Phone must be a 10-digit number (or left empty)", 422)
    if len(body.password) < 8:
        raise err("VALIDATION", "Password must be at least 8 characters", 422)
    roles = [r for r in body.roles if r in VALID_ROLES]
    if not roles:
        raise err("VALIDATION", "Pick at least one valid role", 422)
    if await db.users.find_one({"org_id": config.ORG_ID, "email": email}):
        raise err("EMAIL_EXISTS", "A user with this email already exists", 409)
    if phone and await db.users.find_one({"org_id": config.ORG_ID, "phone": phone}):
        raise err("PHONE_EXISTS", "A user with this phone already exists", 409)
    store_ids = list(dict.fromkeys(body.store_ids))
    all_stores = {s["_id"] for s in await db.stores.find({"org_id": config.ORG_ID})}
    unknown = [s for s in store_ids if s not in all_stores]
    if unknown:
        raise err("VALIDATION", f"Unknown store: {unknown[0]}", 422)
    uid = dbm.new_id("usr")
    await db.users.insert_one({
        "_id": uid, "org_id": config.ORG_ID, "name": name, "email": email,
        "phone": phone, "password_hash": security.hash_password(body.password),
        "roles": roles, "store_ids": store_ids, "status": "active",
        "created_at": dbm.now_iso()})
    await audit(db, user, "user.create", "user", None,
                {"name": name, "email": email, "roles": roles, "store_ids": store_ids}, entity_id=uid)
    await publish(db, "user.created", {"name": name, "roles": ", ".join(roles)})
    return {"_id": uid, "name": name, "email": email, "roles": roles,
            "store_ids": store_ids, "status": "active"}


async def _get_managed_user(db: Database, user_id: str) -> dict:
    target = await db.users.find_one({"org_id": config.ORG_ID, "_id": user_id})
    if not target:
        raise err("NOT_FOUND", "User not found", 404)
    return target


@router.post("/auth/users/{user_id}/status")
async def set_user_status(user_id: str, body: StatusIn, user: dict = Depends(current_user),
                          db: Database = Depends(get_db)):
    """ADM-02b: activate or deactivate a login; deactivated users are blocked at sign-in."""
    _require_admin(user)
    if body.status not in ("active", "disabled"):
        raise err("VALIDATION", "Status must be 'active' or 'disabled'", 422)
    if user_id == user["_id"] and body.status == "disabled":
        raise err("VALIDATION", "You cannot deactivate your own account", 422)
    target = await _get_managed_user(db, user_id)
    if target.get("status") == body.status:
        return {"_id": user_id, "name": target["name"], "status": body.status, "changed": False}
    await db.users.update_one({"_id": user_id}, {"$set": {"status": body.status}})
    await audit(db, user, "user.status", "user", {"status": target.get("status")},
                {"status": body.status}, entity_id=user_id)
    await publish(db, "user.updated", {"name": target["name"], "status": body.status})
    return {"_id": user_id, "name": target["name"], "status": body.status, "changed": True}


@router.post("/auth/users/{user_id}/reset-password")
async def reset_password(user_id: str, body: ResetPasswordIn, user: dict = Depends(current_user),
                         db: Database = Depends(get_db)):
    """ADM-02c: admin sets a new starting password; the user signs in with it immediately."""
    _require_admin(user)
    if len(body.password) < 8:
        raise err("VALIDATION", "Password must be at least 8 characters", 422)
    target = await _get_managed_user(db, user_id)
    await db.users.update_one({"_id": user_id},
                              {"$set": {"password_hash": security.hash_password(body.password)}})
    await audit(db, user, "user.password_reset", "user", None, {"email": target["email"]},
                entity_id=user_id)
    return {"_id": user_id, "email": target["email"], "ok": True}


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
async def audit_log(limit: int = 100, action: str | None = None, who: str | None = None,
                    entity: str | None = None, db: Database = Depends(get_db),
                    flt: dict = Depends(require_section("audit")), user: dict = Depends(current_user)):
    """AUTH-05: change history with filters (action / user / entity)."""
    q = {"org_id": config.ORG_ID}
    if action:
        q["action"] = {"$regex": action, "$options": "i"}
    if who:
        q["who"] = {"$regex": who, "$options": "i"}
    if entity:
        q["entity"] = entity
    rows = await db.audit_log.find(q, sort=[("at", -1)], limit=min(limit, 300))
    return {"audit": rows}


@router.get("/audit/filters")
async def audit_filters(db: Database = Depends(get_db),
                        flt: dict = Depends(require_section("audit")),
                        user: dict = Depends(current_user)):
    actions = await db.audit_log.distinct("action", {"org_id": config.ORG_ID})
    whos = await db.audit_log.distinct("who", {"org_id": config.ORG_ID})
    return {"actions": sorted(a for a in actions if a), "users": sorted(w for w in whos if w)}


_INTEGRATION_FALLBACK = [
    {"_id": "int_whatsapp", "name": "WhatsApp Business Cloud API", "kind": "whatsapp", "status": "demo",
     "note": "Provider interface ready; set credentials in Settings to go live.",
     "last_sync": None, "issues": [], "retries": 0},
    {"_id": "int_sms", "name": "SMS Gateway (DLT)", "kind": "sms", "status": "demo",
     "note": "DLT-registered template IDs pending.", "last_sync": None, "issues": [], "retries": 0},
    {"_id": "int_voice", "name": "Voice / Click-to-call", "kind": "voice", "status": "demo",
     "note": "Telephony API pending.", "last_sync": None, "issues": [], "retries": 0},
    {"_id": "int_payments", "name": "Payment Gateway (Razorpay)", "kind": "payments", "status": "demo",
     "note": "Payment links recorded; live keys pending.", "last_sync": None, "issues": [], "retries": 0},
    {"_id": "int_portals", "name": "Lead Portals (Google/Meta/Justdial)", "kind": "portals", "status": "demo",
     "note": "Webhook endpoints under /api/v1/ingest/{portal}; pollers configured.",
     "last_sync": None, "issues": [], "retries": 0},
    {"_id": "int_database", "name": "MongoDB", "kind": "database", "status": "fallback",
     "note": "In-process document store active (set MONGO_URI for MongoDB).",
     "last_sync": None, "issues": [], "retries": 0},
    {"_id": "int_queue", "name": "Redis", "kind": "queue", "status": "demo",
     "note": "In-process queue active (set REDIS_URL in production).",
     "last_sync": None, "issues": [], "retries": 0},
]


@router.get("/integrations/health")
async def integrations(db: Database = Depends(get_db),
                       flt: dict = Depends(require_section("settings")), user: dict = Depends(current_user)):
    """ADM-03: integrations hub - connection status, last sync, sync issues, retry counts."""
    docs = await db.integrations.find({"org_id": config.ORG_ID}, sort=[("name", 1)])
    if docs:
        return {"integrations": docs}
    return {"integrations": _INTEGRATION_FALLBACK}


@router.post("/integrations/{int_id}/retry")
async def integration_retry(int_id: str, db: Database = Depends(get_db),
                            flt: dict = Depends(require_section("settings")),
                            user: dict = Depends(current_user)):
    """Retry a failing integration sync. Rate-limit issues heal on retry; an expired
    credential is reported back so the user knows a manual fix is needed."""
    import asyncio

    doc = await db.integrations.find_one({"$or": [{"_id": int_id}, {"name": int_id}]})
    if not doc:
        raise err("NOT_FOUND", "Integration not found", 404)
    needs_credential = any(i.get("code") == "WEBHOOK_AUTH" for i in (doc.get("issues") or []))
    await db.integrations.update_one({"_id": doc["_id"]},
                                     {"$set": {"status": "syncing",
                                               "last_retry": dbm.now_iso()},
                                      "$inc": {"retries": 1}})
    await publish(db, "integration.retry", {"name": doc["name"], "status": "syncing"})
    await asyncio.sleep(1.2)  # simulated provider handshake
    if needs_credential:
        await db.integrations.update_one({"_id": doc["_id"]}, {"$set": {"status": "error"}})
        await audit(db, user, "integration.retry_failed", "integration", None,
                    {"name": doc["name"], "reason": "expired credential"})
        await publish(db, "integration.retry_failed", {"name": doc["name"]})
        return {"ok": False, "status": "error",
                "message": f"{doc['name']}: retry failed - the stored token is expired. "
                           f"Update the credential, then retry."}
    await db.integrations.update_one({"_id": doc["_id"]},
                                     {"$set": {"status": "live", "last_sync": dbm.now_iso(),
                                               "issues": []}})
    await audit(db, user, "integration.retry_ok", "integration", {"status": doc.get("status")},
                {"status": "live", "issues_cleared": len(doc.get("issues") or [])},
                entity_id=doc["_id"])
    await publish(db, "integration.recovered", {"name": doc["name"]})
    return {"ok": True, "status": "live",
            "message": f"{doc['name']} recovered - sync is live, issues cleared"}


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
    digits = body.phone.strip().replace(" ", "")
    if not digits.isdigit() or len(digits) != 10:
        return {"accepted": False, "reason": "phone must be 10 digits"}
    phone_norm = digits[-10:]
    if await db.identities.find_one({"org_id": config.ORG_ID, "phone_norm": phone_norm}):
        return {"accepted": False, "reason": "duplicate phone (existing lead/customer)", "code": "PHONE_EXISTS"}
    service = None
    if body.service:
        service = await db.services.find_one({"org_id": config.ORG_ID,
                                              "name": {"$regex": body.service.strip(), "$options": "i"}})
    if not service:  # keep every lead linked to a real service (inter-relation)
        service = (await db.services.find({"org_id": config.ORG_ID}))[0]
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
            "store_id": store["_id"], "service_id": service["_id"],
            "source": src, "source_ref": body.source_ref, "stage": "new", "score": 0,
            "score_factors": [], "consent": {}, "customer_id": None,
            "owner_id": None, "follow_up_at": None, "offered_at": None,
            "lost_reason": None, "notes": None, "last_contact_at": None,
            "stage_history": [{"stage": "new", "at": dbm.now_iso()}],
            "created_at": dbm.now_iso()}
    org = await rules.org_doc(db)
    from ..scoring import score_lead
    score, factors = await score_lead(db, lead, org)
    lead["score"], lead["score_factors"] = score, factors
    await db.leads.insert_one(lead)
    await db.identities.insert_one({"org_id": config.ORG_ID, "phone_norm": phone_norm,
                                    "lead_id": lead["_id"]})
    assigned = await automations.apply_assignment_rules(db, lead, {"name": f"portal:{portal}"})
    if assigned:
        await db.leads.update_one({"_id": lead["_id"]}, {"$set": {"owner_id": assigned}})
        lead["owner_id"] = assigned
    return {"accepted": True, "lead_id": lead["_id"], "score": score}
