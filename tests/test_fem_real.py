"""fem_mesh and fem_static against the real Gmsh and CalculiX, checked by closed-form solutions.  Skipped when OCP, Gmsh or CalculiX is absent.

Material everywhere: E = 210000 MPa (steel), nu = 0.3, so G = E / (2 (1 + nu)) = 80769.23 MPa.

UNIAXIAL BAR 10 x 10 x 100 mm, ends x = 0 and x = 100, F = 10 kN along +x at x = 100 (A = 100 mm^2):
    stress          sigma = F / A = 10000 / 100                = 100 MPa (von Mises = sigma in uniaxial tension)
    strain          eps   = sigma / E = 100 / 210000           = 4.7619048e-4
    tip displacement u    = eps L = 4.7619048e-4 * 100         = 0.047619048 mm
    Poisson contraction at a corner of the section, 5 mm from the axis in y and in z (v = -nu eps * 5):
                    0.3 * 4.7619048e-4 * 5                     = 7.1428571e-4 mm in y and in z
    largest displacement magnitude (the corner at the tip, where all three add):
                    sqrt(0.047619048^2 + 2 * (7.1428571e-4)^2) = 0.047629763 mm
  The support is the minimal one: ux = 0 on the face x = 0 (the section may contract and shear freely there) and three point
  supports at nodes where the exact solution is zero in the constrained direction: uy at (0, 5, 0) and (0, 5, 10) (u_y = -nu eps (y - 5) = 0
  on y = 5) and uz at (0, 0, 5) (u_z = -nu eps (z - 5) = 0 on z = 5).  The field is linear in x, so quadratic elements reproduce it
  exactly: the only error is the 6 digits CalculiX prints (1e-6 in .dat, up to 1e-5 in the nodal stresses of .frd).  Tolerance 1e-4.

CANTILEVER 100 x 10 x 10 mm (L = 100, b = h = 10), P = 1 kN along -z at x = 100:
    I = b h^3 / 12 = 10 * 1000 / 12                            = 833.3333 mm^4
    Euler-Bernoulli  P L^3 / (3 E I) = 1000 * 1e6 / (3 * 210000 * 833.3333) = 1e9 / 5.25e8 = 1.904762 mm
    shear (Timoshenko) P L / (kappa G A), kappa = 10 (1 + nu) / (12 + 11 nu) = 13 / 15.3 = 0.849673 (Cowper, rectangle):
                    1000 * 100 / (0.849673 * 80769.23 * 100)   = 1e5 / 6862745 = 0.014571 mm
    total tip deflection                                       = 1.919333 mm
    bending stress at mid-span, top face: M c / I = (P * 50) * 5 / I = 50000 * 5 / 833.3333 = 300 MPa
  The deflection is read at the neutral-axis nodes of the tip section (the largest displacement MAGNITUDE is a tip corner, which also moves
  along x by the end rotation 5 * P L^2 / (2 E I) = 5 * 0.02857 = 0.143 mm, so it is not the deflection).
  Two roots, both measured on h = 10, 5, 2.5, 1.5, 1.25 mm:
    "x = 0 mm : ux uz" and uy at two points: the face may not rotate or deflect but its section may contract and shear
        freely; tip 1.9074, 1.9110, 1.9128, 1.9135, 1.9135 mm, converging to 0.30 % below Timoshenko and 0.46 % above Euler-Bernoulli:
        the shear term is in the answer.  Tolerance 0.5 % of Timoshenko.
    "x = 0 mm" (the textbook clamp, all three displacements): the section cannot contract at the root, so the beam is stiffer: tip
        1.8942, 1.9012, 1.9042, 1.9055, 1.9057 mm, 0.71 % below Timoshenko, 0.05 % above Euler-Bernoulli (the root restraint takes back
        what the shear adds).  Tolerance 1.5 % of Timoshenko.  Its largest von Mises stress sits at the root corner, is singular, and
        grows with refinement (590, 600, 594, 687, 724 MPa): never compared with beam theory here.

HYDROSTATIC BLOCK 10 x 10 x 10 mm, rollers on x = 0 (ux), y = 0 (uy), z = 0 (uz), pressure p = 100 MPa on x = 10, y = 10, z = 10:
    stress state    sigma_xx = sigma_yy = sigma_zz = -p, no shear  ->  von Mises = 0
    strain          eps = -p (1 - 2 nu) / E = -100 * 0.4 / 210000   = -1.9047619e-4
    displacement    u = eps * 10 = -1.9047619e-3 mm on each face; largest magnitude at the far corner sqrt(3) * 1.9047619e-3 = 3.2991e-3 mm
    reactions       one roller face of area 100 mm^2 carries p * A = 100 * 100 = 10000 N in each direction

EQUILIBRIUM: the reactions are read from CalculiX and compared with the loads the toolkit applied; they agree to better than 1e-6
(measured about 1e-14), and the call fails otherwise (checked below with a doctored solver result).
"""

