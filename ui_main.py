"""CustomTkinter shell: Dashboard, Time, Money, Reports, Settings."""

from __future__ import annotations

import getpass
import logging
import math
import sys
import json
from typing import Any
import os
import re
import subprocess
import shutil
import sqlite3
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from urllib.parse import quote
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
try:
    import winreg
except ImportError:  # non-Windows
    winreg = None  # type: ignore[assignment]

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

import customtkinter as ctk

from data_api import (
    add_finalized_range,
    business_id_for_new_time_entry,
    compute_amount_cents,
    daily_project_breakdown,
    daily_task_breakdown,
    delete_time_entry,
    effective_hourly_cents,
    format_stored_utc_as_local,
    get_work_category,
    insert_time_entry_audit,
    insert_income,
    insert_expense,
    insert_debt,
    insert_project,
    is_time_range_locked,
    list_expenses_between,
    list_income,
    list_income_between,
    money_totals_by_currency_between,
    list_expenses,
    list_debts,
    set_debt_status,
    settle_debt_with_expense,
    add_scheduled_expense,
    insert_resource_entry,
    list_resource_entries,
    upsert_available_funds_account,
    list_available_funds_accounts,
    set_available_funds_account_archived,
    available_funds_totals_by_currency,
    ledger_net_totals_by_currency,
    list_scheduled_expenses,
    run_due_scheduled_expenses,
    set_scheduled_expense_active,
    list_finalized_ranges,
    list_time_entry_audit,
    list_projects,
    list_business_profiles,
    list_quick_actions,
    recent_time_entries,
    list_time_entries_between,
    list_session_events_between,
    list_work_categories,
    remove_finalized_range,
    save_quick_action,
    search_records,
    settings_get,
    settings_set,
    multi_business_enabled,
    get_active_business_id,
    set_active_business_id,
    save_business_profile,
    set_business_profile_archived,
    summary_between,
    summary_today,
    update_time_entry,
    upsert_work_category,
    utc_naive_bounds_for_local_date,
    utc_naive_bounds_for_local_report_range,
)
from db import (
    DbConfig,
    delete_session_event_row,
    restore_session_event_row,
    ensure_schema,
    insert_machine_session,
    insert_rich_time_entry,
    load_db_config,
    maybe_migrate_legacy_desktop_db,
    migrate_registered_users_from_json,
    update_session_event_row,
    upsert_user,
)
from app_version import APP_VERSION
from export_sheet import build_hours_csv_bytes_between
from branding_theme import BrandingPalette, get_branding_palette, iter_loading_image_paths
from help_text import USER_GUIDE
from migrations import (
    FACTORY_APP_SETTINGS_DEFAULTS,
    FACTORY_QUICK_ACTION_SEEDS,
    STD_TIME_CATEGORIES,
    run_migrations,
)
from suite_data import (
    adjust_stock_product_qty,
    adjust_supply_qty,
    archive_client,
    archive_stock_product,
    archive_supply,
    create_invoice,
    delete_invoice,
    delete_schedule_event,
    get_client,
    get_invoice,
    get_schedule_event,
    get_stock_product,
    get_supply,
    list_clients,
    list_invoices,
    list_invoice_lines,
    list_schedule_events_upcoming,
    list_schedule_events,
    list_stock_products,
    list_supplies,
    parse_invoice_lines_text,
    replace_invoice_lines,
    save_client,
    save_schedule_event,
    save_stock_product,
    save_supply,
    schedule_local_labels,
    update_invoice_meta,
    write_invoice_pdf,
    local_input_to_utc_naive_iso,
)
from paths import (
    iter_app_bundle_asset_dirs,
    resolve_shipped_asset,
    user_records_dir,
)
from storage import (
    ensure_user_layout,
    get_machine_session_db_id,
    get_machine_session_started_at,
    init_machine_session,
    load_user_state,
    registered_users_file,
    reset_user_for_new_machine_session,
    set_machine_session_db_id,
)
from tracking_core import break_in, break_out, clock_in, clock_out, handle_activity_text

try:
    import matplotlib

    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure

    _DASHBOARD_CHARTS_AVAILABLE = True
except ImportError:
    Figure = None  # type: ignore[misc, assignment]
    FigureCanvasTkAgg = None  # type: ignore[misc, assignment]
    _DASHBOARD_CHARTS_AVAILABLE = False

LOCAL_USER_ID = 1


def _fmt_money(cents: int | None, currency: str = "USD") -> str:
    if cents is None:
        return "—"
    return f"{currency} {cents / 100:,.2f}"


def _fmt_hm(seconds: float) -> str:
    if seconds <= 0:
        return "0h 0m"
    m = int(seconds // 60)
    h, mm = divmod(m, 60)
    return f"{h}h {mm}m"


def _fmt_currency_totals_line(totals: dict[str, dict[str, int]]) -> str:
    if not totals:
        return "—"
    parts: list[str] = []
    for ccy in sorted(totals.keys()):
        row = totals.get(ccy, {})
        parts.append(
            f"{ccy}: +{(int(row.get('income_cents', 0))/100):,.2f} / -{(int(row.get('expense_cents', 0))/100):,.2f} / net {(int(row.get('net_cents', 0))/100):,.2f}"
        )
    return " | ".join(parts)


def _fmt_setting_display_value(v: Any) -> str:
    if isinstance(v, bool):
        return "on" if v else "off"
    if v is None:
        return "—"
    s = str(v).replace("\n", " ")
    return s if len(s) <= 36 else s[:33] + "…"


def _settings_value_matches_factory(current: Any, factory: Any) -> bool:
    return current == factory


def _pct_change_text(current: float, previous: float) -> str:
    if previous <= 0:
        if current <= 0:
            return "0%"
        return "new"
    pct = ((current - previous) / previous) * 100.0
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.0f}%"


def _dollars_per_hour(cents: float, seconds: float) -> float:
    if seconds <= 0:
        return 0.0
    return (cents / 100.0) / (seconds / 3600.0)


def _default_activity_category_label(names: list[str]) -> str:
    """Prefer Development for active task defaults (legacy fallback: Work)."""
    if "Development" in names:
        return "Development"
    if "Work" in names:
        return "Work"
    return names[0] if names else "Development"


class RootRecordApp(ctk.CTk):
    def __init__(self, cfg: DbConfig, startup_splash: Any | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        try:
            from backup_r2_client import bootstrap_cloud_backup_if_enabled

            bootstrap_cloud_backup_if_enabled(self.cfg)
        except Exception:
            pass
        self._startup_splash = startup_splash
        self.uid = LOCAL_USER_ID
        self._last_activity_desc = ""
        self._active_prompt_popup: tk.Toplevel | None = None
        self._prompt_job_id: str | None = None
        # Single deferred "show check-in soon" callback from _resume_prompts_if_working_now (avoid stacking).
        self._prompt_immediate_job: str | None = None
        self._tray_icon = None
        self._tray_icon_running = False
        self.title("RootRecord Business Manager")
        self.geometry("1120x760")
        self.minsize(960, 640)
        self._apply_app_icon()

        # Keep UI consistently readable; avoid overly bright light mode.
        ctk.set_appearance_mode("dark")
        settings_set(self.cfg, "theme", "dark")
        _pal = get_branding_palette()
        self._theme_bg = _pal.bg
        self._theme_sidebar = _pal.sidebar
        self._theme_panel = _pal.panel
        self._theme_nav_idle = _pal.nav_idle
        self._theme_nav_active = _pal.nav_active
        self._theme_hover = _pal.hover
        self._theme_accent = _pal.accent
        self._theme_text_muted = _pal.text_muted
        self._theme_text_secondary = _pal.text_secondary
        self._theme_text_heading = _pal.text_heading
        self._theme_help_btn_bg = _pal.help_icon_bg
        self._theme_help_btn_fg = _pal.help_icon_fg
        self._theme_border_subtle = _pal.border_subtle
        self._theme_billing_hero_sub = _pal.billing_hero_sub
        self._theme_billing_card_title = _pal.billing_card_title
        self._theme_billing_row_label = _pal.billing_row_label
        self._theme_billing_hint = _pal.billing_hint
        self._theme_chart_bg = _pal.chart_bg
        self._theme_chart_fg = _pal.chart_fg
        self._theme_chart_edge = _pal.chart_edge
        # Flatten default widget surfaces to remove shaded gradients/bands.
        try:
            theme = ctk.ThemeManager.theme
            theme["CTk"]["fg_color"] = [self._theme_bg, self._theme_bg]
            theme["CTkFrame"]["fg_color"] = [self._theme_panel, self._theme_panel]
            theme["CTkScrollableFrame"]["fg_color"] = [self._theme_panel, self._theme_panel]
            theme["CTkToplevel"]["fg_color"] = [self._theme_bg, self._theme_bg]
        except Exception:
            pass
        self.configure(fg_color=self._theme_bg)
        self._pump_startup_splash()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        side = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=self._theme_sidebar)
        side.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self._side = side
        head_row = ctk.CTkFrame(side, fg_color="transparent")
        head_row.pack(fill="x", pady=(16, 6), padx=10)
        ctk.CTkLabel(
            head_row,
            text="RootRecord\nBusiness Manager",
            font=ctk.CTkFont(size=20, weight="bold"),
            justify="left",
        ).pack(side="left", anchor="w")
        self._sidebar_toggle_btn = ctk.CTkButton(
            head_row,
            text="<<",
            width=34,
            command=self._toggle_sidebar,
            fg_color=self._theme_nav_idle,
            hover_color=self._theme_hover,
            border_width=0,
        )
        self._sidebar_toggle_btn.pack(side="right", padx=(8, 0))
        ctk.CTkLabel(
            side,
            text="Your grounding root for\nbusiness productivity",
            font=ctk.CTkFont(size=12),
            text_color=self._theme_text_muted,
            justify="left",
        ).pack(padx=16, anchor="w")
        self._active_business_var = tk.StringVar(value="Master (All Businesses)")
        self._profile_name_to_id: dict[str, int] = {"Master (All Businesses)": 0}
        self._profile_switch_menu = ctk.CTkOptionMenu(
            side,
            values=["Master (All Businesses)"],
            variable=self._active_business_var,
            command=self._on_business_profile_change,
            width=188,
        )
        self._profile_label = ctk.CTkLabel(side, text="Profile", text_color=self._theme_text_muted, font=ctk.CTkFont(size=11))
        self._profile_label.pack(
            padx=16, anchor="w", pady=(8, 2)
        )
        self._profile_switch_menu.pack(padx=16, fill="x")
        self._refresh_business_profiles_ui()
        self._nav_items = (
            "Dashboard",
            "Finance & Clients",
            "Schedule & Bookings",
            "Stock & Supplies",
            "Work Log",
            "Reports",
            "Account Settings",
            "About & Help",
            "Billing",
            "Program Settings",
        )
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        nav = ctk.CTkFrame(side, fg_color="transparent")
        nav.pack(fill="x", padx=12, pady=(18, 8))
        nav_groups: tuple[tuple[str, tuple[str, ...]], ...] = (
            ("Overview", ("Dashboard", "Work Log", "Reports")),
            ("Operations", ("Finance & Clients", "Schedule & Bookings", "Stock & Supplies")),
            ("System", ("Account Settings", "Program Settings", "About & Help", "Billing")),
        )
        for group_name, group_items in nav_groups:
            ctk.CTkLabel(
                nav,
                text=group_name.upper(),
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=self._theme_text_muted,
            ).pack(anchor="w", padx=4, pady=(8, 2))
            for name in group_items:
                btn = ctk.CTkButton(
                    nav,
                    text=name,
                    anchor="w",
                    width=188,
                    fg_color=self._theme_nav_idle,
                    hover_color=self._theme_hover,
                    command=lambda n=name: self._switch_nav(n),
                )
                btn.pack(fill="x", pady=3)
                self._nav_buttons[name] = btn

        # Solid fill so rounded `place()` widgets (sidebar restore) do not show dark square corners
        # against a transparent parent (CustomTkinter alpha quirk on Windows).
        self._body = ctk.CTkFrame(self, fg_color=self._theme_bg, corner_radius=0, border_width=0)
        self._body.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        self._body.grid_rowconfigure(0, weight=1)
        self._body.grid_columnconfigure(0, weight=1)
        self._sidebar_collapsed = False

        self.tabview = ctk.CTkTabview(
            self._body,
            fg_color=self._theme_panel,
            corner_radius=0,
            border_width=0,
        )
        self.tabview.grid(row=0, column=0, sticky="nsew")
        self._process_status_var = tk.StringVar(value="")
        self._process_status_label = ctk.CTkLabel(
            self,
            text=self._process_status_var.get(),
            textvariable=self._process_status_var,
            fg_color=self._theme_nav_active,
            corner_radius=8,
            padx=12,
            pady=6,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self._process_status_clear_job: str | None = None
        self._process_status_label.place_forget()
        self._sidebar_restore_btn = ctk.CTkButton(
            self._body,
            text=">>",
            width=34,
            command=self._toggle_sidebar,
            fg_color=self._theme_nav_idle,
            hover_color=self._theme_hover,
            border_width=0,
        )
        self._sidebar_restore_btn.place_forget()
        for name in self._nav_items:
            self.tabview.add(name)
        # Hide top tab strip and use sidebar as the primary navigation.
        if hasattr(self.tabview, "_segmented_button"):
            try:
                self.tabview._segmented_button.grid_forget()
            except Exception:
                pass
            try:
                self.tabview._segmented_button.pack_forget()
            except Exception:
                pass
            try:
                self.tabview._segmented_button.place_forget()
            except Exception:
                pass
            try:
                self.tabview._segmented_button.configure(height=0)
            except Exception:
                pass
            try:
                # Remove any visible chrome from the hidden header control.
                self.tabview._segmented_button.configure(
                    fg_color=self._theme_panel,
                    selected_color=self._theme_panel,
                    unselected_color=self._theme_panel,
                    selected_hover_color=self._theme_panel,
                    unselected_hover_color=self._theme_panel,
                    text_color=self._theme_panel,
                    text_color_disabled=self._theme_panel,
                    border_width=0,
                )
            except Exception:
                pass
        try:
            # CTkTabview keeps a header grid row; collapse it so no dark top strip remains.
            self.tabview.grid_rowconfigure(0, minsize=0, weight=0)
        except Exception:
            pass

        self._build_dashboard()
        self._build_finance_clients()
        self._build_schedule()
        self._build_stock_and_supplies()
        self._build_calendar()
        self._build_reports()
        self._build_account()
        self._build_help()
        self._build_billing()
        self._build_settings()
        self._switch_nav("Dashboard")
        self._pump_startup_splash()

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(6, 8))
        self._license_footer_banner_lbl = None
        try:
            import license_gate as _license_gate

            from license_client import license_footer_hints
            from license_config import get_license_api_config

            api_on = bool(get_license_api_config())
            hints = license_footer_hints(api_configured=api_on) if api_on else None
            banner_line = hints.line if hints else None
            banner_warn = bool(hints and hints.warn)
            show_sub = bool(hints and hints.show_subscribe)
            tick_trial = bool(hints and hints.tick_trial)
            if not banner_line and api_on and _license_gate.is_read_only():
                banner_line = (
                    "Read-only — trial ended or subscription inactive. View and export only."
                )
                banner_warn = True
                show_sub = True
            if banner_line or show_sub:
                lic_row = ctk.CTkFrame(foot, fg_color="transparent")
                lic_row.pack(fill="x", anchor="w")
                if show_sub:
                    sub_btn = ctk.CTkButton(
                        lic_row,
                        text="Activate Services",
                        width=178,
                        fg_color=self._theme_accent,
                        hover_color=self._theme_hover,
                        border_width=0,
                        command=self._on_subscribe_billing,
                    )
                    sub_btn.pack(side="right")
                    self._attach_tooltip(
                        sub_btn,
                        "Opens Stripe Checkout in your browser to purchase or manage your subscription.",
                    )
                if banner_line:
                    self._license_banner_var = tk.StringVar(value=banner_line)
                    if banner_warn:
                        banner_color = "#e6c35c"
                    elif tick_trial:
                        banner_color = "#ffffff"
                    else:
                        banner_color = self._theme_accent
                    self._license_footer_banner_lbl = ctk.CTkLabel(
                        lic_row,
                        text=self._license_banner_var.get(),
                        textvariable=self._license_banner_var,
                        font=ctk.CTkFont(size=12, weight="bold"),
                        text_color=banner_color,
                        wraplength=1200,
                        justify="left",
                        anchor="w",
                    )
                    self._license_footer_banner_lbl.pack(side="left", fill="x", expand=True, padx=(0, 10))
                    if tick_trial:
                        self.after(60_000, self._refresh_license_banner_text)
        except Exception:
            pass
        self._purge_footer_ctk_label_placeholders()

        self._pump_startup_splash()
        self._startup_splash = None

        self._schedule_prompts()
        self.after(1200, self._auto_backup_if_due)
        self.after(90_000, self._cloud_backup_tick)
        self.after(900, self._show_startup_clocked_in_notice_if_needed)
        self.after(500, self._refresh_dashboard)
        self.after(2000, self._maybe_show_trial_welcome_popup)
        self.after(4000, self._github_update_check_on_startup_deferred)
        self.after(20_000, self._sync_cloud_tick)
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self.bind("<Unmap>", self._on_window_unmap)
        self._schedule_dashboard_clock_live_refresh()

    def _pump_startup_splash(self) -> None:
        s = getattr(self, "_startup_splash", None)
        if s is None:
            return
        try:
            s.update_idletasks()
            s.update()
        except Exception:
            pass

    def _purge_footer_ctk_label_placeholders(self) -> None:
        """Footer labels use text=\"\" at creation; do not configure(text=\"\") here — it clears StringVar display on CTkLabel."""
        return

    def _github_update_check_on_startup_deferred(self) -> None:
        if not bool(settings_get(self.cfg, "github_update_check_enabled", True)):
            return
        try:
            from github_update_check import compare_semver

            dis = str(settings_get(self.cfg, "github_update_dismissed_version", "") or "").strip()
            if dis and compare_semver(APP_VERSION, dis) >= 0:
                settings_set(self.cfg, "github_update_dismissed_version", "")
        except Exception:
            pass
        threading.Thread(target=self._github_update_check_worker_startup, daemon=True, name="github-update").start()

    def _github_update_check_worker_startup(self) -> None:
        from github_update_check import fetch_latest_release, is_newer_than_installed

        try:
            info = fetch_latest_release()
            if info is None or not is_newer_than_installed(info):
                return
            dismissed = str(settings_get(self.cfg, "github_update_dismissed_version", "") or "").strip()
            if dismissed == info.version:
                return
            self.after(0, lambda i=info: self._github_update_prompt_new_version(i, from_startup=True))
        except Exception:
            logging.getLogger("rootrecord.github_update").debug("startup update check failed", exc_info=True)

    def _manual_check_for_updates(self) -> None:
        threading.Thread(target=self._github_update_check_worker_manual, daemon=True, name="github-update-manual").start()

    def _github_update_check_worker_manual(self) -> None:
        from github_update_check import compare_semver, fetch_latest_release, is_newer_than_installed

        try:
            info = fetch_latest_release()

            def finish() -> None:
                try:
                    if not self.winfo_exists():
                        return
                except Exception:
                    return
                if info is None:
                    messagebox.showwarning(
                        "RootRecord Business Manager",
                        "Could not reach GitHub to check for updates.\n"
                        "Check your network connection and try again later.",
                    )
                    return
                if is_newer_than_installed(info):
                    self._github_update_prompt_new_version(info, from_startup=False)
                elif compare_semver(info.version, APP_VERSION) < 0:
                    messagebox.showinfo(
                        "RootRecord Business Manager",
                        f"This build ({APP_VERSION}) is newer than the latest published GitHub release ({info.version}).",
                    )
                else:
                    messagebox.showinfo(
                        "RootRecord Business Manager",
                        f"You are up to date ({APP_VERSION}).",
                    )

            self.after(0, finish)
        except Exception:
            logging.getLogger("rootrecord.github_update").debug("manual update check failed", exc_info=True)

    def _github_update_prompt_new_version(self, info: Any, *, from_startup: bool) -> None:
        from github_update_check import GithubLatestRelease

        if not isinstance(info, GithubLatestRelease):
            return
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        extra = "" if from_startup else "\n\n(You opened this from About: Check for updates.)"
        r = messagebox.askyesnocancel(
            "RootRecord Business Manager",
            f"A newer release is available: {info.version}\n"
            f"You are running {APP_VERSION}.\n\n"
            "Yes: open the installer download in your browser.\n"
            "No: ask again next time you start the app.\n"
            "Cancel: stop offering this version until a newer release exists."
            f"{extra}",
        )
        if r is True:
            url = (info.installer_download_url or "").strip() or info.html_url
            try:
                webbrowser.open(url)
            except Exception:
                messagebox.showerror("RootRecord Business Manager", f"Could not open:\n{url}")
        elif r is None:
            settings_set(self.cfg, "github_update_dismissed_version", info.version)

    def _maybe_show_trial_welcome_popup(self) -> None:
        """One-time info when opening the app on an active trial (license API + cache)."""
        try:
            from license_client import read_cache_file
            from license_config import get_license_api_config

            if not get_license_api_config():
                return
            if settings_get(self.cfg, "trial_welcome_popup_shown", False):
                return
            raw = read_cache_file() or {}
            if str(raw.get("access")) != "full" or str(raw.get("reason")) != "trialing":
                return
            messagebox.showinfo(
                "RootRecord — Free trial",
                "You're on a free trial of RootRecord Business Manager.\n\n"
                "Subscribe before your trial ends to keep full access (use Billing in the sidebar).\n\n"
                "The status bar at the bottom shows your trial time remaining.",
            )
            settings_set(self.cfg, "trial_welcome_popup_shown", True)
        except Exception:
            pass

    def _refresh_license_banner_text(self) -> None:
        """Update trial countdown in the footer about once per minute."""
        try:
            from license_client import license_footer_hints
            from license_config import get_license_api_config

            if not get_license_api_config():
                return
            h = license_footer_hints(api_configured=True)
            var = getattr(self, "_license_banner_var", None)
            if var is not None and h.line and h.tick_trial:
                var.set(h.line)
                self._purge_footer_ctk_label_placeholders()
                self.after(60_000, self._refresh_license_banner_text)
        except Exception:
            pass

    def _on_subscribe_billing(self) -> None:
        """Same as Billing: open the Stripe product / payment link in the browser."""
        self._on_billing_activate_services()

    def _favicon_candidate_paths(self) -> list[Path]:
        out: list[Path] = []
        seen: set[str] = set()
        for d in iter_app_bundle_asset_dirs():
            p = d / "favicon.ico"
            try:
                key = str(p.resolve())
            except Exception:
                key = str(p)
            if key not in seen:
                seen.add(key)
                out.append(p)
        return out

    def _apply_app_icon(self) -> None:
        for ico in self._favicon_candidate_paths():
            if not ico.is_file():
                continue
            p = str(ico.resolve())
            try:
                self.iconbitmap(p)
                return
            except Exception:
                pass
            try:
                self.iconbitmap(default=p)
                return
            except Exception:
                pass

    def _apply_window_icon(self, win: Any) -> None:
        """Set taskbar/window icon. Toplevels need explicit bitmap= on Windows; default= alone can fail."""
        for ico in self._favicon_candidate_paths():
            if not ico.is_file():
                continue
            p = str(ico.resolve())
            try:
                win.iconbitmap(p)
                return
            except Exception:
                pass
            try:
                win.iconbitmap(default=p)
                return
            except Exception:
                pass

    def _safe_destroy_window(self, win: Any) -> None:
        """Release modal grab before destroying; omitting this can freeze the app on Windows."""
        if win is None:
            return
        try:
            if win.winfo_exists():
                try:
                    win.grab_release()
                except Exception:
                    pass
                win.destroy()
        except Exception:
            try:
                win.destroy()
            except Exception:
                pass

    def _about_placeholder_pil(self) -> Any:
        """In-app graphic when no ``about_page_graphic.jpg`` is shipped beside the executable."""
        from PIL import Image, ImageDraw, ImageFont

        w, h = 420, 420
        im = Image.new("RGB", (w, h), (26, 30, 36))
        dr = ImageDraw.Draw(im)
        dr.rectangle([0, 0, w, 68], fill=(38, 110, 88))
        font = ImageFont.load_default()
        dr.text((20, 24), "RootRecord", fill=(235, 240, 245), font=font)
        dr.text((20, 200), "Add about_page_graphic.jpg next to the app to customize.", fill=(130, 148, 168), font=font)
        return im

    def _resolve_about_panel_pil(self) -> Any | None:
        """Load About art: explicit asset names, then splash ``build/branding/Loading.*``, else Pillow placeholder.

        Returns None if Pillow is unavailable or image load fails (caller uses canvas fallback).
        """
        try:
            from PIL import Image
        except ImportError:
            return None

        log = logging.getLogger("rootrecord.ui")
        path = resolve_shipped_asset(
            "about_page_graphic.jpg",
            "about page grahic.jpg",
            "about page graphic.jpg",
            "about.png",
            "about.jpg",
            "About.png",
        )
        if path is None:
            try:
                from branding_theme import first_loading_image_path

                path = first_loading_image_path()
            except Exception:
                path = None

        if path is None:
            for d in iter_app_bundle_asset_dirs():
                cand = d / "assets" / "about_panel_default.png"
                if cand.is_file():
                    path = cand
                    break

        if path is not None:
            try:
                im = Image.open(path)
                if im.mode == "P" and "transparency" in im.info:
                    im = im.convert("RGBA")
                if im.mode in ("RGBA", "LA"):
                    rgba = im.convert("RGBA")
                    base = Image.new("RGB", rgba.size, (248, 249, 250))
                    base.paste(rgba, mask=rgba.split()[3])
                    im = base
                else:
                    im = im.convert("RGB")
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS  # type: ignore[attr-defined]
                im.thumbnail((640, 640), resample)
                return im
            except Exception:
                log.exception("Could not read About image: %s", path)
                return None
        return self._about_placeholder_pil()

    def _pack_about_side_image_fallback_canvas(self, right: ctk.CTkFrame) -> None:
        """Branded placeholder without Pillow (frozen builds, or CTkImage failure)."""
        bg = getattr(self, "_theme_panel", None) or "#2b2b2b"
        accent = getattr(self, "_theme_accent", "#2B8A8F")
        fg = getattr(self, "_theme_text_heading", "#DCE4EE")
        muted = getattr(self, "_theme_text_muted", "#9FA3A9")
        canvas = tk.Canvas(right, width=420, height=420, highlightthickness=0, bg=bg)
        canvas.pack(fill="both", expand=True)
        canvas.create_rectangle(0, 0, 420, 76, fill=accent, outline="")
        canvas.create_text(20, 38, text="RootRecord", anchor="w", fill=fg, font=("Segoe UI", 18, "bold"))
        canvas.create_text(
            20,
            110,
            width=380,
            anchor="nw",
            fill=muted,
            font=("Segoe UI", 10),
            justify="left",
            text=(
                "Optional About artwork was not loaded (Pillow missing in this build, or image error).\n\n"
                "Rebuild with Pillow bundled, or add about_page_graphic.jpg next to the executable, "
                "or ship build/branding/Loading.png for the installer."
            ),
        )

    def _pack_about_side_image(self, right: ctk.CTkFrame) -> None:
        """Right column graphic on About pages (shipped file, Pillow placeholder, or Tk canvas fallback)."""
        self._about_image_ctk = None
        log = logging.getLogger("rootrecord.ui")
        pil: Any | None = None
        try:
            pil = self._resolve_about_panel_pil()
        except Exception:
            log.exception("_resolve_about_panel_pil failed")
        if pil is not None:
            try:
                sz = (420, 420)
                self._about_image_ctk = ctk.CTkImage(light_image=pil, dark_image=pil, size=sz)
                ctk.CTkLabel(right, text="", image=self._about_image_ctk).pack(fill="both", expand=True)
                return
            except Exception:
                log.exception("About panel CTkImage failed")
        self._pack_about_side_image_fallback_canvas(right)

    def _popup_field_style_kwargs(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """CTkEntry / CTkComboBox styling for modal dialogs.

        Brand-derived ``_theme_panel`` is often only ~10% lighter than ``_theme_bg``, so CTk
        entry boxes can look like empty space. Use CustomTkinter's built-in dark palette
        (same as ``dark-blue`` theme) so fields always contrast with any splash-derived bg.
        """
        entry_kw: dict[str, Any] = {
            "fg_color": "#343638",
            "text_color": "#DCE4EE",
            "placeholder_text_color": "#9FA3A9",
            "border_color": "#565B5E",
            "border_width": 2,
            "corner_radius": 6,
            "height": 34,
        }
        combo_kw: dict[str, Any] = {
            "fg_color": "#343638",
            "border_color": "#565B5E",
            "border_width": 2,
            "corner_radius": 6,
            "button_color": "#565B5E",
            "button_hover_color": "#7A848D",
            "dropdown_fg_color": "#343638",
            "dropdown_hover_color": "#4A4D50",
            "dropdown_text_color": "#DCE4EE",
            "text_color": "#DCE4EE",
            "height": 34,
        }
        return entry_kw, combo_kw

    def _attach_tooltip(self, widget: Any, text: str) -> None:
        tip_text = (text or "").strip()
        if not tip_text:
            return

        def _show(_e: Any = None) -> None:
            try:
                if not bool(settings_get(self.cfg, "help_bubbles_enabled", True)):
                    return
                if getattr(widget, "_rr_tip_win", None) is not None:
                    return
                # Plain tk only: CTkLabel inside tk.Toplevel often paints an empty white box on Windows.
                tip_bg = "#2b2f36"
                tip_fg = "#d7dbe2"
                tw = tk.Toplevel(self)
                tw.wm_overrideredirect(True)
                tw.configure(bg=tip_bg, highlightthickness=0)
                self._apply_window_icon(tw)
                x = int(widget.winfo_rootx()) + int(widget.winfo_width()) + 8
                y = int(widget.winfo_rooty()) + 2
                tw.geometry(f"+{x}+{y}")
                outer = tk.Frame(tw, bg=tip_bg, padx=8, pady=5)
                outer.pack(fill="both", expand=True)
                tk.Label(
                    outer,
                    text=tip_text,
                    bg=tip_bg,
                    fg=tip_fg,
                    justify="left",
                    wraplength=320,
                    font=("Segoe UI", 11),
                ).pack(anchor="nw")
                widget._rr_tip_win = tw
            except Exception:
                pass

        def _hide(_e: Any = None) -> None:
            tw = getattr(widget, "_rr_tip_win", None)
            if tw is not None:
                try:
                    tw.destroy()
                except Exception:
                    pass
                widget._rr_tip_win = None

        widget.bind("<Enter>", _show, add="+")
        widget.bind("<Leave>", _hide, add="+")
        widget.bind("<ButtonPress>", _hide, add="+")

    def _refresh_help_bubbles_visibility(self) -> None:
        enabled = bool(settings_get(self.cfg, "help_bubbles_enabled", True))
        for b in getattr(self, "_help_bubbles", []):
            try:
                if enabled:
                    b.configure(text="?", width=18, height=18)
                else:
                    b.configure(text="", width=1, height=1)
            except Exception:
                pass

    def _add_help_bubble(self, parent: Any, text: str, *, side: str = "left", padx: tuple[int, int] = (6, 0)) -> Any:
        bubble = ctk.CTkLabel(
            parent,
            text="?",
            width=18,
            height=18,
            fg_color=self._theme_help_btn_bg,
            text_color=self._theme_help_btn_fg,
            corner_radius=9,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        bubble.pack(side=side, padx=padx)
        if not hasattr(self, "_help_bubbles"):
            self._help_bubbles: list[Any] = []
        self._help_bubbles.append(bubble)
        self._attach_tooltip(bubble, text)
        self._refresh_help_bubbles_visibility()
        return bubble

    def _on_app_close(self) -> None:
        st = load_user_state(self.uid)
        # Always prompt when state says user is active, even if start timestamp is missing.
        is_active = bool(st and st.current_mode in ("working", "on_break"))
        if is_active:
            choice = messagebox.askyesnocancel(
                "RootRecord Business Manager",
                "You are still clocked in.\n\n"
                "Yes: Clock me out and close\n"
                "No: Keep me active and close\n"
                "Cancel: Stay in app",
            )
            if choice is None:
                return
            if choice:
                try:
                    # Persist state/time entry before app exits.
                    clock_out(self.cfg, self.uid)
                except Exception as exc:  # noqa: BLE001
                    messagebox.showerror(
                        "RootRecord Business Manager",
                        f"Could not clock out before closing:\n{exc}",
                    )
                    return
        settings_set(self.cfg, "last_app_closed_utc", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        self._stop_tray_icon()
        self.destroy()

    def _on_window_unmap(self, _event: tk.Event) -> None:
        # Optional behavior: minimize to system tray ("hidden icons").
        if not bool(settings_get(self.cfg, "minimize_to_hidden_icons_enabled", False)):
            return
        try:
            if self.state() != "iconic":
                return
        except Exception:
            return
        self._minimize_to_tray()

    def _minimize_to_tray(self) -> None:
        if self._tray_icon_running:
            try:
                self.withdraw()
            except Exception:
                pass
            return
        try:
            import pystray
            from PIL import Image
        except Exception:
            # Fallback: normal minimize when tray support is unavailable.
            return

        meipass = Path(getattr(sys, "_MEIPASS", "") or "")
        icon_candidates = [
            meipass / "favicon.ico" if meipass else None,
            Path(sys.executable).resolve().parent / "favicon.ico" if getattr(sys, "frozen", False) else None,
            Path(__file__).resolve().parent / "favicon.ico",
            Path(__file__).resolve().parents[2] / "favicon.ico",
        ]
        icon_path = next((p for p in icon_candidates if p is not None and p.is_file()), None)
        if icon_path is None:
            return
        try:
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:
                resample = Image.LANCZOS  # type: ignore[attr-defined]
            raw = Image.open(icon_path).convert("RGBA")
            raw.thumbnail((64, 64), resample)
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            ox = (64 - raw.width) // 2
            oy = (64 - raw.height) // 2
            img.paste(raw, (ox, oy), raw)
        except Exception:
            return

        def on_open(_icon=None, _item=None) -> None:
            self.after(0, self._restore_from_tray)

        def on_exit(_icon=None, _item=None) -> None:
            self.after(0, self._on_app_close)

        menu = pystray.Menu(
            pystray.MenuItem("Open RootRecord", on_open, default=True),
            pystray.MenuItem("Exit", on_exit),
        )
        # Distinct internal name avoids collisions with other tray icons on Windows.
        self._tray_icon = pystray.Icon(
            "RootRecordBusinessManagerTray",
            img,
            "RootRecord Business Manager",
            menu,
        )
        self._tray_icon_running = True
        try:
            self.withdraw()
        except Exception:
            pass

        log = logging.getLogger("rootrecord.ui")

        def _run_tray() -> None:
            try:
                self._tray_icon.run(setup=lambda icon: setattr(icon, "visible", True))
            except Exception:
                log.exception("System tray icon failed")
                try:
                    self.after(0, self._tray_run_failed_restore)
                except Exception:
                    pass

        # Daemon: must pair with ``_stop_tray_icon`` before exit; avoids exit hangs if ``stop()`` is slow.
        threading.Thread(target=_run_tray, name="rootrecord-pystray", daemon=True).start()

    def _tray_run_failed_restore(self) -> None:
        """If the tray loop dies, bring the window back so the app is not headless."""
        icon = getattr(self, "_tray_icon", None)
        self._tray_icon = None
        self._tray_icon_running = False
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
        except Exception:
            pass

    def _restore_from_tray(self) -> None:
        self._stop_tray_icon()
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _stop_tray_icon(self) -> None:
        icon = getattr(self, "_tray_icon", None)
        self._tray_icon = None
        self._tray_icon_running = False
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass

    def _startup_run_value_name(self) -> str:
        return "RootRecordBusinessManager"

    def _startup_command(self) -> str:
        if getattr(sys, "frozen", False):
            return f"\"{Path(sys.executable).resolve()}\""
        return f"\"{Path(sys.executable).resolve()}\" \"{Path(__file__).resolve()}\""

    def _is_start_on_login_enabled(self) -> bool:
        if winreg is None:
            return False
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            ) as key:
                val, _ = winreg.QueryValueEx(key, self._startup_run_value_name())
                return bool(str(val).strip())
        except OSError:
            return False

    def _set_start_on_login_enabled(self, enabled: bool) -> None:
        if winreg is None:
            raise RuntimeError("Start-on-login is only available on Windows.")
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(key, self._startup_run_value_name(), 0, winreg.REG_SZ, self._startup_command())
            else:
                try:
                    winreg.DeleteValue(key, self._startup_run_value_name())
                except OSError:
                    pass

    def _show_startup_clocked_in_notice_if_needed(self) -> None:
        if not bool(settings_get(self.cfg, "startup_clocked_in_prompt_enabled", True)):
            return
        st = load_user_state(self.uid)
        if not st or not st.current_work_start_utc or st.current_mode not in ("working", "on_break"):
            return
        closed_s = str(settings_get(self.cfg, "last_app_closed_utc", "") or "").strip()
        closed_for = "unknown duration"
        if closed_s:
            try:
                then = datetime.fromisoformat(closed_s.replace("Z", "+00:00"))
                sec = max(0, int((datetime.now(timezone.utc) - then).total_seconds()))
                h, rem = divmod(sec, 3600)
                m, s = divmod(rem, 60)
                if h > 0:
                    closed_for = f"{h}h {m}m"
                elif m > 0:
                    closed_for = f"{m}m {s}s"
                else:
                    closed_for = f"{s}s"
            except ValueError:
                closed_for = "unknown duration"
        mode_txt = "on break" if st.current_mode == "on_break" else "clocked in"
        messagebox.showinfo(
            "RootRecord Business Manager",
            "You were already active before opening the app.\n\n"
            f"Status: {mode_txt}\n"
            f"Program was closed for: {closed_for}",
        )

    def _backup_root_dir(self) -> Path:
        return Path(self.cfg.db_path).resolve().parent / "backups"

    def _perform_database_backup(self, *, reason: str = "manual") -> Path:
        self._set_process_status(f"Running {reason} backup...", auto_clear_ms=None)
        db_path = Path(self.cfg.db_path).resolve()
        if not db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")
        out_dir = self._backup_root_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        out_path = out_dir / f"rootrecord-backup-{stamp}.sqlite3"
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(out_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        settings_set(self.cfg, "last_backup_utc", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        settings_set(self.cfg, "last_backup_reason", reason)
        self._set_process_status(f"{reason.capitalize()} backup complete.", auto_clear_ms=1800)
        self._schedule_cloud_copy_upload(out_path)
        try:
            from sync_engine import notify_data_changed

            notify_data_changed(self.cfg, local_user_id=self.uid)
        except Exception:
            pass
        return out_path

    def _schedule_cloud_copy_upload(self, sqlite_path: Path) -> None:
        """Upload newest local backup copy in the background when online backup is enabled."""

        def run() -> None:
            try:
                from backup_r2_client import cloud_backup_enabled, upload_sqlite_file

                if not cloud_backup_enabled(self.cfg):
                    return
                ok, msg = upload_sqlite_file(self.cfg, sqlite_path, filename=sqlite_path.name)
                if ok:
                    self.after(
                        0,
                        lambda: self._set_process_status("Online backup copy saved.", auto_clear_ms=2400),
                    )
                else:
                    self.after(
                        0,
                        lambda m=msg: self._set_process_status(f"Online backup: {m}", auto_clear_ms=4000),
                    )
            except Exception as exc:
                self.after(
                    0,
                    lambda: self._set_process_status("Online backup could not finish. Try again later.", auto_clear_ms=4000),
                )

        threading.Thread(target=run, daemon=True, name="rootrecord-cloud-backup").start()

    def _cloud_backup_tick(self) -> None:
        """When online, upload newest local backup if it changed since last successful cloud copy (not tied to license sync)."""
        try:

            def run() -> None:
                try:
                    from backup_r2_client import maybe_upload_newest_if_stale

                    ok, msg = maybe_upload_newest_if_stale(self.cfg)
                    benign = frozenset(
                        {
                            "Online backup is off.",
                            "Already up to date.",
                            "Online backup is not available on this copy.",
                            "Save Account Settings with online backup turned on.",
                        }
                    )
                    if ok:
                        self.after(
                            0,
                            lambda: self._set_process_status("Online backup copy updated.", auto_clear_ms=2200),
                        )
                    elif msg not in benign:
                        self.after(
                            0,
                            lambda m=msg: self._set_process_status(f"Online backup: {m}", auto_clear_ms=6000),
                        )
                except Exception:
                    pass

            threading.Thread(target=run, daemon=True, name="rootrecord-cloud-backup-tick").start()
        except Exception:
            pass
        try:
            self.after(300_000, self._cloud_backup_tick)
        except Exception:
            pass

    def _auto_backup_if_due(self) -> None:
        try:
            import license_gate as _lg

            if _lg.is_read_only():
                return
        except Exception:
            pass
        if not bool(settings_get(self.cfg, "auto_backup_enabled", False)):
            return
        try:
            every_h = int(settings_get(self.cfg, "auto_backup_interval_hours", 24))
        except (TypeError, ValueError):
            every_h = 24
        every_h = max(1, every_h)
        last_s = str(settings_get(self.cfg, "last_backup_utc", "") or "").strip()
        due = True
        if last_s:
            try:
                last_dt = datetime.fromisoformat(last_s.replace("Z", "+00:00"))
                due = (datetime.now(timezone.utc) - last_dt).total_seconds() >= every_h * 3600
            except ValueError:
                due = True
        if due:
            try:
                self._perform_database_backup(reason="auto")
            except Exception:
                # Non-fatal: never block app startup over backup failures.
                self._set_process_status("Auto backup failed.", auto_clear_ms=2200)
                pass

    def _open_backup_folder(self) -> None:
        out_dir = self._backup_root_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(out_dir))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("RootRecord Business Manager", f"Could not open backup folder:\n{exc}")

    def _switch_nav(self, tab_name: str) -> None:
        prev = getattr(self, "_nav_current_tab", None)
        if prev == "Billing" and tab_name != "Billing":
            self._cancel_billing_poll()
        self._nav_current_tab = tab_name
        self.tabview.set(tab_name)
        for name, btn in self._nav_buttons.items():
            if name == tab_name:
                btn.configure(fg_color=self._theme_nav_active)
            else:
                btn.configure(fg_color=self._theme_nav_idle)
        if tab_name == "Dashboard":
            self._refresh_dashboard()
        elif tab_name == "Finance & Clients":
            self._switch_finance_clients_section("Money")
            self._refresh_expenses()
            self._refresh_debts()
            self._refresh_clients_list()
            self._refresh_invoices_list()
        elif tab_name == "Clients":
            self._refresh_clients_list()
        elif tab_name == "Invoices":
            self._refresh_invoices_list()
        elif tab_name == "Schedule & Bookings":
            self._refresh_schedule_list()
        elif tab_name == "Stock & Supplies":
            self._refresh_stock_list()
            self._refresh_supplies_list()
        elif tab_name == "Work Log":
            try:
                self._refresh_calendar()
            except Exception:
                pass
        elif tab_name == "Account Settings":
            self._refresh_account_views()
        elif tab_name == "Program Settings":
            self._refresh_settings_views()
        elif tab_name == "Billing":
            try:
                self._refresh_billing_panel()
                self._schedule_billing_poll()
            except Exception:
                logging.getLogger("rootrecord.ui").exception("Billing panel refresh failed")
                try:
                    self._billing_apply_panel_texts_fallback()
                except Exception:
                    pass

    def _refresh_all_business_scoped_views(self) -> None:
        self._refresh_dashboard()
        self._refresh_expenses()
        self._refresh_debts()
        self._refresh_clients_list()
        self._refresh_invoices_list()
        self._refresh_schedule_list()
        self._refresh_stock_list()
        self._refresh_supplies_list()
        try:
            self._refresh_calendar()
        except Exception:
            pass
        try:
            self._refresh_report_panel()
        except Exception:
            pass

    def _refresh_business_profiles_ui(self) -> None:
        enabled = bool(multi_business_enabled(self.cfg))
        if getattr(self, "_account_profile_row", None):
            if enabled:
                if not self._account_profile_row.winfo_ismapped():
                    self._account_profile_row.pack(fill="x", pady=(0, 8))
                if getattr(self, "_account_profile_hint", None):
                    self._account_profile_hint.configure(text="Add a profile, or remove the one selected in the sidebar")
            else:
                self._account_profile_row.pack_forget()
                if getattr(self, "_account_profile_hint", None):
                    self._account_profile_hint.configure(text="Turn on multi-business mode to add or remove profiles")
        if getattr(self, "_profile_label", None) and getattr(self, "_profile_switch_menu", None):
            if enabled:
                if not self._profile_label.winfo_ismapped():
                    self._profile_label.pack(padx=16, anchor="w", pady=(8, 2))
                if not self._profile_switch_menu.winfo_ismapped():
                    self._profile_switch_menu.pack(padx=16, fill="x")
            else:
                self._profile_label.pack_forget()
                self._profile_switch_menu.pack_forget()
                return
        add_option = "Add Business Profile..."
        try:
            rows = list_business_profiles(self.cfg, self.uid, include_archived=False)
        except Exception:
            rows = [{"id": 0, "name": "Master (All Businesses)"}]
        real_rows = [r for r in rows if int(r.get("id") or 0) > 0]
        show_master = len(real_rows) > 1
        options: list[str] = []
        self._profile_name_to_id = {}
        for r in rows:
            rid = int(r.get("id") or 0)
            if rid == 0 and not show_master:
                continue
            name = str(r.get("name") or "").strip() or f"Business {int(r.get('id') or 0)}"
            options.append(name)
            self._profile_name_to_id[name] = rid
        if not options:
            options = ["Master (All Businesses)"]
            self._profile_name_to_id = {"Master (All Businesses)": 0}
        options.append(add_option)
        self._profile_name_to_id[add_option] = -1
        active_id = get_active_business_id(self.cfg)
        if active_id == 0 and not show_master and real_rows:
            active_id = int(real_rows[0].get("id") or 1)
            set_active_business_id(self.cfg, active_id)
        active_name = next((n for n, i in self._profile_name_to_id.items() if i == active_id), options[0])
        self._suppress_profile_change = True
        self._profile_switch_menu.configure(values=options)
        self._active_business_var.set(active_name)
        self._suppress_profile_change = False

    def _on_business_profile_change(self, selected_name: str) -> None:
        if bool(getattr(self, "_suppress_profile_change", False)):
            return
        if selected_name == "Add Business Profile..." or int(self._profile_name_to_id.get(selected_name, 1)) == -1:
            self._prompt_add_business_profile()
            self._refresh_business_profiles_ui()
            return
        bid = int(self._profile_name_to_id.get(selected_name, 1))
        set_active_business_id(self.cfg, bid)
        self._set_process_status(f"Switched profile to {selected_name}", auto_clear_ms=1500)
        self._refresh_all_business_scoped_views()

    def _prompt_add_business_profile(self, initial_name: str = "") -> None:
        seed = (initial_name or "").strip()
        try:
            dlg = ctk.CTkInputDialog(text="Enter new business profile name", title="Add Business Profile")
            name = (dlg.get_input() or "").strip()
        except Exception:
            name = ""
        if not name:
            name = seed
        if not name:
            return
        if hasattr(self, "_new_business_name_var"):
            self._new_business_name_var.set(name)
            self._add_business_profile()
            return
        try:
            new_id = save_business_profile(self.cfg, self.uid, profile_id=None, name=name)
            settings_set(self.cfg, "multi_business_enabled", True)
            settings_set(self.cfg, "active_business_id", int(new_id))
            self._refresh_business_profiles_ui()
            self._refresh_all_business_scoped_views()
        except Exception:
            pass

    def _on_business_name_typing(self, *_args: Any) -> None:
        if not getattr(self, "_profile_switch_menu", None):
            return
        active_id = get_active_business_id(self.cfg)
        if active_id <= 0:
            return
        typed = str(self._biz_vars.get("business_name").get() if self._biz_vars.get("business_name") else "").strip()
        if not typed or typed == "Add Business Profile...":
            return
        old_name = next((n for n, i in self._profile_name_to_id.items() if i == active_id), "")
        if not old_name or old_name == typed:
            return
        vals = list(self._profile_switch_menu.cget("values"))
        if old_name in vals:
            vals[vals.index(old_name)] = typed
        self._profile_name_to_id.pop(old_name, None)
        self._profile_name_to_id[typed] = active_id
        self._suppress_profile_change = True
        self._profile_switch_menu.configure(values=vals)
        self._active_business_var.set(typed)
        self._suppress_profile_change = False

    def _set_process_status(self, text: str, *, auto_clear_ms: int | None = 2200) -> None:
        self._process_status_var.set(text.strip() or "")
        if not bool(settings_get(self.cfg, "show_process_status_banner_enabled", True)):
            return
        self._process_status_label.place(relx=0.5, y=8, anchor="n")
        if self._process_status_clear_job:
            try:
                self.after_cancel(self._process_status_clear_job)
            except Exception:
                pass
            self._process_status_clear_job = None
        if auto_clear_ms is not None:
            self._process_status_clear_job = self.after(auto_clear_ms, self._clear_process_status)

    def _clear_process_status(self) -> None:
        self._process_status_var.set("")
        self._process_status_label.place_forget()
        self._process_status_clear_job = None

    def _toggle_sidebar(self) -> None:
        if not getattr(self, "_side", None):
            return
        self._sidebar_collapsed = not self._sidebar_collapsed
        if self._sidebar_collapsed:
            self._side.grid_remove()
            self._sidebar_restore_btn.place(x=6, y=6)
        else:
            self._side.grid()
            self._sidebar_restore_btn.place_forget()

    def _build_finance_clients(self) -> None:
        t = self.tabview.tab("Finance & Clients")
        ctk.CTkLabel(
            t,
            text="Finance & Clients",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            t,
            text="Money, client records, invoices, and tax estimates in one place.",
            text_color=self._theme_text_muted,
        ).pack(anchor="w", pady=(0, 8))

        nav = ctk.CTkFrame(t, fg_color="transparent")
        nav.pack(anchor="w", pady=(0, 8))
        section_names = ("Money", "Clients", "Invoices", "Debts", "Tax Estimator")
        self._finance_clients_nav_buttons: dict[str, ctk.CTkButton] = {}
        for name in section_names:
            btn = ctk.CTkButton(
                nav,
                text=name,
                width=120 if name not in ("Tax Estimator",) else 140,
                command=lambda n=name: self._switch_finance_clients_section(n),
            )
            btn.pack(side="left", padx=(0, 8))
            self._finance_clients_nav_buttons[name] = btn

        self._finance_clients_host = ctk.CTkFrame(t, fg_color="transparent")
        self._finance_clients_host.pack(fill="both", expand=True)
        self._finance_clients_sections: dict[str, ctk.CTkFrame] = {}
        for name in section_names:
            frame = ctk.CTkFrame(self._finance_clients_host, fg_color="transparent")
            self._finance_clients_sections[name] = frame

        self._build_money()
        self._build_clients()
        self._build_invoices()
        self._build_debts()
        self._build_tax_estimator()
        self._switch_finance_clients_section("Money")

    def _switch_finance_clients_section(self, section: str) -> None:
        if not getattr(self, "_finance_clients_sections", None):
            return
        for name, frame in self._finance_clients_sections.items():
            if name == section:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        for name, btn in self._finance_clients_nav_buttons.items():
            if name == section:
                btn.configure(fg_color=self._theme_nav_active)
            else:
                btn.configure(fg_color=self._theme_nav_idle)
        if section == "Debts":
            self._refresh_debts()

    def _open_database_folder(self) -> None:
        folder = Path(self.cfg.db_path).resolve().parent
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("RootRecord Business Manager", f"Could not open database folder:\n{exc}")

    def _import_old_database(self) -> None:
        src_raw = filedialog.askopenfilename(
            title="Select old RootRecord database",
            filetypes=[("Database files", "*.db *.sqlite *.sqlite3"), ("All files", "*.*")],
        )
        if not src_raw:
            return
        src = Path(src_raw).expanduser().resolve()
        dst = Path(self.cfg.db_path).resolve()
        if not src.is_file():
            messagebox.showerror("RootRecord Business Manager", f"File not found:\n{src}")
            return
        if src == dst:
            messagebox.showinfo("RootRecord Business Manager", "Selected database is already the active database.")
            return
        ok = messagebox.askyesno(
            "RootRecord Business Manager",
            "This will replace the active local database with the selected file.\n\n"
            "A backup copy of your current active database will be created first.\n\n"
            "Continue?",
        )
        if not ok:
            return

        dst.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        backup_path = dst.with_name(f"{dst.stem}.before-import-{stamp}{dst.suffix}")
        fd, tmp_name = tempfile.mkstemp(prefix="rootrecord-import-", suffix=".db", dir=str(dst.parent))
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            src_conn = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
            dst_conn = sqlite3.connect(str(tmp))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
                src_conn.close()

            if dst.exists():
                shutil.copy2(dst, backup_path)

            for sidecar in (dst.with_suffix(dst.suffix + "-wal"), dst.with_suffix(dst.suffix + "-shm")):
                try:
                    sidecar.unlink(missing_ok=True)
                except OSError:
                    pass
            os.replace(str(tmp), str(dst))
            messagebox.showinfo(
                "RootRecord Business Manager",
                "Database import complete.\n\n"
                f"Active database:\n{dst}\n\n"
                f"Backup saved:\n{backup_path}",
            )
            self._refresh_account_views()
            self._refresh_dashboard()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("RootRecord Business Manager", f"Database import failed:\n{exc}")
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _refresh_account_views(self) -> None:
        if hasattr(self, "_multi_business_enabled_var"):
            self._multi_business_enabled_var.set(bool(multi_business_enabled(self.cfg)))
        if hasattr(self, "_profile_switch_menu"):
            self._refresh_business_profiles_ui()
        self._refresh_account_local_paths()
        self._refresh_account_cloud_login()

    def _refresh_account_local_paths(self) -> None:
        if getattr(self, "_account_local_db_label", None):
            self._account_local_db_label.configure(text=str(Path(self.cfg.db_path).resolve()))
        self._refresh_cloud_backup_status_label()

    def _refresh_cloud_backup_status_label(self) -> None:
        lbl = getattr(self, "_cloud_backup_status_label", None)
        if lbl is None:
            return
        try:
            from backup_r2_client import cloud_backup_status_summary

            lbl.configure(text=cloud_backup_status_summary(self.cfg))
        except Exception:
            lbl.configure(text="")

    def _on_cloud_backup_test_connection(self) -> None:
        self._set_process_status("Testing online backup service...", auto_clear_ms=None)

        def run() -> None:
            try:
                from backup_r2_client import backup_api_base_url, ping_backup_service

                base = backup_api_base_url(self.cfg)
                if not base:
                    self.after(
                        0,
                        lambda: (
                            self._set_process_status("Online backup: no service URL.", auto_clear_ms=4000),
                            messagebox.showwarning(
                                "RootRecord Business Manager",
                                "No backup service URL is configured for this copy of the app.",
                            ),
                        ),
                    )
                    return
                ok, msg = ping_backup_service(base)
            except Exception as exc:
                ok, msg = False, str(exc)[:300]

            def finish() -> None:
                self._refresh_cloud_backup_status_label()
                if ok:
                    self._set_process_status("Online backup service OK.", auto_clear_ms=2400)
                    messagebox.showinfo("RootRecord Business Manager", "Backup service reachable and R2 is ready.")
                else:
                    self._set_process_status(f"Online backup test failed: {msg}", auto_clear_ms=6000)
                    messagebox.showwarning("RootRecord Business Manager", f"Backup service test failed:\n{msg}")

            self.after(0, finish)

        threading.Thread(target=run, daemon=True, name="rootrecord-cloud-backup-ping").start()

    def _on_cloud_backup_upload_now(self) -> None:
        self._set_process_status("Uploading latest backup copy...", auto_clear_ms=None)

        def run() -> None:
            try:
                from backup_r2_client import cloud_backup_enabled, newest_local_backup_file, upload_sqlite_file

                if not cloud_backup_enabled(self.cfg):
                    self.after(
                        0,
                        lambda: (
                            self._set_process_status("Turn on online backup first.", auto_clear_ms=4000),
                            messagebox.showinfo(
                                "RootRecord Business Manager",
                                "Turn on 'Also save a secure online copy', then Save Account Settings, then try again.",
                            ),
                        ),
                    )
                    return
                p = newest_local_backup_file(self.cfg)
                if p is None:
                    self.after(
                        0,
                        lambda: (
                            self._set_process_status("No local backup file to upload.", auto_clear_ms=5000),
                            messagebox.showinfo(
                                "RootRecord Business Manager",
                                "There is no local .sqlite3 backup yet.\n\n"
                                "Use Program Settings: Backup Now (or enable automatic backups), then try again.",
                            ),
                        ),
                    )
                    return
                ok, msg = upload_sqlite_file(self.cfg, p, filename=p.name)
            except Exception as exc:
                ok, msg = False, str(exc)[:400]

            def finish() -> None:
                self._refresh_cloud_backup_status_label()
                if ok:
                    self._set_process_status("Online backup copy saved.", auto_clear_ms=4000)
                    messagebox.showinfo("RootRecord Business Manager", "Latest local backup was uploaded to secure online storage.")
                else:
                    self._set_process_status(f"Online backup: {msg}", auto_clear_ms=6000)
                    messagebox.showwarning("RootRecord Business Manager", msg)

            self.after(0, finish)

        threading.Thread(target=run, daemon=True, name="rootrecord-cloud-backup-manual").start()

    def _build_account(self) -> None:
        t = self.tabview.tab("Account Settings")
        scroll = ctk.CTkScrollableFrame(t, height=620)
        scroll.pack(fill="both", expand=True)
        ctk.CTkLabel(scroll, text="Account Settings", font=ctk.CTkFont(size=18, weight="bold")).pack(
            anchor="w", pady=(0, 4), padx=8
        )
        ctk.CTkLabel(
            scroll,
            text="Online sign-in, business profiles, local database path, and invoice/business details.",
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(0, 8), padx=8)

        self._account_cloud_panel = ctk.CTkFrame(scroll)
        self._account_cloud_panel.pack(fill="x", padx=8, pady=(0, 12))
        ctk.CTkLabel(
            self._account_cloud_panel,
            text="RootRecord account",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=10, pady=(10, 6))
        self._account_login_status_label = ctk.CTkLabel(
            self._account_cloud_panel,
            text="",
            anchor="w",
            justify="left",
            font=ctk.CTkFont(size=13),
        )
        self._account_login_status_label.pack(anchor="w", padx=10, pady=(0, 2))
        self._account_login_email_label = ctk.CTkLabel(
            self._account_cloud_panel,
            text="",
            anchor="w",
            justify="left",
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=12),
        )
        self._account_login_email_label.pack(anchor="w", padx=10, pady=(0, 2))
        self._account_login_detail_label = ctk.CTkLabel(
            self._account_cloud_panel,
            text="",
            anchor="w",
            justify="left",
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=12),
            wraplength=520,
        )
        self._account_login_detail_label.pack(anchor="w", padx=10, pady=(0, 6))
        _cloud_btn_row = ctk.CTkFrame(self._account_cloud_panel, fg_color="transparent")
        _cloud_btn_row.pack(anchor="w", padx=10, pady=(0, 10))
        self._account_logout_btn = ctk.CTkButton(
            _cloud_btn_row,
            text="Log out",
            width=120,
            command=self._on_cloud_logout,
        )
        self._account_logout_btn.pack(side="left")

        content = ctk.CTkFrame(scroll, fg_color="transparent")
        content.pack(fill="x", expand=True, padx=8, pady=(2, 0))
        content.grid_columnconfigure(0, weight=1, uniform="acct")
        content.grid_columnconfigure(1, weight=1, uniform="acct")

        left_col = ctk.CTkFrame(content, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        right_col = ctk.CTkFrame(content, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="nsew", padx=(0, 4))

        self._multi_business_enabled_var = tk.BooleanVar(value=bool(multi_business_enabled(self.cfg)))
        mb_row = ctk.CTkFrame(left_col, fg_color="transparent")
        mb_row.pack(anchor="w", pady=(0, 8))
        ctk.CTkSwitch(
            mb_row,
            text="Enable multi-business mode",
            variable=self._multi_business_enabled_var,
            onvalue=True,
            offvalue=False,
            command=self._on_toggle_multi_business,
        ).pack(side="left")
        self._add_help_bubble(mb_row, "Turn on multiple business profiles. Money totals then scope to the selected profile.")
        prof_row = ctk.CTkFrame(left_col, fg_color="transparent")
        prof_row.pack(fill="x", pady=(0, 8))
        self._account_profile_row = prof_row
        self._new_business_name_var = tk.StringVar(value="")
        self._account_profile_hint = ctk.CTkLabel(
            prof_row,
            text="Add a profile, or remove the one selected in the sidebar",
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=11),
        )
        self._account_profile_hint.pack(anchor="w", pady=(0, 4))
        ctk.CTkEntry(prof_row, textvariable=self._new_business_name_var, width=210, placeholder_text="New business name").pack(side="left", padx=(0, 8))
        ctk.CTkButton(prof_row, text="Add", width=70, command=self._add_business_profile).pack(side="left", padx=(0, 6))
        ctk.CTkButton(prof_row, text="Remove", width=80, command=self._remove_business_profile).pack(side="left")
        self._add_help_bubble(
            prof_row,
            "Remove archives the profile selected in the sidebar (not Master). "
            "If that was your last profile, multi-business mode turns off. Data stays in the database but is hidden for that profile.",
        )

        ctk.CTkLabel(left_col, text="Local database", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", pady=(4, 4)
        )
        self._account_local_db_label = ctk.CTkLabel(
            left_col,
            text=str(Path(self.cfg.db_path).resolve()),
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(family="Consolas", size=11),
            justify="left",
            wraplength=430,
        )
        self._account_local_db_label.pack(anchor="w", pady=(0, 8))
        db_row = ctk.CTkFrame(left_col, fg_color="transparent")
        db_row.pack(anchor="w", pady=(0, 8))
        ctk.CTkButton(db_row, text="Open database folder", width=170, command=self._open_database_folder).pack(side="left", padx=(0, 8))
        ctk.CTkButton(db_row, text="Import old database", width=170, command=self._import_old_database).pack(side="left")

        ctk.CTkLabel(left_col, text="Sync with cloud", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", pady=(16, 4)
        )
        ctk.CTkLabel(
            left_col,
            text=(
                "When you are signed in, RootRecord sends pending updates to your account and pulls in anything "
                "new from your other devices (work session activity is shared this way). "
                "After you add or change data, a background sync runs automatically about once a minute while "
                "you are online—use the button to run it right away. "
                "A full snapshot of your database file is separate: turn on Online backup below if you want that "
                "after each local backup."
            ),
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=11),
            wraplength=430,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))
        sync_btn_row = ctk.CTkFrame(left_col, fg_color="transparent")
        sync_btn_row.pack(anchor="w", pady=(0, 8))
        ctk.CTkButton(
            sync_btn_row,
            text="Sync with Cloud",
            width=200,
            command=self._on_account_sync_with_cloud,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(left_col, text="Online backup copy", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", pady=(16, 4)
        )
        ctk.CTkLabel(
            left_col,
            text=(
                "Optional: after each local backup, RootRecord can also save a protected full-database copy to "
                "secure online storage. Separate from sync above. If you are offline, it tries again later."
            ),
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=11),
            wraplength=430,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))
        self._set_cloud_backup = ctk.CTkSwitch(
            left_col,
            text="Also save a secure online copy after each local backup",
        )
        if settings_get(self.cfg, "cloud_backup_enabled", False):
            self._set_cloud_backup.select()
        self._set_cloud_backup.pack(anchor="w", pady=(0, 8))
        try:
            from backup_r2_client import bootstrap_cloud_backup_if_enabled

            bootstrap_cloud_backup_if_enabled(self.cfg)
        except Exception:
            pass
        self._cloud_backup_status_label = ctk.CTkLabel(
            left_col,
            text="",
            anchor="w",
            justify="left",
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=11),
            wraplength=430,
        )
        self._cloud_backup_status_label.pack(anchor="w", pady=(0, 6))
        cloud_btns = ctk.CTkFrame(left_col, fg_color="transparent")
        cloud_btns.pack(anchor="w", pady=(0, 8))
        ctk.CTkButton(
            cloud_btns,
            text="Test connection",
            width=130,
            command=self._on_cloud_backup_test_connection,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            cloud_btns,
            text="Upload latest backup now",
            width=180,
            command=self._on_cloud_backup_upload_now,
        ).pack(side="left", padx=(0, 8))
        self._refresh_cloud_backup_status_label()

        ctk.CTkLabel(right_col, text="Business details", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", pady=(0, 8)
        )
        bform = ctk.CTkFrame(right_col)
        bform.pack(fill="x", pady=6)
        biz_fields = [
            ("Business name", "business_name"),
            ("Legal name", "business_legal_name"),
            ("Owner", "business_owner"),
            ("Tax ID", "business_tax_id"),
            ("Email", "business_email"),
            ("Phone", "business_phone"),
            ("Website", "business_website"),
            ("Address", "business_address"),
            ("Timezone", "business_timezone"),
        ]
        self._biz_vars: dict[str, tk.StringVar] = {}
        for i, (label, key) in enumerate(biz_fields):
            ctk.CTkLabel(bform, text=label).grid(row=i, column=0, sticky="w", padx=4, pady=4)
            var = tk.StringVar(value=str(settings_get(self.cfg, key, "")))
            self._biz_vars[key] = var
            ctk.CTkEntry(bform, width=420, textvariable=var).grid(row=i, column=1, sticky="ew", padx=4, pady=4)
            hb = ctk.CTkLabel(
                bform,
                text="?",
                width=18,
                height=18,
fg_color=self._theme_help_btn_bg,
                    text_color=self._theme_help_btn_fg,
                corner_radius=9,
                font=ctk.CTkFont(size=11, weight="bold"),
            )
            hb.grid(row=i, column=2, padx=(6, 0), sticky="w")
            self._attach_tooltip(hb, f"Enter your {label.lower()} for invoices, reports, and account details.")
        if self._biz_vars.get("business_name") is not None:
            self._biz_vars["business_name"].trace_add("write", self._on_business_name_typing)
        ctk.CTkLabel(bform, text="Invoice/payment notes").grid(
            row=len(biz_fields), column=0, sticky="nw", padx=4, pady=4
        )
        self._biz_notes = ctk.CTkTextbox(bform, height=100, width=420)
        self._biz_notes.grid(row=len(biz_fields), column=1, sticky="ew", padx=4, pady=4)
        self._biz_notes.insert("0.0", str(settings_get(self.cfg, "business_invoice_notes", "")))
        ctk.CTkButton(right_col, text="Save Account Settings", width=180, command=self._save_account_settings).pack(
            anchor="w", pady=(12, 8)
        )
        self._refresh_account_local_paths()
        self._account_me_fetch_gen = 0
        self._refresh_account_cloud_login()

    def _apply_account_me_fetch(self, gen: int, payload: tuple[str, object]) -> None:
        if gen != getattr(self, "_account_me_fetch_gen", 0):
            return
        if not getattr(self, "_account_login_detail_label", None):
            return
        kind, data = payload
        if kind == "ok":
            from license_client import CloudAccountInfo

            if not isinstance(data, CloudAccountInfo):
                return
            info = data
            lines = [f"Subscription: {info.subscription_status or '—'}"]
            if info.trial_ends_at:
                lines.append(f"Trial ends: {info.trial_ends_at}")
            if info.account_id:
                short_id = info.account_id[:8] + "…" if len(info.account_id) > 12 else info.account_id
                lines.append(f"Account ID: {short_id}")
            self._account_login_detail_label.configure(text="\n".join(lines))
        else:
            err = str(data)
            self._account_login_detail_label.configure(text=f"Could not load account details: {err[:200]}")

    def _refresh_account_cloud_login(self) -> None:
        if not getattr(self, "_account_login_status_label", None):
            return
        self._account_me_fetch_gen = getattr(self, "_account_me_fetch_gen", 0) + 1
        gen = self._account_me_fetch_gen

        from license_client import LICENSE_SESSION_TOKEN_KEY, read_cache_file
        from license_config import get_license_api_config

        lic = get_license_api_config()
        if not lic:
            self._account_login_status_label.configure(text="Online sign-in is not configured.")
            self._account_login_email_label.configure(
                text="This copy of the app is not set up for online accounts. Contact support if you expected sign-in."
            )
            self._account_login_detail_label.configure(text="")
            self._account_logout_btn.configure(state="disabled")
            return

        tok = (settings_get(self.cfg, LICENSE_SESSION_TOKEN_KEY, "") or "").strip()
        email = (settings_get(self.cfg, "license_account_email", "") or "").strip()
        if not email:
            email = (os.environ.get("LICENSE_EMAIL") or "").strip()

        if tok:
            self._account_login_status_label.configure(text="Signed in")
            self._account_logout_btn.configure(state="normal")
            self._account_login_email_label.configure(text=f"Email: {email or '—'}")
            self._account_login_detail_label.configure(text="Loading account details…")

            def bg() -> None:
                try:
                    from license_client import fetch_cloud_account_me

                    info = fetch_cloud_account_me(tok, cfg=lic)
                    self.after(0, lambda: self._apply_account_me_fetch(gen, ("ok", info)))
                except Exception as exc:
                    self.after(0, lambda e=exc: self._apply_account_me_fetch(gen, ("err", e)))

            threading.Thread(target=bg, daemon=True).start()
        else:
            self._account_login_status_label.configure(text="Not signed in")
            self._account_logout_btn.configure(state="disabled")
            self._account_login_email_label.configure(text=f"Billing email on file: {email or '—'}")
            raw = read_cache_file()
            sub = raw.get("subscription_status") if isinstance(raw, dict) else None
            access = raw.get("access") if isinstance(raw, dict) else None
            extra = ""
            if sub or access:
                extra = f"Last known access: {access or '—'}" + (f" · {sub}" if sub else "")
            self._account_login_detail_label.configure(text=extra or "Sign in at next startup, or use Billing if prompted.")
        self._sync_cloud_backup_toggle_from_settings()

    def _sync_cloud_backup_toggle_from_settings(self) -> None:
        sw = getattr(self, "_set_cloud_backup", None)
        if sw is None:
            return
        if bool(settings_get(self.cfg, "cloud_backup_enabled", False)):
            sw.select()
        else:
            sw.deselect()

    def _on_account_sync_with_cloud(self) -> None:
        """Push sync outbox and pull remote events (requires sign-in). Runs off the UI thread."""
        try:
            import license_gate as _lg

            if _lg.is_read_only():
                messagebox.showinfo(
                    "RootRecord Business Manager",
                    "The app is in read-only mode. Subscribe or finish sign-in, then try again.",
                )
                return
        except Exception:
            pass

        from license_client import LICENSE_SESSION_TOKEN_KEY
        from license_config import get_license_api_config

        if not get_license_api_config():
            messagebox.showinfo(
                "RootRecord Business Manager",
                "Online sign-in is not available on this copy of the app.",
            )
            return
        tok = (settings_get(self.cfg, LICENSE_SESSION_TOKEN_KEY, "") or "").strip()
        if not tok:
            messagebox.showinfo(
                "RootRecord Business Manager",
                "Sign in to your RootRecord account first (restart the app or complete sign-in when prompted).",
            )
            return

        self._set_process_status("Syncing with cloud…", auto_clear_ms=None)

        def run() -> None:
            try:
                from sync_engine import sync_cycle_best_effort

                pushed, pulled, applied = sync_cycle_best_effort(self.cfg, local_user_id=self.uid)
            except Exception as exc:
                err = str(exc)[:400]
                self.after(
                    0,
                    lambda: self._set_process_status(
                        "Sync could not finish. Check your connection and try again.",
                        auto_clear_ms=5000,
                    ),
                )
                self.after(0, lambda e=err: messagebox.showerror("RootRecord Business Manager", e))
                return

            def done() -> None:
                parts: list[str] = []
                if pushed:
                    parts.append(f"sent {pushed} update(s)")
                if pulled:
                    parts.append(f"received {pulled} from cloud")
                if applied:
                    parts.append(f"merged {applied} new item(s) here")
                if not parts:
                    msg = "Already up to date with the cloud."
                else:
                    msg = "Sync complete: " + ", ".join(parts) + "."
                self._set_process_status(msg, auto_clear_ms=4500)
                if applied:
                    try:
                        self._refresh_dashboard()
                    except Exception:
                        pass

            self.after(0, done)

        threading.Thread(target=run, daemon=True, name="rootrecord-sync-with-cloud").start()

    def _on_cloud_logout(self) -> None:
        from license_client import LICENSE_SESSION_TOKEN_KEY
        from license_config import get_license_api_config

        if not get_license_api_config():
            return
        tok = (settings_get(self.cfg, LICENSE_SESSION_TOKEN_KEY, "") or "").strip()
        if not tok:
            messagebox.showinfo("RootRecord", "You are not signed in.")
            return
        if not messagebox.askyesno(
            "Log out",
            "Sign out of your RootRecord account on this computer?\n\n"
            "The application will close. The next time you start RootRecord, you will be asked to sign in again.",
        ):
            return
        try:
            from license_runtime import logout_cloud_session

            logout_cloud_session(self.cfg)
        except Exception as exc:
            messagebox.showerror("RootRecord", str(exc))
            return
        try:
            self._cancel_billing_poll()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            raise SystemExit(0) from None

    def _save_account_settings(self) -> None:
        self._set_process_status("Saving account settings...", auto_clear_ms=None)
        for key, var in self._biz_vars.items():
            settings_set(self.cfg, key, var.get().strip())
        settings_set(self.cfg, "business_invoice_notes", self._biz_notes.get("0.0", "end").strip())
        if getattr(self, "_set_cloud_backup", None) is not None:
            settings_set(self.cfg, "cloud_backup_enabled", self._set_cloud_backup.get() == 1)
            if self._set_cloud_backup.get() == 1:
                try:
                    from backup_r2_client import ensure_vault_token

                    ensure_vault_token(self.cfg)
                except Exception:
                    pass
        active_id = get_active_business_id(self.cfg)
        if active_id > 0:
            bn = str(self._biz_vars.get("business_name").get() if self._biz_vars.get("business_name") else "").strip()
            if bn:
                try:
                    save_business_profile(self.cfg, self.uid, profile_id=active_id, name=bn)
                except Exception:
                    pass
        self._refresh_business_profiles_ui()
        try:
            from sync_engine import notify_data_changed

            notify_data_changed(self.cfg, local_user_id=self.uid)
        except Exception:
            pass
        self._set_process_status("Account settings saved.", auto_clear_ms=1800)
        messagebox.showinfo("RootRecord Business Manager", "Account settings saved.")
        self._refresh_cloud_backup_status_label()
        if getattr(self, "_set_cloud_backup", None) is not None and self._set_cloud_backup.get() == 1:

            def try_cloud_after_save() -> None:
                try:
                    from backup_r2_client import maybe_upload_newest_if_stale, newest_local_backup_file

                    if newest_local_backup_file(self.cfg) is None:
                        self.after(
                            0,
                            lambda: self._set_process_status(
                                "Online backup on: run a local backup (Program Settings) to upload a copy.",
                                auto_clear_ms=7000,
                            ),
                        )
                        return
                    ok, msg = maybe_upload_newest_if_stale(self.cfg)
                    if ok:
                        self.after(
                            0,
                            lambda: self._set_process_status("Online backup copy saved.", auto_clear_ms=4000),
                        )
                    elif msg not in frozenset({"Already up to date."}):
                        self.after(
                            0,
                            lambda m=msg: self._set_process_status(f"Online backup: {m}", auto_clear_ms=6000),
                        )
                    self.after(0, self._refresh_cloud_backup_status_label)
                except Exception:
                    pass

            threading.Thread(target=try_cloud_after_save, daemon=True, name="rootrecord-cloud-backup-after-save").start()

    def _on_toggle_multi_business(self) -> None:
        enabled = bool(self._multi_business_enabled_var.get())
        settings_set(self.cfg, "multi_business_enabled", enabled)
        if not enabled:
            settings_set(self.cfg, "active_business_id", 1)
        self._refresh_business_profiles_ui()
        self._refresh_all_business_scoped_views()

    def _add_business_profile(self) -> None:
        name = self._new_business_name_var.get().strip()
        if not name:
            return
        try:
            new_id = save_business_profile(self.cfg, self.uid, profile_id=None, name=name)
            settings_set(self.cfg, "multi_business_enabled", True)
            settings_set(self.cfg, "active_business_id", int(new_id))
            self._multi_business_enabled_var.set(True)
            self._new_business_name_var.set("")
            self._refresh_business_profiles_ui()
            self._refresh_all_business_scoped_views()
            self._set_process_status(f"Added business profile: {name}", auto_clear_ms=1600)
        except Exception as exc:
            messagebox.showerror("RootRecord Business Manager", str(exc))

    def _remove_business_profile(self) -> None:
        if not bool(multi_business_enabled(self.cfg)):
            messagebox.showinfo(
                "RootRecord Business Manager",
                "Multi-business mode is off. Turn it on if you need separate business profiles.",
            )
            return
        bid = get_active_business_id(self.cfg)
        if bid <= 0:
            messagebox.showinfo(
                "RootRecord Business Manager",
                "Switch the sidebar profile to a specific business first. "
                '"Master (All Businesses)" cannot be removed.',
            )
            return
        name = ""
        if getattr(self, "_profile_name_to_id", None):
            name = next((n for n, i in self._profile_name_to_id.items() if i == bid), "") or str(bid)
        if not messagebox.askyesno(
            "Remove business profile",
            f'Archive profile "{name}"?\n\n'
            "Existing transactions and records for this profile stay in your database but are hidden "
            "while the profile is removed. You can add a new profile anytime.\n\n"
            "If this is your last business profile, multi-business mode will turn off.",
        ):
            return
        try:
            set_business_profile_archived(self.cfg, self.uid, bid, archived=True)
        except Exception as exc:
            messagebox.showerror("RootRecord Business Manager", str(exc))
            return
        try:
            rows = list_business_profiles(self.cfg, self.uid, include_archived=False)
        except Exception:
            rows = [{"id": 0, "name": "Master (All Businesses)"}]
        real = [r for r in rows if int(r.get("id") or 0) > 0]
        if not real:
            settings_set(self.cfg, "multi_business_enabled", False)
            settings_set(self.cfg, "active_business_id", 1)
            self._multi_business_enabled_var.set(False)
            self._set_process_status("Last profile removed — multi-business mode off.", auto_clear_ms=2200)
        else:
            nxt = int(real[0].get("id") or 1)
            set_active_business_id(self.cfg, nxt)
            self._set_process_status(f"Profile archived. Active: {real[0].get('name', nxt)}", auto_clear_ms=2000)
        self._refresh_business_profiles_ui()
        self._refresh_all_business_scoped_views()

    def _cancel_prompt_immediate_job(self) -> None:
        jid = getattr(self, "_prompt_immediate_job", None)
        if jid:
            try:
                self.after_cancel(jid)
            except Exception:
                pass
            self._prompt_immediate_job = None

    def _schedule_prompts(self) -> None:
        prev = getattr(self, "_prompt_job_id", None)
        if prev:
            try:
                self.after_cancel(prev)
            except Exception:
                pass
            self._prompt_job_id = None
        self._cancel_prompt_immediate_job()
        first = int(settings_get(self.cfg, "prompt_first_delay_sec", 120)) * 1000
        interval = int(settings_get(self.cfg, "prompt_interval_sec", 900)) * 1000
        first = max(10_000, first)
        interval = max(60_000, interval)

        def tick() -> None:
            self._prompt_popup()
            self._prompt_job_id = self.after(interval, tick)

        self._prompt_job_id = self.after(first, tick)

    def _on_prompt_immediate(self) -> None:
        self._prompt_immediate_job = None
        self._prompt_popup()

    def _prompt_popup(self) -> None:
        st = load_user_state(self.uid)
        # Timed check-ins are only relevant while actively working.
        if not (st and st.current_mode == "working" and st.current_work_start_utc):
            return
        existing = getattr(self, "_active_prompt_popup", None)
        try:
            exists = existing is not None and bool(existing.winfo_exists())
        except Exception:
            exists = False
        if exists:
            # Do not stack duplicate timed prompts; bring existing one to front.
            try:
                if bool(settings_get(self.cfg, "prompt_popup_topmost", False)):
                    existing.attributes("-topmost", True)
                existing.lift()
                existing.focus_force()
            except Exception:
                pass
            return
        m = self._tk_modal_theme()
        top = tk.Toplevel(self)
        top.title("Check-in")
        top.configure(bg=m["root_bg"])
        self._apply_window_icon(top)
        top.transient(self)
        try:
            top.withdraw()
        except Exception:
            pass
        # One character width for Entry + Combobox so rows align; avoid expand=True on combobox (full-window stretch).
        field_ch = 50
        if bool(settings_get(self.cfg, "prompt_popup_topmost", False)):
            try:
                top.attributes("-topmost", True)
            except Exception:
                pass
            try:
                top.lift()
            except Exception:
                pass
        self._active_prompt_popup = top
        top.bind("<Destroy>", lambda _e: setattr(self, "_active_prompt_popup", None))
        body = tk.Frame(top, bg=m["panel_bg"])
        body.pack(fill="both", expand=False)
        cb_style = self._tk_modal_configure_combobox_style(top, "RRCheckIn.TCombobox", m)
        e_kw: dict[str, Any] = {
            "bg": m["ent_bg"],
            "fg": m["lbl_fg"],
            "insertbackground": m["lbl_fg"],
            "relief": "flat",
            "font": m["font_entry"],
            "width": field_ch,
            "highlightthickness": 1,
            "highlightbackground": m["ent_hl"],
            "highlightcolor": m["ent_hl"],
        }
        tk.Label(
            body,
            text="What are you doing right now?",
            bg=m["panel_bg"],
            fg=m["lbl_fg"],
            font=m["font_title"],
        ).pack(anchor="w", padx=24, pady=(18, 8))
        ent = tk.Entry(body, **e_kw)
        ent.pack(anchor="w", padx=24, pady=(0, 8), ipady=5)
        cats = list_work_categories(self.cfg, self.uid)
        self._popup_cat_map: dict[str, int | None] = {}
        for c in cats:
            self._popup_cat_map[c["name"]] = int(c["id"])
        cat_keys = list(self._popup_cat_map.keys())
        cat_row = tk.Frame(body, bg=m["panel_bg"])
        cat_row.pack(fill="x", padx=24, pady=(6, 4))
        tk.Label(cat_row, text="Category", bg=m["panel_bg"], fg=m["lbl_fg"], font=m["font_lbl"]).pack(side="left")
        cmb_cat = ttk.Combobox(cat_row, values=cat_keys or ["—"], width=field_ch, state="readonly", style=cb_style)
        cmb_cat.set(_default_activity_category_label(cat_keys) if cat_keys else "—")
        cmb_cat.pack(side="left", padx=(10, 8))
        tk.Button(
            cat_row,
            text="Add",
            font=m["font_btn"],
            bg=m["accent"],
            fg="#f0f0f0",
            activebackground=m["accent"],
            activeforeground="#ffffff",
            relief="flat",
            padx=14,
            pady=6,
            command=lambda: self._quick_add_category_from_popup(cmb_cat),
        ).pack(side="left")
        tk.Label(
            body,
            text="Other category (optional — overrides dropdown if set)",
            bg=m["panel_bg"],
            fg=m["lbl_fg"],
            font=m["font_hint"],
        ).pack(anchor="w", padx=24, pady=(4, 4))
        other_popup_cat = tk.Entry(body, **e_kw)
        other_popup_cat.pack(anchor="w", padx=24, pady=(0, 6), ipady=5)

        prows = list_projects(self.cfg, self.uid)
        self._popup_proj_map: dict[str, int | None] = {"—": None}
        for p in prows:
            self._popup_proj_map[p["name"]] = int(p["id"])
        proj_row = tk.Frame(body, bg=m["panel_bg"])
        proj_row.pack(fill="x", padx=24, pady=(4, 8))
        tk.Label(proj_row, text="Project", bg=m["panel_bg"], fg=m["lbl_fg"], font=m["font_lbl"]).pack(side="left")
        cmb_proj = ttk.Combobox(proj_row, values=list(self._popup_proj_map.keys()), width=field_ch, state="readonly", style=cb_style)
        cmb_proj.set("—")
        cmb_proj.pack(side="left", padx=(10, 8))
        tk.Button(
            proj_row,
            text="Add",
            font=m["font_btn"],
            bg=m["accent"],
            fg="#f0f0f0",
            activebackground=m["accent"],
            activeforeground="#ffffff",
            relief="flat",
            padx=14,
            pady=6,
            command=lambda: self._quick_add_project_from_popup(cmb_proj),
        ).pack(side="left")

        # Prefill popup from most recent entry for faster repeated check-ins.
        try:
            latest = recent_time_entries(self.cfg, self.uid, limit=1)
            last = latest[0] if latest else {}
        except Exception:
            last = {}
        seed_desc = str(last.get("description") or "").strip() or getattr(self, "_last_activity_desc", "")
        saved_desc = str(settings_get(self.cfg, "last_popup_desc", "") or "").strip()
        if saved_desc:
            seed_desc = saved_desc
        if seed_desc:
            ent.insert(0, seed_desc)
            ent.icursor(tk.END)
        saved_cat = str(settings_get(self.cfg, "last_popup_category_name", "") or "").strip()
        if saved_cat and saved_cat in self._popup_cat_map:
            cmb_cat.set(saved_cat)
        last_cat_id = last.get("work_category_id")
        if (not saved_cat) and last_cat_id is not None:
            last_cat = next((k for k, v in self._popup_cat_map.items() if v == last_cat_id), "")
            if last_cat:
                cmb_cat.set(last_cat)
        saved_proj = str(settings_get(self.cfg, "last_popup_project_name", "") or "").strip()
        if saved_proj and saved_proj in self._popup_proj_map:
            cmb_proj.set(saved_proj)
        last_proj_id = last.get("project_id")
        if (not saved_proj) and last_proj_id is not None:
            last_proj = next((k for k, v in self._popup_proj_map.items() if v == last_proj_id), "")
            if last_proj:
                cmb_proj.set(last_proj)

        def submit() -> None:
            t = ent.get().strip()
            if t:
                oc = other_popup_cat.get().strip()
                if oc:
                    upsert_work_category(self.cfg, self.uid, oc)
                    self._popup_cat_map[oc] = next(
                        (int(c["id"]) for c in list_work_categories(self.cfg, self.uid) if c["name"] == oc),
                        None,
                    )
                    wc = self._popup_cat_map.get(oc)
                else:
                    wc = self._popup_cat_map.get(cmb_cat.get())
                pid = self._popup_proj_map.get(cmb_proj.get())
                self._log_from_values(t, wc, pid, None, None)
                # Persist the submitted popup fields for the next timed check-in.
                settings_set(self.cfg, "last_popup_desc", t)
                settings_set(self.cfg, "last_popup_category_name", oc if oc else cmb_cat.get().strip())
                settings_set(self.cfg, "last_popup_project_name", cmb_proj.get().strip())
            self._safe_destroy_window(top)

        def close_checkin() -> None:
            self._safe_destroy_window(top)

        tk.Label(
            body,
            text="Press Enter or click Submit",
            bg=m["panel_bg"],
            fg="#9eb0c4",
            font=m["font_hint"],
        ).pack(anchor="w", padx=24, pady=(8, 8))
        action_row = tk.Frame(body, bg=m["panel_bg"])
        action_row.pack(fill="x", padx=24, pady=(8, 18))
        tk.Button(
            action_row,
            text="Submit",
            font=m["font_btn"],
            bg=m["accent"],
            fg="#f8f8f8",
            activebackground=m["accent"],
            activeforeground="#ffffff",
            relief="flat",
            padx=22,
            pady=10,
            command=submit,
        ).pack(side="left")
        tk.Button(
            action_row,
            text="Cancel",
            font=m["font_btn"],
            bg="#4a4a4a",
            fg="#f0f0f0",
            activebackground="#3d3d3d",
            relief="flat",
            padx=22,
            pady=10,
            command=close_checkin,
        ).pack(side="left", padx=12)
        ent.bind("<Return>", lambda _e: submit())
        top.protocol("WM_DELETE_WINDOW", close_checkin)
        timeout_sec = int(settings_get(self.cfg, "prompt_no_response_timeout_sec", 45))
        action = settings_get(self.cfg, "prompt_no_response_action", "none")
        if action == "copy_last":
            def on_timeout() -> None:
                if not top.winfo_exists():
                    return
                last = getattr(self, "_last_activity_desc", "") or "Working"
                self._log_from_values(last, None, None, None, None)
                self._safe_destroy_window(top)
            top.after(max(5, timeout_sec) * 1000, on_timeout)
        try:
            top.update_idletasks()
            win_w = max(560, min(960, top.winfo_reqwidth()))
            win_h = max(340, min(780, top.winfo_reqheight()))
            top.geometry(f"{win_w}x{win_h}")
            top.minsize(520, 320)
            top.deiconify()
            top.update()
        except Exception:
            pass
        self._apply_window_icon(top)
        try:
            top.after(150, lambda w=top: self._apply_window_icon(w))
        except Exception:
            pass
        try:
            top.lift()
            top.focus_force()
        except Exception:
            pass
        try:
            top.grab_set()
        except Exception:
            pass

    def _dash_chart_colors(self) -> tuple[str, str]:
        if ctk.get_appearance_mode() == "Light":
            return "#e9eef5", "#1a2230"
        return self._theme_chart_bg, self._theme_chart_fg

    def _dash_style_axes(self, fig, ax) -> None:
        bg, fg = self._dash_chart_colors()
        fig.patch.set_facecolor(bg)
        ax.set_facecolor(bg)
        ax.tick_params(colors=fg, labelsize=8)
        ax.title.set_color(fg)
        edge = "#6f8198" if ctk.get_appearance_mode() == "Light" else self._theme_chart_edge
        for spine in ax.spines.values():
            spine.set_color(edge)
        ax.yaxis.label.set_color(fg)
        ax.xaxis.label.set_color(fg)

    def _dash_disconnect_pie_hover(self) -> None:
        cid = getattr(self, "_dash_pie_hover_cid", None)
        canvas = getattr(self, "_dash_canvas_pie", None)
        if cid is not None and canvas is not None:
            try:
                canvas.mpl_disconnect(cid)
            except Exception:
                pass
        self._dash_pie_hover_cid = None

    def _dash_pie_draw_leader_labels(
        self,
        ax,
        wedges,
        parts: list[dict],
        total_sec: float,
        fg: str,
        line_color: str,
    ) -> None:
        """Draw leader lines from each slice to an outside label (name, %, time)."""
        fs = 7 if len(wedges) > 8 else 8
        r_line_in = 0.88
        r_line_out = 1.34
        for i, w in enumerate(wedges):
            sec = float(parts[i].get("seconds_total") or 0)
            pct = (100.0 * sec / total_sec) if total_sec > 0 else 0.0
            raw_name = str(parts[i].get("task_name") or "").strip() or "Uncategorized"
            name = raw_name if len(raw_name) <= 22 else raw_name[:19] + "…"
            theta_mid = math.radians((w.theta1 + w.theta2) / 2.0)
            x_in = r_line_in * math.cos(theta_mid)
            y_in = r_line_in * math.sin(theta_mid)
            x_out = r_line_out * math.cos(theta_mid)
            y_out = r_line_out * math.sin(theta_mid)
            ax.plot(
                [x_in, x_out],
                [y_in, y_out],
                color=line_color,
                linewidth=0.9,
                solid_capstyle="round",
                zorder=4,
                clip_on=False,
            )
            if abs(x_out) < 0.12:
                ha: str = "center"
            elif x_out > 0:
                ha = "left"
            else:
                ha = "right"
            label = f"{name}\n{pct:.1f}%\n{_fmt_hm(sec)}"
            ax.text(
                x_out,
                y_out,
                label,
                ha=ha,
                va="center",
                fontsize=fs,
                color=fg,
                zorder=5,
                clip_on=False,
                linespacing=1.15,
            )

    def _dash_pie_setup_hover(self, wedges, parts: list[dict], total_sec: float, fg: str) -> None:
        """Hover highlights the active slice and shows category, %, and time below the chart."""
        self._dash_disconnect_pie_hover()
        self._dash_pie_wedges = wedges
        self._dash_pie_slice_meta = [
            {
                "name": str(b.get("task_name") or "").strip() or "Uncategorized",
                "sec": float(b.get("seconds_total") or 0),
            }
            for b in parts
        ]
        self._dash_pie_total_sec = total_sec
        ax = self._dash_ax_pie
        subtle = "#6b7a8f" if ctk.get_appearance_mode() == "Light" else self._theme_text_muted
        self._dash_pie_hover_strip = ax.text(
            0.5,
            -0.06,
            "Hover a slice for details",
            transform=ax.transAxes,
            ha="center",
            va="top",
            fontsize=9,
            color=subtle,
            linespacing=1.2,
            clip_on=False,
        )

        def on_motion(event) -> None:
            wedges_l = getattr(self, "_dash_pie_wedges", None)
            meta_l = getattr(self, "_dash_pie_slice_meta", None)
            strip = getattr(self, "_dash_pie_hover_strip", None)
            tot = float(getattr(self, "_dash_pie_total_sec", 0) or 0)
            if not wedges_l or not meta_l or strip is None:
                return
            if event.inaxes != ax:
                for w in wedges_l:
                    w.set_alpha(1.0)
                strip.set_text("Hover a slice for details")
                strip.set_color(subtle)
                self._dash_canvas_pie.draw_idle()
                return
            hit: int | None = None
            for j, w in enumerate(wedges_l):
                try:
                    hit_test = w.contains(event)
                    ok = hit_test[0] if isinstance(hit_test, tuple) else bool(hit_test)
                except Exception:
                    ok = False
                if ok:
                    hit = j
                    break
            if hit is None:
                for w in wedges_l:
                    w.set_alpha(1.0)
                strip.set_text("Hover a slice for details")
                strip.set_color(subtle)
                self._dash_canvas_pie.draw_idle()
                return
            m = meta_l[hit]
            nm = m["name"]
            sec = float(m["sec"])
            pct = (100.0 * sec / tot) if tot > 0 else 0.0
            disp = nm if len(nm) <= 40 else nm[:37] + "…"
            strip.set_text(f"{disp}\n{pct:.1f}%  ·  {_fmt_hm(sec)}")
            strip.set_color(fg)
            for j, w in enumerate(wedges_l):
                w.set_alpha(1.0 if j == hit else 0.52)
            self._dash_canvas_pie.draw_idle()

        self._dash_pie_hover_cid = self._dash_canvas_pie.mpl_connect("motion_notify_event", on_motion)

    def _dash_scale_value(self) -> str:
        v = str(getattr(self, "_dash_scale", None).get() if getattr(self, "_dash_scale", None) else "Daily")
        return v if v in {"Daily", "Weekly", "Monthly", "Yearly", "Custom Day"} else "Daily"

    def _on_dash_scale_change(self, value: str) -> None:
        if value != "Custom Day":
            self._refresh_dashboard()
            return
        picked = self._ask_text_dialog(
            title="Custom Day",
            prompt="Enter date (YYYY-MM-DD)",
            placeholder="YYYY-MM-DD",
            initial=(getattr(self, "_dash_custom_day", None) or date.today()).isoformat(),
            ok_text="Apply Day",
        )
        if not picked:
            # Keep previous selection when canceled.
            self._dash_scale.set(getattr(self, "_dash_prev_scale", "Daily"))
            return
        try:
            self._dash_custom_day = date.fromisoformat(picked.strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Use YYYY-MM-DD for custom day.")
            self._dash_scale.set(getattr(self, "_dash_prev_scale", "Daily"))
            return
        self._dash_prev_scale = "Custom Day"
        self._refresh_dashboard()

    def _dash_primary_bounds(self, today: date) -> tuple[str, str, str]:
        scale = self._dash_scale_value()
        if scale == "Daily":
            s, e = utc_naive_bounds_for_local_date(self.cfg, today)
            return s, e, "Today"
        if scale == "Custom Day":
            cd = getattr(self, "_dash_custom_day", today)
            s, e = utc_naive_bounds_for_local_date(self.cfg, cd)
            return s, e, cd.isoformat()
        if scale == "Weekly":
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=7)
            s, e = utc_naive_bounds_for_local_report_range(self.cfg, start, end)
            return s, e, "This week"
        if scale == "Monthly":
            start = date(today.year, today.month, 1)
            end = date(today.year + (1 if today.month == 12 else 0), 1 if today.month == 12 else today.month + 1, 1)
            s, e = utc_naive_bounds_for_local_report_range(self.cfg, start, end)
            return s, e, "This month"
        start = date(today.year, 1, 1)
        end = date(today.year + 1, 1, 1)
        s, e = utc_naive_bounds_for_local_report_range(self.cfg, start, end)
        return s, e, "This year"

    def _dash_previous_bounds(self, today: date) -> tuple[str, str, str]:
        scale = self._dash_scale_value()
        if scale == "Daily":
            prev = today - timedelta(days=1)
            s, e = utc_naive_bounds_for_local_date(self.cfg, prev)
            return s, e, "yesterday"
        if scale == "Custom Day":
            cd = getattr(self, "_dash_custom_day", today)
            prev = cd - timedelta(days=1)
            s, e = utc_naive_bounds_for_local_date(self.cfg, prev)
            return s, e, prev.isoformat()
        if scale == "Weekly":
            this_start = today - timedelta(days=today.weekday())
            prev_start = this_start - timedelta(days=7)
            prev_end = this_start
            s, e = utc_naive_bounds_for_local_report_range(self.cfg, prev_start, prev_end)
            return s, e, "last week"
        if scale == "Monthly":
            this_start = date(today.year, today.month, 1)
            prev_end = this_start
            prev_month = 12 if today.month == 1 else today.month - 1
            prev_year = today.year - 1 if today.month == 1 else today.year
            prev_start = date(prev_year, prev_month, 1)
            s, e = utc_naive_bounds_for_local_report_range(self.cfg, prev_start, prev_end)
            return s, e, "last month"
        this_start = date(today.year, 1, 1)
        prev_start = date(today.year - 1, 1, 1)
        s, e = utc_naive_bounds_for_local_report_range(self.cfg, prev_start, this_start)
        return s, e, "last year"

    def _refresh_dashboard(self) -> None:
        try:
            today = date.today()
            ds, de, label = self._dash_primary_bounds(today)
            s = summary_between(self.cfg, self.uid, ds, de)
            ps, pe, prev_label = self._dash_previous_bounds(today)
            prev = summary_between(self.cfg, self.uid, ps, pe)
            breakdown = daily_task_breakdown(self.cfg, self.uid, ds, de)
            sec = float(s.get("seconds_worked_approx", 0) or 0)
            break_sec = sum(float(b.get("seconds_total") or 0) for b in breakdown if str(b.get("task_name", "")).strip().lower() == "break")
            tm = s.get("income_cents", 0) or 0
            ex = s.get("expense_cents", 0) or 0
            prev_sec = float(prev.get("seconds_worked_approx", 0) or 0)
            prev_tm = float(prev.get("income_cents", 0) or 0)
            prev_ex = float(prev.get("expense_cents", 0) or 0)
            currency_safe = bool(settings_get(self.cfg, "currency_safe_summaries_enabled", False))
            default_cur = str(settings_get(self.cfg, "currency_default", "USD")).upper()
            curr_map = money_totals_by_currency_between(self.cfg, self.uid, ds, de) if currency_safe else {}
            prev_map = money_totals_by_currency_between(self.cfg, self.uid, ps, pe) if currency_safe else {}
            if currency_safe:
                drow = curr_map.get(default_cur, {"income_cents": 0, "expense_cents": 0, "net_cents": 0})
                prow = prev_map.get(default_cur, {"income_cents": 0, "expense_cents": 0, "net_cents": 0})
                tm = int(drow.get("income_cents", 0))
                ex = int(drow.get("expense_cents", 0))
                prev_tm = float(prow.get("income_cents", 0))
                prev_ex = float(prow.get("expense_cents", 0))
            net_per_hr = _dollars_per_hour(float(tm) - float(ex), sec)
            prev_net_per_hr = _dollars_per_hour(prev_tm - prev_ex, prev_sec)
            show_m = settings_get(self.cfg, "show_money_in_dashboard", True)
            pbreak = daily_project_breakdown(self.cfg, self.uid, ds, de)
            top_project = pbreak[0] if pbreak else None
            try:
                self._refresh_dashboard_current_status()
            except Exception:
                logging.getLogger("rootrecord.ui").exception("Dashboard status line failed")
                try:
                    if getattr(self, "_dash_status_lbl", None):
                        self._dash_status_lbl.configure(text="Current Status: (unavailable)")
                except Exception:
                    pass
            self._refresh_dashboard_action_buttons()
            line1 = (
                f"{label} unique time (excluding breaks): {_fmt_hm(sec)}"
                f" ({_pct_change_text(sec, prev_sec)} vs {prev_label})"
                f"  ·  Break time: {_fmt_hm(break_sec)}"
            )
            line2 = "Top project: —"
            if top_project:
                line2 = (
                    f"Top project: {str(top_project.get('project_name'))[:36]} "
                    f"({_fmt_hm(float(top_project.get('seconds_total') or 0))})"
                )
            line3 = "Income: —  ·  Expenses: —"
            line4 = "Net $/hr: —"
            line5 = ""
            if show_m:
                af_map = available_funds_totals_by_currency(self.cfg, self.uid)
                af_parts: list[str] = []
                for cur_code in sorted(af_map.keys()):
                    agg = af_map.get(cur_code, {})
                    # _fmt_money already prefixes the currency code — do not duplicate (e.g. "USD USD").
                    af_parts.append(_fmt_money(int(agg.get("available_cents") or 0), cur_code))
                ledger_fallback = False
                if not af_parts:
                    # No bank/cash accounts: show cumulative ledger net (income − expenses) so
                    # recorded income is visible here until the user adds manual accounts.
                    led_map = ledger_net_totals_by_currency(self.cfg, self.uid)
                    for cur_code in sorted(led_map.keys()):
                        net = int(led_map[cur_code].get("net_cents") or 0)
                        af_parts.append(_fmt_money(net, cur_code))
                    ledger_fallback = bool(af_parts)
                af_text = "  ·  ".join(af_parts) if af_parts else "—"
                if ledger_fallback:
                    af_text = f"{af_text}  ·  (ledger total — add Available funds accounts to track real balances)"
                line3 = (
                    f"Income: {_fmt_money(int(tm), default_cur)} ({_pct_change_text(float(tm), prev_tm)} vs {prev_label})"
                    f"  ·  Expenses: {_fmt_money(int(ex), default_cur)} ({_pct_change_text(float(ex), prev_ex)} vs {prev_label})"
                )
                line4 = (
                    f"Net {default_cur}/hr: {default_cur} {net_per_hr:,.2f} ({_pct_change_text(net_per_hr, prev_net_per_hr)} vs {prev_label})"
                )
                line5 = f"Available funds: {af_text}"
                if currency_safe:
                    line3 = f"{line3}  ·  (default currency strict mode)"
                    line4 = f"{line4}\nAll currencies: {_fmt_currency_totals_line(curr_map)}"
            lines = [line1, line2, line3, line4]
            if show_m:
                lines.append(line5)
            self._dash_stats.configure(text="\n".join(lines))
            if not _DASHBOARD_CHARTS_AVAILABLE or not getattr(self, "_dash_ax_pie", None):
                return
            _, fg = self._dash_chart_colors()
            colors = (
                self._theme_accent,
                "#2fa572",
                "#e67700",
                "#7950f2",
                "#868e96",
                "#c92a2a",
                "#5c4d7d",
            )
            cat_color_map: dict[str, str] = {}
            for c in list_work_categories(self.cfg, self.uid):
                nm = str(c.get("name") or "").strip()
                col = str(c.get("color") or "").strip()
                if nm and re.fullmatch(r"#[0-9A-Fa-f]{6}", col):
                    cat_color_map[nm] = col.upper()

            self._dash_disconnect_pie_hover()
            self._dash_ax_pie.clear()
            self._dash_style_axes(self._dash_fig_pie, self._dash_ax_pie)
            parts = [b for b in breakdown if float(b.get("seconds_total") or 0) > 1]
            if not parts:
                self._dash_ax_pie.text(
                    0.5,
                    0.5,
                    "No time logged today",
                    ha="center",
                    va="center",
                    color=fg,
                    fontsize=11,
                    transform=self._dash_ax_pie.transAxes,
                )
                self._dash_ax_pie.set_xticks([])
                self._dash_ax_pie.set_yticks([])
                self._dash_fig_pie.subplots_adjust(left=0.10, right=0.90, top=0.88, bottom=0.12)
            else:
                sizes = [float(b["seconds_total"]) for b in parts]
                wedge_edge = "#1a1a1a" if ctk.get_appearance_mode() != "Light" else "#cccccc"
                leader_line = "#5a6d82" if ctk.get_appearance_mode() == "Light" else "#7d93ad"
                total_sec = sum(sizes) or 1.0
                slice_colors: list[str] = []
                for bi, b in enumerate(parts):
                    nm = str(b.get("task_name") or "").strip() or "Uncategorized"
                    ccol = cat_color_map.get(nm)
                    slice_colors.append(ccol if ccol else colors[bi % len(colors)])

                wedges, _texts = self._dash_ax_pie.pie(
                    sizes,
                    labels=None,
                    autopct=None,
                    colors=slice_colors,
                    wedgeprops={"linewidth": 0.6, "edgecolor": wedge_edge},
                )
                self._dash_pie_draw_leader_labels(
                    self._dash_ax_pie,
                    wedges,
                    parts,
                    total_sec,
                    fg,
                    leader_line,
                )
                self._dash_ax_pie.axis("equal")
                self._dash_ax_pie.set_xlim(-1.75, 1.75)
                self._dash_ax_pie.set_ylim(-1.75, 1.75)
                self._dash_pie_setup_hover(wedges, parts, total_sec, fg)
                self._dash_fig_pie.subplots_adjust(left=0.10, right=0.90, top=0.88, bottom=0.12)
            self._dash_ax_pie.set_title(f"{label} by category", fontsize=11, pad=10)
            self._dash_canvas_pie.draw()

            self._dash_ax_bar.clear()
            self._dash_style_axes(self._dash_fig_bar, self._dash_ax_bar)
            xlabs: list[str] = []
            hrs: list[float] = []
            bucket_category_hours: list[dict[str, float]] = []
            bar_title = "Recent trend — unique worked time"
            scale = self._dash_scale_value()
            if scale in {"Daily", "Custom Day"}:
                end_day = today if scale == "Daily" else getattr(self, "_dash_custom_day", today)
                for i in range(7):
                    d = end_day - timedelta(days=6 - i)
                    d0, d1 = utc_naive_bounds_for_local_date(self.cfg, d)
                    day_sec = float(summary_between(self.cfg, self.uid, d0, d1).get("seconds_worked_approx") or 0)
                    db = daily_task_breakdown(self.cfg, self.uid, d0, d1)
                    cat_h: dict[str, float] = {}
                    for b in db:
                        nm = str(b.get("task_name") or "").strip() or "Uncategorized"
                        cat_h[nm] = cat_h.get(nm, 0.0) + (float(b.get("seconds_total") or 0) / 3600.0)
                    bucket_category_hours.append(cat_h)
                    xlabs.append(f"{d:%a}\n{d.month}/{d.day}")
                    hrs.append(round(day_sec / 3600.0, 2))
                bar_title = "Last 7 days — unique worked time"
            elif scale == "Weekly":
                wk0 = today - timedelta(days=today.weekday())
                for i in range(8):
                    sday = wk0 - timedelta(days=7 * (7 - i))
                    eday = sday + timedelta(days=7)
                    d0, d1 = utc_naive_bounds_for_local_report_range(self.cfg, sday, eday)
                    wsec = float(summary_between(self.cfg, self.uid, d0, d1).get("seconds_worked_approx") or 0)
                    db = daily_task_breakdown(self.cfg, self.uid, d0, d1)
                    cat_h = {}
                    for b in db:
                        nm = str(b.get("task_name") or "").strip() or "Uncategorized"
                        cat_h[nm] = cat_h.get(nm, 0.0) + (float(b.get("seconds_total") or 0) / 3600.0)
                    bucket_category_hours.append(cat_h)
                    xlabs.append(f"{sday.month}/{sday.day}")
                    hrs.append(round(wsec / 3600.0, 2))
                bar_title = "Last 8 weeks — unique worked time"
            elif scale == "Monthly":
                y, m = today.year, today.month
                months: list[tuple[int, int]] = []
                for _ in range(12):
                    months.append((y, m))
                    m -= 1
                    if m == 0:
                        m = 12
                        y -= 1
                months.reverse()
                for yy, mm in months:
                    sday = date(yy, mm, 1)
                    eday = date(yy + (1 if mm == 12 else 0), 1 if mm == 12 else mm + 1, 1)
                    d0, d1 = utc_naive_bounds_for_local_report_range(self.cfg, sday, eday)
                    msec = float(summary_between(self.cfg, self.uid, d0, d1).get("seconds_worked_approx") or 0)
                    db = daily_task_breakdown(self.cfg, self.uid, d0, d1)
                    cat_h = {}
                    for b in db:
                        nm = str(b.get("task_name") or "").strip() or "Uncategorized"
                        cat_h[nm] = cat_h.get(nm, 0.0) + (float(b.get("seconds_total") or 0) / 3600.0)
                    bucket_category_hours.append(cat_h)
                    xlabs.append(f"{sday:%b}")
                    hrs.append(round(msec / 3600.0, 2))
                bar_title = "Last 12 months — unique worked time"
            else:
                years = list(range(today.year - 4, today.year + 1))
                for yy in years:
                    sday = date(yy, 1, 1)
                    eday = date(yy + 1, 1, 1)
                    d0, d1 = utc_naive_bounds_for_local_report_range(self.cfg, sday, eday)
                    ysec = float(summary_between(self.cfg, self.uid, d0, d1).get("seconds_worked_approx") or 0)
                    db = daily_task_breakdown(self.cfg, self.uid, d0, d1)
                    cat_h = {}
                    for b in db:
                        nm = str(b.get("task_name") or "").strip() or "Uncategorized"
                        cat_h[nm] = cat_h.get(nm, 0.0) + (float(b.get("seconds_total") or 0) / 3600.0)
                    bucket_category_hours.append(cat_h)
                    xlabs.append(str(yy))
                    hrs.append(round(ysec / 3600.0, 2))
                bar_title = "Last 5 years — unique worked time"
            xpos = list(range(len(hrs)))
            totals_by_cat: dict[str, float] = {}
            for bucket in bucket_category_hours:
                for nm, h in bucket.items():
                    totals_by_cat[nm] = totals_by_cat.get(nm, 0.0) + h
            ordered_cats = [nm for nm, _h in sorted(totals_by_cat.items(), key=lambda kv: kv[1], reverse=True)]
            if ordered_cats:
                bucket_raw_totals = [sum(bucket.values()) for bucket in bucket_category_hours]
                cat_bar_vals: dict[str, list[float]] = {}
                for i, nm in enumerate(ordered_cats):
                    vals: list[float] = []
                    for bi, bucket in enumerate(bucket_category_hours):
                        raw_v = bucket.get(nm, 0.0)
                        raw_total = bucket_raw_totals[bi] if bi < len(bucket_raw_totals) else 0.0
                        # Scale category slices so each stacked bar sums to the true total-hours bar.
                        if raw_total > 0 and bi < len(hrs):
                            vals.append(raw_v * (hrs[bi] / raw_total))
                        else:
                            vals.append(0.0)
                    if sum(vals) > 0:
                        cat_bar_vals[nm] = vals
                bottoms = [0.0 for _ in xpos]
                for i, nm in enumerate(ordered_cats):
                    if nm not in cat_bar_vals:
                        continue
                    vals = cat_bar_vals[nm]
                    col = cat_color_map.get(nm, colors[i % len(colors)])
                    self._dash_ax_bar.bar(xpos, vals, bottom=bottoms, color=col, edgecolor="#000000", linewidth=0.35)
                    bottoms = [bottoms[j] + vals[j] for j in range(len(bottoms))]
                # One label per day column (largest segment only) — avoids stacked-label overlap.
                cat_list = [nm for nm in ordered_cats if nm in cat_bar_vals]
                for j in range(len(xpos)):
                    total = hrs[j] if j < len(hrs) else 0.0
                    if total <= 0:
                        continue
                    best_nm: str | None = None
                    best_v = 0.0
                    for nm in cat_list:
                        v = cat_bar_vals[nm][j]
                        if v > best_v:
                            best_v = v
                            best_nm = nm
                    if best_nm is None or best_v <= 0:
                        continue
                    pct = (best_v / total) * 100.0
                    if best_v < 0.42 or pct < 15:
                        continue
                    btm = 0.0
                    for nm in cat_list:
                        if nm == best_nm:
                            break
                        btm += cat_bar_vals[nm][j]
                    yc = btm + best_v / 2.0
                    self._dash_ax_bar.text(
                        xpos[j],
                        yc,
                        f"{pct:.0f}%\n{best_v:.1f}h",
                        ha="center",
                        va="center",
                        fontsize=6,
                        color="#ffffff",
                    )
            else:
                self._dash_ax_bar.bar(
                    xpos,
                    hrs,
                    color=self._theme_accent,
                    edgecolor=self._theme_accent,
                    linewidth=0.0,
                )
            self._dash_ax_bar.set_xticks(xpos)
            self._dash_ax_bar.set_xticklabels(xlabs, fontsize=8)
            self._dash_ax_bar.set_ylabel("Hours")
            self._dash_ax_bar.set_title(bar_title, fontsize=11, pad=8)
            ymax = max(hrs + [0.1])
            self._dash_ax_bar.set_ylim(0, max(ymax * 1.15, 0.5))
            self._dash_canvas_bar.draw()

            if getattr(self, "_dash_ax_money", None):
                self._dash_ax_money.clear()
                self._dash_style_axes(self._dash_fig_money, self._dash_ax_money)
                if show_m:
                    inc_w = int(s.get("income_cents") or 0) / 100.0
                    exp_w = int(s.get("expense_cents") or 0) / 100.0
                    self._dash_ax_money.barh(
                        [0, 1],
                        [inc_w, exp_w],
                        height=0.55,
                        color=["#2fa572", "#e67700"],
                        edgecolor="#333333",
                        linewidth=0.5,
                    )
                    self._dash_ax_money.set_yticks([0, 1])
                    self._dash_ax_money.set_yticklabels(["Income", "Expenses"], fontsize=9)
                    self._dash_ax_money.set_xlabel(f"{label} (major units)")
                    self._dash_ax_money.set_title("Money", fontsize=11, pad=8)
                    mx = max(inc_w, exp_w, 1.0)
                    self._dash_ax_money.set_xlim(0, mx * 1.2)
                    self._dash_fig_money.subplots_adjust(left=0.14, right=0.96, top=0.88, bottom=0.28)
                else:
                    self._dash_ax_money.text(
                        0.5,
                        0.5,
                        "Money charts hidden — enable “Show money on dashboard” in Settings",
                        ha="center",
                        va="center",
                        fontsize=10,
                        color=fg,
                        transform=self._dash_ax_money.transAxes,
                    )
                    self._dash_ax_money.set_xticks([])
                    self._dash_ax_money.set_yticks([])
                    self._dash_ax_money.set_title("Money", fontsize=11, pad=8)
                    self._dash_fig_money.subplots_adjust(left=0.12, right=0.96, top=0.88, bottom=0.30)
                self._dash_canvas_money.draw()
        except Exception as exc:  # noqa: BLE001
            self._dash_stats.configure(text=f"(Could not refresh dashboard: {exc})")
            try:
                if getattr(self, "_dash_status_lbl", None):
                    self._dash_status_lbl.configure(text="Current Status: (unavailable)")
            except Exception:
                pass

    def _schedule_dashboard_clock_live_refresh(self) -> None:
        """While clocked in, refresh dashboard stats periodically so 'today' time updates on screen."""

        def tick() -> None:
            try:
                st = load_user_state(self.uid)
                if st and st.current_work_start_utc and st.current_mode in ("working", "on_break"):
                    try:
                        if self.tabview.get() == "Dashboard":
                            self._refresh_dashboard()
                    except Exception:
                        pass
            except Exception:
                pass
            self.after(20000, tick)

        self.after(8000, tick)

    def _refresh_dashboard_current_status(self) -> None:
        """Update the one-line status; uses .configure(text=) only (CTkLabel + StringVar is unreliable)."""
        lbl = getattr(self, "_dash_status_lbl", None)
        if lbl is None:
            return
        st = load_user_state(self.uid)
        if not st or not st.current_work_start_utc:
            lbl.configure(text="Current Status: Clocked out")
            return
        if st.current_mode == "on_break":
            lbl.configure(text="Current Status: On break")
            return
        desc = (st.current_work_description or "").strip()
        cat_name = ""
        if st.current_work_category_id:
            try:
                c = get_work_category(self.cfg, int(st.current_work_category_id))
                cat_name = str(c.get("name") or "").strip() if c else ""
            except Exception:
                cat_name = ""
        if cat_name and desc:
            lbl.configure(text=f"Current Status: Working — {cat_name} / {desc}")
        elif cat_name:
            lbl.configure(text=f"Current Status: Working — {cat_name}")
        elif desc:
            lbl.configure(text=f"Current Status: Working — {desc}")
        else:
            lbl.configure(text="Current Status: Working")

    def _refresh_dashboard_action_buttons(self) -> None:
        if not getattr(self, "_dash_clock_in_btn", None):
            return
        st = load_user_state(self.uid)
        is_clocked_in = bool(st and st.current_work_start_utc and st.current_mode in ("working", "on_break"))
        if is_clocked_in:
            self._dash_clock_in_btn.pack_forget()
        else:
            if not self._dash_clock_in_btn.winfo_ismapped():
                self._dash_clock_in_btn.pack(anchor="e", pady=(0, 6), before=self._dash_break_btn)

    def _refresh_dashboard_layout_fit(self) -> None:
        try:
            host_w = int(getattr(self, "_dash_host", None).winfo_width()) if getattr(self, "_dash_host", None) else 0
            right_w = int(getattr(self, "_dash_actions_host", None).winfo_width()) if getattr(self, "_dash_actions_host", None) else 180
            avail = max(320, host_w - right_w - 44)
            if getattr(self, "_dash_stats", None):
                self._dash_stats.configure(wraplength=avail)
        except Exception:
            pass

    def _build_dashboard(self) -> None:
        t = self.tabview.tab("Dashboard")
        self._dash_host = ctk.CTkScrollableFrame(t)
        self._dash_host.pack(fill="both", expand=True)
        top = ctk.CTkFrame(self._dash_host, fg_color="transparent")
        top.pack(fill="x", pady=(0, 8))
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        right = ctk.CTkFrame(top, fg_color="transparent")
        right.pack(side="right", anchor="n", padx=(12, 0))
        self._dash_actions_host = right
        ctk.CTkLabel(
            left,
            text="Dashboard",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", pady=(0, 2))
        ctk.CTkLabel(
            left,
            text="Charts use unique clock time (overlaps are not double-counted). Open Work Log for the full entry list.",
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(0, 8))
        self._dash_refresh_btn = ctk.CTkButton(right, text="Refresh", width=140, command=self._refresh_dashboard)
        self._dash_manual_btn = ctk.CTkButton(right, text="Manual Entry", width=140, command=self._open_manual_entry_dialog)
        self._dash_clock_in_btn = ctk.CTkButton(right, text="Clock In", width=140, command=self._on_clock_in)
        self._dash_break_btn = ctk.CTkButton(right, text="Break", width=140, command=self._on_break_in)
        self._dash_clock_out_btn = ctk.CTkButton(right, text="Clock Out", width=140, command=self._on_clock_out)
        self._dash_refresh_btn.pack(anchor="e", pady=(0, 6))
        self._dash_manual_btn.pack(anchor="e", pady=(0, 6))
        self._dash_clock_in_btn.pack(anchor="e", pady=(0, 6))
        self._dash_break_btn.pack(anchor="e", pady=(0, 6))
        self._dash_clock_out_btn.pack(anchor="e", pady=(0, 2))
        self._add_help_bubble(right, "Action buttons let you refresh, add manual entries, and control the live timer.")
        self._refresh_dashboard_action_buttons()
        self._dash_status_lbl = ctk.CTkLabel(
            left,
            text="Current Status: Loading...",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        )
        self._dash_status_lbl.pack(anchor="w", pady=(0, 6))
        self._dash_stats = ctk.CTkLabel(
            left,
            text="Loading…",
            font=ctk.CTkFont(size=13),
            justify="left",
            anchor="w",
            wraplength=540,
        )
        self._dash_stats.pack(fill="x", anchor="w", pady=(0, 10))
        row = ctk.CTkFrame(left, fg_color="transparent")
        row.pack(anchor="w", pady=(0, 8))
        ctk.CTkLabel(row, text="Timescale").pack(side="left", padx=(0, 6))
        self._dash_scale = ctk.CTkSegmentedButton(
            row,
            values=["Daily", "Weekly", "Monthly", "Yearly", "Custom Day"],
            width=340,
            command=self._on_dash_scale_change,
        )
        self._dash_scale.set("Daily")
        self._dash_prev_scale = "Daily"
        self._dash_custom_day = date.today()
        self._dash_scale.pack(side="left")

        if not _DASHBOARD_CHARTS_AVAILABLE or Figure is None or FigureCanvasTkAgg is None:
            ctk.CTkLabel(
                left,
                text="Install matplotlib for charts:  pip install matplotlib",
                text_color="orange",
            ).pack(anchor="w", pady=12)
            return

        charts = ctk.CTkFrame(self._dash_host, fg_color="transparent")
        charts.pack(fill="x", expand=False, padx=4, pady=(4, 12))
        charts.grid_columnconfigure((0, 1), weight=1, uniform="dash")
        # Only the top chart row expands; Money stays a short strip (no huge empty band).
        charts.grid_rowconfigure(0, weight=1)
        charts.grid_rowconfigure(1, weight=0)

        left = ctk.CTkFrame(charts, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 10))
        self._dash_fig_pie = Figure(figsize=(4.2, 3.3), dpi=100)
        self._dash_ax_pie = self._dash_fig_pie.add_subplot(111)
        self._dash_canvas_pie = FigureCanvasTkAgg(self._dash_fig_pie, master=left)
        self._dash_canvas_pie.get_tk_widget().pack(fill="both", expand=True)

        right = ctk.CTkFrame(charts, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 10))
        self._dash_fig_bar = Figure(figsize=(4.2, 3.3), dpi=100)
        self._dash_ax_bar = self._dash_fig_bar.add_subplot(111)
        self._dash_canvas_bar = FigureCanvasTkAgg(self._dash_fig_bar, master=right)
        self._dash_canvas_bar.get_tk_widget().pack(fill="both", expand=True)

        money_fr = ctk.CTkFrame(charts, fg_color="transparent")
        money_fr.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(16, 6))
        self._dash_fig_money = Figure(figsize=(8.0, 1.35), dpi=100)
        self._dash_ax_money = self._dash_fig_money.add_subplot(111)
        self._dash_canvas_money = FigureCanvasTkAgg(self._dash_fig_money, master=money_fr)
        self._dash_canvas_money.get_tk_widget().pack(fill="x", expand=False, anchor="n")
        self.after(120, self._refresh_dashboard_layout_fit)
        top.bind("<Configure>", lambda _e: self._refresh_dashboard_layout_fit(), add="+")

    def _open_manual_entry_dialog(self) -> None:
        """Manual entry uses **tkinter + ttk only** — CustomTkinter widgets often fail to paint in a separate Toplevel on Windows."""
        root_bg = self._ui_hex_color(self._theme_bg, "#1a1a1a")
        panel_bg = self._ui_hex_color(self._theme_panel, "#2b2b2b")
        accent = self._ui_hex_color(self._theme_accent, "#1f538d")
        field_ch = 50
        top = tk.Toplevel(self)
        top.title("Manual Time Entry")
        top.configure(bg=root_bg)
        self._apply_window_icon(top)
        top.transient(self)
        try:
            top.withdraw()
        except Exception:
            pass

        def close_manual() -> None:
            self._safe_destroy_window(top)

        top.protocol("WM_DELETE_WINDOW", close_manual)

        lbl_fg = "#DCE4EE"
        ent_bg = "#343638"
        ent_hl = "#565B5E"
        font_lbl = ("Segoe UI", 12)
        font_title = ("Segoe UI", 15, "bold")
        font_entry = ("Segoe UI", 14)
        font_hint = ("Segoe UI", 11)
        font_btn = ("Segoe UI", 13)
        cb_style = "RRManual.TCombobox"
        try:
            sty = ttk.Style(top)
            sty.theme_use("clam")
            sty.configure(
                cb_style,
                fieldbackground=ent_bg,
                background=ent_bg,
                foreground=lbl_fg,
                arrowcolor=lbl_fg,
                font=font_entry,
            )
            sty.map(cb_style, fieldbackground=[("readonly", ent_bg)], background=[("readonly", ent_bg)])
            try:
                sty.configure(cb_style, padding=(8, 5))
            except tk.TclError:
                pass
        except Exception:
            cb_style = "TCombobox"

        body = tk.Frame(top, bg=panel_bg)
        body.pack(fill="both", expand=False)

        e_kw: dict[str, Any] = {
            "bg": ent_bg,
            "fg": lbl_fg,
            "insertbackground": lbl_fg,
            "relief": "flat",
            "font": font_entry,
            "width": field_ch,
            "highlightthickness": 1,
            "highlightbackground": ent_hl,
            "highlightcolor": ent_hl,
        }
        entry_pack = {"anchor": "w", "padx": 24, "ipady": 5}

        tk.Label(body, text="Start (YYYY-MM-DD HH:MM)", bg=panel_bg, fg=lbl_fg, font=font_lbl).pack(
            anchor="w", padx=24, pady=(20, 6)
        )
        ent_start = tk.Entry(body, **e_kw)
        ent_start.pack(**entry_pack)
        tk.Label(body, text="End (YYYY-MM-DD HH:MM)", bg=panel_bg, fg=lbl_fg, font=font_lbl).pack(
            anchor="w", padx=24, pady=(14, 6)
        )
        ent_end = tk.Entry(body, **e_kw)
        ent_end.pack(**entry_pack)

        tk.Label(
            body,
            text="What are you doing right now?",
            bg=panel_bg,
            fg=lbl_fg,
            font=font_title,
        ).pack(anchor="w", padx=24, pady=(18, 8))
        ent_desc = tk.Entry(body, **e_kw)
        ent_desc.pack(anchor="w", padx=24, pady=(0, 8), ipady=5)

        cats = list_work_categories(self.cfg, self.uid)
        cat_map: dict[str, int | None] = {}
        for c in cats:
            cat_map[c["name"]] = int(c["id"])
        cat_keys = list(cat_map.keys())
        cat_row = tk.Frame(body, bg=panel_bg)
        cat_row.pack(fill="x", padx=24, pady=(6, 4))
        tk.Label(cat_row, text="Category", bg=panel_bg, fg=lbl_fg, font=font_lbl).pack(side="left")
        cmb_cat = ttk.Combobox(cat_row, values=cat_keys or ["—"], width=field_ch, state="readonly", style=cb_style)
        cmb_cat.set(_default_activity_category_label(cat_keys) if cat_keys else "—")
        cmb_cat.pack(side="left", padx=(10, 8))
        tk.Button(
            cat_row,
            text="Add",
            font=font_btn,
            bg=accent,
            fg="#f0f0f0",
            activebackground=accent,
            activeforeground="#ffffff",
            relief="flat",
            padx=14,
            pady=6,
            command=lambda: self._quick_add_category_to_map(cmb_cat, cat_map),
        ).pack(side="left")

        tk.Label(
            body,
            text="Other category (optional — overrides dropdown if set)",
            bg=panel_bg,
            fg=lbl_fg,
            font=font_hint,
        ).pack(anchor="w", padx=24, pady=(4, 4))
        other_manual_cat = tk.Entry(body, **e_kw)
        other_manual_cat.pack(anchor="w", padx=24, pady=(0, 6), ipady=5)

        prows = list_projects(self.cfg, self.uid)
        proj_map: dict[str, int | None] = {"—": None}
        for p in prows:
            proj_map[p["name"]] = int(p["id"])
        proj_row = tk.Frame(body, bg=panel_bg)
        proj_row.pack(fill="x", padx=24, pady=(4, 8))
        tk.Label(proj_row, text="Project", bg=panel_bg, fg=lbl_fg, font=font_lbl).pack(side="left")
        cmb_proj = ttk.Combobox(proj_row, values=list(proj_map.keys()), width=field_ch, state="readonly", style=cb_style)
        cmb_proj.set("—")
        cmb_proj.pack(side="left", padx=(10, 8))
        tk.Button(
            proj_row,
            text="Add",
            font=font_btn,
            bg=accent,
            fg="#f0f0f0",
            activebackground=accent,
            activeforeground="#ffffff",
            relief="flat",
            padx=14,
            pady=6,
            command=lambda: self._quick_add_project_to_map(cmb_proj, proj_map),
        ).pack(side="left")

        try:
            latest = recent_time_entries(self.cfg, self.uid, limit=1)
            last = latest[0] if latest else {}
        except Exception:
            last = {}
        seed_desc = str(last.get("description") or "").strip() or getattr(self, "_last_activity_desc", "")
        saved_desc = str(settings_get(self.cfg, "last_popup_desc", "") or "").strip()
        if saved_desc:
            seed_desc = saved_desc
        if seed_desc:
            ent_desc.insert(0, seed_desc)
            ent_desc.icursor(tk.END)
        saved_cat = str(settings_get(self.cfg, "last_popup_category_name", "") or "").strip()
        if saved_cat and saved_cat in cat_map:
            cmb_cat.set(saved_cat)
        last_cat_id = last.get("work_category_id")
        if (not saved_cat) and last_cat_id is not None:
            last_cat = next((k for k, v in cat_map.items() if v == last_cat_id), "")
            if last_cat:
                cmb_cat.set(last_cat)
        saved_proj = str(settings_get(self.cfg, "last_popup_project_name", "") or "").strip()
        if saved_proj and saved_proj in proj_map:
            cmb_proj.set(saved_proj)
        last_proj_id = last.get("project_id")
        if (not saved_proj) and last_proj_id is not None:
            last_proj = next((k for k, v in proj_map.items() if v == last_proj_id), "")
            if last_proj:
                cmb_proj.set(last_proj)

        def submit() -> None:
            s_txt = ent_start.get().strip()
            e_txt = ent_end.get().strip()
            desc = ent_desc.get().strip() or "Manual entry"
            try:
                if not s_txt and not e_txt:
                    now_iso = datetime.now().replace(second=0, microsecond=0).isoformat()
                    s_iso = now_iso
                    e_iso = now_iso
                elif not s_txt:
                    e_iso = local_input_to_utc_naive_iso(self.cfg, e_txt)
                    e_dt = datetime.fromisoformat(e_iso)
                    s_iso = (e_dt - timedelta(minutes=1)).isoformat()
                elif not e_txt:
                    s_iso = local_input_to_utc_naive_iso(self.cfg, s_txt)
                    s_dt = datetime.fromisoformat(s_iso)
                    e_iso = (s_dt + timedelta(minutes=1)).isoformat()
                else:
                    s_iso = local_input_to_utc_naive_iso(self.cfg, s_txt)
                    e_iso = local_input_to_utc_naive_iso(self.cfg, e_txt)
            except ValueError:
                messagebox.showerror(
                    "RootRecord Business Manager",
                    "Use date format YYYY-MM-DD HH:MM for start/end.",
                )
                return
            if e_iso < s_iso:
                messagebox.showerror("RootRecord Business Manager", "End time must be after start time.")
                return
            oc = other_manual_cat.get().strip()
            if oc:
                upsert_work_category(self.cfg, self.uid, oc)
                cat_map[oc] = next(
                    (int(c["id"]) for c in list_work_categories(self.cfg, self.uid) if c["name"] == oc),
                    None,
                )
                wc_id = cat_map.get(oc)
            else:
                wc_id = cat_map.get(cmb_cat.get())
            pid = proj_map.get(cmb_proj.get())
            cat = get_work_category(self.cfg, wc_id) if wc_id else None
            billable = int(cat["billable"]) if cat else 1
            rate = effective_hourly_cents(
                self.cfg,
                self.uid,
                work_category_id=wc_id,
                project_id=pid,
                override_cents=None,
            )
            amt = compute_amount_cents(s_iso, e_iso, rate, bool(billable))
            insert_rich_time_entry(
                self.cfg,
                self.uid,
                s_iso,
                e_iso,
                "work",
                desc,
                get_machine_session_db_id(),
                work_category_id=wc_id,
                project_id=pid,
                billable=billable,
                hourly_rate_cents=rate,
                amount_cents=amt,
                currency=str(settings_get(self.cfg, "currency_default", "USD")),
                business_id=business_id_for_new_time_entry(self.cfg),
            )
            settings_set(self.cfg, "last_popup_desc", desc)
            settings_set(self.cfg, "last_popup_category_name", oc if oc else cmb_cat.get().strip())
            settings_set(self.cfg, "last_popup_project_name", cmb_proj.get().strip())
            self._last_activity_desc = desc
            self._refresh_dashboard()
            try:
                self._refresh_calendar()
            except Exception:
                pass
            close_manual()

        tk.Label(body, text="Press Enter or click Submit", bg=panel_bg, fg="#9eb0c4", font=font_hint).pack(
            anchor="w", padx=24, pady=(8, 8)
        )
        action_row = tk.Frame(body, bg=panel_bg)
        action_row.pack(fill="x", padx=24, pady=(8, 20))
        tk.Button(
            action_row,
            text="Submit",
            font=font_btn,
            bg=accent,
            fg="#f8f8f8",
            activebackground=accent,
            activeforeground="#ffffff",
            relief="flat",
            padx=22,
            pady=10,
            command=submit,
        ).pack(side="left")
        tk.Button(
            action_row,
            text="Cancel",
            font=font_btn,
            bg="#4a4a4a",
            fg="#f0f0f0",
            activebackground="#3d3d3d",
            relief="flat",
            padx=22,
            pady=10,
            command=close_manual,
        ).pack(side="left", padx=12)
        ent_desc.bind("<Return>", lambda _e: submit())
        try:
            top.update_idletasks()
            win_w = max(620, min(980, top.winfo_reqwidth()))
            win_h = max(360, min(820, top.winfo_reqheight()))
            top.geometry(f"{win_w}x{win_h}")
            top.minsize(560, 320)
            top.deiconify()
            top.update()
        except Exception:
            pass
        self._apply_window_icon(top)
        try:
            top.after(150, lambda w=top: self._apply_window_icon(w))
        except Exception:
            pass
        try:
            top.lift()
            top.focus_force()
            ent_start.focus_set()
        except Exception:
            pass
        try:
            top.grab_set()
        except Exception:
            pass

    def _build_time(self) -> None:
        t = self.tabview.tab("Time")
        cats = list_work_categories(self.cfg, self.uid)
        prows = list_projects(self.cfg, self.uid)
        self._cat_label_to_id: dict[str, int | None] = {"—": None}
        for c in cats:
            self._cat_label_to_id[c["name"]] = int(c["id"])
        self._proj_label_to_id: dict[str, int | None] = {"—": None}
        for p in prows:
            self._proj_label_to_id[p["name"]] = int(p["id"])
        cnames = list(self._cat_label_to_id.keys())
        pnames = list(self._proj_label_to_id.keys())

        ctk.CTkLabel(t, text="Category").pack(anchor="w")
        cat_row = ctk.CTkFrame(t, fg_color="transparent")
        cat_row.pack(anchor="w", pady=4)
        self._c_cat = ctk.CTkComboBox(cat_row, values=cnames, width=300)
        real_cats = [x for x in cnames if x != "—"]
        self._c_cat.set(
            _default_activity_category_label(real_cats) if real_cats else (cnames[0] if cnames else "—")
        )
        self._c_cat.pack(side="left")
        ctk.CTkButton(cat_row, text="Add Category", width=120, command=self._add_category_inline).pack(
            side="left", padx=8
        )
        self._ent_other_cat = ctk.CTkEntry(t, width=340, placeholder_text="Other category (optional, overrides dropdown)")
        self._ent_other_cat.pack(anchor="w", pady=(2, 6))
        if len(cnames) > 10:
            ctk.CTkButton(cat_row, text="Browse", width=84, command=lambda: self._pick_category(self._c_cat)).pack(
                side="left", padx=4
            )

        ctk.CTkLabel(t, text="Project (optional)").pack(anchor="w")
        proj_row = ctk.CTkFrame(t, fg_color="transparent")
        proj_row.pack(anchor="w", pady=4)
        self._c_proj = ctk.CTkComboBox(proj_row, values=pnames, width=280)
        self._c_proj.set("—")
        self._c_proj.pack(side="left")
        ctk.CTkButton(proj_row, text="Add Project", width=110, command=self._add_project_inline).pack(
            side="left", padx=8
        )
        if len(pnames) > 10:
            ctk.CTkButton(proj_row, text="Browse", width=84, command=lambda: self._pick_project(self._c_proj)).pack(
                side="left", padx=4
            )

        self._ent_desc = ctk.CTkEntry(t, width=640, placeholder_text="What are you doing?")
        self._ent_desc.pack(fill="x", pady=8)

        self._ent_tags = ctk.CTkEntry(t, width=640, placeholder_text="Tags (comma-separated) — optional")
        self._ent_tags.pack(fill="x", pady=4)

        row = ctk.CTkFrame(t, fg_color="transparent")
        row.pack(fill="x", pady=8)
        ctk.CTkButton(row, text="Log activity", command=self._on_log_time, height=34).pack(side="left", padx=4)
        ctk.CTkButton(
            row,
            text="Clock In",
            command=self._on_clock_in,
            height=34,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            row,
            text="Break In",
            command=self._on_break_in,
            height=34,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            row,
            text="Break Out",
            command=self._on_break_out,
            height=34,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            row,
            text="Clock Out",
            command=self._on_clock_out,
            height=34,
        ).pack(side="left", padx=4)
        self._add_help_bubble(row, "Log activity updates running task. Clock In/Out and Break buttons control timer states.")

        qa = ctk.CTkScrollableFrame(t, height=120, label_text="Quick actions")
        qa.pack(fill="x", pady=8)
        self._qa_frame = qa
        self._reload_quick_actions()

    def _reload_quick_actions(self) -> None:
        if not getattr(self, "_qa_frame", None):
            return
        for w in self._qa_frame.winfo_children():
            w.destroy()
        for a in list_quick_actions(self.cfg, self.uid):

            def _go(
                x=a,
            ) -> None:
                target = getattr(self, "_dash_task_desc", None) or getattr(self, "_ent_desc", None)
                if target is not None:
                    target.delete(0, "end")
                    if x.get("default_description"):
                        target.insert(0, x["default_description"])

            ctk.CTkButton(
                self._qa_frame,
                text=a.get("label", "Quick"),
                width=160,
                command=_go,
            ).pack(side="left", padx=4, pady=4)

    def _ask_text_dialog(
        self,
        *,
        title: str,
        prompt: str,
        placeholder: str = "",
        initial: str = "",
        ok_text: str = "Add",
    ) -> str | None:
        m = self._tk_modal_theme()
        top = tk.Toplevel(self)
        top.title(title)
        top.configure(bg=m["root_bg"])
        self._apply_window_icon(top)
        top.geometry("560x240")
        top.minsize(480, 200)
        top.transient(self)
        body = tk.Frame(top, bg=m["panel_bg"])
        body.pack(fill="both", expand=True)
        tk.Label(body, text=prompt, bg=m["panel_bg"], fg=m["heading"], font=m["font_title"]).pack(
            anchor="w", padx=20, pady=(18, 8)
        )
        if placeholder:
            tk.Label(body, text=placeholder, bg=m["panel_bg"], fg="#9eb0c4", font=m["font_hint"]).pack(
                anchor="w", padx=20, pady=(0, 4)
            )
        e_kw: dict[str, Any] = {
            "bg": m["ent_bg"],
            "fg": m["lbl_fg"],
            "insertbackground": m["lbl_fg"],
            "relief": "flat",
            "font": m["font_entry"],
            "width": 52,
            "highlightthickness": 1,
            "highlightbackground": m["ent_hl"],
            "highlightcolor": m["ent_hl"],
        }
        ent = tk.Entry(body, **e_kw)
        ent.pack(anchor="w", padx=20, pady=(4, 12), ipady=6)
        if initial:
            ent.insert(0, initial)
            ent.select_range(0, tk.END)
        ent.focus_set()
        result: dict[str, str | None] = {"value": None}

        def submit() -> None:
            txt = ent.get().strip()
            result["value"] = txt if txt else None
            self._safe_destroy_window(top)

        def cancel() -> None:
            result["value"] = None
            self._safe_destroy_window(top)

        row = tk.Frame(body, bg=m["panel_bg"])
        row.pack(fill="x", padx=20, pady=(8, 18))
        tk.Button(
            row,
            text=ok_text,
            font=m["font_btn"],
            bg=m["accent"],
            fg="#f8f8f8",
            activebackground=m["accent"],
            relief="flat",
            padx=20,
            pady=10,
            command=submit,
        ).pack(side="left")
        tk.Button(
            row,
            text="Cancel",
            font=m["font_btn"],
            bg="#4a4a4a",
            fg="#f0f0f0",
            activebackground="#3d3d3d",
            relief="flat",
            padx=20,
            pady=10,
            command=cancel,
        ).pack(side="left", padx=10)
        ent.bind("<Return>", lambda _e: submit())
        top.bind("<Escape>", lambda _e: cancel())
        top.protocol("WM_DELETE_WINDOW", cancel)
        try:
            top.update_idletasks()
        except Exception:
            pass
        self._apply_window_icon(top)
        try:
            top.grab_set()
        except Exception:
            pass
        self.wait_window(top)
        return result["value"]

    def _add_project_inline(self) -> None:
        name = self._ask_text_dialog(
            title="Add Project",
            prompt="Project name",
            placeholder="Enter project name",
            ok_text="Add Project",
        )
        if not name:
            return
        clean = name.strip()
        if not clean:
            return
        insert_project(self.cfg, self.uid, clean)
        prows = list_projects(self.cfg, self.uid)
        self._proj_label_to_id = {"—": None}
        for p in prows:
            self._proj_label_to_id[p["name"]] = int(p["id"])
        self._c_proj.configure(values=list(self._proj_label_to_id.keys()))
        self._c_proj.set(clean)

    def _add_category_inline(self) -> None:
        name = self._ask_text_dialog(
            title="Add Category",
            prompt="Category name",
            placeholder="Enter category name",
            ok_text="Add Category",
        )
        if not name:
            return
        clean = name.strip()
        if not clean:
            return
        upsert_work_category(self.cfg, self.uid, clean)
        cats = list_work_categories(self.cfg, self.uid)
        self._cat_label_to_id = {"—": None}
        for c in cats:
            self._cat_label_to_id[c["name"]] = int(c["id"])
        self._c_cat.configure(values=list(self._cat_label_to_id.keys()))
        self._c_cat.set(clean)

    def _on_log_time(self) -> None:
        desc = self._ent_desc.get().strip()
        wc, pid, tag_ids = self._read_time_form_values()
        hr_ov = None
        msg = handle_activity_text(
            self.cfg,
            self.uid,
            desc,
            work_category_id=wc,
            project_id=pid,
            notes="",
            tag_ids=tag_ids,
            billable=None,
            hourly_override_cents=hr_ov,
        )
        self._last_activity_desc = desc or self._last_activity_desc
        messagebox.showinfo("RootRecord Business Manager", msg)
        self._ent_desc.delete(0, "end")
        self._ent_tags.delete(0, "end")
        self._refresh_dashboard()
        try:
            self._refresh_calendar()
        except Exception:
            pass

    def _read_time_form_values(self) -> tuple[int | None, int | None, list[int] | None]:
        tags_raw = self._ent_tags.get().strip()
        cn = self._c_cat.get()
        pn = self._c_proj.get()
        other_cat = self._ent_other_cat.get().strip() if hasattr(self, "_ent_other_cat") else ""
        if other_cat:
            upsert_work_category(self.cfg, self.uid, other_cat)
            cats = list_work_categories(self.cfg, self.uid)
            self._cat_label_to_id = {"—": None}
            for c in cats:
                self._cat_label_to_id[c["name"]] = int(c["id"])
            self._c_cat.configure(values=list(self._cat_label_to_id.keys()))
            self._c_cat.set(other_cat)
            wc = self._cat_label_to_id.get(other_cat)
        else:
            wc = self._cat_label_to_id.get(cn)
        pid = self._proj_label_to_id.get(pn)
        tag_ids: list[int] = []
        if tags_raw:
            from data_api import ensure_tag

            for part in re.split(r"[,;]", tags_raw):
                p = part.strip()
                if p:
                    tag_ids.append(ensure_tag(self.cfg, self.uid, p))
        return wc, pid, (tag_ids or None)

    def _pick_from_scrollable_list(self, title: str, options: list[str]) -> str | None:
        m = self._tk_modal_theme()
        top = tk.Toplevel(self)
        top.title(title)
        top.configure(bg=m["root_bg"])
        self._apply_window_icon(top)
        top.geometry("600x560")
        top.minsize(480, 420)
        top.transient(self)
        body = tk.Frame(top, bg=m["panel_bg"])
        body.pack(fill="both", expand=True)
        result: dict[str, str | None] = {"value": None}
        current: dict[str, list[str]] = {"vals": list(options)}

        def pick_and_close(n: str) -> None:
            result["value"] = n
            self._safe_destroy_window(top)

        def cancel_pick() -> None:
            result["value"] = None
            self._safe_destroy_window(top)

        tk.Label(body, text=title, bg=m["panel_bg"], fg=m["heading"], font=m["font_title"]).pack(
            anchor="w", padx=16, pady=(14, 8)
        )
        tk.Label(body, text="Filter…", bg=m["panel_bg"], fg="#9eb0c4", font=m["font_hint"]).pack(
            anchor="w", padx=16, pady=(0, 4)
        )
        e_kw: dict[str, Any] = {
            "bg": m["ent_bg"],
            "fg": m["lbl_fg"],
            "insertbackground": m["lbl_fg"],
            "relief": "flat",
            "font": m["font_entry"],
            "width": 58,
            "highlightthickness": 1,
            "highlightbackground": m["ent_hl"],
            "highlightcolor": m["ent_hl"],
        }
        search = tk.Entry(body, **e_kw)
        search.pack(anchor="w", padx=16, pady=(0, 8), ipady=5)

        list_fr = tk.Frame(body, bg=m["panel_bg"])
        list_fr.pack(fill="both", expand=True, padx=16, pady=4)
        scroll = tk.Scrollbar(list_fr)
        scroll.pack(side="right", fill="y")
        lb = tk.Listbox(
            list_fr,
            yscrollcommand=scroll.set,
            bg=m["ent_bg"],
            fg=m["lbl_fg"],
            font=m["font_entry"],
            selectbackground=m["accent"],
            selectforeground="#ffffff",
            height=18,
            width=64,
            activestyle="dotbox",
        )
        lb.pack(side="left", fill="both", expand=True)
        scroll.config(command=lb.yview)

        def refill(vals: list[str]) -> None:
            lb.delete(0, tk.END)
            for x in vals:
                lb.insert(tk.END, x)
            if vals:
                lb.selection_set(0)

        def pick_selected(_e: object = None) -> None:
            sel = lb.curselection()
            if not sel:
                return
            pick_and_close(lb.get(sel[0]))

        def on_search(_e: object = None) -> None:
            q = search.get().strip().lower()
            if not q:
                refill(current["vals"])
                return
            refill([x for x in current["vals"] if q in x.lower()])

        refill(current["vals"])
        search.bind("<KeyRelease>", on_search)
        lb.bind("<Double-Button-1>", pick_selected)

        btn_row = tk.Frame(body, bg=m["panel_bg"])
        btn_row.pack(fill="x", padx=16, pady=14)
        tk.Button(
            btn_row,
            text="OK",
            font=m["font_btn"],
            bg=m["accent"],
            fg="#ffffff",
            activebackground=m["accent"],
            relief="flat",
            padx=20,
            pady=10,
            command=pick_selected,
        ).pack(side="left")
        tk.Button(
            btn_row,
            text="Cancel",
            font=m["font_btn"],
            bg="#4a4a4a",
            fg="#f0f0f0",
            activebackground="#3d3d3d",
            relief="flat",
            padx=20,
            pady=10,
            command=cancel_pick,
        ).pack(side="left", padx=10)
        top.protocol("WM_DELETE_WINDOW", cancel_pick)
        try:
            top.update_idletasks()
        except Exception:
            pass
        self._apply_window_icon(top)
        try:
            top.grab_set()
        except Exception:
            pass
        search.focus_set()
        self.wait_window(top)
        return result["value"]

    def _pick_category(self, combo: ctk.CTkComboBox) -> None:
        choice = self._pick_from_scrollable_list("Select Category", list(self._cat_label_to_id.keys()))
        if choice:
            combo.set(choice)

    def _pick_project(self, combo: ctk.CTkComboBox) -> None:
        choice = self._pick_from_scrollable_list("Select Project", list(self._proj_label_to_id.keys()))
        if choice:
            combo.set(choice)

    def _prompt_clock_in_context(self, default_desc: str) -> tuple[str, int | None, int | None] | None:
        m = self._tk_modal_theme()
        top = tk.Toplevel(self)
        top.title("Start Work")
        top.configure(bg=m["root_bg"])
        self._apply_window_icon(top)
        top.geometry("780x420")
        top.minsize(720, 380)
        top.transient(self)
        body = tk.Frame(top, bg=m["panel_bg"])
        body.pack(fill="both", expand=True)
        cb_style = self._tk_modal_configure_combobox_style(top, "RRStart.TCombobox", m)
        e_kw: dict[str, Any] = {
            "bg": m["ent_bg"],
            "fg": m["lbl_fg"],
            "insertbackground": m["lbl_fg"],
            "relief": "flat",
            "font": m["font_entry"],
            "width": 58,
            "highlightthickness": 1,
            "highlightbackground": m["ent_hl"],
            "highlightcolor": m["ent_hl"],
        }

        tk.Label(
            body,
            text="What are you starting to work on?",
            bg=m["panel_bg"],
            fg=m["heading"],
            font=m["font_title"],
        ).pack(anchor="w", padx=24, pady=(20, 8))
        tk.Label(body, text="Short task description", bg=m["panel_bg"], fg="#9eb0c4", font=m["font_hint"]).pack(
            anchor="w", padx=24, pady=(0, 4)
        )
        ent_desc = tk.Entry(body, **e_kw)
        ent_desc.pack(anchor="w", padx=24, pady=(0, 12), ipady=6)
        if default_desc:
            ent_desc.insert(0, default_desc)

        cats = list_work_categories(self.cfg, self.uid)
        cat_map: dict[str, int] = {str(c["name"]): int(c["id"]) for c in cats}
        tk.Label(body, text="Category", bg=m["panel_bg"], fg=m["lbl_fg"], font=m["font_lbl"]).pack(
            anchor="w", padx=24, pady=(6, 4)
        )
        cat_row = tk.Frame(body, bg=m["panel_bg"])
        cat_row.pack(fill="x", padx=24, pady=(0, 8))
        cmb_cat = ttk.Combobox(
            cat_row,
            values=list(cat_map.keys()) or ["—"],
            width=48,
            state="readonly",
            style=cb_style,
        )
        if cat_map:
            cmb_cat.set(list(cat_map.keys())[0])
        else:
            cmb_cat.set("—")
        cmb_cat.pack(side="left", fill="x", expand=True)

        prows = list_projects(self.cfg, self.uid)
        proj_map: dict[str, int | None] = {"—": None}
        for p in prows:
            proj_map[str(p["name"])] = int(p["id"])
        tk.Label(body, text="Project (optional)", bg=m["panel_bg"], fg=m["lbl_fg"], font=m["font_lbl"]).pack(
            anchor="w", padx=24, pady=(6, 4)
        )
        proj_row = tk.Frame(body, bg=m["panel_bg"])
        proj_row.pack(fill="x", padx=24, pady=(0, 12))
        cmb_proj = ttk.Combobox(proj_row, values=list(proj_map.keys()), width=48, state="readonly", style=cb_style)
        cmb_proj.set("—")
        cmb_proj.pack(side="left", fill="x", expand=True)

        result: dict[str, str | int | None] = {"desc": None, "wc": None, "pid": None}

        def submit() -> None:
            desc = ent_desc.get().strip() or "Working"
            cat_name = cmb_cat.get().strip()
            wc = cat_map.get(cat_name)
            if wc is None and cat_map:
                messagebox.showerror("RootRecord Business Manager", "Please select a category before clocking in.")
                return
            result["desc"] = desc
            result["wc"] = wc
            result["pid"] = proj_map.get(cmb_proj.get().strip())
            self._safe_destroy_window(top)

        def cancel() -> None:
            self._safe_destroy_window(top)

        row = tk.Frame(body, bg=m["panel_bg"])
        row.pack(fill="x", padx=24, pady=(8, 22))
        tk.Button(
            row,
            text="Start",
            font=m["font_btn"],
            bg=m["accent"],
            fg="#f8f8f8",
            activebackground=m["accent"],
            relief="flat",
            padx=22,
            pady=10,
            command=submit,
        ).pack(side="left")
        tk.Button(
            row,
            text="Cancel",
            font=m["font_btn"],
            bg="#4a4a4a",
            fg="#f0f0f0",
            activebackground="#3d3d3d",
            relief="flat",
            padx=22,
            pady=10,
            command=cancel,
        ).pack(side="left", padx=12)
        ent_desc.bind("<Return>", lambda _e: submit())
        top.protocol("WM_DELETE_WINDOW", cancel)
        try:
            top.update_idletasks()
        except Exception:
            pass
        self._apply_window_icon(top)
        try:
            top.grab_set()
        except Exception:
            pass
        ent_desc.focus_set()
        self.wait_window(top)
        if result["desc"] is None:
            return None
        return str(result["desc"]), result["wc"] if isinstance(result["wc"], int) else None, (
            result["pid"] if isinstance(result["pid"], int) else None
        )

    def _on_clock_in(self) -> None:
        default_desc = self._time_action_description(default="Working")
        picked = self._prompt_clock_in_context(default_desc)
        if picked is None:
            return
        desc, wc_id, pid = picked
        st = load_user_state(self.uid)
        if st and st.current_mode == "on_break":
            # Per UX request: Clock In should also resume from break.
            self._run_time_action(
                break_out(
                    self.cfg,
                    self.uid,
                    resume_description=desc or None,
                    work_category_id=wc_id,
                    project_id=pid,
                )
            )
        else:
            self._run_time_action(
                clock_in(
                    self.cfg,
                    self.uid,
                    desc,
                    work_category_id=wc_id,
                    project_id=pid,
                    tag_ids=None,
                )
            )
        self._last_activity_desc = desc
        self._resume_prompts_if_working_now()

    def _on_break_in(self) -> None:
        desc = self._time_action_description(default="Break")
        self._run_time_action(break_in(self.cfg, self.uid, description=desc))

    def _on_break_out(self) -> None:
        desc = self._time_action_description(default="")
        self._run_time_action(break_out(self.cfg, self.uid, resume_description=desc or None, project_id=None))
        if desc:
            self._last_activity_desc = desc
        self._resume_prompts_if_working_now()

    def _on_clock_out(self) -> None:
        self._run_time_action(clock_out(self.cfg, self.uid))

    def _time_action_description(self, *, default: str) -> str:
        if getattr(self, "_dash_task_desc", None) is not None:
            txt = self._dash_task_desc.get().strip()
            if txt:
                return txt
        if getattr(self, "_ent_desc", None) is not None:
            txt = self._ent_desc.get().strip()
            if txt:
                return txt
        return getattr(self, "_last_activity_desc", "") or default

    def _resume_prompts_if_working_now(self) -> None:
        st = load_user_state(self.uid)
        if st and st.current_mode == "working" and st.current_work_start_utc:
            # Re-arm timer loop and show one check-in shortly after (single pending callback).
            self._schedule_prompts()
            self._cancel_prompt_immediate_job()
            self._prompt_immediate_job = self.after(120, self._on_prompt_immediate)

    def _run_time_action(self, msg: str) -> None:
        messagebox.showinfo("RootRecord Business Manager", msg)
        self._refresh_dashboard()
        try:
            self._refresh_calendar()
        except Exception:
            pass

    def _combo_apply_values(self, cmb: Any, values: list[str]) -> None:
        """CTkComboBox and ttk.Combobox both support updating the option list (manual dialog uses ttk)."""
        try:
            cmb.configure(values=values)
            return
        except Exception:
            pass
        try:
            cmb["values"] = values
        except Exception:
            pass

    @staticmethod
    def _ui_hex_color(val: Any, fallback: str = "#1a1a1a") -> str:
        if isinstance(val, str) and val.startswith("#"):
            return val
        if isinstance(val, (list, tuple)) and len(val) > 0:
            return str(val[-1])
        return fallback

    def _tk_modal_theme(self) -> dict[str, Any]:
        """Shared colors/fonts for tk/ttk modal dialogs (CTk popups often blank on Windows)."""
        return {
            "root_bg": self._ui_hex_color(self._theme_bg, "#1a1a1a"),
            "panel_bg": self._ui_hex_color(self._theme_panel, "#2b2b2b"),
            "accent": self._ui_hex_color(self._theme_accent, "#1f538d"),
            "heading": self._ui_hex_color(self._theme_text_heading, "#E0E6F0"),
            "lbl_fg": "#DCE4EE",
            "ent_bg": "#343638",
            "ent_hl": "#565B5E",
            "font_lbl": ("Segoe UI", 12),
            "font_title": ("Segoe UI", 15, "bold"),
            "font_entry": ("Segoe UI", 14),
            "font_hint": ("Segoe UI", 11),
            "font_btn": ("Segoe UI", 13),
        }

    def _tk_modal_configure_combobox_style(self, top: tk.Misc, style_name: str, m: dict[str, Any]) -> str:
        try:
            sty = ttk.Style(top)
            sty.theme_use("clam")
            sty.configure(
                style_name,
                fieldbackground=m["ent_bg"],
                background=m["ent_bg"],
                foreground=m["lbl_fg"],
                arrowcolor=m["lbl_fg"],
                font=m["font_entry"],
            )
            try:
                sty.configure(style_name, padding=(8, 5))
            except tk.TclError:
                pass
            sty.map(style_name, fieldbackground=[("readonly", m["ent_bg"])], background=[("readonly", m["ent_bg"])])
            return style_name
        except Exception:
            return "TCombobox"

    def _quick_add_category_to_map(self, cmb: Any, cat_map: dict[str, int | None]) -> None:
        name = self._ask_text_dialog(
            title="Add Category",
            prompt="Category name",
            placeholder="Enter category name",
            ok_text="Add Category",
        )
        if not name:
            return
        clean = name.strip()
        if not clean:
            return
        upsert_work_category(self.cfg, self.uid, clean)
        cid = next((int(c["id"]) for c in list_work_categories(self.cfg, self.uid) if c["name"] == clean), None)
        cat_map[clean] = cid
        self._combo_apply_values(cmb, list(cat_map.keys()))
        cmb.set(clean)

    def _quick_add_category_from_popup(self, cmb: Any) -> None:
        self._quick_add_category_to_map(cmb, self._popup_cat_map)

    def _quick_add_project_to_map(self, cmb: Any, proj_map: dict[str, int | None]) -> None:
        name = self._ask_text_dialog(
            title="Add Project",
            prompt="Project name",
            placeholder="Enter project name",
            ok_text="Add Project",
        )
        if not name:
            return
        clean = name.strip()
        if not clean:
            return
        insert_project(self.cfg, self.uid, clean)
        pid = next((int(p["id"]) for p in list_projects(self.cfg, self.uid) if p["name"] == clean), None)
        proj_map[clean] = pid
        self._combo_apply_values(cmb, list(proj_map.keys()))
        cmb.set(clean)

    def _quick_add_project_from_popup(self, cmb: Any) -> None:
        self._quick_add_project_to_map(cmb, self._popup_proj_map)

    def _log_from_values(
        self,
        desc: str,
        wc: int | None,
        pid: int | None,
        tags: list[int] | None,
        hr_ov: int | None,
    ) -> None:
        handle_activity_text(
            self.cfg,
            self.uid,
            desc,
            work_category_id=wc,
            project_id=pid,
            tag_ids=tags,
            billable=None,
            hourly_override_cents=hr_ov,
        )
        if desc:
            self._last_activity_desc = desc
        self._refresh_dashboard()

    def _build_money(self) -> None:
        if getattr(self, "_finance_clients_sections", None):
            t = self._finance_clients_sections["Money"]
        else:
            t = self.tabview.tab("Money")
        ctk.CTkLabel(
            t,
            text="Income (primary) and expense tracking",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", pady=(0, 8))

        income = ctk.CTkFrame(t)
        income.pack(fill="x", pady=8)
        ctk.CTkLabel(income, text="Income amount (major units e.g. 350.00)").grid(row=0, column=0, padx=4, pady=4)
        self._inc_amt = ctk.CTkEntry(income, width=120)
        self._inc_amt.grid(row=0, column=1, padx=4)
        hb_inc = ctk.CTkLabel(income, text="?", width=18, height=18, fg_color=self._theme_help_btn_bg, text_color=self._theme_help_btn_fg, corner_radius=9)
        hb_inc.grid(row=0, column=3, padx=(4, 0), sticky="w")
        self._attach_tooltip(hb_inc, "Adds to Income totals (Dashboard, Reports, Tax Estimator net profit).")
        self._inc_cur = ctk.CTkComboBox(income, values=["USD", "EUR", "GBP", "CAD"], width=100)
        self._inc_cur.set(settings_get(self.cfg, "currency_default", "USD"))
        self._inc_cur.grid(row=0, column=2, padx=4)
        ctk.CTkLabel(income, text="Description").grid(row=1, column=0, padx=4)
        self._inc_desc = ctk.CTkEntry(income, width=420)
        self._inc_desc.grid(row=1, column=1, columnspan=2, sticky="ew", padx=4)
        ctk.CTkButton(income, text="Add income", command=self._on_add_income).grid(row=2, column=1, pady=8)

        form = ctk.CTkFrame(t)
        form.pack(fill="x", pady=8)
        ctk.CTkLabel(form, text="Amount (major units e.g. 49.99)").grid(row=0, column=0, padx=4, pady=4)
        self._exp_amt = ctk.CTkEntry(form, width=120)
        self._exp_amt.grid(row=0, column=1, padx=4)
        self._exp_cur = ctk.CTkComboBox(form, values=["USD", "EUR", "GBP", "CAD"], width=100)
        self._exp_cur.set(settings_get(self.cfg, "currency_default", "USD"))
        self._exp_cur.grid(row=0, column=2, padx=4)
        ctk.CTkLabel(form, text="Funding").grid(row=0, column=3, padx=4, pady=4)
        self._exp_funding = ctk.CTkComboBox(
            form,
            values=["cash", "bank", "credit_card", "borrowed_funds"],
            width=140,
        )
        self._exp_funding.set("cash")
        self._exp_funding.grid(row=0, column=4, padx=4)
        hb_funding = ctk.CTkLabel(form, text="?", width=18, height=18, fg_color=self._theme_help_btn_bg, text_color=self._theme_help_btn_fg, corner_radius=9)
        hb_funding.grid(row=0, column=5, padx=(2, 0), sticky="w")
        self._attach_tooltip(
            hb_funding,
            "Funding source tags how the expense was paid. Choosing credit_card or borrowed_funds auto-creates a matching debt entry.",
        )
        ctk.CTkLabel(form, text="Description").grid(row=1, column=0, padx=4)
        self._exp_desc = ctk.CTkEntry(form, width=400)
        self._exp_desc.grid(row=1, column=1, columnspan=4, sticky="ew", padx=4)
        ctk.CTkButton(form, text="Add expense", command=self._on_add_expense).grid(row=2, column=1, pady=8)
        hb_exp = ctk.CTkLabel(form, text="?", width=18, height=18, fg_color=self._theme_help_btn_bg, text_color=self._theme_help_btn_fg, corner_radius=9)
        hb_exp.grid(row=2, column=2, padx=(4, 0), sticky="w")
        self._attach_tooltip(
            hb_exp,
            "Adds to Expense totals and reduces net profit in Dashboard/Reports/Tax. In credit/borrowed mode, liability is also tracked.",
        )

        sched = ctk.CTkFrame(t)
        sched.pack(fill="x", pady=(4, 8))
        ctk.CTkLabel(sched, text="Scheduled expenses", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, columnspan=7, sticky="w", padx=4, pady=(4, 8)
        )
        ctk.CTkLabel(sched, text="Amount").grid(row=1, column=0, padx=4, pady=4)
        self._sch_exp_amt = ctk.CTkEntry(sched, width=100)
        self._sch_exp_amt.grid(row=1, column=1, padx=4)
        self._sch_exp_cur = ctk.CTkComboBox(sched, values=["USD", "EUR", "GBP", "CAD"], width=90)
        self._sch_exp_cur.set(settings_get(self.cfg, "currency_default", "USD"))
        self._sch_exp_cur.grid(row=1, column=2, padx=4)
        self._sch_exp_freq = ctk.CTkComboBox(sched, values=["daily", "weekly", "monthly"], width=110)
        self._sch_exp_freq.set("monthly")
        self._sch_exp_freq.grid(row=1, column=3, padx=4)
        hb_sched = ctk.CTkLabel(sched, text="?", width=18, height=18, fg_color=self._theme_help_btn_bg, text_color=self._theme_help_btn_fg, corner_radius=9)
        hb_sched.grid(row=1, column=7, padx=(4, 0), sticky="w")
        self._attach_tooltip(hb_sched, "Auto-posts due expense rows. Posted rows reduce net profit just like manual expenses.")
        ctk.CTkLabel(sched, text="First due (YYYY-MM-DD)").grid(row=1, column=4, padx=4)
        self._sch_exp_due = ctk.CTkEntry(sched, width=130)
        self._sch_exp_due.insert(0, date.today().isoformat())
        self._sch_exp_due.grid(row=1, column=5, padx=4)
        ctk.CTkButton(sched, text="Add schedule", command=self._on_add_scheduled_expense).grid(row=1, column=6, padx=6)
        ctk.CTkLabel(sched, text="Description").grid(row=2, column=0, padx=4)
        self._sch_exp_desc = ctk.CTkEntry(sched, width=460)
        self._sch_exp_desc.grid(row=2, column=1, columnspan=4, padx=4, sticky="ew")
        ctk.CTkLabel(sched, text="Merchant").grid(row=2, column=5, padx=4)
        self._sch_exp_merchant = ctk.CTkEntry(sched, width=140)
        self._sch_exp_merchant.grid(row=2, column=6, padx=4)
        self._sched_list = ctk.CTkTextbox(sched, height=110, font=ctk.CTkFont(family="Consolas", size=11))
        self._sched_list.grid(row=3, column=0, columnspan=7, sticky="ew", padx=4, pady=(8, 4))
        actions = ctk.CTkFrame(sched, fg_color="transparent")
        actions.grid(row=4, column=0, columnspan=7, sticky="w", padx=4, pady=(4, 0))
        ctk.CTkLabel(actions, text="Schedule ID").pack(side="left")
        self._sched_action_id = ctk.CTkEntry(actions, width=90)
        self._sched_action_id.pack(side="left", padx=6)
        ctk.CTkButton(actions, text="Pause", width=70, command=self._on_pause_schedule).pack(side="left", padx=(0, 6))
        ctk.CTkButton(actions, text="Resume", width=70, command=self._on_resume_schedule).pack(side="left", padx=(0, 6))
        ctk.CTkButton(actions, text="Run due now", width=110, command=self._on_run_due_schedules_now).pack(side="left")
        self._add_help_bubble(actions, "Use Schedule ID to pause/resume. Run due now posts overdue schedules into Expenses.")

        res = ctk.CTkFrame(t)
        res.pack(fill="x", pady=(2, 8))
        ctk.CTkLabel(res, text="Resource entries (non-income funding)", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, columnspan=8, sticky="w", padx=4, pady=(4, 8)
        )
        ctk.CTkLabel(res, text="Amount").grid(row=1, column=0, padx=4, pady=4)
        self._res_amt = ctk.CTkEntry(res, width=100)
        self._res_amt.grid(row=1, column=1, padx=4)
        self._res_cur = ctk.CTkComboBox(res, values=["USD", "EUR", "GBP", "CAD"], width=90)
        self._res_cur.set(settings_get(self.cfg, "currency_default", "USD"))
        self._res_cur.grid(row=1, column=2, padx=4)
        self._res_type = ctk.CTkComboBox(
            res,
            values=["loan_draw", "owner_contribution", "credit_draw", "borrowed_funds"],
            width=160,
        )
        self._res_type.set("loan_draw")
        self._res_type.grid(row=1, column=3, padx=4)
        self._res_desc = ctk.CTkEntry(res, width=360, placeholder_text="Description")
        self._res_desc.grid(row=1, column=4, columnspan=2, padx=4, sticky="ew")
        ctk.CTkButton(res, text="Add resource", command=self._on_add_resource).grid(row=1, column=6, padx=6)
        hb_res = ctk.CTkLabel(res, text="?", width=18, height=18, fg_color=self._theme_help_btn_bg, text_color=self._theme_help_btn_fg, corner_radius=9)
        hb_res.grid(row=1, column=7, padx=(2, 0), sticky="w")
        if not hasattr(self, "_help_bubbles"):
            self._help_bubbles = []
        self._help_bubbles.append(hb_res)
        self._attach_tooltip(
            hb_res,
            "Resource entries track funding inflows (loan draws, owner contributions, credit draws) without counting as income or taxable revenue.",
        )
        self._resource_list = ctk.CTkTextbox(res, height=100, font=ctk.CTkFont(family="Consolas", size=11))
        self._resource_list.grid(row=2, column=0, columnspan=8, sticky="ew", padx=4, pady=(8, 4))

        avail = ctk.CTkFrame(t)
        avail.pack(fill="x", pady=(2, 8))
        ctk.CTkLabel(avail, text="Available funds", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, columnspan=12, sticky="w", padx=4, pady=(4, 8)
        )
        ctk.CTkLabel(avail, text="Account").grid(row=1, column=0, padx=4, pady=4)
        self._af_name = ctk.CTkEntry(avail, width=170)
        self._af_name.grid(row=1, column=1, padx=4)
        ctk.CTkLabel(avail, text="Type").grid(row=1, column=2, padx=4, pady=4)
        self._af_type = ctk.CTkComboBox(
            avail,
            values=["cash", "bank", "reserve", "credit_card", "loan_line", "other"],
            width=130,
        )
        self._af_type.set("bank")
        self._af_type.grid(row=1, column=3, padx=4)
        ctk.CTkLabel(avail, text="Currency").grid(row=1, column=4, padx=4, pady=4)
        self._af_cur = ctk.CTkComboBox(avail, values=["USD", "EUR", "GBP", "CAD"], width=90)
        self._af_cur.set(settings_get(self.cfg, "currency_default", "USD"))
        self._af_cur.grid(row=1, column=5, padx=4)
        ctk.CTkLabel(avail, text="Current").grid(row=1, column=6, padx=4, pady=4)
        self._af_balance = ctk.CTkEntry(avail, width=100)
        self._af_balance.grid(row=1, column=7, padx=4)
        ctk.CTkLabel(avail, text="Credit limit").grid(row=1, column=8, padx=4, pady=4)
        self._af_limit = ctk.CTkEntry(avail, width=100)
        self._af_limit.grid(row=1, column=9, padx=4)
        ctk.CTkButton(avail, text="Save account", command=self._on_save_available_funds_account).grid(
            row=1, column=10, padx=6
        )
        hb_av = ctk.CTkLabel(avail, text="?", width=18, height=18, fg_color=self._theme_help_btn_bg, text_color=self._theme_help_btn_fg, corner_radius=9)
        hb_av.grid(row=1, column=11, padx=(2, 0), sticky="w")
        self._attach_tooltip(
            hb_av,
            "Track liquid balances and available credit lines. Credit cards/loan lines use Current as used amount and Credit limit for remaining availability.",
        )
        ctk.CTkLabel(avail, text="Notes").grid(row=2, column=0, padx=4, pady=4)
        self._af_notes = ctk.CTkEntry(avail, width=420)
        self._af_notes.grid(row=2, column=1, columnspan=6, padx=4, sticky="ew")
        ctk.CTkLabel(avail, text="Edit/Archive ID").grid(row=2, column=7, padx=4, pady=4)
        self._af_id = ctk.CTkEntry(avail, width=90)
        self._af_id.grid(row=2, column=8, padx=4)
        ctk.CTkButton(avail, text="Archive", width=88, command=self._on_archive_available_funds_account).grid(
            row=2, column=9, padx=6
        )
        self._available_funds_list = ctk.CTkTextbox(avail, height=110, font=ctk.CTkFont(family="Consolas", size=11))
        self._available_funds_list.grid(row=3, column=0, columnspan=12, sticky="ew", padx=4, pady=(8, 4))

        self._exp_list = ctk.CTkTextbox(t, height=300, font=ctk.CTkFont(family="Consolas", size=12))
        self._exp_list.pack(fill="both", expand=True, pady=8)
        self._refresh_expenses()

    def _on_add_income(self) -> None:
        raw = self._inc_amt.get().strip().replace(",", ".")
        try:
            major = float(raw)
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Invalid income amount")
            return
        cents = int(round(major * 100))
        desc = self._inc_desc.get().strip() or "Income"
        now = datetime.now().isoformat()
        insert_income(
            self.cfg,
            self.uid,
            now,
            cents,
            self._inc_cur.get(),
            desc,
        )
        self._inc_amt.delete(0, "end")
        self._inc_desc.delete(0, "end")
        self._refresh_expenses()
        self._refresh_dashboard()

    def _on_add_expense(self) -> None:
        raw = self._exp_amt.get().strip().replace(",", ".")
        try:
            major = float(raw)
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Invalid amount")
            return
        cents = int(round(major * 100))
        cur = self._exp_cur.get()
        desc = self._exp_desc.get().strip() or "Expense"
        funding = self._exp_funding.get().strip() or "cash"
        now = datetime.now()
        spent = now.replace(tzinfo=None).isoformat()
        insert_expense(
            self.cfg,
            self.uid,
            spent,
            cents,
            cur,
            desc,
            billable=1,
            funding_source=funding,
        )
        if funding in ("credit_card", "borrowed_funds") and bool(
            settings_get(self.cfg, "auto_create_debt_for_credit_expenses_enabled", True)
        ):
            try:
                insert_debt(
                    self.cfg,
                    self.uid,
                    datetime.now().replace(tzinfo=None).isoformat(),
                    cents,
                    cur,
                    f"Auto debt from expense: {desc}",
                    creditor="Credit Card" if funding == "credit_card" else "Lender",
                    debt_type="credit_card" if funding == "credit_card" else "loan",
                )
            except Exception:
                pass
        self._exp_amt.delete(0, "end")
        self._exp_desc.delete(0, "end")
        self._refresh_expenses()
        self._refresh_dashboard()

    def _on_add_resource(self) -> None:
        raw = self._res_amt.get().strip().replace(",", ".")
        try:
            major = float(raw)
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Invalid resource amount")
            return
        cents = int(round(major * 100))
        try:
            insert_resource_entry(
                self.cfg,
                self.uid,
                datetime.now().replace(tzinfo=None).isoformat(),
                cents,
                self._res_cur.get(),
                source_type=self._res_type.get().strip() or "loan_draw",
                description=self._res_desc.get().strip(),
            )
        except Exception as exc:
            messagebox.showerror("RootRecord Business Manager", str(exc))
            return
        self._res_amt.delete(0, "end")
        self._res_desc.delete(0, "end")
        self._refresh_expenses()

    def _on_save_available_funds_account(self) -> None:
        name = self._af_name.get().strip()
        if not name:
            messagebox.showerror("RootRecord Business Manager", "Account name is required.")
            return
        try:
            bal_major = float((self._af_balance.get().strip() or "0").replace(",", "."))
            lim_major = float((self._af_limit.get().strip() or "0").replace(",", "."))
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Current/Credit limit must be valid numbers.")
            return
        account_id_raw = self._af_id.get().strip()
        try:
            account_id = int(account_id_raw) if account_id_raw else None
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Edit/Archive ID must be a number.")
            return
        try:
            upsert_available_funds_account(
                self.cfg,
                self.uid,
                account_name=name,
                account_type=self._af_type.get().strip() or "cash",
                currency=self._af_cur.get().strip() or "USD",
                current_balance_cents=int(round(bal_major * 100)),
                credit_limit_cents=int(round(lim_major * 100)),
                notes=self._af_notes.get().strip(),
                account_id=account_id,
            )
        except Exception as exc:
            messagebox.showerror("RootRecord Business Manager", str(exc))
            return
        self._af_name.delete(0, "end")
        self._af_balance.delete(0, "end")
        self._af_limit.delete(0, "end")
        self._af_notes.delete(0, "end")
        self._af_id.delete(0, "end")
        self._refresh_expenses()

    def _on_archive_available_funds_account(self) -> None:
        raw = self._af_id.get().strip()
        try:
            aid = int(raw)
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter a valid account ID to archive.")
            return
        try:
            set_available_funds_account_archived(self.cfg, self.uid, aid, True)
        except Exception as exc:
            messagebox.showerror("RootRecord Business Manager", str(exc))
            return
        self._af_id.delete(0, "end")
        self._refresh_expenses()

    def _refresh_expenses(self) -> None:
        if bool(settings_get(self.cfg, "auto_post_scheduled_expenses_enabled", True)):
            try:
                posted = run_due_scheduled_expenses(self.cfg, self.uid)
                if posted > 0:
                    self._set_process_status(f"Auto-added {posted} scheduled expense(s).", auto_clear_ms=2000)
            except Exception:
                pass
        rows = list_expenses(self.cfg, self.uid, 90)
        inc = list_income(self.cfg, self.uid, 90)
        self._exp_list.delete("0.0", "end")
        self._exp_list.insert("end", "Recent income:\n")
        for r in inc:
            self._exp_list.insert(
                "end",
                f"{r['received_at_utc'][:16]}  +{_fmt_money(r['amount_cents'], r['currency'])}  {r['description']}\n",
            )
        self._exp_list.insert("end", "\nRecent expenses:\n")
        for r in rows:
            fs = str(r.get("funding_source") or "cash")
            self._exp_list.insert(
                "end",
                f"{r['spent_at_utc'][:16]}  {_fmt_money(r['amount_cents'], r['currency'])}  [{fs}]  {r['description']}\n",
            )
        if getattr(self, "_resource_list", None):
            self._resource_list.delete("0.0", "end")
            self._resource_list.insert("end", "Recent resource entries (non-income):\n")
            for rr in list_resource_entries(self.cfg, self.uid, 365):
                self._resource_list.insert(
                    "end",
                    f"{str(rr.get('at_utc') or '')[:16]}  {_fmt_money(int(rr.get('amount_cents') or 0), str(rr.get('currency') or 'USD'))}  [{str(rr.get('source_type') or '')}]  {str(rr.get('description') or '')}\n",
                )
        if getattr(self, "_available_funds_list", None):
            self._available_funds_list.delete("0.0", "end")
            totals = available_funds_totals_by_currency(self.cfg, self.uid)
            self._available_funds_list.insert("end", "Available funds by currency:\n")
            for cur, agg in sorted(totals.items()):
                self._available_funds_list.insert(
                    "end",
                    (
                        f"{cur}  available={_fmt_money(int(agg.get('available_cents') or 0), cur)}  "
                        f"used_credit={_fmt_money(int(agg.get('used_credit_cents') or 0), cur)}  "
                        f"limit={_fmt_money(int(agg.get('credit_limit_cents') or 0), cur)}\n"
                    ),
                )
            self._available_funds_list.insert("end", "\nAccounts:\n")
            for a in list_available_funds_accounts(self.cfg, self.uid, include_archived=False):
                aid = int(a.get("id") or 0)
                nm = str(a.get("account_name") or "")
                typ = str(a.get("account_type") or "cash")
                cur = str(a.get("currency") or "USD")
                bal = int(a.get("current_balance_cents") or 0)
                lim = int(a.get("credit_limit_cents") or 0)
                if typ in ("credit_card", "loan_line", "line_of_credit"):
                    avail_c = max(0, lim - max(0, bal))
                else:
                    avail_c = bal
                self._available_funds_list.insert(
                    "end",
                    f"#{aid}  [{typ}]  {nm}  current={_fmt_money(bal, cur)}  available={_fmt_money(avail_c, cur)}  limit={_fmt_money(lim, cur)}\n",
                )
        if getattr(self, "_sched_list", None):
            self._sched_list.delete("0.0", "end")
            self._sched_list.insert("end", "Active schedules:\n")
            for s in list_scheduled_expenses(self.cfg, self.uid, include_inactive=True):
                state = "active" if int(s.get("active") or 0) == 1 else "paused"
                self._sched_list.insert(
                    "end",
                    f"#{int(s.get('id') or 0)}  {str(s.get('next_due_utc') or '')[:16]}  [{s.get('frequency')}]  {_fmt_money(int(s.get('amount_cents') or 0), str(s.get('currency') or 'USD'))}  {str(s.get('description') or '')}  ({state})\n",
                )

    def _on_add_scheduled_expense(self) -> None:
        raw = self._sch_exp_amt.get().strip().replace(",", ".")
        try:
            major = float(raw)
            cents = int(round(major * 100))
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Invalid scheduled amount")
            return
        desc = self._sch_exp_desc.get().strip() or "Scheduled expense"
        due_raw = self._sch_exp_due.get().strip()
        try:
            due_dt = datetime.fromisoformat(due_raw[:10])
            due_iso = due_dt.replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Use date format YYYY-MM-DD for first due date")
            return
        try:
            add_scheduled_expense(
                self.cfg,
                self.uid,
                description=desc,
                amount_cents=cents,
                currency=self._sch_exp_cur.get(),
                frequency=self._sch_exp_freq.get(),
                next_due_utc=due_iso,
                merchant=self._sch_exp_merchant.get().strip(),
            )
        except Exception as exc:
            messagebox.showerror("RootRecord Business Manager", str(exc))
            return
        self._sch_exp_amt.delete(0, "end")
        self._sch_exp_desc.delete(0, "end")
        self._sch_exp_merchant.delete(0, "end")
        self._refresh_expenses()

    def _on_pause_schedule(self) -> None:
        raw = self._sched_action_id.get().strip()
        try:
            sid = int(raw)
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter a valid Schedule ID.")
            return
        set_scheduled_expense_active(self.cfg, self.uid, sid, False)
        self._refresh_expenses()

    def _on_resume_schedule(self) -> None:
        raw = self._sched_action_id.get().strip()
        try:
            sid = int(raw)
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter a valid Schedule ID.")
            return
        set_scheduled_expense_active(self.cfg, self.uid, sid, True)
        self._refresh_expenses()

    def _on_run_due_schedules_now(self) -> None:
        try:
            posted = run_due_scheduled_expenses(self.cfg, self.uid)
        except Exception as exc:
            messagebox.showerror("RootRecord Business Manager", str(exc))
            return
        self._refresh_expenses()
        self._set_process_status(f"Auto-added {posted} scheduled expense(s).", auto_clear_ms=1800)

    def _build_debts(self) -> None:
        if getattr(self, "_finance_clients_sections", None):
            t = self._finance_clients_sections["Debts"]
        else:
            t = self.tabview.tab("Debts")
        ctk.CTkLabel(t, text="Debt tracking", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", pady=(0, 8))
        form = ctk.CTkFrame(t)
        form.pack(fill="x", pady=8)
        ctk.CTkLabel(form, text="Amount").grid(row=0, column=0, padx=4, pady=4)
        self._debt_amt = ctk.CTkEntry(form, width=120)
        self._debt_amt.grid(row=0, column=1, padx=4)
        self._debt_cur = ctk.CTkComboBox(form, values=["USD", "EUR", "GBP", "CAD"], width=100)
        self._debt_cur.set(settings_get(self.cfg, "currency_default", "USD"))
        self._debt_cur.grid(row=0, column=2, padx=4)
        ctk.CTkLabel(form, text="Type").grid(row=0, column=3, padx=4)
        self._debt_type = ctk.CTkComboBox(
            form,
            values=["loan", "credit_card", "mortgage", "line_of_credit", "other"],
            width=140,
        )
        self._debt_type.set("loan")
        self._debt_type.grid(row=0, column=4, padx=4)
        ctk.CTkLabel(form, text="Creditor").grid(row=0, column=5, padx=4)
        self._debt_creditor = ctk.CTkEntry(form, width=180)
        self._debt_creditor.grid(row=0, column=6, padx=4)
        ctk.CTkLabel(form, text="Account").grid(row=1, column=3, padx=4)
        self._debt_account_ref = ctk.CTkEntry(form, width=180)
        self._debt_account_ref.grid(row=1, column=4, padx=4)
        ctk.CTkLabel(form, text="Description").grid(row=1, column=0, padx=4)
        self._debt_desc = ctk.CTkEntry(form, width=540)
        self._debt_desc.grid(row=1, column=1, columnspan=2, sticky="ew", padx=4)
        ctk.CTkButton(form, text="Add debt", command=self._on_add_debt).grid(row=2, column=1, pady=8)
        hb_debt = ctk.CTkLabel(form, text="?", width=18, height=18, fg_color=self._theme_help_btn_bg, text_color=self._theme_help_btn_fg, corner_radius=9)
        hb_debt.grid(row=2, column=2, padx=(4, 0), sticky="w")
        self._attach_tooltip(
            hb_debt,
            "Adding debt tracks liability only (no income impact). Closing debt posts payment to Expenses, which reduces net profit.",
        )
        ctk.CTkLabel(form, text="Debt ID").grid(row=2, column=3, padx=4)
        self._debt_action_id = ctk.CTkEntry(form, width=100)
        self._debt_action_id.grid(row=2, column=4, padx=4)
        ctk.CTkButton(form, text="Close debt", command=self._on_close_debt).grid(row=2, column=5, padx=6)
        self._debt_list = ctk.CTkTextbox(t, height=320, font=ctk.CTkFont(family="Consolas", size=12))
        self._debt_list.pack(fill="both", expand=True, pady=8)
        self._refresh_debts()

    def _on_add_debt(self) -> None:
        raw = self._debt_amt.get().strip().replace(",", ".")
        try:
            major = float(raw)
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Invalid debt amount")
            return
        cents = int(round(major * 100))
        try:
            insert_debt(
                self.cfg,
                self.uid,
                datetime.now().replace(tzinfo=None).isoformat(),
                cents,
                self._debt_cur.get(),
                self._debt_desc.get().strip() or "Debt",
                creditor=self._debt_creditor.get().strip(),
                debt_type=self._debt_type.get().strip() or "loan",
                account_ref=self._debt_account_ref.get().strip(),
            )
        except Exception as exc:
            messagebox.showerror("RootRecord Business Manager", str(exc))
            return
        self._debt_amt.delete(0, "end")
        self._debt_desc.delete(0, "end")
        self._debt_creditor.delete(0, "end")
        self._debt_account_ref.delete(0, "end")
        self._refresh_debts()

    def _refresh_debts(self) -> None:
        if not getattr(self, "_debt_list", None):
            return
        rows = list_debts(self.cfg, self.uid, 365)
        self._debt_list.delete("0.0", "end")
        self._debt_list.insert("end", "Recent debts:\n")
        for r in rows:
            cred = str(r.get("creditor") or "").strip()
            who = f" ({cred})" if cred else ""
            dtyp = str(r.get("debt_type") or "loan").replace("_", " ").title()
            self._debt_list.insert(
                "end",
                f"#{int(r.get('id') or 0)}  {str(r.get('created_at_utc') or '')[:16]}  [{dtyp}]  {_fmt_money(int(r.get('amount_cents') or 0), str(r.get('currency') or 'USD'))}  {str(r.get('description') or '')}{who}  ({str(r.get('status') or 'open')})\n",
            )

    def _on_close_debt(self) -> None:
        raw = self._debt_action_id.get().strip()
        try:
            did = int(raw)
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter a valid Debt ID to close.")
            return
        try:
            exp_id = settle_debt_with_expense(self.cfg, self.uid, did)
        except Exception as exc:
            messagebox.showerror("RootRecord Business Manager", str(exc))
            return
        self._refresh_debts()
        self._refresh_expenses()
        self._refresh_dashboard()
        if bool(settings_get(self.cfg, "notify_on_debt_settlement_enabled", True)):
            messagebox.showinfo(
                "RootRecord Business Manager",
                f"Debt closed and payment posted to expenses.\nExpense entry ID: {exp_id}",
            )

    def _build_clients(self) -> None:
        if getattr(self, "_finance_clients_sections", None):
            t = self._finance_clients_sections["Clients"]
        else:
            t = self.tabview.tab("Clients")
        ctk.CTkLabel(
            t,
            text="Clients & contacts",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", pady=(0, 6))
        ctk.CTkLabel(
            t,
            text="Track billing contacts; link them to invoices and scheduled meetings.",
            text_color=self._theme_text_muted,
        ).pack(anchor="w", pady=(0, 8))
        form = ctk.CTkFrame(t)
        form.pack(fill="x", pady=6)
        self._cl_id = ctk.CTkEntry(form, width=72, placeholder_text="ID (edit)")
        self._cl_id.grid(row=0, column=1, padx=4, pady=4)
        ctk.CTkLabel(form, text="Record ID").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self._cl_name = ctk.CTkEntry(form, width=320, placeholder_text="Display name *")
        self._cl_name.grid(row=1, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(form, text="Name").grid(row=1, column=0, padx=4, sticky="e")
        self._cl_company = ctk.CTkEntry(form, width=320, placeholder_text="Company")
        self._cl_company.grid(row=2, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(form, text="Company").grid(row=2, column=0, padx=4, sticky="e")
        self._cl_email = ctk.CTkEntry(form, width=220, placeholder_text="Email")
        self._cl_email.grid(row=3, column=1, padx=4, pady=4, sticky="w")
        self._cl_phone = ctk.CTkEntry(form, width=180, placeholder_text="Phone")
        self._cl_phone.grid(row=3, column=2, padx=4, pady=4, sticky="w")
        ctk.CTkLabel(form, text="Email / Phone").grid(row=3, column=0, padx=4, sticky="e")
        self._cl_web = ctk.CTkEntry(form, width=420, placeholder_text="Website")
        self._cl_web.grid(row=4, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(form, text="Website").grid(row=4, column=0, padx=4, sticky="e")
        self._cl_tax = ctk.CTkEntry(form, width=220, placeholder_text="Tax ID / VAT")
        self._cl_tax.grid(row=5, column=1, columnspan=2, sticky="w", padx=4, pady=4)
        ctk.CTkLabel(form, text="Tax ID").grid(row=5, column=0, padx=4, sticky="e")
        hb_client_tax = ctk.CTkLabel(form, text="?", width=18, height=18, fg_color=self._theme_help_btn_bg, text_color=self._theme_help_btn_fg, corner_radius=9)
        hb_client_tax.grid(row=5, column=3, padx=(2, 0), pady=4, sticky="w")
        self._attach_tooltip(
            hb_client_tax,
            "Client tax/VAT ID is used in invoice headers and PDFs; it does not change ledger totals.",
        )
        self._cl_addr = ctk.CTkTextbox(form, width=420, height=52)
        self._cl_addr.grid(row=6, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(form, text="Address").grid(row=6, column=0, padx=4, sticky="ne")
        self._cl_notes = ctk.CTkTextbox(form, width=420, height=52)
        self._cl_notes.grid(row=7, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(form, text="Notes").grid(row=7, column=0, padx=4, sticky="ne")
        rowb = ctk.CTkFrame(t, fg_color="transparent")
        rowb.pack(fill="x", pady=6)
        ctk.CTkButton(rowb, text="Save client", command=self._on_save_client).pack(side="left", padx=4)
        ctk.CTkButton(rowb, text="Load by ID", command=self._on_load_client).pack(side="left", padx=4)
        ctk.CTkButton(rowb, text="Clear form", command=self._on_clear_client_form).pack(side="left", padx=4)
        ctk.CTkButton(rowb, text="Archive by ID", command=self._on_archive_client).pack(side="left", padx=4)
        self._add_help_bubble(
            rowb,
            "Clients link to invoices and schedule entries. Archive hides a client from active lists without deleting invoice history.",
        )
        self._cl_list = ctk.CTkTextbox(t, height=280, font=ctk.CTkFont(family="Consolas", size=12))
        self._cl_list.pack(fill="both", expand=True, pady=8)

    def _refresh_clients_list(self) -> None:
        rows = list_clients(self.cfg, self.uid)
        self._cl_list.delete("0.0", "end")
        for r in rows:
            self._cl_list.insert(
                "end",
                f"id {r['id']:>4}  {r['display_name'][:40]:<40}  {str(r.get('company') or '')[:28]}\n",
            )

    def _on_clear_client_form(self) -> None:
        for w in (
            self._cl_id,
            self._cl_name,
            self._cl_company,
            self._cl_email,
            self._cl_phone,
            self._cl_web,
            self._cl_tax,
        ):
            w.delete(0, "end")
        self._cl_addr.delete("0.0", "end")
        self._cl_notes.delete("0.0", "end")

    def _on_load_client(self) -> None:
        try:
            cid = int(self._cl_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter a numeric client ID.")
            return
        r = get_client(self.cfg, self.uid, cid)
        if not r:
            messagebox.showerror("RootRecord Business Manager", "Client not found.")
            return
        self._on_clear_client_form()
        self._cl_id.insert(0, str(r["id"]))
        self._cl_name.insert(0, str(r.get("display_name") or ""))
        self._cl_company.insert(0, str(r.get("company") or ""))
        self._cl_email.insert(0, str(r.get("email") or ""))
        self._cl_phone.insert(0, str(r.get("phone") or ""))
        self._cl_web.insert(0, str(r.get("website") or ""))
        self._cl_tax.insert(0, str(r.get("tax_id") or ""))
        self._cl_addr.insert("0.0", str(r.get("address") or ""))
        self._cl_notes.insert("0.0", str(r.get("notes") or ""))

    def _on_save_client(self) -> None:
        name = self._cl_name.get().strip()
        if not name:
            messagebox.showerror("RootRecord Business Manager", "Display name is required.")
            return
        try:
            cid = int(self._cl_id.get().strip()) if self._cl_id.get().strip() else None
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "ID must be numeric.")
            return
        try:
            save_client(
                self.cfg,
                self.uid,
                client_id=cid,
                display_name=name,
                company=self._cl_company.get().strip(),
                email=self._cl_email.get().strip(),
                phone=self._cl_phone.get().strip(),
                address=self._cl_addr.get("0.0", "end").strip(),
                website=self._cl_web.get().strip(),
                tax_id=self._cl_tax.get().strip(),
                notes=self._cl_notes.get("0.0", "end").strip(),
            )
        except ValueError as e:
            messagebox.showerror("RootRecord Business Manager", str(e))
            return
        self._refresh_clients_list()
        messagebox.showinfo("RootRecord Business Manager", "Client saved.")

    def _on_archive_client(self) -> None:
        try:
            cid = int(self._cl_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter client ID to archive.")
            return
        archive_client(self.cfg, self.uid, cid)
        self._refresh_clients_list()
        messagebox.showinfo("RootRecord Business Manager", "Client archived.")

    def _build_invoices(self) -> None:
        if getattr(self, "_finance_clients_sections", None):
            t = self._finance_clients_sections["Invoices"]
        else:
            t = self.tabview.tab("Invoices")
        ctk.CTkLabel(t, text="Invoicing", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", pady=(0, 6))
        ctk.CTkLabel(
            t,
            text="Draft invoices, line items (description | qty | unit price), PDF for clients.",
            text_color=self._theme_text_muted,
        ).pack(anchor="w", pady=(0, 8))
        top = ctk.CTkFrame(t, fg_color="transparent")
        top.pack(fill="x", pady=4)
        ctk.CTkLabel(top, text="Invoice ID").pack(side="left", padx=(0, 6))
        self._inv_id = ctk.CTkEntry(top, width=80, placeholder_text="id")
        self._inv_id.pack(side="left", padx=4)
        ctk.CTkButton(top, text="Load", command=self._on_invoice_load).pack(side="left", padx=4)
        ctk.CTkButton(top, text="New draft", command=self._on_invoice_new).pack(side="left", padx=4)
        ctk.CTkButton(top, text="Delete", command=self._on_invoice_delete).pack(side="left", padx=4)
        ctk.CTkButton(top, text="Export PDF", command=self._on_invoice_pdf).pack(side="left", padx=4)
        self._add_help_bubble(
            top,
            "Invoices are revenue documents only. They do not affect Income totals until you explicitly record payment as Income.",
        )
        meta = ctk.CTkFrame(t)
        meta.pack(fill="x", pady=6)
        ctk.CTkLabel(meta, text="Client").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self._inv_client_cmb = ctk.CTkComboBox(meta, width=260, values=["—"])
        self._inv_client_cmb.grid(row=0, column=1, padx=4, pady=4, sticky="w")
        self._inv_client_map: dict[str, int | None] = {"—": None}
        ctk.CTkLabel(meta, text="Invoice #").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self._inv_num = ctk.CTkEntry(meta, width=140)
        self._inv_num.grid(row=0, column=3, padx=4, pady=4)
        ctk.CTkLabel(meta, text="Status").grid(row=1, column=0, padx=4, pady=4, sticky="e")
        self._inv_status = ctk.CTkComboBox(meta, values=["draft", "sent", "paid", "void"], width=120)
        self._inv_status.set("draft")
        self._inv_status.grid(row=1, column=1, padx=4, pady=4, sticky="w")
        ctk.CTkLabel(meta, text="Issued (local)").grid(row=1, column=2, padx=4, pady=4, sticky="e")
        self._inv_issued = ctk.CTkEntry(meta, width=160, placeholder_text="YYYY-MM-DD HH:MM")
        self._inv_issued.grid(row=1, column=3, padx=4, pady=4)
        ctk.CTkLabel(meta, text="Due (local)").grid(row=2, column=0, padx=4, pady=4, sticky="e")
        self._inv_due = ctk.CTkEntry(meta, width=160, placeholder_text="optional")
        self._inv_due.grid(row=2, column=1, padx=4, pady=4, sticky="w")
        ctk.CTkLabel(meta, text="Tax (major)").grid(row=2, column=2, padx=4, pady=4, sticky="e")
        self._inv_tax = ctk.CTkEntry(meta, width=100, placeholder_text="0")
        self._inv_tax.grid(row=2, column=3, padx=4, pady=4, sticky="w")
        hb_inv_tax = ctk.CTkLabel(meta, text="?", width=18, height=18, fg_color=self._theme_help_btn_bg, text_color=self._theme_help_btn_fg, corner_radius=9)
        hb_inv_tax.grid(row=2, column=4, padx=(2, 0), pady=4, sticky="w")
        self._attach_tooltip(
            hb_inv_tax,
            "Invoice tax updates invoice totals only. It does not post to expense/income ledgers by itself.",
        )
        ctk.CTkLabel(meta, text="Footer notes").grid(row=3, column=0, padx=4, pady=4, sticky="ne")
        self._inv_notes = ctk.CTkTextbox(meta, width=520, height=48)
        self._inv_notes.grid(row=3, column=1, columnspan=3, sticky="ew", padx=4, pady=4)
        ctk.CTkButton(t, text="Save header / tax", command=self._on_invoice_save_meta).pack(anchor="w", padx=4, pady=4)
        meta_help = ctk.CTkFrame(t, fg_color="transparent")
        meta_help.pack(anchor="w", padx=4, pady=(0, 2))
        self._add_help_bubble(
            meta_help,
            "Save header updates invoice metadata (client, number, dates, tax, notes).",
            side="left",
            padx=(0, 0),
        )
        ctk.CTkLabel(
            t,
            text="Lines: each row  description | qty | unit price (e.g. Consulting | 3 | 150.00)",
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=4)
        self._inv_lines = ctk.CTkTextbox(t, height=160, font=ctk.CTkFont(family="Consolas", size=12))
        self._inv_lines.pack(fill="x", padx=4, pady=4)
        ctk.CTkButton(t, text="Save line items", command=self._on_invoice_save_lines).pack(anchor="w", padx=4, pady=4)
        inv_lines_help = ctk.CTkFrame(t, fg_color="transparent")
        inv_lines_help.pack(anchor="w", padx=4, pady=(0, 2))
        self._add_help_bubble(
            inv_lines_help,
            "Line items recalculate invoice subtotal/total. This is separate from Income ledger posting.",
            side="left",
            padx=(0, 0),
        )
        self._inv_list = ctk.CTkTextbox(t, height=200, font=ctk.CTkFont(family="Consolas", size=12))
        self._inv_list.pack(fill="both", expand=True, pady=8)

    def _refresh_inv_client_combo(self) -> None:
        clients = list_clients(self.cfg, self.uid)
        self._inv_client_map = {"—": None}
        for c in clients:
            self._inv_client_map[c["display_name"][:48]] = int(c["id"])
        self._inv_client_cmb.configure(values=list(self._inv_client_map.keys()))
        if self._inv_client_cmb.get() not in self._inv_client_map:
            self._inv_client_cmb.set("—")

    def _refresh_invoices_list(self) -> None:
        self._refresh_inv_client_combo()
        rows = list_invoices(self.cfg, self.uid, 80)
        self._inv_list.delete("0.0", "end")
        for r in rows:
            cn = str(r.get("client_name") or "—")[:20]
            self._inv_list.insert(
                "end",
                f"id {r['id']:>4}  {r['invoice_number']:<14}  {r['status']:<6}  "
                f"{_fmt_money(int(r.get('total_cents') or 0), r.get('currency', 'USD'))}  {cn}\n",
            )

    def _on_invoice_new(self) -> None:
        self._refresh_inv_client_combo()
        cid = self._inv_client_map.get(self._inv_client_cmb.get())
        issued = datetime.now().strftime("%Y-%m-%d %H:%M")
        due = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d %H:%M")
        try:
            iu = local_input_to_utc_naive_iso(self.cfg, issued)
            du = local_input_to_utc_naive_iso(self.cfg, due)
        except ValueError as e:
            messagebox.showerror("RootRecord Business Manager", str(e))
            return
        try:
            tax_cents = int(round(float(self._inv_tax.get().strip() or "0") * 100))
        except ValueError:
            tax_cents = 0
        try:
            iid = create_invoice(
                self.cfg,
                self.uid,
                client_id=cid,
                invoice_number=None,
                issued_at_utc=iu,
                due_at_utc=du,
                currency=settings_get(self.cfg, "currency_default", "USD"),
                tax_cents=tax_cents,
                notes="",
                status="draft",
            )
        except ValueError as e:
            messagebox.showerror("RootRecord Business Manager", str(e))
            return
        self._inv_id.delete(0, "end")
        self._inv_id.insert(0, str(iid))
        self._on_invoice_load()
        self._refresh_invoices_list()
        messagebox.showinfo("RootRecord Business Manager", f"Created invoice id {iid}. Edit lines and save.")

    def _on_invoice_load(self) -> None:
        try:
            iid = int(self._inv_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter invoice ID.")
            return
        self._refresh_inv_client_combo()
        inv = get_invoice(self.cfg, self.uid, iid)
        if not inv:
            messagebox.showerror("RootRecord Business Manager", "Invoice not found.")
            return
        self._inv_num.delete(0, "end")
        self._inv_num.insert(0, str(inv.get("invoice_number") or ""))
        self._inv_status.set(str(inv.get("status") or "draft"))
        self._inv_issued.delete(0, "end")
        self._inv_issued.insert(0, schedule_local_labels(self.cfg, {"starts_at_utc": inv["issued_at_utc"]})["start_local"])
        self._inv_due.delete(0, "end")
        if inv.get("due_at_utc"):
            self._inv_due.insert(0, schedule_local_labels(self.cfg, {"starts_at_utc": inv["due_at_utc"]})["start_local"])
        self._inv_tax.delete(0, "end")
        self._inv_tax.insert(0, f"{int(inv.get('tax_cents') or 0) / 100:.2f}")
        self._inv_notes.delete("0.0", "end")
        self._inv_notes.insert("0.0", str(inv.get("notes") or ""))
        cname = str(inv.get("client_name") or "")
        for label, xid in self._inv_client_map.items():
            if xid == inv.get("client_id"):
                self._inv_client_cmb.set(label)
                break
        else:
            self._inv_client_cmb.set("—")
        lines = list_invoice_lines(self.cfg, self.uid, iid)
        self._inv_lines.delete("0.0", "end")
        for ln in lines:
            u = int(ln.get("unit_price_cents") or 0) / 100.0
            self._inv_lines.insert(
                "end",
                f"{ln.get('description', '')} | {ln.get('quantity', 1)} | {u:.2f}\n",
            )
        self._refresh_invoices_list()

    def _on_invoice_save_meta(self) -> None:
        try:
            iid = int(self._inv_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Load an invoice first.")
            return
        try:
            iu = local_input_to_utc_naive_iso(self.cfg, self._inv_issued.get().strip())
        except ValueError as e:
            messagebox.showerror("RootRecord Business Manager", f"Issued date: {e}")
            return
        due_raw = self._inv_due.get().strip()
        try:
            du = local_input_to_utc_naive_iso(self.cfg, due_raw) if due_raw else None
        except ValueError as e:
            messagebox.showerror("RootRecord Business Manager", f"Due date: {e}")
            return
        try:
            tax_cents = int(round(float(self._inv_tax.get().strip() or "0") * 100))
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Invalid tax amount.")
            return
        cid = self._inv_client_map.get(self._inv_client_cmb.get())
        inv_num = self._inv_num.get().strip()
        try:
            update_invoice_meta(
                self.cfg,
                self.uid,
                iid,
                client_id=cid,
                invoice_number=inv_num if inv_num else None,
                status=self._inv_status.get().strip(),
                issued_at_utc=iu,
                due_at_utc=du,
                tax_cents=tax_cents,
                notes=self._inv_notes.get("0.0", "end").strip(),
            )
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Could not update invoice.")
            return
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("RootRecord Business Manager", str(e))
            return
        self._refresh_invoices_list()
        messagebox.showinfo("RootRecord Business Manager", "Invoice header saved.")

    def _on_invoice_save_lines(self) -> None:
        try:
            iid = int(self._inv_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Load an invoice first.")
            return
        parsed = parse_invoice_lines_text(self._inv_lines.get("0.0", "end"))
        if not parsed:
            messagebox.showerror("RootRecord Business Manager", "Add at least one line: desc | qty | price")
            return
        replace_invoice_lines(self.cfg, self.uid, iid, parsed)
        self._on_invoice_load()
        messagebox.showinfo("RootRecord Business Manager", "Line items saved.")

    def _on_invoice_delete(self) -> None:
        try:
            iid = int(self._inv_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter invoice ID.")
            return
        if not messagebox.askyesno("RootRecord Business Manager", "Delete this invoice permanently?"):
            return
        delete_invoice(self.cfg, self.uid, iid)
        self._refresh_invoices_list()

    def _on_invoice_pdf(self) -> None:
        try:
            iid = int(self._inv_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter invoice ID.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile=f"invoice_{iid}.pdf",
        )
        if not path:
            return
        try:
            write_invoice_pdf(self.cfg, self.uid, iid, path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("RootRecord Business Manager", str(e))
            return
        messagebox.showinfo("RootRecord Business Manager", f"Saved:\n{path}")

    def _build_schedule(self) -> None:
        t = self.tabview.tab("Schedule & Bookings")
        ctk.CTkLabel(t, text="Schedule & Bookings", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", pady=(0, 6))
        ctk.CTkLabel(
            t,
            text="Meetings and deadlines (times in your Business timezone / system local).",
            text_color=self._theme_text_muted,
        ).pack(anchor="w", pady=(0, 8))
        form = ctk.CTkFrame(t)
        form.pack(fill="x", pady=6)
        ctk.CTkLabel(form, text="Event ID").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self._sch_id = ctk.CTkEntry(form, width=72, placeholder_text="edit")
        self._sch_id.grid(row=0, column=1, padx=4, pady=4, sticky="w")
        self._sch_title = ctk.CTkEntry(form, width=400, placeholder_text="Title *")
        self._sch_title.grid(row=1, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(form, text="Title").grid(row=1, column=0, padx=4, sticky="e")
        self._sch_start = ctk.CTkEntry(form, width=180, placeholder_text="Start YYYY-MM-DD HH:MM")
        self._sch_start.grid(row=2, column=1, padx=4, pady=4, sticky="w")
        self._sch_end = ctk.CTkEntry(form, width=180, placeholder_text="End (optional)")
        self._sch_end.grid(row=2, column=2, padx=4, pady=4, sticky="w")
        ctk.CTkLabel(form, text="Start / End").grid(row=2, column=0, padx=4, sticky="e")
        hb_sch_time = ctk.CTkLabel(form, text="?", width=18, height=18, fg_color=self._theme_help_btn_bg, text_color=self._theme_help_btn_fg, corner_radius=9)
        hb_sch_time.grid(row=2, column=4, padx=(4, 0), sticky="w")
        self._attach_tooltip(hb_sch_time, "Use local time format YYYY-MM-DD HH:MM. End is optional.")
        ctk.CTkLabel(form, text="Client").grid(row=3, column=0, padx=4, pady=4, sticky="e")
        self._sch_client_cmb = ctk.CTkComboBox(form, width=260, values=["—"])
        self._sch_client_cmb.grid(row=3, column=1, padx=4, pady=4, sticky="w")
        self._sch_client_map: dict[str, int | None] = {"—": None}
        ctk.CTkLabel(form, text="Project").grid(row=3, column=2, padx=4, pady=4, sticky="e")
        self._sch_proj_cmb = ctk.CTkComboBox(form, width=200, values=["—"])
        self._sch_proj_cmb.grid(row=3, column=3, padx=4, pady=4, sticky="w")
        self._sch_proj_map: dict[str, int | None] = {"—": None}
        self._sch_loc = ctk.CTkEntry(form, width=400, placeholder_text="Location / link")
        self._sch_loc.grid(row=4, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(form, text="Location").grid(row=4, column=0, padx=4, sticky="e")
        self._sch_stat = ctk.CTkComboBox(form, values=["scheduled", "done", "cancelled"], width=140)
        self._sch_stat.set("scheduled")
        self._sch_stat.grid(row=5, column=1, padx=4, pady=4, sticky="w")
        ctk.CTkLabel(form, text="Status").grid(row=5, column=0, padx=4, sticky="e")
        self._sch_notes = ctk.CTkTextbox(form, width=520, height=56)
        self._sch_notes.grid(row=6, column=1, columnspan=3, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(form, text="Notes").grid(row=6, column=0, padx=4, sticky="ne")
        br = ctk.CTkFrame(t, fg_color="transparent")
        br.pack(fill="x", pady=6)
        ctk.CTkButton(br, text="Save event", command=self._on_schedule_save).pack(side="left", padx=4)
        ctk.CTkButton(br, text="Load by ID", command=self._on_schedule_load).pack(side="left", padx=4)
        ctk.CTkButton(br, text="Clear", command=self._on_schedule_clear).pack(side="left", padx=4)
        ctk.CTkButton(br, text="Delete", command=self._on_schedule_delete).pack(side="left", padx=4)
        self._add_help_bubble(br, "Save creates/updates event. Load by ID pulls a listed event. Delete removes it.")
        today = date.today()
        self._sch_range_start = tk.StringVar(value=today.isoformat())
        self._sch_range_end = tk.StringVar(value=(today + timedelta(days=30)).isoformat())
        pr = ctk.CTkFrame(t, fg_color="transparent")
        pr.pack(fill="x", pady=(2, 6))
        ctk.CTkLabel(pr, text="Print range").pack(side="left", padx=(4, 4))
        ctk.CTkEntry(pr, width=120, textvariable=self._sch_range_start).pack(side="left", padx=4)
        ctk.CTkLabel(pr, text="to").pack(side="left", padx=2)
        ctk.CTkEntry(pr, width=120, textvariable=self._sch_range_end).pack(side="left", padx=4)
        ctk.CTkButton(pr, text="Print Schedule", command=self._print_schedule_pdf).pack(side="left", padx=10)
        self._sch_list = ctk.CTkTextbox(t, height=320, font=ctk.CTkFont(family="Consolas", size=12))
        self._sch_list.pack(fill="both", expand=True, pady=8)

    def _refresh_schedule_combos(self) -> None:
        clients = list_clients(self.cfg, self.uid)
        self._sch_client_map = {"—": None}
        for c in clients:
            self._sch_client_map[c["display_name"][:48]] = int(c["id"])
        self._sch_client_cmb.configure(values=list(self._sch_client_map.keys()))
        prows = list_projects(self.cfg, self.uid)
        self._sch_proj_map = {"—": None}
        for p in prows:
            self._sch_proj_map[p["name"][:40]] = int(p["id"])
        self._sch_proj_cmb.configure(values=list(self._sch_proj_map.keys()))

    def _refresh_schedule_list(self) -> None:
        self._refresh_schedule_combos()
        rows = list_schedule_events_upcoming(self.cfg, self.uid, 30)
        self._sch_list.delete("0.0", "end")
        for r in rows:
            lb = schedule_local_labels(self.cfg, r)
            elb = lb["end_local"] or "—"
            cn = str(r.get("client_name") or "")[:16]
            self._sch_list.insert(
                "end",
                f"id {r['id']:<4}  {lb['start_local']} – {elb}  {str(r.get('status', '')):<10}  {r['title'][:40]}  {cn}\n",
            )

    def _on_schedule_clear(self) -> None:
        self._sch_id.delete(0, "end")
        self._sch_title.delete(0, "end")
        self._sch_start.delete(0, "end")
        self._sch_end.delete(0, "end")
        self._sch_loc.delete(0, "end")
        self._sch_notes.delete("0.0", "end")
        self._sch_stat.set("scheduled")

    def _on_schedule_load(self) -> None:
        try:
            eid = int(self._sch_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter event ID from list.")
            return
        self._refresh_schedule_combos()
        row = get_schedule_event(self.cfg, self.uid, eid)
        if not row:
            messagebox.showerror("RootRecord Business Manager", "Event not found.")
            return
        self._on_schedule_clear()
        self._sch_id.insert(0, str(row["id"]))
        self._sch_title.insert(0, str(row.get("title") or ""))
        fake = {"starts_at_utc": row["starts_at_utc"]}
        self._sch_start.insert(0, schedule_local_labels(self.cfg, fake)["start_local"])
        if row.get("ends_at_utc"):
            self._sch_end.insert(0, schedule_local_labels(self.cfg, {"starts_at_utc": row["ends_at_utc"]})["start_local"])
        self._sch_loc.insert(0, str(row.get("location") or ""))
        self._sch_notes.insert("0.0", str(row.get("notes") or ""))
        self._sch_stat.set(str(row.get("status") or "scheduled"))
        cid, pid = row.get("client_id"), row.get("project_id")
        for lab, x in self._sch_client_map.items():
            if x == cid:
                self._sch_client_cmb.set(lab)
                break
        for lab, x in self._sch_proj_map.items():
            if x == pid:
                self._sch_proj_cmb.set(lab)
                break

    def _on_schedule_save(self) -> None:
        title = self._sch_title.get().strip()
        if not title:
            messagebox.showerror("RootRecord Business Manager", "Title required.")
            return
        try:
            su = local_input_to_utc_naive_iso(self.cfg, self._sch_start.get().strip())
        except ValueError as e:
            messagebox.showerror("RootRecord Business Manager", str(e))
            return
        er = self._sch_end.get().strip()
        try:
            eu = local_input_to_utc_naive_iso(self.cfg, er) if er else None
        except ValueError as e:
            messagebox.showerror("RootRecord Business Manager", str(e))
            return
        try:
            eid = int(self._sch_id.get().strip()) if self._sch_id.get().strip() else None
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Event ID must be numeric.")
            return
        cid = self._sch_client_map.get(self._sch_client_cmb.get())
        pid = self._sch_proj_map.get(self._sch_proj_cmb.get())
        try:
            save_schedule_event(
                self.cfg,
                self.uid,
                event_id=eid,
                title=title,
                starts_at_utc=su,
                ends_at_utc=eu,
                client_id=cid,
                project_id=pid,
                location=self._sch_loc.get().strip(),
                notes=self._sch_notes.get("0.0", "end").strip(),
                status=self._sch_stat.get().strip(),
            )
        except ValueError as e:
            messagebox.showerror("RootRecord Business Manager", str(e))
            return
        self._refresh_schedule_list()
        messagebox.showinfo("RootRecord Business Manager", "Event saved.")

    def _on_schedule_delete(self) -> None:
        try:
            eid = int(self._sch_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter event ID.")
            return
        if not messagebox.askyesno("RootRecord Business Manager", "Delete this event?"):
            return
        delete_schedule_event(self.cfg, self.uid, eid)
        self._refresh_schedule_list()

    def _print_schedule_pdf(self) -> None:
        try:
            s = date.fromisoformat(self._sch_range_start.get().strip())
            e = date.fromisoformat(self._sch_range_end.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Use YYYY-MM-DD for schedule print range.")
            return
        if e <= s:
            messagebox.showerror("RootRecord Business Manager", "Print range end must be after start.")
            return
        u0, u1 = utc_naive_bounds_for_local_report_range(self.cfg, s, e)
        rows = list_schedule_events(self.cfg, self.uid, start_utc=u0, end_utc=u1)
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile=f"schedule_{s.isoformat()}_{(e - timedelta(days=1)).isoformat()}.pdf",
        )
        if not path:
            return
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas
        except Exception:
            messagebox.showerror("RootRecord Business Manager", "PDF export requires reportlab. Install dependencies and retry.")
            return

        c = canvas.Canvas(path, pagesize=letter)
        width, height = letter
        left, right = 46, width - 46
        y = height - 46

        def line(txt: str, *, size: int = 10, bold: bool = False, step: int = 14) -> None:
            nonlocal y
            if y < 54:
                c.showPage()
                y = height - 46
            c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
            c.setFillGray(0.08)
            c.drawString(left, y, (txt or "")[:122])
            y -= step

        line("RootRecord Business Manager — Schedule", size=14, bold=True, step=20)
        c.setStrokeGray(0.70)
        c.line(left, y + 6, right, y + 6)
        y -= 4
        line(f"Range: {s.isoformat()} to {(e - timedelta(days=1)).isoformat()}", size=10, step=16)
        line(f"Events: {len(rows)}", size=10, step=16)
        line("", step=8)

        if not rows:
            line("No schedule events in this range.", size=10)
        else:
            line("Entries", bold=True, size=11, step=16)
            for r in rows:
                lb = schedule_local_labels(self.cfg, r)
                end_local = lb.get("end_local") or "—"
                status = str(r.get("status") or "scheduled")
                title = str(r.get("title") or "")
                client = str(r.get("client_name") or "—")
                project = str(r.get("project_name") or "—")
                location = str(r.get("location") or "—")
                line(f"{lb['start_local']} -> {end_local}   [{status}]")
                line(f"{title}", bold=True, step=13)
                line(f"Client: {client}   Project: {project}", step=13)
                line(f"Location: {location}", step=13)
                notes = str(r.get("notes") or "").strip()
                if notes:
                    line(f"Notes: {notes}", step=13)
                line("", step=8)

        c.save()
        messagebox.showinfo("RootRecord Business Manager", f"Saved PDF:\n{path}")

    def _build_reports(self) -> None:
        t = self.tabview.tab("Reports")
        ctk.CTkLabel(
            t,
            text="Search (reporting lives on Work Log: ranges, presets, PDF)",
        ).pack(anchor="w", pady=8)
        srow = ctk.CTkFrame(t, fg_color="transparent")
        srow.pack(fill="x", pady=6)
        self._search_txt = ctk.CTkEntry(srow, width=340, placeholder_text="Search time, income, expenses...")
        self._search_txt.pack(side="left", padx=4)
        ctk.CTkButton(srow, text="Search", command=self._run_search).pack(side="left", padx=4)
        self._add_help_bubble(
            srow,
            "Search scans recorded time, income, and expense entries. This is read-only and does not change totals.",
        )

        self._search_box = ctk.CTkTextbox(t, height=220, font=ctk.CTkFont(family="Consolas", size=12))
        self._search_box.pack(fill="both", expand=True, pady=8)

        ctk.CTkLabel(t, text="Open Work Log for report presets and exports.").pack(anchor="w", pady=8)

    def _build_tax_estimator(self) -> None:
        if getattr(self, "_finance_clients_sections", None):
            t = self._finance_clients_sections["Tax Estimator"]
        else:
            t = self.tabview.tab("Tax Estimator")
        ctk.CTkLabel(
            t,
            text="Tax Estimator",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", pady=(0, 6))
        ctk.CTkLabel(
            t,
            text=(
                "Estimate taxes from tracked Income and Expenses for a selected range. "
                "Rates are editable and saved in Settings."
            ),
            text_color=self._theme_text_muted,
            wraplength=820,
            justify="left",
        ).pack(anchor="w", pady=(0, 10))

        row = ctk.CTkFrame(t, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(row, text="Start (YYYY-MM-DD)").pack(side="left")
        self._tax_start = ctk.CTkEntry(row, width=120)
        self._tax_start.pack(side="left", padx=(6, 12))
        ctk.CTkLabel(row, text="End (YYYY-MM-DD)").pack(side="left")
        self._tax_end = ctk.CTkEntry(row, width=120)
        self._tax_end.pack(side="left", padx=(6, 12))
        self._add_help_bubble(
            row,
            "Date range is [start, end). Tax estimator uses Income - Expenses posted in that window.",
        )

        today = date.today()
        month_start = date(today.year, today.month, 1)
        next_month = date(today.year + (1 if today.month == 12 else 0), 1 if today.month == 12 else today.month + 1, 1)
        self._tax_start.insert(0, month_start.isoformat())
        self._tax_end.insert(0, next_month.isoformat())

        def _tax_preset(name: str) -> None:
            if name == "This Month":
                s, e = month_start, next_month
            elif name == "This Year":
                s, e = date(today.year, 1, 1), date(today.year + 1, 1, 1)
            else:
                s, e = date(today.year - 1, 1, 1), date(today.year, 1, 1)
            self._tax_start.delete(0, "end")
            self._tax_start.insert(0, s.isoformat())
            self._tax_end.delete(0, "end")
            self._tax_end.insert(0, e.isoformat())
            _run_tax_estimate()

        ctk.CTkButton(row, text="This Month", width=100, command=lambda: _tax_preset("This Month")).pack(side="left", padx=4)
        ctk.CTkButton(row, text="This Year", width=100, command=lambda: _tax_preset("This Year")).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Last Year", width=100, command=lambda: _tax_preset("Last Year")).pack(side="left", padx=4)

        rates = ctk.CTkFrame(t)
        rates.pack(fill="x", pady=(4, 8))
        ctk.CTkLabel(rates, text="Federal %").grid(row=0, column=0, padx=8, pady=8, sticky="e")
        self._tax_rate_fed = ctk.CTkEntry(rates, width=90)
        self._tax_rate_fed.insert(0, str(float(settings_get(self.cfg, "tax_rate_federal_pct", 12.0))))
        self._tax_rate_fed.grid(row=0, column=1, padx=8, pady=8, sticky="w")
        ctk.CTkLabel(rates, text="State %").grid(row=0, column=2, padx=8, pady=8, sticky="e")
        self._tax_rate_state = ctk.CTkEntry(rates, width=90)
        self._tax_rate_state.insert(0, str(float(settings_get(self.cfg, "tax_rate_state_pct", 4.0))))
        self._tax_rate_state.grid(row=0, column=3, padx=8, pady=8, sticky="w")
        ctk.CTkLabel(rates, text="Self-Employment %").grid(row=0, column=4, padx=8, pady=8, sticky="e")
        self._tax_rate_se = ctk.CTkEntry(rates, width=90)
        self._tax_rate_se.insert(0, str(float(settings_get(self.cfg, "tax_rate_self_employment_pct", 15.3))))
        self._tax_rate_se.grid(row=0, column=5, padx=8, pady=8, sticky="w")
        hb_rates = ctk.CTkLabel(rates, text="?", width=18, height=18, fg_color=self._theme_help_btn_bg, text_color=self._theme_help_btn_fg, corner_radius=9)
        hb_rates.grid(row=0, column=6, padx=(2, 0), pady=8, sticky="w")
        self._attach_tooltip(
            hb_rates,
            "Rates are additive percentages (Federal + State + SE) applied to taxable profit for estimate only.",
        )

        self._tax_result = ctk.CTkTextbox(t, height=220, font=ctk.CTkFont(family="Consolas", size=12))
        self._tax_result.pack(fill="both", expand=True, pady=(8, 8))

        def _run_tax_estimate() -> None:
            try:
                s_day = date.fromisoformat(self._tax_start.get().strip())
                e_day = date.fromisoformat(self._tax_end.get().strip())
            except ValueError:
                messagebox.showerror("RootRecord Business Manager", "Use YYYY-MM-DD for tax estimate dates.")
                return
            if e_day <= s_day:
                messagebox.showerror("RootRecord Business Manager", "End date must be after start date.")
                return
            try:
                r_fed = float(self._tax_rate_fed.get().strip() or "0")
                r_state = float(self._tax_rate_state.get().strip() or "0")
                r_se = float(self._tax_rate_se.get().strip() or "0")
            except ValueError:
                messagebox.showerror("RootRecord Business Manager", "Tax rates must be numeric percentages.")
                return
            s_utc, e_utc = utc_naive_bounds_for_local_report_range(self.cfg, s_day, e_day)
            currency_safe = bool(settings_get(self.cfg, "currency_safe_summaries_enabled", False))
            curr = str(settings_get(self.cfg, "currency_default", "USD")).upper()
            if currency_safe:
                cmap = money_totals_by_currency_between(self.cfg, self.uid, s_utc, e_utc)
                d = cmap.get(curr, {"income_cents": 0, "expense_cents": 0})
                income_cents = int(d.get("income_cents") or 0)
                expense_cents = int(d.get("expense_cents") or 0)
            else:
                sums = summary_between(self.cfg, self.uid, s_utc, e_utc)
                income_cents = int(sums.get("income_cents") or 0)
                expense_cents = int(sums.get("expense_cents") or 0)
            net_cents = income_cents - expense_cents
            taxable_cents = max(0, net_cents)
            total_rate = max(0.0, r_fed) + max(0.0, r_state) + max(0.0, r_se)
            estimate_cents = int(round(taxable_cents * (total_rate / 100.0)))
            days = max(1, (e_day - s_day).days)
            weekly_set_aside_cents = int(round(estimate_cents * (7.0 / float(days))))
            monthly_set_aside_cents = int(round(estimate_cents * (30.4375 / float(days))))

            self._tax_result.configure(state="normal")
            self._tax_result.delete("0.0", "end")
            mode_line = f"Mode: Currency-safe ({curr} default only)\n\n" if currency_safe else ""
            self._tax_result.insert(
                "end",
                (
                    f"Range: {s_day.isoformat()} to {e_day.isoformat()} (exclusive end)\n\n"
                    f"{mode_line}"
                    f"Income:           {_fmt_money(income_cents, curr)}\n"
                    f"Expenses:         {_fmt_money(expense_cents, curr)}\n"
                    f"Net profit:       {_fmt_money(net_cents, curr)}\n"
                    f"Taxable profit:   {_fmt_money(taxable_cents, curr)}\n\n"
                    f"Federal ({r_fed:.2f}%):           {_fmt_money(int(round(taxable_cents * (max(0.0, r_fed) / 100.0))), curr)}\n"
                    f"State ({r_state:.2f}%):             {_fmt_money(int(round(taxable_cents * (max(0.0, r_state) / 100.0))), curr)}\n"
                    f"Self-employment ({r_se:.2f}%):  {_fmt_money(int(round(taxable_cents * (max(0.0, r_se) / 100.0))), curr)}\n"
                    f"----------------------------------------------\n"
                    f"Estimated total tax ({total_rate:.2f}%): {_fmt_money(estimate_cents, curr)}\n\n"
                    f"Suggested set-aside per week:  {_fmt_money(weekly_set_aside_cents, curr)}\n"
                    f"Suggested set-aside per month: {_fmt_money(monthly_set_aside_cents, curr)}\n"
                ),
            )
            self._tax_result.configure(state="disabled")

        def _save_tax_defaults() -> None:
            try:
                settings_set(self.cfg, "tax_rate_federal_pct", float(self._tax_rate_fed.get().strip() or "0"))
                settings_set(self.cfg, "tax_rate_state_pct", float(self._tax_rate_state.get().strip() or "0"))
                settings_set(self.cfg, "tax_rate_self_employment_pct", float(self._tax_rate_se.get().strip() or "0"))
            except ValueError:
                messagebox.showerror("RootRecord Business Manager", "Tax rates must be numeric percentages.")
                return
            messagebox.showinfo("RootRecord Business Manager", "Tax defaults saved.")
            _run_tax_estimate()

        actions = ctk.CTkFrame(t, fg_color="transparent")
        actions.pack(fill="x", pady=(0, 4))
        ctk.CTkButton(actions, text="Estimate Taxes", command=_run_tax_estimate).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Save Tax Defaults", command=_save_tax_defaults).pack(side="left")
        self._add_help_bubble(
            actions,
            "Tax estimate uses Income minus Expenses in selected date range. Debts affect tax only after a settlement posts an expense.",
        )
        _run_tax_estimate()

    def _run_search(self) -> None:
        term = self._search_txt.get().strip()
        rows = search_records(self.cfg, self.uid, term, days=3650)
        self._search_box.delete("0.0", "end")
        if not rows:
            self._search_box.insert("end", "No results.\n")
            return
        for r in rows:
            prefix = str(r.get("kind", "")).upper().ljust(7)
            amount = int(r.get("amount_cents") or 0)
            cur = r.get("currency") or "USD"
            self._search_box.insert(
                "end",
                f"{prefix} {str(r.get('at_utc', ''))[:16]}  {r.get('description', '')[:80]}  {_fmt_money(amount, cur)}\n",
            )

    def _build_stock_and_supplies(self) -> None:
        t = self.tabview.tab("Stock & Supplies")
        ctk.CTkLabel(
            t,
            text="Stock & Supplies",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", pady=(0, 2))
        ctk.CTkLabel(
            t,
            text=(
                "Track what moves in and out of the business in one place. "
                "Use Products for finished goods you sell or ship. "
                "Use Supplies for everything else—office basics, raw materials that go into products, packaging, parts—"
                "and use Category to tell them apart (e.g. Office, Production, Packaging)."
            ),
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=12),
            wraplength=720,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        scroll = ctk.CTkScrollableFrame(t)
        scroll.pack(fill="both", expand=True)

        ctk.CTkLabel(
            scroll,
            text="Products (sellable inventory)",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", pady=(4, 2))
        ctk.CTkLabel(
            scroll,
            text="SKUs, on-hand counts, reorder hints, optional unit cost and price.",
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", pady=(0, 6))
        form = ctk.CTkFrame(scroll)
        form.pack(fill="x", pady=6)
        ctk.CTkLabel(form, text="ID").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self._stk_id = ctk.CTkEntry(form, width=72, placeholder_text="edit")
        self._stk_id.grid(row=0, column=1, padx=4, sticky="w")
        self._stk_name = ctk.CTkEntry(form, width=320, placeholder_text="Name *")
        self._stk_name.grid(row=1, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(form, text="Name").grid(row=1, column=0, padx=4, sticky="e")
        self._stk_sku = ctk.CTkEntry(form, width=160, placeholder_text="SKU / code")
        self._stk_sku.grid(row=2, column=1, padx=4, pady=4, sticky="w")
        self._stk_unit = ctk.CTkEntry(form, width=80, placeholder_text="ea")
        self._stk_unit.grid(row=2, column=2, padx=4, pady=4, sticky="w")
        ctk.CTkLabel(form, text="SKU / Unit").grid(row=2, column=0, padx=4, sticky="e")
        self._stk_qty = ctk.CTkEntry(form, width=100, placeholder_text="Qty on hand")
        self._stk_qty.grid(row=3, column=1, padx=4, pady=4, sticky="w")
        self._stk_reorder = ctk.CTkEntry(form, width=100, placeholder_text="Reorder at")
        self._stk_reorder.grid(row=3, column=2, padx=4, pady=4, sticky="w")
        ctk.CTkLabel(form, text="Qty / Reorder").grid(row=3, column=0, padx=4, sticky="e")
        self._stk_cost = ctk.CTkEntry(form, width=100, placeholder_text="Cost / unit")
        self._stk_cost.grid(row=4, column=1, padx=4, pady=4, sticky="w")
        self._stk_price = ctk.CTkEntry(form, width=100, placeholder_text="Price / unit")
        self._stk_price.grid(row=4, column=2, padx=4, pady=4, sticky="w")
        ctk.CTkLabel(form, text="Cost / Price").grid(row=4, column=0, padx=4, sticky="e")
        ctk.CTkLabel(form, text="Currency").grid(row=5, column=0, padx=4, sticky="e")
        self._stk_cur = ctk.CTkComboBox(form, values=["USD", "EUR", "GBP", "CAD"], width=100)
        self._stk_cur.set(settings_get(self.cfg, "currency_default", "USD"))
        self._stk_cur.grid(row=5, column=1, padx=4, pady=4, sticky="w")
        ctk.CTkLabel(form, text="Description").grid(row=6, column=0, padx=4, sticky="ne")
        self._stk_desc = ctk.CTkTextbox(form, width=420, height=48)
        self._stk_desc.grid(row=6, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(form, text="Notes").grid(row=7, column=0, padx=4, sticky="ne")
        self._stk_notes = ctk.CTkTextbox(form, width=420, height=44)
        self._stk_notes.grid(row=7, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        adj = ctk.CTkFrame(scroll, fg_color="transparent")
        adj.pack(fill="x", pady=4)
        ctk.CTkLabel(adj, text="Quick Δ qty (loaded ID)").pack(side="left", padx=(0, 6))
        self._stk_delta = ctk.CTkEntry(adj, width=80, placeholder_text="+1 / -2")
        self._stk_delta.pack(side="left", padx=4)
        ctk.CTkButton(adj, text="Apply Δ", command=self._on_stock_apply_delta).pack(side="left", padx=6)
        rowb = ctk.CTkFrame(scroll, fg_color="transparent")
        rowb.pack(fill="x", pady=6)
        ctk.CTkButton(rowb, text="Save product", command=self._on_save_stock).pack(side="left", padx=4)
        ctk.CTkButton(rowb, text="Load by ID", command=self._on_load_stock).pack(side="left", padx=4)
        ctk.CTkButton(rowb, text="Clear form", command=self._on_clear_stock_form).pack(side="left", padx=4)
        ctk.CTkButton(rowb, text="Archive", command=self._on_archive_stock).pack(side="left", padx=4)
        self._add_help_bubble(rowb, "Products are sellable inventory. Archive hides old items without deleting history.")
        self._stk_list = ctk.CTkTextbox(scroll, height=220, font=ctk.CTkFont(family="Consolas", size=12))
        self._stk_list.pack(fill="x", pady=(8, 16))

        ctk.CTkLabel(
            scroll,
            text="Supplies & materials",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", pady=(12, 2))
        ctk.CTkLabel(
            scroll,
            text=(
                "Consumables and inputs—not the customer-facing catalog. "
                "Category is free-form: try Office, Production / raw, Packaging, or Parts so you can scan the list quickly."
            ),
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=11),
            wraplength=720,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))
        sform = ctk.CTkFrame(scroll)
        sform.pack(fill="x", pady=6)
        ctk.CTkLabel(sform, text="ID").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self._sup_id = ctk.CTkEntry(sform, width=72, placeholder_text="edit")
        self._sup_id.grid(row=0, column=1, padx=4, sticky="w")
        self._sup_name = ctk.CTkEntry(sform, width=360, placeholder_text="Name *")
        self._sup_name.grid(row=1, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(sform, text="Name").grid(row=1, column=0, padx=4, sticky="e")
        self._sup_cat = ctk.CTkEntry(sform, width=200, placeholder_text="Office · Production · …")
        self._sup_cat.grid(row=2, column=1, padx=4, pady=4, sticky="w")
        self._sup_unit = ctk.CTkEntry(sform, width=80, placeholder_text="ea")
        self._sup_unit.grid(row=2, column=2, padx=4, pady=4, sticky="w")
        ctk.CTkLabel(sform, text="Category / Unit").grid(row=2, column=0, padx=4, sticky="e")
        self._sup_qty = ctk.CTkEntry(sform, width=100, placeholder_text="Qty on hand")
        self._sup_qty.grid(row=3, column=1, padx=4, pady=4, sticky="w")
        self._sup_reorder = ctk.CTkEntry(sform, width=100, placeholder_text="Reorder at")
        self._sup_reorder.grid(row=3, column=2, padx=4, pady=4, sticky="w")
        ctk.CTkLabel(sform, text="Qty / Reorder").grid(row=3, column=0, padx=4, sticky="e")
        self._sup_vendor = ctk.CTkEntry(sform, width=400, placeholder_text="Vendor / source")
        self._sup_vendor.grid(row=4, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(sform, text="Vendor").grid(row=4, column=0, padx=4, sticky="e")
        ctk.CTkLabel(sform, text="Notes").grid(row=5, column=0, padx=4, sticky="ne")
        self._sup_notes = ctk.CTkTextbox(sform, width=400, height=56)
        self._sup_notes.grid(row=5, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        sadj = ctk.CTkFrame(scroll, fg_color="transparent")
        sadj.pack(fill="x", pady=4)
        ctk.CTkLabel(sadj, text="Quick Δ qty").pack(side="left", padx=(0, 6))
        self._sup_delta = ctk.CTkEntry(sadj, width=80, placeholder_text="+1 / -3")
        self._sup_delta.pack(side="left", padx=4)
        ctk.CTkButton(sadj, text="Apply Δ", command=self._on_supply_apply_delta).pack(side="left", padx=6)
        srowb = ctk.CTkFrame(scroll, fg_color="transparent")
        srowb.pack(fill="x", pady=6)
        ctk.CTkButton(srowb, text="Save supply", command=self._on_save_supply).pack(side="left", padx=4)
        ctk.CTkButton(srowb, text="Load by ID", command=self._on_load_supply).pack(side="left", padx=4)
        ctk.CTkButton(srowb, text="Clear form", command=self._on_clear_supply_form).pack(side="left", padx=4)
        ctk.CTkButton(srowb, text="Archive", command=self._on_archive_supply).pack(side="left", padx=4)
        self._add_help_bubble(srowb, "Supplies are internal consumables/materials. Archive keeps records but hides item.")
        self._sup_list = ctk.CTkTextbox(scroll, height=220, font=ctk.CTkFont(family="Consolas", size=12))
        self._sup_list.pack(fill="x", pady=8)

    def _refresh_stock_list(self) -> None:
        rows = list_stock_products(self.cfg, self.uid)
        self._stk_list.delete("0.0", "end")
        self._stk_list.insert("end", "id    sku          qty      reorder  name\n")
        for r in rows:
            q = float(r.get("qty_on_hand") or 0)
            ro = float(r.get("reorder_level") or 0)
            flag = "  LOW" if ro > 0 and q <= ro else ""
            sku = str(r.get("sku") or "")[:12]
            self._stk_list.insert(
                "end",
                f"{r['id']:<5} {sku:<12} {q:>8.2f} {ro:>8.2f}  {r['name'][:42]}{flag}\n",
            )

    def _on_clear_stock_form(self) -> None:
        for w in (self._stk_id, self._stk_name, self._stk_sku, self._stk_unit, self._stk_qty, self._stk_reorder, self._stk_cost, self._stk_price, self._stk_delta):
            w.delete(0, "end")
        self._stk_unit.insert(0, "ea")
        self._stk_desc.delete("0.0", "end")
        self._stk_notes.delete("0.0", "end")
        self._stk_cur.set(settings_get(self.cfg, "currency_default", "USD"))

    def _on_load_stock(self) -> None:
        try:
            pid = int(self._stk_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter numeric product ID.")
            return
        r = get_stock_product(self.cfg, self.uid, pid)
        if not r:
            messagebox.showerror("RootRecord Business Manager", "Product not found.")
            return
        self._on_clear_stock_form()
        self._stk_id.insert(0, str(r["id"]))
        self._stk_name.insert(0, str(r.get("name") or ""))
        self._stk_sku.insert(0, str(r.get("sku") or ""))
        self._stk_unit.insert(0, str(r.get("unit") or "ea"))
        self._stk_qty.insert(0, str(r.get("qty_on_hand") or 0))
        self._stk_reorder.insert(0, str(r.get("reorder_level") or 0))
        if r.get("unit_cost_cents") is not None:
            self._stk_cost.insert(0, f"{int(r['unit_cost_cents']) / 100:.2f}")
        if r.get("unit_price_cents") is not None:
            self._stk_price.insert(0, f"{int(r['unit_price_cents']) / 100:.2f}")
        self._stk_cur.set(str(r.get("currency") or "USD"))
        self._stk_desc.insert("0.0", str(r.get("description") or ""))
        self._stk_notes.insert("0.0", str(r.get("notes") or ""))

    def _on_save_stock(self) -> None:
        name = self._stk_name.get().strip()
        if not name:
            messagebox.showerror("RootRecord Business Manager", "Name is required.")
            return
        try:
            pid = int(self._stk_id.get().strip()) if self._stk_id.get().strip() else None
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "ID must be numeric.")
            return
        try:
            q = float(self._stk_qty.get().strip() or "0")
            ro = float(self._stk_reorder.get().strip() or "0")
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Invalid quantity or reorder level.")
            return
        cost_c = None
        price_c = None
        try:
            if self._stk_cost.get().strip():
                cost_c = int(round(float(self._stk_cost.get().strip()) * 100))
            if self._stk_price.get().strip():
                price_c = int(round(float(self._stk_price.get().strip()) * 100))
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Invalid cost or price.")
            return
        try:
            save_stock_product(
                self.cfg,
                self.uid,
                product_id=pid,
                name=name,
                sku=self._stk_sku.get().strip(),
                description=self._stk_desc.get("0.0", "end").strip(),
                unit=self._stk_unit.get().strip() or "ea",
                qty_on_hand=q,
                reorder_level=ro,
                unit_cost_cents=cost_c,
                unit_price_cents=price_c,
                currency=self._stk_cur.get(),
                notes=self._stk_notes.get("0.0", "end").strip(),
            )
        except ValueError as e:
            messagebox.showerror("RootRecord Business Manager", str(e))
            return
        self._refresh_stock_list()
        messagebox.showinfo("RootRecord Business Manager", "Product saved.")

    def _on_archive_stock(self) -> None:
        try:
            pid = int(self._stk_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter product ID to archive.")
            return
        archive_stock_product(self.cfg, self.uid, pid)
        self._refresh_stock_list()
        messagebox.showinfo("RootRecord Business Manager", "Product archived.")

    def _on_stock_apply_delta(self) -> None:
        try:
            pid = int(self._stk_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Load a product (ID) first.")
            return
        try:
            d = float(self._stk_delta.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter a number for Δ qty.")
            return
        try:
            new_q = adjust_stock_product_qty(self.cfg, self.uid, pid, d)
        except ValueError as e:
            messagebox.showerror("RootRecord Business Manager", str(e))
            return
        self._stk_qty.delete(0, "end")
        self._stk_qty.insert(0, str(new_q))
        self._stk_delta.delete(0, "end")
        self._refresh_stock_list()

    def _refresh_supplies_list(self) -> None:
        rows = list_supplies(self.cfg, self.uid)
        self._sup_list.delete("0.0", "end")
        self._sup_list.insert("end", "id    qty      reorder  category        name\n")
        for r in rows:
            q = float(r.get("qty_on_hand") or 0)
            ro = float(r.get("reorder_level") or 0)
            flag = "  LOW" if ro > 0 and q <= ro else ""
            cat = str(r.get("category") or "")[:14]
            self._sup_list.insert(
                "end",
                f"{r['id']:<5} {q:>8.2f} {ro:>8.2f}  {cat:<14}  {r['name'][:36]}{flag}\n",
            )

    def _on_clear_supply_form(self) -> None:
        for w in (self._sup_id, self._sup_name, self._sup_cat, self._sup_unit, self._sup_qty, self._sup_reorder, self._sup_vendor, self._sup_delta):
            w.delete(0, "end")
        self._sup_unit.insert(0, "ea")
        self._sup_notes.delete("0.0", "end")

    def _on_load_supply(self) -> None:
        try:
            sid = int(self._sup_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter numeric supply ID.")
            return
        r = get_supply(self.cfg, self.uid, sid)
        if not r:
            messagebox.showerror("RootRecord Business Manager", "Supply not found.")
            return
        self._on_clear_supply_form()
        self._sup_id.insert(0, str(r["id"]))
        self._sup_name.insert(0, str(r.get("name") or ""))
        self._sup_cat.insert(0, str(r.get("category") or ""))
        self._sup_unit.insert(0, str(r.get("unit") or "ea"))
        self._sup_qty.insert(0, str(r.get("qty_on_hand") or 0))
        self._sup_reorder.insert(0, str(r.get("reorder_level") or 0))
        self._sup_vendor.insert(0, str(r.get("vendor") or ""))
        self._sup_notes.insert("0.0", str(r.get("notes") or ""))

    def _on_save_supply(self) -> None:
        name = self._sup_name.get().strip()
        if not name:
            messagebox.showerror("RootRecord Business Manager", "Name is required.")
            return
        try:
            sid = int(self._sup_id.get().strip()) if self._sup_id.get().strip() else None
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "ID must be numeric.")
            return
        try:
            q = float(self._sup_qty.get().strip() or "0")
            ro = float(self._sup_reorder.get().strip() or "0")
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Invalid quantity or reorder level.")
            return
        try:
            save_supply(
                self.cfg,
                self.uid,
                supply_id=sid,
                name=name,
                category=self._sup_cat.get().strip(),
                unit=self._sup_unit.get().strip() or "ea",
                qty_on_hand=q,
                reorder_level=ro,
                vendor=self._sup_vendor.get().strip(),
                notes=self._sup_notes.get("0.0", "end").strip(),
            )
        except ValueError as e:
            messagebox.showerror("RootRecord Business Manager", str(e))
            return
        self._refresh_supplies_list()
        messagebox.showinfo("RootRecord Business Manager", "Supply saved.")

    def _on_archive_supply(self) -> None:
        try:
            sid = int(self._sup_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter supply ID to archive.")
            return
        archive_supply(self.cfg, self.uid, sid)
        self._refresh_supplies_list()
        messagebox.showinfo("RootRecord Business Manager", "Supply archived.")

    def _on_supply_apply_delta(self) -> None:
        try:
            sid = int(self._sup_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Load a supply (ID) first.")
            return
        try:
            d = float(self._sup_delta.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Enter a number for Δ qty.")
            return
        try:
            new_q = adjust_supply_qty(self.cfg, self.uid, sid, d)
        except ValueError as e:
            messagebox.showerror("RootRecord Business Manager", str(e))
            return
        self._sup_qty.delete(0, "end")
        self._sup_qty.insert(0, str(new_q))
        self._sup_delta.delete(0, "end")
        self._refresh_supplies_list()

    def _build_help(self) -> None:
        t = self.tabview.tab("About & Help")
        pages = ctk.CTkTabview(t)
        pages.pack(fill="both", expand=True)
        pages.add("About")
        pages.add("Help")

        about_tab = pages.tab("About")
        body = ctk.CTkFrame(about_tab, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)

        head = ctk.CTkFrame(body, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            head,
            text="RootRecord — Monitoring, automation, and data services",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", pady=(0, 2))
        ctk.CTkLabel(
            head,
            text=f"Version {APP_VERSION}",
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(0, 6))

        content = ctk.CTkFrame(body, fg_color="transparent")
        content.grid(row=2, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=4)
        content.grid_columnconfigure(1, weight=3)
        content.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(content, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = ctk.CTkFrame(content, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        about_text = (
            "Your grounding root for productivity, operations, and real-world data.\n\n"
            "Purpose:\n"
            "RootRecord is built to make complex environments easier to operate in one calm workspace.\n\n"
            "Principles:\n"
            "- Reliability first\n"
            "- Clarity over cleverness\n"
            "- Operability in the real world\n"
            "- Composable services\n"
            "- Respectful communication\n\n"
            "Services (modular): monitoring, automation, alerts, weather intelligence,\n"
            "energy awareness, notes/records, AI-assisted reporting, and community touchpoints.\n\n"
            "Contact channels:\n"
            "- X (Twitter): @RootRecord\n"
            "- Telegram Bot: @RootRecord_Bot\n"
            "- Community: Telegram groups\n\n"
            "Policies:\n"
            "Use the buttons below to open Terms of Service and Privacy Policy."
        )
        box = ctk.CTkTextbox(left, font=ctk.CTkFont(size=12))
        box.pack(fill="both", expand=True, pady=(0, 8))
        box.insert("0.0", about_text)
        box.configure(state="disabled")

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.grid(row=3, column=0, sticky="w", pady=(8, 0))
        ctk.CTkButton(row, text="Open Website", command=lambda: webbrowser.open("https://rootrecord.info/")).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            row,
            text="Terms of Service",
            command=lambda: webbrowser.open("https://rootrecord.info/terms"),
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            row,
            text="Privacy Policy",
            command=lambda: webbrowser.open("https://rootrecord.info/privacy"),
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            row,
            text="Contact",
            command=lambda: webbrowser.open("https://rootrecord.info/contact"),
        ).pack(side="left", padx=4)

        self._pack_about_side_image(right)

        help_tab = pages.tab("Help")
        ctk.CTkLabel(
            help_tab,
            text="Help & how it works",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", pady=(0, 6))
        ctk.CTkLabel(
            help_tab,
            text="Overview of features, data, and workflows. Your database file path is shown in the footer.",
            text_color=self._theme_text_muted,
        ).pack(anchor="w", pady=(0, 8))
        help_box = ctk.CTkTextbox(help_tab, font=ctk.CTkFont(family="Consolas", size=12))
        help_box.pack(fill="both", expand=True, pady=4)
        help_box.insert("0.0", USER_GUIDE.strip() + "\n")
        help_box.configure(state="disabled")

    def _build_billing(self) -> None:
        """Top-level sidebar page (not nested inside About & Help)."""
        self._billing_poll_after = None
        billing_tab = self.tabview.tab("Billing")
        ctk.CTkLabel(
            billing_tab,
            text="Your subscription",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(anchor="w", padx=8, pady=(8, 2))
        ctk.CTkLabel(
            billing_tab,
            text="Manage your plan and activation.",
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=13),
            wraplength=920,
            justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 12))

        bill_scroll = ctk.CTkScrollableFrame(billing_tab, fg_color="transparent")
        bill_scroll.pack(fill="both", expand=True, padx=6, pady=(0, 12))

        hero = ctk.CTkFrame(bill_scroll, fg_color=self._theme_nav_active, corner_radius=16)
        hero.pack(fill="x", pady=(0, 16))
        hero_inner = ctk.CTkFrame(hero, fg_color="transparent")
        hero_inner.pack(fill="x", padx=22, pady=20)
        self._billing_hero_title_lbl = ctk.CTkLabel(
            hero_inner,
            text="Loading…",
            text_color=self._theme_text_heading,
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
            justify="left",
        )
        self._billing_hero_title_lbl.pack(anchor="w", fill="x")
        self._billing_hero_sub_lbl = ctk.CTkLabel(
            hero_inner,
            text="",
            text_color=self._theme_billing_hero_sub,
            font=ctk.CTkFont(size=13),
            anchor="w",
            justify="left",
            wraplength=860,
        )
        self._billing_hero_sub_lbl.pack(anchor="w", fill="x", pady=(8, 0))

        def _billing_card(title: str) -> ctk.CTkFrame:
            shell = ctk.CTkFrame(bill_scroll, fg_color="transparent")
            shell.pack(fill="x", pady=(0, 14))
            ctk.CTkLabel(
                shell,
                text=title,
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color=self._theme_billing_card_title,
                anchor="w",
            ).pack(anchor="w", padx=4, pady=(0, 8))
            card = ctk.CTkFrame(
                shell,
                fg_color=self._theme_nav_idle,
                corner_radius=14,
                border_width=1,
                border_color=self._theme_border_subtle,
            )
            card.pack(fill="x")
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="x", padx=18, pady=16)
            return inner

        def _billing_row(parent: ctk.CTkFrame, label: str, initial: str = "—") -> ctk.CTkLabel:
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=6)
            ctk.CTkLabel(
                row,
                text=label,
                width=210,
                anchor="w",
                text_color=self._theme_billing_row_label,
                font=ctk.CTkFont(size=13),
            ).pack(side="left", anchor="n")
            val_lbl = ctk.CTkLabel(
                row,
                text=initial,
                text_color=self._theme_text_heading,
                anchor="w",
                justify="left",
                wraplength=600,
                font=ctk.CTkFont(size=13),
            )
            val_lbl.pack(side="left", fill="x", expand=True)
            return val_lbl

        acct = _billing_card("Account")
        self._billing_lbl_email = _billing_row(acct, "Email", "—")
        self._billing_lbl_cloud_id = _billing_row(acct, "Account ID", "—")
        row_sync = ctk.CTkFrame(acct, fg_color="transparent")
        row_sync.pack(fill="x", pady=6)
        ctk.CTkLabel(
            row_sync,
            text="Last updated",
            width=210,
            anchor="w",
            text_color=self._theme_billing_row_label,
            font=ctk.CTkFont(size=13),
        ).pack(side="left", anchor="n")
        self._billing_sync_label = ctk.CTkLabel(
            row_sync,
            text="—",
            text_color=self._theme_text_heading,
            anchor="w",
            justify="left",
            wraplength=600,
            font=ctk.CTkFont(size=13),
        )
        self._billing_sync_label.pack(side="left", fill="x", expand=True)

        plan = _billing_card("Plan")
        self._billing_lbl_plan = _billing_row(plan, "Current plan", "—")
        self._billing_lbl_account_since = _billing_row(plan, "Account since", "—")
        self._billing_lbl_trial_end = _billing_row(plan, "Trial ends", "—")
        self._billing_lbl_trial_left = _billing_row(plan, "Time remaining", "—")

        hint = ctk.CTkFrame(bill_scroll, fg_color="transparent")
        hint.pack(fill="x", pady=(4, 10))
        ctk.CTkLabel(
            hint,
            text=(
                "Your subscription is tied to the Account ID above. Your trial starts when that account "
                "is created. After paying in the browser, use Refresh status so this app updates right away."
            ),
            text_color=self._theme_billing_hint,
            font=ctk.CTkFont(size=12),
            wraplength=880,
            justify="left",
        ).pack(anchor="w", padx=4)

        bill_btns = ctk.CTkFrame(billing_tab, fg_color="transparent")
        bill_btns.pack(fill="x", padx=8, pady=(0, 10))
        self._billing_activate_btn = ctk.CTkButton(
            bill_btns,
            text="Open subscription link",
            width=220,
            height=42,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=self._theme_accent,
            hover_color=self._theme_hover,
            border_width=0,
            command=self._on_billing_activate_services,
        )
        self._billing_activate_btn.pack(side="left", padx=(0, 12))
        ctk.CTkButton(
            bill_btns,
            text="Refresh status",
            width=150,
            height=42,
            fg_color=self._theme_nav_active,
            hover_color=self._theme_hover,
            command=self._refresh_billing_panel,
        ).pack(side="left")
        self._attach_tooltip(
            self._billing_activate_btn,
            "Opens your secure payment page in the browser to subscribe or manage billing.",
        )

    def _billing_apply_panel_texts_fallback(self) -> None:
        """Best-effort text when refresh fails (avoids stuck hero / empty-looking rows)."""
        try:
            self._billing_hero_title_lbl.configure(text="Subscription status unavailable")
            self._billing_hero_sub_lbl.configure(
                text="If this keeps happening, use Refresh status or check the activity log under Settings."
            )
        except Exception:
            pass
        for attr, txt in (
            ("_billing_lbl_email", "—"),
            ("_billing_lbl_cloud_id", "—"),
            ("_billing_lbl_plan", "—"),
            ("_billing_lbl_account_since", "—"),
            ("_billing_lbl_trial_end", "—"),
            ("_billing_lbl_trial_left", "—"),
        ):
            lbl = getattr(self, attr, None)
            if lbl is not None:
                try:
                    lbl.configure(text=txt)
                except Exception:
                    pass
        sync = getattr(self, "_billing_sync_label", None)
        if sync is not None:
            try:
                sync.configure(text="—", text_color=self._theme_billing_hint)
            except Exception:
                pass

    def _cancel_billing_poll(self) -> None:
        jid = getattr(self, "_billing_poll_after", None)
        if jid is not None:
            try:
                self.after_cancel(jid)
            except Exception:
                pass
            self._billing_poll_after = None

    def _schedule_billing_poll(self) -> None:
        self._cancel_billing_poll()

        def tick() -> None:
            self._billing_poll_after = None
            try:
                if self.tabview.get() != "Billing":
                    return
                self._refresh_billing_panel()
                self._billing_poll_after = self.after(25000, tick)
            except Exception:
                logging.getLogger("rootrecord.ui").exception("Billing poll refresh failed")

        self._billing_poll_after = self.after(25000, tick)

    def _sync_footer_license_display(self) -> None:
        try:
            from license_client import license_footer_hints
            from license_config import get_license_api_config

            if not get_license_api_config():
                pass
            else:
                h = license_footer_hints(api_configured=True)
                if hasattr(self, "_license_banner_var"):
                    self._license_banner_var.set((h.line or ""))
                    self._purge_footer_ctk_label_placeholders()
        except Exception:
            pass

    def _sync_cloud_tick(self) -> None:
        """Periodically sync account data when online sign-in is configured and a session exists."""
        _interval_ms = 60_000
        try:
            from license_config import get_license_api_config

            if not get_license_api_config():
                self.after(_interval_ms, self._sync_cloud_tick)
                return
            from sync_engine import sync_cycle_best_effort

            pushed, pulled, applied = sync_cycle_best_effort(self.cfg, local_user_id=self.uid)
            if pushed or pulled or applied:
                logging.getLogger("rootrecord.sync").info(
                    "sync cycle: pushed=%s pulled=%s applied=%s", pushed, pulled, applied
                )
        except Exception:
            logging.getLogger("rootrecord.sync").debug("sync tick", exc_info=True)
        finally:
            try:
                self.after(_interval_ms, self._sync_cloud_tick)
            except Exception:
                pass

    def _on_billing_payment_link(self) -> None:
        from license_config import get_payment_link_url

        email = (settings_get(self.cfg, "license_account_email", "") or "").strip() or (
            os.environ.get("LICENSE_EMAIL") or ""
        ).strip()
        base = get_payment_link_url().strip()
        if not base.startswith("http"):
            messagebox.showerror("RootRecord", "Invalid payment link configuration.")
            return
        if email:
            sep = "&" if "?" in base else "?"
            url = f"{base}{sep}prefilled_email={quote(email)}"
        else:
            url = base
        webbrowser.open(url)

    def _on_billing_activate_services(self) -> None:
        """Open the Stripe product / payment link immediately (no remote checkout hop)."""
        email = (settings_get(self.cfg, "license_account_email", "") or "").strip() or (
            os.environ.get("LICENSE_EMAIL") or ""
        ).strip()
        if not email:
            messagebox.showinfo(
                "RootRecord",
                "Set your billing email first (sign-in prompt or Account Settings).",
            )
            return
        self._on_billing_payment_link()

    def _refresh_billing_panel(self) -> None:
        if getattr(self, "_billing_lbl_email", None) is None:
            return
        from license_client import (
            LICENSE_CLOUD_ACCOUNT_ID_KEY,
            LicenseConflictError,
            format_trial_remaining_human,
            read_cache_file,
        )
        from license_config import get_license_api_config
        from license_runtime import refresh_entitlement_and_apply

        api = get_license_api_config()
        email = (settings_get(self.cfg, "license_account_email", "") or "").strip() or (
            os.environ.get("LICENSE_EMAIL") or ""
        ).strip()

        self._billing_lbl_email.configure(text=email or "Not set")

        fetch_note = ""
        sync_color = self._theme_billing_row_label
        if api and email:
            try:
                refresh_entitlement_and_apply(self.cfg)
                fetch_note = "Just now"
                sync_color = "#5dd39e"
            except LicenseConflictError as exc:
                fetch_note = exc.message[:120] if len(exc.message) > 120 else exc.message
                sync_color = "#e07a7a"
            except RuntimeError:
                fetch_note = "Could not update"
                sync_color = "#e6c35c"
            except Exception:
                fetch_note = "Could not update"
                sync_color = "#e07a7a"
        elif not api:
            fetch_note = "—"
            sync_color = self._theme_billing_hint
        else:
            fetch_note = "Add email first"
            sync_color = "#e6c35c"

        try:
            self._billing_sync_label.configure(text=fetch_note, text_color=sync_color)
        except Exception:
            pass

        raw = read_cache_file() or {}
        cloud_id = (settings_get(self.cfg, LICENSE_CLOUD_ACCOUNT_ID_KEY, "") or "").strip()
        if not cloud_id:
            rid = raw.get("account_id")
            if isinstance(rid, str) and len(rid.strip()) >= 32:
                cloud_id = rid.strip()
                settings_set(self.cfg, LICENSE_CLOUD_ACCOUNT_ID_KEY, cloud_id)
        try:
            self._billing_lbl_cloud_id.configure(text=cloud_id or "—")
        except Exception:
            pass

        access = str(raw.get("access") or "—")
        reason = str(raw.get("reason") or "—")
        trial_end = raw.get("trial_ends_at")
        trial_start = raw.get("trial_started_at")

        def _fmt_local(iso_val: Any) -> str:
            if not isinstance(iso_val, str) or not iso_val.strip():
                return "—"
            try:
                return format_stored_utc_as_local(self.cfg, iso_val.strip().replace("Z", "+00:00"))
            except Exception:
                return "—"

        def _plan_label() -> str:
            if access == "full" and reason == "paid":
                return "Active subscription"
            if reason == "trialing":
                return "Free trial"
            if reason == "past_due":
                return "Payment required"
            if reason == "trial_expired":
                return "Trial ended"
            if access == "read_only":
                return "Subscription required"
            return "—"

        self._billing_lbl_plan.configure(text=_plan_label())
        self._billing_lbl_account_since.configure(text=_fmt_local(trial_start))

        if isinstance(trial_end, str) and trial_end.strip():
            self._billing_lbl_trial_end.configure(text=_fmt_local(trial_end))
            rem = format_trial_remaining_human(trial_end)
            self._billing_lbl_trial_left.configure(text=rem or "—")
        else:
            self._billing_lbl_trial_end.configure(text="—")
            self._billing_lbl_trial_left.configure(text="—")

        hero = "Subscription"
        subhero = ""

        if not api:
            hero = "Not connected"
            subhero = "This copy is not linked to online subscription checks."
        elif not email:
            hero = "Email not set"
            subhero = "Add your billing email under Account Settings (or sign in when prompted)."
        elif access == "full" and reason == "paid":
            hero = "You're subscribed"
            subhero = ""
        elif access == "full" and reason == "trialing":
            rem = format_trial_remaining_human(trial_end if isinstance(trial_end, str) else None)
            hero = "Free trial"
            subhero = (
                "Your trial started when your account was created."
                + (f" {rem} left." if rem else "")
            )
        elif reason == "past_due":
            hero = "Payment required"
            subhero = "Update your payment method, then Refresh status."
        elif access == "read_only" or reason in ("trial_expired",):
            hero = "Activate to continue"
            subhero = "Use Open subscription link, complete checkout in the browser, then Refresh status."
        else:
            hero = "Subscription"
            subhero = "Tap Refresh status to update."

        try:
            self._billing_hero_title_lbl.configure(text=hero)
            self._billing_hero_sub_lbl.configure(text=subhero)
        except Exception:
            pass

        self._sync_footer_license_display()

    def _build_calendar(self) -> None:
        t = self.tabview.tab("Work Log")
        from datetime import datetime, timedelta, date

        ctk.CTkLabel(t, text="Work Log", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            t,
            text="Day timeline, entry list, summaries, and report exports for the selected range.",
            text_color=self._theme_text_muted,
        ).pack(anchor="w", pady=(0, 8))
        self._cal_day = tk.StringVar(value=datetime.now().date().isoformat())
        today = datetime.now().date()
        self._range_start = tk.StringVar(value=today.isoformat())
        self._range_end = tk.StringVar(value=(today + timedelta(days=1)).isoformat())
        row = ctk.CTkFrame(t, fg_color="transparent")
        row.pack(fill="x", pady=8)
        ctk.CTkButton(row, text="<", width=36, command=lambda: self._shift_day(-1)).pack(side="left", padx=4)
        self._cal_entry = ctk.CTkEntry(row, width=140, textvariable=self._cal_day)
        self._cal_entry.pack(side="left", padx=4)
        ctk.CTkButton(row, text=">", width=36, command=lambda: self._shift_day(1)).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Load day", command=self._refresh_calendar).pack(side="left", padx=8)
        ctk.CTkButton(row, text="Bulk Edit", command=self._open_bulk_edit_dialog).pack(side="left", padx=8)
        self._add_help_bubble(row, "Load day refreshes entries. Work Log report exports include Income/Expenses and therefore reflect net calculations.")

        rp = ctk.CTkFrame(t)
        rp.pack(fill="x", pady=6)
        ctk.CTkLabel(rp, text="Report range start").grid(row=0, column=0, padx=4, pady=4, sticky="w")
        ctk.CTkEntry(rp, width=130, textvariable=self._range_start).grid(row=0, column=1, padx=4, pady=4)
        ctk.CTkLabel(rp, text="end (exclusive)").grid(row=0, column=2, padx=4, pady=4, sticky="w")
        ctk.CTkEntry(rp, width=130, textvariable=self._range_end).grid(row=0, column=3, padx=4, pady=4)
        ctk.CTkButton(rp, text="Apply custom", command=self._apply_custom_range).grid(row=0, column=4, padx=6)
        ctk.CTkButton(rp, text="Export CSV", command=self._export_report_csv).grid(row=0, column=5, padx=6)
        ctk.CTkButton(rp, text="Export PDF", command=self._export_report_pdf).grid(row=0, column=6, padx=6)
        hb_rp = ctk.CTkLabel(rp, text="?", width=18, height=18, fg_color=self._theme_help_btn_bg, text_color=self._theme_help_btn_fg, corner_radius=9)
        hb_rp.grid(row=0, column=7, padx=(4, 0), pady=4, sticky="w")
        self._attach_tooltip(
            hb_rp,
            "Report range end is exclusive. CSV/PDF exports include summary totals based on this range.",
        )

        presets = ctk.CTkFrame(t, fg_color="transparent")
        presets.pack(fill="x", pady=4)
        for i, (label, key) in enumerate(
            (
                ("Today", "today"),
                ("Yesterday", "yesterday"),
                ("This Week", "this_week"),
                ("Last Week", "last_week"),
                ("This Month", "this_month"),
                ("Last Month", "last_month"),
            )
        ):
            ctk.CTkButton(presets, text=label, width=98, command=lambda k=key: self._apply_range_preset(k)).grid(
                row=0, column=i, padx=3, pady=3
            )

        self._report_box = ctk.CTkTextbox(t, height=120, font=ctk.CTkFont(family="Consolas", size=12))
        self._report_box.pack(fill="x", pady=(2, 6))

        self._cal_summary = ctk.CTkTextbox(t, height=120, font=ctk.CTkFont(family="Consolas", size=12))
        self._cal_summary.pack(fill="x", pady=6)
        ctk.CTkLabel(
            t,
            text=(
                "Entries below mix time blocks (from the timer) with session markers (clock in/out, breaks). "
                "Click a line to edit: time rows use Start/End/Category/Project; session rows use Start as event time "
                "and End is ignored. IDs: numeric = time entry; SESSION id=N = session marker."
            ),
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=11),
            wraplength=920,
            justify="left",
        ).pack(anchor="w", pady=(0, 4))
        self._cal_entries = ctk.CTkTextbox(t, height=260, font=ctk.CTkFont(family="Consolas", size=12))
        self._cal_entries.pack(fill="both", expand=True, pady=6)
        self._cal_entries.bind("<ButtonRelease-1>", self._on_calendar_entry_click)
        self._worklog_edit_kind = "time"
        self._worklog_edit_session_row = None

        edit = ctk.CTkFrame(t, fg_color="transparent")
        edit.pack(fill="x", pady=6)
        self._edit_id = ctk.CTkEntry(edit, width=80, placeholder_text="Entry ID")
        self._edit_id.pack(side="left", padx=4)
        self._edit_start = ctk.CTkEntry(edit, width=170, placeholder_text="Start local (YYYY-MM-DD HH:MM)")
        self._edit_start.pack(side="left", padx=4)
        self._edit_end = ctk.CTkEntry(edit, width=170, placeholder_text="End local (YYYY-MM-DD HH:MM)")
        self._edit_end.pack(side="left", padx=4)
        self._edit_desc = ctk.CTkEntry(edit, width=240, placeholder_text="Description")
        self._edit_desc.pack(side="left", padx=4)
        cats = list_work_categories(self.cfg, self.uid)
        self._edit_cat_map = {c["name"]: int(c["id"]) for c in cats}
        self._edit_cat = ctk.CTkComboBox(edit, width=150, values=list(self._edit_cat_map.keys()) or ["Development"])
        self._edit_cat.set((list(self._edit_cat_map.keys()) or ["Development"])[0])
        self._edit_cat.pack(side="left", padx=4)
        projs = list_projects(self.cfg, self.uid)
        self._edit_proj_map = {"(none)": None}
        for p in projs:
            self._edit_proj_map[p["name"]] = int(p["id"])
        self._edit_proj = ctk.CTkComboBox(edit, width=150, values=list(self._edit_proj_map.keys()) or ["(none)"])
        self._edit_proj.set("(none)")
        self._edit_proj.pack(side="left", padx=4)
        ctk.CTkButton(edit, text="Preview", command=self._preview_day_entry_change).pack(side="left", padx=4)
        ctk.CTkButton(edit, text="Save", command=self._update_day_entry).pack(side="left", padx=4)
        ctk.CTkButton(edit, text="Cancel", command=self._cancel_day_entry_edit).pack(side="left", padx=4)
        ctk.CTkButton(edit, text="Delete", command=self._delete_day_entry).pack(side="left", padx=4)
        ctk.CTkButton(edit, text="Undo Last", command=self._undo_last_calendar_change).pack(side="left", padx=4)
        self._edit_hint = ctk.CTkLabel(t, text="", text_color="#d18b00")
        self._edit_hint.pack(anchor="w", padx=4, pady=(0, 6))

        lock_row = ctk.CTkFrame(t, fg_color="transparent")
        lock_row.pack(fill="x", pady=(0, 6))
        self._lock_status = ctk.CTkLabel(lock_row, text="Range lock: unlocked", text_color=self._theme_text_muted)
        self._lock_status.pack(side="left", padx=4)
        ctk.CTkButton(lock_row, text="Lock selected report range", command=self._lock_selected_report_range).pack(
            side="left", padx=6
        )
        ctk.CTkButton(lock_row, text="Unlock selected report range", command=self._unlock_selected_report_range).pack(
            side="left", padx=6
        )
        ctk.CTkButton(lock_row, text="Auto Condense", command=self._auto_condense_selected_range).pack(
            side="left", padx=6
        )

        self._audit_box = ctk.CTkTextbox(t, height=120, font=ctk.CTkFont(family="Consolas", size=11))
        self._audit_box.pack(fill="x", pady=(0, 6))
        self._apply_range_preset("today")
        self._refresh_calendar()

    def _shift_day(self, delta: int) -> None:
        from datetime import date, timedelta
        try:
            d = date.fromisoformat(self._cal_day.get().strip())
        except ValueError:
            return
        self._cal_day.set((d + timedelta(days=delta)).isoformat())
        self._refresh_calendar()

    def _refresh_calendar(self) -> None:
        from datetime import date, datetime, timedelta
        try:
            d = date.fromisoformat(self._cal_day.get().strip())
        except ValueError:
            self._cal_summary.delete("0.0", "end")
            self._cal_summary.insert("end", "Invalid date. Use YYYY-MM-DD\n")
            return
        start, end = utc_naive_bounds_for_local_date(self.cfg, d)
        rows = list_time_entries_between(self.cfg, self.uid, start, end)
        self._calendar_rows = rows
        self._calendar_rows_by_id = {int(r["id"]): r for r in rows}
        try:
            sess_rows = list_session_events_between(self.cfg, self.uid, start, end)
        except Exception:
            sess_rows = []
        self._calendar_session_by_id = {int(r["id"]): r for r in sess_rows}
        breakdown = daily_task_breakdown(self.cfg, self.uid, start, end)
        iso_year, iso_week, _iso_weekday = d.isocalendar()
        self._cal_summary.delete("0.0", "end")
        self._cal_summary.insert(
            "end",
            f"Daily summary: {d.isoformat()}  (ISO Week {iso_week}, {iso_year})\n",
        )
        for b in breakdown:
            self._cal_summary.insert(
                "end",
                f"- {b['task_name']}: {b['percent_of_day']:.1f}% ({_fmt_hm(b['seconds_total'])})\n",
            )
        self._cal_summary.insert(
            "end",
            "\nTotals use unique time on the clock (overlapping rows are not double-counted).\n",
        )
        self._cal_entries.delete("0.0", "end")
        merged: list[tuple[str, str, dict[str, Any]]] = []
        for r in rows:
            merged.append((str(r.get("start_utc") or ""), "time", r))
        for r in sess_rows:
            merged.append((str(r.get("created_at_utc") or ""), "session", r))
        merged.sort(key=lambda x: (x[0], 0 if x[1] == "time" else 1, int(x[2].get("id") or 0)))
        for _sort_key, kind, r in merged:
            if kind == "time":
                label = ((r.get("category_name") or r.get("category") or "—").strip() or "—")[:24]
                t0 = format_stored_utc_as_local(self.cfg, str(r["start_utc"]))
                t1 = format_stored_utc_as_local(self.cfg, str(r["end_utc"]))
                self._cal_entries.insert(
                    "end",
                    f"{t0} -> {t1}  · ID {int(r['id']):5}  "
                    f"{label:10}  {r['description']}\n",
                )
            else:
                t0 = format_stored_utc_as_local(self.cfg, str(r.get("created_at_utc") or ""))
                et = str(r.get("event_type") or "event")[:14]
                sid = int(r["id"])
                detail = str(r.get("detail") or "").replace("\n", " ").strip()
                self._cal_entries.insert(
                    "end",
                    f"{t0}  · {et:14}  · SESSION id={sid}  {detail}\n",
                )
        self._refresh_lock_status()
        self._refresh_audit_panel()
        self._refresh_report_panel()

    def _on_calendar_entry_click(self, _event: Any = None) -> None:
        try:
            idx = self._cal_entries.index("insert")
            line = self._cal_entries.get(f"{idx} linestart", f"{idx} lineend")
        except Exception:
            return
        if not line:
            return
        ms = re.search(r"SESSION\s+id=(\d+)\b", line)
        if ms:
            sid = int(ms.group(1))
            srow = getattr(self, "_calendar_session_by_id", {}).get(sid)
            if srow:
                self._load_session_into_editor(srow)
            return
        m = re.search(r"\bID\s+(\d+)\b", line)
        if not m:
            return
        rid = int(m.group(1))
        row = getattr(self, "_calendar_rows_by_id", {}).get(rid)
        if not row:
            return
        self._load_row_into_editor(row)

    def _load_row_into_editor(self, row: dict[str, Any]) -> None:
        self._worklog_edit_kind = "time"
        self._worklog_edit_session_row = None
        self._edit_id.delete(0, "end")
        self._edit_id.insert(0, str(row.get("id") or ""))
        self._edit_start.delete(0, "end")
        self._edit_start.insert(0, format_stored_utc_as_local(self.cfg, str(row.get("start_utc") or "")))
        self._edit_end.delete(0, "end")
        self._edit_end.insert(0, format_stored_utc_as_local(self.cfg, str(row.get("end_utc") or "")))
        self._edit_desc.delete(0, "end")
        self._edit_desc.insert(0, str(row.get("description") or ""))
        current_cat = str(row.get("category_name") or row.get("category") or "").strip()
        if current_cat and current_cat in getattr(self, "_edit_cat_map", {}):
            self._edit_cat.set(current_cat)
        proj_name = str(row.get("project_name") or "").strip()
        self._edit_proj.set(proj_name if proj_name in getattr(self, "_edit_proj_map", {}) else "(none)")
        self._edit_hint.configure(text="")

    def _load_session_into_editor(self, row: dict[str, Any]) -> None:
        """Clock in/out and other rr_session_events rows (not rr_time_entries)."""
        self._worklog_edit_kind = "session"
        self._worklog_edit_session_row = dict(row)
        self._edit_id.delete(0, "end")
        self._edit_id.insert(0, f"session:{int(row.get('id') or 0)}")
        self._edit_start.delete(0, "end")
        self._edit_start.insert(0, format_stored_utc_as_local(self.cfg, str(row.get("created_at_utc") or "")))
        self._edit_end.delete(0, "end")
        self._edit_end.insert(0, format_stored_utc_as_local(self.cfg, str(row.get("created_at_utc") or "")))
        self._edit_desc.delete(0, "end")
        self._edit_desc.insert(0, str(row.get("detail") or ""))
        self._edit_hint.configure(
            text=f"Session marker ({row.get('event_type') or 'event'}): Start = event time; End is ignored. "
            "Save updates timestamp + detail only.",
        )

    def _parse_range_dates(self) -> tuple[datetime, datetime] | None:
        try:
            s = datetime.fromisoformat(self._range_start.get().strip())
            e = datetime.fromisoformat(self._range_end.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Use ISO date format YYYY-MM-DD for report range.")
            return None
        if e <= s:
            messagebox.showerror("RootRecord Business Manager", "Report range end must be after start.")
            return None
        return s, e

    def _apply_custom_range(self) -> None:
        if self._parse_range_dates() is None:
            return
        self._refresh_report_panel()

    def _apply_range_preset(self, preset: str) -> None:
        from datetime import date, timedelta

        today = date.today()
        if preset == "today":
            s, e = today, today + timedelta(days=1)
        elif preset == "yesterday":
            s, e = today - timedelta(days=1), today
        elif preset == "this_week":
            s = today - timedelta(days=today.weekday())
            e = s + timedelta(days=7)
        elif preset == "last_week":
            e = today - timedelta(days=today.weekday())
            s = e - timedelta(days=7)
        elif preset == "this_month":
            s = today.replace(day=1)
            e = (s.replace(day=28) + timedelta(days=4)).replace(day=1)
        elif preset == "last_month":
            this_month = today.replace(day=1)
            e = this_month
            s = (this_month - timedelta(days=1)).replace(day=1)
        else:
            return
        self._range_start.set(s.isoformat())
        self._range_end.set(e.isoformat())
        self._refresh_report_panel()

    def _refresh_report_panel(self) -> None:
        parsed = self._parse_range_dates()
        if parsed is None:
            return
        s, e = parsed
        s_iso, e_iso = utc_naive_bounds_for_local_report_range(self.cfg, s.date(), e.date())
        summary = summary_between(self.cfg, self.uid, s_iso, e_iso)
        currency_safe = bool(settings_get(self.cfg, "currency_safe_summaries_enabled", False))
        default_cur = str(settings_get(self.cfg, "currency_default", "USD")).upper()
        curr_map = money_totals_by_currency_between(self.cfg, self.uid, s_iso, e_iso) if currency_safe else {}
        time_rows = list_time_entries_between(self.cfg, self.uid, s_iso, e_iso)
        income_rows = list_income_between(self.cfg, self.uid, s_iso, e_iso)
        exp_rows = list_expenses_between(self.cfg, self.uid, s_iso, e_iso)
        self._report_cache = {
            "start": s_iso,
            "end": e_iso,
            "summary": summary,
            "time_rows": time_rows,
            "income_rows": income_rows,
            "expense_rows": exp_rows,
        }
        self._report_box.delete("0.0", "end")
        iso_s = s.date().isocalendar()
        iso_e = (e - timedelta(days=1)).date().isocalendar()
        self._report_box.insert(
            "end",
            f"Report range: {s.date().isoformat()} to {(e - timedelta(days=1)).date().isoformat()}\n",
        )
        self._report_box.insert(
            "end",
            f"ISO week(s): {iso_s[0]}-W{iso_s[1]:02d}"
            + (f" to {iso_e[0]}-W{iso_e[1]:02d}" if (iso_s[0], iso_s[1]) != (iso_e[0], iso_e[1]) else "")
            + "\n",
        )
        self._report_box.insert("end", f"Total time worked: {_fmt_hm(summary['seconds_worked_approx'])}\n")
        if currency_safe:
            d = curr_map.get(default_cur, {"income_cents": 0, "expense_cents": 0})
            self._report_box.insert("end", f"Income ({default_cur}): {_fmt_money(int(d.get('income_cents', 0)), default_cur)}\n")
            self._report_box.insert("end", f"Expenses ({default_cur}): {_fmt_money(int(d.get('expense_cents', 0)), default_cur)}\n")
            self._report_box.insert("end", f"All currencies: {_fmt_currency_totals_line(curr_map)}\n")
        else:
            self._report_box.insert("end", f"Income: {_fmt_money(summary['income_cents'])}\n")
            self._report_box.insert("end", f"Expenses: {_fmt_money(summary['expense_cents'])}\n")
        self._report_box.insert("end", f"Time entries: {len(time_rows)} | Income items: {len(income_rows)} | Expense items: {len(exp_rows)}\n")
        proj_break = daily_project_breakdown(self.cfg, self.uid, s_iso, e_iso)
        if proj_break:
            self._report_box.insert("end", "Project breakdown:\n")
            for pr in proj_break[:6]:
                self._report_box.insert(
                    "end",
                    f"- {pr['project_name']}: {_fmt_hm(float(pr['seconds_total']))} ({pr['percent_of_day']:.1f}%)\n",
                )

    def _export_report_csv(self) -> None:
        parsed = self._parse_range_dates()
        if parsed is None:
            return
        s, e = parsed
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=f"RootRecord_report_{s.date().isoformat()}_{(e - timedelta(days=1)).date().isoformat()}.csv",
        )
        if not path:
            return
        cache = getattr(self, "_report_cache", None)
        if not cache or not cache.get("start"):
            self._refresh_report_panel()
            cache = getattr(self, "_report_cache", {})
        u_s = datetime.fromisoformat(str(cache["start"]))
        u_e = datetime.fromisoformat(str(cache["end"]))
        rawb, _ = build_hours_csv_bytes_between(self.cfg, self.uid, u_s, u_e)
        Path(path).write_bytes(rawb)
        messagebox.showinfo("RootRecord Business Manager", f"Saved CSV:\n{path}")

    def _export_report_pdf(self) -> None:
        parsed = self._parse_range_dates()
        if parsed is None:
            return
        s, e = parsed
        self._refresh_report_panel()
        cache = getattr(self, "_report_cache", {}) or {}
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile=f"RootRecord_report_{s.date().isoformat()}_{(e - timedelta(days=1)).date().isoformat()}.pdf",
        )
        if not path:
            return
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.lib.utils import ImageReader
            from reportlab.pdfgen import canvas
            import io
        except Exception:
            messagebox.showerror("RootRecord Business Manager", "PDF export requires reportlab. Install dependencies and retry.")
            return

        c = canvas.Canvas(path, pagesize=letter)
        width, height = letter
        y = height - 48
        left = 48
        right = width - 48

        def line(
            txt: str,
            step: int = 15,
            *,
            font: str = "Helvetica",
            size: int = 10,
            bold: bool = False,
        ) -> None:
            nonlocal y
            if y < 56:
                c.showPage()
                y = height - 48
            face = "Helvetica-Bold" if bold else font
            c.setFont(face, size)
            c.drawString(left, y, (txt or "")[:118])
            y -= step

        def biz(label: str, key: str) -> None:
            raw = settings_get(self.cfg, key, "")
            val = (str(raw) if raw is not None else "").strip()
            line(f"{label} {val if val else '—'}")

        line("RootRecord Business Manager", 22, bold=True, size=14)
        c.setFont("Helvetica", 9)
        c.setStrokeGray(0.65)
        c.line(left, y + 6, right, y + 6)
        y -= 6
        line(f"Range: {s.date().isoformat()} to {(e - timedelta(days=1)).date().isoformat()}", step=18)
        line("Business details", bold=True, size=11)
        biz("Business:", "business_name")
        biz("Legal:", "business_legal_name")
        biz("Owner:", "business_owner")
        biz("Email:", "business_email")
        biz("Phone:", "business_phone")
        biz("Address:", "business_address")
        line("")
        line("Summary", bold=True, size=11)

        summ = cache.get("summary", {}) or {}
        u0 = str(cache.get("start", "")).strip()
        u1 = str(cache.get("end", "")).strip()
        time_rows: list[dict] = list(cache.get("time_rows", []) or [])
        breakdown: list[dict] = []
        if u0 and u1:
            breakdown = daily_task_breakdown(self.cfg, self.uid, u0, u1)
        brk_total = sum(float(b.get("seconds_total") or 0) for b in breakdown)

        line(f"Total time worked: {_fmt_hm(brk_total)}")
        line(f"Income: {_fmt_money(int(summ.get('income_cents', 0) or 0))}")
        line(f"Expenses: {_fmt_money(int(summ.get('expense_cents', 0) or 0))}")
        line("")
        line("Task breakdown (same window as total time)", bold=True, size=11)
        if not breakdown:
            line("No time entries in this range.")
        else:
            for b in breakdown:
                line(
                    f"• {b['task_name']}: {b['percent_of_day']:.1f}% ({_fmt_hm(float(b['seconds_total']))})",
                    step=14,
                )

        line("")
        line("Work Log entries", bold=True, size=11)
        if not time_rows:
            line("No work-log entries in this range.")
        else:
            for r in time_rows:
                cat = ((r.get("category_name") or r.get("category") or "—").strip() or "—")[:18]
                t0 = format_stored_utc_as_local(self.cfg, str(r.get("start_utc", "")))
                t1 = format_stored_utc_as_local(self.cfg, str(r.get("end_utc", "")))
                desc = str(r.get("description") or "").replace("\n", " ").strip()
                line(f"• {t0} -> {t1}  {cat:18}  {desc}", step=13)

        def draw_chart_image(fig, max_h: float = 200.0) -> None:
            nonlocal y
            bio = io.BytesIO()
            fig.savefig(bio, format="png", dpi=120, bbox_inches="tight")
            bio.seek(0)
            img = ImageReader(bio)
            iw, ih = img.getSize()
            avail_w = right - left
            scale = min(avail_w / float(iw), max_h / float(ih))
            dw = float(iw) * scale
            dh = float(ih) * scale
            if y - dh < 56:
                c.showPage()
                y = height - 48
            c.drawImage(img, left, y - dh, width=dw, height=dh, preserveAspectRatio=True, mask="auto")
            y -= (dh + 12)

        if _DASHBOARD_CHARTS_AVAILABLE and Figure is not None and u0 and u1:
            line("")
            line("Charts for selected report range", bold=True, size=11)
            try:
                # Pie: category share in selected range.
                fig_p = Figure(figsize=(6.6, 3.2), dpi=110)
                ax_p = fig_p.add_subplot(111)
                vals = [float(b.get("seconds_total") or 0) for b in breakdown if float(b.get("seconds_total") or 0) > 1]
                names = [str(b.get("task_name") or "")[:22] for b in breakdown if float(b.get("seconds_total") or 0) > 1]
                if vals:
                    ax_p.pie(
                        vals,
                        labels=names,
                        autopct=lambda p: f"{p:.0f}%" if p >= 4 else "",
                        pctdistance=0.72,
                    )
                    ax_p.axis("equal")
                else:
                    ax_p.text(0.5, 0.5, "No time entries", ha="center", va="center")
                    ax_p.set_xticks([])
                    ax_p.set_yticks([])
                ax_p.set_title("Category share")
                draw_chart_image(fig_p, max_h=190)

                # Trend: choose sensible buckets based on range size.
                sdt = datetime.fromisoformat(u0)
                edt = datetime.fromisoformat(u1)
                total_days = max(1, (edt.date() - sdt.date()).days)
                labels: list[str] = []
                hrs: list[float] = []
                if total_days <= 31:
                    cur = sdt.date()
                    while cur < edt.date():
                        d0, d1 = utc_naive_bounds_for_local_date(self.cfg, cur)
                        sec = float(summary_between(self.cfg, self.uid, d0, d1).get("seconds_worked_approx") or 0)
                        labels.append(f"{cur.month}/{cur.day}")
                        hrs.append(round(sec / 3600.0, 2))
                        cur += timedelta(days=1)
                    trend_title = "Daily worked hours"
                elif total_days <= 180:
                    cur = sdt.date()
                    while cur < edt.date():
                        nxt = min(cur + timedelta(days=7), edt.date())
                        d0, d1 = utc_naive_bounds_for_local_report_range(self.cfg, cur, nxt)
                        sec = float(summary_between(self.cfg, self.uid, d0, d1).get("seconds_worked_approx") or 0)
                        labels.append(f"{cur.month}/{cur.day}")
                        hrs.append(round(sec / 3600.0, 2))
                        cur = nxt
                    trend_title = "Weekly worked hours"
                elif total_days <= 730:
                    cur = date(sdt.year, sdt.month, 1)
                    end_m = date(edt.year, edt.month, 1)
                    while cur <= end_m:
                        nxt = date(cur.year + (1 if cur.month == 12 else 0), 1 if cur.month == 12 else cur.month + 1, 1)
                        d0, d1 = utc_naive_bounds_for_local_report_range(self.cfg, cur, min(nxt, edt.date()))
                        sec = float(summary_between(self.cfg, self.uid, d0, d1).get("seconds_worked_approx") or 0)
                        labels.append(f"{cur:%b %y}")
                        hrs.append(round(sec / 3600.0, 2))
                        cur = nxt
                    trend_title = "Monthly worked hours"
                else:
                    for yy in range(sdt.year, edt.year + 1):
                        ys = date(yy, 1, 1)
                        ye = date(yy + 1, 1, 1)
                        d0, d1 = utc_naive_bounds_for_local_report_range(self.cfg, max(ys, sdt.date()), min(ye, edt.date()))
                        sec = float(summary_between(self.cfg, self.uid, d0, d1).get("seconds_worked_approx") or 0)
                        labels.append(str(yy))
                        hrs.append(round(sec / 3600.0, 2))
                    trend_title = "Yearly worked hours"

                fig_b = Figure(figsize=(6.6, 3.0), dpi=110)
                ax_b = fig_b.add_subplot(111)
                xs = list(range(len(hrs)))
                ax_b.bar(xs, hrs, color=self._theme_accent)
                ax_b.set_xticks(xs)
                ax_b.set_xticklabels(labels, fontsize=7, rotation=30 if len(labels) > 8 else 0, ha="right")
                ax_b.set_ylabel("Hours")
                ax_b.set_title(trend_title)
                draw_chart_image(fig_b, max_h=190)

                # Money bars for selected range.
                fig_m = Figure(figsize=(6.6, 1.8), dpi=110)
                ax_m = fig_m.add_subplot(111)
                inc_w = int(summ.get("income_cents", 0) or 0) / 100.0
                exp_w = int(summ.get("expense_cents", 0) or 0) / 100.0
                ax_m.barh([0, 1], [inc_w, exp_w], color=["#2fa572", "#e67700"])
                ax_m.set_yticks([0, 1])
                ax_m.set_yticklabels(["Income", "Expenses"])
                ax_m.set_xlabel("Selected range")
                ax_m.set_title("Money")
                draw_chart_image(fig_m, max_h=130)
            except Exception as exc:  # noqa: BLE001
                line(f"(Could not render charts in PDF: {exc})")
        c.save()
        messagebox.showinfo("RootRecord Business Manager", f"Saved PDF:\n{path}")

    def _update_day_entry(self) -> None:
        if self._selected_range_locked():
            self._edit_hint.configure(text="This report range is locked. Unlock it before editing.")
            return
        if getattr(self, "_worklog_edit_kind", "time") == "session":
            self._update_session_day_entry()
            return
        try:
            eid = int(self._edit_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Entry ID must be a number.")
            return
        old = getattr(self, "_calendar_rows_by_id", {}).get(eid)
        if not old:
            messagebox.showerror("RootRecord Business Manager", "Entry not found in current day list.")
            return
        parsed = self._build_validated_edit_payload(old)
        if parsed is None:
            return
        start_utc, end_utc, desc, cat_id, proj_id = parsed
        preview = self._build_edit_preview(old, start_utc, end_utc, desc, cat_id, proj_id)
        if not messagebox.askyesno("Confirm changes", preview):
            return
        ok = update_time_entry(
            self.cfg,
            self.uid,
            eid,
            start_utc=start_utc,
            end_utc=end_utc,
            description=desc,
            work_category_id=cat_id,
            project_id=proj_id,
        )
        if ok:
            new_row = dict(old)
            new_row.update(
                {
                    "start_utc": start_utc,
                    "end_utc": end_utc,
                    "description": desc,
                    "work_category_id": cat_id,
                    "project_id": proj_id,
                }
            )
            self._last_calendar_undo = {"kind": "update", "old": old, "new": new_row}
            insert_time_entry_audit(self.cfg, self.uid, entry_id=eid, action="update", old_row=old, new_row=new_row)
            self._refresh_calendar()
            self._refresh_dashboard()
            self._edit_hint.configure(text="Saved. You can Undo Last if needed.")
        else:
            self._edit_hint.configure(text="Could not save. Another entry overlaps this time range.")

    def _update_session_day_entry(self) -> None:
        raw = self._edit_id.get().strip()
        if not raw.startswith("session:"):
            messagebox.showerror("RootRecord Business Manager", "Select a SESSION line from the list first.")
            return
        try:
            sid = int(raw.split(":", 1)[1])
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Invalid session selection.")
            return
        old = getattr(self, "_worklog_edit_session_row", None) or getattr(self, "_calendar_session_by_id", {}).get(sid)
        if not old or int(old.get("id") or 0) != sid:
            messagebox.showerror("RootRecord Business Manager", "Session row not found. Load day again.")
            return
        when_raw = self._edit_start.get().strip()
        d_raw = self._edit_desc.get().strip()
        if not when_raw:
            self._edit_hint.configure(text="Start (event time) is required.")
            return
        try:
            new_when = local_input_to_utc_naive_iso(self.cfg, when_raw)
        except Exception:
            self._edit_hint.configure(text="Use local date/time like 2026-04-08 13:45.")
            return
        old_when = str(old.get("created_at_utc") or "")
        old_detail = str(old.get("detail") or "")
        preview = (
            f"Update session marker ({old.get('event_type') or 'event'})?\n\n"
            f"Time: {old_when} -> {new_when}\n"
            f"Detail: {old_detail!r} -> {d_raw!r}"
        )
        if not messagebox.askyesno("Confirm changes", preview):
            return
        ok = update_session_event_row(
            self.cfg,
            self.uid,
            sid,
            created_at_utc=new_when,
            detail=d_raw,
        )
        if ok:
            self._last_calendar_undo = {
                "kind": "session_update",
                "old": dict(old),
                "new": {**dict(old), "created_at_utc": new_when, "detail": d_raw},
            }
            self._refresh_calendar()
            self._refresh_dashboard()
            self._edit_hint.configure(text="Session marker saved. Undo Last can revert.")
        else:
            self._edit_hint.configure(text="Could not save session marker.")

    def _delete_day_entry(self) -> None:
        if self._selected_range_locked():
            self._edit_hint.configure(text="This report range is locked. Unlock it before deleting.")
            return
        if getattr(self, "_worklog_edit_kind", "time") == "session":
            self._delete_session_day_entry()
            return
        try:
            eid = int(self._edit_id.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Entry ID must be a number.")
            return
        old = getattr(self, "_calendar_rows_by_id", {}).get(eid)
        if not old:
            messagebox.showerror("RootRecord Business Manager", "Entry not found in current day list.")
            return
        if delete_time_entry(self.cfg, self.uid, eid):
            self._last_calendar_undo = {"kind": "delete", "old": old}
            insert_time_entry_audit(self.cfg, self.uid, entry_id=eid, action="delete", old_row=old, new_row=None)
            self._refresh_calendar()
            self._refresh_dashboard()
            self._edit_hint.configure(text="Deleted. Use Undo Last to restore.")
        else:
            messagebox.showerror("RootRecord Business Manager", "Could not delete entry.")

    def _delete_session_day_entry(self) -> None:
        raw = self._edit_id.get().strip()
        if not raw.startswith("session:"):
            messagebox.showerror("RootRecord Business Manager", "Select a SESSION line from the list first.")
            return
        try:
            sid = int(raw.split(":", 1)[1])
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Invalid session selection.")
            return
        old = getattr(self, "_worklog_edit_session_row", None) or getattr(self, "_calendar_session_by_id", {}).get(sid)
        if not old or int(old.get("id") or 0) != sid:
            messagebox.showerror("RootRecord Business Manager", "Session row not found.")
            return
        if not messagebox.askyesno(
            "RootRecord Business Manager",
            "Delete this session marker from the log?\n\n"
            "This removes the clock in/out / break line only. It does not delete time blocks (ID rows).",
        ):
            return
        if delete_session_event_row(self.cfg, self.uid, sid):
            self._last_calendar_undo = {"kind": "session_delete", "old": dict(old)}
            self._refresh_calendar()
            self._refresh_dashboard()
            self._edit_hint.configure(text="Session marker deleted. Undo Last can restore.")
        else:
            messagebox.showerror("RootRecord Business Manager", "Could not delete session marker.")

    def _preview_day_entry_change(self) -> None:
        if getattr(self, "_worklog_edit_kind", "time") == "session":
            raw = self._edit_id.get().strip()
            if not raw.startswith("session:"):
                self._edit_hint.configure(text="Select a SESSION line first.")
                return
            try:
                sid = int(raw.split(":", 1)[1])
            except ValueError:
                self._edit_hint.configure(text="Invalid session selection.")
                return
            old = getattr(self, "_worklog_edit_session_row", None) or getattr(self, "_calendar_session_by_id", {}).get(sid)
            if not old:
                self._edit_hint.configure(text="Session row not found.")
                return
            when_raw = self._edit_start.get().strip()
            d_raw = self._edit_desc.get().strip()
            if not when_raw:
                self._edit_hint.configure(text="Start (event time) is required.")
                return
            try:
                new_when = local_input_to_utc_naive_iso(self.cfg, when_raw)
            except Exception:
                self._edit_hint.configure(text="Use local date/time like 2026-04-08 13:45.")
                return
            self._edit_hint.configure(
                text=f"Preview session {old.get('event_type')}: {old.get('created_at_utc')} -> {new_when}; detail -> {d_raw!r}",
            )
            return
        try:
            eid = int(self._edit_id.get().strip())
        except ValueError:
            self._edit_hint.configure(text="Select an entry row first.")
            return
        old = getattr(self, "_calendar_rows_by_id", {}).get(eid)
        if not old:
            self._edit_hint.configure(text="Entry not found in current day list.")
            return
        parsed = self._build_validated_edit_payload(old)
        if parsed is None:
            return
        start_utc, end_utc, desc, cat_id, proj_id = parsed
        self._edit_hint.configure(text=self._build_edit_preview(old, start_utc, end_utc, desc, cat_id, proj_id))

    def _build_validated_edit_payload(
        self, old_row: dict[str, Any]
    ) -> tuple[str, str, str, int | None, int | None] | None:
        s_raw = self._edit_start.get().strip()
        e_raw = self._edit_end.get().strip()
        d_raw = self._edit_desc.get().strip()
        if not s_raw or not e_raw:
            self._edit_hint.configure(text="Start and end are required.")
            return None
        try:
            start_utc = local_input_to_utc_naive_iso(self.cfg, s_raw)
            end_utc = local_input_to_utc_naive_iso(self.cfg, e_raw)
        except Exception:
            self._edit_hint.configure(text="Use local date/time like 2026-04-08 13:45.")
            return None
        try:
            s_dt = datetime.fromisoformat(start_utc)
            e_dt = datetime.fromisoformat(end_utc)
        except ValueError:
            self._edit_hint.configure(text="Invalid parsed datetime.")
            return None
        if e_dt <= s_dt:
            self._edit_hint.configure(text="End must be after start.")
            return None
        eid = int(old_row["id"])
        for rid, r in getattr(self, "_calendar_rows_by_id", {}).items():
            if rid == eid:
                continue
            try:
                rs = datetime.fromisoformat(str(r.get("start_utc")))
                re_ = datetime.fromisoformat(str(r.get("end_utc")))
            except ValueError:
                continue
            if rs < e_dt and re_ > s_dt:
                self._edit_hint.configure(text=f"Overlap conflict with entry ID {rid}.")
                return None
        cat_name = self._edit_cat.get().strip()
        cat_id = getattr(self, "_edit_cat_map", {}).get(cat_name, old_row.get("work_category_id"))
        proj_name = self._edit_proj.get().strip()
        proj_id = getattr(self, "_edit_proj_map", {}).get(proj_name, old_row.get("project_id"))
        return start_utc, end_utc, d_raw or "Updated", cat_id, proj_id

    def _build_edit_preview(
        self,
        old: dict[str, Any],
        start_utc: str,
        end_utc: str,
        desc: str,
        cat_id: int | None,
        proj_id: int | None,
    ) -> str:
        old_s = str(old.get("start_utc") or "")
        old_e = str(old.get("end_utc") or "")
        old_d = str(old.get("description") or "")
        old_cat = str(old.get("category_name") or old.get("category") or "—")
        old_proj = str(old.get("project_name") or "(none)")
        new_cat = next((k for k, v in getattr(self, "_edit_cat_map", {}).items() if v == cat_id), old_cat)
        new_proj = next((k for k, v in getattr(self, "_edit_proj_map", {}).items() if v == proj_id), "(none)")
        parts: list[str] = []
        if old_s != start_utc:
            try:
                delta_min = int(
                    (datetime.fromisoformat(start_utc) - datetime.fromisoformat(old_s)).total_seconds() // 60
                )
                parts.append(f"start moved {delta_min:+}m")
            except ValueError:
                parts.append("start changed")
        if old_e != end_utc:
            parts.append("end changed")
        if old_cat != new_cat:
            parts.append(f"category {old_cat} -> {new_cat}")
        if old_proj != new_proj:
            parts.append(f"project {old_proj} -> {new_proj}")
        if old_d != desc:
            parts.append("description updated")
        if not parts:
            return "No changes detected."
        return "Preview: " + "; ".join(parts)

    def _undo_last_calendar_change(self) -> None:
        undo = getattr(self, "_last_calendar_undo", None)
        if not undo:
            self._edit_hint.configure(text="Nothing to undo.")
            return
        kind = str(undo.get("kind"))
        if kind == "update":
            old = dict(undo.get("old") or {})
            eid = int(old.get("id") or 0)
            if eid <= 0:
                return
            ok = update_time_entry(
                self.cfg,
                self.uid,
                eid,
                start_utc=str(old.get("start_utc") or ""),
                end_utc=str(old.get("end_utc") or ""),
                description=str(old.get("description") or ""),
                work_category_id=old.get("work_category_id"),
                project_id=old.get("project_id"),
            )
            if ok:
                insert_time_entry_audit(
                    self.cfg, self.uid, entry_id=eid, action="undo_update", old_row=undo.get("new"), new_row=old
                )
        elif kind == "session_update":
            new = dict(undo.get("new") or {})
            old = dict(undo.get("old") or {})
            sid = int(old.get("id") or 0)
            if sid <= 0:
                return
            ok = update_session_event_row(
                self.cfg,
                self.uid,
                sid,
                created_at_utc=str(old.get("created_at_utc") or ""),
                detail=str(old.get("detail") or ""),
            )
            if not ok:
                self._edit_hint.configure(text="Undo session update failed.")
                return
        elif kind == "session_delete":
            old = dict(undo.get("old") or {})
            sid = int(old.get("id") or 0)
            if sid <= 0:
                return
            restore_session_event_row(
                self.cfg,
                self.uid,
                event_id=sid,
                event_type=str(old.get("event_type") or "event"),
                detail=str(old.get("detail") or ""),
                created_at_utc=str(old.get("created_at_utc") or ""),
            )
        elif kind == "delete":
            old = dict(undo.get("old") or {})
            try:
                insert_rich_time_entry(
                    self.cfg,
                    user_id=self.uid,
                    start_utc=str(old.get("start_utc") or ""),
                    end_utc=str(old.get("end_utc") or ""),
                    category=str(old.get("category") or ""),
                    description=str(old.get("description") or ""),
                    machine_session_id=get_machine_session_db_id(self.cfg),
                    work_category_id=old.get("work_category_id"),
                    project_id=old.get("project_id"),
                    amount_cents=old.get("amount_cents"),
                    currency=str(old.get("currency") or "USD"),
                    billable=old.get("billable"),
                    hourly_rate_cents=old.get("hourly_rate_cents"),
                    business_id=old.get("business_id")
                    if old.get("business_id") is not None
                    else business_id_for_new_time_entry(self.cfg),
                )
                insert_time_entry_audit(self.cfg, self.uid, entry_id=None, action="undo_delete", old_row=None, new_row=old)
            except Exception as exc:  # noqa: BLE001
                self._edit_hint.configure(text=f"Undo failed: {exc}")
                return
        self._last_calendar_undo = None
        self._refresh_calendar()
        self._refresh_dashboard()
        self._edit_hint.configure(text="Undo applied.")

    def _cancel_day_entry_edit(self) -> None:
        if getattr(self, "_worklog_edit_kind", "time") == "session":
            row = getattr(self, "_worklog_edit_session_row", None)
            if row:
                self._load_session_into_editor(row)
                self._edit_hint.configure(text="Reverted unsaved changes for selected session marker.")
            else:
                self._edit_hint.configure(text="Edit cleared.")
            return
        try:
            eid = int(self._edit_id.get().strip())
        except ValueError:
            self._edit_hint.configure(text="Edit cleared.")
            return
        row = getattr(self, "_calendar_rows_by_id", {}).get(eid)
        if row:
            self._load_row_into_editor(row)
            self._edit_hint.configure(text="Reverted unsaved changes for selected entry.")

    def _selected_range_locked(self) -> bool:
        parsed = self._parse_range_dates()
        if parsed is None:
            return False
        s, e = parsed
        s_iso, e_iso = utc_naive_bounds_for_local_report_range(self.cfg, s.date(), e.date())
        return is_time_range_locked(self.cfg, self.uid, start_utc=s_iso, end_utc=e_iso)

    def _lock_selected_report_range(self) -> None:
        parsed = self._parse_range_dates()
        if parsed is None:
            return
        s, e = parsed
        s_iso, e_iso = utc_naive_bounds_for_local_report_range(self.cfg, s.date(), e.date())
        add_finalized_range(self.cfg, self.uid, start_utc=s_iso, end_utc=e_iso, notes="Submitted report range")
        self._refresh_lock_status()

    def _unlock_selected_report_range(self) -> None:
        parsed = self._parse_range_dates()
        if parsed is None:
            return
        s, e = parsed
        s_iso, e_iso = utc_naive_bounds_for_local_report_range(self.cfg, s.date(), e.date())
        remove_finalized_range(self.cfg, self.uid, start_utc=s_iso, end_utc=e_iso)
        self._refresh_lock_status()

    def _refresh_lock_status(self) -> None:
        locked = self._selected_range_locked()
        ranges = list_finalized_ranges(self.cfg, self.uid)
        self._lock_status.configure(
            text=f"Range lock: {'LOCKED' if locked else 'unlocked'}  | finalized ranges: {len(ranges)}",
            text_color="#b45500" if locked else "gray",
        )

    def _refresh_audit_panel(self) -> None:
        rows = list_time_entry_audit(self.cfg, self.uid, limit=10)
        self._audit_box.delete("0.0", "end")
        self._audit_box.insert("end", "Recent edit history\n")
        for r in rows:
            when = str(r.get("changed_at") or "")[:19].replace("T", " ")
            self._audit_box.insert("end", f"- {when} | {r.get('action')} | entry {r.get('entry_id')}\n")

    def _auto_condense_selected_range(self) -> None:
        try:
            d0 = date.fromisoformat(self._range_start.get().strip())
            d1 = date.fromisoformat(self._range_end.get().strip())
        except ValueError:
            messagebox.showerror("RootRecord Business Manager", "Use YYYY-MM-DD for report range.")
            return
        if d1 <= d0:
            messagebox.showerror("RootRecord Business Manager", "Report range end must be after start.")
            return
        if not messagebox.askyesno(
            "RootRecord Business Manager",
            "Auto condense selected range?\n\n"
            "- Only adjacent entries are merged\n"
            "- Any non-time difference prevents merging\n"
            "- Entries with different entries between them are never merged",
        ):
            return

        s_utc, e_utc = utc_naive_bounds_for_local_report_range(self.cfg, d0, d1)
        self._set_process_status("Auto condensing entries...", auto_clear_ms=None)
        merged_count = 0

        def _same_non_time(a: dict[str, Any], b: dict[str, Any]) -> bool:
            for k in (
                "description",
                "category",
                "work_category_id",
                "project_id",
                "billable",
                "hourly_rate_cents",
                "amount_cents",
            ):
                if a.get(k) != b.get(k):
                    return False
            return True

        merged_any = True
        while merged_any:
            merged_any = False
            rows = list_time_entries_between(self.cfg, self.uid, s_utc, e_utc)
            rows.sort(key=lambda r: (str(r.get("start_utc") or ""), int(r.get("id") or 0)))
            for i in range(len(rows) - 1):
                a = rows[i]
                b = rows[i + 1]
                if not _same_non_time(a, b):
                    continue
                # Strictly adjacent only.
                if str(a.get("end_utc") or "") != str(b.get("start_utc") or ""):
                    continue
                ok = update_time_entry(
                    self.cfg,
                    self.uid,
                    int(a["id"]),
                    start_utc=str(a["start_utc"]),
                    end_utc=str(b["end_utc"]),
                    description=str(a.get("description") or ""),
                    work_category_id=(int(a["work_category_id"]) if a.get("work_category_id") is not None else None),
                    project_id=(int(a["project_id"]) if a.get("project_id") is not None else None),
                )
                if not ok:
                    continue
                if delete_time_entry(self.cfg, self.uid, int(b["id"])):
                    merged_count += 1
                    merged_any = True
                    break

        self._refresh_calendar()
        self._refresh_dashboard()
        if merged_count > 0:
            self._set_process_status(f"Auto condense complete ({merged_count} merged).", auto_clear_ms=2200)
            messagebox.showinfo("RootRecord Business Manager", f"Auto condense complete. Merged {merged_count} entries.")
        else:
            self._set_process_status("Auto condense: no eligible entries.", auto_clear_ms=1800)
            messagebox.showinfo("RootRecord Business Manager", "No eligible adjacent entries to condense.")

    def _open_bulk_edit_dialog(self) -> None:
        rows: list[dict] = getattr(self, "_calendar_rows", [])
        if not rows:
            messagebox.showinfo("RootRecord Business Manager", "No entries loaded for this day.")
            return

        # Same stack as Check-in: tk.Toplevel + tk widgets. If anything raises before cv.pack(),
        # only the title line appears — so pack the list shell first, harden row formatting, and surface errors.
        top: tk.Toplevel | None = None
        try:
            m = self._tk_modal_theme()
            top = tk.Toplevel(self)
            top.title("Bulk Edit Entries")
            top.configure(bg=m["root_bg"])
            self._apply_window_icon(top)
            top.transient(self)
            try:
                top.withdraw()
            except Exception:
                pass

            def close_bulk() -> None:
                self._safe_destroy_window(top)

            top.protocol("WM_DELETE_WINDOW", close_bulk)
            cb_style = self._tk_modal_configure_combobox_style(top, "RRBulkEdit.TCombobox", m)
            e_kw: dict[str, Any] = {
                "bg": m["ent_bg"],
                "fg": m["lbl_fg"],
                "insertbackground": m["lbl_fg"],
                "relief": "flat",
                "font": m["font_entry"],
                "width": 8,
                "highlightthickness": 1,
                "highlightbackground": m["ent_hl"],
                "highlightcolor": m["ent_hl"],
            }

            body = tk.Frame(top, bg=m["panel_bg"])
            # Height follows content; avoid stretching to a tall default window (empty band below buttons).
            body.pack(fill="both", expand=False)

            tk.Label(
                body,
                text="Select entries, then apply one or more actions at once.",
                bg=m["panel_bg"],
                fg=m["heading"],
                font=m["font_title"],
                wraplength=920,
                justify=tk.LEFT,
            ).pack(anchor="nw", fill="x", padx=14, pady=(12, 8))

            list_host = tk.Frame(body, bg=m["panel_bg"])
            list_host.pack(fill="x", padx=12, pady=6)
            list_host.pack_propagate(False)
            line_px, max_vis = 28, 14
            host_h = min(380, max(120, min(len(rows), max_vis) * line_px + 12))
            list_host.configure(height=host_h)

            # Default ttk vertical scrollbar only — custom style names like "*.VScroll" omit the
            # Vertical.TScrollbar layout on Windows and raise "Layout ... not found".
            cv = tk.Canvas(list_host, bg=m["ent_bg"], highlightthickness=0, bd=0, height=host_h)
            inner = tk.Frame(cv, bg=m["ent_bg"])
            inner_id = cv.create_window((0, 0), window=inner, anchor="nw", width=880)

            def sync_scroll(_e: Any = None) -> None:
                try:
                    bb = cv.bbox("all")
                    if bb is not None:
                        cv.configure(scrollregion=bb)
                    w = max(240, int(cv.winfo_width()) - 8)
                    cv.itemconfigure(inner_id, width=w)
                except tk.TclError:
                    pass

            inner.bind("<Configure>", lambda _e: sync_scroll())

            def on_cv_cfg(e: Any) -> None:
                try:
                    cv.itemconfigure(inner_id, width=max(240, int(e.width) - 8))
                except tk.TclError:
                    pass

            cv.bind("<Configure>", on_cv_cfg)

            def on_wheel(e: Any) -> None:
                try:
                    cv.yview_scroll(int(-1 * (e.delta / 120)), "units")
                except tk.TclError:
                    pass

            cv.bind("<MouseWheel>", on_wheel)
            inner.bind("<MouseWheel>", on_wheel)

            sb = ttk.Scrollbar(list_host, orient="vertical", command=cv.yview)
            cv.configure(yscrollcommand=sb.set)
            cv.pack(side="left", fill="both", expand=True, padx=(0, 2))
            sb.pack(side="right", fill="y")

            self._bulk_listbox = None
            self._bulk_row_checks: list[tuple[int, tk.IntVar]] = []
            for r in rows:
                rid = int(r["id"])
                sel = tk.IntVar(master=top, value=0)
                self._bulk_row_checks.append((rid, sel))
                cat_raw = r.get("category_name")
                if cat_raw is None:
                    cat_raw = r.get("category")
                cat_disp = str(cat_raw if cat_raw is not None else "").strip()
                label10 = (cat_disp or "—")[:10]
                desc_disp = str(r.get("description") or "")
                line = (
                    f"ID {rid}  {str(r.get('start_utc', ''))[:16]} -> {str(r.get('end_utc', ''))[:16]}  "
                    f"{label10:<10}  {desc_disp}"
                )
                if len(line) > 260:
                    line = line[:257] + "..."
                rf = tk.Frame(inner, bg=m["ent_bg"])
                rf.pack(fill="x", padx=2, pady=1)
                tk.Checkbutton(
                    rf,
                    variable=sel,
                    onvalue=1,
                    offvalue=0,
                    bg=m["ent_bg"],
                    fg=m["lbl_fg"],
                    font=m["font_lbl"],
                    selectcolor=m["accent"],
                    activebackground=m["ent_bg"],
                    activeforeground=m["lbl_fg"],
                    highlightthickness=0,
                ).pack(side="left", padx=(6, 4))
                tk.Label(rf, text=line, anchor="w", bg=m["ent_bg"], fg=m["lbl_fg"], font=m["font_lbl"]).pack(
                    side="left", fill="x", expand=True
                )

            top.after(30, sync_scroll)

            act1 = tk.Frame(body, bg=m["panel_bg"])
            act1.pack(fill="x", padx=12, pady=(8, 4))
            self._bulk_delete = tk.IntVar(master=top, value=0)
            tk.Checkbutton(
                act1,
                text="Delete selected",
                variable=self._bulk_delete,
                onvalue=1,
                offvalue=0,
                bg=m["panel_bg"],
                fg=m["lbl_fg"],
                font=m["font_lbl"],
                selectcolor=m["ent_bg"],
                activebackground=m["panel_bg"],
                activeforeground=m["lbl_fg"],
            ).pack(side="left", padx=6)
            self._bulk_merge = tk.IntVar(master=top, value=0)
            tk.Checkbutton(
                act1,
                text="Merge adjacent selected (same description/category/project)",
                variable=self._bulk_merge,
                onvalue=1,
                offvalue=0,
                bg=m["panel_bg"],
                fg=m["lbl_fg"],
                font=m["font_lbl"],
                selectcolor=m["ent_bg"],
                activebackground=m["panel_bg"],
                activeforeground=m["lbl_fg"],
            ).pack(side="left", padx=10)
            tk.Label(act1, text="Shift minutes", bg=m["panel_bg"], fg=m["lbl_fg"], font=m["font_lbl"]).pack(
                side="left", padx=(10, 4)
            )
            self._bulk_shift_min_str = tk.StringVar(master=top, value="0")
            tk.Entry(act1, textvariable=self._bulk_shift_min_str, **e_kw).pack(side="left", padx=4, ipady=3)

            def bump_shift(delta: int) -> None:
                try:
                    cur = int(self._bulk_shift_min_str.get().strip() or "0")
                except ValueError:
                    cur = 0
                self._bulk_shift_min_str.set(str(cur + delta))

            tk.Button(
                act1,
                text="-15m",
                font=m["font_btn"],
                bg=m["accent"],
                fg="#ffffff",
                activebackground=m["accent"],
                padx=8,
                pady=4,
                relief="flat",
                command=lambda: bump_shift(-15),
            ).pack(side="left", padx=2)
            tk.Button(
                act1,
                text="+15m",
                font=m["font_btn"],
                bg=m["accent"],
                fg="#ffffff",
                activebackground=m["accent"],
                padx=8,
                pady=4,
                relief="flat",
                command=lambda: bump_shift(15),
            ).pack(side="left", padx=2)

            cat_row = tk.Frame(body, bg=m["panel_bg"])
            cat_row.pack(fill="x", padx=12, pady=(4, 4))
            proj_row = tk.Frame(body, bg=m["panel_bg"])
            proj_row.pack(fill="x", padx=12, pady=(2, 8))
            cats = list_work_categories(self.cfg, self.uid)
            cat_map: dict[str, int | None] = {"(keep current)": None}
            for c in cats:
                cat_map[c["name"]] = int(c["id"])
            prows = list_projects(self.cfg, self.uid)
            proj_map: dict[str, int | None] = {"(keep current)": None}
            for p in prows:
                proj_map[p["name"]] = int(p["id"])
            self._bulk_cat_map = cat_map
            self._bulk_proj_map = proj_map

            tk.Label(cat_row, text="Set category", bg=m["panel_bg"], fg=m["lbl_fg"], font=m["font_lbl"]).pack(
                side="left", padx=(6, 8)
            )
            self._bulk_cat_cmb = ttk.Combobox(
                cat_row, values=list(cat_map.keys()), width=36, state="readonly", style=cb_style
            )
            self._bulk_cat_cmb.set("(keep current)")
            self._bulk_cat_cmb.pack(side="left", padx=4, fill="x", expand=True)
            tk.Button(
                cat_row,
                text="Add",
                font=m["font_btn"],
                bg=m["accent"],
                fg="#ffffff",
                activebackground=m["accent"],
                padx=12,
                pady=4,
                relief="flat",
                command=lambda: self._bulk_add_category(cats, cat_map),
            ).pack(side="left", padx=6)

            tk.Label(proj_row, text="Set project", bg=m["panel_bg"], fg=m["lbl_fg"], font=m["font_lbl"]).pack(
                side="left", padx=(6, 8)
            )
            self._bulk_proj_cmb = ttk.Combobox(
                proj_row, values=list(proj_map.keys()), width=44, state="readonly", style=cb_style
            )
            self._bulk_proj_cmb.set("(keep current)")
            self._bulk_proj_cmb.pack(side="left", padx=4, fill="x", expand=True)

            def apply_bulk() -> None:
                if self._selected_range_locked():
                    messagebox.showerror("RootRecord Business Manager", "Selected report range is locked.")
                    return
                checks = getattr(self, "_bulk_row_checks", None)
                if not checks:
                    messagebox.showerror("RootRecord Business Manager", "Entry list is not available.")
                    return
                selected_ids = [rid for rid, v in checks if int(v.get() or 0) == 1]
                if not selected_ids:
                    messagebox.showerror(
                        "RootRecord Business Manager",
                        "Tick at least one entry in the list above, then choose your actions and click Apply.",
                    )
                    return
                rows_by_id = {int(r["id"]): r for r in rows}
                selected_rows = [rows_by_id[rid] for rid in selected_ids if rid in rows_by_id]
                selected_rows.sort(key=lambda x: str(x.get("start_utc", "")))
                delete_ids: set[int] = set()
                changed = 0

                cat_choice = self._bulk_cat_cmb.get()
                proj_choice = self._bulk_proj_cmb.get()
                set_cat = self._bulk_cat_map.get(cat_choice)
                set_proj = self._bulk_proj_map.get(proj_choice)
                apply_cat = cat_choice != "(keep current)"
                apply_proj = proj_choice != "(keep current)"
                try:
                    shift_min = int(getattr(self, "_bulk_shift_min_str", None).get().strip() or "0")
                except (ValueError, AttributeError, tk.TclError):
                    shift_min = 0

                for r in selected_rows:
                    rid = int(r["id"])
                    if rid in delete_ids:
                        continue
                    if self._bulk_delete.get() == 1:
                        insert_time_entry_audit(
                            self.cfg, self.uid, entry_id=rid, action="bulk_delete", old_row=r, new_row=None
                        )
                        if delete_time_entry(self.cfg, self.uid, rid):
                            changed += 1
                        continue
                    next_start = str(r["start_utc"])
                    next_end = str(r["end_utc"])
                    if shift_min != 0:
                        try:
                            next_start = (datetime.fromisoformat(next_start) + timedelta(minutes=shift_min)).isoformat()
                            next_end = (datetime.fromisoformat(next_end) + timedelta(minutes=shift_min)).isoformat()
                        except ValueError:
                            pass
                    if apply_cat or apply_proj:
                        ok = update_time_entry(
                            self.cfg,
                            self.uid,
                            rid,
                            start_utc=next_start,
                            end_utc=next_end,
                            description=str(r["description"]),
                            work_category_id=set_cat if apply_cat else r.get("work_category_id"),
                            project_id=set_proj if apply_proj else r.get("project_id"),
                        )
                        if ok:
                            new_r = dict(r)
                            new_r["start_utc"] = next_start
                            new_r["end_utc"] = next_end
                            new_r["work_category_id"] = set_cat if apply_cat else r.get("work_category_id")
                            new_r["project_id"] = set_proj if apply_proj else r.get("project_id")
                            insert_time_entry_audit(
                                self.cfg, self.uid, entry_id=rid, action="bulk_update", old_row=r, new_row=new_r
                            )
                            changed += 1
                    elif shift_min != 0:
                        ok = update_time_entry(
                            self.cfg,
                            self.uid,
                            rid,
                            start_utc=next_start,
                            end_utc=next_end,
                            description=str(r["description"]),
                            work_category_id=r.get("work_category_id"),
                            project_id=r.get("project_id"),
                        )
                        if ok:
                            new_r = dict(r)
                            new_r["start_utc"] = next_start
                            new_r["end_utc"] = next_end
                            insert_time_entry_audit(
                                self.cfg, self.uid, entry_id=rid, action="bulk_shift", old_row=r, new_row=new_r
                            )
                            changed += 1

                if self._bulk_merge.get() == 1 and self._bulk_delete.get() == 0:
                    i = 0
                    while i < len(selected_rows) - 1:
                        base = selected_rows[i]
                        if int(base["id"]) in delete_ids:
                            i += 1
                            continue
                        j = i + 1
                        end_utc = str(base["end_utc"])
                        while j < len(selected_rows):
                            nxt = selected_rows[j]
                            if int(nxt["id"]) in delete_ids:
                                j += 1
                                continue
                            contiguous = str(nxt["start_utc"]) == end_utc
                            same_meta = (
                                str(nxt.get("description", "")) == str(base.get("description", ""))
                                and nxt.get("work_category_id") == base.get("work_category_id")
                                and nxt.get("project_id") == base.get("project_id")
                            )
                            if not (contiguous and same_meta):
                                break
                            end_utc = str(nxt["end_utc"])
                            delete_ids.add(int(nxt["id"]))
                            j += 1
                        if end_utc != str(base["end_utc"]):
                            ok = update_time_entry(
                                self.cfg,
                                self.uid,
                                int(base["id"]),
                                start_utc=str(base["start_utc"]),
                                end_utc=end_utc,
                                description=str(base["description"]),
                                work_category_id=base.get("work_category_id"),
                                project_id=base.get("project_id"),
                            )
                            if ok:
                                changed += 1
                        i = j
                    for rid in delete_ids:
                        if delete_time_entry(self.cfg, self.uid, rid):
                            changed += 1

                self._refresh_calendar()
                self._refresh_dashboard()
                messagebox.showinfo("RootRecord Business Manager", f"Bulk edit complete. Updated {changed} records.")
                close_bulk()

            btn_bar = tk.Frame(body, bg=m["panel_bg"])
            btn_bar.pack(fill="x", padx=14, pady=(4, 14))
            tk.Button(
                btn_bar,
                text="Cancel",
                font=m["font_btn"],
                bg="#4a4a4a",
                fg="#f0f0f0",
                activebackground="#3d3d3d",
                relief="flat",
                padx=22,
                pady=10,
                command=close_bulk,
            ).pack(side="right")
            tk.Button(
                btn_bar,
                text="Apply Bulk Edit",
                font=("Segoe UI", 13, "bold"),
                bg=m["accent"],
                fg="#f8f8f8",
                activebackground=m["accent"],
                activeforeground="#ffffff",
                relief="flat",
                padx=22,
                pady=10,
                command=apply_bulk,
            ).pack(side="right", padx=(0, 12))

            try:
                top.update_idletasks()
                win_w = max(920, min(1280, top.winfo_reqwidth()))
                win_h = max(340, min(900, top.winfo_reqheight()))
                if win_h < 280:
                    win_h = max(340, min(900, 72 + host_h + 260))
                top.geometry(f"{win_w}x{win_h}")
                top.minsize(720, 320)
                try:
                    top.deiconify()
                except Exception:
                    pass
                top.lift()
                top.focus_force()
            except Exception:
                pass
            try:
                top.grab_set()
            except Exception:
                pass

        except Exception as exc:
            import traceback

            traceback.print_exc()
            if top is not None:
                try:
                    self._safe_destroy_window(top)
                except Exception:
                    pass
            messagebox.showerror(
                "RootRecord Business Manager",
                f"Bulk Edit could not open:\n\n{exc!s}",
            )
            return

    def _bulk_add_category(self, cats: list[dict], cat_map: dict[str, int | None]) -> None:
        name = self._ask_text_dialog(
            title="Add Category",
            prompt="Category name",
            placeholder="Enter category name",
            ok_text="Add Category",
        )
        if not name:
            return
        clean = name.strip()
        if not clean:
            return
        upsert_work_category(self.cfg, self.uid, clean)
        refreshed = list_work_categories(self.cfg, self.uid)
        cat_map.clear()
        cat_map["(keep current)"] = None
        for c in refreshed:
            cat_map[c["name"]] = int(c["id"])
        self._bulk_cat_map = cat_map
        self._combo_apply_values(self._bulk_cat_cmb, list(cat_map.keys()))
        self._bulk_cat_cmb.set(clean)

    def _build_about(self) -> None:
        t = self.tabview.tab("About")
        body = ctk.CTkFrame(t, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)

        head = ctk.CTkFrame(body, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")

        ctk.CTkLabel(
            head,
            text="RootRecord — Monitoring, automation, and data services",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", pady=(0, 2))
        ctk.CTkLabel(
            head,
            text=f"Version {APP_VERSION}",
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(0, 6))

        content = ctk.CTkFrame(body, fg_color="transparent")
        content.grid(row=2, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=4)
        content.grid_columnconfigure(1, weight=3)
        content.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(content, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = ctk.CTkFrame(content, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        about_text = (
            "Your grounding root for productivity, operations, and real-world data.\n\n"
            "Purpose:\n"
            "RootRecord is built to make complex environments easier to operate in one calm workspace.\n\n"
            "Principles:\n"
            "- Reliability first\n"
            "- Clarity over cleverness\n"
            "- Operability in the real world\n"
            "- Composable services\n"
            "- Respectful communication\n\n"
            "Services (modular): monitoring, automation, alerts, weather intelligence,\n"
            "energy awareness, notes/records, AI-assisted reporting, and community touchpoints.\n\n"
            "Contact channels:\n"
            "- X (Twitter): @RootRecord\n"
            "- Telegram Bot: @RootRecord_Bot\n"
            "- Community: Telegram groups\n\n"
            "Policies:\n"
            "Use the buttons below to open Terms of Service and Privacy Policy."
        )
        box = ctk.CTkTextbox(left, font=ctk.CTkFont(size=12))
        box.pack(fill="both", expand=True, pady=(0, 8))
        box.insert("0.0", about_text)
        box.configure(state="disabled")

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.grid(row=3, column=0, sticky="w", pady=(8, 0))
        ctk.CTkButton(row, text="Open Website", command=lambda: webbrowser.open("https://rootrecord.info/")).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            row,
            text="Terms of Service",
            command=lambda: webbrowser.open("https://rootrecord.info/terms"),
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            row,
            text="Privacy Policy",
            command=lambda: webbrowser.open("https://rootrecord.info/privacy"),
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            row,
            text="Contact",
            command=lambda: webbrowser.open("https://rootrecord.info/contact"),
        ).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Check for updates", command=self._manual_check_for_updates).pack(side="left", padx=4)

        # Right-side About panel graphic.
        self._pack_about_side_image(right)

    def _build_plugins(self) -> None:
        t = self.tabview.tab("Plugins")
        self._plugins_pages = ctk.CTkTabview(t)
        self._plugins_pages.pack(fill="both", expand=True)
        self._plugins_pages.add("Catalog")
        self._plugins_pages.add("Energy Management")
        catalog_tab = self._plugins_pages.tab("Catalog")

        ctk.CTkLabel(
            catalog_tab,
            text="Plugins",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", pady=(4, 6))
        ctk.CTkLabel(
            catalog_tab,
            text=(
                "Plugins are published at rootrecord.info/plugins. Installed plugins appear on this page."
            ),
            text_color=self._theme_text_muted,
            wraplength=820,
            justify="left",
        ).pack(anchor="w", pady=(0, 10))
        ctk.CTkButton(
            catalog_tab,
            text="Browse Plugins",
            width=160,
            command=lambda: webbrowser.open("https://rootrecord.info/plugins"),
        ).pack(anchor="w", pady=(0, 8))

        self._plugin_rows = ctk.CTkScrollableFrame(catalog_tab, height=300)
        self._plugin_rows.pack(fill="x", pady=6)

        actions = ctk.CTkFrame(catalog_tab, fg_color="transparent")
        actions.pack(fill="x", pady=(4, 8))
        ctk.CTkButton(
            actions,
            text="Open Plugin Folder",
            width=170,
            command=self._open_plugin_folder,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            actions,
            text="Save Plugin Preferences",
            width=220,
            command=self._save_plugin_preferences,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Refresh Catalog", width=140, command=self._refresh_plugins_status).pack(side="left")

        status_card = ctk.CTkFrame(catalog_tab)
        status_card.pack(fill="both", expand=True, pady=(6, 4))
        ctk.CTkLabel(
            status_card,
            text="Plugin status",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=10, pady=(10, 6))
        self._plugins_status = ctk.CTkTextbox(status_card, height=170, font=ctk.CTkFont(family="Consolas", size=11))
        self._plugins_status.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._build_energy_management_page(self._plugins_pages.tab("Energy Management"))
        self._refresh_plugins_status()
        self._refresh_energy_management()

    def _build_energy_management_page(self, tab: ctk.CTkFrame) -> None:
        ctk.CTkLabel(
            tab,
            text="Energy Management",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", pady=(6, 6))
        ctk.CTkLabel(
            tab,
            text="Live EcoFlow telemetry trends from Power Monitoring snapshots.",
            text_color=self._theme_text_muted,
            wraplength=820,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        controls = ctk.CTkFrame(tab, fg_color="transparent")
        controls.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(
            controls,
            text="Refresh Graphs",
            width=140,
            command=self._refresh_energy_management,
        ).pack(side="left")
        self._energy_status = ctk.CTkLabel(controls, text="", text_color=self._theme_text_muted)
        self._energy_status.pack(side="left", padx=(12, 0))
        self._energy_tabs = ctk.CTkTabview(tab)
        self._energy_tabs.pack(fill="both", expand=True, pady=(4, 4))
        self._energy_tabs.add("Overview")
        self._energy_overview_host = ctk.CTkFrame(self._energy_tabs.tab("Overview"))
        self._energy_overview_host.pack(fill="both", expand=True)
        self._energy_overview_canvas = None
        self._energy_device_hosts: dict[str, ctk.CTkFrame] = {}
        self._energy_device_canvases: dict[str, Any] = {}

    def _refresh_energy_management(self) -> None:
        host = getattr(self, "_energy_overview_host", None)
        status = getattr(self, "_energy_status", None)
        available = getattr(self, "_available_plugin_ids", set())
        if host is None or status is None:
            return
        if "power_monitoring" not in available:
            status.configure(text="Install/enable Power Monitoring plugin to view graphs.")
            return
        rows = list_power_buckets_recent(self.cfg, self.uid, limit=180)
        if not rows:
            status.configure(text="No aggregated power data yet.")
            return
        serials = sorted({str(r.get("device_id") or "").strip() for r in rows if str(r.get("device_id") or "").strip()})
        status.configure(text=f"Loaded {len(rows)} buckets across {len(serials)} device(s)")
        if not _DASHBOARD_CHARTS_AVAILABLE or Figure is None or FigureCanvasTkAgg is None:
            return
        self._sync_energy_device_tabs(serials)
        self._render_energy_overview(rows)
        for serial in serials:
            self._render_energy_device_page(serial, [r for r in rows if str(r.get("device_id") or "").strip() == serial])

    def _sync_energy_device_tabs(self, serials: list[str]) -> None:
        tabs = getattr(self, "_energy_tabs", None)
        if tabs is None:
            return
        existing = set(getattr(self, "_energy_device_hosts", {}).keys())
        wanted = set(serials)
        for serial in sorted(wanted - existing):
            tabs.add(serial)
            frame = ctk.CTkFrame(tabs.tab(serial))
            frame.pack(fill="both", expand=True)
            self._energy_device_hosts[serial] = frame
        for serial in sorted(existing - wanted):
            try:
                tabs.delete(serial)
            except Exception:
                pass
            self._energy_device_hosts.pop(serial, None)
            canvas = self._energy_device_canvases.pop(serial, None)
            if canvas is not None:
                try:
                    canvas.get_tk_widget().destroy()
                except Exception:
                    pass

    def _render_energy_overview(self, rows: list[dict[str, Any]]) -> None:
        host = getattr(self, "_energy_overview_host", None)
        if host is None:
            return
        if getattr(self, "_energy_overview_canvas", None) is not None:
            try:
                self._energy_overview_canvas.get_tk_widget().destroy()
            except Exception:
                pass
        fig = Figure(figsize=(9.2, 7.2), dpi=100)
        ax_batt = fig.add_subplot(411)
        ax_ac = fig.add_subplot(412, sharex=ax_batt)
        ax_dc = fig.add_subplot(413, sharex=ax_batt)
        ax_out = fig.add_subplot(414, sharex=ax_batt)
        series = self._build_bucket_series(rows)
        for serial, vals in series.items():
            label = serial if serial else "(unknown)"
            times = vals["times"]
            ax_batt.plot(times, vals["battery"], linewidth=1.2, alpha=0.85, label=label)
            ax_ac.plot(times, vals["ac_in"], linewidth=1.2, alpha=0.85, label=label)
            ax_dc.plot(times, vals["dc_in"], linewidth=1.2, alpha=0.85, label=label)
            ax_out.plot(times, vals["out"], linewidth=1.2, alpha=0.85, label=label)
        merged = self._build_merged_series(rows)
        ax_batt.plot(merged["times"], merged["battery"], color="#ffffff", linewidth=2.0, label="Merged")
        ax_ac.plot(merged["times"], merged["ac_in"], color="#ffffff", linewidth=2.0, label="Merged")
        ax_dc.plot(merged["times"], merged["dc_in"], color="#ffffff", linewidth=2.0, label="Merged")
        ax_out.plot(merged["times"], merged["out"], color="#ffffff", linewidth=2.0, label="Merged")
        ax_batt.set_title("EcoFlow Overview (per device + merged)")
        ax_batt.set_ylabel("Battery %")
        ax_batt.set_ylim(0, 100)
        ax_ac.set_ylabel("AC In W")
        ax_dc.set_ylabel("DC/Solar In W")
        ax_out.set_ylabel("Out W")
        for ax in (ax_batt, ax_ac, ax_dc, ax_out):
            ax.grid(alpha=0.22)
        ax_batt.legend(loc="upper right", fontsize=7, ncol=2)
        fig.autofmt_xdate()
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=host)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=8)
        self._energy_overview_canvas = canvas

    def _render_energy_device_page(self, serial: str, rows: list[dict[str, Any]]) -> None:
        host = self._energy_device_hosts.get(serial)
        if host is None:
            return
        old = self._energy_device_canvases.get(serial)
        if old is not None:
            try:
                old.get_tk_widget().destroy()
            except Exception:
                pass
        fig = Figure(figsize=(9.2, 7.2), dpi=100)
        ax_batt = fig.add_subplot(411)
        ax_ac = fig.add_subplot(412, sharex=ax_batt)
        ax_dc = fig.add_subplot(413, sharex=ax_batt)
        ax_out = fig.add_subplot(414, sharex=ax_batt)
        vals = self._build_single_series(rows)
        ax_batt.plot(vals["times"], vals["battery"], color="#4dabf7", linewidth=1.6)
        ax_ac.plot(vals["times"], vals["ac_in"], color="#40c057", linewidth=1.6)
        ax_dc.plot(vals["times"], vals["dc_in"], color="#15aabf", linewidth=1.6)
        ax_out.plot(vals["times"], vals["out"], color="#f08c00", linewidth=1.6)
        ax_batt.set_title(f"Device {serial}")
        ax_batt.set_ylabel("Battery %")
        ax_batt.set_ylim(0, 100)
        ax_ac.set_ylabel("AC In W")
        ax_dc.set_ylabel("DC/Solar In W")
        ax_out.set_ylabel("Out W")
        for ax in (ax_batt, ax_ac, ax_dc, ax_out):
            ax.grid(alpha=0.22)
        fig.autofmt_xdate()
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=host)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=8)
        self._energy_device_canvases[serial] = canvas

    def _build_bucket_series(self, rows: list[dict[str, Any]]) -> dict[str, dict[str, list[Any]]]:
        out: dict[str, dict[str, list[Any]]] = {}
        by_serial: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            serial = str(r.get("device_id") or "").strip()
            by_serial.setdefault(serial, []).append(r)
        for serial, rr in by_serial.items():
            out[serial] = self._build_single_series(rr)
        return out

    def _build_single_series(self, rows: list[dict[str, Any]]) -> dict[str, list[Any]]:
        times: list[datetime] = []
        battery: list[float] = []
        ac_in: list[float] = []
        dc_in: list[float] = []
        out_w: list[float] = []
        for r in sorted(rows, key=lambda x: str(x.get("bucket_start_utc") or "")):
            try:
                times.append(datetime.fromisoformat(str(r.get("bucket_start_utc") or "")))
            except ValueError:
                continue
            battery.append(float(r["avg_battery_pct"]) if r.get("avg_battery_pct") is not None else float("nan"))
            ac_in.append(float(r["avg_ac_input_watts"]) if r.get("avg_ac_input_watts") is not None else float("nan"))
            dc_in.append(float(r["avg_dc_input_watts"]) if r.get("avg_dc_input_watts") is not None else float("nan"))
            out_w.append(float(r["avg_output_watts"]) if r.get("avg_output_watts") is not None else float("nan"))
        return {"times": times, "battery": battery, "ac_in": ac_in, "dc_in": dc_in, "out": out_w}

    def _build_merged_series(self, rows: list[dict[str, Any]]) -> dict[str, list[Any]]:
        points: dict[str, dict[str, list[float]]] = {}
        for r in rows:
            k = str(r.get("bucket_start_utc") or "")
            d = points.setdefault(k, {"b": [], "ac": [], "dc": [], "out": []})
            if r.get("avg_battery_pct") is not None:
                d["b"].append(float(r["avg_battery_pct"]))
            if r.get("avg_ac_input_watts") is not None:
                d["ac"].append(float(r["avg_ac_input_watts"]))
            if r.get("avg_dc_input_watts") is not None:
                d["dc"].append(float(r["avg_dc_input_watts"]))
            if r.get("avg_output_watts") is not None:
                d["out"].append(float(r["avg_output_watts"]))
        times: list[datetime] = []
        batt: list[float] = []
        ac: list[float] = []
        dc: list[float] = []
        out_w: list[float] = []
        for k in sorted(points.keys()):
            try:
                times.append(datetime.fromisoformat(k))
            except ValueError:
                continue
            d = points[k]
            batt.append((sum(d["b"]) / len(d["b"])) if d["b"] else float("nan"))
            ac.append(sum(d["ac"]) if d["ac"] else float("nan"))
            dc.append(sum(d["dc"]) if d["dc"] else float("nan"))
            out_w.append(sum(d["out"]) if d["out"] else float("nan"))
        return {"times": times, "battery": batt, "ac_in": ac, "dc_in": dc, "out": out_w}

    def _plugin_catalog(self) -> list[dict[str, str]]:
        items, errs = merged_plugin_catalog()
        self._plugin_scan_errors = errs
        return items

    def _plugin_enabled_key(self, plugin_id: str) -> str:
        return f"plugin_enabled_{plugin_id}"

    def _open_plugin_folder(self) -> None:
        plugin_dir = ensure_plugin_dir()
        try:
            os.startfile(str(plugin_dir))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("RootRecord Business Manager", f"Could not open plugin folder:\n{exc}")

    def _save_plugin_preferences(self) -> None:
        for pid, sw in getattr(self, "_plugin_switches", {}).items():
            enabled = sw.get() == 1
            settings_set(self.cfg, self._plugin_enabled_key(pid), enabled)
            if pid == "power_monitoring":
                plugin_setting_set(self.cfg, pid, "enabled", enabled)
                form = getattr(self, "_plugin_forms", {}).get(pid, {})
                vars_s = form.get("vars", {})
                if isinstance(vars_s, dict):
                    serial_widget = vars_s.get("device_serials_widget")
                    if serial_widget is not None:
                        try:
                            serial_text = str(serial_widget.get("0.0", "end")).strip()
                            plugin_setting_set(self.cfg, pid, "device_serials", serial_text)
                        except Exception:
                            pass
                    for key in (
                        "access_key",
                        "secret_key",
                        "selected_device_serial",
                    ):
                        var = vars_s.get(key)
                        if var is None:
                            continue
                        plugin_setting_set(self.cfg, pid, key, str(var.get()).strip())
            if pid == "usgs_earthquake_alerts":
                plugin_setting_set(self.cfg, pid, "enabled", enabled)
                form = getattr(self, "_plugin_forms", {}).get(pid, {})
                vars_s = form.get("vars", {})
                if isinstance(vars_s, dict):
                    for key in (
                        "poll_interval_sec",
                        "lat",
                        "lon",
                        "radius_miles",
                        "min_magnitude",
                        "quiet_start",
                        "quiet_end",
                    ):
                        var = vars_s.get(key)
                        if var is None:
                            continue
                        plugin_setting_set(self.cfg, pid, key, str(var.get()).strip())
        self._refresh_plugins_status()
        messagebox.showinfo("RootRecord Business Manager", "Plugin preferences saved.")

    def _refresh_plugins_status(self) -> None:
        rows = getattr(self, "_plugin_rows", None)
        if rows is not None:
            for w in rows.winfo_children():
                w.destroy()
        self._plugin_switches = {}
        self._plugin_forms: dict[str, dict[str, Any]] = {}
        catalog = self._plugin_catalog()
        self._available_plugin_ids = {str(p.get("id") or "").strip() for p in catalog}
        for plugin in catalog:
            pid = plugin["id"]
            row = ctk.CTkFrame(self._plugin_rows)
            row.pack(fill="x", padx=2, pady=4)
            ctk.CTkLabel(row, text=plugin["name"], font=ctk.CTkFont(size=13, weight="bold")).pack(
                anchor="w", padx=10, pady=(8, 2)
            )
            ctk.CTkLabel(
                row,
                text=f"ID: {pid}  |  Version: {plugin['version']}  |  Source: {plugin.get('source', 'builtin')}",
                text_color=self._theme_text_muted,
                font=ctk.CTkFont(size=11),
            ).pack(anchor="w", padx=10)
            ctk.CTkLabel(
                row,
                text=plugin["desc"],
                text_color=self._theme_text_muted,
                wraplength=760,
                justify="left",
            ).pack(anchor="w", padx=10, pady=(2, 6))
            sw = ctk.CTkSwitch(row, text="Enabled")
            enabled_val = bool(settings_get(self.cfg, self._plugin_enabled_key(pid), False))
            if pid == "power_monitoring":
                enabled_val = bool(plugin_setting_get(self.cfg, pid, "enabled", enabled_val))
            if pid == "usgs_earthquake_alerts":
                enabled_val = bool(plugin_setting_get(self.cfg, pid, "enabled", enabled_val))
            if enabled_val:
                sw.select()
            sw.pack(anchor="w", padx=10, pady=(0, 8))
            self._plugin_switches[pid] = sw
            if pid == "power_monitoring":
                self._render_power_monitoring_row(row, pid)
            if pid == "usgs_earthquake_alerts":
                self._render_usgs_row(row, pid)

        box = getattr(self, "_plugins_status", None)
        if box is None:
            return
        box.delete("0.0", "end")
        box.insert("end", "Enabled plugins\n")
        box.insert("end", "---------------\n")
        enabled = [p["name"] for p in catalog if bool(settings_get(self.cfg, self._plugin_enabled_key(p["id"]), False))]
        if not catalog:
            box.insert("end", "- No installed plugins detected.\n")
            box.insert("end", "- Install from rootrecord.info/plugins, then click Refresh Catalog.\n")
        elif not enabled:
            box.insert("end", "- none enabled\n")
        else:
            for label in enabled:
                box.insert("end", f"- {label}\n")
        errs = list(getattr(self, "_plugin_scan_errors", []) or [])
        if errs:
            box.insert("end", "\nManifest warnings:\n")
            for e in errs[:8]:
                box.insert("end", f"- {e}\n")
        box.insert(
            "end",
            "\nPlugin contract (target): manifest + settings schema + optional UI hooks + poll/worker hooks.",
        )
        pm = getattr(self, "_power_plugin", None)
        if pm is not None and "power_monitoring" in getattr(self, "_available_plugin_ids", set()):
            box.insert("end", f"\n\nPower runtime: {pm.runtime_status()}\n")
        up = getattr(self, "_usgs_plugin", None)
        if up is not None:
            box.insert("end", "\n")
            for line in up.status_lines():
                box.insert("end", line + "\n")

    def _render_power_monitoring_row(self, row: ctk.CTkFrame, plugin_id: str) -> None:
        frm = ctk.CTkFrame(row, fg_color="transparent")
        frm.pack(fill="x", padx=10, pady=(0, 8))
        frm.grid_columnconfigure(1, weight=1)
        defaults = {
            "access_key": "",
            "secret_key": "",
            "device_serials": "",
            "selected_device_serial": "__all__",
        }
        vars_s: dict[str, tk.StringVar] = {}
        for k, default in defaults.items():
            vars_s[k] = tk.StringVar(value=str(plugin_setting_get(self.cfg, plugin_id, k, default)))
        serial_values = [x.strip() for x in vars_s["device_serials"].get().replace(",", "\n").splitlines() if x.strip()]
        if vars_s["selected_device_serial"].get().strip() not in (["__all__"] + serial_values):
            vars_s["selected_device_serial"].set("__all__")

        fields = [
            ("EcoFlow API URL (fixed)", "__fixed_api_url__"),
            ("AccessKey", "access_key"),
            ("SecretKey", "secret_key"),
            ("Device serial numbers (one per line)", "device_serials"),
            ("Selected device", "selected_device_serial"),
            ("Poll interval", "__fixed_poll__"),
        ]
        for i, (label, key) in enumerate(fields):
            ctk.CTkLabel(frm, text=label, text_color=self._theme_text_muted).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            if key == "__fixed_api_url__":
                ctk.CTkLabel(frm, text="https://api.ecoflow.com", text_color=self._theme_text_muted).grid(
                    row=i, column=1, sticky="w", pady=2
                )
            elif key == "__fixed_poll__":
                ctk.CTkLabel(frm, text="5 seconds (fixed)", text_color=self._theme_text_muted).grid(
                    row=i, column=1, sticky="w", pady=2
                )
            elif key == "device_serials":
                box = ctk.CTkTextbox(frm, height=70, width=320)
                box.grid(row=i, column=1, sticky="ew", pady=2)
                box.insert("0.0", vars_s["device_serials"].get())
                vars_s["device_serials_widget"] = box  # type: ignore[assignment]
            elif key == "selected_device_serial":
                opts = ["__all__"] + serial_values if serial_values else ["__all__"]
                ctk.CTkOptionMenu(
                    frm,
                    variable=vars_s["selected_device_serial"],
                    values=opts,
                    width=320,
                ).grid(row=i, column=1, sticky="ew", pady=2)
            else:
                show = "*" if key == "secret_key" else None
                ctk.CTkEntry(frm, textvariable=vars_s[key], width=320, show=show).grid(
                    row=i, column=1, sticky="ew", pady=2
                )

        latest = self._power_plugin.latest_snapshot() if getattr(self, "_power_plugin", None) else None
        if latest:
            last_line = (
                f"Last poll: {format_stored_utc_as_local(self.cfg, str(latest.get('polled_at') or ''))} | "
                f"Battery: {latest.get('battery_pct') or '?'}% | "
                f"In: {latest.get('input_watts') or '?'}W | Out: {latest.get('output_watts') or '?'}W | "
                f"Result: {'ok' if int(latest.get('poll_ok') or 0) == 1 else 'error'}"
            )
            if latest.get("error_text"):
                last_line += f" ({str(latest.get('error_text'))[:80]})"
            ctk.CTkLabel(frm, text=last_line, text_color=self._theme_text_muted, wraplength=760, justify="left").grid(
                row=len(fields), column=0, columnspan=2, sticky="w", pady=(4, 0)
            )
        self._plugin_forms[plugin_id] = {"vars": vars_s}

    def _tick_plugins(self) -> None:
        try:
            available = getattr(self, "_available_plugin_ids", set())
            if getattr(self, "_power_plugin", None) is not None and "power_monitoring" in available:
                self._power_plugin.tick()
            if getattr(self, "_usgs_plugin", None) is not None and "usgs_earthquake_alerts" in available:
                alerts = self._usgs_plugin.tick()
                if alerts and not self._usgs_plugin.should_suppress_popup():
                    messagebox.showinfo(
                        "USGS Earthquake Alerts",
                        "\n".join(alerts[:3]) + ("\n..." if len(alerts) > 3 else ""),
                    )
            if self.tabview.get() == "Plugins":
                self._refresh_energy_management()
        except Exception:  # noqa: BLE001
            pass
        self.after(5000, self._tick_plugins)

    def _render_usgs_row(self, row: ctk.CTkFrame, plugin_id: str) -> None:
        frm = ctk.CTkFrame(row, fg_color="transparent")
        frm.pack(fill="x", padx=10, pady=(0, 8))
        frm.grid_columnconfigure(1, weight=1)
        defaults = {
            "poll_interval_sec": "300",
            "lat": "0.0",
            "lon": "0.0",
            "radius_miles": "100",
            "min_magnitude": "2.5",
            "quiet_start": "",
            "quiet_end": "",
        }
        vars_s: dict[str, tk.StringVar] = {
            k: tk.StringVar(value=str(plugin_setting_get(self.cfg, plugin_id, k, d)))
            for k, d in defaults.items()
        }
        fields = [
            ("Poll interval sec (min 60)", "poll_interval_sec"),
            ("Business latitude", "lat"),
            ("Business longitude", "lon"),
            ("Alert radius (miles)", "radius_miles"),
            ("Minimum magnitude", "min_magnitude"),
            ("Quiet start (HH:MM)", "quiet_start"),
            ("Quiet end (HH:MM)", "quiet_end"),
        ]
        for i, (label, key) in enumerate(fields):
            ctk.CTkLabel(frm, text=label, text_color=self._theme_text_muted).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            ctk.CTkEntry(frm, textvariable=vars_s[key], width=220).grid(row=i, column=1, sticky="ew", pady=2)

        action_row = ctk.CTkFrame(frm, fg_color="transparent")
        action_row.grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(5, 5))
        ctk.CTkButton(action_row, text="Test Alert", width=120, command=self._usgs_test_alert).pack(side="left", padx=(0, 8))

        recent = list_recent_usgs_quake_matches(self.cfg, limit=6)
        if recent:
            lines = ["Recent quake matches:"]
            for r in recent:
                when = format_stored_utc_as_local(self.cfg, str(r.get("event_time_utc") or ""))
                mag = r.get("magnitude")
                dist = r.get("distance_miles")
                place = str(r.get("place") or "")
                lines.append(f"- {when} | M{mag if mag is not None else '?'} | {dist:.1f}mi | {place}" if isinstance(dist, (int, float)) else f"- {when} | M{mag if mag is not None else '?'} | {place}")
            ctk.CTkLabel(frm, text="\n".join(lines), text_color=self._theme_text_muted, wraplength=760, justify="left").grid(
                row=len(fields) + 1, column=0, columnspan=2, sticky="w", pady=(3, 0)
            )
        self._plugin_forms[plugin_id] = {"vars": vars_s}

    def _usgs_test_alert(self) -> None:
        text = self._usgs_plugin.test_alert_text() if getattr(self, "_usgs_plugin", None) else "USGS test alert."
        test_id = f"test-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        insert_usgs_quake_event(
            self.cfg,
            event_id=test_id,
            event_time_utc=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            magnitude=3.2,
            place="Simulated nearby event",
            latitude=0.0,
            longitude=0.0,
            depth_km=5.0,
            detail_url="",
            raw_json='{"test":true}',
        )
        insert_usgs_quake_alert(
            self.cfg,
            event_id=test_id,
            distance_miles=0.0,
            rule_snapshot_json='{"test":true}',
            acknowledged=False,
        )
        messagebox.showinfo("USGS Earthquake Alerts", text)

    def _refresh_settings_views(self) -> None:
        if getattr(self, "_set_multi_business_support", None):
            if bool(settings_get(self.cfg, "multi_business_enabled", False)):
                self._set_multi_business_support.select()
            else:
                self._set_multi_business_support.deselect()
        if getattr(self, "_set_currency_safe", None):
            if bool(settings_get(self.cfg, "currency_safe_summaries_enabled", False)):
                self._set_currency_safe.select()
            else:
                self._set_currency_safe.deselect()
        if getattr(self, "_set_help_bubbles", None):
            if bool(settings_get(self.cfg, "help_bubbles_enabled", True)):
                self._set_help_bubbles.select()
            else:
                self._set_help_bubbles.deselect()
        if getattr(self, "_set_auto_sched_expenses", None):
            if bool(settings_get(self.cfg, "auto_post_scheduled_expenses_enabled", True)):
                self._set_auto_sched_expenses.select()
            else:
                self._set_auto_sched_expenses.deselect()
        if getattr(self, "_set_auto_debt_from_credit", None):
            if bool(settings_get(self.cfg, "auto_create_debt_for_credit_expenses_enabled", True)):
                self._set_auto_debt_from_credit.select()
            else:
                self._set_auto_debt_from_credit.deselect()
        if getattr(self, "_set_status_banner", None):
            if bool(settings_get(self.cfg, "show_process_status_banner_enabled", True)):
                self._set_status_banner.select()
            else:
                self._set_status_banner.deselect()
        if getattr(self, "_set_notify_debt_settlement", None):
            if bool(settings_get(self.cfg, "notify_on_debt_settlement_enabled", True)):
                self._set_notify_debt_settlement.select()
            else:
                self._set_notify_debt_settlement.deselect()
        if getattr(self, "_set_cloud_backup", None):
            if bool(settings_get(self.cfg, "cloud_backup_enabled", False)):
                self._set_cloud_backup.select()
            else:
                self._set_cloud_backup.deselect()
        if not bool(settings_get(self.cfg, "show_process_status_banner_enabled", True)):
            self._clear_process_status()
        self._refresh_help_bubbles_visibility()
        self._refresh_settings_defaults_reference()
        self._refresh_settings_reference_lists()

    def _refresh_settings_defaults_reference(self) -> None:
        """Full factory-default reference vs current app_settings (and seed catalog)."""
        if not getattr(self, "_settings_defaults_ref", None):
            return
        lines: list[str] = [
            "FACTORY DEFAULTS  →  compare with your stored values (* = different from factory)",
            "",
            f"{'Setting':<34} {'Yours':<18} {'Factory':<18}",
            "-" * 72,
        ]
        for key in sorted(FACTORY_APP_SETTINGS_DEFAULTS.keys()):
            factory = FACTORY_APP_SETTINGS_DEFAULTS[key]
            current = settings_get(self.cfg, key, factory)
            c_s = _fmt_setting_display_value(current)
            f_s = _fmt_setting_display_value(factory)
            mark = "" if _settings_value_matches_factory(current, factory) else " *"
            lines.append(f"{key[:33]:<34} {c_s:<18} {f_s:<18}{mark}")
        lines.extend(
            [
                "",
                f"Database file: {self.cfg.db_path}",
                "",
                "TIME CATEGORIES (seeded when you had none; migration 5 adds missing names)",
                "-" * 72,
            ]
        )
        for name, color, _icon, billable, _rate, _so in STD_TIME_CATEGORIES:
            bil = "billable" if billable else "non-bill"
            lines.append(f"  • {name}  {color}  ({bil})")
        lines.extend(
            [
                "",
                "QUICK ACTIONS (seeded only when quick_actions was empty for your user)",
                "-" * 72,
            ]
        )
        for lab, desc in FACTORY_QUICK_ACTION_SEEDS:
            lines.append(f"  • {lab}  →  {desc}")
        lines.extend(
            [
                "",
                "PROJECTS — none are seeded; add from below or check-in prompts / manual entries.",
                "",
                "Live rows in your database (current app) are listed under Categories / Projects /",
                "Quick actions further down this page.",
            ]
        )
        self._settings_defaults_ref.configure(state="normal")
        self._settings_defaults_ref.delete("0.0", "end")
        self._settings_defaults_ref.insert("0.0", "\n".join(lines) + "\n")
        self._settings_defaults_ref.configure(state="disabled")

    def _refresh_settings_reference_lists(self) -> None:
        """Fill read-only lists on Settings with DB-backed categories, projects, quick actions."""
        if not getattr(self, "_settings_cat_list", None):
            return

        self._settings_cat_list.configure(state="normal")
        self._settings_cat_list.delete("0.0", "end")
        cats = list_work_categories(self.cfg, self.uid)
        self._settings_cats_rows = cats
        if not cats:
            self._settings_cat_list.insert(
                "0.0",
                "(No categories found. Fully quit and reopen the app so migrations can create defaults.)\n",
            )
        else:
            for c in cats:
                cid = int(c.get("id") or 0)
                nm = str(c.get("name") or "")
                col = str(c.get("color") or "")
                bil = "billable" if int(c.get("billable") or 0) else "non-bill"
                self._settings_cat_list.insert("end", f"• #{cid:<3} {nm:<20} {col:<8} ({bil})\n")
        self._settings_cat_list.configure(state="disabled")
        if getattr(self, "_edit_cat_pick", None):
            names = [str(c.get("name") or "") for c in cats]
            self._edit_cat_pick.configure(values=names or ["—"])
            if names:
                cur = self._edit_cat_pick.get().strip()
                pick = cur if cur in names else names[0]
                self._edit_cat_pick.set(pick)
                self._load_selected_category_for_edit()
            else:
                self._edit_cat_pick.set("—")

        self._settings_proj_list.configure(state="normal")
        self._settings_proj_list.delete("0.0", "end")
        prows = list_projects(self.cfg, self.uid)
        if not prows:
            self._settings_proj_list.insert("0.0", "(No projects yet — add one below.)\n")
        else:
            for p in prows:
                cn = (p.get("client_name") or "").strip()
                line = f"• {p.get('name', '')}"
                if cn:
                    line += f"  —  client: {cn}"
                self._settings_proj_list.insert("end", line + "\n")
        self._settings_proj_list.configure(state="disabled")

        self._settings_qa_list.configure(state="normal")
        self._settings_qa_list.delete("0.0", "end")
        qrows = list_quick_actions(self.cfg, self.uid)
        if not qrows:
            self._settings_qa_list.insert(
                "0.0",
                "(No quick actions yet — starter buttons are added on first run when this list is empty.)\n",
            )
        else:
            for a in qrows:
                lab = str(a.get("label") or "")
                desc = (a.get("default_description") or "").strip()
                self._settings_qa_list.insert(
                    "end",
                    (f"• {lab}: {desc}\n" if desc else f"• {lab}\n"),
                )
        self._settings_qa_list.configure(state="disabled")

    def _load_selected_category_for_edit(self) -> None:
        if not getattr(self, "_edit_cat_pick", None):
            return
        name = self._edit_cat_pick.get().strip()
        rows = getattr(self, "_settings_cats_rows", [])
        row = next((c for c in rows if str(c.get("name") or "") == name), None)
        if not row:
            return
        self._edit_cat_name.delete(0, "end")
        self._edit_cat_name.insert(0, str(row.get("name") or ""))
        self._edit_cat_color.delete(0, "end")
        self._edit_cat_color.insert(0, str(row.get("color") or "#2B8A8F"))
        if int(row.get("billable") or 0):
            self._edit_cat_billable.select()
        else:
            self._edit_cat_billable.deselect()

    def _save_selected_category_edit(self) -> None:
        name = self._edit_cat_pick.get().strip()
        rows = getattr(self, "_settings_cats_rows", [])
        row = next((c for c in rows if str(c.get("name") or "") == name), None)
        if not row:
            messagebox.showerror("RootRecord Business Manager", "Pick a category to edit.")
            return
        new_name = self._edit_cat_name.get().strip()
        if not new_name:
            messagebox.showerror("RootRecord Business Manager", "Category name is required.")
            return
        new_color = self._normalize_hex_color(self._edit_cat_color.get(), "#2B8A8F")
        upsert_work_category(
            self.cfg,
            self.uid,
            new_name,
            color=new_color,
            kind=str(row.get("kind") or "time"),
            billable=1 if self._edit_cat_billable.get() == 1 else 0,
            default_hourly_cents=(int(row["default_hourly_cents"]) if row.get("default_hourly_cents") is not None else None),
            sort_order=int(row.get("sort_order") or 0),
            cid=int(row.get("id")),
        )
        self._refresh_settings_views()
        messagebox.showinfo("RootRecord Business Manager", f"Updated category '{new_name}'. Re-open Time tab to refresh dropdowns.")

    def _normalize_hex_color(self, raw: str, default: str = "#2B8A8F") -> str:
        s = (raw or "").strip()
        if not s:
            return default
        if not s.startswith("#"):
            s = f"#{s}"
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", s):
            return s.upper()
        return default

    def _pick_color_for_entry(self, entry: ctk.CTkEntry, fallback: str = "#2B8A8F") -> None:
        seed = self._normalize_hex_color(entry.get(), fallback)
        picked = colorchooser.askcolor(color=seed, title="Select color")
        if not picked or not picked[1]:
            return
        entry.delete(0, "end")
        entry.insert(0, str(picked[1]).upper())

    def _build_settings(self) -> None:
        t = self.tabview.tab("Program Settings")
        scroll = ctk.CTkScrollableFrame(t, height=620)
        scroll.pack(fill="both", expand=True)

        ctk.CTkLabel(
            scroll,
            text="All factory defaults & your values",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            scroll,
            text=(
                "Everything the app ships with: default app_settings, category seeds, quick-action seeds. "
                "“Yours” is what is stored now; open sections below to edit or add rows."
            ),
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=11),
            wraplength=640,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))
        self._settings_defaults_ref = ctk.CTkTextbox(
            scroll, height=340, font=ctk.CTkFont(family="Consolas", size=11)
        )
        self._settings_defaults_ref.pack(fill="x", pady=(0, 16))

        ctk.CTkLabel(
            scroll,
            text=f"Installed version: {APP_VERSION}",
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", pady=(0, 16))

        ctk.CTkLabel(scroll, text="Defaults", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", pady=(16, 4)
        )
        ctk.CTkLabel(
            scroll,
            text=(
                "Factory defaults: hourly 0¢, prompt every 900s after 120s delay, no-response action none / 45s timeout, "
                "money on dashboard on, timed popup topmost off. See the reference panel above for exact values."
            ),
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=11),
            wraplength=640,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        row2 = ctk.CTkFrame(scroll, fg_color="transparent")
        row2.pack(fill="x", pady=4)
        ctk.CTkLabel(row2, text="Prompt interval (sec):").pack(side="left")
        self._set_pi = ctk.CTkEntry(row2, width=100)
        self._set_pi.insert(0, str(int(settings_get(self.cfg, "prompt_interval_sec", 900))))
        self._set_pi.pack(side="left", padx=8)
        ctk.CTkLabel(row2, text="First prompt delay (sec):").pack(side="left", padx=(16, 0))
        self._set_fd = ctk.CTkEntry(row2, width=100)
        self._set_fd.insert(0, str(int(settings_get(self.cfg, "prompt_first_delay_sec", 120))))
        self._set_fd.pack(side="left", padx=8)

        row3 = ctk.CTkFrame(scroll, fg_color="transparent")
        row3.pack(fill="x", pady=4)
        ctk.CTkLabel(row3, text="No-response prompt action:").pack(side="left")
        self._set_paction = ctk.CTkComboBox(row3, values=["none", "copy_last"], width=140)
        self._set_paction.set(str(settings_get(self.cfg, "prompt_no_response_action", "none")))
        self._set_paction.pack(side="left", padx=8)
        ctk.CTkLabel(row3, text="Timeout (sec):").pack(side="left")
        self._set_ptimeout = ctk.CTkEntry(row3, width=90)
        self._set_ptimeout.insert(0, str(int(settings_get(self.cfg, "prompt_no_response_timeout_sec", 45))))
        self._set_ptimeout.pack(side="left", padx=8)
        ctk.CTkLabel(
            scroll,
            text="Timed check-in popups run only while actively working. They pause during breaks and when clocked out.",
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=11),
            wraplength=640,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        row_showmoney = ctk.CTkFrame(scroll, fg_color="transparent")
        row_showmoney.pack(anchor="w", pady=8)
        self._set_showmoney = ctk.CTkSwitch(row_showmoney, text="Show money on dashboard")
        if settings_get(self.cfg, "show_money_in_dashboard", True):
            self._set_showmoney.select()
        self._set_showmoney.pack(side="left")
        self._add_help_bubble(row_showmoney, "Toggle income/expense charts on the dashboard.")
        self._set_ptop = ctk.CTkSwitch(scroll, text="Timed check-in popups stay above other windows")
        if settings_get(self.cfg, "prompt_popup_topmost", False):
            self._set_ptop.select()
        self._set_ptop.pack(anchor="w", pady=(0, 8))
        self._set_autobackup = ctk.CTkSwitch(scroll, text="Enable automatic database backups")
        if settings_get(self.cfg, "auto_backup_enabled", False):
            self._set_autobackup.select()
        self._set_autobackup.pack(anchor="w", pady=(0, 6))
        self._set_start_prompt = ctk.CTkSwitch(
            scroll, text="Show startup notice when already clocked in"
        )
        if settings_get(self.cfg, "startup_clocked_in_prompt_enabled", True):
            self._set_start_prompt.select()
        self._set_start_prompt.pack(anchor="w", pady=(0, 6))
        self._set_min_to_tray = ctk.CTkSwitch(scroll, text='Minimize to "Hidden Icons" (system tray)')
        if settings_get(self.cfg, "minimize_to_hidden_icons_enabled", False):
            self._set_min_to_tray.select()
        self._set_min_to_tray.pack(anchor="w", pady=(0, 6))
        self._set_start_on_login = ctk.CTkSwitch(scroll, text="Start on system login")
        if self._is_start_on_login_enabled() or settings_get(self.cfg, "start_on_login_enabled", False):
            self._set_start_on_login.select()
        self._set_start_on_login.pack(anchor="w", pady=(0, 6))
        row_multi = ctk.CTkFrame(scroll, fg_color="transparent")
        row_multi.pack(anchor="w", pady=(0, 6))
        self._set_multi_business_support = ctk.CTkSwitch(row_multi, text="Enable Multi-Business Support")
        if settings_get(self.cfg, "multi_business_enabled", False):
            self._set_multi_business_support.select()
        self._set_multi_business_support.pack(side="left")
        self._add_help_bubble(
            row_multi,
            "When enabled, all money calculations are filtered by the active business profile (or Master aggregate when available).",
        )
        row_currency_safe = ctk.CTkFrame(scroll, fg_color="transparent")
        row_currency_safe.pack(anchor="w", pady=(0, 6))
        self._set_currency_safe = ctk.CTkSwitch(
            row_currency_safe,
            text="Enable currency-safe summaries",
        )
        if settings_get(self.cfg, "currency_safe_summaries_enabled", False):
            self._set_currency_safe.select()
        self._set_currency_safe.pack(side="left")
        self._add_help_bubble(
            row_currency_safe,
            "Strict mode prevents mixed-currency math: Dashboard/Reports/Tax use default currency totals and also show per-currency breakdowns.",
        )
        row_help_bubbles = ctk.CTkFrame(scroll, fg_color="transparent")
        row_help_bubbles.pack(anchor="w", pady=(0, 6))
        self._set_help_bubbles = ctk.CTkSwitch(
            row_help_bubbles,
            text="Enable help text bubbles",
        )
        if settings_get(self.cfg, "help_bubbles_enabled", True):
            self._set_help_bubbles.select()
        self._set_help_bubbles.pack(side="left")
        self._add_help_bubble(
            row_help_bubbles,
            "Toggle all '?' contextual help bubbles across the app.",
        )
        row_github_upd = ctk.CTkFrame(scroll, fg_color="transparent")
        row_github_upd.pack(anchor="w", pady=(0, 6))
        self._set_github_update_check = ctk.CTkSwitch(
            row_github_upd,
            text="Check GitHub for new installer releases on startup",
        )
        if settings_get(self.cfg, "github_update_check_enabled", True):
            self._set_github_update_check.select()
        self._set_github_update_check.pack(side="left")
        self._add_help_bubble(
            row_github_upd,
            "Compares this app's version to the latest release on GitHub and offers a download link when a newer installer is published.",
        )
        row_auto_sched = ctk.CTkFrame(scroll, fg_color="transparent")
        row_auto_sched.pack(anchor="w", pady=(0, 6))
        self._set_auto_sched_expenses = ctk.CTkSwitch(
            row_auto_sched,
            text="Auto-post scheduled expenses",
        )
        if settings_get(self.cfg, "auto_post_scheduled_expenses_enabled", True):
            self._set_auto_sched_expenses.select()
        self._set_auto_sched_expenses.pack(side="left")
        self._add_help_bubble(
            row_auto_sched,
            "When enabled, due scheduled expenses are posted automatically during refresh.",
        )
        row_auto_debt = ctk.CTkFrame(scroll, fg_color="transparent")
        row_auto_debt.pack(anchor="w", pady=(0, 6))
        self._set_auto_debt_from_credit = ctk.CTkSwitch(
            row_auto_debt,
            text="Auto-create debt from credit-funded expenses",
        )
        if settings_get(self.cfg, "auto_create_debt_for_credit_expenses_enabled", True):
            self._set_auto_debt_from_credit.select()
        self._set_auto_debt_from_credit.pack(side="left")
        self._add_help_bubble(
            row_auto_debt,
            "When enabled, credit card/borrowed expense funding creates a matching debt entry automatically.",
        )
        row_status_banner = ctk.CTkFrame(scroll, fg_color="transparent")
        row_status_banner.pack(anchor="w", pady=(0, 6))
        self._set_status_banner = ctk.CTkSwitch(
            row_status_banner,
            text="Show process status banner",
        )
        if settings_get(self.cfg, "show_process_status_banner_enabled", True):
            self._set_status_banner.select()
        self._set_status_banner.pack(side="left")
        self._add_help_bubble(
            row_status_banner,
            "Shows/hides top status notifications for auto-processes (sync, condense, saves, etc.).",
        )
        row_debt_notify = ctk.CTkFrame(scroll, fg_color="transparent")
        row_debt_notify.pack(anchor="w", pady=(0, 6))
        self._set_notify_debt_settlement = ctk.CTkSwitch(
            row_debt_notify,
            text="Notify when debt settlement posts expense",
        )
        if settings_get(self.cfg, "notify_on_debt_settlement_enabled", True):
            self._set_notify_debt_settlement.select()
        self._set_notify_debt_settlement.pack(side="left")
        self._add_help_bubble(
            row_debt_notify,
            "Shows a confirmation popup after a debt is closed and payment expense is created.",
        )
        row4 = ctk.CTkFrame(scroll, fg_color="transparent")
        row4.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(row4, text="Auto-backup interval (hours):").pack(side="left")
        self._set_backup_hours = ctk.CTkEntry(row4, width=90)
        self._set_backup_hours.insert(0, str(int(settings_get(self.cfg, "auto_backup_interval_hours", 24))))
        self._set_backup_hours.pack(side="left", padx=8)

        def run_manual_backup() -> None:
            try:
                out = self._perform_database_backup(reason="manual")
            except Exception as exc:  # noqa: BLE001
                self._set_process_status("Manual backup failed.", auto_clear_ms=2200)
                messagebox.showerror("RootRecord Business Manager", f"Backup failed:\n{exc}")
                return
            messagebox.showinfo("RootRecord Business Manager", f"Backup created:\n{out}")

        ctk.CTkButton(
            row4,
            text="Backup Now",
            width=110,
            command=run_manual_backup,
        ).pack(side="left", padx=(12, 6))
        ctk.CTkButton(row4, text="Open Backup Folder", width=140, command=self._open_backup_folder).pack(side="left", padx=4)

        def save_defaults() -> None:
            try:
                self._set_process_status("Saving program settings...", auto_clear_ms=None)
                settings_set(self.cfg, "prompt_interval_sec", int(self._set_pi.get()))
                settings_set(self.cfg, "prompt_first_delay_sec", int(self._set_fd.get()))
                settings_set(self.cfg, "prompt_no_response_action", self._set_paction.get().strip())
                settings_set(self.cfg, "prompt_no_response_timeout_sec", int(self._set_ptimeout.get()))
                settings_set(self.cfg, "show_money_in_dashboard", self._set_showmoney.get() == 1)
                settings_set(self.cfg, "prompt_popup_topmost", self._set_ptop.get() == 1)
                settings_set(self.cfg, "auto_backup_enabled", self._set_autobackup.get() == 1)
                settings_set(
                    self.cfg,
                    "startup_clocked_in_prompt_enabled",
                    self._set_start_prompt.get() == 1,
                )
                settings_set(self.cfg, "minimize_to_hidden_icons_enabled", self._set_min_to_tray.get() == 1)
                start_on_login = self._set_start_on_login.get() == 1
                self._set_start_on_login_enabled(start_on_login)
                settings_set(self.cfg, "start_on_login_enabled", start_on_login)
                multi_enabled = self._set_multi_business_support.get() == 1
                settings_set(self.cfg, "multi_business_enabled", multi_enabled)
                settings_set(self.cfg, "currency_safe_summaries_enabled", self._set_currency_safe.get() == 1)
                settings_set(self.cfg, "help_bubbles_enabled", self._set_help_bubbles.get() == 1)
                settings_set(self.cfg, "github_update_check_enabled", self._set_github_update_check.get() == 1)
                settings_set(self.cfg, "auto_post_scheduled_expenses_enabled", self._set_auto_sched_expenses.get() == 1)
                settings_set(
                    self.cfg,
                    "auto_create_debt_for_credit_expenses_enabled",
                    self._set_auto_debt_from_credit.get() == 1,
                )
                settings_set(self.cfg, "show_process_status_banner_enabled", self._set_status_banner.get() == 1)
                settings_set(
                    self.cfg,
                    "notify_on_debt_settlement_enabled",
                    self._set_notify_debt_settlement.get() == 1,
                )
                if not multi_enabled:
                    settings_set(self.cfg, "active_business_id", 1)
                if hasattr(self, "_multi_business_enabled_var"):
                    self._multi_business_enabled_var.set(multi_enabled)
                self._refresh_business_profiles_ui()
                self._refresh_all_business_scoped_views()
                self._refresh_help_bubbles_visibility()
                settings_set(self.cfg, "auto_backup_interval_hours", int(self._set_backup_hours.get()))
                # Apply changed prompt timing/settings immediately, no app restart needed.
                self._schedule_prompts()
                self._refresh_settings_views()
                self._set_process_status("Program settings saved.", auto_clear_ms=1800)
                messagebox.showinfo("RootRecord Business Manager", "Settings saved.")
            except ValueError:
                self._set_process_status("Program settings save failed.", auto_clear_ms=2200)
                messagebox.showerror("RootRecord Business Manager", "Use whole numbers for timing and backup fields.")
            except RuntimeError as exc:
                self._set_process_status("Program settings save failed.", auto_clear_ms=2200)
                messagebox.showerror("RootRecord Business Manager", str(exc))
            except OSError as exc:
                self._set_process_status("Program settings save failed.", auto_clear_ms=2200)
                messagebox.showerror("RootRecord Business Manager", f"Could not update startup setting:\n{exc}")

        ctk.CTkButton(scroll, text="Save Settings", command=save_defaults, width=160).pack(anchor="w", pady=(6, 12))

        ctk.CTkLabel(scroll, text="Categories", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", pady=(20, 4)
        )
        ctk.CTkLabel(
            scroll,
            text=(
                "Your database rows (live). Full shipped category list is in the reference panel at the top. "
                "These names also appear on the Time tab and in prompts."
            ),
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=11),
            wraplength=640,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))
        self._settings_cat_list = ctk.CTkTextbox(
            scroll, height=160, font=ctk.CTkFont(family="Consolas", size=12)
        )
        self._settings_cat_list.pack(fill="x", pady=(0, 8))
        cf = ctk.CTkFrame(scroll, fg_color="transparent")
        cf.pack(fill="x", pady=4)
        self._new_cat = ctk.CTkEntry(cf, width=200, placeholder_text="New category name")
        self._new_cat.pack(side="left", padx=4)
        self._new_cat_c = ctk.CTkEntry(cf, width=80, placeholder_text="#hex")
        self._new_cat_c.insert(0, "#2B8A8F")
        self._new_cat_c.pack(side="left", padx=4)
        ctk.CTkButton(
            cf,
            text="Select color",
            width=110,
            command=lambda: self._pick_color_for_entry(self._new_cat_c, "#2B8A8F"),
        ).pack(side="left", padx=4)

        def add_cat() -> None:
            n = self._new_cat.get().strip()
            if not n:
                return
            upsert_work_category(
                self.cfg,
                self.uid,
                n,
                color=self._normalize_hex_color(self._new_cat_c.get(), "#2B8A8F"),
            )
            self._new_cat.delete(0, "end")
            self._refresh_settings_views()
            messagebox.showinfo(
                "RootRecord Business Manager",
                f"Added category '{n}'. Re-open the Time tab to refresh category dropdowns.",
            )

        ctk.CTkButton(cf, text="Add", width=80, command=add_cat).pack(side="left", padx=4)
        editf = ctk.CTkFrame(scroll, fg_color="transparent")
        editf.pack(fill="x", pady=(6, 8))
        ctk.CTkLabel(editf, text="Edit existing").pack(side="left", padx=(4, 6))
        self._edit_cat_pick = ctk.CTkComboBox(editf, values=["—"], width=200, command=lambda _v: self._load_selected_category_for_edit())
        self._edit_cat_pick.pack(side="left", padx=4)
        self._edit_cat_name = ctk.CTkEntry(editf, width=180, placeholder_text="New name")
        self._edit_cat_name.pack(side="left", padx=4)
        self._edit_cat_color = ctk.CTkEntry(editf, width=90, placeholder_text="#hex")
        self._edit_cat_color.pack(side="left", padx=4)
        ctk.CTkButton(
            editf,
            text="Select color",
            width=110,
            command=lambda: self._pick_color_for_entry(self._edit_cat_color, "#2B8A8F"),
        ).pack(side="left", padx=4)
        self._edit_cat_billable = ctk.CTkSwitch(editf, text="Billable")
        self._edit_cat_billable.pack(side="left", padx=8)
        ctk.CTkButton(editf, text="Save category", width=110, command=self._save_selected_category_edit).pack(
            side="left", padx=4
        )

        ctk.CTkLabel(scroll, text="Projects", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", pady=(20, 4)
        )
        ctk.CTkLabel(
            scroll,
            text="Factory default: no projects. Your current projects:",
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=11),
            wraplength=640,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))
        self._settings_proj_list = ctk.CTkTextbox(
            scroll, height=100, font=ctk.CTkFont(family="Consolas", size=12)
        )
        self._settings_proj_list.pack(fill="x", pady=(0, 8))
        pf = ctk.CTkFrame(scroll, fg_color="transparent")
        pf.pack(fill="x", pady=4)
        self._new_proj = ctk.CTkEntry(pf, width=200, placeholder_text="Project name")
        self._new_proj.pack(side="left", padx=4)
        self._new_cli = ctk.CTkEntry(pf, width=160, placeholder_text="Client (optional)")
        self._new_cli.pack(side="left", padx=4)

        def add_proj() -> None:
            n = self._new_proj.get().strip()
            if not n:
                return
            insert_project(self.cfg, self.uid, n, client_name=self._new_cli.get().strip() or None)
            self._new_proj.delete(0, "end")
            self._new_cli.delete(0, "end")
            self._refresh_settings_views()
            messagebox.showinfo("RootRecord Business Manager", f"Added project '{n}'.")

        ctk.CTkButton(pf, text="Add project", width=100, command=add_proj).pack(side="left", padx=4)

        ctk.CTkLabel(scroll, text="Quick actions", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", pady=(20, 4)
        )
        ctk.CTkLabel(
            scroll,
            text="Factory seeds (Code / Review / Meeting) are listed at the top; below is what is stored for you now.",
            text_color=self._theme_text_muted,
            font=ctk.CTkFont(size=11),
            wraplength=640,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))
        self._settings_qa_list = ctk.CTkTextbox(
            scroll, height=100, font=ctk.CTkFont(family="Consolas", size=12)
        )
        self._settings_qa_list.pack(fill="x", pady=(0, 8))
        qf = ctk.CTkFrame(scroll, fg_color="transparent")
        qf.pack(fill="x", pady=4)
        self._qa_lab = ctk.CTkEntry(qf, width=160, placeholder_text="Button label")
        self._qa_lab.pack(side="left", padx=4)
        self._qa_txt = ctk.CTkEntry(qf, width=280, placeholder_text="Default description text")
        self._qa_txt.pack(side="left", padx=4)

        def add_qa() -> None:
            lb = self._qa_lab.get().strip()
            if not lb:
                return
            save_quick_action(self.cfg, self.uid, lb, default_description=self._qa_txt.get().strip())
            self._qa_lab.delete(0, "end")
            self._qa_txt.delete(0, "end")
            self._reload_quick_actions()
            self._refresh_settings_views()

        ctk.CTkButton(qf, text="Add quick action", command=add_qa).pack(side="left", padx=4)

        self._refresh_settings_views()


def bootstrap(cfg: DbConfig) -> None:
    ensure_user_layout(LOCAL_USER_ID)
    upsert_user(cfg, LOCAL_USER_ID, os.environ.get("USERNAME") or getpass.getuser(), None)
    started = get_machine_session_started_at()
    if not started:
        started = init_machine_session()
    if get_machine_session_db_id() is None:
        mid = insert_machine_session(cfg, started)
        set_machine_session_db_id(mid)
    from db import insert_session_event

    insert_session_event(cfg, LOCAL_USER_ID, "desktop_start", "ui")
    if load_user_state(LOCAL_USER_ID) is None:
        reset_user_for_new_machine_session(LOCAL_USER_ID, started)


def _restart_rootrecord_app() -> None:
    """Spawn a fresh process and exit (e.g. after database import)."""
    argv = [sys.executable] + sys.argv[1:]
    try:
        subprocess.Popen(argv)
    except Exception as exc:
        messagebox.showerror("RootRecord Business Manager", f"Could not restart the application:\n{exc}")
        return
    os._exit(0)


def _startup_splash_image_candidates() -> list[Path]:
    """Prefer build/branding/Loading.* (installer art); fall back to About graphic, then favicon."""
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            return
        seen.add(key)
        candidates.append(p)

    for p in iter_loading_image_paths():
        add(p)
    ap = resolve_shipped_asset(
        "about_page_graphic.jpg",
        "about page grahic.jpg",
        "about page graphic.jpg",
    )
    if ap is not None:
        add(ap)
    for d in iter_app_bundle_asset_dirs():
        add(d / "favicon.ico")
    return candidates


def _attach_startup_splash_spinner(root: Any, parent: Any, pal: BrandingPalette) -> None:
    """Small rotating arc + 'Loading...' (tk event loop must run via update / main pump)."""
    import tkinter as tk

    bg = pal.bg
    row = tk.Frame(parent, bg=bg)
    row.pack(pady=(14, 0))
    sz = 28
    pad = 2
    accent = pal.accent
    cv = tk.Canvas(
        row,
        width=sz,
        height=sz,
        bg=bg,
        highlightthickness=0,
        bd=0,
    )
    cv.pack(side=tk.LEFT, padx=(0, 10))
    tk.Label(
        row,
        text="Loading...",
        fg=pal.splash_label,
        bg=bg,
        font=("Segoe UI", 11),
    ).pack(side=tk.LEFT)
    angle = [0]

    def draw() -> None:
        cv.delete("all")
        a = angle[0] % 360
        cv.create_arc(
            pad,
            pad,
            sz - pad,
            sz - pad,
            start=a,
            extent=270,
            style=tk.ARC,
            outline=accent,
            width=3,
        )
        angle[0] = (angle[0] + 10) % 360

    spin_job: list[str | None] = [None]

    def tick() -> None:
        try:
            if not root.winfo_exists() or not cv.winfo_exists():
                return
        except Exception:
            return
        draw()
        spin_job[0] = root.after(48, tick)

    def on_destroy(_evt: Any = None) -> None:
        j = spin_job[0]
        if j is not None:
            try:
                root.after_cancel(j)
            except Exception:
                pass
            spin_job[0] = None

    draw()
    spin_job[0] = root.after(48, tick)
    try:
        root.bind("<Destroy>", on_destroy, add="+")
    except Exception:
        pass


def _show_startup_splash_screen() -> Any | None:
    """Small borderless window with branding while the main UI loads."""
    import tkinter as tk

    pal = get_branding_palette()
    root = tk.Tk()
    root.configure(bg=pal.bg)
    root.overrideredirect(True)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    outer = tk.Frame(root, bg=pal.bg, padx=22, pady=18)
    outer.pack()
    root._splash_outer_frame = outer  # noqa: SLF001 — cancel row + used by startup pump
    photo_keep: list[Any] = []
    img_path: Path | None = None
    for p in _startup_splash_image_candidates():
        if p.is_file():
            img_path = p
            break
    placed = False
    if img_path is not None:
        try:
            from PIL import Image, ImageTk

            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:
                resample = Image.LANCZOS  # type: ignore[attr-defined]
            pil = Image.open(img_path)
            if pil.mode == "P" and "transparency" in getattr(pil, "info", {}):
                pil = pil.convert("RGBA")
            br, bgc, bb = pal.bg_rgb()
            if pil.mode == "RGBA":
                bg = Image.new("RGB", pil.size, (br, bgc, bb))
                bg.paste(pil, mask=pil.split()[3])
                pil = bg
            else:
                pil = pil.convert("RGB")
            pil.thumbnail((300, 220), resample)
            ph = ImageTk.PhotoImage(pil)
            photo_keep.append(ph)
            tk.Label(outer, image=ph, bg=pal.bg).pack()
            placed = True
        except Exception:
            placed = False
    if not placed:
        tk.Label(
            outer,
            text="RootRecord",
            fg=pal.text_heading,
            bg=pal.bg,
            font=("Segoe UI", 18, "bold"),
        ).pack()
    _attach_startup_splash_spinner(root, outer, pal)
    root._splash_photo_keep = photo_keep  # noqa: SLF001 — keep PhotoImage refs alive
    root.update_idletasks()
    ww = max(root.winfo_reqwidth(), 280)
    wh = root.winfo_reqheight()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = max(0, (sw - ww) // 2)
    y = max(0, (sh - wh) // 3)
    root.geometry(f"{ww}x{wh}+{x}+{y}")
    root.update()
    return root


def _resize_splash_to_fit_content(splash: Any) -> None:
    """Recompute splash bounds after adding widgets (avoids clipping Cancel below a fixed height)."""
    try:
        splash.update_idletasks()
        ww = max(int(splash.winfo_reqwidth()), 300)
        wh = max(int(splash.winfo_reqheight()), 120)
        sw = int(splash.winfo_screenwidth())
        sh = int(splash.winfo_screenheight())
        x = max(0, (sw - ww) // 2)
        y = max(0, (sh - wh) // 3)
        splash.geometry(f"{ww}x{wh}+{x}+{y}")
        splash.update_idletasks()
    except Exception:
        pass


def _add_startup_splash_cancel_immediate(splash: Any, cancel_event: threading.Event) -> None:
    """Always show Cancel on the splash so startup can be aborted without waiting on a timer."""
    import tkinter as tk

    if getattr(splash, "_splash_cancel_immediate_added", False):
        return
    pal = get_branding_palette()
    bg = pal.bg
    outer = getattr(splash, "_splash_outer_frame", None)
    if outer is None:
        return
    row = tk.Frame(outer, bg=bg)
    row.pack(pady=(12, 0))
    splash._splash_cancel_immediate_added = True  # noqa: SLF001
    splash._splash_cancel_row = row  # noqa: SLF001

    def on_cancel() -> None:
        cancel_event.set()
        try:
            for w in row.winfo_children():
                if isinstance(w, tk.Button):
                    w.configure(state=tk.DISABLED)
        except Exception:
            pass
        # Hard exit: cooperative cancel can miss cases (e.g. nested Tk / blocking dialogs).
        os._exit(0)

    tk.Label(
        row,
        text="Starting… (database or network may take a moment)",
        fg=pal.splash_label,
        bg=bg,
        font=("Segoe UI", 9),
    ).pack()
    tk.Button(
        row,
        text="Cancel",
        command=on_cancel,
        font=("Segoe UI", 10),
    ).pack(pady=(6, 0))
    _resize_splash_to_fit_content(splash)
    try:
        splash.update()
    except Exception:
        pass


def _schedule_startup_splash_stuck_hint(splash: Any, delay_ms: int = 12_000) -> None:
    """If startup is still showing, add a short hint (Cancel is already visible)."""
    import tkinter as tk

    pal = get_branding_palette()
    bg = pal.bg

    def add_hint() -> None:
        try:
            if not splash.winfo_exists():
                return
        except Exception:
            return
        if getattr(splash, "_splash_stuck_hint", None) is not None:
            return
        outer = getattr(splash, "_splash_outer_frame", None)
        if outer is None or not outer.winfo_exists():
            return
        hint = tk.Label(
            outer,
            text="Still waiting? Use Cancel to quit if something is stuck.",
            fg=pal.splash_label,
            bg=bg,
            font=("Segoe UI", 8),
        )
        hint.pack(pady=(6, 0))
        splash._splash_stuck_hint = hint  # noqa: SLF001
        _resize_splash_to_fit_content(splash)
        try:
            splash.update()
        except Exception:
            pass

    try:
        splash.after(delay_ms, add_hint)
    except Exception:
        pass


def _startup_load_database_phase(splash: Any | None, cancel_event: threading.Event) -> DbConfig:
    """Run DB init on a worker thread so the main thread can pump the splash and honor Cancel."""
    from license_client import LicenseStartupCancelled

    cfg_out: list[DbConfig] = []
    err_out: list[Exception] = []

    def work() -> None:
        try:
            c = load_db_config()
            maybe_migrate_legacy_desktop_db()
            ensure_schema(c)
            migrate_registered_users_from_json(c, registered_users_file())
            run_migrations(c, local_user_id=LOCAL_USER_ID)
            bootstrap(c)
            cfg_out.append(c)
        except Exception as exc:  # noqa: BLE001
            err_out.append(exc)

    t = threading.Thread(target=work, daemon=True, name="rootrecord-startup-db")
    t.start()
    while t.is_alive():
        if cancel_event.is_set():
            raise LicenseStartupCancelled()
        if splash is not None:
            try:
                splash.update_idletasks()
                splash.update()
            except Exception:
                pass
        time.sleep(0.02)
    t.join(timeout=5.0)
    if err_out:
        raise err_out[0]
    if not cfg_out:
        raise RuntimeError("Database startup did not complete.")
    return cfg_out[0]


def run_app() -> None:
    from single_instance import ensure_single_instance_or_exit

    ensure_single_instance_or_exit()

    log = logging.getLogger("rootrecord.startup")
    splash: Any | None = None
    try:
        splash = _show_startup_splash_screen()
    except Exception:
        log.debug("Startup splash failed to load", exc_info=True)
        splash = None

    from license_client import LicenseStartupCancelled
    from license_runtime import apply_license_at_startup

    cancel_event = threading.Event()

    def _pump_startup_splash() -> None:
        if splash is None:
            return
        try:
            splash.update_idletasks()
            splash.update()
        except Exception:
            pass

    if splash is not None:
        _add_startup_splash_cancel_immediate(splash, cancel_event)
        _schedule_startup_splash_stuck_hint(splash)

    try:
        cfg = _startup_load_database_phase(splash, cancel_event)
    except LicenseStartupCancelled:
        if splash is not None:
            try:
                splash.destroy()
            except Exception:
                pass
        log.info("Startup cancelled from splash during database preparation")
        raise SystemExit(0)
    except OSError as exc:
        log.exception("Database init failed")
        messagebox.showerror("RootRecord Business Manager", f"Database init failed:\n{exc}")
        if splash is not None:
            try:
                splash.destroy()
            except Exception:
                pass
        raise SystemExit(1) from exc
    except Exception as exc:
        log.exception("Database init failed")
        messagebox.showerror("RootRecord Business Manager", f"Database init failed:\n{exc}")
        if splash is not None:
            try:
                splash.destroy()
            except Exception:
                pass
        raise SystemExit(1) from exc

    try:
        apply_license_at_startup(
            cfg,
            ui_pump=_pump_startup_splash if splash is not None else None,
            cancel_check=(lambda: cancel_event.is_set()) if splash is not None else None,
            ui_master=splash,
        )
    except LicenseStartupCancelled:
        if splash is not None:
            try:
                splash.destroy()
            except Exception:
                pass
        log.info("Startup cancelled from splash while waiting for license server")
        raise SystemExit(0)

    app: RootRecordApp | None = None
    try:
        app = RootRecordApp(cfg, startup_splash=splash)
    finally:
        if splash is not None:
            try:
                splash.destroy()
            except Exception:
                pass
    if app is not None:
        app.mainloop()
