"""Eval corpus loader (D7).

A corpus is a directory of YAML files; each file may contain one case
(top-level mapping) or many (top-level list of mappings). A case has:

    id:          short stable identifier (filename + index if omitted)
    prompt:      string sent to the agent
    expect:      one or more judge specs (see agent.eval.judges)
    skills:      optional list of skills to load
    model:       optional per-case model override
    tags:        optional list of strings (for filtering / reporting)

Anything else is preserved untouched in Case.extra for forward-compat.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Optional


@dataclass
class Case:
    id: str
    prompt: str
    expect: List[dict] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    model: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    source: Optional[Path] = None
    extra: dict = field(default_factory=dict)


def _coerce_expect(raw: Any) -> List[dict]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        out: List[dict] = []
        for item in raw:
            if isinstance(item, dict):
                out.append(item)
            elif isinstance(item, str):
                # Shorthand: bare string → contains-substring judge
                out.append({"kind": "contains", "value": item})
            else:
                raise ValueError(
                    f"expect entry must be a mapping or string, got {type(item).__name__}"
                )
        return out
    if isinstance(raw, str):
        return [{"kind": "contains", "value": raw}]
    raise ValueError(f"expect must be mapping/list/string, got {type(raw).__name__}")


def _case_from_dict(data: dict, *, source: Path, default_id: str) -> Case:
    if not isinstance(data, dict):
        raise ValueError(f"case at {source} must be a mapping")
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"case at {source} requires a non-empty 'prompt'")
    cid = str(data.get("id") or default_id).strip()
    expect = _coerce_expect(data.get("expect"))
    skills_raw = data.get("skills") or []
    if isinstance(skills_raw, str):
        skills = [skills_raw]
    elif isinstance(skills_raw, list):
        skills = [str(s).strip() for s in skills_raw if str(s).strip()]
    else:
        raise ValueError("skills must be a string or list")
    tags_raw = data.get("tags") or []
    if isinstance(tags_raw, str):
        tags = [tags_raw]
    elif isinstance(tags_raw, list):
        tags = [str(t).strip() for t in tags_raw if str(t).strip()]
    else:
        raise ValueError("tags must be a string or list")
    model = data.get("model")
    extra = {
        k: v for k, v in data.items()
        if k not in {"id", "prompt", "expect", "skills", "tags", "model"}
    }
    return Case(
        id=cid, prompt=prompt, expect=expect, skills=skills,
        model=str(model) if model else None, tags=tags,
        source=source, extra=extra,
    )


def _iter_corpus_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    if not root.is_dir():
        raise FileNotFoundError(f"corpus path does not exist: {root}")
    for ext in ("*.yaml", "*.yml"):
        yield from sorted(root.rglob(ext))


def load_corpus(root: Path) -> List[Case]:
    """Load every YAML case-file under *root* and return a list of Case.

    Order is stable: files sorted alphabetically by path, multi-case
    files preserve declared order.
    """
    import yaml  # runtime dep
    root = Path(root)
    cases: List[Case] = []
    for fpath in _iter_corpus_files(root):
        try:
            with fpath.open("r", encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ValueError(f"corpus file {fpath} is not valid YAML: {exc}")
        if doc is None:
            continue
        if isinstance(doc, dict):
            cases.append(_case_from_dict(
                doc, source=fpath, default_id=fpath.stem,
            ))
        elif isinstance(doc, list):
            for i, item in enumerate(doc):
                cases.append(_case_from_dict(
                    item, source=fpath, default_id=f"{fpath.stem}#{i}",
                ))
        else:
            raise ValueError(
                f"corpus file {fpath} top-level must be a mapping or list, "
                f"got {type(doc).__name__}"
            )
    return cases
