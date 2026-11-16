"""FastAPI dependencies: JWT auth, RBAC, store scoping, global filter (DASH-03)."""
from fastapi import Depends, HTTPException, Query, Request, WebSocket

from . import config, db as dbm, security
from .db import Database, get_db


async def current_user(request: Request, token: str | None = Query(None, alias="token"),
                       db: Database = Depends(get_db)) -> dict:
    header = request.headers.get("Authorization", "")
    jwt_token = None
    if header.startswith("Bearer "):
        jwt_token = header[7:]
    elif token:
        jwt_token = token  # SSE (EventSource cannot set headers)
    if not jwt_token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = security.decode_token(jwt_token)
        if payload.get("typ") != "access":
            raise HTTPException(401, "Invalid token type")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    user = await db.users.find_one({"_id": payload["sub"]})
    if not user or user.get("status") != "active":
        raise HTTPException(401, "User disabled")
    return user


def require_section(section: str):
    """AUTH-02 RBAC gate + global filter resolution. Returns {'period', 'store_ids',
    'store_id', 'user'} so every list/analytics endpoint shares one dependency."""
    async def _dep(period: str = Query("30"), store_id: str | None = Query(None),
                   user: dict = Depends(current_user)) -> dict:
        from .config import PERMISSIONS
        if section not in PERMISSIONS or user["roles"][0] not in PERMISSIONS[section]:
            raise HTTPException(403, f"Your role ({user['roles'][0]}) cannot access {section}")
        return await _resolve(period, store_id, user)
    return _dep


async def _resolve(period: str, store_id: str | None, user: dict) -> dict:
    if period not in ("today", "7", "30", "90", "all"):
        period = "30"
    scope = store_scope(user)
    if store_id and scope and store_id not in scope:
        raise HTTPException(403, "Store outside your scope")
    if store_id and not scope:
        scope = [store_id]
    return {"period": period, "store_ids": scope,
            "store_id": store_id or (scope[0] if scope and len(scope) == 1 else None),
            "user": user}


async def resolve_filter(period: str = Query("30"), store_id: str | None = Query(None),
                         user: dict = Depends(current_user)) -> dict:
    """Global filter (period + store), validated against the user's store scope."""
    return await _resolve(period, store_id, user)


def store_scope(user: dict) -> list:
    """AUTH-03: [] means all stores (admin/marketing/readonly); otherwise assigned stores."""
    if "admin" in user["roles"]:
        return []
    return user.get("store_ids") or []


async def resolve_filter(period: str = Query("30"), store_id: str | None = Query(None),
                         user: dict = Depends(current_user), db: Database = Depends(get_db)):
    """Global filter (period + store), validated against the user's store scope."""
    if period not in ("today", "7", "30", "90", "all"):
        period = "30"
    scope = store_scope(user)
    if store_id and scope and store_id not in scope:
        raise HTTPException(403, "Store outside your scope")
    if store_id and not scope:
        scope = [store_id]
    return {"period": period, "store_ids": scope, "store_id": store_id or (scope[0] if scope and len(scope) == 1 else None)}


def err(code: str, message: str, status: int = 400, details=None) -> HTTPException:
    return HTTPException(status, {"error": {"code": code, "message": message, "details": details}})


async def audit(db: Database, user, action, entity, before, after, entity_id=None):
    from .audit import audit as _audit
    await _audit(db, user, action, entity, before, after, entity_id)
