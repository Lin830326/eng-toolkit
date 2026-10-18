"""metrology_check_scan checked against hand calculations on a 100 x 50 x 20 mm box.

Every point of a synthetic scan is placed at a known offset from a known face, so the expected
deviations follow from arithmetic.  Deviation is the signed distance to the model surface,
positive outside the material.  Distances are recomputed in float64 against the tessellated
mesh, so values are compared to 1e-6 mm.

Offsets from the top face z = 20 (200 points each, the same x/y grid):

    200 points at z = 20.05  -> +0.05 mm
    200 points at z = 19.97  -> -0.03 mm

    mean = (200 * 0.05 - 200 * 0.03) / 400                 = 0.01 mm
    rms  = sqrt((0.05^2 + 0.03^2) / 2) = sqrt(0.0017)      = 0.0412311 mm
    std  = sqrt(rms^2 - mean^2) = sqrt(0.0017 - 0.0001)    = 0.04 mm   (population, ddof = 0)
    max |d| = 0.05, max positive = 0.05, minimum signed = -0.03
    p95 of |d|: half the points are 0.05, so the 95th percentile is 0.05 mm
    tolerance 0.04 mm: only the 200 points at +0.05 are out of tolerance -> 0.5
    tolerance 0.06 mm: none -> 0.0
"""

import csv
import io
import json
import math
from pathlib import Path

import numpy as np
import pytest

_NEED_ENGINES = "the scan engines are not installed; run: uv sync --extra scan"
pytest.importorskip("open3d", reason=_NEED_ENGINES)
pytest.importorskip("OCP", reason=_NEED_ENGINES)

from OCP.BRep import BRep_Builder  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder  # noqa: E402
from OCP.gp import gp_Pln, gp_Pnt  # noqa: E402
from OCP.IFSelect import IFSelect_RetDone  # noqa: E402
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer  # noqa: E402
from OCP.TopoDS import TopoDS_Compound  # noqa: E402

from eng_toolkit.contract import ToolResult  # noqa: E402
from eng_toolkit.domains.metrology import scan as scan_module  # noqa: E402
from eng_toolkit.domains.metrology.tools import check_scan  # noqa: E402

TOL = 1e-6  # mm, float64 distance to the tessellated mesh
RMS_HAND = math.sqrt(0.0017)


# --------------------------------------------------------------------------- fixtures


def _write_step(shape, path: Path) -> Path:
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    assert writer.Write(str(path)) == IFSelect_RetDone
    return path


def _box(x=0.0, y=0.0, z=0.0, length=100.0, width=50.0, height=20.0):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), length, width, height).Shape()


def _compound(*shapes):
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


def write_binary_ply(path: Path, points, *, endian="little", scalar="double", extra=b"", extra_header=()) -> Path:
    code = "<" if endian == "little" else ">"
    pts = np.asarray(points, dtype=float).reshape(-1, 3).astype(code + {"double": "f8", "float": "f4"}[scalar])
    header = [
        "ply",
        f"format binary_{endian}_endian 1.0",
        f"element vertex {len(pts)}",
        f"property {scalar} x",
        f"property {scalar} y",
        f"property {scalar} z",
        *extra_header,
        "end_header",
    ]
    path.write_bytes(("\n".join(header) + "\n").encode("ascii") + pts.tobytes() + extra)
    return path


def write_ply(path: Path, points) -> Path:
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    header = [
        "ply",
        "format ascii 1.0",
        f"element vertex {len(pts)}",
        "property double x",
        "property double y",
        "property double z",
        "end_header",
    ]
    body = [" ".join(repr(float(v)) for v in row) for row in pts]
    path.write_text("\n".join(header + body) + "\n", encoding="ascii")
    return path


@pytest.fixture(scope="module")
def models(tmp_path_factory):
    root = tmp_path_factory.mktemp("models")
    shell = BRepBuilderAPI_MakeFace(gp_Pln(), 0.0, 100.0, 0.0, 50.0).Face()
    return {
        "box": _write_step(BRepPrimAPI_MakeBox(100.0, 50.0, 20.0).Shape(), root / "box.step"),
        "cylinder": _write_step(BRepPrimAPI_MakeCylinder(10.0, 30.0).Shape(), root / "cylinder.stp"),
        "shell": _write_step(shell, root / "shell.step"),
        "plate": _write_step(_box(length=3000.0), root / "plate.step"),
        "far_box": _write_step(_box(10_000.0, 10_000.0, 10_000.0), root / "far_box.step"),
    }


@pytest.fixture
def box(models):
    return models["box"]


def grid(xs, ys, z):
    gx, gy = np.meshgrid(xs, ys)
    return np.column_stack([gx.ravel(), gy.ravel(), np.full(gx.size, float(z))])


def top_face_grid(z):
    """200 points on the top face: 20 x 10 grid over x 10..90, y 10..40."""
    return grid(np.linspace(10, 90, 20), np.linspace(10, 40, 10), z)


def two_sided_cloud():
    """The -0.03 layer is offset in x/y so that no point sits 0.08 mm above or below another one."""
    return np.vstack([top_face_grid(20.05), top_face_grid(19.97) + [2.1, 1.6, 0.0]])


def three_face_cloud(shift_x=0.0):
    """Points at least 6 mm from every edge of the top, the x = 100 and the y = 50 faces."""
    top = grid(np.linspace(6, 94, 21), np.linspace(6, 44, 9), 20.0)
    side_x = np.column_stack(
        [np.full(45, 100.0), *[a.ravel() for a in np.meshgrid(np.linspace(6, 44, 9), np.linspace(6, 14, 5))]]
    )
    gx, gz = np.meshgrid(np.linspace(6, 94, 21), np.linspace(6, 14, 5))
    side_y = np.column_stack([gx.ravel(), np.full(gx.size, 50.0), gz.ravel()])
    cloud = np.vstack([top, side_x, side_y])
    return cloud + np.array([shift_x, 0.0, 0.0])


def run(scan_path, model_path, **kwargs) -> ToolResult:
    params = {"scan_unit": "mm", "tolerance": "0.04 mm"}
    params.update(kwargs)
    fn = getattr(check_scan, "fn", check_scan)
    result = fn(scan_path=str(scan_path), model_path=str(model_path), **params)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def scan_file(tmp_path, points, name="scan.ply") -> Path:
    return write_ply(tmp_path / name, points)


