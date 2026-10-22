"""Finite Euler-Bernoulli beam on a bilateral, linear Winkler foundation."""

from dataclasses import dataclass
import math
from typing import Annotated

import numpy as np
from numpy.polynomial import polynomial as poly
from pydantic import Field

from eng_toolkit.domains.structures3d._common import Result, System, choice, count, csv_text, fields, invalid, number, q, records, run
from eng_toolkit.domains.structures3d._server import server
from eng_toolkit.domains.structures3d._model import accurate_residual


@dataclass(frozen=True)
class _Beam:
    x: np.ndarray
    displacement: np.ndarray
    reaction: np.ndarray
    coefficients: tuple[tuple[np.ndarray, ...], ...]
    residual: float


def _mesh(length, elements, points):
    required = np.array(sorted({0., length, *(x for x, _, _ in points)}))
    spans = np.diff(required)
    if len(spans) > elements:
        invalid("elements", f"at least {len(spans)} elements are needed to include every point-load location")
    if np.min(spans) < length*1e-9:
        invalid("point_loads", "distinct load locations and ends must be separated by at least 1e-9 times the beam length")
    shares = (elements-len(spans))*spans/length
    divisions = 1+np.floor(shares).astype(int)
    order = np.argsort(-(shares-np.floor(shares)), kind="stable")
    divisions[order[:elements-int(sum(divisions))]] += 1
    return np.concatenate([np.linspace(a, b, int(n)+1)[:-1] for a, b, n in zip(required[:-1], required[1:], divisions)]+[np.array([length])])


def _element(length, rigidity, stiffness, load):
    h = length
    bending = rigidity/h**3*np.array([[12, 6*h, -12, 6*h], [6*h, 4*h*h, -6*h, 2*h*h],
                                     [-12, -6*h, 12, -6*h], [6*h, 2*h*h, -6*h, 4*h*h]])
    foundation = stiffness*h/420*np.array([[156, 22*h, 54, -13*h], [22*h, 4*h*h, 13*h, -3*h*h],
                                           [54, 13*h, 156, -22*h], [-13*h, -3*h*h, -22*h, 4*h*h]])
    force = load*np.array([h/2, h*h/12, h/2, -h*h/12])
    return bending+foundation, force


def _solve(length, rigidity, stiffness, load, points, left, right, elements):
    x = _mesh(length, elements, points)
    # Remove the exact free/free uniform particular solution before assembly.
    # Otherwise bending roundoff creates forces in an exactly rigid translation.
    baseline = load/stiffness if left == right == "free" else 0.
    remaining_load = 0. if left == right == "free" else load
    size = 2*len(x)
    matrix, force = np.zeros((size, size)), np.zeros(size)
    operators = []
    for i, h in enumerate(np.diff(x)):
        ke, fe = _element(h, rigidity, stiffness, remaining_load)
        dofs = slice(2*i, 2*i+4)
        matrix[dofs, dofs] += ke
        force[dofs] += fe
        operators.append((ke, fe))
    for location, p, moment in points:
        node = int(np.argmin(abs(x-location)))
        force[2*node:2*node+2] += (p, moment)
    fixed = []
    for end, kind in ((0, left), (size-2, right)):
        if kind != "free":
            fixed.append(end)
        if kind == "fixed":
            fixed.append(end+1)
    free = np.array([i for i in range(size) if i not in fixed], dtype=int)
    if not len(free):
        invalid("elements", "beam mesh has no free DOFs; increase elements", "input.out_of_range")
    u = np.zeros(size)
    if len(free):
        kf = matrix[np.ix_(free, free)]
        scale = 1/np.sqrt(np.diag(kf))
        scaled = scale[:, None]*kf*scale[None, :]
        # Diagonal equilibration makes the conditioning check independent of units.
        eigenvalues = np.linalg.eigvalsh(scaled)
        if eigenvalues[0] <= eigenvalues[-1]*2e-13:
            invalid("inputs", "beam stiffness is too ill-conditioned for this mesh and foundation contrast", "input.out_of_range")
        factor = np.linalg.cholesky(scaled)
        u[free] = scale*np.linalg.solve(factor.T, np.linalg.solve(factor, scale*force[free]))
        for _ in range(2):
            correction = accurate_residual(kf, u[free], force[free])
            u[free] += scale*np.linalg.solve(factor.T, np.linalg.solve(factor, scale*correction))
    residual = matrix@u-force
    denominator = np.linalg.norm(matrix, np.inf)*np.linalg.norm(u, np.inf)+np.linalg.norm(force, np.inf)
    relative = float(np.max(abs(residual[free]))/denominator) if len(free) and denominator else 0.
    if relative > 1e-9:
        invalid("inputs", "beam solution residual exceeds the supported accuracy", "input.out_of_range")
    reaction = np.zeros(size)
    reaction[fixed] = residual[fixed]
    coefficients = []
    for i, h in enumerate(np.diff(x)):
        wi, ti, wj, tj = u[2*i:2*i+4]
        w = np.array([wi, h*ti, -3*wi-2*h*ti+3*wj-h*tj, 2*wi+h*ti-2*wj+h*tj])
        theta = poly.polyder(w)/h
        traction = operators[i][0]@u[2*i:2*i+4]-operators[i][1]
        net = stiffness*w
        net[0] -= remaining_load
        shear = h*poly.polyint(net)
        shear[0] = -traction[0]
        moment = h*poly.polyint(shear)
        moment[0] = traction[1]
        w[0] += baseline
        coefficients.append((w, theta, moment, shear, -stiffness*w))
    u[::2] += baseline
    return _Beam(x, u, reaction, tuple(coefficients), relative)


