"""Dual-mode data layer.

- When MONGO_URI is set: MongoDB via Motor (production path, PRD section 10).
- Otherwise: an in-process document store implementing the same subset of the Mongo
  query/update syntax, including unique compound indexes (the double-booking guard,
  PRD section 9.3) and a global transaction lock.

API code writes plain Mongo-style filters, so both backends run the same service code.
"""
import asyncio
import itertools
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config


class DuplicateKeyError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------- query matcher
_OPS = ("$in", "$nin", "$gte", "$lte", "$gt", "$lt", "$ne", "$regex", "$exists")


def _get_path(doc, path):
    val = doc
    for part in path.split("."):
        if isinstance(val, dict) and part in val:
            val = val[part]
        else:
            return None
    return val


def _match(cond, doc):
    if cond is None:
        return True
    for k, v in cond.items():
        if k == "$or":
            if not any(_match(c, doc) for c in v):
                return False
        elif k == "$and":
            if not all(_match(c, doc) for c in v):
                return False
        elif k == "$nor":
            if any(_match(c, doc) for c in v):
                return False
        else:
            val = _get_path(doc, k)
            if isinstance(v, dict) and any(op in v for op in _OPS):
                if "$exists" in v:
                    if v["$exists"] != (val is not None):
                        return False
                if "$in" in v and val not in v["$in"]:
                    return False
                if "$nin" in v and val in v["$nin"]:
                    return False
                if "$ne" in v and val == v["$ne"]:
                    return False
                for op, bound in (("$gte", v.get("$gte")), ("$lte", v.get("$lte")),
                                  ("$gt", v.get("$gt")), ("$lt", v.get("$lt"))):
                    if op in v and (val is None or not _cmp_ok(val, bound, op)):
                        return False
                if "$regex" in v:
                    flags = re.I if "i" in v.get("$options", "") else 0
                    if not isinstance(val, str) or not re.search(v["$regex"], val, flags):
                        return False
            elif isinstance(v, dict) and "$not" in v:
                if _match(v["$not"], {k: val} if not isinstance(val, dict) else doc):
                    return False
            elif isinstance(val, list) and not isinstance(v, (dict, list)):
                # Mongo semantics: a scalar query matches when the array field
                # contains it (e.g. {"roles": "sales"} on users)
                if v not in val:
                    return False
            else:
                if val != v:
                    return False
    return True


def _cmp_ok(a, b, op):
    try:
        return {"$gte": a >= b, "$lte": a <= b, "$gt": a > b, "$lt": a < b}[op]
    except TypeError:
        return False


def _sort_key(sort_spec):
    def key_fn(doc):
        parts = []
        for f, direction in sort_spec:
            v = _get_path(doc, f)
            if isinstance(v, bool):
                v = int(v)  # bool is an int subclass; keep sort keys numeric-only
            if isinstance(v, str):
                parts.append((0, v if direction >= 0 else _invert_str(v)))
            elif v is None:
                parts.append((1, "") if direction >= 0 else (-1, "\uffff"))
            elif isinstance(v, (int, float)):
                # numbers: apply the direction to the value itself (zero-padding alone
                # would make every DESC sort come out ascending)
                parts.append((0, -float(v) if direction < 0 else float(v)))
            else:
                parts.append((0, str(v) if direction >= 0 else _invert_str(str(v))))
        return tuple(parts)
    return key_fn


def _invert_str(s):
    return "".join(chr(0x10FFFF - ord(c)) for c in s)


