import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from todo_app.store import CALENDAR_CELLS, Store


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.tmp.name) / "test.db"
        self.store = Store(self.db)

    def tearDown(self):
        self.tmp.cleanup()


class TaskStoreTests(StoreTestCase):
    def test_create_and_get(self):
        task = self.store.create_task("買い物", date(2026, 10, 29), "牛乳")
        self.assertEqual(task["title"], "買い物")
        self.assertEqual(task["notes"], "牛乳")
        self.assertEqual(task["due_date"], "2026-10-29")
        self.assertIs(task["done"], False)
        self.assertEqual(self.store.get_task(task["id"]), task)

    def test_get_missing(self):
        self.assertIsNone(self.store.get_task(999))

    def test_list_range_and_order(self):
        a = self.store.create_task("A", date(2026, 10, 2))
        b = self.store.create_task("B", date(2026, 10, 1))
        c = self.store.create_task("C", date(2026, 10, 1))
        self.store.create_task("outside", date(2026, 11, 1))
        self.store.update_task(b["id"], {"done": True})
        ids = [t["id"] for t in self.store.list_tasks(date(2026, 10, 1), date(2026, 10, 31))]
        # By date, open before done, then creation order.
        self.assertEqual(ids, [c["id"], b["id"], a["id"]])

    def test_partial_update_keeps_other_fields(self):
        task = self.store.create_task("元", date(2026, 10, 29), "メモ")
        updated = self.store.update_task(task["id"], {"done": True})
        assert updated is not None
        self.assertIs(updated["done"], True)
        self.assertEqual(updated["title"], "元")
        self.assertEqual(updated["notes"], "メモ")
        updated = self.store.update_task(task["id"], {"title": "新", "due_date": date(2026, 11, 3)})
        assert updated is not None
        self.assertEqual((updated["title"], updated["due_date"], updated["done"]), ("新", "2026-11-03", True))

    def test_update_missing_returns_none(self):
        self.assertIsNone(self.store.update_task(42, {"done": True}))

    def test_update_rejects_unknown_or_empty_changes(self):
        task = self.store.create_task("x", date(2026, 1, 1))
        with self.assertRaises(ValueError):
            self.store.update_task(task["id"], {"id": 5})
        with self.assertRaises(ValueError):
            self.store.update_task(task["id"], {})

    def test_delete(self):
        task = self.store.create_task("消す", date(2026, 10, 29))
        self.assertTrue(self.store.delete_task(task["id"]))
        self.assertFalse(self.store.delete_task(task["id"]))
        self.assertIsNone(self.store.get_task(task["id"]))

    def test_overdue(self):
        today = date(2026, 10, 29)
        old = self.store.create_task("old", date(2026, 10, 1))
        finished = self.store.create_task("finished", date(2026, 10, 1))
        self.store.update_task(finished["id"], {"done": True})
        self.store.create_task("today", today)
        self.store.create_task("future", date(2026, 11, 1))
        tasks, truncated = self.store.list_overdue(today)
        self.assertEqual([t["id"] for t in tasks], [old["id"]])
        self.assertFalse(truncated)

    def test_overdue_reports_truncation(self):
        with mock.patch("todo_app.store.OVERDUE_LIMIT", 3):
            for i in range(3):
                self.store.create_task(f"t{i}", date(2026, 10, 1 + i))
            tasks, truncated = self.store.list_overdue(date(2026, 10, 29))
            self.assertEqual((len(tasks), truncated), (3, False))
            self.store.create_task("t3", date(2026, 10, 10))
            tasks, truncated = self.store.list_overdue(date(2026, 10, 29))
            self.assertEqual((len(tasks), truncated), (3, True))
            self.assertEqual([t["title"] for t in tasks], ["t0", "t1", "t2"])  # oldest first

    def test_sql_metacharacters_are_data(self):
        title = "'); DROP TABLE tasks; --"
        task = self.store.create_task(title, date(2026, 10, 29))
        self.assertEqual(self.store.get_task(task["id"])["title"], title)


class JournalStoreTests(StoreTestCase):
    def test_upsert_one_entry_per_day(self):
        day = date(2026, 10, 29)
        self.assertIsNone(self.store.get_journal(day))
        self.store.save_journal(day, "朝")
        entry = self.store.save_journal(day, "朝\n夜")
        self.assertEqual(entry["content"], "朝\n夜")
        self.assertEqual(self.store.get_journal(day)["content"], "朝\n夜")
        self.assertEqual(self.store.list_journal_dates(day, day), ["2026-10-29"])

    def test_blank_deletes(self):
        day = date(2026, 10, 29)
        self.store.save_journal(day, "書いた")
        self.assertIsNone(self.store.save_journal(day, "  \n "))
        self.assertIsNone(self.store.get_journal(day))
        self.assertIsNone(self.store.save_journal(day, ""))  # deleting nothing is fine

    def test_dates_in_range(self):
        for d in [date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 31), date(2026, 11, 1)]:
            self.store.save_journal(d, "x")
        self.assertEqual(
            self.store.list_journal_dates(date(2026, 10, 1), date(2026, 10, 31)),
            ["2026-10-01", "2026-10-31"],
        )


