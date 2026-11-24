"""Damped Newton on node heads with monotone link inversions."""

import math
from dataclasses import dataclass
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit.units import UnitsError

from ._common import Density, G, Kinematic, Result, Viscosity, bisect, choice, count, csv_text, fields, fluid, identifier, invalid, pipe_loss, q, records, run, scalar
from ._model import Curve, curve
from ._server import server

HW_SI = 4.727 * .3048**(4.871 - 3*1.852)


@dataclass
class Link:
    name: str
    i: int
    j: int
    length: float = 0.
    diameter: float = 0.
    roughness: float = 0.
    minor: float = 0.
    closed: bool = False
    pump: bool = False
    h0: float = 0.
    a: float = 0.
    b: float = 0.
    check: bool = True
    points: Curve | None = None


def connected(n, links):
    adjacency = [set() for _ in range(n)]
    for edge in links:
        if not edge.closed:
            adjacency[edge.i].add(edge.j)
            adjacency[edge.j].add(edge.i)
    seen, pending = {0}, [0]
    while pending:
        for j in adjacency[pending.pop()]:
            if j not in seen:
                seen.add(j)
                pending.append(j)
    if len(seen) != n:
        invalid("nodes", "network is disconnected after closed valves are removed")


def link_flow(edge, dh, model, rho, mu):
    """Return Q and dQ/d(head_i-head_j); no iteration outside bounded roots."""
    if edge.closed:
        return 0., 0.
    if edge.pump:
        target = dh+edge.h0
        if target <= 0:
            return 0., 0.
        if edge.points is not None:
            c = edge.points
            required = -dh
            # Extend the final segment only for Newton trials; final results never extrapolate.
            index = next((k for k in range(len(c.heads)-1) if required >= c.heads[k+1]), len(c.heads)-2)
            slope = (c.flows[index+1]-c.flows[index])/(c.heads[index]-c.heads[index+1])
            return c.flows[index]+(c.heads[index]-required)*slope, slope
        if edge.b:
            flow = 2*target/(edge.a+math.sqrt(edge.a*edge.a+4*edge.b*target))
        else:
            flow = target/edge.a
        if flow > 1e6:
            invalid("pumps", "pump flow exceeds 1e6 m^3/s", "input.out_of_range")
        return flow, 1/max(edge.a+2*edge.b*flow, 1e-12)
    a = math.pi*edge.diameter**2/4
    if model == "darcy_weisbach":
        def loss(flow):
            return pipe_loss(flow, edge.length, edge.diameter, edge.roughness, edge.minor, rho, mu)[0]
        linear = 128*mu*edge.length/(rho*G*math.pi*edge.diameter**4)
        if dh == 0:
            return 0., 1/linear
        maximum_flow = min(1e6, 1e12*mu*a/(rho*edge.diameter))
        hi = min(maximum_flow, max(1e-15, a*math.sqrt(abs(dh)*2*G/(.02*edge.length/edge.diameter+edge.minor))))
        for _ in range(60):
            if loss(hi) >= abs(dh):
                break
            if hi == maximum_flow:
                invalid("pipes", "required flow exceeds Reynolds/flow limit", "input.out_of_range")
            hi = min(maximum_flow, hi*2)
        else:
            invalid("pipes", "could not bracket pipe flow", "input.nonconvergence")
        flow = bisect(lambda x: loss(x)-abs(dh), 0., hi, "pipes", atol=0.)
        if abs(loss(flow)-abs(dh)) > 1e-10*abs(dh):
            invalid("pipes", "pipe-flow inversion cannot resolve the head loss", "input.out_of_range")
        step = flow*1e-5
        low = max(0., flow-step)
        slope = (loss(flow+step)-loss(low))/(flow+step-low)
    else:
        resistance = HW_SI*edge.length/(edge.roughness**1.852*edge.diameter**4.871)
        minor = edge.minor/(2*G*a*a)
        if dh == 0:
            # Finite numerical derivative at the power-law origin, with exactly zero flow.
            return 0., 1/(1.852*resistance*1e-10**.852+2*minor*1e-10)
        hi = (abs(dh)/resistance)**(1/1.852)
        flow = hi if minor == 0 else bisect(lambda x: resistance*x**1.852+minor*x*x-abs(dh), 0., hi, "pipes", atol=0.)
        slope = 1.852*resistance*flow**.852+2*minor*flow
    return math.copysign(flow, dh), 1/slope


