import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from . import config


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, dk = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return secrets.compare_digest(check.hex(), dk)


def _encode(payload: dict, minutes: int) -> str:
    payload = dict(payload, iat=datetime.now(timezone.utc),
                   exp=datetime.now(timezone.utc) + timedelta(minutes=minutes),
                   jti=uuid.uuid4().hex)
    return jwt.encode(payload, config.SECRET_KEY, algorithm="HS256")


def create_access_token(user: dict) -> str:
    return _encode({"sub": user["_id"], "org": user["org_id"], "roles": user["roles"],
                    "stores": user.get("store_ids", []), "typ": "access"},
                   config.ACCESS_TOKEN_MINUTES)


def create_refresh_token(user: dict) -> str:
    return _encode({"sub": user["_id"], "org": user["org_id"], "typ": "refresh"},
                   config.REFRESH_TOKEN_DAYS * 24 * 60)


def decode_token(token: str) -> dict:
    return jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
