"""Regression tests for the six issues of the independent review of geometry_view (docs/geometry-view/design.md, "Review fixes").

1. memory and time are bounded before and during meshing (pre-mesh triangle estimate, 600,000 cap, budget between faces);
2. the DFM legend speaks of severity only, exactness is per finding;
3. a finding that names fewer faces than it concerns is disclosed;
4. an overflowing limit is blamed on its own pointer;
5. a finding without faces (probe part as sheet metal) is listed as not coloured;
6. only closed solids are accepted (open shells and loose faces are refused), and an empty colour group writes no primitive.
Engine tests skip without OCP.
"""

import json
import time
from pathlib import Path

import numpy as np
import pytest
from test_geometry_view import artifact, checked, refused, run
from view_readers import groups_of_faces, read_view_glb

from eng_toolkit import brep
from eng_toolkit.domains.geometry import convert as conversion
from eng_toolkit.domains.geometry import dfm
from eng_toolkit.domains.geometry import view as viewer
from eng_toolkit.domains.geometry.tools import convert

NEED_OCP = "OCP is not installed; run: uv sync --extra cad"


@pytest.fixture(scope="module")
def ocp():
    return pytest.importorskip("OCP", reason=NEED_OCP)


@pytest.fixture()
def cad(ocp):
    import cad_fixtures

    return cad_fixtures


@pytest.fixture()
def fx(ocp):
    import dfm_fixtures

    return dfm_fixtures


def sphere(radius):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere

    return BRepPrimAPI_MakeSphere(radius).Shape()


def torus(major, minor):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeTorus

    return BRepPrimAPI_MakeTorus(major, minor).Shape()


def cone(r1, r2, height):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone

    return BRepPrimAPI_MakeCone(r1, r2, height).Shape()


def loft():
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Elips, gp_Pnt

    sections = BRepOffsetAPI_ThruSections(True, False)
    sections.AddWire(BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 20)).Edge()).Wire())
    sections.AddWire(BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(gp_Elips(gp_Ax2(gp_Pnt(0, 0, 40), gp_Dir(0, 0, 1)), 30, 10)).Edge()).Wire())
    sections.Build()
    return sections.Shape()


def as_solid(shape):
    return brep.Solid(_handle=shape, solids=1, faces=1)


# --------------------------------------------------------------------------- 1. memory and time


@pytest.mark.parametrize(
    ("label", "make", "deflection"),
    [
        ("sphere r10", lambda cad, fx: sphere(10.0), 0.01),
        ("sphere r60", lambda cad, fx: sphere(60.0), 0.1),
        ("torus 50/5", lambda cad, fx: torus(50.0, 5.0), 0.01),
        ("torus 20/15", lambda cad, fx: torus(20.0, 15.0), 0.1),
        ("cone", lambda cad, fx: cone(20.0, 5.0, 30.0), 0.01),
        ("cylinder", lambda cad, fx: cad.cylinder(0, 0, 0, 50.0, 10.0), 0.001),
        ("loft (B-spline)", lambda cad, fx: loft(), 0.01),
        ("probe part", lambda cad, fx: cad.probe_part(), 0.01),
        ("hole grid", lambda cad, fx: fx.hole_grid(10, 10), 0.001),
        ("comb", lambda cad, fx: fx.comb(20), 0.01),
    ],
)
def test_the_estimate_is_within_a_factor_two_of_the_mesh(cad, fx, label, make, deflection):
    solid = as_solid(make(cad, fx))
    estimate = brep.estimate_triangles(solid, deflection)
    actual = len(brep.tessellate_faces(solid, deflection, with_edges=False).triangles)
    assert actual <= 2.0 * estimate.total, (label, actual, estimate.total)  # never under-estimates by more than 2x
    assert estimate.total <= 3.0 * actual, (label, actual, estimate.total)  # and does not refuse wildly early
    assert len(estimate.per_face) == len(brep.tessellate_faces(solid, deflection, with_edges=False).triangle_ranges)


