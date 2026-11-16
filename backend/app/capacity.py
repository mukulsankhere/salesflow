"""Capacity engine (PRD 12.1, 12.2; BR-03 to BR-07).

Availability is computed server-side from employees, skills, shifts, leaves and
existing slot_reservations. place() picks the least-loaded trained employees.
"""
from datetime import datetime, timedelta

from . import config, db as dbm, rules

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SHIFT_HOURS = {"off": (0, 0), "morning": (10, 16), "evening": (16, 22), "full": (10, 22)}


def effective_shift(employee: dict, date_str: str, overrides: dict) -> str:
    if employee.get("_id") in overrides:
        return overrides[employee["_id"]]
    pattern = employee.get("shift_pattern") or {}
    wd = WEEKDAYS[datetime.fromisoformat(date_str).weekday()]
    return pattern.get(wd, "full")


def shift_covers(shift_type: str, start_hour: int, duration: int) -> bool:
    start, end = SHIFT_HOURS.get(shift_type, (0, 0))
    return start <= start_hour and start_hour + duration <= end


async def load_day_ctx(db, store_ids, date_str: str) -> dict:
    emps = await db.employees.find(
        {"org_id": config.ORG_ID, "store_id": {"$in": store_ids}, "status": "active"})
    ids = [e["_id"] for e in emps]
    overrides = {}
    if ids:
        for s in await db.shifts.find({"employee_id": {"$in": ids}, "date": date_str}):
            overrides[s["employee_id"]] = s["type"]
    leaves = await db.leaves.find(
        {"employee_id": {"$in": ids}, "status": "approved",
         "from": {"$lte": date_str}, "to": {"$gte": date_str}})
    on_leave = {l["employee_id"] for l in leaves}
    reservations = await db.slot_reservations.find({"org_id": config.ORG_ID, "date": date_str})
    # keyed by (employee_id, hour) -> 1 reservation hour
    booked_hours = {(r["employee_id"], r["hour"]): 1 for r in reservations}
    return {"date": date_str, "employees": emps, "shift_overrides": overrides,
            "on_leave": on_leave, "booked_hours": booked_hours}


def emp_booked_hours(ctx: dict, emp_id: str) -> int:
    return sum(1 for (e, _h) in ctx["booked_hours"] if e == emp_id)


def free_employees(ctx: dict, store_id: str, service: dict, start_hour: int) -> list[dict]:
    """Trained, shifted, not-on-leave employees with no overlapping reservation (BR-03/04)."""
    du = service["duration_hours"]
    out = []
    for e in ctx["employees"]:
        if e["store_id"] != store_id:
            continue
        if service["_id"] not in (e.get("service_ids") or []):
            continue
        shift = effective_shift(e, ctx["date"], ctx["shift_overrides"])
        if not shift_covers(shift, start_hour, du):
            continue
        if not all((e["_id"], h) not in ctx["booked_hours"] for h in range(start_hour, start_hour + du)):
            continue
        out.append(e)
    # fair load: least booked hours that day first (PRD 12.1)
    out.sort(key=lambda e: (emp_booked_hours(ctx, e["_id"]), e.get("name", "")))
    return out


def trained_count(ctx: dict, store_id: str, service_id: str) -> int:
    return sum(1 for e in ctx["employees"]
               if e["store_id"] == store_id and service_id in (e.get("service_ids") or []))


def place(ctx: dict, store_id: str, service: dict, start_hour: int) -> list[dict] | None:
    """PRD 12.1. Returns the assigned employees, or None."""
    need = service["staff_required"]
    du = service["duration_hours"]
    if start_hour + du > 22:
        return None
    candidates = free_employees(ctx, store_id, service, start_hour)
    if len(candidates) < need:
        return None
    return candidates[:need]


def store_offers(ctx: dict, store_id: str, service: dict) -> bool:
    return trained_count(ctx, store_id, service["_id"]) >= service["staff_required"]  # BR-05


async def availability_grid(db, store_id: str, service: dict, date_str: str) -> dict:
    """BOOK-01/02: hour-by-hour availability for one store/service/day."""
    store = await db.stores.find_one({"org_id": config.ORG_ID, "_id": store_id})
    if not store:
        return {"error": "store_not_found"}
    ctx = await load_day_ctx(db, [store_id], date_str)
    trained = trained_count(ctx, store_id, service["_id"])
    offered = store_offers(ctx, store_id, service)  # BR-05
    hours = []
    for h in config.SLOT_HOURS:
        free = free_employees(ctx, store_id, service, h) if offered else []
        hours.append({
            "hour": h,
            "capacity": trained,
            "free": len(free),
            "employees": [{"id": e["_id"], "name": e["name"]} for e in free[:service["staff_required"]]],
            "status": "passed" if not offered else ("available" if place(ctx, store_id, service, h) else "full"),
        })
    return {"store_id": store_id, "store_name": store["name"], "date": date_str,
            "service_id": service["_id"], "offered": offered, "trained": trained, "hours": hours}