import csv
import io
import math
import os
import re
from pathlib import Path

import pytest

pytest.importorskip("OCP", reason="OCP is not installed; run: uv sync --extra cad")

from eng_toolkit import external as X
from eng_toolkit.domains.fem import selectors as sel
from eng_toolkit.domains.fem import solver
from eng_toolkit.domains.fem import tools as T
from eng_toolkit.external_catalog import CCX, GMSH
from tests import cad_fixtures as cad
from tests.test_fem_internals import assert_deck_is_closed

pytestmark = [
    pytest.mark.skipif(X.locate(GMSH)[0] is None, reason="Gmsh is not installed"),
    pytest.mark.skipif(X.locate(CCX)[0] is None, reason="CalculiX is not installed"),
]

E = "210000 MPa"
BAR_SUPPORTS = "x = 0 mm : ux; point(0 mm, 5 mm, 0 mm) : uy; point(0 mm, 5 mm, 10 mm) : uy; point(0 mm, 0 mm, 5 mm) : uz"
SQRT2 = math.sqrt(2.0)


def write_step_inches(shape, path: Path) -> Path:
    """A STEP file whose length unit is the inch and whose numbers are in inches.  (tests/dfm_fixtures.write_step_inches sets the
    unit before the writer exists, and with the name 'IN'; OCCT ignores both and writes millimetres.)"""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    writer = STEPControl_Writer()  # registers the parameters
    assert Interface_Static.SetCVal_s("write.step.unit", "INCH")
    try:
        writer.Transfer(shape, STEPControl_AsIs)
        assert writer.Write(str(path)) == IFSelect_RetDone
    finally:
        Interface_Static.SetCVal_s("write.step.unit", "MM")
    return path


def bar(tmp_path, name="bar.step", length=100.0, width=10.0, height=10.0):
    return str(cad.write_step(cad.box(0, 0, 0, length, width, height), tmp_path / name))


def static(model, **over):
    args = dict(model_path=model, element_size="5 mm", youngs_modulus=E, poisson_ratio=0.3, supports=BAR_SUPPORTS, loads="force (10 kN, 0 N, 0 N) on x = 100 mm")
    args.update(over)
    return T.static(**args)


def ok(result):
    assert result.ok, result.error
    assert result.review_status == "unreviewed"
    return result


def v(result, name):
    return result.values[name].quantity


def artifact(result, name):
    return next(Path(a.path) for a in result.artifacts if Path(a.path).name == name)


def read_csv(result):
    rows = list(csv.DictReader(io.StringIO(artifact(result, "results.csv").read_text())))
    return {k: [float(r[k]) for r in rows] for k in rows[0]}


# --------------------------------------------------------------------------- uniaxial bar


@pytest.mark.parametrize("size", ["5 mm", "2.5 mm"])
def test_uniaxial_bar_matches_force_length_over_area_modulus(tmp_path, size):
    r = ok(static(bar(tmp_path), element_size=size))
    tip_x = 10000.0 * 100.0 / (100.0 * 210000.0)  # F L / (A E) = 0.047619048 mm
    assert r.data["max_displacement_components_mm"][0] == pytest.approx(tip_x, rel=1e-4)
    assert v(r, "max_displacement") == pytest.approx(math.sqrt(tip_x**2 + 2 * (0.3 * tip_x / 100.0 * 5.0) ** 2), rel=1e-4)  # 0.047629763 mm
    assert v(r, "max_von_mises") == pytest.approx(100.0, rel=1e-4)
    assert r.values["max_displacement"].unit == "millimeter" and r.values["max_von_mises"].unit == "megapascal"
    # the largest displacement is at the tip, in a corner
    x, y, z = r.data["max_displacement_at_mm"]
    assert x == pytest.approx(100.0) and y in (0.0, 10.0) and z in (0.0, 10.0)


