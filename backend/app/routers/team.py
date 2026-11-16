"""Store & team management (TEAM-01..TEAM-05): utilization, workload, roster, CRUD, leave."""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import capacity, config, db as dbm, rules
from ..audit import audit
from ..deps import current_user, err, require_section
from ..db import Database, get_db

router = APIRouter(prefix="/api/v1")


class EmployeeIn(BaseModel):
    name: str
    role: str = "therapist"
    store_id: str
    service_ids: list = []
    shift_pattern: dict | None = None  # {"mon": "morning", ...}


class MoveIn(BaseModel):
    store_id: str


class LeaveIn(BaseModel):
    from_date: str
    to_date: str


class ShiftIn(BaseModel):
    date: str
    type: str  # off | morning | evening | full


async def _booked_future(db, employee_id: str) -> int:
    res = await db.slot_reservations.find({"employee_id": employee_id,
                                           "date": {"$gte": datetime.now().strftime("%Y-%m-%d")}})
    return len(res)


@router.get("/employees")
async def list_employees(store_id: str | None = None, flt: dict = Depends(require_section("team")),
                         db: Database = Depends(get_db)):
    q = {"org_id": config.ORG_ID, "status": "active"}
    if flt["store_ids"] and not store_id:
        q["store_id"] = {"$in": flt["store_ids"]}
    if store_id:
        q["store_id"] = store_id
    emps = await db.employees.find(q, sort=[("name", 1)])
    today = datetime.now().strftime("%Y-%m-%d")
    ctx = await capacity.load_day_ctx(db, list({e["store_id"] for e in emps}) or ["-"], today)
    stores = {s["_id"]: s["name"] for s in await db.stores.find({"org_id": config.ORG_ID})}
    services = {s["_id"]: s["name"] for s in await db.services.find({"org_id": config.ORG_ID})}
    appts = await db.appointments.find({"org_id": config.ORG_ID, "date": today,
                                        "status": {"$in": ["booked", "completed"]}})
    out = []
    for e in emps:
        mine = [a for a in appts if e["_id"] in (a.get("employee_ids") or [])]
        hours = sum(a["duration"] for a in mine)
        out.append({**e, "store_name": stores.get(e["store_id"]),
                    "service_names": [services.get(s) for s in (e.get("service_ids") or [])],
                    "booked_today": len(mine), "hours_today": hours,
                    "utilization": round(100 * hours / 12),
                    "shift_today": capacity.effective_shift(e, today, ctx["shift_overrides"])})
    return {"employees": out}


@router.post("/employees", status_code=201)
async def add_employee(body: EmployeeIn, db: Database = Depends(get_db),
                       flt: dict = Depends(require_section("team")), user: dict = Depends(current_user)):
    doc = {"_id": dbm.new_id("emp"), "org_id": config.ORG_ID, "name": body.name.strip(),
           "role": body.role, "store_id": body.store_id, "service_ids": body.service_ids,
           "shift_pattern": body.shift_pattern or {"mon": "full", "tue": "full", "wed": "full",
                                                   "thu": "full", "fri": "full", "sat": "full",
                                                   "sun": "morning"},
           "status": "active", "user_id": None, "hire_date": dbm.now_iso()[:10]}
    await db.employees.insert_one(doc)
    # BR-05: store service list recomputes from trained staff
    await _recompute_store_services(db, body.store_id)
    await audit(db, user, "team.employee_add", "employee", None,
                {"name": doc["name"], "store_id": doc["store_id"]}, entity_id=doc["_id"])
    return doc


@router.post("/employees/{emp_id}/move")
async def move_employee(emp_id: str, body: MoveIn, db: Database = Depends(get_db),
                        flt: dict = Depends(require_section("team")), user: dict = Depends(current_user)):
    emp = await db.employees.find_one({"_id": emp_id})
    if not emp:
        raise err("NOT_FOUND", "Employee not found", 404)
    if await _booked_future(db, emp_id):  # BR-23
        raise err("EMPLOYEE_BOOKED", "Employee has booked appointments - reassign them first", 409)
    before = emp["store_id"]
    await db.employees.update_one({"_id": emp_id}, {"$set": {"store_id": body.store_id}})
    await _recompute_store_services(db, before)
    await _recompute_store_services(db, body.store_id)
    await audit(db, user, "team.employee_move", "employee", {"store_id": before},
                {"store_id": body.store_id}, entity_id=emp_id)
    return {"ok": True}


