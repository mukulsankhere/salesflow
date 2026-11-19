"""Lead-management feature tests: priority, queue actions, automation rules,
hygiene merge/archive, analytics report. Run against the live dev server."""
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
    H = {"Authorization": f"Bearer {tok}"}

    # ---- priority on list rows
    s, d = call("GET", "/leads?limit=5", token=tok)
    check("leads list has priority + reasons", s == 200 and all("priority" in l and "priority_reasons" in l for l in d.get("leads", [])), s)

    # ---- analytics report
    s, d = call("GET", "/leads/analytics?period=all", token=tok)
    rep = d.get("report") or {}
    check("analytics report fields", all(k in rep for k in
          ("priority_counts", "overdue_followups", "avg_first_contact_min", "conversion_rate", "leads_in_range")), rep)

    # ---- queue: reschedule + complete round trip
    s, d = call("GET", "/queue/today", token=tok)
    fu_items = [i for i in d.get("items", []) if i["kind"] == "followup"]
    check("queue has next_step on items", all(i.get("next_step") for i in d.get("items", [])[:10]), d.get("total"))
    if fu_items:
        item = fu_items[0]
        fu_at = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        s, d = call("POST", "/queue/action", {"kind": "lead", "ref_id": item["ref_id"],
                                              "action": "reschedule", "follow_up_at": fu_at}, tok)
        check("queue reschedule", s == 200 and d.get("ok"), (s, d))
        s, d = call("POST", "/queue/action", {"kind": "lead", "ref_id": item["ref_id"],
                                              "action": "complete", "note": "Called customer, will visit tomorrow"}, tok)
        check("queue complete", s == 200 and d.get("ok"), (s, d))
        # completed action is visible in the lead's activity history
        s, d = call("GET", f"/leads/{item['ref_id']}", token=tok)
        inters = d.get("interactions", [])
        check("complete logged to lead history", any("Called customer" in (i.get("content") or "") for i in inters), len(inters))
        check("follow_up cleared after complete", not d.get("follow_up_at"), d.get("follow_up_at"))
        s, d = call("POST", "/queue/action", {"kind": "lead", "ref_id": item["ref_id"],
                                              "action": "reschedule", "follow_up_at": "garbage"}, tok)
        check("reschedule rejects bad date", s == 422, (s, err_code(d)))
    else:
        check("queue has followup items", False, "no followup items to test")

    # ---- automation rules: create -> preview -> enable -> run -> log
    s, d = call("GET", "/automation-rules", token=tok)
    check("rules list", s == 200 and len(d.get("rules", [])) >= 2 and d.get("owners"), d.get("rules"))
    owner = d["owners"][0]["_id"]
    s, d = call("POST", "/automation-rules", {"name": "Test Google Assign", "type": "assign_new_leads",
                                              "source": "google_ads", "owner_id": owner}, tok)
    check("rule created paused", s == 201 and d.get("enabled") is False, (s, d))
    rule_id = d.get("_id")
    s, d = call("POST", f"/automation-rules/{rule_id}/preview", token=tok)
    check("rule preview has matches+effect", s == 200 and "matches" in d and "effect" in d, (s, d))
    s, d = call("POST", f"/automation-rules/{rule_id}/run", token=tok)
    check("paused rule cannot run", s == 422, (s, err_code(d)))
    s, d = call("PUT", f"/automation-rules/{rule_id}", {"enabled": True}, tok)
    check("rule enabled", s == 200, (s, d))
    # enabled assign rule applies on creation
    s, d = call("GET", "/stores", token=tok)
    store_id = d["stores"][0]["_id"]
    s, d = call("GET", "/services", token=tok)
    svc_id = d["services"][0]["_id"]
    s, d = call("POST", "/leads", {"name": "Rule Assignee", "phone": "9955570001", "store_id": store_id,
                                   "service_id": svc_id, "source": "google_ads"}, tok)
    check("lead created", s == 201, (s, d))
    check("assign rule auto-applied owner", d.get("owner_id") == owner, d.get("owner_id"))
    s, d = call("GET", "/automation-log", token=tok)
    check("automation log has entries", any(e.get("rule_id") == rule_id for e in d.get("log", [])), len(d.get("log", [])))
    s, d = call("PUT", f"/automation-rules/{rule_id}", {"enabled": False}, tok)
    s, d = call("DELETE", f"/automation-rules/{rule_id}", token=tok)
    check("rule deleted", s == 200, (s, d))
    # invalid payloads rejected
    s, d = call("POST", "/automation-rules", {"name": "Bad", "type": "assign_new_leads",
                                              "source": "not_a_source", "owner_id": owner}, tok)
    check("invalid source rejected", s == 422, (s, err_code(d)))
    s, d = call("POST", "/automation-rules", {"name": "Bad days", "type": "no_contact_reminder", "days": 99}, tok)
    check("days out of range rejected", s == 422, (s, err_code(d)))

    # reminder rule: enable + run sets follow-ups
    s, d = call("POST", "/automation-rules", {"name": "Test Reminder", "type": "no_contact_reminder", "days": 10}, tok)
    rid = d.get("_id")
    s, d = call("PUT", f"/automation-rules/{rid}", {"enabled": True}, tok)
    s, d = call("POST", f"/automation-rules/{rid}/preview", token=tok)
    n_matches = d.get("matches", 0)
    s, d = call("POST", f"/automation-rules/{rid}/run", token=tok)
    check("reminder rule ran", s == 200 and d.get("affected") == n_matches, (s, d.get("affected"), n_matches))
    s, d = call("DELETE", f"/automation-rules/{rid}", token=tok)

    # ---- hygiene: duplicates visible, merge preserves history, archive/unarchive
    s, d = call("GET", "/leads/hygiene", token=tok)
    dups = d.get("duplicates", [])
    check("hygiene finds duplicates", s == 200 and len(dups) >= 1, d.get("counts"))
    if dups:
        pair = dups[0]["leads"][:2]
        primary, dup = pair[0], pair[1]
        # log an interaction on the duplicate to prove history moves on merge
        s, d = call("POST", f"/leads/{dup['_id']}/interactions",
                    {"channel": "call", "direction": "out", "outcome": "call_made",
                     "content": "Pre-merge call on duplicate"}, tok)
        check("interaction logged on duplicate", s == 200 or s in (200, 201), (s, d))
        s, d = call("POST", "/leads/merge", {"primary_id": primary["_id"], "duplicate_id": dup["_id"]}, tok)
        check("merge ok", s == 200 and d.get("ok"), (s, d))
        s, d = call("GET", f"/leads/{primary['_id']}", token=tok)
        check("history moved to primary", any("Pre-merge call" in (i.get("content") or "") for i in d.get("interactions", [])), d.get("interactions"))
        s, d = call("GET", f"/leads/{dup['_id']}", token=tok)
        check("duplicate archived with pointer", d.get("archived") is True and d.get("merged_into") == primary["_id"], d.get("archived"))
        s, d = call("POST", "/leads/merge", {"primary_id": primary["_id"], "duplicate_id": dup["_id"]}, tok)
        check("re-merge rejected", s == 422, (s, err_code(d)))
        s, d = call("POST", "/leads/merge", {"primary_id": primary["_id"], "duplicate_id": primary["_id"]}, tok)
        check("self-merge rejected", s == 422, (s, err_code(d)))

    # incomplete + stale present
    s, d = call("GET", "/leads/hygiene", token=tok)
    check("hygiene incomplete+stale present", (d.get("counts") or {}).get("incomplete", 0) >= 1
          and (d.get("counts") or {}).get("stale", 0) >= 1, d.get("counts"))

    # archive/unarchive an open lead
    s, d = call("GET", "/leads?limit=5&stage=new", token=tok)
    victim = d["leads"][0]
    s, d = call("POST", f"/leads/{victim['_id']}/archive", token=tok)
    check("archive ok", s == 200, (s, d))
    s, d = call("GET", "/leads?limit=200&stage=new", token=tok)
    check("archived lead hidden from list", all(l["_id"] != victim["_id"] for l in d.get("leads", [])))
    s, d = call("POST", f"/leads/{victim['_id']}/unarchive", token=tok)
    check("unarchive ok", s == 200, (s, d))
    s, d = call("GET", "/leads?limit=200&stage=new", token=tok)
    check("unarchived lead visible again", any(l["_id"] == victim["_id"] for l in d.get("leads", [])))

    # audit trail captured the destructive actions
    s, d = call("GET", "/audit?limit=100", token=tok)
    actions = {a["action"] for a in d.get("audit", [])}
    check("audit has lead.merge/archive", "lead.merge" in actions and "lead.archive" in actions, actions)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
