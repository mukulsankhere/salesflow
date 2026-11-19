"""User-defined follow-up automation rules - rule-based, transparent and safe.

Two rule types, both internal and reversible (they never send messages and never
delete anything):

- assign_new_leads: when a new lead arrives from a chosen source, assign it to a
  chosen salesperson. Runs once per lead at creation time (owner_id metadata only).
- no_contact_reminder: open leads with no logged outbound contact for N days get a
  follow-up date, so they surface in the owner's action queue. Runs only when the
  user clicks "Run now".

Every run is written to the automation_log collection. Message automations
(WhatsApp templates) remain separate: they only run on an explicit user click and
are gated by consent in campaigns.py.
"""
from datetime import datetime, timedelta, timezone

from . import config, db as dbm, rules

RULE_TYPES = {
    "assign_new_leads": {
        "label": "Assign new leads",
        "fields": ["source", "store_id", "service_id", "owner_id"],
        "effect": "New leads matching the chosen source/store/service are assigned on creation - "
                  "to the chosen salesperson, or to the least-loaded salesperson of the store when no one is chosen.",
        "needs": "at least one filter (source, store or service); owner optional",
    },
    "no_contact_reminder": {
        "label": "Remind owner when no contact is logged",
        "fields": ["days"],
        "effect": "Open leads with no logged contact for the chosen number of days get a follow-up date and appear in the action queue.",
        "needs": "days (1-30)",
    },
}


def _now():
    return datetime.now(timezone.utc)


def _clean(rule: dict) -> dict:
    """API-ready copy (keeps _id - clients address rules by it)."""
    return dict(rule)


def _rule_matches(rule: dict, lead: dict) -> bool:
    """A rule matches a lead when every filter it defines agrees."""
    if rule.get("source") and lead.get("source") != rule["source"]:
        return False
    if rule.get("store_id") and lead.get("store_id") != rule["store_id"]:
        return False
    if rule.get("service_id") and lead.get("service_id") != rule["service_id"]:
        return False
    return True


async def _pick_owner(db, rule: dict, lead: dict) -> str | None:
    """Explicit owner, else the least-loaded active salesperson for the store."""
    if rule.get("owner_id"):
        return rule["owner_id"]
    store_id = rule.get("store_id") or lead.get("store_id")
    users = await db.users.find({"org_id": config.ORG_ID, "roles": "sales", "status": "active"})
    users = [u for u in users if not u.get("store_ids") or (store_id and store_id in u["store_ids"])]
    if not users:
        return None
    open_leads = await db.leads.find({"org_id": config.ORG_ID, "archived": {"$ne": True},
                                      "stage": {"$nin": ["booked", "lost"]}})
    load: dict = {}
    for l in open_leads:
        load[l.get("owner_id")] = load.get(l.get("owner_id"), 0) + 1
    return min(users, key=lambda u: load.get(u["_id"], 0))["_id"]


async def apply_assignment_rules(db, lead: dict, user: dict) -> str | None:
    """Called when a lead is created. Returns the owner_id chosen by the first
    matching enabled rule, or None."""
    rules_list = await db.automation_rules.find({"org_id": config.ORG_ID, "enabled": True,
                                                 "type": "assign_new_leads"})
    for r in rules_list:
        if not _rule_matches(r, lead):
            continue
        owner_id = await _pick_owner(db, r, lead)
        owner = await db.users.find_one({"org_id": config.ORG_ID, "_id": owner_id,
                                         "status": "active"}) if owner_id else None
        if not owner:
            continue
        await db.automation_log.insert_one({
            "_id": dbm.new_id("alog"), "org_id": config.ORG_ID, "at": _now().isoformat(),
            "rule_id": r["_id"], "rule_name": r["name"], "type": r["type"],
            "action": "applied_on_create", "affected": 1,
            "sample": [f"{lead.get('name')} -> {owner['name']}"],
            "by": user.get("name") or "system"})
        await db.automation_rules.update_one({"_id": r["_id"]},
                                             {"$inc": {"stats.runs": 1, "stats.matched": 1}})
        return owner["_id"]
    return None


async def _matches_no_contact(db, lead: dict, days: int, since: datetime) -> bool:
    if lead.get("stage") in ("booked", "lost"):
        return False
    last = lead.get("last_contact_at")
    if last:
        return rules.parse_dt(last) < since
    created = rules.parse_dt(lead.get("created_at"))
    return bool(created and created < since)


async def preview_rule(db, rule: dict) -> dict:
    """What would this rule do right now? No changes are made."""
    now = _now()
    if rule["type"] == "assign_new_leads":
        q = {"org_id": config.ORG_ID, "stage": "new", "archived": {"$ne": True}}
        for f in ("source", "store_id", "service_id"):
            if rule.get(f):
                q[f] = rule[f]
        owner = await db.users.find_one({"_id": rule.get("owner_id")}) if rule.get("owner_id") else None
        leads = [l for l in await db.leads.find(q)
                 if l.get("owner_id") != rule.get("owner_id") or not rule.get("owner_id")]
        src_label = config.SOURCE_LABELS.get(rule.get("source"), rule.get("source") or "any source")
        who = owner["name"] if owner else "the least-loaded salesperson of the store"
        return {"matches": len(leads), "sample": [l["name"] for l in leads[:5]],
                "effect": f"Would assign {len(leads)} matching new lead(s) ({src_label}) to {who} on creation "
                          f"({len(leads)} existing matching lead(s) can be assigned by Run now)."}
    days = int(rule.get("days") or 1)
    since = now - timedelta(days=days)
    leads = await db.leads.find({"org_id": config.ORG_ID, "archived": {"$ne": True},
                                 "stage": {"$nin": ["booked", "lost"]}})
    stale = [l for l in leads if await _matches_no_contact(db, l, days, since)
             and (not l.get("follow_up_at") or l["follow_up_at"] > now.isoformat())]
    return {"matches": len(stale), "sample": [l["name"] for l in stale[:5]],
            "effect": f"Would set a follow-up reminder for {len(stale)} open lead(s) with no "
                      f"contact logged in {days} day(s). No messages are sent."}