# ---------------------------------------------------------------- in-memory store
class MemCollection:
    def __init__(self, name, indexes):
        self.name = name
        self.docs = []
        self.indexes = indexes  # list of tuples of field names (unique)
        self._ids = itertools.count(1)

    def _check_unique(self, doc, exclude_id=None):
        for fields in self.indexes:
            key = tuple(_get_path(doc, f) for f in fields)
            if any(k is None for k in key):
                continue
            for d in self.docs:
                if exclude_id is not None and d.get("_id") == exclude_id:
                    continue
                if tuple(_get_path(d, f) for f in fields) == key:
                    raise DuplicateKeyError(
                        f"{self.name} duplicate key {fields}: {key}")

    def _apply_update(self, doc, update):
        if "$set" in update:
            for k, v in update["$set"].items():
                _set_path(doc, k, v)
        if "$inc" in update:
            for k, v in update["$inc"].items():
                cur = _get_path(doc, k) or 0
                _set_path(doc, k, cur + v)
        if "$push" in update:
            for k, v in update["$push"].items():
                lst = _get_path(doc, k)
                if lst is None:
                    lst = []
                    _set_path(doc, k, lst)
                if isinstance(v, dict) and "$each" in v:
                    lst.extend(v["$each"])
                else:
                    lst.append(v)
        if "$pull" in update:
            for k, v in update["$pull"].items():
                lst = _get_path(doc, k) or []
                _set_path(doc, k, [x for x in lst if x != v])
        if "$addToSet" in update:
            for k, v in update["$addToSet"].items():
                lst = _get_path(doc, k) or []
                _set_path(doc, k, lst)
                if v not in lst:
                    lst.append(v)
        if "$unset" in update:
            for k in update["$unset"]:
                _unset_path(doc, k)
        for k, v in update.items():
            if not k.startswith("$"):
                _set_path(doc, k, v)

    async def insert_one(self, doc):
        doc = dict(doc)
        doc.setdefault("_id", new_id("id"))
        doc.setdefault("created_at", now_iso())
        doc.setdefault("updated_at", doc["created_at"])
        self._check_unique(doc)
        self.docs.append(dict(doc))
        return str(doc["_id"])

    async def insert_many(self, docs):
        for d in docs:
            await self.insert_one(d)

    async def find_one(self, query):
        for d in self.docs:
            if _match(query, d):
                return dict(d)
        return None

    async def find(self, query=None, sort=None, limit=0, skip=0):
        out = [dict(d) for d in self.docs if _match(query, d)]
        if sort:
            try:
                out.sort(key=_sort_key(sort))
            except Exception:
                out.sort(key=lambda d: str(d.get("_id")))
        if skip:
            out = out[skip:]
        if limit:
            out = out[:limit]
        return out

    async def update_one(self, query, update, upsert: bool = False):
        for d in self.docs:
            if _match(query, d):
                changed = dict(d)
                self._apply_update(changed, update)
                changed["updated_at"] = now_iso()
                self._check_unique(changed, exclude_id=changed["_id"])
                d.clear()
                d.update(changed)
                return 1
        if upsert:
            doc = {k: v for k, v in query.items() if not isinstance(v, dict)}
            self._apply_update(doc, update)
            doc.setdefault("_id", new_id("id"))
            doc.setdefault("created_at", now_iso())
            doc.setdefault("updated_at", doc["created_at"])
            self._check_unique(doc)
            self.docs.append(dict(doc))
            return 0
        return 0

    async def update_many(self, query, update):
        n = 0
        for d in [x for x in self.docs if _match(query, x)]:
            self._apply_update(d, update)
            d["updated_at"] = now_iso()
            n += 1
        return n

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _match(query, d):
                del self.docs[i]
                return 1
        return 0

    async def delete_many(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(query, d)]
        return before - len(self.docs)

    async def count_documents(self, query=None):
        return sum(1 for d in self.docs if _match(query, d))

    async def distinct(self, field, query=None):
        seen, out = set(), []
        for d in self.docs:
            if _match(query, d):
                v = _get_path(d, field)
                if v is not None and v not in seen:
                    seen.add(v)
                    out.append(v)
        return out


def _set_path(doc, path, value):
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        if not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def _unset_path(doc, path):
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        if not isinstance(cur.get(p), dict):
            return
        cur = cur[p]
    cur.pop(parts[-1], None)


class _AsyncTx:
    """Async context manager wrapping a sync lock (transaction boundary)."""

    def __init__(self, lock):
        self._lock = lock

    async def __aenter__(self):
        await self._lock.acquire()

    async def __aexit__(self, *exc):
        self._lock.release()


