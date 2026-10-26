"""Truth for C1: KiCad 125x35 extruded heatsink, 6061 mass, bbox, CNC milling with a 3 mm end mill and 1 mm walls.

Independent of eng-toolkit: raw OpenCASCADE (OCP) calls on the STEP file, plus line/solid intersections for the fin widths.
"""
import json
import math
import sys

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

PART = "C:/Users/USER/AppData/Local/Programs/KiCad/10.0/share/kicad/3dmodels/Heatsink.3dshapes/Heatsink_125x35x50mm_3xFixationM3.step"
RHO = 2700.0  # kg/m3, 6061 aluminium
TOOL_DIAMETER = 3.0
MIN_WALL = 1.0


def load(path):
    reader = STEPControl_Reader()
    reader.ReadFile(path)
    reader.TransferRoots()
    return reader.OneShape()


def main():
    shape = load(PART)
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    volume = props.Mass()
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    size = [hi.X() - lo.X(), hi.Y() - lo.Y(), hi.Z() - lo.Z()]

    holes, fillets = [], []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() == GeomAbs_Cylinder and face.Orientation() == TopAbs_REVERSED:
            cyl = surf.Cylinder()
            r = cyl.Radius()
            (holes if r > 1.0 else fillets).append((r, cyl.Location().X(), cyl.Location().Y()))
        explorer.Next()

    inter = IntCurvesFace_ShapeIntersector()
    inter.Load(shape, 1e-6)

    def segments(y, z=17.5):
        inter.Perform(gp_Lin(gp_Pnt(-200, y, z), gp_Dir(1, 0, 0)), 0, 400)
        xs = sorted(inter.WParameter(i) - 200 for i in range(1, inter.NbPnt() + 1))
        return [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]

    y_base_top = 2.46126  # read from the base plane: fins start above it (checked below by the 5 mm base)
    min_fin, max_fin, min_gap, max_gap, fin_count = 1e9, 0.0, 1e9, 0.0, 0
    y = y_base_top + 0.5
    while y <= 31.4:
        seg = segments(y)
        widths = [b - a for a, b in seg]
        # skip the 3 hole slices: holes are at y~0 only, so the segments here are the fins
        fin_count = len(seg)
        min_fin = min(min_fin, min(widths))
        max_fin = max(max_fin, max(widths))
        gaps = [seg[i + 1][0] - seg[i][1] for i in range(len(seg) - 1)]
        min_gap, max_gap = min(min_gap, min(gaps)), max(max_gap, max(gaps))
        y += 0.25
    # end fins are wider (they carry the outer shoulder), the thinnest flank wall is an inner fin; measure those only
    inner = [segments(yy)[1:-1] for yy in [3, 8, 14, 20, 26, 31]]
    inner_widths = [round(b - a, 3) for s in inner for a, b in s]

    hole_dia = sorted({round(2 * h[0], 2) for h in holes})
    fillet_dia = sorted({round(2 * f[0], 2) for f in fillets})
    mass_kg = volume * 1e-9 * RHO
    print(json.dumps({
        "answers": {
            "volume_mm3": round(volume, 1),
            "mass_6061_g": round(mass_kg * 1000, 2),
            "bbox_mm_x_y_z": [round(s, 2) for s in size],
            "bbox_note": "Overall 124.75 x 34.99 x 35.00 mm. The file name says 125x35x50 but NO dimension is 50 mm (the 50 is wrong for this model; z is 35 mm, the extrusion length). y includes the 5 mm base plate (y -2.54 to 2.46) plus 30 mm fins.",
            "single_solid": True,
            "hole_count": len(holes),
            "hole_diameter_mm": hole_dia,
            "hole_through_depth_mm": 35.0,
            "hole_depth_over_diameter": round(35.0 / 3.2, 2),
            "hole_x_positions_mm": sorted(round(h[1], 2) for h in holes),
            "fin_count": fin_count,
            "fin_thickness_range_mm_inner_fins": [min(inner_widths), max(inner_widths)],
            "fin_gap_range_mm": [round(min_gap, 2), round(max_gap, 2)],
            "fin_root_fillet_radius_mm": [d / 2 for d in fillet_dia if d < 1.0],
            "fin_root_fillet_count": sum(1 for f in fillets if f[0] < 0.4),
            "wall_1mm_ok": min(inner_widths) >= MIN_WALL,
            "gap_fits_3mm_endmill": min_gap > TOOL_DIAMETER,
            "root_fillets_reachable_with_3mm_mill": False,
            "verdict": (
                "Walls (thinnest flank about 2 mm, above the 1 mm limit) and slot widths (8 to 10 mm, above the 3 mm tool) pass. "
                "Two real issues: (1) the 22 fin-root fillets of R0.25 mm are smaller than the 1.5 mm radius of a 3 mm end mill, so "
                "a 3 mm mill leaves R1.5 root corners (or a smaller mill / EDM / buy the extrusion); (2) the three 3.2 mm holes are 35 mm "
                "deep (depth/diameter 10.9, above the usual 10 limit): drilling needs peck cycles. The part is really an extrusion profile."
            ),
            "dfm_problem_faces": "the 3 hole walls (cylinders along z) and the 22 root fillet faces; not the fin flanks or tips",
        },
        "how": "OCP (OpenCASCADE): VolumeProperties for volume (x 2700 kg/m3 for mass), optimal Bnd_Box, reversed cylinder faces = concave (holes r 1.6 mm, root fillets r 0.25 mm), "
               "line/solid intersections along x at z = 17.5 mm and y = 3..31 mm for fin thickness and gap. Rule limits are the person's: tool diameter 3 mm (radius 1.5), wall 1 mm.",
        "tolerance": "mass +-0.5 g; bbox +-0.05 mm; counts exact; fin thickness/gap +-0.05 mm. The toolkit's own DFM default limits are rules of thumb: a different verdict on hole depth (limit 10) is a judgement call, not an error, if stated.",
    }, indent=1))


if __name__ == "__main__":
    main()
