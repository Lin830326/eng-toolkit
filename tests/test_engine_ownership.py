"""One owner per engine: OCP lives in eng_toolkit/brep, Open3D in metrology/scan.py, and brep's internals stay inside brep."""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "eng_toolkit"
OCP_OWNER = "brep/"
OPEN3D_OWNER = "domains/metrology/scan.py"
BREP = "eng_toolkit.brep"


def _is_module(name: str, root: str) -> bool:
    return name == root or name.startswith(root + ".")


def _dynamic_import_target(node: ast.Call) -> str | None:
    func = node.func
    callee = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    if callee in ("import_module", "__import__") and node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def violations(source: str, relpath: str) -> list[str]:
    """Ownership rules broken by one module; ``relpath`` is its path below eng_toolkit/ with forward slashes."""
    inside_brep = relpath.startswith(OCP_OWNER)
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        imported: list[str] = []
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported = [node.module]
        elif isinstance(node, ast.Call):
            target = _dynamic_import_target(node)
            imported = [target] if target else []
        for name in imported:
            if _is_module(name, "OCP") and not inside_brep:
                found.append(f"{relpath}:{node.lineno} imports {name}; only eng_toolkit/brep may use OCP")
            if _is_module(name, "open3d") and relpath != OPEN3D_OWNER:
                found.append(f"{relpath}:{node.lineno} imports {name}; only {OPEN3D_OWNER} may use Open3D")
            if _is_module(name, BREP) and not inside_brep:
                private = [part for part in name[len(BREP) :].split(".") if part.startswith("_")]
                if private:
                    found.append(f"{relpath}:{node.lineno} imports private module {name}")
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and not inside_brep:
            if _is_module(node.module, BREP):
                for alias in node.names:
                    if alias.name.startswith("_"):
                        found.append(f"{relpath}:{node.lineno} imports private name {alias.name} from {node.module}")
        if isinstance(node, ast.Attribute) and not inside_brep:
            if node.attr == "_handle":
                found.append(f"{relpath}:{node.lineno} reads ._handle, which never leaves eng_toolkit/brep")
            if isinstance(node.value, ast.Name) and node.value.id == "brep" and node.attr.startswith("_"):
                found.append(f"{relpath}:{node.lineno} reaches into brep.{node.attr}")
    return found


def _modules() -> list[tuple[str, str]]:
    return [(path.relative_to(PACKAGE).as_posix(), path.read_text(encoding="utf-8")) for path in sorted(PACKAGE.rglob("*.py"))]


def test_every_module_respects_engine_ownership():
    modules = _modules()
    assert len(modules) > 10
    found = [message for relpath, source in modules for message in violations(source, relpath)]
    assert not found, "\n".join(found)


def test_the_owners_actually_use_their_engines():
    sources = dict(_modules())
    assert "OCP" in sources["brep/_files.py"]
    assert "open3d" in sources[OPEN3D_OWNER]


@pytest.mark.parametrize(
    ("source", "relpath"),
    [
        ("import OCP\n", "domains/geometry/tools.py"),
        ("import OCP.gp as gp\n", "domains/geometry/tools.py"),
        ("from OCP.gp import gp_Pnt\n", "domains/metrology/scan.py"),
        ("def f():\n    from OCP import BRep\n", "runner.py"),
        ("import importlib\nimportlib.import_module('OCP.BRep')\n", "units.py"),
        ("__import__('OCP')\n", "units.py"),
        ("import open3d as o3d\n", "domains/geometry/tools.py"),
        ("from open3d import geometry\n", "brep/_files.py"),
        ("def f():\n    import open3d\n", "domains/metrology/tools.py"),
        ("from eng_toolkit.brep import _handle\n", "domains/geometry/tools.py"),
        ("from eng_toolkit.brep._files import read_solid\n", "domains/geometry/tools.py"),
        ("import eng_toolkit.brep._occ\n", "domains/geometry/tools.py"),
        ("def f(solid):\n    return solid._handle\n", "domains/geometry/tools.py"),
        ("from eng_toolkit import brep\nbrep._occ.load()\n", "domains/geometry/tools.py"),
    ],
)
def test_the_checker_reports_a_forbidden_use(source, relpath):
    assert violations(source, relpath)


@pytest.mark.parametrize(
    ("source", "relpath"),
    [
        ("import OCP\nfrom OCP.gp import gp_Pnt\n", "brep/_files.py"),
        ("solid._handle\n", "brep/_files.py"),
        ("from eng_toolkit.brep._files import read_solid\n", "brep/__init__.py"),
        ("import open3d as o3d\n", "domains/metrology/scan.py"),
        ("from eng_toolkit import brep\nbrep.read_solid\n", "domains/metrology/scan.py"),
        ("from eng_toolkit.brep import read_solid, Solid\n", "domains/geometry/tools.py"),
        ("import numpy\n", "domains/geometry/tools.py"),
        ("my_open3d = 1\nOCPus = 2\n", "runner.py"),
    ],
)
def test_the_checker_accepts_the_allowed_uses(source, relpath):
    assert violations(source, relpath) == []
