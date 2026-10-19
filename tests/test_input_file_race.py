"""ENG_TOOLKIT_READ_DIRS and the time between checking a path and opening it.

A directory inside the allowed one is swapped, over and over, for a link to a directory outside it.  Whatever ``input_file`` hands to a tool
must be the bytes of a file that was inside when it was opened: the tool must never read the outside file, however the swap falls.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import JobHandle
from eng_toolkit.workspace import Workspace

SECRET = b"OUTSIDE_SECRET_9"
INSIDE = b"INSIDE_FILE_1"
SECONDS = 4.0


def make_dir_link(link: Path, target: Path) -> bool:
    """A directory junction on Windows (no privilege needed), a symbolic link elsewhere; False when neither can be made."""
    try:
        if sys.platform == "win32":
            done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
            return done.returncode == 0 and link.is_dir()
        os.symlink(target, link, target_is_directory=True)
        return link.is_dir()
    except OSError:
        return False


@pytest.fixture
def arena(tmp_path, monkeypatch):
    allowed, outside = tmp_path / "allowed", tmp_path / "outside"
    for d in (allowed, outside, allowed / "swap"):
        d.mkdir()
    (outside / "model.step").write_bytes(SECRET)
    (allowed / "swap" / "model.step").write_bytes(INSIDE)
    if not make_dir_link(allowed / "other", outside):
        pytest.skip("this machine cannot make a directory link")
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(allowed))
    return allowed, outside


class Flipper(threading.Thread):
    """Exchanges allowed/swap and allowed/other as fast as it can: one is a real directory, the other a link to the outside."""

    def __init__(self, allowed: Path):
        super().__init__(daemon=True)
        self.allowed, self.stop, self.flips = allowed, False, 0

    def run(self):
        a, b, hold = self.allowed / "swap", self.allowed / "other", self.allowed / "hold"
        while not self.stop:
            step = 0
            try:
                os.rename(a, hold)
                step = 1
                os.rename(b, a)
                step = 2
                os.rename(hold, b)
                self.flips += 1
            except OSError:  # a file inside is open at this moment (Windows): put things back and try again
                try:
                    if step == 1:
                        os.rename(hold, a)
                    elif step == 2:
                        os.rename(hold, b)
                except OSError:
                    pass


def handle() -> JobHandle:
    return JobHandle(Workspace().new_job("race"))


def test_no_read_through_a_flipping_link_ever_gives_the_outside_file(arena):
    allowed, outside = arena
    flipper = Flipper(allowed)
    flipper.start()
    leaks = reads = refused = 0
    started = time.time()
    path = str(allowed / "swap" / "model.step")
    try:
        while time.time() - started < SECONDS:
            try:
                returned = handle().input_file(path, param="model_path", extensions=(".step",))
            except ToolFailure as exc:
                assert exc.family in ("input.path_not_allowed", "input.file_not_found"), exc.family
                refused += 1
                continue
            reads += 1
            try:
                if returned.read_bytes() == SECRET:  # what a tool reads from the path it was given
                    leaks += 1
            except OSError:
                pass  # the path vanished under the swap: the tool would fail, it would not leak
    finally:
        flipper.stop = True
        flipper.join(timeout=5)
    assert flipper.flips > 20, f"the swap did not happen often enough to mean anything ({flipper.flips} flips)"
    assert reads + refused >= 100, f"too few attempts to mean anything ({reads} reads, {refused} refused)"
    assert leaks == 0, f"{leaks} of {reads} reads gave the outside file"
    assert refused > 0, "the link was never seen: the test does not exercise the race"


def test_the_provenance_hash_is_the_hash_of_the_bytes_the_tool_gets(arena):
    allowed, _ = arena
    h = handle()
    returned = h.input_file(str(allowed / "swap" / "model.step"), param="model_path", extensions=(".step",))
    import hashlib

    assert h.input_files[0].sha256 == hashlib.sha256(returned.read_bytes()).hexdigest()
    assert Path(h.input_files[0].path) == (allowed / "swap" / "model.step").resolve()
    assert returned.name.endswith("model.step") and returned.suffix == ".step"


def test_a_file_that_grew_past_the_cap_while_it_was_read_is_refused(tmp_path):
    path = tmp_path / "grow.step"
    path.write_bytes(b"x" * 100)
    with pytest.raises(ToolFailure) as caught:
        handle().input_file(str(path), param="model_path", extensions=(".step",), max_bytes=50)
    assert caught.value.family == "input.too_large"


def test_the_drawing_reader_never_leaks_through_the_swap(arena):
    """The same race through the real tool, a few calls (each starts a child process)."""
    from eng_toolkit.domains.drawing.tools import read

    import raw_dxf as R

    allowed, outside = arena
    for d in (allowed / "swap", outside):
        R.write(d / "d.dxf", R.document(R.V2000, insunits=4, entities=(R.text("INSIDE" if d == allowed / "swap" else "OUTSIDE_SECRET_9", (0, 0)),)))
    flipper = Flipper(allowed)
    flipper.start()
    fn = getattr(read, "fn", read)
    leaks = 0
    try:
        for _ in range(8):
            result = fn(path=str(allowed / "swap" / "d.dxf"))
            if "OUTSIDE_SECRET_9" in json.dumps(result.model_dump(mode="json"), ensure_ascii=False):
                leaks += 1
    finally:
        flipper.stop = True
        flipper.join(timeout=5)
    assert leaks == 0
