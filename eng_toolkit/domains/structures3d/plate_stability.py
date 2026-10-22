"""Elastic rectangular-plate buckling and simply supported vibration."""

import math
from typing import Annotated

import numpy as np
from pydantic import Field

from ._common import Result, System, choice, count, invalid, number, q, run
from ._server import server


def _sssf_characteristic(p, alpha, nu):
    """Free-edge determinant after imposing W(0)=W''(0)=0; b=1.

    W''''-2 alpha^2 W''+(alpha^4-p alpha^2)W=0,
    W''(1)-nu alpha^2 W(1)=W'''(1)-(2-nu)alpha^2 W'(1)=0.
    Hyperbolic columns are divided by cosh to avoid overflow. The second
    column becomes sin(beta*y)/beta when alpha^2-alpha*sqrt(p)<0.
    """
    h = alpha*math.sqrt(p)
    u = math.sqrt(alpha*alpha+h)
    v2 = alpha*alpha-h
    c = (1-nu)*alpha*alpha
    ratio2 = ((c-h)/(c+h))**2
    if v2 >= 0:
        v = math.sqrt(v2)
        return ratio2*(math.tanh(v)/v if v else 1.)-math.tanh(u)/u
    v = math.sqrt(-v2)
    return ratio2*math.sin(v)/v-math.tanh(u)/u*math.cos(v)


def _sssf_coefficient(aspect, nu):
    # Completing the square in the bending energy and using the Dirichlet/free
    # Poincare bound gives p >= (1-nu^2)*alpha^2+(1-nu)*pi^2/2.
    # W=y is an admissible Rayleigh trial: p <= alpha^2+6*(1-nu).
    best, selected, count_ = math.inf, 1, 0
    m = 1
    while True:
        alpha = m*math.pi/aspect
        low = (1-nu*nu)*alpha*alpha+(1-nu)*math.pi**2/2
        if low > best:
            break
        high = alpha*alpha+6*(1-nu)
        f_low = _sssf_characteristic(low, alpha, nu)
        f_high = _sssf_characteristic(high, alpha, nu)
        if f_low*f_high > 0:
            invalid("poisson_ratio", "SSSF characteristic root could not be bracketed", "input.out_of_range")
        for _ in range(80):
            middle = (low+high)/2
            value = _sssf_characteristic(middle, alpha, nu)
            if f_low*value <= 0:
                high = middle
            else:
                low, f_low = middle, value
            if high-low <= 2e-13*middle:
                break
        else:
            invalid("length", "SSSF characteristic root did not converge", "input.out_of_range")
        root = (low+high)/2
        if root < best:
            best, selected = root, m
        count_ += 1
        m += 1
    return best/math.pi**2, selected, count_


