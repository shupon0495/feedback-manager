"""Install into an explicit project without modifying hook trust or runtime data."""
import argparse
import json
from pathlib import Path
import shutil

SOURCE = Path(__file__).resolve().parents[1]
FILES = ("SKILL.md", "pyproject.toml", "uv.lock", "README.md", ".gitignore")
DIRECTORIES = ("src", "scripts", "adapters", "docs", "tests")


def install(project, update=False):
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError("Target project must exist")
    target = project / ".agents/skills/feedback-manager"
    hook_path = project / ".codex/hooks.json"
    plugin_path = project / ".opencode/plugins/feedback-manager.js"
    template = json.loads((SOURCE / "adapters/hooks.json").read_text())
    config = json.loads(hook_path.read_text()) if hook_path.exists() else {"hooks": {}}
    if not isinstance(config, dict) or not isinstance(config.get("hooks", {}), dict):
        raise ValueError("Existing hooks.json has an invalid hooks object")
    groups = config.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
    if not isinstance(groups, list):
        raise ValueError("Existing UserPromptSubmit must be an array")
    group = template["hooks"]["UserPromptSubmit"][0]
    if target.exists() and target != SOURCE and not update:
        raise ValueError("Skill exists; use --update after reviewing changes")
    if plugin_path.exists() and plugin_path.read_bytes() != (SOURCE / "adapters/opencode.js").read_bytes() and not update:
        raise ValueError("Plugin exists; use --update after reviewing changes")
    if target != SOURCE:
        if SOURCE.is_relative_to(target) or target.is_relative_to(SOURCE):
            raise ValueError("Install destination and source must not be nested")
        target.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            shutil.copy2(SOURCE / name, target / name)
        for name in DIRECTORIES:
            shutil.copytree(SOURCE / name, target / name, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    if group not in groups:
        groups.append(group)
    hook_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    plugin_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE / "adapters/opencode.js", plugin_path)
    ignore = project / ".gitignore"
    content = ignore.read_text() if ignore.exists() else ""
    if "/.feedback-manager/" not in content.splitlines():
        ignore.write_text(content + ("\n" if content and not content.endswith("\n") else "") + "/.feedback-manager/\n")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    try:
        target = install(args.project, args.update)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Installation failed: {exc}\n")
    print(f"Installed: {target}\nCodex: review project and hook trust using /hooks before use.")
