"""Functions the child process of ``run_isolated`` runs in the tests: a good one, a hanging one, one that dies, and so on."""

import os
import time

from eng_toolkit.errors import ToolFailure


def add(a, b):
    return {"sum": a + b, "pid": os.getpid()}


def nothing_printed():
    print("noise on stdout that must not reach the result")
    return {"ok": True}


def sleeps(seconds):
    time.sleep(seconds)
    return {"slept": seconds}


def dies():
    os._exit(3)


def raises_tool_failure():
    raise ToolFailure("drawing.corrupt", "not a drawing", {"offset": 12})


def raises_value_error():
    raise ValueError("boom")


def eats_memory(megabytes):
    blocks = []
    for _ in range(megabytes // 16):
        blocks.append(bytearray(16 * 1024 * 1024))
    return {"allocated": len(blocks)}


def returns_non_json():
    return {"x": object()}


def returns_surrogate():
    return {"text": "a\ud800b and a valid pair \U0001F600"}


def returns_nan():
    return {"x": float("nan")}
