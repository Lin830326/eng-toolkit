"""Bounded Kirchhoff rectangular plate series, with explicit singularity reporting."""

import math
from typing import Annotated

import numpy as np
from pydantic import Field

from ._common import Result, System, choice, count, csv_text, fields, invalid, number, q, records, run
from ._server import server

MAX_SERIES_TERMS = 2049


def _navier(a, b, rigidity, nu, pressure, points, terms, x, y):
    modes = np.arange(1, terms + 1, dtype=float)
    alpha, beta = modes * math.pi / a, modes * math.pi / b
    odd = (modes % 2 == 1).astype(float)
    load = 16 * pressure / math.pi**2 * np.outer(odd / modes, odd / modes)
    for px, py, force in points:
        load += 4 * force / (a * b) * np.outer(np.sin(alpha * px), np.sin(beta * py))
    coefficients = load / (rigidity * (alpha[:, None] ** 2 + beta[None, :] ** 2) ** 2)
    sx, sy = np.sin(np.outer(x, alpha)), np.sin(np.outer(y, beta))
    cx, cy = np.cos(np.outer(x, alpha)), np.cos(np.outer(y, beta))
    w = sx @ coefficients @ sy.T
    wxx = sx @ (-alpha[:, None] ** 2 * coefficients) @ sy.T
    wyy = sx @ (-beta[None, :] ** 2 * coefficients) @ sy.T
    wxy = cx @ (alpha[:, None] * beta[None, :] * coefficients) @ cy.T
    return w, -rigidity * (wxx + nu * wyy), -rigidity * (wyy + nu * wxx), -rigidity * (1 - nu) * wxy


def _levy_basis(alpha, half_width, z, derivative):
    """Symmetric homogeneous basis, differentiated and divided by alpha**derivative."""
    left, right = alpha * (z - half_width), -alpha * (z + half_width)
    eleft, eright = np.exp(left), np.exp(right)
    sign = (-1) ** derivative
    return np.array([eleft + sign * eright,
                     (left + derivative) * eleft + sign * (right + derivative) * eright])


def _levy(a, b, rigidity, nu, pressure, edges, terms, x, y):
    w, wxx, wyy, wxy = (np.zeros((len(x), len(y))) for _ in range(4))
    for mode in range(1, terms + 1, 2):
        alpha = mode * math.pi / a
        boundary = [_levy_basis(alpha, b / 2, b / 2, derivative) for derivative in range(4)]
        if edges == "SSSS":
            matrix, rhs = np.array([boundary[0], boundary[2]]), [-1., 0.]
        elif edges == "SCSC":
            matrix, rhs = np.array([boundary[0], boundary[1]]), [-1., 0.]
        else:
            matrix = np.array([boundary[2] - nu * boundary[0], boundary[3] - (2 - nu) * boundary[1]])
            rhs = [nu, 0.]
        constants = np.linalg.solve(matrix, rhs)
        amplitude = 4 * pressure / (mode * math.pi * rigidity * alpha**4)
        shape = amplitude * (1 + constants @ _levy_basis(alpha, b / 2, y - b / 2, 0))
        first = amplitude * alpha * (constants @ _levy_basis(alpha, b / 2, y - b / 2, 1))
        second = amplitude * alpha**2 * (constants @ _levy_basis(alpha, b / 2, y - b / 2, 2))
        sine, cosine = np.sin(alpha * x), np.cos(alpha * x)
        w += np.outer(sine, shape)
        wxx -= alpha**2 * np.outer(sine, shape)
        wyy += np.outer(sine, second)
        wxy += alpha * np.outer(cosine, first)
    return w, -rigidity * (wxx + nu * wyy), -rigidity * (wyy + nu * wxx), -rigidity * (1 - nu) * wxy


def _sample_axis(span, grid, coordinates):
    sources = np.unique(coordinates)
    samples = [value for value in np.linspace(0, span, grid)
               if value in (0., span) or all(abs(value - coordinate) > 1e-12 * span for coordinate in sources)]
    return np.sort(np.r_[samples, sources])


