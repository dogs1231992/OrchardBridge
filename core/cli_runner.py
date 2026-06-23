"""Helpers for invoking pymobiledevice3 CLI safely from source or PyInstaller.

In a normal Python run, ``sys.executable -m pymobiledevice3 ...`` is correct.
In a PyInstaller ``--onefile`` executable, however, ``sys.executable`` is the
OrchardBridge executable itself. Launching it with ``-m pymobiledevice3`` would
start another OrchardBridge GUI instead of the CLI. Use the private
``--pymobiledevice3`` dispatch flag handled in ``main.py``.
"""

from __future__ import annotations

import sys


def pymobiledevice3_cmd(*args: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--pymobiledevice3", *args]
    return [sys.executable, "-m", "pymobiledevice3", *args]
