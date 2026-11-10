"""User-owned TOML policy; profile selection belongs to the launching host."""
import os
from pathlib import Path
import tomllib


def load_policy(project):
    configured = os.environ.get("FM_CONFIG")
    path = Path(configured) if configured else project / "feedback-manager.toml"
    if configured and not path.is_absolute():
        raise ValueError("FM_CONFIG must be an absolute path.")
    if configured or path.exists():
        with path.open("rb") as stream:
            config = tomllib.load(stream)
    else:
        config = {}
    if set(config) - {"default_mode", "allow_update", "allow_delete", "profiles"}:
        raise ValueError("Unsupported configuration field.")
    policy = {"mode": config.get("default_mode", "ask"),
              "allow_update": config.get("allow_update", True),
              "allow_delete": config.get("allow_delete", False)}
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("profiles must be a table.")
    # Validate inactive profiles too, so a typo never silently weakens a policy.
    for profile in profiles.values():
        if not isinstance(profile, dict) or set(profile) - set(policy):
            raise ValueError("Invalid profile fields.")
        validate({**policy, **profile})
    validate(policy)
    selected = os.environ.get("FM_PROFILE")
    if selected:
        if selected not in profiles:
            raise ValueError("FM_PROFILE is not configured.")
        policy.update(profiles[selected])
    return {**policy, "profile": selected, "config": str(path)}


def validate(policy):
    if policy["mode"] not in ("off", "read_only", "ask", "auto"):
        raise ValueError("mode must be off, read_only, ask or auto.")
    if any(type(policy[key]) is not bool for key in ("allow_update", "allow_delete")):
        raise ValueError("Permissions must be TOML booleans.")
