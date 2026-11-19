"""Leads (LEAD-01..LEAD-14) and Customer 360 (CUST-01..CUST-07)."""
import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .. import automations, config, db as dbm, priority, rules, scoring
from ..audit import audit
from ..deps import current_user, err, require_section, store_scope
from ..db import Database, get_db
from ..realtime import publish

router = APIRouter(prefix="/api/v1")


def _now():
    return datetime.now(timezone.utc)


def _stage_push(stage, note=""):
    return [{"stage": stage, "at": _now().isoformat(), "note": note}]


def _append_stage(lead, stage, note=""):
    hist = list(lead.get("stage_history") or [])
    hist.append({"stage": stage, "at": _now().isoformat(), "note": note})
    return hist


class LeadIn(BaseModel):
    name: str
    phone: str
    store_id: str | None = None
    service_id: str | None = None
    source: str = "others"
    age_group: str | None = None
    gender: str | None = None
    consent_whatsapp: bool = False
    consent_sms: bool = False
    notes: str | None = None


class StageIn(BaseModel):
    stage: str
    note: str | None = None


class FollowUpIn(BaseModel):
    follow_up_at: str


class LostIn(BaseModel):
    reason: str


class InteractionIn(BaseModel):
    channel: str
    direction: str = "out"
    outcome: str | None = None
    content: str | None = None


class ConsentIn(BaseModel):
    channel: str
    action: str  # opt-in | opt-out | dnd


class ImportIn(BaseModel):
    csv: str


class MergeIn(BaseModel):
    primary_id: str
    duplicate_id: str


async def _enrich_leads(db, leads, org):
    stores = {s["_id"]: s for s in await db.stores.find({"org_id": config.ORG_ID})}
    services = {s["_id"]: s for s in await db.services.find({"org_id": config.ORG_ID})}
    prio = await priority.compute_priority_many(db, leads)
    out = []
    now = _now()
    for l in leads:
        sla = scoring.sla_for(org, l.get("source", "others"))
        deadline = (datetime.fromisoformat(l["created_at"]) + timedelta(minutes=sla)) if l.get("created_at") else None
        p = prio.get(l["_id"], {"tier": "low", "score": l.get("score", 0), "reasons": []})
        out.append({**l,
                    "store_name": stores.get(l.get("store_id"), {}).get("name"),
                    "service_name": services.get(l.get("service_id"), {}).get("name"),
                    "band": rules.score_band(l.get("score", 0)),
                    "priority": p["tier"],
                    "priority_reasons": p["reasons"],
                    "sla_minutes": sla,
                    "sla_breached": bool(deadline and now > deadline and l.get("stage") == "new"),
                    "source_label": config.SOURCE_LABELS.get(l.get("source"), l.get("source"))})
    return out


# archived/merged leads disappear from the working views but keep their history
ARCHIVE_FILTER = {"archived": {"$ne": True}}


@router.get("/leads")
async def list_leads(q: str | None = None, stage: str | None = None, source: str | None = None,
                     service_id: str | None = None, page: int = 1, limit: int = 20,
                     flt: dict = Depends(require_section("leads")), db: Database = Depends(get_db),
                     sort: str = "created_at:desc"):
    period, store_ids = flt["period"], flt["store_ids"]
    start, _ = rules.period_range(period, _now())
    query = {"org_id": config.ORG_ID}
    query.update(ARCHIVE_FILTER)
    if store_ids:
        query["store_id"] = {"$in": store_ids}
    if flt.get("store_id"):
        query["store_id"] = flt["store_id"]
    if stage:
        query["stage"] = stage
    if source:
        query["source"] = source
    if service_id:
        query["service_id"] = service_id
    if start and period != "all" and stage in (None, "new", "contacted", "interested", "offered"):
        query["created_at"] = {"$gte": start.isoformat()}
    if q:
        query["$or"] = [{"name": {"$regex": q, "$options": "i"}},
                        {"phone_norm": {"$regex": q}}]
    field, _, direction = sort.partition(":")
    total = await db.leads.count_documents(query)
    leads = await db.leads.find(query, sort=[(field, -1 if direction == "desc" else 1)],
                                limit=min(limit, 200), skip=(page - 1) * limit)
    org = await rules.org_doc(db)
    return {"leads": await _enrich_leads(db, leads, org), "total": total,
            "page": page, "limit": limit}


@router.get("/leads/pipeline")
async def pipeline(flt: dict = Depends(require_section("leads")), db: Database = Depends(get_db)):
    store_ids = flt["store_ids"] or ([flt["store_id"]] if flt.get("store_id") else [])
    query = {"org_id": config.ORG_ID}
    query.update(ARCHIVE_FILTER)
    if store_ids:
        query["store_id"] = {"$in": store_ids}
    leads = await db.leads.find(query)
    org = await rules.org_doc(db)
    stages = {}
    for s in config.STAGES:
        stages[s] = [l for l in leads if l["stage"] == s]
    out = {}
    for s, ls in stages.items():
        ls.sort(key=lambda l: -l.get("score", 0))
        out[s] = {"total": len(ls), "leads": await _enrich_leads(db, ls[:6], org)}
    return {"stages": out}


