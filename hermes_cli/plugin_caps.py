"""Plugin capabilities (D3).

Plugins ship a ``plugin.yaml`` manifest. Hermes already trusts that file
for ``provides_tools`` / ``requires_env``; this module adds an optional
``permissions:`` block that declares what the plugin is allowed to do at
runtime:

    permissions:
      fs_read:   ["~/.hermes/state.db", "/etc/hosts"]
      fs_write:  ["~/.hermes/plugins/foo/cache/**"]
      net:
        allow_hosts: ["api.example.com", "*.internal"]
        allow_ports: [80, 443]
      subprocess: false
      env: ["FOO_API_KEY"]

The defaults are deliberately permissive — omitting any field means
"unrestricted, behave as before". Once a plugin declares any field the
corresponding gate is enforced (fail-closed for paths/hosts/ports that
aren't listed). This keeps existing plugins working untouched while
giving operators a declarative surface to tighten things up.

Glob style follows ``fnmatch`` (the same shape used in
``tools/url_safety.py`` and the MCP target-ACL feature on the security
branch). Paths are expanded (``~`` + relative → absolute) before
matching so glob authors don't need to know the user's HOME.

This slice ships the declarative + check surface only. The wiring of
each check into ``tools/path_security.py`` / ``tools/url_safety.py`` /
subprocess call sites is a follow-up so the diff stays reviewable.
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple


@dataclass
class PluginCapabilities:
    """Declared capabilities for a plugin.

    Each list is ``None`` when the field is *unset* (no restriction) and
    a (possibly empty) list when *declared* (enforced). Empty list with
    declared=True means "deny-all" for that gate.
    """
    fs_read: Optional[List[str]] = None
    fs_write: Optional[List[str]] = None
    net_hosts: Optional[List[str]] = None
    net_ports: Optional[List[int]] = None
    subprocess: Optional[bool] = None        # None = unrestricted; bool = explicit
    env_allowlist: Optional[List[str]] = None
    declared: bool = False  # True when the manifest had a `permissions:` block

    def __post_init__(self):
        # Normalise glob strings (expand ~, no os.sep coercion — fnmatch is
        # platform-neutral but the *patterns* benefit from a stable form).
        self.fs_read = self._normalise_paths(self.fs_read)
        self.fs_write = self._normalise_paths(self.fs_write)

    @staticmethod
    def _normalise_paths(items: Optional[List[str]]) -> Optional[List[str]]:
        if items is None:
            return None
        out: List[str] = []
        for s in items:
            if not isinstance(s, str):
                continue
            expanded = os.path.expanduser(s.strip())
            if expanded:
                out.append(expanded)
        return out


def _to_str_list(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    raise ValueError(f"expected string or list, got {type(value).__name__}")


def _to_int_list(value: Any) -> Optional[List[int]]:
    if value is None:
        return None
    if isinstance(value, int):
        return [value]
    if isinstance(value, (list, tuple)):
        out: List[int] = []
        for x in value:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                raise ValueError(f"port must be int, got {x!r}")
        return out
    raise ValueError(f"expected int or list of ints, got {type(value).__name__}")


def parse_capabilities(raw: Any) -> PluginCapabilities:
    """Parse the ``permissions:`` block from a plugin.yaml.

    Accepts ``None`` (no declared capabilities), an empty dict (declared
    but every gate unrestricted), or a populated dict. Unknown keys are
    ignored so future fields land additively.
    """
    if raw is None:
        return PluginCapabilities()
    if not isinstance(raw, dict):
        raise ValueError(
            f"permissions: must be a mapping, got {type(raw).__name__}"
        )

    fs_read = _to_str_list(raw.get("fs_read"))
    fs_write = _to_str_list(raw.get("fs_write"))

    net_hosts = None
    net_ports = None
    net_raw = raw.get("net")
    if isinstance(net_raw, dict):
        net_hosts = _to_str_list(net_raw.get("allow_hosts"))
        net_ports = _to_int_list(net_raw.get("allow_ports"))
    elif net_raw is not None:
        raise ValueError("permissions.net must be a mapping or unset")

    subprocess_raw = raw.get("subprocess")
    if subprocess_raw is None:
        subprocess_flag: Optional[bool] = None
    else:
        subprocess_flag = bool(subprocess_raw)

    env_allowlist = _to_str_list(raw.get("env"))

    return PluginCapabilities(
        fs_read=fs_read,
        fs_write=fs_write,
        net_hosts=net_hosts,
        net_ports=net_ports,
        subprocess=subprocess_flag,
        env_allowlist=env_allowlist,
        declared=True,
    )


# ---------------------------------------------------------------------------
# Check helpers — each returns (allowed: bool, reason: str). Use ``reason``
# in logs / errors so operators can see *which* declared rule fired.
# ---------------------------------------------------------------------------

def _match_path(target: str, patterns: List[str]) -> Optional[str]:
    """Return the first matching pattern, or None."""
    if not patterns:
        return None
    # Use realpath so symlink games don't trivially defeat the gate.
    try:
        resolved = str(Path(target).expanduser().resolve(strict=False))
    except (OSError, RuntimeError):
        resolved = os.path.expanduser(target)
    for pat in patterns:
        # fnmatch handles ``*`` and ``?``; we extend with ``**`` for any
        # subdirectory depth, matching what cron's workdir patterns use.
        if _glob_match(resolved, pat):
            return pat
    return None


def _glob_match(path: str, pattern: str) -> bool:
    """``**`` matches any number of path segments; ``*`` is single-segment."""
    if "**" not in pattern:
        # fnmatch ``*`` matches *anything including slashes* by default,
        # which is the behaviour we want for short patterns.
        return fnmatch.fnmatchcase(path, pattern)
    # Translate ``**`` to a fnmatch-compatible ``*`` and let the matcher
    # consume slashes — equivalent for our needs and avoids a regex dep.
    translated = pattern.replace("**", "*")
    return fnmatch.fnmatchcase(path, translated)


def check_fs_read(caps: PluginCapabilities, path: str) -> Tuple[bool, str]:
    if caps.fs_read is None:
        return True, "fs_read: unrestricted"
    matched = _match_path(path, caps.fs_read)
    if matched:
        return True, f"fs_read: matched {matched!r}"
    return False, f"fs_read: {path!r} not in allowlist"


def check_fs_write(caps: PluginCapabilities, path: str) -> Tuple[bool, str]:
    if caps.fs_write is None:
        return True, "fs_write: unrestricted"
    matched = _match_path(path, caps.fs_write)
    if matched:
        return True, f"fs_write: matched {matched!r}"
    return False, f"fs_write: {path!r} not in allowlist"


def check_host(caps: PluginCapabilities, host: str) -> Tuple[bool, str]:
    if caps.net_hosts is None:
        return True, "net.allow_hosts: unrestricted"
    if not host:
        return False, "net.allow_hosts: empty host"
    host = host.lower()
    for pat in caps.net_hosts:
        if fnmatch.fnmatchcase(host, pat.lower()):
            return True, f"net.allow_hosts: matched {pat!r}"
    return False, f"net.allow_hosts: {host!r} not in allowlist"


def check_port(caps: PluginCapabilities, port: int) -> Tuple[bool, str]:
    if caps.net_ports is None:
        return True, "net.allow_ports: unrestricted"
    if int(port) in caps.net_ports:
        return True, f"net.allow_ports: {port} allowed"
    return False, f"net.allow_ports: {port} not in allowlist"


def check_subprocess(caps: PluginCapabilities) -> Tuple[bool, str]:
    if caps.subprocess is None:
        return True, "subprocess: unrestricted"
    if caps.subprocess:
        return True, "subprocess: explicitly allowed"
    return False, "subprocess: denied by plugin.yaml"


def filter_env(caps: PluginCapabilities, env: dict) -> dict:
    """Return *env* filtered to only the keys the plugin declared.

    Unset env_allowlist → no filtering. Empty list → deny-all (empty dict).
    """
    if caps.env_allowlist is None:
        return dict(env)
    allowed = set(caps.env_allowlist)
    return {k: v for k, v in env.items() if k in allowed}
