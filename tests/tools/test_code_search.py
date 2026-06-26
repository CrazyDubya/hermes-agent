"""Tests for tools.code_search_tool (D9)."""
import os
import time
from pathlib import Path

import pytest

from tools.code_search_tool import CodeIndex, extract_python


SAMPLE = '''\
"""Module docstring."""
import os
from typing import List

CONFIG = {"x": 1}

def public_fn(a, b):
    """Top-level function."""
    return a + b

async def async_helper():
    pass

class Widget:
    """A widget."""

    CLASS_CONST = 10

    def __init__(self, name):
        self.name = name

    def render(self):
        return self.name

    class Inner:
        def deep(self):
            return 1
'''


# ---------------------------------------------------------------------------
# extract_python
# ---------------------------------------------------------------------------

class TestExtractPython:
    def test_finds_top_level_function(self):
        syms = extract_python(SAMPLE)
        names = {s.name for s in syms if s.kind == "function"}
        assert "public_fn" in names
        assert "async_helper" in names

    def test_async_function_kind(self):
        syms = extract_python(SAMPLE)
        async_helper = next(s for s in syms if s.name == "async_helper")
        assert async_helper.kind == "function"
        assert "async def" in (async_helper.signature or "")

    def test_finds_class(self):
        syms = extract_python(SAMPLE)
        widgets = [s for s in syms if s.name == "Widget"]
        assert len(widgets) == 1
        assert widgets[0].kind == "class"

    def test_methods_have_parent(self):
        syms = extract_python(SAMPLE)
        render = next(s for s in syms if s.name == "render")
        assert render.kind == "method"
        assert render.parent == "Widget"

    def test_nested_class_methods_carry_qualified_parent(self):
        syms = extract_python(SAMPLE)
        deep = next(s for s in syms if s.name == "deep")
        assert deep.parent == "Widget.Inner"

    def test_top_level_assignments_indexed(self):
        syms = extract_python(SAMPLE)
        assert any(s.name == "CONFIG" and s.kind == "variable" for s in syms)

    def test_imports_indexed(self):
        syms = extract_python(SAMPLE)
        kinds = {(s.name, s.kind) for s in syms}
        assert ("os", "import") in kinds
        assert ("List", "import") in kinds

    def test_syntax_error_returns_empty(self):
        syms = extract_python("def boom(:\n")
        assert syms == []

    def test_signature_includes_args(self):
        syms = extract_python(SAMPLE)
        public = next(s for s in syms if s.name == "public_fn")
        assert "a, b" in (public.signature or "")


# ---------------------------------------------------------------------------
# CodeIndex
# ---------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "foo.py").write_text(SAMPLE)
    (root / "bar.py").write_text(
        "def other_fn():\n    pass\n\nclass Other:\n    pass\n"
    )
    (root / "README.md").write_text("# not python")
    sub = root / "pkg"
    sub.mkdir()
    (sub / "deep.py").write_text("def deep_fn():\n    return 'd'\n")
    # Directory that should be excluded.
    venv = root / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "ghost.py").write_text("def ghost():\n    return 1\n")
    return root


@pytest.fixture
def idx(tmp_path, repo):
    db_path = tmp_path / "index.db"
    return CodeIndex(db_path=db_path, repo_root=repo)


class TestIndexing:
    def test_indexes_repo(self, idx):
        stats = idx.index()
        assert stats.indexed == 3
        assert stats.symbols > 0
        assert stats.errors == 0

    def test_skips_excluded_dirs(self, idx, repo):
        idx.index()
        # ghost should not be indexed (inside .venv/).
        hits = idx.search("ghost")
        assert hits == []

    def test_skips_non_python(self, idx):
        idx.index()
        files = idx._conn.execute("SELECT path FROM files").fetchall()
        paths = [f["path"] for f in files]
        assert all(p.endswith(".py") for p in paths)

    def test_incremental_skips_unchanged(self, idx):
        idx.index()
        stats2 = idx.index()
        assert stats2.indexed == 0
        assert stats2.skipped == 3

    def test_reindexes_when_mtime_changes(self, idx, repo):
        idx.index()
        time.sleep(0.05)
        # Modify foo.py
        (repo / "foo.py").write_text(SAMPLE + "\ndef brand_new():\n    pass\n")
        stats2 = idx.index()
        assert stats2.indexed == 1
        assert stats2.skipped == 2
        hits = idx.search("brand_new")
        assert len(hits) == 1

    def test_removes_deleted_files(self, idx, repo):
        idx.index()
        (repo / "bar.py").unlink()
        stats2 = idx.index()
        assert stats2.removed == 1
        # And the symbols are gone.
        hits = idx.search("other_fn")
        assert hits == []


class TestSearch:
    def test_substring(self, idx):
        idx.index()
        hits = idx.search("widget", kind=None)
        assert len(hits) >= 1
        assert any(h["name"] == "Widget" for h in hits)

    def test_kind_filter(self, idx):
        idx.index()
        functions = idx.search("fn", kind="function")
        for h in functions:
            assert h["kind"] == "function"

    def test_exact(self, idx):
        idx.index()
        hits = idx.search("Widget", exact=True)
        assert len(hits) == 1

    def test_empty_query_returns_empty(self, idx):
        idx.index()
        assert idx.search("") == []

    def test_limit(self, idx):
        idx.index()
        hits = idx.search("e", limit=3)
        assert len(hits) <= 3


class TestFindDefinition:
    def test_finds_function(self, idx):
        idx.index()
        defs = idx.find_definition("public_fn")
        assert len(defs) == 1
        assert defs[0]["kind"] == "function"

    def test_finds_method(self, idx):
        idx.index()
        defs = idx.find_definition("render")
        assert len(defs) == 1
        assert defs[0]["kind"] == "method"
        assert defs[0]["parent"] == "Widget"

    def test_ignores_variables_imports(self, idx):
        idx.index()
        defs = idx.find_definition("CONFIG")
        assert defs == []  # variable, not a definition


class TestFindReferences:
    def test_returns_grep_hits(self, idx):
        idx.index()
        refs = idx.find_references("Widget")
        # Class def line + any usages — at minimum the class definition.
        assert len(refs) >= 1
        assert all("Widget" in r["snippet"] for r in refs)


class TestStats:
    def test_returns_counts(self, idx):
        idx.index()
        s = idx.stats()
        assert s["files"] == 3
        assert s["symbols"] > 0
        # Function count breakdown.
        assert s.get("kind_function", 0) >= 1
        assert s.get("kind_class", 0) >= 1