def mm(r: ToolResult, key: str) -> float:
    return r.values[key].quantity


# --------------------------------------------------------------------------- statistics


def test_two_sided_offsets_match_the_hand_calculation(tmp_path, box):
    r = run(scan_file(tmp_path, two_sided_cloud()), box, tolerance="0.04 mm")
    assert r.ok, r.error
    assert r.provenance.engine == "eng_toolkit.metrology.check_scan"
    assert r.value == r.values["rms_deviation"]
    assert r.value.unit == "millimeter"
    assert mm(r, "rms_deviation") == pytest.approx(RMS_HAND, abs=TOL)
    assert mm(r, "mean_deviation") == pytest.approx(0.01, abs=TOL)
    assert mm(r, "std_deviation") == pytest.approx(0.04, abs=TOL)
    assert mm(r, "max_deviation") == pytest.approx(0.05, abs=TOL)
    assert mm(r, "max_positive_deviation") == pytest.approx(0.05, abs=TOL)
    assert mm(r, "max_negative_deviation") == pytest.approx(-0.03, abs=TOL)
    assert mm(r, "p95_abs_deviation") == pytest.approx(0.05, abs=TOL)
    assert r.values["out_of_tolerance_fraction"].quantity == pytest.approx(0.5)
    assert r.values["out_of_tolerance_fraction"].unit == "dimensionless"
    assert r.values["points_evaluated"].quantity == 400
    assert r.values["points_evaluated"].unit == "dimensionless"
    assert r.data["signed"] is True
    assert r.data["tolerance_mm"] == pytest.approx(0.04)
    assert r.data["points"] == {"original": 400, "used": 400}
    assert r.data["mesh"]["watertight"] is True
    assert r.data["mesh"]["triangles"] == 12
    assert "outside" in r.data["sign_convention"]


def test_histogram_spans_zero_to_the_largest_absolute_deviation(tmp_path, box):
    r = run(scan_file(tmp_path, two_sided_cloud()), box)
    hist = r.data["histogram"]
    assert len(hist["counts"]) == 20 and len(hist["edges"]) == 21
    assert sum(hist["counts"]) == 400
    assert hist["edges"][0] == 0.0
    assert hist["edges"][-1] == pytest.approx(0.05, abs=TOL)
    assert hist["counts"][-1] == 200  # the +0.05 points sit in the last bin


def test_worst_points_are_the_plus_0_05_points_with_their_coordinates(tmp_path, box):
    r = run(scan_file(tmp_path, two_sided_cloud()), box)
    worst = r.data["worst_points"]
    assert len(worst) == 10
    for p in worst:
        assert p["deviation_mm"] == pytest.approx(0.05, abs=TOL)
        assert p["z_mm"] == pytest.approx(20.05)
        assert 10 <= p["x_mm"] <= 90 and 10 <= p["y_mm"] <= 40


def test_tolerance_above_every_deviation_has_no_regions_and_an_empty_csv(tmp_path, box):
    r = run(scan_file(tmp_path, two_sided_cloud()), box, tolerance="0.06 mm")
    assert r.ok, r.error
    assert r.values["out_of_tolerance_fraction"].quantity == 0.0
    assert r.data["regions"] == []
    assert _csv_rows(r) == []


def test_out_of_tolerance_points_form_one_region_in_the_two_sided_case(tmp_path, box):
    r = run(scan_file(tmp_path, two_sided_cloud()), box, tolerance="0.04 mm")
    (region,) = r.data["regions"]
    assert region["count"] == 200
    assert region["max_abs_deviation_mm"] == pytest.approx(0.05, abs=TOL)
    assert region["centroid_mm"] == pytest.approx([50.0, 25.0, 20.05], abs=1e-3)


def test_standing_warning_names_the_mesh_error_bound(tmp_path, box):
    r = run(scan_file(tmp_path, two_sided_cloud()), box, mesh_deflection="0.02 mm")
    text = " ".join(r.warnings).lower()
    assert "tessellated" in text and "deflection" in text and "float64" in text and "float32" not in text
    assert r.data["mesh"]["deflection_mm"] == pytest.approx(0.02)


def test_a_long_plate_with_every_point_on_its_face_has_no_deviation(tmp_path, models):
    rng = np.random.default_rng(5)
    n = 20_000
    on_face = np.column_stack([rng.uniform(1, 2999, n), rng.uniform(1, 49, n), np.full(n, 20.0)])
    r = run(scan_file(tmp_path, on_face), models["plate"], tolerance="0.001 mm", max_points=50_000)
    assert r.ok, r.error
    assert mm(r, "max_deviation") < 1e-6
    assert mm(r, "rms_deviation") < 1e-6
    assert r.values["out_of_tolerance_fraction"].quantity == 0.0
    assert r.data["regions"] == []


def test_a_long_plate_measures_a_known_offset_exactly(tmp_path, models):
    rng = np.random.default_rng(6)
    n = 20_000
    above = np.column_stack([rng.uniform(1, 2999, n), rng.uniform(1, 49, n), np.full(n, 20.005)])
    r = run(scan_file(tmp_path, above), models["plate"], tolerance="0.001 mm", max_points=50_000)
    assert mm(r, "max_negative_deviation") == pytest.approx(0.005, abs=1e-9)
    assert mm(r, "max_positive_deviation") == pytest.approx(0.005, abs=1e-9)


def test_a_part_far_from_the_origin_is_measured_as_accurately_as_one_at_the_origin(tmp_path, models):
    top = grid(np.linspace(10_010, 10_090, 20), np.linspace(10_010, 10_040, 10), 10_020.0)
    below = top.copy()
    below[:, 2] -= 0.03
    above = top.copy()
    above[:, 2] += 0.05
    r = run(scan_file(tmp_path, np.vstack([above, below])), models["far_box"], tolerance="0.04 mm")
    assert mm(r, "rms_deviation") == pytest.approx(RMS_HAND, abs=TOL)
    assert mm(r, "max_positive_deviation") == pytest.approx(0.05, abs=TOL)
    assert mm(r, "max_negative_deviation") == pytest.approx(-0.03, abs=TOL)


# --------------------------------------------------------------------------- units


