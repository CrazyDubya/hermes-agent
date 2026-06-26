"""Tests for `hermes_cli.repo_config` (D4)."""
import os
from pathlib import Path

import pytest

from hermes_cli import repo_config


def _mk_repo(root: Path, content: str = "default_model: opus\n") -> Path:
    cfg_dir = root / ".hermes"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "agents.yaml"
    path.write_text(content)
    return path


class TestFindRepoConfig:
    def test_finds_file_in_cwd(self, tmp_path, monkeypatch):
        _mk_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        found = repo_config.find_repo_config()
        assert found is not None
        assert found.name == "agents.yaml"

    def test_finds_in_ancestor(self, tmp_path, monkeypatch):
        _mk_repo(tmp_path)
        sub = tmp_path / "a" / "b" / "c"
        sub.mkdir(parents=True)
        monkeypatch.chdir(sub)
        found = repo_config.find_repo_config()
        assert found is not None
        assert found.parent.parent == tmp_path

    def test_returns_none_when_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Ensure no parent above tmp_path has a .hermes/agents.yaml.
        # On the typical test machine /home/opc/.hermes/ exists but we
        # explicitly start above any home directory.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        found = repo_config.find_repo_config(start=tmp_path)
        assert found is None

    def test_accepts_yml_extension(self, tmp_path, monkeypatch):
        cfg_dir = tmp_path / ".hermes"
        cfg_dir.mkdir()
        (cfg_dir / "agents.yml").write_text("default_model: m\n")
        monkeypatch.chdir(tmp_path)
        found = repo_config.find_repo_config()
        assert found is not None
        assert found.name == "agents.yml"

    def test_stops_at_home_dir(self, tmp_path, monkeypatch):
        # Simulate $HOME containing a .hermes/agents.yaml — must not be picked.
        fake_home = tmp_path / "home" / "user"
        fake_home.mkdir(parents=True)
        _mk_repo(fake_home)
        sub = fake_home / "project"
        sub.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        found = repo_config.find_repo_config(start=sub)
        assert found is None  # walked up to home, stopped before reading


class TestLoadRepoConfig:
    def test_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        data, path = repo_config.load_repo_config(start=tmp_path)
        assert data == {}
        assert path is None

    def test_returns_parsed_yaml(self, tmp_path, monkeypatch):
        _mk_repo(tmp_path, "default_model: opus-x\nagent:\n  max_turns: 42\n")
        monkeypatch.chdir(tmp_path)
        data, path = repo_config.load_repo_config()
        assert data["default_model"] == "opus-x"
        assert data["agent"]["max_turns"] == 42
        assert path is not None

    def test_malformed_yaml_returns_empty_dict_with_path(self, tmp_path, monkeypatch):
        _mk_repo(tmp_path, "default_model: : not yaml\n  - oops\n")
        monkeypatch.chdir(tmp_path)
        data, path = repo_config.load_repo_config()
        assert data == {}
        # We still report the path so /whoami can say "found but couldn't parse".
        assert path is not None
        assert path.name == "agents.yaml"

    def test_non_mapping_top_level_returns_empty(self, tmp_path, monkeypatch):
        _mk_repo(tmp_path, "- just\n- a\n- list\n")
        monkeypatch.chdir(tmp_path)
        data, path = repo_config.load_repo_config()
        assert data == {}
        assert path is not None


class TestConfigIntegration:
    """Repo config merges into load_config() with precedence above user file."""

    def test_repo_overrides_default_when_user_silent(self, tmp_path, monkeypatch):
        # Set up a repo with .hermes/agents.yaml
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _mk_repo(repo_root, "default_model: from-repo\n")
        monkeypatch.chdir(repo_root)
        # Point HERMES_HOME elsewhere so user config doesn't interfere.
        empty_home = tmp_path / "home"
        empty_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(empty_home))
        # Clear the load_config cache so the new HERMES_HOME path is honoured.
        from hermes_cli import config as cfg_mod
        cfg_mod._LOAD_CONFIG_CACHE.clear()
        cfg = cfg_mod.load_config_readonly()
        assert cfg.get("default_model") == "from-repo"

    def test_repo_overrides_user_config(self, tmp_path, monkeypatch):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _mk_repo(repo_root, "default_model: from-repo\n")
        monkeypatch.chdir(repo_root)
        home = tmp_path / "home"
        home.mkdir()
        (home / "config.yaml").write_text("default_model: from-user\n")
        monkeypatch.setenv("HERMES_HOME", str(home))
        from hermes_cli import config as cfg_mod
        cfg_mod._LOAD_CONFIG_CACHE.clear()
        cfg = cfg_mod.load_config_readonly()
        assert cfg.get("default_model") == "from-repo"

    def test_no_repo_config_falls_back_to_user(self, tmp_path, monkeypatch):
        # No .hermes/agents.yaml anywhere — should use the user config.
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        monkeypatch.chdir(repo_root)
        home = tmp_path / "home"
        home.mkdir()
        (home / "config.yaml").write_text("default_model: from-user\n")
        monkeypatch.setenv("HERMES_HOME", str(home))
        # Pin Path.home() so the ancestor walk stops before any real $HOME.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from hermes_cli import config as cfg_mod
        cfg_mod._LOAD_CONFIG_CACHE.clear()
        cfg = cfg_mod.load_config_readonly()
        assert cfg.get("default_model") == "from-user"
