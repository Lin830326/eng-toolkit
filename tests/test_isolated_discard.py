"""The scratch folder of an isolated read is removed without ever replacing the read's own result.

On a GitHub Windows runner a 50 ms timeout killed the child while the real interpreter behind the virtual environment's
launcher still held ``stderr.txt`` open; removing the folder raised WinError 32 and the tool reported ``engine.failure``
instead of ``engine.timeout``.
"""

import sys
import threading
import time

import pytest

from eng_toolkit import isolated


def test_a_free_folder_is_removed(tmp_path):
    folder = tmp_path / "scratch"
    folder.mkdir()
    (folder / "stderr.txt").write_text("x")
    isolated._discard(str(folder))
    assert not folder.exists()


def test_a_folder_that_is_already_gone_is_not_an_error(tmp_path):
    isolated._discard(str(tmp_path / "never-made"))


@pytest.mark.skipif(sys.platform != "win32", reason="an open file blocks its removal only on Windows")
def test_a_file_held_open_for_a_moment_is_waited_for(tmp_path):
    folder = tmp_path / "scratch"
    folder.mkdir()
    held = (folder / "stderr.txt").open("wb")
    threading.Timer(0.4, held.close).start()
    started = time.monotonic()
    isolated._discard(str(folder), patience_s=5.0)
    assert not folder.exists()
    assert 0.3 < time.monotonic() - started < 4.0


@pytest.mark.skipif(sys.platform != "win32", reason="an open file blocks its removal only on Windows")
def test_a_file_that_stays_open_is_given_up_without_an_error(tmp_path):
    folder = tmp_path / "scratch"
    folder.mkdir()
    with (folder / "stderr.txt").open("wb"):
        started = time.monotonic()
        isolated._discard(str(folder), patience_s=0.3)  # must return, not raise
        assert 0.25 < time.monotonic() - started < 3.0
        assert folder.exists()
