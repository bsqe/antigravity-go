"""Frozen PyInstaller executable entry point for AntigravityGoBridge.exe."""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path

from agy_bridge.main import cli


def _configure_frozen_runtime() -> None:
    """Point google-antigravity SDK at the bundled localharness binary when frozen."""
    if not getattr(sys, "frozen", False) or not hasattr(sys, "_MEIPASS"):
        return
    if os.environ.get("ANTIGRAVITY_HARNESS_PATH"):
        return
    binary_name = "localharness.exe" if sys.platform == "win32" else "localharness"
    bundled = Path(getattr(sys, "_MEIPASS")) / "google" / "antigravity" / "bin" / binary_name
    if bundled.is_file():
        os.environ["ANTIGRAVITY_HARNESS_PATH"] = str(bundled)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _configure_frozen_runtime()
    raise SystemExit(cli(sys.argv[1:]))
