"""Host-neutral bootstrap executable for the OpenCode adapter."""
import argparse
import json
from pathlib import Path
import sys

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "src"))
from feedback_manager.context import render_context
from feedback_manager.core import Store

parser = argparse.ArgumentParser()
parser.add_argument("--project", required=True, type=Path)
args = parser.parse_args()
store = Store(args.project)
result = store.execute({"op": "bootstrap"})
if result["status"] == "ok":
    result["data"]["context"] = render_context(result["data"].copy(), store.project, SKILL)
print(json.dumps(result, ensure_ascii=False))
raise SystemExit(0 if result["status"] == "ok" else 1)