def add_system_curve(handle, r, links, demands, fixed, solve, ids, rows_wanted):
    """system_curve.csv for a network with exactly one pump: the head the rest of the network needs at each flow, next to the pump curve.

    The pump is taken out and its flow Q is prescribed instead (a demand of Q at its suction node, an inflow of Q at its discharge node);
    the solved head difference between the two nodes is the head the pump has to deliver, the system head at Q.  The pump curve is the
    model's own.  Where a flow cannot be solved the table stops there and says so.
    """
    pumps = [edge for edge in links if edge.pump]
    if len(pumps) != 1:
        r.warnings.append(f"curve_points: a system curve needs exactly one pump; this network has {len(pumps)}, no table was written")
        return
    pump = pumps[0]
    rest = [edge for edge in links if edge is not pump]
    if not rest:
        r.warnings.append("curve_points: the pump is the only link, so there is no system to draw a curve for")
        return
    if pump.points is not None:
        flow_end = pump.points.flows[-1]
        pump_head = pump.points.head
    else:
        flow_end = 2*pump.h0/(pump.a+math.sqrt(pump.a*pump.a+4*pump.b*pump.h0)) if pump.b else pump.h0/pump.a

        def pump_head(flow):
            return pump.h0-pump.a*flow-pump.b*flow*flow
    record = next(p for p in r.data["pumps"] if p["id"] == pump.name)
    flow_op, head_op = record["flow_m3_s"], record["head_gain_m"]

    def system_head(flow):
        shifted = list(demands)
        shifted[pump.i] += flow
        shifted[pump.j] -= flow
        heads = solve(rest, shifted)[0]
        return float(heads[pump.j]-heads[pump.i])

    grid = [flow_end*i/(rows_wanted-1) for i in range(rows_wanted)]
    if 0 < flow_op < flow_end and all(abs(flow_op-g) > 1e-12*flow_end for g in grid):
        grid = sorted([*grid, flow_op])
    rows = []
    for flow in grid:
        try:
            system = system_head(flow)
        except (UnitsError, ArithmeticError, ValueError) as exc:
            r.warnings.append(f"curve_points: the table stops at {flow:.6g} m^3/s, where the network cannot be solved ({getattr(exc, 'message', str(exc))})")
            break
        rows.append([flow, system, pump_head(flow), 1 if abs(flow-flow_op) <= 1e-12*flow_end else 0])
    if not rows:
        return
    r.artifacts.append(handle.write_text("system_curve.csv", csv_text(["flow_m3_s", "system_head_m", "pump_head_m", "is_operating_point"], rows), fmt="csv"))
    r.data["operating_point"] = {"flow_m3_s": flow_op, "head_m": head_op, "system_head_m": system_head(flow_op) if flow_op <= flow_end else None, "pump_head_m": pump_head(flow_op) if flow_op <= flow_end else None}
    r.data["curve_table"] = {"artifact": "system_curve.csv", "rows": len(rows), "flow_max_m3_s": flow_end, "pump": pump.name,
                             "columns": ["flow_m3_s", "system_head_m", "pump_head_m", "is_operating_point"],
                             "note": "system head = head difference the rest of the network needs between the pump's two nodes at that flow (pump removed, its flow prescribed); "
                                     "pump head = the pump curve; they cross at the operating point; no efficiency column: pipe_network has no efficiency data (use pump_system)"}


