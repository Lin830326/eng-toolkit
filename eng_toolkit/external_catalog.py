"""Descriptors of the external programs the toolkit knows how to find and start.

Data only.  A domain that uses a program registers it in its own ``engines.py``:

    from eng_toolkit.external_catalog import NGSPICE
    EXTERNAL_TOOLS = (NGSPICE,)

so a program is registered (and shown by ``toolkit_doctor``) only when a domain that needs it is installed.  The places to look and the
``path_additions`` below were checked against the real programs on Windows 11 (``tests/test_external_real.py``); the Linux and macOS
entries are the usual package locations and were not run.  Nothing here is read from a document.

Tiers: ``gpl-isolated`` for a GPL program (dwg2dxf, Gmsh, CalculiX, kicad-cli), ``permissive`` for the rest (ngspice is a modified
BSD licence, Yosys is ISC).  Both are only ever started as separate processes.
"""

from __future__ import annotations

from eng_toolkit.licenses import TIER_GPL, TIER_PERMISSIVE, ExternalTool

_CONDA_ROOTS = (r"%USERPROFILE%\miniconda3", r"%USERPROFILE%\anaconda3", r"%USERPROFILE%\miniforge3", r"%LOCALAPPDATA%\miniconda3", r"%ProgramData%\miniconda3")
_CONDA_WINDOWS = tuple(f"{root}{tail}" for root in _CONDA_ROOTS for tail in (r"\envs\*\Library\bin", r"\Library\bin"))
_UNIX_BIN = ("/usr/local/bin", "/usr/bin", "/opt/homebrew/bin", "/snap/bin", "~/.local/bin")
_OSS_CAD = (r"%USERPROFILE%\tools\oss-cad-suite-root\oss-cad-suite\bin", r"%USERPROFILE%\oss-cad-suite\bin", r"C:\oss-cad-suite\bin", "/opt/oss-cad-suite/bin", "~/oss-cad-suite/bin")

DWG2DXF = ExternalTool(
    name="dwg2dxf",
    license="GPL-3.0-or-later",
    tier=TIER_GPL,
    role="converts a DWG drawing to DXF (LibreDWG); started as a separate process only, never bundled, imported or linked",
    env_var="ENG_TOOLKIT_DWG2DXF",
    executables=("dwg2dxf", "dwg2dxf.exe"),
    project="https://www.gnu.org/software/libredwg/",
    domains=("drawing",),
    locations=(*_UNIX_BIN, r"C:\Program Files\LibreDWG\bin", r"C:\libredwg\bin"),
    keep_env=("LD_LIBRARY_PATH",),
)

NGSPICE = ExternalTool(
    name="ngspice",
    license="BSD-3-Clause",
    tier=TIER_PERMISSIVE,
    role="circuit simulator, run in batch mode (ngspice_con on Windows)",
    env_var="ENG_TOOLKIT_NGSPICE",
    executables=("ngspice_con.exe", "ngspice_con", "ngspice.exe", "ngspice"),
    project="https://ngspice.sourceforge.io/",
    domains=("spice",),
    locations=(*_CONDA_WINDOWS, r"C:\Program Files\Spice64\bin", r"C:\Spice64\bin", *_UNIX_BIN),
    version_args=("-v",),
    version_regex=r"ngspice-(\d+(?:\.\d+)*)",
)

YOSYS = ExternalTool(
    name="yosys",
    license="ISC",
    tier=TIER_PERMISSIVE,
    role="logic synthesis from Verilog",
    env_var="ENG_TOOLKIT_YOSYS",
    executables=("yosys.exe", "yosys"),
    project="https://yosyshq.net/yosys/",
    domains=("digital",),
    locations=(*_OSS_CAD, *_UNIX_BIN),
    path_additions=("{bin}", "{bin}/../lib"),  # the OSS CAD Suite build finds its DLLs next to its bin and lib folders only
    version_args=("-V",),
    version_regex=r"Yosys (\d+(?:\.\d+)*(?:\+\d+)?)",
)

GMSH = ExternalTool(
    name="gmsh",
    license="GPL-2.0-or-later",
    tier=TIER_GPL,
    role="mesh generator (batch mode, geometry in a .geo file)",
    env_var="ENG_TOOLKIT_GMSH",
    executables=("gmsh.exe", "gmsh"),
    project="https://gmsh.info/",
    domains=("fem",),
    locations=(*_CONDA_WINDOWS, r"C:\Program Files\gmsh", *_UNIX_BIN),
    version_args=("--version",),
)

CCX = ExternalTool(
    name="ccx",
    license="GPL-2.0-or-later",
    tier=TIER_GPL,
    role="CalculiX finite element solver (batch mode, an .inp job)",
    env_var="ENG_TOOLKIT_CCX",
    executables=("ccx.exe", "ccx", "ccx_2.23", "ccx_2.22", "ccx_2.21"),
    project="https://www.calculix.de/",
    domains=("fem",),
    locations=(*_CONDA_WINDOWS, *_UNIX_BIN),
    path_additions=("{bin}", "{bin}/../mingw-w64/bin"),  # the conda build needs the MinGW runtime DLLs; without them it exits 0xC0000135
    version_args=("-v",),  # prints "This is Version 2.23" and exits 201
    version_regex=r"Version (\d+(?:\.\d+)+)",
)

KICAD_CLI = ExternalTool(
    name="kicad-cli",
    license="GPL-3.0-or-later",
    tier=TIER_GPL,
    role="KiCad command line: design rule and electrical rule checks, exports",
    env_var="ENG_TOOLKIT_KICAD_CLI",
    executables=("kicad-cli.exe", "kicad-cli"),
    project="https://www.kicad.org/",
    domains=("pcb",),
    locations=(
        r"%LOCALAPPDATA%\Programs\KiCad\*\bin",
        r"%ProgramFiles%\KiCad\*\bin",
        "/Applications/KiCad/KiCad.app/Contents/MacOS",
        *_UNIX_BIN,
    ),
    version_args=("--version",),
)

CATALOG: dict[str, ExternalTool] = {t.name: t for t in (DWG2DXF, NGSPICE, YOSYS, GMSH, CCX, KICAD_CLI)}

__all__ = ["CATALOG", "CCX", "DWG2DXF", "GMSH", "KICAD_CLI", "NGSPICE", "YOSYS"]
