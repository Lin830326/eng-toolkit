"""A tiny SVG writer: escaping, fixed number formatting, themes, palette and text-width estimates.

Only these elements are ever written: ``svg g path polyline line rect circle text title desc style clipPath defs``.  Attribute
names are fixed by this code; every attribute value and text node is XML-escaped (``& < > " '``); caller text has its control
characters replaced before it gets here (:func:`_common.clean_text`).  No script, no foreignObject, no href, no event attributes,
no external references (``url(#id)`` refers to a clip path in the same file), no web fonts.  Numbers are written with at most two
decimals, trailing zeros trimmed, so identical input gives identical bytes.
"""

from __future__ import annotations

import math
import unicodedata

from eng_toolkit.domains.plot._common import _BAD, _sub

FONT_STACK = "system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif"
CHAR_EM = 0.6  # estimated advance of one character, in font sizes (generous for digits, so labels never collide)
WIDE_EM = 1.0  # east-asian wide and fullwidth characters
ELLIPSIS = "…"
PX_CLAMP = 1e7  # pixel coordinates are clamped to +-1e7 (a far-away point keeps its direction to within 0.25 px on screen)

# Okabe-Ito based categorical palette (colour-blind safe), a lighter variant for dark backgrounds, and greys for print;
# every series also gets its own dash pattern and marker shape, the second channel that survives greyscale.
LIGHT_PALETTE = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#882255", "#555555")
DARK_PALETTE = ("#5AB4F0", "#F08A4B", "#3CC79A", "#E39CC6", "#F2B33D", "#9AD3F5", "#D2729A", "#BBBBBB")
PRINT_PALETTE = ("#000000", "#4D4D4D", "#000000", "#4D4D4D", "#000000", "#4D4D4D", "#000000", "#4D4D4D")
DASHES = ("", "7 3", "2 2.5", "9 3 2 3", "12 4", "1.5 3.5", "6 2 2 2 2 2", "14 3 3 3")
MARKERS = ("circle", "square", "triangle", "diamond", "triangle_down", "plus", "cross", "hollow_square")

THEMES = {
    "light": {"bg": "#FFFFFF", "fg": "#1F2328", "muted": "#57606A", "grid": "#D0D7DE", "minor": "#EEF1F4", "frame": "#8C959F", "ref": "#424A53", "band": "#8C959F"},
    "dark": {"bg": "#15181D", "fg": "#E6EDF3", "muted": "#9DA7B3", "grid": "#3A414B", "minor": "#262B33", "frame": "#6E7681", "ref": "#C9D1D9", "band": "#ADBAC7"},
    "print": {"bg": "#FFFFFF", "fg": "#000000", "muted": "#333333", "grid": "#C8C8C8", "minor": "#E8E8E8", "frame": "#000000", "ref": "#000000", "band": "#000000"},
}
PALETTES = {"light": LIGHT_PALETTE, "dark": DARK_PALETTE, "print": PRINT_PALETTE}
ROLES = ("bg", "fg", "muted", "grid", "minor", "frame", "ref", "band")
COLOURS = len(LIGHT_PALETTE)


def colour_of(theme: str, role: str) -> str:
    base = "light" if theme == "auto" else theme
    if role.startswith("s"):
        return PALETTES[base][int(role[1:]) % COLOURS]
    return THEMES[base][role]


