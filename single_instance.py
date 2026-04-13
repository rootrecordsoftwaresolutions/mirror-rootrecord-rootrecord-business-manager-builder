"""Ensure only one GUI process runs at a time (Windows named mutex; Unix flock file)."""

from __future__ import annotations

import atexit
import logging
import os
import sys
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("rootrecord.single_instance")

_MUTEX_NAME = "Local\\RootRecordBusinessManagerSingleInstance"
# Must match ``RootRecordApp.title()`` so a second launch can find the main window.
_MAIN_WINDOW_TITLE = "RootRecord Business Manager"
_lock_fp: Any = None
_mutex_handle: int | None = None


def _allow_multi_instance() -> bool:
    v = (os.environ.get("ROOTRECORD_ALLOW_MULTI_INSTANCE") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _release_windows_mutex() -> None:
    global _mutex_handle
    h = _mutex_handle
    _mutex_handle = None
    if h and sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(h)
        except Exception:
            pass


def _release_unix_lock() -> None:
    global _lock_fp
    fp = _lock_fp
    _lock_fp = None
    if fp is not None:
        try:
            fp.close()
        except Exception:
            pass


def _second_instance_message_windows() -> None:
    try:
        import ctypes

        MB_OK = 0
        MB_ICONINFORMATION = 0x40
        text = (
            "RootRecord Business Manager is already running.\n\n"
            "We could not bring the existing window to the front automatically.\n"
            "Check the taskbar, the notification area (hidden icons / system tray), or Task Manager."
        )
        ctypes.windll.user32.MessageBoxW(0, text, "RootRecord Business Manager", MB_OK | MB_ICONINFORMATION)
    except Exception:
        pass


def _try_activate_existing_rootrecord_window_windows() -> bool:
    """Find the main Tk window and restore/foreground it (e.g. after minimize-to-tray)."""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return False

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    hwnd = int(user32.FindWindowW(None, _MAIN_WINDOW_TITLE))
    if hwnd == 0:
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd_el: int, _lp: int) -> bool:
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd_el, buf, 512)
            t = buf.value
            if t == _MAIN_WINDOW_TITLE:
                found.append(int(hwnd_el))
            return True

        user32.EnumWindows(_enum, 0)
        if not found:
            return False
        hwnd = found[-1]

    SW_RESTORE = 9
    SW_SHOW = 5
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.ShowWindow(hwnd, SW_SHOW)

    fg = user32.GetForegroundWindow()
    cur_tid = int(kernel32.GetCurrentThreadId())
    fg_tid = int(user32.GetWindowThreadProcessId(fg, None) or 0) if fg else 0
    if fg_tid and fg_tid != cur_tid:
        try:
            user32.AttachThreadInput(cur_tid, fg_tid, True)
            user32.SetForegroundWindow(hwnd)
        finally:
            try:
                user32.AttachThreadInput(cur_tid, fg_tid, False)
            except Exception:
                pass
    else:
        user32.SetForegroundWindow(hwnd)

    try:
        FLASHW_ALL = 3
        FLASHW_TIMERNOFG = 12

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("hwnd", wintypes.HWND),
                ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT),
                ("dwTimeout", wintypes.DWORD),
            ]

        fi = FLASHWINFO()
        fi.cbSize = ctypes.sizeof(FLASHWINFO)
        fi.hwnd = hwnd
        fi.dwFlags = FLASHW_ALL | FLASHW_TIMERNOFG
        fi.uCount = 3
        fi.dwTimeout = 0
        user32.FlashWindowEx(ctypes.byref(fi))
    except Exception:
        pass

    _LOG.info("Second instance asked existing window to foreground (hwnd=%s)", hwnd)
    return True


def _acquire_windows() -> bool:
    """Return True if this process should continue as the owner."""
    global _mutex_handle
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    CreateMutexW = kernel32.CreateMutexW
    CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    CreateMutexW.restype = wintypes.HANDLE
    GetLastError = kernel32.GetLastError
    GetLastError.restype = wintypes.DWORD
    CloseHandle = kernel32.CloseHandle
    CloseHandle.argtypes = [wintypes.HANDLE]
    CloseHandle.restype = wintypes.BOOL

    ERROR_ALREADY_EXISTS = 183
    kernel32.SetLastError(0)
    mutex = CreateMutexW(None, False, _MUTEX_NAME)
    if not mutex:
        _LOG.warning("CreateMutexW failed; allowing multiple instances (GetLastError=%s)", GetLastError())
        return True
    err = int(GetLastError())
    if err == ERROR_ALREADY_EXISTS:
        CloseHandle(mutex)
        if _try_activate_existing_rootrecord_window_windows():
            raise SystemExit(0)
        return False
    _mutex_handle = int(mutex)
    atexit.register(_release_windows_mutex)
    return True


def _acquire_unix() -> bool:
    """Return True if this process should continue as the owner."""
    global _lock_fp
    try:
        import fcntl
    except ImportError:
        _LOG.warning("fcntl not available; cannot enforce single instance on this platform")
        return True

    try:
        from paths import resolve_app_root

        root = resolve_app_root()
    except Exception:
        root = Path.home() / ".rootrecord"
    lock_path = root / "data" / ".rootrecord_single_instance.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        fp = open(lock_path, "a+b")  # noqa: SIM115 — held until exit
    except OSError as exc:
        _LOG.warning("Could not open single-instance lock %s: %s", lock_path, exc)
        return True
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            fp.close()
        except Exception:
            pass
        return False
    _lock_fp = fp
    atexit.register(_release_unix_lock)
    return True


def ensure_single_instance_or_exit() -> None:
    """If another copy is running, notify the user and exit. No-op when ROOTRECORD_ALLOW_MULTI_INSTANCE is set."""
    if _allow_multi_instance():
        return
    if sys.platform == "win32":
        ok = _acquire_windows()
        if not ok:
            _second_instance_message_windows()
            raise SystemExit(0)
        return
    ok = _acquire_unix()
    if not ok:
        try:
            print("RootRecord Business Manager is already running.", file=sys.stderr)
        except Exception:
            pass
        raise SystemExit(0)
