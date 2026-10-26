"""Find and run a program the user installed themselves, as a separate process with hard limits.

For programs the toolkit may use only as separate processes (``gpl-isolated`` in :mod:`eng_toolkit.licenses`: LibreDWG's
``dwg2dxf``, Gmsh, CalculiX, KiCad; also permissive ones that are native programs: ngspice, Yosys).  Nothing here imports,
links or ships a program.  ``docs/external/design.md`` is the long version.

* A program is described once by a :class:`~eng_toolkit.licenses.ExternalTool` (names, environment variable, places to look,
  child ``PATH`` additions, version probe, licence and tier).  A domain registers its descriptors in its own ``engines.py``.
* :func:`locate` finds it: the environment variable first (and only that, if it is set), then ``PATH``, then the descriptor's
  well-known locations.  Never from a document, never from the working directory.
* :func:`run` starts it with a fixed argument list (no shell), the job directory as working directory, a scrubbed environment,
  no stdin, a time limit that kills the whole process tree, a cap on what it writes, a cap on what it prints, and a memory cap
  where the platform has one; it returns a :class:`Record` (exit code, time, tails of both streams, the files it created).
* :func:`probe` is the row ``toolkit_doctor`` shows.

The program is trusted with nothing: the caller validates what it wrote.  Nothing here is a sandbox (see the design note).
"""

from __future__ import annotations

import contextlib
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eng_toolkit.errors import ToolFailure
from eng_toolkit.licenses import ExternalTool

STREAM_CAP = 1_000_000  # bytes of stdout, and of stderr, a program may print before it is stopped
TAIL_CHARS = 3000  # how much of the end of each stream a Record keeps
MESSAGE_CHARS = 300  # how much of stderr an error message quotes
POLL_S = 0.05
MAX_RECORDED_FILES = 500
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MEMORY_MB = 2048
DEFAULT_OUTPUT_BYTES = 50_000_000
VERSION_TIMEOUT_S = 10.0
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
ALIAS_OVER = 180  # a job directory path longer than this (Windows only) is shown to the program through a short junction
_WINDOWS_PROGRAM = (".exe", ".com")  # a file with another suffix (.cmd, .bat) is started only when ENV_VAR names it

# Variables copied from the server to the child on top of the ones built for it.  Everything else (API keys, tokens, the
# toolkit's own settings, the user's PATH) is left out.
_SYSTEM_ENV_WINDOWS = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS")
_SYSTEM_ENV_POSIX = ("LANG", "LC_ALL", "LC_CTYPE")


@dataclass(frozen=True)
class Located:
    path: str
    source: str  # "env" (the descriptor's variable), "path" or "location"
    via: str  # the variable's name, the PATH directory, or the location pattern that matched


@dataclass(frozen=True)
class FileRecord:
    name: str  # relative to the working directory, with "/"
    bytes: int
    sha256: str
    status: str  # "created" or "modified"


@dataclass(frozen=True)
class Record:
    program: str
    source: str
    returncode: int
    seconds: float
    stdout_tail: str
    stderr_tail: str
    files: tuple[FileRecord, ...]
    files_truncated: bool
    memory_cap_applied: bool
    limits: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "program": self.program,
            "found_via": self.source,
            "returncode": self.returncode,
            "seconds": round(self.seconds, 3),
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "files": [{"name": f.name, "bytes": f.bytes, "sha256": f.sha256, "status": f.status} for f in self.files],
            "files_truncated": self.files_truncated,
            "limits": {**self.limits, "memory_cap_applied": self.memory_cap_applied},
        }


# --------------------------------------------------------------------------- finding it


def install_hint(tool: ExternalTool) -> str:
    """How to get the program: its project page and the two ways to tell the toolkit where it is.  Never a download or install command."""
    return (
        f"Install {tool.name} yourself from {tool.project} (it is {tool.license} software that the toolkit only starts as a separate program, "
        f"never bundles) and either put {_names(tool)} on PATH or set {tool.env_var} to its full path."
    )


def _names(tool: ExternalTool) -> str:
    return " or ".join(tool.executables)


