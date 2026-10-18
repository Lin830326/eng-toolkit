"""eng_toolkit.brep: reading STEP into a Solid and tessellating it, checked against hand values."""

import sys
from pathlib import Path

import numpy as np
import pytest

from eng_toolkit import brep
from eng_toolkit.errors import ToolFailure

try:
    import OCP  # noqa: F401

    HAVE_OCP = True
except (ImportError, OSError):
    HAVE_OCP = False

needs_ocp = pytest.mark.skipif(not HAVE_OCP, reason="the OCP engine is not installed; run: uv sync --extra cad")

BOX_VOLUME_MM3 = 100.0 * 50.0 * 20.0


def _write_step(shapes, path: Path) -> Path:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    writer = STEPControl_Writer()
    for shape in shapes:
        writer.Transfer(shape, STEPControl_AsIs)
    assert writer.Write(str(path)) == IFSelect_RetDone
    return path


def _box(x=0.0, y=0.0, z=0.0, length=100.0, width=50.0, height=20.0):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), length, width, height).Shape()


def _compound(*shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


def signed_volume(mesh: brep.Mesh) -> float:
    corners = mesh.vertices[mesh.triangles]
    return float(np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])).sum() / 6.0)


@pytest.fixture
def box_step(tmp_path):
    return _write_step([_box()], tmp_path / "box.step")


@needs_ocp
def test_a_box_reads_as_one_solid_of_six_faces_from_one_root(box_step):
    solid = brep.read_solid(box_step)
    assert (solid.solids, solid.faces) == (1, 6)
    assert (solid.roots_in_file, solid.roots_transferred) == (1, 1)


@needs_ocp
def test_the_box_mesh_encloses_exactly_its_volume_with_outward_winding(box_step):
    mesh = brep.triangulate(brep.read_solid(box_step), 0.01)
    assert mesh.vertices.dtype == np.float64
    assert mesh.triangles.dtype == np.int32
    assert mesh.deflection_mm == 0.01
    assert signed_volume(mesh) == pytest.approx(BOX_VOLUME_MM3, abs=1e-6)


@needs_ocp
def test_the_mesh_is_not_welded_each_face_keeps_its_own_nodes(box_step):
    mesh = brep.triangulate(brep.read_solid(box_step), 0.01)
    assert len(mesh.vertices) > len(np.unique(np.round(mesh.vertices, 9), axis=0))
    assert mesh.triangles.max() == len(mesh.vertices) - 1


@needs_ocp
def test_a_finer_deflection_keeps_a_cylinders_volume_within_the_chord_bound(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    path = _write_step([BRepPrimAPI_MakeCylinder(10.0, 30.0).Shape()], tmp_path / "cylinder.step")
    exact = np.pi * 10.0**2 * 30.0
    coarse = signed_volume(brep.triangulate(brep.read_solid(path), 0.5))
    fine = signed_volume(brep.triangulate(brep.read_solid(path), 0.001))
    assert 0 < exact - fine < exact - coarse
    assert exact - fine < 0.01 * exact


@needs_ocp
def test_two_disjoint_boxes_count_two_solids_and_twelve_faces(tmp_path):
    path = _write_step([_compound(_box(), _box(200.0, 0.0, 0.0))], tmp_path / "two.step")
    solid = brep.read_solid(path)
    assert (solid.solids, solid.faces) == (2, 12)
    assert signed_volume(brep.triangulate(solid, 0.01)) == pytest.approx(2 * BOX_VOLUME_MM3, abs=1e-6)


@needs_ocp
def test_a_second_root_that_cannot_be_transferred_is_counted_not_transferred(tmp_path):
    whole = _write_step([_box(), _box(200.0, 0.0, 0.0)], tmp_path / "two_roots.step")
    assert (brep.read_solid(whole).roots_in_file, brep.read_solid(whole).roots_transferred) == (2, 2)
    text = whole.read_text()
    second = [i for i in range(len(text)) if text.startswith("MANIFOLD_SOLID_BREP", i)][1]
    end = text.index(";", second)
    broken = tmp_path / "two_roots_broken.step"
    broken.write_text(text[:second] + text[second:end].split(",#")[0] + ",#9999999)" + text[end:])
    solid = brep.read_solid(broken)
    assert (solid.roots_in_file, solid.roots_transferred) == (2, 1)
    assert (solid.solids, solid.faces) == (1, 6)


@needs_ocp
def test_a_text_file_named_step_is_not_a_model(tmp_path):
    fake = tmp_path / "fake.step"
    fake.write_text("this is not a STEP file\n")
    with pytest.raises(ToolFailure) as caught:
        brep.read_solid(fake)
    assert caught.value.family == "input.invalid"
    assert repr(str(fake)) in caught.value.message
    assert caught.value.details == {"path": str(fake)}


@needs_ocp
def test_a_step_without_any_shape_is_invalid(tmp_path):
    empty = tmp_path / "empty.step"
    empty.write_text(
        "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\nFILE_NAME('e','',(''),(''),'','','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
    )
    with pytest.raises(ToolFailure) as caught:
        brep.read_solid(empty)
    assert caught.value.family == "input.invalid"
    assert caught.value.details["path"] == str(empty)


@needs_ocp
def test_the_parameter_name_prefixes_the_message_when_given(tmp_path):
    fake = tmp_path / "fake.step"
    fake.write_text("this is not a STEP file\n")
    with pytest.raises(ToolFailure) as caught:
        brep.read_solid(fake, param="model_path")
    assert caught.value.message.startswith("model_path: ")


def test_a_missing_engine_raises_engine_unavailable_with_the_install_command(tmp_path, monkeypatch):
    path = tmp_path / "box.step"
    path.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n")
    monkeypatch.setitem(sys.modules, "OCP", None)
    with pytest.raises(brep.EngineUnavailable) as caught:
        brep.read_solid(path)
    err = caught.value
    assert isinstance(err, ToolFailure)
    assert err.family == "engine.unavailable"
    assert "uv sync --extra cad" in err.message
    assert err.details["module"] == "OCP"
    assert err.details["dist"] == "cadquery-ocp-novtk"
    assert err.details["install"] == "uv sync --extra cad"


def test_the_public_names_are_the_package_surface():
    assert set(brep.__all__) >= {"EngineUnavailable", "Mesh", "Solid", "read_solid", "triangulate"}
    assert not any(name.startswith("_") for name in brep.__all__)
