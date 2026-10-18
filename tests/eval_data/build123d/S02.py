from build123d import *

# Open-top box: outside 60 x 40 x 30 mm, walls 3 mm, bottom 3 mm.
# Outer box is centred on the origin, so z runs from -15 to 15.
# The cavity is 54 x 34 mm in plan and starts at z = -12 (3 mm floor),
# extending above the top face so the box is open at the top.
with BuildPart() as box:
    Box(60, 40, 30)
    with Locations((0, 0, 4)):
        Box(54, 34, 32, mode=Mode.SUBTRACT)

result = box.part