@pytest.mark.parametrize(("unit", "factor"), [("m", 0.001), ("inch", 1 / 25.4)])
def test_scan_unit_converts_the_cloud_to_millimetres(tmp_path, box, unit, factor):
    reference = run(scan_file(tmp_path, two_sided_cloud(), "mm.ply"), box)
    scaled = run(scan_file(tmp_path, two_sided_cloud() * factor, f"{unit}.ply"), box, scan_unit=unit)
    assert scaled.ok, scaled.error
    for key in ("rms_deviation", "mean_deviation", "max_deviation", "max_negative_deviation"):
        assert mm(scaled, key) == pytest.approx(mm(reference, key), abs=TOL), key
    assert scaled.values["out_of_tolerance_fraction"].quantity == pytest.approx(0.5)


def test_output_unit_rescales_values_but_not_fractions_or_counts(tmp_path, box):
    r = run(scan_file(tmp_path, two_sided_cloud()), box, output_unit="um")
    assert r.ok, r.error
    assert r.value.unit == "micrometer"
    assert mm(r, "rms_deviation") == pytest.approx(RMS_HAND * 1000, abs=1e-3)
    assert mm(r, "max_negative_deviation") == pytest.approx(-30.0, abs=1e-3)
    assert r.values["out_of_tolerance_fraction"].unit == "dimensionless"
    assert r.values["points_evaluated"].quantity == 400
    assert r.data["tolerance_mm"] == pytest.approx(0.04)


# --------------------------------------------------------------------------- regions and artifact


def _csv_rows(r: ToolResult) -> list[dict[str, str]]:
    (artifact,) = [a for a in r.artifacts if a.path.endswith("deviations.csv")]
    assert artifact.format == "csv"
    text = Path(artifact.path).read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(text))
    assert reader.fieldnames == ["x_mm", "y_mm", "z_mm", "deviation_mm", "region"]
    return list(reader)


def region_cloud():
    block_a = grid(np.arange(10, 20), np.arange(10, 20), 20.2)
    block_b = grid(np.arange(70, 80), np.arange(10, 20), 20.2)
    in_tolerance = grid(np.linspace(30, 60, 10), np.linspace(25, 45, 10), 20.0)
    return np.vstack([block_a, block_b, in_tolerance])


def test_two_separate_bumps_become_two_regions_and_a_csv(tmp_path, box):
    r = run(scan_file(tmp_path, region_cloud()), box, tolerance="0.1 mm")
    assert r.ok, r.error
    assert r.values["out_of_tolerance_fraction"].quantity == pytest.approx(200 / 300)
    regions = sorted(r.data["regions"], key=lambda g: g["centroid_mm"][0])
    assert [g["count"] for g in regions] == [100, 100]
    assert regions[0]["centroid_mm"] == pytest.approx([14.5, 14.5, 20.2], abs=1e-3)
    assert regions[1]["centroid_mm"] == pytest.approx([74.5, 14.5, 20.2], abs=1e-3)
    assert regions[0]["bbox_min_mm"] == pytest.approx([10, 10, 20.2], abs=1e-3)
    assert regions[0]["bbox_max_mm"] == pytest.approx([19, 19, 20.2], abs=1e-3)
    assert regions[1]["bbox_min_mm"] == pytest.approx([70, 10, 20.2], abs=1e-3)
    assert regions[1]["bbox_max_mm"] == pytest.approx([79, 19, 20.2], abs=1e-3)
    for g in regions:
        assert g["max_abs_deviation_mm"] == pytest.approx(0.2, abs=TOL)
    assert {g["id"] for g in regions} == {1, 2}

    rows = _csv_rows(r)
    assert len(rows) == 200
    assert {row["region"] for row in rows} == {"1", "2"}
    assert all(float(row["deviation_mm"]) == pytest.approx(0.2, abs=TOL) for row in rows)
    assert r.data["deviations_csv"]["rows"] == 200
    assert r.data["deviations_csv"]["capped"] is False


def test_regions_are_sorted_by_point_count_descending(tmp_path, box):
    big = grid(np.arange(10, 20), np.arange(10, 20), 20.2)  # 100 points
    small = grid(np.arange(70, 75), np.arange(10, 15), 20.2)  # 25 points
    r = run(scan_file(tmp_path, np.vstack([small, big])), box, tolerance="0.1 mm")
    assert [g["count"] for g in r.data["regions"]] == [100, 25]
    assert [g["id"] for g in r.data["regions"]] == [1, 2]


@pytest.mark.parametrize("copies", [2, 3])
def test_points_recorded_several_times_give_the_same_regions_as_points_recorded_once(tmp_path, box, copies):
    once = run(scan_file(tmp_path, region_cloud(), "once.ply"), box, tolerance="0.1 mm")
    repeated = run(scan_file(tmp_path, np.tile(region_cloud(), (copies, 1)), "many.ply"), box, tolerance="0.1 mm")
    assert once.data["regions_total"] == repeated.data["regions_total"] == 2
    assert repeated.data["regions"] == once.data["regions"]
    assert repeated.data["points_outside_regions"] == 0
    assert repeated.values["out_of_tolerance_fraction"].quantity == pytest.approx(200 / 300)


def test_one_defect_sampled_at_random_is_a_handful_of_regions_at_most(tmp_path, box):
    rng = np.random.default_rng(14)
    patch = np.column_stack([rng.uniform(20, 60, 4000), rng.uniform(10, 40, 4000), np.full(4000, 20.2)])  # 3.3 /mm2
    background = np.column_stack([rng.uniform(65, 95, 3000), rng.uniform(10, 40, 3000), np.full(3000, 20.0)])
    cloud = np.vstack([patch, background])
    once = run(scan_file(tmp_path, cloud, "uniform.ply"), box, tolerance="0.1 mm")
    assert 1 <= once.data["regions_total"] <= 3
    assert once.data["regions"][0]["count"] >= 0.95 * 4000
    twice = run(scan_file(tmp_path, np.vstack([cloud, cloud]), "twice.ply"), box, tolerance="0.1 mm")
    assert twice.data["regions"] == once.data["regions"]


def test_a_region_counts_each_distinct_position_once(tmp_path, box):
    pts = np.vstack([grid(np.arange(10, 20), np.arange(10, 20), 20.2)] * 2)
    r = run(scan_file(tmp_path, pts), box, tolerance="0.1 mm")
    (region,) = r.data["regions"]
    assert region["count"] == 100
    assert r.data["deviations_csv"]["rows"] == 200


