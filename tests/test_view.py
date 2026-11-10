"""Inspection through the real CLI, including no-write guarantees."""
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


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fm view ")
        self.project = Path(self.tmp.name)
        self.env = patch.dict(os.environ)
        self.env.start()
        os.environ.pop("FM_CONFIG", None)
        os.environ.pop("FM_PROFILE", None)
        (self.project / "feedback-manager.toml").write_text('default_mode = "auto"\n')
        self.store = Store(self.project)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def view(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts/feedback.py"), "--project", str(self.project), "--view", *args],
                              stdin=subprocess.DEVNULL, text=True, capture_output=True)

    def create(self, text="図を大きくする"):
        result = self.store.execute({"op": "create", "guideline": text, "original": "今後は図を大きく\n余白も確保して", "tags": {"existing": [], "create": []}})
        self.assertEqual(result["status"], "ok", result)
        return result["data"]

    def test_empty_view_does_not_create_database(self):
        result = self.view()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0件", result.stdout)
        self.assertIn("保存された指針はありません", result.stdout)
        self.assertFalse(self.store.directory.exists())
        self.assertIn("変更履歴はありません", self.view("history").stdout)
        self.assertFalse(self.store.directory.exists())

    def test_active_all_history_and_no_record_changes(self):
        first = self.create()
        second = self.create("文字は少なく")
        self.store.execute({"op": "disable", "id": second["id"]})
        before = self.store.execute({"op": "history"})
        active, all_rows, history = self.view(), self.view("all"), self.view("history")
        for result in (active, all_rows, history):
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1件", active.stdout)
        self.assertIn(first["id"], active.stdout)
        self.assertIn("図を大きくする", active.stdout)
        self.assertIn("> 今後は図を大きく\n> 余白も確保して", active.stdout)
        self.assertIn("UTC", active.stdout)
        self.assertNotIn(second["id"], active.stdout)
        self.assertIn("2件", all_rows.stdout)
        self.assertIn(second["id"], all_rows.stdout)
        self.assertIn("状態: 無効", all_rows.stdout)
        self.assertIn("変更前:", history.stdout)
        self.assertIn("変更後:", history.stdout)
        self.assertIn("disable", history.stdout)
        self.assertEqual(self.store.execute({"op": "history"}), before)

    def test_read_only_off_and_invalid_config(self):
        row = self.create()
        (self.project / "feedback-manager.toml").write_text('default_mode = "read_only"\n')
        self.assertIn(row["id"], self.view().stdout)
        (self.project / "feedback-manager.toml").write_text('default_mode = "off"\n')
        result = self.view()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("FM_DISABLED", result.stderr)
        (self.project / "feedback-manager.toml").write_text('default_mode = "typo"\n')
        result = self.view()
        self.assertEqual(result.returncode, 1)
        self.assertIn("CONFIG_ERROR", result.stderr)

    def test_v1_view_does_not_migrate_and_controls_are_literal(self):
        self.create("図\x1b[31mを大きく\x07")
        with sqlite3.connect(self.store.path) as con:
            con.execute("DROP TABLE history")
            con.execute("PRAGMA user_version=1")
        result = self.view()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("\x1b", result.stdout)
        self.assertNotIn("\x07", result.stdout)
        self.assertIn("変更履歴はありません", self.view("history").stdout)
        with sqlite3.connect(self.store.path) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='history'").fetchone())

    def test_json_protocol_still_works_and_inspection_rejects_mutations(self):
        row = self.create()
        result = subprocess.run([sys.executable, str(ROOT / "scripts/feedback.py"), "--project", str(self.project)],
                                input='{"op":"list"}', text=True, capture_output=True)
        self.assertEqual(json.loads(result.stdout)["data"], [row])
        denied = self.store.execute({"op": "disable", "id": row["id"]}, read_only=True)
        self.assertEqual(denied["error"]["code"], "WRITE_FORBIDDEN")
        self.assertEqual(self.view("unknown").returncode, 1)


if __name__ == "__main__":
    unittest.main()
