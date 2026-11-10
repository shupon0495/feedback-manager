"""Validation, transactions and JSON result contract (no host dependencies)."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from uuid import UUID, uuid4

from .policy import load_policy


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


HISTORY_SCHEMA = "CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, operation TEXT NOT NULL, before_state TEXT NOT NULL, after_state TEXT NOT NULL)"
MUTATIONS = {"create", "update", "disable", "delete", "undo"}


class Store:
    def __init__(self, project: Path, timeout: float = 5.0):
        self.project = Path(project).resolve()
        self.directory = self.project / ".feedback-manager"
        self.path = self.directory / "feedback.sqlite3"
        self.timeout = timeout

    def connect(self, *, read_only=False):
        if not self.project.is_dir():
            fail("PROJECT_NOT_FOUND", "The explicit project directory must exist.")
        readonly = read_only or self.policy["mode"] == "read_only"
        if readonly:
            if not self.path.exists():
                con = sqlite3.connect(":memory:")
                for statement in (*SCHEMA, HISTORY_SCHEMA):
                    con.execute(statement)
                con.execute("PRAGMA user_version=2")
                con.commit()
            else:
                con = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=self.timeout)
        else:
            self.directory.mkdir(mode=0o700, exist_ok=True)
            con = sqlite3.connect(self.path, timeout=self.timeout)
        try:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                fail("SCHEMA_VERSION", "Database schema is newer than this application.")
            if not readonly:
                con.execute("PRAGMA journal_mode=WAL")
            if version < 2 and not readonly:
                with con:
                    for statement in SCHEMA:
                        con.execute(statement)
                    con.execute(HISTORY_SCHEMA)
                    con.execute("PRAGMA user_version=2")
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
        # Parsing can fail before execute() loads the policy.
        try:
            mode = load_policy(self.project)["mode"]
        except (ValueError, OSError, TypeError):
            mode = None
        if mode not in ("off", "read_only"):
            self.log_error(operation, result["error"])
        return result

    def execute(self, request, *, read_only=False):
        operation = request.get("op") if isinstance(request, dict) else None
        if not isinstance(operation, str) or operation not in {"create", "list", "get", "update", "disable", "delete", "tags", "search", "bootstrap", "batch", "history", "undo", "policy"}:
            operation = "unknown"
        try:
            if operation == "unknown":
                fail("INVALID_OPERATION", "Unknown or missing operation.")
            try:
                self.policy = load_policy(self.project)
            except (ValueError, OSError, TypeError):
                fail("CONFIG_ERROR", "Could not load a valid Feedback Manager policy; check config and FM_PROFILE.")
            if operation == "policy":
                fields(request, ("op",))
                return {"status": "ok", "operation": operation, "data": self.policy}
            if self.policy["mode"] == "off":
                if operation == "bootstrap":
                    fields(request, ("op",))
                    return {"status": "ok", "operation": operation, "data": {"policy": self.policy, "feedback": [], "tags": []}}
                fail("FM_DISABLED", "Feedback Manager is disabled by configuration.")
            self.authorize(request)
            if read_only and (operation in MUTATIONS or operation == "batch"):
                fail("WRITE_FORBIDDEN", "This invocation permits inspection only.")
            with closing(self.connect(read_only=read_only)) as con, con:
                # Reserve the writer before taking snapshots or validating an undo.
                if operation in MUTATIONS or operation == "batch":
                    con.execute("BEGIN IMMEDIATE")
                    before = self.snapshot(con)
                    result = self.dispatch(con, request)
                    after = self.snapshot(con)
                    con.execute("INSERT INTO history(timestamp,operation,before_state,after_state) VALUES (?,?,?,?)",
                                (utc_now(), operation, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False)))
                else:
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

    def authorize(self, request, confirmed=False):
        op = request.get("op")
        if not isinstance(op, str) or (op not in MUTATIONS and op != "batch"):
            return
        if self.policy["mode"] == "read_only":
            fail("WRITE_FORBIDDEN", "Feedback Manager is read-only.")
        if op in ("update", "disable", "undo") and not self.policy["allow_update"]:
            fail("UPDATE_FORBIDDEN", "Existing feedback changes are disabled.")
        if op == "delete" and not self.policy["allow_delete"]:
            fail("DELETE_FORBIDDEN", "Feedback deletion is disabled.")
        if "confirmed" in request and type(request["confirmed"]) is not bool:
            fail("INVALID_INPUT", "confirmed must be a JSON boolean.")
        confirmed = confirmed or request.get("confirmed") is True
        if self.policy["mode"] == "ask" and not confirmed:
            fail("CONFIRMATION_REQUIRED", "Ask the user to approve the proposed changes first.")
        if op == "batch" and isinstance(request.get("operations"), list):
            for item in request["operations"]:
                if isinstance(item, dict):
                    self.authorize(item, confirmed)

    def snapshot(self, con):
        return {"feedback": self.records(con, "SELECT id FROM feedback ORDER BY created_at,id"), "tags": self.tags(con)}

    @staticmethod
    def tags(con):
        return [r[0] for r in con.execute("SELECT name FROM tags ORDER BY name")]

    def select_tags(self, con, value):
        fields(value, ("existing", "create"), ("confirmed",))
        if "confirmed" in value and type(value["confirmed"]) is not bool:
            fail("INVALID_INPUT", "Tag confirmed must be a JSON boolean.")
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
        # Confirmation belongs to the entire mutation, not to tag classification.
        request = {key: value for key, value in request.items() if key != "confirmed"} if op in MUTATIONS or op == "batch" else request
        if op == "history":
            fields(request, ("op",))
            if not con.execute("SELECT 1 FROM sqlite_master WHERE name='history' AND type='table'").fetchone():
                return []
            return [{**dict(row), "before_state": json.loads(row["before_state"]), "after_state": json.loads(row["after_state"])}
                    for row in con.execute("SELECT * FROM history ORDER BY id")]
        if op == "undo":
            fields(request, ("op", "history_id"))
            latest = con.execute("SELECT * FROM history ORDER BY id DESC LIMIT 1").fetchone()
            if type(request["history_id"]) is not int or latest is None or latest["id"] != request["history_id"]:
                fail("UNDO_CONFLICT", "Only the latest history entry can be undone; inspect history first.")
            before, after = json.loads(latest["before_state"]), json.loads(latest["after_state"])
            if self.snapshot(con) != after:
                fail("UNDO_CONFLICT", "Feedback changed outside the recorded history.")
            old_ids = {row["id"] for row in before["feedback"]}
            if any(row["id"] not in old_ids for row in after["feedback"]) and not self.policy["allow_delete"]:
                fail("DELETE_FORBIDDEN", "Undo would remove feedback; deletion is disabled.")
            con.execute("DELETE FROM feedback_tags")
            con.execute("DELETE FROM feedback")
            con.execute("DELETE FROM tags")
            con.executemany("INSERT INTO tags(name) VALUES (?)", [(tag,) for tag in before["tags"]])
            for row in before["feedback"]:
                con.execute("INSERT INTO feedback VALUES (?,?,?,?,?,?)", tuple(row[k] for k in ("id", "guideline", "original", "active", "created_at", "updated_at")))
                self.attach(con, row["id"], row["tags"])
            return {"undone_history_id": latest["id"]}
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
            return {"policy": self.policy, "feedback": self.dispatch(con, {"op": "search", "tags": []}), "tags": self.tags(con)}
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
