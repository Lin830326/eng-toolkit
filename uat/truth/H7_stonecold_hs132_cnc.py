"""Truth for H7: KiCad's Stonecold HS-132 heatsink (32 x 14 mm profile, 28.4 mm long) for CNC milling in 6063 aluminium.

Independent of eng-toolkit: raw OpenCASCADE (OCP) calls on the STEP file. Volume and mass by BRepGProp, bounding box, the round
hole by its cylindrical face, the internal sharp corners by a concavity test of my own (edge between two planar faces, the in-plane
direction into face 2 lying on the material side of face 1), and the thinnest wall by rays shot from every planar face into the material.
"""
import json
import math
import sys

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.collections import IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as EdgeToFaces
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

PART = "C:/Users/USER/AppData/Local/Programs/KiCad/10.0/share/kicad/3dmodels/Heatsink.3dshapes/Heatsink_Stonecold_HS-132_32x14mm_2xFixation1.5mm.step"
RHO = 2700.0           # kg/m3, 6063 aluminium
TOOL_RADIUS = 1.0      # mm: 2 mm end mill
MIN_WALL = 0.8         # mm
ANGLE_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0   # concave edges below this angle between the normals count as tangent, not corners


def load(path):
    reader = STEPControl_Reader()
    reader.ReadFile(path)
    reader.TransferRoots()
    return reader.OneShape()


def plane_normal(face):
    surf = BRepAdaptor_Surface(face)
    n = surf.Plane().Axis().Direction()
    sign = -1.0 if face.Orientation() == TopAbs_REVERSED else 1.0
    return np.array([sign * n.X(), sign * n.Y(), sign * n.Z()])


def surface_normal(face, point):
    """Outward normal of a planar or cylindrical face at a point on it."""
    surf = BRepAdaptor_Surface(face)
    if surf.GetType() == GeomAbs_Plane:
        return plane_normal(face)
    cyl = surf.Cylinder()
    a, o = cyl.Axis().Direction(), cyl.Location()
    axis, origin = np.array([a.X(), a.Y(), a.Z()]), np.array([o.X(), o.Y(), o.Z()])
    radial = point - origin
    radial = radial - np.dot(radial, axis) * axis
    radial /= np.linalg.norm(radial)
    return -radial if face.Orientation() == TopAbs_REVERSED else radial


def face_centre(face):
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    c = props.CentreOfMass()
    return np.array([c.X(), c.Y(), c.Z()])