async def run_rule(db, user: dict, rule: dict) -> dict:
    """Execute the rule now (explicit user action). Logs everything it touched."""
    now = _now()
    if rule["type"] == "assign_new_leads":
        q = {"org_id": config.ORG_ID, "stage": "new", "archived": {"$ne": True}}
        for f in ("source", "store_id", "service_id"):
            if rule.get(f):
                q[f] = rule[f]
        leads = await db.leads.find(q)
        owner_fixed = await db.users.find_one({"_id": rule.get("owner_id")}) if rule.get("owner_id") else None
        all_users = {u["_id"]: u["name"] for u in await db.users.find({"org_id": config.ORG_ID})}
        assigned = 0
        for l in leads:
            owner_id = await _pick_owner(db, rule, l)
            if not owner_id:
                continue
            await db.leads.update_one({"_id": l["_id"]}, {"$set": {"owner_id": owner_id}})
            assigned += 1
            await db.automation_log.insert_one({
                "_id": dbm.new_id("alog"), "org_id": config.ORG_ID, "at": now.isoformat(),
                "rule_id": rule["_id"], "rule_name": rule["name"], "type": rule["type"],
                "action": "assigned", "affected": 1,
                "sample": [f"{l['name']} -> {all_users.get(owner_id, '?')}"],
                "by": user["name"]})
        await db.automation_rules.update_one({"_id": rule["_id"]},
                                             {"$inc": {"stats.runs": 1, "stats.matched": assigned}})
        await _log_audit(db, user, rule, assigned)
        return {"affected": assigned, "sample": [l["name"] for l in leads[:5]],
                "message": f"Assigned {assigned} lead(s)"
                           + (f" to {owner_fixed['name']}" if owner_fixed else " by least-loaded routing")}

    days = int(rule.get("days") or 1)
    since = now - timedelta(days=days)
    leads = await db.leads.find({"org_id": config.ORG_ID, "archived": {"$ne": True},
                                 "stage": {"$nin": ["booked", "lost"]}})
    stale = [l for l in leads if await _matches_no_contact(db, l, days, since)
             and (not l.get("follow_up_at") or l["follow_up_at"] > now.isoformat())]
    fu = (now + timedelta(days=1)).isoformat()
    owners = {u["_id"]: u["name"] for u in await db.users.find({"org_id": config.ORG_ID})}
    for l in stale:
        await db.leads.update_one({"_id": l["_id"]}, {"$set": {"follow_up_at": fu}})
        await db.automation_log.insert_one({
            "_id": dbm.new_id("alog"), "org_id": config.ORG_ID, "at": now.isoformat(),
            "rule_id": rule["_id"], "rule_name": rule["name"], "type": rule["type"],
            "action": "reminder_set", "affected": 1,
            "sample": [f"{l['name']} (owner {owners.get(l.get('owner_id'), '-')}) -> follow-up {fu[:10]}"],
            "by": user["name"]})
    await db.automation_rules.update_one({"_id": rule["_id"]},
                                         {"$inc": {"stats.runs": 1, "stats.matched": len(stale)}})
    await _log_audit(db, user, rule, len(stale))
    return {"affected": len(stale), "sample": [l["name"] for l in stale[:5]],
            "message": f"Set follow-up reminders for {len(stale)} lead(s) - visible in the action queue"}


async def _log_audit(db, user, rule, affected):
    from .audit import audit
    await audit(db, user, "automation.run", "automation", None,
                {"rule": rule["name"], "type": rule["type"], "affected": affected})


def validate_rule_payload(body: dict) -> tuple[dict | None, str | None]:
    """Return (clean_fields, error)."""
    rtype = body.get("type")
    if rtype not in RULE_TYPES:
        return None, f"Unknown rule type {rtype!r}"
    name = (body.get("name") or "").strip()
    if not name:
        return None, "Rule name is required"
    fields = {"name": name, "type": rtype}
    if rtype == "assign_new_leads":
        source = body.get("source")
        if source and source not in config.SOURCES:
            return None, "Pick a valid lead source"
        if not any([source, body.get("store_id"), body.get("service_id")]):
            return None, "Pick at least one filter: source, store or service"
        fields["source"] = source
        for f in ("store_id", "service_id"):
            if body.get(f):
                fields[f] = body[f]
        if body.get("owner_id"):
            fields["owner_id"] = body["owner_id"]
    else:
        try:
            days = int(body.get("days") or 1)
        except (TypeError, ValueError):
            return None, "Days must be a number"
        if not 1 <= days <= 30:
            return None, "Days must be between 1 and 30"
        fields["days"] = days
    return fields, None