@pytest.mark.parametrize("radius", [60.0, 300.0])
def test_a_fine_sphere_is_refused_before_meshing(cad, tmp_path, radius):
    """Before: r = 60 mm at 0.001 mm meshed 603,802 triangles (4.1 GB, 26 s); r = 300 mm ran out of memory and time."""
    source = cad.write_step(sphere(radius), tmp_path / "sphere.step")
    started = time.perf_counter()
    r = refused(run(source, mesh_deflection="0.001 mm", max_seconds=5), "input.out_of_range", "/mesh_deflection")
    assert time.perf_counter() - started < 5.0
    details = r.error.details
    assert details["estimated"] is True and details["limit"] == 600_000 and details["triangles"] > 600_000
    assert "about 4 GB" in r.error.message and "coarser" in r.error.message
    suggested = details["suggested_mesh_deflection_mm"]
    assert brep.estimate_triangles(brep.read_solid(source), suggested).total <= 600_000  # the suggested deflection fits
    # (meshing it would take ~20 s for ~500,000 triangles; the coarser 0.01 mm shows the call works on this file)
    assert run(source, mesh_deflection="0.01 mm" if radius == 60.0 else "0.1 mm").ok


def test_the_cap_is_checked_after_every_face(fx, cad, tmp_path, monkeypatch):
    source = cad.write_step(fx.hole_grid(10, 10), tmp_path / "grid.step")
    monkeypatch.setattr(brep, "estimate_triangles", lambda solid, d, deadline=None: brep.TriangleEstimate(total=1, per_face=()))
    monkeypatch.setattr(viewer, "MAX_TRIANGLES", 500)
    seen = []
    original = brep.tessellate_faces

    def counting(*args, **kwargs):
        budget = kwargs["after_face"]
        kwargs["after_face"] = lambda n: (seen.append(n), budget(n))
        return original(*args, **kwargs)

    monkeypatch.setattr(brep, "tessellate_faces", counting)
    r = refused(run(source), "input.too_large", "/mesh_deflection")
    assert r.error.details["estimated"] is False and "reached" in r.error.message
    assert seen[-1] > 500 and all(n <= 500 for n in seen[:-1])  # stopped at the first face over the cap
    assert len(seen) < 106  # long before the last of the 106 faces


def test_the_time_budget_is_checked_before_every_face(fx, cad, tmp_path, monkeypatch):
    source = cad.write_step(fx.hole_grid(10, 10), tmp_path / "grid.step")
    original = dfm.Deadline
    mesh_checks = []

    def counting(seconds):
        budget = original(seconds)
        expired = original(seconds, started=budget.started - 10_000)

        def check(phase):
            if phase == "mesh":
                mesh_checks.append(phase)
                if len(mesh_checks) == 3:  # expire while the third face is next
                    expired(phase)
            budget(phase)

        return check

    monkeypatch.setattr(dfm, "Deadline", counting)
    r = refused(run(source, max_seconds=5), "geometry.time_budget_exceeded", "/max_seconds")
    assert r.error.details["phase"] == "mesh" and len(mesh_checks) == 3
    monkeypatch.setattr(dfm, "Deadline", lambda seconds: (lambda phase: mesh_checks.append(phase)))
    mesh_checks.clear()
    assert run(source).ok
    assert mesh_checks.count("mesh") >= 106  # once per face (106 faces), plus the step boundaries


def test_geometry_convert_refuses_a_fine_sphere_before_meshing(cad, tmp_path):
    source = cad.write_step(sphere(60.0), tmp_path / "sphere.step")
    started = time.perf_counter()
    r = getattr(convert, "fn", convert)(input_path=str(source), output_format="glb", mesh_deflection="0.001 mm")
    assert r.ok is False and r.error.family == "input.out_of_range" and r.error.details["estimated"] is True
    assert r.error.details["param"] == "mesh_deflection"  # geometry_convert's own convention: bare parameter names
    assert time.perf_counter() - started < 5.0
    assert conversion.MAX_TESSELLATION_TRIANGLES == 600_000


