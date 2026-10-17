"""calc.hydraulics tools checked against hand calculations."""

import json
import math
from pathlib import Path

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.calc.hydraulics import cylinder_force, pipe_pressure_drop


def _call(tool, **kwargs) -> ToolResult:
    # FastMCP wraps the function in a FunctionTool; .fn is the original callable.
    fn = getattr(tool, "fn", tool)
    result = fn(**kwargs)
    assert isinstance(result, ToolResult)
    # Every result must survive a JSON round trip through the contract model.
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


# --------------------------------------------------------------------------- cylinder force
# Hand calculation: bore 100 mm, rod 50 mm, 210 bar
#   A_bore    = pi/4 * 0.100^2            = 7.853981634e-3 m^2
#   A_annulus = pi/4 * (0.100^2 - 0.050^2) = 5.890486225e-3 m^2
#   F_push    = 21.0e6 Pa * A_bore        = 164 933.61 N
#   F_pull    = 21.0e6 Pa * A_annulus     = 123 700.21 N
PUSH_N = 21.0e6 * math.pi / 4 * 0.1**2
PULL_N = 21.0e6 * math.pi / 4 * (0.1**2 - 0.05**2)


def test_cylinder_force_matches_hand_calc():
    r = _call(cylinder_force, bore="100 mm", rod="50 mm", pressure="210 bar", output_unit="N")
    assert r.ok, r.error
    assert r.value.unit == "newton"
    assert math.isclose(r.value.quantity, PUSH_N, rel_tol=1e-9)
    assert math.isclose(r.value.quantity, 164933.61, rel_tol=1e-6)
    assert math.isclose(r.values["pull_force"].quantity, PULL_N, rel_tol=1e-9)
    assert math.isclose(r.values["pull_force"].quantity, 123700.21, rel_tol=1e-6)
    assert math.isclose(r.values["bore_area"].quantity, 7853.981634, rel_tol=1e-9)
    assert r.values["bore_area"].unit == "millimeter ** 2"
    assert math.isclose(r.values["area_ratio"].quantity, 4 / 3, rel_tol=1e-12)
    assert r.review_status == "unreviewed"
    assert r.provenance.engine == "eng_toolkit.calc.hydraulics"
    assert r.provenance.inputs_hash.startswith("sha256:")
    assert any("seal friction" in w for w in r.warnings)


def test_cylinder_force_default_unit_is_kN_and_accepts_mapping_inputs():
    r = _call(
        cylinder_force,
        bore={"value": 100, "unit": "mm"},
        rod={"value": 50, "unit": "mm"},
        pressure={"value": 21, "unit": "MPa"},
    )
    assert r.ok
    assert r.value.unit == "kilonewton"
    assert math.isclose(r.value.quantity, PUSH_N / 1000, rel_tol=1e-9)


def test_cylinder_force_imperial_inputs_are_converted():
    # 4 in bore, 2 in rod, 3000 psi -> push = 3000 * pi = 37 699.1 lbf
    r = _call(cylinder_force, bore="4 inch", rod="2 inch", pressure="3000 psi", output_unit="lbf")
    assert r.ok
    assert math.isclose(r.value.quantity, 3000 * math.pi / 4 * 16, rel_tol=1e-9)
    assert math.isclose(r.values["pull_force"].quantity, 3000 * math.pi / 4 * 12, rel_tol=1e-9)


def test_cylinder_force_without_rod_warns():
    r = _call(cylinder_force, bore="100 mm", pressure="210 bar", output_unit="N")
    assert r.ok
    assert math.isclose(r.values["pull_force"].quantity, PUSH_N, rel_tol=1e-9)
    assert any("rod not given" in w for w in r.warnings)


def test_cylinder_force_bare_number_is_structured_error():
    r = _call(cylinder_force, bore="100", rod="50 mm", pressure="210 bar")
    assert r.ok is False
    assert r.value is None
    assert r.error.family == "units.bare_number"
    assert r.error.details["param"] == "bore"
    assert r.review_status == "unreviewed"
    # inputs.json is still written so the failed call is reproducible
    assert [a.format for a in r.artifacts] == ["json"]


def test_cylinder_force_dimension_mismatch():
    r = _call(cylinder_force, bore="100 mm", rod="50 mm", pressure="210 mm")
    assert r.ok is False
    assert r.error.family == "units.dimension_mismatch"
    assert r.error.details["param"] == "pressure"


def test_cylinder_force_rod_not_smaller_than_bore():
    r = _call(cylinder_force, bore="50 mm", rod="50 mm", pressure="210 bar")
    assert r.ok is False
    assert r.error.family == "input.invalid"