@router.post("/leads", status_code=201)
async def add_lead(body: LeadIn, db: Database = Depends(get_db),
                   flt: dict = Depends(require_section("leads")), user: dict = Depends(current_user)):
    phone_norm = body.phone.strip()[-10:]
    if not body.name.strip():
        raise err("VALIDATION_ERROR", "Name is required")
    if len(phone_norm) != 10 or not phone_norm.isdigit():
        raise err("VALIDATION_ERROR", "Phone must be a 10-digit number")
    if not body.store_id or not await db.stores.find_one({"org_id": config.ORG_ID, "_id": body.store_id}):
        raise err("VALIDATION_ERROR", "Pick a valid store for the lead", 422)
    if not body.service_id or not await db.services.find_one({"org_id": config.ORG_ID, "_id": body.service_id}):
        raise err("VALIDATION_ERROR", "Pick a valid service for the lead", 422)
    ident = await db.identities.find_one({"org_id": config.ORG_ID, "phone_norm": phone_norm})
    if ident:  # BR-14: phone is the unique identity; LEAD-03 dedupe
        raise err("PHONE_EXISTS", "This phone number already exists as a lead or customer", 409,
                  {"lead_id": ident.get("lead_id"), "customer_id": ident.get("customer_id")})
    org = await rules.org_doc(db)
    lead = {"_id": dbm.new_id("led"), "org_id": config.ORG_ID, "name": body.name.strip(),
            "phone_norm": phone_norm, "phone_display": body.phone.strip(),
            "age_group": body.age_group, "gender": body.gender,
            "store_id": body.store_id, "service_id": body.service_id,
            "source": body.source if body.source in config.SOURCES else "others",
            "stage": "new", "score": 0, "score_factors": [], "follow_up_at": None,
            "offered_at": None, "lost_reason": None, "owner_id": None,
            "consent": {"whatsapp": body.consent_whatsapp, "sms": body.consent_sms},
            "customer_id": None, "notes": body.notes,
            "created_at": _now().isoformat(), "last_contact_at": None,
            "stage_history": _stage_push("new", "Lead created")}
    score, factors = await scoring.score_lead(db, lead, org)
    lead["score"], lead["score_factors"] = score, factors
    # LEAD-06: round-robin owner among sales users of the store
    owners = [u for u in await db.users.find({"org_id": config.ORG_ID, "roles": "sales", "status": "active"})
              if not store_scope(u) or (body.store_id and body.store_id in store_scope(u))]
    if owners:
        n = await db.leads.count_documents({"org_id": config.ORG_ID})
        lead["owner_id"] = owners[n % len(owners)]["_id"]
    await db.leads.insert_one(lead)
    await db.identities.insert_one({"org_id": config.ORG_ID, "phone_norm": phone_norm,
                                    "lead_id": lead["_id"]})
    # enabled "assign new leads" rules run on creation: internal assignment only
    # (no messages, nothing irreversible) - logged in the automation activity log
    assigned_by_rule = await automations.apply_assignment_rules(db, lead, user)
    if assigned_by_rule:
        await db.leads.update_one({"_id": lead["_id"]}, {"$set": {"owner_id": assigned_by_rule}})
        lead["owner_id"] = assigned_by_rule
    await audit(db, user, "lead.create", "lead", None, {"id": lead["_id"], "name": lead["name"]},
                entity_id=lead["_id"])
    await publish(db, "lead.created", {"lead_id": lead["_id"], "name": lead["name"],
                                       "score": score, "source": config.SOURCE_LABELS.get(lead["source"])},
                  store_id=body.store_id)
    enriched = (await _enrich_leads(db, [lead], org))[0]
    return enriched


@router.get("/leads/followups")
async def followups(when: str = "today", flt: dict = Depends(require_section("leads")),
                    db: Database = Depends(get_db)):
    now = _now()
    today = now.date().isoformat()
    tomorrow = (now + timedelta(days=1)).date().isoformat()
    query = {"org_id": config.ORG_ID, "follow_up_at": {"$ne": None},
             "stage": {"$nin": ["booked", "lost"]}}
    query.update(ARCHIVE_FILTER)
    if flt["store_ids"]:
        query["store_id"] = {"$in": flt["store_ids"]}
    leads = await db.leads.find(query)
    org = await rules.org_doc(db)
    out = []
    for l in leads:
        fu = l["follow_up_at"]
        day = fu[:10]
        if when == "overdue" and fu < now.isoformat():
            out.append(l)
        elif when == "today" and day == today:
            out.append(l)
        elif when == "tomorrow" and day == tomorrow:
            out.append(l)
    out.sort(key=lambda l: l["follow_up_at"])
    return {"leads": await _enrich_leads(db, out, org)}


@router.get("/leads/analytics")
async def lead_analytics(flt: dict = Depends(require_section("leads")), db: Database = Depends(get_db)):
    start, _ = rules.period_range(flt["period"], _now())
    query = {"org_id": config.ORG_ID}
    if flt["store_ids"]:
        query["store_id"] = {"$in": flt["store_ids"]}
    leads = await db.leads.find(query)
    if start:
        leads = [l for l in leads if (l.get("created_at") or "") >= start.isoformat()]
    funnel = {s: sum(1 for l in leads if l["stage"] == s) for s in config.STAGES}
    org = await rules.org_doc(db)
    ad_spend = (org.get("ad_spend") or {})  # rupees per source
    src_perf = {}
    for l in leads:
        src = l.get("source", "others")
        d = src_perf.setdefault(src, {"leads": 0, "booked": 0, "score_sum": 0})
        d["leads"] += 1
        d["booked"] += 1 if l["stage"] == "booked" else 0
        d["score_sum"] += l.get("score", 0)
    for src, d in src_perf.items():
        d["conversion"] = round(100 * d["booked"] / d["leads"]) if d["leads"] else 0
        d["avg_score"] = round(d["score_sum"] / d["leads"]) if d["leads"] else 0
        spend = ad_spend.get(src, 0) * 100
        d["cost_per_booking"] = int(spend / d["booked"]) if d["booked"] else None
        d["source_label"] = config.SOURCE_LABELS.get(src, src)

    # ------- reporting extras (all computed from real rows, no estimates)
    prio = await priority.compute_priority_many(db, leads)
    priority_counts = {"hot": 0, "warm": 0, "low": 0}
    for p in prio.values():
        priority_counts[p["tier"]] += 1
    now = _now()
    overdue = sum(1 for l in leads
                  if l.get("follow_up_at") and l["follow_up_at"] < now.isoformat()
                  and l["stage"] not in ("booked", "lost") and not l.get("archived"))
    lead_ids = [l["_id"] for l in leads]
    first_out: dict[str, str] = {}
    if lead_ids:
        inters = await db.interactions.find({"org_id": config.ORG_ID,
                                             "person_id": {"$in": lead_ids}, "direction": "out"})
        for i in inters:
            pid = i["person_id"]
            if pid not in first_out or (i.get("at") or "") < first_out[pid]:
                first_out[pid] = i.get("at")
    mins = []
    for l in leads:
        fo = first_out.get(l["_id"]) or l.get("last_contact_at")
        created = rules.parse_dt(l.get("created_at"))
        resp = rules.parse_dt(fo)
        if created and resp:
            mins.append(max(0, (resp - created).total_seconds() / 60))
    booked_total = funnel.get("booked", 0)
    return {"funnel": funnel, "sources": src_perf,
            "report": {"priority_counts": priority_counts,
                       "overdue_followups": overdue,
                       "avg_first_contact_min": round(sum(mins) / len(mins)) if mins else None,
                       "responded": len(mins),
                       "conversion_rate": round(100 * booked_total / len(leads)) if leads else 0,
                       "leads_in_range": len(leads)}}


