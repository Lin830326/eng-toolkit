"""The one FastMCP server of the ``plot`` domain; the tool modules register on it."""

from __future__ import annotations

from fastmcp import FastMCP

ENGINE = "eng_toolkit.plot"

server = FastMCP(
    "plot",
    instructions=(
        "Deterministic, self-contained SVG charts of tables. Data is a CSV file with a header row (for example the CSV artifact "
        "another eng-toolkit tool wrote) named in 'data_file', or inline JSON in 'series': "
        '[{"name": "a", "x": [...], "y": [...]}]. At most 20 series of 200000 points each. Labels are plain text (at most 120 '
        "characters, escaped); write the unit in the label yourself, e.g. 'Frequency (Hz)'. The SVG is returned inline when it "
        "is at most 150 kB and written as plot.svg; the default format=both also writes plot.png, 1600 px wide, whose path is data.png_path "
        "(needs the png extra) and which comes back as an image in the reply when it is <= 600 kB (inline_image). Nothing you send is executed."
    ),
)

__all__ = ["ENGINE", "server"]
