"""Local code-search tool (D9).

Builds an incremental symbol index across a repo and exposes a small
query API. The index is a single SQLite database at a caller-chosen
path (defaulting to ``~/.hermes/state/code_index.db``).

This first slice handles Python via the stdlib ``ast`` module — no
new dependency, real symbol awareness (functions, classes, methods,
top-level assignments). Other languages slot in as ``Extractor``
implementations registered in ``_EXTRACTORS``; adding tree-sitter
later is a one-class plug-in.

Surfaces:

    idx = CodeIndex(db_path, repo_root)
    stats = idx.index()                           # incremental
    hits = idx.search("propose_times", kind="symbol")
    defs = idx.find_definition("propose_times")
    refs = idx.find_references("propose_times")   # text-based fallback
"""
from __future__ import annotations

import ast
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    language TEXT NOT NULL,
    indexed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,      -- function, class, method, variable, import
    lineno INTEGER NOT NULL,
    end_lineno INTEGER,
    parent TEXT,             -- qualified parent (for methods)
    signature TEXT,
    FOREIGN KEY (path) REFERENCES files(path) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);
CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path);
"""


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

@dataclass
class Symbol:
    name: str
    kind: str
    lineno: int
    end_lineno: Optional[int] = None
    parent: Optional[str] = None
    signature: Optional[str] = None


def _python_signature(node: ast.AST) -> Optional[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        try:
            args = ast.unparse(node.args)
        except Exception:
            args = ""
        prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
        return f"{prefix}{node.name}({args})"
    if isinstance(node, ast.ClassDef):
        bases = []
        for b in node.bases:
            try:
                bases.append(ast.unparse(b))
            except Exception:
                bases.append("?")
        return f"class {node.name}" + (f"({', '.join(bases)})" if bases else "")
    return None


def extract_python(source: str) -> List[Symbol]:
    """AST-driven symbol extraction for a Python source string."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        logger.debug("code_search: skipping unparseable file: %s", exc)
        return []
    syms: List[Symbol] = []

    def visit(node: ast.AST, parent: Optional[str] = None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "method" if parent else "function"
                syms.append(Symbol(
                    name=child.name, kind=kind,
                    lineno=child.lineno,
                    end_lineno=getattr(child, "end_lineno", None),
                    parent=parent,
                    signature=_python_signature(child),
                ))
                # Recurse so nested defs are captured.
                visit(child, parent=(f"{parent}.{child.name}" if parent else child.name))
            elif isinstance(child, ast.ClassDef):
                syms.append(Symbol(
                    name=child.name, kind="class",
                    lineno=child.lineno,
                    end_lineno=getattr(child, "end_lineno", None),
                    parent=parent,
                    signature=_python_signature(child),
                ))
                visit(child, parent=(f"{parent}.{child.name}" if parent else child.name))
            elif isinstance(child, ast.Assign) and parent is None:
                # Top-level assignments only — module-level constants/state.
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        syms.append(Symbol(
                            name=target.id, kind="variable",
                            lineno=target.lineno,
                        ))
            elif isinstance(child, (ast.Import, ast.ImportFrom)) and parent is None:
                for alias in child.names:
                    syms.append(Symbol(
                        name=alias.asname or alias.name,
                        kind="import",
                        lineno=child.lineno,
                    ))

    visit(tree)
    return syms


# Map file extension → (language, extractor). Easy plug-in shape.
ExtractorFn = Callable[[str], List[Symbol]]
_EXTRACTORS: Dict[str, Tuple[str, ExtractorFn]] = {
    ".py": ("python", extract_python),
}


def register_extractor(extension: str, language: str, fn: ExtractorFn) -> None:
    """Register a language extractor for a file extension (lowercase, w/ leading dot)."""
    if not extension.startswith("."):
        extension = "." + extension
    _EXTRACTORS[extension.lower()] = (language, fn)


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

_EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
    "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".pytest-cache",
}

_DEFAULT_DB_PATH = Path.home() / ".hermes" / "state" / "code_index.db"


@dataclass
class IndexStats:
    indexed: int = 0
    skipped: int = 0
    removed: int = 0
    errors: int = 0
    symbols: int = 0


