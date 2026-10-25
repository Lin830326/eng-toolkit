"""``spice_simulate`` and ``spice_check_netlist``.

Both read the netlist as data through :mod:`~eng_toolkit.domains.spice.netlist` (an allow-list parser).  ``spice_simulate`` then builds the ngspice
deck itself from validated parameters and runs it through :mod:`eng_toolkit.external`; ``spice_check_netlist`` never starts a program.

Sign convention (ngspice, as in every SPICE): the current of a voltage source is positive when it flows **into** the source's + terminal, so a
source that drives a load shows a negative current (5 V across 2 kOhm: ``i(v1) = -2.5 mA``).  ``i()`` is available for voltage sources and inductors.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import Artifact, QuantityOut
from eng_toolkit.domains.spice import engine as ng
from eng_toolkit.domains.spice import netlist as nlmod
from eng_toolkit.domains.spice._server import ENGINE, server
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError, parse_quantity

TOOL_NAMES: tuple[str, ...] = ("simulate", "check_netlist", "measure")

NETLIST_EXTENSIONS = (".cir", ".sp", ".spice", ".net", ".ckt", ".txt")
MAX_SWEEP_POINTS = 100_000  # dc and tran
MAX_AC_POINTS = 50_000
MAX_AC_PER_DECADE = 500
FREQ_RANGE_HZ = (1e-3, 1e12)
TIME_RANGE_S = (1e-15, 1e6)
SWEEP_RANGE = 1e6  # volts or amperes of a DC sweep
LOW_DB = -400.0

_OUT_V = re.compile(r"^v\(([a-z0-9_][a-z0-9_.\-#]*)\)$")
_OUT_I = re.compile(r"^i\(([a-z][a-z0-9_.\-]*)\)$")

STANDING = (
    "ngspice solves the circuit numerically: operating points are exact to the solver tolerances (reltol 1e-3 by default), time-domain results also carry "
    "the integration error of the time step control. Device models are only as good as the .model parameters supplied.",
)
ANALYSES = ("op", "dc", "ac", "tran")


# --------------------------------------------------------------------------- input


def read_netlist(handle: JobHandle, netlist: str, netlist_file: str) -> str:
    """The netlist text from exactly one of the inline parameter and a file read through ``JobHandle.input_file``."""
    inline = bool(netlist and netlist.strip())
    given_file = bool(netlist_file and netlist_file.strip())
    if inline == given_file:
        raise ToolFailure(
            "input.invalid" if inline else "input.missing",
            "give exactly one of 'netlist' (the text) and 'netlist_file' (a path)" if inline else "give the netlist as text in 'netlist' or as a path in 'netlist_file'",
            {"params": ["netlist", "netlist_file"], "param": "/netlist"},
        )
    if inline:
        return netlist
    path = handle.input_file(netlist_file.strip(), param="netlist_file", extensions=NETLIST_EXTENSIONS, max_bytes=nlmod.MAX_BYTES)
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolFailure(
            "input.invalid",
            f"the netlist file is not valid UTF-8 (the byte at offset {exc.start} cannot be decoded)",
            {"reason_code": "encoding", "offset": exc.start, "param": "/netlist_file"},
        ) from None


def netlist_param(netlist: str) -> str:
    """The pointer to the argument the netlist text came from: ``/netlist`` when text was given, else ``/netlist_file``."""
    return "/netlist" if netlist and netlist.strip() else "/netlist_file"


def load(handle: JobHandle, netlist: str, netlist_file: str) -> nlmod.Netlist:
    text = read_netlist(handle, netlist, netlist_file)
    try:
        return nlmod.parse(text)
    except ToolFailure as exc:
        exc.details.setdefault("param", netlist_param(netlist))
        raise


def _split_outputs(text: str) -> list[str]:
    return [item.strip().lower() for item in text.split(",") if item.strip()]


def choose_outputs(nl: nlmod.Netlist, outputs: str) -> list[str]:
    names = _split_outputs(outputs)
    if not names:
        if not nl.nodes:
            raise ToolFailure("input.invalid", "the circuit has no node to report", {"param": "outputs"})
        if len(nl.nodes) > ng.MAX_OUTPUTS:
            raise ToolFailure(
                "input.invalid",
                f"'outputs' is empty and the circuit has {len(nl.nodes)} nodes (more than {ng.MAX_OUTPUTS}); list the ones you want, e.g. 'v({nl.nodes[0]})'",
                {"param": "outputs", "nodes": len(nl.nodes)},
            )
        return [f"v({n})" for n in nl.nodes]
    if len(names) > ng.MAX_OUTPUTS:
        raise ToolFailure("input.too_large", f"'outputs' lists {len(names)} items, over the limit of {ng.MAX_OUTPUTS}", {"param": "outputs", "limit": ng.MAX_OUTPUTS})
    chosen: list[str] = []
    for name in names:
        v = _OUT_V.match(name)
        i = _OUT_I.match(name)
        if v:
            node = v.group(1)
            if node in ("0", "gnd"):
                raise ToolFailure("input.invalid", "'outputs': v(0) is the reference node and always zero; name another node", {"param": "outputs", "output": name})
            if node not in nl.nodes:
                raise ToolFailure(
                    "input.invalid",
                    f"'outputs': {nlmod.excerpt(name, 40)!r} names a node the circuit does not have; nodes: {', '.join(nl.nodes[:20])}",
                    {"param": "outputs", "output": nlmod.excerpt(name, 40), "nodes": nl.nodes[:20]},
                )
        elif i:
            src = i.group(1)
            letter = nl.sources.get(src)
            if letter not in ("v", "l"):
                raise ToolFailure(
                    "input.invalid",
                    f"'outputs': i({nlmod.excerpt(src, 30)}) needs the name of a voltage source or an inductor of the top level; found: "
                    f"{', '.join(n for n, k in nl.sources.items() if k in 'vl') or 'none'}",
                    {"param": "outputs", "output": nlmod.excerpt(name, 40)},
                )
        else:
            raise ToolFailure(
                "input.invalid",
                f"'outputs': {nlmod.excerpt(name, 40)!r} is not 'v(node)' or 'i(source)'",
                {"param": "outputs", "output": nlmod.excerpt(name, 40)},
            )
        if name not in chosen:
            chosen.append(name)
    return chosen


def _unit_of(output: str) -> str:
    return "volt" if output.startswith("v(") else "ampere"


def _quantity(raw: str, *, name: str, dimension: str, unit: str, lo: float, hi: float, allow_zero: bool = False) -> float:
    """``raw`` (a string with a unit) in ``unit``; its magnitude must lie in ``[lo, hi]`` (zero allowed for ``allow_zero``)."""
    if not raw or not raw.strip():
        raise UnitsError("input.missing", f"'{name}' is required for this analysis", {"param": name})
    qty = parse_quantity(raw, name=name, dimension=dimension)
    if "radian" in str(qty.units) or "revolution" in str(qty.units) or "rpm" in str(qty.units):
        raise UnitsError("units.parse_error", f"'{name}': give hertz (e.g. '1 kHz'), not an angular unit", {"param": name, "received": raw})
    value = float(qty.to(unit).magnitude)
    if not math.isfinite(value):
        raise UnitsError("input.invalid", f"'{name}' is not finite", {"param": name})
    magnitude = abs(value)
    if not ((allow_zero and value == 0.0) or lo <= magnitude <= hi):
        raise UnitsError(
            "input.out_of_range",
            f"'{name}' = {raw.strip()} is outside the supported range {lo:g} .. {hi:g} {unit}",
            {"param": name, "received": raw.strip(), "min": lo, "max": hi, "unit": unit},
        )
    return value


def analysis_script(nl: nlmod.Netlist, analysis: str, *, sweep_source: str, start: str, stop: str, step: str, points: int, sweep: str, uic: bool) -> tuple[list[str], dict[str, Any]]:
    """The control-script lines for the analysis and a description of it; every number comes from a parsed, range-checked parameter."""
    if analysis not in ANALYSES:
        raise ToolFailure("input.invalid", f"'analysis' must be one of {list(ANALYSES)}, got {nlmod.excerpt(str(analysis), 30)!r}", {"param": "analysis", "allowed": list(ANALYSES)})
    if analysis == "op":
        return ["op"], {"analysis": "op"}
    if analysis == "dc":
        src = (sweep_source or "").strip().lower()
        letter = nl.sources.get(src)
        if letter not in ("v", "i"):
            raise ToolFailure(
                "input.invalid",
                f"'sweep_source' must name a voltage or current source of the top level; found: {', '.join(n for n, k in nl.sources.items() if k in 'vi') or 'none'}",
                {"param": "sweep_source"},
            )
        dim, unit = ("[electric_potential]", "volt") if letter == "v" else ("[current]", "ampere")
        a = _quantity(start, name="start", dimension=dim, unit=unit, lo=1e-15, hi=SWEEP_RANGE, allow_zero=True)
        b = _quantity(stop, name="stop", dimension=dim, unit=unit, lo=1e-15, hi=SWEEP_RANGE, allow_zero=True)
        h = _quantity(step, name="step", dimension=dim, unit=unit, lo=1e-15, hi=SWEEP_RANGE)
        if a == b:
            raise UnitsError("input.invalid", "'start' and 'stop' are equal; a DC sweep needs a range (use analysis 'op' for one point)", {"param": "stop"})
        count = math.floor(abs(b - a) / abs(h) + 1e-9) + 1
        if count > MAX_SWEEP_POINTS:
            raise UnitsError(
                "input.out_of_range",
                f"this sweep has {count:g} points, over the limit of {MAX_SWEEP_POINTS}; use a larger 'step'",
                {"param": "step", "points": count, "limit": MAX_SWEEP_POINTS},
            )
        signed = abs(h) if b > a else -abs(h)
        return [f"dc {src} {ng.format_number(a, '/start')} {ng.format_number(b, '/stop')} {ng.format_number(signed, '/step')}"], {
            "analysis": "dc",
            "source": src,
            "start": a,
            "stop": b,
            "step": signed,
            "unit": unit,
            "points": count,
        }
    if analysis == "ac":
        kind = (sweep or "dec").strip().lower()
        if kind not in ("dec", "oct", "lin"):
            raise ToolFailure("input.invalid", "'sweep' must be 'dec', 'oct' or 'lin'", {"param": "sweep"})
        f0 = _quantity(start, name="start", dimension="1 / [time]", unit="hertz", lo=FREQ_RANGE_HZ[0], hi=FREQ_RANGE_HZ[1])
        f1 = _quantity(stop, name="stop", dimension="1 / [time]", unit="hertz", lo=FREQ_RANGE_HZ[0], hi=FREQ_RANGE_HZ[1])
        if isinstance(points, bool) or not isinstance(points, int) or points < 1:
            raise ToolFailure("input.invalid", "'points' must be a whole number >= 1 (points per decade or octave, or the total for 'lin')", {"param": "points"})
        if f1 < f0 or (f1 == f0 and not (kind == "lin" and points == 1)):
            raise UnitsError("input.invalid", "'stop' must be above 'start' (equal only for 'lin' with 'points' = 1)", {"param": "stop"})
        if kind == "lin":
            count = points
        else:
            span = math.log10(f1 / f0) if kind == "dec" else math.log2(f1 / f0)
            if points > MAX_AC_PER_DECADE:
                raise ToolFailure("input.out_of_range", f"'points' = {points} is over the limit of {MAX_AC_PER_DECADE} per decade or octave", {"param": "points", "limit": MAX_AC_PER_DECADE})
            count = math.floor(points * span + 1e-9) + 1
        if count > MAX_AC_POINTS:
            raise ToolFailure("input.out_of_range", f"this sweep has {count} points, over the limit of {MAX_AC_POINTS}", {"param": "points", "points": count, "limit": MAX_AC_POINTS})
        return [f"ac {kind} {points} {ng.format_number(f0, '/start')} {ng.format_number(f1, '/stop')}"], {
            "analysis": "ac",
            "sweep": kind,
            "points_param": points,
            "start_hz": f0,
            "stop_hz": f1,
            "points": count,
        }
    # tran
    h = _quantity(step, name="step", dimension="[time]", unit="second", lo=TIME_RANGE_S[0], hi=TIME_RANGE_S[1])
    t1 = _quantity(stop, name="stop", dimension="[time]", unit="second", lo=TIME_RANGE_S[0], hi=TIME_RANGE_S[1])
    t0 = _quantity(start, name="start", dimension="[time]", unit="second", lo=TIME_RANGE_S[0], hi=TIME_RANGE_S[1], allow_zero=True) if (start and start.strip()) else 0.0
    if t1 <= t0:
        raise UnitsError("input.invalid", "'stop' must be later than 'start'", {"param": "stop"})
    if h > t1 - t0:
        raise UnitsError("input.invalid", "'step' is longer than the run ('stop' - 'start'); the step is the spacing of the output points", {"param": "step"})
    count = math.floor((t1 - t0) / h + 1e-9) + 1
    if count > MAX_SWEEP_POINTS:
        raise UnitsError(
            "input.out_of_range",
            f"this transient has {count:g} points ((stop - start) / step), over the limit of {MAX_SWEEP_POINTS}; use a larger 'step' or a shorter run",
            {"param": "step", "points": count, "limit": MAX_SWEEP_POINTS},
        )
    line = f"tran {ng.format_number(h, '/step')} {ng.format_number(t1, '/stop')} {ng.format_number(t0, '/start')} {ng.format_number(h, '/step')}" + (" uic" if uic else "")
    return [line], {"analysis": "tran", "start_s": t0, "stop_s": t1, "step_s": h, "uic": bool(uic), "points": count}


# --------------------------------------------------------------------------- result building


def _stats(name: str, xs: list[float], ys: list[float]) -> dict[str, float]:
    lo = min(range(len(ys)), key=ys.__getitem__)
    hi = max(range(len(ys)), key=ys.__getitem__)
    return {"min": ys[lo], "min_at": xs[lo], "max": ys[hi], "max_at": xs[hi], "first": ys[0], "final": ys[-1]}


def _csv(header: list[str], columns: list[list[float]]) -> str:
    rows = [",".join(header)]
    for row in zip(*columns):
        rows.append(",".join(repr(float(v)) for v in row))
    return "\n".join(rows) + "\n"


def ac_columns(table: ng.Table, outputs: list[str]) -> dict[str, list[float]]:
    """Real, imaginary, magnitude, dB (floored at -400 dB) and phase in degrees (wrapped to -180..180) of every complex output."""
    out: dict[str, list[float]] = {}
    for name in outputs:
        re_, im_ = table.columns[f"{name}.re"], table.columns[f"{name}.im"]
        mag = [math.hypot(a, b) for a, b in zip(re_, im_)]
        out[f"{name}.re"] = re_
        out[f"{name}.im"] = im_
        out[f"{name}.mag"] = mag
        out[f"{name}.db"] = [20.0 * math.log10(m) if m > 1e-20 else LOW_DB for m in mag]
        out[f"{name}.phase_deg"] = [math.degrees(math.atan2(b, a)) for a, b in zip(re_, im_)]
    return out


def _finite_values(values: dict[str, QuantityOut]) -> None:
    for key, q in values.items():
        if not math.isfinite(q.quantity):
            raise ToolFailure("engine.failure", f"the result {key} is not a finite number", {"reason_code": "non_finite"})


def build_result(handle: JobHandle, table: ng.Table, outputs: list[str], described: dict[str, Any]) -> Computed:
    analysis = described["analysis"]
    values: dict[str, QuantityOut] = {}
    data: dict[str, Any] = {"analysis": described, "outputs": outputs}
    artifacts: list[Artifact] = []
    if analysis == "op":
        for name in outputs:
            values[name] = QuantityOut(quantity=table.columns[name][0], unit=_unit_of(name))
        _finite_values(values)
        return Computed(values=values, primary=outputs[0], data=data, artifacts=artifacts)

    xs = table.x
    x_name = {"dc": f"sweep_{described.get('source', '')}", "ac": "frequency", "tran": "time"}[analysis]
    x_unit = {"dc": described.get("unit", ""), "ac": "hertz", "tran": "second"}[analysis]
    if analysis == "ac":
        cols = ac_columns(table, outputs)
        header = [x_name, *cols]
        summary = {}
        for name in outputs:
            mag = _stats(name, xs, cols[f"{name}.mag"])
            summary[name] = {
                "magnitude": mag,
                "magnitude_peak_frequency_hz": mag["max_at"],
                "db_first": cols[f"{name}.db"][0],
                "db_final": cols[f"{name}.db"][-1],
                "phase_deg_first": cols[f"{name}.phase_deg"][0],
                "phase_deg_final": cols[f"{name}.phase_deg"][-1],
            }
            unit = _unit_of(name)
            values[f"{name}.min"] = QuantityOut(quantity=mag["min"], unit=unit)
            values[f"{name}.max"] = QuantityOut(quantity=mag["max"], unit=unit)
            values[f"{name}.final"] = QuantityOut(quantity=mag["final"], unit=unit)
    else:
        cols = dict(table.columns)
        header = [x_name, *cols]
        summary = {}
        for name in outputs:
            st = _stats(name, xs, cols[name])
            summary[name] = st
            unit = _unit_of(name)
            values[f"{name}.min"] = QuantityOut(quantity=st["min"], unit=unit)
            values[f"{name}.max"] = QuantityOut(quantity=st["max"], unit=unit)
            values[f"{name}.final"] = QuantityOut(quantity=st["final"], unit=unit)
    _finite_values(values)
    artifacts.append(handle.write_text("result.csv", _csv(header, [xs, *cols.values()]), fmt="csv"))
    data["x"] = {"name": x_name, "unit": x_unit, "first": xs[0], "last": xs[-1], "samples": len(xs)}
    data["summary"] = summary
    data["table"] = {"artifact": "result.csv", "columns": header, "rows": len(xs)}
    if analysis == "ac":
        data["table"]["note"] = "magnitude in volt or ampere per unit source; phase in degrees wrapped to (-180, 180]; dB floored at -400"
    if analysis == "tran":
        data["table"]["note"] = "rows are ngspice's own time points (not a uniform grid); the step limits the largest interval"
    return Computed(values=values, primary=f"{outputs[0]}.final" if analysis != "ac" else f"{outputs[0]}.max", data=data, artifacts=artifacts)


def netlist_summary(nl: nlmod.Netlist) -> dict[str, Any]:
    return {
        "title": nlmod.excerpt(nl.title, 80),
        "title_forwarded": False,
        "elements": nlmod.element_counts(nl),
        "element_count": len(nl.top.elements),
        "expanded_elements": nl.expanded_elements,
        "nodes": nl.nodes[:200],
        "node_count": len(nl.nodes),
        "ground": nl.has_ground,
        "models": dict(sorted(nl.top.models.items())),
        "subckts": {n: s.ports for n, s in nl.scopes.items() if n},
        "params": nl.params[:50],
        "options": nl.options,
    }


# --------------------------------------------------------------------------- spice_simulate


@server.tool(
    name="simulate",
    description=(
        "Simulate a circuit with ngspice and return numbers. Give the netlist as text ('netlist') or a file ('netlist_file'), choose 'analysis' "
        "op (DC operating point), dc (sweep one source), ac (small-signal frequency sweep) or tran (transient), and list 'outputs' such as 'v(out),i(v1)'. "
        "op returns every output as a value; sweeps return min/max/final of each output (ac: of the magnitude) and write the full table to result.csv. "
        "The first line of a netlist is its title. The netlist is parsed against an allow-list: .control, .include, .lib, .write, code models (A, N "
        "devices) and the like are refused; analyses come from these parameters, not from the netlist. ngspice must be installed (see toolkit_doctor). "
        "Current sign: i(v1) is positive into the + terminal, so a source driving a load shows a negative current."
    ),
)
def simulate(
    analysis: Annotated[str, Field(description="One of: op, dc, ac, tran")],
    netlist: Annotated[str, Field(description="The netlist text (first line = title). Give this OR netlist_file.")] = "",
    netlist_file: Annotated[str, Field(description="Path to a netlist file (.cir .sp .spice .net .ckt .txt). Give this OR netlist.")] = "",
    outputs: Annotated[str, Field(description="Comma-separated, e.g. 'v(out),i(v1)': v(node) of a node, i(name) of a voltage source or inductor. Empty = every node voltage (at most 16 nodes).")] = "",
    sweep_source: Annotated[str, Field(description="dc only: the name of the voltage or current source to sweep, e.g. 'V1'")] = "",
    start: Annotated[str, Field(description="dc: first value with unit ('0 V'); ac: first frequency ('10 Hz'); tran: first time kept (default '0 s')")] = "",
    stop: Annotated[str, Field(description="dc: last value ('5 V'); ac: last frequency ('1 MHz'); tran: end time ('1 ms')")] = "",
    step: Annotated[str, Field(description="dc: increment with unit ('0.1 V'); tran: output step and largest internal step ('10 us'); at most 100000 points")] = "",
    points: Annotated[int, Field(description="ac only: points per decade/octave (e.g. 20), or the total number of points for sweep 'lin'")] = 0,
    sweep: Annotated[str, Field(description="ac only: spacing 'dec' (default), 'oct' or 'lin'")] = "dec",
    use_initial_conditions: Annotated[bool, Field(description="tran only: start from the .ic / ic= values instead of a DC operating point (SPICE 'uic')")] = False,
):
    inputs: dict[str, Any] = {
        "analysis": analysis,
        "netlist": netlist,
        "netlist_file": netlist_file,
        "outputs": outputs,
        "sweep_source": sweep_source,
        "start": start,
        "stop": stop,
        "step": step,
        "points": points,
        "sweep": sweep,
        "use_initial_conditions": use_initial_conditions,
    }

    def compute(handle: JobHandle) -> Computed:
        nl = load(handle, netlist, netlist_file)
        if nl.problems:
            first = nl.problems[0]
            raise ToolFailure(
                "input.netlist_invalid",
                f"the netlist cannot be simulated: line {first['line']}: {first['message']}" + (f" (and {len(nl.problems) - 1} more problems)" if len(nl.problems) > 1 else ""),
                {"problems": nl.problems[:10], "problem_count": len(nl.problems), "param": netlist_param(netlist)},
            )
        chosen = choose_outputs(nl, outputs)
        script, described = analysis_script(
            nl,
            analysis,
            sweep_source=sweep_source,
            start=start,
            stop=stop,
            step=step,
            points=points,
            sweep=sweep,
            uic=use_initial_conditions,
        )
        emitted = "\n".join(["* eng-toolkit: the circuit exactly as it was handed to ngspice (normalised, comments and title removed)", *nl.body, ""])
        net_art = handle.write_text("netlist.cir", emitted, fmt="spice")
        job_dir = Path(net_art.path).parent
        artifacts = [net_art]

        def write_file(name: str, text: str) -> None:
            artifacts.append(handle.write_text(name, text, fmt="spice"))

        deck = ng.build_deck(nl.body, script, chosen)
        table, record, ngspice_warnings = ng.simulate(deck, job_dir, write_file=write_file, complex_columns=analysis == "ac", outputs=chosen, netlist_param=netlist_param(netlist))
        computed = build_result(handle, table, chosen, described)
        log_file = job_dir / "ngspice.log"
        if log_file.is_file():
            artifacts.append(handle.write_bytes("ngspice.log", log_file.read_bytes(), fmt="text"))
        info = {
            "program": record.program,
            "found_via": record.source,
            "seconds": round(record.seconds, 3),
            "returncode": record.returncode,
            "limits": {**record.limits, "memory_cap_applied": record.memory_cap_applied},
            "version_banner": _banner(record.stdout_tail),
        }
        computed.data["engine"] = info
        computed.data["netlist"] = netlist_summary(nl)
        computed.data["sign_convention"] = "i(v) is positive when the current flows into the + terminal of the source (a source driving a load shows a negative current)"
        computed.artifacts = artifacts + computed.artifacts
        computed.warnings = [*nl.warnings, *ngspice_warnings, *computed.warnings]
        return computed

    return run_tool(
        tool_name="spice_simulate",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=STANDING,
    )


def _banner(text: str) -> str:
    m = re.search(r"ngspice-\d+(?:\.\d+)*", text)
    return m.group(0) if m else ""


# --------------------------------------------------------------------------- spice_check_netlist


@server.tool(
    name="check_netlist",
    description=(
        "Check a netlist without running ngspice: parse it against the allow-list and report what it holds (elements by type, nodes, models, subcircuits) and "
        "what is wrong with it (no ground node 0, duplicate element names, a missing .model or .subckt, undefined controlling sources, floating nodes). "
        "A netlist that uses something the toolkit never runs (.control, .include, .lib, .write, A/N devices, 'file', 'external', random sources) is refused "
        "with the line number and the directive. Give the text ('netlist') or a file ('netlist_file'). Works without ngspice installed."
    ),
)
def check_netlist(
    netlist: Annotated[str, Field(description="The netlist text (first line = title). Give this OR netlist_file.")] = "",
    netlist_file: Annotated[str, Field(description="Path to a netlist file (.cir .sp .spice .net .ckt .txt). Give this OR netlist.")] = "",
):
    inputs = {"netlist": netlist, "netlist_file": netlist_file}

    def compute(handle: JobHandle) -> Computed:
        nl = load(handle, netlist, netlist_file)
        summary = netlist_summary(nl)
        emitted = "\n".join(["* eng-toolkit: the circuit as ngspice would receive it (normalised, comments and title removed)", *nl.body, ""])
        artifact = handle.write_text("netlist.cir", emitted, fmt="spice")
        summary.update(
            {
                "runnable": not nl.problems,
                "problems": nl.problems,
                "limits": {
                    "max_bytes": nlmod.MAX_BYTES,
                    "max_lines": nlmod.MAX_LINES,
                    "max_expanded_elements": nlmod.MAX_ELEMENTS,
                    "max_tran_points": MAX_SWEEP_POINTS,
                    "literal_magnitude": [nlmod.MIN_LITERAL, nlmod.MAX_LITERAL],
                },
                "allowed_directives": sorted(nlmod.DIRECTIVES),
                "allowed_elements": sorted(c.upper() for c in nlmod.ELEMENT_LETTERS),
            }
        )
        values = {
            "elements": QuantityOut(quantity=float(len(nl.top.elements)), unit="dimensionless"),
            "nodes": QuantityOut(quantity=float(len(nl.nodes)), unit="dimensionless"),
        }
        return Computed(values=values, primary="elements", warnings=list(nl.warnings), data=summary, artifacts=[artifact])

    return run_tool(tool_name="spice_check_netlist", engine=ENGINE, version=__version__, inputs=inputs, compute=compute)


__all__ = ["ANALYSES", "TOOL_NAMES", "analysis_script", "check_netlist", "choose_outputs", "load", "read_netlist", "simulate"]