def test_the_histogram_edges_never_repeat_even_for_a_perfect_scan(tmp_path, box):
    r = run(scan_file(tmp_path, top_face_grid(20.0)), box)
    edges = r.data["histogram"]["edges"]
    assert r.data["histogram"]["counts"][0] == 200
    assert all(b > a for a, b in zip(edges, edges[1:])), edges


def test_input_file_paths_are_reported_absolute(tmp_path, box, monkeypatch):
    scan_file(tmp_path, two_sided_cloud(), "relative.ply")
    monkeypatch.chdir(tmp_path)
    r = run("relative.ply", box)
    assert r.ok, r.error
    scan_ref, model_ref = r.provenance.input_files
    assert scan_ref.path == str((tmp_path / "relative.ply").resolve())
    assert Path(model_ref.path).is_absolute()


# --------------------------------------------------------------------------- registration


def test_without_registration_a_shifted_scan_keeps_its_offset(tmp_path, box):
    side = three_face_cloud(shift_x=0.3)
    side = side[np.isclose(side[:, 0], 100.3)]
    assert len(side) == 45
    r = run(scan_file(tmp_path, side), box, tolerance="0.1 mm")
    assert mm(r, "mean_deviation") == pytest.approx(0.3, abs=TOL)
    assert mm(r, "rms_deviation") == pytest.approx(0.3, abs=TOL)
    assert r.data["registration"] == {
        "mode": "none",
        "transform": None,
        "fitness": None,
        "inlier_rmse": None,
        "source_points": None,
        "unconstrained": None,
        "note": None,
        "unit": "mm",
    }


def test_icp_recovers_a_known_shift(tmp_path, box):
    r = run(scan_file(tmp_path, three_face_cloud(shift_x=0.3)), box, registration="icp", tolerance="0.1 mm")
    assert r.ok, r.error
    reg = r.data["registration"]
    assert reg["mode"] == "icp"
    transform = np.array(reg["transform"])
    assert transform.shape == (4, 4)
    assert transform[0, 3] == pytest.approx(-0.3, abs=5e-3)
    assert transform[1, 3] == pytest.approx(0.0, abs=5e-3)
    assert transform[2, 3] == pytest.approx(0.0, abs=5e-3)
    assert transform[:3, :3] == pytest.approx(np.eye(3), abs=1e-3)
    assert reg["fitness"] == pytest.approx(1.0, abs=0.01)
    assert reg["inlier_rmse"] < 5e-3
    assert mm(r, "rms_deviation") < 5e-3
    assert r.values["out_of_tolerance_fraction"].quantity == 0.0
    assert any("converge" in w for w in r.warnings)
    assert reg["source_points"] == r.data["points"]["used"] == 339
    assert reg["unconstrained"] == []
    assert "1e-5" in reg["note"] and "between identical runs" in reg["note"]
    assert not any("unconstrained" in w for w in r.warnings)


def test_icp_warns_that_the_best_fit_describes_form_and_not_location(tmp_path, box):
    r = run(scan_file(tmp_path, three_face_cloud(shift_x=0.3)), box, registration="icp", tolerance="0.1 mm")
    text = " ".join(r.warnings)
    assert "form, not location" in text
    assert "absorb" in text and "defect" in text


def test_icp_on_a_single_plane_names_the_directions_it_cannot_fix(tmp_path, box):
    r = run(scan_file(tmp_path, top_face_grid(20.2)), box, registration="icp", tolerance="0.1 mm")
    assert r.ok, r.error
    assert sorted(r.data["registration"]["unconstrained"]) == [
        "rotation about (0.00, 0.00, 1.00)",
        "translation in any direction perpendicular to (0.00, 0.00, 1.00)",
    ]
    (warning,) = [w for w in r.warnings if "unconstrained" in w]
    assert "rotation about (0.00, 0.00, 1.00)" in warning
    assert "perpendicular to (0.00, 0.00, 1.00)" in warning


def _surface(kind: str):
    """Points and unit normals of a plane x + y = 0, a sphere and a cylinder along z."""
    rng = np.random.default_rng(12)
    if kind == "plane":
        s, t = rng.uniform(-40, 40, (2, 800))
        points = np.column_stack([s / math.sqrt(2), -s / math.sqrt(2), t])
        normals = np.tile([1 / math.sqrt(2), 1 / math.sqrt(2), 0.0], (800, 1))
    elif kind == "sphere":
        normals = rng.normal(size=(20_000, 3))
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        points = 30 * normals
    else:
        angle, z = rng.uniform(0, 2 * np.pi, 800), rng.uniform(-40, 40, 800)
        normals = np.column_stack([np.cos(angle), np.sin(angle), np.zeros(800)])
        points = np.column_stack([10 * np.cos(angle), 10 * np.sin(angle), z])
    return points, normals


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("plane", ["rotation about (0.71, 0.71, 0.00)", "translation in any direction perpendicular to (0.71, 0.71, 0.00)"]),
        ("sphere", ["rotation about every axis"]),
        ("cylinder", ["rotation about (0.00, 0.00, 1.00)", "translation along (0.00, 0.00, 1.00)"]),
    ],
)
def test_unconstrained_directions_of_simple_surfaces(kind, expected):
    points, normals = _surface(kind)
    assert sorted(scan_module.unconstrained_directions(points, normals)) == sorted(expected)


def test_unconstrained_directions_are_empty_when_three_normals_fix_the_fit():
    rng = np.random.default_rng(13)
    normals = np.repeat(np.eye(3), 300, axis=0)
    points = rng.uniform(-40, 40, (900, 3))
    assert scan_module.unconstrained_directions(points, normals) == []


def test_icp_fitness_is_the_share_of_points_within_the_search_distance_of_the_surface(tmp_path):
    plate = _write_step(_box(length=1000.0, width=1000.0), tmp_path / "plate_1m.step")
    rng = np.random.default_rng(8)
    on_face = np.column_stack([rng.uniform(10, 990, 20_000), rng.uniform(10, 990, 20_000), np.full(20_000, 20.0)])
    r = run(scan_file(tmp_path, on_face), plate, registration="icp", icp_max_distance="1 mm", tolerance="0.05 mm")
    assert r.ok, r.error
    assert r.data["registration"]["fitness"] == pytest.approx(1.0)
    assert r.data["registration"]["inlier_rmse"] < 1e-6
    assert not any("ICP fitness is" in w for w in r.warnings)