def test_poisson_contraction_is_free_with_the_minimal_supports(tmp_path):
    r = ok(static(bar(tmp_path)))
    contraction = 0.3 * (100.0 / 210000.0) * 5.0  # 7.1428571e-4 mm at 5 mm from the axis
    ux, uy, uz = r.data["max_displacement_components_mm"]
    assert uy == pytest.approx(contraction, rel=1e-4) and uz == pytest.approx(contraction, rel=1e-4)


def test_a_fully_fixed_face_over_constrains_the_contraction_and_says_so(tmp_path):
    free = ok(static(bar(tmp_path)))
    clamped = ok(static(bar(tmp_path), supports="x = 0 mm"))
    # the clamp stops the section contracting at the root, so the section near it carries more than 100 MPa and the contraction is smaller
    assert v(clamped, "max_von_mises") > v(free, "max_von_mises") * 1.01
    root_contraction = lambda r: max(abs(uy) for x, uy in zip(read_csv(r)["x_mm"], read_csv(r)["uy_mm"]) if abs(x) < 1e-9)  # noqa: E731
    assert root_contraction(free) == pytest.approx(0.3 * (100.0 / 210000.0) * 5.0, rel=1e-4)  # the section contracts at the root: 7.1428571e-4 mm
    assert root_contraction(clamped) == 0.0  # and the clamp forbids it
    assert clamped.data["max_displacement_components_mm"][0] < free.data["max_displacement_components_mm"][0]  # a stiffer bar
    assert any("over-constrain" in w or "prevents the cross-section from contracting" in w for w in clamped.warnings)
    assert not any("prevents the cross-section" in w for w in free.warnings)


def test_reactions_equal_the_applied_load(tmp_path):
    r = ok(static(bar(tmp_path)))
    assert v(r, "applied_force_x") == pytest.approx(10000.0, rel=1e-12)
    assert v(r, "reaction_force_x") == pytest.approx(-10000.0, rel=1e-6)
    assert abs(v(r, "reaction_force_y")) < 1e-6 and abs(v(r, "reaction_force_z")) < 1e-6
    equilibrium = r.data["equilibrium"]
    assert equilibrium["force_residual_relative"] < 1e-6 and equilibrium["moment_residual_relative"] < 1e-5
    assert v(r, "equilibrium_residual") < 1e-6


def test_the_csv_holds_every_node_and_the_stress_of_the_bar(tmp_path):
    r = ok(static(bar(tmp_path)))
    table = read_csv(r)
    assert len(table["node"]) == int(v(r, "node_count"))
    assert list(table) == list(T.CSV_COLUMNS)
    assert max(table["von_mises_mpa"]) == pytest.approx(100.0, rel=1e-4)
    assert max(table["u_mag_mm"]) == pytest.approx(v(r, "max_displacement"), rel=1e-6)
    assert all(math.isfinite(x) for column in table.values() for x in column)
    # sxx = 100 MPa everywhere (4-digit rows of the frd): its mean over the nodes
    assert sum(table["sxx_mpa"]) / len(table["sxx_mpa"]) == pytest.approx(100.0, rel=1e-4)


def test_artifacts_are_the_deck_the_results_and_the_mesh(tmp_path):
    r = ok(static(bar(tmp_path)))
    names = {Path(a.path).name for a in r.artifacts}
    assert {"model.inp", "result.frd", "result.dat", "results.csv", "mesh.msh", "inputs.json", "result.json", "manifest.json"} >= {"model.inp", "result.frd", "result.dat", "results.csv", "mesh.msh", "inputs.json"}
    assert {"model.inp", "result.frd", "result.dat", "results.csv", "mesh.msh"} <= names
    manifest = Path(r.data["job_dir"], "manifest.json").read_text()
    for name in ("model.inp", "result.frd", "result.dat", "results.csv", "mesh.msh"):
        assert name in manifest  # every artifact is hashed into the manifest
    deck = artifact(r, "model.inp").read_text()
    assert_deck_is_closed(deck)
    assert "C3D10" in deck


# --------------------------------------------------------------------------- cantilever


