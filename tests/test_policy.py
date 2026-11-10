"""Observable policy enforcement, hook behavior and transactional history."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from feedback_manager.core import Store
from feedback_manager.context import render_context


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.env = patch.dict(os.environ)
        self.env.start()
        os.environ.pop("FM_PROFILE", None)
        os.environ.pop("FM_CONFIG", None)
        self.store = Store(self.project)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def configure(self, text):
        (self.project / "feedback-manager.toml").write_text(text)

    def call(self, op, **kwargs):
        result = self.store.execute({"op": op, **kwargs})
        self.assertEqual(result["status"], "ok", result)
        return result["data"]

    def create(self, **kwargs):
        return self.call("create", guideline="図を大きく", original="今後も図は大きくして", tags={"existing": [], "create": []}, **kwargs)

    def denied(self, code, request):
        result = self.store.execute(request)
        self.assertEqual(result.get("error", {}).get("code"), code, result)

    def test_default_ask_confirms_whole_mutation_and_batch(self):
        self.denied("CONFIRMATION_REQUIRED", {"op": "create", "guideline": "x", "original": "x", "tags": {"existing": [], "create": [], "confirmed": True}})
        row = self.create(confirmed=True)
        self.denied("CONFIRMATION_REQUIRED", {"op": "update", "id": row["id"], "guideline": "new"})
        self.call("batch", confirmed=True, operations=[{"op": "update", "id": row["id"], "guideline": "new"}, {"op": "disable", "id": row["id"]}])
        self.assertFalse(self.call("get", id=row["id"])["active"])
        self.denied("DELETE_FORBIDDEN", {"op": "delete", "id": row["id"], "confirmed": True})

    def test_auto_tags_and_append_only_batch_preflight(self):
        self.configure('default_mode = "auto"\nallow_update = false\n')
        row = self.create()
        before = self.call("history")
        self.denied("UPDATE_FORBIDDEN", {"op": "batch", "operations": [
            {"op": "create", "guideline": "new", "original": "new", "tags": {"existing": [], "create": ["new"]}},
            {"op": "disable", "id": row["id"]}]})
        self.assertEqual(self.call("list"), [row])
        self.assertEqual(self.call("tags"), [])
        self.assertEqual(self.call("history"), before)
        self.denied("UPDATE_FORBIDDEN", {"op": "undo", "history_id": before[-1]["id"]})

    def test_read_only_and_off_do_not_initialize_storage(self):
        self.configure('default_mode = "read_only"\n')
        self.assertEqual(self.call("bootstrap")["feedback"], [])
        self.denied("WRITE_FORBIDDEN", {"op": "create", "confirmed": True})
        self.assertFalse(self.store.directory.exists())
        self.configure('default_mode = "off"\n')
        data = self.call("bootstrap")
        self.assertEqual(data["tags"], [])
        self.assertEqual(render_context(data, self.project, ROOT), "")
        self.denied("FM_DISABLED", {"op": "history"})
        self.assertFalse(self.store.directory.exists())
        self.assertEqual(self.call("policy")["mode"], "off")
        run = subprocess.run([sys.executable, str(ROOT / "scripts/feedback.py"), "--project", str(self.project)],
                             input="invalid JSON", text=True, capture_output=True)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)["error"]["code"], "INVALID_JSON")
        self.assertFalse(self.store.directory.exists())

    def test_readonly_existing_and_off_hide_stored_text(self):
        self.configure('default_mode = "auto"\n')
        row = self.create()
        self.configure('default_mode = "read_only"\n')
        self.assertEqual(self.call("list"), [row])
        self.denied("WRITE_FORBIDDEN", {"op": "batch", "confirmed": True, "operations": [{"op": "delete", "id": row["id"]}]})
        self.configure('default_mode = "off"\n')
        for adapter in ("codex_hook.py", "bootstrap.py"):
            command = [sys.executable, str(ROOT / "adapters" / adapter), "--project", str(self.project)]
            run = subprocess.run(command, input=json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(self.project)}), text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertNotIn(row["guideline"], run.stdout)
            if adapter == "codex_hook.py":
                self.assertEqual(json.loads(run.stdout), {})
            else:
                self.assertEqual(json.loads(run.stdout)["data"]["context"], "")

    def test_profile_selection_external_config_and_invalid_policy(self):
        external = self.project / "external.toml"
        external.write_text('default_mode = "ask"\nallow_update = false\n[profiles.trusted]\nmode = "auto"\n')
        os.environ["FM_CONFIG"] = str(external)
        os.environ["FM_PROFILE"] = "trusted"
        self.assertEqual(self.call("policy")["mode"], "auto")
        self.assertFalse(self.call("policy")["allow_update"])
        self.create()
        os.environ["FM_PROFILE"] = "missing"
        self.denied("CONFIG_ERROR", {"op": "bootstrap"})
        os.environ.pop("FM_PROFILE")
        for value in ('default_mode = "typo"', 'allow_delete = "false"', '[profiles.x]\nunknown = true', 'default_mode = ["auto"]', 'bad TOML', 'profiles = []'):
            external.write_text(value)
            self.denied("CONFIG_ERROR", {"op": "bootstrap"})
        os.environ["FM_CONFIG"] = "relative.toml"
        self.denied("CONFIG_ERROR", {"op": "bootstrap"})

    def test_history_undo_delete_and_stale_id(self):
        self.configure('default_mode = "auto"\nallow_delete = true\n')
        row = self.create()
        self.call("batch", operations=[{"op": "update", "id": row["id"], "guideline": "changed", "tags": {"existing": [], "create": ["slidev"]}}, {"op": "disable", "id": row["id"]}])
        history = self.call("history")
        self.assertEqual(len(history), 2)
        self.denied("UNDO_CONFLICT", {"op": "undo", "history_id": history[0]["id"]})
        self.call("undo", history_id=history[-1]["id"])
        self.assertEqual(self.call("get", id=row["id"]), row)
        self.assertEqual(self.call("tags"), [])
        self.call("delete", id=row["id"])
        self.call("undo", history_id=self.call("history")[-1]["id"])
        self.assertEqual(self.call("get", id=row["id"]), row)
        # Undo creation must honor the deletion restriction.
        self.create()
        self.configure('default_mode = "auto"\n')
        self.denied("DELETE_FORBIDDEN", {"op": "undo", "history_id": self.call("history")[-1]["id"]})

    def test_failed_batch_has_no_history_and_external_edits_block_undo(self):
        self.configure('default_mode = "auto"\n')
        row = self.create()
        history = self.call("history")
        result = self.store.execute({"op": "batch", "operations": [{"op": "update", "id": row["id"], "guideline": "new"}, {"op": "disable", "id": "bad"}]})
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.call("history"), history)
        self.assertEqual(self.call("get", id=row["id"]), row)
        with sqlite3.connect(self.store.path) as con:
            con.execute("UPDATE feedback SET guideline='external'")
        self.denied("UNDO_CONFLICT", {"op": "undo", "history_id": history[-1]["id"]})

    def test_v1_readonly_then_migration_preserves_records(self):
        self.configure('default_mode = "auto"\n')
        row = self.create()
        with sqlite3.connect(self.store.path) as con:
            con.execute("DROP TABLE history")
            con.execute("PRAGMA user_version=1")
        self.configure('default_mode = "read_only"\n')
        self.assertEqual(self.call("history"), [])
        self.assertEqual(self.call("list"), [row])
        with sqlite3.connect(self.store.path) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
        self.configure('default_mode = "auto"\n')
        self.call("update", id=row["id"], guideline="updated")
        self.assertEqual(self.call("history")[0]["before_state"]["feedback"], [row])
        with sqlite3.connect(self.store.path) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
