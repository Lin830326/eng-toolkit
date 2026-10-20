import pathlib
import sys

out = pathlib.Path(sys.argv[1])
HEAD = open(sys.argv[2], encoding="utf-8").read()  # file header: version, layers, setup

PAD1 = '(pad "1" smd rect (at 0 -1) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "SIG"))'
PAD2_SMD = '(pad "2" smd rect (at 0 1) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask") (net 2 "GND"))'
PAD2_THT = '(pad "2" thru_hole circle (at 0 1) (size 1.2 1.2) (drill 0.6) (layers "*.Cu" "*.Mask") (net 2 "GND"))'


def fp(ref, x, y, pad2):
    return f'''  (footprint "R2P" (layer "F.Cu") (tstamp 00000000-0000-0000-0000-0000000000{ref[1]}0) (at {x} {y})
    (attr smd)
    (fp_text reference "{ref}" (at 0 -2.5) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))
    (fp_text value "10k" (at 0 2.5) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))
    {PAD1}
    {pad2}
  )
'''


def seg(x1, y1, x2, y2, w, net, layer="F.Cu"):
    return f'  (segment (start {x1} {y1}) (end {x2} {y2}) (width {w}) (layer "{layer}") (net {net}))\n'


NETS = '  (net 0 "")\n  (net 1 "SIG")\n  (net 2 "GND")\n  (net 3 "N3")\n  (net 4 "N4")\n'
OUTLINE = '  (gr_rect (start 0 0) (end 20 20) (layer "Edge.Cuts") (width 0.1) (fill none))\n'
FPS = fp("R1", 5, 15, PAD2_SMD) + fp("R2", 15, 15, PAD2_THT)
SIG = seg(5, 14, 15, 14, 0.25, 1)
GND = seg(5, 16, 15, 16, 0.25, 2)
BOT = seg(10, 16, 15, 16, 0.25, 2, "B.Cu")
VIA = '  (via (at 10 16) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 2))\n'


def board(name, outline=True, sig=SIG, gnd=GND, extra=""):
    text = HEAD + NETS + "\n" + (OUTLINE if outline else "") + FPS + sig + gnd + BOT + VIA + extra + ")\n"
    (out / name).write_text(text, encoding="utf-8")


board("clean.kicad_pcb")
# Perpendicular pair on N3 and N4: gap 0.1 mm between the round end cap of the vertical track and the horizontal track
board("clearance.kicad_pcb", extra=seg(5, 5, 15, 5, 0.2, 3) + seg(10, 5.3, 10, 9, 0.2, 4))
board("unconnected.kicad_pcb", sig="")
board("narrow.kicad_pcb", gnd=seg(5, 16, 15, 16, 0.1, 2))
board("no_outline.kicad_pcb", outline=False)
