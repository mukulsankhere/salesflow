"""Lead scoring (PRD LEAD-05 / 12.3): recomputed on every change; explainable factors."""
from datetime import datetime, timedelta, timezone

from . import capacity, config, rules


async def score_lead(db, lead: dict, org: dict) -> tuple[int, list]:
    ctx = {}
    src = lead.get("source", "others")
    ctx["capacity_nearby"] = await _capacity_nearby(db, lead, org)
    ident = await db.identities.find_one({"org_id": config.ORG_ID, "phone_norm": lead["phone_norm"]})
    ctx["existing_person"] = bool(ident and (ident.get("customer_id") or ident.get("lead_id") not in (None, lead.get("_id"))))
    replied = False
    if lead.get("_id"):
        inter = await db.interactions.find_one(
            {"person_id": lead["_id"], "direction": "in",
             "channel": {"$in": ["whatsapp", "sms", "call"]}})
        replied = bool(inter)
    ctx["replied"] = replied
    return rules.lead_score(lead, ctx)


async def _capacity_nearby(db, lead, org) -> bool:
    """Service has free capacity near the lead (+20): any store offering the service with
    utilisation below the reroute threshold today."""
    if not lead.get("service_id"):
        return False
    service = await db.services.find_one({"_id": lead["service_id"]})
    if not service:
        return False
    threshold = rules.rule_value(org, "BR-06", 90)
    stores = await db.stores.find({"org_id": config.ORG_ID, "status": "active"})
    today = datetime.now().strftime("%Y-%m-%d")
    for s in stores:
        u = (await capacity.store_day_utilization(db, s["_id"], today))["utilization_pct"]
        if u < threshold:
            ctx = await capacity.load_day_ctx(db, [s["_id"]], today)
            if capacity.store_offers(ctx, s["_id"], service):
                return True
    return False


def sla_for(org: dict, source: str) -> int:
    sla = rules.rule_value(org, "BR-16", {})
    if isinstance(sla, dict):
        return sla.get(source, 15)
    return 15


def sla_deadline(created_at: str, sla_min: int) -> str:
    return (datetime.fromisoformat(created_at) + timedelta(minutes=sla_min)).isoformat()
