"""Validation, transactions and JSON result contract (no host dependencies)."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from uuid import UUID, uuid4


class FeedbackError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def fail(code: str, message: str):
    raise FeedbackError(code, message)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        fail("INVALID_INPUT", "Required fields are missing or unsupported fields are present.")


def nonempty(value, label, limit=20000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        fail("INVALID_INPUT", f"{label} must be a nonempty string within its size limit, without NUL.")
    return value


def tag_names(value):
    if not isinstance(value, list) or len(value) > 100:
        fail("INVALID_INPUT", "Tags must be an array of at most 100 strings.")
    names = [nonempty(x, "Tag", 100).strip() for x in value]
    if len(set(names)) != len(names):
        fail("INVALID_INPUT", "Duplicate tags are not allowed after trimming.")
    return names


def identifier(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        fail("INVALID_ID", "ID must be a canonical UUID string.")
    return value


SCHEMA = (
    "CREATE TABLE IF NOT EXISTS feedback (id TEXT PRIMARY KEY, guideline TEXT NOT NULL, original TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS tags (name TEXT PRIMARY KEY)",
    "CREATE TABLE IF NOT EXISTS feedback_tags (feedback_id TEXT NOT NULL REFERENCES feedback(id) ON DELETE CASCADE, tag TEXT NOT NULL REFERENCES tags(name), PRIMARY KEY(feedback_id,tag))",
    "CREATE INDEX IF NOT EXISTS feedback_tags_tag ON feedback_tags(tag)",
)


class Store:
    def __init__(self, project: Path, timeout: float = 5.0):
        self.project = Path(project).resolve()
        self.directory = self.project / ".feedback-manager"
        self.path = self.directory / "feedback.sqlite3"
        self.timeout = timeout

    def connect(self):
        if not self.project.is_dir():
            fail("PROJECT_NOT_FOUND", "The explicit project directory must exist.")
        self.directory.mkdir(mode=0o700, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=self.timeout)
        try:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                fail("SCHEMA_VERSION", "Database schema is newer than this application.")
            con.execute("PRAGMA journal_mode=WAL")
            if version == 0:
                with con:
                    for statement in SCHEMA:
                        con.execute(statement)
                    con.execute("PRAGMA user_version=1")
            return con
        except Exception:
            con.close()
            raise

    def log_error(self, operation, error):
        # Fixed messages only: never persist requests, SQL errors, paths or user text.
        entry = {"timestamp": utc_now(), "operation": operation, "code": error["code"], "message": error["message"]}
        try:
            self.directory.mkdir(mode=0o700, exist_ok=True)
            path = self.directory / "errors.jsonl"
            if path.exists() and path.stat().st_size >= 1024 * 1024:
                path.replace(self.directory / "errors.jsonl.1")
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            print(json.dumps({"status": "error", "code": "LOG_WRITE_FAILED", "message": "Could not persist the error log.", "error": entry}, ensure_ascii=False), file=sys.stderr)

    def error(self, operation, code, message):
        result = {"status": "error", "operation": operation, "error": {"code": code, "message": message}}
        self.log_error(operation, result["error"])
        return result

    def execute(self, request):
        operation = request.get("op") if isinstance(request, dict) else None
        if not isinstance(operation, str) or operation not in {"create", "list", "get", "update", "disable", "delete", "tags", "search", "bootstrap", "batch"}:
            operation = "unknown"
        try:
            if operation == "unknown":
                fail("INVALID_OPERATION", "Unknown or missing operation.")
            with closing(self.connect()) as con, con:
                result = self.dispatch(con, request)
            return {"status": "ok", "operation": operation, "data": result}
        except FeedbackError as exc:
            return self.error(operation, exc.code, exc.message)
        except sqlite3.Error as exc:
            code = "DB_LOCKED" if getattr(exc, "sqlite_errorcode", 0) & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else "DB_ERROR"
            return self.error(operation, code, "Database operation failed; check access, integrity and concurrent writers.")
        except OSError:
            return self.error(operation, "IO_ERROR", "Project storage could not be accessed.")
        except UnicodeError:
            return self.error(operation, "INVALID_INPUT", "Text must contain valid Unicode scalar values.")

    @staticmethod
    def tags(con):
        return [r[0] for r in con.execute("SELECT name FROM tags ORDER BY name")]

    def select_tags(self, con, value):
        fields(value, ("existing", "create", "confirmed"))
        if value["confirmed"] is not True:
            fail("TAG_CONFIRMATION_REQUIRED", "Confirm tag selection with the user first.")
        existing, new = tag_names(value["existing"]), tag_names(value["create"])
        if set(existing) & set(new):
            fail("INVALID_INPUT", "Existing and new tags must not overlap.")
        known = set(self.tags(con))
        if set(existing) - known:
            fail("UNKNOWN_TAG", "An existing tag is unregistered; use tags to inspect the catalogue or explicitly create it.")
        if set(new) & known:
            fail("TAG_EXISTS", "A proposed new tag already exists; select it as existing.")
        con.executemany("INSERT INTO tags(name) VALUES (?)", [(x,) for x in new])
        return existing + new

    @staticmethod
    def record(con, record_id):
        record_id = identifier(record_id)
        row = con.execute("SELECT * FROM feedback WHERE id=?", (record_id,)).fetchone()
        if row is None:
            fail("NOT_FOUND", "No feedback exists with the supplied ID.")
        result = dict(row)
        result["active"] = bool(result["active"])
        result["tags"] = [r[0] for r in con.execute("SELECT tag FROM feedback_tags WHERE feedback_id=? ORDER BY tag", (record_id,))]
        return result

    def records(self, con, query, params=()):
        return [self.record(con, row[0]) for row in con.execute(query, params).fetchall()]

    @staticmethod
    def attach(con, record_id, tags):
        con.execute("DELETE FROM feedback_tags WHERE feedback_id=?", (record_id,))
        con.executemany("INSERT INTO feedback_tags VALUES (?,?)", [(record_id, tag) for tag in tags])

    def dispatch(self, con, request):
        op = request["op"]
        if op == "create":
            fields(request, ("op", "guideline", "original", "tags"))
            guideline = nonempty(request["guideline"], "Guideline", 4000)
            original = nonempty(request["original"], "Original")
            tags = self.select_tags(con, request["tags"])
            record_id, now = str(uuid4()), utc_now()
            con.execute("INSERT INTO feedback VALUES (?,?,?,1,?,?)", (record_id, guideline, original, now, now))
            self.attach(con, record_id, tags)
            return self.record(con, record_id)
        if op == "update":
            fields(request, ("op", "id"), ("guideline", "original", "active", "tags"))
            if len(request) == 2:
                fail("INVALID_INPUT", "Update requires at least one changed field.")
            row = self.record(con, request["id"])
            for key, limit in (("guideline", 4000), ("original", 20000)):
                if key in request:
                    row[key] = nonempty(request[key], key, limit)
            if "active" in request:
                if type(request["active"]) is not bool:
                    fail("INVALID_INPUT", "Active must be a JSON boolean.")
                row["active"] = request["active"]
            if "tags" in request:
                self.attach(con, row["id"], self.select_tags(con, request["tags"]))
            con.execute("UPDATE feedback SET guideline=?, original=?, active=?, updated_at=? WHERE id=?", (row["guideline"], row["original"], row["active"], utc_now(), row["id"]))
            return self.record(con, row["id"])
        if op in ("get", "disable", "delete"):
            fields(request, ("op", "id"))
            row = self.record(con, request["id"])
            if op == "disable":
                con.execute("UPDATE feedback SET active=0, updated_at=? WHERE id=?", (utc_now(), row["id"]))
                return self.record(con, row["id"])
            if op == "delete":
                con.execute("DELETE FROM feedback WHERE id=?", (row["id"],))
                return {"deleted_id": row["id"]}
            return row
        if op == "list":
            fields(request, ("op",), ("include_inactive",))
            if type(request.get("include_inactive", False)) is not bool:
                fail("INVALID_INPUT", "include_inactive must be a JSON boolean.")
            return self.records(con, "SELECT id FROM feedback" + ("" if request.get("include_inactive") else " WHERE active=1") + " ORDER BY created_at,id")
        if op == "tags":
            fields(request, ("op",))
            return self.tags(con)
        if op == "search":
            fields(request, ("op", "tags"))
            names = tag_names(request["tags"])
            if set(names) - set(self.tags(con)):
                fail("UNKNOWN_TAG", "Search tags must be selected from the existing catalogue.")
            placeholders = ",".join("?" for _ in names) or "NULL"
            return self.records(con, f"SELECT id FROM feedback f WHERE active=1 AND (NOT EXISTS (SELECT 1 FROM feedback_tags t WHERE t.feedback_id=f.id) OR EXISTS (SELECT 1 FROM feedback_tags t WHERE t.feedback_id=f.id AND t.tag IN ({placeholders}))) ORDER BY created_at,id", names)
        if op == "bootstrap":
            fields(request, ("op",))
            return {"feedback": self.dispatch(con, {"op": "search", "tags": []}), "tags": self.tags(con)}
        if op == "batch":
            fields(request, ("op", "operations"))
            operations = request["operations"]
            if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
                fail("INVALID_INPUT", "Batch requires 1 to 100 operations.")
            results = []
            for item in operations:
                if not isinstance(item, dict) or item.get("op") not in ("create", "update", "disable", "delete"):
                    fail("INVALID_INPUT", "Batch accepts create, update, disable and delete only.")
                results.append(self.dispatch(con, item))
            return results
        fail("INVALID_OPERATION", "Unsupported operation.")