BEAM_L, BEAM_I, BEAM_P = 100.0, 10.0 * 10.0**3 / 12.0, 1000.0
EULER = BEAM_P * BEAM_L**3 / (3 * 210000.0 * BEAM_I)  # 1.904762 mm
KAPPA = 10 * 1.3 / (12 + 11 * 0.3)  # 0.849673
SHEAR = BEAM_P * BEAM_L / (KAPPA * (210000.0 / 2.6) * 100.0)  # 0.014571 mm
TIMOSHENKO = EULER + SHEAR  # 1.919333 mm


FREE_ROOT = "x = 0 mm : ux uz; point(0 mm, 5 mm, 0 mm) : uy; point(0 mm, 5 mm, 10 mm) : uy"


def cantilever(tmp_path, size, supports="x = 0 mm", name="beam.step"):
    return static(bar(tmp_path, name), element_size=size, supports=supports, loads="force (0 N, 0 N, -1 kN) on x = 100 mm")


def tip_deflection(result) -> float:
    """Mean uz of the nodes of the tip section on the neutral axis (z = 5 mm); negative is down."""
    t = read_csv(result)
    values = [uz for x, z, uz in zip(t["x_mm"], t["z_mm"], t["uz_mm"]) if abs(x - 100.0) < 1e-6 and abs(z - 5.0) < 1e-6]
    assert values
    return sum(values) / len(values)


def midspan_top_stress(result) -> float:
    t = read_csv(result)
    values = [s for x, z, s in zip(t["x_mm"], t["z_mm"], t["sxx_mpa"]) if abs(x - 50.0) < 1e-6 and abs(z - 10.0) < 1e-6]
    assert values
    return sum(values) / len(values)


def test_beam_theory_numbers_by_hand():
    assert EULER == pytest.approx(1.904762, abs=1e-6)
    assert SHEAR == pytest.approx(0.014571, abs=1e-6)
    assert TIMOSHENKO == pytest.approx(1.919333, abs=1e-6)


def test_cantilever_with_a_free_section_root_shows_the_shear_term(tmp_path):
    r = ok(cantilever(tmp_path, "2.5 mm", FREE_ROOT))
    tip = -tip_deflection(r)
    assert tip == pytest.approx(TIMOSHENKO, rel=0.005)  # measured 1.9128 mm, 0.35 % below
    assert EULER < tip < TIMOSHENKO  # the shear term is in the result
    assert v(r, "reaction_force_z") == pytest.approx(1000.0, rel=1e-6) and v(r, "applied_force_z") == pytest.approx(-1000.0, rel=1e-12)
    assert midspan_top_stress(r) == pytest.approx(300.0, rel=0.01)  # M c / I


def test_cantilever_with_the_textbook_clamp_is_beam_theory_within_one_and_a_half_percent(tmp_path):
    r = ok(cantilever(tmp_path, "2.5 mm"))
    tip = -tip_deflection(r)
    assert tip == pytest.approx(TIMOSHENKO, rel=0.015)  # measured 1.9042 mm, 0.78 % below
    assert tip < TIMOSHENKO  # the root restraint stiffens the beam
    assert r.data["max_displacement_at_mm"][0] == pytest.approx(100.0)
    assert v(r, "reaction_force_z") == pytest.approx(1000.0, rel=1e-6)
    assert midspan_top_stress(r) == pytest.approx(300.0, rel=0.03)
    table = read_csv(r)
    bottom = [s for x, z, s in zip(table["x_mm"], table["z_mm"], table["sxx_mpa"]) if abs(x - 50.0) < 1e-6 and abs(z) < 1e-6]
    assert sum(bottom) / len(bottom) == pytest.approx(-300.0, rel=0.03)


def test_halving_the_element_size_moves_the_cantilever_toward_the_converged_value(tmp_path):
    tips = {}
    for size in ("10 mm", "5 mm", "2.5 mm", "1.25 mm"):
        tips[size] = -tip_deflection(ok(cantilever(tmp_path, size, FREE_ROOT, name=f"beam-{size.split()[0]}.step")))
    finest = tips["1.25 mm"]
    errors = [abs(tips[s] - finest) for s in ("10 mm", "5 mm", "2.5 mm")]
    assert errors[0] > errors[1] > errors[2] > 0.0, tips  # each halving gets closer to the finest mesh
    assert errors[0] > 3 * errors[2]  # measured: 6.1e-3, 2.5e-3, 7.5e-4 mm
    assert list(tips.values()) == sorted(tips.values())  # and from below
    assert finest == pytest.approx(TIMOSHENKO, rel=0.005)  # the finest sits 0.30 % below beam theory


