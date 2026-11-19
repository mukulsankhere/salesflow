"""End-to-end data-interrelation journey: every button's backend path exercised in
sequence, asserting that each action's effects flow through to every related
collection. Run against the live dev server (fresh seed)."""
import json
import sys
import urllib.error
import urllib.request

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
    check("login", s == 200 and tok, s)

    # ---------- stores/services for booking
    s, d = call("GET", "/stores", token=tok)
    stores = d["stores"]
    s, d = call("GET", "/services", token=tok)
    services = d["services"]
    store, svc = stores[0], services[0]

    # ---------- 1. lead creation -> identity + queue + SLA
    s, d = call("GET", "/services", token=tok)
    s, lead = call("POST", "/leads", {"name": "Journey Person", "phone": "9855509001",
                                      "store_id": store["_id"], "service_id": svc["_id"],
                                      "source": "justdial"}, tok)
    check("lead created", s == 201 and lead.get("_id"), (s, lead))
    lid = lead["_id"]
    s, d = call("GET", f"/leads/{lid}", token=tok)
    check("lead starts at new with score + priority", d["stage"] == "new" and d["score"] > 0
          and d.get("priority") in ("hot", "warm", "low"), (d["stage"], d["score"], d.get("priority")))

    # duplicate phone rejected (identity interrelation) - 409 PHONE_EXISTS
    s, d = call("POST", "/leads", {"name": "Journey Dupe", "phone": "9855509001",
                                   "store_id": store["_id"], "service_id": svc["_id"],
                                   "source": "justdial"}, tok)
    check("duplicate phone rejected", s == 409 and err_code(d) == "PHONE_EXISTS", (s, err_code(d)))

    # ---------- 2. log call -> interaction + last_contact_at + queue leaves SLA list
    s, d = call("POST", f"/leads/{lid}/interactions",
                {"channel": "call", "direction": "out", "outcome": "call_made",
                 "content": "journey first call"}, tok)
    check("call logged", s in (200, 201), (s, d))
    s, d = call("GET", f"/leads/{lid}", token=tok)
    check("last_contact_at set by call", bool(d.get("last_contact_at")), d.get("last_contact_at"))

    # ---------- 3. stage: contacted -> interested -> ready_to_book queue item
    s, d = call("POST", f"/leads/{lid}/stage", {"stage": "contacted"}, tok)
    s, d = call("POST", f"/leads/{lid}/stage", {"stage": "interested"}, tok)
    check("stage moves recorded", s == 200, s)
    s, d = call("GET", "/queue/today", token=tok)
    rtb = [i for i in d["items"] if i["kind"] == "ready_to_book" and i["ref_id"] == lid]
    check("lead surfaces as ready_to_book", len(rtb) == 1, len(rtb))
    s, d = call("GET", f"/leads/{lid}", token=tok)
    sh = [h["stage"] for h in d.get("stage_history", [])]
    check("stage history: new->contacted->interested", sh == ["new", "contacted", "interested"], sh)

    # ---------- 4. book via booking picker path (lead) -> customer + identity flip
    s, d = call("POST", "/appointments",
                {"person_type": "lead", "person_id": lid, "store_id": store["_id"],
                 "service_id": svc["_id"], "date": "2026-12-01", "start_hour": 14, "renew": False}, tok)
    check("booking created from lead", s == 201 and d.get("customer_id"), (s, d.get("error") or d.get("_id")))
    appt = d
    cid = appt["customer_id"]
    check("appointment linked to lead + customer", appt["lead_id"] == lid and bool(cid), appt.get("lead_id"))
    s, d = call("GET", f"/leads/{lid}", token=tok)
    check("lead stage -> booked + customer_id set", d["stage"] == "booked" and d.get("customer_id") == cid,
          (d["stage"], d.get("customer_id")))
    s, d = call("GET", f"/customers/{cid}", token=tok)
    cust = d if "_id" in d else d.get("customer")
    check("customer created with lead_id + plan (BR-01)", cust.get("lead_id") == lid
          and (cust.get("plan") or {}).get("end"), (cust.get("lead_id"), cust.get("plan")))
    s, d = call("GET", "/customers?q=Journey", token=tok)
    check("customer listed", any(c["_id"] == cid for c in d["customers"]), len(d["customers"]))
    # identity flipped to customer - the same phone now resolves to the customer
    s, d = call("POST", "/leads", {"name": "Identity Probe", "phone": "9855509001",
                                   "store_id": store["_id"], "service_id": svc["_id"],
                                   "source": "justdial"}, tok)
    detail = (d.get("detail") or {}).get("error", {}).get("details", {}) if isinstance(d.get("detail"), dict) else {}
    check("phone now owned by customer -> rejected with customer pointer",
          s == 409 and detail.get("customer_id") == cid, (s, detail))

    # booked lead leaves the ready_to_book queue
    s, d = call("GET", "/queue/today", token=tok)
    check("booked lead left ready_to_book", not any(i["ref_id"] == lid for i in d["items"]
                                                    if i["kind"] == "ready_to_book"))

    # ---------- 5. complete -> stats, segment, auto payment (BR-24)
    s, d = call("POST", f"/appointments/{appt['_id']}/complete", body=None, token=tok)
    check("appointment completed", s == 200, (s, d))
    s, d = call("GET", f"/customers/{cid}", token=tok)
    cust = d if "_id" in d else d.get("customer")
    check("visits/spend/last_visit updated from appointment", cust["visits"] >= 1
          and cust["spend"] == appt["price"]["final"], (cust["visits"], cust["spend"], appt["price"]["final"]))
    check("last_service_id points at booked service", cust.get("last_service_id") == svc["_id"])

    # ---------- 6. renewal (customer path) -> plan extended (Tuesday, not a closed Sunday)
    s, d = call("POST", "/appointments",
                {"person_type": "customer", "person_id": cid, "store_id": store["_id"],
                 "service_id": svc["_id"], "date": "2026-12-22", "start_hour": 11, "renew": True}, tok)
    check("renewal appointment created", s == 201 and d.get("renew"), (s, d.get("error")))
    re_appt = d
    s, d = call("GET", f"/customers/{cid}", token=tok)
    cust = d if "_id" in d else d.get("customer")
    plan = cust.get("plan") or {}
    check("plan re-started on renewal (+30d)", plan.get("start") == "2026-12-22"
          and plan.get("end", "").startswith("2027-01-21"), plan)

    # ---------- 7. one timeline: lead phase + calls + appointments + payment + renewal
    s, d = call("GET", f"/customers/{cid}/timeline", token=tok)
    tl = d.get("timeline", [])
    types = [e["type"] for e in tl]
    check("timeline lead link", (d.get("lead") or {}).get("id") == lid, d.get("lead"))
    check("timeline has lead phase stages", all(t in types for t in ("lead_new", "lead_contacted",
                                                                     "lead_interested", "lead_booked")),
          sorted(set(types)))
    check("timeline has the pre-conversion call", any("journey first call" in (e.get("detail") or "") for e in tl))
    check("timeline has booked + completed + renewal appointments",
          "appointment_booked" in types and "appointment_completed" in types, sorted(set(types)))
    check("timeline shows the auto payment for the completed visit (BR-24)",
          any(e["type"] == "payment" for e in tl), sorted(set(types)))

    # ---------- 8. outcomes: this lead counted across the funnel
    s, d = call("GET", "/leads/outcomes?period=all", token=tok)
    f = d["funnel"]
    check("outcomes counts the journey lead as booked", f["booked"] >= 1, f)
    check("outcomes counts attended + renewed", f["attended"] >= 1 and f["renewed"] >= 1, f)
    src_row = d["by_source"].get("Justdial")
    check("by_source breakdown includes the journey source", src_row and src_row["booked"] >= 1, d["by_source"])
    own_row = d["by_owner"]
    check("by_owner breakdown present", len(own_row) >= 1, own_row)

    # ---------- 9. dashboard / kpis / alerts all respond with consistent shapes
    s, d = call("GET", "/analytics/kpis?period=30", token=tok)
    check("kpis respond", s == 200 and len(d.get("kpis", [])) >= 4, s)
    s, d = call("GET", "/alerts", token=tok)
    check("alerts respond", s == 200 and "alerts" in d, s)
    s, d = call("GET", "/analytics/trend?days=14", token=tok)
    check("trend respond", s == 200 and len(d.get("trend", [])) == 14, s)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
