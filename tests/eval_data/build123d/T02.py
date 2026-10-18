from build123d import *

# Block 60 x 30 x 15 with one 8 mm through hole along z at the centre
block = Box(60, 30, 15)
hole = Cylinder(radius=4, height=20)  # longer than block, so it goes fully through
result = block - hole