class CodeIndex:
    """SQLite-backed incremental symbol index."""

    def __init__(self, db_path: Optional[Path] = None, repo_root: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.repo_root = Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # -- indexing ---------------------------------------------------------

    def _iter_source_files(self) -> Iterable[Path]:
        for dirpath, dirnames, filenames in os.walk(self.repo_root):
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS and not d.startswith(".")]
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in _EXTRACTORS:
                    yield Path(dirpath) / fname

    def _file_needs_reindex(self, path: str, mtime: float, size: int) -> bool:
        row = self._conn.execute(
            "SELECT mtime, size FROM files WHERE path = ?", (path,),
        ).fetchone()
        if row is None:
            return True
        return abs(row["mtime"] - mtime) > 1e-6 or row["size"] != size

    def index(self) -> IndexStats:
        """Run an incremental index pass. Returns IndexStats."""
        import time

        stats = IndexStats()
        seen: set[str] = set()
        for fpath in self._iter_source_files():
            try:
                st = fpath.stat()
            except OSError:
                stats.errors += 1
                continue
            path_str = str(fpath)
            seen.add(path_str)
            if not self._file_needs_reindex(path_str, st.st_mtime, st.st_size):
                stats.skipped += 1
                continue
            ext = fpath.suffix.lower()
            language, extractor = _EXTRACTORS[ext]
            try:
                source = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                stats.errors += 1
                continue
            syms = extractor(source)
            now = time.time()

            cur = self._conn.cursor()
            cur.execute("DELETE FROM symbols WHERE path = ?", (path_str,))
            cur.executemany(
                "INSERT INTO symbols (path, name, kind, lineno, end_lineno, parent, signature) "
                "VALUES (?,?,?,?,?,?,?)",
                [
                    (path_str, s.name, s.kind, s.lineno, s.end_lineno, s.parent, s.signature)
                    for s in syms
                ],
            )
            cur.execute(
                "INSERT OR REPLACE INTO files (path, mtime, size, language, indexed_at) "
                "VALUES (?,?,?,?,?)",
                (path_str, st.st_mtime, st.st_size, language, now),
            )
            stats.indexed += 1
            stats.symbols += len(syms)

        # Drop rows for files that no longer exist (or are outside the
        # repo_root being indexed in this pass).
        rows = self._conn.execute(
            "SELECT path FROM files WHERE path LIKE ?", (f"{self.repo_root}%",),
        ).fetchall()
        for r in rows:
            if r["path"] not in seen:
                self._conn.execute("DELETE FROM symbols WHERE path = ?", (r["path"],))
                self._conn.execute("DELETE FROM files WHERE path = ?", (r["path"],))
                stats.removed += 1

        self._conn.commit()
        return stats

    # -- query ------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        kind: Optional[str] = None,
        limit: int = 50,
        exact: bool = False,
    ) -> List[Dict[str, Any]]:
        """Find symbols matching *query*. Default: substring match on name."""
        if not query:
            return []
        params: List[Any] = []
        if exact:
            sql = "SELECT * FROM symbols WHERE name = ?"
            params.append(query)
        else:
            sql = "SELECT * FROM symbols WHERE name LIKE ?"
            params.append(f"%{query}%")
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY name, path, lineno LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def find_definition(self, name: str, *, limit: int = 10) -> List[Dict[str, Any]]:
        """Find definitions (function/class/method) with the exact name."""
        rows = self._conn.execute(
            "SELECT * FROM symbols WHERE name = ? AND kind IN ('function','class','method') "
            "ORDER BY path, lineno LIMIT ?",
            (name, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def find_references(self, name: str, *, limit: int = 100) -> List[Dict[str, Any]]:
        """Text-grep fallback for references — scans indexed files for *name*.

        AST-level reference resolution is the natural next step (and the
        tree-sitter integration's main payoff); for now this gives a
        useful "where is X used" answer without a heavy dep.
        """
        rows = self._conn.execute(
            "SELECT path FROM files ORDER BY path"
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                with open(r["path"], "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, start=1):
                        if name in line:
                            out.append({
                                "path": r["path"], "lineno": i,
                                "snippet": line.rstrip()[:200],
                            })
                            if len(out) >= limit:
                                return out
            except OSError:
                continue
        return out

    def stats(self) -> Dict[str, int]:
        """Return rough counts (files, symbols by kind)."""
        files = self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        total_syms = self._conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        by_kind = {
            r["kind"]: r["count"]
            for r in self._conn.execute(
                "SELECT kind, COUNT(*) AS count FROM symbols GROUP BY kind"
            )
        }
        return {"files": files, "symbols": total_syms, **{f"kind_{k}": v for k, v in by_kind.items()}}