# --------------------------------------------------------------------------- 2. severity, not exactness, in the legend


def test_the_dfm_legend_speaks_of_severity_and_each_finding_of_its_check(fx, cad, tmp_path):
    source = cad.write_step(fx.stepped_plate(), tmp_path / "stepped.step")
    r = run(source, color_by="dfm", process="sheet_metal")
    assert r.ok, r.error
    legend = {row["group"]: row["meaning"] for row in r.data["legend"]}
    for group in ("dfm_error", "dfm_warn", "dfm_info"):
        assert "exact rule" not in legend[group] and "heuristic rule" not in legend[group]
        assert group.split("_")[1] in legend[group]
    rows = {row["rule"]: row for row in r.data["findings_coloured"]}
    uniform = rows["sheet.thickness_uniform"]
    assert uniform["severity"] == "error" and uniform["check"] == "heuristic"
    table = json.loads(artifact(r, "view.json").read_text(encoding="utf-8"))
    assert any(f["rule"] == "sheet.thickness_uniform" and f["check"] == "heuristic" for f in table["findings"])
    groups = groups_of_faces(read_view_glb(artifact(r, "view.glb")))
    assert all(groups[f"F{i}"] == "dfm_error" for i in uniform["face_ids"])  # still red: colours follow severity


# --------------------------------------------------------------------------- 3. faces beyond a finding's list


def test_a_finding_that_names_only_part_of_its_faces_is_disclosed(fx, cad, tmp_path):
    plate = cad.box(length=200, width=200, height=2)
    bosses = [cad.box(x=10 + 22 * i, y=10 + 22 * j, z=2, length=3, width=3, height=1) for i in range(8) for j in range(8)]
    source = cad.write_step(fx.fuse(plate, cad.compound(*bosses)), tmp_path / "bosses.step")
    r = run(source, color_by="dfm", process="sheet_metal")
    assert r.ok, r.error
    (partial,) = r.data["findings_partly_coloured"]
    assert partial["rule"] == "sheet.thickness_uniform"
    assert partial["faces_total"] == 320 and partial["faces_named"] == 50 and partial["faces_not_coloured"] == 270
    assert any("names only 50" in w and "270" in w for w in r.warnings)
    table = json.loads(artifact(r, "view.json").read_text(encoding="utf-8"))
    row = next(f for f in table["findings"] if f["rule"] == "sheet.thickness_uniform")
    assert (row["faces_named"], row["faces_total"], row["faces_not_coloured"]) == (50, 320, 270)
    groups = groups_of_faces(read_view_glb(artifact(r, "view.glb")))
    assert sum(1 for g in groups.values() if g == "dfm_error") == 50  # geometry_check_dfm names only these 50


# --------------------------------------------------------------------------- 4. overflowing limits


NINES = "9" * 400


@pytest.mark.parametrize("color_by", ["dfm", "face_kind"])
@pytest.mark.parametrize(
    ("limits", "pointer"),
    [
        (f'{{"min_wall": "{NINES} mm"}}', "/limits/min_wall"),
        (f'{{"max_hole_depth_ratio": {NINES}}}', "/limits/max_hole_depth_ratio"),
        (f'{{"max_hole_depth_ratio": "{NINES}"}}', "/limits/max_hole_depth_ratio"),
    ],
)
def test_an_overflowing_limit_names_its_own_pointer(cad, tmp_path, color_by, limits, pointer):
    source = cad.write_step(cad.box(), tmp_path / "box.step")
    r = run(source, color_by=color_by, limits=limits)
    assert r.ok is False and r.error.details["param"] == pointer, r.error
    assert r.error.family in ("input.out_of_range", "input.invalid", "units.parse_error")


# --------------------------------------------------------------------------- 5. a finding without faces


