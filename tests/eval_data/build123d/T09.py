from build123d import *

# Stepped shaft along z, coaxial
# Lower section: diameter 20 (r 10), z 0..30
# Upper section: diameter 12 (r 6), z 30..70
lower = Pos(0, 0, 15) * Cylinder(radius=10, height=30)
upper = Pos(0, 0, 50) * Cylinder(radius=6, height=40)
result = lower + upper