# --------------------------------------------------------------------------- pressure and rollers


def block(tmp_path):
    return str(cad.write_step(cad.box(0, 0, 0, 10, 10, 10), tmp_path / "block.step"))


def test_hydrostatic_pressure_gives_uniform_compression_and_no_von_mises(tmp_path):
    r = ok(
        static(
            block(tmp_path),
            supports="x = 0 mm : ux; y = 0 mm : uy; z = 0 mm : uz",
            loads="pressure 100 MPa on x = 10 mm; pressure 100 MPa on normal +y at y = 10 mm; pressure 0.1 GPa on z = 10 mm",
        )
    )
    eps = 100.0 * (1 - 2 * 0.3) / 210000.0  # 1.9047619e-4 (compressive)
    assert max(abs(c) for c in r.data["max_displacement_components_mm"]) == pytest.approx(eps * 10.0, rel=1e-4)  # 1.9047619e-3 mm
    assert v(r, "max_displacement") == pytest.approx(math.sqrt(3.0) * eps * 10.0, rel=1e-4)  # 3.2991e-3 mm at the far corner
    assert r.data["max_displacement_at_mm"] == pytest.approx([10.0, 10.0, 10.0])
    assert v(r, "max_von_mises") < 1e-3  # zero in theory; CalculiX prints 6 digits of 100 MPa
    table = read_csv(r)
    assert sum(table["sxx_mpa"]) / len(table["sxx_mpa"]) == pytest.approx(-100.0, rel=1e-4)
    assert v(r, "applied_force_x") == pytest.approx(-10000.0, rel=1e-6)  # p * A on a face whose outward normal is +x, pressing in: -x
    assert v(r, "reaction_force_x") == pytest.approx(10000.0, rel=1e-6)


def test_a_negative_pressure_pulls_and_the_sign_of_the_force_follows(tmp_path):
    r = ok(static(block(tmp_path), supports="x = 0 mm : ux; y = 0 mm : uy; z = 0 mm : uz", loads="pressure -50 MPa on x = 10 mm"))
    assert v(r, "applied_force_x") == pytest.approx(5000.0, rel=1e-9)  # -50 MPa * 100 mm^2, pulling out along +x
    assert r.data["max_displacement_components_mm"][0] > 0.0


# --------------------------------------------------------------------------- units


def test_a_model_in_inches_gives_the_same_physics(tmp_path):
    path = str(write_step_inches(cad.box(0, 0, 0, 100, 10, 10), tmp_path / "bar-inch.step"))  # a 100 mm bar, written as 3.937 inch
    text = Path(path).read_text(errors="replace")
    assert "CONVERSION_BASED_UNIT('INCH'" in text and "3.937007874" in text  # the file says inch and its numbers are inches
    r = ok(
        static(
            path,
            element_size="0.2 inch",  # 5.08 mm
            supports="x = 0 inch : ux; point(0 inch, 0.19685039370078738 inch, 0 inch) : uy; point(0 mm, 5 mm, 10 mm) : uy; point(0 mm, 0 mm, 5 mm) : uz",
            loads="force (2248.0894308 lbf, 0 N, 0 N) on x = 3.937007874015748 inch",  # 10 kN = 10000 / 4.4482216152605 lbf
            output_length_unit="inch",
            output_stress_unit="psi",
        )
    )
    assert r.data["max_displacement_components_mm"][0] == pytest.approx(0.047619048, rel=1e-4)
    assert v(r, "max_von_mises") == pytest.approx(100.0 * 145.0377377, rel=1e-4)  # 100 MPa in psi
    assert r.values["max_von_mises"].unit == "pound_force_per_square_inch"
    assert v(r, "max_displacement") == pytest.approx(0.047629763 / 25.4, rel=1e-4)
    assert r.values["max_displacement"].unit == "inch"
    assert v(r, "applied_force_x") == pytest.approx(10000.0, rel=1e-7)
    assert v(r, "reaction_force_x") == pytest.approx(-10000.0, rel=1e-7)


# --------------------------------------------------------------------------- the mesh tool