# ---------------------------------------------- lead-to-booking outcomes (LEAD-15)
@router.get("/leads/outcomes")
async def lead_outcomes(service_id: str | None = None, source: str | None = None,
                        owner_id: str | None = None,
                        flt: dict = Depends(require_section("leads")), db: Database = Depends(get_db)):
    """Where follow-up work ends up: contacted -> booked -> attended -> renewed, or lost.
    Cohort: leads created in the selected window (plus the global store filter);
    every extra filter (service, source, staff member) narrows the cohort. All
    counts come from real rows - no estimates."""
    start, _ = rules.period_range(flt["period"], _now())
    query = {"org_id": config.ORG_ID}
    query.update(ARCHIVE_FILTER)
    if flt["store_ids"]:
        query["store_id"] = {"$in": flt["store_ids"]}
    if flt.get("store_id"):
        query["store_id"] = flt["store_id"]
    if service_id:
        query["service_id"] = service_id
    if source:
        query["source"] = source
    if owner_id:
        query["owner_id"] = owner_id
    leads = await db.leads.find(query)
    if start and flt["period"] != "all":
        leads = [l for l in leads if (l.get("created_at") or "") >= start.isoformat()]
    ids = [l["_id"] for l in leads]
    idset = set(ids)

    contacted = sum(1 for l in leads if l.get("last_contact_at"))
    booked_ids = {l["_id"] for l in leads if l["stage"] == "booked" or l.get("customer_id")}
    lost = sum(1 for l in leads if l["stage"] == "lost")

    attended_ids, renewed_ids = set(), set()
    if ids:
        for a in await db.appointments.find({"org_id": config.ORG_ID, "lead_id": {"$in": ids}}):
            if a.get("status") == "completed":
                attended_ids.add(a["lead_id"])
            if a.get("renew") and a.get("status") in ("booked", "completed"):
                renewed_ids.add(a["lead_id"])

    def _funnel(sub: list) -> dict:
        n = len(sub)
        sub_ids = {l["_id"] for l in sub}
        b = len(sub_ids & booked_ids)
        at = len(sub_ids & attended_ids)
        return {"leads": n,
                "contacted": sum(1 for l in sub if l.get("last_contact_at")),
                "booked": b,
                "attended": at,
                "renewed": len(sub_ids & renewed_ids),
                "lost": sum(1 for l in sub if l["stage"] == "lost"),
                "booking_rate": round(100 * b / n) if n else 0,
                "attendance_rate": round(100 * at / b) if b else 0}

    by_source, by_owner = {}, {}
    users = {u["_id"]: u["name"] for u in await db.users.find({"org_id": config.ORG_ID})}
    for l in leads:
        by_source.setdefault(l.get("source", "others"), []).append(l)
        if l.get("owner_id"):
            by_owner.setdefault(l["owner_id"], []).append(l)
    return {"funnel": _funnel(leads),
            "by_source": {config.SOURCE_LABELS.get(k, k): _funnel(v)
                          for k, v in sorted(by_source.items(), key=lambda kv: -len(kv[1]))},
            "by_owner": {users.get(k, k): _funnel(v)
                         for k, v in sorted(by_owner.items(), key=lambda kv: -len(kv[1]))[:8]},
            "owners": [{"_id": k, "name": v} for k, v in users.items()],
            "filters": {"service_id": service_id, "source": source, "owner_id": owner_id}}


# ------------------------------------------------------------------ lead hygiene
async def _automation_log(db, user, rule: dict, action: str, affected: int, sample: list):
    await db.automation_log.insert_one({
        "_id": dbm.new_id("alog"), "org_id": config.ORG_ID, "at": _now().isoformat(),
        "rule_id": rule.get("_id"), "rule_name": rule.get("name"), "type": rule.get("type"),
        "action": action, "affected": affected, "sample": sample[:5],
        "by": user.get("name") or "system"})


@router.get("/leads/hygiene")
async def lead_hygiene(flt: dict = Depends(require_section("leads")), db: Database = Depends(get_db)):
    """Review queue: duplicate phones, incomplete records and stale open leads.
    Every item explains what needs attention; nothing is changed automatically."""
    now = _now()
    q = {"org_id": config.ORG_ID}
    q.update(ARCHIVE_FILTER)
    if flt["store_ids"]:
        q["store_id"] = {"$in": flt["store_ids"]}
    leads = await db.leads.find(q)
    org = await rules.org_doc(db)
    enriched = await _enrich_leads(db, leads, org)
    users = {u["_id"]: u for u in await db.users.find({"org_id": config.ORG_ID})}

    by_phone: dict[str, list] = {}
    for l in enriched:
        by_phone.setdefault(l.get("phone_norm"), []).append(l)
    duplicates = [{"kind": "phone", "key": p, "leads": ls}
                  for p, ls in by_phone.items() if p and len(ls) > 1]
    # name-based: the phone index already blocks exact phone duplicates, so also
    # flag identical names on different numbers (verify with the customer first)
    by_name: dict[str, list] = {}
    for l in enriched:
        if l["stage"] in ("booked", "lost"):
            continue
        key = " ".join((l.get("name") or "").lower().split())
        if key:
            by_name.setdefault(key, []).append(l)
    for key, ls in by_name.items():
        if len(ls) > 1 and len({l.get("phone_norm") for l in ls}) > 1:
            duplicates.append({"kind": "name", "key": ls[0]["name"], "leads": ls})

    incomplete, stale = [], []
    week_ago = (now - timedelta(days=10)).isoformat()
    for l in enriched:
        missing = []
        if not l.get("store_id"):
            missing.append("store")
        if not l.get("service_id"):
            missing.append("service")
        if not l.get("owner_id"):
            missing.append("owner")
        if not l.get("consent", {}).get("whatsapp") and not l.get("consent", {}).get("sms"):
            missing.append("consent (no WhatsApp or SMS opt-in)")
        if missing and l["stage"] not in ("booked", "lost"):
            incomplete.append({"lead": l, "missing": missing})
        if l["stage"] in ("contacted", "interested", "offered") and \
                (l.get("last_contact_at") or l.get("created_at")) < week_ago:
            stale.append({"lead": l,
                          "reason": f"Open {l['stage']} lead with no logged contact in 10+ days"})
    return {"duplicates": duplicates,
            "incomplete": incomplete[:20],
            "stale": stale[:20],
            "counts": {"duplicates": len(duplicates), "incomplete": len(incomplete),
                       "stale": len(stale)},
            "owners": [{"_id": u["_id"], "name": u["name"]} for u in users.values()]}


