"""Min/max envelope decimation: at most four points per horizontal pixel column, extrema kept exactly.

A series with more than ``DECIMATE_PER_PX`` points per pixel of plot width is reduced as follows.  When x never decreases, the
points are grouped by the pixel column they fall in (columns left and right of the plot area are one group each); when x is not
sorted, they are grouped in runs of ``ceil(n / plot width)`` consecutive points.  Of each group the first, the last, the one with
the smallest y and the one with the largest y are kept (for unsorted x also the smallest and largest x), in their original order.
The global minimum and maximum of y are the minimum and maximum of some group, so they are always drawn: the plotted extrema
never change.  Nothing is interpolated or averaged.
"""

from __future__ import annotations

import math

import numpy as np

from eng_toolkit.domains.plot._common import DECIMATE_PER_PX

METHOD_SORTED = "min/max envelope per pixel column: first, last, minimum-y and maximum-y point of every column, in order"
METHOD_UNSORTED = "min/max envelope per run of consecutive points (x not sorted): first, last, min/max y and min/max x of every run, in order"


def _first_where(mask: np.ndarray, gid: np.ndarray, groups: int) -> np.ndarray:
    idx = np.flatnonzero(mask)
    _, first = np.unique(gid[idx], return_index=True)
    out = idx[first]
    assert len(out) == groups  # every group holds its own extremum
    return out


def decimate(x: np.ndarray, y: np.ndarray, x_px: np.ndarray, plot_left: float, plot_width: float) -> tuple[np.ndarray, str | None]:
    """Indices of the points to draw and the method used (None when the series is drawn whole)."""
    n = len(y)
    if n <= DECIMATE_PER_PX * plot_width:
        return np.arange(n), None
    if n < 2 or bool(np.all(np.diff(x) >= 0)):
        col = np.clip(np.floor(np.clip(x_px, -1e7, 1e7) - plot_left), -1, math.ceil(plot_width)).astype(np.int64)
        starts = np.flatnonzero(np.r_[True, col[1:] != col[:-1]])
        method = METHOD_SORTED
        extra_x = False
    else:
        chunk = math.ceil(n / plot_width)
        starts = np.arange(0, n, chunk)
        method = METHOD_UNSORTED
        extra_x = True
    ends = np.r_[starts[1:], n]
    groups = len(starts)
    gid = np.repeat(np.arange(groups), ends - starts)
    keep = [starts, ends - 1]
    for values in ([y, x] if extra_x else [y]):
        lo = np.minimum.reduceat(values, starts)
        hi = np.maximum.reduceat(values, starts)
        keep.append(_first_where(values == lo[gid], gid, groups))
        keep.append(_first_where(values == hi[gid], gid, groups))
    return np.unique(np.concatenate(keep)), method


MARKER_CELL = 2.0  # px; one marker per occupied cell of this size (doubled until at most MARKER_CAP cells are occupied)
MARKER_CAP = 20_000


def thin_markers(x: np.ndarray, y: np.ndarray, x_px: np.ndarray, y_px: np.ndarray, plot_left: float, plot_top: float, plot_width: float,
                 *, stems: bool = False) -> tuple[np.ndarray, str | None, float]:
    """Markers to draw when a scatter or stem series has more than DECIMATE_PER_PX points per pixel of plot width.

    Density-preserving, not an envelope: one marker per occupied ``k x k`` px cell (the first point of the cell in input order),
    plus the points with the smallest and largest y (so the plotted extrema are the data's); for stems also the per-column
    envelope, so the drawn stems still reach every point.  Every point that is not drawn lies within ``k * sqrt(2)`` px of a
    drawn marker centre, i.e. under it for k = 2 (markers have a 3.5 px radius).  Returns (indices, method, k)."""
    n = len(y)
    if n <= DECIMATE_PER_PX * plot_width:
        return np.arange(n), None, 0.0
    cx_all = np.clip(x_px, -1e7, 1e7) - plot_left
    cy_all = np.clip(y_px, -1e7, 1e7) - plot_top
    k = MARKER_CELL
    while True:
        keys = np.floor(cx_all / k).astype(np.int64) * (1 << 32) + np.floor(cy_all / k).astype(np.int64)
        _, first = np.unique(keys, return_index=True)
        if len(first) <= MARKER_CAP or k >= 64:
            break
        k *= 2
    keep = [first, np.array([int(np.argmin(y)), int(np.argmax(y))])]
    if stems:
        env, _ = decimate(x, y, x_px, plot_left, plot_width)
        keep.append(env)
    method = (f"marker thinning: one marker per occupied {k:g} x {k:g} px cell (the first point of the cell), plus the minimum and maximum y"
              + (" and the per-column extremes (the stems reach every point)" if stems else "")
              + f"; every point not drawn lies within {k * 1.4143:.1f} px of a drawn marker centre")
    return np.unique(np.concatenate(keep)), method, k


__all__ = ["MARKER_CAP", "MARKER_CELL", "METHOD_SORTED", "METHOD_UNSORTED", "decimate", "thin_markers"]
