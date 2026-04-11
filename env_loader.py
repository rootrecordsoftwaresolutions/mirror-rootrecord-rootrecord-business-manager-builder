"""Load .env before other modules (especially paths.py) read os.environ.

Development: optional %%LOCALAPPDATA%%\\RootRecord\\.env, then project .env (last wins).
PyInstaller: same broad-to-narrow merge; .env next to RootRecord.exe wins over AppData.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_LOADED = False


def load_rootrecord_env() -> None:
    global _LOADED
    if _LOADED:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        _LOADED = True
        return

    _LOADED = True
    pkg = Path(__file__).resolve().parent
    candidates: list[Path] = []

    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if local:
            candidates.append(Path(local) / "RootRecord" / ".env")
        rh = os.environ.get("ROOTRECORD_HOME", "").strip()
        if rh:
            candidates.append(Path(rh).expanduser().resolve() / ".env")
        candidates.append(Path(sys.executable).resolve().parent / ".env")
    else:
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if local:
            candidates.append(Path(local) / "RootRecord" / ".env")
        rh = os.environ.get("ROOTRECORD_HOME", "").strip()
        if rh:
            candidates.append(Path(rh).expanduser().resolve() / ".env")
        candidates.append(pkg / ".env")

    for p in candidates:
        if p.is_file():
            # Later files override earlier ones; project / exe-adjacent .env wins.
            load_dotenv(p, override=True, encoding="utf-8-sig")
