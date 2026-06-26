"""Tests for tools.scheduling_tool (D8 — propose_times)."""
from datetime import datetime, timedelta

import pytest

from tools.scheduling_tool import (
    Slot,
    free_slots_within,
    merge_busy,
    propose_times,
    union_busy,
)


def dt(h, m=0, *, day=28):
    """Helper: 2026-05-<day> HH:MM (Thursday when day=28)."""
    return datetime(2026, 5, day, h, m)


# ---------------------------------------------------------------------------
# merge_busy
# ---------------------------------------------------------------------------

class TestMergeBusy:
    def test_empty(self):
        assert merge_busy([]) == []

    def test_no_overlap(self):
        ivals = [(dt(9), dt(10)), (dt(11), dt(12))]
        assert merge_busy(ivals) == ivals

    def test_overlap_merged(self):
        out = merge_busy([(dt(9), dt(11)), (dt(10), dt(12))])
        assert out == [(dt(9), dt(12))]

    def test_adjacent_merged(self):
        out = merge_busy([(dt(9), dt(10)), (dt(10), dt(11))])
        assert out == [(dt(9), dt(11))]

    def test_zero_or_negative_dropped(self):
        out = merge_busy([(dt(9), dt(9)), (dt(11), dt(10))])
        assert out == []

    def test_unordered_input(self):
        out = merge_busy([(dt(13), dt(14)), (dt(9), dt(10))])
        assert out == [(dt(9), dt(10)), (dt(13), dt(14))]


# ---------------------------------------------------------------------------
# union_busy
# ---------------------------------------------------------------------------

class TestUnionBusy:
    def test_two_participants(self):
        out = union_busy({
            "a": [(dt(9), dt(10))],
            "b": [(dt(10), dt(11))],
        })
        assert out == [(dt(9), dt(11))]


# ---------------------------------------------------------------------------
# free_slots_within
# ---------------------------------------------------------------------------

class TestFreeSlots:
    def test_no_busy_returns_full_window(self):
        out = free_slots_within((dt(9), dt(17)), [])
        assert out == [(dt(9), dt(17))]

    def test_one_busy_middle(self):
        out = free_slots_within((dt(9), dt(17)), [(dt(12), dt(13))])
        assert out == [(dt(9), dt(12)), (dt(13), dt(17))]

    def test_busy_outside_window_ignored(self):
        out = free_slots_within((dt(9), dt(17)), [(dt(7), dt(8)), (dt(18), dt(19))])
        assert out == [(dt(9), dt(17))]

    def test_busy_covers_window(self):
        out = free_slots_within((dt(9), dt(17)), [(dt(8), dt(18))])
        assert out == []

    def test_busy_touches_edges(self):
        out = free_slots_within((dt(9), dt(17)), [(dt(8), dt(9)), (dt(16), dt(18))])
        assert out == [(dt(9), dt(16))]


# ---------------------------------------------------------------------------
# propose_times — main behaviour
# ---------------------------------------------------------------------------

