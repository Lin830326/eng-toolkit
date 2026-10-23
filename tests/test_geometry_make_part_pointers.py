"""Every refusal of geometry_make_part names its parameter as a JSON pointer in ``details.param``.

Refusals inside the spec already carried ``details.pointer`` (``/features/2/diameter``): that is now also ``param``; a refusal about
a call parameter (``name``, ``formats``) is ``/name``; one that names nothing points at the spec.
"""

import asyncio
import json

import pytest
from fastmcp import Client

from eng_toolkit.gateway.server import gateway

BOX = {"op": "box", "size": ["10 mm", "10 mm", "10 mm"]}


def _spec(*features, params=None):
    return json.dumps({"params": params or {}, "features": list(features)})


def _call(**kwargs):
    async def run():
        async with Client(gateway) as client:
            return (await client.call_tool("geometry_make_part", kwargs, raise_on_error=False)).structured_content

    return asyncio.run(run())


CASES = [
    ("not json", dict(spec="{"), "/spec"),
    ("empty spec", dict(spec=""), "/spec"),
    ("unknown key in a feature", dict(spec=_spec({"op": "box", "sise": ["1 mm", "1 mm", "1 mm"]})), "/features/0/sise"),
    ("unknown op", dict(spec=_spec({"op": "teapot"})), None),
    ("wrong unit dimension", dict(spec=_spec({"op": "box", "size": ["10 kg", "10 mm", "10 mm"]})), None),
    ("zero size", dict(spec=_spec({"op": "box", "size": ["0 mm", "10 mm", "10 mm"]})), None),
    ("hostile size text", dict(spec=_spec({"op": "box", "size": ["9**9**9 mm", "1 mm", "1 mm"]})), None),
    ("bad name", dict(spec=_spec(BOX), name="a b"), "/name"),
    ("bad format", dict(spec=_spec(BOX), formats="step,iges"), "/formats"),
    ("6000-character name", dict(spec=_spec(BOX), name="a" * 6000), "/name"),
    ("6000-character spec", dict(spec=" " * 6000 + "{"), "/spec"),
]


@pytest.mark.parametrize("label,kwargs,expected", CASES, ids=[c[0] for c in CASES])
def test_every_refusal_has_a_json_pointer(label, kwargs, expected):
    result = _call(**kwargs)
    assert result["ok"] is False, label
    param = result["error"]["details"].get("param")
    assert isinstance(param, str) and param.startswith("/") and len(param) > 1, (label, result["error"])
    if expected is not None:
        assert param == expected
    inner = result["error"]["details"].get("pointer")
    if inner:  # a pointer inside the spec is the param; "" means the whole spec text
        assert param == inner
    elif inner == "":
        assert param == "/spec"
    assert "Traceback" not in json.dumps(result["error"])