@router.post("/leads/merge")
async def merge_leads(body: MergeIn, db: Database = Depends(get_db),
                      flt: dict = Depends(require_section("leads")), user: dict = Depends(current_user)):
    """Merge a duplicate lead into the primary (explicit user action only).
    Activity history - interactions, campaign messages, appointments, waitlist,
    consent log - is re-pointed to the primary so nothing is lost; the duplicate
    is archived with a pointer instead of deleted."""
    primary = await db.leads.find_one({"_id": body.primary_id})
    dup = await db.leads.find_one({"_id": body.duplicate_id})
    if not primary or not dup:
        raise err("NOT_FOUND", "Lead not found", 404)
    if primary["_id"] == dup["_id"]:
        raise err("VALIDATION_ERROR", "Pick two different leads", 422)
    if dup.get("archived"):
        raise err("VALIDATION_ERROR", "Duplicate is already archived", 422)
    # keep the richer record as the data source
    updates = {}
    if dup.get("score", 0) > primary.get("score", 0):
        updates.update({"score": dup["score"], "score_factors": dup.get("score_factors")})
    if dup.get("notes") and not primary.get("notes"):
        updates["notes"] = dup["notes"]
    if (dup.get("consent") or {}).get("whatsapp") and not (primary.get("consent") or {}).get("whatsapp"):
        updates["consent"] = dup.get("consent")
    if updates:
        await db.leads.update_one({"_id": primary["_id"]}, {"$set": updates})
    # re-point every reference so the timeline stays complete on the primary
    await db.interactions.update_many({"person_id": dup["_id"]},
                                      {"$set": {"person_id": primary["_id"]}})
    await db.campaign_messages.update_many({"person_id": dup["_id"], "person_kind": "leads"},
                                           {"$set": {"person_id": primary["_id"]}})
    await db.appointments.update_many({"lead_id": dup["_id"]},
                                      {"$set": {"lead_id": primary["_id"],
                                                "customer_id": primary.get("customer_id"),
                                                "person_id": primary.get("customer_id") or primary["_id"]}})
    await db.waitlist.update_many({"person_id": dup["_id"]},
                                  {"$set": {"person_id": primary["_id"]}})
    await db.consent_log.update_many({"person_id": dup["_id"]},
                                     {"$set": {"person_id": primary["_id"]}})
    # identity: point the phone at the primary; drop the now-duplicate identity row
    ident_dup = await db.identities.find_one({"org_id": config.ORG_ID, "lead_id": dup["_id"]})
    ident_pri = await db.identities.find_one({"org_id": config.ORG_ID, "phone_norm": primary["phone_norm"]})
    if ident_dup and ident_pri and ident_dup["_id"] != ident_pri["_id"]:
        await db.identities.delete_one({"_id": ident_dup["_id"]})
    elif ident_dup:
        await db.identities.update_one({"_id": ident_dup["_id"]},
                                       {"$set": {"lead_id": primary["_id"]}})
    await db.leads.update_one({"_id": dup["_id"]},
                              {"$set": {"archived": True, "merged_into": primary["_id"],
                                        "merged_at": _now().isoformat()}})
    await db.interactions.insert_one({
        "_id": dbm.new_id("int"), "org_id": config.ORG_ID, "person_id": primary["_id"],
        "person_name": primary["name"], "channel": "note", "direction": "out",
        "content": f"Merged duplicate lead {dup['name']} ({dup['phone_norm']}) into this record",
        "outcome": "merged", "user_id": user["_id"], "user_name": user["name"],
        "at": _now().isoformat()})
    await audit(db, user, "lead.merge", "lead", {"duplicate": dup["_id"], "duplicate_name": dup["name"]},
                {"primary": primary["_id"]}, entity_id=primary["_id"])
    await publish(db, "lead.merged", {"primary": primary["name"], "duplicate": dup["name"]},
                  store_id=primary.get("store_id"))
    return {"ok": True, "primary_id": primary["_id"]}


@router.post("/leads/{lead_id}/archive")
async def archive_lead(lead_id: str, db: Database = Depends(get_db),
                       flt: dict = Depends(require_section("leads")), user: dict = Depends(current_user)):
    lead = await db.leads.find_one({"_id": lead_id})
    if not lead:
        raise err("NOT_FOUND", "Lead not found", 404)
    if lead.get("archived"):
        raise err("VALIDATION_ERROR", "Lead is already archived", 422)
    await db.leads.update_one({"_id": lead_id},
                              {"$set": {"archived": True, "archived_at": _now().isoformat(),
                                        "archived_by": user["name"]}})
    await audit(db, user, "lead.archive", "lead", {"archived": False},
                {"archived": True, "name": lead["name"]}, entity_id=lead_id)
    return {"ok": True}


@router.post("/leads/{lead_id}/unarchive")
async def unarchive_lead(lead_id: str, db: Database = Depends(get_db),
                         flt: dict = Depends(require_section("leads")), user: dict = Depends(current_user)):
    lead = await db.leads.find_one({"_id": lead_id})
    if not lead:
        raise err("NOT_FOUND", "Lead not found", 404)
    await db.leads.update_one({"_id": lead_id},
                              {"$unset": {"archived": "", "archived_at": "", "archived_by": ""}})
    await audit(db, user, "lead.unarchive", "lead", {"archived": True},
                {"archived": False, "name": lead["name"]}, entity_id=lead_id)
    return {"ok": True}


@router.get("/leads/export", response_class=PlainTextResponse)
async def export_csv(flt: dict = Depends(require_section("leads")), db: Database = Depends(get_db)):
    start, _ = rules.period_range(flt["period"], _now())
    query = {"org_id": config.ORG_ID}
    if flt["store_ids"]:
        query["store_id"] = {"$in": flt["store_ids"]}
    leads = await db.leads.find(query)
    if start and flt["period"] != "all":
        leads = [l for l in leads if (l.get("created_at") or "") >= start.isoformat()]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "name", "phone", "store", "service", "source", "stage", "score",
                     "band", "follow_up_at", "lost_reason", "created_at"])
    enriched = await _enrich_leads(db, leads, await rules.org_doc(db))
    for l in enriched:
        writer.writerow([l["_id"], l["name"], l["phone_norm"], l.get("store_name"),
                         l.get("service_name"), l.get("source_label"), l["stage"], l.get("score"),
                         l.get("band"), l.get("follow_up_at"), l.get("lost_reason"), l.get("created_at")])
    await audit(db, {"_id": None, "name": "export"}, "lead.export", "lead", None,
                {"count": len(enriched)})
    return buf.getvalue()


