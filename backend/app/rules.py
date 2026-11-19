"""Business rules catalog helpers (PRD section 6 + algorithms, section 12)."""
from datetime import datetime, timedelta, timezone

from . import config, db as dbm


def get_rules(org_doc) -> dict:
    """Effective rules = org overrides over defaults."""
    rules = {k: dict(v) for k, v in config.RULES.items()}
    for k, v in (org_doc or {}).get("rule_overrides", {}).items():
        if k in rules:
            rules[k]["value"] = v
    return rules


def rule_value(org_doc, key, default=None):
    r = get_rules(org_doc).get(key)
    return r["value"] if r else default


# ------------------------------------------------------------------- money (paise)
def fmt_money(paise: int) -> str:
    rupees = round(paise / 100)
    return f"Rs {rupees:,}"


def rupees(paise: int) -> float:
    return round(paise / 100)


def price_breakup(service: dict, distance_km: float | None, org_doc: dict,
                  extra_hours: int = 0, extra_staff: int = 0) -> dict:
    """BR-09: price = base + extra hours x hourly add-on + extra employees x staff add-on.
    BR-07: far-location benefit applies to the total."""
    base = service["base_price"]
    hour_pct = rule_value(org_doc, "BR-09H", 40) / 100
    staff_pct = rule_value(org_doc, "BR-09S", 30) / 100
    extra_hours_charge = int(extra_hours * hour_pct * base)
    extra_staff_charge = int(extra_staff * staff_pct * base)
    subtotal = base + extra_hours_charge + extra_staff_charge
    far_km = rule_value(org_doc, "BR-07KM", 5)
    benefit_pct = 0
    if distance_km is not None and distance_km > far_km:
        benefit_pct = rule_value(org_doc, "BR-07", 10)
    benefit = int(subtotal * benefit_pct / 100)
    return {"base": base, "extra_hours": extra_hours, "extra_hours_charge": extra_hours_charge,
            "extra_staff": extra_staff, "extra_staff_charge": extra_staff_charge,
            "subtotal": subtotal, "benefit_pct": benefit_pct, "benefit": benefit,
            "final": subtotal - benefit}


# ------------------------------------------------------------------- lead scoring
HIGH_INTENT = {"google_ads", "referral", "walk_in_call", "justdial"}
SOCIAL = {"instagram_ads", "others"}


def lead_score(lead: dict, ctx: dict) -> tuple[int, list]:
    """PRD 12.3. ctx: {'capacity_nearby': bool, 'existing_person': bool}"""
    factors = []
    src = lead.get("source", "others")
    if src in HIGH_INTENT:
        factors.append({"f": "source_intent", "v": 30, "d": f"High-intent source ({src})"})
    elif src in SOCIAL:
        factors.append({"f": "source_intent", "v": 15, "d": "Social ad source"})
    fields_ok = all(lead.get(f) for f in ("name", "phone_norm", "store_id", "service_id"))
    if lead.get("age_group") and lead.get("gender"):
        fields_ok = True
    if fields_ok:
        factors.append({"f": "data_completeness", "v": 15, "d": "Complete data"})
    if ctx.get("capacity_nearby"):
        factors.append({"f": "capacity_nearby", "v": 20, "d": "Free capacity near the lead"})
    if ctx.get("replied"):
        factors.append({"f": "replied", "v": 25, "d": "Replied to first message"})
    if ctx.get("existing_person"):
        factors.append({"f": "existing_customer", "v": 20, "d": "Existing or lapsed customer"})
    score = min(100, sum(f["v"] for f in factors))
    return score, factors


def score_band(score: int) -> str:
    return "hot" if score >= 80 else ("warm" if score >= 65 else "cold")


STAGE_BASE = {"new": 0.12, "contacted": 0.22, "interested": 0.45, "offered": 0.55, "booked": 0.9, "lost": 0.08}


def chance_to_book(stage: str, score: int, offer_pct: int = 0) -> float:
    """PRD 12.3: min(0.85, stage_base x (0.5 + score/100) x (1 + offer_pct/200))"""
    base = STAGE_BASE.get(stage, 0.12)
    if stage == "booked":
        return 0.0
    return round(min(0.85, base * (0.5 + score / 100) * (1 + offer_pct / 200)), 3)


# ------------------------------------------------------------------- segments
def segment_for(c: dict, now: datetime) -> str:
    """PRD 12.5 / BR-20. Priority: renewal-due > expired stages > value segments."""
    plan = c.get("plan") or {}
    end = plan.get("end")
    last = c.get("last_visit_at")
    visits = c.get("visits", 0)
    spend = c.get("spend", 0)
    if end:
        end_dt = _parse(end)
        if end_dt and 0 <= (end_dt - now).days <= 5:
            return "renewal_due"
        if end_dt and end_dt < now:
            days = (now - end_dt).days
            return "expired_0_7" if days <= 7 else ("expired_8_21" if days <= 21 else "expired_22p")
    if last:
        days_since = (now - _parse(last)).days
        if visits >= 8 and spend >= 4_000_000:  # Rs 40,000 in paise
            return "vip"
        if visits <= 1 and days_since <= 30:
            return "new"
        if visits >= 4 and days_since <= 45:
            return "loyal"
        if 46 <= days_since <= 90:
            return "at_risk"
        if days_since > 90:
            return "inactive"
    return "new" if visits <= 1 else "loyal"


def parse_dt(s):
    """Parse ISO strings; naive datetimes are treated as UTC so arithmetic never mixes
    offset-naive and offset-aware values."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


_parse = parse_dt


SEGMENT_LABELS = {
    "vip": "VIP", "loyal": "Loyal", "new": "New", "at_risk": "At Risk (46-90 days)",
    "inactive": "Inactive (90+)", "renewal_due": "Plans Ending in 5 Days",
    "expired_0_7": "Expired 0-7 Days", "expired_8_21": "Expired 8-21 Days", "expired_22p": "Expired 22+ Days",
}

# Customer campaign conversion bands (PRD 12.4)
SEGMENT_CONV = {"vip": (0.30, 0.40), "loyal": (0.25, 0.33), "new": (0.18, 0.26),
                "at_risk": (0.14, 0.22), "inactive": (0.06, 0.12), "renewal_due": (0.45, 0.60)}


def period_range(period: str, now: datetime) -> tuple[datetime | None, datetime]:
    start = None
    days = {"today": 1, "7": 7, "30": 30, "90": 90}.get(str(period))
    if days:
        start = now - timedelta(days=days)
    return start, now


async def org_doc(db):
    return await db.organizations.find_one({"_id": config.ORG_ID})


def render_template(body: str, person: dict, service: dict, store: dict,
                    price_final: int | None = None, discount_pct: int = 0, alt_store: dict | None = None) -> str:
    out = body
    repl = {
        "{name}": person.get("name", "there"),
        "{service}": (service or {}).get("name", "your service"),
        "{store}": (store or {}).get("name", "your store"),
        "{price}": fmt_money((service or {}).get("base_price", 0)) if price_final is None else fmt_money(price_final),
        "{discount}": f"{discount_pct}%",
        "{alt_store}": (alt_store or {}).get("name", "a nearby store"),
        "{source}": str(person.get("source", "")).replace("_", " ").title(),
    }
    for k, v in repl.items():
        out = out.replace(k, v)
    return out
