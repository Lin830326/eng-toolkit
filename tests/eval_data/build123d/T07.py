from build123d import *

# L-bracket, one solid.
# Base plate: x 0..60, y -20..20, z 0..5
# Vertical wall: x 0..5 (5 mm thick), y -20..20 (40 mm wide), z 0..30
base = Pos(30, 0, 2.5) * Box(60, 40, 5)
wall = Pos(2.5, 0, 15) * Box(5, 40, 30)
result = base + wall
