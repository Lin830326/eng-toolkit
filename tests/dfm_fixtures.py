"""Parts for the geometry_check_dfm tests, built with OCP from primitives whose dimensions are the expected numbers.

Import this only after ``pytest.importorskip("OCP")``.  Every part says which numbers a finding must show.
"""

from pathlib import Path

import cad_fixtures as cad
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Fuse
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS


def fuse(*shapes):
    result = shapes[0]
    for shape in shapes[1:]:
        result = BRepAlgoAPI_Fuse(result, shape).Shape()
    unify = ShapeUpgrade_UnifySameDomain(result, True, True, True)
    unify.Build()
    return unify.Shape()


def cylinder_along(axis, x, y, z, radius, height):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(*axis)), radius, height).Shape()


def vertical_fillets(shape, radius):
    """Fillet every edge of ``shape`` that runs along z."""
    fillets = BRepFilletAPI_MakeFillet(shape)
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    seen = []
    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        curve = BRepAdaptor_Curve(edge)
        a, b = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
        if abs(a.X() - b.X()) < 1e-9 and abs(a.Y() - b.Y()) < 1e-9 and abs(a.Z() - b.Z()) > 1e-9 and not any(edge.IsSame(e) for e in seen):
            seen.append(edge)
            fillets.Add(radius, edge)
        explorer.Next()
    assert len(seen) == 4
    return fillets.Shape()


def wall_block(wall=1.0):
    """Block 60 x 40 x 20 with a sharp pocket x from ``wall`` to 31, y 10 to 30, z 5 to 20 (open on top).

    Thin walls: x = 0 to ``wall`` (gap ``wall``), floor 5 mm, side walls 10 mm, x 31 to 60 is 29 mm.
    Four sharp vertical inside corners, at (wall, 10), (31, 10), (31, 30), (wall, 30).
    """
    return cad.cut(cad.box(length=60, width=40, height=20), cad.box(x=wall, y=10, z=5, length=31 - wall, width=20, height=20))


def pocket_block(corner_radius, *, depth=15.0):
    """Block 60 x 40 x 20 with a 30 x 20 pocket whose four vertical inside corners have radius ``corner_radius`` (0 = sharp)."""
    tool = cad.box(x=10, y=10, z=20 - depth, length=30, width=20, height=depth + 1)
    if corner_radius > 0:
        tool = vertical_fillets(tool, corner_radius)
    return cad.cut(cad.box(length=60, width=40, height=20), tool)


def deep_hole_block(diameter=2.0, depth=30.0):
    """Block 40 x 40 x 40 with a blind vertical hole of ``diameter`` and ``depth`` from the top at (10, 10): depth/diameter = 15."""
    return cad.cut(cad.box(length=40, width=40, height=40), cylinder_along((0, 0, 1), 10, 10, 40 - depth, diameter / 2, depth + 1))


def clean_part():
    """Block 50 x 40 x 20; pocket 30 x 20 x 10 with R4 inside corners; Ø6 hole 12 mm deep at (45, 20); every planar wall
    at least 5 mm, the hole wall 2 mm from the side x = 50 and from the pocket wall x = 40."""
    pocket = vertical_fillets(cad.box(x=10, y=10, z=10, length=30, width=20, height=11), 4.0)
    return cad.cut(cad.box(length=50, width=40, height=20), pocket, cylinder_along((0, 0, 1), 45, 20, 8, 3.0, 13))


def hollow_block():
    """Block 40 x 30 x 20 with a closed 36 x 26 x 16 cavity in it: six 2 mm walls and six faces nothing can reach."""
    return cad.cut(cad.box(length=40, width=30, height=20), cad.box(x=2, y=2, z=2, length=36, width=26, height=16))


def tube(outer=10.0, inner=7.0, length=30.0):
    """A tube: wall = outer - inner radius."""
    return cad.cut(cylinder_along((0, 0, 1), 0, 0, 0, outer, length), cylinder_along((0, 0, 1), 0, 0, -1, inner, length + 2))


