"""ASTM E1049 three-point rainflow and linear damage."""

from collections import deque
import math
import re
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit.units import UnitsError

from ._common import Result, SPECS, choice, csv_text, integer, invalid, json_numbers, literal, number, positive_exp, q, run
from ._server import server
from ._sn import correction, curve, sn_points


def reversals(values, threshold=0.):
    """Directional hysteresis: interior excursions < threshold are suppressed; endpoints retained.

    Until the first excursion of at least the threshold, the running minimum and maximum are both
    tracked. When their spread reaches the threshold the older of the two is the first reversal
    (unless it is the starting sample) and the newer one is the current extreme."""
    out = [float(values[0])]
    extreme = out[0]
    low = high = out[0]
    direction = 0
    for raw in values[1:]:
        value = float(raw)
        if direction == 0:
            # The newest sample is the only one that can extend the spread, so it is the newer extreme.
            if value > high:
                high = value
                if high - low >= threshold:
                    if low != out[0]:
                        out.append(low)
                    direction, extreme = 1, high
            elif value < low:
                low = value
                if high - low >= threshold:
                    if high != out[0]:
                        out.append(high)
                    direction, extreme = -1, low
        elif direction > 0:
            if value >= extreme:
                extreme = value
            elif extreme - value >= threshold:
                out.append(extreme)
                extreme, direction = value, -1
        else:
            if value <= extreme:
                extreme = value
            elif value - extreme >= threshold:
                out.append(extreme)
                extreme, direction = value, 1
    if extreme != out[-1]:
        out.append(extreme)
    if float(values[-1]) != out[-1]:
        out.append(float(values[-1]))
    return out


def count_cycles(points):
    """E1049 5.4.4, including one half cycle per adjacent pair in the residue."""
    stack = deque()
    cycles = []
    for value in points:
        stack.append(value)
        while len(stack) >= 3:
            older = abs(stack[-2] - stack[-3])
            newer = abs(stack[-1] - stack[-2])
            if older > newer:
                break
            mean = (stack[-2] + stack[-3]) / 2
            if len(stack) == 3:
                cycles.append((older, mean, .5))
                stack.popleft()
            else:
                cycles.append((older, mean, 1.))
                last = stack.pop()
                stack.pop()
                stack.pop()
                stack.append(last)
    while len(stack) > 1:
        first = stack.popleft()
        cycles.append((abs(first - stack[0]), (first + stack[0]) / 2, .5))
    return [c for c in cycles if c[0] > 0]


_HEADER_FIELD = re.compile(r"[A-Za-z_\u00b5\u00b0][A-Za-z0-9_ .()\[\]/%^*+\u00b5\u00b0-]{0,63}")


def _is_header(parts):
    """A first line of column names: every field starts with a letter or underscore and holds no quotes or other code; nan and inf are not names."""
    return all(_HEADER_FIELD.fullmatch(p) and p.lower().lstrip("+-") not in ("nan", "inf", "infinity") for p in parts)


SPIKE_SIGMAS = 6.0
MAX_SPIKES_LISTED = 5


