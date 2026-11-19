"""Rule-based lead priority (Hot / Warm / Low) with a transparent explanation.

This is NOT AI: every tier comes from fixed, inspectable rules over data the app
already has (the 0-100 explainable score, pipeline stage, recency of contact,
overdue follow-ups and reply history). The reason strings are shown to the user
so the tier is always understandable.
"""
from datetime import datetime, timedelta, timezone

from . import rules


def _now():
    return datetime.now(timezone.utc)


async def compute_priority(db, lead: dict) -> dict:
    """Return {tier, score, reasons} for a single lead."""
    res = await compute_priority_many(db, [lead])
    return res[lead["_id"]]


async def compute_priority_many(db, leads: list) -> dict:
    """Batch version: one query for replies, one for identities, one result per lead id."""
    now = _now()
    ids = [l["_id"] for l in leads]
    replied_ids: set = set()
    if ids:
        for i in await db.interactions.find(
                {"org_id": leads[0].get("org_id") if leads else None,
                 "person_id": {"$in": ids}, "direction": "in"}):
            replied_ids.add(i["person_id"])
    # prior visits: a phone that already owns a customer record (returning visitor)
    returning_phones: set = set()
    phones = list({l.get("phone_norm") for l in leads if l.get("phone_norm")})
    if phones:
        for i in await db.identities.find({"org_id": leads[0].get("org_id") if leads else None,
                                           "phone_norm": {"$in": phones}, "customer_id": {"$ne": None}}):
            returning_phones.add(i["phone_norm"])

    out: dict[str, dict] = {}
    for lead in leads:
        score = int(lead.get("score") or 0)
        stage = lead.get("stage", "new")
        reasons: list[str] = []

        # base tier from the existing score band
        if score >= 80:
            tier = "hot"
            reasons.append(f"Rule-based score {score}/100 (high)")
        elif score >= 65:
            tier = "warm"
            reasons.append(f"Rule-based score {score}/100 (medium)")
        else:
            tier = "low"
            reasons.append(f"Rule-based score {score}/100 (low)")

        # stage raises urgency
        if stage == "offered":
            tier = "hot"
            reasons.append("Appointment offered - waiting on the customer")
        elif stage == "interested" and tier == "low":
            tier = "warm"
            reasons.append("Interested in a service - worth a follow-up")

        # overdue follow-up is always hot
        fu = rules.parse_dt(lead.get("follow_up_at"))
        if fu and fu < now and stage not in ("booked", "lost"):
            tier = "hot"
            hours = round((now - fu).total_seconds() / 3600)
            reasons.append(f"Follow-up overdue by {hours}h")

        # recency: a hot lead with no contact for 3+ days drops a level
        last = lead.get("last_contact_at")
        if last:
            lc = rules.parse_dt(last)
            if lc and lc < now - timedelta(days=3) and stage in ("contacted", "interested", "offered"):
                if tier == "hot":
                    tier = "warm"
                    reasons.append("No contact logged in the last 3 days")
            if lc and lc < now - timedelta(days=7) and tier == "low":
                reasons.append("No contact for over a week")

        # a reply from the customer always raises urgency
        if lead["_id"] in replied_ids and stage not in ("booked", "lost"):
            if stage in ("interested", "offered"):
                tier = "hot"
            elif tier == "low":
                tier = "warm"
            reasons.append("Customer replied to a message")

        # prior visits: this phone already belongs to a customer record
        if lead.get("phone_norm") in returning_phones and stage not in ("booked", "lost"):
            if tier != "hot":
                tier = "warm"
            reasons.append("Visited before - returning visitor")

        if stage == "lost":
            tier = "low"
            reasons.insert(0, f"Marked lost ({lead.get('lost_reason') or 'no reason'})")

        reasons.sort(key=lambda r: 0 if "overdue" in r else 1 if "replied" in r else 2 if ("offered" in r or "Interested" in r or "No contact" in r) else 3)
        out[lead["_id"]] = {"tier": tier, "score": score, "reasons": reasons[:4]}
    return out


TIER_META = {
    "hot": {"label": "Hot", "chip": "bg-rose-100 text-rose-700 border-rose-200"},
    "warm": {"label": "Warm", "chip": "bg-amber-100 text-amber-700 border-amber-200"},
    "low": {"label": "Low", "chip": "bg-slate-100 text-slate-500 border-slate-200"},
}