@router.get("/leads/{lead_id}")
async def get_lead(lead_id: str, db: Database = Depends(get_db),
                   flt: dict = Depends(require_section("leads")), user: dict = Depends(current_user)):
    lead = await db.leads.find_one({"_id": lead_id})
    if not lead:
        raise err("NOT_FOUND", "Lead not found", 404)
    org = await rules.org_doc(db)
    interactions = await db.interactions.find({"person_id": lead_id}, sort=[("at", -1)], limit=50)
    appointments = await db.appointments.find({"lead_id": lead_id}, sort=[("date", -1)])
    owner = await db.users.find_one({"_id": lead.get("owner_id")}) if lead.get("owner_id") else None
    customer = await db.customers.find_one({"_id": lead.get("customer_id")}) if lead.get("customer_id") else None
    sla = scoring.sla_for(org, lead.get("source", "others"))
    deadline = (datetime.fromisoformat(lead["created_at"]) + timedelta(minutes=sla)).isoformat()
    enriched = (await _enrich_leads(db, [lead], org))[0]
    return {**enriched, "interactions": interactions, "appointments": appointments,
            "owner_name": owner.get("name") if owner else None,
            "customer": customer, "sla_deadline": deadline}


@router.put("/leads/{lead_id}")
async def edit_lead(lead_id: str, body: LeadIn, db: Database = Depends(get_db),
                    flt: dict = Depends(require_section("leads")), user: dict = Depends(current_user)):
    lead = await db.leads.find_one({"_id": lead_id})
    if not lead:
        raise err("NOT_FOUND", "Lead not found", 404)
    if lead.get("stage") == "booked":  # BR-19: store/service locked while booked
        body.store_id, body.service_id = lead.get("store_id"), lead.get("service_id")
    before = {"name": lead["name"], "phone": lead["phone_norm"], "store_id": lead.get("store_id"),
              "service_id": lead.get("service_id")}
    phone_norm = body.phone.strip()[-10:]
    if len(phone_norm) != 10 or not phone_norm.isdigit():
        raise err("VALIDATION_ERROR", "Phone must be a 10-digit number")
    if phone_norm != lead["phone_norm"]:
        clash = await db.identities.find_one({"org_id": config.ORG_ID, "phone_norm": phone_norm})
        if clash:
            raise err("PHONE_EXISTS", "Another lead/customer already uses this phone", 409)
    updates = {"name": body.name.strip(), "phone_norm": phone_norm,
               "phone_display": body.phone.strip(), "age_group": body.age_group,
               "gender": body.gender, "store_id": body.store_id or lead.get("store_id"),
               "service_id": body.service_id or lead.get("service_id"),
               "source": body.source, "notes": body.notes,
               "consent": {"whatsapp": body.consent_whatsapp, "sms": body.consent_sms}}
    merged = {**lead, **updates}
    score, factors = await scoring.score_lead(db, merged, await rules.org_doc(db))
    updates["score"], updates["score_factors"] = score, factors
    await db.leads.update_one({"_id": lead_id}, {"$set": updates})
    if phone_norm != lead["phone_norm"]:
        await db.identities.update_one({"org_id": config.ORG_ID, "phone_norm": lead["phone_norm"]},
                                       {"$unset": {"lead_id": ""}})
        await db.identities.insert_one({"org_id": config.ORG_ID, "phone_norm": phone_norm,
                                        "lead_id": lead_id})
    # 12.7: propagate edits to linked customer
    if lead.get("customer_id"):
        await db.customers.update_one({"_id": lead["customer_id"]},
                                      {"$set": {"name": updates["name"], "phone_norm": phone_norm,
                                                "phone_display": updates["phone_display"],
                                                "store_id": updates["store_id"]}})
    await audit(db, user, "lead.update", "lead", before, updates, entity_id=lead_id)
    return await db.leads.find_one({"_id": lead_id})


@router.post("/leads/{lead_id}/stage")
async def stage(lead_id: str, body: StageIn, db: Database = Depends(get_db),
                flt: dict = Depends(require_section("leads")), user: dict = Depends(current_user)):
    lead = await db.leads.find_one({"_id": lead_id})
    if not lead:
        raise err("NOT_FOUND", "Lead not found", 404)
    if body.stage not in config.STAGES:
        raise err("VALIDATION_ERROR", "Unknown stage")
    if body.stage == "booked":
        raise err("VALIDATION_ERROR", "Use the booking picker to book - stage moves automatically")
    if lead["stage"] == "booked":
        raise err("VALIDATION_ERROR", "A booked lead cannot move stages (BR-18/19)")
    updates = {"stage": body.stage, "stage_history": _append_stage(lead, body.stage, body.note or "")}
    if body.stage == "offered":
        updates["offered_at"] = _now().isoformat()
    if body.stage == "contacted":
        updates["last_contact_at"] = _now().isoformat()
    await db.leads.update_one({"_id": lead_id}, {"$set": updates})
    await audit(db, user, "lead.stage", "lead", {"stage": lead["stage"]},
                {"stage": body.stage}, entity_id=lead_id)
    return await db.leads.find_one({"_id": lead_id})


@router.post("/leads/{lead_id}/follow-up")
async def follow_up(lead_id: str, body: FollowUpIn, db: Database = Depends(get_db),
                    flt: dict = Depends(require_section("leads")), user: dict = Depends(current_user)):
    lead = await db.leads.find_one({"_id": lead_id})
    if not lead:
        raise err("NOT_FOUND", "Lead not found", 404)
    try:
        fu = datetime.fromisoformat(body.follow_up_at)
    except ValueError:
        raise err("VALIDATION_ERROR", "Invalid follow-up date")
    if fu.tzinfo is None:  # normalize datetime-local input to UTC so every
        fu = fu.replace(tzinfo=timezone.utc)  # stored timestamp stays comparable
    await db.leads.update_one({"_id": lead_id}, {"$set": {"follow_up_at": fu.isoformat()}})
    await audit(db, user, "lead.followup", "lead", {"follow_up_at": lead.get("follow_up_at")},
                {"follow_up_at": body.follow_up_at}, entity_id=lead_id)
    return {"ok": True}


