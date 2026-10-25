"""Run a function of this package in a child process with a hard time and memory limit.

For parsers of untrusted files (DXF, PDF) that cannot be interrupted from inside: a late child is killed, a child that
dies or runs out of memory is a structured failure, and nothing the child prints or leaves behind reaches the server.

The request goes to the child as JSON on stdin and the answer comes back as JSON in a file, never as a pickle, so the
parent never unpickles what an untrusted file's parser produced.  The child is ``python -P -m eng_toolkit.isolated``
(``-P``: the working directory is not put on ``sys.path``).  Where the platform has a way to cap a process's memory it
does so before it touches the file: a Job Object on Windows, ``RLIMIT_AS`` on Linux; elsewhere only the time limit
applies and ``Outcome.memory_cap_applied`` says so.
"""

from __future__ import annotations

from eng_toolkit import jsonsafe
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eng_toolkit.errors import ToolFailure

TIMEOUT_S = 30.0
MEMORY_MB = 2048
STDERR_TAIL = 2000
# a lone UTF-16 surrogate escape (a valid pair, an astral character, is kept)
_SURROGATE = re.compile(r"(\\u[dD][89abAB][0-9a-fA-F]{2}\\u[dD][c-fC-F][0-9a-fA-F]{2})|\\u[dD][89a-fA-F][0-9a-fA-F]{2}")


def strict_json(value: object) -> str:
    """JSON text of ``value``: ASCII only, no NaN or Infinity, and lone surrogates replaced by U+FFFD so that the text can always be encoded."""
    text = json.dumps(value, ensure_ascii=True, allow_nan=False)
    return _SURROGATE.sub(lambda m: m.group(1) or "\\ufffd", text)


@dataclass(frozen=True)
class Outcome:
    value: Any
    memory_cap_applied: bool


# --------------------------------------------------------------------------- parent


def run_isolated(
    target: str,
    args: list[Any],
    *,
    timeout_s: float = TIMEOUT_S,
    memory_mb: int = MEMORY_MB,
    python_path: list[str] | None = None,
) -> Outcome:
    """Call ``module:function(*args)`` in a child process; ``args`` and the result must be JSON.

    Raises :class:`ToolFailure`: ``engine.timeout``, ``engine.resource_limit``, ``engine.worker_died`` (the child ended
    without an answer), ``engine.failure`` (any exception that is not a ToolFailure), or whatever ToolFailure the child
    function raised, with its family, message and details.
    """
    if ":" not in target:
        raise ValueError(f"target must be 'module:function', got {target!r}")
    here = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([here, *(python_path or []), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])])
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    limits = {"timeout_s": timeout_s, "memory_mb": memory_mb}
    with tempfile.TemporaryDirectory(prefix="eng-toolkit-iso-") as scratch:
        answer = Path(scratch) / "answer.json"
        errors = Path(scratch) / "stderr.txt"
        request = json.dumps({"target": target, "args": args, "memory_mb": memory_mb, "answer": str(answer)})
        with errors.open("wb") as err:
            proc = subprocess.Popen(
                [sys.executable, "-P", "-m", "eng_toolkit.isolated"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=err,
                env=env,
                cwd=scratch,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                proc.communicate(request.encode("utf-8"), timeout=timeout_s)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                proc.communicate()
                raise ToolFailure(
                    "engine.timeout", f"the file took longer than {timeout_s:g} s to read and was stopped", limits
                ) from None
        tail = errors.read_text(encoding="utf-8", errors="replace")[-STDERR_TAIL:]
        if not answer.is_file():
            hint = " (possibly out of memory)" if "MemoryError" in tail or proc.returncode in (-9, -11, 0xC0000005 - (1 << 32)) else ""
            raise ToolFailure(
                "engine.worker_died",
                f"the reading process ended without an answer (exit code {proc.returncode}){hint}",
                {**limits, "returncode": proc.returncode, "stderr_tail": tail},
            )
        try:
            reply = jsonsafe.loads(answer.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ToolFailure("engine.failure", f"unreadable answer from the reading process: {exc}", limits) from exc
    if reply.get("ok"):
        return Outcome(reply["value"], bool(reply.get("memory_cap_applied")))
    family = reply["family"]
    details = dict(reply.get("details") or {})
    if family == "engine.resource_limit":
        details = {**limits, **details}
    raise ToolFailure(family, reply["message"], details)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the child and what it started: on Windows a virtual environment's python.exe is a launcher that starts the real interpreter."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    proc.kill()


# --------------------------------------------------------------------------- child


def _limit_memory(megabytes: int) -> bool:
    """Cap this process's memory (and, on Windows, forbid it to start other processes); False when the platform cannot."""
    limit = megabytes * 1024 * 1024
    if sys.platform == "win32":
        return _limit_memory_windows(limit)
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        return True
    except (ImportError, ValueError, OSError):
        return False


def _limit_memory_windows(limit: int) -> bool:
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
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return False
    info = ExtendedLimits()
    info.Basic.LimitFlags = 0x100 | 0x8  # JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    info.Basic.ActiveProcessLimit = 1
    info.ProcessMemoryLimit = limit
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # JobObjectExtendedLimitInformation
        return False
    return bool(kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()))


def _child_main() -> None:
    request = jsonsafe.loads(sys.stdin.buffer.read().decode("utf-8"))
    answer = Path(request["answer"])
    applied = _limit_memory(int(request["memory_mb"]))
    try:
        module_name, _, function_name = request["target"].partition(":")
        function = getattr(importlib.import_module(module_name), function_name)
        value = function(*request["args"])
        reply: dict[str, Any] = {"ok": True, "value": value, "memory_cap_applied": applied}
        text = strict_json(reply)
    except ToolFailure as exc:
        text = strict_json({"ok": False, "family": exc.family, "message": exc.message, "details": jsonsafe.loads(json.dumps(exc.details, default=str))})
    except MemoryError:
        text = json.dumps({"ok": False, "family": "engine.resource_limit", "message": "the file needs more memory than the limit allows", "details": {}})
    except BaseException as exc:  # noqa: BLE001 - whatever the parser did, the parent gets a structured answer
        message = f"{type(exc).__name__}: {exc}"
        text = strict_json({"ok": False, "family": "engine.failure", "message": message[:500], "details": {"traceback": traceback.format_exc(limit=5)[-1500:]}})
    answer.write_text(text, encoding="ascii")


if __name__ == "__main__":
    _child_main()
