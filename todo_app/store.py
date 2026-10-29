"""SQLite persistence for tasks and journal entries.

Dates are stored as ISO strings (YYYY-MM-DD). Years are limited to four digits by
validation, so string comparison matches date order.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL,
    notes      TEXT    NOT NULL DEFAULT '',
    due_date   TEXT    NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1)),
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks (due_date);
CREATE TABLE IF NOT EXISTS journal (
    entry_date TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

CALENDAR_CELLS = 42  # 6 weeks, so every month fits
OVERDUE_LIMIT = 500
# Columns a task update may touch; keys are API field names.
_UPDATABLE = {"title": "title", "notes": "notes", "due_date": "due_date", "done": "done"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _task(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "notes": row["notes"],
        "due_date": row["due_date"],
        "done": bool(row["done"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        # One short-lived connection per operation keeps threads independent.
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # commit on success, rollback on error
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------- tasks
    def create_task(self, title: str, due_date: date, notes: str = "") -> dict[str, Any]:
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO tasks (title, notes, due_date, done, created_at, updated_at)"
                " VALUES (?, ?, ?, 0, ?, ?)",
                (title, notes, due_date.isoformat(), now, now),
            )
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _task(row)

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task(row) if row else None

    def list_tasks(self, start: date, end: date) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE due_date BETWEEN ? AND ?"
                " ORDER BY due_date, done, id",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return [_task(r) for r in rows]

    def list_overdue(self, today: date) -> tuple[list[dict[str, Any]], bool]:
        """Open tasks due before today, oldest first, and whether the list was cut at OVERDUE_LIMIT."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE done = 0 AND due_date < ?"
                " ORDER BY due_date, id LIMIT ?",
                (today.isoformat(), OVERDUE_LIMIT + 1),
            ).fetchall()
        return [_task(r) for r in rows[:OVERDUE_LIMIT]], len(rows) > OVERDUE_LIMIT

    def update_task(self, task_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
        """Apply already-validated changes. Returns None when the task does not exist."""
        unknown = set(changes) - set(_UPDATABLE)
        if unknown or not changes:
            raise ValueError(f"invalid task changes: {sorted(unknown) or 'empty'}")
        values: list[Any] = []
        assignments: list[str] = []
        for field, value in changes.items():
            if isinstance(value, date):
                value = value.isoformat()
            elif isinstance(value, bool):
                value = int(value)
            assignments.append(f"{_UPDATABLE[field]} = ?")
            values.append(value)
        assignments.append("updated_at = ?")
        values.extend([_now(), task_id])
        with self._conn() as conn:
            cur = conn.execute(f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?", values)
            if cur.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task(row)

    def delete_task(self, task_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cur.rowcount > 0

    # ----------------------------------------------------------- journal
    def get_journal(self, day: date) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM journal WHERE entry_date = ?", (day.isoformat(),)
            ).fetchone()
        if row is None:
            return None
        return {"date": row["entry_date"], "content": row["content"], "updated_at": row["updated_at"]}

    def save_journal(self, day: date, content: str) -> dict[str, Any] | None:
        """Create or replace the entry for a day. Blank content deletes it and returns None."""
        with self._conn() as conn:
            if not content.strip():
                conn.execute("DELETE FROM journal WHERE entry_date = ?", (day.isoformat(),))
                return None
            now = _now()
            conn.execute(
                "INSERT INTO journal (entry_date, content, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT (entry_date) DO UPDATE SET"
                " content = excluded.content, updated_at = excluded.updated_at",
                (day.isoformat(), content, now),
            )
        return {"date": day.isoformat(), "content": content, "updated_at": now}

    def list_journal_dates(self, start: date, end: date) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT entry_date FROM journal WHERE entry_date BETWEEN ? AND ?"
                " ORDER BY entry_date",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return [r["entry_date"] for r in rows]

    # ---------------------------------------------------------- calendar
    def month_summary(self, year: int, month: int, today: date) -> dict[str, Any]:
        """A Sunday-first 6-week grid for the month, with per-day task and journal info."""
        first = date(year, month, 1)
        start = first - timedelta(days=(first.weekday() + 1) % 7)
        end = start + timedelta(days=CALENDAR_CELLS - 1)
        with self._conn() as conn:
            counts = {
                r["due_date"]: (r["open_count"], r["done_count"])
                for r in conn.execute(
                    "SELECT due_date, SUM(done = 0) AS open_count, SUM(done = 1) AS done_count"
                    " FROM tasks WHERE due_date BETWEEN ? AND ? GROUP BY due_date",
                    (start.isoformat(), end.isoformat()),
                )
            }
        journal_dates = set(self.list_journal_dates(start, end))
        days = []
        for offset in range(CALENDAR_CELLS):
            day = start + timedelta(days=offset)
            key = day.isoformat()
            open_count, done_count = counts.get(key, (0, 0))
            days.append(
                {
                    "date": key,
                    "day": day.day,
                    "in_month": day.month == month,
                    "is_today": day == today,
                    "open_count": open_count,
                    "done_count": done_count,
                    "overdue": open_count > 0 and day < today,
                    "has_journal": key in journal_dates,
                }
            )
        return {"month": f"{year:04d}-{month:02d}", "today": today.isoformat(), "days": days}