def history_extremes(arr, times, header, unit):
    """Min, max and range of the history, where they are, and the isolated single-sample spikes in it.

    A spike is an interior sample that is more than ``SPIKE_SIGMAS`` robust sigmas away from BOTH neighbours, on the same side of both (an
    excursion of one sample).  The robust sigma is 1.4826 times the median of the absolute sample-to-sample steps (for white noise this is the
    standard deviation of a step), with a floor of 1e-6 of the history range so that a held (step-like) record does not make every change a spike.
    """
    lo, hi = float(np.min(arr)), float(np.max(arr))
    i_lo, i_hi = int(np.argmin(arr)), int(np.argmax(arr))
    time_name = header.split(",")[0] if header else "time"

    def where(i):
        return {"index": i} if times is None else {"index": i, "time": float(times[i])}

    data = {
        "history_min": lo,
        "history_max": hi,
        "history_range": hi - lo,
        "history_unit": unit,
        "history_extremes": {"min": {"value": lo, **where(i_lo)}, "max": {"value": hi, **where(i_hi)}, "index_base": 0,
                             **({"time_column": time_name} if times is not None else {})},
    }
    warnings = []
    spikes = []
    if len(arr) >= 5:
        step = np.diff(arr)
        sigma = max(1.4826 * float(np.median(np.abs(step))), 1e-6 * (hi - lo))
        before, after = step[:-1], -step[1:]  # x[i] - x[i-1] and x[i] - x[i+1] for the interior samples i = 1 .. n-2
        hit = (np.abs(before) > SPIKE_SIGMAS * sigma) & (np.abs(after) > SPIKE_SIGMAS * sigma) & (np.sign(before) == np.sign(after))
        idx = np.flatnonzero(hit) + 1
        if idx.size:
            depth = np.minimum(np.abs(before[idx - 1]), np.abs(after[idx - 1]))
            order = idx[np.argsort(-depth, kind="stable")]
            spikes = [{**where(int(i)), "value": float(arr[i]), "previous": float(arr[i - 1]), "next": float(arr[i + 1]),
                       "sigmas": float(min(abs(arr[i] - arr[i - 1]), abs(arr[i] - arr[i + 1])) / sigma)} for i in order[:MAX_SPIKES_LISTED]]
            data["spikes"] = {"count": int(idx.size), "listed": spikes, "robust_sigma": sigma, "threshold_sigmas": SPIKE_SIGMAS,
                              "rule": "interior sample more than 6 robust sigmas (1.4826 x median |step|) from both neighbours, on the same side of both"}
            parts = ", ".join(
                f"index {s['index']}" + (f" ({time_name} = {s['time']:g})" if "time" in s else "") + f" = {s['value']:.6g} {unit}" for s in spikes)
            warnings.append(
                f"{int(idx.size)} isolated single-sample spike(s) (more than {SPIKE_SIGMAS:g} robust sigmas from both neighbours): {parts}"
                + (" (largest ones listed)" if idx.size > len(spikes) else "")
                + ". A spike is usually a logger glitch or a wrong scale, and it dominates a rainflow count; check the record before relying on the damage.")
    if not spikes:
        data["spikes"] = {"count": 0, "listed": [], "threshold_sigmas": SPIKE_SIGMAS}
    return {"min": lo, "max": hi, "data": data, "warnings": warnings}


def dominant_cycle(rows, total_damage):
    """The single cycle with the largest damage and its share of ``total_damage`` (None without damage).

    ``rows`` are the lines of rainflow_cycles.csv (range, mean, full, half, count, life, damage); a row is a class of identical cycles, so one
    cycle of it carries ``damage / (full + half)``: a half cycle is one occurrence of the same range and mean with half the weight.
    """
    if not total_damage or total_damage <= 0:
        return None
    index = max(range(len(rows)), key=lambda i: (rows[i][6] or 0.0) / ((rows[i][2] + rows[i][3]) or 1))
    ran, mean, full, half, count, life, dmg = rows[index]
    if not dmg:
        return None
    occurrences = (full + half) or 1
    return {"range": ran, "mean": mean, "index": index, "index_base": 0, "occurrences": occurrences, "weighted_count": count, "life_cycles": life,
            "damage_of_one_cycle": dmg / occurrences, "damage_share": dmg / occurrences / total_damage, "damage_share_of_its_class": dmg / total_damage}


def _history_literal(text, line):
    try:
        return literal(text, "history_file")
    except UnitsError as exc:
        if exc.family == "input.invalid":
            invalid("history_file", f"line {line}: {text[:30]!r} is not a number (the file holds numbers only, after an optional header line of column names)")
        invalid("history_file", f"line {line}: {text[:30]!r} is outside the supported range", exc.family)


def read_history(handle, inline, file):
    """``(values, header, times)``: the samples of the inline list or of a CSV file, the header line the file started with (else None) and the
    time column of a two-column file (else None)."""
    if bool(inline) == bool(file):
        invalid("history", "supply exactly one of history and history_file", "input.missing" if not inline else "input.invalid")
    if inline:
        return json_numbers(inline, "history"), None, None
    if len(file) > 4096:
        invalid("history_file", "path cap is 4096 characters", "input.out_of_range")
    path = handle.input_file(file, param="history_file", extensions=(".csv",), max_bytes=48000000, too_large_family="input.out_of_range")
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        invalid("history_file", "expected UTF-8 numeric CSV")
    values, times, columns, previous_time, header, header_line = [], [], None, None, None, None
    for i, row in enumerate(text.splitlines(), start=1):
        if not row.strip():
            continue
        parts = [p.strip() for p in row.split(",")]
        if columns is None and header is None and _is_header(parts):
            header, header_line = parts, i  # an optional first line of column names
            continue
        if columns is None:
            columns = len(parts)
            if header is not None and len(header) != columns:
                invalid("history_file", f"line {i}: {columns} column(s), but the header on line {header_line} names {len(header)}")
        if len(parts) != columns or columns not in (1, 2):
            invalid("history_file", f"line {i}: {len(parts)} column(s); the file needs one value column or two columns time,value (after an optional header line of names)")
        parsed = [_history_literal(p, i) for p in parts]
        if columns == 2:
            if previous_time is not None and parsed[0] <= previous_time:
                invalid("history_file", f"line {i}: the time column must be strictly increasing")
            previous_time = parsed[0]
            times.append(parsed[0])
        values.append(parsed[-1])
        if len(values) > 1000000:
            invalid("history_file", "point cap is 1,000,000", "input.out_of_range")
    return values, None if header is None else ",".join(header), times or None


