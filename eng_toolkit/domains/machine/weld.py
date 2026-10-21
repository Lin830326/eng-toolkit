"""Exact straight-line weld group integration and elastic traction distribution."""

import math
import re
from typing import Annotated

from pydantic import Field

from eng_toolkit.contract import ToolResult

from eng_toolkit.domains.machine._common import Q, Result, System, invalid, paired, q, run
from eng_toolkit.domains.machine._server import server

SEGMENT = re.compile(r"\(([^()]*)\)\s*-\s*\(([^()]*)\)")


def segments(raw: str):
    if len(raw) > 20000 or not 1 <= raw.count(";")+1 <= 50:
        invalid("segments_text", "give 1..50 segments, at most 20000 characters")
    lines = []
    for i, item in enumerate(raw.split(";")):
        match = SEGMENT.fullmatch(item.strip())
        parts = [p for g in match.groups() for p in g.split(",")] if match else []
        if len(parts) != 4 or any(not p.strip() for p in parts):
            invalid(f"segments_text/{i}", "expected (x unit,y unit)-(x unit,y unit); no function calls")
        coords = [q(s.strip(), f"segments_text/{i}/{j}", "length", signed=True) for j, s in enumerate(parts)]
        x1, y1, x2, y2 = coords
        length = math.hypot(x2-x1, y2-y1)
        if length < 1e-6:
            invalid(f"segments_text/{i}", "segment length must be >=1e-6 mm", "input.out_of_range")
        # Reject positive-length collinear overlaps, including duplicate lines.
        for ax, ay, bx, by, _ in lines:
            dx, dy = x2-x1, y2-y1
            if abs(dx*(ay-y1)-dy*(ax-x1)) <= 1e-12*length**2 and abs(dx*(by-y1)-dy*(bx-x1)) <= 1e-12*length**2:
                u, v = ((ax-x1)*dx+(ay-y1)*dy)/length**2, ((bx-x1)*dx+(by-y1)*dy)/length**2
                if min(1, max(u, v))-max(0, min(u, v)) > 1e-12:
                    invalid(f"segments_text/{i}", "collinear overlapping segments double-count weld area")
        lines.append((x1, y1, x2, y2, length))
    return lines