def series_style(index: int) -> tuple[int, int, int]:
    """(colour index, dash index, marker index) of series ``index``; the first 64 combinations are all different."""
    colour = index % COLOURS
    dash = (index + index // COLOURS) % len(DASHES)
    marker = (index + 2 * (index // COLOURS)) % len(MARKERS)
    return colour, dash, marker


# ----------------------------------------------------------------------------------------------- text


def esc(text: str) -> str:
    """XML escape; characters that XML 1.0 forbids (or UTF-8 cannot carry) are replaced first, whatever the source of the text."""
    text = _BAD.sub(_sub, text) if _BAD.search(text) else text
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def char_em(ch: str) -> float:
    return WIDE_EM if unicodedata.east_asian_width(ch) in ("W", "F") else CHAR_EM


def text_width(text: str, size: float) -> float:
    """Estimated rendered width in px: 0.6 em per character (1.0 em for wide characters)."""
    return sum(char_em(ch) for ch in text) * size


def fit(text: str, max_px: float, size: float) -> tuple[str, bool]:
    """``text`` shortened with an ellipsis so its estimated width is at most ``max_px``; returns (text, truncated)."""
    if text_width(text, size) <= max_px:
        return text, False
    budget = max_px - CHAR_EM * size
    used = 0.0
    out = []
    for ch in text:
        used += char_em(ch) * size
        if used > budget:
            break
        out.append(ch)
    return "".join(out).rstrip() + ELLIPSIS, True


def num(v: float) -> str:
    """A pixel coordinate: two decimals, trailing zeros trimmed, never -0, clamped to +-1e7."""
    if not math.isfinite(v):
        v = 0.0
    v = max(-PX_CLAMP, min(PX_CLAMP, v))
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "", "-") else s


# ----------------------------------------------------------------------------------------------- markers


def marker_path(shape: str, x: float, y: float, r: float = 3.5) -> tuple[str, bool]:
    """Path data of one marker centred on (x, y) and whether it is filled (else stroked)."""
    X, Y = num(x), num(y)
    if shape == "circle":
        return f"M{num(x - r)} {Y}a{num(r)} {num(r)} 0 1 0 {num(2 * r)} 0a{num(r)} {num(r)} 0 1 0 {num(-2 * r)} 0Z", True
    if shape in ("square", "hollow_square"):
        s = 0.88 * r
        return f"M{num(x - s)} {num(y - s)}h{num(2 * s)}v{num(2 * s)}h{num(-2 * s)}Z", shape == "square"
    if shape == "triangle":
        return f"M{X} {num(y - r * 1.1)}L{num(x + r)} {num(y + 0.8 * r)}L{num(x - r)} {num(y + 0.8 * r)}Z", True
    if shape == "triangle_down":
        return f"M{X} {num(y + r * 1.1)}L{num(x + r)} {num(y - 0.8 * r)}L{num(x - r)} {num(y - 0.8 * r)}Z", True
    if shape == "diamond":
        return f"M{X} {num(y - r * 1.15)}L{num(x + r)} {Y}L{X} {num(y + r * 1.15)}L{num(x - r)} {Y}Z", True
    if shape == "plus":
        return f"M{num(x - r)} {Y}H{num(x + r)}M{X} {num(y - r)}V{num(y + r)}", False
    return f"M{num(x - r)} {num(y - r)}L{num(x + r)} {num(y + r)}M{num(x - r)} {num(y + r)}L{num(x + r)} {num(y - r)}", False  # cross


# ----------------------------------------------------------------------------------------------- the document


class Canvas:
    """Collects SVG elements; every colour is written as a fallback attribute plus a class the auto theme restyles."""

    def __init__(self, width: int, height: int, theme: str, pid: str):
        self.width, self.height, self.theme, self.pid = width, height, theme, pid
        self.parts: list[str] = []
        self.elements = 0

    def colour(self, role: str) -> str:
        return colour_of(self.theme, role)

    def el(self, tag: str, attrs: list[tuple[str, str]], *, fill: str | None = None, stroke: str | None = None, text: str | None = None) -> None:
        """One element.  ``fill``/``stroke`` are colour roles ('fg', 'grid', 's3', ...) or 'none'."""
        out = list(attrs)
        classes = []
        if fill is not None:
            if fill == "none":
                out.append(("fill", "none"))
            else:
                out.append(("fill", self.colour(fill)))
                classes.append(f"{fill}-f")
        if stroke is not None:
            out.append(("stroke", self.colour(stroke)))
            classes.append(f"{stroke}-s")
        if classes:
            out.append(("class", " ".join(classes)))
        body = "".join(f' {k}="{esc(v)}"' for k, v in out)
        self.elements += 1
        if text is None:
            self.parts.append(f"<{tag}{body}/>")
        else:
            self.parts.append(f"<{tag}{body}>{esc(text)}</{tag}>")

    def begin(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        self.parts.append(f"<{tag}" + "".join(f' {k}="{esc(v)}"' for k, v in attrs) + ">")

    def end(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")

    def text(self, x: float, y: float, text: str, *, size: float, anchor: str = "start", role: str = "fg", weight: str | None = None, rotate: float | None = None) -> None:
        attrs = [("x", num(x)), ("y", num(y)), ("font-size", num(size))]
        if anchor != "start":
            attrs.append(("text-anchor", anchor))
        if weight:
            attrs.append(("font-weight", weight))
        if rotate is not None:
            attrs.append(("transform", f"rotate({num(rotate)} {num(x)} {num(y)})"))
        self.el("text", attrs, fill=role, text=text)

    def style(self) -> str:
        p = f"#{self.pid}"
        rules = [f"{p} text{{font-family:{FONT_STACK}}}"]
        if self.theme == "auto":
            def block(base: str) -> str:
                decl = [f"--etk-{r}:{THEMES[base][r]}" for r in ROLES] + [f"--etk-s{i}:{c}" for i, c in enumerate(PALETTES[base])]
                return f"{p}{{{';'.join(decl)}}}"

            rules.append(block("light"))
            rules.append(f"@media (prefers-color-scheme: dark){{{block('dark')}}}")
            names = list(ROLES) + [f"s{i}" for i in range(COLOURS)]
            for r in names:
                fallback = colour_of("light", r)
                rules.append(f"{p} .{r}-f{{fill:var(--etk-{r},{fallback})}}")
                rules.append(f"{p} .{r}-s{{stroke:var(--etk-{r},{fallback})}}")
        return "".join(rules)

    def document(self, title: str, desc: str) -> str:
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" id="{esc(self.pid)}" viewBox="0 0 {self.width} {self.height}" width="{self.width}" '
            f'height="{self.height}" role="img" aria-labelledby="{esc(self.pid)}-title {esc(self.pid)}-desc" font-family="{esc(FONT_STACK)}" font-size="12">'
            f'<title id="{esc(self.pid)}-title">{esc(title)}</title><desc id="{esc(self.pid)}-desc">{esc(desc)}</desc>'
            f"<style>{self.style()}</style>"
        )
        return head + "".join(self.parts) + "</svg>\n"


__all__ = [
    "COLOURS", "Canvas", "DASHES", "ELLIPSIS", "FONT_STACK", "MARKERS", "THEMES", "colour_of", "esc", "fit", "marker_path", "num",
    "series_style", "text_width",
]