@router.post("/leads/{lead_id}/lost")
async def mark_lost(lead_id: str, body: LostIn, db: Database = Depends(get_db),
                    flt: dict = Depends(require_section("leads")), user: dict = Depends(current_user)):
    lead = await db.leads.find_one({"_id": lead_id})
    if not lead:
        raise err("NOT_FOUND", "Lead not found", 404)
    if lead["stage"] == "booked":  # BR-18
        raise err("VALIDATION_ERROR", "A booked lead cannot be lost until its appointment is cancelled")
    if body.reason not in config.LOST_REASONS:
        raise err("VALIDATION_ERROR", f"Reason must be one of {config.LOST_REASONS}")
    await db.leads.update_one({"_id": lead_id}, {
        "$set": {"stage": "lost", "lost_reason": body.reason,
                 "stage_history": _append_stage(lead, "lost", body.reason)}})
    await audit(db, user, "lead.lost", "lead", {"stage": lead["stage"]},
                {"stage": "lost", "reason": body.reason}, entity_id=lead_id)
    return {"ok": True}


@router.post("/leads/{lead_id}/reopen")
async def reopen(lead_id: str, db: Database = Depends(get_db),
                 flt: dict = Depends(require_section("leads")), user: dict = Depends(current_user)):
    lead = await db.leads.find_one({"_id": lead_id})
    if not lead:
        raise err("NOT_FOUND", "Lead not found", 404)
    await db.leads.update_one({"_id": lead_id}, {
        "$set": {"stage": "contacted", "lost_reason": None,
                 "follow_up_at": (_now() + timedelta(days=1)).isoformat(),
                 "stage_history": _append_stage(lead, "contacted", "Reopened")}})
    await audit(db, user, "lead.reopen", "lead", {"stage": "lost"}, {"stage": "contacted"},
                entity_id=lead_id)
    return await db.leads.find_one({"_id": lead_id})


@router.post("/leads/{lead_id}/interactions")
async def log_interaction(lead_id: str, body: InteractionIn, db: Database = Depends(get_db),
                          flt: dict = Depends(require_section("leads")), user: dict = Depends(current_user)):
    lead = await db.leads.find_one({"_id": lead_id})
    if not lead:
        raise err("NOT_FOUND", "Lead not found", 404)
    await db.interactions.insert_one({
        "_id": dbm.new_id("int"), "org_id": config.ORG_ID, "person_id": lead_id,
        "person_name": lead["name"], "channel": body.channel, "direction": body.direction,
        "content": body.content or "", "outcome": body.outcome or "logged",
        "user_id": user["_id"], "user_name": user["name"], "at": _now().isoformat()})
    updates = {"last_contact_at": _now().isoformat()}
    if body.direction == "in" and body.channel in ("whatsapp", "sms", "call"):
        merged = {**lead}
        score, factors = await scoring.score_lead(db, merged, await rules.org_doc(db))
        if score != lead.get("score"):
            updates["score"], updates["score_factors"] = score, factors
    await db.leads.update_one({"_id": lead_id}, {"$set": updates})
    await publish(db, "interaction.logged", {"lead_id": lead_id, "channel": body.channel,
                                             "person": lead["name"]}, store_id=lead.get("store_id"))
    return {"ok": True}


@router.post("/leads/import")
async def import_csv(body: ImportIn, db: Database = Depends(get_db),
                     flt: dict = Depends(require_section("leads")), user: dict = Depends(current_user)):
    """LEAD-12: CSV import with validation and duplicate check; rejected rows reported."""
    reader = csv.DictReader(io.StringIO(body.csv))
    created, rejected, duplicates = 0, [], 0
    stores = {s["area"].lower(): s for s in await db.stores.find({"org_id": config.ORG_ID})}
    services = {}
    for s in await db.services.find({"org_id": config.ORG_ID}):
        services[s["name"].lower()] = s
    org = await rules.org_doc(db)
    for i, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        phone = (row.get("phone") or "").strip()[-10:]
        if not name or not phone or not phone.isdigit():
            rejected.append({"row": i, "reason": "missing/invalid name or phone"})
            continue
        if await db.identities.find_one({"org_id": config.ORG_ID, "phone_norm": phone}):
            duplicates += 1
            continue
        store = stores.get((row.get("area") or "").strip().lower())
        service = services.get((row.get("service") or "").strip().lower())
        lead = {"_id": dbm.new_id("led"), "org_id": config.ORG_ID, "name": name,
                "phone_norm": phone, "phone_display": phone,
                "store_id": store["_id"] if store else None,
                "service_id": service["_id"] if service else None,
                "source": row.get("source", "others") if row.get("source", "others") in config.SOURCES else "others",
                "stage": "new", "score": 0, "score_factors": [], "consent": {},
                "customer_id": None, "created_at": _now().isoformat(),
                "stage_history": _stage_push("new", "CSV import")}
        score, factors = await scoring.score_lead(db, lead, org)
        lead["score"], lead["score_factors"] = score, factors
        await db.leads.insert_one(lead)
        await db.identities.insert_one({"org_id": config.ORG_ID, "phone_norm": phone,
                                        "lead_id": lead["_id"]})
        created += 1
    await audit(db, user, "lead.import", "lead", None, {"created": created, "rejected": len(rejected)})
    return {"created": created, "duplicates": duplicates, "rejected": rejected}


# ------------------------------------------------------------------ customers
def _next_best_action(c, now):
    plan = c.get("plan") or {}
    end = plan.get("end")
    if end:
        end_dt = rules.parse_dt(end)
        if not end_dt:
            return "Healthy plan - schedule the next visit"
        days = (end_dt - now).days
        if days > 0:
            return f"Renewal due in {days} days - book renewal and hold a slot"
        days = -days
        if days <= 7:
            return f"Expired {days}d ago - call now with a comeback offer"
        if days <= 21:
            return f"Expired {days}d ago - send staged win-back message"
        return f"Expired {days}d ago - final win-back call"
    return "Healthy plan - schedule the next visit"


@router.get("/customers")
async def list_customers(segment: str | None = None, q: str | None = None, page: int = 1,
                         limit: int = 20, flt: dict = Depends(require_section("customers")),
                         db: Database = Depends(get_db)):
    query = {"org_id": config.ORG_ID}
    if flt["store_ids"]:
        query["store_id"] = {"$in": flt["store_ids"]}
    if q:
        query["$or"] = [{"name": {"$regex": q, "$options": "i"}}, {"phone_norm": {"$regex": q}}]
    total = await db.customers.count_documents(query)
    customers = await db.customers.find(query, sort=[("spend", -1)], limit=min(limit, 200),
                                        skip=(page - 1) * limit)
    stores = {s["_id"]: s["name"] for s in await db.stores.find({"org_id": config.ORG_ID})}
    services = {s["_id"]: s["name"] for s in await db.services.find({"org_id": config.ORG_ID})}
    now = _now()
    out = []
    for c in customers:
        seg = rules.segment_for(c, now)
        if segment and seg != segment:
            continue
        out.append({**c, "segment": seg, "segment_label": rules.SEGMENT_LABELS.get(seg, seg),
                    "store_name": stores.get(c.get("store_id")),
                    "last_service_name": services.get(c.get("last_service_id")),
                    "next_best_action": _next_best_action(c, now),
                    "spend_display": rules.fmt_money(c.get("spend", 0))})
    return {"customers": out, "total": total, "page": page, "limit": limit}


