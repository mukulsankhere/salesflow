"""API tests: single-admin access model + user directory (ADM-02).

This deployment has exactly one sign-in (the Admin). Employees exist as staff
records (assignment, roster, routing) but can never sign in. These tests pin that
behaviour: admin logins work, every employee login is rejected (401 wrong-password
for seeded staff who have no password, 403 for accounts that have one), created
users become records without sign-in, and validation/audit paths stay intact.
"""
import json
import random
import urllib.request
import urllib.error

_R = random.randint(1000, 9999)  # per-run suffix - the suite stays re-runnable on a live server
BASE = "http://localhost:8000/api/v1"
PASS = 0
FAIL = 0


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


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def login(who, pw):
    s, d = call("POST", "/auth/login", {"email_or_phone": who, "password": pw})
    return s, (d.get("access_token") if s == 200 else d)


# --- single-admin sign-in ------------------------------------------------
s, admin = login("admin@salesflow.app", "Demo@123")
check("admin login", s == 200 and admin, s)

for who in ("sales@salesflow.app", "manager@salesflow.app", "marketing@salesflow.app",
            "staff@salesflow.app", "viewer@salesflow.app"):
    s, d = login(who, "Demo@123")
    code = (d or {}).get("detail", {}).get("error", {}).get("code") if s != 200 else None
    check(f"employee login blocked ({who.split('@')[0]})",
          s in (401, 403) and code in ("EMPLOYEE_LOGIN_DISABLED", "INVALID_CREDENTIALS", "ACCOUNT_DISABLED"),
          (s, code))

s, d = login("admin@salesflow.app", "wrongpw")
check("wrong admin password 401", s == 401, s)

# --- staff records (no sign-in) ------------------------------------------
EMAIL = f"priya.sharma.{_R}@salesflow.app"
PHONE = f"9{random.randint(100000000, 999999999)}"  # 10-digit mobile

s, d = call("POST", "/auth/users", {
    "name": "Priya Sharma", "email": EMAIL, "phone": PHONE,
    "password": "Start@1234", "roles": ["sales"], "store_ids": ["st_vijay"]}, admin)
uid = d.get("_id", "")
check("create staff record 201", s == 201 and uid.startswith("usr_"), (s, d))
check("create returns active", d.get("status") == "active")

s, d = call("POST", "/auth/users", {"name": "Dup", "email": EMAIL,
            "password": "Start@1234", "roles": ["sales"]}, admin)
check("duplicate email 409", s == 409 and d["detail"]["error"]["code"] == "EMAIL_EXISTS", (s, d))

s, d = call("POST", "/auth/users", {"name": "Dup", "email": "other@x.app", "phone": PHONE,
            "password": "Start@1234", "roles": ["sales"]}, admin)
check("duplicate phone 409", s == 409 and d["detail"]["error"]["code"] == "PHONE_EXISTS", (s, d))

s, d = call("POST", "/auth/users", {"name": "Short", "email": "short@x.app",
            "password": "abc", "roles": ["sales"]}, admin)
check("short password 422", s == 422, (s, d))

s, d = call("POST", "/auth/users", {"name": "BadRole", "email": "badrole@x.app",
            "password": "Start@1234", "roles": ["superuser"]}, admin)
check("invalid role only 422", s == 422, (s, d))

s, d = call("POST", "/auth/users", {"name": "Ghost", "email": "ghost@x.app",
            "password": "Start@1234", "roles": ["sales"], "store_ids": ["st_nope"]}, admin)
check("unknown store 422", s == 422, (s, d))

s, d = login(EMAIL, "Start@1234")
code = (d or {}).get("detail", {}).get("error", {}).get("code", "")
check("created non-admin cannot sign in", s in (401, 403) and code != "", (s, code))
s, d = login(PHONE, "Start@1234")
check("created non-admin cannot sign in (phone)", s in (401, 403), (s, d))

# --- status toggle (records stay manageable) ------------------------------
s, d = call("POST", f"/auth/users/{uid}/status", {"status": "disabled"}, admin)
check("deactivate 200", s == 200 and d.get("status") == "disabled" and d.get("changed"), (s, d))

s, d = login(EMAIL, "Start@1234")
check("deactivated login blocked 403", s == 403, (s, d))

s, tok = login("admin@salesflow.app", "Demo@123")
check("admin still fine", s == 200, s)

s, d = call("GET", "/auth/users", token=admin)
row = next((u for u in d.get("users", []) if u["_id"] == uid), None)
check("list shows disabled", row and row["status"] == "disabled", row)

s, d = call("POST", f"/auth/users/{uid}/status", {"status": "active"}, admin)
check("reactivate 200", s == 200 and d.get("status") == "active", (s, d))
s, d = login(EMAIL, "Start@1234")
check("non-admin still blocked after reactivate", s in (401, 403), (s, d))

s, d = call("POST", f"/auth/users/{uid}/status", {"status": "weird"}, admin)
check("bad status 422", s == 422, (s, d))

s, d = call("POST", "/auth/users/usr_nope/status", {"status": "disabled"}, admin)
check("unknown user 404", s == 404, (s, d))

# --- self-protection + password reset --------------------------------------
s, me = call("GET", "/auth/me", token=admin)
my_id = me["user"]["_id"]
s, d = call("POST", f"/auth/users/{my_id}/status", {"status": "disabled"}, admin)
check("self-deactivate 422", s == 422, (s, d))

s, d = call("POST", f"/auth/users/{uid}/reset-password", {"password": "abc"}, admin)
check("short reset 422", s == 422, (s, d))

s, d = call("POST", f"/auth/users/{uid}/reset-password", {"password": "NewPass@99"}, admin)
check("reset 200", s == 200 and d.get("ok"), (s, d))

s, d = login(EMAIL, "NewPass@99")
check("reset non-admin still blocked", s in (401, 403), (s, d))

# --- audit trail -----------------------------------------------------------
s, d = call("GET", "/audit?limit=100", token=admin)
actions = [a["action"] for a in d.get("audit", [])]
check("audit user.create", "user.create" in actions, actions[:10])
check("audit user.status", "user.status" in actions, actions[:10])
check("audit user.password_reset", "user.password_reset" in actions, actions[:10])

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