@server.tool(description="Steady incompressible looped pipe network by damped node-head Newton, Colebrook-White Darcy or Hazen-Williams. Fixed heads, demands, pumps/check valves and pipe valves. Smooth Re 2000..4000 blend. <=200 nodes, 400 pipes, 40 pumps, 100 iterations; convergence requires mass residual and head change. curve_points (one pump): system_curve.csv, system head vs pump head over flow.")
def pipe_network(
    nodes: Annotated[str, Field(description="id,elevation,demand|head,value, e.g. 'r,0 m,head,20 m;j,0 m,demand,1 L/s'; <=200 nodes, fixed head required.")] = "",
    pipes: Annotated[str, Field(description="id,start,end,L,D,roughness_or_C,K, e.g. 'p,r,j,10 m,50 mm,0 mm,0'; <=400 pipes; C is a bare coefficient.")] = "",
    headloss_model: Annotated[str, Field(description="darcy_weisbach or hazen_williams; Hazen-Williams is restricted to turbulent water, 5..25 C caller assumption.")] = "darcy_weisbach",
    pumps: Annotated[str, Field(description="<=40 'id,i,j,poly,H0,a,b,yes|no' or 'id,i,j,points,Q:H|Q:H,yes|no'; units required; final field is check valve.")] = "",
    valves: Annotated[str, Field(description="Pipe valve records 'pipe_id,open|closed,K', e.g. 'p,open,2'; <=400; added minor K, '' omits valves.")] = "",
    vapour_pressure: Annotated[str, Field(description="Absolute fluid vapour pressure, e.g. '2.34 kPa'; '' omits absolute-pressure cavitation comparison.")] = "",
    atmospheric_pressure: Annotated[str, Field(description="Absolute atmospheric pressure for gauge-head conversion, e.g. '101325 Pa'; used with vapour_pressure.")] = "101325 Pa",
    max_iterations: Annotated[int, Field(description="Newton iteration cap, 1..100; default 100; unconverged calls return an error.")] = 100,
    density: Density = "1000 kg/m^3",
    dynamic_viscosity: Viscosity = "1 mPa*s",
    kinematic_viscosity: Kinematic = "",
    curve_points: Annotated[int, Field(description="0 (default) none; 2..200: with exactly one pump, rows of system_curve.csv over its flow range (operating point inserted).")] = 0,
):
    inputs = dict(locals())

    def compute(handle):
        rho, mu = fluid(density, dynamic_viscosity, kinematic_viscosity)
        model = choice(headloss_model, "headloss_model", ("darcy_weisbach", "hazen_williams"))
        limit = count(max_iterations, "max_iterations", 1, 100)
        rows_wanted = 0 if (curve_points == 0 and curve_points is not False) else count(curve_points, "curve_points", 2, 200)
        if model == "hazen_williams" and not (995 <= rho <= 1001 and .00089 <= mu <= .00152):
            invalid("headloss_model", "Hazen-Williams requires caller water properties within the documented 5..25 C envelope")
        ids, elevation, demands, fixed = [], [], [], {}
        for k, row in enumerate(records(nodes, "nodes", 200, required=True)):
            path = f"nodes/{k}"
            fields(row, (4,), path)
            name = identifier(row[0], path+"/0")
            if name in ids:
                invalid(path, "duplicate node id")
            ids.append(name)
            elevation.append(q(row[1], path+"/1", "length", signed=True))
            choice(row[2], path+"/2", ("head", "demand"))
            if row[2] == "head":
                fixed[k] = q(row[3], path+"/3", "length", signed=True)
                demands.append(0.)
            else:
                demands.append(q(row[3], path+"/3", "flow", signed=True))
        if not fixed:
            invalid("nodes", "at least one fixed-head node is required")
        index = {name: k for k, name in enumerate(ids)}
        links, names = [], set()

        def ends(row, path):
            name = identifier(row[0], path+"/0")
            if name in names:
                invalid(path, "duplicate link id")
            names.add(name)
            if row[1] not in index or row[2] not in index or row[1] == row[2]:
                invalid(path, "links require two different existing nodes")
            return name, index[row[1]], index[row[2]]

        for k, row in enumerate(records(pipes, "pipes", 400)):
            path = f"pipes/{k}"
            fields(row, (7,), path)
            name, i, j = ends(row, path)
            l = q(row[3], path+"/3", "length")
            d = q(row[4], path+"/4", "length")
            rough = q(row[5], path+"/5", "length", zero=True) if model == "darcy_weisbach" else scalar(row[5], path+"/5", 20, 200)
            if model == "darcy_weisbach" and rough/d > .05:
                invalid(path+"/5", "roughness/diameter exceeds 0.05")
            links.append(Link(name, i, j, l, d, rough, scalar(row[6], path+"/6")))
        pipe_by_id = {edge.name: edge for edge in links}
        seen_valves = set()
        for k, row in enumerate(records(valves, "valves", 400)):
            path = f"valves/{k}"
            fields(row, (3,), path)
            if row[0] not in pipe_by_id or row[0] in seen_valves:
                invalid(path, "valve must reference a pipe once")
            seen_valves.add(row[0])
            edge = pipe_by_id[row[0]]
            edge.closed = choice(row[1], path+"/1", ("open", "closed")) == "closed"
            edge.minor += scalar(row[2], path+"/2")
        for k, row in enumerate(records(pumps, "pumps", 40)):
            path = f"pumps/{k}"
            fields(row, (6, 8), path)
            name, i, j = ends(row, path)
            edge = Link(name, i, j, pump=True)
            choice(row[3], path+"/3", ("poly", "points"))
            if row[3] == "poly":
                fields(row, (8,), path)
                edge.h0 = q(row[4], path+"/4", "length")
                edge.a = q(row[5], path+"/5", "a", zero=True)
                edge.b = q(row[6], path+"/6", "b", zero=True)
                if edge.a == edge.b == 0:
                    invalid(path, "pump curve must decrease with forward flow")
            else:
                fields(row, (6,), path)
                pieces = row[4].split("|")
                if len(pieces) > 20:
                    invalid(path+"/4", "at most 20 points per network pump", "input.out_of_range")
                points = []
                for p, piece in enumerate(pieces):
                    pair = piece.split(":")
                    fields(pair, (2,), path+f"/4/{p}")
                    points.append((q(pair[0], path+f"/4/{p}/0", "flow", zero=True), q(pair[1], path+f"/4/{p}/1", "length", zero=True), 1.))
                edge.points = curve(points, path+"/4")
                edge.h0 = edge.points.heads[0]
            edge.check = choice(row[-1], path+"/check", ("yes", "no")) == "yes"
            links.append(edge)
        if not links:
            invalid("pipes", "at least one pipe or pump is required")
        connected(len(ids), links)
        unknown = [i for i in range(len(ids)) if i not in fixed]

        def solve(link_set, demand_values):
            """Damped Newton on the node heads of ``link_set`` with the prescribed demands; returns the converged state or raises."""
            incidence = np.zeros((len(ids), len(link_set)))
            for k, edge in enumerate(link_set):
                incidence[edge.i, k], incidence[edge.j, k] = 1., -1.
            b = incidence[unknown, :]
            h = np.full(len(ids), sum(fixed.values())/len(fixed))
            for i, value in fixed.items():
                h[i] = value
            demand = np.array(demand_values)[unknown]
            demand_scale = float(np.sum(np.abs(demand)))

            def mass_tolerance(flows):
                return 1e-9*max(demand_scale, float(np.max(np.abs(flows))))

            def evaluate(heads):
                pairs = [link_flow(edge, heads[edge.i]-heads[edge.j], model, rho, mu) for edge in link_set]
                flows = np.array([p[0] for p in pairs])
                slopes = np.array([p[1] for p in pairs])
                return flows, slopes, b@flows+demand

            def anchored(slopes):
                adjacency = [set() for _ in ids]
                for edge, slope in zip(link_set, slopes):
                    if slope > 0:
                        adjacency[edge.i].add(edge.j)
                        adjacency[edge.j].add(edge.i)
                seen, pending = set(fixed), list(fixed)
                while pending:
                    for j in adjacency[pending.pop()]:
                        if j not in seen:
                            seen.add(j)
                            pending.append(j)
                return len(seen) == len(ids)

            last_change = 0.
            for iteration in range(1, limit+1):
                flows, slopes, residual = evaluate(h)
                if not anchored(slopes):
                    invalid("pumps", "closed check valves leave an unanchored head component")
                norm = float(np.max(np.abs(residual))) if unknown else 0.
                tolerance = mass_tolerance(flows)
                if norm <= tolerance and last_change <= 1e-8*max(1., float(np.max(np.abs(h)))):
                    break
                jac = (b*slopes)@b.T
                diag = np.diag(jac)
                if np.any(diag <= 0):
                    invalid("pumps", "closed check valves isolate a demand node; no supported forward-flow solution")
                scale = np.sqrt(diag)
                step = np.linalg.solve(jac/scale[:, None]/scale[None, :], -residual/scale)/scale
                if not np.all(np.isfinite(step)):
                    invalid("nodes", "node-head system is numerically indeterminate", "input.out_of_range")
                for backtrack in range(25):
                    fraction = .5**backtrack
                    trial = h.copy()
                    trial[unknown] += fraction*step
                    trial_flows, trial_slopes, trial_residual = evaluate(trial)
                    trial_norm = float(np.max(np.abs(trial_residual)))
                    if anchored(trial_slopes) and (trial_norm < norm*(1-1e-4*fraction) or trial_norm <= mass_tolerance(trial_flows)):
                        h = trial
                        last_change = float(np.max(np.abs(fraction*step)))
                        break
                else:
                    invalid("nodes", "node-head line search cannot resolve the mass residual", "input.out_of_range")
            else:
                invalid("max_iterations", "network failed mass/head convergence within the iteration cap", "input.nonconvergence")
            flows, _, residual = evaluate(h)
            norm = float(np.max(np.abs(residual))) if unknown else 0.
            tolerance = mass_tolerance(flows)
            if norm > tolerance:
                invalid("nodes", "final network mass residual exceeds the flow-scaled tolerance", "input.out_of_range")
            if np.max(np.abs(h)) > 1e6 or np.max(np.abs(flows)) > 1e6:
                invalid("nodes", "solved head or flow exceeds 1e6 in SI units", "input.out_of_range")
            return h, flows, norm, tolerance, last_change, iteration, incidence

        h, flows, norm, tolerance, last_change, iteration, incidence = solve(links, demands)
        r = Result({"network": "B Q(H)+d=0; J=B diag(dQ/dDeltaH) B^T; scaled numpy solve and damped Newton",
                    "Darcy-Weisbach": "h=(f L/D+K) v|v|/(2g); Colebrook 1/sqrt(f)=-2 log10(eps/(3.7D)+2.51/(Re sqrt(f)))",
                    "Hazen-Williams": "h=kSI L Q|Q|^0.852/(C^1.852 D^4.871)+K v|v|/(2g); kSI=4.727*0.3048^(4.871-3*1.852)",
                    "pump": "H=H0-aQ-bQ^2 or monotone linear interpolation; Q>=0; closed check valve blocks reverse flow"},
                   "Steady incompressible Newtonian liquid; demands are prescribed outflows, reservoirs supply imbalance. Gauge heads. No pressure-dependent demand.",
                   "Darcy Re<=1e12, eps/D<=0.05; laminar <2000, smoothstep blend 2000..4000. Hazen-Williams: turbulent water only, Re>=4000, C=20..200.")
        mass = incidence@flows+np.array(demands)
        pv = q(vapour_pressure, "vapour_pressure", "pressure", zero=True, optional=True)
        patm = q(atmospheric_pressure, "atmospheric_pressure", "pressure")
        r.data["nodes"] = []
        for i, name in enumerate(ids):
            pressure = rho*G*(h[i]-elevation[i])
            r.data["nodes"].append({"id": name, "head_m": h[i], "gauge_pressure_Pa": pressure,
                                    "demand_m3_s": demands[i], "reservoir_supply_m3_s": mass[i] if i in fixed else None,
                                    "mass_residual_m3_s": 0. if i in fixed else mass[i]})
            if pressure < 0:
                r.warnings.append(f"Node {name}: negative gauge pressure.")
            if pv is not None and pressure+patm <= pv:
                r.warnings.append(f"Node {name}: absolute pressure at/below vapour pressure; cavitation risk.")
        r.data["pipes"], r.data["pumps"] = [], []
        for edge, flow in zip(links, flows):
            if edge.pump:
                if flow == 0 and not edge.check:
                    invalid("pumps", "reverse/stalled operation without a check valve is outside the pump model")
                if edge.points is not None and flow > edge.points.flows[-1]*(1+1e-10):
                    invalid("pumps", "operating flow lies outside supplied pump points")
                if flow > 0 and h[edge.j] < h[edge.i]:
                    invalid("pumps", "pump operating point exceeds the positive-head curve range")
                r.data["pumps"].append({"id": edge.name, "flow_m3_s": flow, "head_gain_m": h[edge.j]-h[edge.i], "check_valve_closed": flow == 0})
                continue
            velocity = flow/(math.pi*edge.diameter**2/4)
            re = rho*abs(velocity)*edge.diameter/mu
            f = pipe_loss(flow, edge.length, edge.diameter, edge.roughness, edge.minor, rho, mu)[3] if model == "darcy_weisbach" else None
            if model == "hazen_williams" and 0 < re < 4000:
                invalid("headloss_model", "Hazen-Williams solution is outside turbulent validity (Re<4000)")
            r.data["pipes"].append({"id": edge.name, "flow_m3_s": flow, "velocity_m_s": velocity, "reynolds_number": re,
                                    "headloss_m": h[edge.i]-h[edge.j] if not edge.closed else None, "darcy_factor": f, "closed": edge.closed})
            if abs(velocity) > 3:
                r.warnings.append(f"Pipe {edge.name}: velocity exceeds the conventional 3 m/s guideline, not a universal limit.")
            if flow < 0:
                r.warnings.append(f"Pipe {edge.name}: flow reverses the record's orientation.")
            if model == "darcy_weisbach" and 2000 <= re < 4000:
                r.warnings.append(f"Pipe {edge.name}: friction uses the documented transition blend.")
        if rows_wanted:
            add_system_curve(handle, r, links, demands, fixed, solve, ids, rows_wanted)
        r.add("mass_balance_residual", norm, "m^3/s")
        r.add("head_change", last_change, "m")
        r.add("iterations", iteration)
        r.data["mass_tolerance_m3_s"] = tolerance
        return r.done("mass_balance_residual")

    return run("pipe_network", inputs, compute)
