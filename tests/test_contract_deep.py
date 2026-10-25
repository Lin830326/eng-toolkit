"""Deep hostile inputs against every tool: nesting, huge digit strings, large payloads and unusable files.

``test_contract_sweep`` covers short hostile values. This module adds the classes that hang or crash parsers:
documents nested tens of thousands of levels, a 200 000 digit number, exponents with thousands of digits, multi-megabyte
text, and for every file argument empty / garbage / directory / missing / over-long / NUL-containing paths. The rule is the
same: a structured result, a family and a ``details.param`` pointer on every refusal, nothing ending as
``engine.failure``, and a bounded time per call. ``scripts/deep_sweep.py`` runs the larger version (more extensions and
12 MB files) outside the test suite.
"""

import json
import os
import re
import time

import pytest
from fastmcp import Client

from eng_toolkit.gateway.server import gateway

FILE_PARAM = re.compile(r"(^|_)(path|file|files)$|^files_[ab]$")
EXTENSIONS = [".step", ".csv", ".json", ".v", ".cir", ".kicad_pcb", ".dxf", ".pdf"]
SLOW_SECONDS = 20.0


def _strings():
    return {
        "deep_array": "[" * 60000 + "]" * 60000,
        "deep_object": '{"a":' * 60000 + "1" + "}" * 60000,
        "deep_parens": "(" * 60000 + "1" + ")" * 60000,
        "huge_digits": "9" * 200000,
        "huge_digits_unit": "9" * 200000 + " m",
        "huge_exp_digits": "1e" + "9" * 5000 + " m",
        "big_string": "A" * 2_000_000,
        "many_lines": "1 m\n" * 100_000,
        "combining": "5 m" + "́" * 5000,
    }


def _numbers():
    return {"int_2_63": 2**63, "int_1e400": 10**400, "float_max": 1.7976931348623157e308, "neg_zero": -0.0}


def _types(prop):
    t = prop.get("type")
    types = {t} if isinstance(t, str) else set(t or [])
    for sub in prop.get("anyOf", []) or []:
        if "type" in sub:
            types.add(sub["type"])
    return types


def _filler(prop):
    if prop.get("enum"):
        return prop["enum"][0]
    for sub in prop.get("anyOf", []) or []:
        if sub.get("enum"):
            return sub["enum"][0]
    if prop.get("default") is not None:
        return prop["default"]
    types = _types(prop)
    if "string" in types or not types:
        return "1 m"
    if "integer" in types:
        return 2
    if "number" in types:
        return 1.0
    if "boolean" in types:
        return False
    return None


def _finite(obj):
    try:
        json.dumps(obj, allow_nan=False)
        return True
    except ValueError:
        return False


def _domains():
    import asyncio

    async def names():
        async with Client(gateway) as client:
            return {tool.name.split("_", 1)[0] for tool in await client.list_tools()}

    return sorted(asyncio.run(names()))


def _files(root):
    paths = {}
    garbage = bytes((i * 37 + 11) % 256 for i in range(4096))
    for ext in EXTENSIONS:
        for kind, data in (("empty", b""), ("garbage", garbage), ("text", b"hello world\n" * 40)):
            path = root / f"{kind}{ext}"
            path.write_bytes(data)
            paths[f"{kind}{ext}"] = str(path)
        directory = root / f"dir{ext}"
        directory.mkdir()
        paths[f"dir{ext}"] = str(directory)
        paths[f"missing{ext}"] = str(root / f"missing{ext}")
    paths["nul"] = str(root / "x\u0000.csv")
    paths["long"] = str(root / ("d" * 5000) / "x.csv")
    paths["device"] = "NUL"
    return paths


def _check(tool, param, case, reply, elapsed, broken):
    where = f"{tool}({param}={case})"
    result = reply.structured_content
    if elapsed > SLOW_SECONDS:
        broken.append(f"{where}: took {elapsed:.0f} s")
    if result is None:
        broken.append(f"{where}: no structured result: {str(reply.content)[:80]}")
        return
    if not _finite(result):
        broken.append(f"{where}: non-finite output")
    if result.get("ok"):
        return
    error = result.get("error") or {}
    family = error.get("family", "")
    param_pointer = (error.get("details") or {}).get("param")
    text = json.dumps(error, ensure_ascii=False)
    if len(text) > 20000:
        broken.append(f"{where}: error of {len(text)} bytes echoes the input")
    if not family:
        broken.append(f"{where}: refusal without a family")
    elif family == "engine.failure" or "Traceback" in text[:4000]:
        broken.append(f"{where}: {family}: {error.get('message', '')[:100]}")
    elif not (isinstance(param_pointer, str) and param_pointer.startswith("/")):
        broken.append(f"{where}: {family} has details.param {param_pointer!r}")


@pytest.mark.parametrize("domain", _domains())
async def test_deep_hostile_inputs_keep_the_contract(domain, tmp_path, monkeypatch):
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    monkeypatch.setenv("ENG_TOOLKIT_WS", str(tmp_path / "ws"))
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(files_dir))
    file_cases = _files(files_dir)
    strings, numbers = _strings(), _numbers()
    broken = []
    async with Client(gateway) as client:
        for tool in await client.list_tools():
            if tool.name.split("_", 1)[0] != domain:
                continue
            schema = tool.inputSchema or {}
            props = schema.get("properties", {})
            base = {name: _filler(props[name]) for name in schema.get("required", [])}
            for name in list(props)[:14]:
                types = _types(props[name])
                cases = dict(numbers)
                if "string" in types or not types:
                    cases.update(strings)
                if FILE_PARAM.search(name):
                    cases.update({"file_" + key: value for key, value in file_cases.items()})
                for case, value in cases.items():
                    started = time.monotonic()
                    reply = await client.call_tool(tool.name, {**base, name: value}, raise_on_error=False)
                    _check(tool.name, name, case, reply, time.monotonic() - started, broken)
    assert not broken, "\n".join(broken[:25]) + (f"\n... {len(broken)} in all" if len(broken) > 25 else "")
