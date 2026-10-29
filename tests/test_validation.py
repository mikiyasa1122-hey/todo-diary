import unittest
from datetime import date

from todo_app import validation as v


class DateTests(unittest.TestCase):
    def test_valid_dates(self):
        self.assertEqual(v.parse_date("2026-10-29"), date(2026, 10, 29))
        self.assertEqual(v.parse_date("2024-02-29"), date(2024, 2, 29))  # leap year
        self.assertEqual(v.parse_date("1900-01-01"), date(1900, 1, 1))
        self.assertEqual(v.parse_date("2999-12-31"), date(2999, 12, 31))

    def test_invalid_dates(self):
        for bad in [
            "2026-02-29",  # not a leap year
            "2026-02-30",
            "2026-13-01",
            "2026-00-10",
            "2026-1-1",
            "26-01-01",
            "2026/01/01",
            "2026-01-01T00:00",
            "2026-01-01\n",
            " 2026-01-01",
            "２０２６-０１-０１",  # full-width digits
            "1899-12-31",
            "3000-01-01",
            "",
        ]:
            with self.subTest(bad=bad), self.assertRaises(v.ValidationError):
                v.parse_date(bad)

    def test_non_string_dates(self):
        for bad in [None, 20261029, ["2026-01-01"], {"d": 1}, True]:
            with self.subTest(bad=bad), self.assertRaises(v.ValidationError):
                v.parse_date(bad)


class MonthTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(v.parse_month("2026-01"), (2026, 1))
        self.assertEqual(v.parse_month("2026-12"), (2026, 12))

    def test_invalid(self):
        for bad in ["2026-13", "2026-00", "2026-1", "2026", "1899-12", "3000-01", "２０２６-０１", None, 202601]:
            with self.subTest(bad=bad), self.assertRaises(v.ValidationError):
                v.parse_month(bad)


class TaskIdTests(unittest.TestCase):
    def test_ids(self):
        self.assertEqual(v.parse_task_id("1"), 1)
        self.assertEqual(v.parse_task_id("123456789012345678"), 123456789012345678)
        for bad in ["0", "-1", "abc", "1.5", "", "1234567890123456789", "１"]:
            with self.subTest(bad=bad), self.assertRaises(v.ValidationError):
                v.parse_task_id(bad)


class TitleTests(unittest.TestCase):
    def test_trims_and_accepts(self):
        self.assertEqual(v.clean_title("  牛乳を買う "), "牛乳を買う")
        self.assertEqual(v.clean_title("a" * v.TITLE_MAX), "a" * v.TITLE_MAX)
        self.assertEqual(v.clean_title("<script>alert(1)</script>"), "<script>alert(1)</script>")

    def test_rejects(self):
        for bad in ["", "   ", "\u3000" * 3, "a" * (v.TITLE_MAX + 1), "line\nbreak", "tab\tchar",
                    "nul\x00", None, 1, ["x"]]:
            with self.subTest(bad=bad), self.assertRaises(v.ValidationError):
                v.clean_title(bad)


class UnicodeEdgeTests(unittest.TestCase):
    def test_invisible_only_titles_are_empty(self):
        for bad in ["\u200b", "\u200b\u200d", "\ufeff", " \u3000\u200b ", "\u2060",
                    "\u3164", "\u2800\u2800", "\uffa0", "\u115f\u1160"]:
            with self.subTest(bad=repr(bad)), self.assertRaises(v.ValidationError):
                v.clean_title(bad)

    def test_line_separators_and_bidi_rejected_in_title(self):
        for bad in ["a\u2028b", "a\u2029b", "abc\u202edef", "x\u2066y"]:
            with self.subTest(bad=repr(bad)), self.assertRaises(v.ValidationError):
                v.clean_title(bad)

    def test_visible_unicode_titles_allowed(self):
        family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"  # emoji joined with ZWJ
        for ok in [family, "café", "日本語のタスク", "a\u200bb"]:
            with self.subTest(ok=ok):
                self.assertEqual(v.clean_title(ok), ok)

    def test_lone_surrogates_rejected(self):
        for bad in ["\ud800", "ok\udfff", "\ud83d"]:
            with self.subTest(bad=repr(bad)):
                self.assertFalse(v.is_encodable(bad))
                with self.assertRaises(v.ValidationError):
                    v.clean_title(bad)
                with self.assertRaises(v.ValidationError):
                    v.clean_notes(bad)
                with self.assertRaises(v.ValidationError):
                    v.clean_journal(bad)
        self.assertTrue(v.is_encodable("\U0001f600"))

    def test_line_separators_become_newlines_in_text(self):
        self.assertEqual(v.clean_journal("a\u2028b\u2029c"), "a\nb\nc")


class TextTests(unittest.TestCase):
    def test_notes(self):
        self.assertEqual(v.clean_notes("  1行目\r\n2行目  "), "1行目\n2行目")
        self.assertEqual(v.clean_notes(""), "")
        with self.assertRaises(v.ValidationError):
            v.clean_notes("x" * (v.NOTES_MAX + 1))
        with self.assertRaises(v.ValidationError):
            v.clean_notes(None)

    def test_journal(self):
        self.assertEqual(v.clean_journal("  字下げ\r\n\tタブ\n\n"), "  字下げ\n\tタブ")
        self.assertEqual(v.clean_journal("x" * v.JOURNAL_MAX), "x" * v.JOURNAL_MAX)
        for bad in ["x" * (v.JOURNAL_MAX + 1), "bell\x07", 123, None]:
            with self.subTest(bad=repr(bad)[:20]), self.assertRaises(v.ValidationError):
                v.clean_journal(bad)


class BoolTests(unittest.TestCase):
    def test_strict_bool(self):
        self.assertIs(v.clean_bool(True, "done"), True)
        self.assertIs(v.clean_bool(False, "done"), False)
        for bad in ["true", 1, 0, None, "false"]:
            with self.subTest(bad=bad), self.assertRaises(v.ValidationError):
                v.clean_bool(bad, "done")


if __name__ == "__main__":
    unittest.main()
