import math
from build123d import *

# Round flange: OD 80 (r 40), thickness 10 along z
# Central hole 20 mm diameter (r 10)
# Six 6 mm holes (r 3) at 60 degree spacing on a 60 mm bolt circle (radius 30)
flange = Cylinder(radius=40, height=10)
result = flange - Cylinder(radius=10, height=12)
for i in range(6):
    a = math.radians(60 * i)
    hole = Pos(30 * math.cos(a), 30 * math.sin(a), 0) * Cylinder(radius=3, height=12)
    result = result - hole
