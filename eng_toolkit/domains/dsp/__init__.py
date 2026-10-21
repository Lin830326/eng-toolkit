"""``dsp`` domain: digital signal processing on sampled data (filter design, spectra, filtering, sampling, correlation, windows)."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.dsp import correlate, filter_apply, filter_design, sampling, spectrum, window  # noqa: F401  (register their tools)
from eng_toolkit.domains.dsp._server import server

TOOL_NAMES: tuple[str, ...] = ("filter_design", "spectrum", "filter_apply", "sampling", "correlate", "window")

DOMAIN = DomainInfo(
    name="dsp",
    title="Digital signal processing",
    description=(
        "Sampled-data tools: Butterworth and Chebyshev IIR and window-method FIR design with minimum-order equations, spectra "
        "(amplitude, PSD, Welch, peaks, SNR/SINAD/THD/SFDR/ENOB), filtering (SOS, zero-phase), aliasing, quantisation, decimation "
        "and interpolation, correlation, delay and Hilbert envelope, and window properties. Pure Python with numpy, no scipy."
    ),
    engines=("numpy",),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "TOOL_NAMES", "server"]
