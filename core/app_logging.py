r"""Run logging utilities for OrchardBridge.

Every GUI run gets exactly one timestamped log file under:
    %LOCALAPPDATA%\OrchardBridge\Logs

The logger mirrors stdout/stderr to both the terminal and the log file.  A
``latest.txt`` snapshot is created only when the user creates a bug report or
when the process is really exiting.  Hiding/minimizing the window does not close
or rotate the log.
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import sys
import traceback
from pathlib import Path

APP_NAME = "OrchardBridge"
_current_log_path: Path | None = None
_log_file = None
_original_stdout = sys.stdout
_original_stderr = sys.stderr


def _base_local_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root)
    return Path.home() / "AppData" / "Local"


def get_log_dir() -> Path:
    path = _base_local_dir() / APP_NAME / "Logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_bug_report_dir() -> Path:
    path = _base_local_dir() / APP_NAME / "BugReports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_current_log_path() -> Path | None:
    return _current_log_path


def snapshot_latest_log() -> Path | None:
    """Return the active run log.

    The newest run_*.log is already the latest log. Keeping one active log path
    also prevents bug reports from accidentally attaching a short helper-process log.
    """
    try:
        if _log_file:
            _log_file.flush()
    except Exception:
        pass
    if _current_log_path and _current_log_path.exists():
        return _current_log_path
    return get_latest_log_path()


def get_latest_log_path() -> Path | None:
    if _current_log_path and _current_log_path.exists():
        return _current_log_path
    logs = sorted(get_log_dir().glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


class _Tee:
    def __init__(self, stream, log_file):
        self._stream = stream
        self._log_file = log_file

    def write(self, data):
        try:
            self._stream.write(data)
            self._stream.flush()
        except Exception:
            pass
        try:
            self._log_file.write(data)
            self._log_file.flush()
        except Exception:
            pass

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass
        try:
            self._log_file.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return self._stream.isatty()
        except Exception:
            return False

    @property
    def encoding(self):
        return getattr(self._stream, "encoding", "utf-8")


def setup_run_logging() -> Path:
    """Start tee logging for the current Python process."""
    global _current_log_path, _log_file
    if _current_log_path and _log_file:
        return _current_log_path

    log_dir = get_log_dir()
    inherited = os.environ.get("IPBT_ACTIVE_LOG")
    if inherited:
        _current_log_path = Path(inherited)
        _current_log_path.parent.mkdir(parents=True, exist_ok=True)
        file_mode = "a"
    else:
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        _current_log_path = log_dir / f"run_{ts}.log"
        os.environ["IPBT_ACTIVE_LOG"] = str(_current_log_path)
        file_mode = "w"
    _log_file = open(_current_log_path, file_mode, encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = _Tee(_original_stdout, _log_file)
    sys.stderr = _Tee(_original_stderr, _log_file)

    print("=" * 72)
    print("OrchardBridge run log" if file_mode == "w" else "OrchardBridge run log - continued process")
    print(f"Started: {_dt.datetime.now().isoformat(timespec='seconds')}")
    print(f"Executable: {sys.executable}")
    print(f"Arguments: {sys.argv}")
    print(f"Working directory: {os.getcwd()}")
    print(f"Log file: {_current_log_path}")
    print("=" * 72)

    def _excepthook(exc_type, exc, tb):
        print("\n[UNCAUGHT EXCEPTION]", file=sys.stderr)
        traceback.print_exception(exc_type, exc, tb)
        try:
            _original_stderr.flush()
        except Exception:
            pass

    sys.excepthook = _excepthook
    return _current_log_path


def close_run_logging() -> None:
    global _log_file
    try:
        print("=" * 72)
        print(f"Finished: {_dt.datetime.now().isoformat(timespec='seconds')}")
        print("=" * 72)
    except Exception:
        pass
    # Do not snapshot latest.txt here.  latest.txt is intentionally created
    # only by the GUI when the user reports a bug or when the user explicitly
    # exits through the normal close path.  This prevents automatic restarts or
    # transient process exits from generating misleading latest.txt files.
    try:
        if _log_file:
            _log_file.flush()
            _log_file.close()
    except Exception:
        pass
    _log_file = None