@router.delete("/employees/{emp_id}")
async def remove_employee(emp_id: str, db: Database = Depends(get_db),
                          flt: dict = Depends(require_section("team")), user: dict = Depends(current_user)):
    emp = await db.employees.find_one({"_id": emp_id})
    if not emp:
        raise err("NOT_FOUND", "Employee not found", 404)
    if await _booked_future(db, emp_id):  # BR-23
        raise err("EMPLOYEE_BOOKED", "Employee has booked appointments - reassign them first", 409)
    await db.employees.update_one({"_id": emp_id}, {"$set": {"status": "removed"}})
    await _recompute_store_services(db, emp["store_id"])
    await audit(db, user, "team.employee_remove", "employee", {"name": emp["name"]}, None,
                entity_id=emp_id)
    return {"ok": True}


@router.post("/employees/{emp_id}/leave")
async def add_leave(emp_id: str, body: LeaveIn, db: Database = Depends(get_db),
                    flt: dict = Depends(require_section("team")), user: dict = Depends(current_user)):
    emp = await db.employees.find_one({"_id": emp_id})
    if not emp:
        raise err("NOT_FOUND", "Employee not found", 404)
    if await _booked_future(db, emp_id):
        raise err("EMPLOYEE_BOOKED", "Employee has booked appointments in the period - reassign first", 409)
    doc = {"_id": dbm.new_id("lv"), "org_id": config.ORG_ID, "employee_id": emp_id,
           "from": body.from_date, "to": body.to_date, "status": "approved",
           "approved_by": user["name"], "created_at": dbm.now_iso()}
    await db.leaves.insert_one(doc)
    await audit(db, user, "team.leave", "employee", None,
                {"employee": emp["name"], "from": body.from_date, "to": body.to_date},
                entity_id=emp_id)
    return doc


@router.get("/employees/{emp_id}/leave-preview")
async def leave_preview(emp_id: str, from_date: str, to_date: str,
                        db: Database = Depends(get_db), flt: dict = Depends(require_section("team")), user: dict = Depends(current_user)):
    """TEAM-05: capacity impact preview before approving leave."""
    emp = await db.employees.find_one({"_id": emp_id})
    if not emp:
        raise err("NOT_FOUND", "Employee not found", 404)
    impact = []
    d = datetime.fromisoformat(from_date).date()
    end = datetime.fromisoformat(to_date).date()
    while d <= end and len(impact) < 14:
        ds = d.isoformat()
        ctx = await capacity.load_day_ctx(db, [emp["store_id"]], ds)
        before = sum(1 for e in ctx["employees"]
                     if capacity.effective_shift(e, ds, ctx["shift_overrides"]) != "off")
        ctx2 = dict(ctx)
        ctx2["on_leave"] = ctx["on_leave"] | {emp_id}
        after = sum(1 for e in ctx2["employees"]
                    if capacity.effective_shift(e, ds, ctx2["shift_overrides"]) != "off"
                    and e["_id"] not in ctx2["on_leave"])
        impact.append({"date": ds, "employees_before": before, "employees_after": after})
        d = d.fromordinal(d.toordinal() + 1)
    return {"impact": impact}


