from build123d import *

# Plate 100 x 40 x 10 mm, centred on the origin (z from -5 to 5).
# Countersunk through hole at the centre:
#   - through hole diameter 6 mm (radius 3)
#   - countersink opening on the top face (z = 5) with diameter 12 mm (radius 6)
#   - 90 degree included angle -> 45 degree half angle, so the countersink
#     depth is (6 - 3) / tan(45) = 3 mm, running from z = 2 up to z = 5.
#     The cone is extended 1 mm above the top face (radius 7 at z = 6) to
#     guarantee a clean cut; the 45 degree slope is kept.
with BuildPart() as plate:
    Box(100, 40, 10)
    Cylinder(radius=3, height=20, mode=Mode.SUBTRACT)
    with Locations((0, 0, 2)):
        Cone(
            bottom_radius=3,
            top_radius=7,
            height=4,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
            mode=Mode.SUBTRACT,
        )

result = plate.part
