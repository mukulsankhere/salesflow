"""Dashboard & analytics (DASH-01..DASH-03), alerts (RT-03), live feed + SSE (RT-01/02)."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from .. import capacity, config, rules, scoring
from ..deps import current_user, resolve_filter, store_scope
from ..db import Database, get_db
from ..realtime import format_sse, subscribe, unsubscribe

router = APIRouter(prefix="/api/v1")


def _now():
    return datetime.now(timezone.utc)


async def _window(db, period, store_ids):
    """(start, prev_start, now) for KPI deltas."""
    now = _now()
    days = {"today": 1, "7": 7, "30": 30, "90": 90}.get(period)
    if not days:
        return None, None, now
    start = now - timedelta(days=days)
    prev_start = now - timedelta(days=2 * days)
    return start, prev_start, now


@router.get("/analytics/kpis")
async def kpis(flt: dict = Depends(resolve_filter), db: Database = Depends(get_db)):
    period, store_ids = flt["period"], flt["store_ids"]
    start, prev_start, now = await _window(db, period, store_ids)

    async def collect(ws):
        q = {"org_id": config.ORG_ID}
        if store_ids:
            q["store_id"] = {"$in": store_ids}
        leads = await db.leads.find(q)
        if ws:
            leads = [l for l in leads if (l.get("created_at") or "") >= ws.isoformat()]
        appts_q = dict(q)
        appts = await db.appointments.find(appts_q)
        if ws:
            appts = [a for a in appts if (a.get("date") or "") >= ws.strftime("%Y-%m-%d")]
        book_appts = [a for a in appts if a.get("status") in ("booked", "completed")]
        lead_appts = [a for a in book_appts if a.get("lead_id")]
        leads_booked = len({a["lead_id"] for a in lead_appts})
        pay_q = {"org_id": config.ORG_ID, "status": "paid"}
        pays = await db.payments.find(pay_q)
        if ws:
            pays = [p for p in pays if (p.get("paid_at") or p.get("created_at") or "") >= ws.isoformat()]
        revenue = sum(p.get("amount", 0) for p in pays)
        cus_q = {"org_id": config.ORG_ID}
        if store_ids:
            cus_q["store_id"] = {"$in": store_ids}
        customers = await db.customers.find(cus_q)
        if ws:
            customers = [c for c in customers
                         if c.get("last_visit_at") and c["last_visit_at"] >= ws.isoformat()]
        repeat = sum(1 for c in customers if (c.get("visits") or 0) >= 2)
        # utilisation over the window (daily average across stores)
        utils = await _avg_utilization(db, store_ids, ws, now)
        return {"new_leads": len(leads),
                "lead_conversion": round(100 * leads_booked / len(leads)) if leads else 0,
                "bookings_from_leads": len(lead_appts),
                "repeat_customers": repeat,
                "revenue": revenue,
                "store_utilization": utils}

    cur = await collect(start)
    prev = await collect(prev_start) if start else {k: 0 for k in
                                                    ["new_leads", "lead_conversion", "bookings_from_leads",
                                                     "repeat_customers", "revenue", "store_utilization"]}
    kpi_list = []
    for key, label, section in [
            ("new_leads", "New Leads", "leads"), ("lead_conversion", "Lead Conversion", "leads"),
            ("bookings_from_leads", "Bookings From Leads", "appointments"),
            ("repeat_customers", "Repeat Customers", "customers"),
            ("revenue", "Revenue", "dashboard"), ("store_utilization", "Store Utilization", "team")]:
        v, pv = cur[key], prev[key]
        delta = round(v - pv, 1) if isinstance(v, (int, float)) and isinstance(pv, (int, float)) else 0
        kpi_list.append({"key": key, "label": label, "value": v, "prev": pv,
                         "delta": delta, "delta_pct": round(100 * delta / pv) if pv else None,
                         "section": section})
    return {"kpis": kpi_list, "period": period}


async def _avg_utilization(db, store_ids, start, now):
    stores = await db.stores.find({"org_id": config.ORG_ID, "status": "active"})
    if store_ids:
        stores = [s for s in stores if s["_id"] in store_ids]
    if not stores:
        return 0
    q = {"org_id": config.ORG_ID, "status": {"$in": ["booked", "completed"]}}
    if store_ids:
        q["store_id"] = {"$in": store_ids}
    appts = await db.appointments.find(q)
    emp_counts = {}
    for s in stores:
        ctx = await capacity.load_day_ctx(db, [s["_id"]], now.strftime("%Y-%m-%d"))
        emp_counts[s["_id"]] = len(ctx["employees"])
    days = 1 if not start else max(1, (now - start).days)
    per_store = {s["_id"]: 0 for s in stores}
    for a in appts:
        if a["store_id"] in per_store:
            per_store[a["store_id"]] += a["duration"] * len(a.get("employee_ids") or [])
    utils = []
    for s in stores:
        denom = emp_counts.get(s["_id"], 1) * 12 * days
        utils.append(round(100 * per_store[s["_id"]] / denom) if denom else 0)
    return round(sum(utils) / len(utils)) if utils else 0


@router.get("/analytics/trend")
async def trend(days: int = 14, flt: dict = Depends(resolve_filter), db: Database = Depends(get_db)):
    days = min(days, 30)
    store_ids = flt["store_ids"]
    q = {"org_id": config.ORG_ID}
    if store_ids:
        q["store_id"] = {"$in": store_ids}
    leads = await db.leads.find(q)
    appts = await db.appointments.find(q)
    today = _now().date()
    out = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        n_leads = sum(1 for l in leads if (l.get("created_at") or "")[:10] == d)
        n_book = sum(1 for a in appts if a.get("date") == d and a.get("status") in ("booked", "completed"))
        out.append({"date": d, "label": d[5:], "leads": n_leads, "bookings": n_book})
    return {"trend": out}


@router.get("/analytics/sources")
async def sources(flt: dict = Depends(resolve_filter), db: Database = Depends(get_db)):
    store_ids = flt["store_ids"]
    q = {"org_id": config.ORG_ID}
    if store_ids:
        q["store_id"] = {"$in": store_ids}
    start, _ = rules.period_range(flt["period"], _now())
    if start:
        q["created_at"] = {"$gte": start.isoformat()}
    leads = await db.leads.find(q)
    out = []
    for src in config.SOURCES:
        ls = [l for l in leads if l.get("source") == src]
        booked = sum(1 for l in ls if l.get("stage") == "booked")
        out.append({"source": src, "label": config.SOURCE_LABELS.get(src, src), "leads": len(ls),
                    "booked": booked})
    return {"sources": [s for s in out if s["leads"] or s["booked"]]}


@router.get("/analytics/source-quality")
async def source_quality(flt: dict = Depends(resolve_filter), db: Database = Depends(get_db)):
    """SRC-01: compare lead sources by completed visits and revenue (not just volume),
    against ad spend, to guide marketing budget."""
    now = _now()
    start, _ = rules.period_range(flt["period"], now)
    q = {"org_id": config.ORG_ID}
    if flt["store_ids"]:
        q["store_id"] = {"$in": flt["store_ids"]}
    leads = await db.leads.find(q)
    if start and flt["period"] != "all":
        leads = [l for l in leads if (l.get("created_at") or "") >= start.isoformat()]
    src_of_lead = {l["_id"]: l.get("source", "others") for l in leads}
    cust_src = {c["_id"]: c.get("source", "others")
                for c in await db.customers.find(q)}
    stats: dict[str, dict] = {}

    def bucket(src):
        return stats.setdefault(src, {"leads": 0, "contacted": 0, "booked": 0, "visits": 0,
                                      "revenue": 0, "no_show": 0})

    for l in leads:
        b = bucket(l.get("source", "others"))
        b["leads"] += 1
        if l.get("stage") != "new" or l.get("last_contact_at"):
            b["contacted"] += 1
    appts = await db.appointments.find(q)
    for a in appts:
        src = src_of_lead.get(a.get("lead_id")) or cust_src.get(a.get("customer_id"))
        if not src:
            continue
        st = a.get("status")
        b = bucket(src)
        if st in ("booked", "completed"):
            b["booked"] += 1
        if st == "completed":
            b["visits"] += 1
            b["revenue"] += (a.get("price") or {}).get("final", 0)
        if st == "no_show":
            b["no_show"] += 1
    org = await rules.org_doc(db)
    ad_spend = org.get("ad_spend") or {}
    out = []
    for src, b in stats.items():
        spend_paise = int(ad_spend.get(src, 0)) * 100
        out.append({"source": src, "label": config.SOURCE_LABELS.get(src, src),
                    "leads": b["leads"], "contacted": b["contacted"],
                    "contact_rate": round(100 * b["contacted"] / b["leads"]) if b["leads"] else 0,
                    "booked": b["booked"], "visits": b["visits"],
                    "revenue": b["revenue"], "revenue_display": rules.fmt_money(b["revenue"]),
                    "conversion_pct": round(100 * b["visits"] / b["leads"]) if b["leads"] else 0,
                    "spend": ad_spend.get(src, 0),
                    "cost_per_lead": int(spend_paise / b["leads"]) if b["leads"] and spend_paise else None,
                    "cost_per_visit": int(spend_paise / b["visits"]) if b["visits"] and spend_paise else None,
                    "roas": round(b["revenue"] / spend_paise, 1) if spend_paise else None})
    out.sort(key=lambda s: -s["revenue"])
    qualified = [s for s in out if s["leads"] >= 3 and s["roas"] is not None]
    best = max(qualified, key=lambda s: s["roas"], default=None)
    worst = min(qualified, key=lambda s: s["roas"], default=None)
    rec = None
    if best:
        rec = (f"Put more budget behind {best['label']}: {best['revenue_display']} of completed-visit revenue "
               f"is attributed to this acquisition source ({best['visits']} visits, ROAS {best['roas']}x).")
    if worst and worst is not best and (worst.get("roas") or 0) < 1:
        rec += f" Review {worst['label']}: ROAS {worst['roas']}x is below break-even."
    return {"sources": out, "recommendation": rec,
            "totals": {"revenue": sum(s["revenue"] for s in out),
                       "spend": sum(s["spend"] for s in out)}}


@router.get("/analytics/funnel")
async def funnel(flt: dict = Depends(resolve_filter), db: Database = Depends(get_db)):
    store_ids = flt["store_ids"]
    q = {"org_id": config.ORG_ID}
    if store_ids:
        q["store_id"] = {"$in": store_ids}
    leads = await db.leads.find(q)
    return {"funnel": [{"stage": s, "label": config.STAGE_LABELS[s],
                        "count": sum(1 for l in leads if l["stage"] == s)} for s in config.STAGES]}


@router.get("/analytics/store-utilization")
async def store_utilization(flt: dict = Depends(resolve_filter), db: Database = Depends(get_db)):
    org = await rules.org_doc(db)
    target = rules.rule_value(org, "UTIL_TARGET", 85)
    today = _now().strftime("%Y-%m-%d")
    stores = await db.stores.find({"org_id": config.ORG_ID, "status": "active"})
    if flt["store_ids"]:
        stores = [s for s in stores if s["_id"] in flt["store_ids"]]
    out = []
    for s in stores:
        u = await capacity.store_day_utilization(db, s["_id"], today)
        appts = await db.appointments.count_documents(
            {"store_id": s["_id"], "date": today, "status": {"$in": ["booked", "completed"]}})
        revenue = sum(p.get("amount", 0) for p in
                      await db.payments.find({"store_id": s["_id"], "status": "paid"}))
        out.append({"store_id": s["_id"], "name": s["name"], "area": s.get("area"),
                    "utilization": u["utilization_pct"], "target": target,
                    "booked_emp_hours": u["booked_emp_hours"],
                    "appointments_today": appts,
                    "revenue": revenue})
    return {"stores": out, "target": target}


@router.get("/analytics/employee-workload")
async def employee_workload(flt: dict = Depends(resolve_filter), db: Database = Depends(get_db)):
    store_ids = flt["store_ids"]
    q = {"org_id": config.ORG_ID, "status": "active"}
    if store_ids:
        q["store_id"] = {"$in": store_ids}
    emps = await db.employees.find(q)
    today = _now().strftime("%Y-%m-%d")
    services = {s["_id"]: s["name"] for s in await db.services.find({"org_id": config.ORG_ID})}
    stores = {s["_id"]: s["name"] for s in await db.stores.find({"org_id": config.ORG_ID})}
    appts = await db.appointments.find({"org_id": config.ORG_ID, "date": today,
                                        "status": {"$in": ["booked", "completed"]}})
    out = []
    for e in emps:
        mine = [a for a in appts if e["_id"] in (a.get("employee_ids") or [])]
        booked_hours = sum(a["duration"] for a in mine)
        out.append({"employee_id": e["_id"], "name": e["name"], "role": e.get("role"),
                    "store_id": e["store_id"], "store_name": stores.get(e["store_id"]),
                    "services": [services.get(s) for s in (e.get("service_ids") or [])],
                    "booked_today": len(mine),
                    "hours_today": booked_hours,
                    "utilization": round(100 * booked_hours / 12)})
    return {"employees": out}


@router.get("/alerts")
async def alerts(flt: dict = Depends(resolve_filter), db: Database = Depends(get_db)):
    org = await rules.org_doc(db)
    threshold = rules.rule_value(org, "BR-06", 90)
    store_ids = flt["store_ids"]
    stores = await db.stores.find({"org_id": config.ORG_ID, "status": "active"})
    if store_ids:
        stores = [s for s in stores if s["_id"] in store_ids]
    today = _now().strftime("%Y-%m-%d")
    now = _now()
    out = []
    for s in stores:
        u = await capacity.store_day_utilization(db, s["_id"], today)
        if u["utilization_pct"] >= threshold:
            out.append({"kind": "store_full", "severity": "high",
                        "text": f"{s['name']} is at {u['utilization_pct']}% (reroute threshold {threshold}%)",
                        "section": "team", "store_id": s["_id"]})
    leq = {"org_id": config.ORG_ID}
    if store_ids:
        leq["store_id"] = {"$in": store_ids}
    leads = await db.leads.find(leq)
    overdue = [l for l in leads if l.get("follow_up_at") and l["follow_up_at"] < now.isoformat()
               and l.get("stage") not in ("booked", "lost")]
    if overdue:
        out.append({"kind": "overdue_followups", "severity": "high",
                    "text": f"{len(overdue)} follow-ups are overdue", "section": "leads"})
    # BR-16 response SLA: new leads nobody contacted in time - alert names the owners
    breached = []
    for l in leads:
        if l.get("stage") != "new" or not l.get("created_at"):
            continue
        sla = scoring.sla_for(org, l.get("source", "others"))
        deadline = rules.parse_dt(l["created_at"])
        if deadline and now > deadline + timedelta(minutes=sla):
            breached.append(l)
    if breached:
        owner_counts: dict = {}
        for l in breached:
            owner_counts[l.get("owner_id")] = owner_counts.get(l.get("owner_id"), 0) + 1
        owner_names = {u["_id"]: u["name"] for u in await db.users.find({"org_id": config.ORG_ID})}
        top = ", ".join(f"{owner_names.get(k, 'unassigned')} ({v})" for k, v in
                        sorted(owner_counts.items(), key=lambda kv: -kv[1])[:3])
        out.append({"kind": "sla_breach", "severity": "high",
                    "text": f"{len(breached)} new lead(s) past the response SLA - contact them now. Owners: {top}",
                    "section": "queue"})
    hot = [l for l in leads if l.get("score", 0) >= 80 and l.get("stage") not in ("booked", "lost")]
    if hot:
        out.append({"kind": "hot_leads", "severity": "medium",
                    "text": f"{len(hot)} hot leads (80+) waiting", "section": "leads"})
    interested = [l for l in leads if l.get("stage") == "interested"]
    if interested:
        out.append({"kind": "interested_waiting", "severity": "medium",
                    "text": f"{len(interested)} interested leads have not been offered an appointment",
                    "section": "leads"})
    customers = await db.customers.find(leq)
    due = [c for c in customers if (c.get("plan") or {}).get("end")
           and rules.parse_dt(c["plan"]["end"])
           and 0 <= (rules.parse_dt(c["plan"]["end"]) - now).days <= 5]
    if due:
        out.append({"kind": "plans_ending", "severity": "medium",
                    "text": f"{len(due)} plans end within 5 days - start renewals", "section": "customers"})
    wl = await db.waitlist.count_documents({"org_id": config.ORG_ID, "status": "waiting"})
    if wl:
        out.append({"kind": "waitlist", "severity": "low",
                    "text": f"{wl} people on the waitlist", "section": "appointments"})
    return {"alerts": out}


@router.get("/events")
async def events(limit: int = 30, flt: dict = Depends(resolve_filter), db: Database = Depends(get_db)):
    store_ids = flt["store_ids"]
    evs = await db.events.find({"org_id": config.ORG_ID}, sort=[("at", -1)], limit=min(limit, 100))
    if store_ids:
        evs = [e for e in evs if not e.get("payload", {}).get("store_id")
               or e["payload"]["store_id"] in store_ids]
    return {"events": evs}


@router.get("/events/stream")
async def stream(request: Request, user: dict = Depends(current_user)):
    """RT-01: Server-Sent Events; store-scoped."""
    store_ids = store_scope(user)
    key = "all" if not store_ids else f"store:{store_ids[0]}"
    q = subscribe(key)

    async def gen():
        try:
            yield f"event: connected\ndata: {json.dumps({'user': user['name'], 'stores': store_ids})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=25)
                    yield format_sse(ev)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            unsubscribe(key, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