@server.tool(description="Fillet weld group as 1..50 straight lines: exact centroid/Ix/Iy/Ixy/J, direct and torsional shear plus out-of-plane normal/bending stress. Format '(0 mm,0 mm)-(100 mm,0 mm); ...'. Uses throat=0.707*leg_size. Reports chosen point or worst endpoint. Static elastic line method; no weld fatigue.")
def weld(
    segments_text: Annotated[str, Field(description="1..50 weld lines, e.g. '(0 mm,0 mm)-(100 mm,0 mm); (0 mm,60 mm)-(100 mm,60 mm)'. Every coordinate needs units.")],
    leg_size: Q, force_x: Q = "0 N", force_y: Q = "0 N", force_z: Q = "0 N",
    load_x: Q = "0 mm", load_y: Q = "0 mm", load_z: Q = "0 mm",
    point_x: Q = "", point_y: Q = "", allowable_shear: Q = "", unit_system: System = "metric",
) -> ToolResult:
    inputs = dict(locals())

    def compute():
        r = Result(unit_system, {
            "throat": "a=0.707*h; Aw=a*sum(L)",
            "line_integrals": "Ix=sum(L*(yc^2+dy^2/12)); Iy=sum(L*(xc^2+dx^2/12)); Ixy=sum(L*(xc*yc+dx*dy/12)); Ju=Ix+Iy; Iu tensor=[[Iy,Ixy],[Ixy,Ix]]",
            "tractions": "tau_x=Fx/Aw-Mz*y/(a*Ju); tau_y=Fy/Aw+Mz*x/(a*Ju); sigma_z=Fz/Aw+c_x*x+c_y*y; a*[[Iy,Ixy],[Ixy,Ix]]*[c_x,c_y]=[-My,Mx]",
            "resultant": "sqrt(tau_x^2+tau_y^2+sigma_z^2); safety=allowable_shear/resultant (conservative traction norm)",
        }, "Uniform throat, rigid connected parts, small elastic deformation. Coordinates in the weld plane; z normal. Positive applied forces and moments follow the right-hand rule. Normal stress is reported separately; allowable shear is compared conservatively with the total traction norm. Endpoints give the exact maximum norm of this affine traction field on each segment. No local notch stress or weld fatigue.",
                   "1..50 nonzero segments, no collinear overlaps; dimensions with units on every coordinate. Out-of-plane bending must be supportable by the line-pattern inertia tensor. This is a line model, not a weld design code check.")
        lines = segments(segments_text)
        h = q(leg_size, "leg_size", "length")
        throat = .707*h
        forces = [q(raw, name, "force", signed=True) for raw, name in ((force_x, "force_x"), (force_y, "force_y"), (force_z, "force_z"))]
        fx, fy, fz = forces
        if not any(forces):
            invalid("force_x", "at least one force must be nonzero")
        lx, ly, lz = [q(raw, name, "length", signed=True) for raw, name in ((load_x, "load_x"), (load_y, "load_y"), (load_z, "load_z"))]
        px = q(point_x, "point_x", "length", signed=True, optional=True)
        py = q(point_y, "point_y", "length", signed=True, optional=True)
        paired(px, py, "point_y")
        allow = q(allowable_shear, "allowable_shear", "stress", optional=True)
        total = sum(s[4] for s in lines)
        # Subtract a reference before taking moments to retain translation accuracy.
        ox, oy = lines[0][:2]
        cx = ox+sum(l*((x1-ox)+(x2-ox))/2 for x1, y1, x2, y2, l in lines)/total
        cy = oy+sum(l*((y1-oy)+(y2-oy))/2 for x1, y1, x2, y2, l in lines)/total
        ix = iy = ixy = 0.0
        for x1, y1, x2, y2, l in lines:
            x, y = ((x1-cx)+(x2-cx))/2, ((y1-cy)+(y2-cy))/2
            dx, dy = x2-x1, y2-y1
            ix += l*(y*y+dy*dy/12)
            iy += l*(x*x+dx*dx/12)
            ixy += l*(x*y+dx*dy/12)
        ju, area = ix+iy, throat*total
        mx, my, mz = (ly-cy)*fz-lz*fy, lz*fx-(lx-cx)*fz, (lx-cx)*fy-(ly-cy)*fx
        tx, ty = -my/throat, mx/throat
        # Solve the bending traction in the principal frame, where the inertia tensor is diagonal; the two
        # principal moments are summed directly (all terms positive) so a thin pattern keeps its weak axis.
        th = .5*math.atan2(2*ixy, iy-ix)
        c, sn = math.cos(th), math.sin(th)
        iu = iv = 0.0
        extent = 0.0
        for x1, y1, x2, y2, l in lines:
            u1, v1 = (x1-cx)*c+(y1-cy)*sn, -(x1-cx)*sn+(y1-cy)*c
            u2, v2 = (x2-cx)*c+(y2-cy)*sn, -(x2-cx)*sn+(y2-cy)*c
            iu += l*(((u1+u2)/2)**2+(u2-u1)**2/12)
            iv += l*(((v1+v2)/2)**2+(v2-v1)**2/12)
            extent = max(extent, math.hypot(u1, v1), math.hypot(u2, v2))
        floor = (1e-12*extent)**2*total
        tu, tv = tx*c+ty*sn, -tx*sn+ty*c
        scale = max(math.hypot(tx, ty), 1e-300)
        bu = bv = 0.0
        for t_, i_, name in ((tu, iu, "u"), (tv, iv, "v")):
            if i_ > floor:
                if name == "u":
                    bu = t_/i_
                else:
                    bv = t_/i_
            elif abs(t_) > 1e-9*scale:
                invalid("segments_text", "line pattern cannot support this out-of-plane bending moment")
        bx, by = bu*c-bv*sn, bu*sn+bv*c
        def traction(x, y):
            dx, dy = x-cx, y-cy
            sx, sy = fx/area-mz*dy/(throat*ju), fy/area+mz*dx/(throat*ju)
            sz = fz/area+bx*dx+by*dy
            return math.sqrt(sx*sx+sy*sy+sz*sz), sx, sy, sz
        ends = [(x, y) for x1, y1, x2, y2, _ in lines for x, y in ((x1, y1), (x2, y2))]
        worst = max(ends, key=lambda xy: traction(*xy)[0])
        point = worst if px is None else (px, py)
        result, sx, sy, sz = traction(*point)
        for name, value in (("throat", throat), ("total_length", total), ("centroid_x", cx), ("centroid_y", cy),
                            ("point_x", point[0]), ("point_y", point[1]), ("worst_x", worst[0]), ("worst_y", worst[1])):
            r.add(name, value, "length")
        r.add("throat_area", area, "area")
        for name, value in (("ix_unit", ix), ("iy_unit", iy), ("ixy_unit", ixy), ("polar_unit", ju)):
            r.add(name, value, "line_inertia")
        for name, value in (("moment_x", mx), ("moment_y", my), ("moment_z", mz)):
            r.add(name, value, "moment")
        for name, value in (("direct_shear_x", fx/area), ("direct_shear_y", fy/area),
                            ("torsional_shear_x", -mz*(point[1]-cy)/(throat*ju)),
                            ("torsional_shear_y", mz*(point[0]-cx)/(throat*ju)),
                            ("shear_x", sx), ("shear_y", sy), ("normal_stress", sz),
                            ("resultant", result), ("worst_resultant", traction(*worst)[0])):
            r.add(name, value, "stress")
        r.safety("safety_factor", allow, result)
        r.safety("group_safety_factor", allow, traction(*worst)[0])
        r.data["I_u"] = {"xx": "iy_unit", "yy": "ix_unit", "xy": "ixy_unit"}
        if h > total/10:
            r.warnings.append("Leg size is large relative to total line length; thin line approximation needs review.")
        return r.done("resultant")
    return run("weld", inputs, compute)