def test_icp_fitness_counts_points_that_stay_farther_than_the_search_distance(tmp_path, box):
    half_far = np.vstack([top_face_grid(20.0)[:100], top_face_grid(40.0)[:100]])
    r = run(scan_file(tmp_path, half_far), box, registration="icp", icp_max_distance="1 mm")
    assert r.data["registration"]["fitness"] == pytest.approx(0.5)


def test_icp_estimates_the_transform_from_a_capped_seeded_subset_and_applies_it_to_every_point(
    tmp_path, box, monkeypatch
):
    monkeypatch.setattr(scan_module, "ICP_MAX_SOURCE_POINTS", 1500)
    rng = np.random.default_rng(9)
    top = np.column_stack([rng.uniform(6, 94, 3000), rng.uniform(6, 44, 3000), np.full(3000, 20.0)])
    side_x = np.column_stack([np.full(1000, 100.0), rng.uniform(6, 44, 1000), rng.uniform(6, 14, 1000)])
    side_y = np.column_stack([rng.uniform(6, 94, 1000), np.full(1000, 50.0), rng.uniform(6, 14, 1000)])
    cloud = np.vstack([top, side_x, side_y]) + [0.3, 0.0, 0.0]
    first = run(scan_file(tmp_path, cloud), box, registration="icp", tolerance="0.1 mm")
    second = run(scan_file(tmp_path, cloud), box, registration="icp", tolerance="0.1 mm")
    assert first.data["points"] == {"original": 5000, "used": 5000}
    assert first.data["registration"]["source_points"] == 1500
    assert first.values["points_evaluated"].quantity == 5000
    assert np.array(first.data["registration"]["transform"])[0, 3] == pytest.approx(-0.3, abs=5e-3)
    assert first.values["out_of_tolerance_fraction"].quantity == 0.0
    assert np.array(first.data["registration"]["transform"]) == pytest.approx(
        np.array(second.data["registration"]["transform"]), abs=1e-4
    )


def test_icp_with_no_overlap_warns_about_low_fitness_and_keeps_the_identity(tmp_path, box):
    far = top_face_grid(50.0)  # 30 mm above the face, search radius 1 mm
    r = run(scan_file(tmp_path, far), box, registration="icp", icp_max_distance="1 mm")
    assert r.ok, r.error
    assert r.data["registration"]["fitness"] == 0.0
    assert np.array(r.data["registration"]["transform"]) == pytest.approx(np.eye(4))
    assert any("fitness" in w for w in r.warnings)


# --------------------------------------------------------------------------- model kinds


def test_open_shell_falls_back_to_unsigned_distance(tmp_path, models):
    pts = grid(np.linspace(10, 90, 10), np.linspace(10, 40, 5), 0.05)
    r = run(scan_file(tmp_path, pts), models["shell"], tolerance="0.01 mm")
    assert r.ok, r.error
    assert r.data["signed"] is False
    assert r.data["mesh"]["watertight"] is False
    assert mm(r, "mean_deviation") == pytest.approx(0.05, abs=TOL)
    assert mm(r, "max_deviation") == pytest.approx(0.05, abs=TOL)
    assert "max_positive_deviation" not in r.values and "max_negative_deviation" not in r.values
    assert r.values["out_of_tolerance_fraction"].quantity == 1.0
    assert any("unsigned" in w for w in r.warnings)


def test_curved_surface_is_measured_within_the_mesh_deflection(tmp_path, models):
    angles = np.linspace(0, 2 * np.pi, 360, endpoint=False)
    ring = np.column_stack([10.05 * np.cos(angles), 10.05 * np.sin(angles), np.full(360, 15.0)])
    r = run(scan_file(tmp_path, ring), models["cylinder"], tolerance="0.1 mm", mesh_deflection="0.001 mm")
    assert r.ok, r.error
    assert r.data["signed"] is True
    assert r.data["mesh"]["watertight"] is True
    assert r.data["mesh"]["deflection_mm"] == pytest.approx(0.001)
    # The mesh is inscribed in the cylinder, so a point 0.05 mm outside the true surface is between
    # 0.05 mm (at a mesh node) and 0.05 mm + the deflection (at the middle of a chord) from the mesh.
    assert mm(r, "max_negative_deviation") >= 0.05 - TOL
    assert mm(r, "max_positive_deviation") <= 0.05 + 0.001
    assert 0.05 <= mm(r, "mean_deviation") <= 0.05 + 0.001


def test_points_above_the_mesh_nodes_of_a_cylinder_are_exactly_0_05_mm_away(tmp_path, models):
    mesh = scan_module.read_model_mesh(models["cylinder"], 0.001)
    nodes = mesh.vertices[np.isclose(mesh.vertices[:, 2], 0.0) & (np.hypot(*mesh.vertices[:, :2].T) > 5.0)]
    assert len(nodes) > 100
    radial = nodes[:, :2] / np.hypot(*nodes[:, :2].T)[:, None]
    ring = np.column_stack([nodes[:, :2] + 0.05 * radial, np.full(len(nodes), 15.0)])
    r = run(scan_file(tmp_path, ring), models["cylinder"], tolerance="0.1 mm", mesh_deflection="0.001 mm")
    assert mm(r, "max_deviation") == pytest.approx(0.05, abs=1e-4)
    assert mm(r, "max_negative_deviation") == pytest.approx(0.05, abs=1e-6)
    assert mm(r, "max_positive_deviation") == pytest.approx(0.05, abs=1e-6)


def test_distance_equals_the_brute_force_distance_to_every_triangle(models):
    mesh = scan_module.read_model_mesh(models["cylinder"], 0.001)
    rng = np.random.default_rng(3)
    angle = rng.uniform(0, 2 * np.pi, 300)
    radius = rng.uniform(9.9, 10.1, 300)
    points = np.column_stack([radius * np.cos(angle), radius * np.sin(angle), rng.uniform(0.5, 29.5, 300)])
    measured = scan_module.measure(points, mesh, signed=True).distance
    corners = mesh.vertices[mesh.triangles]
    exact = np.empty(len(points))
    for i, p in enumerate(points):
        at = np.broadcast_to(p, (len(corners), 3))
        nearest = scan_module.closest_points_on_triangles(at, corners[:, 0], corners[:, 1], corners[:, 2])
        exact[i] = np.linalg.norm(at - nearest, axis=1).min()
    assert np.abs(np.abs(measured) - exact).max() < 1e-9
    assert (measured[radius < 10.0 - 0.002] < 0).all()
    assert (measured[radius > 10.0 + 0.002] > 0).all()


