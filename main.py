#!/usr/bin/env python3
"""
OrchardBridge - main entry.

Logging setup is inside main(), not at module import time. On Windows,
ProcessPoolExecutor workers import this module during spawn; doing logging at
import time creates extra run_*.log files. Keeping setup under the __main__ guard
ensures one visible app run gets one log file.
"""
from __future__ import annotations

import sys
import os
import asyncio
import atexit
import multiprocessing

if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _dispatch_pymobiledevice3_cli() -> int:
    """Run pymobiledevice3's CLI inside the frozen executable.

    This prevents the PyInstaller onefile EXE from recursively opening new GUI
    windows when the app needs to run commands such as ``usbmux list`` or
    ``backup2 backup``.
    """
    args = sys.argv[2:]
    sys.argv = ["pymobiledevice3", *args]
    try:
        from pymobiledevice3.__main__ import main as pmd3_main
        result = pmd3_main()
        return int(result or 0)
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        return 0 if code is None else 1
    except Exception as exc:
        print(f"[pymobiledevice3-cli] {exc!r}", file=sys.stderr)
        return 1


def main():
    from core.app_logging import setup_run_logging, close_run_logging

    run_log_path = setup_run_logging()
    atexit.register(close_run_logging)
    print(f"[LOG] Runtime log is being saved to: {run_log_path}")

    from gui.app import BackupApp

    app = BackupApp()
    app.run()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--pymobiledevice3":
        raise SystemExit(_dispatch_pymobiledevice3_cli())
    main()
