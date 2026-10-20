"""Helpers for tests that need an external program: a fake one behind a shim, a descriptor for it, and a few process helpers.

The fake is ``tests/fake_exe.py`` (Python).  ``ENG_TOOLKIT_<NAME>`` must name something the system can start: on Windows a ``.cmd``
file that calls the Python interpreter with the script, elsewhere a ``/bin/sh`` script that does the same.  The shim passes the
path of a JSON configuration as the script's first argument and everything else through unchanged, so the script records the exact
argument list the toolkit built.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from eng_toolkit.licenses import TIER_GPL, ExternalTool

FAKE = Path(__file__).with_name("fake_exe.py")
ENV_VAR = "ENG_TOOLKIT_FAKE_TOOL"

TOOL = ExternalTool(
    name="fake-tool",
    license="GPL-3.0-or-later",
    tier=TIER_GPL,
    role="a fake program for tests",
    env_var=ENV_VAR,
    executables=("fake-tool.exe", "fake-tool"),
    project="https://example.invalid/fake-tool",
    domains=("tests",),
    version_args=("--version",),
    version_regex=r"fake-tool (\d+(?:\.\d+)+)",
)


@dataclass
class Fake:
    shim: Path
    config_path: Path
    calls_path: Path
    config: dict

    def calls(self) -> list[dict]:
        if not self.calls_path.exists():
            return []
        return [json.loads(line) for line in self.calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def runs(self) -> list[dict]:
        """The calls that were not the version query."""
        return [c for c in self.calls() if c["argv"] != self.config.get("version_args")]

    def set(self, **changes) -> None:
        self.config.update(changes)
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")


def install_fake(tmp_path: Path, monkeypatch, *, mode: str = "ok", env_var: str = ENV_VAR, name: str = "fake-tool", **config) -> Fake:
    """Make a fake program in ``tmp_path/fake`` and point ``env_var`` at it.  ``config`` is what the script reads (see its docstring)."""
    home = tmp_path / "fake"
    home.mkdir(exist_ok=True)
    values = {"mode": mode, "calls": str(home / "calls.jsonl"), "version_args": ["--version"], "version_text": "fake-tool 1.2.3", **config}
    config_path = home / "config.json"
    config_path.write_text(json.dumps(values), encoding="utf-8")
    if sys.platform == "win32":
        shim = home / f"{name}.cmd"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{FAKE}" "{config_path}" %*\r\nexit /b %ERRORLEVEL%\r\n', encoding="ascii")
    else:
        shim = home / name
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "{config_path}" "$@"\n', encoding="ascii")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv(env_var, str(shim))
    return Fake(shim, config_path, home / "calls.jsonl", values)


def no_tool(monkeypatch, tmp_path: Path, tool: ExternalTool = TOOL) -> None:
    """The program is nowhere: no variable and a PATH that holds only an empty directory."""
    empty = tmp_path / "empty-bin"
    empty.mkdir(exist_ok=True)
    monkeypatch.delenv(tool.env_var, raising=False)
    monkeypatch.setenv("PATH", str(empty))


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def wait_dead(pid: int, seconds: float = 10.0) -> bool:
    import time

    deadline = time.monotonic() + seconds
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    return not pid_alive(pid)
