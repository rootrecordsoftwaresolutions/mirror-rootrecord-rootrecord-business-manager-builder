"""Configure process-wide logging to %LOCALAPPDATA%\\RootRecord\\data\\logs (or dev data dir)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from paths import data_dir

_CONFIGURED = False


def configure_rootrecord_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    log_dir = data_dir() / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    log_path = log_dir / "rootrecord.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    try:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass

    # Console: useful in dev; frozen builds often have no console — harmless if missing.
    if getattr(sys, "frozen", False):
        return
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    root.addHandler(sh)
