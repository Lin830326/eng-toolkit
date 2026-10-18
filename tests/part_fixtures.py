"""Part specs shared by the make_part tests, each with its hand-calculated volume in mm^3.  Pure: no engine needed."""

import json
import math


def spec(features, **params) -> str:
    return json.dumps({"params": params, "features": features})


def box(size, at=("0 mm", "0 mm", "0 mm"), **extra):
    return {"op": "box", "size": list(size), "at": list(at), **extra}


def cylinder(diameter, height, at=("0 mm", "0 mm", "0 mm"), axis="+z", **extra):
    return {"op": "cylinder", "diameter": diameter, "height": height, "at": list(at), "axis": axis, **extra}


def sphere(diameter, at=("0 mm", "0 mm", "0 mm"), **extra):
    return {"op": "sphere", "diameter": diameter, "at": list(at), **extra}


def cone(diameter, top_diameter, height, at=("0 mm", "0 mm", "0 mm"), axis="+z", **extra):
    return {"op": "cone", "diameter": diameter, "top_diameter": top_diameter, "height": height, "at": list(at), "axis": axis, **extra}


def hole(at, diameter, depth="thru", axis="-z", **extra):
    return {"op": "hole", "at": [list(p) for p in at], "diameter": diameter, "depth": depth, "axis": axis, **extra}


def fillet(edges, radius):
    return {"op": "fillet", "edges": edges, "radius": radius}


def chamfer(edges, distance):
    return {"op": "chamfer", "edges": edges, "distance": distance}


PROBE_VOLUME = 100000 - 25 * math.pi * 20 - 4 * (4 - math.pi) * 20  # 98360.531085
PROBE_AREA = 16395.699052
PROBE_FEATURES = [
    box(("100 mm", "50 mm", "20 mm")),
    hole([("25 mm", "25 mm", "20 mm")], "10 mm"),
    fillet("|z & convex", "2 mm"),
]
PROBE_SPEC = spec(PROBE_FEATURES)

