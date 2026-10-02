"""Utility helpers for x-cli."""

from __future__ import annotations

import re


def parse_tweet_id(input_str: str) -> str:
    """Extract a tweet ID from a URL or raw numeric string."""
    match = re.search(r"(?:twitter\.com|x\.com)/\w+/status/(\d+)", input_str)
    if match:
        return match.group(1)
    stripped = input_str.strip()
    if re.fullmatch(r"\d+", stripped):
        return stripped
    raise ValueError(f"Invalid tweet ID or URL: {input_str}")


def strip_at(username: str) -> str:
    """Remove leading @ from a username if present."""
    return username.lstrip("@")
