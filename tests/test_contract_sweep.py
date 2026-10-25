"""Contract sweep over every registered tool.

The rule is a pattern, not an instance: whatever a caller sends, the answer is a structured result. A refusal has a
family and a ``details.param`` JSON pointer that starts with "/"; nothing ends as ``engine.failure`` or a framework
error; an ok result is finite JSON. Each parameter of each tool is hit with hostile values while every other required
parameter holds a plausible filler, so a new tool that forgets the contract fails here without anyone writing a test
for it.
"""

import json

import pytest
from fastmcp import Client

from eng_toolkit.gateway.server import gateway

HOSTILE_STRINGS = {
    "nan": "NaN",
    "junk": "){[;rm -rf",
    "offset_compound": "1 m*degC",
    "nul": "5 m\u0000",
    "surrogate": "1 \ud800 m",
    "empty": "",
}
HOSTILE_ANY = {"bare_number": 24, "none": None, "list": [1, 2], "dict": {"a": 1}}
HOSTILE_NUMBERS = {"negative": -5, "huge": 1e300, "text": "abc"}
MAX_PARAMS = 14


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


def _cases(prop):
    types = _types(prop)
    cases = dict(HOSTILE_ANY)
    if "string" in types or not types:
        cases.update(HOSTILE_STRINGS)
    if types & {"number", "integer"}:
        cases.update(HOSTILE_NUMBERS)
    return cases


def _finite(obj):
    try:
        json.dumps(obj, allow_nan=False)
        return True
    except ValueError:
        return False


async def _tools():
    async with Client(gateway) as client:
        return await client.list_tools()


def _domains():
    import asyncio

    return sorted({tool.name.split("_", 1)[0] for tool in asyncio.run(_tools())})


@pytest.mark.parametrize("domain", _domains())
async def test_every_refusal_in_the_domain_keeps_the_contract(domain, tmp_path, monkeypatch):
    monkeypatch.setenv("ENG_TOOLKIT_WS", str(tmp_path))
    broken = []
    async with Client(gateway) as client:
        for tool in await client.list_tools():
            if tool.name.split("_", 1)[0] != domain:
                continue
            schema = tool.inputSchema or {}
            props = schema.get("properties", {})
            base = {name: _filler(props[name]) for name in schema.get("required", [])}
            for name in list(props)[:MAX_PARAMS]:
                for case, value in _cases(props[name]).items():
                    reply = await client.call_tool(tool.name, {**base, name: value}, raise_on_error=False)
                    where = f"{tool.name}({name}={case})"
                    result = reply.structured_content
                    if result is None:
                        broken.append(f"{where}: no structured result: {str(reply.content)[:80]}")
                        continue
                    if not _finite(result):
                        broken.append(f"{where}: non-finite output")
                    if result.get("ok"):
                        continue
                    error = result.get("error") or {}
                    family = error.get("family", "")
                    param = (error.get("details") or {}).get("param")
                    if not family:
                        broken.append(f"{where}: refusal without a family")
                    elif family == "engine.failure" or "Traceback" in json.dumps(error)[:4000]:
                        broken.append(f"{where}: {family}: {error.get('message', '')[:80]}")
                    elif not (isinstance(param, str) and param.startswith("/")):
                        broken.append(f"{where}: {family} has details.param {param!r}")
    assert not broken, "\n".join(broken[:25]) + (f"\n... {len(broken)} in all" if len(broken) > 25 else "")
