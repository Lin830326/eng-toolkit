"""The one FastMCP server of the ``dsp`` domain; the tool modules register on it."""

from __future__ import annotations

from fastmcp import FastMCP

ENGINE = "eng_toolkit.dsp"

server = FastMCP(
    "dsp",
    instructions=(
        "Digital signal processing on sampled data. Samples are a JSON list of numbers in the 'samples' parameter or a CSV file "
        "(one column of values, or two columns time,value with time in seconds) named in 'samples_file'. Frequencies and sample "
        "rates are strings with a unit: '48 kHz', '1000 Hz', '6283 rad/s'. At most 4194304 samples; IIR order at most 40; FIR at "
        "most 4095 taps. Results are unreviewed estimates; the tools never execute the text you give them."
    ),
)

__all__ = ["ENGINE", "server"]
