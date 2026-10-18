"""pyproject's optional-dependencies and the engine registry describe the same extras."""

import re
import tomllib
from pathlib import Path

import pytest

from eng_toolkit.licenses import KNOWN, install_hint

PYPROJECT = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
PROJECT_NAME = PYPROJECT["project"]["name"]
EXTRAS: dict[str, list[str]] = PYPROJECT["project"]["optional-dependencies"]
REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def dists_of(extra: str, seen: frozenset[str] = frozenset()) -> set[str]:
    """Distributions an extra installs, following entries that point back at this project's own extras."""
    assert extra not in seen, f"extra {extra!r} includes itself"
    found: set[str] = set()
    for line in EXTRAS[extra]:
        match = REQUIREMENT.match(line)
        assert match, f"cannot parse requirement {line!r}"
        name = _canonical(match.group(1))
        if name == _canonical(PROJECT_NAME):
            for inner in (e.strip() for e in (match.group(2) or "").split(",") if e.strip()):
                found |= dists_of(inner, seen | {extra})
        else:
            found.add(name)
    return found


OPTIONAL = [engine for engine in KNOWN.values() if engine.optional]


def test_the_registry_has_optional_engines_to_check():
    assert {e.dist for e in OPTIONAL} >= {"open3d", "cadquery-ocp-novtk"}


@pytest.mark.parametrize("engine", OPTIONAL, ids=lambda e: e.dist)
def test_an_optional_engine_names_the_extra_that_installs_it(engine):
    assert engine.extra, f"{engine.dist} is optional but has no extra"
    assert engine.extra in EXTRAS, f"{engine.dist}: extra {engine.extra!r} is not in pyproject"
    assert engine.extra in install_hint(engine.dist)
    assert install_hint(engine.dist) == f"uv sync --extra {engine.extra}"
    assert _canonical(engine.dist) in dists_of(engine.extra)


@pytest.mark.parametrize("engine", [e for e in KNOWN.values() if not e.optional], ids=lambda e: e.dist)
def test_a_core_engine_has_no_extra(engine):
    assert engine.extra == ""
    assert install_hint(engine.dist) == "uv sync"


@pytest.mark.parametrize("extra", sorted(EXTRAS))
def test_every_distribution_in_an_extra_is_in_the_registry(extra):
    unknown = {d for d in dists_of(extra) if d not in {_canonical(k) for k in KNOWN}}
    assert not unknown, f"extra {extra!r} installs {sorted(unknown)} which licenses.KNOWN does not declare"


def test_scan_installs_both_engines_and_cad_only_the_kernel():
    assert dists_of("cad") == {"cadquery-ocp-novtk"}
    assert dists_of("scan") == {"cadquery-ocp-novtk", "open3d"}


def test_install_hint_rejects_an_unknown_distribution():
    with pytest.raises(KeyError):
        install_hint("no-such-distribution")
