"""Evaluation tasks: natural-language part descriptions with analytic volume and bounding-box size.

The expected values are closed-form (no CAD kernel involved), so they are an independent check.
"""

import math

pi = math.pi

TASKS = [
    {
        "id": "T01",
        "text": "A rectangular block 80 mm long (x), 40 mm wide (y) and 10 mm high (z).",
        "volume": 80 * 40 * 10,
        "size": (80, 40, 10),
    },
    {
        "id": "T02",
        "text": "A block 60 (x) by 30 (y) by 15 (z) mm with one 8 mm diameter hole drilled straight through the full "
        "height (z) at the centre of the block.",
        "volume": 60 * 30 * 15 - pi * 4**2 * 15,
        "size": (60, 30, 15),
    },
    {
        "id": "T03",
        "text": "A flat plate 100 (x) by 60 (y) by 8 (z) mm with four 6 mm diameter through holes (along z), one near each "
        "corner, each hole centred 10 mm in from the two plate edges that meet at that corner.",
        "volume": 100 * 60 * 8 - 4 * pi * 3**2 * 8,
        "size": (100, 60, 8),
    },
    {
        "id": "T04",
        "text": "A solid cylinder of diameter 30 mm and height 40 mm with its axis along z.",
        "volume": pi * 15**2 * 40,
        "size": (30, 30, 40),
    },
    {
        "id": "T05",
        "text": "A tube with its axis along z: outer diameter 40 mm, inner diameter 30 mm, length 100 mm.",
        "volume": pi * (20**2 - 15**2) * 100,
        "size": (40, 40, 100),
    },
    {
        "id": "T06",
        "text": "A block 50 (x) by 50 (y) by 20 (z) mm whose four vertical edges (the edges parallel to z) are rounded "
        "with a 3 mm radius fillet.",
        "volume": 50 * 50 * 20 - 4 * (3**2 - pi * 3**2 / 4) * 20,
        "size": (50, 50, 20),
    },
    {
        "id": "T07",
        "text": "An L-shaped bracket made of one solid: a base plate 60 (x) by 40 (y) by 5 (z) mm, and a vertical wall "
        "5 mm thick in x and 40 mm wide in y standing on the x=0 end of the plate, rising to a total height of 30 mm "
        "(z) measured from the bottom of the base plate.",
        "volume": 60 * 40 * 5 + 5 * 40 * 25,
        "size": (60, 40, 30),
    },
    {
        "id": "T08",
        "text": "A round flange: a disc of diameter 80 mm and thickness 10 mm (axis along z) with a central through hole "
        "of diameter 20 mm and six through holes of diameter 6 mm spaced evenly (60 degrees apart) on a bolt circle of "
        "diameter 60 mm.",
        "volume": pi * 40**2 * 10 - pi * 10**2 * 10 - 6 * pi * 3**2 * 10,
        "size": (80, 80, 10),
    },
    {
        "id": "T09",
        "text": "A stepped shaft with its axis along z: a lower section of diameter 20 mm and height 30 mm, and on top of "
        "it, coaxial, an upper section of diameter 12 mm and height 40 mm.",
        "volume": pi * 10**2 * 30 + pi * 6**2 * 40,
        "size": (20, 20, 70),
    },
    {
        "id": "T10",
        "text": "A block 40 (x) by 40 (y) by 30 (z) mm with a blind hole of diameter 10 mm and depth 15 mm drilled "
        "downward from the centre of the top face.",
        "volume": 40 * 40 * 30 - pi * 5**2 * 15,
        "size": (40, 40, 30),
    },
    {
        "id": "T11",
        "text": "A block 100 (x) by 50 (y) by 20 (z) mm with a rectangular pocket 30 mm (x) by 20 mm (y) and 8 mm deep "
        "cut into the top face, centred on the block.",
        "volume": 100 * 50 * 20 - 30 * 20 * 8,
        "size": (100, 50, 20),
    },
    {
        "id": "T12",
        "text": "A block 60 (x) by 40 (y) by 20 (z) mm with a 2 mm chamfer (2 mm along each of the two faces) on each of "
        "its four vertical edges.",
        "volume": 60 * 40 * 20 - 4 * (2 * 2 / 2) * 20,
        "size": (60, 40, 20),
    },
    {
        "id": "T13",
        "text": "A washer made by revolving a rectangle about the z axis: inner radius 8 mm, outer radius 14 mm, height "
        "3 mm.",
        "volume": pi * (14**2 - 8**2) * 3,
        "size": (28, 28, 3),
    },
]

REL_TOL_VOLUME = 1e-4
ABS_TOL_SIZE = 1e-3