class TestProposeTimes:
    def test_returns_empty_when_no_room(self):
        slots = propose_times(
            {"a": [(dt(9), dt(17))]},
            duration=timedelta(minutes=30),
            window=(dt(9), dt(17)),
        )
        assert slots == []

    def test_finds_morning_slot(self):
        slots = propose_times(
            {"a": [(dt(13), dt(17))]},
            duration=timedelta(minutes=30),
            window=(dt(9), dt(17)),
            max_results=10,
        )
        assert len(slots) > 0
        # All slots must fit before 13:00 (when 'a' goes busy).
        for s in slots:
            assert s.end <= dt(13)

    def test_avoids_busy_intersection(self):
        slots = propose_times(
            {
                "a": [(dt(10), dt(11))],
                "b": [(dt(11), dt(12))],
                "c": [(dt(14), dt(15))],
            },
            duration=timedelta(minutes=30),
            window=(dt(9), dt(17)),
            max_results=20,
        )
        for s in slots:
            # No slot intersects any busy block.
            for busy_start, busy_end in [
                (dt(10), dt(11)), (dt(11), dt(12)), (dt(14), dt(15)),
            ]:
                assert not (s.start < busy_end and s.end > busy_start)

    def test_max_results_respected(self):
        slots = propose_times(
            {"a": []},
            duration=timedelta(minutes=30),
            window=(dt(9), dt(17)),
            max_results=3,
        )
        assert len(slots) == 3

    def test_working_hours_constraint(self):
        slots = propose_times(
            {"a": []},
            duration=timedelta(minutes=60),
            window=(dt(0), dt(23, 59)),
            constraints={"working_hours": (9, 17), "exclude_weekends": False},
            max_results=50,
        )
        for s in slots:
            assert 9 <= s.start.hour < 17
            assert s.end.hour <= 17

    def test_exclude_weekends_default(self):
        # 2026-05-30 is a Saturday.
        sat = datetime(2026, 5, 30, 9)
        sun_end = datetime(2026, 5, 31, 17)
        slots = propose_times(
            {"a": []},
            duration=timedelta(minutes=30),
            window=(sat, sun_end),
            max_results=20,
        )
        assert slots == []

    def test_exclude_weekends_off(self):
        sat = datetime(2026, 5, 30, 9)
        sun_end = datetime(2026, 5, 30, 17)
        slots = propose_times(
            {"a": []},
            duration=timedelta(minutes=30),
            window=(sat, sun_end),
            constraints={"exclude_weekends": False},
            max_results=5,
        )
        assert len(slots) > 0

    def test_min_gap_avoids_back_to_back(self):
        # Busy 10:00–11:00. With 30-min gap, slots starting before 11:30
        # right after that block should be dropped.
        slots = propose_times(
            {"a": [(dt(10), dt(11))]},
            duration=timedelta(minutes=30),
            window=(dt(9), dt(17)),
            constraints={"min_gap_minutes": 30, "exclude_weekends": False},
            max_results=50,
        )
        for s in slots:
            # No start in (11:00, 11:30) range.
            if s.start > dt(11):
                assert s.start >= dt(11, 30)
            # No end in (9:30, 10:00) range (slot ending just before busy).
            if s.end < dt(10):
                assert s.end <= dt(9, 30)

    def test_ranking_prefers_preferred_hour(self):
        slots = propose_times(
            {"a": []},
            duration=timedelta(minutes=30),
            window=(dt(9), dt(17)),
            max_results=20,
        )
        # The very top slot should be at a "preferred" hour (10, 11, 14, 15).
        assert slots[0].start.hour in (10, 11, 14, 15)

    def test_ranking_score_descending(self):
        slots = propose_times(
            {"a": []},
            duration=timedelta(minutes=30),
            window=(dt(9), dt(17)),
            max_results=10,
        )
        scores = [s.score for s in slots]
        assert scores == sorted(scores, reverse=True)

    def test_zero_duration_raises(self):
        with pytest.raises(ValueError):
            propose_times(
                {"a": []},
                duration=timedelta(),
                window=(dt(9), dt(17)),
            )

    def test_bad_window_raises(self):
        with pytest.raises(ValueError):
            propose_times(
                {"a": []},
                duration=timedelta(minutes=30),
                window=(dt(17), dt(9)),
            )

    def test_bad_working_hours_raises(self):
        with pytest.raises(ValueError):
            propose_times(
                {"a": []},
                duration=timedelta(minutes=30),
                window=(dt(9), dt(17)),
                constraints={"working_hours": (17, 9)},
            )

    def test_slot_to_dict(self):
        s = Slot(start=dt(10), end=dt(10, 30), score=0.5, reasons=["x"])
        d = s.to_dict()
        assert d["start"].startswith("2026-05-28")
        assert d["score"] == 0.5
        assert d["reasons"] == ["x"]