def flat_sheet(thickness=2.0, hole_x=6.0):
    """Plate 60 x 30 x ``thickness`` with a Ø5 hole at (``hole_x``, 15): distance to the nearest edge x = 0 is hole_x - 2.5."""
    return cad.cut(cad.box(length=60, width=30, height=thickness), cylinder_along((0, 0, 1), hole_x, 15, -1, 2.5, thickness + 2))


def bent_sheet(thickness=2.0, inner_radius=3.0, width=30.0, flat=40.0, flange=25.0):
    """An L of sheet: flat x 0..flat at z 0..t, a 90 degree bend upward (inner radius r on top, r + t outside), a vertical flange.

    The bend axis runs along y through (flat, ., t + r).
    """
    t, r = thickness, inner_radius
    base = cad.box(length=flat, width=width, height=t)
    ring = cad.cut(cylinder_along((0, 1, 0), flat, 0, t + r, r + t, width), cylinder_along((0, 1, 0), flat, -1, t + r, r, width + 2))
    quarter = BRepAlgoAPI_Common(ring, cad.box(x=flat, y=-1, z=-1, length=r + t + 1, width=width + 2, height=t + r + 1)).Shape()
    wall = cad.box(x=flat + r, y=0, z=t + r, length=t, width=width, height=flange)
    return fuse(base, quarter, wall)


def sheet_with_hole_near_edge(thickness=2.0, distance=3.5):
    """Plate 60 x 30 x t with a Ø5 hole whose wall is ``distance`` from the edge x = 0."""
    return flat_sheet(thickness, hole_x=distance + 2.5)


def stepped_plate():
    """Plate 40 x 40 x 2 with a 20 x 20 boss on top raising it to 3 mm: two thicknesses, 2 and 3."""
    return fuse(cad.box(length=40, width=40, height=2), cad.box(x=10, y=10, z=2, length=20, width=20, height=1))


def hole_grid(columns, rows, pitch=4.0, diameter=2.0):
    """Plate with columns x rows round through holes: columns * rows + 6 faces."""
    plate = cad.box(length=columns * pitch + pitch, width=rows * pitch + pitch, height=5)
    tools = [
        cylinder_along((0, 0, 1), pitch * (c + 1), pitch * (r + 1), -1, diameter / 2, 7)
        for c in range(columns)
        for r in range(rows)
    ]
    return cad.cut(plate, cad.compound(*tools))  # one boolean with all the tools: cutting them one by one is quadratic


def comb(teeth, pitch=3.0, tooth=1.5, height=10.0):
    """A bar with ``teeth`` fins ``tooth`` thick at ``pitch``: 6 + 4 * teeth planar faces; fin thickness = ``tooth`` exactly."""
    bar = cad.box(length=pitch * teeth + pitch, width=20, height=5)
    fins = [cad.box(x=pitch * (i + 0.5) - tooth / 2 + pitch / 2, y=0, z=5, length=tooth, width=20, height=height) for i in range(teeth)]
    return fuse(bar, cad.compound(*fins))


def write_step_inches(shape, path: Path) -> Path:
    """A STEP file whose length unit is the inch (the numbers in the file are in inches)."""
    writer = STEPControl_Writer()  # registers the STEP parameters before setting the unit
    assert Interface_Static.SetCVal_s("write.step.unit", "INCH")
    try:
        writer.Transfer(shape, STEPControl_AsIs)
        assert writer.Write(str(path)) == IFSelect_RetDone
    finally:
        Interface_Static.SetCVal_s("write.step.unit", "MM")
    return path


__all__ = [name for name in dir() if not name.startswith("_")]


HEATSINK_FINS = (-12.0, 0.0, 12.0)  # fin centre lines x
HEATSINK_BOTTOM = -2.54  # base bottom y; the holes at y = 0 of radius 1.6 leave 2.54 - 1.6 = 0.94 mm to it


