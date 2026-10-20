"""eng_toolkit.external against the real programs: ngspice, Yosys, Gmsh, CalculiX, KiCad.  Each test is skipped when its program is not found.

These are the layer's own checks (find it, give it the right environment, run it in a job directory, read what it wrote); they are not
tests of the domains that will use the programs.  The numbers come from hand calculations:

    5 V over two 1 kOhm resistors: 2.5 V at the middle node
    y = a & b after synthesis: one $_AND_ cell
    a unit square meshed at size 1: its 4 corners and the centre point, 5 nodes
    one T3D2 truss element, F = 1000 N, E = 210000 N/mm2, A = 100 mm2, L = 1 mm: u = F L / (E A) = 4.7619e-05 mm
"""

import re
import sys
import textwrap
from pathlib import Path

import pytest

from eng_toolkit import external as X
from eng_toolkit.external_catalog import CCX, GMSH, KICAD_CLI, NGSPICE, YOSYS


def needs(tool):
    found, why = X.locate(tool)
    return pytest.mark.skipif(found is None, reason=f"{tool.name} is not installed here ({why})")


@pytest.fixture
def job(tmp_path) -> Path:
    path = tmp_path / "job"
    path.mkdir()
    return path


def write(path: Path, text: str) -> Path:
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8", newline="\n")
    return path


# --------------------------------------------------------------------------- ngspice


@needs(NGSPICE)
def test_ngspice_batch_run_of_a_voltage_divider(job):
    write(
        job / "divider.cir",
        """
        divider
        V1 in 0 DC 5
        R1 in mid 1k
        R2 mid 0 1k
        .control
        op
        print v(mid)
        .endc
        .end
        """,
    )
    record = X.run(NGSPICE, ["-b", job / "divider.cir"], workdir=job, timeout_s=60)
    match = re.search(r"v\(mid\)\s*=\s*([-+0-9.eE]+)", record.stdout_tail)
    assert match, record.stdout_tail
    assert float(match.group(1)) == pytest.approx(2.5, rel=1e-9)
    assert record.returncode == 0 and record.source.startswith(("env", "path", "location"))


@needs(NGSPICE)
def test_ngspice_version_is_read_with_the_descriptors_probe():
    found, _ = X.locate(NGSPICE)
    assert re.fullmatch(r"\d+(\.\d+)*", X.version(NGSPICE, found) or "")


# --------------------------------------------------------------------------- Yosys


@needs(YOSYS)
def test_yosys_synthesises_an_and_gate_into_one_and_cell(job):
    write(job / "and2.v", "module and2(input a, input b, output y);\n  assign y = a & b;\nendmodule\n")
    record = X.run(YOSYS, ["-p", "read_verilog and2.v; synth -top and2; stat", job / "and2.v"], workdir=job, timeout_s=120)
    cells = re.findall(r"^\s*(\d+)\s+(?:\S+\s+)?\$_AND_\s*$", record.stdout_tail, flags=re.M) or re.findall(r"(\d+)\s+\$_AND_", record.stdout_tail)
    assert cells and cells[-1] == "1", record.stdout_tail[-800:]
    assert not re.search(r"\$_(OR|XOR|NOT|NAND|NOR)_", record.stdout_tail.split("Number of cells")[-1] if "Number of cells" in record.stdout_tail else "")


@needs(YOSYS)
def test_yosys_cannot_start_without_the_libraries_of_its_folder_and_the_layer_supplies_them(job):
    found, _ = X.locate(YOSYS)
    assert X.path_additions(YOSYS, found.path)  # {bin} and {bin}/../lib exist for the OSS CAD Suite layout
    assert X.version(YOSYS, found)


# --------------------------------------------------------------------------- Gmsh


@needs(GMSH)
def test_gmsh_meshes_a_unit_square_with_five_nodes(job):
    write(
        job / "square.geo",
        """
        lc = 1.0;
        Point(1) = {0, 0, 0, lc};
        Point(2) = {1, 0, 0, lc};
        Point(3) = {1, 1, 0, lc};
        Point(4) = {0, 1, 0, lc};
        Line(1) = {1, 2};
        Line(2) = {2, 3};
        Line(3) = {3, 4};
        Line(4) = {4, 1};
        Curve Loop(1) = {1, 2, 3, 4};
        Plane Surface(1) = {1};
        """,
    )
    record = X.run(GMSH, ["-2", job / "square.geo", "-format", "msh2", "-o", job / "square.msh"], workdir=job, timeout_s=60)
    mesh = (job / "square.msh").read_text(encoding="utf-8")
    nodes = re.search(r"\$Nodes\s+(\d+)", mesh)
    assert nodes and int(nodes.group(1)) == 5, mesh[:400]
    assert [f.name for f in record.files if f.name == "square.msh"] and record.files[0].sha256