class CalendarTests(StoreTestCase):
    def grid(self, year, month, today=date(2026, 10, 29)):
        return self.store.month_summary(year, month, today)

    def check_grid_shape(self, year, month):
        data = self.grid(year, month)
        days = data["days"]
        self.assertEqual(len(days), CALENDAR_CELLS)
        first = date.fromisoformat(days[0]["date"])
        self.assertEqual(first.weekday(), 6, "grid must start on Sunday")
        # Consecutive days, and every day of the month appears exactly once.
        for i in range(1, len(days)):
            self.assertEqual(
                (date.fromisoformat(days[i]["date"]) - date.fromisoformat(days[i - 1]["date"])).days, 1
            )
        in_month = [d["date"] for d in days if d["in_month"]]
        self.assertEqual(in_month[0], f"{year:04d}-{month:02d}-01")
        self.assertTrue(all(d.startswith(f"{year:04d}-{month:02d}-") for d in in_month))
        return in_month

    def test_month_lengths(self):
        self.assertEqual(len(self.check_grid_shape(2024, 2)), 29)  # leap year
        self.assertEqual(len(self.check_grid_shape(2026, 2)), 28)  # starts on Sunday
        self.assertEqual(len(self.check_grid_shape(2026, 10)), 31)
        self.assertEqual(len(self.check_grid_shape(2026, 12)), 31)  # spills into next year
        self.assertEqual(len(self.check_grid_shape(2027, 1)), 31)  # starts in previous year
        self.assertEqual(len(self.check_grid_shape(2026, 8)), 31)  # needs all 6 weeks
        self.assertEqual(len(self.check_grid_shape(1900, 1)), 31)
        self.assertEqual(len(self.check_grid_shape(2999, 12)), 31)

    def test_sunday_first_month_has_no_leading_days(self):
        days = self.grid(2026, 2)["days"]
        self.assertEqual(days[0]["date"], "2026-02-01")

    def test_counts_overdue_today_and_journal(self):
        today = date(2026, 10, 29)
        self.store.create_task("past open", date(2026, 10, 1))
        done = self.store.create_task("past done", date(2026, 10, 1))
        self.store.update_task(done["id"], {"done": True})
        finished = self.store.create_task("only done", date(2026, 10, 2))
        self.store.update_task(finished["id"], {"done": True})
        self.store.create_task("today", today)
        self.store.create_task("next month cell", date(2026, 11, 1))
        self.store.save_journal(date(2026, 10, 5), "日誌")
        by_date = {d["date"]: d for d in self.grid(2026, 10, today)["days"]}

        self.assertEqual((by_date["2026-10-01"]["open_count"], by_date["2026-10-01"]["done_count"]), (1, 1))
        self.assertTrue(by_date["2026-10-01"]["overdue"])
        self.assertFalse(by_date["2026-10-02"]["overdue"], "all done is not overdue")
        self.assertFalse(by_date["2026-10-29"]["overdue"], "today is not overdue")
        self.assertTrue(by_date["2026-10-29"]["is_today"])
        self.assertEqual(sum(d["is_today"] for d in by_date.values()), 1)
        self.assertEqual(by_date["2026-11-01"]["open_count"], 1)
        self.assertFalse(by_date["2026-11-01"]["in_month"])
        self.assertTrue(by_date["2026-10-05"]["has_journal"])
        self.assertFalse(by_date["2026-10-06"]["has_journal"])


class PersistenceTests(StoreTestCase):
    def test_data_survives_reopen(self):
        task = self.store.create_task("残る", date(2026, 10, 29))
        self.store.save_journal(date(2026, 10, 29), "残る日誌")
        reopened = Store(self.db)
        self.assertEqual(reopened.get_task(task["id"])["title"], "残る")
        self.assertEqual(reopened.get_journal(date(2026, 10, 29))["content"], "残る日誌")


class ConcurrencyTests(StoreTestCase):
    def test_parallel_writes(self):
        errors = []

        def worker(n):
            try:
                for i in range(10):
                    task = self.store.create_task(f"{n}-{i}", date(2026, 10, 29))
                    self.store.update_task(task["id"], {"done": i % 2 == 0})
                self.store.save_journal(date(2026, 10, 29), f"writer {n}")
            except Exception as exc:  # noqa: BLE001 - collected and asserted below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        tasks = self.store.list_tasks(date(2026, 10, 29), date(2026, 10, 29))
        self.assertEqual(len(tasks), 200)
        self.assertEqual(len({t["title"] for t in tasks}), 200)
        self.assertEqual(sum(t["done"] for t in tasks), 100)
        self.assertEqual(self.store.list_journal_dates(date(2026, 10, 29), date(2026, 10, 29)), ["2026-10-29"])


if __name__ == "__main__":
    unittest.main()