def heatsink(*, root_fillet=0.25, depth=35.0):
    """A heatsink shaped like KiCad's Heatsink_125x35x50mm_3xFixationM3, extruded ``depth`` along z.

    Base x -18..18, y -2.54..3.  Three tapered fins centred on x = -12, 0, 12: 4 mm wide at the root (y = 3), 2 mm at
    y = 30, closed by a semicircular tip of radius 1 through (x, 31).  The flank leans in by atan(1/27) = 2.12 degrees
    while the tip arc starts vertical, so flank and tip meet at a concave kink of material angle 182.12 degrees (two per
    fin): near-tangent, not a corner.  The six root corners carry ``root_fillet`` fillets (concave, along z).  Three
    diameter-3.2 through holes along z at (x, 0): depth/diameter = 35 / 3.2 = 10.9375, and 0.94 mm of material below each.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.GC import GC_MakeArcOfCircle, GC_MakeSegment
    from OCP.gp import gp_Vec

    def p(x, y):
        return gp_Pnt(x, y, 0.0)

    corners = [(-18.0, HEATSINK_BOTTOM), (18.0, HEATSINK_BOTTOM), (18.0, 3.0)]
    wire = BRepBuilderAPI_MakeWire()
    segments = []
    for a, b in zip(corners, corners[1:]):
        segments.append(GC_MakeSegment(p(*a), p(*b)).Value())
    last = corners[-1]
    roots = []
    for xc in sorted(HEATSINK_FINS, reverse=True):
        right_root, right_tip, left_tip, left_root = (xc + 2.0, 3.0), (xc + 1.0, 30.0), (xc - 1.0, 30.0), (xc - 2.0, 3.0)
        segments.append(GC_MakeSegment(p(*last), p(*right_root)).Value())
        segments.append(GC_MakeSegment(p(*right_root), p(*right_tip)).Value())
        segments.append(GC_MakeArcOfCircle(p(*right_tip), p(xc, 31.0), p(*left_tip)).Value())
        segments.append(GC_MakeSegment(p(*left_tip), p(*left_root)).Value())
        last = left_root
        roots += [right_root, left_root]
    segments.append(GC_MakeSegment(p(*last), p(-18.0, 3.0)).Value())
    segments.append(GC_MakeSegment(p(-18.0, 3.0), p(*corners[0])).Value())
    for curve in segments:
        wire.Add(BRepBuilderAPI_MakeEdge(curve).Edge())
    face = BRepBuilderAPI_MakeFace(wire.Wire()).Face()
    body = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, depth)).Shape()
    if root_fillet > 0:
        fillets = BRepFilletAPI_MakeFillet(body)
        explorer = TopExp_Explorer(body, TopAbs_EDGE)
        seen = []
        while explorer.More():
            edge = TopoDS.Edge(explorer.Current())
            explorer.Next()
            curve = BRepAdaptor_Curve(edge)
            a = curve.Value(curve.FirstParameter())
            b = curve.Value(curve.LastParameter())
            vertical = abs(a.X() - b.X()) < 1e-9 and abs(a.Y() - b.Y()) < 1e-9 and abs(a.Z() - b.Z()) > 1e-9
            if vertical and any(abs(a.X() - x) < 1e-9 and abs(a.Y() - y) < 1e-9 for x, y in roots) and not any(edge.IsSame(e) for e in seen):
                seen.append(edge)
                fillets.Add(root_fillet, edge)
        assert len(seen) == 2 * len(HEATSINK_FINS)
        body = fillets.Shape()
    holes = [cylinder_along((0, 0, 1), xc, 0.0, -1.0, 1.6, depth + 2.0) for xc in HEATSINK_FINS]
    return cad.cut(body, cad.compound(*holes))


def sharp_l_sheet(thickness=2.0, flat=40.0, width=30.0, flange=25.0):
    """An L of sheet with no bend radius: flat x 0..flat at z 0..t, a flange x flat-t..flat rising to t + flange.

    The inside corner at x = flat - t, z = t is a sharp concave edge between two faces of the sheet.
    """
    return fuse(
        cad.box(length=flat, width=width, height=thickness),
        cad.box(x=flat - thickness, y=0, z=thickness, length=thickness, width=width, height=flange),
    )
