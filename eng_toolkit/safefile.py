"""Open a caller's file once, learn where the open file really is, and copy exactly those bytes.

A path is a name that can point somewhere else a moment later: checking it and then opening it (or handing it to another
process that opens it) leaves a gap in which a directory can be swapped for a link to somewhere outside the allowed
directories.  Here the file is opened once, the real location of the *open file* is asked from the operating system (that
cannot be swapped any more), and the bytes are copied from the open file.  Tools then read the copy.
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path

CHUNK = 1 << 20


class TooLarge(Exception):
    def __init__(self, size: int):
        super().__init__(size)
        self.size = size


def open_regular(path: Path) -> int:
    """A read-only descriptor of a regular file; OSError when the path is not one (a directory, a device, nothing)."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
    except BaseException:
        os.close(fd)
        raise
    return fd


def final_path(fd: int, path: Path) -> str:
    """The real path of the file behind an open descriptor: every link, junction, short name and ``..`` resolved by the system."""
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel32.GetFinalPathNameByHandleW(msvcrt.get_osfhandle(fd), buffer, len(buffer), 0)  # normalized name, DOS volume name
        if length == 0 or length >= len(buffer):
            raise OSError("cannot learn the path of an open file")
        text = buffer.value
        if text.startswith("\\\\?\\UNC\\"):
            return "\\\\" + text[8:]
        return text[4:] if text.startswith("\\\\?\\") else text
    if sys.platform.startswith("linux"):
        return os.readlink(f"/proc/self/fd/{fd}")
    if sys.platform == "darwin":
        import fcntl

        return os.fsdecode(fcntl.fcntl(fd, 50, b"\0" * 1024).split(b"\0", 1)[0])  # F_GETPATH
    real = os.path.realpath(path)  # no system call for it: resolve, then check that the result is the file that is open
    if not os.path.samestat(os.fstat(fd), os.stat(real)):
        raise OSError("the path changed while it was being checked")
    return real


def within(final: str, roots: list[Path]) -> bool:
    """True when the real path ``final`` lies below one of ``roots`` (the roots are resolved here; case-insensitive on Windows)."""
    here = os.path.normcase(final)
    for root in roots:
        base = os.path.normcase(str(root.resolve()))
        if here == base or here.startswith(base.rstrip(os.sep) + os.sep):
            return True
    return False


def copy_from(fd: int, dest: Path, max_bytes: int | None) -> tuple[int, str]:
    """Copy the open file to ``dest`` and return (bytes, sha256 hex) of what was copied; TooLarge if it is or grows over ``max_bytes``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest, total = hashlib.sha256(), 0
    with dest.open("wb") as out:
        while True:
            block = os.read(fd, CHUNK)
            if not block:
                break
            total += len(block)
            if max_bytes is not None and total > max_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                raise TooLarge(total)
            digest.update(block)
            out.write(block)
    return total, digest.hexdigest()
