"""Polynomials for the ``control`` tools: input parsing, arithmetic, roots, and the exact Routh and Jury stability tests.

Polynomials are numpy float arrays in **descending** powers (``[1, 3, 2]`` is ``s^2 + 3 s + 2``) except inside the text parser,
which builds ascending arrays.  Nothing here evaluates caller text with Python: the polynomial text is tokenised and parsed by a
small recursive-descent parser whose grammar is

    expr   := term (('+' | '-') term)*
    term   := ('+' | '-')* power ( '*' power | power )*      (juxtaposition only before a variable or '(' : ``3 s``, ``(s+1)(s+2)``)
    power  := atom ( ('^' | '**') INTEGER )?                 (INTEGER = 0 .. 40, unsigned, no decimals)
    atom   := NUMBER | VARIABLE | '(' expr ')'
    NUMBER := digits with optional decimal point and optional exponent (``2``, ``0.5``, ``1e-3``)
    VARIABLE := ``s`` (continuous model) or ``z`` (discrete model), the one that matches the model, nothing else

Names, function calls, attributes, strings, commas and every other character are refused with ``input.invalid``.

Exactness: :func:`routh_counts` and :func:`jury_table` run on :class:`fractions.Fraction` built from the float coefficients, so
for the numbers as given they are exact (no tolerance anywhere).  :func:`roots` is numpy's companion-matrix eigenvalue method and
is approximate.
"""

from __future__ import annotations

from eng_toolkit import jsonsafe
import json
import math
import re
from fractions import Fraction
from typing import Any

import numpy as np

from eng_toolkit.units import UnitsError

MAX_ORDER = 40
COEF_MIN = 1e-12
COEF_MAX = 1e12
MAX_TEXT = 5000
MAX_NESTING = 40
JURY_MAX_ORDER = 12  # the Jury table squares the bit length of its entries every row: exact entries are affordable up to about order 12
_INTERMEDIATE_MAX = 1e300
_MULTIPLIERS = (Fraction(1), Fraction(2), Fraction(3), Fraction(1, 2), Fraction(5), Fraction(7), Fraction(1, 3), Fraction(11))

_POINTER_LIST = "write the polynomial as a JSON list of coefficients in descending powers, e.g. '[1, 3, 2]' for s^2 + 3 s + 2, or as text such as 's^2 + 3*s + 2'"


def _bad(message: str, param: str, **extra: Any) -> UnitsError:
    return UnitsError("input.invalid", message, {"param": param, **extra})


def _range(message: str, param: str, **extra: Any) -> UnitsError:
    return UnitsError("input.out_of_range", message, {"param": param, **extra})


def _snippet(text: str, pos: int) -> str:
    start = max(0, pos - 12)
    return repr(text[start : pos + 12])


# ----------------------------------------------------------------------------------------------- coefficient checks


def check_coefficients(values: list[float], *, param: str) -> np.ndarray:
    """Validate a descending coefficient list: 1..41 entries, finite, each zero or within ``COEF_MIN..COEF_MAX`` in magnitude."""
    if not values:
        raise _bad(f"'{param}' is empty; {_POINTER_LIST}", param)
    if len(values) > MAX_ORDER + 1:
        raise _range(f"'{param}' has {len(values)} coefficients, i.e. order {len(values) - 1}; the limit is order {MAX_ORDER}", param, limit_order=MAX_ORDER)
    out = np.empty(len(values), dtype=float)
    for i, c in enumerate(values):
        if isinstance(c, bool) or not isinstance(c, (int, float)):
            raise _bad(f"'{param}'[{i}] = {repr(c)[:30]} is not a number; {_POINTER_LIST}", param)
        try:
            x = float(c)
        except OverflowError as exc:
            raise _range(f"'{param}'[{i}] is far outside the accepted magnitude range 1e-12 .. 1e12", param) from exc
        if not math.isfinite(x):
            raise _bad(f"'{param}'[{i}] is not finite (NaN or infinity)", param)
        if x != 0.0 and not COEF_MIN <= abs(x) <= COEF_MAX:
            raise _range(
                f"'{param}'[{i}] = {x:g}: every non-zero coefficient must have a magnitude between 1e-12 and 1e12 (rescale the time or the gain)",
                param, min=COEF_MIN, max=COEF_MAX,
            )
        out[i] = x
    return out