@server.tool(description="Rectangular plate elastic buckling (analysis uniaxial/biaxial/shear/bending: exact finite SSSS modes, long-plate edge coefficients, shear and bending estimates) AND natural frequencies (analysis='vibration', exact SSSS, needs density). No design-code or postbuckling check.")
def plate_stability(
    length: Annotated[str, Field(description="Plate x length a parallel to primary compression, e.g. '2 m'.")] = "",
    width: Annotated[str, Field(description="Plate y width b between longitudinal edges, e.g. '1 m'.")] = "",
    thickness: Annotated[str, Field(description="Constant thin-plate thickness, e.g. '5 mm'; t/min(a,b)<0.1.")] = "",
    youngs_modulus: Annotated[str, Field(description="Young modulus for elastic plate stiffness, e.g. '200 GPa'.")] = "",
    poisson_ratio: Annotated[float, Field(description="Material Poisson ratio in supported range -0.999..0.499.")] = 0.3,
    analysis: Annotated[str, Field(description="One of uniaxial, biaxial, shear, bending, vibration.")] = "uniaxial",
    edges: Annotated[str, Field(description="Cyclic x=0,y=0,x=a,y=b supports: SSSS, SSSC, SCSC, SSSF; SSSC/SCSC only long uniaxial; SSSF finite uniaxial.")] = "SSSS",
    stress_x: Annotated[str, Field(description="Reference x compression, shear demand, or peak in-plane bending stress; nonnegative, e.g. '10 MPa'.")] = "0 Pa",
    stress_y: Annotated[str, Field(description="Reference y compression for biaxial loading only, e.g. '5 MPa'; otherwise '0 Pa'.")] = "0 Pa",
    density: Annotated[str, Field(description="Mass density required for vibration, e.g. '7850 kg/m^3'; '' omits for all buckling branches.")] = "",
    modes: Annotated[int, Field(description="Number of exact SSSS vibration modes, 1..30; leave 6 for buckling.")] = 6,
    series_terms: Annotated[int, Field(description="Biaxial integer-mode search limit per axis, 2..200; leave 80 for other analyses.")] = 80,
    yield_strength: Annotated[str, Field(description="Uniaxial yield stress for optional elastic-scope warning, e.g. '250 MPa'; '' omits; unavailable for vibration.")] = "",
    unit_system: System = "metric",
):
    inputs = dict(locals())

    def compute(_):
        a, b, t, e = (q(value, name, kind) for value, name, kind in (
            (length, "length", "length"), (width, "width", "length"),
            (thickness, "thickness", "length"), (youngs_modulus, "youngs_modulus", "stress")))
        nu = number(poisson_ratio, "poisson_ratio", -0.999, 0.499)
        branch = choice(analysis, "analysis", ("uniaxial", "biaxial", "shear", "bending", "vibration"))
        boundary = choice(edges, "edges", ("SSSS", "SSSC", "SCSC", "SSSF"))
        sx, sy = q(stress_x, "stress_x", "stress", zero=True), q(stress_y, "stress_y", "stress", zero=True)
        rho = q(density, "density", "density", optional=True)
        strength = q(yield_strength, "yield_strength", "stress", optional=True)
        requested = count(modes, "modes", 1, 30)
        limit = count(series_terms, "series_terms", 2, 200)
        if t / min(a, b) >= 0.1:
            invalid("thickness", "Kirchhoff thin-plate scope requires t/min(a,b) < 0.1")
        aspect = a / b
        if not 0.05 <= aspect <= 100:
            invalid("length", "supported aspect ratio a/b is 0.05..100", "input.out_of_range")
        if branch != "biaxial" and sy != 0:
            invalid("stress_y", "transverse reference compression is only accepted for biaxial analysis")
        if branch != "biaxial" and limit != 80:
            invalid("series_terms", "series_terms is only adjustable for biaxial analysis")
        if branch != "vibration" and (rho is not None or requested != 6):
            invalid("density", "density and adjustable modes are only accepted for vibration")
        if boundary != "SSSS" and branch != "uniaxial":
            invalid("edges", "biaxial, shear, bending and vibration require SSSS edges")
        rigidity = e * t**3 / (12 * (1 - nu**2))
        base_stress = math.pi**2 * rigidity / (t * b**2)
        r = Result(unit_system, {
            "rigidity": "D=E*t^3/[12*(1-nu^2)]",
            "uniaxial": "sigma_cr=k*pi^2*D/(t*b^2); SSSS k=min_m (m*b/a+a/(m*b))^2, n=1",
            "biaxial": "lambda_mn=D*(alpha^2+beta^2)^2/[t*(sigma_x*alpha^2+sigma_y*beta^2)]",
            "shear": "k=5.34+4/(a/b)^2, a/b>=1; tau_cr=k*pi^2*D/(t*b^2)",
            "bending": "k=23.9, long SSSS plate with linear in-plane stress from +sigma to -sigma across b",
            "vibration": "f_mn=pi/2*sqrt(D/(rho*t))*[(m/a)^2+(n/b)^2]",
        }, "Flat, homogeneous isotropic elastic thin plate, ideal edge restraints; no imperfections, residual stress, postbuckling resistance or code factors. Compression is positive.",
                   "t/min(a,b)<0.1. SSSC/SCSC compression and in-plane bending coefficients are long-plate estimates restricted to a/b>=10. SSSF solves the finite-length characteristic equation. Shear requires SSSS and a/b>=1. Vibration is exact only for unloaded SSSS plates.")
        r.add("flexural_rigidity", rigidity, "plate_rigidity")
        r.add("aspect_ratio", aspect)
        r.data["analysis"], r.data["edges"] = branch, boundary
        r.data["references"] = [
            "https://pkel015.connect.amazon.auckland.ac.nz/SolidMechanicsBooks/Part_II/06_PlateTheory/06_PlateTheory_Complete.pdf, sections 6.6 and 6.7",
            "https://www.aisc.org/media/a0jdm5pv/localbuckling-limits-report_aisc_adhoctg.pdf, Attachment BT-3 Table 2",
            "https://cfsei.memberclicks.net/assets/docs/publications/aisi-standards/aisi%20s100-16%20%20s100-16-c_e_s.pdf, G2.3 shear coefficient and Commentary 1.1.2 pure-bending coefficient",
        ]
        if branch == "vibration":
            if rho is None:
                invalid("density", "vibration requires mass density")
            if sx != 0 or sy != 0 or strength is not None:
                invalid("stress_x", "vibration is unloaded; omit reference stresses and yield_strength")
            candidates = [(math.pi / 2 * math.sqrt(rigidity / (rho * t)) * ((m / a)**2 + (n / b)**2), m, n)
                          for m in range(1, requested + 1) for n in range(1, requested + 1)]
            selected = sorted(candidates)[:requested]
            r.data["modes"] = [{"m": m, "n": n, "frequency": r.quantity(frequency, "frequency"),
                                "shape": f"sin({m}*pi*x/a)*sin({n}*pi*y/b)"} for frequency, m, n in selected]
            r.data["solution"] = "exact unloaded SSSS Kirchhoff eigenfrequencies"
            r.add("fundamental_frequency", selected[0][0], "frequency")
            return r.done("fundamental_frequency")
        if branch == "biaxial":
            if max(sx, sy) == 0:
                invalid("stress_x", "biaxial analysis needs at least one positive reference compression")
            integers = np.arange(1, limit + 1)
            alpha2 = (integers[:, None] * math.pi / a)**2
            beta2 = (integers[None, :] * math.pi / b)**2
            factors = rigidity * (alpha2 + beta2)**2 / (t * (sx * alpha2 + sy * beta2))
            index = np.unravel_index(np.argmin(factors), factors.shape)
            factor = float(factors[index])
            outside_lower_bound = rigidity / (t * max(sx, sy)) * min(
                ((limit + 1) * math.pi / a)**2 + (math.pi / b)**2,
                (math.pi / a)**2 + ((limit + 1) * math.pi / b)**2)
            if outside_lower_bound <= factor:
                invalid("series_terms", "mode bound cannot certify the global minimum; increase series_terms or reduce aspect ratio", "input.out_of_range")
            r.data["critical_mode"] = {"m": int(index[0] + 1), "n": int(index[1] + 1)}
            r.data["solution"] = "exact SSSS integer-mode minimum with excluded-mode lower bound"
            r.add("load_multiplier", factor)
            r.add("critical_stress_x", factor * sx, "stress")
            r.add("critical_stress_y", factor * sy, "stress")
            r.add("critical_resultant_x", factor * sx * t, "line_load")
            r.add("critical_resultant_y", factor * sy * t, "line_load")
            if strength is not None and factor * math.sqrt(sx**2 - sx * sy + sy**2) > strength:
                r.warnings.append("Biaxial critical plane-stress von Mises demand exceeds supplied yield strength; elastic buckling is outside material scope.")
            return r.done("load_multiplier")
        if branch == "uniaxial":
            if boundary == "SSSS":
                trial = {max(1, math.floor(aspect)), max(1, math.ceil(aspect))}
                coefficient, half_waves = min(((m / aspect + aspect / m)**2, m) for m in trial)
                r.data["critical_mode"] = {"m": half_waves, "n": 1}
                r.data["solution"] = "exact finite-aspect-ratio SSSS integer minimum"
            elif boundary == "SSSF":
                coefficient, half_waves, searched = _sssf_coefficient(aspect, nu)
                r.data["critical_mode"] = {"m": half_waves}
                r.data["solution"] = "finite SSSF characteristic equation with bracketed roots and certified integer minimum"
                r.data["coefficient_method"] = {
                    "equation": "W''''-2 alpha^2 W''+(alpha^4-p alpha^2)W=0; W(0)=W''(0)=0; W''(1)-nu alpha^2 W(1)=W'''(1)-(2-nu)alpha^2 W'(1)=0; alpha=m*pi*b/a; p=k*pi^2",
                    "root_relative_bracket": 2e-13, "half_wave_numbers_checked": searched,
                    "long_wavelength_limit": 6*(1-nu)/math.pi**2,
                    "excluded_mode_lower_bound": "p >= (1-nu^2)*(m*pi*b/a)^2+(1-nu)*pi^2/2"}
            else:
                if aspect < 10:
                    invalid("edges", "long-plate edge coefficients require a/b>=10; finite-plate non-SSSS solutions are unsupported")
                coefficient = {"SSSC": 5.42, "SCSC": 6.97}[boundary]
                r.data["solution"] = "long-plate limiting coefficient estimate"
                r.data["coefficient_method"] = {"type": "tabulated approximation", "decimal_places": 2,
                    "reference_exact_limit": {"SSSC": 5.4097, "SCSC": 6.9709}[boundary],
                    "source": r.data["references"][1],
                    "poisson_dependence": "k is independent of nu: W=0 removes nu from simple/clamped unloaded-edge conditions; D retains nu dependence"}
                r.warnings.append("Long-plate limiting coefficient used; finite-length boundary effects are not solved.")
        elif branch == "shear":
            if aspect < 1:
                invalid("length", "SSSS shear estimate requires a/b>=1; exchange axes for a short panel")
            coefficient = 5.34 + 4 / aspect**2
            r.data["solution"] = "SSSS shear coefficient approximation"
            r.data["coefficient_method"] = {"type": "tabulated fit", "constants": "5.34 (two decimals), 4 (fit coefficient)",
                "source": r.data["references"][2], "precision": "coefficient rounding only; finite-aspect fit has no certified error bound",
                "poisson_dependence": "SSSS k is independent of nu; D retains nu dependence"}
        else:
            if aspect < 10:
                invalid("length", "k=23.9 requires a long SSSS plate, a/b>=10, in pure in-plane bending")
            coefficient = 23.9
            r.data["solution"] = "long SSSS pure in-plane bending coefficient estimate"
            r.data["coefficient_method"] = {"type": "tabulated approximation", "decimal_places": 1,
                "source": r.data["references"][2], "precision": "one decimal in the long-plate coefficient; finite-length error is not bounded",
                "poisson_dependence": "SSSS k is independent of nu; D retains nu dependence"}
            r.warnings.append("Bending means linear longitudinal membrane stress from equal compression to tension across the width; it is not transverse plate bending.")
        critical = coefficient * base_stress
        r.add("buckling_coefficient", coefficient)
        r.add("critical_stress", critical, "stress")
        r.add("critical_resultant", critical * t, "line_load")
        r.safety("load_multiplier", critical, sx)
        if strength is not None and critical > (strength / math.sqrt(3) if branch == "shear" else strength):
            r.warnings.append("Elastic critical stress exceeds supplied yield strength (von Mises shear criterion where applicable); elastic buckling is outside material scope.")
        return r.done("critical_stress")

    return run("plate_stability", inputs, compute)
