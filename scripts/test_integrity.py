"""Integrity tests, two parts:

A. API: write endpoints reject bogus references (422/404) and accept valid ones.
B. In-process flow: seed -> create lead -> book -> complete -> cancel-a-copy -> run the
   full integrity scan on the mutated database; every rule must still pass.
"""
import asyncio
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
import random as _rnd

_R = str(_rnd.randint(1000, 9999))  # per-run suffix - the suite stays re-runnable on a live server

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


# ---------------------------------------------------------------- part A: API
def api_tests():
    print("Part A - API reference validation")
    s, d = call("POST", "/auth/login", {"email_or_phone": "admin@salesflow.app", "password": "Demo@123"})
    tok = d.get("access_token")
    check("admin login", s == 200 and tok, s)

    # leads
    s, d = call("POST", "/leads", {"name": "Bogus Store", "phone": "99111" + _R + "1",
                                   "store_id": "st_nope", "service_id": "svc_skin_glow"}, tok)
    check("lead bogus store 422", s == 422 and err_code(d) == "VALIDATION_ERROR", (s, d))
    s, d = call("POST", "/leads", {"name": "Bogus Service", "phone": "99111" + _R + "2",
                                   "store_id": "st_vijay", "service_id": "svc_nope"}, tok)
    check("lead bogus service 422", s == 422, (s, d))
    s, d = call("POST", "/leads", {"name": "No Store", "phone": "99111" + _R + "3",
                                   "service_id": "svc_skin_glow"}, tok)
    check("lead missing store 422", s == 422, (s, d))
    s, d = call("POST", "/leads", {"name": "Valid Lead", "phone": "99111" + _R + "4",
                                   "store_id": "st_vijay", "service_id": "svc_skin_glow",
                                   "source": "referral"}, tok)
    check("lead valid 201", s == 201 and d.get("store_id") == "st_vijay", (s, d))
    lead_id = d.get("_id", "")

    # employees
    s, d = call("POST", "/employees", {"name": "Bogus Emp", "role": "Therapist",
                                       "store_id": "st_nope", "service_ids": ["svc_skin_glow"]}, tok)
    check("employee bogus store 422", s == 422, (s, d))
    s, d = call("POST", "/employees", {"name": "Bogus Emp", "role": "Therapist",
                                       "store_id": "st_vijay", "service_ids": ["svc_nope"]}, tok)
    check("employee bogus service 422", s == 422, (s, d))
    s, d = call("POST", "/employees", {"name": "Valid Emp", "role": "Therapist",
                                       "store_id": "st_vijay", "service_ids": ["svc_skin_glow"]}, tok)
    check("employee valid 201", s == 201 and d.get("_id", "").startswith("emp_"), (s, d))
    emp_id = d.get("_id", "")
    s, d = call("POST", f"/employees/{emp_id}/move", {"store_id": "st_nope"}, tok)
    check("employee move bogus store 422", s == 422, (s, d))
    s, d = call("POST", f"/employees/{emp_id}/move", {"store_id": "st_palasia"}, tok)
    check("employee move valid", s == 200, (s, d))

    # waitlist
    s, d = call("POST", "/waitlist", {"person_type": "lead", "person_id": "led_nope",
                                      "store_id": "st_vijay", "service_id": "svc_skin_glow",
                                      "windows": [{"date": "2030-01-01", "start_hour": 11}]}, tok)
    check("waitlist bogus person 404", s == 404, (s, d))
    s, d = call("POST", "/waitlist", {"person_type": "alien", "person_id": lead_id,
                                      "store_id": "st_vijay", "service_id": "svc_skin_glow",
                                      "windows": [{"date": "2030-01-01", "start_hour": 11}]}, tok)
    check("waitlist bad person_type 422", s == 422, (s, d))
    s, d = call("POST", "/waitlist", {"person_type": "lead", "person_id": lead_id,
                                      "store_id": "st_nope", "service_id": "svc_skin_glow",
                                      "windows": [{"date": "2030-01-01", "start_hour": 11}]}, tok)
    check("waitlist bogus store 422", s == 422, (s, d))
    s, d = call("POST", "/waitlist", {"person_type": "lead", "person_id": lead_id,
                                      "store_id": "st_vijay", "service_id": "svc_nope",
                                      "windows": [{"date": "2030-01-01", "start_hour": 11}]}, tok)
    check("waitlist bogus service 422", s == 422, (s, d))
    s, d = call("POST", "/waitlist", {"person_type": "lead", "person_id": lead_id,
                                      "store_id": "st_vijay", "service_id": "svc_skin_glow",
                                      "windows": [{"date": "2030-01-01", "start_hour": 11}]}, tok)
    check("waitlist valid 201", s == 201, (s, d))

    # booking
    future = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    s, d = call("POST", "/appointments", {"person_type": "lead", "person_id": "led_nope",
                                          "store_id": "st_vijay", "service_id": "svc_skin_glow",
                                          "date": future, "start_hour": 11}, tok)
    check("booking bogus person 422", s in (400, 422) and err_code(d) == "VALIDATION_ERROR", (s, d))
    s, d = call("POST", "/appointments", {"person_type": "lead", "person_id": lead_id,
                                          "store_id": "st_nope", "service_id": "svc_skin_glow",
                                          "date": future, "start_hour": 11}, tok)
    check("booking bogus store 422", s in (400, 422), (s, d))
    s, d = call("POST", "/appointments", {"person_type": "lead", "person_id": lead_id,
                                          "store_id": "st_vijay", "service_id": "svc_nope",
                                          "date": future, "start_hour": 11}, tok)
    check("booking bogus service 422", s in (400, 422), (s, d))
    s, d = call("POST", "/appointments", {"person_type": "lead", "person_id": lead_id,
                                          "store_id": "st_vijay", "service_id": "svc_skin_glow",
                                          "date": future, "start_hour": 11}, tok)
    check("booking valid 201 + converted customer", s == 201 and d.get("customer_id"), (s, d))
    appt_id = d.get("_id", "")
    s, d = call("POST", f"/appointments/{appt_id}/complete", {}, tok)
    check("complete 200", s == 200 and d.get("status") == "completed", (s, d))

    # payment created for the completed appointment
    s, d = call("GET", "/audit?limit=200", token=tok)
    check("audit has appointment.complete", any(a["action"] == "appointment.complete" for a in d.get("audit", [])))

    # portal ingest: unknown service name still lands on a real service
    s, d = call("POST", "/ingest/justdial", {"name": "Portal Lead", "phone": "99222" + _R + "2",
                                             "service": "Nonexistent Service", "area": "Vijay Nagar"})
    check("ingest accepted", s == 200 and d.get("accepted"), (s, d))
    s, d2 = call("GET", f"/leads/{d.get('lead_id')}", token=tok)
    svc_ok = d2.get("service_id") in ("svc_skin_glow", "svc_hair_restore", "svc_fitness_pro",
                                      "svc_wellness_spa", "svc_dental_care")
    check("ingest lead has real service_id", s == 200 and svc_ok, (s, d2.get("service_id")))


