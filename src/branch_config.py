"""Branch dictionary — maps target branch names to their metadata.

Default config is built from the ov-arizona-backend-ecuador README.
Can be overridden at runtime via BRANCH_CONFIG_PATH env var (path to a JSON file)
or via the future UI config layer (POST /config/branches).

Schema of each entry:
  {
    "label":       human-readable name shown in logs and API responses,
    "environment": deployed environment name (or null),
    "url":         environment URL (or null),
    "is_base":     true if feature/fix branches are cut from this branch,
    "role":        "base" | "integration"  — logical use of the branch
  }
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_DEFAULTS: dict[str, dict] = {
    "developer": {
        "label":       "desarrollo",
        "environment": "DEV (UAT)",
        "url":         "https://uat-oficinavirtual.zurichseguros.com.ec",
        "is_base":     False,
        "role":        "integration",
    },
    "test": {
        "label":       "pruebas",
        "environment": "Test / Preprod",
        "url":         "https://preprod-oficinavirtual.zurichseguros.com.ec",
        "is_base":     False,
        "role":        "integration",
    },
    "develop": {
        "label":       "producción (pre)",
        "environment": None,
        "url":         None,
        "is_base":     True,
        "role":        "base",
    },
    "main": {
        "label":       "producción (desplegado)",
        "environment": "Producción",
        "url":         "https://oficinavirtual.zurichseguros.com.ec",
        "is_base":     False,
        "role":        "integration",
    },
}

# Runtime override — future UI layer will write to this path
_CONFIG_PATH = Path(os.environ.get("BRANCH_CONFIG_PATH", "/data/branch_config.json"))

_registry: dict[str, dict] | None = None


def _load() -> dict[str, dict]:
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {**_DEFAULTS, **data}
        except (ValueError, OSError):
            pass
    return dict(_DEFAULTS)


def get_registry() -> dict[str, dict]:
    """Return the branch registry, loading from disk on first call."""
    global _registry
    if _registry is None:
        _registry = _load()
    return _registry


def reload() -> dict[str, dict]:
    """Force reload from disk. Called after UI writes a new config file."""
    global _registry
    _registry = _load()
    return _registry


def get(branch: str) -> dict | None:
    """Return metadata for *branch*, or None if unknown."""
    return get_registry().get(branch)


def label(branch: str) -> str:
    """Return human-readable label for *branch*, falling back to the branch name itself."""
    entry = get(branch)
    return entry["label"] if entry else branch


def base_branch() -> str:
    """Return the branch that feature/fix branches are cut from."""
    for name, meta in get_registry().items():
        if meta.get("is_base"):
            return name
    return "develop"


def role(branch: str) -> str | None:
    """Return the logical role of *branch* from the global registry, or None if unknown."""
    entry = get(branch)
    return entry.get("role") if entry else None


def known_targets() -> list[str]:
    """Return all branch names that can be used as PR targets (non-base branches)."""
    return [name for name, meta in get_registry().items() if not meta.get("is_base")]


def save(new_config: dict[str, dict]) -> None:
    """Persist a new branch config to disk (future UI layer calls this).

    Merges with defaults so unknown fields in _DEFAULTS are preserved.
    """
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = {**_DEFAULTS, **new_config}
    _CONFIG_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    reload()