def _reject_constant(name: str):
    raise ValueError(f"the JSON constant {name} is not a number")


def parse_polynomial(raw: Any, *, param: str, var: str = "s") -> np.ndarray:
    """A polynomial from a JSON list string ``'[1, 3, 2]'`` or from the small polynomial text; descending coefficients.

    Leading zeros of a list are kept (the caller decides whether that is an error); text results have none.
    """
    if isinstance(raw, (list, tuple)):
        return check_coefficients(list(raw), param=param)
    if not isinstance(raw, str):
        raise _bad(f"'{param}' must be a string; {_POINTER_LIST}", param, received=type(raw).__name__)
    text = raw.strip()
    if len(text) > MAX_TEXT:
        raise _range(f"'{param}' is {len(text)} characters long; the limit is {MAX_TEXT}", param, limit_chars=MAX_TEXT)
    if not text:
        raise _bad(f"'{param}' is empty; {_POINTER_LIST}", param)
    if text.startswith("["):
        try:
            items = jsonsafe.loads(text, parse_constant=_reject_constant)
        except RecursionError as exc:
            raise _bad(f"'{param}' is nested too deeply; {_POINTER_LIST}", param) from exc
        except ValueError as exc:
            raise _bad(f"'{param}' is not a valid JSON list of numbers ({str(exc)[:80]}); {_POINTER_LIST}", param) from exc
        if not isinstance(items, list):
            raise _bad(f"'{param}' must be a list of numbers; {_POINTER_LIST}", param)
        return check_coefficients(items, param=param)
    asc = _TextParser(text, var, param).parse()
    return check_coefficients([float(c) for c in asc[::-1]], param=param)


# ----------------------------------------------------------------------------------------------- the text parser

_NUMBER = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