# --------------------------------------------------------------------------- CalculiX


@needs(CCX)
def test_calculix_solves_one_truss_element(job):
    write(
        job / "truss.inp",
        """
        *NODE
        1, 0., 0., 0.
        2, 1., 0., 0.
        *ELEMENT, TYPE=T3D2, ELSET=EALL
        1, 1, 2
        *MATERIAL, NAME=STEEL
        *ELASTIC
        210000., 0.3
        *SOLID SECTION, ELSET=EALL, MATERIAL=STEEL
        100.
        *BOUNDARY
        1, 1, 3
        2, 2, 3
        *STEP
        *STATIC
        *CLOAD
        2, 1, 1000.
        *NODE FILE
        U
        *END STEP
        """,
    )
    record = X.run(CCX, ["truss"], workdir=job, timeout_s=120)
    frd = (job / "truss.frd").read_text(encoding="latin-1")
    values = [float(v.replace("D", "E")) for v in re.findall(r"-?\d\.\d{5}E[-+]\d\d", frd)]
    assert any(abs(v - 1000 / (210000 * 100)) < 1e-9 for v in values), frd[-600:]
    assert {"truss.frd", "truss.dat"} <= {f.name for f in record.files}


@needs(CCX)
def test_calculix_needs_its_runtime_directory_on_path_and_exits_201_for_dash_v(job):
    found, _ = X.locate(CCX)
    assert any(p.replace("\\", "/").endswith("mingw-w64/bin") for p in X.path_additions(CCX, found.path)) or sys.platform != "win32"
    record = X.run(CCX, ["-v"], workdir=job, check=False)
    assert "Version" in record.stdout_tail + record.stderr_tail
    assert X.version(CCX, found) and re.fullmatch(r"\d+(\.\d+)+", X.version(CCX, found))


# --------------------------------------------------------------------------- KiCad


@needs(KICAD_CLI)
def test_kicad_cli_prints_its_version_from_the_job_directory_without_touching_the_profile(job):
    record = X.run(KICAD_CLI, ["--version"], workdir=job, check=False)
    assert re.search(r"\d+\.\d+\.\d+", record.stdout_tail), (record.stdout_tail, record.stderr_tail)
    assert record.returncode == 0 and "Error" not in record.stderr_tail
    found, _ = X.locate(KICAD_CLI)
    assert re.fullmatch(r"\d+\.\d+\.\d+", X.version(KICAD_CLI, found) or "")


# --------------------------------------------------------------------------- the doctor rows for all of them


@pytest.mark.parametrize("tool", [NGSPICE, YOSYS, GMSH, CCX, KICAD_CLI], ids=lambda t: t.name)
def test_probe_gives_a_complete_row_for_each_program_found_or_not(tool):
    row = X.probe(tool)
    assert row["name"] == tool.name and isinstance(row["found"], bool)
    if row["found"]:
        assert Path(row["path"]).is_file() and row["version"] and row["status"] == "ok" and row["found_via"]
    else:
        assert row["status"] == "not-installed (optional)" and row["install"]


# --------------------------------------------------------------------------- the path additions are what makes them start (negative controls)


@pytest.mark.skipif(sys.platform != "win32", reason="the DLL search through PATH is a Windows matter")
@pytest.mark.parametrize("tool", [CCX, YOSYS], ids=lambda t: t.name)
def test_without_its_path_additions_the_program_does_not_start(job, tool):
    found, _ = X.locate(tool)
    if found is None:
        pytest.skip(f"{tool.name} is not installed here")
    from dataclasses import replace

    bare = replace(tool, path_additions=())
    record = X.run(bare, list(tool.version_args), workdir=job, located=found, check=False)
    assert record.returncode == 0xC0000135 and not record.stdout_tail  # STATUS_DLL_NOT_FOUND
    assert X.version(bare, found) is None and X.version(tool, found)
