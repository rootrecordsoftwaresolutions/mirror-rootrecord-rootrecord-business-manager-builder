"""User folders and local session state. Authoritative time entries live in SQLite (RootRecord)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paths import RECORD_SUBDIRS, data_dir, user_root

UTC = timezone.utc


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def ensure_user_layout(user_id: int) -> Path:
    """Create users/<id>/records/{sheets,docs,...} if missing. Return user root."""
    root = user_root(user_id)
    for name in RECORD_SUBDIRS:
        (root / "records" / name).mkdir(parents=True, exist_ok=True)
    return root


def state_json_path(user_id: int) -> Path:
    return user_root(user_id) / "state.json"


# --- Machine session (workstation session anchor for prompts / exports) ---


def machine_session_path() -> Path:
    return data_dir() / "machine_session.json"


def registered_users_file() -> Path:
    return data_dir() / "registered_users.json"


def _ensure_data_dir() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)


def init_machine_session() -> str:
    """Record session start time (UTC ISO). Returns the timestamp."""
    _ensure_data_dir()
    started = _now_iso()
    msf = machine_session_path()
    msf.write_text(
        json.dumps({"started_at_utc": started, "db_machine_session_id": None}, indent=2),
        encoding="utf-8",
    )
    return started


def set_machine_session_db_id(db_id: int) -> None:
    _ensure_data_dir()
    msf = machine_session_path()
    raw: dict[str, Any] = {}
    if msf.is_file():
        try:
            raw = json.loads(msf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = {}
    raw["db_machine_session_id"] = int(db_id)
    msf.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def get_machine_session_db_id() -> int | None:
    msf = machine_session_path()
    if not msf.is_file():
        return None
    try:
        data = json.loads(msf.read_text(encoding="utf-8"))
        v = data.get("db_machine_session_id")
        return int(v) if v is not None else None
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def get_machine_session_started_at() -> str | None:
    msf = machine_session_path()
    if not msf.is_file():
        return None
    try:
        data = json.loads(msf.read_text(encoding="utf-8"))
        return data.get("started_at_utc")
    except (json.JSONDecodeError, OSError):
        return None


# --- Per-user tracking state ---


@dataclass
class UserState:
    evaluation_active: bool
    evaluation_start_utc: str | None
    current_work_start_utc: str | None
    current_work_description: str | None
    current_work_category_id: int | None = None
    current_project_id: int | None = None
    current_tag_ids: list[int] | None = None
    current_mode: str = "off"  # off | working | on_break
    last_task_description: str | None = None
    last_prompt_auto_fill_utc: str | None = None


def default_user_state(machine_session_start: str | None) -> UserState:
    start = machine_session_start or _now_iso()
    return UserState(
        evaluation_active=True,
        evaluation_start_utc=start,
        current_work_start_utc=None,
        current_work_description=None,
        current_work_category_id=None,
        current_project_id=None,
        current_tag_ids=None,
        current_mode="off",
        last_task_description=None,
        last_prompt_auto_fill_utc=None,
    )


def load_user_state(user_id: int) -> UserState | None:
    p = state_json_path(user_id)
    if not p.is_file():
        return None
    try:
        raw: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        return UserState(
            evaluation_active=bool(raw.get("evaluation_active", True)),
            evaluation_start_utc=raw.get("evaluation_start_utc"),
            current_work_start_utc=raw.get("current_work_start_utc"),
            current_work_description=raw.get("current_work_description"),
            current_work_category_id=raw.get("current_work_category_id"),
            current_project_id=raw.get("current_project_id"),
            current_tag_ids=raw.get("current_tag_ids"),
            current_mode=str(raw.get("current_mode") or "off"),
            last_task_description=raw.get("last_task_description"),
            last_prompt_auto_fill_utc=raw.get("last_prompt_auto_fill_utc"),
        )
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def save_user_state(user_id: int, state: UserState) -> None:
    ensure_user_layout(user_id)
    p = state_json_path(user_id)
    payload = {
        "evaluation_active": state.evaluation_active,
        "evaluation_start_utc": state.evaluation_start_utc,
        "current_work_start_utc": state.current_work_start_utc,
        "current_work_description": state.current_work_description,
        "current_work_category_id": state.current_work_category_id,
        "current_project_id": state.current_project_id,
        "current_tag_ids": state.current_tag_ids,
        "current_mode": state.current_mode,
        "last_task_description": state.last_task_description,
        "last_prompt_auto_fill_utc": state.last_prompt_auto_fill_utc,
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def reset_user_for_new_machine_session(user_id: int, machine_start_iso: str) -> UserState:
    st = UserState(
        evaluation_active=True,
        evaluation_start_utc=machine_start_iso,
        current_work_start_utc=None,
        current_work_description=None,
        current_work_category_id=None,
        current_project_id=None,
        current_tag_ids=None,
        current_mode="off",
        last_task_description=None,
        last_prompt_auto_fill_utc=None,
    )
    save_user_state(user_id, st)
    return st
