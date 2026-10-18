from build123d import *

# Plate 100 x 60 x 8 mm with four 6 mm through holes, 10 mm in from each corner
plate = Box(100, 60, 8)
result = plate
for x in (-40, 40):
    for y in (-20, 20):
        hole = Pos(x, y, 0) * Cylinder(radius=3, height=20)
        result = result - hole
