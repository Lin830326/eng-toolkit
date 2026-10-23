"""Small, bounded arithmetic language. No Python syntax is interpreted."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from eng_toolkit.errors import ToolFailure


FUNCTIONS = {
    "sin": (np.sin, 1), "cos": (np.cos, 1), "tan": (np.tan, 1),
    "asin": (np.arcsin, 1), "acos": (np.arccos, 1), "atan": (np.arctan, 1),
    "atan2": (np.arctan2, 2), "sinh": (np.sinh, 1), "cosh": (np.cosh, 1),
    "tanh": (np.tanh, 1), "exp": (np.exp, 1), "log": (np.log, 1),
    "log10": (np.log10, 1), "sqrt": (np.sqrt, 1), "abs": (np.abs, 1),
    "min": (np.minimum, 2), "max": (np.maximum, 2), "pow": (np.power, 2),
    "floor": (np.floor, 1), "ceil": (np.ceil, 1), "sign": (np.sign, 1),
}
CONSTANTS = {"pi": math.pi, "e": math.e}
LIMIT = 1e100


def fail(param, message, position=0, family="input.invalid"):
    raise ToolFailure(family, f"{message} at character {position}", {"param": "/" + param.lstrip("/"), "position": position})


def identifier(name):
    return (isinstance(name, str) and 1 <= len(name) <= 32 and
            name[0] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_" and
            all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_0123456789" for c in name))


def literal(text, param="expression", position=0):
    try:
        value = float(text)
    except (ValueError, OverflowError):
        fail(param, "expected a finite numeric literal", position)
    if not math.isfinite(value) or abs(value) > LIMIT or (value != 0 and abs(value) < 1e-100):
        fail(param, "numeric magnitude outside 1e-100..1e100 (or zero)", position, "input.out_of_range")
    if value == 0 and any(c in "123456789" for c in text.lower().split("e")[0]):
        fail(param, "nonzero literal underflows", position, "input.out_of_range")
    return value


@dataclass(frozen=True)
class Node:
    op: str
    value: float | str | None
    children: tuple[Node, ...]
    position: int
    depth: int


class TrialError(Exception):
    """A domain or range failure at a numerical trial, never a success value."""

    def __init__(self, message, param="expression"):
        super().__init__(message)
        self.param = param


def tokens(text, param):
    if not isinstance(text, str) or not text.strip():
        fail(param, "expression is empty")
    if len(text) > 2000:
        fail(param, "expression exceeds 2000 characters", 2000, "input.out_of_range")
    out, i = [], 0
    while i < len(text):
        c, start = text[i], i
        if c in " \t\r\n":
            i += 1
            continue
        if c in "0123456789" or c == ".":
            digits = 0
            while i < len(text) and text[i] in "0123456789":
                i += 1
                digits += 1
            if i < len(text) and text[i] == ".":
                i += 1
                while i < len(text) and text[i] in "0123456789":
                    i += 1
                    digits += 1
            if not digits:
                fail(param, "unexpected decimal point", start)
            if i < len(text) and text[i] in "eE":
                i += 1
                if i < len(text) and text[i] in "+-":
                    i += 1
                expstart = i
                while i < len(text) and text[i] in "0123456789":
                    i += 1
                if i == expstart:
                    fail(param, "missing exponent digits", i)
            out.append(("number", literal(text[start:i], param, start), start))
        elif c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_":
            i += 1
            while i < len(text) and text[i] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_0123456789":
                i += 1
            name = text[start:i]
            if len(name) > 32:
                fail(param, "identifier exceeds 32 characters", start)
            out.append(("name", name, start))
        elif c in "+-*/^(),":
            i += 1
            if c == "*" and i < len(text) and text[i] == "*":
                c = "^"
                i += 1
            out.append((c, c, start))
        else:
            fail(param, "unsupported character", i)
        if len(out) > 1500:
            fail(param, "token limit exceeded", i, "input.out_of_range")
    out.append(("end", "", len(text)))
    return out


class Expression:
    """Parse once; evaluate scalar or same-shaped array bindings with one budget."""

    def __init__(self, text, names=(), param="expression"):
        self.param = param
        self.names = tuple(names)
        if len(self.names) > 16:
            fail(param, "at most 16 declared names", 0, "input.out_of_range")
        if len(set(self.names)) != len(self.names) or any(not identifier(n) or n in FUNCTIONS or n in CONSTANTS for n in self.names):
            fail(param, "invalid, repeated or reserved variable name")
        self._tokens = tokens(text, param)
        self._index, self._nodes = 0, 0
        self.tree = self._parse(0, 0)
        if self._peek()[0] != "end":
            fail(param, "unexpected trailing token", self._peek()[2])
        self.node_count = self._nodes
        self.smooth = not any(t[1] in ("abs", "min", "max", "floor", "ceil", "sign") for t in self._tokens)
        del self._tokens

    def _peek(self):
        return self._tokens[self._index]

    def _take(self):
        token = self._peek()
        self._index += 1
        return token

    def _node(self, op, value, children, pos):
        self._nodes += 1
        depth = 1 + max((c.depth for c in children), default=0)
        if self._nodes > 500 or depth > 64:
            fail(self.param, "tree exceeds 500 nodes or depth 64", pos, "input.out_of_range")
        return Node(op, value, tuple(children), pos, depth)

    def _parse(self, minimum, depth):
        if depth >= 64:
            fail(self.param, "parser nesting exceeds 64", self._peek()[2], "input.out_of_range")
        kind, value, pos = self._take()
        if kind == "number":
            left = self._node("number", value, (), pos)
        elif kind in ("+", "-"):
            left = self._node("unary" + kind, None, (self._parse(25, depth + 1),), pos)
        elif kind == "(":
            left = self._parse(0, depth + 1)
            if self._peek()[0] != ")":
                fail(self.param, "expected closing parenthesis", self._peek()[2])
            self._take()
        elif kind == "name":
            if value in FUNCTIONS:
                if self._peek()[0] != "(":
                    fail(self.param, "function requires parentheses", pos)
                self._take()
                args = []
                if self._peek()[0] != ")":
                    args.append(self._parse(0, depth + 1))
                    while self._peek()[0] == ",":
                        self._take()
                        if len(args) >= 2:
                            fail(self.param, "too many function arguments", self._peek()[2])
                        args.append(self._parse(0, depth + 1))
                if self._peek()[0] != ")" or len(args) != FUNCTIONS[value][1]:
                    fail(self.param, "wrong function arity or missing parenthesis", pos)
                self._take()
                left = self._node("call", value, args, pos)
            elif value in CONSTANTS:
                left = self._node("number", CONSTANTS[value], (), pos)
            elif value in self.names:
                left = self._node("name", value, (), pos)
            else:
                fail(self.param, "unknown name " + value, pos)
        else:
            fail(self.param, "expected number, declared name or parenthesis", pos)
        while self._peek()[0] in ("+", "-", "*", "/", "^"):
            op, _, pos = self._peek()
            power = {"+": 10, "-": 10, "*": 20, "/": 20, "^": 30}[op]
            if power < minimum:
                break
            self._take()
            right = self._parse(power if op == "^" else power + 1, depth + 1)
            left = self._node(op, None, (left, right), pos)
        return left

    def evaluate(self, bindings, budget=None):
        if any(n not in bindings for n in self.names):
            fail(self.param, "missing declared variable binding")
        sizes = [np.asarray(bindings[n]).size for n in self.names]
        size = max(sizes, default=1)
        if size > 200000 or any(s not in (1, size) for s in sizes):
            fail(self.param, "array bindings must share a shape and have at most 200000 entries", 0, "input.out_of_range")
        if budget is not None:
            budget.consume(size, self.node_count * size)
        def walk(node):
            if budget is not None:
                budget.check()
            if node.op == "number":
                result = np.float64(node.value)
            elif node.op == "name":
                result = np.asarray(bindings[node.value], dtype=float)
                if result.ndim > 1:
                    raise TrialError("only scalar or one-dimensional array bindings are supported")
            else:
                args = [walk(child) for child in node.children]
                if node.op == "call":
                    if node.value == "pow" and np.any(np.abs(args[1]) > 10000):
                        raise TrialError("power exponent exceeds 10000")
                    result = FUNCTIONS[node.value][0](*args)
                elif node.op == "unary+":
                    result = args[0]
                elif node.op == "unary-":
                    result = -args[0]
                elif node.op == "+":
                    result = args[0] + args[1]
                elif node.op == "-":
                    result = args[0] - args[1]
                elif node.op == "*":
                    result = args[0] * args[1]
                elif node.op == "/":
                    result = np.divide(args[0], args[1])
                else:
                    if np.any(np.abs(args[1]) > 10000):
                        raise TrialError("power exponent exceeds 10000")
                    result = np.power(args[0], args[1])
            if np.any(~np.isfinite(result)) or np.any(np.abs(result) > LIMIT):
                raise TrialError(f"nonfinite or excessive intermediate at character {node.position}")
            if np.any((result != 0) & (np.abs(result) < np.finfo(float).tiny)):
                fail(self.param, "expression intermediate underflows to subnormal", node.position, "input.out_of_range")
            return result
        try:
            with np.errstate(over="raise", divide="raise", invalid="raise", under="raise"):
                value = walk(self.tree)
                if budget is not None:
                    budget.check()
                return value
        except ToolFailure:
            raise  # budget failures must stop the call, not become rejected trial points
        except TrialError as exc:
            raise TrialError(str(exc), self.param) from None
        except (FloatingPointError, OverflowError, ValueError, ZeroDivisionError) as exc:
            if isinstance(exc, FloatingPointError) and "underflow" in str(exc):
                fail(self.param, "expression intermediate underflows", family="input.out_of_range")
            raise TrialError(f"expression domain/range failure: {exc}", self.param) from None
