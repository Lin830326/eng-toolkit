"""Stretch tasks: some need operations the v1 spec language does not have (sphere), others stress it."""

import math

pi = math.pi

# volume of a frustum with radii r1 (bottom) and r2 (top) and height h
def frustum(r1, r2, h):
    return pi * h / 3 * (r1**2 + r1 * r2 + r2**2)


TASKS = [
    {
        "id": "S01",
        "text": "A solid sphere of diameter 30 mm.",
        "volume": 4 / 3 * pi * 15**3,
        "size": (30, 30, 30),
    },
    {
        "id": "S02",
        "text": "An open-top rectangular box made from one solid: outside 60 (x) by 40 (y) by 30 (z) mm, walls 3 mm thick "
        "and a 3 mm thick bottom, open at the top.",
        "volume": 60 * 40 * 30 - 54 * 34 * 27,
        "size": (60, 40, 30),
    },
    {
        "id": "S03",
        "text": "A truncated cone (frustum) with its axis along z: bottom diameter 40 mm, top diameter 20 mm, height 30 mm.",
        "volume": frustum(20, 10, 30),
        "size": (40, 40, 30),
    },
    {
        "id": "S04",
        "text": "A plate 100 (x) by 40 (y) by 10 (z) mm with one countersunk through hole at the centre: hole diameter 6 mm, "
        "countersink opening at the top face with diameter 12 mm and a 90 degree included angle.",
        # cylinder r3 through the plate, plus the cone part of the countersink above the cylinder (depth 3 mm)
        "volume": 100 * 40 * 10 - pi * 3**2 * 10 - (frustum(6, 3, 3) - pi * 3**2 * 3),
        "size": (100, 40, 10),
    },
    {
        "id": "S05",
        "text": "A bar 100 (x) by 20 (y) by 8 (z) mm with five 6 mm diameter through holes (along z) in a row along x, "
        "15 mm apart, the first one 10 mm from the left end (x = 10), all centred in y.",
        "volume": 100 * 20 * 8 - 5 * pi * 3**2 * 8,
        "size": (100, 20, 8),
    },
]

REL_TOL_VOLUME = 1e-4
ABS_TOL_SIZE = 1e-3