class _TextParser:
    def __init__(self, text: str, var: str, param: str):
        self.text, self.var, self.param = text, var, param
        self.tokens = self._tokenise()
        self.i = 0
        self.depth = 0

    def _tokenise(self) -> list[tuple[str, Any, int]]:
        text, pos, tokens = self.text, 0, []
        while pos < len(text):
            ch = text[pos]
            if ch in " \t":
                pos += 1
                continue
            m = _NUMBER.match(text, pos)
            if m:
                tokens.append(("num", m.group(), pos))
                pos = m.end()
                continue
            m = _NAME.match(text, pos)
            if m:
                tokens.append(("name", m.group(), pos))
                pos = m.end()
                continue
            if text.startswith("**", pos):
                tokens.append(("op", "^", pos))
                pos += 2
                continue
            if ch in "+-*^()":
                tokens.append(("op", ch, pos))
                pos += 1
                continue
            if ch == ",":
                raise _bad(f"'{self.param}': a comma at {_snippet(text, pos)}; for a coefficient list write it in brackets, e.g. '[1, 3, 2]'", self.param)
            raise _bad(f"'{self.param}': the character {ch!r} at {_snippet(text, pos)} is not allowed; {_POINTER_LIST}", self.param)
        return tokens

    def _peek(self) -> tuple[str, Any, int] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _take(self) -> tuple[str, Any, int]:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def parse(self) -> np.ndarray:
        if not self.tokens:
            raise _bad(f"'{self.param}' is empty; {_POINTER_LIST}", self.param)
        value = self._expr()
        tok = self._peek()
        if tok is not None:
            raise _bad(f"'{self.param}': unexpected {tok[1]!r} at {_snippet(self.text, tok[2])}; {_POINTER_LIST}", self.param)
        return value

    def _expr(self) -> np.ndarray:
        left = self._term()
        while (tok := self._peek()) is not None and tok[0] == "op" and tok[1] in "+-":
            self._take()
            right = self._term()
            left = self._guard(_padd(left, right if tok[1] == "+" else -right))
        return left

    def _term(self) -> np.ndarray:
        sign = 1.0
        while (tok := self._peek()) is not None and tok[0] == "op" and tok[1] in "+-":
            self._take()
            sign = -sign if tok[1] == "-" else sign
        value = self._power()
        while (tok := self._peek()) is not None:
            if tok[0] == "op" and tok[1] == "*":
                self._take()
                value = self._guard(_pmul(value, self._power()))
            elif tok[0] == "name" or (tok[0] == "op" and tok[1] == "("):
                value = self._guard(_pmul(value, self._power()))
            elif tok[0] == "num":
                raise _bad(
                    f"'{self.param}': two numbers in a row at {_snippet(self.text, tok[2])}; use '*' between factors, or write a coefficient list in brackets, e.g. '[1, 3, 2]'",
                    self.param,
                )
            else:
                break
        return sign * value

    def _power(self) -> np.ndarray:
        base = self._atom()
        tok = self._peek()
        if tok is not None and tok[0] == "op" and tok[1] == "^":
            self._take()
            exp = self._peek()
            if exp is None or exp[0] != "num" or not exp[1].isdigit():
                at = _snippet(self.text, exp[2]) if exp else "the end"
                raise _bad(f"'{self.param}': the exponent after '^' must be a whole number 0..{MAX_ORDER} (found {at})", self.param)
            self._take()
            n = int(exp[1])
            if n > MAX_ORDER:
                raise _range(f"'{self.param}': exponent {n} exceeds the order limit {MAX_ORDER}", self.param, limit_order=MAX_ORDER)
            result = np.array([1.0])
            for _ in range(n):
                result = self._guard(_pmul(result, base))
            return result
        return base

    def _atom(self) -> np.ndarray:
        tok = self._peek()
        if tok is None:
            raise _bad(f"'{self.param}': the expression ends unexpectedly; {_POINTER_LIST}", self.param)
        kind, val, pos = tok
        self._take()
        if kind == "num":
            x = float(val)
            if not math.isfinite(x):
                raise _range(f"'{self.param}': the number {val[:20]!r} is far too large", self.param)
            return np.array([x])
        if kind == "name":
            if val == self.var:
                return np.array([0.0, 1.0])
            if val in ("s", "z"):
                other = "continuous (variable s)" if val == "s" else "discrete (variable z)"
                mine = "s" if self.var == "s" else "z"
                raise _bad(f"'{self.param}': the variable {val!r} belongs to a {other} model but this model uses {mine!r}; check 'domain'", self.param)
            raise _bad(
                f"'{self.param}': the name {val[:30]!r} is not allowed (only the variable {self.var!r}, numbers, + - * ^ and parentheses; no functions or other names). {_POINTER_LIST}",
                self.param,
            )
        if val == "(":
            self.depth += 1
            if self.depth > MAX_NESTING:
                raise _bad(f"'{self.param}': parentheses are nested more than {MAX_NESTING} deep", self.param)
            inner = self._expr()
            self.depth -= 1
            close = self._peek()
            if close is None or close[1] != ")":
                raise _bad(f"'{self.param}': a closing ')' is missing; {_POINTER_LIST}", self.param)
            self._take()
            return inner
        raise _bad(f"'{self.param}': unexpected {val!r} at {_snippet(self.text, pos)}; {_POINTER_LIST}", self.param)

    def _guard(self, p: np.ndarray) -> np.ndarray:
        p = _ptrim(p)
        if len(p) - 1 > MAX_ORDER:
            raise _range(f"'{self.param}': the polynomial order exceeds {MAX_ORDER}", self.param, limit_order=MAX_ORDER)
        if not np.all(np.isfinite(p)) or np.max(np.abs(p)) > _INTERMEDIATE_MAX:
            raise _range(f"'{self.param}': a coefficient is far outside the accepted magnitude range", self.param)
        return p


def _ptrim(p: np.ndarray) -> np.ndarray:
    """Ascending polynomial without trailing (highest-power) zeros; at least one entry."""
    nz = np.nonzero(p)[0]
    return p[: nz[-1] + 1] if nz.size else np.array([0.0])


