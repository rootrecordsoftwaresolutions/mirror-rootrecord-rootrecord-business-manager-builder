"""Process-wide flag: when True, `db.connect` enables SQLite `PRAGMA query_only` (no writes)."""

from __future__ import annotations

_read_only = False


def set_read_only(value: bool) -> None:
    global _read_only
    _read_only = bool(value)


def is_read_only() -> bool:
    return _read_only
