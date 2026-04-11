"""Synchronous time-tracking logic for the desktop app."""

from __future__ import annotations

from datetime import datetime, timezone

from data_api import (
    business_id_for_new_time_entry,
    compute_amount_cents,
    effective_hourly_cents,
    get_work_category,
    get_work_category_id_by_name,
)
import sqlite3

from db import (
    DbConfig,
    insert_rich_time_entry,
    insert_session_event,
    insert_time_entry,
)
from storage import (
    UserState,
    default_user_state,
    get_machine_session_db_id,
    get_machine_session_started_at,
    load_user_state,
    save_user_state,
)

UTC = timezone.utc


def _primary_task_category_id(cfg: DbConfig, user_id: int) -> int | None:
    """Default category for task time: Development, with legacy fallback."""
    return get_work_category_id_by_name(cfg, user_id, "Development") or get_work_category_id_by_name(
        cfg, user_id, "Work"
    )


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def get_or_init_state(user_id: int) -> UserState:
    ms = get_machine_session_started_at()
    loaded = load_user_state(user_id)
    if loaded is not None:
        return loaded
    return default_user_state(ms)


def _legacy_insert(
    cfg: DbConfig,
    user_id: int,
    start_iso: str,
    end_iso: str,
    legacy_cat: str,
    description: str,
    mid: int | None,
) -> int:
    """Fallback if rich columns are missing (should not happen after migrations)."""
    return insert_time_entry(cfg, user_id, start_iso, end_iso, legacy_cat, description, mid)


def close_and_log_work(
    cfg: DbConfig,
    user_id: int,
    state: UserState,
    end_utc: str,
) -> UserState:
    if not (state.current_work_start_utc and state.current_work_description):
        return state
    mid = get_machine_session_db_id()
    wc_id = state.current_work_category_id or _primary_task_category_id(cfg, user_id)
    pid = state.current_project_id
    cat = get_work_category(cfg, wc_id) if wc_id else None
    billable = int(cat["billable"]) if cat else 1
    rate = effective_hourly_cents(
        cfg,
        user_id,
        work_category_id=wc_id,
        project_id=pid,
        override_cents=None,
    )
    amt = compute_amount_cents(
        state.current_work_start_utc,
        end_utc,
        rate,
        bool(billable),
    )
    try:
        insert_rich_time_entry(
            cfg,
            user_id,
            state.current_work_start_utc,
            end_utc,
            "work",
            state.current_work_description,
            mid,
            work_category_id=wc_id,
            project_id=pid,
            notes=None,
            billable=billable,
            hourly_rate_cents=rate,
            amount_cents=amt,
            currency="USD",
            tag_ids=state.current_tag_ids,
            business_id=business_id_for_new_time_entry(cfg),
        )
    except sqlite3.OperationalError:
        _legacy_insert(
            cfg,
            user_id,
            state.current_work_start_utc,
            end_utc,
            "work",
            state.current_work_description,
            mid,
        )
    return UserState(
        evaluation_active=state.evaluation_active,
        evaluation_start_utc=state.evaluation_start_utc,
        current_work_start_utc=None,
        current_work_description=None,
        current_work_category_id=None,
        current_project_id=None,
        current_tag_ids=None,
        current_mode="off",
        last_task_description=state.last_task_description or state.current_work_description,
        last_prompt_auto_fill_utc=state.last_prompt_auto_fill_utc,
    )


