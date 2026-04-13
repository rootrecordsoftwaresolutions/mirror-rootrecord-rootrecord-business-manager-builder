"""Tk dialogs for remote license sign-in / sign-up (email + password)."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Literal

from branding_theme import get_branding_palette
from paths import iter_app_bundle_asset_dirs

Action = Literal["login", "signup", "claim"]

# Must match server-side MIN_PASSWORD_LEN (same value as auth in the license service).
_LICENSE_MIN_PASSWORD_LEN = 10


def _apply_favicon_to_window(window: tk.Misc) -> None:
    """Match main app: favicon.ico from bundle / install dir (Windows iconbitmap)."""
    top = window.winfo_toplevel()
    for d in iter_app_bundle_asset_dirs():
        p = d / "favicon.ico"
        if not p.is_file():
            continue
        sp = str(p.resolve())
        try:
            top.iconbitmap(sp)
            return
        except Exception:
            pass
        try:
            top.iconbitmap(default=sp)
            return
        except Exception:
            pass


def prompt_license_signin_or_signup(master: tk.Misc | None = None) -> tuple[Action, str, str] | None:
    """
    Modal: email, password, Sign in / Create account / Set password on this device / Cancel.

    When ``master`` is set (e.g. startup splash), the dialog shares that Tk so the splash
    stays responsive (Cancel, spinner). Otherwise a temporary hidden root is used.

    Returns (action, email, password) or None if cancelled.
    """
    pal = get_branding_palette()
    bg = pal.bg
    fg_heading = pal.text_heading
    fg_muted = pal.text_muted
    panel = pal.panel
    accent = pal.accent
    border = pal.border_subtle

    out: list[tuple[Action, str, str] | None] = [None]
    own_host = False
    if master is not None:
        host = master.winfo_toplevel()
        dlg = tk.Toplevel(master)
    else:
        host = tk.Tk()
        host.withdraw()
        own_host = True
        dlg = tk.Toplevel(host)

    dlg.title("RootRecord — sign in")
    dlg.resizable(False, False)
    dlg.configure(bg=bg)
    dlg.transient(host)
    dlg.grab_set()
    _apply_favicon_to_window(dlg)

    pad_x = 18
    pad_y = 14
    frm = tk.Frame(dlg, bg=bg, padx=pad_x, pady=pad_y)
    frm.pack(fill="both", expand=True)

    tk.Label(
        frm,
        text="Use the email for your trial or subscription.\n"
        "“Set password on this device” is for accounts created before passwords (this PC must already be registered).",
        fg=fg_muted,
        bg=bg,
        justify="left",
        wraplength=440,
        font=("Segoe UI", 10),
    ).pack(anchor="w", pady=(0, 12))

    email_var = tk.StringVar()
    pw_var = tk.StringVar()

    entry_style = {
        "bg": panel,
        "fg": fg_heading,
        "insertbackground": fg_heading,
        "relief": tk.FLAT,
        "font": ("Segoe UI", 10),
        "highlightthickness": 1,
        "highlightbackground": border,
        "highlightcolor": accent,
    }

    # Entries must be children of the same row Frame as their labels (not `frm`), or pack lands in the wrong parent.
    row_email = tk.Frame(frm, bg=bg)
    row_email.pack(fill="x", pady=6)
    tk.Label(row_email, text="Email", fg=fg_muted, bg=bg, width=11, anchor="w", font=("Segoe UI", 10)).pack(
        side="left", padx=(0, 10)
    )
    email_e = tk.Entry(row_email, textvariable=email_var, **entry_style)
    email_e.pack(side="left", fill="x", expand=True)

    row_pw = tk.Frame(frm, bg=bg)
    row_pw.pack(fill="x", pady=6)
    tk.Label(row_pw, text="Password", fg=fg_muted, bg=bg, width=11, anchor="w", font=("Segoe UI", 10)).pack(
        side="left", padx=(0, 10)
    )
    pw_e = tk.Entry(row_pw, textvariable=pw_var, show="*", **entry_style)
    pw_e.pack(side="left", fill="x", expand=True)

    btn_row = tk.Frame(frm, bg=bg)
    btn_row.pack(fill="x", pady=(16, 4))

    def finish(action: Action | None) -> None:
        if action is None:
            out[0] = None
        else:
            em = email_var.get().strip()
            pw = pw_var.get()
            if not em:
                messagebox.showwarning("RootRecord", "Enter your email.", parent=dlg)
                return
            if not pw:
                messagebox.showwarning("RootRecord", "Enter a password.", parent=dlg)
                return
            if len(pw) < _LICENSE_MIN_PASSWORD_LEN:
                messagebox.showwarning(
                    "RootRecord",
                    f"Password must be at least {_LICENSE_MIN_PASSWORD_LEN} characters.",
                    parent=dlg,
                )
                return
            out[0] = (action, em, pw)
        dlg.grab_release()
        dlg.destroy()

    def on_login() -> None:
        finish("login")

    def on_signup() -> None:
        finish("signup")

    def on_claim() -> None:
        finish("claim")

    def on_cancel() -> None:
        finish(None)

    def _primary_btn(text: str, cmd) -> tk.Button:
        return tk.Button(
            btn_row,
            text=text,
            command=cmd,
            font=("Segoe UI", 10, "bold"),
            bg=accent,
            fg="#f0f4fc",
            activebackground=pal.nav_active,
            activeforeground="#f0f4fc",
            relief=tk.FLAT,
            padx=12,
            pady=6,
            cursor="hand2",
        )

    def _secondary_btn(text: str, cmd) -> tk.Button:
        return tk.Button(
            btn_row,
            text=text,
            command=cmd,
            font=("Segoe UI", 10),
            bg=panel,
            fg=fg_heading,
            activebackground=border,
            activeforeground=fg_heading,
            relief=tk.FLAT,
            padx=10,
            pady=6,
            cursor="hand2",
        )

    _primary_btn("Sign in", on_login).pack(side="left", padx=(0, 6))
    _primary_btn("Create account", on_signup).pack(side="left", padx=(0, 6))
    _secondary_btn("Set password on this device", on_claim).pack(side="left", padx=(0, 6))
    _secondary_btn("Cancel", on_cancel).pack(side="left", padx=(8, 0))

    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.bind("<Return>", lambda _e: on_login())
    dlg.bind("<Escape>", lambda _e: on_cancel())

    email_e.focus_set()
    try:
        host.update_idletasks()
        dlg.update_idletasks()
        hx = host.winfo_rootx()
        hy = host.winfo_rooty()
        hw = max(host.winfo_width(), 1)
        hh = max(host.winfo_height(), 1)
        dw = dlg.winfo_reqwidth()
        dh = dlg.winfo_reqheight()
        dlg.geometry(f"+{max(0, hx + (hw - dw) // 2)}+{max(0, hy + (hh - dh) // 2)}")
    except Exception:
        pass
    host.wait_window(dlg)
    if own_host:
        try:
            host.destroy()
        except Exception:
            pass

    return out[0]