@server.tool(description="ASTM E1049 three-point rainflow with half-cycle residue, optional hysteresis gate, range histogram and range-mean matrix. Basquin/Goodman-family Miner damage, endurance cutoff or Haibach extension. Uniaxial stress or count-only force histories; at most 1,000,000 samples; CSV artifacts. Reports the history min/max and warns about a dominant cycle (over 50 % of the damage) or isolated single-sample spikes.")
def rainflow(
    history: Annotated[str, Field(description="Flat JSON number list, e.g. '[0,100,-100,0]'; <=1,000,000 points, 24 MB; '' uses history_file.")] = "",
    history_file: Annotated[str, Field(description="Numeric CSV path, one value column or time,value, optional header line, <=48 MB; '' uses inline history.")] = "",
    history_kind: Annotated[str, Field(description="stress for stress counting/damage, force for count-only loads; selects the sample_scale dimension.")] = "stress",
    sample_scale: Annotated[str, Field(description="Physical value of one history unit, e.g. '1 MPa' for stress or '1 N' for force; required in all modes.")] = "",
    closed: Annotated[bool, Field(description="True treats history as periodic: close and rotate to a global maximum; paired residue becomes full cycles.")] = False,
    hysteresis: Annotated[str, Field(description="Peak-valley reversal gate, same dimension as samples, e.g. '2 MPa'; '' means zero, endpoints retained.")] = "",
    range_bins: Annotated[int, Field(description="Equal-width histogram range bins, 1..256, default 20; edges include the maximum range.")] = 20,
    mean_bins: Annotated[int, Field(description="Equal-width range-mean matrix mean bins, 1..128, default 20; matrix has at most 32768 cells.")] = 20,
    basquin_a: Annotated[str, Field(description="Fatigue Basquin coefficient A, e.g. '1000 MPa'; with b enables damage; '' omits or uses sn_points.")] = "",
    basquin_b: Annotated[float, Field(description="Fatigue Basquin slope b, dimensionless -1..-0.00001; used with A; e.g. -0.1.")] = -.1,
    sn_points: Annotated[str, Field(description="Alternative two failure points 'stress,cycles;stress,cycles', e.g. '400 MPa,1000;200 MPa,1e6'; slope b>=-1; '' omits.")] = "",
    endurance_limit: Annotated[str, Field(description="Optional S-N endurance amplitude, e.g. '150 MPa'; sets knee from A,b; '' means no knee.")] = "",
    below_knee: Annotated[str, Field(description="none (default endurance cutoff), elementary (same slope), or haibach (2m-1, m=-1/b) below the knee.")] = "none",
    mean_method: Annotated[str, Field(description="Per-cycle correction: none, goodman, gerber, soderberg, morrow, swt or walker; compressive mean gives no benefit.")] = "none",
    ultimate_strength: Annotated[str, Field(description="Material ultimate tensile strength, e.g. '600 MPa'; required by Goodman/Gerber; '' omits.")] = "",
    yield_strength: Annotated[str, Field(description="Material tensile yield strength, e.g. '400 MPa'; Soderberg requires it, otherwise optional peak check; '' omits.")] = "",
    fatigue_strength_coefficient: Annotated[str, Field(description="Morrow fatigue coefficient sigma_f', e.g. '900 MPa'; required by Morrow; '' omits.")] = "",
    walker_gamma: Annotated[float, Field(description="Walker gamma on amplitude, dimensionless 0..1, default .5; used only for Walker per-cycle correction.")] = .5,
    repeats: Annotated[float, Field(description="Number of identical history blocks, dimensionless >0..1e12, default 1; damage scales linearly.")] = 1,
    reference_life: Annotated[str, Field(description="Cycles over which to distribute block damage as constant amplitude, e.g. '1e6 cycle'; '' omits equivalent range.")] = "",
):
    inputs = dict(locals())

    def compute(handle):
        r = Result({"rainflow": "ASTM E1049 three-point ranges; residue adjacent pairs each count .5",
                    "Miner": "D=sum(n_i/N_i); repeats_to_failure=1/D; equivalent_range=2*A*(Nref/D)^b",
                    "Haibach": "N=Nk*(Se/Sa)^(2m-1), m=-1/b; elementary uses m"},
                   "Signed uniaxial history. Linear damage, no sequence effects. Open residue is half cycles; periodic histories start at a global maximum. Counts precede binning.",
                   "2..1,000,000 finite samples, nonconstant; 24 MB inline/48 MB CSV. Damage needs stresses and a decreasing S-N curve; each computed finite life must be >=1 cycle.")
        kind = choice(history_kind, "history_kind", ("stress", "force"))
        scale = q(sample_scale, "sample_scale", kind)
        threshold = q(hysteresis, "hysteresis", kind, zero=True, optional=True) or 0.
        nb = integer(range_bins, "range_bins", 1, 256)
        mb = integer(mean_bins, "mean_bins", 1, 128)
        repeat = number(repeats, "repeats", 1e-12, 1e12)
        below = choice(below_knee, "below_knee", ("none", "elementary", "haibach"))
        values, header_skipped, times = read_history(handle, history, history_file)
        if len(values) < 2:
            invalid("history", "at least two samples are required")
        raw = np.asarray(values, dtype=float)
        arr = raw * scale
        lower, upper = SPECS[kind][1:]
        bad = ~np.isfinite(arr) | (np.abs(arr) > upper) | ((raw != 0) & (np.abs(arr) < lower))
        if np.any(bad):
            invalid(f"history/{int(np.flatnonzero(bad)[0])}", "converted sample exceeds supported magnitude", "input.out_of_range")
        if np.ptp(arr) == 0:
            invalid("history", "constant history has no cycles")
        unit = "MPa" if kind == "stress" else "N"
        extremes = history_extremes(arr, times, header_skipped, unit)  # of the history as given, before closing or rotating it
        if closed:
            if arr[0] == arr[-1]:
                arr = arr[:-1]
            start = int(np.argmax(arr))
            arr = np.concatenate((arr[start:], arr[:start], arr[start:start+1]))
        rev = reversals(arr, threshold)
        cycles_list = count_cycles(rev)
        if not cycles_list:
            invalid("hysteresis", "gate leaves no countable range")
        grouped = {}
        for ran, mean, count in cycles_list:
            item = grouped.setdefault((ran, mean), [0, 0])
            item[0 if count == 1 else 1] += 1
        if closed:
            for counts in grouped.values():
                if counts[1] % 2:
                    invalid("history", "periodic residue pairing failed", "numerical.no_convergence")
                counts[0] += counts[1] // 2
                counts[1] = 0
        counts_total = [f + .5 * h for f, h in grouped.values()]
        ranges = [k[0] for k in grouped]
        means = [k[1] for k in grouped]
        hist, edges = np.histogram(ranges, bins=nb, range=(0, max(ranges)), weights=counts_total)
        matrix, redges, medges = np.histogram2d(ranges, means, bins=(edges, mb), weights=counts_total)
        r.data.update(range_histogram={"edges": edges, "counts": hist, "unit": unit},
                      range_mean_matrix={"range_edges": redges, "mean_edges": medges, "counts": matrix, "unit": unit},
                      input_samples=len(values), reversal_count=len(rev), closed=closed)
        r.data.update(extremes["data"])
        r.add("history_max", extremes["max"], unit)
        r.add("history_min", extremes["min"], unit)
        r.warnings.extend(extremes["warnings"])
        r.add("full_cycles", sum(f for f, h in grouped.values()))
        r.add("half_cycles", sum(h for f, h in grouped.values()))
        r.add("weighted_cycles", math.fsum(counts_total), "count")
        r.add("range_count_sum", math.fsum(ran * count for ran, count in zip(ranges, counts_total)), unit)
        r.add("filtered_total_variation", math.fsum(abs(b-a) for a, b in zip(rev, rev[1:])), unit)
        model = None
        if basquin_a and sn_points:
            invalid("sn_points", "give A,b or two points, not both")
        if basquin_a or sn_points:
            if kind != "stress":
                invalid("history_kind", "force histories support counting only; convert loads to stresses first")
            if sn_points:
                from ._sn import sn_points as fit_points
                a, b, stats, runouts, failures = fit_points(sn_points, param="sn_points")
                if len(failures) != 2 or runouts:
                    invalid("sn_points", "exactly two failure points, without runouts, are required")
                # Same slope limit as direct basquin_b: b>=-1 keeps Haibach's 2m-1 >= m (m=-1/b).
                number(b, "sn_points", -1, -1e-5)
            else:
                a, b = q(basquin_a, "basquin_a", "stress"), number(basquin_b, "basquin_b", -1, -1e-5)
            se = q(endurance_limit, "endurance_limit", "stress", optional=True)
            model = curve(a, b, se)
            if below != "none" and se is None:
                invalid("endurance_limit", "below-knee extension requires an endurance knee", "input.missing")
        elif endurance_limit or reference_life or mean_method != "none" or ultimate_strength or yield_strength or fatigue_strength_coefficient or below != "none":
            invalid("basquin_a", "damage settings require an S-N curve", "input.missing")
        corr = correction(mean_method, ultimate_strength, yield_strength, fatigue_strength_coefficient, walker_gamma)
        rows, damage_terms = [], []
        for (ran, mean), (full, half) in grouped.items():
            count = full + half / 2
            damage, life = None, None
            if model is not None:
                eq = corr.equivalent(ran / 2, mean, "history")
                log_life = model.log_life(eq, below)
                if log_life is not None:
                    if log_life < 0:
                        invalid("history", "S-N predicts life below one cycle", "input.out_of_range")
                    life = positive_exp(log_life, "history")
                    damage = count / life
                else:
                    damage = 0.
                damage_terms.append(damage)
            rows.append([ran, mean, full, half, count, life, damage])
        r.artifacts.append(handle.write_text("rainflow_cycles.csv", csv_text(
            ["range_"+unit, "mean_"+unit, "full_cycles", "half_cycles", "weighted_count", "life_cycles", "damage_per_block"], rows), fmt="csv"))
        r.data["cycle_preview"] = rows[:50]
        r.data["header_skipped"] = header_skipped
        if model is not None:
            damage = math.fsum(damage_terms)
            dominant = dominant_cycle(rows, damage)
            if dominant is not None:
                r.data["dominant_cycle"] = dominant
                if dominant["damage_share"] > .5 and len(rows) > 1:
                    r.warnings.append(
                        f"One cycle carries {100 * dominant['damage_share']:.0f} % of the damage: range {dominant['range']:.6g} {unit}, mean {dominant['mean']:.6g} {unit} "
                        f"(row {dominant['index']}, counting from 0, of rainflow_cycles.csv; it occurs {dominant['occurrences']} time(s)). The result rests on that one cycle: "
                        "check it is a real load and not a spike, a drift or a wrong scale before relying on the life.")
            r.add("damage_per_block", damage)
            r.add("damage", damage * repeat)
            if damage:
                r.add("repeats_to_failure", 1 / damage)
            else:
                r.data["repeats_to_failure"] = None
                r.data["damage_status"] = "all cycles at or below endurance; unbounded repeats omitted"
            ref = q(reference_life, "reference_life", "cycles", optional=True)
            if ref is not None:
                number(ref, "reference_life", 1, 1e18)
                if damage == 0:
                    r.data["equivalent_range"] = None
                    r.warnings.append("Zero damage does not define a unique equivalent range on an endurance plateau.")
                else:
                    log_n = math.log(ref / damage)
                    if model.knee is not None and log_n > math.log(model.knee) and below != "none":
                        slope = -1 / model.b if below == "elementary" else -2 / model.b - 1
                        eq = positive_exp(math.log(model.endurance) - (log_n - math.log(model.knee)) / slope, "reference_life")
                    else:
                        eq = positive_exp(math.log(model.a) + model.b * log_n, "reference_life")
                        if model.endurance is not None and eq <= model.endurance:
                            invalid("reference_life", "no exact positive-damage equivalent exists on the endurance plateau")
                    r.add("equivalent_range", 2 * eq, "MPa")
            if not yield_strength:
                r.warnings.append("Yield strength omitted; per-cycle elastic peak validity was not checked.")
        if threshold:
            r.warnings.append("The hysteresis gate alters interior reversals and damage; endpoints are retained. Range identities refer to the filtered history.")
        return r.done("damage" if model is not None else "weighted_cycles")
    return run("rainflow", inputs, compute)
