"""CAD files for the geometry tests, built with OCP.  Import this only after ``pytest.importorskip("OCP")``."""

import math
from pathlib import Path

from OCP.BRep import BRep_Builder
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_Line
from OCP.gp import gp_Ax2, gp_Dir, gp_Pln, gp_Pnt
from OCP.IFSelect import IFSelect_RetDone
from OCP.IGESControl import IGESControl_Writer
from OCP.ShapeUpgrade import ShapeUpgrade_ShapeDivideClosed
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound


def box(x=0.0, y=0.0, z=0.0, length=100.0, width=50.0, height=20.0):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), length, width, height).Shape()


def cylinder(x, y, z, radius, height):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, 1)), radius, height).Shape()


def compound(*shapes):
    builder = BRep_Builder()
    result = TopoDS_Compound()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def planar_shell():
    """One planar face 100 x 50 mm: no solid, so no volume."""
    return BRepBuilderAPI_MakeFace(gp_Pln(), 0.0, 100.0, 0.0, 50.0).Face()


def probe_part():
    """100 x 50 x 20 box, a vertical diameter-10 hole through it at (25, 25), R2 fillets on the 4 outer vertical edges."""
    drilled = BRepAlgoAPI_Cut(box(), cylinder(25, 25, -1, 5.0, 22.0)).Shape()
    fillets = BRepFilletAPI_MakeFillet(drilled)
    seen = []
    explorer = TopExp_Explorer(drilled, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        curve = BRepAdaptor_Curve(edge)
        start = curve.Value(curve.FirstParameter())
        on_corner = start.X() in (0.0, 100.0) and start.Y() in (0.0, 50.0)
        vertical = abs(abs(curve.Line().Direction().Z()) - 1.0) < 1e-9 if curve.GetType() == GeomAbs_Line else False
        if vertical and on_corner and not any(edge.IsSame(other) for other in seen):
            seen.append(edge)
            fillets.Add(2.0, edge)
        explorer.Next()
    assert len(seen) == 4
    return fillets.Shape()


def blind_part():
    """The box with a vertical diameter-6 hole 8 mm deep at (50, 25), drilled from the top face."""
    return BRepAlgoAPI_Cut(box(), cylinder(50, 25, 12, 3.0, 9.0)).Shape()


def cut(base, *tools):
    for tool in tools:
        base = BRepAlgoAPI_Cut(base, tool).Shape()
    return base


def side_hole_part():
    """The box with a diameter-8 hole through it along x at (y, z) = (25, 10)."""
    tool = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(-1, 25, 10), gp_Dir(1, 0, 0)), 4.0, 102.0).Shape()
    return cut(box(), tool)


def drill_point_part():
    """A diameter-6 hole 8 mm deep from the top at (50, 25), ending in a 118 degree drill-point cone."""
    cone = BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(50, 25, 12), gp_Dir(0, 0, -1)), 3.0, 0.0, 3.0 / math.tan(math.radians(59))).Shape()
    return cut(box(), cylinder(50, 25, 12, 3.0, 9.0), cone)


def groove_part():
    """The box with an annular groove 5 mm deep between radius 10 and 15 around (50, 25) on the top face."""
    ring = cut(cylinder(50, 25, 15, 15.0, 6.0), cylinder(50, 25, 14, 10.0, 8.0))
    return cut(box(), ring)


def counterbore_part():
    """A diameter-6 hole through the box with a diameter-12 counterbore 5 mm deep at (50, 25)."""
    return cut(box(), cylinder(50, 25, -1, 3.0, 22.0), cylinder(50, 25, 15, 6.0, 6.0))


def split_faces(shape):
    """The same shape with every closed face (a cylinder wall) divided into two half faces."""
    divider = ShapeUpgrade_ShapeDivideClosed(shape)
    divider.SetNbSplitPoints(1)
    assert divider.Perform()
    return divider.Result()


def write_step(shape, path: Path) -> Path:
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    assert writer.Write(str(path)) == IFSelect_RetDone
    return path


def write_iges(shape, path: Path) -> Path:
    writer = IGESControl_Writer("MM", 1)  # mode 1: a manifold solid B-rep with real cylinders; mode 0 writes loose faces
    writer.AddShape(shape)
    writer.ComputeModel()
    assert writer.Write(str(path))
    return path


def write_brep(shape, path: Path) -> Path:
    assert BRepTools.Write_s(shape, str(path))
    return path
