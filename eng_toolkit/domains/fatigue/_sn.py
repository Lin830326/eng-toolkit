"""Shared uniaxial stress-life equations, with stresses in MPa and life in cycles."""

from dataclasses import dataclass
import math

from ._common import choice, fields, invalid, literal, number, positive_exp, q, records, regression

SURFACES = {"ground": (1.58, -0.085), "machined": (4.51, -0.265),
            "hot_rolled": (57.7, -0.718), "as_forged": (272.0, -0.995)}
RELIABILITY = {50: 1.0, 90: .897, 95: .868, 99: .814, 99.9: .753, 99.99: .702}
METHODS = ("none", "goodman", "gerber", "soderberg", "morrow", "swt", "walker")


@dataclass(frozen=True)
class Correction:
    method: str
    ultimate: float | None = None
    yield_strength: float | None = None
    coefficient: float | None = None
    gamma: float = .5

    def equivalent(self, amplitude, mean, param="mean_stress"):
        # No compressive-mean benefit is claimed, for any supported method.
        m = max(0., mean)
        if self.ultimate is not None and m >= self.ultimate:
            invalid(param, "tensile mean stress must be below ultimate strength")
        if self.yield_strength is not None and amplitude + abs(mean) > self.yield_strength:
            invalid(param, "peak stress exceeds yield; use local strain analysis")
        strength = {"goodman": self.ultimate, "gerber": self.ultimate,
                    "soderberg": self.yield_strength, "morrow": self.coefficient}.get(self.method)
        if strength is not None:
            if m >= strength:
                invalid(param, "mean stress reaches the correction's limiting strength")
            ratio = m / strength
            return amplitude / ((1 - ratio) * (1 + ratio) if self.method == "gerber" else 1 - ratio)
        if self.method in ("swt", "walker"):
            gamma = .5 if self.method == "swt" else self.gamma
            return positive_exp(gamma * math.log(amplitude) + (1 - gamma) * math.log(amplitude + m), param)
        return amplitude

    def safety(self, amplitude, mean, strength):
        m = max(0., mean)
        a = amplitude / strength
        limit = {"goodman": self.ultimate, "gerber": self.ultimate,
                 "soderberg": self.yield_strength, "morrow": self.coefficient}.get(self.method)
        if limit is not None:
            b = m / limit
            return 2 / (a + math.hypot(a, 2 * b)) if self.method == "gerber" else 1 / (a + b)
        return strength / self.equivalent(amplitude, mean)


def correction(method, ultimate, yield_strength, coefficient, gamma):
    choice(method, "mean_method", METHODS)
    su = q(ultimate, "ultimate_strength", "stress", optional=True)
    sy = q(yield_strength, "yield_strength", "stress", optional=True)
    sf = q(coefficient, "fatigue_strength_coefficient", "stress", optional=True)
    g = number(gamma, "walker_gamma", 0, 1)
    if su is not None and sy is not None and sy > su:
        invalid("yield_strength", "yield cannot exceed ultimate strength")
    required = {"goodman": (su, "ultimate_strength"), "gerber": (su, "ultimate_strength"),
                "soderberg": (sy, "yield_strength"), "morrow": (sf, "fatigue_strength_coefficient")}
    if method in required and required[method][0] is None:
        invalid(required[method][1], f"{method} requires this strength", "input.missing")
    return Correction(method, su, sy, sf, g)


@dataclass(frozen=True)
class SNCurve:
    a: float
    b: float
    endurance: float | None = None
    knee: float | None = None

    def stress(self, cycles):
        value = positive_exp(math.log(self.a) + self.b * math.log(cycles), "cycles")
        return max(self.endurance or 0, value)

    def log_life(self, stress, below="none"):
        if self.endurance is not None and stress <= self.endurance:
            if below == "none":
                return None
            m = -1 / self.b
            slope = 2 * m - 1 if below == "haibach" else m
            return math.log(self.knee) + slope * math.log(self.endurance / stress)
        return (math.log(stress) - math.log(self.a)) / self.b


FIT_METHODS = ("e739", "s_on_n")


def sn_points(text, method="e739", param="points"):
    """Basquin fit of failures. e739: log10 N on log10 S (ASTM E739, life dependent), then
    inverted to S=A*N^b. s_on_n: log10 S on log10 N. Runouts are excluded from both."""
    failures, runouts = [], []
    for i, row in enumerate(records(text, param)):
        path = f"{param}/{i}"
        fields(row, (2, 3), path)
        stress = q(row[0], path + "/stress", "stress")
        cycles = literal(row[1], path + "/cycles", 1, 1e18)
        status = choice(row[2], path + "/status", ("failure", "runout")) if len(row) == 3 else "failure"
        (runouts if status == "runout" else failures).append((stress, cycles))
    log_s = [math.log10(s) for s, n in failures]
    log_n = [math.log10(n) for s, n in failures]
    if method == "e739":
        stats = regression(log_s, log_n, param)
        if stats["slope"] >= 0:
            invalid(param, "fitted life must decrease with stress amplitude")
        b = number(1 / stats["slope"], param, -2, -1e-5)
        a = positive_exp(-stats["intercept"] / stats["slope"] * math.log(10), param)
        response = "life"
    else:
        stats = regression(log_n, log_s, param)
        b = number(stats["slope"], param, -2, -1e-5)
        a = positive_exp(stats["intercept"] * math.log(10), param)
        response = "stress"
    se, scatter = stats.pop("standard_error_log10"), stats.pop("scatter_factor_2se")
    stats.update({"method": method, "regression": "log10 N on log10 S" if method == "e739" else "log10 S on log10 N",
                  f"standard_error_log10_{response}": se, f"{response}_scatter_factor_2se": scatter,
                  "regression_slope": stats.pop("slope"), "regression_intercept": stats.pop("intercept")})
    return a, b, stats, runouts, failures


def curve(a, b, endurance=None, knee=None):
    if endurance is not None and knee is not None:
        predicted = positive_exp(math.log(a) + b * math.log(knee), "knee_cycles")
        if not math.isclose(predicted, endurance, rel_tol=1e-6):
            invalid("knee_cycles", "knee and endurance must lie on the Basquin curve")
    if endurance is not None and knee is None:
        knee = positive_exp((math.log(endurance) - math.log(a)) / b, "endurance_limit")
        number(knee, "endurance_limit", 1, 1e18)
    if knee is not None and endurance is None:
        endurance = positive_exp(math.log(a) + b * math.log(knee), "knee_cycles")
    return SNCurve(a, b, endurance, knee)
