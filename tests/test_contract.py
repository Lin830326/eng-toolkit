import json

import pytest
from pydantic import ValidationError

from eng_toolkit.contract import (
    Provenance,
    QuantityOut,
    ToolResult,
    canonical_json,
    failure,
    inputs_hash,
)

CONTRACT_KEYS = {
    "ok",
    "value",
    "values",
    "data",
    "artifacts",
    "provenance",
    "warnings",
    "error",
    "review_status",
}


def _prov():
    return Provenance(engine="test", version="0", inputs_hash=inputs_hash({}))


def test_success_shape_round_trips_through_json():
    r = ToolResult(
        ok=True,
        value=QuantityOut(quantity=1.0, unit="newton"),
        values={"f": QuantityOut(quantity=1.0, unit="newton", uncertainty=0.1)},
        provenance=_prov(),
    )
    payload = json.loads(r.model_dump_json())
    assert set(payload) == CONTRACT_KEYS
    assert payload["review_status"] == "unreviewed"
    assert payload["value"] == {"quantity": 1.0, "unit": "newton", "uncertainty": None}
    assert payload["provenance"]["inputs_hash"].startswith("sha256:")
    assert ToolResult.model_validate(payload) == r


def test_review_status_cannot_be_anything_else():
    with pytest.raises(ValidationError):
        ToolResult(ok=True, provenance=_prov(), review_status="approved")


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        ToolResult(ok=True, provenance=_prov(), bogus=1)


def test_failure_helper_satisfies_contract():
    r = failure(
        family="units.bare_number",
        message="no unit",
        engine="test",
        version="0",
        inputs={"bore": 100},
        details={"param": "bore"},
    )
    assert r.ok is False
    assert r.value is None
    assert r.error.family == "units.bare_number"
    assert r.error.details == {"param": "bore"}
    assert r.review_status == "unreviewed"
    assert set(json.loads(r.model_dump_json())) == CONTRACT_KEYS


def test_inputs_hash_is_deterministic_and_order_independent():
    a = inputs_hash({"bore": "100 mm", "pressure": "210 bar"})
    b = inputs_hash({"pressure": "210 bar", "bore": "100 mm"})
    c = inputs_hash({"bore": "100 mm", "pressure": "211 bar"})
    assert a == b
    assert a != c
    assert a.startswith("sha256:") and len(a) == len("sha256:") + 64


def test_canonical_json_has_no_whitespace_variance():
    assert canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'