@router.put("/employees/{emp_id}/shift")
async def set_shift(emp_id: str, body: ShiftIn, db: Database = Depends(get_db),
                    flt: dict = Depends(require_section("team")), user: dict = Depends(current_user)):
    if body.type not in ("off", "morning", "evening", "full"):
        raise err("VALIDATION_ERROR", "Shift type must be off, morning, evening or full")
    emp = await db.employees.find_one({"_id": emp_id})
    if not emp:
        raise err("NOT_FOUND", "Employee not found", 404)
    if body.type == "off" and await _booked_future(db, emp_id):
        raise err("EMPLOYEE_BOOKED", "Employee has booked appointments - reassign them first", 409)
    await db.shifts.update_one({"employee_id": emp_id, "date": body.date},
                               {"$set": {"type": body.type, "org_id": config.ORG_ID}}, upsert=True)
    await audit(db, user, "team.shift", "employee", None,
                {"employee": emp["name"], "date": body.date, "type": body.type}, entity_id=emp_id)
    return {"ok": True}


@router.get("/team/roster")
async def roster(date: str | None = None, store_id: str | None = None,
                 flt: dict = Depends(require_section("team")), db: Database = Depends(get_db)):
    date = date or datetime.now().strftime("%Y-%m-%d")
    q = {"org_id": config.ORG_ID, "status": "active"}
    if flt["store_ids"] and not store_id:
        q["store_id"] = {"$in": flt["store_ids"]}
    if store_id:
        q["store_id"] = store_id
    emps = await db.employees.find(q, sort=[("name", 1)])
    ctx = await capacity.load_day_ctx(db, list({e["store_id"] for e in emps}) or ["-"], date)
    stores = {s["_id"]: s["name"] for s in await db.stores.find({"org_id": config.ORG_ID})}
    rows = [{"employee_id": e["_id"], "name": e["name"], "role": e.get("role"),
             "store_id": e["store_id"], "store_name": stores.get(e["store_id"]),
             "pattern": e.get("shift_pattern") or {},
             "effective": capacity.effective_shift(e, date, ctx["shift_overrides"]),
             "on_leave": e["_id"] in ctx["on_leave"]}
            for e in emps]
    return {"date": date, "roster": rows,
            "options": ["off", "morning", "evening", "full"],
            "labels": {"off": "Off", "morning": "Morning (10-4)", "evening": "Evening (4-10)",
                       "full": "Full Day"}}


@router.get("/team/utilization")
async def team_utilization(flt: dict = Depends(require_section("team")), db: Database = Depends(get_db)):
    """TEAM-01: utilization vs target for the roster date + AI utilization insights."""
    org = await rules.org_doc(db)
    target = rules.rule_value(org, "UTIL_TARGET", 85)
    today = datetime.now().strftime("%Y-%m-%d")
    stores = await db.stores.find({"org_id": config.ORG_ID, "status": "active"})
    if flt["store_ids"]:
        stores = [s for s in stores if s["_id"] in flt["store_ids"]]
    out = []
    for s in stores:
        u = await capacity.store_day_utilization(db, s["_id"], today)
        emps = await db.employees.count_documents({"store_id": s["_id"], "status": "active"})
        appts = await db.appointments.count_documents(
            {"store_id": s["_id"], "date": today, "status": {"$in": ["booked", "completed"]}})
        revenue = sum(p.get("amount", 0) for p in
                      await db.payments.find({"store_id": s["_id"], "status": "paid"}))
        out.append({**u, "name": s["name"], "area": s.get("area"), "employees": emps,
                    "target": target, "appointments_today": appts, "revenue": revenue})
    ins = await db.insights.find({"org_id": config.ORG_ID, "tab": "store",
                                  "status": {"$nin": ["applied", "dismissed"]}})
    return {"stores": out, "target": target, "ai_insights": ins}


async def _recompute_store_services(db, store_id: str):
    """BR-05: a store offers a service only if trained employees >= staff_required."""
    today = datetime.now().strftime("%Y-%m-%d")
    ctx = await capacity.load_day_ctx(db, [store_id], today)
    services = await db.services.find({"org_id": config.ORG_ID})
    for svc in services:
        offered = capacity.store_offers(ctx, store_id, svc)
        existing = await db.store_services.find_one({"store_id": store_id, "service_id": svc["_id"]})
        if existing:
            await db.store_services.update_one({"_id": existing["_id"]}, {"$set": {"enabled": offered}})
        else:
            await db.store_services.insert_one({"store_id": store_id, "service_id": svc["_id"],
                                                "enabled": offered})