class MemAdapter:
    """In-process adapter with a global lock acting as the transaction boundary."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._cols = {}

    def col(self, name):
        if name not in self._cols:
            self._cols[name] = MemCollection(name, UNIQUE_INDEXES.get(name, []))
        return self._cols[name]

    def tx(self):
        return _AsyncTx(self._lock)


# ---------------------------------------------------------------- mongo adapter
class MongoCollection:
    """Thin wrapper so the API code (find returns a list, etc.) runs unchanged on Motor."""

    def __init__(self, col):
        self._c = col

    async def find(self, query=None, sort=None, limit=0, skip=0):
        cur = self._c.find(query or {}, skip=skip)
        if limit:
            cur = cur.limit(limit)
        if sort:
            cur = cur.sort(sort)
        return await cur.to_list(length=limit or None)

    async def find_one(self, query):
        return await self._c.find_one(query)

    async def insert_one(self, doc):
        doc = dict(doc)
        res = await self._c.insert_one(doc)
        return str(res.inserted_id)

    async def insert_many(self, docs):
        await self._c.insert_many(list(docs))

    async def update_one(self, query, update, upsert: bool = False):
        return await self._c.update_one(query, update, upsert=upsert)

    async def update_many(self, query, update):
        return await self._c.update_many(query, update)

    async def delete_one(self, query):
        return await self._c.delete_one(query)

    async def delete_many(self, query):
        return await self._c.delete_many(query)

    async def count_documents(self, query=None):
        return await self._c.count_documents(query or {})

    async def distinct(self, field, query=None):
        return await self._c.distinct(field, query or {})


class MongoAdapter:
    def __init__(self, client, db):
        self._client = client
        self._db = db
        self._cols = {}

    def col(self, name):
        if name not in self._cols:
            self._cols[name] = MongoCollection(self._db[name])
        return self._cols[name]

    @contextmanager
    def tx(self):
        class _Null:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *a):
                return False
        return _Null()

    async def ensure_indexes(self):
        from motor.motor_asyncio import AsyncIOMotorClient
        for name, fields in UNIQUE_INDEXES.items():
            for spec in fields:
                await self._db[name].create_index(
                    [(f, 1) for f in spec], unique=True, name="uq_" + "_".join(spec))
        secondary = {
            "leads": [("org_id", "stage", "score"), ("org_id", "store_id", "created_at"),
                      ("owner_id", "follow_up_at")],
            "appointments": [("org_id", "store_id", "date", "status"), ("customer_id", "date")],
            "daily_rollups": [("org_id", "date", "store_id", "service_id", "source")],
            "events": [("org_id", "at")],
            "audit_log": [("org_id", "at"), ("entity", "entity_id")],
        }
        for name, idxs in secondary.items():
            for spec in idxs:
                await self._db[name].create_index([(f, 1) for f in spec])


UNIQUE_INDEXES = {
    "users": [("org_id", "email")],
    "leads": [("org_id", "phone_norm")],
    "customers": [("org_id", "phone_norm")],
    "identities": [("org_id", "phone_norm")],
    "slot_reservations": [("org_id", "store_id", "date", "hour", "employee_id")],
    "stores": [("org_id", "name")],
    "services": [("org_id", "name")],
    "store_services": [("store_id", "service_id")],
    "store_distances": [("from_store", "to_store")],
    "shifts": [("employee_id", "date")],
    "campaign_messages": [("campaign_id", "person_id")],
    "templates": [("org_id", "name")],
    "insights": [("org_id", "scope", "key")],
    "daily_rollups": [("org_id", "date", "store_id", "service_id", "source")],
}


class Database:
    def __init__(self, adapter):
        self._a = adapter

    def __getattr__(self, name):
        return self._a.col(name)

    @property
    def is_mongo(self):
        return isinstance(self._a, MongoAdapter)

    def tx(self):
        return self._a.tx()


_db = None


async def get_db() -> Database:
    global _db
    if _db is None:
        if config.MONGO_URI:
            from motor.motor_asyncio import AsyncIOMotorClient
            client = AsyncIOMotorClient(config.MONGO_URI)
            _db = Database(MongoAdapter(client, client[config.MONGO_DB]))
            await _db._a.ensure_indexes()
        else:
            _db = Database(MemAdapter())
    return _db