def handle_activity_text(
    cfg: DbConfig,
    user_id: int,
    text: str,
    *,
    work_category_id: int | None = None,
    project_id: int | None = None,
    notes: str = "",
    tag_ids: list[int] | None = None,
    billable: bool | None = None,
    hourly_override_cents: int | None = None,
) -> str:
    text = (text or "").strip()
    if not text:
        return "Enter a non-empty description of what you're doing."

    state = get_or_init_state(user_id)
    end_utc = now_iso()
    mid = get_machine_session_db_id()

    if state.evaluation_active:
        eval_start = state.evaluation_start_utc or get_machine_session_started_at() or end_utc
        ev_id = get_work_category_id_by_name(cfg, user_id, "Evaluation")
        ec = get_work_category(cfg, ev_id) if ev_id else None
        bil_ev = int(ec["billable"]) if ec else 0
        rate_ev = effective_hourly_cents(
            cfg,
            user_id,
            work_category_id=ev_id,
            project_id=None,
            override_cents=hourly_override_cents,
        )
        amt_ev = compute_amount_cents(eval_start, end_utc, rate_ev, bool(bil_ev))
        try:
            insert_rich_time_entry(
                cfg,
                user_id,
                eval_start,
                end_utc,
                "evaluation",
                "Planning / reviewing objectives (pre-work)",
                mid,
                work_category_id=ev_id,
                project_id=None,
                notes=None,
                billable=bil_ev,
                hourly_rate_cents=rate_ev,
                amount_cents=amt_ev,
                currency="USD",
                tag_ids=tag_ids,
                business_id=business_id_for_new_time_entry(cfg),
            )
        except sqlite3.OperationalError:
            _legacy_insert(
                cfg,
                user_id,
                eval_start,
                end_utc,
                "evaluation",
                "Planning / reviewing objectives (pre-work)",
                mid,
            )

        wc = work_category_id or _primary_task_category_id(cfg, user_id)
        state = UserState(
            evaluation_active=False,
            evaluation_start_utc=None,
            current_work_start_utc=end_utc,
            current_work_description=text,
            current_work_category_id=wc,
            current_project_id=project_id,
            current_tag_ids=tag_ids,
            current_mode="working",
            last_task_description=text,
            last_prompt_auto_fill_utc=state.last_prompt_auto_fill_utc,
        )
        save_user_state(user_id, state)
        insert_session_event(cfg, user_id, "first_entry", "Evaluation closed; task block started")
        return (
            "Logged evaluation time, then started on this task:\n"
            f"{text}\n\n"
            "Log again when you switch to something else."
        )

    state = close_and_log_work(cfg, user_id, state, end_utc)
    wc = work_category_id or _primary_task_category_id(cfg, user_id)
    state = UserState(
        evaluation_active=False,
        evaluation_start_utc=None,
        current_work_start_utc=end_utc,
        current_work_description=text,
        current_work_category_id=wc,
        current_project_id=project_id,
        current_tag_ids=tag_ids,
        current_mode="working",
        last_task_description=text,
        last_prompt_auto_fill_utc=state.last_prompt_auto_fill_utc,
    )
    save_user_state(user_id, state)
    insert_session_event(cfg, user_id, "activity_update", text[:500])
    return (
        "Updated. Previous block closed; now tracking:\n"
        f"{text}"
    )


def _save_new_running_state(
    user_id: int,
    *,
    start_utc: str,
    desc: str,
    wc_id: int | None,
    pid: int | None = None,
    tag_ids: list[int] | None = None,
    mode: str = "working",
) -> None:
    save_user_state(
        user_id,
        UserState(
            evaluation_active=False,
            evaluation_start_utc=None,
            current_work_start_utc=start_utc,
            current_work_description=desc,
            current_work_category_id=wc_id,
            current_project_id=pid,
            current_tag_ids=tag_ids,
            current_mode=mode,
            last_task_description=desc,
            last_prompt_auto_fill_utc=None,
        ),
    )


def clock_in(
    cfg: DbConfig,
    user_id: int,
    description: str,
    *,
    work_category_id: int | None = None,
    project_id: int | None = None,
    tag_ids: list[int] | None = None,
) -> str:
    state = get_or_init_state(user_id)
    now = now_iso()
    state = close_and_log_work(cfg, user_id, state, now)
    # Do not auto-assign a default category on Clock In.
    wc = work_category_id
    _save_new_running_state(
        user_id,
        start_utc=now,
        desc=description.strip() or "Working",
        wc_id=wc,
        pid=project_id,
        tag_ids=tag_ids,
        mode="working",
    )
    insert_session_event(cfg, user_id, "clock_in", description[:500] if description else "Working")
    return "Clocked in."


def break_in(cfg: DbConfig, user_id: int, *, description: str = "Break") -> str:
    state = get_or_init_state(user_id)
    now = now_iso()
    state = close_and_log_work(cfg, user_id, state, now)
    break_id = get_work_category_id_by_name(cfg, user_id, "Break")
    _save_new_running_state(
        user_id,
        start_utc=now,
        desc=description,
        wc_id=break_id,
        mode="on_break",
    )
    insert_session_event(cfg, user_id, "break_in", description[:500])
    return "Break started."


def break_out(
    cfg: DbConfig,
    user_id: int,
    *,
    resume_description: str | None = None,
    work_category_id: int | None = None,
    project_id: int | None = None,
) -> str:
    state = get_or_init_state(user_id)
    now = now_iso()
    state = close_and_log_work(cfg, user_id, state, now)
    action_id = work_category_id or _primary_task_category_id(cfg, user_id)
    resume = (resume_description or state.last_task_description or "Working").strip()
    _save_new_running_state(
        user_id,
        start_utc=now,
        desc=resume,
        wc_id=action_id,
        pid=project_id,
        mode="working",
    )
    insert_session_event(cfg, user_id, "break_out", resume[:500])
    return "Break ended — timer running again."


def clock_out(cfg: DbConfig, user_id: int) -> str:
    state = get_or_init_state(user_id)
    now = now_iso()
    state = close_and_log_work(cfg, user_id, state, now)
    save_user_state(
        user_id,
        UserState(
            evaluation_active=False,
            evaluation_start_utc=None,
            current_work_start_utc=None,
            current_work_description=None,
            current_work_category_id=None,
            current_project_id=None,
            current_tag_ids=None,
            current_mode="off",
            last_task_description=state.last_task_description,
            last_prompt_auto_fill_utc=state.last_prompt_auto_fill_utc,
        ),
    )
    insert_session_event(cfg, user_id, "clock_out", "")
    return "Clocked out."
