"""Portable entry point; run with uv run --project <skill> <this file>."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from feedback_manager.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
