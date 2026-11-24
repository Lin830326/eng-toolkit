"""The element-count estimate before meshing against what Gmsh really makes (round 3, S3 r2-2: the old ``6 sqrt(2) V / h^3`` was 1.6 times the
real count, so a 2.0 mm run on the S3 L-bracket was refused as needing 76000 elements when Gmsh makes about 47000).

The estimate is ``4.0 V / h^3 + 1.7 A / h^2``, fitted to 43 real meshes (docs/fem/design.md, "Element estimate calibration").  The mesher tests
need OCP and Gmsh (not CalculiX) and are skipped without them; the refusal tests run the arithmetic only.
"""

import pytest

from eng_toolkit import external as X
from eng_toolkit.domains.fem import mesh as M
from eng_toolkit.domains.fem import tools as T
from eng_toolkit.errors import ToolFailure
from eng_toolkit.external_catalog import GMSH


def have_mesher() -> bool:
    try:
        import OCP  # noqa: F401
    except ImportError:
        return False
    return X.locate(GMSH)[0] is not None


needs_mesher = pytest.mark.skipif(not have_mesher(), reason="needs OCP and Gmsh")


def build_shapes(tmp_path):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    from tests import cad_fixtures as cad

    polygon = BRepBuilderAPI_MakePolygon()
    for x, z in ((0, 0), (6, 0), (6, 94), (60, 94), (60, 100), (0, 100)):
        polygon.Add(gp_Pnt(x, 0, z))
    polygon.Close()
    bracket = BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(polygon.Wire()).Face(), gp_Vec(0, 80, 0)).Shape()
    shapes = {
        "bracket": (bracket, (3.0, 2.2)),
        "block": (cad.box(0, 0, 0, 60, 40, 30), (5.0, 3.0)),
        "plate_with_hole": (BRepAlgoAPI_Cut(cad.box(0, 0, 0, 100, 50, 6), cad.cylinder(50, 25, -1, 8.0, 8.0)).Shape(), (3.0, 2.0)),  # 8 mm radius hole
        "cylinder": (cad.cylinder(0, 0, 0, 15.0, 60.0), (5.0, 3.0)),
    }
    return {name: (str(cad.write_step(shape, tmp_path / f"{name}.step")), sizes) for name, (shape, sizes) in shapes.items()}


@needs_mesher
def test_the_estimate_is_within_0_9_and_1_3_of_the_real_count_on_four_shapes_at_two_sizes(tmp_path):
    seen = []
    for name, (path, sizes) in build_shapes(tmp_path).items():
        for size in sizes:
            result = T.mesh(model_path=path, element_size=f"{size} mm")
            assert result.ok, (name, size, result.error)
            real = result.values["element_count"].quantity
            estimate = result.data["estimated_elements"]
            assert real > 500, (name, size, real)  # a calibration point, not the floor Gmsh keeps on tiny meshes
            seen.append((name, size, real, estimate / real))
            assert 0.9 <= estimate / real <= 1.3, (name, size, real, estimate)
    assert len(seen) == 8


@needs_mesher
def test_the_s3_bracket_at_2_0_mm_is_no_longer_refused(tmp_path):
    """Round 2, S3: the L-bracket at 2.0 mm was refused as needing 76000 elements; Gmsh makes about 47000 for it."""
    path, _ = build_shapes(tmp_path)["bracket"]
    result = T.mesh(model_path=path, element_size="2.0 mm")
    assert result.ok, result.error
    real = result.values["element_count"].quantity
    assert real <= M.MAX_ELEMENTS
    assert result.data["estimated_elements"] <= M.MAX_ELEMENTS
    assert 0.9 <= result.data["estimated_elements"] / real <= 1.3


def test_the_refusal_names_the_estimate_the_calibration_and_the_finest_size_that_passes():
    bbox = ((0.0, 0.0, 0.0), (100.0, 80.0, 6.0))
    volume, area = 100.0 * 80.0 * 6.0, 2.0 * (100 * 80 + 100 * 6 + 80 * 6)
    with pytest.raises(ToolFailure) as caught:
        M.check_before_meshing(volume, bbox[0], bbox[1], 1.0, area)
    err = caught.value
    assert err.family == "input.too_large" and err.details["param"] == "/element_size"
    assert err.details["estimated_elements"] > M.MAX_ELEMENTS
    assert err.details["calibration"]["estimate_over_real_min"] == M.CALIBRATION_RATIO_MIN
    assert err.details["calibration"]["estimate_over_real_max"] == M.CALIBRATION_RATIO_MAX
    assert f"{err.details['estimated_elements']:.3g}" in err.message and "estimate / real" in err.message and "finest size that passes" in err.message
    finest = err.details["finest_passing_size_mm"]
    assert M.check_before_meshing(volume, bbox[0], bbox[1], finest, area) <= M.MAX_ELEMENTS  # the size it names passes
    with pytest.raises(ToolFailure):
        M.check_before_meshing(volume, bbox[0], bbox[1], finest * 0.97, area)  # 3 % finer does not


def test_the_cap_is_unchanged():
    assert M.MAX_ELEMENTS == 60_000
    assert M.estimate_elements(1e6, 1.0, 6e4) > M.MAX_ELEMENTS
    assert M.finest_size_within_cap(73920.0, 20_000.0) == pytest.approx(M.finest_size_within_cap(73920.0, 20_000.0, 60_000), rel=1e-12)
