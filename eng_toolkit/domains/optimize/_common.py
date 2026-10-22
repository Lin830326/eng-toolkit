"""Strict boundaries, call budgets, and finite result construction."""

from __future__ import annotations

import csv
import io
import json
import math
import time
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import Computed, run_tool
from eng_toolkit.units import parse_quantity
from ._expr import CONSTANTS, FUNCTIONS, TrialError, identifier, literal, tokens
from ._server import ENGINE

GRAMMAR = "Grammar: numbers, names, + - * / ^ (**), parentheses, whitelisted math calls; no comparisons inside expressions. Variables are plain numbers in the units you choose; the tool does not track units inside expressions."
TimeLimit = Annotated[str, Field(description="Wall-clock budget with time units, e.g. '10 s'; supported 0.001..60 s, shared by all solver stages.")]
Seed = Annotated[int, Field(description="Deterministic random seed, e.g. 0; integer 0..4294967295, echoed in the result.")]
Tolerance = Annotated[float, Field(description="Dimensionless convergence tolerance, e.g. 1e-7; supported 1e-12..0.01.")]


def invalid(param, message, family="input.invalid", **details):
    raise ToolFailure(family, message, {"param": "/" + param.lstrip("/"), **clean(details)})


def number(value, param, lo=-1e100, hi=1e100):
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        invalid(param, "expected a dimensionless number")
    try:
        result = float(value)
    except OverflowError:
        invalid(param, "number exceeds the supported range", "input.out_of_range")
    if not math.isfinite(result) or not lo <= result <= hi or (result != 0 and abs(result) < 1e-100):
        invalid(param, f"number outside supported range {lo:g}..{hi:g}", "input.out_of_range")
    return result


def count(value, param, lo, hi):
    value = number(value, param, lo, hi)
    if value != int(value):
        invalid(param, "expected an integer")
    return int(value)


def choice(value, param, choices):
    if value not in choices:
        invalid(param, "expected one of " + ", ".join(choices))
    return value


def json_value(text, param, default=None):
    if text == "" and default is not None:
        return default
    if not isinstance(text, str) or not text.strip():
        invalid(param, "required JSON input is missing")
    if len(text) > 4000000:
        invalid(param, "JSON text exceeds 4000000 characters", "input.out_of_range")
    depth = 0
    for c in text:
        if c in "[{":
            depth += 1
            if depth > 8:
                invalid(param, "JSON nesting exceeds 8", "input.out_of_range")
        elif c in "]}":
            depth -= 1
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                invalid(param, "duplicate JSON key " + k)
            result[k] = v
        return result
    try:
        return json.loads(text, parse_float=lambda x: literal(x, param),
                          parse_int=lambda x: literal(x, param),
                          parse_constant=lambda x: invalid(param, "nonfinite JSON literal " + x), object_pairs_hook=pairs)
    except (ValueError, RecursionError) as exc:
        invalid(param, "invalid JSON: " + str(exc)[:180])


def array(text, param, *, ndim=1, cap=200000, default=None):
    value = json_value(text, param, default)
    if not isinstance(value, list):
        invalid(param, "expected a JSON number list")
    def numeric(v, path):
        if isinstance(v, list):
            return [numeric(x, path + "/" + str(i)) for i, x in enumerate(v)]
        return number(v, path)
    value = numeric(value, param)
    try:
        result = np.asarray(value, dtype=float)
    except ValueError:
        invalid(param, "array rows must have the same length")
    if result.ndim != ndim:
        invalid(param, f"expected an array with {ndim} dimensions")
    if result.size > cap:
        invalid(param, f"array cap is {cap} numbers", "input.out_of_range")
    return result


def records(text, param, cap=16):
    if not isinstance(text, str):
        invalid(param, "expected semicolon/newline separated records")
    if len(text) > 42000:
        invalid(param, "record text exceeds 42000 characters", "input.out_of_range")
    if not text.strip():
        return []
    parts = text.replace("\r\n", "\n").replace("\n", ";").split(";")
    if len(parts) > cap:
        invalid(param, f"record cap is {cap}", "input.out_of_range")
    if any(not p.strip() for p in parts):
        invalid(param, "empty records are not allowed")
    return [p.strip() for p in parts]


def named(text, param="initial", *, cap=16, required=True):
    result = {}
    for i, item in enumerate(records(text, param, cap)):
        path = f"{param}/{i}"
        if item.count("=") != 1:
            invalid(path, "expected name=number")
        name, raw = (p.strip() for p in item.split("="))
        if not identifier(name) or name in FUNCTIONS or name in CONSTANTS or name in result:
            invalid(path, "invalid, repeated or reserved variable name")
        parts = tokens(raw, path)
        sign = 1
        if parts[0][0] in ("+", "-"):
            sign = -1 if parts[0][0] == "-" else 1
            parts = parts[1:]
        if len(parts) != 2 or parts[0][0] != "number":
            invalid(path, "values must be numeric literals, without formulas")
        result[name] = sign * parts[0][1]
    if required and not result:
        invalid(param, "at least one name=number record is required")
    return result


