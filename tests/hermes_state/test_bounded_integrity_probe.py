import sqlite3
from pathlib import Path

import hermes_state_repair


class FakeConn:
    def __init__(self, error=None):
        self.error = error
        self.handlers = []
        self.sql = []

    def set_progress_handler(self, handler, steps):
        self.handlers.append((handler, steps))

    def execute(self, sql):
        self.sql.append(sql)
        if self.error:
            raise self.error
        return self

    def fetchall(self):
        return [("ok",)]


def test_slow_interrupted_integrity_probe_is_skipped():
    conn = FakeConn(sqlite3.OperationalError("interrupted"))
    assert hermes_state_repair._bounded_integrity_check(conn, Path("state.db")) is None
    assert conn.sql == ["PRAGMA integrity_check(20)"]


def test_real_integrity_probe_error_is_returned():
    conn = FakeConn(sqlite3.OperationalError("disk I/O error"))
    assert hermes_state_repair._bounded_integrity_check(conn, Path("state.db")) == "disk I/O error"


def test_integrity_probe_always_clears_progress_handler():
    conn = FakeConn()
    assert hermes_state_repair._bounded_integrity_check(conn, Path("state.db")) is None
    assert conn.handlers[-1] == (None, 0)
