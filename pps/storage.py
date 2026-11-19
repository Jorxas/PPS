import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from .errors import PPSError


def identity(prefix):
    return prefix + "_" + uuid.uuid4().hex


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        with self.transaction() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, value TEXT, created REAL, PRIMARY KEY(kind,id))")
            db.execute("CREATE TABLE IF NOT EXISTS idempotency (scope TEXT, key TEXT, digest TEXT, value TEXT, PRIMARY KEY(scope,key))")

    @contextmanager
    def transaction(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
            try:
                with db:
                    yield db
            finally:
                db.close()

    def put(self, kind, value, db=None):
        if db is None:
            with self.transaction() as conn:
                return self.put(kind, value, conn)
        db.execute("INSERT INTO records VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value",
                   (kind, value["id"], json.dumps(value, ensure_ascii=False), time.time()))
        return value

    def get(self, kind, identifier, db=None):
        if db is None:
            with self.transaction() as conn:
                return self.get(kind, identifier, conn)
        row = db.execute("SELECT value FROM records WHERE kind=? AND id=?", (kind, identifier)).fetchone()
        if not row:
            raise PPSError("NOT_FOUND", "Élément introuvable.", 404, {"kind": kind, "id": identifier})
        return json.loads(row[0])

    def list(self, kind, db=None):
        if db is None:
            with self.transaction() as conn:
                return self.list(kind, conn)
        return [json.loads(x[0]) for x in db.execute("SELECT value FROM records WHERE kind=? ORDER BY created", (kind,))]

    def list_status(self, kind, statuses, db=None, exclude=False):
        """Records whose status is (or, with exclude, is not) in statuses, in creation order.

        Filters inside SQLite instead of parsing every record (~0.6 s for 900 jobs) in Python.
        """
        if db is None:
            with self.transaction() as conn:
                return self.list_status(kind, statuses, conn, exclude)
        statuses = list(statuses)
        marks = ",".join("?" * len(statuses))
        operator = "NOT IN" if exclude else "IN"
        try:
            rows = db.execute(f"SELECT value FROM records WHERE kind=? AND json_extract(value,'$.status') {operator} ({marks}) "
                              "ORDER BY created", (kind, *statuses)).fetchall()
        except sqlite3.OperationalError:
            values = self.list(kind, db)
            return [v for v in values if (v.get("status") in statuses) != exclude]
        return [json.loads(x[0]) for x in rows]

    def update(self, kind, identifier, **changes):
        with self.transaction() as db:
            value = self.get(kind, identifier, db)
            value.update(changes)
            self.put(kind, value, db)
            return value

    def remembered(self, scope, key, digest, db):
        if not key:
            return None
        row = db.execute("SELECT digest,value FROM idempotency WHERE scope=? AND key=?", (scope, key)).fetchone()
        if row and row[0] != digest:
            raise PPSError("IDEMPOTENCY_CONFLICT", "Cette clé correspond à une autre requête.", 409)
        return json.loads(row[1]) if row else None

    def remember(self, scope, key, digest, value, db):
        if key:
            db.execute("INSERT INTO idempotency VALUES(?,?,?,?)", (scope, key, digest, json.dumps(value)))