@router.get("/customers/segments")
async def customer_segments_list(flt: dict = Depends(require_section("customers")),
                                 db: Database = Depends(get_db)):
    q = {"org_id": config.ORG_ID}
    if flt["store_ids"]:
        q["store_id"] = {"$in": flt["store_ids"]}
    customers = await db.customers.find(q)
    now = _now()
    counts = {}
    for c in customers:
        seg = rules.segment_for(c, now)
        counts[seg] = counts.get(seg, 0) + 1
    return {"segments": [{"key": k, "label": rules.SEGMENT_LABELS.get(k, k), "count": v}
                         for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]}


@router.get("/customers/{customer_id}")
async def get_customer(customer_id: str, db: Database = Depends(get_db),
                       flt: dict = Depends(require_section("customers")), user: dict = Depends(current_user)):
    c = await db.customers.find_one({"_id": customer_id})
    if not c:
        raise err("NOT_FOUND", "Customer not found", 404)
    now = _now()
    seg = rules.segment_for(c, now)
    interactions = await db.interactions.find({"person_id": customer_id}, sort=[("at", -1)], limit=50)
    appointments = await db.appointments.find({"customer_id": customer_id}, sort=[("date", -1)])
    payments = await db.payments.find({"customer_id": customer_id}, sort=[("created_at", -1)])
    lead = await db.leads.find_one({"_id": c.get("lead_id")}) if c.get("lead_id") else None
    services = {s["_id"]: s["name"] for s in await db.services.find({"org_id": config.ORG_ID})}
    stores = {s["_id"]: s["name"] for s in await db.stores.find({"org_id": config.ORG_ID})}
    return {**c, "segment": seg, "segment_label": rules.SEGMENT_LABELS.get(seg, seg),
            "store_name": stores.get(c.get("store_id")),
            "last_service_name": services.get(c.get("last_service_id")),
            "next_best_action": _next_best_action(c, now), "interactions": interactions,
            "appointments": appointments, "payments": payments, "source_lead": lead,
            "spend_display": rules.fmt_money(c.get("spend", 0))}


@router.get("/customers/{customer_id}/timeline")
async def customer_timeline(customer_id: str, limit: int = 100,
                            flt: dict = Depends(require_section("customers")),
                            db: Database = Depends(get_db)):
    """CUST-08: unified conversation history - one timeline per person covering the
    lead phase (creation, stage moves, first calls, notes), conversion, calls,
    messages, appointment changes, campaign responses, payments, renewals and
    consent events."""
    c = await db.customers.find_one({"_id": customer_id})
    if not c:
        raise err("NOT_FOUND", "Customer not found", 404)
    lead = await db.leads.find_one({"_id": c["lead_id"]}) if c.get("lead_id") else None
    lead_id = lead["_id"] if lead else None
    person_ids = [customer_id] + ([lead_id] if lead_id else [])
    events = []
    channels = {"call": "Call", "whatsapp": "WhatsApp", "sms": "SMS", "note": "Note",
                "visit": "Meeting"}
    # interactions carry the lead id until conversion - match both phases
    for i in await db.interactions.find({"$or": [{"person_id": {"$in": person_ids}},
                                                 {"customer_id": customer_id}]},
                                        sort=[("at", -1)], limit=60):
        ch = channels.get(i.get("channel"), i.get("channel") or "message")
        events.append({"at": i.get("at"), "type": f"message_{i.get('channel')}",
                       "icon": "phone" if i.get("channel") == "call" else "chat",
                       "direction": i.get("direction"),
                       "title": f"{ch} {'from' if i.get('direction') == 'in' else 'to'} {i.get('person_name')}",
                       "detail": i.get("content"), "by": i.get("user_name")})
    # lead-phase stage history (created -> contacted -> offered -> booked)
    if lead:
        stage_labels = dict(getattr(config, "STAGE_LABELS", {}) or {})
        for h in (lead.get("stage_history") or []):
            st = h.get("stage")
            events.append({"at": h.get("at"), "type": f"lead_{st}", "icon": "lead",
                           "title": f"Lead: {stage_labels.get(st, st or 'update')}",
                           "detail": h.get("note") or None, "by": None})
    services = {s["_id"]: s["name"] for s in await db.services.find({"org_id": config.ORG_ID})}
    stores = {s["_id"]: s["name"] for s in await db.stores.find({"org_id": config.ORG_ID})}
    status_text = {"booked": "Appointment booked", "completed": "Visit completed",
                   "cancelled": "Appointment cancelled", "no_show": "Marked as no-show"}
    for a in await db.appointments.find({"$or": [{"customer_id": customer_id},
                                                 {"lead_id": lead_id} if lead_id else {"customer_id": customer_id}]}):
        svc = a.get("service_name") or services.get(a.get("service_id"), "appointment")
        base = f"{svc} at {stores.get(a.get('store_id'), '-')}" + (" (renewal)" if a.get("renew") else "")
        for h in (a.get("status_history") or []):
            events.append({"at": h.get("at"), "type": f"appointment_{h.get('status')}",
                           "icon": "calendar",
                           "title": status_text.get(h.get("status"), f"Appointment {h.get('status')}"),
                           "detail": base + (f" ({h.get('reason')})" if h.get("reason") else ""),
                           "by": h.get("by")})
    camp_names = {cm["_id"]: cm["name"] for cm in await db.campaigns.find({"org_id": config.ORG_ID})}
    for m in await db.campaign_messages.find({"person_id": {"$in": person_ids}},
                                             sort=[("created_at", -1)], limit=30):
        events.append({"at": m.get("sent_at") or m.get("created_at"),
                       "type": f"campaign_{m.get('status')}", "icon": "megaphone",
                       "title": f"Campaign: {camp_names.get(m.get('campaign_id'), m.get('campaign_id'))}",
                       "detail": f"{m.get('rendered_text')} [{m.get('status')}]"})
    for p in await db.payments.find({"customer_id": customer_id}, sort=[("created_at", -1)], limit=30):
        events.append({"at": p.get("paid_at") or p.get("created_at"), "type": "payment",
                       "icon": "wallet",
                       "title": f"Payment {rules.fmt_money(p.get('amount', 0))} ({p.get('method')})",
                       "detail": f"Status: {p.get('status')}"})
    for cl in await db.consent_log.find({"person_id": {"$in": person_ids}}, sort=[("at", -1)], limit=20):
        events.append({"at": cl.get("at"), "type": f"consent_{cl.get('action')}", "icon": "shield",
                       "title": f"Consent {cl.get('action')} - {cl.get('channel')}",
                       "detail": cl.get("source") or (f"by {cl.get('by')}" if cl.get("by") else None)})
    events = [e for e in events if e.get("at")]
    events.sort(key=lambda e: e["at"], reverse=True)
    return {"customer": {"name": c["name"], "phone_display": c.get("phone_display")},
            "lead": {"id": lead_id, "name": lead["name"]} if lead else None,
            "timeline": events[:limit]}


