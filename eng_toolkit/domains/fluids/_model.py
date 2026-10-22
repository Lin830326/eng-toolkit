"""Monotone pump curves shared only within the fluids domain."""

from dataclasses import dataclass

import numpy as np

from ._common import fields, identifier, invalid, q, records, scalar


@dataclass(frozen=True)
class Curve:
    flows: tuple[float, ...]
    heads: tuple[float, ...]
    efficiencies: tuple[float, ...]

    def head(self, flow):
        if not 0 <= flow <= self.flows[-1]:
            invalid("pump_points", "flow lies outside supplied pump curve")
        return float(np.interp(flow, self.flows, self.heads))

    def efficiency(self, flow):
        return float(np.interp(flow, self.flows, self.efficiencies))

    def flow(self, head):
        if head >= self.heads[0]:
            return 0.
        if head < self.heads[-1]:
            invalid("pump_points", "head lies below supplied pump curve")
        return float(np.interp(head, self.heads[::-1], self.flows[::-1]))


def curve(points, path):
    if len(points) < 2 or points[0][0] != 0:
        invalid(path, "curve needs >=2 points starting at zero flow")
    for left, right in zip(points, points[1:]):
        if right[0] <= left[0] or right[1] >= left[1]:
            invalid(path, "points must have strictly increasing flow and strictly decreasing head")
    return Curve(*(tuple(row[i] for row in points) for i in range(3)))


def pump_curves(text, speed, diameter):
    groups = {}
    for i, row in enumerate(records(text, "pump_points", 120, required=True)):
        path = f"pump_points/{i}"
        fields(row, (4,), path)
        name = identifier(row[0], path+"/0")
        flow = q(row[1], path+"/1", "flow", zero=True)*speed*diameter
        head = q(row[2], path+"/2", "length", zero=True)*(speed*diameter)**2
        eta = scalar(row[3], path+"/3", .01, 1)
        groups.setdefault(name, []).append((flow, head, eta))
    if len(groups) > 8:
        invalid("pump_points", "at most 8 pump curves", "input.out_of_range")
    return {name: curve(points, "pump_points/"+name) for name, points in groups.items()}
