"""Tests for D3 plugin capabilities surface."""
import pytest

from hermes_cli.plugin_caps import (
    PluginCapabilities,
    check_fs_read,
    check_fs_write,
    check_host,
    check_port,
    check_subprocess,
    filter_env,
    parse_capabilities,
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class TestParseCapabilities:
    def test_none_yields_unrestricted_undeclared(self):
        caps = parse_capabilities(None)
        assert caps.declared is False
        assert caps.fs_read is None
        assert caps.fs_write is None
        assert caps.net_hosts is None
        assert caps.net_ports is None
        assert caps.subprocess is None

    def test_empty_dict_yields_declared_but_unrestricted(self):
        caps = parse_capabilities({})
        assert caps.declared is True
        assert caps.fs_read is None

    def test_full_block(self):
        caps = parse_capabilities({
            "fs_read": ["/etc/hosts"],
            "fs_write": ["~/.cache/foo/**"],
            "net": {"allow_hosts": ["*.example.com"], "allow_ports": [443]},
            "subprocess": True,
            "env": ["FOO_KEY"],
        })
        assert caps.declared is True
        assert caps.fs_read == ["/etc/hosts"]
        assert caps.fs_write[0].endswith("/.cache/foo/**")
        assert caps.net_hosts == ["*.example.com"]
        assert caps.net_ports == [443]
        assert caps.subprocess is True
        assert caps.env_allowlist == ["FOO_KEY"]

    def test_scalar_coerced_to_list(self):
        caps = parse_capabilities({"fs_read": "/single/path"})
        assert caps.fs_read == ["/single/path"]

    def test_invalid_top_level_type_raises(self):
        with pytest.raises(ValueError):
            parse_capabilities("not a dict")

    def test_invalid_net_block_raises(self):
        with pytest.raises(ValueError):
            parse_capabilities({"net": ["not", "a", "mapping"]})

    def test_invalid_port_raises(self):
        with pytest.raises(ValueError):
            parse_capabilities({"net": {"allow_ports": ["http"]}})

    def test_unknown_keys_ignored(self):
        caps = parse_capabilities({"fs_read": ["/a"], "future_field": 123})
        assert caps.fs_read == ["/a"]

    def test_home_expansion(self):
        caps = parse_capabilities({"fs_read": ["~/data"]})
        assert "~" not in caps.fs_read[0]


# ---------------------------------------------------------------------------
# Path / host / port checks
# ---------------------------------------------------------------------------

class TestPathChecks:
    def test_unrestricted_path_allowed(self):
        caps = PluginCapabilities()
        ok, _ = check_fs_read(caps, "/anywhere")
        assert ok is True

    def test_exact_path_allowed(self):
        caps = parse_capabilities({"fs_read": ["/etc/hosts"]})
        ok, reason = check_fs_read(caps, "/etc/hosts")
        assert ok is True
        assert "/etc/hosts" in reason

    def test_outside_allowlist_denied(self):
        caps = parse_capabilities({"fs_read": ["/etc/hosts"]})
        ok, _ = check_fs_read(caps, "/etc/passwd")
        assert ok is False

    def test_double_star_glob(self, tmp_path):
        caps = parse_capabilities({"fs_write": [f"{tmp_path}/**"]})
        ok, _ = check_fs_write(caps, str(tmp_path / "a" / "b" / "c.txt"))
        assert ok is True

    def test_empty_allowlist_denies_all(self):
        caps = parse_capabilities({"fs_read": []})
        ok, _ = check_fs_read(caps, "/etc/hosts")
        assert ok is False


class TestHostChecks:
    def test_unrestricted(self):
        caps = PluginCapabilities()
        ok, _ = check_host(caps, "evil.com")
        assert ok is True

    def test_exact_host(self):
        caps = parse_capabilities({"net": {"allow_hosts": ["api.example.com"]}})
        assert check_host(caps, "api.example.com")[0] is True
        assert check_host(caps, "other.example.com")[0] is False

    def test_wildcard_host(self):
        caps = parse_capabilities({"net": {"allow_hosts": ["*.internal"]}})
        assert check_host(caps, "metrics.internal")[0] is True
        assert check_host(caps, "metrics.external")[0] is False

    def test_case_insensitive(self):
        caps = parse_capabilities({"net": {"allow_hosts": ["API.example.com"]}})
        assert check_host(caps, "api.example.com")[0] is True

    def test_empty_host_denied_when_declared(self):
        caps = parse_capabilities({"net": {"allow_hosts": ["a"]}})
        assert check_host(caps, "")[0] is False


class TestPortChecks:
    def test_unrestricted(self):
        caps = PluginCapabilities()
        assert check_port(caps, 22)[0] is True

    def test_allowed_port(self):
        caps = parse_capabilities({"net": {"allow_ports": [80, 443]}})
        assert check_port(caps, 443)[0] is True
        assert check_port(caps, 22)[0] is False


class TestSubprocessCheck:
    def test_unrestricted(self):
        assert check_subprocess(PluginCapabilities())[0] is True

    def test_explicit_allow(self):
        caps = parse_capabilities({"subprocess": True})
        assert check_subprocess(caps)[0] is True

    def test_explicit_deny(self):
        caps = parse_capabilities({"subprocess": False})
        assert check_subprocess(caps)[0] is False


class TestFilterEnv:
    def test_unrestricted(self):
        caps = PluginCapabilities()
        assert filter_env(caps, {"A": "1", "B": "2"}) == {"A": "1", "B": "2"}

    def test_allowlist_filters(self):
        caps = parse_capabilities({"env": ["KEEP"]})
        out = filter_env(caps, {"KEEP": "1", "DROP": "2"})
        assert out == {"KEEP": "1"}

    def test_empty_allowlist_denies_all(self):
        caps = parse_capabilities({"env": []})
        assert filter_env(caps, {"KEEP": "1"}) == {}


# ---------------------------------------------------------------------------
# Loader integration: PluginManifest.capabilities is populated
# ---------------------------------------------------------------------------

class TestManifestParsing:
    def _write_plugin(self, tmp_path, yaml_body):
        plugin_dir = tmp_path / "plugins" / "demo"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.yaml").write_text(yaml_body)
        (plugin_dir / "__init__.py").write_text("def register(ctx): pass\n")
        return plugin_dir

    def test_no_permissions_block_yields_undeclared(self, tmp_path):
        plugin_dir = self._write_plugin(
            tmp_path, "name: demo\nkind: standalone\n",
        )
        from hermes_cli.plugins import PluginManager
        mgr = PluginManager.__new__(PluginManager)  # don't run __init__
        manifest = mgr._parse_manifest(
            plugin_dir / "plugin.yaml", plugin_dir, "test", "",
        )
        assert manifest is not None
        assert manifest.capabilities is not None
        assert manifest.capabilities.declared is False

    def test_with_permissions_block(self, tmp_path):
        plugin_dir = self._write_plugin(
            tmp_path,
            "name: demo\nkind: standalone\n"
            "permissions:\n"
            "  fs_read: ['/etc/hosts']\n"
            "  net:\n"
            "    allow_hosts: ['api.example.com']\n",
        )
        from hermes_cli.plugins import PluginManager
        mgr = PluginManager.__new__(PluginManager)
        manifest = mgr._parse_manifest(
            plugin_dir / "plugin.yaml", plugin_dir, "test", "",
        )
        assert manifest.capabilities.declared is True
        assert manifest.capabilities.fs_read == ["/etc/hosts"]
        assert manifest.capabilities.net_hosts == ["api.example.com"]

    def test_malformed_permissions_falls_back_to_unrestricted(self, tmp_path, caplog):
        plugin_dir = self._write_plugin(
            tmp_path,
            "name: demo\nkind: standalone\n"
            "permissions:\n"
            "  net: ['not', 'a', 'mapping']\n",
        )
        from hermes_cli.plugins import PluginManager
        mgr = PluginManager.__new__(PluginManager)
        manifest = mgr._parse_manifest(
            plugin_dir / "plugin.yaml", plugin_dir, "test", "",
        )
        # Bad block → fallback to undeclared/unrestricted, not None.
        assert manifest is not None
        assert manifest.capabilities.declared is False
        # And we logged a warning.
        assert any("invalid permissions" in r.message for r in caplog.records)
