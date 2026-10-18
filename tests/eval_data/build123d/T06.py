from build123d import *

# Block 50 x 50 x 20 mm with the four vertical (z-parallel) edges filleted, R3
with BuildPart() as p:
    Box(50, 50, 20)
    fillet(p.edges().filter_by(Axis.Z), radius=3)
result = p.part
