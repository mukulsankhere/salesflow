"""Audit trail (AUTH-05): every create/update/delete/booking/rule change/export."""
import json

from . import db as dbm


async def audit(db, user, action, entity, before, after, entity_id=None):
    safe = lambda x: json.loads(json.dumps(x, default=str)) if x else None
    await db.audit_log.insert_one({
        "_id": dbm.new_id("aud"), "org_id": dbm.config.ORG_ID,
        "who_id": user.get("_id"), "who": user.get("name"), "roles": user.get("roles", []),
        "action": action, "entity": entity, "entity_id": entity_id,
        "before": safe(before), "after": safe(after), "at": dbm.now_iso()})
