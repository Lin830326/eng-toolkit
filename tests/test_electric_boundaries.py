"""Parser and solver boundary checks shared by all six calculations."""
import pytest

from eng_toolkit.domains.electric._common import bisect, q, run
from eng_toolkit.units import UnitsError
from tests.electric_testing import validate


def test_iteration_exhaustion_returns_structured_refusal():
    # Root of x²-2 is sqrt(2); one iteration on [0,2] cannot reach 1e-11 accuracy.
    result = run("power_flow", {"test": "bounded root"}, lambda handle: bisect(lambda x: x*x-2, 0, 2, "load_torque", maxiter=1))
    result = validate(result)
    assert not result.ok
    assert result.error.family == "electric.nonconvergence"
    assert result.error.details["param"] == "/load_torque"


@pytest.mark.parametrize("raw", [1, 0.1, True])
def test_physical_bare_numbers_rejected_before_pint(raw):
    with pytest.raises(UnitsError) as caught:
        q(raw, "voltage", "V")
    assert caught.value.family == "units.bare_number"


@pytest.mark.parametrize("unit", ["degC/W", "celsius/W", "degree_Celsius/W", "degF/W", "degree_Fahrenheit/W"])
def test_offset_temperature_factor_rejected_before_pint_product_rewrite(unit):
    with pytest.raises(UnitsError) as caught:
        q("3 "+unit, "thermal_resistance", "K/W")
    assert caught.value.details["param"] == "/thermal_resistance"


def test_delta_temperature_conversion():
    # A 9 degree Fahrenheit difference equals 5 K, unlike an absolute 9 degF.
    assert q("9 delta_degF", "rise", "K") == pytest.approx(5)
