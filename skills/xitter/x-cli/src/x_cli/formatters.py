"""Output formatters: human (rich), JSON, TSV/plain, markdown."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


# ---- JSON ----

def output_json(data: Any, verbose: bool = False) -> None:
    """Raw JSON to stdout."""
    if not verbose and isinstance(data, dict):
        # Strip includes/meta, just emit data
        inner = data.get("data")
        if inner is not None:
            print(json.dumps(inner, indent=2, default=str))
            return
    print(json.dumps(data, indent=2, default=str))


# ---- Plain/TSV ----

def output_plain(data: Any, verbose: bool = False) -> None:
    """TSV output for piping."""
    if isinstance(data, dict):
        inner = data.get("data")
        if inner is None:
            inner = data
        if isinstance(inner, list):
            _plain_list(inner, verbose)
        elif isinstance(inner, dict):
            _plain_dict(inner, verbose)
        else:
            print(inner)
    elif isinstance(data, list):
        _plain_list(data, verbose)
    else:
        print(data)


def _plain_dict(d: dict, verbose: bool = False) -> None:
    skip = set() if verbose else {"public_metrics", "entities", "edit_history_tweet_ids", "attachments", "referenced_tweets", "profile_image_url"}
    for k, v in d.items():
        if not verbose and k in skip:
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v, default=str)
        print(f"{k}\t{v}")


def _plain_list(items: list, verbose: bool = False) -> None:
    if not items:
        return
    if not isinstance(items[0], dict):
        for item in items:
            print(item)
        return
    # Pick columns based on verbose
    all_keys = list(items[0].keys())
    if verbose:
        keys = all_keys
    else:
        # Compact: only the most useful fields
        if "username" in items[0]:
            keys = [k for k in ["username", "name", "description"] if k in all_keys]
        else:
            keys = [k for k in ["id", "author_id", "text", "created_at"] if k in all_keys]
        if not keys:
            keys = all_keys
    print("\t".join(keys))
    for item in items:
        vals = []
        for k in keys:
            v = item.get(k, "")
            if isinstance(v, (dict, list)):
                v = json.dumps(v, default=str)
            vals.append(str(v))
        print("\t".join(vals))


# ---- Markdown ----

def output_markdown(data: Any, title: str = "", verbose: bool = False) -> None:
    """Markdown output to stdout."""
    if isinstance(data, dict):
        inner = data.get("data")
        includes = data.get("includes", {})
        meta = data.get("meta", {})
        if inner is None:
            inner = data

        if isinstance(inner, list):
            _md_list(inner, includes, title, verbose)
        elif isinstance(inner, dict):
            _md_single(inner, includes, title, verbose)
        else:
            print(str(inner))

        if verbose and meta.get("next_token"):
            print(f"\n*Next page: `--next-token {meta['next_token']}`*")
    elif isinstance(data, list):
        _md_list(data, {}, title, verbose)
    else:
        print(str(data))


def _md_single(item: dict, includes: dict, title: str = "", verbose: bool = False) -> None:
    if "username" in item:
        _md_user(item, verbose)
    else:
        _md_tweet(item, includes, title, verbose)


def _md_tweet(tweet: dict, includes: dict, title: str = "", verbose: bool = False) -> None:
    author = _resolve_author(tweet.get("author_id"), includes)
    text = tweet.get("text", "")
    tweet_id = tweet.get("id", "")

    note = tweet.get("note_tweet", {})
    if note and note.get("text"):
        text = note["text"]

    if title:
        print(f"## {title}\n")

    print(f"**{author}**")
    if verbose:
        created = tweet.get("created_at", "")
        if created:
            print(f"*{created}*")
    print(f"\n{text}\n")

    if verbose:
        metrics = tweet.get("public_metrics", {})
        if metrics:
            parts = [f"{k.replace('_count', '')}: {v}" for k, v in metrics.items()]
            print(" | ".join(parts))
            print()
    print(f"ID: `{tweet_id}`")


def _md_user(user: dict, verbose: bool = False) -> None:
    name = user.get("name", "")
    username = user.get("username", "")
    desc = user.get("description", "")

    print(f"## {name} (@{username})\n")
    if desc:
        print(f"{desc}\n")

    metrics = user.get("public_metrics", {})
    if metrics:
        parts = [f"**{k.replace('_count', '')}**: {v:,}" for k, v in metrics.items()]
        print(" | ".join(parts))
        print()

    if verbose:
        loc = user.get("location", "")
        created = user.get("created_at", "")
        if loc:
            print(f"Location: {loc}")
        if created:
            print(f"Joined: {created}")


def _md_list(items: list, includes: dict, title: str = "", verbose: bool = False) -> None:
    if not items:
        return
    if title:
        print(f"## {title}\n")
    if items and "username" in items[0]:
        _md_user_table(items, verbose)
    else:
        for i, item in enumerate(items):
            if i > 0:
                print("\n---\n")
            _md_tweet(item, includes, verbose=verbose)


def _md_user_table(users: list, verbose: bool = False) -> None:
    if verbose:
        print("| Username | Name | Followers | Description |")
        print("|----------|------|-----------|-------------|")
        for u in users:
            m = u.get("public_metrics", {})
            followers = f"{m.get('followers_count', 0):,}"
            desc = (u.get("description", "") or "")[:60].replace("|", "/").replace("\n", " ")
            print(f"| @{u.get('username', '')} | {u.get('name', '')} | {followers} | {desc} |")
    else:
        print("| Username | Name | Followers |")
        print("|----------|------|-----------|")
        for u in users:
            m = u.get("public_metrics", {})
            followers = f"{m.get('followers_count', 0):,}"
            print(f"| @{u.get('username', '')} | {u.get('name', '')} | {followers} |")


# ---- Rich (human-readable) ----

_console = Console(stderr=True)
_stdout = Console()


def output_human(data: Any, title: str = "", verbose: bool = False) -> None:
    """Pretty-print with rich."""
    if isinstance(data, dict):
        inner = data.get("data")
        includes = data.get("includes", {})
        meta = data.get("meta", {})
        if inner is None:
            inner = data

        if isinstance(inner, list):
            _human_tweet_list(inner, includes, title, verbose)
        elif isinstance(inner, dict):
            _human_single(inner, includes, title, verbose)
        else:
            _stdout.print(inner)

        if verbose and meta.get("next_token"):
            _console.print(f"[dim]Next page: --next-token {meta['next_token']}[/dim]")
    elif isinstance(data, list):
        _human_tweet_list(data, {}, title, verbose)
    else:
        _stdout.print(data)


def _resolve_author(author_id: str | None, includes: dict) -> str:
    if not author_id:
        return "?"
    users = includes.get("users", [])
    for u in users:
        if u.get("id") == author_id:
            return f"@{u.get('username', '?')}"
    return author_id


def _human_single(item: dict, includes: dict, title: str = "", verbose: bool = False) -> None:
    if "username" in item:
        _human_user(item, verbose)
    else:
        _human_tweet(item, includes, title, verbose)


def _human_tweet(tweet: dict, includes: dict, title: str = "", verbose: bool = False) -> None:
    author = _resolve_author(tweet.get("author_id"), includes)
    text = tweet.get("text", "")
    tweet_id = tweet.get("id", "")

    note = tweet.get("note_tweet", {})
    if note and note.get("text"):
        text = note["text"]

    content = f"[bold]{author}[/bold]"
    if verbose:
        created = tweet.get("created_at", "")
        content += f"  [dim]{created}[/dim]"
    content += f"\n\n{text}"

    if verbose:
        metrics = tweet.get("public_metrics", {})
        if metrics:
            parts = [f"{k.replace('_count', '').replace('_', ' ')}: {v}" for k, v in metrics.items()]
            content += f"\n\n[dim]{' | '.join(parts)}[/dim]"

    panel_title = title or f"Tweet {tweet_id}"
    _stdout.print(Panel(content, title=panel_title, border_style="blue", expand=False))


def _human_user(user: dict, verbose: bool = False) -> None:
    name = user.get("name", "")
    username = user.get("username", "")
    desc = user.get("description", "")

    metrics = user.get("public_metrics", {})
    metrics_parts = []
    if metrics:
        for k, v in metrics.items():
            label = k.replace("_count", "").replace("_", " ")
            metrics_parts.append(f"{label}: {v:,}")

    content = f"[bold]{name}[/bold] @{username}"
    if user.get("verified"):
        content += " [blue]verified[/blue]"
    if desc:
        content += f"\n{desc}"

    if verbose:
        loc = user.get("location", "")
        created = user.get("created_at", "")
        if loc:
            content += f"\n[dim]Location: {loc}[/dim]"
        if created:
            content += f"\n[dim]Joined: {created}[/dim]"

    if metrics_parts:
        content += f"\n\n{' | '.join(metrics_parts)}"

    _stdout.print(Panel(content, title=f"@{username}", border_style="green", expand=False))


def _human_tweet_list(items: list, includes: dict, title: str = "", verbose: bool = False) -> None:
    if items and "username" in items[0]:
        _human_user_table(items, title, verbose)
    else:
        for item in items:
            _human_tweet(item, includes, verbose=verbose)


def _human_user_table(users: list, title: str = "", verbose: bool = False) -> None:
    table = Table(title=title or "Users", show_lines=True)
    table.add_column("Username", style="bold")
    table.add_column("Name")
    table.add_column("Followers", justify="right")
    if verbose:
        table.add_column("Description", max_width=50)

    for u in users:
        metrics = u.get("public_metrics", {})
        followers = str(metrics.get("followers_count", ""))
        row = [
            f"@{u.get('username', '')}",
            u.get("name", ""),
            followers,
        ]
        if verbose:
            row.append((u.get("description", "") or "")[:50])
        table.add_row(*row)
    _stdout.print(table)


# ---- Router ----

def format_output(data: Any, mode: str = "human", title: str = "", verbose: bool = False) -> None:
    """Route to the appropriate formatter."""
    if mode == "json":
        output_json(data, verbose)
    elif mode == "plain":
        output_plain(data, verbose)
    elif mode == "markdown":
        output_markdown(data, title, verbose)
    else:
        output_human(data, title, verbose)