def test_closest_point_on_a_triangle_matches_dense_sampling():
    rng = np.random.default_rng(11)
    a, b, c = rng.normal(size=(3, 40, 3)) * 10
    p = rng.normal(size=(40, 3)) * 12
    exact = np.linalg.norm(p - scan_module.closest_points_on_triangles(p, a, b, c), axis=1)
    u, v = np.meshgrid(np.linspace(0, 1, 301), np.linspace(0, 1, 301))
    keep = (u + v) <= 1
    u, v = u[keep], v[keep]
    for i in range(len(p)):
        surface = a[i] + u[:, None] * (b[i] - a[i]) + v[:, None] * (c[i] - a[i])
        sampled = np.linalg.norm(surface - p[i], axis=1).min()
        assert exact[i] <= sampled + 1e-9
        assert sampled - exact[i] < 0.2, i


def test_a_single_solid_is_counted_and_has_no_model_warnings(tmp_path, box):
    r = run(scan_file(tmp_path, two_sided_cloud()), box)
    assert r.data["mesh"]["solids"] == 1
    assert r.data["mesh"]["step_roots"] == {"in_file": 1, "transferred": 1}
    assert r.data["signed"] is True
    assert not any("solid" in w.lower() or "transferred" in w for w in r.warnings)


def test_two_disjoint_solids_are_measured_unsigned_with_a_precise_warning(tmp_path):
    model = _write_step(_compound(_box(), _box(200.0, 0.0, 0.0)), tmp_path / "two.step")
    pts = np.vstack([grid(np.linspace(10, 90, 5), np.linspace(10, 40, 5), 19.9), grid(np.linspace(210, 290, 5), np.linspace(10, 40, 5), 19.9)])
    r = run(scan_file(tmp_path, pts), model, tolerance="0.05 mm")
    assert r.ok, r.error
    assert r.data["mesh"]["solids"] == 2
    assert r.data["signed"] is False
    assert mm(r, "mean_deviation") == pytest.approx(0.1, abs=TOL)
    assert "max_positive_deviation" not in r.values
    text = " ".join(r.warnings)
    assert "more than one solid: overlapping bodies make inside and outside ambiguous" in text
    assert "open shell" not in text and "not a closed solid" not in text


def test_overlapping_solids_report_the_distance_to_the_nearest_surface_not_a_parity_artefact(tmp_path):
    model = _write_step(_compound(_box(), _box(50.0, 0.0, 0.0)), tmp_path / "overlap.step")
    deep = grid(np.linspace(70, 80, 3), np.linspace(20, 30, 3), 10.0)  # 10 mm from the top and bottom of both bodies
    r = run(scan_file(tmp_path, deep), model, tolerance="0.05 mm")
    assert r.data["signed"] is False
    assert r.data["mesh"]["solids"] == 2
    assert mm(r, "mean_deviation") == pytest.approx(10.0, abs=TOL)
    assert mm(r, "max_deviation") == pytest.approx(10.0, abs=TOL)


def test_a_step_whose_second_root_cannot_be_transferred_warns(tmp_path):
    writer = STEPControl_Writer()
    writer.Transfer(_box(), STEPControl_AsIs)
    writer.Transfer(_box(200.0, 0.0, 0.0), STEPControl_AsIs)
    whole = tmp_path / "two_roots.step"
    assert writer.Write(str(whole)) == IFSelect_RetDone
    text = whole.read_text()
    second = [i for i in range(len(text)) if text.startswith("MANIFOLD_SOLID_BREP", i)][1]
    end = text.index(";", second)
    broken = tmp_path / "two_roots_broken.step"
    broken.write_text(text[:second] + text[second:end].split(",#")[0] + ",#9999999)" + text[end:])
    pts = grid(np.linspace(10, 90, 5), np.linspace(10, 40, 5), 20.0)
    ok = run(scan_file(tmp_path, pts), whole, tolerance="0.05 mm")
    assert ok.data["mesh"]["step_roots"] == {"in_file": 2, "transferred": 2}
    assert not any("transferred" in w for w in ok.warnings)
    r = run(scan_file(tmp_path, pts), broken, tolerance="0.05 mm")
    assert r.ok, r.error
    assert r.data["mesh"]["step_roots"] == {"in_file": 2, "transferred": 1}
    assert any("1 of 2" in w and "transferred" in w for w in r.warnings), r.warnings


# --------------------------------------------------------------------------- downsampling


def test_large_cloud_is_downsampled_reproducibly(tmp_path, box):
    rng = np.random.default_rng(1)
    pts = np.column_stack([rng.uniform(10, 90, 20000), rng.uniform(10, 40, 20000), rng.normal(20.0, 0.02, 20000)])
    scan = scan_file(tmp_path, pts)
    first = run(scan, box, tolerance="0.02 mm", max_points=5000)
    second = run(scan, box, tolerance="0.02 mm", max_points=5000)
    assert first.ok, first.error
    assert first.data["points"] == {"original": 20000, "used": 5000}
    assert first.values["points_evaluated"].quantity == 5000
    assert first.model_dump()["values"] == second.model_dump()["values"]
    assert first.data["worst_points"] == second.data["worst_points"]
    assert first.data["regions"] == second.data["regions"]
    assert any("5000" in w and "20000" in w for w in first.warnings)


# --------------------------------------------------------------------------- over MCP


@pytest.mark.asyncio
async def test_tool_is_callable_through_the_gateway(tmp_path, box):
    from fastmcp import Client

    from eng_toolkit.gateway.server import gateway

    scan = scan_file(tmp_path, two_sided_cloud())
    async with Client(gateway) as client:
        call = await client.call_tool(
            "metrology_check_scan",
            {
                "scan_path": str(scan),
                "scan_unit": "mm",
                "model_path": str(box),
                "tolerance": "0.04 mm",
                "max_points": 2000,
            },
        )
    r = ToolResult.model_validate(call.structured_content)
    assert r.ok, r.error
    assert r.values["out_of_tolerance_fraction"].quantity == pytest.approx(0.5)


# --------------------------------------------------------------------------- errors


