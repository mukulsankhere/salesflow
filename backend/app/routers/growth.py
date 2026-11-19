"""Marketing (MKT-01..MKT-10) and Insights (rule-based recommendations)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import automations as auto_svc, campaigns, config, insights as insight_svc, rules
from ..audit import audit
from ..deps import current_user, err, require_section
from ..db import Database, get_db
from ..db import new_id as _new_id, now_iso as _now_iso

router = APIRouter(prefix="/api/v1")


class PreviewIn(BaseModel):
    kind: str = "leads"          # leads | customers
    segment_key: str
    sub_key: str | None = None
    offer_pct: int = 10
    channel: str = "whatsapp"
    template_body: str
    schedule_at: str | None = None


class LaunchIn(PreviewIn):
    name: str
    period: str = "30"


class AutomationRunIn(BaseModel):
    name: str


@router.get("/lead-segments")
async def lead_segments(flt: dict = Depends(require_section("marketing")), db: Database = Depends(get_db)):
    segs = await campaigns.lead_segments(db, flt["period"], flt["store_ids"])
    services = {s["_id"]: s["name"] for s in await db.services.find({"org_id": config.ORG_ID})}
    sources = config.SOURCE_LABELS
    lost = {}
    for l in segs.get("lost_by_reason", []):
        lost[l.get("lost_reason", "Unknown")] = lost.get(l.get("lost_reason", "Unknown"), 0) + 1
    by_source = {}
    for l in segs.get("by_source", []):
        by_source[l.get("source", "others")] = by_source.get(l.get("source", "others"), 0) + 1
    by_service = {}
    for l in segs.get("by_service", []):
        by_service[l.get("service_id", "")] = by_service.get(l.get("service_id", ""), 0) + 1
    out = []
    for key in ("hot", "interested", "new", "stale_offers", "overdue_followups"):
        out.append({"key": key, "label": campaigns.LEAD_SEGMENT_DEFS[key], "count": len(segs.get(key, [])),
                    "kind": "fixed"})
    out.append({"kind": "group", "label": "By Source", "key": "by_source",
                "options": [{"key": k, "label": sources.get(k, k), "count": v} for k, v in
                            sorted(by_source.items(), key=lambda kv: -kv[1])]})
    out.append({"kind": "group", "label": "By Service", "key": "by_service",
                "options": [{"key": k, "label": services.get(k, k), "count": v} for k, v in
                            sorted(by_service.items(), key=lambda kv: -kv[1]) if k]})
    out.append({"kind": "group", "label": "Lost By Reason", "key": "lost_by_reason",
                "options": [{"key": k, "label": k, "count": v} for k, v in
                            sorted(lost.items(), key=lambda kv: -kv[1])]})
    return {"segments": out}


@router.get("/customer-segments")
async def customer_segments(flt: dict = Depends(require_section("marketing")), db: Database = Depends(get_db)):
    segs = await campaigns.customer_segments(db, flt["store_ids"])
    counts = {k: len(v) for k, v in segs.items()}
    # MKT-04: AI recommendation = biggest convertible audience
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    best = ranked[0][0] if ranked else None
    return {"segments": [{"key": k, "label": rules.SEGMENT_LABELS.get(k, k), "count": v}
                         for k, v in sorted(counts.items(), key=lambda kv: -kv[1])],
            "ai_recommendation": {"segment_key": best,
                                  "label": rules.SEGMENT_LABELS.get(best, best) if best else None,
                                  "text": (f"Send a {rules.SEGMENT_LABELS.get(best, best)} campaign now: "
                                           f"{ranked[0][1]} customers match and the conversion band is "
                                           f"{int(rules.SEGMENT_CONV.get(best, (0.1, 0.2))[0] * 100)}-"
                                           f"{int(rules.SEGMENT_CONV.get(best, (0.1, 0.2))[1] * 100)}%.") if best else None}}


@router.post("/campaigns/preview")
async def preview(body: PreviewIn, flt: dict = Depends(require_section("marketing")),
                  db: Database = Depends(get_db)):
    try:
        return await campaigns.preview(db, body.kind, body.segment_key, body.sub_key,
                                       body.offer_pct, body.channel, body.template_body,
                                       flt["period"], flt["store_ids"])
    except Exception:
        raise err("VALIDATION_ERROR", "Could not build the preview - check the segment")


@router.post("/campaigns", status_code=201)
async def launch(body: LaunchIn, flt: dict = Depends(require_section("marketing")),
                 db: Database = Depends(get_db), user: dict = Depends(current_user)):
    camp = await campaigns.launch(db, user, body.kind, body.name, body.segment_key, body.sub_key,
                                  body.offer_pct, body.channel, body.template_body,
                                  body.schedule_at, body.period, flt["store_ids"])
    return camp


@router.get("/campaigns")
async def list_campaigns(status: str | None = None, db: Database = Depends(get_db),
                         flt: dict = Depends(require_section("marketing")), user: dict = Depends(current_user)):
    query = {"org_id": config.ORG_ID}
    if status:
        query["status"] = status
    camps = await db.campaigns.find(query, sort=[("created_at", -1)])
    return {"campaigns": camps}


@router.post("/campaigns/{cid}/{action}")
async def campaign_action(cid: str, action: str, db: Database = Depends(get_db),
                          flt: dict = Depends(require_section("marketing")), user: dict = Depends(current_user)):
    """MKT-07: pause, resume, stop, duplicate."""
    import asyncio

    from ..realtime import publish

    camp = await db.campaigns.find_one({"_id": cid})
    if not camp:
        raise err("NOT_FOUND", "Campaign not found", 404)
    if action == "pause":
        await db.campaigns.update_one({"_id": cid}, {"$set": {"status": "paused"}})
    elif action == "resume":
        await db.campaigns.update_one({"_id": cid}, {"$set": {"status": "live"}})
        asyncio.get_event_loop().create_task(campaigns.dispatch(db, cid))
    elif action == "stop":
        await db.campaigns.update_one({"_id": cid}, {"$set": {"status": "stopped"}})
        await db.campaign_messages.update_many({"campaign_id": cid, "status": "queued"},
                                               {"$set": {"status": "cancelled"}})
    elif action == "duplicate":
        new_id = _new_id("cmp")
        await db.campaigns.insert_one({**{k: v for k, v in camp.items() if k not in ("_id", "stats", "created_at")},
                                       "_id": new_id,
                                       "name": f"{camp['name']} (Copy)",
                                       "status": "scheduled",
                                       "stats": {"audience": 0, "sent": 0, "delivered": 0,
                                                 "read": 0, "replied": 0, "booked": 0, "revenue": 0},
                                       "created_at": _now_iso()})
        return await db.campaigns.find_one({"_id": new_id})
    else:
        raise err("VALIDATION_ERROR", "Action must be pause, resume, stop or duplicate")
    await publish(db, "campaign.status", {"campaign_id": cid, "status": action})
    return await db.campaigns.find_one({"_id": cid})


@router.get("/campaigns/{cid}/results")
async def campaign_results(cid: str, db: Database = Depends(get_db),
                           flt: dict = Depends(require_section("marketing")), user: dict = Depends(current_user)):
    camp = await db.campaigns.find_one({"_id": cid})
    if not camp:
        raise err("NOT_FOUND", "Campaign not found", 404)
    msgs = await db.campaign_messages.find({"campaign_id": cid}, sort=[("created_at", -1)], limit=50)
    return {"campaign": camp, "messages": msgs}


@router.get("/templates")
async def templates(db: Database = Depends(get_db), user: dict = Depends(current_user)):
    return {"templates": await db.templates.find({"org_id": config.ORG_ID})}


@router.get("/automations")
async def automations(db: Database = Depends(get_db), flt: dict = Depends(require_section("marketing")), user: dict = Depends(current_user)):
    stored = {a["name"]: a for a in await db.automations.find({"org_id": config.ORG_ID})}
    out = []
    for d in campaigns.AUTOMATION_DEFS:
        st = stored.get(d["name"], {})
        out.append({**d, "enabled": st.get("enabled", True),
                    "stats": st.get("stats", {"runs": 0, "messages_sent": 0})})
    return {"automations": out}


@router.put("/automations/{name}")
async def toggle_automation(name: str, body: dict, db: Database = Depends(get_db),
                            flt: dict = Depends(require_section("marketing")), user: dict = Depends(current_user)):
    await db.automations.update_one({"org_id": config.ORG_ID, "name": name},
                                    {"$set": {"enabled": bool(body.get("enabled"))}}, upsert=True)
    return {"ok": True}


@router.post("/automations/run")
async def run_automation(body: AutomationRunIn, db: Database = Depends(get_db),
                         flt: dict = Depends(require_section("marketing")), user: dict = Depends(current_user)):
    result = await campaigns.run_automation(db, user, body.name)
    if not result:
        raise err("NOT_FOUND", "Automation not found", 404)
    return result


# --------------------------------------------------- follow-up automation rules
# Rule-based helpers for the sales team. They only assign owners or set follow-up
# dates - they never send messages or delete records. Message automations above
# stay behind an explicit Run click with consent checks.

class RuleIn(BaseModel):
    name: str
    type: str            # assign_new_leads | no_contact_reminder
    source: str | None = None
    store_id: str | None = None
    service_id: str | None = None
    owner_id: str | None = None
    days: int | None = None


@router.get("/automation-rules")
async def list_rules(db: Database = Depends(get_db), user: dict = Depends(current_user)):
    owners = {u["_id"]: u["name"] for u in
              await db.users.find({"org_id": config.ORG_ID, "roles": "sales", "status": "active"})}
    stores = {s["_id"]: s["name"] for s in await db.stores.find({"org_id": config.ORG_ID})}
    services = {s["_id"]: s["name"] for s in await db.services.find({"org_id": config.ORG_ID})}
    out = []
    for r in await db.automation_rules.find({"org_id": config.ORG_ID}, sort=[("created_at", 1)]):
        out.append({**auto_svc._clean(r),
                    "owner_name": owners.get(r.get("owner_id"), "-"),
                    "store_name": stores.get(r.get("store_id")),
                    "service_name": services.get(r.get("service_id")),
                    "meta": auto_svc.RULE_TYPES.get(r["type"])})
    return {"rules": out, "types": auto_svc.RULE_TYPES,
            "owners": [{"_id": k, "name": v} for k, v in owners.items()],
            "stores": [{"_id": k, "name": v} for k, v in stores.items()],
            "services": [{"_id": k, "name": v} for k, v in services.items()]}


@router.post("/automation-rules", status_code=201)
async def create_rule(body: RuleIn, db: Database = Depends(get_db), user: dict = Depends(current_user)):
    fields, error = auto_svc.validate_rule_payload(body.model_dump())
    if error:
        raise err("VALIDATION_ERROR", error, 422)
    if body.owner_id and not await db.users.find_one({"org_id": config.ORG_ID, "_id": body.owner_id}):
        raise err("VALIDATION_ERROR", "Unknown salesperson", 422)
    if body.store_id and not await db.stores.find_one({"org_id": config.ORG_ID, "_id": body.store_id}):
        raise err("VALIDATION_ERROR", "Unknown store", 422)
    if body.service_id and not await db.services.find_one({"org_id": config.ORG_ID, "_id": body.service_id}):
        raise err("VALIDATION_ERROR", "Unknown service", 422)
    doc = {"_id": _new_id("rule"), "org_id": config.ORG_ID, **fields,
           "enabled": False,  # disabled until the user previews and enables it
           "stats": {"runs": 0, "matched": 0}, "created_at": datetime.now(timezone.utc).isoformat()}
    await db.automation_rules.insert_one(doc)
    await audit(db, user, "automation.rule_create", "automation", None,
                {"name": doc["name"], "type": doc["type"]}, entity_id=doc["_id"])
    return auto_svc._clean(doc)


@router.put("/automation-rules/{rule_id}")
async def update_rule(rule_id: str, body: dict, db: Database = Depends(get_db),
                      user: dict = Depends(current_user)):
    rule = await db.automation_rules.find_one({"org_id": config.ORG_ID, "_id": rule_id})
    if not rule:
        raise err("NOT_FOUND", "Rule not found", 404)
    updates = {}
    if "enabled" in body:
        updates["enabled"] = bool(body["enabled"])
        await db.automation_log.insert_one({
            "_id": _new_id("alog"), "org_id": config.ORG_ID,
            "at": datetime.now(timezone.utc).isoformat(), "rule_id": rule_id,
            "rule_name": rule["name"], "type": rule["type"],
            "action": "enabled" if body["enabled"] else "paused", "affected": 0,
            "sample": [], "by": user["name"]})
    for k in ("name", "days"):
        if k in body and body[k] is not None:
            updates[k] = body[k]
    if updates.get("days") is not None:
        try:
            updates["days"] = int(updates["days"])
            if not 1 <= updates["days"] <= 30:
                raise ValueError
        except ValueError:
            raise err("VALIDATION_ERROR", "Days must be between 1 and 30", 422)
    if updates:
        await db.automation_rules.update_one({"_id": rule_id}, {"$set": updates})
    await audit(db, user, "automation.rule_update", "automation", {"enabled": rule.get("enabled")},
                {"enabled": updates.get("enabled", rule.get("enabled"))}, entity_id=rule_id)
    return {"ok": True}


@router.delete("/automation-rules/{rule_id}")
async def delete_rule(rule_id: str, db: Database = Depends(get_db), user: dict = Depends(current_user)):
    rule = await db.automation_rules.find_one({"org_id": config.ORG_ID, "_id": rule_id})
    if not rule:
        raise err("NOT_FOUND", "Rule not found", 404)
    await db.automation_rules.delete_one({"_id": rule_id})
    await db.automation_log.insert_one({
        "_id": _new_id("alog"), "org_id": config.ORG_ID,
        "at": datetime.now(timezone.utc).isoformat(), "rule_id": rule_id,
        "rule_name": rule["name"], "type": rule["type"], "action": "deleted",
        "affected": 0, "sample": [], "by": user["name"]})
    await audit(db, user, "automation.rule_delete", "automation", {"name": rule["name"]}, None,
                entity_id=rule_id)
    return {"ok": True}


@router.post("/automation-rules/{rule_id}/preview")
async def preview_rule(rule_id: str, db: Database = Depends(get_db), user: dict = Depends(current_user)):
    rule = await db.automation_rules.find_one({"org_id": config.ORG_ID, "_id": rule_id})
    if not rule:
        raise err("NOT_FOUND", "Rule not found", 404)
    preview = await auto_svc.preview_rule(db, rule)
    return {**preview, "rule": auto_svc._clean(rule)}


@router.post("/automation-rules/{rule_id}/run")
async def run_rule(rule_id: str, db: Database = Depends(get_db), user: dict = Depends(current_user)):
    rule = await db.automation_rules.find_one({"org_id": config.ORG_ID, "_id": rule_id})
    if not rule:
        raise err("NOT_FOUND", "Rule not found", 404)
    if not rule.get("enabled"):
        raise err("VALIDATION_ERROR", "Enable the rule before running it - preview it first", 422)
    return await auto_svc.run_rule(db, user, rule)


@router.get("/automation-log")
async def automation_log(limit: int = 40, db: Database = Depends(get_db),
                         user: dict = Depends(current_user)):
    entries = await db.automation_log.find({"org_id": config.ORG_ID},
                                           sort=[("at", -1)], limit=min(limit, 100))
    return {"log": entries}


# ------------------------------------------------------------------ insights
@router.get("/insights")
async def get_insights(flt: dict = Depends(require_section("insights")), db: Database = Depends(get_db)):
    return await insight_svc.compute(db, flt["period"], flt["store_ids"])


@router.post("/insights/{scope}/{key}/apply")
async def apply_insight(scope: str, key: str, db: Database = Depends(get_db),
                        flt: dict = Depends(require_section("insights")), user: dict = Depends(current_user)):
    await insight_svc.apply(db, user, scope, key)
    return {"ok": True, "key": key}


@router.post("/insights/{scope}/{key}/dismiss")
async def dismiss_insight(scope: str, key: str, db: Database = Depends(get_db),
                          flt: dict = Depends(require_section("insights")), user: dict = Depends(current_user)):
    await insight_svc.dismiss(db, user, scope, key)
    return {"ok": True}
