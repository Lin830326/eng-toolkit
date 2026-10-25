import math

import pytest

from eng_toolkit.units import Q_, UnitsError, parse_quantity, require_positive, to_quantity_out


def test_string_with_unit_parses():
    q = parse_quantity("100 mm", name="bore", dimension="length")
    assert math.isclose(q.to("m").magnitude, 0.1)


def test_json_string_form_parses():
    q = parse_quantity('{"value": 210, "unit": "bar"}', name="p", dimension="pressure")
    assert math.isclose(q.to("Pa").magnitude, 21.0e6)


def test_invalid_json_string_is_parse_error():
    with pytest.raises(UnitsError) as ei:
        parse_quantity('{"value": 210, unit: bar}', name="p", dimension="pressure")
    assert ei.value.family == "units.parse_error"


def test_mapping_form_parses():
    q = parse_quantity({"value": 210, "unit": "bar"}, name="p", dimension="pressure")
    assert math.isclose(q.to("Pa").magnitude, 21.0e6)


@pytest.mark.parametrize("raw", [100, 100.0, "100", " 42.5 "])
def test_bare_numbers_rejected(raw):
    with pytest.raises(UnitsError) as ei:
        parse_quantity(raw, name="bore", dimension="length")
    assert ei.value.family == "units.bare_number"
    assert ei.value.details["param"] == "/bore"


def test_mapping_with_empty_unit_is_bare_number():
    with pytest.raises(UnitsError) as ei:
        parse_quantity({"value": 5, "unit": ""}, name="x", dimension="length")
    assert ei.value.family == "units.bare_number"


def test_dimension_mismatch_is_structured():
    with pytest.raises(UnitsError) as ei:
        parse_quantity("100 mm", name="pressure", dimension="pressure")
    err = ei.value
    assert err.family == "units.dimension_mismatch"
    assert err.details["expected_dimension"] == "pressure"
    assert "[length]" in err.details["received_dimensionality"]


def test_kinematic_viscosity_rejected_where_dynamic_required():
    with pytest.raises(UnitsError) as ei:
        parse_quantity("46 cSt", name="viscosity", dimension="dynamic_viscosity")
    assert ei.value.family == "units.dimension_mismatch"


@pytest.mark.parametrize("raw", ["abc", "mm", "100 furlongs_per_fortnight_xyz", {"unit": "mm"}, [1, 2]])
def test_unparseable_inputs(raw):
    with pytest.raises(UnitsError) as ei:
        parse_quantity(raw, name="x", dimension="length")
    assert ei.value.family == "units.parse_error"


def test_missing_is_structured():
    with pytest.raises(UnitsError) as ei:
        parse_quantity(None, name="x", dimension="length")
    assert ei.value.family == "input.missing"


def test_require_positive():
    with pytest.raises(UnitsError) as ei:
        require_positive(Q_(0, "mm"), name="bore")
    assert ei.value.family == "input.invalid"
    assert require_positive(Q_(1, "mm"), name="bore").magnitude == 1


def test_to_quantity_out_converts_value_and_uncertainty():
    out = to_quantity_out(Q_(1000, "N"), "kN", uncertainty=Q_(50, "N"))
    assert out.unit == "kilonewton"
    assert math.isclose(out.quantity, 1.0)
    assert math.isclose(out.uncertainty, 0.05)


def test_to_quantity_out_without_uncertainty():
    out = to_quantity_out(Q_(2, "bar"))
    assert out.uncertainty is None
    assert out.unit == "bar"