def test_mesh_reports_counts_quality_and_the_exact_volume(tmp_path):
    r = ok(T.mesh(model_path=bar(tmp_path), element_size="5 mm"))
    assert r.value.quantity == v(r, "element_count") and v(r, "node_count") > v(r, "element_count")
    assert v(r, "exact_volume") == pytest.approx(10000.0, rel=1e-6)  # 10 * 10 * 100
    assert v(r, "mesh_volume") == pytest.approx(10000.0, rel=1e-6)
    assert abs(v(r, "volume_relative_difference")) < 1e-9  # flat faces: the mesh fills the box exactly
    assert 0.2 < v(r, "min_quality") <= v(r, "mean_quality") <= 1.0
    assert r.data["quality"]["slivers"] == 0
    # the estimate is 6 sqrt(2) * 10000 / 5^3 = 678.8; Gmsh makes fewer, in the same order of magnitude
    assert r.data["estimated_elements"] == pytest.approx(678.82, abs=0.01)
    assert 0.3 * 678.8 < v(r, "element_count") < 1.5 * 678.8
    msh = artifact(r, "mesh.msh").read_text()
    assert msh.startswith("$MeshFormat\n2.2 0 8")


def test_mesh_volume_of_a_cylinder_is_pi_r_squared_h_to_a_part_in_ten_thousand(tmp_path):
    cylinder = str(cad.write_step(cad.cylinder(0, 0, 0, 10.0, 30.0), tmp_path / "cyl.step"))
    exact = math.pi * 100.0 * 30.0  # 9424.7780 mm^3
    for size in ("8 mm", "4 mm", "2 mm"):
        r = ok(T.mesh(model_path=cylinder, element_size=size))
        assert v(r, "exact_volume") == pytest.approx(exact, rel=1e-9)
        # second-order elements put their midside nodes on the true cylinder: measured 2.6e-5, 2.6e-5, 1.8e-6 (not monotone: Gmsh's
        # subdivision of the circle changes with the size), against about 1e-2 for straight-sided elements of this size
        assert abs(v(r, "volume_relative_difference")) < 1e-4


def test_mesh_in_other_units(tmp_path):
    r = ok(T.mesh(model_path=bar(tmp_path), element_size="0.5 cm", output_unit="cm"))
    assert v(r, "exact_volume") == pytest.approx(10.0, rel=1e-9)  # 10000 mm^3 = 10 cm^3
    assert r.values["exact_volume"].unit == "centimeter ** 3"


def test_a_coarse_mesh_warns(tmp_path):
    r = ok(T.mesh(model_path=bar(tmp_path), element_size="9 mm"))
    assert any("more than half the thinnest side" in w for w in r.warnings)


# --------------------------------------------------------------------------- refused, with the programs available


def failure(result, family):
    assert not result.ok and result.error is not None
    assert result.error.family == family, result.error
    assert result.warnings == []
    return result.error.message


def test_a_selector_that_matches_nothing_is_an_error_naming_it(tmp_path):
    message = failure(static(bar(tmp_path), supports="x = 55 mm"), "fem.empty_selection")
    assert "'x = 55 mm'" in message
    message = failure(static(bar(tmp_path), loads="force (1 kN, 0 N, 0 N) on normal -x at x = 100 mm"), "fem.empty_selection")  # the end faces +x
    assert "normal -x at x = 100 mm" in message


def test_supports_that_leave_a_rigid_body_motion_are_refused_before_the_solver(tmp_path):
    message = failure(static(bar(tmp_path), supports="x = 0 mm : ux"), "fem.not_constrained")
    assert "rigid-body" in message and "translation along y" in message and "rotation about x" in message
    failure(static(bar(tmp_path), supports="point(0 mm, 5 mm, 5 mm)"), "fem.not_constrained")  # one pinned node: three rotations free


def test_a_loaded_support_is_refused(tmp_path):
    message = failure(static(bar(tmp_path), supports="x = 100 mm"), "fem.overlapping_selection")
    assert "both a support" in message and "loaded" in message
    failure(static(bar(tmp_path), supports="x = 0 mm; point(100 mm, 5 mm, 5 mm)"), "fem.overlapping_selection")


def test_a_point_support_far_from_the_model_is_refused(tmp_path):
    message = failure(static(bar(tmp_path), supports="x = 0 mm; point(500 mm, 5 mm, 5 mm) : uy"), "fem.point_not_on_model")
    assert "not on the model" in message