def unavailable(tool: ExternalTool, why: str, **details: Any) -> ToolFailure:
    return ToolFailure(
        "engine.unavailable",
        f"{tool.name} could not be found ({why}). {install_hint(tool)}",
        {"tool": tool.name, "env_var": tool.env_var, "project": tool.project, "reason": why, **details},
    )


def _natural(text: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def _usable(path: Path, windows: bool) -> bool:
    if not path.is_file():
        return False
    if windows:
        return path.suffix.lower() in _WINDOWS_PROGRAM
    return os.access(path, os.X_OK)


def _expand(pattern: str) -> list[str]:
    """Directories matching a location pattern, the highest version first; none for a pattern with an unset variable or a relative one."""
    text = os.path.expandvars(os.path.expanduser(pattern))
    if "%" in text or "$" in text or not os.path.isabs(text):
        return []
    found = [p for p in glob.glob(text) if os.path.isdir(p)]
    return sorted(found, key=_natural, reverse=True)


def locate(tool: ExternalTool) -> tuple[Located | None, str]:
    """``(where, why_not)``: the program and how it was found, or ``None`` and a sentence saying what was looked at.

    1. ``tool.env_var`` set means exactly that file.  It must be an absolute path to an existing file; otherwise the answer is
       "not found" and nothing else is consulted, because a wrong setting the operator made must not be hidden by another copy.
    2. The directories of ``PATH``, in order, for the exact names of ``tool.executables``.  Relative and empty entries (the
       current directory) are skipped, so a program planted in the working directory is never started.
    3. The directories of ``tool.locations``, in order, the highest version first.

    On Windows a file found in steps 2 and 3 must end in ``.exe`` or ``.com``; a ``.cmd`` or ``.bat`` is started only through
    the variable.  On POSIX the executable bit is required.
    """
    windows = sys.platform == "win32"
    raw = os.environ.get(tool.env_var, "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            return None, f"{tool.env_var} is set to {raw!r}, which is not a full path"
        if not candidate.is_file():
            return None, f"{tool.env_var} is set to {raw!r}, which is not a file"
        return Located(str(candidate), "env", tool.env_var), ""

    def search(directories: list[str], source: str, via=lambda d: d) -> Located | None:
        for directory in directories:
            if not directory or not os.path.isabs(directory):
                continue
            for name in tool.executables:
                candidate = Path(directory) / name
                if _usable(candidate, windows):
                    return Located(str(candidate), source, via(directory))
        return None

    found = search(os.environ.get("PATH", "").split(os.pathsep), "path")
    if found:
        return found, ""
    for pattern in tool.locations:
        found = search(_expand(pattern), "location", lambda _d, pattern=pattern: pattern)
        if found:
            return found, ""
    looked = f"{_names(tool)} is not on PATH, {tool.env_var} is not set"
    return None, looked + (f" and it is not in the {len(tool.locations)} places the toolkit knows" if tool.locations else "")


# --------------------------------------------------------------------------- the child's world


def _system_directories() -> list[str]:
    if sys.platform == "win32":
        root = os.environ.get("SystemRoot") or r"C:\Windows"
        return [str(Path(root) / "System32"), root]
    return ["/usr/bin", "/bin"]


def path_additions(tool: ExternalTool, program: str) -> list[str]:
    """The ``path_additions`` of the descriptor with ``{bin}`` replaced by the folder holding ``program``; only directories that exist."""
    here = str(Path(program).resolve().parent)
    out: list[str] = []
    for item in tool.path_additions:
        candidate = os.path.normpath(item.replace("{bin}", here))
        if os.path.isdir(candidate) and candidate not in out:
            out.append(candidate)
    return out


def child_environment(tool: ExternalTool, program: str, workdir: Path, temp: Path | None = None) -> dict[str, str]:
    """The environment of the child: system variables, ``PATH`` (additions, the program's own folder, the system directories),
    ``TEMP``/``TMP``/``TMPDIR`` (``temp``, else ``workdir/tmp``), the home directories inside ``workdir``, the descriptor's
    ``keep_env`` and ``env``."""
    windows = sys.platform == "win32"
    wanted = {name.upper() for name in (_SYSTEM_ENV_WINDOWS if windows else _SYSTEM_ENV_POSIX)}
    env = {key: value for key, value in os.environ.items() if key.upper() in wanted}
    for name in tool.keep_env:
        if name in os.environ:
            env[name] = os.environ[name]
    directories = [*path_additions(tool, program), str(Path(program).resolve().parent), *_system_directories()]
    env["PATH"] = os.pathsep.join(dict.fromkeys(directories))
    if temp is None:
        temp = workdir / "tmp"
    home = workdir / "home"
    for d in (temp, home):
        d.mkdir(exist_ok=True)
    for name in ("TEMP", "TMP", "TMPDIR"):
        env[name] = str(temp)
    env["HOME"] = str(home)
    if windows:
        env["USERPROFILE"] = str(home)
        env["APPDATA"] = str(home / "AppData" / "Roaming")
        env["LOCALAPPDATA"] = str(home / "AppData" / "Local")
        for name in ("APPDATA", "LOCALAPPDATA"):
            Path(env[name]).mkdir(parents=True, exist_ok=True)
    else:
        env["XDG_CONFIG_HOME"] = str(home / ".config")
        env["XDG_CACHE_HOME"] = str(home / ".cache")
        env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env.update(dict(tool.env))
    return env


@contextlib.contextmanager
def _scratch():
    """A short, unique, private folder for the program's temporary files, removed afterwards.

    It lives directly under the system temporary folder (a name like ``etk1a2b3c4d``), not inside the job directory: a program such
    as Yosys starts ABC, which writes ``<TEMP>/yosys-abc-XXXXXX/stdcells.genlib``, and a job directory below a long workspace path
    would push that name over the 260-character limit of Windows programs.
    """
    path = Path(tempfile.mkdtemp(prefix="etk"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _alias(workdir: Path, scratch: Path) -> Path | None:
    """On Windows, a junction ``<scratch>/j`` to a long ``workdir`` (the program then sees a short path), else ``None``.

    The 260-character limit is checked on the path a program passes to Windows, not on where a junction leads, so everything
    below ``workdir`` stays reachable.  ``None`` when no alias is needed or none can be made; the program then gets ``workdir``.
    """
    if sys.platform != "win32" or len(str(workdir)) <= ALIAS_OVER:
        return None
    try:
        import _winapi

        link = scratch / "j"
        _winapi.CreateJunction(str(workdir), str(link))
        return link
    except (ImportError, AttributeError, OSError):
        return None


def _drop_alias(link: Path | None) -> None:
    if link is not None:
        try:
            os.rmdir(link)  # removes the junction only, never what it leads to
        except OSError:
            pass


def clean_text(raw: bytes | str, limit: int, hide: tuple[tuple[str, str], ...] = ()) -> str:
    """Printable text of at most ``limit`` characters from the end of ``raw``.

    Each ``(path, label)`` of ``hide`` is replaced by its label wherever it appears (case-insensitively, with either slash and
    with doubled backslashes), longest paths first.
    """
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    text = _CONTROL.sub(" ", text)
    for path, label in sorted(hide, key=lambda item: -len(item[0])):
        if path:
            for form in {path, path.replace("\\", "/"), path.replace("\\", "\\\\")}:
                text = re.sub(re.escape(form), lambda _m, label=label: label, text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def _hidden_paths(workdir: Path, *aliases: Path | None) -> tuple[tuple[str, str], ...]:
    pairs = [(str(workdir), "<job>"), (str(workdir.resolve()), "<job>")]
    pairs.extend((str(alias), "<job>") for alias in aliases if alias is not None)
    for home in {os.path.expanduser("~"), os.environ.get("USERPROFILE", "")}:
        if home and home != "~":
            pairs.append((home, "<home>"))
    return tuple(pairs)


# --------------------------------------------------------------------------- running it


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Relative name -> (size, mtime_ns) of every file below ``root``."""
    out: dict[str, tuple[int, int]] = {}
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    st = os.stat(entry.path, follow_symlinks=False)  # not entry.stat(): on Windows that size is stale for a file still open for writing
                    out[Path(entry.path).relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
            except OSError:
                continue
    return out


def _grown(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> int:
    """Bytes written since ``before``: the size of every new file and the size increase of every file that grew."""
    total = 0
    for name, (size, _mtime) in after.items():
        old = before.get(name)
        total += size if old is None else max(0, size - old[0])
    return total


def _file_records(root: Path, before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> tuple[tuple[FileRecord, ...], bool]:
    records: list[FileRecord] = []
    changed = sorted(name for name, stat in after.items() if before.get(name) != stat)
    for name in changed[:MAX_RECORDED_FILES]:
        digest = hashlib.sha256()
        try:
            with open(root / name, "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
        except OSError:
            continue
        records.append(FileRecord(name, after[name][0], digest.hexdigest(), "created" if name not in before else "modified"))
    return tuple(records), len(changed) > MAX_RECORDED_FILES


def _confine(args: list[Any], workdir: Path, alias: Path | None = None) -> list[str]:
    """The argument list as strings.  A :class:`~os.PathLike` argument must lie inside ``workdir``; a string is passed as it is.

    With ``alias`` (a short junction to ``workdir``) the part of an argument that names ``workdir`` is written with the alias."""
    base = os.path.normcase(str(workdir.resolve()))
    out: list[str] = []
    for arg in args:
        if isinstance(arg, os.PathLike):
            resolved = os.path.normcase(str(Path(arg).resolve()))
            if resolved != base and not resolved.startswith(base.rstrip(os.sep) + os.sep):
                raise ToolFailure(
                    "input.path_not_allowed",
                    "a program is only given files inside its job directory; the toolkit copies inputs there first",
                    {"path": str(arg), "workdir": str(workdir)},
                )
        text = os.fspath(arg) if isinstance(arg, os.PathLike) else str(arg)
        if "\x00" in text:
            raise ToolFailure("input.invalid", "an argument holds a NUL character", {})
        if alias is not None:
            for form in dict.fromkeys((str(workdir), str(workdir.resolve()))):
                text = re.sub(re.escape(form), lambda _m: str(alias), text, flags=re.IGNORECASE)
        out.append(text)
    return out


def run(
    tool: ExternalTool,
    args: list[Any],
    *,
    workdir: Path,
    located: Located | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    memory_mb: int | None = DEFAULT_MEMORY_MB,
    max_output_bytes: int = DEFAULT_OUTPUT_BYTES,
    check: bool = True,
) -> Record:
    """Run ``tool`` with ``args`` in ``workdir`` and return what happened.

    ``args`` is the whole argument list after the program name, used as given (no shell, no splitting); every
    :class:`~os.PathLike` in it must lie inside ``workdir``.  Raises :class:`ToolFailure`:

    * ``engine.unavailable``: the program is not found;
    * ``engine.timeout``: still running after ``timeout_s``; it and what it started are killed;
    * ``engine.resource_limit``: it wrote more than ``max_output_bytes`` in total below ``workdir`` (counted while it runs),
      or printed more than :data:`STREAM_CAP` bytes to one stream; it is killed;
    * ``engine.failure``: it could not be started, or (with ``check``) it exited with a non-zero code.  The message quotes
      the end of stderr, and the details hold the :class:`Record`.

    With ``check=False`` the exit code is only reported.  The program is never run twice.
    """
    if located is None:
        located, why = locate(tool)
        if located is None:
            raise unavailable(tool, why)
    workdir = Path(os.path.abspath(workdir))  # the program starts inside it, so nothing handed to it may stay relative
    with _scratch() as scratch:
        link = _alias(workdir, scratch)
        try:
            return _run_in(
                tool, args, workdir=workdir, located=located, timeout_s=timeout_s, memory_mb=memory_mb, max_output_bytes=max_output_bytes,
                check=check, temp=scratch / "t", alias=link,
            )
        finally:
            _drop_alias(link)


def _run_in(
    tool: ExternalTool,
    args: list[Any],
    *,
    workdir: Path,
    located: Located,
    timeout_s: float,
    memory_mb: int | None,
    max_output_bytes: int,
    check: bool,
    temp: Path,
    alias: Path | None,
) -> Record:
    """:func:`run` once the temporary folder (``temp``) exists and the short alias of a long ``workdir`` (``alias``) is made."""
    temp.mkdir(exist_ok=True)
    start_dir = alias if alias is not None else workdir
    argv = [located.path, *_confine(args, workdir, alias)]
    env = child_environment(tool, located.path, start_dir, temp)
    hide = (*_hidden_paths(workdir, alias), (str(temp.parent), "<tmp>"))
    before = _snapshot(workdir)
    limits = {"timeout_s": timeout_s, "memory_mb": memory_mb, "max_output_bytes": max_output_bytes}
    started = time.monotonic()
    memory_cap = False
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(  # noqa: S603 - fixed list, no shell
                argv,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                cwd=str(start_dir),
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                start_new_session=sys.platform != "win32",
            )
        except OSError as exc:
            raise ToolFailure("engine.failure", f"{tool.name} could not be started: {exc.strerror or exc}", {"tool": tool.name, "errno": exc.errno}) from exc
        guard = _MemoryGuard(proc, memory_mb, max_output_bytes)
        try:
            while True:
                try:
                    proc.wait(timeout=POLL_S)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if time.monotonic() - started > timeout_s:
                    _kill_tree(proc)
                    raise ToolFailure("engine.timeout", f"{tool.name} took longer than {timeout_s:g} s and was stopped", {"tool": tool.name, **limits})
                over = _over(out, err, before, workdir, max_output_bytes, temp)
                if over:
                    _kill_tree(proc)
                    raise _limit_failure(tool, over, max_output_bytes, limits)
            over = _over(out, err, before, workdir, max_output_bytes, temp)  # written faster than the polling interval
            if over:
                raise _limit_failure(tool, over, max_output_bytes, limits)
        finally:
            if proc.poll() is None:
                _kill_tree(proc)
            memory_cap = guard.applied
            guard.close()
        stdout_tail, stderr_tail = _tail(out), _tail(err)
    after = _snapshot(workdir)
    files, truncated = _file_records(workdir, before, after)
    record = Record(
        program=located.path,
        source=f"{located.source}: {located.via}",
        returncode=proc.returncode,
        seconds=time.monotonic() - started,
        stdout_tail=clean_text(stdout_tail, TAIL_CHARS, hide),
        stderr_tail=clean_text(stderr_tail, TAIL_CHARS, hide),
        files=files,
        files_truncated=truncated,
        memory_cap_applied=memory_cap,
        limits=limits,
    )
    if check and record.returncode != 0:
        said = clean_text(stderr_tail, MESSAGE_CHARS, hide) or clean_text(stdout_tail, MESSAGE_CHARS, hide)
        raise ToolFailure(
            "engine.failure",
            f"{tool.name} exited with code {record.returncode}" + (f" and said: {said}" if said else " and printed nothing"),
            {"tool": tool.name, "returncode": record.returncode, "stderr_tail": record.stderr_tail, "stdout_tail": record.stdout_tail, "seconds": round(record.seconds, 3)},
        )
    return record


def _over(out, err, before: dict[str, tuple[int, int]], workdir: Path, cap: int, temp: Path | None = None) -> tuple[str, int] | None:
    for stream, handle in (("stdout", out), ("stderr", err)):
        size = os.fstat(handle.fileno()).st_size
        if size > STREAM_CAP:
            return stream, size
    written = _grown(before, _snapshot(workdir))
    if temp is not None:
        written += _grown({}, _snapshot(temp))  # what it leaves in its temporary folder counts too
    return ("output", written) if written > cap else None


def _limit_failure(tool: ExternalTool, over: tuple[str, int], cap: int, limits: dict[str, Any]) -> ToolFailure:
    stream, size = over
    limit = cap if stream == "output" else STREAM_CAP
    where = "files in its job directory" if stream == "output" else f"to {stream}"
    return ToolFailure(
        "engine.resource_limit",
        f"{tool.name} wrote more than {limit} bytes ({where}) and was stopped",
        {"tool": tool.name, "stream": stream, "bytes": size, "limit_bytes": limit, **limits},
    )


def _tail(handle) -> bytes:
    size = handle.seek(0, os.SEEK_END)
    handle.seek(max(0, size - 4 * TAIL_CHARS))
    return handle.read()


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the program and what it started: a launcher or a ``.cmd`` shim starts the real program as a child."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        import signal

        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - a killed process ends
        pass


class _MemoryGuard:
    """A memory cap on a started process: a Job Object on Windows (closed, it also kills what is left), ``prlimit`` on Linux.

    Applied just after the process starts, so a program could allocate in the first milliseconds before the cap holds.  Where
    the platform has no way (macOS), ``applied`` stays False and the caller reports it.
    """

    def __init__(self, proc: subprocess.Popen, memory_mb: int | None, output_cap: int | None) -> None:
        self.applied = False
        self._job = None
        if not memory_mb:
            return
        limit = memory_mb * 1024 * 1024
        try:
            if sys.platform == "win32":
                self._job = _windows_job(proc, limit)
                self.applied = self._job is not None
            elif sys.platform.startswith("linux"):
                import resource

                resource.prlimit(proc.pid, resource.RLIMIT_AS, (limit, limit))
                if output_cap is not None:
                    resource.prlimit(proc.pid, resource.RLIMIT_FSIZE, (output_cap + 1, output_cap + 1))
                self.applied = True
        except (OSError, ValueError, ImportError, AttributeError):
            self.applied = False

    def close(self) -> None:
        if self._job is not None:
            import ctypes

            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._job)
            self._job = None


def _windows_job(proc: subprocess.Popen, limit: int):
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("r", "w", "o", "rb", "wb", "ob")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("Basic", BasicLimits),
            ("Io", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = ExtendedLimits()
    info.Basic.LimitFlags = 0x100 | 0x2000  # JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    info.ProcessMemoryLimit = limit
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # JobObjectExtendedLimitInformation
        kernel32.CloseHandle(job)
        return None
    process = kernel32.OpenProcess(0x0100 | 0x0001, False, proc.pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    if not process:
        kernel32.CloseHandle(job)
        return None
    assigned = kernel32.AssignProcessToJobObject(job, process)
    kernel32.CloseHandle(process)
    if not assigned:
        kernel32.CloseHandle(job)
        return None
    return job


# --------------------------------------------------------------------------- version and the doctor row


def version(tool: ExternalTool, located: Located) -> str | None:
    """The version the program prints for ``tool.version_args`` (group 1 of ``tool.version_regex``, else the whole match), or ``None``.  Never raises."""
    try:
        with tempfile.TemporaryDirectory(prefix="eng-toolkit-ver-") as scratch:
            record = run(tool, list(tool.version_args), workdir=Path(scratch), located=located, timeout_s=VERSION_TIMEOUT_S, memory_mb=512, check=False)
    except (ToolFailure, OSError):
        return None
    match = re.search(tool.version_regex, f"{record.stdout_tail}\n{record.stderr_tail}")
    if not match:
        return None
    return (match.group(1) if match.groups() else match.group(0))[:120]


def probe(tool: ExternalTool) -> dict[str, Any]:
    """One row for ``toolkit_doctor``: found or not, where and how, version, tier.  Never raises; an optional program that is absent is not a failure."""
    found, why_not = locate(tool)
    row: dict[str, Any] = {
        "name": tool.name,
        "role": tool.role,
        "domains": list(tool.domains),
        "declared_license": tool.license,
        "declared_tier": tool.tier,
        "optional": tool.optional,
        "found": found is not None,
        "path": found.path if found else None,
        "found_via": f"{found.source}: {found.via}" if found else None,
        "version": version(tool, found) if found else None,
        "env_var": tool.env_var,
        "project": tool.project,
    }
    row["status"] = "ok" if found else ("not-installed (optional)" if tool.optional else "missing")
    if not found:
        row["reason"] = why_not
        row["install"] = install_hint(tool)
    return row


__all__ = [
    "FileRecord",
    "Located",
    "Record",
    "child_environment",
    "clean_text",
    "install_hint",
    "locate",
    "path_additions",
    "probe",
    "run",
    "unavailable",
    "version",
]