def test_cylinder_force_writes_workspace_files(isolated_workspace):
    r = _call(cylinder_force, bore="100 mm", rod="50 mm", pressure="210 bar")
    job_dir = Path(r.data["job_dir"])
    assert job_dir.parent == isolated_workspace
    assert {p.name for p in job_dir.iterdir()} == {"inputs.json", "result.json", "manifest.json"}
    inputs = json.loads((job_dir / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["bore"] == "100 mm"
    saved = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
    assert saved["value"] == r.value.model_dump()
    manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"]["inputs.json"] == r.artifacts[0].sha256


# --------------------------------------------------------------------------- pipe pressure drop
# Laminar hand calculation (Hagen-Poiseuille, exact):
#   oil rho = 870 kg/m^3, mu = 0.040 Pa*s, D = 10 mm, L = 1 m, v = 1 m/s
#   Q  = v * pi/4 * D^2 = 7.853981634e-5 m^3/s
#   Re = rho v D / mu = 870 * 1 * 0.01 / 0.04 = 217.5  (laminar)
#   f  = 64 / Re = 0.294253
#   dp = f (L/D) rho v^2 / 2 = 0.294253 * 100 * 435 = 12 800 Pa
#   check: 32 mu L v / D^2 = 32 * 0.04 * 1 * 1 / 1e-4 = 12 800 Pa  (identical)
Q_LAMINAR = math.pi / 4 * 0.01**2  # m^3/s for v = 1 m/s


def test_pipe_pressure_drop_laminar_matches_hagen_poiseuille():
    r = _call(
        pipe_pressure_drop,
        diameter="10 mm",
        length="1 m",
        flow_rate=f"{Q_LAMINAR} m^3/s",
        density="870 kg/m^3",
        viscosity="0.04 Pa*s",
        output_unit="Pa",
    )
    assert r.ok, r.error
    assert r.value.unit == "pascal"
    assert math.isclose(r.value.quantity, 12800.0, rel_tol=1e-9)
    assert math.isclose(r.values["velocity"].quantity, 1.0, rel_tol=1e-9)
    assert math.isclose(r.values["reynolds"].quantity, 217.5, rel_tol=1e-9)
    assert math.isclose(r.values["friction_factor_darcy"].quantity, 64 / 217.5, rel_tol=1e-9)
    assert math.isclose(r.values["head_loss"].quantity, 12800 / (870 * 9.80665), rel_tol=1e-9)
    assert r.data["regime"] == "laminar"
    assert r.value.uncertainty is not None and math.isclose(r.value.uncertainty, 0.05 * 12800)
    assert r.provenance.engine == "fluids"
    assert any("fittings" in w for w in r.warnings)


def test_pipe_pressure_drop_output_unit_and_cP_input():
    # same case, 40 cP == 0.04 Pa*s, flow in L/min, output in bar
    r = _call(
        pipe_pressure_drop,
        diameter="10 mm",
        length="1 m",
        flow_rate=f"{Q_LAMINAR * 60_000} L/min",
        density="870 kg/m^3",
        viscosity="40 cP",
        output_unit="bar",
    )
    assert r.ok, r.error
    assert r.value.unit == "bar"
    assert math.isclose(r.value.quantity, 0.128, rel_tol=1e-9)


def test_pipe_pressure_drop_turbulent_vs_swamee_jain():
    # water 20 degC: rho 998 kg/m^3, mu 1.002e-3 Pa*s, D 25 mm, L 10 m, v 2 m/s, eps 0.045 mm
    # Re = 998*2*0.025/1.002e-3 = 49 800 ; eps/D = 0.0018
    # Swamee-Jain: f = 0.25 / [log10(eps/(3.7 D) + 5.74/Re^0.9)]^2 ~ 0.0263 (within ~1-2 % of Colebrook)
    rho, mu, D, L, v, eps = 998.0, 1.002e-3, 0.025, 10.0, 2.0, 0.045e-3
    Q = v * math.pi / 4 * D**2
    Re = rho * v * D / mu
    f_sj = 0.25 / (math.log10(eps / (3.7 * D) + 5.74 / Re**0.9)) ** 2
    dp_hand = f_sj * (L / D) * rho * v**2 / 2

    r = _call(
        pipe_pressure_drop,
        diameter="25 mm",
        length="10 m",
        flow_rate=f"{Q} m^3/s",
        density="998 kg/m^3",
        viscosity="1.002e-3 Pa*s",
        roughness="0.045 mm",
        output_unit="Pa",
    )
    assert r.ok, r.error
    assert r.data["regime"] == "turbulent"
    assert math.isclose(r.values["reynolds"].quantity, Re, rel_tol=1e-9)
    assert math.isclose(r.value.quantity, dp_hand, rel_tol=0.03)
    assert math.isclose(r.value.uncertainty, 0.10 * r.value.quantity, rel_tol=1e-9)
    assert 20_000 < r.value.quantity < 22_000  # ~0.21 bar


def test_pipe_pressure_drop_rejects_kinematic_viscosity():
    r = _call(
        pipe_pressure_drop,
        diameter="10 mm",
        length="1 m",
        flow_rate="5 L/min",
        density="870 kg/m^3",
        viscosity="46 cSt",
    )
    assert r.ok is False
    assert r.error.family == "units.dimension_mismatch"
    assert r.error.details["param"] == "viscosity"


def test_pipe_pressure_drop_rejects_bare_number():
    r = _call(
        pipe_pressure_drop,
        diameter=10,
        length="1 m",
        flow_rate="5 L/min",
        density="870 kg/m^3",
        viscosity="40 cP",
    )
    assert r.ok is False
    assert r.error.family == "units.bare_number"
    assert r.error.details["param"] == "diameter"


@pytest.mark.parametrize("field", ["diameter", "length", "flow_rate"])
def test_pipe_pressure_drop_rejects_non_positive(field):
    kwargs = dict(diameter="10 mm", length="1 m", flow_rate="5 L/min", density="870 kg/m^3", viscosity="40 cP")
    unit = kwargs[field].split(" ", 1)[1]
    kwargs[field] = f"0 {unit}"
    r = _call(pipe_pressure_drop, **kwargs)
    assert r.ok is False
    assert r.error.family == "input.invalid"
    assert r.error.details["param"] == field
