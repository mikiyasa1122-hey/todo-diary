"""Input validation shared by the store and HTTP layers.

Every function raises ValidationError with a message that is safe to show to users.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

MIN_YEAR = 1900
MAX_YEAR = 2999
TITLE_MAX = 200
NOTES_MAX = 2000
JOURNAL_MAX = 10000

# [0-9] instead of \d: \d also matches full-width and other Unicode digits.
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MONTH_RE = re.compile(r"([0-9]{4})-([0-9]{2})")
_ID_RE = re.compile(r"[0-9]{1,18}")
# Single-line text: no control characters at all.
_LINE_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# Multi-line text: newlines and tabs are fine, other control characters are not.
_TEXT_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Bidirectional overrides/isolates can make a title display as different text.
_BIDI_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069]")
# Characters that render as nothing: a title made only of these looks empty.
_INVISIBLE_CATEGORIES = {"Cc", "Cf", "Zs", "Zl", "Zp"}
# Letters/symbols that are drawn as blank space (Hangul fillers, Braille blank).
_BLANK_CHARS = frozenset("\u115f\u1160\u3164\uffa0\u2800")


class ValidationError(ValueError):
    """Raised when client-supplied data is invalid."""


def is_encodable(text: str) -> bool:
    """False for strings with lone surrogates (valid in JSON escapes, not in UTF-8)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def parse_date(value: Any, field: str = "日付") -> date:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ValidationError(f"{field}は YYYY-MM-DD 形式で指定してください")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"{field}が存在しない日付です: {value}") from None
    if not MIN_YEAR <= parsed.year <= MAX_YEAR:
        raise ValidationError(f"{field}は {MIN_YEAR}〜{MAX_YEAR} 年の範囲で指定してください")
    return parsed


def parse_month(value: Any) -> tuple[int, int]:
    match = _MONTH_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValidationError("月は YYYY-MM 形式で指定してください")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise ValidationError("月は 01〜12 で指定してください")
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise ValidationError(f"年は {MIN_YEAR}〜{MAX_YEAR} の範囲で指定してください")
    return year, month


def parse_task_id(value: str) -> int:
    if not _ID_RE.fullmatch(value) or int(value) <= 0:
        raise ValidationError("タスクIDが不正です")
    return int(value)


def clean_title(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("タイトルは文字列で指定してください")
    if not is_encodable(value):
        raise ValidationError("タイトルに使えない文字が含まれています")
    title = value.strip()
    if all(ch in _BLANK_CHARS or unicodedata.category(ch) in _INVISIBLE_CATEGORIES for ch in title):
        raise ValidationError("タイトルを入力してください")
    if _LINE_CONTROL_RE.search(title) or "\u2028" in title or "\u2029" in title:
        raise ValidationError("タイトルに改行や制御文字は使えません")
    if _BIDI_RE.search(title):
        raise ValidationError("タイトルに文字の向きを変える制御文字は使えません")
    if len(title) > TITLE_MAX:
        raise ValidationError(f"タイトルは {TITLE_MAX} 文字以内にしてください")
    return title


def _clean_text(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label}は文字列で指定してください")
    if not is_encodable(value):
        raise ValidationError(f"{label}に使えない文字が含まれています")
    text = value.replace("\r\n", "\n").replace("\r", "\n").replace("\u2028", "\n").replace("\u2029", "\n")
    if _TEXT_CONTROL_RE.search(text):
        raise ValidationError(f"{label}に制御文字は使えません")
    if len(text) > limit:
        raise ValidationError(f"{label}は {limit} 文字以内にしてください")
    return text


def clean_notes(value: Any) -> str:
    return _clean_text(value, "メモ", NOTES_MAX).strip()


def clean_journal(value: Any) -> str:
    # Keep inner formatting, but trailing whitespace alone is not content.
    return _clean_text(value, "日誌", JOURNAL_MAX).rstrip()


def clean_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{field}は true / false で指定してください")
    return value