@router.put("/customers/{customer_id}")
async def edit_customer(customer_id: str, body: dict, db: Database = Depends(get_db),
                        flt: dict = Depends(require_section("customers")), user: dict = Depends(current_user)):
    c = await db.customers.find_one({"_id": customer_id})
    if not c:
        raise err("NOT_FOUND", "Customer not found", 404)
    allowed = {k: body[k] for k in ("name", "phone_display", "store_id", "consent", "tags", "notes")
               if k in body}
    if "phone_norm" in body:
        pn = str(body["phone_norm"]).strip()[-10:]
        if pn != c["phone_norm"]:
            clash = await db.identities.find_one({"org_id": config.ORG_ID, "phone_norm": pn})
            if clash:
                raise err("PHONE_EXISTS", "Another record already uses this phone", 409)
            allowed["phone_norm"] = pn
    await db.customers.update_one({"_id": customer_id}, {"$set": allowed})
    # 12.7: propagate to source lead
    if c.get("lead_id"):
        await db.leads.update_one({"_id": c["lead_id"]},
                                  {"$set": {k: v for k, v in allowed.items()
                                            if k in ("name", "phone_norm", "phone_display", "store_id")}})
    await audit(db, user, "customer.update", "customer",
                {"name": c["name"]}, allowed, entity_id=customer_id)
    return await db.customers.find_one({"_id": customer_id})


@router.post("/customers/{customer_id}/consent")
async def consent(customer_id: str, body: ConsentIn, db: Database = Depends(get_db),
                  flt: dict = Depends(require_section("customers")), user: dict = Depends(current_user)):
    c = await db.customers.find_one({"_id": customer_id})
    if not c:
        raise err("NOT_FOUND", "Customer not found", 404)
    consent = dict(c.get("consent") or {})
    consent[body.channel] = body.action == "opt-in"
    if body.action == "dnd":
        consent["dnd"] = True
    await db.customers.update_one({"_id": customer_id}, {"$set": {"consent": consent}})
    await db.consent_log.insert_one({"_id": dbm.new_id("clog"), "org_id": config.ORG_ID,
                                     "person_id": customer_id, "channel": body.channel,
                                     "action": body.action, "by": user["name"],
                                     "at": _now().isoformat()})
    await audit(db, user, "customer.consent", "customer", None,
                {"channel": body.channel, "action": body.action}, entity_id=customer_id)
    return {"ok": True, "consent": consent}


@router.post("/customers/{customer_id}/interactions")
async def log_customer_interaction(customer_id: str, body: InteractionIn, db: Database = Depends(get_db),
                                   flt: dict = Depends(require_section("customers")), user: dict = Depends(current_user)):
    """CUST-06: log a manual outbound message (WhatsApp/SMS/call) to a customer."""
    c = await db.customers.find_one({"_id": customer_id})
    if not c:
        raise err("NOT_FOUND", "Customer not found", 404)
    if body.direction == "out" and body.channel in ("whatsapp", "sms") and not (c.get("consent") or {}).get(body.channel):
        raise err("CONSENT_MISSING", f"Customer has not opted in to {body.channel} - record consent first", 403)
    await db.interactions.insert_one({
        "_id": dbm.new_id("int"), "org_id": config.ORG_ID, "person_id": customer_id,
        "person_name": c["name"], "channel": body.channel, "direction": body.direction,
        "content": body.content or "", "outcome": body.outcome or "logged",
        "user_id": user["_id"], "user_name": user["name"], "at": _now().isoformat()})
    await db.customers.update_one({"_id": customer_id}, {"$set": {"last_contact_at": _now().isoformat()}})
    await publish(db, "interaction.logged", {"customer_id": customer_id, "channel": body.channel,
                                             "person": c["name"]}, store_id=c.get("store_id"))
    await audit(db, user, "customer.message", "customer", None,
                {"channel": body.channel, "outcome": body.outcome or "logged"}, entity_id=customer_id)
    return {"ok": True}


@router.post("/customers/merge")
async def merge(body: MergeIn, db: Database = Depends(get_db),
                flt: dict = Depends(require_section("customers")), user: dict = Depends(current_user)):
    primary = await db.customers.find_one({"_id": body.primary_id})
    dup = await db.customers.find_one({"_id": body.duplicate_id})
    if not primary or not dup or primary["_id"] == dup["_id"]:
        raise err("VALIDATION_ERROR", "Pick two different customer records")
    if primary.get("plan") is None and dup.get("plan"):
        await db.customers.update_one({"_id": primary["_id"]}, {"$set": {"plan": dup["plan"]}})
    await db.customers.update_one({"_id": primary["_id"]},
                                  {"$inc": {"visits": dup.get("visits", 0), "spend": dup.get("spend", 0)}})
    await db.appointments.update_many({"customer_id": dup["_id"]}, {"$set": {"customer_id": primary["_id"]}})
    await db.interactions.update_many({"person_id": dup["_id"]}, {"$set": {"person_id": primary["_id"]}})
    await db.customers.delete_one({"_id": dup["_id"]})
    await db.identities.update_one({"org_id": config.ORG_ID, "phone_norm": dup["phone_norm"]},
                                   {"$set": {"customer_id": primary["_id"]}})
    await audit(db, user, "customer.merge", "customer", {"duplicate": dup["_id"]},
                {"primary": primary["_id"]}, entity_id=primary["_id"])
    return {"ok": True}
