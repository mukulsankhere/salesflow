"""Seed the SalesFlow demo organisation (PRD section 5.2: 5 stores, 5 services,
20 employees, 48 leads, 120+ customers, ~95 appointments today, 30-day history)."""
import random
from datetime import datetime, timedelta, timezone

from . import config, db as dbm, rules
from .campaigns import AUTOMATION_DEFS
from .security import hash_password

random.seed(42)

FIRST = ["Anita", "Riya", "Pooja", "Neha", "Sneha", "Divya", "Meera", "Kavya", "Isha", "Tara",
         "Aman", "Rahul", "Vikram", "Arjun", "Kabir", "Rohit", "Dev", "Yash", "Karan", "Sameer",
         "Aditi", "Nisha", "Priya", "Shreya", "Manav", "Lalit", "Omkar", "Jatin", "Varun", "Rishi"]
LAST = ["Verma", "Sharma", "Patel", "Joshi", "Dixit", "Kulkarni", "Nair", "Bose", "Chauhan",
        "Rane", "Mehta", "Shah", "Rao", "Gupta", "Singh", "Bhatt", "Pillai", "Desai"]
EMP_FIRST = ["Neha", "Devika", "Pooja", "Sana", "Rekha", "Aarav", "Vikas", "Sunit", "Maya", "Farah",
             "Gaurav", "Kiran", "Ritu", "Alok", "Zoya", "Nilesh", "Payal", "Harsh", "Swati", "Imran"]
EMP_ROLE = {"skin": "Therapist", "hair": "Stylist", "fitness": "Trainer", "spa": "Therapist",
            "dental": "Dentist"}

SERVICES = [
    {"key": "skin_glow", "name": "Skin Glow", "role_required": "Therapist", "duration_hours": 1,
     "staff_required": 1, "base_price": 350000},
    {"key": "hair_restore", "name": "Hair Restore", "role_required": "Stylist", "duration_hours": 2,
     "staff_required": 1, "base_price": 480000},
    {"key": "fitness_pro", "name": "Fitness Pro", "role_required": "Trainer", "duration_hours": 1,
     "staff_required": 2, "base_price": 250000},
    {"key": "wellness_spa", "name": "Wellness Spa", "role_required": "Therapist", "duration_hours": 2,
     "staff_required": 2, "base_price": 300000},
    {"key": "dental_care", "name": "Dental Care", "role_required": "Dentist", "duration_hours": 1,
     "staff_required": 1, "base_price": 550000},
]

ROLE_BY_SERVICE = {"svc_skin_glow": "Therapist", "svc_hair_restore": "Stylist",
                   "svc_fitness_pro": "Trainer", "svc_wellness_spa": "Therapist",
                   "svc_dental_care": "Dentist"}

STORES = [("Vijay Nagar", "Vijay Nagar", 22.7533, 75.8937),
          ("Palasia", "Palasia", 22.7595, 75.8896),
          ("Rajendra Nagar", "Rajendra Nagar", 22.7445, 75.8795),
          ("Bhawarkuan", "Bhawarkuan", 22.7196, 75.8577),
          ("Sudama Nagar", "Sudama Nagar", 22.7396, 75.8643)]

DIST = {(0, 1): 2.5, (0, 2): 4.2, (0, 3): 6.8, (0, 4): 8.1,
        (1, 2): 3.1, (1, 3): 7.4, (1, 4): 6.2,
        (2, 3): 9.3, (2, 4): 10.4, (3, 4): 5.5}


def _name(used):
    while True:
        n = f"{random.choice(FIRST)} {random.choice(LAST)}"
        if n not in used:
            used.add(n)
            return n


def _phone(used):
    while True:
        p = f"9{random.randint(100000000, 999999999)}"
        if p not in used:
            used.add(p)
            return p


