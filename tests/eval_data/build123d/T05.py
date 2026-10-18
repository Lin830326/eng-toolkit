from build123d import *

# Tube along z: outer diameter 40 (r 20), inner diameter 30 (r 15), length 100
outer = Cylinder(radius=20, height=100)
inner = Cylinder(radius=15, height=100)
result = outer - inner
