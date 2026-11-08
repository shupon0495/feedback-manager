import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from feedback_manager.core import Store


def choice(existing=(), create=()):
    return {"existing": list(existing), "create": list(create), "confirmed": True}


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fm test ")
        self.project = Path(self.tmp.name)
        self.store = Store(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, op, **kw):
        result = self.store.execute({"op": op, **kw})
        self.assertEqual(result["status"], "ok", result)
        return result["data"]

    def create(self, **kw):
        return self.call("create", **{"guideline": "短く説明", "original": " 今後は短く説明して\n", "tags": choice(), **kw})

    def test_lifecycle_and_timestamps(self):
        row = self.create()
        self.assertTrue(row["active"])
        self.assertTrue(row["created_at"].endswith("+00:00"))
        self.assertEqual(row["original"], " 今後は短く説明して\n")
        self.assertEqual(self.call("get", id=row["id"]), row)
        self.assertEqual(self.call("list"), [row])
        changed = self.call("update", id=row["id"], guideline="具体的に", original="今後は具体的に")
        self.assertEqual(changed["created_at"], row["created_at"])
        self.assertGreater(changed["updated_at"], row["updated_at"])
        self.assertFalse(self.call("disable", id=row["id"])["active"])
        self.assertEqual(self.call("list"), [])
        self.assertEqual(len(self.call("list", include_inactive=True)), 1)
        self.assertTrue(self.call("update", id=row["id"], active=True)["active"])
        self.call("delete", id=row["id"])
        self.assertEqual(self.store.execute({"op": "get", "id": row["id"]})["error"]["code"], "NOT_FOUND")

    def test_tag_or_untagged_inactive_and_catalogue(self):
        general = self.create()
        a = self.create(tags=choice(create=[" 文書 ", "UI"]))
        b = self.create(tags=choice(create=["テスト"]))
        self.create(tags=choice(create=["無関係"]))
        inactive = self.create(tags=choice(existing=["文書"]))
        self.call("disable", id=inactive["id"])
        self.assertEqual({r["id"] for r in self.call("search", tags=["文書", "テスト"])}, {general["id"], a["id"], b["id"]})
        self.assertEqual(self.call("search", tags=[]), [general])
        self.assertEqual(self.call("bootstrap")["feedback"], [general])
        self.call("delete", id=a["id"])
        self.assertIn("UI", self.call("tags"))
        with sqlite3.connect(self.store.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM feedback_tags WHERE feedback_id=?", (a["id"],)).fetchone()[0], 0)

    def test_tag_validation_and_explicit_creation(self):
        row = self.create(tags=choice(create=["known"]))
        for value, code in [(choice(existing=["unknown"]), "UNKNOWN_TAG"), (choice(create=["known"]), "TAG_EXISTS"), (choice(create=[" "]), "INVALID_INPUT"), (choice(create=["a", " a "]), "INVALID_INPUT"), ({**choice(), "confirmed": False}, "TAG_CONFIRMATION_REQUIRED"), (choice(existing=["known"], create=["known"]), "INVALID_INPUT")]:
            with self.subTest(value=value):
                result = self.store.execute({"op": "update", "id": row["id"], "tags": value})
                self.assertEqual(result["error"]["code"], code)
                self.assertEqual(self.call("get", id=row["id"])["tags"], ["known"])
        self.call("update", id=row["id"], guideline="changed")
        self.assertEqual(self.call("get", id=row["id"])["tags"], ["known"])
        self.call("update", id=row["id"], tags=choice())
        self.assertEqual(self.call("get", id=row["id"])["tags"], [])
        self.assertEqual(self.store.execute({"op": "search", "tags": ["typo"]})["error"]["code"], "UNKNOWN_TAG")

    def test_batch_rollback_includes_tags_and_delete(self):
        a, b = self.create(), self.create()
        result = self.store.execute({"op": "batch", "operations": [
            {"op": "update", "id": a["id"], "guideline": "new", "tags": choice(create=["new-tag"])},
            {"op": "delete", "id": b["id"]},
            {"op": "disable", "id": str(uuid4())},
        ]})
        self.assertEqual(result["error"]["code"], "NOT_FOUND")
        self.assertEqual(self.call("get", id=a["id"]), a)
        self.assertEqual(self.call("get", id=b["id"]), b)
        self.assertEqual(self.call("tags"), [])

    def test_successful_atomic_merge(self):
        a, b = self.create(), self.create()
        self.call("batch", operations=[{"op": "update", "id": a["id"], "guideline": "merged", "original": "merge them"}, {"op": "disable", "id": b["id"]}])
        self.assertEqual([r["guideline"] for r in self.call("search", tags=[])], ["merged"])

    def test_sql_failure_rolls_back_partial_tag_creation(self):
        self.call("tags")
        with sqlite3.connect(self.store.path) as db:
            db.execute("CREATE TRIGGER reject_insert BEFORE INSERT ON feedback BEGIN SELECT RAISE(ABORT, 'private detail'); END")
        result = self.store.execute({"op": "create", "guideline": "x", "original": "secret", "tags": choice(create=["new"])})
        self.assertEqual(result["error"]["code"], "DB_ERROR")
        self.assertEqual(self.call("tags"), [])
        self.assertNotIn("private detail", (self.store.directory / "errors.jsonl").read_text())

    def test_invalid_data_does_not_save(self):
        for request in [None, [], {"op": []}, {"op": "create"}, {"op": "create", "guideline": " ", "original": "x", "tags": choice()}, {"op": "list", "include_inactive": 1}, {"op": "list", "extra": "secret"}, {"op": "get", "id": "' OR 1=1"}, {"op": "batch", "operations": [{"op": []}]}]:
            with self.subTest(request=request):
                self.assertEqual(self.store.execute(request)["status"], "error")
        self.assertEqual(self.call("list", include_inactive=True), [])

    def test_invalid_unicode_is_structured(self):
        result = self.store.execute({"op": "create", "guideline": "\ud800", "original": "x", "tags": choice()})
        self.assertEqual(result["error"]["code"], "INVALID_INPUT")
        self.assertEqual(self.call("list"), [])

    def test_busy_timeout_and_error_log(self):
        self.create()
        self.store.timeout = 0.1
        with sqlite3.connect(self.store.path) as other:
            other.execute("BEGIN IMMEDIATE")
            start = time.monotonic()
            result = self.store.execute({"op": "create", "guideline": "x", "original": "SENSITIVE", "tags": choice()})
            self.assertGreaterEqual(time.monotonic() - start, 0.08)
            self.assertEqual(result["error"]["code"], "DB_LOCKED")
        log = (self.store.directory / "errors.jsonl").read_text()
        self.assertNotIn("SENSITIVE", log)
        self.assertEqual(json.loads(log)["operation"], "create")

    def test_broken_database_logs_and_unwritable_log_falls_back(self):
        self.store.directory.mkdir()
        self.store.path.write_text("not a sqlite database")
        self.assertEqual(self.store.execute({"op": "tags"})["error"]["code"], "DB_ERROR")
        self.assertTrue((self.store.directory / "errors.jsonl").exists())
        with patch.object(Path, "open", side_effect=PermissionError), contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = self.store.execute({"op": "tags"})
        self.assertEqual(result["status"], "error")
        self.assertIn("LOG_WRITE_FAILED", stderr.getvalue())

    def test_log_rotation_and_future_schema(self):
        self.call("tags")
        log = self.store.directory / "errors.jsonl"
        log.write_text("x" * 1024 * 1024)
        with sqlite3.connect(self.store.path) as db:
            db.execute("PRAGMA user_version=99")
        self.assertEqual(self.store.execute({"op": "tags"})["error"]["code"], "SCHEMA_VERSION")
        self.assertTrue(log.with_name("errors.jsonl.1").exists())

    def test_cli_json_protocol(self):
        for payload in ['{"op":"tags"}', 'oops', '{"op":"tags","op":"list"}', '{"op":NaN}']:
            process = subprocess.run([sys.executable, str(ROOT / "scripts/feedback.py"), "--project", str(self.project)], input=payload, text=True, capture_output=True)
            result = json.loads(process.stdout)
            self.assertEqual(process.returncode, 0 if payload == '{"op":"tags"}' else 1)
            self.assertIn("status", result)
        process = subprocess.run([sys.executable, str(ROOT / "scripts/feedback.py")], cwd=self.project, text=True, capture_output=True)
        self.assertEqual(json.loads(process.stdout)["error"]["code"], "INVALID_ARGUMENT")

    def test_project_isolation(self):
        self.create()
        other = self.project / "other"
        other.mkdir()
        self.assertEqual(Store(other).execute({"op": "list"})["data"], [])


class InstallationTests(unittest.TestCase):
    def test_move_install_merge_ignore_and_codex_contract(self):
        spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts/install.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="fm portable ") as tmp:
            project = Path(tmp) / "project with spaces"
            project.mkdir()
            (project / ".codex").mkdir()
            existing = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}}
            (project / ".codex/hooks.json").write_text(json.dumps(existing))
            target = module.install(project)
            Store(project).execute({"op": "create", "guideline": "portable marker", "original": "remember", "tags": choice()})
            module.install(project, update=True)
            config = json.loads((project / ".codex/hooks.json").read_text())
            self.assertEqual(config["hooks"]["Stop"], existing["hooks"]["Stop"])
            self.assertEqual(len(config["hooks"]["UserPromptSubmit"]), 1)
            moved = Path(tmp) / "moved project"
            shutil.move(project, moved)
            sub = moved / "nested"
            sub.mkdir()
            adapter = moved / target.relative_to(project) / "adapters/codex_hook.py"
            process = subprocess.run([sys.executable, str(adapter), "--project", str(moved)], input=json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(sub)}), text=True, capture_output=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            context = json.loads(process.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("portable marker", context)
            self.assertIn(str(moved), context)
            self.assertNotIn(str(project), context)
            # Exercise the exact configured shell command, including upward lookup and uv.
            env = {**os.environ, "UV_PYTHON": sys.executable}
            command = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
            launched = subprocess.run(["sh", "-c", command], cwd=sub, env=env, input=json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(sub)}), text=True, capture_output=True)
            self.assertEqual(launched.returncode, 0, launched.stderr)
            self.assertIn("portable marker", json.loads(launched.stdout)["hookSpecificOutput"]["additionalContext"])
            failed = subprocess.run([sys.executable, str(adapter), "--project", str(moved)], input='{}', text=True, capture_output=True)
            self.assertEqual(failed.returncode, 2)
            self.assertIn("INVALID_HOOK_INPUT", failed.stderr)
            if shutil.which("git"):
                subprocess.run(["git", "init", "-q", str(moved)], check=True)
                paths = [".feedback-manager/feedback.sqlite3", ".feedback-manager/feedback.sqlite3-wal", ".feedback-manager/feedback.sqlite3-shm", ".feedback-manager/feedback.sqlite3-journal", ".feedback-manager/errors.jsonl"]
                ignored = subprocess.run(["git", "check-ignore", *paths], cwd=moved, text=True, capture_output=True, check=True)
                self.assertEqual(set(ignored.stdout.splitlines()), set(paths))


if __name__ == "__main__":
    unittest.main()
