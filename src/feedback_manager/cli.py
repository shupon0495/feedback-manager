"""JSON requests on stdin, or --view for human-readable database inspection."""
import argparse
import json
from pathlib import Path
import sys

from .core import FeedbackError, Store
from .view import render_view


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys")
        result[key] = value
    return result


def read_request(stream):
    text = stream.read(1_048_577)
    if len(text) > 1_048_576 or len(text.encode("utf-8")) > 1_048_576:
        raise ValueError("Request too large")
    return json.loads(text, object_pairs_hook=reject_duplicates, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-JSON constant")))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise FeedbackError("INVALID_ARGUMENT", "Use --project PATH with JSON stdin or --view [active|all|history].")


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path, help="Explicit target project root (not the skill directory)")
    parser.add_argument("--view", nargs="?", const="active", choices=("active", "all", "history"),
                        help="Print readable Markdown without stdin or database writes (default: active)")
    try:
        args = parser.parse_args(argv)
    except FeedbackError as exc:
        result = Store(Path.cwd()).error("cli", exc.code, exc.message)
    else:
        store = Store(args.project)
        if args.view:
            policy = store.execute({"op": "policy"}, read_only=True)
            request = {"op": "history"} if args.view == "history" else {"op": "list", "include_inactive": args.view == "all"}
            result = store.execute(request, read_only=True) if policy["status"] == "ok" else policy
            if result["status"] != "ok":
                print(f"Feedback Manager: {result['error']['code']}: {result['error']['message']}", file=sys.stderr)
                return 1
            print(render_view(store.project, policy["data"], args.view, result["data"]), end="")
            return 0
        try:
            request = read_request(sys.stdin)
        except (ValueError, UnicodeError, OSError, RecursionError):
            result = store.error("parse", "INVALID_JSON", "Expected one UTF-8 JSON object up to 1 MiB, with unique keys.")
        else:
            result = store.execute(request)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 1