def test_a_finding_without_faces_is_listed_as_not_coloured(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    r = run(source, color_by="dfm", process="sheet_metal")
    assert r.ok, r.error
    faceless = [row for row in r.data["findings_not_coloured"] if row["rule"] == "sheet.thickness"]
    assert faceless and faceless[0]["severity"] == "error" and faceless[0]["face_ids"] == [] and faceless[0]["coloured"] is False
    assert any("name no face" in w for w in r.warnings)
    table = json.loads(artifact(r, "view.json").read_text(encoding="utf-8"))
    assert any(f["rule"] == "sheet.thickness" and f["coloured"] is False for f in table["findings"])


# --------------------------------------------------------------------------- 6. closed solids only, empty groups


def test_a_solid_with_a_loose_face_beside_it_is_refused(cad, tmp_path):
    source = cad.write_step(cad.compound(cad.box(), cad.planar_shell()), tmp_path / "loose.step")
    r = refused(run(source), "geometry.not_a_solid", "/model_path")
    assert r.error.details["faces_outside_solids"] == 1 and r.error.details["open_shells"] == 0


def test_a_solid_made_of_an_open_shell_is_refused(cad, tmp_path):
    from OCP.BRep import BRep_Builder
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Shell, TopoDS_Solid

    builder = BRep_Builder()
    shell = TopoDS_Shell()
    builder.MakeShell(shell)
    explorer = TopExp_Explorer(cad.box(), TopAbs_FACE)
    for _ in range(5):  # five of the six faces of the box: open at the top
        builder.Add(shell, explorer.Current())
        explorer.Next()
    body = TopoDS_Solid()
    builder.MakeSolid(body)
    builder.Add(body, shell)
    source = cad.write_brep(body, tmp_path / "open.brep")
    r = refused(run(source), "geometry.not_a_solid", "/model_path")
    assert r.error.details["open_shells"] == 1 and r.error.details["free_edges"] == 4


def test_closed_solids_pass_the_closure_check(cad, fx, tmp_path):
    for shape in (cad.box(), cad.probe_part(), fx.hollow_block(), sphere(5.0)):
        found = brep.closure(as_solid(shape))
        assert found.open_shells == 0 and found.free_edges == 0 and found.faces_outside_solids == 0 and found.solids == 1


def test_a_colour_group_without_triangles_writes_no_primitive(cad, tmp_path, monkeypatch):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    original = brep.tessellate_faces

    def without_first_face(*args, **kwargs):
        fm = original(*args, **kwargs)
        first, count = fm.triangle_ranges[0]
        keep = np.ones(len(fm.triangles), dtype=bool)
        keep[first : first + count] = False
        ranges = [(0, 0)] + [(t0 - count, n) for t0, n in fm.triangle_ranges[1:]]
        return brep.FaceMesh(
            vertices=fm.vertices,
            triangles=fm.triangles[keep],
            vertex_ranges=fm.vertex_ranges,
            triangle_ranges=tuple(ranges),
            edges=fm.edges,
            seam_edges=fm.seam_edges,
            degenerate_edges=fm.degenerate_edges,
            edges_without_polygon=fm.edges_without_polygon,
            deflection_mm=fm.deflection_mm,
        )

    monkeypatch.setattr(brep, "tessellate_faces", without_first_face)
    r = run(source, color_by="highlight", highlight="F1")
    assert r.ok, r.error
    parsed = read_view_glb(artifact(r, "view.glb"))
    assert [p["extras"]["group"] for p in parsed["primitives"] if p["mode"] == 4] == ["neutral"]
    assert all(a["count"] > 0 for a in parsed["document"]["accessors"])
    assert all(v["byteLength"] > 0 for v in parsed["document"]["bufferViews"])
    assert any("['highlight']" in w for w in r.warnings) and any("F1" in w for w in r.warnings)
    table = json.loads(Path(artifact(r, "view.json")).read_text(encoding="utf-8"))
    assert table["faces"][0]["triangle_count"] == 0 and table["faces"][0]["group"] == "highlight"
