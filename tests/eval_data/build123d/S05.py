from build123d import *

# Bar 100 (x) x 20 (y) x 8 (z) mm, with its lower-left-front corner at the origin
# so the first hole sits at x = 10 measured from the left end.
# Five 6 mm diameter through holes along z, 15 mm apart in x, centred in y (y = 10).
# Hole x positions: 10, 25, 40, 55, 70.
hole_positions = [(10 + 15 * i, 10, 4) for i in range(5)]  # z = 4 is the bar's mid-height

with BuildPart() as bar:
    Box(100, 20, 8, align=(Align.MIN, Align.MIN, Align.MIN))
    with Locations(*hole_positions):
        Cylinder(radius=3, height=12, mode=Mode.SUBTRACT)  # spans z = -2 to 10, clears both faces

result = bar.part
