"""HTTP API and static file server, built on the standard library only.

The server is meant for one person on one machine: it listens on 127.0.0.1 and
rejects requests that look like they come from another site (wrong Host header,
foreign Origin, or a non-JSON body), so a web page cannot write to it.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from . import validation as v
from .store import Store

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
# Exact paths only: nothing outside this table is ever read from disk.
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
MAX_BODY = 64 * 1024
MAX_DRAIN = 1024 * 1024
MAX_RANGE_DAYS = 366
MAX_QUERY_FIELDS = 10
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; form-action 'self'",
}

Payload = dict[str, Any]
Query = dict[str, str]


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


Handler = Callable[[re.Match[str], Query, Payload], tuple[HTTPStatus, Payload]]


class App:
    """Routing and request handling, independent of the socket layer."""

    def __init__(self, store: Store, today: Callable[[], date] = date.today) -> None:
        self.store = store
        self.today = today
        self.routes: list[tuple[re.Pattern[str], dict[str, Handler]]] = [
            (re.compile(r"/api/today"), {"GET": self.get_today}),
            (re.compile(r"/api/calendar"), {"GET": self.get_calendar}),
            (re.compile(r"/api/tasks"), {"GET": self.list_tasks, "POST": self.create_task}),
            (re.compile(r"/api/tasks/overdue"), {"GET": self.list_overdue}),
            (
                re.compile(r"/api/tasks/(?P<id>[^/]+)"),
                {"GET": self.get_task, "PATCH": self.update_task, "DELETE": self.delete_task},
            ),
            (re.compile(r"/api/journal"), {"GET": self.list_journal}),
            (
                re.compile(r"/api/journal/(?P<date>[^/]+)"),
                {"GET": self.get_journal, "PUT": self.put_journal},
            ),
        ]

    def resolve(self, method: str, path: str) -> tuple[Handler, re.Match[str]]:
        for pattern, methods in self.routes:
            match = pattern.fullmatch(path)
            if match:
                if method not in methods:
                    raise ApiError(HTTPStatus.METHOD_NOT_ALLOWED, "このメソッドは使えません")
                return methods[method], match
        raise ApiError(HTTPStatus.NOT_FOUND, "見つかりません")

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _date_range(query: Query) -> tuple[date, date]:
        start = v.parse_date(query.get("from"), "from")
        end = v.parse_date(query.get("to"), "to")
        if end < start:
            raise v.ValidationError("to は from 以降の日付にしてください")
        if (end - start).days >= MAX_RANGE_DAYS:
            raise v.ValidationError(f"期間は {MAX_RANGE_DAYS} 日以内にしてください")
        return start, end

    @staticmethod
    def _only(body: Payload, allowed: set[str], required: set[str]) -> None:
        unknown = set(body) - allowed
        if unknown:
            raise v.ValidationError(f"不明な項目があります: {', '.join(sorted(unknown))}")
        missing = required - set(body)
        if missing:
            raise v.ValidationError(f"必須項目がありません: {', '.join(sorted(missing))}")

    def _task_id(self, match: re.Match[str]) -> int:
        try:
            return v.parse_task_id(match.group("id"))
        except v.ValidationError:
            raise ApiError(HTTPStatus.NOT_FOUND, "タスクが見つかりません") from None

    # ----------------------------------------------------------- handlers
    def get_today(self, match: re.Match[str], query: Query, body: Payload) -> tuple[HTTPStatus, Payload]:
        return HTTPStatus.OK, {"today": self.today().isoformat()}

    def get_calendar(self, match: re.Match[str], query: Query, body: Payload) -> tuple[HTTPStatus, Payload]:
        year, month = v.parse_month(query.get("month"))
        return HTTPStatus.OK, self.store.month_summary(year, month, self.today())

    def list_tasks(self, match: re.Match[str], query: Query, body: Payload) -> tuple[HTTPStatus, Payload]:
        start, end = self._date_range(query)
        return HTTPStatus.OK, {"tasks": self.store.list_tasks(start, end)}

    def list_overdue(self, match: re.Match[str], query: Query, body: Payload) -> tuple[HTTPStatus, Payload]:
        today = self.today()
        return HTTPStatus.OK, {"today": today.isoformat(), "tasks": self.store.list_overdue(today)}

    def create_task(self, match: re.Match[str], query: Query, body: Payload) -> tuple[HTTPStatus, Payload]:
        self._only(body, {"title", "due_date", "notes"}, {"title", "due_date"})
        task = self.store.create_task(
            title=v.clean_title(body["title"]),
            due_date=v.parse_date(body["due_date"], "期日"),
            notes=v.clean_notes(body.get("notes", "")),
        )
        return HTTPStatus.CREATED, {"task": task}

    def get_task(self, match: re.Match[str], query: Query, body: Payload) -> tuple[HTTPStatus, Payload]:
        task = self.store.get_task(self._task_id(match))
        if task is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "タスクが見つかりません")
        return HTTPStatus.OK, {"task": task}

    def update_task(self, match: re.Match[str], query: Query, body: Payload) -> tuple[HTTPStatus, Payload]:
        task_id = self._task_id(match)
        self._only(body, {"title", "due_date", "notes", "done"}, set())
        if not body:
            raise v.ValidationError("変更する項目を指定してください")
        changes: dict[str, Any] = {}
        if "title" in body:
            changes["title"] = v.clean_title(body["title"])
        if "due_date" in body:
            changes["due_date"] = v.parse_date(body["due_date"], "期日")
        if "notes" in body:
            changes["notes"] = v.clean_notes(body["notes"])
        if "done" in body:
            changes["done"] = v.clean_bool(body["done"], "done")
        task = self.store.update_task(task_id, changes)
        if task is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "タスクが見つかりません")
        return HTTPStatus.OK, {"task": task}

    def delete_task(self, match: re.Match[str], query: Query, body: Payload) -> tuple[HTTPStatus, Payload]:
        if not self.store.delete_task(self._task_id(match)):
            raise ApiError(HTTPStatus.NOT_FOUND, "タスクが見つかりません")
        return HTTPStatus.OK, {"deleted": True}

    def list_journal(self, match: re.Match[str], query: Query, body: Payload) -> tuple[HTTPStatus, Payload]:
        start, end = self._date_range(query)
        return HTTPStatus.OK, {"dates": self.store.list_journal_dates(start, end)}

    def get_journal(self, match: re.Match[str], query: Query, body: Payload) -> tuple[HTTPStatus, Payload]:
        day = v.parse_date(match.group("date"))
        return HTTPStatus.OK, {
            "date": day.isoformat(),
            "editable": day <= self.today(),
            "entry": self.store.get_journal(day),
        }

    def put_journal(self, match: re.Match[str], query: Query, body: Payload) -> tuple[HTTPStatus, Payload]:
        day = v.parse_date(match.group("date"))
        self._only(body, {"content"}, {"content"})
        if day > self.today():
            raise v.ValidationError("未来の日付の日誌は書けません")
        content = v.clean_journal(body["content"])
        return HTTPStatus.OK, {"date": day.isoformat(), "entry": self.store.save_journal(day, content)}


class RequestHandler(BaseHTTPRequestHandler):
    app: App  # set by make_server
    server_version = "TodoDiary/1.0"
    sys_version = ""

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def log_message(self, format: str, *args: Any) -> None:
        log.info("%s - %s", self.address_string(), format % args)

    # -------------------------------------------------------------------
    def _dispatch(self, method: str) -> None:
        try:
            self._check_host()
            url = urlsplit(self.path)
            if method == "GET" and url.path in STATIC_FILES:
                self._send_static(url.path)
                return
            if not url.path.startswith("/api/"):
                raise ApiError(HTTPStatus.NOT_FOUND, "見つかりません")
            handler, match = self.app.resolve(method, url.path)
            body: Payload = {}
            if method in ("POST", "PUT", "PATCH"):
                self._check_write_origin()
                body = self._read_json()
            elif method == "DELETE":
                self._check_write_origin()
            status, payload = handler(match, self._query(url.query), body)
            self._send_json(status, payload)
        except ApiError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except v.ValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("unhandled error for %s %s", method, self.path)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "内部エラーが発生しました"})

    def _allowed_hosts(self) -> set[str]:
        port = cast(ThreadingHTTPServer, self.server).server_port
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _check_host(self) -> None:
        # Blocks DNS rebinding: a foreign domain resolving to 127.0.0.1 keeps its own Host.
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in self._allowed_hosts():
            raise ApiError(HTTPStatus.FORBIDDEN, "許可されていないホストです")

    def _check_write_origin(self) -> None:
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in {f"http://{h}" for h in self._allowed_hosts()}:
            raise ApiError(HTTPStatus.FORBIDDEN, "他のサイトからの書き込みは許可されていません")
        if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            raise ApiError(HTTPStatus.FORBIDDEN, "他のサイトからの書き込みは許可されていません")

    def _query(self, raw: str) -> Query:
        try:
            parsed = parse_qs(raw, keep_blank_values=True, max_num_fields=MAX_QUERY_FIELDS)
        except ValueError:
            raise v.ValidationError("クエリが不正です") from None
        if any(len(values) != 1 for values in parsed.values()):
            raise v.ValidationError("同じクエリ項目が複数あります")
        return {key: values[0] for key, values in parsed.items()}

    def _read_json(self) -> Payload:
        # A JSON content type cannot be sent cross-site without a CORS preflight,
        # which this server never approves.
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type は application/json にしてください")
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise ApiError(HTTPStatus.LENGTH_REQUIRED, "Content-Length が必要です")
        try:
            length = int(length_header)
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Content-Length が不正です") from None
        if length < 0:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Content-Length が不正です")
        if length > MAX_BODY:
            # Drain a moderately oversized body so the client can read the error
            # instead of getting a connection reset.
            if length <= MAX_DRAIN:
                self.rfile.read(length)
            self.close_connection = True
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "リクエストが大きすぎます")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ApiError(HTTPStatus.BAD_REQUEST, "リクエスト本文が途中で切れています")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "JSON が不正です") from None
        if not isinstance(data, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "JSON はオブジェクトで送ってください")
        return data

    def _send_static(self, path: str) -> None:
        name, content_type = STATIC_FILES[path]
        data = (STATIC_DIR / name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: HTTPStatus, payload: Payload) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _security_headers(self) -> None:
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)


def make_server(
    store: Store, port: int = 8000, today: Callable[[], date] = date.today
) -> ThreadingHTTPServer:
    """Create a server bound to 127.0.0.1. Port 0 picks a free port."""
    handler = type("BoundHandler", (RequestHandler,), {"app": App(store, today)})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server

