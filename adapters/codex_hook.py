"""Codex 0.159.3 UserPromptSubmit adapter."""
import argparse
import json
from pathlib import Path
import sys

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "src"))
from feedback_manager.cli import read_request
from feedback_manager.context import render_context
from feedback_manager.core import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, type=Path)
    args = parser.parse_args()
    store = Store(args.project)
    try:
        event = read_request(sys.stdin)
        if not isinstance(event, dict) or event.get("hook_event_name") != "UserPromptSubmit":
            raise ValueError
        cwd = event.get("cwd")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).resolve().is_relative_to(store.project):
            raise ValueError
    except (ValueError, OSError, UnicodeError, RecursionError):
        result = store.error("codex_hook", "INVALID_HOOK_INPUT", "Expected UserPromptSubmit with cwd inside the configured project.")
    else:
        result = store.execute({"op": "bootstrap"})
    if result["status"] == "ok":
        if result["data"]["policy"]["mode"] == "off":
            print("{}")
            return 0
        output = {"systemMessage": "Feedback Manager: project preferences and tag catalogue loaded.", "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": render_context(result["data"], store.project, SKILL)}}
        print(json.dumps(output, ensure_ascii=False))
        return 0
    # A failure must not masquerade as an empty successful lookup. Exit 2 blocks this prompt.
    print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