def test_too_fine_a_mesh_is_refused_before_gmsh_starts(tmp_path, monkeypatch):
    def fail(*args, **kwargs):  # noqa: ANN002
        raise AssertionError("a program was started")

    monkeypatch.setattr(X, "run", fail)
    message = failure(static(bar(tmp_path), element_size="1e-9 mm"), "input.too_large")
    assert "elements" in message and "8.49e+31" in message
    failure(static(bar(tmp_path), element_size="0.5 mm"), "input.too_large")  # 6 sqrt(2) * 10000 / 0.125 = 679 000 elements
    failure(T.mesh(model_path=bar(tmp_path), element_size="0.5 mm"), "input.too_large")


def test_a_load_that_dwarfs_the_modulus_is_out_of_range(tmp_path):
    failure(static(bar(tmp_path), loads="force (1e12 N, 0 N, 0 N) on x = 100 mm"), "input.out_of_range")  # 1e12 / 100 mm^2 = 1e10 MPa on a 210000 MPa material


def test_a_non_solid_and_a_zero_volume_solid_are_refused(tmp_path):
    shell = str(cad.write_step(cad.planar_shell(), tmp_path / "plate.step"))
    message = failure(T.mesh(model_path=shell, element_size="5 mm"), "input.invalid")
    assert "closed solid" in message
    failure(static(shell), "input.invalid")


def test_two_solids_are_refused(tmp_path):
    pair = str(cad.write_step(cad.compound(cad.box(0, 0, 0, 10, 10, 10), cad.box(50, 0, 0, 10, 10, 10)), tmp_path / "pair.step"))
    message = failure(T.mesh(model_path=pair, element_size="5 mm"), "input.unsupported")
    assert "2 solids" in message


# --------------------------------------------------------------------------- nothing from the caller reaches a program


def test_the_programs_get_fixed_arguments_and_only_the_toolkits_own_files(tmp_path, monkeypatch):
    calls = []
    real = X.run

    def spy(tool, args, **kwargs):
        calls.append((tool.name, [str(a) for a in args], kwargs["workdir"]))
        return real(tool, args, **kwargs)

    monkeypatch.setattr(X, "run", spy)
    r = ok(static(bar(tmp_path), supports=BAR_SUPPORTS + "; x = 0 mm : uy"))  # the extra clause is legal but irrelevant to the check
    real_runs = [c for c in calls if "eng-toolkit-ver" not in str(c[2])]  # not the version probes of the result's data
    assert [c[0] for c in real_runs] == ["gmsh", "ccx"]
    gmsh_args = real_runs[0][1]
    assert Path(gmsh_args[0]).name == "part.brep" and gmsh_args[1:4] == ["-3", "-order", "2"]
    assert re.fullmatch(r"[0-9.e+-]+", gmsh_args[gmsh_args.index("-clmax") + 1])
    assert not any(a.endswith(".geo") or a.startswith("-") and a in ("-", "-run", "-setstring", "-string", "-merge", "-check") for a in gmsh_args)
    assert real_runs[1][1] == ["-i", "job"]
    job = Path(r.data["job_dir"])
    assert not list(job.rglob("*.geo")), "no Gmsh script is ever written"
    assert sorted(p.name for p in (job / "calculix").glob("*.inp")) == ["job.inp"]


def test_text_inside_the_cad_file_never_reaches_the_deck(tmp_path):
    path = Path(bar(tmp_path))
    text = path.read_text(errors="replace")
    marked = re.sub(r"'Open CASCADE STEP translator[^']*'", "'*INCLUDE, INPUT=evil.inp'", text)
    assert marked != text
    path.write_text(marked)
    r = ok(static(str(path)))
    deck = artifact(r, "model.inp").read_text()
    assert "evil" not in deck and "INCLUDE" not in deck
    assert_deck_is_closed(deck)


def test_a_failed_equilibrium_discards_the_result(tmp_path, monkeypatch):
    real = solver.read_dat

    def doctored(path, n):
        out = real(path, n)
        return type(out)(out.displacement, out.reaction_nodes, out.reaction, out.reaction_total * 1.001)  # the reaction total off by 0.1 %

    monkeypatch.setattr(solver, "read_dat", doctored)
    message = failure(static(bar(tmp_path)), "fem.equilibrium_failed")
    assert "do not balance" in message and "discarded" in message