def main():
    shape = load(PART)
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    volume = props.Mass()
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    size = [hi.X() - lo.X(), hi.Y() - lo.Y(), hi.Z() - lo.Z()]

    holes = []
    faces = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        surf = BRepAdaptor_Surface(face)
        faces.append(face)
        if surf.GetType() == GeomAbs_Cylinder:
            cyl = surf.Cylinder()
            a = cyl.Axis().Direction()
            holes.append({"diameter_mm": round(2 * cyl.Radius(), 3), "axis": [round(a.X(), 2), round(a.Y(), 2), round(a.Z(), 2)],
                          "concave_hole": bool(face.Orientation() == TopAbs_REVERSED)})
        explorer.Next()

    # internal sharp corners: straight edge shared by two planar faces, concave dihedral
    edge_to_faces = EdgeToFaces()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, edge_to_faces)
    corners = {"x": 0, "y": 0, "z": 0}
    corner_angles = {"x": [], "y": [], "z": []}
    for i in range(1, edge_to_faces.Extent() + 1):
        edge = TopoDS.Edge(edge_to_faces.FindKey(i))
        fl = edge_to_faces.FindFromIndex(i)
        fs = [TopoDS.Face(f) for f in fl]
        if len(fs) != 2:
            continue
        curve = BRepAdaptor_Curve(edge)
        if curve.GetType() != GeomAbs_Line:
            continue
        if any(BRepAdaptor_Surface(f).GetType() not in (GeomAbs_Plane, GeomAbs_Cylinder) for f in fs):
            continue
        d = curve.Line().Direction()
        t = np.array([d.X(), d.Y(), d.Z()])
        axis = "xyz"[int(np.argmax(np.abs(t)))] if np.max(np.abs(t)) > 0.999 else None
        if axis is None:
            continue
        pm = curve.Value(0.5 * (curve.FirstParameter() + curve.LastParameter()))
        pm = np.array([pm.X(), pm.Y(), pm.Z()])
        n1, n2 = surface_normal(fs[0], pm), surface_normal(fs[1], pm)
        # direction of the edge as face 1's boundary runs along it (outer boundary counter-clockwise seen from outside)
        sign = 1.0
        walker = TopExp_Explorer(fs[0], TopAbs_EDGE)
        while walker.More():
            if walker.Current().IsSame(edge):
                sign = -1.0 if walker.Current().Orientation() == TopAbs_REVERSED else 1.0
                break
            walker.Next()
        concave = float(np.dot(np.cross(n1, n2), sign * t)) < -1e-9   # cube check: top face (+z) and +x face give (z x x) . (+y) > 0 = convex
        angle = math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(n1, n2))))))   # angle between the outward normals
        if concave and angle > ANGLE_MIN:
            corners[axis] += 1
            corner_angles[axis].append(round(angle, 1))

    # thinnest wall: ray from each planar face centre into the material along the inward normal, to the first exit
    inter = IntCurvesFace_ShapeIntersector()
    inter.Load(shape, 1e-6)
    thin = []
    for face in faces:
        if BRepAdaptor_Surface(face).GetType() != GeomAbs_Plane:
            continue
        n = plane_normal(face)
        c = face_centre(face) - n * 1e-3
        inter.Perform(gp_Lin(gp_Pnt(*c), gp_Dir(*(-n))), 0.0, 100.0)
        ts = sorted(inter.WParameter(k) for k in range(1, inter.NbPnt() + 1) if inter.WParameter(k) > 1e-4)
        if ts:
            thin.append(ts[0])
    smallest_wall = min(thin) if thin else None

    answers = {
        "volume_mm3": round(volume, 1), "mass_g_6063": round(volume * 1e-9 * RHO * 1e3, 2),
        "size_xyz_mm": [round(v, 2) for v in size], "face_count": len(faces),
        "round_faces": holes,
        "internal_sharp_corners_by_edge_direction": corners,
        "corner_dihedral_angles_deg": {k: sorted(set(v)) for k, v in corner_angles.items()},
        "smallest_wall_by_normal_rays_mm": round(smallest_wall, 2) if smallest_wall else None,
        "interpretation": "The profile (32 x 14 mm, extruded along z) has no corner radii: every concave corner of the section is a sharp edge parallel to z (30 of them: 24 turn 45 degrees, 2 turn 90, 2 turn 118, 2 turn 127). With the spindle along z (milling the profile from the end) each of them needs a tool of radius 0 and the 1 mm radius end mill fails on all of them; with the spindle along x or y there is no internal corner that the tool axis sees and no finding. The 3.2 mm round hole (axis y) is 3 mm deep and goes through the base; the two 1.4 mm round faces are the fixation pins (axis z) standing on the base, not holes. The part is cheaper and better extruded, with milling only for the length and holes.",
    }
    print(json.dumps({"answers": answers,
                      "how": "OCP on the STEP file: BRepGProp volume, Bnd_Box, cylinder faces, a concavity test on straight edges between planar faces (grouped by edge direction), inward-normal rays for the thinnest wall",
                      "tolerance": "Volume 5447 mm3 and mass 14.7 g within 0.5 %; size 32.0 x 14.0 x 28.4 mm within 0.05 mm; the count of sharp internal corners along the stated spindle axis within +/-3 of the truth value, and the verdict depends on the axis (z: fails, x or y: no corner issue). The thinnest wall is at least 1.4 mm by any method (the toolkit's sampling says 1.4, rays from face centres say 2.0): above the 0.8 mm limit either way, so walls are not a problem. The assistant must ask or state the spindle axis; a verdict without an axis is incomplete."},
                     indent=1))


if __name__ == "__main__":
    main()