async def store_day_utilization(db, store_id: str, date_str: str) -> dict:
    """TEAM-01: booked employee-hours / (available employees x operating hours)."""
    ctx = await load_day_ctx(db, [store_id], date_str)
    available = sum(1 for e in ctx["employees"]
                    if effective_shift(e, date_str, ctx["shift_overrides"]) != "off"
                    and e["_id"] not in ctx["on_leave"])
    booked_hours = sum(v for (e, _h), v in ctx["booked_hours"].items()
                       if e in {x["_id"] for x in ctx["employees"]})
    denom = available * len(config.SLOT_HOURS)
    return {"store_id": store_id, "date": date_str, "available_employees": available,
            "booked_emp_hours": booked_hours, "total_emp_hours": denom,
            "utilization_pct": round(100 * booked_hours / denom) if denom else 0}


async def distances_from(db, store_id: str) -> dict:
    rows = await db.store_distances.find({"from_store": store_id})
    return {r["to_store"]: r["km"] for r in rows}


async def reroute_options(db, service: dict, date_str: str, start_hour: int,
                          preferred_store_id: str, org: dict) -> dict:
    """PRD 12.2 / BOOK-04: preferred first; reroute when at/above threshold or no placement."""
    threshold = rules.rule_value(org, "BR-06", 90)
    stores = await db.stores.find({"org_id": config.ORG_ID, "status": "active"})
    store_by_id = {s["_id"]: s for s in stores}
    dists = await distances_from(db, preferred_store_id)
    ctx = await load_day_ctx(db, list(store_by_id.keys()), date_str)
    far_km = rules.rule_value(org, "BR-07KM", 5)

    async def option(store_id, assigned, util):
        s = store_by_id[store_id]
        km = dists.get(store_id)
        price = rules.price_breakup(service, km, org)
        return {"store_id": store_id, "store_name": s["name"], "area": s.get("area"),
                "distance_km": km, "far": km is not None and km > far_km,
                "utilization_pct": util,
                "employees": [{"id": e["_id"], "name": e["name"]} for e in assigned],
                "placeable": bool(assigned), "price": price}

    options = []
    rerouted = False
    if preferred_store_id in store_by_id:
        u = (await store_day_utilization(db, preferred_store_id, date_str))["utilization_pct"]
        assigned = place(ctx, preferred_store_id, service, start_hour)
        if assigned and u < threshold:
            return {"rerouted": False, "threshold": threshold,
                    "options": [await option(preferred_store_id, assigned, u)]}
        rerouted = True
        if assigned or u >= threshold:
            options.append(await option(preferred_store_id, assigned or [], u))
    else_rows = []
    for s in stores:
        if s["_id"] == preferred_store_id or not store_offers(ctx, s["_id"], service):
            continue
        u = (await store_day_utilization(db, s["_id"], date_str))["utilization_pct"]
        assigned = place(ctx, s["_id"], service, start_hour)
        if assigned and u < threshold:
            else_rows.append((u, dists.get(s["_id"], 999), await option(s["_id"], assigned, u)))
    else_rows.sort(key=lambda r: (r[0], r[1]))  # lowest utilisation, then nearest
    options.extend(r[2] for r in else_rows[:3])
    return {"rerouted": rerouted, "threshold": threshold, "options": options[:4]}


async def earliest_available(db, service: dict, org: dict, from_date: str | None = None) -> dict | None:
    """BOOK-05: pick earliest available across all stores that offer the service."""
    stores = await db.stores.find({"org_id": config.ORG_ID, "status": "active"})
    start_d = datetime.fromisoformat(from_date or datetime.now().strftime("%Y-%m-%d")).date()
    now = datetime.now()
    for offset in range(0, 15):
        d = (start_d + timedelta(days=offset)).isoformat()
        ctx = await load_day_ctx(db, [s["_id"] for s in stores], d)
        best = None
        for s in stores:
            if not store_offers(ctx, s["_id"], service):
                continue
            for h in config.SLOT_HOURS:
                if offset == 0 and datetime.now() >= datetime.fromisoformat(f"{d}T{h:02d}:00:00"):
                    continue  # hour already past
                if place(ctx, s["_id"], service, h):
                    if best is None or h < best["start_hour"]:
                        best = {"store_id": s["_id"], "store_name": s["name"], "date": d, "start_hour": h}
                    break
        if best:
            return best
    return None