def _padd(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    n = max(len(a), len(b))
    out = np.zeros(n)
    out[: len(a)] += a
    out[: len(b)] += b
    return out


def _pmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.convolve(a, b)


# ----------------------------------------------------------------------------------------------- descending arithmetic


def trim(p: np.ndarray) -> np.ndarray:
    """Descending polynomial without leading zeros (``[0]`` stays ``[0]``)."""
    nz = np.nonzero(p)[0]
    return p[nz[0] :] if nz.size else np.array([0.0])


def degree(p: np.ndarray) -> int:
    return len(trim(p)) - 1


def add(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    n = max(len(a), len(b))
    out = np.zeros(n)
    out[n - len(a) :] += a
    out[n - len(b) :] += b
    return trim(out)


def mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return trim(np.convolve(a, b))


def polyval(p: np.ndarray, x: np.ndarray | complex | float) -> np.ndarray:
    """Horner evaluation, vectorised over ``x`` (real or complex)."""
    x = np.asarray(x)
    result = np.zeros(x.shape, dtype=np.result_type(x.dtype, np.float64)) + p[0]
    for c in p[1:]:
        result = result * x + c
    return result


def roots(p: np.ndarray) -> np.ndarray:
    """Roots (complex array, sorted by real part descending then imaginary part); numpy's companion-matrix method."""
    p = trim(p)
    if len(p) <= 1:
        return np.array([], dtype=complex)
    r = np.roots(p).astype(complex)
    return r[np.lexsort((-r.imag, -r.real))]


def trailing_zeros(p: np.ndarray) -> int:
    """Number of exact zero coefficients at the low-power end (the multiplicity of the root at the origin)."""
    p = trim(p)
    count = 0
    for c in p[::-1]:
        if c != 0.0:
            break
        count += 1
    return count if len(p) > 1 or p[0] != 0.0 else 0


def multiplicity_at(p: np.ndarray, root: float, rel_tol: float = 1e-9) -> int:
    """How many times ``(x - root)`` divides ``p`` (synthetic division, remainder compared with ``rel_tol * sum |c|``)."""
    q = trim(p).astype(float)
    count = 0
    while len(q) > 1:
        scale = float(np.sum(np.abs(q))) * max(1.0, abs(root)) ** (len(q) - 1)
        acc = q[0]
        out = [acc]
        for c in q[1:]:
            acc = acc * root + c
            out.append(acc)
        if abs(out[-1]) > rel_tol * scale:
            break
        q = np.array(out[:-1])
        count += 1
    return count


def lowest_order(p: np.ndarray) -> tuple[int, float]:
    """(power, coefficient) of the lowest-power non-zero term of a descending polynomial; ``(0, 0.0)`` for the zero polynomial."""
    p = trim(p)
    tz = trailing_zeros(p)
    if p[0] == 0.0:
        return 0, 0.0
    return tz, float(p[len(p) - 1 - tz])


def to_text(p: np.ndarray, var: str = "s") -> str:
    p = trim(p)
    n = len(p) - 1
    parts: list[str] = []
    for i, c in enumerate(p):
        if c == 0.0:
            continue
        power = n - i
        mag = f"{abs(c):.10g}"
        term = mag if power == 0 else (var if mag == "1" else f"{mag} {var}") if power == 1 else (f"{var}^{power}" if mag == "1" else f"{mag} {var}^{power}")
        parts.append(("-" if c < 0 else "+") + " " + term)
    if not parts:
        return "0"
    first = parts[0]
    text = (first[2:] if first[0] == "+" else "-" + first[2:]) + "".join(" " + q for q in parts[1:])
    return text


# ----------------------------------------------------------------------------------------------- exact Routh array


def _frac_text(x: Fraction) -> float | str:
    """A float when it is representable and not absurdly small, else a decimal string (a Routh entry can be 1e500)."""
    if x == 0:
        return 0.0
    try:
        f = float(x)
    except OverflowError:
        f = math.inf
    if math.isfinite(f) and 1e-300 < abs(f) < 1e300:
        return f
    from decimal import Decimal, localcontext

    with localcontext() as ctx:
        ctx.prec = 10
        return f"{Decimal(x.numerator) / Decimal(x.denominator):.9E}"


def _sign_changes(values: list[Fraction]) -> int:
    count, last = 0, 0
    for v in values:
        s = 1 if v > 0 else -1
        if last and s != last:
            count += 1
        last = s
    return count


def _routh_rows(p: list[Fraction]) -> tuple[list[list[Fraction]], list[tuple[int, int]]] | None:
    """The Routh array of ``p`` (descending, ``p[0] != 0``, ``p[-1] != 0``) with the zero-row rule applied.

    Returns ``(rows, auxiliaries)`` where each auxiliary is ``(row index of the replaced zero row, degree of its auxiliary polynomial)``;
    ``None`` when a first-column element is zero without the whole row being zero (the caller then multiplies by ``s + a``).
    """
    n = len(p) - 1
    even, odd = p[0::2], p[1::2]
    width = len(even)
    odd = odd + [Fraction(0)] * (width - len(odd))
    rows = [even, odd]
    aux: list[tuple[int, int]] = []
    for i in range(2, n + 1):
        prev, prev2 = rows[-1], rows[-2]
        if all(v == 0 for v in prev):
            degree_aux = n - (i - 2)  # the row i-2 held the coefficients of the auxiliary polynomial, power s^(n-(i-2))
            if degree_aux % 2:
                return None
            prev = [prev2[j] * (degree_aux - 2 * j) for j in range(width)]
            rows[-1] = prev
            aux.append((i - 1, degree_aux))
        if prev[0] == 0:
            return None
        row = []
        for j in range(width):
            a = prev2[j + 1] if j + 1 < width else Fraction(0)
            b = prev[j + 1] if j + 1 < width else Fraction(0)
            row.append((prev[0] * a - prev2[0] * b) / prev[0])
        rows.append(row)
    last = rows[-1]
    if all(v == 0 for v in last):
        # a zero last row (power s^0): the auxiliary polynomial would be of degree 1, impossible for a polynomial with p(0) != 0
        return None
    if last[0] == 0:
        return None
    return rows, aux


def _routh_core(p: list[Fraction]) -> dict[str, Any]:
    """Routh counts for exact fractions ``p`` (descending, ``p[0] != 0``): rows, first column and the three root counts."""
    n = len(p) - 1
    if n == 0:
        return {"rhp": 0, "imag": 0, "lhp": 0, "rows": [[p[0]]], "column": [p[0]], "multiplier": None, "exact": True}
    z0 = 0
    p = list(p)
    while p[-1] == 0:
        p.pop()
        z0 += 1
    if len(p) == 1:
        return {"rhp": 0, "imag": n, "lhp": 0, "rows": [], "column": [], "multiplier": None, "exact": True}
    multiplier: Fraction | None = None
    result = _routh_rows(p)
    if result is None:
        for a in _MULTIPLIERS:
            result = _routh_rows(_poly_times_linear(p, a))
            if result is not None:
                multiplier = a
                break
    if result is None:
        return {"rhp": None, "imag": None, "lhp": None, "rows": [], "column": [], "multiplier": None, "exact": False}
    rows, aux = result
    column = [r[0] for r in rows]
    rhp = _sign_changes(column)
    imag_q = 0
    if aux:
        row_index, degree_aux = aux[0]
        imag_q = degree_aux - 2 * _sign_changes(column[row_index - 1 :])
    lhp = len(p) - 1 - rhp - imag_q
    return {
        "rhp": rhp, "imag": imag_q + z0, "lhp": lhp, "rows": rows, "column": column, "multiplier": multiplier,
        "aux_degrees": [d for _, d in aux], "exact": lhp >= 0 and imag_q >= 0,
    }


def routh_counts(coeffs: np.ndarray | list[float]) -> dict[str, Any]:
    """Exact Routh-Hurwitz result for a polynomial in ``s`` (descending coefficients).

    Returns ``{"rhp", "imag", "lhp", "array", "first_column", "multiplier", "exact"}``: the numbers of roots in the open right
    half plane, on the imaginary axis (with multiplicity) and in the open left half plane, counted from the coefficients as
    given with rational arithmetic; the array rows and first column are floats (decimal strings if out of float range).
    ``exact`` is False if the method could not decide (not seen in the tests: a first-column zero is cured by multiplying
    with ``s + a``, which adds one left-half-plane root and changes nothing else).
    """
    res = _routh_core([Fraction(float(c)) for c in trim(np.asarray(coeffs, dtype=float))])
    return {
        "rhp": res["rhp"], "imag": res["imag"], "lhp": res["lhp"], "exact": res["exact"],
        "array": [[_frac_text(v) for v in r] for r in res["rows"]],
        "first_column": [_frac_text(v) for v in res["column"]],
        "multiplier": None if res["multiplier"] is None else float(res["multiplier"]),
        "zero_rows": len(res.get("aux_degrees", [])),
        "auxiliary_polynomial_degrees": res.get("aux_degrees", []),
    }


def _poly_times_linear(p: list[Fraction], a: Fraction) -> list[Fraction]:
    """``p(s) * (s + a)`` for a descending Fraction polynomial."""
    out = [Fraction(0)] * (len(p) + 1)
    for i, c in enumerate(p):
        out[i] += c
        out[i + 1] += c * a
    return out


# ----------------------------------------------------------------------------------------------- exact half-plane counts (Cauchy index)
#
# Routh's array has degenerate cases (a zero first element, a zero row followed by a zero) that need symbolic epsilons.  The
# count below has none: with p(j w) = P(w) + j Q(w), d = gcd(P, Q) carries the roots on the imaginary axis, and the rest follows
# from the Cauchy index of Q/P (computed with a Sturm chain) - all in rational arithmetic.


def _ftrim(p: list[Fraction]) -> list[Fraction]:
    i = 0
    while i < len(p) and p[i] == 0:
        i += 1
    return p[i:]


def _fdivmod(a: list[Fraction], b: list[Fraction]) -> tuple[list[Fraction], list[Fraction]]:
    a = list(a)
    nb = len(b)
    if len(a) < nb:
        return [], _ftrim(a)
    q = []
    for i in range(len(a) - nb + 1):
        c = a[i] / b[0]
        q.append(c)
        if c != 0:
            for j in range(nb):
                a[i + j] -= c * b[j]
    return q, _ftrim(a[len(a) - nb + 1 :])


def _fgcd(a: list[Fraction], b: list[Fraction]) -> list[Fraction]:
    a, b = _ftrim(a), _ftrim(b)
    while b:
        a, b = b, _fdivmod(a, b)[1]
    return [c / a[0] for c in a] if a else []


def _fderiv(p: list[Fraction]) -> list[Fraction]:
    n = len(p) - 1
    return _ftrim([c * (n - i) for i, c in enumerate(p[:-1])])


def _sturm_chain(f0: list[Fraction], f1: list[Fraction]) -> list[list[Fraction]]:
    chain = [f0, f1]
    while True:
        r = _fdivmod(chain[-2], chain[-1])[1]
        if not r:
            return chain
        chain.append([-c for c in r])


def _variations(chain: list[list[Fraction]], at_plus: bool) -> int:
    signs = []
    for f in chain:
        s = 1 if f[0] > 0 else -1
        if not at_plus and (len(f) - 1) % 2:
            s = -s
        signs.append(s)
    return sum(1 for x, y in zip(signs, signs[1:]) if x != y)


def _cauchy_index(num: list[Fraction], den: list[Fraction]) -> int:
    """Cauchy index over the real line of num/den (+1 for a jump from -infinity to +infinity); both non-zero polynomials."""
    chain = _sturm_chain(den, num)
    return _variations(chain, False) - _variations(chain, True)


def _distinct_real_roots(f: list[Fraction]) -> int:
    if len(f) <= 1:
        return 0
    chain = _sturm_chain(f, _fderiv(f))
    return _variations(chain, False) - _variations(chain, True)


def _real_roots_with_multiplicity(f: list[Fraction]) -> tuple[int, int]:
    """(number of real roots counted with multiplicity, number of distinct real roots) of ``f``."""
    f = _ftrim(f)
    total, distinct = 0, None
    while len(f) > 1:
        g = _fgcd(f, _fderiv(f))
        square_free = _fdivmod(f, g)[0] if len(g) > 1 else f
        count = _distinct_real_roots(_ftrim(square_free))
        total += count
        distinct = count if distinct is None else distinct
        f = g if len(g) > 1 else [Fraction(1)]
    return total, distinct or 0


def half_plane_counts(coeffs: Any) -> dict[str, Any]:
    """Exact numbers of roots of ``p(s)`` in the open right half plane, on the imaginary axis and in the open left half plane.

    ``coeffs``: descending floats (or Fractions).  Rational arithmetic on the coefficients as given; no tolerance and no special
    cases (see the comment above).  The result is ``{"rhp", "imag", "lhp", "exact"}``; ``exact`` is False only if an internal
    consistency check fails (never seen).
    """
    if len(coeffs) and isinstance(coeffs[0], Fraction):
        p = _ftrim(list(coeffs))
    else:
        p = [Fraction(float(c)) for c in trim(np.asarray(coeffs, dtype=float))]
    n = len(p) - 1
    if n == 0:
        return {"rhp": 0, "imag": 0, "imag_distinct": 0, "lhp": 0, "exact": True}
    # p(j w) = sum c_k (j w)^k, k = n - index: even k -> real part c_k (-1)^(k/2) w^k, odd k -> imaginary part c_k (-1)^((k-1)/2) w^k
    big_p = [Fraction(0)] * (n + 1)
    big_q = [Fraction(0)] * (n + 1)
    for idx, c in enumerate(p):
        k = n - idx
        if k % 2 == 0:
            big_p[idx] = c * (-1) ** (k // 2)
        else:
            big_q[idx] = c * (-1) ** ((k - 1) // 2)
    big_p, big_q = _ftrim(big_p), _ftrim(big_q)
    d = _fgcd(big_p, big_q) if big_p and big_q else (big_p or big_q)
    axis, axis_distinct = _real_roots_with_multiplicity(d)
    p1 = _ftrim(_fdivmod(big_p, d)[0]) if big_p else []
    q1 = _ftrim(_fdivmod(big_q, d)[0]) if big_q else []
    if not p1 or not q1:
        left_minus_right = 0  # p/d(j w) is a real or an imaginary constant times a real function: no net argument change
    else:
        ends = 0
        if len(q1) > len(p1):
            ends = 1 if (q1[0] > 0) == (p1[0] > 0) else -1
        left_minus_right = ends - _cauchy_index(q1, p1)
    rest = n - axis
    twice_right = rest - left_minus_right
    ok = rest >= 0 and twice_right % 2 == 0 and 0 <= twice_right // 2 <= rest
    right = twice_right // 2 if ok else 0
    return {"rhp": right, "imag": axis, "imag_distinct": axis_distinct, "lhp": rest - right if ok else 0, "exact": ok}


# ----------------------------------------------------------------------------------------------- discrete: Jury and the bilinear map


def _binomial_product(k: int, n: int) -> list[int]:
    """Descending integer coefficients of ``(1 + w)^k * (1 - w)^(n-k)``."""
    result = [1]
    for _ in range(k):
        result = _int_mul(result, [1, 1])
    for _ in range(n - k):
        result = _int_mul(result, [-1, 1])
    return result


def _int_mul(a: list[int], b: list[int]) -> list[int]:
    out = [0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            out[i + j] += x * y
    return out


def unit_circle_counts(coeffs: np.ndarray | list[float]) -> dict[str, Any]:
    """Exact numbers of roots of ``p(z)`` inside, on and outside the unit circle (bilinear map ``z = (1 + w)/(1 - w)`` + Routh)."""
    p = [Fraction(float(c)) for c in trim(np.asarray(coeffs, dtype=float))]
    n = len(p) - 1
    if n == 0:
        return {"outside": 0, "on_circle": 0, "on_circle_distinct": 0, "inside": 0, "exact": True}
    # q(w) = sum_k a_k (1+w)^k (1-w)^(n-k), with a_k the coefficient of z^k (ascending index)
    q = [Fraction(0)] * (n + 1)  # descending in w
    for idx, c in enumerate(p):
        k = n - idx
        poly = _binomial_product(k, n)
        for j, v in enumerate(poly):
            q[j] += c * v
    lead = 0
    while lead < len(q) and q[lead] == 0:
        lead += 1  # roots at z = -1 (w = infinity)
    if lead == len(q):
        return {"outside": 0, "on_circle": n, "on_circle_distinct": 1, "inside": 0, "exact": True}
    q = q[lead:]
    if len(q) == 1:
        return {"outside": 0, "on_circle": n, "on_circle_distinct": 1 if lead else 0, "inside": 0, "exact": True}
    res = half_plane_counts(q)
    if not res["exact"]:
        return {"outside": None, "on_circle": None, "on_circle_distinct": None, "inside": None, "exact": False}
    return {
        "outside": res["rhp"], "on_circle": res["imag"] + lead, "on_circle_distinct": res["imag_distinct"] + (1 if lead else 0),
        "inside": res["lhp"], "exact": True,
    }


def jury_table(coeffs: np.ndarray | list[float]) -> dict[str, Any]:
    """The Jury stability table for ``p(z)`` (descending coefficients), in exact rational arithmetic.

    Necessary and sufficient for all roots strictly inside the unit circle: ``p(1) > 0``, ``(-1)^n p(-1) > 0``, ``|a0| < an`` and
    ``|b0| > |b_{n-1}|``, ``|c0| > |c_{n-2}|``, ... down to a row of three.  Returns every condition with its two sides and the
    rows of the table (floats).  ``stable`` is True only if every inequality holds strictly.
    """
    p = [Fraction(float(c)) for c in trim(np.asarray(coeffs, dtype=float))]
    n = len(p) - 1
    if n > JURY_MAX_ORDER:
        return {
            "stable": None, "omitted": True, "order": n, "conditions": [], "rows": [],
            "reason": f"the exact Jury table is only built up to order {JURY_MAX_ORDER} (its entries double in bit length every row); the exact unit-circle root count is used instead",
        }
    if p[0] < 0:
        p = [-c for c in p]
    asc = p[::-1]
    conditions: list[dict[str, Any]] = []
    if n == 0:
        return {"stable": True, "conditions": [], "rows": [], "order": 0}
    p1 = sum(asc)
    pm1 = sum(c * (-1) ** k for k, c in enumerate(asc))
    conditions.append({"name": "p(1) > 0", "lhs": _frac_text(p1), "holds": p1 > 0})
    conditions.append({"name": "(-1)^n p(-1) > 0", "lhs": _frac_text(pm1 * (-1) ** n), "holds": pm1 * (-1) ** n > 0})
    rows: list[list[Any]] = []
    row = asc
    first = True
    while len(row) >= 3:
        rows.append([_frac_text(v) for v in row])
        lo, hi = abs(row[0]), abs(row[-1])
        if first:
            conditions.append({"name": "|a0| < an", "lhs": _frac_text(lo), "rhs": _frac_text(hi), "holds": lo < hi})
        else:
            conditions.append({"name": f"|row[0]| > |row[-1]| (row of {len(row)})", "lhs": _frac_text(lo), "rhs": _frac_text(hi), "holds": lo > hi})
        m = len(row)
        row = [row[0] * row[k] - row[-1] * row[m - 1 - k] for k in range(m - 1)]
        first = False
    return {"stable": all(c["holds"] for c in conditions), "conditions": conditions, "rows": rows, "order": n}


__all__ = [
    "COEF_MAX", "COEF_MIN", "MAX_ORDER", "add", "check_coefficients", "degree", "half_plane_counts", "jury_table", "lowest_order", "mul", "multiplicity_at",
    "parse_polynomial", "polyval", "roots", "routh_counts", "to_text", "trailing_zeros", "trim", "unit_circle_counts",
]
