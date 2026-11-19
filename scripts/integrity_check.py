"""SalesFlow referential-integrity checker.

Loads the app's own data layer, seeds the demo database exactly like first boot,
then verifies every cross-collection relationship (foreign keys, denormalized
snapshots, lifecycle invariants). Exit 1 on any violation.

Run:  python3 scripts/integrity_check.py
"""
import asyncio
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "/home/z/my-project/download/salesflow/backend")

from app import config, rules  # noqa: E402
from app.db import Database, MemAdapter  # noqa: E402
from app.seed import seed  # noqa: E402

VIOL = defaultdict(list)


def bad(rule, col, doc_id, detail):
    VIOL[rule].append(f"{col}[{doc_id}] {detail}")


async def all_(db, col):
    return list(await getattr(db, col).find({}))


async def scan(db):
    """Run every integrity rule against a seeded/live database. Returns {rule: [msgs]}."""
    VIOL.clear()

    # ---------------- load everything ----------------
    cols = {}
    for c in ("organizations", "users", "stores", "store_distances", "store_services",
              "services", "employees", "shifts", "leaves", "leads", "customers",
              "identities", "interactions", "appointments", "slot_reservations",
              "slot_holds", "waitlist", "payments", "templates", "campaigns",
              "campaign_messages", "automations", "integrations", "consent_log",
              "audit_log", "events", "insights"):
        cols[c] = await all_(db, c)
    ids = {c: {d["_id"] for d in docs} for c, docs in cols.items()}
    org = cols["organizations"][0]
    leads_by_id = {l["_id"]: l for l in cols["leads"]}
    cus_by_id = {c["_id"]: c for c in cols["customers"]}

    for c, docs in cols.items():
        print(f"  {c}: {len(docs)}")

    # ---------------- org_id on every doc ----------------
    for c, docs in cols.items():
        if c == "organizations":
            continue
        for d in docs:
            if d.get("org_id") != org["_id"]:
                bad("org_id", c, d["_id"], f"org_id={d.get('org_id')!r} not the seeded org")

    # ---------------- simple FK edges ----------------
    EDGES = {
        "store_distances": [("from_store", "stores"), ("to_store", "stores")],
        "store_services": [("store_id", "stores"), ("service_id", "services")],
        "employees": [("store_id", "stores")],
        "shifts": [("employee_id", "employees")],
        "leaves": [("employee_id", "employees")],
        "leads": [("store_id", "stores"), ("service_id", "services"),
                  ("owner_id", "users"), ("customer_id", "customers")],
        "customers": [("store_id", "stores"), ("last_service_id", "services"),
                      ("lead_id", "leads")],
        "appointments": [("store_id", "stores"), ("service_id", "services"),
                         ("source_campaign_id", "campaigns")],
        "slot_reservations": [("appointment_id", "appointments"), ("store_id", "stores"),
                              ("employee_id", "employees")],
        "slot_holds": [("store_id", "stores"), ("user_id", "users")],
        "waitlist": [("store_id", "stores"), ("service_id", "services")],
        "payments": [("appointment_id", "appointments"), ("store_id", "stores")],
        "campaign_messages": [("campaign_id", "campaigns")],
    }
    for col, edges in EDGES.items():
        for d in cols[col]:
            for field, target in edges:
                v = d.get(field)
                if v is None:
                    continue
                if isinstance(v, list):
                    for x in v:
                        if x not in ids[target]:
                            bad("fk", col, d["_id"], f"{field} -> {x!r} missing in {target}")
                elif v not in ids[target]:
                    bad("fk", col, d["_id"], f"{field} -> {v!r} missing in {target}")

    # nullable single refs
    for e in cols["employees"]:
        if e.get("user_id") and e["user_id"] not in ids["users"]:
            bad("fk", "employees", e["_id"], f"user_id -> {e['user_id']!r} missing")
        for s in (e.get("service_ids") or []):
            if s not in ids["services"]:
                bad("fk", "employees", e["_id"], f"service_ids -> {s!r} missing")
    for p in cols["payments"]:
        if p.get("customer_id") and p["customer_id"] not in ids["customers"]:
            bad("fk", "payments", p["_id"], f"customer_id -> {p['customer_id']!r} missing")
    for w in cols["waitlist"]:
        tgt = "leads" if w.get("person_type") == "lead" else "customers"
        if w.get("person_id") not in ids[tgt]:
            bad("fk", "waitlist", w["_id"], f"person_id -> {w.get('person_id')!r} missing in {tgt}")
    for cm in cols["campaign_messages"]:
        tgt = "leads" if cm.get("person_kind") == "leads" else "customers"
        if cm.get("person_id") not in ids[tgt]:
            bad("fk", "campaign_messages", cm["_id"],
                f"person_id -> {cm.get('person_id')!r} missing in {tgt}")
    for a in cols["appointments"]:
        if a.get("person_type") == "customer":
            if a.get("customer_id") not in ids["customers"] or a.get("lead_id"):
                bad("person_ref", "appointments", a["_id"], "customer appt must set customer_id only")
        elif a.get("person_type") == "lead":
            if a.get("lead_id") not in ids["leads"] or not a.get("customer_id"):
                bad("person_ref", "appointments", a["_id"], "lead appt must set a valid lead_id")
            elif a.get("customer_id"):
                c = cus_by_id.get(a["customer_id"])
                if not c or c.get("lead_id") != a.get("lead_id"):
                    bad("person_ref", "appointments", a["_id"],
                        f"customer_id {a['customer_id']!r} is not the conversion of lead {a['lead_id']!r}")
        else:
            bad("person_ref", "appointments", a["_id"], f"bad person_type {a.get('person_type')!r}")
        expected = a.get("customer_id") if a.get("person_type") == "customer" else a.get("lead_id")
        if a.get("person_id") != expected:
            bad("person_ref", "appointments", a["_id"], f"person_id != {expected!r}")

    # ---------------- identities: every row must own its person ----------------
    seen_phones = defaultdict(list)
    for i in cols["identities"]:
        p = i.get("phone_norm")
        seen_phones[p].append(i)
        if not (i.get("lead_id") or i.get("customer_id")):
            bad("identity", "identities", i["_id"], "identity owns neither a lead nor a customer")
    for p, rows in seen_phones.items():
        if len(rows) > 1:
            # duplicate phones are a surfaced hygiene state: allowed only while all
            # rows belong to leads (the merge review queue resolves them)
            if any(r.get("customer_id") for r in rows):
                bad("identity", "identities", rows[0]["_id"],
                    f"phone {p} shared by {len(rows)} rows including a customer")
    cus_rows = {p: rows for p, rows in seen_phones.items() if len(rows) == 1}
    for c in cols["customers"]:
        i = (seen_phones.get(c.get("phone_norm")) or [None])[0]
        if not i:
            bad("identity", "customers", c["_id"], f"no identity row for phone {c.get('phone_norm')}")
        elif c.get("lead_id") and i.get("customer_id") != c["_id"]:
            bad("identity", "customers", c["_id"], "converted customer's identity row lacks customer_id")
    for l in cols["leads"]:
        rows = seen_phones.get(l.get("phone_norm")) or []
        if not any(r.get("lead_id") == l["_id"] for r in rows):
            bad("identity", "leads", l["_id"],
                f"no identity row for phone {l.get('phone_norm')} points to this lead "
                f"(rows: {[r.get('lead_id') for r in rows]})")

    # ---------------- lead <-> customer conversion pairs ----------------
    leads_by_id = {l["_id"]: l for l in cols["leads"]}
    cus_by_id = {c["_id"]: c for c in cols["customers"]}
    for l in cols["leads"]:
        cid = l.get("customer_id")
        if cid:
            c = cus_by_id.get(cid)
            if not c or c.get("lead_id") != l["_id"]:
                bad("conversion", "leads", l["_id"], f"customer_id {cid} not mutual (customer.lead_id={c and c.get('lead_id')})")
    for c in cols["customers"]:
        lid = c.get("lead_id")
        if lid:
            l = leads_by_id.get(lid)
            if not l or l.get("customer_id") != c["_id"]:
                bad("conversion", "customers", c["_id"], f"lead_id {lid} not mutual (lead.customer_id={l and l.get('customer_id')})")
            if l.get("stage") != "booked":
                bad("conversion", "customers", c["_id"], f"converted lead {lid} stage={l.get('stage')!r}, expected 'booked'")
    for l in cols["leads"]:
        if l.get("stage") == "booked" and not l.get("customer_id"):
            bad("conversion", "leads", l["_id"], "stage 'booked' but customer_id is None")

    # ---------------- customers: visits/spend/no_shows/segment derive from real rows ----------------
    from datetime import datetime, timezone
    now_dt = datetime.now(timezone.utc)
    per_cus = defaultdict(lambda: {"visits": 0, "spend": 0, "no_shows": 0, "last": None, "svc": None})
    for a in cols["appointments"]:
        cid = a.get("customer_id")
        if not cid or cid not in cus_by_id:
            continue
        st = per_cus[cid]
        when = None
        for h in reversed(a.get("status_history") or []):
            if h.get("status") == a.get("status"):
                when = h.get("at")
                break
        if a["status"] == "completed":
            st["visits"] += 1
            st["spend"] += (a.get("price") or {}).get("final") or 0
            st["svc"] = a.get("service_id")
            if when and (not st["last"] or when > st["last"]):
                st["last"] = when
        elif a["status"] == "no_show":
            st["no_shows"] += 1
            if when and (not st["last"] or when > st["last"]):
                st["last"] = when
    for c in cols["customers"]:
        st = per_cus.get(c["_id"], {"visits": 0, "spend": 0, "no_shows": 0, "last": None, "svc": None})
        if c.get("visits") != st["visits"]:
            bad("derived_customer", "customers", c["_id"], f"visits {c.get('visits')} != {st['visits']} completed visits")
        if c.get("spend") != st["spend"]:
            bad("derived_customer", "customers", c["_id"], f"spend {c.get('spend')} != {st['spend']} (sum of payments)")
        if c.get("no_shows") != st["no_shows"]:
            bad("derived_customer", "customers", c["_id"], f"no_shows {c.get('no_shows')} != {st['no_shows']}")
        if st["svc"] and c.get("last_service_id") != st["svc"]:
            bad("derived_customer", "customers", c["_id"], f"last_service_id {c.get('last_service_id')!r} != last completed {st['svc']!r}")
        seg = rules.segment_for(c, now_dt)
        if c.get("segment") != seg:
            bad("derived_customer", "customers", c["_id"], f"segment {c.get('segment')!r} != derived {seg!r}")

    # ---------------- appointments: staff eligibility + denormalized snapshots ----------------
    emps = {e["_id"]: e for e in cols["employees"]}
    stores = {s["_id"]: s for s in cols["stores"]}
    services = {s["_id"]: s for s in cols["services"]}
    for a in cols["appointments"]:
        eids = a.get("employee_ids") or []
        if not eids:
            bad("appointment_staff", "appointments", a["_id"], "no staff assigned")
        for eid in eids:
            e = emps.get(eid)
            if not e:
                continue
            if e.get("store_id") != a.get("store_id"):
                bad("appointment_staff", "appointments", a["_id"],
                    f"staff {eid} belongs to store {e.get('store_id')}, appt at {a.get('store_id')}")
            if a.get("service_id") not in (e.get("service_ids") or []):
                bad("appointment_staff", "appointments", a["_id"],
                    f"staff {eid} not trained for {a.get('service_id')}")
        names = [emps[e]["name"] if e in emps else "?" for e in eids]
        if (a.get("employee_names") or []) != names:
            bad("snapshot", "appointments", a["_id"], f"employee_names {a.get('employee_names')} != {names}")
        if a.get("store_name") != (stores.get(a.get("store_id")) or {}).get("name"):
            bad("snapshot", "appointments", a["_id"], f"store_name {a.get('store_name')!r} stale")
        if a.get("service_name") != (services.get(a.get("service_id")) or {}).get("name"):
            bad("snapshot", "appointments", a["_id"], f"service_name {a.get('service_name')!r} stale")
        if a.get("status") not in ("booked", "completed", "cancelled", "no_show"):
            bad("appointment_status", "appointments", a["_id"], f"status {a.get('status')!r}")
        if a.get("person_type") == "lead" and a.get("status") == "completed":
            pass  # seed may complete lead appointments; app converts on booking
        for h in (a.get("status_history") or []):
            if h.get("status") not in ("booked", "completed", "cancelled", "no_show"):
                bad("appointment_status", "appointments", a["_id"], f"history status {h.get('status')!r}")

    # ---------------- slot reservations vs appointments ----------------
    appts = {a["_id"]: a for a in cols["appointments"]}
    seen_res = set()
    for r in cols["slot_reservations"]:
        a = appts.get(r.get("appointment_id"))
        if not a:
            continue
        if r.get("employee_id") not in (a.get("employee_ids") or []):
            bad("reservation", "slot_reservations", r["_id"], f"employee not on appointment {a['_id']}")
        if not (a.get("start_hour") <= r.get("hour", -1) < a.get("start_hour", 0) + a.get("duration", 0)):
            bad("reservation", "slot_reservations", r["_id"], f"hour {r.get('hour')} outside appointment window")
        if a.get("status") not in ("booked", "completed"):
            bad("reservation", "slot_reservations", r["_id"], f"appointment status {a.get('status')!r} holds no slot")
        if (a["status"] in ("booked", "completed")) and r.get("store_id") != a.get("store_id"):
            bad("reservation", "slot_reservations", r["_id"], "store mismatch with appointment")
        seen_res.add((r.get("appointment_id"), r.get("employee_id"), r.get("hour")))
    for a in cols["appointments"]:
        held = sum(1 for r in cols["slot_reservations"] if r.get("appointment_id") == a["_id"])
        if a.get("status") == "booked":
            for eid in a.get("employee_ids") or []:
                for h in range(a.get("start_hour"), a.get("start_hour") + a.get("duration")):
                    if (a["_id"], eid, h) not in seen_res:
                        bad("reservation", "appointments", a["_id"], f"no reservation for {eid}@{h}")
        elif held:
            bad("reservation", "appointments", a["_id"],
                f"{a.get('status')} appointment still holds {held} reservations (app releases on complete/cancel)")

    # ---------------- payments vs appointments ----------------
    for p in cols["payments"]:
        a = appts.get(p.get("appointment_id"))
        if not a:
            continue
        if a.get("status") != "completed":
            bad("payment", "payments", p["_id"], f"appointment status {a.get('status')!r} but payment exists")
        if a.get("customer_id") and p.get("customer_id") != a.get("customer_id"):
            bad("payment", "payments", p["_id"], "customer mismatch with appointment")
        if p.get("amount") != (a.get("price") or {}).get("final"):
            bad("payment", "payments", p["_id"], f"amount {p.get('amount')} != appointment final {(a.get('price') or {}).get('final')}")
        if p.get("status") not in ("paid", "pending", "cancelled", "refunded"):
            bad("payment", "payments", p["_id"], f"status {p.get('status')!r}")

    # ---------------- interactions / consent / campaign messages person refs ----------------
    people = {**leads_by_id, **cus_by_id}
    for i in cols["interactions"]:
        p = people.get(i.get("person_id"))
        if not p:
            bad("person_ref", "interactions", i["_id"], f"person_id {i.get('person_id')!r} not lead/customer")
        elif i.get("person_name") != p.get("name"):
            bad("snapshot", "interactions", i["_id"], f"person_name {i.get('person_name')!r} != {p.get('name')!r}")
        if i.get("channel") not in ("call", "whatsapp", "sms", "email", "visit"):
            bad("channel", "interactions", i["_id"], f"channel {i.get('channel')!r}")
    for cl in cols["consent_log"]:
        if cl.get("person_id") not in people:
            bad("person_ref", "consent_log", cl["_id"], f"person_id {cl.get('person_id')!r} not lead/customer")
    for cm in cols["campaign_messages"]:
        p = people.get(cm.get("person_id"))
        if p and cm.get("person_name") != p.get("name"):
            bad("snapshot", "campaign_messages", cm["_id"], f"person_name stale")

    # ---------------- campaign stats must equal real rows ----------------
    msgs = defaultdict(list)
    for cm in cols["campaign_messages"]:
        msgs[cm.get("campaign_id")].append(cm)
    attributed = defaultdict(list)
    for a in cols["appointments"]:
        if a.get("source_campaign_id"):
            attributed[a["source_campaign_id"]].append(a)
    for c in cols["campaigns"]:
        st = c.get("stats") or {}
        n_sent = len(msgs.get(c["_id"], []))
        if st.get("sent") != n_sent:
            bad("campaign_stats", "campaigns", c["_id"], f"stats.sent={st.get('sent')} but {n_sent} campaign_messages rows")
        n_att = [a for a in attributed.get(c["_id"], []) if a.get("status") in ("booked", "completed")]
        if st.get("booked") != len(n_att):
            bad("campaign_stats", "campaigns", c["_id"], f"stats.booked={st.get('booked')} but {len(n_att)} attributed appointments")
        rev = sum((a.get("price") or {}).get("final") or 0 for a in n_att)
        if st.get("revenue") != rev:
            bad("campaign_stats", "campaigns", c["_id"], f"stats.revenue={st.get('revenue')} but attributed sum={rev}")
        if c.get("kind") == "leads" and c.get("segment_key") not in (
                "hot", "interested", "new", "stale_offers", "overdue_followups",
                "by_source", "by_service", "lost_by_reason"):
            bad("campaign_stats", "campaigns", c["_id"], f"unknown lead segment {c.get('segment_key')!r}")

    # ---------------- sources / ad spend ----------------
    for l in cols["leads"]:
        if l.get("source") not in config.SOURCES:
            bad("source", "leads", l["_id"], f"source {l.get('source')!r} not in config.SOURCES")
    for c in cols["customers"]:
        if c.get("source") not in config.SOURCES:
            bad("source", "customers", c["_id"], f"source {c.get('source')!r} not in config.SOURCES")
    if set((org.get("ad_spend") or {}).keys()) != set(config.SOURCES):
        bad("source", "organizations", org["_id"], "ad_spend keys != config.SOURCES")

    # ---------------- users <-> employees ----------------
    users_by_id = {u["_id"]: u for u in cols["users"]}
    linked = set()
    for e in cols["employees"]:
        uid = e.get("user_id")
        if not uid:
            continue
        if uid in linked:
            bad("user_link", "employees", e["_id"], f"user_id {uid} linked to multiple employees")
        linked.add(uid)
        u = users_by_id.get(uid)
        if u and "staff" not in (u.get("roles") or []):
            bad("user_link", "employees", e["_id"], f"linked user roles {u.get('roles')} lack 'staff'")

    # ---------------- audit entity refs ----------------
    ENTITY_COL = {"user": "users", "lead": "leads", "customer": "customers",
                  "employee": "employees", "appointment": "appointments",
                  "campaign": "campaigns", "store": "stores", "service": "services"}
    for a in cols["audit_log"]:
        eid = a.get("entity_id")
        col = ENTITY_COL.get(a.get("entity"))
        if col and eid and eid not in ids[col]:
            bad("audit_ref", "audit_log", a["_id"], f"entity_id {eid!r} missing in {col}")

    # ---------------- operational coverage ----------------
    for s in cols["stores"]:
        n = sum(1 for e in cols["employees"] if e.get("store_id") == s["_id"])
        if n == 0:
            bad("coverage", "stores", s["_id"], "no employees assigned")
    for e in cols["employees"]:
        if not (e.get("service_ids") or []):
            bad("coverage", "employees", e["_id"], "no trained services")

    # ---------------- report ----------------
    return VIOL


async def main():
    db = Database(MemAdapter())
    await seed(db)
    viol = await scan(db)
    print()
    if not viol:
        print("INTEGRITY OK - all relationships consistent")
        return 0
    total = 0
    for rule in sorted(viol):
        items = viol[rule]
        total += len(items)
        print(f"RULE {rule}: {len(items)} violation(s)")
        for x in items[:8]:
            print(f"   - {x}")
        if len(items) > 8:
            print(f"   ... and {len(items) - 8} more")
    print(f"\n{total} violation(s) across {len(viol)} rules")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
