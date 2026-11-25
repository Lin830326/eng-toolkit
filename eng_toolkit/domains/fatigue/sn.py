"""Stress-life fitting, steel estimates, mean stress and notch sensitivity."""

from dataclasses import replace
import math
from typing import Annotated

from pydantic import Field

from ._common import Result, choice, csv_text, invalid, number, q, run
from ._server import server
from ._sn import FIT_METHODS, RELIABILITY, SURFACES, correction, curve, sn_points


@server.tool(description="Basquin S-N fit or Shigley steel estimate with Marin factors; Goodman, Gerber, Soderberg, Morrow, SWT or Walker mean stress and proportional-load safety. Peterson/Neuber notch sensitivity. Uniaxial elastic fatigue; runouts excluded; compressive mean benefit omitted. Safety factors assume PROPORTIONAL loading (mean and alternating grow together): NOT valid for preloaded bolts, where the mean is fixed.")
def sn(
    mode: Annotated[str, Field(description="fit, curve, estimate, correction or notch; choose the source of the stress-life calculation.")] = "fit",
    points: Annotated[str, Field(description="Fit records 'stress,cycles[,failure|runout]', e.g. '400 MPa,1000'; ;/newline separators, <=2000; '' omits.")] = "",
    basquin_a: Annotated[str, Field(description="Basquin amplitude at one cycle, e.g. '1000 MPa'; required in curve mode; '' omits otherwise.")] = "",
    basquin_b: Annotated[float, Field(description="Basquin log-log slope, dimensionless -2..-0.00001; curve mode, e.g. -0.1.")] = -.1,
    alternating_stress: Annotated[str, Field(description="Nominal stress amplitude, e.g. '100 MPa'; optional life query; required for correction; '' omits.")] = "",
    mean_stress: Annotated[str, Field(description="Nominal signed mean stress, e.g. '50 MPa'; used with an amplitude; negative means receive no benefit.")] = "0 MPa",
    cycles: Annotated[str, Field(description="Life for a strength query, e.g. '1e5 cycle'; optional except correction safety requires it or endurance; '' omits.")] = "",
    endurance_limit: Annotated[str, Field(description="Fully reversed endurance amplitude, e.g. '200 MPa'; optional fit/curve or correction safety; '' omits.")] = "",
    knee_cycles: Annotated[str, Field(description="Endurance knee, e.g. '1e6 cycle'; optional fit/curve, must match endurance if both supplied; '' omits.")] = "",
    ultimate_strength: Annotated[str, Field(description="Tensile ultimate strength, e.g. '600 MPa'; estimate, Goodman/Gerber or automatic notch fit; '' omits.")] = "",
    yield_strength: Annotated[str, Field(description="Tensile yield strength, e.g. '400 MPa'; Soderberg requires it, otherwise optional elastic check; '' omits.")] = "",
    fatigue_strength_coefficient: Annotated[str, Field(description="Fatigue strength coefficient sigma_f', e.g. '900 MPa'; required for Morrow mean correction; '' omits.")] = "",
    mean_method: Annotated[str, Field(description="Mean correction: none, goodman, gerber, soderberg, morrow, swt or walker; compressive mean is clamped to zero.")] = "none",
    walker_gamma: Annotated[float, Field(description="Walker amplitude exponent gamma, dimensionless 0..1; gamma=0.5 gives SWT; used only by Walker.")] = .5,
    surface_finish: Annotated[str, Field(description="Estimate surface: polished, ground, machined, hot_rolled or as_forged; Shigley steel coefficients use Su in MPa.")] = "polished",
    diameter: Annotated[str, Field(description="Effective round diameter for automatic Marin size, e.g. '20 mm'; 2.79..254 mm; estimate mode; '' omits.")] = "",
    size_factor: Annotated[float, Field(description="Marin size factor kb, dimensionless 0..2; 0 requires diameter in estimate mode, axial defaults to 1.")] = 0,
    load_type: Annotated[str, Field(description="Estimate/notch basis: bending, axial or torsion; Marin kc=1, .85 or .59; torsion uses shear stress and its Neuber fit.")] = "bending",
    temperature_factor: Annotated[float, Field(description="Caller Marin temperature factor kd, dimensionless >0..2, default 1; estimate mode, no temperature extrapolation.")] = 1,
    reliability: Annotated[float, Field(description="Estimate reliability percentage: 50, 90, 95, 99, 99.9 or 99.99; rounded Shigley normal-quantile factors.")] = 50,
    kt: Annotated[float, Field(description="Elastic notch concentration Kt, dimensionless 1..100; multiplies mean and elastic peak stress.")] = 1,
    notch_method: Annotated[str, Field(description="none, peterson or neuber; a supplied material length overrides the automatic steel correlation.")] = "none",
    notch_radius: Annotated[str, Field(description="Notch root radius, e.g. '1 mm'; required for Peterson/Neuber; '' omits with no notch correction.")] = "",
    notch_material_length: Annotated[str, Field(description="Peterson a or Neuber a_N, e.g. '0.1 mm'; optional measured constant for selected notch method; '' uses steel fit.")] = "",
    fit_method: Annotated[str, Field(description="Fit regression: e739 (default; log N on log S, ASTM E739) or s_on_n (log S on log N); fit mode only.")] = "e739",
    shear_ultimate_strength: Annotated[str, Field(description="Shear ultimate Ssu for torsion estimates, e.g. '400 MPa', at most Su; '' uses Shigley Ssu=0.67 Su.")] = "",
):
    inputs = dict(locals())

    def compute(handle):
        kind = choice(mode, "mode", ("fit", "curve", "estimate", "correction", "notch"))
        r = Result({"Basquin": "Sa=A*N^b; fit e739 regresses log10 N on log10 S (default), s_on_n log10 S on log10 N; endurance plateau at Se",
                    "Marin": "Se=ka*kb*kc*kd*ke*min(.5*Su,700 MPa); anchors (f*S,1e3),(Se,1e6): bending .9Su, axial .75Su, torsion .9Ssu, Ssu=.67Su default",
                    "mean_stress": "Goodman Sa/(1-Sm/Su); Gerber Sa/(1-(Sm/Su)^2); Soderberg Sy; Morrow sigma_f'; SWT sqrt(Sa*Smax); Walker Sa^gamma*Smax^(1-gamma); torsion estimate uses Ssu and Ssy=.577Sy",
                    "notch": "Peterson q=1/(1+a/r); Neuber q=1/(1+sqrt(a_N/r)); Kf=1+q*(Kt-1)"},
                   "Uniaxial constant amplitude, tensile sign positive. Mean compression clamped to zero. The safety factor assumes proportional loading: mean and alternating stress scale together along a load line through the origin. It is NOT valid where the mean stays fixed and only the alternating part grows (a preloaded bolt, a pressure-vessel cover): the factor is then far too conservative. Cycles N, not reversals.",
                   "Steel estimates Su=400..2000 MPa, N>=1000; measured fits may extrapolate with warning. Elastic peak must not exceed supplied yield. No statistical design allowables.")
        corr = correction(mean_method, ultimate_strength, yield_strength, fatigue_strength_coefficient, walker_gamma)
        sa = q(alternating_stress, "alternating_stress", "stress", optional=True)
        sm = q(mean_stress, "mean_stress", "stress", signed=True, zero=True)
        life = q(cycles, "cycles", "cycles", optional=True)
        if life is not None:
            number(life, "cycles", 1, 1e18)
        se = q(endurance_limit, "endurance_limit", "stress", optional=True)
        knee = q(knee_cycles, "knee_cycles", "cycles", optional=True)
        if knee is not None:
            number(knee, "knee_cycles", 1, 1e18)
        ssu = q(shear_ultimate_strength, "shear_ultimate_strength", "stress", optional=True)
        if ssu is not None and (kind != "estimate" or load_type != "torsion"):
            invalid("shear_ultimate_strength", "shear ultimate applies only to torsion estimates")
        kval = number(kt, "kt", 1, 100)
        loading = choice(load_type, "load_type", ("bending", "axial", "torsion"))
        method = choice(notch_method, "notch_method", ("none", "peterson", "neuber"))
        kf = 1.
        if method != "none":
            radius = q(notch_radius, "notch_radius", "length")
            material_length = q(notch_material_length, "notch_material_length", "length", optional=True)
            if material_length is None:
                if loading == "torsion" and method == "peterson":
                    invalid("notch_material_length", "torsional Peterson correction requires a measured material length", "input.missing")
                if corr.ultimate is None:
                    invalid("ultimate_strength", "automatic notch correlation requires steel ultimate strength", "input.missing")
                # Dowling states the Peterson steel fit for Su>=550 MPa; Shigley's Neuber fit covers 340..1700 MPa.
                su = number(corr.ultimate, "ultimate_strength", 550 if method == "peterson" else 345, 1500)
                if method == "peterson":
                    material_length = .0254 * (2070 / su) ** 1.8 / 1000
                elif loading == "torsion":
                    # Shigley 6-35: Su in ksi, sqrt(a_s) in sqrt(inch).
                    su_ksi = su / 6.894757293168361
                    root_a = .190 - 2.51e-3 * su_ksi + 1.35e-5 * su_ksi**2 - 2.67e-8 * su_ksi**3
                    material_length = root_a**2 * .0254
                else:
                    # Shigley bending/axial fit, sqrt(a_N) in sqrt(mm).
                    root_a = 1.24 - 2.25e-3 * su + 1.60e-6 * su**2 - 4.11e-10 * su**3
                    material_length = root_a**2 / 1000
            sensitivity = 1 / (1 + (material_length / radius if method == "peterson" else math.sqrt(material_length / radius)))
            kf = 1 + sensitivity * (kval - 1)
            r.add("notch_sensitivity", sensitivity)
            r.add("notch_material_length", material_length, "m")
        elif notch_radius or notch_material_length or kval != 1:
            invalid("notch_method", "nontrivial Kt or notch dimensions require Peterson or Neuber")
        r.add("fatigue_notch_factor", kf)
        if kind == "notch":
            if method == "none":
                invalid("notch_method", "notch mode needs Peterson or Neuber")
            if points or basquin_a or sa is not None or life is not None or se is not None or knee is not None:
                invalid("mode", "notch mode does not consume S-N or loading inputs")
            return r.done("fatigue_notch_factor")
        model = None
        fitting = choice(fit_method, "fit_method", FIT_METHODS)
        if kind != "fit" and fitting != "e739":
            invalid("fit_method", "fit method applies to fit mode only")
        if kind == "fit":
            if basquin_a:
                invalid("basquin_a", "fit mode uses points, not a direct coefficient")
            a, b, stats, runouts, failures = sn_points(points, fitting)
            r.data.update(fit=stats, excluded_runouts=[{"stress_MPa": s, "cycles": n} for s, n in runouts])
            r.data["fit_cycles_range"] = [min(n for s, n in failures), max(n for s, n in failures)]
            model = curve(a, b, se, knee)
            r.warnings.append("Runouts are censored observations excluded from least squares; scatter is descriptive, not a survival probability.")
        elif kind == "curve":
            if points:
                invalid("points", "curve mode uses A and b")
            model = curve(q(basquin_a, "basquin_a", "stress"), number(basquin_b, "basquin_b", -2, -1e-5), se, knee)
        elif kind == "estimate":
            if points or basquin_a or se is not None or knee is not None:
                invalid("mode", "estimated anchors conflict with points, A, endurance or knee")
            if corr.ultimate is None:
                invalid("ultimate_strength", "steel estimate requires ultimate strength", "input.missing")
            su = number(corr.ultimate, "ultimate_strength", 400, 2000)
            finish = choice(surface_finish, "surface_finish", ("polished", *SURFACES))
            kb = number(size_factor, "size_factor", 0, 2)
            d = q(diameter, "diameter", "length", optional=True)
            if kb and d is not None:
                invalid("size_factor", "supply size factor or diameter, not both")
            if not kb:
                if loading == "axial":
                    kb = 1.
                else:
                    if d is None:
                        invalid("diameter", "automatic size factor needs effective diameter", "input.missing")
                    mm = number(d * 1000, "diameter", 2.79, 254)
                    kb = 1.24 * mm**-.107 if mm <= 51 else 1.51 * mm**-.157
            ka = min(1., SURFACES[finish][0] * su ** SURFACES[finish][1]) if finish != "polished" else 1.
            rel = number(reliability, "reliability", 50, 99.99)
            if rel not in RELIABILITY:
                invalid("reliability", "use a tabulated reliability percentile")
            factors = dict(ka=ka, kb=kb, kc={"bending": 1, "axial": .85, "torsion": .59}[loading],
                           kd=number(temperature_factor, "temperature_factor", 1e-6, 2), ke=RELIABILITY[rel])
            se = min(.5 * su, 700) * math.prod(factors.values())
            basis = su
            if loading == "torsion":
                # Shear states: Shigley Ssu=.67 Su and distortion-energy Ssy=.577 Sy replace Su and Sy.
                basis = .67 * su if ssu is None else ssu
                if basis > su:
                    invalid("shear_ultimate_strength", "steel shear ultimate cannot exceed tensile ultimate")
                ssy = .577 * corr.yield_strength if corr.yield_strength is not None else None
                if ssy is not None and ssy > basis:
                    invalid("yield_strength", "shear yield .577 Sy exceeds the shear ultimate")
                corr = replace(corr, ultimate=basis, yield_strength=ssy)
                r.add("shear_ultimate_strength", basis, "MPa")
                if ssy is not None:
                    r.add("shear_yield_strength", ssy, "MPa")
            fraction = {"bending": .9, "axial": .75, "torsion": .9}[loading]
            anchor = fraction * basis
            if se >= anchor:
                invalid("size_factor", "Marin product must put endurance below the 1000-cycle anchor")
            b = math.log10(se / anchor) / 3
            model = curve(anchor / 1000**b, b, se, 1e6)
            r.data["Marin_factors"] = factors
            r.data["anchor_1000_cycles"] = {"fraction": fraction, "strength": "Ssu" if loading == "torsion" else "Su",
                                            "amplitude_MPa": anchor}
            r.warnings.append("Empirical steel estimate; the 1000-cycle anchor (bending .9 Su, axial .75 Su, torsion .9 Ssu) is approximate. "
                              "Torsion uses shear amplitudes, kc=.59, Ssu and Ssy=.577 Sy, not von Mises stresses.")
        else:
            for name, given in (("points", points), ("basquin_a", basquin_a), ("knee_cycles", knee is not None), ("cycles", life is not None)):
                if given:
                    invalid(name, f"drop '{name}' in correction mode (it takes the endurance as the safety strength, no curve or life), or use mode=curve, fit or estimate")
            if sa is None:
                invalid("alternating_stress", "correction mode needs an amplitude", "input.missing")
        if kind != "estimate" and (diameter or size_factor or surface_finish != "polished" or load_type != "bending" or temperature_factor != 1 or reliability != 50):
            invalid("mode", "Marin parameters require estimate mode")
        if sa is None and (sm != 0 or mean_method != "none"):
            invalid("alternating_stress", "mean stress correction requires an amplitude", "input.missing")
        if model is not None:
            r.add("basquin_a", model.a, "MPa")
            r.add("basquin_b", model.b)
            if model.endurance is not None:
                r.add("endurance_limit", model.endurance, "MPa")
                r.add("knee_cycles", model.knee, "count")
            if life is not None:
                if kind == "estimate" and life < 1000:
                    invalid("cycles", "estimated S-N construction requires at least 1000 cycles", "input.out_of_range")
                r.add("stress_at_life", model.stress(life), "MPa")
            sample_life = [10 ** (3 + i * .1) for i in range(61)]
            r.artifacts.append(handle.write_text("sn_curve.csv", csv_text(["cycles", "amplitude_MPa"], [[n, model.stress(n)] for n in sample_life]), fmt="csv"))
        if sa is not None:
            if corr.yield_strength is not None and kval * (sa + abs(sm)) > corr.yield_strength:
                invalid("alternating_stress", "elastic Kt peak exceeds yield; stress-life notch approximation is invalid")
            eq = corr.equivalent(kf * sa, kval * sm)
            r.add("equivalent_amplitude", eq, "MPa")
            if model is not None:
                log_life = model.log_life(eq)
                if log_life is None:
                    r.data["life_cycles"] = None
                    r.data["life_status"] = "at_or_below_endurance; idealized unbounded life omitted"
                else:
                    n = math.exp(number(log_life, "alternating_stress", 0, math.log(1e18)))
                    if kind == "estimate" and n < 1000:
                        invalid("alternating_stress", "predicted life below the 1000-cycle construction limit", "input.out_of_range")
                    r.add("life", n, "count")
                    r.data["life_cycles"] = n
                strength = model.stress(life) if life is not None else model.endurance
            else:
                strength = se
            if strength is not None:
                r.add("safety_factor", corr.safety(kf * sa, kval * sm, strength))
                if kind == "correction" or sm > 0:
                    text = (
                        "The safety factor assumes proportional loading: mean and alternating stress grow together along a load line through the origin. "
                        "It is NOT valid when the mean stays fixed and only the alternating part grows, as in a preloaded bolt (mean = preload stress): "
                        "the factor is then far too conservative (for a bolt it can be several times too low). Use the preloaded-bolt form with the constant preload."
                    )
                    if sa > 0 and sm > 20 * sa:
                        text += f" Here the mean is {sm / sa:.3g} times the amplitude, the pattern of a preloaded joint."
                    r.warnings.append(text)
            if corr.yield_strength is None:
                r.warnings.append("Yield strength omitted; elastic peak validity was not checked.")
        if sm < 0:
            r.warnings.append("Compressive mean stress was clamped to zero for fatigue correction; signed peaks still enter the yield check.")
        if kind == "fit":
            r.warnings.append("Curve CSV and life queries may extrapolate beyond the reported fitted cycle range.")
        return r.done("equivalent_amplitude" if sa is not None else "basquin_a")
    return run("sn", inputs, compute)
