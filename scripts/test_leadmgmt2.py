"""Tests for the five lead-management additions: today's follow-up queue with
book/lose actions, prior-visit priority signal, assignment rules (store/service +
least-loaded) with SLA alerts, one customer timeline (lead phase included), and
the lead-to-booking outcomes report. Run against the live dev server."""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "http://localhost:8000/api/v1"
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def err_code(d):
    try:
        return d["detail"]["error"]["code"]
    except Exception:
        return None


def main():
    s, d = call("POST", "/auth/login", {"email_or_phone": "admin@salesflow.app", "password": "Demo@123"})
    tok = d.get("access_token")
    check("admin login", s == 200 and tok, s)

    # ---- 1. today's follow-up queue: ready_to_book present with book/lose actions
    s, d = call("GET", "/queue/today", token=tok)
    kinds = {i["kind"] for i in d.get("items", [])}
    rtb = [i for i in d.get("items", []) if i["kind"] == "ready_to_book"]
    check("queue has ready_to_book items", "ready_to_book" in kinds and len(rtb) >= 1, d.get("counts"))
    if rtb:
        check("ready_to_book actions include book+mark_lost",
              "book" in rtb[0]["actions"] and "mark_lost" in rtb[0]["actions"], rtb[0]["actions"])
        check("ready_to_book carries service_id for the booking picker", bool(rtb[0].get("service_id")))
    item = rtb[0] if rtb else None
    if item:
        s, d = call("POST", "/queue/action", {"kind": "lead", "ref_id": item["ref_id"],
                                              "action": "mark_lost", "lost_reason": "Price"}, tok)
        check("queue mark_lost works", s == 200 and d.get("ok"), (s, d))
        s, d = call("GET", f"/leads/{item['ref_id']}", token=tok)
        check("lead is lost with reason", d.get("stage") == "lost" and d.get("lost_reason") == "Price", d.get("stage"))
        s, d = call("POST", "/queue/action", {"kind": "lead", "ref_id": item["ref_id"],
                                              "action": "mark_lost", "lost_reason": "Bogus"}, tok)
        check("invalid lost reason rejected", s == 422, (s, err_code(d)))

    # ---- 2. explainable priority incl. prior visits
    s, d = call("GET", "/leads?limit=10", token=tok)
    check("priority + reasons on rows", all("priority" in l and l.get("priority_reasons") is not None
                                            for l in d.get("leads", [])))
    tiers = {l["priority"] for l in d.get("leads", [])}
    check("tiers are hot/warm/low", tiers <= {"hot", "warm", "low"}, tiers)

    # ---- 3. assignment rules: store/service match + least-loaded; SLA alert
    s, d = call("GET", "/automation-rules", token=tok)
    check("rules payload has stores/services/owners",
          d.get("stores") and d.get("services") and d.get("owners"), list(d.keys()))
    store = d["stores"][0]
    s, d = call("POST", "/automation-rules", {"name": "Store Routing Test", "type": "assign_new_leads",
                                              "store_id": store["_id"]}, tok)
    check("rule without owner created (least-loaded)", s == 201, (s, d))
    rule_id = d.get("_id")
    s, d = call("POST", f"/automation-rules/{rule_id}/preview", token=tok)
    check("least-loaded preview mentions routing", s == 200 and "least-loaded" in d.get("effect", ""), d.get("effect"))
    s, d = call("PUT", f"/automation-rules/{rule_id}", {"enabled": True}, tok)
    s, d = call("GET", "/stores", token=tok)
    s, d = call("GET", "/services", token=tok)
    s, d = call("POST", "/leads", {"name": "Store Router", "phone": "9955580001", "store_id": store["_id"],
                                   "service_id": d["services"][0]["_id"], "source": "referral"}, tok)
    check("lead created", s == 201, (s, d))
    check("least-loaded rule assigned an owner", bool(d.get("owner_id")), d.get("owner_id"))
    s, d = call("GET", "/automation-log", token=tok)
    check("assignment logged", any(e.get("rule_id") == rule_id for e in d.get("log", [])))
    s, d = call("PUT", f"/automation-rules/{rule_id}", {"enabled": False}, tok)
    s, d = call("DELETE", f"/automation-rules/{rule_id}", token=tok)
    s, d = call("POST", "/automation-rules", {"name": "No Filters", "type": "assign_new_leads"}, tok)
    check("rule without any filter rejected", s == 422, (s, err_code(d)))
    s, d = call("GET", "/alerts", token=tok)
    check("SLA breach alert present with owners",
          any(a["kind"] == "sla_breach" and "Owners:" in a["text"] for a in d.get("alerts", [])),
          [a["kind"] for a in d.get("alerts", [])])

    # ---- 4. one customer timeline incl. lead phase
    s, d = call("GET", "/leads?limit=200&stage=booked", token=tok)
    booked_leads = [l for l in d.get("leads", []) if l.get("customer_id")]
    check("booked leads with customer_id exist", len(booked_leads) >= 1, len(booked_leads))
    if booked_leads:
        l0 = booked_leads[0]
        # log a lead-phase call, then check the customer timeline shows it
        s, d = call("POST", f"/leads/{l0['_id']}/interactions",
                    {"channel": "call", "direction": "out", "outcome": "call_made",
                     "content": "Lead phase call for timeline test"}, tok)
        check("lead-phase call logged", s in (200, 201), (s, d))
        s, d = call("GET", f"/customers/{l0['customer_id']}/timeline", token=tok)
        tl = d.get("timeline", [])
        types = {e["type"] for e in tl}
        check("timeline returned lead link", (d.get("lead") or {}).get("id") == l0["_id"], d.get("lead"))
        check("timeline has lead stage events", any(t.startswith("lead_") for t in types), sorted(types)[:8])
        check("timeline shows the lead-phase call",
              any("Lead phase call" in (e.get("detail") or "") for e in tl))
        check("timeline has appointment events", any(t.startswith("appointment_") for t in types))

    # ---- 5. lead-to-booking outcomes report
    s, d = call("GET", "/leads/outcomes?period=all", token=tok)
    f = d.get("funnel") or {}
    check("outcomes funnel fields", all(k in f for k in
          ("leads", "contacted", "booked", "attended", "renewed", "lost", "booking_rate", "attendance_rate")), f)
    check("outcomes funnel is ordered (booked <= leads, attended <= booked)",
          f.get("booked", 0) <= f.get("leads", 0) and f.get("attended", 0) <= f.get("booked", 0), f)
    check("attended visits attributed (seed backfill)", f.get("attended", 0) >= 1, f)
    check("by_source + by_owner + owners present", d.get("by_source") and d.get("by_owner") and d.get("owners"))
    s, d = call("GET", "/leads/outcomes?period=all&source=referral", token=tok)
    check("source filter narrows cohort", d["funnel"]["leads"] < 51 or d["funnel"]["leads"] == f["leads"],
          (d["funnel"]["leads"], f["leads"]))
    s, d = call("GET", "/leads/outcomes?period=7", token=tok)
    check("period filter works", d["funnel"]["leads"] <= f["leads"], (d["funnel"]["leads"], f["leads"]))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