async def seed(db):
    now = datetime.now(timezone.utc)
    today = datetime.now()
    today_s = today.strftime("%Y-%m-%d")

    org_id = config.ORG_ID
    await db.organizations.insert_one({
        "_id": org_id, "name": "GlowWell Services (Indore)", "city": "Indore",
        "timezone": "Asia/Kolkata", "currency": "INR",
        "settings": {"operating_hours": "10:00-22:00", "slot_minutes": 60},
        "rule_overrides": {}, "rule_history": [],
        "ad_spend": {"instagram_ads": 45000, "google_ads": 60000, "website_form": 12000,
                     "referral": 6000, "walk_in_call": 0, "justdial": 25000, "others": 0},
        "created_at": dbm.now_iso()})

    users = {}
    pw = hash_password("Demo@123")
    for email, name, roles, stores in [
            ("admin@salesflow.app", "Ravi Menon (Admin)", ["admin"], []),
            ("manager@salesflow.app", "Sunita Rao (Manager)", ["manager"], ["st_vijay"]),
            ("sales@salesflow.app", "Amit Jain (Sales)", ["sales"], ["st_vijay", "st_palasia"]),
            ("marketing@salesflow.app", "Farida Khan (Marketing)", ["marketing"], []),
            ("staff@salesflow.app", "Neha Singh (Staff)", ["staff"], []),
            ("viewer@salesflow.app", "Board Viewer", ["readonly"], [])]:
        uid = dbm.new_id("usr")
        users[roles[0]] = uid
        await db.users.insert_one({
            "_id": uid, "org_id": org_id, "name": name, "email": email,
            "phone": _phone(set()),
            # Single-login deployment: only the admin keeps a password; employees are
            # staff records (assignees, roster) without sign-in.
            "password_hash": pw if roles == ["admin"] else None,
            "login_disabled": roles != ["admin"], "roles": roles,
            "store_ids": stores, "status": "active", "created_at": dbm.now_iso()})

    store_ids = []
    for i, (name, area, lat, lng) in enumerate(STORES):
        sid = f"st_{name.split()[0].lower()}"
        store_ids.append(sid)
        await db.stores.insert_one({
            "_id": sid, "org_id": org_id, "name": name, "area": area, "zone": "West" if i < 2 else "South",
            "city": "Indore", "geo": {"lat": lat, "lng": lng},
            "target_utilization": 85, "status": "active", "created_at": dbm.now_iso()})
    for (a, b), km in DIST.items():
        await db.store_distances.insert_one({"_id": dbm.new_id("dist"), "org_id": org_id,
                                             "from_store": store_ids[a], "to_store": store_ids[b],
                                             "km": km})
        await db.store_distances.insert_one({"_id": dbm.new_id("dist"), "org_id": org_id,
                                             "from_store": store_ids[b], "to_store": store_ids[a],
                                             "km": km})

    services = {}
    for s in SERVICES:
        sid = f"svc_{s['key']}"
        services[sid] = {**s, "_id": sid}
        await db.services.insert_one({"_id": sid, "org_id": org_id, **{k: v for k, v in s.items() if k != "key"},
                                      "status": "active",
                                      "extra_rules": {"extra_hour_pct": 40, "extra_staff_pct": 30}})

    # 20 employees: 4 per store covering all 5 services (BR-05)
    skill_sets = [["svc_skin_glow", "svc_hair_restore"], ["svc_fitness_pro", "svc_wellness_spa"],
                  ["svc_fitness_pro", "svc_wellness_spa"], ["svc_dental_care", "svc_skin_glow"]]
    used_names = set()
    used_phones = set()
    emp_ids = {sid: [] for sid in store_ids}
    patterns = [{"mon": "full", "tue": "full", "wed": "full", "thu": "full", "fri": "full", "sat": "morning", "sun": "off"},
                {"mon": "morning", "tue": "morning", "wed": "full", "thu": "full", "fri": "evening", "sat": "full", "sun": "off"},
                {"mon": "evening", "tue": "full", "wed": "full", "thu": "evening", "fri": "full", "sat": "full", "sun": "morning"},
                {"mon": "full", "tue": "full", "wed": "morning", "thu": "full", "fri": "full", "sat": "evening", "sun": "off"}]
    for si, sid in enumerate(store_ids):
        for ei in range(4):
            eid = f"emp_{sid[3:]}_{ei + 1}"
            emp_ids[sid].append(eid)
            name = EMP_FIRST[si * 4 + ei] + " " + LAST[si + ei]
            used_names.add(name)
            role = ROLE_BY_SERVICE[skill_sets[ei][0]]
            await db.employees.insert_one({
                "_id": eid, "org_id": org_id, "name": name, "role": role,
                "store_id": sid, "service_ids": skill_sets[ei],
                "level": "senior" if ei == 0 else "standard",
                "shift_pattern": patterns[ei], "status": "active", "user_id": None,
                "hire_date": "2025-03-01"})
    # link the staff demo user to the first employee of Vijay Nagar
    await db.employees.update_one({"_id": emp_ids["st_vijay"][0]},
                                  {"$set": {"user_id": users["staff"], "name": "Neha Singh"}})
    used_names.add("Neha Singh")

    used_names_all = set(used_names)
    used_phones_all = set()

    # ---- 48 leads across stages with history over the last 30 days
    stage_weights = ["new"] * 9 + ["contacted"] * 8 + ["interested"] * 9 + ["offered"] * 8 + ["booked"] * 8 + ["lost"] * 6
    leads = []
    for i in range(48):
        stage = stage_weights[i % len(stage_weights)]
        created = now - timedelta(days=random.uniform(0, 30), hours=random.uniform(0, 10))
        lead = {
            "_id": f"led_{1000 + i}", "org_id": org_id,
            "name": _name(used_names_all), "phone_norm": _phone(used_phones_all),
            "phone_display": "", "age_group": random.choice(["18-25", "26-35", "36-45", "45+"]),
            "gender": random.choice(["Female", "Male"]),
            "store_id": random.choice(store_ids),
            "service_id": random.choice(list(services.keys())),
            "source": random.choice(config.SOURCES), "source_ref": f"ref_{random.randint(10000, 99999)}",
            "stage": stage, "stage_history": [{"stage": "new", "at": created.isoformat()},
                                              {"stage": stage, "at": (created + timedelta(hours=5)).isoformat()}],
            "score": 0, "score_factors": [], "follow_up_at": None, "offered_at": None,
            "lost_reason": None, "owner_id": users["sales"] if random.random() < 0.7 else users["manager"],
            "consent": {"whatsapp": random.random() < 0.75, "sms": random.random() < 0.5},
            "customer_id": None, "notes": None,
            "created_at": created.isoformat(),
            "last_contact_at": (created + timedelta(hours=2)).isoformat() if stage != "new" else None,
        }
        lead["phone_display"] = lead["phone_norm"]
        if stage == "offered":
            lead["offered_at"] = (created + timedelta(days=random.choice([1, 3, 7, 16, 20]))).isoformat()
        if stage in ("interested", "offered") and random.random() < 0.5:
            lead["follow_up_at"] = (now + timedelta(hours=random.uniform(-40, 40))).isoformat()
        if stage == "lost":
            lead["lost_reason"] = random.choice(config.LOST_REASONS)
        # deterministic score (12.3 factors)
        f = []
        if lead["source"] in ("google_ads", "referral", "walk_in_call", "justdial"):
            f.append({"f": "source_intent", "v": 30})
        elif lead["source"] in ("instagram_ads", "others"):
            f.append({"f": "source_intent", "v": 15})
        f.append({"f": "data_completeness", "v": 15})
        if random.random() < 0.8:
            f.append({"f": "capacity_nearby", "v": 20})
        if stage in ("interested", "offered"):
            f.append({"f": "replied", "v": 25})
        lead["score"] = min(100, sum(x["v"] for x in f))
        lead["score_factors"] = f
        await db.leads.insert_one(lead)
        await db.identities.insert_one({"org_id": org_id, "phone_norm": lead["phone_norm"],
                                        "lead_id": lead["_id"]})
        leads.append(lead)

    # ---- hygiene demo data: same-name leads with different phones (possible
    # duplicates for the review queue - the phone identity index already blocks
    # exact phone duplicates) + one incomplete lead
    name_dup = leads[0]
    for i, ph in enumerate(["9000000001", "9000000002"]):
        await db.leads.insert_one({
            "_id": f"led_90{i}", "org_id": org_id, "name": name_dup["name"],
            "phone_norm": ph, "phone_display": ph,
            "age_group": "26-35", "gender": "Female", "store_id": name_dup["store_id"],
            "service_id": name_dup["service_id"], "source": "website_form",
            "source_ref": None, "stage": "contacted", "score": 45, "score_factors": [],
            "follow_up_at": None, "offered_at": None, "lost_reason": None,
            "owner_id": None, "consent": {"whatsapp": True}, "customer_id": None,
            "notes": None, "created_at": (now - timedelta(days=3)).isoformat(),
            "last_contact_at": (now - timedelta(days=2)).isoformat(),
            "stage_history": [{"stage": "new", "at": (now - timedelta(days=3)).isoformat()},
                              {"stage": "contacted", "at": (now - timedelta(days=2)).isoformat()}]})
        await db.identities.insert_one({"org_id": org_id, "phone_norm": ph,
                                        "lead_id": f"led_90{i}"})
    await db.leads.insert_one({
        "_id": "led_909", "org_id": org_id, "name": "Incomplete Intake",
        "phone_norm": "9000000009", "phone_display": "9000000009",
        "age_group": None, "gender": None, "store_id": None, "service_id": None,
        "source": "justdial", "source_ref": None, "stage": "new", "score": 30,
        "score_factors": [], "follow_up_at": None, "offered_at": None,
        "lost_reason": None, "owner_id": None, "consent": {}, "customer_id": None,
        "notes": "Portal lead missing store and service", "created_at": now.isoformat(),
        "last_contact_at": None,
        "stage_history": [{"stage": "new", "at": now.isoformat()}]})
    await db.identities.insert_one({"org_id": org_id, "phone_norm": "9000000009",
                                    "lead_id": "led_909"})

    # ---- 130 customers with plans, visits, spend
    svc_price = {sid: s["base_price"] for sid, s in services.items()}
    customers = []
    plan_pool = (["active"] * 70 + ["renewal_due"] * 10 + ["expired_0_7"] * 8 +
                 ["expired_8_21"] * 9 + ["expired_22p"] * 15 + ["no_plan"] * 18)
    for i in range(132):
        kind = plan_pool[i % len(plan_pool)]
        store = random.choice(store_ids)
        svc = random.choice(list(services.keys()))
        visits = random.randint(1, 14) if kind != "no_plan" else random.randint(1, 3)
        spend = int(visits * svc_price[svc] * random.uniform(0.9, 1.3))
        c = {
            "_id": f"cus_{2000 + i}", "org_id": org_id,
            "name": _name(used_names_all), "phone_norm": _phone(used_phones_all),
            "phone_display": "", "store_id": store, "last_service_id": svc,
            "visits": visits, "spend": spend, "last_visit_at": None,
            "plan": None, "segment": "new", "consent": {"whatsapp": random.random() < 0.8, "sms": random.random() < 0.6},
            "lead_id": None, "source": random.choice(config.SOURCES), "tags": [],
            "no_shows": 1 if random.random() < 0.12 else 0, "notes": None,
            "created_at": (now - timedelta(days=random.uniform(10, 120))).isoformat(),
        }
        c["phone_display"] = c["phone_norm"]
        if kind == "active":
            start = today - timedelta(days=random.randint(5, 24))
            c["plan"] = {"start": start.strftime("%Y-%m-%d"),
                         "end": (start + timedelta(days=30)).strftime("%Y-%m-%d")}
            c["last_visit_at"] = (now - timedelta(days=random.uniform(0, 12))).isoformat()
        elif kind == "renewal_due":
            start = today - timedelta(days=random.randint(25, 27))
            c["plan"] = {"start": start.strftime("%Y-%m-%d"),
                         "end": (start + timedelta(days=30)).strftime("%Y-%m-%d")}
            c["last_visit_at"] = (now - timedelta(days=random.uniform(0, 10))).isoformat()
        elif kind.startswith("expired"):
            days = {"expired_0_7": random.randint(1, 7), "expired_8_21": random.randint(8, 21),
                    "expired_22p": random.randint(22, 60)}[kind]
            end = today - timedelta(days=days)
            c["plan"] = {"start": (end - timedelta(days=30)).strftime("%Y-%m-%d"),
                         "end": end.strftime("%Y-%m-%d")}
            c["last_visit_at"] = (now - timedelta(days=days + random.randint(1, 10))).isoformat()
        else:
            c["last_visit_at"] = (now - timedelta(days=random.uniform(2, 40))).isoformat()
        await db.customers.insert_one(c)
        await db.identities.insert_one({"org_id": org_id, "phone_norm": c["phone_norm"],
                                        "customer_id": c["_id"]})
        customers.append(c)
    # visits/spend/last_visit are DERIVED from the appointment history further down
    # (inter-relation pass 2), so they always equal the customer's real visits and
    # payments; VIP emerges from BR-20 thresholds instead of being forced.

    # ---- appointments: ~95 today + 5 future days + 30-day history
    hour_now = datetime.now().hour
    booked_hours = {(eid, h) for eid in store_ids for h in []}  # set of (emp, hour)
    booked_set = set()
    statuses_pool = ["completed"] * 8 + ["no_show"] + ["cancelled"]

    async def make_appt(store, svc, date_s, hour, status, person, person_type, day_offset):
        svc_doc = services[svc]
        need, du = svc_doc["staff_required"], svc_doc["duration_hours"]
        if hour + du > 22:
            return None
        free = []
        for eid in emp_ids[store]:
            idx = int(eid.split("_")[-1]) - 1
            skills = skill_sets[idx]
            pattern = patterns[idx]
            from .capacity import effective_shift, shift_covers
            shift = effective_shift({"_id": eid, "shift_pattern": pattern}, date_s, {})
            if svc in skills and shift_covers(shift, hour, du) \
                    and all((eid, hh) not in booked_set for hh in range(hour, hour + du)):
                free.append((eid, sum(1 for (a, h) in booked_set if a == eid)))
        if len(free) < need:
            return None
        free.sort(key=lambda x: x[1])
        chosen = [e for e, _ in free[:need]]
        for eid in chosen:
            for hh in range(hour, hour + du):
                booked_set.add((eid, hh))
        appt_id = dbm.new_id("appt")
        price = rules.price_breakup(services[svc], None, {"rule_overrides": {}})
        emp_names = []
        for eid in chosen:
            emp = await db.employees.find_one({"_id": eid})
            emp_names.append(emp["name"])
        appt = {
            "_id": appt_id, "org_id": org_id, "person_type": person_type, "person_id": person["_id"],
            "customer_id": person["_id"] if person_type == "customer" else None,
            "lead_id": person["_id"] if person_type == "lead" else None,
            "person": {"name": person["name"], "phone": person["phone_display"]},
            "store_id": store, "service_id": svc, "service_name": services[svc]["name"],
            "store_name": dict(zip(store_ids, [s[0] for s in STORES]))[store],
            "employee_ids": chosen, "employee_names": emp_names,
            "date": date_s, "start_hour": hour, "duration": du, "renew": False,
            "price": price, "status": status,
            "status_history": [{"status": status, "at": (datetime.fromisoformat(date_s + "T10:00:00") + timedelta(hours=hour)).isoformat()}],
            "source_campaign_id": None, "created_customer": False,
            "created_at": (datetime.fromisoformat(date_s + "T00:00:00") - timedelta(days=day_offset)).isoformat(),
            "created_by": "Seed"}
        await db.appointments.insert_one(appt)
        if status == "booked":  # reservations live only while the slot is held; the
            for eid in chosen:  # app releases them on complete/cancel/no-show (booking.py)
                for hh in range(hour, hour + du):
                    await db.slot_reservations.insert_one({
                        "_id": dbm.new_id("res"), "org_id": org_id, "appointment_id": appt_id,
                        "store_id": store, "date": date_s, "hour": hh, "employee_id": eid})
        if status == "completed":
            await db.payments.insert_one({
                "_id": dbm.new_id("pay"), "org_id": org_id, "appointment_id": appt_id,
                "customer_id": person["_id"] if person_type == "customer" else None,
                "store_id": store, "amount": price["final"], "method": random.choice(["upi", "card", "cash"]),
                "status": "paid", "provider_ref": f"pay_{random.randint(10**9, 10**10 - 1)}",
                "created_at": appt["created_at"], "paid_at": appt["created_at"]})
        return appt

    stores_names = {sid: s[0] for sid, s in zip(store_ids, STORES)}
    n_today = 0
    for si, store in enumerate(store_ids):
        placed = 0
        for _ in range(80):  # ~95 today across 5 stores (capacity- and shift-limited)
            svc = random.choice(list(services.keys()))
            hour = random.choice(config.SLOT_HOURS)
            if hour <= hour_now:
                status = random.choice(statuses_pool[:9])
            else:
                status = "booked"
            person = random.choice(customers) if random.random() < 0.75 else random.choice(leads)
            ptype = "customer" if person in customers else "lead"
            got = await make_appt(store, svc, today_s, hour, status, person, ptype, 0)
            if got:
                placed += 1
                n_today += 1
            if placed >= 26:
                break

    # future days (lighter)
    for off in range(1, 6):
        d = (today + timedelta(days=off)).strftime("%Y-%m-%d")
        booked_set = set()  # per-date occupancy
        for _ in range(8):
            store = random.choice(store_ids)
            svc = random.choice(list(services.keys()))
            person = random.choice(customers) if random.random() < 0.8 else random.choice(leads)
            ptype = "customer" if person in customers else "lead"
            await make_appt(store, svc, d, random.choice(config.SLOT_HOURS), "booked", person, ptype, off)

    # 30-day history (completed) for trends and revenue
    for off in range(1, 31):
        d = (today - timedelta(days=off)).strftime("%Y-%m-%d")
        booked_set = set()  # per-date occupancy
        for _ in range(random.randint(28, 38)):
            store = random.choice(store_ids)
            svc = random.choice(list(services.keys()))
            person = random.choice(customers)
            await make_appt(store, svc, d, random.choice(config.SLOT_HOURS), "completed", person, "customer", off)

    # ---- INTER-RELATION PASS 1: lead -> customer conversion (mirrors booking.book:
    # every lead that reaches the booking stage becomes a customer, linked both ways,
    # and the appointment now carries the converted customer_id)
    async def _convert_lead(l):
        cid = dbm.new_id("cus")
        doc = {
            "_id": cid, "org_id": org_id, "name": l["name"],
            "phone_norm": l["phone_norm"],
            "phone_display": l.get("phone_display") or l["phone_norm"],
            "store_id": l["store_id"], "last_service_id": l["service_id"],
            "visits": 0, "spend": 0, "last_visit_at": None,
            "plan": {"start": today_s, "end": (today + timedelta(days=30)).strftime("%Y-%m-%d")},
            "segment": "new", "consent": l.get("consent") or {},
            "lead_id": l["_id"], "source": l.get("source"), "tags": [],
            "no_shows": 0,
            "created_at": (now - timedelta(days=random.uniform(1, 10))).isoformat(),
        }
        await db.customers.insert_one(doc)
        converted_docs.append(doc)
        await db.identities.update_one({"org_id": org_id, "phone_norm": l["phone_norm"]},
                                       {"$set": {"customer_id": cid}})
        hist = (l.get("stage_history") or []) + [{"stage": "booked", "at": now.isoformat()}]
        await db.leads.update_one({"_id": l["_id"]},
                                  {"$set": {"customer_id": cid, "stage": "booked",
                                            "stage_history": hist}})
        l["customer_id"], l["stage"] = cid, "booked"
        return cid

    converted_docs: list = []
    leads_by_id = {l["_id"]: l for l in leads}
    lead_appts = [a for a in await db.appointments.find({"org_id": org_id, "person_type": "lead"})
                  if a.get("status") in ("booked", "completed")]
    for a in lead_appts:
        l = leads_by_id[a["lead_id"]]
        if not l.get("customer_id"):
            await _convert_lead(l)
        await db.appointments.update_one({"_id": a["_id"]},
                                         {"$set": {"customer_id": l["customer_id"],
                                                   "created_customer": True}})
        # the completed appointment's payment must follow its appointment's customer
        await db.payments.update_one({"appointment_id": a["_id"]},
                                     {"$set": {"customer_id": l["customer_id"]}})
    for l in leads:
        if l["stage"] == "booked" and not l.get("customer_id"):
            await _convert_lead(l)

    # a slice of the converted leads already finished the journey: their booking is
    # completed and every third one renewed. Clones mirror a real booking made
    # through the app (person_type lead, lead_id + converted customer_id, the same
    # trained staff) on unique past dates so no slot collides.
    comp_appts = [a for a in await db.appointments.find({"org_id": org_id, "status": "completed"})]
    for i, c in enumerate(converted_docs):
        if not comp_appts or i % 3 == 2:
            continue
        base = comp_appts[i % len(comp_appts)]
        base_d = datetime.strptime(base["date"], "%Y-%m-%d")
        apt = {**base, "_id": dbm.new_id("apt"),
               "person_type": "lead", "person_id": c.get("lead_id"),
               "customer_id": c["_id"], "lead_id": c.get("lead_id"),
               "person": {"name": c["name"], "phone": c.get("phone_display")},
               "date": (base_d - timedelta(days=45 + i)).strftime("%Y-%m-%d"),
               "renew": i % 3 == 1,
               "created_at": c.get("created_at"), "created_by": "seed"}
        await db.appointments.insert_one(apt)

    # ---- INTER-RELATION PASS 2: derive per-customer history from their real
    # appointments (mirrors booking.complete: visits/spend/last_visit accumulate per
    # completed visit, no_shows per no-show, segment via rules.segment_for)
    stats = {}
    for a in await db.appointments.find({"org_id": org_id}):
        cid = a.get("customer_id")
        if not cid:
            continue
        st = stats.setdefault(cid, {"visits": 0, "spend": 0, "no_shows": 0,
                                    "last": None, "svc": None})
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
    for c in customers + converted_docs:
        st = stats.get(c["_id"], {"visits": 0, "spend": 0, "no_shows": 0,
                                  "last": None, "svc": None})
        upd = {"visits": st["visits"], "spend": st["spend"], "no_shows": st["no_shows"],
               "last_visit_at": st["last"],
               "last_service_id": st["svc"] or c.get("last_service_id")}
        upd["segment"] = rules.segment_for({**c, **upd}, now)
        await db.customers.update_one({"_id": c["_id"]}, {"$set": upd})
        c.update(upd)

    # ---- interactions for leads (call logs, replies)
    for l in leads[:40]:
        for k in range(random.randint(1, 3)):
            direction = "out" if k == 0 else random.choice(["out", "in"])
            await db.interactions.insert_one({
                "_id": dbm.new_id("int"), "org_id": org_id, "person_id": l["_id"],
                "person_name": l["name"], "channel": random.choice(["call", "whatsapp", "sms"]),
                "direction": direction, "content": "Campaign follow-up" if direction == "out" else "Interested, asked for timings",
                "outcome": "logged" if direction == "out" else "reply",
                "user_name": "Amit Jain (Sales)", "at": (datetime.fromisoformat(l["created_at"]) + timedelta(hours=k * 6)).isoformat()})

    # ---- waitlist
    wl_people = [(l, "lead") for l in leads if l["stage"] == "interested"][:3]
    wl_people += [(c, "customer") for c in customers[:1]]
    for i, (p, ptype) in enumerate(wl_people):
        svc = p.get("service_id") or p.get("last_service_id")
        await db.waitlist.insert_one({
            "_id": dbm.new_id("wl"), "org_id": org_id, "person_type": ptype, "person_id": p["_id"],
            "person_name": p["name"], "store_id": p["store_id"] if ptype == "lead" else p["store_id"],
            "service_id": svc, "windows": [{"date": (today + timedelta(days=1)).strftime("%Y-%m-%d"),
                                            "start_hour": random.randint(17, 20)}],
            "renew": ptype == "customer", "status": "offered" if i == 0 else "waiting",
            "offer": {"date": (today + timedelta(days=1)).strftime("%Y-%m-%d"), "start_hour": 18,
                      "expires_at": (datetime.now() + timedelta(minutes=30)).isoformat()} if i == 0 else None,
            "created_at": (now - timedelta(hours=i * 5)).isoformat()})

    # ---- templates
    for t in [
        {"name": "Hot Lead Offer", "channel": "whatsapp", "body": "Hi {name}, your {service} slot at {store} is ready! Book this week and get {discount} off - final price {price}. Shall I hold it for you?", "provider_status": "approved"},
        {"name": "New Lead Welcome", "channel": "whatsapp", "body": "Hi {name}, thanks for your interest in {service} at {store}! When can we call you to fix a slot?", "provider_status": "approved"},
        {"name": "Booking Confirmation", "channel": "whatsapp", "body": "Hi {name}, your {service} at {store} is booked for {price}. Reply to reschedule anytime.", "provider_status": "approved"},
        {"name": "Renewal Reminder", "channel": "whatsapp", "body": "Hi {name}, your plan at {store} ends soon. Renew now and keep your preferred slot and benefits!", "provider_status": "approved"},
        {"name": "Win-Back Offer", "channel": "whatsapp", "body": "Hi {name}, we miss you at {store}! Come back for {service} at a special price of {price} this week.", "provider_status": "approved"},
        {"name": "No-Show Follow-Up", "channel": "whatsapp", "body": "Hi {name}, we missed you today at {store}. Shall we rebook your {service} for another slot?", "provider_status": "approved"},
    ]:
        await db.templates.insert_one({"_id": dbm.new_id("tpl"), "org_id": org_id,
                                       "placeholders": ["name", "service", "store", "price", "discount"],
                                       **t})

    # ---- campaigns (one live with stats, one completed)
    live_id = "cmp_hot_21"
    await db.campaigns.insert_one({
        "_id": live_id, "org_id": org_id, "name": "Hot Lead Offer - Week 46", "kind": "leads",
        "segment_key": "hot", "sub_key": None,
        "filter_snapshot": {"period": "30", "store_ids": []}, "channel": "whatsapp",
        "template_body": "Hi {name}, your {service} slot at {store} is ready! 10% off this week - {price}.",
        "offer_pct": 10, "schedule": None, "status": "live",
        "stats": {"audience": 18, "sent": 18, "delivered": 18, "read": 13, "replied": 5, "booked": 2,
                  "revenue": 810000},
        "created_by": "Farida Khan (Marketing)", "created_at": (now - timedelta(days=2)).isoformat()})
    await db.campaigns.insert_one({
        "_id": "cmp_winback_9", "org_id": org_id, "name": "Win-Back Wave - October", "kind": "customers",
        "segment_key": "expired_22p", "sub_key": None,
        "filter_snapshot": {"period": "all", "store_ids": []}, "channel": "whatsapp",
        "template_body": "Hi {name}, we miss you at {store}! {service} at {price} this week.",
        "offer_pct": 15, "schedule": None, "status": "completed",
        "stats": {"audience": 24, "sent": 24, "delivered": 24, "read": 19, "replied": 6, "booked": 3,
                  "revenue": 1140000},
        "created_by": "Farida Khan (Marketing)", "created_at": (now - timedelta(days=20)).isoformat()})
    hot_leads = [l for l in leads if l["score"] >= 80][:18]
    for i, l in enumerate(hot_leads):
        svc = services.get(l["service_id"], services["svc_skin_glow"])
        await db.campaign_messages.insert_one({
            "_id": dbm.new_id("cmsg"), "org_id": org_id, "campaign_id": live_id, "person_id": l["_id"],
            "person_name": l["name"], "person_kind": "leads", "channel": "whatsapp",
            "rendered_text": f"Hi {l['name']}, your {svc['name']} slot is ready! 10% off this week.",
            "status": "replied" if i < 5 else "delivered",
            "created_at": (now - timedelta(days=2)).isoformat(),
            "sent_at": (now - timedelta(days=2)).isoformat()})

    # ---- automations (enabled defaults)
    for d in AUTOMATION_DEFS:
        await db.automations.insert_one({"_id": dbm.new_id("aut"), "org_id": org_id, "name": d["name"],
                                         "enabled": True, "stats": {"runs": 0, "messages_sent": 0}})

    # ---- follow-up automation rules (disabled until the user previews + enables them)
    await db.automation_rules.insert_one({
        "_id": dbm.new_id("rule"), "org_id": org_id, "name": "Instagram Ads -> Amit Jain (Sales)",
        "type": "assign_new_leads", "source": "instagram_ads", "owner_id": users["sales"],
        "enabled": False, "stats": {"runs": 0, "matched": 0},
        "created_at": now.isoformat()})
    await db.automation_rules.insert_one({
        "_id": dbm.new_id("rule"), "org_id": org_id, "name": "No contact in 1 day -> remind owner",
        "type": "no_contact_reminder", "days": 1,
        "enabled": False, "stats": {"runs": 0, "matched": 0},
        "created_at": now.isoformat()})

    # ---- integrations hub state (connection status, last sync, sync issues, retries)
    for it in [
        {"_id": "int_whatsapp", "name": "WhatsApp Business Cloud API", "kind": "whatsapp", "status": "error",
         "note": "Provider interface ready; demo credentials active.",
         "last_sync": (now - timedelta(minutes=42)).isoformat(),
         "issues": [{"code": "RATE_LIMIT", "detail": "429 rate limit on 3 broadcast batches",
                     "count": 3, "since": (now - timedelta(hours=2)).isoformat()}]},
        {"_id": "int_sms", "name": "SMS Gateway (DLT)", "kind": "sms", "status": "demo",
         "note": "DLT-registered template IDs pending.", "last_sync": None, "issues": []},
        {"_id": "int_voice", "name": "Voice / Click-to-call", "kind": "voice", "status": "demo",
         "note": "Telephony API pending.", "last_sync": None, "issues": []},
        {"_id": "int_payments", "name": "Payment Gateway (Razorpay)", "kind": "payments", "status": "live",
         "note": "Payment links recorded; settlement sync every 15 min.",
         "last_sync": (now - timedelta(minutes=12)).isoformat(), "issues": []},
        {"_id": "int_portals", "name": "Lead Portals (Google/Meta/Justdial)", "kind": "portals", "status": "error",
         "note": "Webhook endpoints under /api/v1/ingest/{portal}; pollers configured.",
         "last_sync": (now - timedelta(hours=5)).isoformat(),
         "issues": [{"code": "WEBHOOK_AUTH", "detail": "Justdial poller token expired - needs a fresh token",
                     "count": 17, "since": (now - timedelta(hours=5)).isoformat()}]},
        {"_id": "int_calendar", "name": "Google Calendar Sync", "kind": "calendar", "status": "live",
         "note": "Two-way sync of booked appointments.",
         "last_sync": (now - timedelta(minutes=3)).isoformat(), "issues": []},
        {"_id": "int_database", "name": "MongoDB", "kind": "database",
         "status": "live" if db.is_mongo else "fallback",
         "note": "MongoDB replica set connected." if db.is_mongo
                 else "In-process document store active (set MONGO_URI for MongoDB).",
         "last_sync": now.isoformat(), "issues": []},
        {"_id": "int_queue", "name": "Redis", "kind": "queue", "status": "demo",
         "note": "In-process queue active (set REDIS_URL in production).", "last_sync": None, "issues": []},
    ]:
        await db.integrations.insert_one({**it, "org_id": org_id, "retries": 0})

    # ---- customer interactions (call / message history for unified timelines)
    cust_for_int = [c for c in customers if (c.get("consent") or {}).get("whatsapp")][:24]
    for c in cust_for_int:
        for j in range(random.randint(1, 2)):
            at = now - timedelta(days=random.uniform(1, 25), hours=random.uniform(0, 8))
            await db.interactions.insert_one({
                "_id": dbm.new_id("int"), "org_id": org_id, "person_id": c["_id"],
                "person_name": c["name"], "channel": random.choice(["call", "whatsapp"]),
                "direction": "out" if j == 0 else random.choice(["in", "out"]),
                "content": random.choice(["Renewal reminder call", "Checked in after the last visit",
                                          "Asked about slot availability", "Feedback: loved the therapist",
                                          "Reschedule request handled"]),
                "outcome": "logged" if j == 0 else "reply",
                "user_name": "Amit Jain (Sales)", "at": at.isoformat()})

    # ---- campaign messages to customers (win-back wave) so timelines show campaign responses
    winback_aud = [c for c in customers
                   if c.get("plan") and rules.parse_dt(c["plan"]["end"]) and rules.parse_dt(c["plan"]["end"]) < now][:10]
    for c in winback_aud:
        svc = services.get(c.get("last_service_id"), services["svc_skin_glow"])
        await db.campaign_messages.insert_one({
            "_id": dbm.new_id("cmsg"), "org_id": org_id, "campaign_id": "cmp_winback_9",
            "person_id": c["_id"],
            "person_name": c["name"], "person_kind": "customers", "channel": "whatsapp",
            "rendered_text": f"Hi {c['name']}, we miss you! {svc['name']} at a special price this week.",
            "status": random.choice(["delivered", "read", "replied"]),
            "created_at": (now - timedelta(days=18)).isoformat(),
            "sent_at": (now - timedelta(days=18)).isoformat()})

    # ---- INTER-RELATION PASS 3: campaign attribution + stats derived from real rows
    # (booked/revenue equal the attributed appointments; sent/delivered/read/replied
    # equal the campaign_messages rows - nothing is hand-written)
    async def _refresh_campaign_stats(camp_id):
        msgs = await db.campaign_messages.find({"org_id": org_id, "campaign_id": camp_id})
        atts = [a for a in await db.appointments.find({"org_id": org_id, "source_campaign_id": camp_id})
                if a.get("status") in ("booked", "completed")]
        replied = sum(1 for m in msgs if m.get("status") == "replied")
        read = sum(1 for m in msgs if m.get("status") in ("read", "replied"))
        await db.campaigns.update_one({"_id": camp_id}, {"$set": {"stats": {
            "audience": len(msgs), "sent": len(msgs), "delivered": len(msgs),
            "read": read, "replied": replied, "booked": len(atts),
            "revenue": sum((a.get("price") or {}).get("final") or 0 for a in atts)}}})

    async def _attribute(camp_id, pids, want):
        n = 0
        for a in await db.appointments.find({"org_id": org_id}):
            if n >= want:
                break
            pid = a.get("customer_id") or a.get("lead_id")
            if pid in pids and a.get("status") in ("booked", "completed") and not a.get("source_campaign_id"):
                await db.appointments.update_one({"_id": a["_id"]},
                                                 {"$set": {"source_campaign_id": camp_id}})
                n += 1
        return n

    hot_pids = {m["person_id"] for m in await db.campaign_messages.find(
        {"org_id": org_id, "campaign_id": live_id})}
    n_att = await _attribute(live_id, hot_pids, 2)
    for l in [x for x in leads if x["_id"] in hot_pids]:
        if n_att >= 2:
            break
        if not l.get("customer_id"):
            await _convert_lead(l)
        for hour in [18, 17, 19, 16, 11, 12]:
            d = (today + timedelta(days=2)).strftime("%Y-%m-%d")
            appt = await make_appt(l["store_id"], l["service_id"], d, hour, "booked",
                                   {"_id": l["customer_id"], "name": l["name"],
                                    "phone_display": l.get("phone_display") or l["phone_norm"]},
                                   "customer", 2)
            if appt:
                await db.appointments.update_one({"_id": appt["_id"]},
                                                 {"$set": {"source_campaign_id": live_id}})
                n_att += 1
                break
    await _refresh_campaign_stats(live_id)

    win_pids = {m["person_id"] for m in await db.campaign_messages.find(
        {"org_id": org_id, "campaign_id": "cmp_winback_9"})}
    await _attribute("cmp_winback_9", win_pids, 3)
    await _refresh_campaign_stats("cmp_winback_9")

    # ---- consent log + audit + feed events
    for c in customers[:30]:
        await db.consent_log.insert_one({"_id": dbm.new_id("clog"), "org_id": org_id,
                                         "person_id": c["_id"], "channel": "whatsapp",
                                         "action": "opt-in", "source": "intake form",
                                         "at": (now - timedelta(days=30)).isoformat()})
    for i in range(12):
        await db.audit_log.insert_one({"_id": dbm.new_id("aud"), "org_id": org_id,
                                       "who": "Ravi Menon (Admin)", "who_id": users["admin"],
                                       "roles": ["admin"], "action": "seed.bootstrap",
                                       "entity": "system", "at": (now - timedelta(hours=3)).isoformat()})
    feed = [
        ("lead.created", "New lead Riya Verma (Skin Glow, Vijay Nagar) - score 84"),
        ("interaction.logged", "WhatsApp reply from Aman Patel: 'Yes, what times are free tomorrow?'"),
        ("appointment.booked", "Anita Verma booked Wellness Spa at Palasia for tomorrow 5 PM"),
        ("visit.completed", "Kabir Joshi completed Fitness Pro at Rajendra Nagar"),
        ("followup.due", "Follow-up due: Sneha Dixit (Hair Restore)"),
        ("campaign.status", "Campaign 'Hot Lead Offer - Week 46' sent 18 messages"),
        ("waitlist.slots_open", "A slot opened for Wellness Spa at Bhawarkuan - waitlist notified"),
        ("lead.created", "New lead Rohit Nair (Dental Care, Sudama Nagar) - score 65"),
    ]
    for t, (etype, text) in enumerate(feed):
        await db.events.insert_one({"_id": dbm.new_id("evt"), "org_id": org_id, "type": "feed",
                                    "payload": {"text": text, "kind": etype},
                                    "at": (now - timedelta(minutes=90 - t * 9)).isoformat()})

    # ---- BR-05 projection: what each store can offer today (same logic as the
    # team page recompute, so the table matches the trained staff on shift)
    from .capacity import load_day_ctx, store_offers
    for sid in store_ids:
        ctx = await load_day_ctx(db, [sid], today_s)
        for svc in services.values():
            existing = await db.store_services.find_one({"org_id": org_id, "store_id": sid,
                                                         "service_id": svc["_id"]})
            offered = store_offers(ctx, sid, svc)
            if existing:
                await db.store_services.update_one({"_id": existing["_id"]},
                                                   {"$set": {"enabled": offered}})
            else:
                await db.store_services.insert_one({"_id": dbm.new_id("ssv"), "org_id": org_id,
                                                    "store_id": sid, "service_id": svc["_id"],
                                                    "enabled": offered})

    print(f"[seed] org={org_id} stores={len(store_ids)} services={len(services)} "
          f"employees=20 leads=48 customers={132 + len(converted_docs)} "
          f"appointments_today={n_today}")