def _roots(coefficients):
    derivative = poly.polyder(coefficients)
    if not np.any(derivative):
        return []
    derivative = np.trim_zeros(derivative, "b")
    roots = poly.polyroots(derivative/np.max(abs(derivative)))
    return [float(z.real) for z in roots if abs(z.imag) < 1e-9 and 0 < z.real < 1]


def _value(beam, location, side="right"):
    i = int(np.searchsorted(beam.x, location, side=side))-1
    i = min(max(i, 0), len(beam.coefficients)-1)
    t = (location-beam.x[i])/(beam.x[i+1]-beam.x[i])
    return np.array([poly.polyval(t, c) for c in beam.coefficients[i]])


def _extrema(beam):
    maxima = [(0., 0., "right") for _ in range(5)]
    for i, coefficients in enumerate(beam.coefficients):
        h = beam.x[i+1]-beam.x[i]
        for j, c in enumerate(coefficients):
            for t in (0., 1., *_roots(c)):
                value = abs(float(poly.polyval(t, c)))
                if value > maxima[j][0]:
                    maxima[j] = (value, float(beam.x[i]+h*t), "left" if t == 1 else "right")
    return maxima


def _convergence(beam, other, length, points, tolerance):
    samples = {(float(x), "right") for x in np.linspace(0, length, 201)}
    samples |= {(x, side) for x, _, _ in points for side in ("left", "right")}
    a = np.array([_value(beam, x, side) for x, side in sorted(samples)])
    b = np.array([_value(other, x, side) for x, side in sorted(samples)])
    changes = np.max(abs(a-b), axis=0)
    norms = np.maximum(np.max(abs(a), axis=0), np.max(abs(b), axis=0))
    # Moment and shear can vanish identically, e.g. free ends under uniform load.
    # Compare their changes to load-resultant scales when cancellation dominates.
    displacement_scale = max(norms[0], norms[1]*length)
    floors = np.array([displacement_scale, displacement_scale/length, norms[4]*length**2, norms[4]*length, norms[4]])*1e-8
    ratios = np.divide(changes, np.maximum(norms, floors), out=np.zeros(5), where=np.maximum(norms, floors) > 0)
    return {"comparison_elements": len(other.x)-1, "tolerance": tolerance,
            "relative_changes": dict(zip(("deflection", "slope", "moment", "shear", "soil_reaction"), ratios.tolist())),
            "converged": bool(np.max(ratios) <= tolerance),
            "method": "Two meshes, at most 200 elements each; compare 201 common stations and both sides of point loads. Relative changes use a 1e-8 load-scale floor for near-zero fields; this is an estimate, not an error bound."}


