"""AI insight engine (PRD AI-01..AI-05 / 12.6): explainable recommendations with
expected bookings/revenue and one-click actions; recomputed per filter scope;
applied/dismissed insights do not repeat for the same scope until data changes."""
import hashlib
from datetime import datetime, timedelta, timezone

from . import capacity, config, db as dbm, rules
from .campaigns import lead_segments, customer_segments


def scope_hash(period: str, store_ids: list) -> str:
    return hashlib.md5(f"{period}:{','.join(sorted(store_ids or []))}".encode()).hexdigest()[:10]


async def _period_appts(db, start, store_ids):
    q = {"org_id": config.ORG_ID}
    if store_ids:
        q["store_id"] = {"$in": store_ids}
    appts = await db.appointments.find(q)
    out = [a for a in appts if not start or (a.get("date") or "") >= start.strftime("%Y-%m-%d")]
    return out


async def compute(db, period: str, store_ids: list) -> dict:
    org = await rules.org_doc(db)
    start, now = rules.period_range(period, datetime.now(timezone.utc))
    scope = scope_hash(period, store_ids)
    seen = {i["key"] for i in await db.insights.find(
        {"org_id": config.ORG_ID, "scope": scope, "status": {"$in": ["applied", "dismissed"]}})}

    insights: list[dict] = []

    def add(key, tab, title, text, evidence, exp_bookings, exp_revenue, action):
        if key in seen:
            return
        insights.append({"key": key, "tab": tab, "title": title, "text": text,
                         "evidence": evidence,
                         "expected_bookings": round(exp_bookings, 1),
                         "expected_revenue": int(exp_revenue),
                         "action": action, "scope": scope, "computed_at": dbm.now_iso()})

    stores = await db.stores.find({"org_id": config.ORG_ID, "status": "active"})
    services = {s["_id"]: s for s in await db.services.find({"org_id": config.ORG_ID})}
    store_by_id = {s["_id"]: s for s in stores}
    leads_q = {"org_id": config.ORG_ID}
    if store_ids:
        leads_q["store_id"] = {"$in": store_ids}
    leads = await db.leads.find(leads_q)
    open_leads = [l for l in leads if l.get("stage") not in ("booked", "lost")]

    # ---------------- Lead & Conversion
    hot = [l for l in open_leads if l.get("score", 0) >= 80]
    if hot:
        eb = sum(rules.chance_to_book(l["stage"], l["score"], 10) for l in hot)
        er = sum(rules.chance_to_book(l["stage"], l["score"], 10) *
                 (services.get(l.get("service_id"), {}).get("base_price", 0) * 0.9) for l in hot)
        add("hot_not_booked", "lead", "Hot leads waiting to be booked",
            f"{len(hot)} leads with score 80+ are still open. The best converters are in stage "
            f"'{max(hot, key=lambda l: l.get('score', 0))['stage']}'. A 10% offer campaign converts "
            f"typically {int(eb)}-{int(eb * 1.4) + 1} of them within a week.",
            {"count": len(hot), "avg_score": round(sum(l.get('score', 0) for l in hot) / len(hot))},
            eb, er, {"type": "campaign", "label": "Open Campaign Wizard (Hot Leads)",
                     "payload": {"kind": "leads", "segment_key": "hot", "offer_pct": 10,
                                 "channel": "whatsapp", "period": period}})

    overdue = [l for l in open_leads if l.get("follow_up_at") and l["follow_up_at"] < now.isoformat()]
    if overdue:
        eb = sum(rules.chance_to_book(l["stage"], l["score"]) * 0.45 for l in overdue)
        er = sum(rules.chance_to_book(l["stage"], l["score"]) * 0.45 *
                 services.get(l.get("service_id"), {}).get("base_price", 0) for l in overdue)
        add("overdue_followups", "lead", "Overdue follow-ups are leaking bookings",
            f"{len(overdue)} leads have follow-ups overdue by more than a day. Calling them today "
            f"typically recovers about half; expected {int(eb)} bookings.",
            {"count": len(overdue),
             "oldest": min((l["follow_up_at"] for l in overdue), default="")},
            eb, er, {"type": "open_list", "label": "Open Overdue Follow-Ups",
                     "payload": {"section": "leads", "tab": "followups", "filter": "overdue"}})

    stale = [l for l in open_leads if l.get("stage") == "offered" and l.get("offered_at")
             and l["offered_at"] < (now - timedelta(days=14)).isoformat()]
    if stale:
        eb = len(stale) * 0.35
        er = eb * sum(s.get("base_price", 0) for s in services.values()) / max(len(services), 1)
        add("stale_offers", "lead", "Offers older than 14 days",
            f"{len(stale)} leads were offered an appointment more than 14 days ago and have not "
            f"booked. A short re-offer message with an alternative store converts about 1 in 3.",
            {"count": len(stale)}, eb, er,
            {"type": "campaign", "label": "Re-Offer Campaign (Stale Offers)",
             "payload": {"kind": "leads", "segment_key": "stale_offers", "offer_pct": 10,
                         "channel": "whatsapp", "period": period}})

    conv_by_src: dict[str, list] = {}
    for l in leads:
        conv_by_src.setdefault(l.get("source", "others"), []).append(l)
    best_src, best_rate = None, 0.0
    for src, ls in conv_by_src.items():
        booked = sum(1 for l in ls if l.get("stage") == "booked")
        if len(ls) >= 3 and booked / len(ls) > best_rate:
            best_src, best_rate = src, booked / len(ls)
    if best_src:
        remaining = [l for l in conv_by_src[best_src] if l.get("stage") not in ("booked", "lost")]
        eb = sum(rules.chance_to_book(l["stage"], l["score"]) for l in remaining)
        add("best_source", "lead", f"{config.SOURCE_LABELS.get(best_src, best_src)} converts best",
            f"{config.SOURCE_LABELS.get(best_src, best_src)} converts at {int(best_rate * 100)}% "
            f"({len(conv_by_src[best_src])} leads). Shifting more budget here and pushing the "
            f"{len(remaining)} still-open leads of this source adds about {int(eb)} "
            f"booking{'s' if int(eb) != 1 else ''}.",
            {"source": best_src, "rate": round(best_rate, 2), "open": len(remaining)},
            eb, eb * 350000,
            {"type": "open_list", "label": "Open Leads Of This Source",
             "payload": {"section": "leads", "tab": "all", "filter": {"source": best_src}}})

    # ---------------- Marketing
    segs = await lead_segments(db, period, store_ids)
    ranked = []
    for key in ("hot", "interested", "stale_offers", "overdue_followups"):
        ls = segs.get(key, [])
        if not ls:
            continue
        eb = sum(rules.chance_to_book(l["stage"], l.get("score", 50), 10) for l in ls)
        er = sum(rules.chance_to_book(l["stage"], l.get("score", 50), 10) *
                 (services.get(l.get("service_id"), {}).get("base_price", 0) * 0.9) for l in ls)
        ranked.append((er, key, len(ls), eb))
    if ranked:
        ranked.sort(reverse=True)
        er, key, n, eb = ranked[0]
        add("top_segment", "marketing", f"Best segment to campaign now: {key.replace('_', ' ').title()}",
            f"The {key.replace('_', ' ')} segment has {n} leads worth about Rs {int(er / 100):,} in "
            f"expected revenue - the highest of all lead segments right now.",
            {"segment": key, "count": n}, eb, er,
            {"type": "campaign", "label": "Open Campaign Wizard (Preset)",
             "payload": {"kind": "leads", "segment_key": key, "offer_pct": 10,
                         "channel": "whatsapp", "period": period}})

    # ---------------- Customer Retention
    csegs = await customer_segments(db, store_ids)
    due = csegs.get("renewal_due", [])
    if due:
        eb = len(due) * 0.52  # mid of the 45-60% renewal band (12.4)
        er = sum((services.get(c.get("last_service_id"), {}).get("base_price", 0)) for c in due) * 0.52
        add("plans_ending", "retention", "Plans ending within 5 days",
            f"{len(due)} customers have plans ending within 5 days. Renewal slots can be held for "
            f"7 days; reminders on day 25 convert about half of them.",
            {"count": len(due)}, eb, er,
            {"type": "campaign", "label": "Open Renewal Campaign",
             "payload": {"kind": "customers", "segment_key": "renewal_due", "offer_pct": 0,
                         "channel": "whatsapp", "period": period}})

    winback = csegs.get("expired_8_21", []) + csegs.get("expired_22p", [])
    if winback:
        eb = len(winback) * 0.09
        er = sum((services.get(c.get("last_service_id"), {}).get("base_price", 0)) for c in winback) * 0.09
        add("win_back", "retention", "Win-back audience is ready",
            f"{len(winback)} customers expired 8+ days ago. Staged win-back (reminder, then offer, "
            f"then final call) recovers about 9% per wave.",
            {"count": len(winback)}, eb, er,
            {"type": "campaign", "label": "Open Win-Back Campaign",
             "payload": {"kind": "customers", "segment_key": "expired_22p", "offer_pct": 15,
                         "channel": "whatsapp", "period": period}})

    vip = csegs.get("vip", [])
    if vip:
        eb = len(vip) * 0.06
        er = sum((services.get(c.get("last_service_id"), {}).get("base_price", 0)) for c in vip) * 0.06
        add("vip_thanks", "retention", "Thank your VIP customers",
            f"{len(vip)} VIP customers (8+ visits, Rs 40,000+ spend) deserve a thank-you with an "
            f"exclusive preview offer; it lifts repeat visits without discount leakage.",
            {"count": len(vip)}, eb, er,
            {"type": "campaign", "label": "Open VIP Thank-You Campaign",
             "payload": {"kind": "customers", "segment_key": "vip", "offer_pct": 5,
                         "channel": "whatsapp", "period": period}})

    # ---------------- Store & Team
    today = datetime.now().strftime("%Y-%m-%d")
    utils = {}
    for s in stores:
        utils[s["_id"]] = (await capacity.store_day_utilization(db, s["_id"], today))["utilization_pct"]
    if utils and len(utils) >= 2:
        busiest = max(utils, key=utils.get)
        quietest = min(utils, key=utils.get)
        target = rules.rule_value(org, "UTIL_TARGET", 85)
        if utils[busiest] - utils[quietest] >= 15 or utils[quietest] < target - 25:
            ctx = await capacity.load_day_ctx(db, list(store_by_id.keys()), today)
            svc_ids = {l.get("service_id") for l in open_leads if l.get("service_id")}
            movable = []
            for e in ctx["employees"]:
                if e["store_id"] == quietest and not capacity.emp_booked_hours(ctx, e["_id"]):
                    if any(svc in (e.get("service_ids") or []) for svc in svc_ids or set()):
                        movable.append(e)
            eb = (target - utils[quietest]) / 100 * 12  # freed slots become bookings
            svc_txt = services.get(next(iter(svc_ids)), {}).get("name", "the top service") if svc_ids else "the top service"
            add("utilization_gap", "store", "Move staff from the quietest to the busiest store",
                f"{store_by_id[busiest]['name']} is at {utils[busiest]}% while "
                f"{store_by_id[quietest]['name']} sits at {utils[quietest]}% today. Moving "
                f"{min(2, len(movable))} free employee(s) trained for {svc_txt} closes the gap and "
                f"converts about {int(eb)} extra bookings.",
                {"busiest": store_by_id[busiest]["name"], "busiest_pct": utils[busiest],
                 "quietest": store_by_id[quietest]["name"], "quietest_pct": utils[quietest]},
                eb, eb * 380000,
                {"type": "move_employee" if movable else "open_waitlist", "label":
                 "Move A Free Employee" if movable else "Open Waitlist",
                 "payload": {"employee_ids": [e["_id"] for e in movable[:2]],
                             "to_store": busiest, "from_store": quietest}})
        low = min(utils, key=utils.get)
        wl = await db.waitlist.count_documents({"store_id": low, "status": "waiting"})
        add("low_utilization", "store", f"{store_by_id[low]['name']} has idle capacity today",
            f"{store_by_id[low]['name']} runs at {utils[low]}% today against the {target}% target."
            + (f" {wl} people are waitlisted at other stores - offering them this store fills the gap."
               if wl else " Push an off-peak offer to nearby leads to fill it."),
            {"store": store_by_id[low]["name"], "util": utils[low], "waitlist": wl},
            max(0, (target - utils[low]) / 100 * 6), max(0, (target - utils[low]) / 100 * 6) * 320000,
            {"type": "open_waitlist", "label": "Open Waitlist",
             "payload": {"store_id": low}})

    appts_p = await _period_appts(db, start, store_ids)
    demand: dict[str, int] = {}
    for a in appts_p:
        if a.get("status") in ("booked", "completed"):
            demand[a["service_id"]] = demand.get(a["service_id"], 0) + 1
    if demand:
        top_svc = max(demand, key=demand.get)
        svc = services.get(top_svc, {})
        short_stores = [s["_id"] for s in stores
                        if not capacity.store_offers(await capacity.load_day_ctx(db, [s["_id"]], today),
                                                     s["_id"], svc)]
        if short_stores and demand[top_svc] >= 3:
            add("demand_service", "store", f"{svc['name']} demand outstrips capacity",
                f"{svc['name']} has {demand[top_svc]} appointments in the period but "
                f"{len(short_stores)} store(s) cannot offer it (not enough trained staff). Training "
                f"one more employee per store unlocks reroutes and waitlist conversions.",
                {"service": svc.get("name"), "appts": demand[top_svc],
                 "stores_short": [store_by_id[s]["name"] for s in short_stores]},
                len(short_stores) * 1.5, len(short_stores) * 1.5 * svc.get("base_price", 0),
                {"type": "open_list", "label": "Open Store & Team",
                 "payload": {"section": "team"}})

    tabs = {"lead": [], "marketing": [], "retention": [], "store": []}
    for i in insights:
        tabs[i["tab"]].append(i)
    impact = {"expected_bookings": round(sum(i["expected_bookings"] for i in insights), 1),
              "expected_revenue": sum(i["expected_revenue"] for i in insights),
              "open": len(insights)}
    return {"tabs": tabs, "impact": impact, "scope": scope,
            "applied_keys": sorted(seen)}


async def apply(db, user, scope: str, key: str):
    ins = await db.insights.find_one({"org_id": config.ORG_ID, "scope": scope, "key": key})
    payload = (ins or {}).get("payload")
    if not ins:
        # the insight was never persisted; persist it now as applied
        payload = None
    await db.insights.update_one(
        {"org_id": config.ORG_ID, "scope": scope, "key": key},
        {"$set": {"status": "applied", "applied_at": dbm.now_iso(),
                  "applied_by": user["name"], "payload": payload}},
        upsert=True)
    return True


async def dismiss(db, user, scope: str, key: str):
    await db.insights.update_one(
        {"org_id": config.ORG_ID, "scope": scope, "key": key},
        {"$set": {"status": "dismissed", "dismissed_at": dbm.now_iso()}}, upsert=True)
    return True
