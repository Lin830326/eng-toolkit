from build123d import *

# Washer made by revolving a rectangle about the z axis
# Rectangle: radial span 8..14 (inner r 8, outer r 14), height 0..3 along z
with BuildPart() as p:
    with BuildSketch(Plane.XZ):
        with Locations((11, 1.5)):
            Rectangle(6, 3)
    revolve(axis=Axis.Z)
result = p.part
