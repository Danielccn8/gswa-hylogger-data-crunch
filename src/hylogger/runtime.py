"""Resource paths and subprocess commands in source and frozen Windows builds."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def root() -> Path:
    return (
        Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    )


def command(module: str, *args: str) -> list[str]:
    flag = "--module" if getattr(sys, "frozen", False) else "-m"
    return [sys.executable, flag, module, *args]


def hidden() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
