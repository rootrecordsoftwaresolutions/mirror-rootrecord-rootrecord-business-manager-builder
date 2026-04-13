"""
Root Record — launch the desktop UI (CustomTkinter).

Legacy: this file previously contained a minimal tkinter UI; the full app lives in ui_main.run_app.
"""

from __future__ import annotations

from env_loader import load_rootrecord_env

load_rootrecord_env()

from log_config import configure_rootrecord_logging

configure_rootrecord_logging()


def main() -> None:
    from ui_main import run_app

    run_app()


if __name__ == "__main__":
    main()
