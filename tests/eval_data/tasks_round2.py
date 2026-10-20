"""Round 2 evaluation tasks: parts that need the ops added after the first evaluation (pattern, mirror, shell, path profiles,
taper, of: selectors).  Same rules as tasks.py: plain English with explicit axes, closed-form volume and bounding-box size,
no CAD kernel involved.

The texts do not name the ops.  A task counts as passed when the part is right, whichever ops the model picked.
"""

import math

pi = math.pi

_R_TOP = 12.0  # boss radius of R08
_FILLET = 3.0  # R08 fillet radius
_U0 = _R_TOP - _FILLET
# the removed ring of a fillet on the convex rim of a cylinder: area r^2 (1 - pi/4), moved about the axis at its centroid radius (Pappus)
_R08_REMOVED = 2 * pi * (_FILLET**2 * (_U0 + _FILLET / 2) - (pi * _FILLET**2 / 4) * (_U0 + 4 * _FILLET / (3 * pi)))

_R10_R, _R10_F = 8.0, 2.0
_R10_REMOVED = 2 * pi * (_R10_F**2 * ((_R10_R - _R10_F) + _R10_F / 2) - (pi * _R10_F**2 / 4) * ((_R10_R - _R10_F) + 4 * _R10_F / (3 * pi)))

_T07_A2 = 30 - 2 * 20 * math.tan(math.radians(5))

TASKS = [
    {
        "id": "R01",
        "text": "A flat bar 120 (x) by 20 (y) by 8 (z) mm with six 5 mm diameter through holes along z. The holes are in one row along "
        "x at y = 10: the first hole is centred at x = 10 and the others follow every 20 mm (x = 30, 50, 70, 90, 110).",
        "volume": 120 * 20 * 8 - 6 * pi * 2.5**2 * 8,
        "size": (120, 20, 8),
    },
    {
        "id": "R02",
        "text": "A round plate of diameter 100 mm and thickness 6 mm, its axis along z, bottom face on z = 0, centred on the z axis. "
        "It has eight 6 mm diameter through holes (along z) on a 70 mm diameter bolt circle centred on the plate axis, evenly "
        "spaced all the way round, the first one on the +x axis (centre at x = 35, y = 0).",
        "volume": pi * 50**2 * 6 - 8 * pi * 3**2 * 6,
        "size": (100, 100, 6),
    },
    {
        "id": "R03",
        "text": "A part that is mirror symmetric about the plane x = 0. Its +x half is a block 30 (x) by 40 (y) by 10 (z) mm occupying "
        "x from 0 to 30, with one 8 mm diameter through hole along z whose centre is at x = 15, y = 20. The whole part is that "
        "half together with its mirror image in the plane x = 0 (so it spans x from -30 to 30 and has two holes).",
        "volume": 2 * (30 * 40 * 10 - pi * 4**2 * 10),
        "size": (60, 40, 10),
    },
    {
        "id": "R04",
        "text": "A round cup made by hollowing a solid cylinder: the cylinder has diameter 50 mm and height 40 mm with its axis along z "
        "and its bottom on z = 0. The top (z = 40) is open; the walls and the bottom are all 2.5 mm thick.",
        "volume": pi * (25**2 * 40 - 22.5**2 * 37.5),
        "size": (50, 50, 40),
    },
    {
        "id": "R05",
        "text": "A plate 80 (x) by 50 (y) by 6 (z) mm with one stadium-shaped through slot (two straight sides joined by semicircular "
        "ends) cut along z. The slot is centred in the plate, its long direction along x, its straight sides are 30 mm long and "
        "its width is 16 mm (so the semicircular ends have radius 8 mm).",
        "volume": 80 * 50 * 6 - (30 * 16 + pi * 8**2) * 6,
        "size": (80, 50, 6),
    },
    {
        "id": "R06",
        "text": "A D-shaped plate 5 mm thick (z from 0 to 5). Seen from above (looking down z) its outline is a straight edge on the line "
        "x = 0 running from y = -15 to y = 15, closed by a semicircular arc of radius 15 mm that bulges towards +x (so the plate "
        "spans x from 0 to 15 and y from -15 to 15).",
        "volume": pi * 15**2 / 2 * 5,
        "size": (15, 30, 5),
    },
    {
        "id": "R07",
        "text": "A tapered square boss: its base is a 30 mm by 30 mm square on z = 0 (x and y from 0 to 30), it is 20 mm high, and its four "
        "side walls lean inward by 5 degrees from vertical all the way up, so the top face is a smaller square (the part is a "
        "frustum of a square pyramid).",
        "volume": 20 / 3 * (30**2 + _T07_A2**2 + 30 * _T07_A2),
        "size": (30, 30, 20),
    },
    {
        "id": "R08",
        "text": "A plate 80 (x) by 50 (y) by 10 (z) mm with a cylindrical boss of diameter 24 mm and height 14 mm standing on its top face, "
        "centred at x = 40, y = 25 (so the part is 24 mm high in total). Only the circular top edge of the boss is rounded, with a "
        "3 mm radius fillet; no other edge is touched.",
        "volume": 80 * 50 * 10 + pi * 12**2 * 14 - _R08_REMOVED,
        "size": (80, 50, 24),
    },
    # R09 and R10 were added after the first run, because Haiku met R01 and R02 with explicit lists of holes and R08 with an
    # ordinary edge selector, so those runs did not exercise pattern or of:.  These two cannot be done well without them.
    {
        "id": "R09",
        "added_after_run1": True,
        "text": "A round disc of diameter 80 mm and thickness 8 mm (axis along z, bottom on z = 0, centred on the z axis) with six "
        "rectangular through slots, each 20 mm long and 4 mm wide, arranged like spokes: each slot lies along a radius of the disc "
        "from 15 mm to 35 mm out from the axis. The first slot is along the +x axis (its long side from x = 15 to x = 35, y from -2 to "
        "2) and the others are the same slot turned about the z axis in steps of 60 degrees.",
        "volume": pi * 40**2 * 8 - 6 * 20 * 4 * 8,
        "size": (80, 80, 8),
    },
    {
        "id": "R10",
        "added_after_run1": True,
        "text": "A plate 80 (x) by 50 (y) by 10 (z) mm with two cylindrical bosses of diameter 16 mm and height 12 mm standing on its top "
        "face, one centred at x = 20, y = 25 and the other at x = 60, y = 25 (so the part is 22 mm high). Only the circular top edge of "
        "the boss at x = 20 is rounded, with a 2 mm radius fillet; the top edge of the other boss and every other edge stay sharp.",
        "volume": 80 * 50 * 10 + 2 * pi * 8**2 * 12 - _R10_REMOVED,
        "size": (80, 50, 22),
    },
]
