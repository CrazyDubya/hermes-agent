"""propose_times: multi-participant calendar scheduling (D8).

Given each participant's busy intervals (caller-provided so the function
stays adapter-agnostic), find slots of *duration* within *window* where
everyone is free, apply scheduling constraints, and return ranked
candidates.

Designed to slot underneath any calendar adapter — pass in pre-fetched
free/busy data from Google Calendar, Outlook, ICS, or a mock fixture.
No timezone gymnastics: datetimes must all share a tz (or all be
naive). The caller normalises before invoking.

Usage::

    from datetime import datetime, timedelta
    from tools.scheduling_tool import propose_times

    slots = propose_times(
        participants_busy={
            "alice": [(datetime(2026,5,28,10,0), datetime(2026,5,28,11,0))],
            "bob":   [(datetime(2026,5,28,14,0), datetime(2026,5,28,15,0))],
        },
        duration=timedelta(minutes=30),
        window=(datetime(2026,5,28,9,0), datetime(2026,5,28,17,0)),
        constraints={
            "working_hours": (9, 17),
            "exclude_weekends": True,
            "min_gap_minutes": 15,
        },
        max_results=5,
    )
    # → [Slot(start=..., end=..., score=0.95, reasons=[...]), ...]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

DateRange = Tuple[datetime, datetime]


@dataclass
class Slot:
    start: datetime
    end: datetime
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "score": round(self.score, 4),
            "reasons": list(self.reasons),
        }


# ---------------------------------------------------------------------------
# Busy-interval helpers
# ---------------------------------------------------------------------------

def merge_busy(intervals: Iterable[DateRange]) -> List[DateRange]:
    """Merge overlapping/adjacent intervals; return sorted, non-overlapping list."""
    sortable: List[DateRange] = []
    for iv in intervals:
        if iv is None:
            continue
        start, end = iv
        if start >= end:
            continue
        sortable.append((start, end))
    sortable.sort(key=lambda x: x[0])
    merged: List[DateRange] = []
    for start, end in sortable:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def union_busy(
    participants_busy: Dict[str, Iterable[DateRange]],
) -> List[DateRange]:
    """Union every participant's busy intervals into a single merged list."""
    flat: List[DateRange] = []
    for items in participants_busy.values():
        for iv in items or []:
            flat.append((iv[0], iv[1]))
    return merge_busy(flat)


def free_slots_within(
    window: DateRange, busy: List[DateRange]
) -> List[DateRange]:
    """Return the complement of *busy* inside *window*."""
    win_start, win_end = window
    if win_start >= win_end:
        return []
    cursor = win_start
    free: List[DateRange] = []
    for b_start, b_end in busy:
        if b_end <= cursor:
            continue
        if b_start >= win_end:
            break
        if b_start > cursor:
            free.append((cursor, min(b_start, win_end)))
        cursor = max(cursor, b_end)
        if cursor >= win_end:
            break
    if cursor < win_end:
        free.append((cursor, win_end))
    return free


# ---------------------------------------------------------------------------
# Candidate generation + constraints
# ---------------------------------------------------------------------------

def _enumerate_candidates(
    free: List[DateRange], duration: timedelta, granularity: timedelta,
) -> List[Slot]:
    """Slide a *duration*-sized window through each free interval."""
    out: List[Slot] = []
    if duration.total_seconds() <= 0:
        return out
    g = max(int(granularity.total_seconds()), 60)
    for start, end in free:
        # Latest possible start for a slot that still fits in this interval.
        latest = end - duration
        if latest < start:
            continue
        cur = start
        while cur <= latest:
            out.append(Slot(start=cur, end=cur + duration))
            cur = cur + timedelta(seconds=g)
    return out


def _apply_working_hours(
    slots: List[Slot], hours: Tuple[int, int]
) -> List[Slot]:
    """Keep only slots whose start and end fall within [hours[0], hours[1])."""
    lo, hi = hours
    if not (0 <= lo < hi <= 24):
        raise ValueError(f"working_hours must be (lo, hi) with 0 <= lo < hi <= 24")
    kept: List[Slot] = []
    for s in slots:
        if s.start.hour < lo:
            continue
        if s.start.hour >= hi:
            continue
        # End must land at or before close.
        if s.end.hour > hi:
            continue
        if s.end.hour == hi and s.end.minute > 0:
            continue
        kept.append(s)
    return kept


