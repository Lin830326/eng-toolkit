"""Cyclic plasticity and strain-life equations with log-space root brackets."""

from dataclasses import dataclass
import math
from typing import Annotated

import numpy as np
from pydantic import Field

from ._common import Result, choice, csv_text, fields, invalid, number, positive_exp, q, records, root, run
from ._server import server
from .rainflow import count_cycles, reversals


@dataclass(frozen=True)
class Cyclic:
    e: float
    k: float
    n: float

    def strain(self, stress):
        log_strain = float(np.logaddexp(math.log(stress/self.e), math.log(stress/self.k)/self.n))
        return positive_exp(log_strain, "cyclic_strength")

    def local(self, nominal, kt, rule):
        elastic = nominal * kt
        target = elastic**2 / self.e

        def residual(log_stress):
            stress = math.exp(log_stress)
            log_elastic = 2*log_stress-math.log(self.e)
            log_plastic = log_stress+math.log(stress/self.k)/self.n
            if rule == "glinka":
                log_plastic += math.log(2/(self.n+1))
            return float(np.logaddexp(log_elastic, log_plastic))-math.log(target)

        # Elastic stress is an upper bound. Search down to 1e-12 MPa in log space.
        stress = math.exp(root(residual, math.log(max(1e-12, elastic * 1e-8)), math.log(elastic), "nominal_stress"))
        strain = self.strain(stress)
        number(strain, "nominal_stress", 1e-15, .1)
        return stress, strain


@dataclass(frozen=True)
class StrainCurve:
    e: float
    sf: float
    ef: float
    b: float
    c: float

    def strain(self, reversals_count, mean=0.):
        return (self.sf - max(mean, 0)) / self.e * reversals_count**self.b + self.ef * reversals_count**self.c

    def life(self, amplitude, method, mean=0., maximum=None):
        if method == "swt":
            if maximum is None or maximum <= 0:
                invalid("maximum_stress", "SWT needs positive local maximum stress", "input.missing")
            log_target = math.log(maximum * amplitude * self.e)
            log_a, exp_a = 2 * math.log(self.sf), 2 * self.b
            log_b, exp_b = math.log(self.sf * self.ef * self.e), self.b + self.c
        else:
            strength = self.sf - max(0., mean) if method == "morrow" else self.sf
            if strength <= 0:
                invalid("mean_stress", "Morrow mean must be below the fatigue strength coefficient")
            log_target = math.log(amplitude)
            log_a, exp_a = math.log(strength / self.e), self.b
            log_b, exp_b = math.log(self.ef), self.c
        def fn(log_reversals):
            return float(np.logaddexp(log_a + exp_a * log_reversals, log_b + exp_b * log_reversals)) - log_target
        log_life = root(fn, 0., math.log(1e18), "strain_amplitude")
        return math.exp(log_life)