def test_engine_output_never_reaches_stdout(tmp_path, box, capfd):
    scan = scan_file(tmp_path, two_sided_cloud())
    bad = tmp_path / "bad.ply"
    bad.write_text("not a ply\n")
    fake_step = tmp_path / "fake.step"
    fake_step.write_text("not a step file\n")
    capfd.readouterr()
    assert run(scan, box).ok
    assert run(bad, box).ok is False
    assert run(scan, fake_step).ok is False
    assert capfd.readouterr().out == ""


def test_non_ascii_paths_are_readable(tmp_path, box):
    folder = tmp_path / "掃描資料"
    folder.mkdir()
    scan = scan_file(folder, two_sided_cloud(), "零件.ply")
    model = folder / "模型.step"
    model.write_bytes(box.read_bytes())
    r = run(scan, model)
    assert r.ok, r.error
    assert r.data["points"]["used"] == 400


def test_non_finite_coordinates_are_invalid(tmp_path, box):
    scan = tmp_path / "nan.xyz"
    scan.write_text("1 2 3\nnan 2 3\n4 5 6\n")
    _fails(run(scan, box), "input.invalid")


def _cut(source: Path, target: Path, share: float = 0.6) -> Path:
    data = source.read_bytes()
    target.write_bytes(data[: int(len(data) * share)])
    return target


def _scan_variants(tmp_path) -> dict[str, Path]:
    pts = grid(np.linspace(10, 90, 20), np.linspace(10, 40, 10), 20.02)
    return {
        "ascii": write_ply(tmp_path / "full_ascii.ply", pts),
        "binary_little": write_binary_ply(tmp_path / "full_little.ply", pts),
        "binary_big": write_binary_ply(tmp_path / "full_big.ply", pts, endian="big"),
        "binary_float": write_binary_ply(tmp_path / "full_float.ply", pts, scalar="float"),
    }


@pytest.mark.parametrize("kind", ["ascii", "binary_little", "binary_big", "binary_float"])
def test_truncated_ply_is_rejected_on_every_attempt(tmp_path, box, kind):
    cut = _cut(_scan_variants(tmp_path)[kind], tmp_path / "cut.ply")
    for attempt in range(5):
        r = _fails(run(cut, box), "input.invalid")
        assert r.error.details["path"] == str(cut), attempt
        assert "truncated" in r.error.message or "fewer" in r.error.message, r.error.message


@pytest.mark.parametrize("kind", ["ascii", "binary_little", "binary_big", "binary_float"])
def test_ply_cut_just_before_its_last_row_is_rejected(tmp_path, box, kind):
    data = _scan_variants(tmp_path)[kind].read_bytes()
    if kind == "ascii":
        without_last_row = data[: data.rstrip().rfind(b"\n") + 1]
    else:
        without_last_row = data[: -{"binary_little": 24, "binary_big": 24, "binary_float": 12}[kind]]
    cut = tmp_path / "almost.ply"
    cut.write_bytes(without_last_row)
    _fails(run(cut, box), "input.invalid")


@pytest.mark.parametrize("kind", ["ascii", "binary_little", "binary_big", "binary_float"])
def test_complete_ply_files_of_every_flavour_are_read(tmp_path, box, kind):
    r = run(_scan_variants(tmp_path)[kind], box, tolerance="0.001 mm")
    assert r.ok, r.error
    assert r.data["points"]["used"] == 200
    assert mm(r, "mean_deviation") == pytest.approx(0.02, abs=1e-5)


def test_ply_with_a_face_element_after_the_vertices_is_read(tmp_path, box):
    pts = grid(np.linspace(10, 90, 4), np.linspace(10, 40, 3), 20.02)
    ascii_path = tmp_path / "faces_ascii.ply"
    body = "\n".join(" ".join(repr(float(v)) for v in row) for row in pts)
    ascii_path.write_text(
        "ply\nformat ascii 1.0\nelement vertex 12\nproperty double x\nproperty double y\nproperty double z\n"
        f"element face 1\nproperty list uchar int vertex_indices\nend_header\n{body}\n3 0 1 2\n",
        encoding="ascii",
    )
    binary_path = write_binary_ply(
        tmp_path / "faces_binary.ply",
        pts,
        extra=b"\x03" + (np.array([0, 1, 2], dtype="<i4")).tobytes(),
        extra_header=("element face 1", "property list uchar int vertex_indices"),
    )
    for path in (ascii_path, binary_path):
        r = run(path, box, tolerance="0.001 mm")
        assert r.ok, r.error
        assert r.data["points"]["used"] == 12


def test_ply_filled_with_garbage_is_rejected_on_every_attempt(tmp_path, box):
    garbage = tmp_path / "garbage.ply"
    header = b"ply\nformat binary_little_endian 1.0\nelement vertex 300\nproperty double x\nproperty double y\nproperty double z\nend_header\n"
    garbage.write_bytes(header + np.random.default_rng(4).bytes(300 * 24))
    for attempt in range(5):
        r = _fails(run(garbage, box), "input.invalid")
        assert r.error.details["path"] == str(garbage), attempt


def test_ply_with_huge_values_is_rejected(tmp_path, box):
    pts = grid(np.linspace(10, 90, 20), np.linspace(10, 40, 10), 20.02)
    pts[7] = [1e30, 0.0, 0.0]
    for path in (write_ply(tmp_path / "huge_ascii.ply", pts), write_binary_ply(tmp_path / "huge_bin.ply", pts)):
        r = _fails(run(path, box), "input.invalid")
        assert r.error.details["path"] == str(path)
        assert "1e7" in r.error.message or "10000000" in r.error.message


def test_ply_with_an_infinite_value_is_rejected(tmp_path, box):
    pts = grid(np.linspace(10, 90, 20), np.linspace(10, 40, 10), 20.02)
    pts[3, 1] = np.inf
    path = write_binary_ply(tmp_path / "inf.ply", pts)
    r = _fails(run(path, box), "input.invalid")
    assert r.error.details["path"] == str(path)


def test_coordinates_beyond_1e7_mm_after_unit_conversion_are_rejected(tmp_path, box):
    for name, text in (("far.xyz", "1 2 3\n4 5 6\n1e12 2 3\n"), ("far.pts", "3\n1 2 3\n4 5 6\n1e12 2 3\n")):
        path = tmp_path / name
        path.write_text(text)
        r = _fails(run(path, box), "input.invalid")
        assert r.error.details["path"] == str(path)
    in_metres = scan_file(tmp_path, grid(np.linspace(1e5, 2e5, 4), np.linspace(1e5, 2e5, 4), 1e5), "km.ply")
    r = _fails(run(in_metres, box, scan_unit="m"), "input.invalid")
    assert r.error.details["path"] == str(in_metres)


