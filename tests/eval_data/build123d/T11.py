from build123d import *

# Block 100 x 50 x 20, centred on the origin: top face at z=10
# Pocket 30 (x) x 20 (y) x 8 deep, centred on the top face (floor at z=2)
# Cutter spans z 2..12 so it opens through the top face cleanly
block = Box(100, 50, 20)
pocket = Pos(0, 0, 7) * Box(30, 20, 10)
result = block - pocket
