"""Hand-made projections for the drawing tests.  Pure: no OpenCASCADE and no DXF library."""

import math

from eng_toolkit.brep import VIEW_FRAMES, Arc2, Ellipse2, Line2, Poly2, Projection


def projection(view: str, box=(100.0, 50.0, 20.0), visible=None, hidden=()) -> Projection:
    """The projection of a box with one corner at the origin; only what the layout and the writers read is filled in."""
    frame = VIEW_FRAMES[view]
    corners = [(x, y, z) for x in (0.0, box[0]) for y in (0.0, box[1]) for z in (0.0, box[2])]
    u = [sum(c[i] * frame.x_axis[i] for i in range(3)) for c in corners]
    v = [sum(c[i] * frame.y_axis[i] for i in range(3)) for c in corners]
    lo, hi = (min(u), min(v)), (max(u), max(v))
    if visible is None:
        visible = (
            Line2(lo, (hi[0], lo[1])),
            Line2((hi[0], lo[1]), hi),
            Line2((lo[0], hi[1]), hi),
            Line2(lo, (lo[0], hi[1])),
        )
    return Projection(
        view=view,
        direction=tuple(-c for c in frame.normal),
        x_axis=frame.x_axis,
        y_axis=frame.y_axis,
        visible=tuple(visible),
        hidden=tuple(hidden),
        bbox_min=lo,
        bbox_max=hi,
        dropped={},
        method="exact",
        verified=True,
    )


def rich_front() -> Projection:
    """A front view of the 100 x 20 box carrying one primitive of every kind and two hidden lines.

    Visible: 4 lines (240 mm), a circle R5 (31.4159), a quarter arc R3 (4.7124), a full ellipse of semi-axes 8 and 4
    (about 40.1), a three-point polyline (2 * hypot(5, 4) = 12.8062).  Hidden: two lines of 20 mm.
    """
    return projection(
        "front",
        visible=(
            Line2((0.0, 0.0), (100.0, 0.0)),
            Line2((100.0, 0.0), (100.0, 20.0)),
            Line2((0.0, 20.0), (100.0, 20.0)),
            Line2((0.0, 0.0), (0.0, 20.0)),
            Arc2((50.0, 10.0), 5.0, 0.0, 2.0 * math.pi),
            Arc2((20.0, 10.0), 3.0, 0.0, math.pi / 2.0),
            Ellipse2((70.0, 10.0), (8.0, 0.0), 0.5, 0.0, 2.0 * math.pi),
            Poly2(((80.0, 2.0), (85.0, 6.0), (90.0, 2.0))),
        ),
        hidden=(Line2((30.0, 0.0), (30.0, 20.0)), Line2((40.0, 0.0), (40.0, 20.0))),
    )
