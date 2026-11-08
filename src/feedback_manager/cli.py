"""One JSON request on stdin; one JSON result on stdout."""
import argparse
import json
from pathlib import Path
import sys

from .core import FeedbackError, Store


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
        raise FeedbackError("INVALID_ARGUMENT", "Use --project PATH and provide one JSON request on stdin.")


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path, help="Explicit target project root (not the skill directory)")
    try:
        args = parser.parse_args(argv)
    except FeedbackError as exc:
        result = Store(Path.cwd()).error("cli", exc.code, exc.message)
    else:
        store = Store(args.project)
        try:
            request = read_request(sys.stdin)
        except (ValueError, UnicodeError, OSError, RecursionError):
            result = store.error("parse", "INVALID_JSON", "Expected one UTF-8 JSON object up to 1 MiB, with unique keys.")
        else:
            result = store.execute(request)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 1