@server.tool(description="Finite constant-EI Euler-Bernoulli beam on a linear Winkler foundation. Free/pinned/fixed ends, uniform load and point forces/moments, consistent Hermite foundation stiffness, equilibrium M/V, CSV and bounded mesh convergence. Bilateral springs carry tension; no uplift/contact or nonlinear soil.")
def foundation(
    length: Annotated[str, Field(description="Beam span with units, e.g. '10 m'.")] = "",
    flexural_rigidity: Annotated[str, Field(description="Constant EI with units, e.g. '2e7 N*m^2'.")] = "",
    foundation_stiffness: Annotated[str, Field(description="Positive spring stiffness per beam length, e.g. '1e6 N/m^2'; include tributary width.")] = "",
    left_end: Annotated[str, Field(description="Left end: free, pinned or fixed; zero prescribed movement.")] = "free",
    right_end: Annotated[str, Field(description="Right end: free, pinned or fixed; zero prescribed movement.")] = "free",
    uniform_load: Annotated[str, Field(description="Signed constant transverse load, e.g. '10 kN/m'; positive along deflection.")] = "0 N/m",
    point_loads: Annotated[str, Field(description="Records 'x,force,moment', e.g. '5 m,20 kN,0 N*m'; moment acts along positive slope; at most 100; '' omits.")] = "",
    elements: Annotated[int, Field(description="Initial Hermite elements, 1..200; include all point locations; doubles automatically up to 200 for convergence.")] = 40,
    stations: Annotated[int, Field(description="Uniform table stations, 2..2001; mesh ends and FE extrema are added.")] = 201,
    convergence_tolerance: Annotated[float, Field(description="Relative two-mesh change threshold, 1e-6..0.2; default 0.01.")] = .01,
    foundation_model: Annotated[str, Field(description="Only 'linear' bilateral Winkler springs are supported.")] = "linear",
    unit_system: System = "metric",
):
    inputs = dict(locals())

    def compute(handle):
        span = q(length, "length", "length")
        ei = q(flexural_rigidity, "flexural_rigidity", "rigidity")
        k = q(foundation_stiffness, "foundation_stiffness", "foundation")
        load = q(uniform_load, "uniform_load", "line_load", signed=True, zero=True)
        left = choice(left_end, "left_end", ("free", "pinned", "fixed"))
        right = choice(right_end, "right_end", ("free", "pinned", "fixed"))
        choice(foundation_model, "foundation_model", ("linear",))
        n = count(elements, "elements", 1, 200)
        samples = count(stations, "stations", 2, 2001)
        tolerance = number(convergence_tolerance, "convergence_tolerance", 1e-6, .2)
        points = []
        for i, row in enumerate(records(point_loads, "point_loads", 100)):
            path = f"point_loads/{i}"
            fields(row, (3,), path)
            location = q(row[0], path+"/0", "length", zero=True)
            if location > span:
                invalid(path+"/0", "point-load location must lie within the beam")
            points.append((location, q(row[1], path+"/1", "force", signed=True, zero=True), q(row[2], path+"/2", "moment", signed=True, zero=True)))
        # A clamped single element has no admissible displacement coordinates.
        if n == 1 and left == right == "fixed":
            n = 2
        beam = _solve(span, ei, k, load, points, left, right, n)
        minimum = len({0., span, *(x for x, _, _ in points)})-1
        comparison_n = min(2*n, 200) if n < 200 else max(100, minimum)
        while True:
            if comparison_n == n:
                invalid("elements", "no second mesh fits within the 200-element convergence cap", "input.out_of_range")
            comparison = _solve(span, ei, k, load, points, left, right, comparison_n)
            if comparison_n > n:
                beam, comparison = comparison, beam
                n, comparison_n = comparison_n, n
            convergence = _convergence(beam, comparison, span, points, tolerance)
            if convergence["converged"]:
                break
            if n == 200:
                invalid("elements", f"mesh did not converge at 200 elements: {convergence['relative_changes']}", "input.out_of_range")
            comparison_n = min(2*n, 200)
        beta = (k/(4*ei))**.25
        beta_length = beta*span
        r = Result(unit_system, {
            "equation": "EI*w''''+k*w=q; soil reaction=-k*w; M=-EI*w''; V=dM/dx; point jumps V_right-V_left=-P and M_right-M_left=C",
            "element": "K=integral(EI*N''^T*N''+k*N^T*N)dx; cubic Hermite N; both integrals exact; uniform load=integral(N^T*q)dx",
            "recovery": "Cubic FE displacement; recover V'=k*w-q and M'=V from element nodal tractions, so end forces and global equilibrium are consistent",
            "beta": "beta=(k/(4*EI))^(1/4); short beta*L<pi/4; medium pi/4<=beta*L<=pi; long beta*L>pi",
            "infinite_point": "w(x)=P*beta/(2*k)*exp(-beta*abs(x-a))*(cos(beta*abs(x-a))+sin(beta*abs(x-a))) for an infinite beam only",
        }, "Prismatic Euler-Bernoulli beam, constant EI and foundation k including tributary width. Positive loads/deflections share a transverse direction; positive moment is conjugate to positive slope. Linear bilateral springs carry both tension and compression and remain attached.",
                   "1..200 finite elements, at most 100 point records, two bounded meshes. Zero end settlements. No shear deformation, axial force, nonlinear soil, uplift, compression-only contact or plasticity. Reported extrema are exact extrema of the FE recovery polynomials, not analytical continuum extrema.")
        maxima = _extrema(beam)
        kinds = ("length", "angle", "moment", "force", "line_load")
        names = ("deflection", "slope", "moment", "shear", "soil_reaction")
        for name, kind, (value, location, side) in zip(names, kinds, maxima):
            r.add("maximum_abs_"+name, value, kind)
            r.data["maximum_abs_"+name+"_location"] = {"x": r.quantity(location, "length"), "side": side, "method": "FE polynomial stationary points and element ends"}
        r.add("beta_length", beta_length)
        r.add("characteristic_length", 1/beta, "length")
        r.data["beta"] = {"quantity": beta*.0254 if r.imperial else beta, "unit": "1 / inch" if r.imperial else "1 / meter"}
        r.data["length_classification"] = "short" if beta_length < math.pi/4 else "long" if beta_length > math.pi else "medium"
        r.warnings.append({
            "short": "Short beam by beta*L<pi/4. End restraints strongly influence response; the finite boundary solution is used.",
            "medium": "Medium beam by pi/4<=beta*L<=pi. Foundation and both end boundaries interact; the finite boundary solution is used.",
            "long": "Long beam by beta*L>pi. Infinite-beam formulas approximate interior loads only when both ends are several characteristic lengths away.",
        }[r.data["length_classification"]])
        r.data["elements"] = n
        r.data["requested_elements"] = elements
        r.data["convergence"] = convergence
        r.data["solve_relative_residual"] = beam.residual
        r.data["end_reactions"] = [{"end": name, "force": r.quantity(beam.reaction[index], "force"), "moment": r.quantity(beam.reaction[index+1], "moment")} for name, index in (("left", 0), ("right", len(beam.reaction)-2))]
        soil_force, soil_moment = 0., 0.
        for i, coefficients in enumerate(beam.coefficients):
            h, origin = beam.x[i+1]-beam.x[i], beam.x[i]
            soil = coefficients[4]
            integral = sum(c/(j+1) for j, c in enumerate(soil))
            soil_force += h*integral
            soil_moment += h*(origin*integral+h*sum(c/(j+2) for j, c in enumerate(soil)))
        external_force = load*span+sum(p for _, p, _ in points)
        external_moment = load*span**2/2+sum(x*p+c for x, p, c in points)
        r.data["soil_resultants"] = {"force": r.quantity(soil_force, "force"), "moment_about_left": r.quantity(soil_moment, "moment")}
        r.data["equilibrium_residual"] = {"force": r.quantity(external_force+soil_force+sum(beam.reaction[::2]), "force"), "moment_about_left": r.quantity(external_moment+soil_moment+beam.x@beam.reaction[::2]+sum(beam.reaction[1::2]), "moment")}
        locations = {(float(x), "right") for x in np.linspace(0, span, samples)}
        locations |= {(float(x), side) for x in beam.x for side in ("left", "right") if (x > 0 or side == "right") and (x < span or side == "left")}
        locations |= {(x, side) for _, x, side in maxima}
        rows = [[x, side, *_value(beam, x, side).tolist()] for x, side in sorted(locations)]
        artifact = handle.write_text("foundation_stations.csv", csv_text(["x_m", "side", "deflection_m", "slope_rad", "moment_N_m", "shear_N", "soil_reaction_N_per_m"], rows), fmt="csv")
        r.artifacts.append(artifact)
        r.data["station_table"] = {"file": artifact.path, "rows": len(rows), "units": "SI; left/right identify one-sided element recovery"}
        eligible = len(points) == 1 and points[0][1] != 0 and points[0][2] == 0 and load == 0
        remote = eligible and beta*min(points[0][0], span-points[0][0]) >= 6
        if remote:
            a, p, _ = points[0]
            reference_at_load = p*beta/(2*k)
            comparisons = []
            for offset in (-3, -2, -1, 0, 1, 2, 3):
                location = a+offset/beta
                distance = abs(location-a)
                reference = reference_at_load*math.exp(-beta*distance)*(math.cos(beta*distance)+math.sin(beta*distance))
                actual = float(_value(beam, location)[0])
                comparisons.append([location, location-a, actual, reference, abs(actual-reference), abs(actual-reference)/abs(reference_at_load)])
            artifact = handle.write_text("foundation_infinite_comparison.csv", csv_text(["x_m", "distance_from_load_m", "finite_deflection_m", "infinite_deflection_m", "absolute_difference_m", "difference_relative_to_infinite_at_load"], comparisons), fmt="csv")
            r.artifacts.append(artifact)
            r.data["infinite_comparison"] = {
                "applicable": True, "minimum_end_distance_beta": beta*min(a, span-a),
                "reference_at_load": r.quantity(reference_at_load, "length"),
                "finite_at_load": r.quantity(float(_value(beam, a)[0]), "length"),
                "maximum_difference_relative_to_load": max(row[-1] for row in comparisons),
                "table": {"file": artifact.path, "rows": len(comparisons), "units": "SI"},
                "samples": [{"x": r.quantity(row[0], "length"), "distance_from_load": r.quantity(row[1], "length"), "finite_deflection": r.quantity(row[2], "length"), "infinite_deflection": r.quantity(row[3], "length"), "difference_relative_to_infinite_at_load": row[5]} for row in comparisons],
                "qualification": "Hetenyi infinite-beam approximation for a single force with both ends at least six characteristic lengths away. Differences include finite-boundary effects and discretization error; mesh convergence is reported separately.",
            }
            r.warnings.append("Hetenyi comparison is approximate: both ends are at least six characteristic lengths from the single force, but finite-boundary effects remain. Compare its table and the mesh-convergence results.")
        else:
            r.data["infinite_comparison"] = {"applicable": False, "reason": "Requires one nonzero point force, no point moment or uniform load, and both ends at least six characteristic lengths from the force."}
            r.warnings.append("Infinite-beam point-load comparison is unavailable for this loading or end distance. The finite beam solution and two-mesh convergence apply.")
        r.data["sources"] = ["https://archive.nptel.ac.in/content/storage2/courses/105106049/lecnotes/mainch11.html", "https://congress.cimne.com/eccm_ecfd2018/admin/files/filePaper/p264.pdf"]
        if max(abs(beam.displacement[1::2])) > .05:
            r.warnings.append("Nodal slope exceeds 0.05 rad; small-displacement Euler-Bernoulli assumptions need review.")
        r.warnings.append("Foundation carries tension. Compression-only soil contact and uplift require a different nonlinear model. Station CSV uses SI units.")
        return r.done("maximum_abs_deflection")

    return run("foundation", inputs, compute)