@server.tool(description="Ramberg-Osgood cyclic strain, Coffin-Manson-Basquin life with Morrow/SWT, Manson or Modified Universal Slopes estimates. Neuber/Glinka local notch solution and doubled Masing ranges; rainflow block damage. Log-bracketed roots; uniaxial stabilized response, no residual-stress memory.")
def strain_life(
    mode: Annotated[str, Field(description="life, cyclic, notch, block or estimate; selects a strain-life query, cyclic curve, local notch or reversal block.")] = "life",
    elastic_modulus: Annotated[str, Field(description="Young's modulus E, e.g. '200 GPa'; required in every mode.")] = "",
    cyclic_strength: Annotated[str, Field(description="Cyclic strength K', e.g. '1000 MPa'; paired with cyclic_exponent; '' derives a compatible curve from CMB.")] = "",
    cyclic_exponent: Annotated[float, Field(description="Cyclic hardening n', dimensionless .02..1; 0 means derive from strain-life constants, with K' omitted.")] = 0,
    fatigue_strength: Annotated[str, Field(description="CMB fatigue strength sigma_f', e.g. '1000 MPa'; required for measured life/block; '' uses estimation.")] = "",
    fatigue_ductility: Annotated[str, Field(description="CMB fatigue ductility epsilon_f', e.g. '0.5 dimensionless'; paired with fatigue_strength; '' uses estimation.")] = "",
    b: Annotated[float, Field(description="CMB elastic fatigue exponent, dimensionless -1..-0.001, e.g. -0.1; direct measured constants only.")] = -.1,
    c: Annotated[float, Field(description="CMB plastic fatigue exponent, dimensionless -2..-0.01, e.g. -0.5; must be less than b.")] = -.5,
    estimate_method: Annotated[str, Field(description="none, universal or modified_universal; estimates constants from monotonic Su and reduction of area.")] = "none",
    ultimate_strength: Annotated[str, Field(description="Monotonic ultimate tensile strength, e.g. '600 MPa'; required by Universal Slopes estimates; '' omits.")] = "",
    reduction_of_area: Annotated[float, Field(description="Tensile reduction-of-area fraction, .01..0.95 for estimates; 0 means omitted; D=-ln(1-RA).")] = 0,
    strain_amplitude: Annotated[str, Field(description="Local total strain amplitude, e.g. '0.005 dimensionless'; life mode input, maximum .1; '' omits.")] = "",
    stress_amplitude: Annotated[str, Field(description="Local cyclic stress amplitude, e.g. '300 MPa'; cyclic mode input or life alternative to strain; '' omits.")] = "",
    mean_method: Annotated[str, Field(description="Life mean-stress rule: none, morrow or swt; compressive mean gives no benefit.")] = "none",
    mean_stress: Annotated[str, Field(description="Prescribed stabilized local mean stress, e.g. '50 MPa'; life/block correction, not a nominal notch mean.")] = "0 MPa",
    maximum_stress: Annotated[str, Field(description="Local maximum stress for SWT, e.g. '350 MPa'; '' derives amplitude from cyclic curve plus tensile mean.")] = "",
    nominal_stress: Annotated[str, Field(description="Elastic nominal amplitude or range, e.g. '200 MPa'; required for notch mode; '' omits.")] = "",
    kt: Annotated[float, Field(description="Notch concentration multiplier Kt (or caller's effective Kf), dimensionless 1..100; notch/block modes.")] = 1,
    notch_rule: Annotated[str, Field(description="neuber (stress-strain product) or glinka (equivalent strain energy); used for notch/block ranges.")] = "neuber",
    range_input: Annotated[bool, Field(description="Notch mode only: nominal stress is a range; use doubled Masing curve and report local stress/strain ranges.")] = False,
    reversal_levels: Annotated[str, Field(description="Block nominal stress levels, e.g. '100 MPa;-100 MPa;100 MPa'; ;/newline separated, <=2000; '' omits.")] = "",
):
    inputs = dict(locals())

    def compute(handle):
        kind = choice(mode, "mode", ("life", "cyclic", "notch", "block", "estimate"))
        r = Result({"Ramberg_Osgood": "eps_a=sigma_a/E+(sigma_a/K')^(1/n')",
                    "Coffin_Manson_Basquin": "eps_a=(sigma_f'/E)*(2N)^b+eps_f'*(2N)^c",
                    "Morrow": "replace sigma_f' by sigma_f'-max(sigma_m,0) in the elastic term",
                    "SWT": "sigma_max*eps_a*E=sigma_f'^2*(2N)^(2b)+sigma_f'*eps_f'*E*(2N)^(b+c)",
                    "Neuber_Glinka": "Neuber sigma*eps=(Kt*S)^2/E; Glinka sigma^2/(2E)+sigma*(sigma/K')^(1/n')/(n'+1)=(Kt*S)^2/(2E)",
                    "Masing": "Delta_eps=Delta_sigma/E+2*(Delta_sigma/(2K'))^(1/n')"},
                   "Stabilized isotropic uniaxial cyclic curve. Cycles N; solver coordinate is log(2N). Compressive mean fatigue benefit omitted. Block uses independent Masing ranges with prescribed constant local mean; no plastic memory.",
                   "Local strain amplitude <=.1; 1<=2N<=1e18; .02<=n'<=1; c<b<0. Slopes estimates are preliminary ductile-metal sub-creep estimates, not brittle/cast-iron or creep-fatigue models.")
        e = q(elastic_modulus, "elastic_modulus", "stress")
        method = choice(mean_method, "mean_method", ("none", "morrow", "swt"))
        rule = choice(notch_rule, "notch_rule", ("neuber", "glinka"))
        estimation = choice(estimate_method, "estimate_method", ("none", "universal", "modified_universal"))
        kval = number(kt, "kt", 1, 100)
        mean = q(mean_stress, "mean_stress", "stress", signed=True, zero=True)
        maximum = q(maximum_stress, "maximum_stress", "stress", optional=True)
        sf = q(fatigue_strength, "fatigue_strength", "stress", optional=True)
        ef = q(fatigue_ductility, "fatigue_ductility", "strain", optional=True)
        if (sf is None) != (ef is None):
            invalid("fatigue_strength", "fatigue strength and ductility must be supplied together")
        bv = number(b, "b", -1, -.001)
        cv = number(c, "c", -2, -.01)
        if cv >= bv:
            invalid("c", "plastic exponent c must be less than b")
        if estimation != "none":
            if sf is not None:
                invalid("estimate_method", "estimated and measured fatigue constants conflict")
            su = q(ultimate_strength, "ultimate_strength", "stress")
            ra = number(reduction_of_area, "reduction_of_area", .01, .95)
            number(su / e, "ultimate_strength", .001, .02)
            ductility = -math.log1p(-ra)
            if estimation == "universal":
                # Original Manson range/cycle equation, transformed exactly to amplitude/reversal convention.
                sf, ef, bv, cv = 1.75 * 2**.12 * su, .5 * 2**.6 * ductility**.6, -.12, -.6
            else:
                sf, ef, bv, cv = .623 * e * (su/e)**.832, .0196 * ductility**.155 * (su/e)**-.53, -.09, -.56
            r.warnings.append("Universal Slopes are rough monotonic-property estimates; a factor-of-three life discrepancy is plausible and is not a guaranteed bound.")
        elif ultimate_strength or reduction_of_area:
            invalid("estimate_method", "monotonic estimate inputs require an estimate method")
        model = StrainCurve(e, sf, ef, bv, cv) if sf is not None else None
        if model is None and kind in ("life", "block", "estimate"):
            invalid("fatigue_strength", "supply strain-life constants or a monotonic estimation method", "input.missing")
        k = q(cyclic_strength, "cyclic_strength", "stress", optional=True)
        nv = number(cyclic_exponent, "cyclic_exponent", 0, 1)
        if (k is None) != (nv == 0):
            invalid("cyclic_strength", "cyclic K' and n' must be supplied together")
        if k is None and model is not None:
            nv = bv / cv
            k = sf / ef**nv
            r.warnings.append("Cyclic K' and n' inferred by compatible CMB elimination: n'=b/c, K'=sigma_f'/epsilon_f'^(b/c); measured cyclic data are preferable.")
        cyclic = None
        if k is not None:
            number(nv, "cyclic_exponent", .02, 1)
            cyclic = Cyclic(e, k, nv)
            r.add("cyclic_strength", k, "MPa")
            r.add("cyclic_exponent", nv)
        if model is not None:
            r.add("fatigue_strength", sf, "MPa")
            r.add("fatigue_ductility", ef)
            r.add("b", bv)
            r.add("c", cv)
            log_transition = math.log(ef * e / sf) / (bv - cv)
            r.add("transition_reversals", positive_exp(log_transition, "fatigue_ductility"))
        sa = q(stress_amplitude, "stress_amplitude", "stress", optional=True)
        ea = q(strain_amplitude, "strain_amplitude", "strain", optional=True)
        nominal = q(nominal_stress, "nominal_stress", "stress", optional=True)
        if sa is not None and ea is not None:
            invalid("strain_amplitude", "supply stress amplitude or strain amplitude, not both")
        if kind not in ("notch", "block") and (nominal is not None or kval != 1 or range_input or reversal_levels or notch_rule != "neuber"):
            invalid("mode", "nominal stress and notch/block settings require notch or block mode")
        if kind == "estimate":
            if ea is not None or sa is not None or maximum is not None or mean != 0:
                invalid("mode", "estimate mode only reports material constants")
            return r.done("fatigue_strength")
        if kind == "block":
            if nominal is not None or sa is not None or ea is not None or range_input or maximum is not None:
                invalid("mode", "block accepts reversal levels and prescribed local mean, not individual amplitudes")
            levels = []
            for i, row in enumerate(records(reversal_levels, "reversal_levels", cap=2000)):
                fields(row, (1,), f"reversal_levels/{i}")
                levels.append(q(row[0], f"reversal_levels/{i}", "stress", signed=True, zero=True))
            if len(levels) < 2 or max(levels) == min(levels):
                invalid("reversal_levels", "block must have at least two nonconstant levels")
            rows = []
            for ran, nominal_mean, count in count_cycles(reversals(levels)):
                local_stress, local_strain = cyclic.local(ran/2, kval, rule)
                two_n = model.life(local_strain, method, mean, local_stress + max(mean, 0))
                damage = 2 * count / two_n
                rows.append([ran, nominal_mean, count, local_stress, local_strain, two_n/2, damage])
            damage = math.fsum(row[-1] for row in rows)
            r.add("damage_per_block", damage)
            r.add("repeats_to_failure", 1 / damage)
            r.data["counted_cycles"] = math.fsum(row[2] for row in rows)
            r.artifacts.append(handle.write_text("strain_block.csv", csv_text(
                ["nominal_range_MPa", "nominal_mean_MPa", "count", "local_amplitude_MPa", "local_strain_amplitude", "life_cycles", "damage"], rows), fmt="csv"))
            r.warnings.append("Block nominal means are reported but not used to infer local mean relaxation. Each range uses the caller's prescribed local mean stress.")
            return r.done("damage_per_block")
        if kind == "notch":
            if nominal is None:
                invalid("nominal_stress", "notch mode needs a nominal stress", "input.missing")
            if sa is not None or ea is not None or reversal_levels:
                invalid("mode", "notch mode accepts nominal stress only")
            if cyclic is None:
                invalid("cyclic_strength", "notch mode needs a cyclic curve", "input.missing")
            sa, ea = cyclic.local(nominal / (2 if range_input else 1), kval, rule)
            if range_input:
                r.add("local_stress_range", 2 * sa, "MPa")
                r.add("local_strain_range", 2 * ea)
            r.add("local_stress", sa, "MPa")
        else:
            if sa is not None:
                if cyclic is None:
                    invalid("cyclic_strength", "stress-to-strain conversion needs a cyclic curve", "input.missing")
                ea = cyclic.strain(sa)
            elif ea is not None and cyclic is not None:
                sa = math.exp(root(lambda ls: math.log(cyclic.strain(math.exp(ls)) / ea),
                                   math.log(max(1e-12, e * ea * 1e-8)), math.log(e * ea), "strain_amplitude"))
            elif ea is None:
                invalid("strain_amplitude", "supply a strain or stress amplitude", "input.missing")
        number(ea, "strain_amplitude", 1e-15, .1)
        r.add("strain_amplitude", ea)
        if sa is not None:
            r.add("stress_amplitude", sa, "MPa")
        if kind == "cyclic":
            if cyclic is None:
                invalid("cyclic_strength", "cyclic mode needs a cyclic curve", "input.missing")
            return r.done("strain_amplitude")
        if model is not None:
            if method == "swt" and maximum is None:
                maximum_value = sa + max(mean, 0) if sa is not None else None
            else:
                maximum_value = maximum
            if maximum is not None and method != "swt":
                invalid("maximum_stress", "maximum stress is used only by SWT")
            two_n = model.life(ea, method, mean, maximum_value)
            r.add("reversals_to_failure", two_n)
            r.add("life", two_n / 2, "count")
            return r.done("life")
        return r.done("strain_amplitude")
    return run("strain_life", inputs, compute)