# ------------------------------------------------- part B: in-process flow + scan
def flow_tests():
    print("Part B - in-process runtime flow + full integrity scan")
    sys.path.insert(0, "/home/z/my-project/scripts")
    sys.path.insert(0, "/home/z/my-project/download/salesflow/backend")
    from integrity_check import scan  # noqa: E402
    from app import booking as booking_svc  # noqa: E402
    from app.db import Database, MemAdapter  # noqa: E402
    from app.routers import people  # noqa: E402
    from app.routers.people import LeadIn  # noqa: E402
    from datetime import datetime, timedelta  # noqa: E402

    async def run():
        db = Database(MemAdapter())
        from app.seed import seed
        await seed(db)
        user = await db.users.find_one({"email": "admin@salesflow.app"})
        flt = {"period": "30", "store_ids": [], "store_id": None, "user": user}

        body = LeadIn(name="Flow Lead", phone="99333" + _R + "3", store_id="st_vijay",
                      service_id="svc_skin_glow", source="referral")
        lead = await people.add_lead(body, db, flt, user)
        check("flow: lead created", bool(lead.get("_id")), lead)

        future = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        appt = await booking_svc.book(db, user, "lead", lead["_id"], "st_vijay",
                                      "svc_skin_glow", future, 11)
        check("flow: booked + auto-converted", appt.get("customer_id"), appt.get("customer_id"))
        done = await booking_svc.complete(db, user, appt["_id"])
        check("flow: completed", done.get("status") == "completed")
        pay = await db.payments.find_one({"appointment_id": appt["_id"]})
        check("flow: payment row created", bool(pay), pay)
        cus = await db.customers.find_one({"_id": appt["customer_id"]})
        check("flow: visits/spend incremented", cus.get("visits") == 1 and cus.get("spend") == appt["price"]["final"],
              (cus.get("visits"), cus.get("spend")))

        # bogus refs must raise, not corrupt
        from app.booking import BookingError
        raised = 0
        for args in [("lead", "led_nope", "st_vijay", "svc_skin_glow"),
                     ("lead", lead["_id"], "st_nope", "svc_skin_glow"),
                     ("lead", lead["_id"], "st_vijay", "svc_nope")]:
            try:
                await booking_svc.book(db, user, args[0], args[1], args[2], args[3], future, 15)
            except BookingError:
                raised += 1
        check("flow: 3 bogus bookings rejected", raised == 3, raised)

        # cancel flow keeps integrity too
        appt2 = await booking_svc.book(db, user, "customer", appt["customer_id"], "st_vijay",
                                       "svc_skin_glow", future, 14)
        await booking_svc.cancel(db, user, appt2["_id"], "test")
        n_res = await db.slot_reservations.count_documents({"appointment_id": appt2["_id"]})
        check("flow: cancel releases reservations", n_res == 0, n_res)

        viol = await scan(db)
        if viol:
            for rule, items in viol.items():
                print(f"    RULE {rule}: {len(items)}")
                for x in items[:4]:
                    print(f"      - {x}")
        check("flow: integrity scan clean after mutations", not viol, list(viol))

    asyncio.run(run())


if __name__ == "__main__":
    api_tests()
    print()
    flow_tests()
    print(f"\n{PASS} passed, {FAIL} failed")
    raise SystemExit(1 if FAIL else 0)
