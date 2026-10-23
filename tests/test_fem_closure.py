"""fem_mesh and fem_static refuse a file that is not made of closed solids only, before any program starts.

An open 5-face box saved as a solid still integrates to a volume (80000 mm3) and reached Gmsh as an ``engine.failure``;
a box beside a loose 10 x 10 face meshed and then reported a false volume difference.  Both are now ``input.invalid`` at
``/model_path`` with the free edges / loose face ids.
"""

import asyncio
import os

import pytest

OCP = pytest.importorskip("OCP")

import cad_fixtures as cad  # noqa: E402
from fastmcp import Client  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing  # noqa: E402
from OCP.gp import gp_Pln  # noqa: E402
from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402
from OCP.TopoDS import TopoDS  # noqa: E402

from eng_toolkit.gateway.server import gateway  # noqa: E402


def _open_box_solid():
    """A SOLID built from the 5 faces of a box (one face missing): an open shell that still has a volume."""
    sewing = BRepBuilderAPI_Sewing(1e-6)
    explorer = TopExp_Explorer(cad.box(), TopAbs_FACE)
    k = 0
    while explorer.More():
        if k != 5:
            sewing.Add(explorer.Current())
        k += 1
        explorer.Next()
    sewing.Perform()
    sewn = sewing.SewedShape()
    assert sewn.ShapeType() == TopAbs_SHELL
    return BRepBuilderAPI_MakeSolid(TopoDS.Shell(sewn)).Solid()


def _box_and_loose_face():
    face = BRepBuilderAPI_MakeFace(gp_Pln(), 0.0, 10.0, 0.0, 10.0).Face()
    return cad.compound(cad.box(x=200.0), face)


def _call(tool, **kwargs):
    async def run():
        async with Client(gateway) as client:
            return (await client.call_tool(tool, kwargs, raise_on_error=False)).structured_content

    return asyncio.run(run())


@pytest.fixture()
def models(tmp_path, monkeypatch):
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(tmp_path))
    return {
        "open_shell": cad.write_brep(_open_box_solid(), tmp_path / "open_shell.brep"),
        "loose_face": cad.write_step(_box_and_loose_face(), tmp_path / "loose_face.step"),
    }


@pytest.mark.parametrize("tool,extra", [
    ("fem_mesh", {}),
    ("fem_static", dict(youngs_modulus="210 GPa", poisson_ratio=0.3, supports="x = 0 mm", loads="force (0 N, 0 N, -1 kN) on x = 100 mm")),
])
@pytest.mark.parametrize("which,field", [("open_shell", "free_edges"), ("loose_face", "faces_outside_solids")])
def test_not_closed_solids_are_refused_before_meshing(models, tool, extra, which, field):
    result = _call(tool, model_path=str(models[which]), element_size="10 mm", **extra)
    assert result["ok"] is False
    assert result["error"]["family"] == "input.invalid"
    details = result["error"]["details"]
    assert details["param"] == "/model_path"
    assert details[field] > 0
    assert "closed" in result["error"]["message"]
    assert "Traceback" not in str(result["error"])


def test_the_loose_face_is_named(models):
    result = _call("fem_mesh", model_path=str(models["loose_face"]), element_size="10 mm")
    assert result["error"]["details"]["loose_face_ids"]  # F<n> numbering of geometry_measure


@pytest.mark.skipif(os.environ.get("ENG_TOOLKIT_SKIP_REAL") == "1", reason="opt-out")
def test_a_closed_box_is_still_accepted(tmp_path, monkeypatch):
    from eng_toolkit import external as X
    from eng_toolkit.external_catalog import GMSH

    if X.locate(GMSH)[0] is None:
        pytest.skip("needs Gmsh")
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(tmp_path))
    path = cad.write_step(cad.box(length=20.0, width=10.0, height=10.0), tmp_path / "box.step")
    result = _call("fem_mesh", model_path=str(path), element_size="5 mm")
    assert result["ok"] is True, result.get("error")