# (id, spec text, expected volume in mm^3, hand calculation)
VOLUME_CASES = [
    ("box", spec([box(("80 mm", "40 mm", "10 mm"))]), 32000.0, "80*40*10"),
    (
        "cylinder",
        spec([cylinder("30 mm", "40 mm")]),
        math.pi * 225 * 40,
        "pi*15^2*40 = 28274.334",
    ),
    ("sphere", spec([sphere("30 mm")]), 4 / 3 * math.pi * 15**3, "4/3*pi*15^3 = 14137.1669"),
    (
        "sphere_cut_from_a_cube",
        spec([box(("40 mm", "40 mm", "40 mm")), sphere("30 mm", at=("20 mm", "20 mm", "20 mm"), mode="cut")]),
        64000 - 4 / 3 * math.pi * 15**3,
        "40^3 - 4/3*pi*15^3 = 49862.8331",
    ),
    (
        "frustum",
        spec([cone("40 mm", "20 mm", "30 mm")]),
        math.pi * 30 / 3 * (400 + 200 + 100),
        "pi*h/3*(R^2 + R*r + r^2) with h=30, R=20, r=10 = 21991.1486",
    ),
    ("full_cone", spec([cone("30 mm", "0 mm", "20 mm")]), math.pi * 225 * 20 / 3, "pi*15^2*20/3 = 4712.3890"),
    (
        "cone_with_equal_diameters",
        spec([cone("30 mm", "30 mm", "40 mm")]),
        math.pi * 225 * 40,
        "the cylinder pi*15^2*40",
    ),
    (
        "through_hole",
        spec([box(("60 mm", "30 mm", "15 mm")), hole([("30 mm", "15 mm", "15 mm")], "8 mm")]),
        27000 - math.pi * 16 * 15,
        "60*30*15 - pi*4^2*15 = 26246.018",
    ),
    (
        "plate_with_four_holes",
        spec(
            [
                box(("100 mm", "60 mm", "8 mm")),
                hole(
                    [("10 mm", "10 mm", "8 mm"), ("90 mm", "10 mm", "8 mm"), ("10 mm", "50 mm", "8 mm"), ("90 mm", "50 mm", "8 mm")],
                    "6 mm",
                ),
            ]
        ),
        48000 - 4 * math.pi * 9 * 8,
        "100*60*8 - 4*pi*3^2*8 = 47095.221",
    ),
    (
        "tube",
        spec([cylinder("40 mm", "100 mm"), cylinder("30 mm", "100 mm", mode="cut")]),
        math.pi * (400 - 225) * 100,
        "pi*(20^2 - 15^2)*100 = 54977.871",
    ),
    (
        "filleted_box",
        spec([box(("50 mm", "50 mm", "20 mm")), fillet("|z", "3 mm")]),
        50000 - 4 * (9 - 9 * math.pi / 4) * 20,
        "50*50*20 - 4*(3^2 - pi*3^2/4)*20 = 49845.4867",
    ),
    (
        "l_bracket",
        spec([box(("60 mm", "40 mm", "5 mm")), box(("5 mm", "40 mm", "25 mm"), at=("0 mm", "0 mm", "5 mm"))]),
        12000 + 5000,
        "60*40*5 + 5*40*25 = 17000",
    ),
    (
        "stepped_shaft",
        spec([cylinder("20 mm", "30 mm"), cylinder("12 mm", "40 mm", at=("0 mm", "0 mm", "30 mm"))]),
        math.pi * 100 * 30 + math.pi * 36 * 40,
        "pi*10^2*30 + pi*6^2*40 = 13948.671",
    ),
    (
        "blind_hole",
        spec([box(("40 mm", "40 mm", "30 mm")), hole([("20 mm", "20 mm", "30 mm")], "10 mm", depth="15 mm")]),
        48000 - math.pi * 25 * 15,
        "40*40*30 - pi*5^2*15 = 46821.903",
    ),
    (
        "pocket",
        spec([box(("100 mm", "50 mm", "20 mm")), box(("30 mm", "20 mm", "8 mm"), at=("35 mm", "15 mm", "12 mm"), mode="cut")]),
        100000 - 30 * 20 * 8,
        "100*50*20 - 30*20*8 = 95200",
    ),
    (
        "chamfered_box",
        spec([box(("60 mm", "40 mm", "20 mm")), chamfer("|z", "2 mm")]),
        48000 - 4 * 2 * 20,
        "60*40*20 - 4*(2*2/2)*20 = 47840",
    ),
    (
        "washer_by_revolve",
        spec(
            [
                {
                    "op": "revolve",
                    "plane": "xz",
                    "profile": {"rect": {"size": ["6 mm", "3 mm"], "at": ["8 mm", "0 mm"]}},
                    "axis": "v",
                }
            ]
        ),
        math.pi * (196 - 64) * 3,
        "inner radius 8, outer radius 14, height 3: pi*(14^2 - 8^2)*3 = 1244.0708",
    ),
    (
        "extruded_rectangle",
        spec([{"op": "extrude", "profile": {"rect": {"size": ["20 mm", "10 mm"]}}, "distance": "5 mm"}]),
        1000.0,
        "20*10*5",
    ),
    (
        "extruded_centered_rectangle_downwards",
        spec(
            [
                {
                    "op": "extrude",
                    "plane": "xy",
                    "offset": "7 mm",
                    "profile": {"rect": {"size": ["20 mm", "10 mm"], "centered": True}},
                    "distance": "-5 mm",
                }
            ]
        ),
        1000.0,
        "20*10*5, built from z = 7 down to z = 2",
    ),
    (
        "extruded_circle_on_xz",
        spec([{"op": "extrude", "plane": "xz", "profile": {"circle": {"diameter": "10 mm"}}, "distance": "20 mm"}]),
        math.pi * 25 * 20,
        "pi*5^2*20",
    ),
    (
        "extruded_triangle_on_yz",
        spec(
            [
                {
                    "op": "extrude",
                    "plane": "yz",
                    "profile": {"polygon": {"points": [["0 mm", "0 mm"], ["10 mm", "0 mm"], ["0 mm", "6 mm"]]}},
                    "distance": "4 mm",
                }
            ]
        ),
        0.5 * 10 * 6 * 4,
        "0.5*10*6*4 = 120",
    ),
    (
        "extruded_slot",
        spec([{"op": "extrude", "profile": {"slot": {"length": "30 mm", "width": "10 mm", "angle": "30 deg"}}, "distance": "2 mm"}]),
        ((30 - 10) * 10 + math.pi * 25) * 2,
        "((30-10)*10 + pi*5^2)*2, the angle does not change the area",
    ),
    (
        "half_revolution",
        spec(
            [
                {
                    "op": "revolve",
                    "plane": "xz",
                    "profile": {"rect": {"size": ["6 mm", "3 mm"], "at": ["8 mm", "0 mm"]}},
                    "axis": "v",
                    "angle": "180 deg",
                }
            ]
        ),
        math.pi * (196 - 64) * 3 / 2,
        "half of the washer",
    ),
    (
        "intersect",
        spec([box(("20 mm", "20 mm", "20 mm")), cylinder("20 mm", "20 mm", at=("10 mm", "10 mm", "0 mm"), mode="intersect")]),
        math.pi * 100 * 20,
        "the 20 mm cylinder lies inside the 20 x 20 mm square: pi*10^2*20",
    ),
    (
        "counterbore",
        spec(
            [
                box(("40 mm", "40 mm", "20 mm")),
                hole(
                    [("20 mm", "20 mm", "20 mm")],
                    "6.6 mm",
                    counterbore={"diameter": "11 mm", "depth": "6.5 mm"},
                ),
            ]
        ),
        40 * 40 * 20 - math.pi / 4 * (11**2 * 6.5 + 6.6**2 * 13.5),
        "40*40*20 - pi/4*(11^2*6.5 + 6.6^2*13.5)",
    ),
    (
        "countersink",
        spec(
            [
                box(("40 mm", "40 mm", "20 mm")),
                hole(
                    [("20 mm", "20 mm", "20 mm")],
                    "6 mm",
                    countersink={"diameter": "12 mm", "angle": "90 deg"},
                ),
            ]
        ),
        # cone from radius 6 down to radius 3 over 3 mm (45 degree half angle), then the plain bore below it
        40 * 40 * 20 - (math.pi * 3 / 3 * (36 + 18 + 9) + math.pi * 9 * 17),
        "40*40*20 - [pi*h/3*(R^2 + R*r + r^2) with h=3, R=6, r=3, plus pi*3^2*17]",
    ),
    (
        "blind_hole_along_x_from_the_side",
        spec([box(("40 mm", "40 mm", "30 mm")), hole([("0 mm", "20 mm", "15 mm")], "10 mm", depth="12 mm", axis="+x")]),
        48000 - math.pi * 25 * 12,
        "40*40*30 - pi*5^2*12",
    ),
    (
        "crossing_through_holes",
        spec(
            [
                box(("40 mm", "40 mm", "40 mm")),
                hole([("20 mm", "20 mm", "40 mm")], "10 mm"),
                hole([("0 mm", "20 mm", "20 mm")], "10 mm", axis="+x"),
            ]
        ),
        64000 - 2 * math.pi * 25 * 40 + 16 / 3 * 125,
        "40^3 - 2*pi*5^2*40 + 16/3*5^3 (two equal cylinders crossing share a Steinmetz solid of 16/3 r^3)",
    ),
    (
        "revolve_about_the_u_axis",
        spec([{"op": "revolve", "plane": "xz", "profile": {"rect": {"size": ["4 mm", "3 mm"], "at": ["0 mm", "5 mm"]}}, "axis": "u"}]),
        math.pi * (64 - 25) * 4,
        "pi*(8^2 - 5^2)*4 = 490.088",
    ),
    (
        "blind_hole_deeper_than_the_part",
        spec([box(("40 mm", "40 mm", "30 mm")), hole([("20 mm", "20 mm", "30 mm")], "10 mm", depth="500 mm")]),
        48000 - math.pi * 25 * 30,
        "the hole cuts through: 40*40*30 - pi*5^2*30",
    ),
]


def flange_case():
    """Cylinder 80 x 10, a diameter-20 hole and six diameter-6 holes on a 60 mm bolt circle (coordinates computed here)."""
    centre = 40.0
    bolts = [
        (f"{centre + 30 * math.cos(math.radians(60 * k)):.12f} mm", f"{centre + 30 * math.sin(math.radians(60 * k)):.12f} mm", "10 mm")
        for k in range(6)
    ]
    text = spec(
        [
            cylinder("80 mm", "10 mm", at=("40 mm", "40 mm", "0 mm")),
            hole([("40 mm", "40 mm", "10 mm")], "20 mm"),
            hole(bolts, "6 mm"),
        ]
    )
    return text, math.pi * (1600 * 10 - 100 * 10 - 6 * 9 * 10)
