"""A stand-in for an external program, used only by the tests of eng_toolkit.external (and of the domains built on it).

Started through a shim made by ``exe_fixtures.install_fake``:  ``python fake_exe.py <config.json> <the arguments the toolkit passed>``.
The first argument is the test's configuration (a JSON file); everything after it is what the toolkit sent.  Every call is recorded,
as one JSON line in the file the configuration names (``calls``): the arguments exactly, the working directory, the whole
environment, the process id and what standard input held.

``mode`` in the configuration:

* ``ok``      print ``stdout`` and ``stderr``, exit with ``exit`` (default 0); also write the files in ``files`` (name -> text) below the working directory
* ``sleep``   sleep ``sleep_s`` seconds, then behave like ``ok``
* ``spawn``   start a child that sleeps 60 s and write its pid to ``child_pid``, then sleep 60 s
* ``write``   write ``total`` bytes to the file ``file`` in chunks of ``chunk`` bytes, ``delay`` seconds apart
* ``flood``   print to stdout without end
* ``alloc``   allocate ``alloc_mb`` megabytes, then behave like ``ok``
* ``modify``  append a line to the existing file ``file``, then behave like ``ok``

``version_text`` is printed (and the exit code ``version_exit`` used) when the arguments are exactly ``version_args``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def main() -> int:
    config = json.load(open(sys.argv[1], encoding="utf-8"))
    argv = sys.argv[2:]
    record = {"argv": argv, "cwd": os.getcwd(), "env": dict(os.environ), "pid": os.getpid(), "stdin": sys.stdin.read() if sys.stdin is not None else None}
    with open(config["calls"], "a", encoding="utf-8") as log:
        log.write(json.dumps(record) + "\n")
    if argv == config.get("version_args"):
        print(config.get("version_text", ""))
        return int(config.get("version_exit", 0))
    mode = config["mode"]
    if mode == "sleep":
        time.sleep(float(config["sleep_s"]))
    elif mode == "spawn":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        open(config["child_pid"], "w").write(str(child.pid))
        time.sleep(60)
    elif mode == "write":
        written, chunk = 0, int(config.get("chunk", 100_000))
        with open(config["file"], "wb") as handle:
            while written < int(config["total"]):
                handle.write(b"x" * chunk)
                handle.flush()
                written += chunk
                time.sleep(float(config.get("delay", 0.02)))
    elif mode == "flood":
        while True:
            sys.stdout.write("y" * 100_000)
            sys.stdout.flush()
            time.sleep(0.01)
    elif mode == "alloc":
        block = bytearray(int(config["alloc_mb"]) * 1024 * 1024)
        print("allocated", len(block))
    elif mode == "modify":
        with open(config["file"], "a", encoding="utf-8") as handle:
            handle.write("one more line\n")
    for name, text in (config.get("files") or {}).items():
        os.makedirs(os.path.dirname(name) or ".", exist_ok=True)
        open(name, "w", encoding="utf-8").write(text)
    if config.get("stdout"):
        print(config["stdout"])
    if config.get("stderr"):
        print(config["stderr"], file=sys.stderr)
    return int(config.get("exit", 0))


if __name__ == "__main__":
    raise SystemExit(main())
