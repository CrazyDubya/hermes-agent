"""Repo-aware agent boot (D4).

Walks upward from the current working directory looking for a
``.hermes/agents.yaml`` file. When found, its contents are merged into
the runtime config between the user-level ``~/.hermes/config.yaml``
and any CLI flag overrides, so:

    defaults  <  ~/.hermes/config.yaml  <  .hermes/agents.yaml  <  CLI flags

The file is YAML and uses the same key shape as the user config. Common
fields:

    default_model: claude-opus-4-7
    agent:
      max_turns: 100
    skills:
      enabled: [github, productivity]
      disabled: [creative]
    persona: senior-engineer

A missing file is silently ignored. A malformed file logs a warning and
falls back to no repo override (never blocks startup).

The discovery uses an ancestor walk (cwd → / ) so running `hermes` from
any subdirectory of the repo picks the same file. Discovery stops at
the first match; sibling repo configs are not merged.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REPO_CONFIG_DIRNAME = ".hermes"
REPO_CONFIG_FILENAME = "agents.yaml"
# Also accept agents.yml — many editors default to .yml for new files.
_REPO_CONFIG_FILENAMES = ("agents.yaml", "agents.yml")
# Safety: never walk beyond this many ancestors. Prevents pathological
# walks on broken filesystems or symlink cycles.
_MAX_ANCESTORS = 64


def find_repo_config(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from *start* (or cwd) looking for .hermes/agents.yaml.

    Returns the first matching path, or None. The user's home directory
    is **never** treated as a repo root — if you want repo-style config
    in ~, set it in ~/.hermes/config.yaml directly.
    """
    if start is None:
        try:
            start = Path(os.getcwd())
        except (FileNotFoundError, OSError):
            return None
    try:
        cur = Path(start).resolve()
    except (OSError, RuntimeError):
        return None

    home = None
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        pass

    for _ in range(_MAX_ANCESTORS):
        # Skip the home directory itself — user-level config lives in
        # ~/.hermes/config.yaml, not ~/.hermes/agents.yaml. Treating
        # home as a "repo" would surprise users who keep dotfiles there.
        if home is not None and cur == home:
            return None
        repo_dir = cur / REPO_CONFIG_DIRNAME
        if repo_dir.is_dir():
            for name in _REPO_CONFIG_FILENAMES:
                candidate = repo_dir / name
                if candidate.is_file():
                    return candidate
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def load_repo_config(start: Optional[Path] = None) -> tuple[dict, Optional[Path]]:
    """Return (config_dict, source_path).

    Returns ({}, None) when no file is found or the file is unreadable.
    A malformed file logs a warning and returns ({}, path) so callers
    can surface "we found it but couldn't parse it" in /whoami.
    """
    path = find_repo_config(start)
    if path is None:
        return {}, None

    try:
        import yaml  # imported lazily; pyyaml is a runtime dep already
    except Exception:  # pragma: no cover — pyyaml is shipped with hermes
        logger.debug("repo_config: pyyaml unavailable, skipping %s", path)
        return {}, path

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("repo_config: failed to parse %s: %s", path, exc)
        return {}, path

    if not isinstance(data, dict):
        logger.warning(
            "repo_config: %s top-level must be a mapping, got %s — ignored",
            path, type(data).__name__,
        )
        return {}, path
    return data, path