def _fails(r: ToolResult, family: str) -> ToolResult:
    assert r.ok is False
    assert r.error.family == family, r.error
    assert r.warnings == []
    assert r.value is None
    return r


def test_missing_scan_file(tmp_path, box):
    missing = tmp_path / "nope.ply"
    r = _fails(run(missing, box), "input.file_not_found")
    assert r.error.details["path"] == str(missing)


def test_missing_model_file(tmp_path):
    scan = scan_file(tmp_path, two_sided_cloud())
    missing = tmp_path / "nope.step"
    r = _fails(run(scan, missing), "input.file_not_found")
    assert r.error.details["path"] == str(missing)


def test_unsupported_scan_extension(tmp_path, box):
    scan = tmp_path / "scan.txt"
    scan.write_text("0 0 0\n")
    r = _fails(run(scan, box), "input.unsupported_format")
    assert ".txt" in r.error.message


def test_unsupported_model_extension(tmp_path):
    scan = scan_file(tmp_path, two_sided_cloud())
    model = tmp_path / "model.stl"
    model.write_text("solid x\nendsolid x\n")
    r = _fails(run(scan, model), "input.unsupported_format")
    assert ".stl" in r.error.message


def test_text_file_named_step_is_not_a_model(tmp_path):
    scan = scan_file(tmp_path, two_sided_cloud())
    fake = tmp_path / "fake.step"
    fake.write_text("this is not a STEP file\n")
    r = _fails(run(scan, fake), "input.invalid")
    assert r.error.details["path"] == str(fake)


def test_step_without_faces_is_invalid(tmp_path, box):
    wire_only = tmp_path / "empty.step"
    wire_only.write_text(
        "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\nFILE_NAME('e','',(''),(''),'','','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
    )
    scan = scan_file(tmp_path, two_sided_cloud())
    _fails(run(scan, wire_only), "input.invalid")


def test_empty_point_cloud_is_invalid(tmp_path, box):
    empty = scan_file(tmp_path, np.empty((0, 3)))
    r = _fails(run(empty, box), "input.invalid")
    assert r.error.details["path"] == str(empty)


def test_garbage_point_cloud_is_invalid(tmp_path, box):
    bad = tmp_path / "bad.ply"
    bad.write_text("not a ply\n")
    _fails(run(bad, box), "input.invalid")


def test_bare_number_tolerance_is_rejected(tmp_path, box):
    _fails(run(scan_file(tmp_path, two_sided_cloud()), box, tolerance="0.05"), "units.bare_number")


@pytest.mark.parametrize("param", ["tolerance", "icp_max_distance", "mesh_deflection"])
def test_non_positive_lengths_are_rejected(tmp_path, box, param):
    _fails(run(scan_file(tmp_path, two_sided_cloud()), box, **{param: "0 mm"}), "input.invalid")


def test_scan_unit_must_be_a_length(tmp_path, box):
    _fails(run(scan_file(tmp_path, two_sided_cloud()), box, scan_unit="kg"), "units.dimension_mismatch")


@pytest.mark.parametrize("unit", ["0 mm", "2 mm", "1 mm", "0.001 m", "mm*2", "2mm", "-1 mm", "1e3 mm", "0.5"])
def test_scan_unit_is_a_unit_and_never_carries_a_magnitude(tmp_path, box, unit):
    r = _fails(run(scan_file(tmp_path, two_sided_cloud()), box, scan_unit=unit), "input.invalid")
    assert "scan_unit" in r.error.message


@pytest.mark.parametrize("unit", ["mm", "m", "inch", "um", " mm "])
def test_scan_unit_accepts_plain_length_units(tmp_path, box, unit):
    r = run(scan_file(tmp_path, top_face_grid(20.0) * 1e-6), box, scan_unit=unit)
    assert r.ok, r.error


def test_unknown_scan_unit_is_a_unit_error(tmp_path, box):
    _fails(run(scan_file(tmp_path, two_sided_cloud()), box, scan_unit="furlongs_of_doom"), "units.parse_error")


def test_unknown_registration_mode(tmp_path, box):
    _fails(run(scan_file(tmp_path, two_sided_cloud()), box, registration="ransac"), "input.invalid")


@pytest.mark.parametrize("max_points", [10, 999, 5_000_001, 6_000_000])
def test_max_points_out_of_range(tmp_path, box, max_points):
    _fails(run(scan_file(tmp_path, two_sided_cloud()), box, max_points=max_points), "input.invalid")


# --------------------------------------------------------------------------- provenance and STEP units


def test_input_file_hashes_are_reported_so_the_run_can_be_reproduced(tmp_path, box):
    import hashlib

    scan_path = scan_file(tmp_path, two_sided_cloud())
    r = run(scan_path, box)
    assert r.ok, r.error
    scan_ref, model_ref = r.provenance.input_files
    assert scan_ref.sha256 == hashlib.sha256(scan_path.read_bytes()).hexdigest()
    assert model_ref.sha256 == hashlib.sha256(Path(box).read_bytes()).hexdigest()
    assert model_ref.bytes == Path(box).stat().st_size


def test_step_written_in_metres_is_read_back_in_millimetres(tmp_path):
    # The same 100 x 50 x 20 mm box stored in a STEP file whose length unit is the metre holds the
    # numbers 0.1, 0.05, 0.02; the reader must return millimetres, so the top face is still at z = 20.
    from OCP.Interface import Interface_Static

    Interface_Static.SetCVal_s("write.step.unit", "M")
    try:
        metre_box = _write_step(BRepPrimAPI_MakeBox(100.0, 50.0, 20.0).Shape(), tmp_path / "box_m.step")
    finally:
        Interface_Static.SetCVal_s("write.step.unit", "MM")
    assert "SI_UNIT($,.METRE.)" in metre_box.read_text(errors="ignore")
    r = run(scan_file(tmp_path, top_face_grid(20.05)), metre_box, tolerance="0.04 mm")
    assert r.ok, r.error
    assert mm(r, "mean_deviation") == pytest.approx(0.05, abs=TOL)