@server.tool(description="Kirchhoff rectangular plate deflection and moments: Navier SSSS uniform/point loads, or Levy SSSS/SCSC/SFSF uniform load. Bounded series and grid convergence estimates; point-source moments and invalid small-deflection stresses omitted.")
def plate(
    length: Annotated[str, Field(description="Plate x span a, e.g. '1 m'.")] = "",
    width: Annotated[str, Field(description="Plate y span b, e.g. '1 m'.")] = "",
    thickness: Annotated[str, Field(description="Uniform plate thickness t, e.g. '10 mm'; t/min(a,b)<0.1.")] = "",
    youngs_modulus: Annotated[str, Field(description="Isotropic elastic modulus E, e.g. '200 GPa'.")] = "",
    poisson_ratio: Annotated[float, Field(description="Isotropic Poisson ratio in supported range -0.999..0.499.")] = 0.3,
    pressure: Annotated[str, Field(description="Signed uniform transverse pressure, e.g. '1 kPa'; positive deflects +w.")] = "0 Pa",
    point_loads: Annotated[str, Field(description="Interior Navier loads 'x,y,P', e.g. '0.4 m,0.6 m,100 N'; up to 10 records; '' omits.")] = "",
    method: Annotated[str, Field(description="Series solution: navier or levy; Levy accepts uniform pressure only.")] = "navier",
    edges: Annotated[str, Field(description="Cyclic x=0,y=0,x=a,y=b restraints: SSSS, SCSC or SFSF; S simple, C clamped, F free.")] = "SSSS",
    terms: Annotated[int, Field(description="Initial largest Fourier mode index, 5..121; automatically increases to at most 2049 for convergence.")] = 41,
    grid_points: Annotated[int, Field(description="Odd uniform sampling count per axis, 5..51; load coordinates are also sampled.")] = 21,
    convergence_tolerance: Annotated[float, Field(description="Relative series/grid tolerance, 0.00001..0.1; automatic refinement, then refusal at the cap.")] = 0.005,
    unit_system: System = "metric",
):
    inputs = dict(locals())

    def compute(handle):
        a, b, t, e = (q(value, name, kind) for value, name, kind in (
            (length, "length", "length"), (width, "width", "length"),
            (thickness, "thickness", "length"), (youngs_modulus, "youngs_modulus", "stress")))
        nu = number(poisson_ratio, "poisson_ratio", -0.999, 0.499)
        load = q(pressure, "pressure", "stress", signed=True, zero=True)
        method_name = choice(method, "method", ("navier", "levy"))
        boundary = choice(edges, "edges", ("SSSS", "SCSC", "SFSF"))
        order = count(terms, "terms", 5, 121)
        grid = count(grid_points, "grid_points", 5, 51)
        tolerance = number(convergence_tolerance, "convergence_tolerance", 1e-5, 0.1)
        if grid % 2 == 0:
            invalid("grid_points", "use an odd grid count so the comparison grid is nested")
        if t / min(a, b) >= 0.1:
            invalid("thickness", "Kirchhoff thin-plate scope requires t/min(a,b) < 0.1")
        if not 0.05 <= a / b <= 20:
            invalid("length", "supported aspect ratio a/b is 0.05..20", "input.out_of_range")
        if method_name == "navier" and boundary != "SSSS":
            invalid("edges", "Navier requires four simply supported edges")
        points = []
        for index, row in enumerate(records(point_loads, "point_loads", 10)):
            path = f"point_loads/{index}"
            fields(row, (3,), path)
            px, py = q(row[0], path + "/x", "length"), q(row[1], path + "/y", "length")
            force = q(row[2], path + "/P", "force", signed=True, zero=True)
            if not 0 < px < a or not 0 < py < b:
                invalid(path, "point loads must lie strictly inside the plate")
            if force != 0:
                points.append((px, py, force))
        if method_name == "levy" and point_loads.strip():
            invalid("point_loads", "Levy implementation accepts uniform pressure only")
        rigidity = e * t**3 / (12 * (1 - nu**2))
        x = _sample_axis(a, grid, [point[0] for point in points])
        y = _sample_axis(b, grid, [point[1] for point in points])
        requested_x, requested_y = x.copy(), y.copy()

        def evaluate(n, xs, ys):
            if method_name == "navier":
                return _navier(a, b, rigidity, nu, load, points, n, xs, ys)
            return _levy(a, b, rigidity, nu, load, boundary, n, xs, ys)

        while True:
            full = evaluate(order, x, y)
            coarse = evaluate(order // 2, x, y)
            regular = np.ones(full[0].shape, dtype=bool)
            for px, py, _ in points:
                regular[np.ix_(abs(x-px) <= 1e-12*a, abs(y-py) <= 1e-12*b)] = False
            scale = float(np.max(np.abs(full[0])))
            displacement_error = float(np.max(np.abs(full[0] - coarse[0]))) / scale if scale else 0.
            moment_scale = max(float(np.max(np.abs(component[regular]))) for component in full[1:])
            moment_error = max(float(np.max(np.abs(fine[regular] - rough[regular]))) for fine, rough in zip(full[1:], coarse[1:])) / moment_scale if moment_scale else 0.
            coarse_x = _sample_axis(a, (grid+1)//2, [p[0] for p in points])
            coarse_y = _sample_axis(b, (grid+1)//2, [p[1] for p in points])
            coarse_peak = float(np.max(np.abs(evaluate(order, coarse_x, coarse_y)[0])))
            grid_error = abs(scale - coarse_peak) / scale if scale else 0.
            if max(displacement_error, moment_error, grid_error) <= tolerance:
                break
            if max(displacement_error, moment_error) > tolerance:
                if order == MAX_SERIES_TERMS:
                    invalid("terms", f"series did not converge at {MAX_SERIES_TERMS} terms; "
                            f"relative deflection/moment changes {displacement_error:.6g}/{moment_error:.6g}; "
                            "point-source moments are logarithmically singular and are excluded only at the source", "input.out_of_range")
                order = min(2*order+1, MAX_SERIES_TERMS)
            if grid_error > tolerance:
                if grid == 51:
                    invalid("terms", "sampled-peak comparison did not converge at the 51-point grid cap", "input.out_of_range")
                grid = min(2*grid-1, 51)
                x = _sample_axis(a, grid, requested_x)
                y = _sample_axis(b, grid, requested_y)
        r = Result(unit_system, {
            "rigidity": "D=E*t^3/[12*(1-nu^2)]",
            "Navier": "w=sum(q_mn*sin(m*pi*x/a)*sin(n*pi*y/b)/(D*((m*pi/a)^2+(n*pi/b)^2)^2))",
            "Levy": "W''''-2*alpha^2*W''+alpha^4*W=q_m/D; symmetric exponential homogeneous basis",
            "moments": "Mx=-D*(w_xx+nu*w_yy); My=-D*(w_yy+nu*w_xx); Mxy=-D*(1-nu)*w_xy",
            "stress": "surface sigma_x=6*Mx/t^2; sigma_y=6*My/t^2; tau_xy=6*Mxy/t^2; opposite face reverses signs",
        }, "Homogeneous isotropic Kirchhoff plate; linear elastic small deflections; constant thickness; ideal bilateral edge supports. Positive pressure and point force produce positive w.",
                   "t/min(a,b)<0.1; 0.05<=a/b<=20. Stress omitted for any point load or sampled |w|>t/2. Series/grid comparisons estimate convergence and do not certify global extrema.")
        r.add("flexural_rigidity", rigidity, "plate_rigidity")
        r.add("maximum_sampled_abs_deflection", scale, "length")
        r.add("centre_deflection", float(evaluate(order, np.array([a / 2]), np.array([b / 2]))[0][0, 0]), "length")
        if not points and load != 0:
            r.add("alpha", scale * rigidity / (abs(load) * a**4))
            r.add("beta_x", float(np.max(np.abs(full[1]))) / (abs(load) * a**2))
            r.add("beta_y", float(np.max(np.abs(full[2]))) / (abs(load) * a**2))
            r.data["coefficient_normalization"] = "alpha=max_sampled|w|*D/(|p|*a^4); beta_x=max_sampled|Mx|/(|p|*a^2); beta_y=max_sampled|My|/(|p|*a^2); these are sampled maxima, not necessarily centre values"
        elif len(points) == 1 and load == 0:
            r.add("alpha", scale * rigidity / (abs(points[0][2]) * a**2))
            r.data["coefficient_normalization"] = "alpha=max_sampled|w|*D/(|P|*a^2) for one point load; beta omitted because point-source bending moments are singular"
        else:
            r.data["coefficient_normalization"] = "alpha and beta omitted for zero load or superposed uniform/point or multiple-point loading"
        peak = np.unravel_index(np.argmax(np.abs(full[0])), full[0].shape)
        r.data["peak_sample_location"] = {"x": r.quantity(x[peak[0]], "length"), "y": r.quantity(y[peak[1]], "length")}
        r.data["method"] = method_name
        r.data["edges"] = boundary
        r.data["convergence"] = {"terms": order, "requested_terms": terms, "maximum_terms": MAX_SERIES_TERMS,
                                 "grid_points": grid, "comparison_terms": order // 2, "relative_deflection_change": displacement_error,
                                 "relative_moment_change_away_from_sources": moment_error, "relative_sampled_peak_grid_change": grid_error,
                                 "tolerance": tolerance, "passed": max(displacement_error, moment_error, grid_error) <= tolerance}
        stress_valid = not points and scale <= t / 2
        r.data["stress_available"] = stress_valid
        if points:
            r.warnings.append("Ideal point loads have logarithmically singular bending moments at their source; source moments and all stress maxima are omitted. Moments elsewhere are truncated-series samples.")
        else:
            for name, component in zip(("x", "y", "xy"), full[1:]):
                r.add("maximum_sampled_abs_moment_" + name, float(np.max(np.abs(component))), "plate_moment")
        if scale > t / 2:
            r.warnings.append("Sampled deflection exceeds t/2; linear small-deflection stresses are omitted. Deflection is a linear-theory estimate requiring nonlinear review.")
        if stress_valid:
            sx, sy, tau = (6 * component / t**2 for component in full[1:])
            von_mises = np.sqrt(sx**2 - sx * sy + sy**2 + 3 * tau**2)
            r.add("maximum_sampled_von_mises_stress", float(np.max(von_mises)), "stress")
            r.add("maximum_sampled_abs_sigma_x", float(np.max(np.abs(sx))), "stress")
            r.add("maximum_sampled_abs_sigma_y", float(np.max(np.abs(sy))), "stress")
        rows = []
        for i, px in enumerate(x):
            for j, py in enumerate(y):
                moments = [float(component[i, j]) for component in full[1:]] if regular[i, j] else [None] * 3
                rows.append([float(px), float(py), float(full[0][i, j]), *moments])
        artifact = handle.write_text("plate_samples.csv", csv_text(["x_m", "y_m", "w_m", "Mx_N", "My_N", "Mxy_N"], rows), fmt="csv")
        r.artifacts.append(artifact)
        r.data["samples"] = {"file": artifact.path, "rows": len(rows), "units": "SI; moments per unit width in N; blank moments at point sources"}
        r.data["point_load_deflections"] = [{"x": r.quantity(px, "length"), "y": r.quantity(py, "length"),
            "w": r.quantity(float(full[0][np.flatnonzero(x == px)[0], np.flatnonzero(y == py)[0]]), "length"),
            "moments": None} for px, py, _ in points]
        r.data["references"] = ["https://pkel015.connect.amazon.auckland.ac.nz/SolidMechanicsBooks/Part_II/06_PlateTheory/06_PlateTheory_Complete.pdf, sections 6.3 and 6.5"]
        return r.done("maximum_sampled_abs_deflection")

    return run("plate", inputs, compute)
