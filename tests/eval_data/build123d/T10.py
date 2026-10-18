from build123d import *

# Block 40 x 40 x 30, centred on the origin: top face at z=15, bottom at z=-15
# Blind hole, diameter 10 (r 5), depth 15 down from the top face centre
# Hole spans z 0..20 in the cutter, so its bottom sits at z=0 (15 below the top)
block = Box(40, 40, 30)
hole = Pos(0, 0, 10) * Cylinder(radius=5, height=20)
result = block - hole