def decimal(text, param):
    """A strict signed decimal literal using the expression-language number grammar.

    Unlike float(), it refuses digit separators ('1_000'), non-ASCII digits ('６'),
    'nan'/'inf' spellings and surrounding text."""
    if not isinstance(text, str) or not text.strip():
        invalid(param, "expected a numeric literal")
    parts = tokens(text.strip(), param)
    sign = 1
    if parts[0][0] in ("+", "-"):
        sign = -1 if parts[0][0] == "-" else 1
        parts = parts[1:]
    if len(parts) != 2 or parts[0][0] != "number":
        invalid(param, "expected a plain decimal numeric literal")
    return sign * parts[0][1]


def bounds_array(text, names, initial=None, param="bounds"):
    n = len(names)
    if text == "":
        return np.full(n, -1e100), np.full(n, 1e100)
    raw = json_value(text, param)
    if not isinstance(raw, list) or len(raw) != n:
        invalid(param, "bounds require one [lower,upper] pair per variable; null means unbounded")
    lo, hi = [], []
    for i, pair in enumerate(raw):
        if not isinstance(pair, list) or len(pair) != 2:
            invalid(f"{param}/{i}", "expected [lower,upper]")
        a = -1e100 if pair[0] is None else number(pair[0], f"{param}/{i}/0")
        b = 1e100 if pair[1] is None else number(pair[1], f"{param}/{i}/1")
        if a > b:
            invalid(f"{param}/{i}", "lower bound exceeds upper bound")
        lo.append(a)
        hi.append(b)
    lo, hi = np.array(lo), np.array(hi)
    if initial is not None and (np.any(initial < lo) or np.any(initial > hi)):
        invalid(param, "initial point lies outside bounds")
    return lo, hi


class Budget:
    def __init__(self, time_limit="10 s", max_evaluations=200000, max_work=100000000):
        if not isinstance(time_limit, str):
            invalid("time_limit", "time requires a string with units", "units.bare_number")
        pieces = time_limit.strip().split()
        if len(pieces) != 2 or not pieces[1].isascii() or not pieces[1].isalpha():
            invalid("time_limit", "expected a number and a named time unit, e.g. '10 s'")
        magnitude = named("t=" + pieces[0], "time_limit")["t"]
        qty = parse_quantity({"value": magnitude, "unit": pieces[1]}, name="time_limit", dimension="time")
        seconds = float(qty.to("s").magnitude)
        number(seconds, "time_limit", .001, 60)
        self.deadline = time.monotonic() + seconds
        self.evaluations, self.work, self.rows = 0, 0, 0
        self.max_evaluations, self.max_work = max_evaluations, max_work
        self.per_call, self.max_rows = False, None

    def count_calls(self, max_rows=200000000, max_work=2000000000):
        """Charge one evaluation per vectorised model call; rows and node work are capped
        separately (used by fit, where every call evaluates all data rows at once)."""
        self.per_call, self.max_rows, self.max_work = True, max_rows, max_work
        return self

    def check(self):
        if time.monotonic() > self.deadline:
            invalid("time_limit", "optimize.time_budget_exceeded", "input.out_of_range",
                    reason="optimize.time_budget_exceeded", evaluations=self.evaluations)

    def consume(self, evaluations=1, work=1):
        self.check()
        if self.per_call:
            self.evaluations += 1
            self.rows += int(evaluations)
        else:
            self.evaluations += int(evaluations)
        self.work += int(work)
        if (self.evaluations > self.max_evaluations or self.work > self.max_work
                or (self.max_rows is not None and self.rows > self.max_rows)):
            invalid("inputs", "optimize.evaluation_budget_exceeded", "input.out_of_range",
                    reason="optimize.evaluation_budget_exceeded", evaluations=self.evaluations,
                    **({"row_evaluations": self.rows} if self.per_call else {}))


def clean(obj):
    if isinstance(obj, np.ndarray):
        obj = obj.tolist()
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and (not math.isfinite(obj) or abs(obj) > 1e100):
        raise ToolFailure("input.out_of_range", "result exceeds supported finite range", {"param": "/inputs"})
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def csv_text(header, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(clean(rows))
    return stream.getvalue()


def result(data, primary, value, *, formulas, assumptions, validity, artifacts=(), warnings=()):
    data = clean({**data, "formulas": formulas, "assumptions": assumptions, "validity": validity})
    return Computed(values={primary: QuantityOut(quantity=clean(float(value)), unit="dimensionless")},
                    primary=primary, data=data, artifacts=list(artifacts), warnings=list(warnings))


def run(name, inputs, compute):
    def guarded(handle):
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                return compute(handle)
        except ToolFailure as exc:
            details = dict(exc.details)
            details["param"] = "/" + str(details.get("param", "inputs")).lstrip("/")
            raise ToolFailure(exc.family, exc.message, clean(details)) from None
        except TrialError as exc:
            invalid(exc.param, str(exc), "input.out_of_range")
        except (ArithmeticError, np.linalg.LinAlgError, ValueError) as exc:
            invalid("inputs", "unsupported numerical range or conditioning: " + str(exc)[:200], "input.out_of_range")
    return run_tool(tool_name="optimize_" + name, engine=ENGINE, version=__version__, inputs=inputs,
                    compute=guarded, engines=("numpy", "pint"),
                    standing_warnings=("Unreviewed numerical result. Verify scaling, convergence, assumptions and engineering applicability.",))
