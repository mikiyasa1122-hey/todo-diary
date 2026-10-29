import http.client
import json
import random
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path

from todo_app.server import MAX_BODY, make_server
from todo_app.store import Store

TODAY = date(2026, 10, 29)


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = Store(Path(self.tmp.name) / "api.db")
        self.server = make_server(self.store, port=0, today=lambda: TODAY)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def request(self, method, path, body=None, *, raw=None, headers=None):
        """Send a request; returns (status, parsed JSON or raw bytes, headers)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}"}
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        elif raw is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        h = {k: v for k, v in h.items() if v is not None}
        try:
            conn.request(method, path, body=data, headers=h)
            res = conn.getresponse()
            payload = res.read()
            ctype = res.getheader("Content-Type", "")
            if ctype.startswith("application/json") and payload:
                payload = json.loads(payload)
            return res.status, payload, res
        finally:
            conn.close()

    def create(self, title="タスク", due_date="2026-10-29", **extra):
        status, data, _ = self.request("POST", "/api/tasks", {"title": title, "due_date": due_date, **extra})
        self.assertEqual(status, 201, data)
        return data["task"]


class TaskApiTests(ApiTestCase):
    def test_full_lifecycle(self):
        task = self.create("レポートを書く", notes="第2章まで")
        self.assertEqual(task["title"], "レポートを書く")
        self.assertIs(task["done"], False)

        status, data, _ = self.request("GET", "/api/tasks?from=2026-10-29&to=2026-10-29")
        self.assertEqual(status, 200)
        self.assertEqual([t["id"] for t in data["tasks"]], [task["id"]])

        status, data, _ = self.request("PATCH", f"/api/tasks/{task['id']}", {"done": True})
        self.assertEqual(status, 200)
        self.assertIs(data["task"]["done"], True)
        self.assertEqual(data["task"]["notes"], "第2章まで")

        status, data, _ = self.request(
            "PATCH", f"/api/tasks/{task['id']}", {"title": " 改題 ", "due_date": "2026-11-02", "notes": ""}
        )
        self.assertEqual(status, 200)
        self.assertEqual((data["task"]["title"], data["task"]["due_date"], data["task"]["notes"]),
                         ("改題", "2026-11-02", ""))

        status, data, _ = self.request("GET", f"/api/tasks/{task['id']}")
        self.assertEqual((status, data["task"]["title"]), (200, "改題"))

        status, data, _ = self.request("DELETE", f"/api/tasks/{task['id']}")
        self.assertEqual((status, data), (200, {"deleted": True}))
        self.assertEqual(self.request("GET", f"/api/tasks/{task['id']}")[0], 404)
        self.assertEqual(self.request("DELETE", f"/api/tasks/{task['id']}")[0], 404)
        self.assertEqual(self.request("PATCH", f"/api/tasks/{task['id']}", {"done": True})[0], 404)

    def test_create_validation(self):
        cases = [
            {"title": "", "due_date": "2026-10-29"},
            {"title": "   ", "due_date": "2026-10-29"},
            {"title": "x" * 201, "due_date": "2026-10-29"},
            {"title": "ok", "due_date": "2026-02-30"},
            {"title": "ok", "due_date": "10/29"},
            {"title": "ok"},
            {"due_date": "2026-10-29"},
            {"title": 123, "due_date": "2026-10-29"},
            {"title": "ok", "due_date": "2026-10-29", "done": True},  # unknown on create
            {"title": "ok", "due_date": "2026-10-29", "notes": 5},
        ]
        for body in cases:
            with self.subTest(body=body):
                status, data, _ = self.request("POST", "/api/tasks", body)
                self.assertEqual(status, 400, data)
                self.assertIn("error", data)
        status, data, _ = self.request("GET", "/api/tasks?from=1900-01-01&to=1900-12-31")
        self.assertEqual(data["tasks"], [])
        self.assertEqual(self.store.list_tasks(date(1900, 1, 1), date(2999, 1, 1)), [])

    def test_update_validation(self):
        task = self.create()
        for body in [{}, {"done": "true"}, {"done": 1}, {"title": ""}, {"due_date": "x"}, {"id": 3}]:
            with self.subTest(body=body):
                self.assertEqual(self.request("PATCH", f"/api/tasks/{task['id']}", body)[0], 400)
        self.assertEqual(self.store.get_task(task["id"]), task, "failed updates must not change data")

    def test_bad_ids_are_404(self):
        for path in ["/api/tasks/abc", "/api/tasks/0", "/api/tasks/-1", "/api/tasks/1.5",
                     "/api/tasks/99999999999999999999", "/api/tasks/1/extra"]:
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_list_range_validation(self):
        for query in ["", "?from=2026-10-01", "?from=2026-10-31&to=2026-10-01",
                      "?from=2026-01-01&to=2027-01-02", "?from=x&to=y",
                      "?from=2026-10-01&from=2026-10-02&to=2026-10-31"]:
            with self.subTest(query=query):
                self.assertEqual(self.request("GET", f"/api/tasks{query}")[0], 400)
        # 366 days inclusive is the largest allowed span.
        self.assertEqual(self.request("GET", "/api/tasks?from=2026-01-01&to=2027-01-01")[0], 200)


class CalendarApiTests(ApiTestCase):
    def test_today(self):
        self.assertEqual(self.request("GET", "/api/today")[1], {"today": "2026-10-29"})

    def test_calendar_month(self):
        self.create("past", "2026-10-01")
        self.create("today", "2026-10-29")
        self.request("PUT", "/api/journal/2026-10-29", {"content": "日誌"})
        status, data, _ = self.request("GET", "/api/calendar?month=2026-10")
        self.assertEqual(status, 200)
        self.assertEqual((data["month"], data["today"], len(data["days"])), ("2026-10", "2026-10-29", 42))
        by_date = {d["date"]: d for d in data["days"]}
        self.assertTrue(by_date["2026-10-01"]["overdue"])
        self.assertEqual(by_date["2026-10-29"]["open_count"], 1)
        self.assertTrue(by_date["2026-10-29"]["has_journal"])

    def test_calendar_validation(self):
        for query in ["", "?month=2026-13", "?month=2026-1", "?month=abc", "?month=3000-01"]:
            with self.subTest(query=query):
                self.assertEqual(self.request("GET", f"/api/calendar{query}")[0], 400)

    def test_overdue_list(self):
        old = self.create("old", "2026-10-01")
        done = self.create("done", "2026-10-02")
        self.request("PATCH", f"/api/tasks/{done['id']}", {"done": True})
        self.create("today", "2026-10-29")
        status, data, _ = self.request("GET", "/api/tasks/overdue")
        self.assertEqual(status, 200)
        self.assertEqual([t["id"] for t in data["tasks"]], [old["id"]])


class JournalApiTests(ApiTestCase):
    def test_write_read_update_delete(self):
        status, data, _ = self.request("GET", "/api/journal/2026-10-29")
        self.assertEqual((status, data["entry"], data["editable"]), (200, None, True))

        status, data, _ = self.request("PUT", "/api/journal/2026-10-29", {"content": "朝: 散歩\r\n夜: 読書\n\n"})
        self.assertEqual(status, 200)
        self.assertEqual(data["entry"]["content"], "朝: 散歩\n夜: 読書")

        self.request("PUT", "/api/journal/2026-10-29", {"content": "書き直し"})
        status, data, _ = self.request("GET", "/api/journal/2026-10-29")
        self.assertEqual(data["entry"]["content"], "書き直し")

        status, data, _ = self.request("PUT", "/api/journal/2026-10-29", {"content": "   "})
        self.assertEqual((status, data["entry"]), (200, None))
        self.assertIsNone(self.request("GET", "/api/journal/2026-10-29")[1]["entry"])

    def test_past_is_editable_future_is_not(self):
        self.assertEqual(self.request("PUT", "/api/journal/2026-10-28", {"content": "昨日"})[0], 200)
        status, data, _ = self.request("GET", "/api/journal/2026-10-30")
        self.assertEqual((status, data["editable"]), (200, False))
        status, data, _ = self.request("PUT", "/api/journal/2026-10-30", {"content": "明日"})
        self.assertEqual(status, 400)
        self.assertIsNone(self.store.get_journal(date(2026, 10, 30)))

    def test_validation(self):
        for path, body in [
            ("/api/journal/2026-10-29", {"content": "x" * 10001}),
            ("/api/journal/2026-10-29", {"content": 5}),
            ("/api/journal/2026-10-29", {}),
            ("/api/journal/2026-10-29", {"content": "a", "mood": "good"}),
            ("/api/journal/2026-02-30", {"content": "a"}),
            ("/api/journal/today", {"content": "a"}),
        ]:
            with self.subTest(path=path, body=str(body)[:40]):
                self.assertEqual(self.request("PUT", path, body)[0], 400)
        self.assertEqual(self.request("PUT", "/api/journal/2026-10-29", {"content": "x" * 10000})[0], 200)

    def test_list_dates(self):
        self.request("PUT", "/api/journal/2026-10-01", {"content": "a"})
        self.request("PUT", "/api/journal/2026-10-29", {"content": "b"})
        status, data, _ = self.request("GET", "/api/journal?from=2026-10-01&to=2026-10-31")
        self.assertEqual((status, data["dates"]), (200, ["2026-10-01", "2026-10-29"]))


class RobustnessTests(ApiTestCase):
    def test_malformed_bodies(self):
        cases = [
            b"",
            b"{",
            b"null",
            b"[]",
            b'"string"',
            b"\xff\xfe",
            b'{"title": "a", "due_date": "2026-10-29"',
            b"[" * 100000,  # also over the size limit
            b"[" * 30000 + b"]" * 30000,  # deep nesting under the size limit
            b'{"title": NaN, "due_date": "2026-10-29"}',
        ]
        for raw in cases:
            with self.subTest(raw=raw[:30]):
                status, data, _ = self.request("POST", "/api/tasks", raw=raw)
                self.assertIn(status, (400, 413), data)

    def test_oversized_body(self):
        raw = json.dumps({"title": "x", "due_date": "2026-10-29", "notes": "y" * MAX_BODY}).encode()
        self.assertEqual(self.request("POST", "/api/tasks", raw=raw)[0], 413)

    def test_bad_content_length(self):
        for value in ["abc", "-1"]:
            with self.subTest(value=value):
                status, _, _ = self.request("POST", "/api/tasks", raw=b"{}", headers={"Content-Length": value})
                self.assertEqual(status, 400)

    def test_unknown_routes_and_methods(self):
        self.assertEqual(self.request("GET", "/api/nothing")[0], 404)
        self.assertEqual(self.request("GET", "/nothing")[0], 404)
        self.assertEqual(self.request("PUT", "/api/tasks", {"x": 1})[0], 405)
        self.assertEqual(self.request("DELETE", "/api/today")[0], 405)
        self.assertEqual(self.request("POST", "/", {"x": 1})[0], 404)

    def test_lone_surrogates_return_400(self):
        # JSON allows \ud800 escapes, but such strings cannot be stored as UTF-8.
        task = self.create("元のタイトル")
        cases = [
            ("POST", "/api/tasks", rb'{"title": "\ud800", "due_date": "2026-10-29"}'),
            ("POST", "/api/tasks", rb'{"title": "ok", "due_date": "2026-10-29", "notes": "\udfff"}'),
            ("POST", "/api/tasks", rb'{"\ud800": 1, "title": "ok", "due_date": "2026-10-29"}'),
            ("PATCH", f"/api/tasks/{task['id']}", rb'{"title": "\ud800"}'),
            ("PATCH", f"/api/tasks/{task['id']}", rb'{"\udfff": true}'),
            ("PUT", "/api/journal/2026-10-29", rb'{"content": "\ud83d"}'),
            ("PUT", "/api/journal/2026-10-29", rb'{"\ud83d": "x"}'),
        ]
        for method, path, raw in cases:
            with self.subTest(method=method, raw=raw):
                status, data, _ = self.request(method, path, raw=raw)
                self.assertEqual(status, 400, data)
                self.assertIn("error", data)
        self.assertEqual(self.store.get_task(task["id"]), task)
        self.assertEqual(len(self.store.list_tasks(date(2026, 10, 29), date(2026, 10, 29))), 1)
        self.assertIsNone(self.store.get_journal(date(2026, 10, 29)))

    def test_head_and_options_are_405_json(self):
        for method, path in [("OPTIONS", "/api/tasks"), ("OPTIONS", "/"), ("HEAD", "/"), ("HEAD", "/api/today")]:
            with self.subTest(method=method, path=path):
                status, data, res = self.request(method, path)
                self.assertEqual(status, 405)
                self.assertIsNone(res.getheader("Access-Control-Allow-Origin"))
                if method == "OPTIONS":
                    self.assertIn("error", data)

    def test_random_inputs_never_cause_server_errors(self):
        rng = random.Random(20261029)
        values = [None, True, False, 0, -1, 1.5, 10**30, "", " ", "2026-10-29", "2026-02-30", "x" * 300,
                  "<b>", "\x00", "日本語", "\ud800", "a\udfffb", "\u200b", "\u202e", [], {}, ["2026-10-29"], {"a": 1}]
        fields = ["title", "due_date", "notes", "done", "content", "id", "", "\ud800"]
        paths = ["/api/tasks", "/api/tasks/1", "/api/tasks/overdue", "/api/journal/2026-10-29",
                 "/api/journal/2026-10-30", "/api/calendar?month=2026-10", "/api/tasks/%2e%2e",
                 "/api/journal/..%2f..%2f", "/api/tasks?from=2026-10-01&to=2026-10-31"]
        self.create()
        for _ in range(300):
            method = rng.choice(["GET", "POST", "PUT", "PATCH", "DELETE"])
            body = {rng.choice(fields): rng.choice(values) for _ in range(rng.randint(0, 4))}
            path = rng.choice(paths)
            send_body = body if method in ("POST", "PUT", "PATCH") else None
            status, data, _ = self.request(method, path, send_body)
            self.assertLess(status, 500, f"{method} {path} {body!r} -> {status} {data!r}")
        # The server still works after the noise.
        self.assertEqual(self.request("GET", "/api/today")[0], 200)


class SecurityTests(ApiTestCase):
    def test_rejects_foreign_host(self):
        for host in ["evil.example", f"evil.example:{self.port}", "127.0.0.1:1", ""]:
            with self.subTest(host=host):
                status, _, _ = self.request("GET", "/api/today", headers={"Host": host})
                self.assertEqual(status, 403)
        self.assertEqual(self.request("GET", "/api/today", headers={"Host": f"localhost:{self.port}"})[0], 200)

    def test_rejects_cross_site_writes(self):
        before = self.create()
        for headers in [{"Origin": "http://evil.example"}, {"Origin": "null"},
                        {"Origin": f"http://127.0.0.1:{self.port}", "Sec-Fetch-Site": "cross-site"}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("POST", "/api/tasks", {"title": "x", "due_date": "2026-10-29"},
                                              headers=headers)[0], 403)
                self.assertEqual(self.request("DELETE", f"/api/tasks/{before['id']}", headers=headers)[0], 403)
        self.assertIsNotNone(self.store.get_task(before["id"]))
        ok = self.request("POST", "/api/tasks", {"title": "same origin", "due_date": "2026-10-29"},
                          headers={"Origin": f"http://127.0.0.1:{self.port}", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(ok[0], 201)

    def test_requires_json_content_type(self):
        raw = json.dumps({"title": "x", "due_date": "2026-10-29"}).encode()
        for ctype in ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", None]:
            with self.subTest(ctype=ctype):
                status, _, _ = self.request("POST", "/api/tasks", raw=raw, headers={"Content-Type": ctype})
                self.assertEqual(status, 415)
        self.assertEqual(len(self.store.list_tasks(date(2026, 10, 29), date(2026, 10, 29))), 0)

    def test_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_xss_payload_is_returned_as_json_data(self):
        task = self.create("<img src=x onerror=alert(1)>")
        status, data, res = self.request("GET", f"/api/tasks/{task['id']}")
        self.assertEqual(data["task"]["title"], "<img src=x onerror=alert(1)>")
        self.assertTrue(res.getheader("Content-Type").startswith("application/json"))
        self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff")


class StaticFileTests(ApiTestCase):
    def test_serves_app_files_with_security_headers(self):
        for path, ctype in [("/", "text/html"), ("/app.js", "text/javascript"), ("/style.css", "text/css")]:
            with self.subTest(path=path):
                status, body, res = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertTrue(res.getheader("Content-Type").startswith(ctype))
                self.assertIn("default-src 'self'", res.getheader("Content-Security-Policy"))
                self.assertEqual(res.getheader("X-Frame-Options"), "DENY")
                self.assertGreater(len(body), 0)

    def test_path_traversal_is_not_served(self):
        for path in ["/../todo_app/server.py", "/%2e%2e/todo_app/server.py", "/static/app.js",
                     "/..%2fserver.py", "/server.py", "/app.js/../server.py", "//etc/passwd",
                     "/C:/Windows/win.ini", "/index.html%00.js"]:
            with self.subTest(path=path):
                status, body, _ = self.request("GET", path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"import", body if isinstance(body, bytes) else json.dumps(body).encode())


if __name__ == "__main__":
    unittest.main()
