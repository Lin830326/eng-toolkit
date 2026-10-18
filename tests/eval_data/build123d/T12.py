from build123d import *

# Block 60 x 40 x 20 with a 2 mm chamfer on each of the four vertical edges
with BuildPart() as p:
    Box(60, 40, 20)
    chamfer(p.edges().filter_by(Axis.Z), length=2)
result = p.part
