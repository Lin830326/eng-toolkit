"""Helpers for the DWG tests: a DWG-looking file, a known DXF, and an installed fake ``dwg2dxf``.

The fake is ``tests/fake_dwg2dxf.py`` (Python) behind a shim, because ``ENG_TOOLKIT_DWG2DXF`` must name something the system can start:
on Windows a ``.cmd`` file that calls the Python interpreter with the script, elsewhere a ``/bin/sh`` script that does the same.  The
shim passes the path of a JSON configuration as the script's first argument and everything else through unchanged, so the script
records exactly the argument list the toolkit built.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import raw_dxf as R

FAKE = Path(__file__).with_name("fake_dwg2dxf.py")
DWG_HEADER = {"R12": b"AC1009", "R2000": b"AC1015", "R2004": b"AC1018", "R2007": b"AC1021", "R2010": b"AC1024", "R2013": b"AC1027", "R2018": b"AC1032"}


def dwg_bytes(code: bytes = b"AC1032", size: int = 400) -> bytes:
    """The start of a DWG (six version bytes, then the bytes a real file has around them) and filler; the fake never parses it."""
    return code + b"\x00\x00\x00\x00\x00\x06\x01" + bytes((i * 7) % 251 for i in range(size))


def write_dwg(path: Path, code: bytes = b"AC1032", size: int = 400) -> Path:
    path.write_bytes(dwg_bytes(code, size))
    return path


def known_dxf(path: Path) -> Path:
    """A DXF with one 100 mm line in millimetres: 283.465 pt in a PDF at 1:1."""
    R.write(path, R.document(R.V2000, insunits=4, entities=(R.line((0, 0), (100, 0)),)))
    return path


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

    def conversions(self) -> list[dict]:
        """The calls that were not ``--version``."""
        return [c for c in self.calls() if c["argv"] != ["--version"]]

    def set(self, **changes) -> None:
        self.config.update(changes)
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")


def install_fake(tmp_path: Path, monkeypatch, *, mode: str = "copy", name: str = "dwg2dxf", **config) -> Fake:
    """Make a fake dwg2dxf in ``tmp_path/fake`` and point ``ENG_TOOLKIT_DWG2DXF`` at it.  ``config`` is what the script reads (see its docstring)."""
    home = tmp_path / "fake"
    home.mkdir(exist_ok=True)
    dxf = known_dxf(home / "known.dxf")
    values = {"mode": mode, "dxf": str(dxf), "calls": str(home / "calls.jsonl"), "version": "dwg2dxf 0.13.3 (fake)", **config}
    config_path = home / "config.json"
    config_path.write_text(json.dumps(values), encoding="utf-8")
    if sys.platform == "win32":
        shim = home / f"{name}.cmd"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{FAKE}" "{config_path}" %*\r\nexit /b %ERRORLEVEL%\r\n', encoding="ascii")
    else:
        shim = home / name
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "{config_path}" "$@"\n', encoding="ascii")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("ENG_TOOLKIT_DWG2DXF", str(shim))
    return Fake(shim, config_path, home / "calls.jsonl", values)


def remove_tool(monkeypatch, tmp_path: Path) -> None:
    """No dwg2dxf anywhere: no variable, a PATH that holds only an empty directory, and no well-known install location (so a LibreDWG
    installed on the machine running the tests cannot be found), for the DWG code and for the doctor."""
    from dataclasses import replace

    from eng_toolkit import licenses
    from eng_toolkit.domains.drawing import dwg

    empty = tmp_path / "empty-bin"
    empty.mkdir(exist_ok=True)
    monkeypatch.delenv("ENG_TOOLKIT_DWG2DXF", raising=False)
    monkeypatch.setenv("PATH", str(empty))
    bare = replace(dwg.TOOL, locations=())
    monkeypatch.setattr(dwg, "TOOL", bare)
    monkeypatch.setitem(licenses.EXTERNAL, "dwg2dxf", bare)


def hide_known_locations(monkeypatch) -> None:
    """Keep the descriptor but drop the well-known locations, so a test that installs a fake is not answered by a real install."""
    from dataclasses import replace

    from eng_toolkit import licenses
    from eng_toolkit.domains.drawing import dwg

    bare = replace(dwg.TOOL, locations=())
    monkeypatch.setattr(dwg, "TOOL", bare)
    monkeypatch.setitem(licenses.EXTERNAL, "dwg2dxf", bare)


from exe_fixtures import pid_alive, wait_dead  # noqa: E402,F401 - shared with the tests of the external layer