def _apply_weekend_exclusion(slots: List[Slot]) -> List[Slot]:
    return [s for s in slots if s.start.weekday() < 5]


def _apply_min_gap(
    slots: List[Slot], busy: List[DateRange], gap: timedelta
) -> List[Slot]:
    """Drop candidates whose start/end is within *gap* of any busy interval."""
    if gap.total_seconds() <= 0 or not busy:
        return slots
    kept: List[Slot] = []
    for s in slots:
        bad = False
        for b_start, b_end in busy:
            # If the candidate starts <gap after a busy block ends, drop it.
            if 0 < (s.start - b_end).total_seconds() < gap.total_seconds():
                bad = True
                break
            # If the candidate ends <gap before a busy block starts, drop it.
            if 0 < (b_start - s.end).total_seconds() < gap.total_seconds():
                bad = True
                break
        if not bad:
            kept.append(s)
    return kept


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

_DEFAULT_PREFERRED_HOURS = (10, 11, 14, 15)  # Empirical "good meeting" slots.


def _score(slot: Slot, *, preferred_hours: Tuple[int, ...]) -> Slot:
    score = 0.0
    reasons: List[str] = []
    # Prefer mornings/early afternoons over very early or late slots.
    if slot.start.hour in preferred_hours:
        score += 0.4
        reasons.append(f"hour {slot.start.hour}:00 is commonly available")
    # Prefer Tuesday–Thursday for cross-team meetings (Mon = catchup,
    # Fri = wrap-up; empirical scheduling-tool norm).
    weekday = slot.start.weekday()
    if 1 <= weekday <= 3:
        score += 0.2
        reasons.append("mid-week (Tue/Wed/Thu)")
    elif weekday == 0:
        score += 0.05
    # Prefer on-the-hour or half-hour starts.
    if slot.start.minute in (0, 30):
        score += 0.1
        reasons.append("clean :00 or :30 start")
    # Slight earliest-first bonus so ties break toward the soonest date.
    # Capped so ranking remains dominated by quality.
    slot.score = score
    slot.reasons = reasons
    return slot


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def propose_times(
    participants_busy: Dict[str, Iterable[DateRange]],
    *,
    duration: timedelta,
    window: DateRange,
    constraints: Optional[Dict[str, Any]] = None,
    max_results: int = 5,
    granularity: Optional[timedelta] = None,
) -> List[Slot]:
    """Find ranked slots of *duration* within *window* where everyone is free.

    *constraints* keys (all optional):
      - ``working_hours``: ``(lo, hi)`` in 0..24; default ``(9, 17)``.
      - ``exclude_weekends``: bool, default True.
      - ``min_gap_minutes``: int gap (in minutes) required around adjacent
        busy intervals. Default 0.
      - ``preferred_hours``: iterable of int hour-of-day to boost in ranking.

    *granularity* controls the candidate-start step (default 15 min).
    *max_results* caps the return list. Returns ``[]`` when no slot fits.
    """
    if duration.total_seconds() <= 0:
        raise ValueError("duration must be positive")
    win_start, win_end = window
    if win_start >= win_end:
        raise ValueError("window must have start < end")

    cons = dict(constraints or {})
    working_hours = cons.get("working_hours", (9, 17))
    exclude_weekends = bool(cons.get("exclude_weekends", True))
    min_gap = timedelta(minutes=int(cons.get("min_gap_minutes", 0)))
    preferred = tuple(int(h) for h in cons.get("preferred_hours", _DEFAULT_PREFERRED_HOURS))
    grain = granularity or timedelta(minutes=15)

    busy = union_busy(participants_busy)
    free = free_slots_within(window, busy)
    candidates = _enumerate_candidates(free, duration, grain)

    if working_hours is not None:
        candidates = _apply_working_hours(candidates, working_hours)
    if exclude_weekends:
        candidates = _apply_weekend_exclusion(candidates)
    if min_gap.total_seconds() > 0:
        candidates = _apply_min_gap(candidates, busy, min_gap)

    scored = [_score(c, preferred_hours=preferred) for c in candidates]
    # Sort by (score DESC, start ASC) for stable earliest-wins ties.
    scored.sort(key=lambda s: (-s.score, s.start))
    return scored[: max(0, int(max_results))]
