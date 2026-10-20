"""A stand-in for LibreDWG's ``dwg2dxf``, used only by the tests (the real program is not installed and is never run here).

Started through a shim made by ``dwg_fixtures.install_fake``:  ``python fake_dwg2dxf.py <config.json> <the arguments the toolkit passed>``.
The first argument is the test's configuration (a JSON file); everything after it is what the program under test sent, and is recorded
exactly, with the working directory, the whole environment, the process id and the state of the input file, as one JSON line in the
file the configuration names (``calls``).

Modes (``mode`` in the configuration):  copy (write the DXF named by ``dxf`` to the output path), fail (print ``stderr``, exit ``exit``),
sleep (sleep ``sleep_s`` seconds, then copy), garbage (write bytes that are not a DXF), huge (write a DXF header and then comment records
until ``huge_bytes``, slowly), empty (write an empty file), nothing (exit 0 with no file), notdxf_big (a valid start, then binary junk).
``--version`` prints ``version`` (nothing when it is empty) and exits 0, in every mode except ``version_broken`` (exit 3).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time


def main() -> int:
    config = json.load(open(sys.argv[1], encoding="utf-8"))
    argv = sys.argv[2:]
    record = {"argv": argv, "cwd": os.getcwd(), "env": dict(os.environ), "pid": os.getpid(), "stdin": sys.stdin.read() if sys.stdin is not None else None}
    if argv and not argv[-1].startswith("-") and os.path.isfile(argv[-1]):
        data = open(argv[-1], "rb").read()
        record["input"] = {"path": argv[-1], "sha256": hashlib.sha256(data).hexdigest(), "head": data[:6].decode("latin-1")}
    with open(config["calls"], "a", encoding="utf-8") as log:
        log.write(json.dumps(record) + "\n")
    mode = config["mode"]
    if argv == ["--version"]:
        if mode == "version_broken":
            return 3
        if config.get("version"):
            print(config["version"])
        return 0
    if len(argv) != 3 or argv[0] != "-o":
        print("fake dwg2dxf: unexpected arguments " + repr(argv), file=sys.stderr)
        return 2
    out = argv[1]
    if mode == "fail":
        print(config.get("stderr", "error") + ((" " + " ".join(argv)) if config.get("echo_args") else ""), file=sys.stderr)
        return int(config.get("exit", 1))
    if mode == "sleep":
        time.sleep(float(config["sleep_s"]))
        shutil.copyfile(config["dxf"], out)
        return 0
    if mode == "copy":
        shutil.copyfile(config["dxf"], out)
        if config.get("stderr"):
            print(config["stderr"], file=sys.stderr)
        return 0
    if mode == "garbage":
        open(out, "wb").write(config.get("bytes", "this is not a dxf at all\x00\x01\x02").encode("latin-1"))
        return 0
    if mode == "empty":
        open(out, "wb").close()
        return 0
    if mode == "nothing":
        return 0
    if mode == "huge":
        with open(out, "wb") as handle:
            handle.write(b"  0\nSECTION\n  2\nHEADER\n")
            written = 0
            chunk = b"999\n" + b"x" * 252 + b"\n"
            block = chunk * 1000
            while written < int(config["huge_bytes"]):
                handle.write(block)
                handle.flush()
                written += len(block)
                time.sleep(0.02)
        return 0
    if mode == "notdxf_big":
        open(out, "wb").write(b"\x7fELF" + bytes(2000))
        return 0
    print("fake dwg2dxf: unknown mode " + mode, file=sys.stderr)
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
