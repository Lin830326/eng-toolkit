"""Truth for C2: A3 dimensioned drawing and hole list of the KiCad Raspberry Pi Pico STEP model.

Independent of eng-toolkit: raw OpenCASCADE (OCP) on the STEP file; compared with the Raspberry Pi Pico datasheet
(board 21 x 51 mm, four 2.1 mm mounting holes, 40 through-hole pins on 2.54 mm pitch, 17.78 mm between the two rows).
"""
import collections
import json

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

PART = "C:/Users/USER/AppData/Local/Programs/KiCad/10.0/share/kicad/3dmodels/Module.3dshapes/RaspberryPi_Pico.step"


def main():
    reader = STEPControl_Reader()
    reader.ReadFile(PART)
    reader.TransferRoots()
    shape = reader.OneShape()
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    overall = [round(hi.X() - lo.X(), 2), round(hi.Y() - lo.Y(), 2), round(hi.Z() - lo.Z(), 2)]

    centres = collections.defaultdict(set)  # diameter -> {(x, y)}, full-circle holes only
    half_holes = 0  # castellated half-holes on the board edge: not holes for a drawing callout
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() == GeomAbs_Cylinder and face.Orientation() == TopAbs_REVERSED:
            cyl = surf.Cylinder()
            axis = cyl.Axis().Direction()
            if abs(axis.Z()) > 0.99:  # holes through the 1 mm board (axis along z)
                d = round(2 * cyl.Radius(), 3)
                if d in (1.0, 2.1):
                    if surf.LastUParameter() - surf.FirstUParameter() > 6.0:  # full 2 pi turn = a real hole
                        centres[d].add((round(cyl.Location().X(), 2), round(cyl.Location().Y(), 2)))
                    else:
                        half_holes += 1
        explorer.Next()

    mount = sorted(centres[2.1])
    small = sorted(centres[1.0])
    rows = [p for p in small if abs(abs(p[0]) - 8.89) < 0.01]
    debug = [p for p in small if abs(p[1] + 23.9) < 0.01 and abs(p[0]) < 3]

    inter = IntCurvesFace_ShapeIntersector()
    inter.Load(shape, 1e-6)
    inter.Perform(gp_Lin(gp_Pnt(4, 0, 0.5), gp_Dir(0, 1, 0)), -100, 100)
    ys = sorted(inter.WParameter(i) for i in range(1, inter.NbPnt() + 1))
    board_len = round(ys[-1] - ys[0], 2)

    xs_m = sorted({abs(p[0]) for p in mount})
    ys_m = sorted({abs(p[1]) for p in mount})
    print(json.dumps({
        "answers": {
            "overall_size_mm_x_y_z": overall,
            "pcb_size_mm": [21.0, board_len],
            "pcb_thickness_mm": 1.0,
            "usb_connector_overhang_mm": round(overall[1] - board_len, 2),
            "note_overall_vs_datasheet": "datasheet board is 21 x 51 mm; the model's overall Y is 52.3 mm because the micro-USB shell overhangs the board edge by 1.3 mm; overall height 3.7 mm (1.0 mm PCB + parts)",
            "mounting_holes": {"count": len(mount), "diameter_mm": 2.1, "through": True,
                               "centres_mm": [list(p) for p in mount],
                               "spacing_mm_x_y": [round(2 * xs_m[0], 2), round(2 * ys_m[0], 2)]},
            "pin_holes": {"count": len(rows), "diameter_mm": 1.0, "pitch_mm": 2.54, "row_spacing_mm": 17.78},
            "debug_pad_holes": {"count": len(debug), "diameter_mm": 1.0, "pitch_mm": 2.54},
            "total_holes": len(mount) + len(small),
            "castellated_half_holes_on_board_edge": half_holes,
            "hole_sizes": sorted(centres.keys()),
            "drawing_requirements": "A3 landscape (420 x 297 mm) sheet, at least front/top/side views, overall dimensions 21 / 52.3 / 3.7, hole callouts '4x D2.1 THRU' and 43x D1.0 THRU (40 pin holes + 3 debug holes), scale stated, title block; the mounting hole spacing 11.4 x 47 should be readable from the drawing (dimension or ordinates).",
        },
        "how": "OCP: reversed (concave) cylinder faces with axis along z, centres de-duplicated per hole; board length by line/solid intersection along y at x=4, z=0.5 mm; overall box with an optimal Bnd_Box. Cross-checked by hand against the Raspberry Pi Pico datasheet numbers (21 x 51 mm, 2.1 mm holes at 11.4 x 47 mm, 2.54 mm pitch, 17.78 mm row spacing).",
        "tolerance": "hole counts and sizes exact; positions +-0.02 mm; overall size +-0.05 mm. What the drawing must SHOW is judged from the PNG/PDF: legibility (no overlapping dimension text), A3 sheet, views, callouts. Drawn text is outline-only in PDF/PNG (the DXF holds text), so a reader can only read numbers from the image.",
    }, indent=1))


if __name__ == "__main__":
    main()
