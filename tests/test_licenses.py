"""License policy: the toolkit environment must contain no GPL/AGPL/forbidden distributions."""

import pytest

from eng_toolkit.licenses import (
    KNOWN,
    Engine,
    TIER_FORBIDDEN,
    TIER_GPL,
    TIER_LGPL,
    TIER_PERMISSIVE,
    TIER_UNKNOWN,
    classify_license,
    installed_license,
    scan_environment,
    tier_explained_by_bundle,
    violates_policy,
)


@pytest.mark.parametrize(
    "text,tier",
    [
        ("MIT", TIER_PERMISSIVE),
        ("Apache-2.0", TIER_PERMISSIVE),
        ("BSD-3-Clause", TIER_PERMISSIVE),
        ("License :: OSI Approved :: BSD License", TIER_PERMISSIVE),
        ("LGPL-2.1-or-later", TIER_LGPL),
        ("GNU Lesser General Public License v3", TIER_LGPL),
        ("GPL-3.0-only", TIER_GPL),
        ("License :: OSI Approved :: GNU General Public License v2 (GPLv2)", TIER_GPL),
        ("AGPL-3.0", TIER_FORBIDDEN),
        ("GNU Affero General Public License v3", TIER_FORBIDDEN),
        ("CC-BY-NC-4.0", TIER_FORBIDDEN),
        ("Business Source License 1.1", TIER_FORBIDDEN),
        (None, TIER_UNKNOWN),
        ("Proprietary", TIER_UNKNOWN),
    ],
)
def test_classify_license(text, tier):
    assert classify_license(text) == tier


def test_every_required_engine_is_installed_and_permissive():
    required = {dist: e for dist, e in KNOWN.items() if not e.optional}
    assert required, "the core engines must be listed"
    for dist, engine in required.items():
        version, text = installed_license(dist)
        assert version is not None, f"{dist} not installed"
        assert engine.tier == TIER_PERMISSIVE
        assert classify_license(text) == TIER_PERMISSIVE, (dist, text)


def test_optional_engines_are_declared_with_their_real_tier():
    assert KNOWN["open3d"].optional and KNOWN["open3d"].tier == TIER_PERMISSIVE
    ocp = KNOWN["cadquery-ocp-novtk"]
    assert ocp.optional
    assert ocp.tier == TIER_LGPL
    # the wrapper metadata says Apache-2.0; the tier comes from the OCCT code bundled in the wheel
    assert classify_license(ocp.bundled_license) == TIER_LGPL


def test_bundle_explains_only_a_declared_lgpl_dynamic_against_a_permissive_wrapper():
    ocp = KNOWN["cadquery-ocp-novtk"]
    assert tier_explained_by_bundle(ocp, TIER_PERMISSIVE)
    assert not tier_explained_by_bundle(ocp, TIER_LGPL)  # equal tiers need no explanation
    assert not tier_explained_by_bundle(ocp, TIER_GPL)  # a looser declaration than reality is a real mismatch
    assert not tier_explained_by_bundle(ocp, TIER_FORBIDDEN)
    assert not tier_explained_by_bundle(ocp, TIER_UNKNOWN)
    # without a bundled license a stricter declaration is never explained away
    plain = Engine("plain", "LGPL-2.1", TIER_LGPL, "test engine")
    assert not tier_explained_by_bundle(plain, TIER_PERMISSIVE)


@pytest.mark.parametrize("declared", [TIER_GPL, TIER_FORBIDDEN])
@pytest.mark.parametrize("detected", [TIER_PERMISSIVE, TIER_LGPL, TIER_GPL, TIER_FORBIDDEN, TIER_UNKNOWN])
def test_a_bundled_license_never_explains_away_a_gpl_or_forbidden_declaration(declared, detected):
    engine = Engine("x", "MIT", declared, "engine bundling GPL code", bundled_license="GPL-3.0-only solver in the wheel")
    assert not tier_explained_by_bundle(engine, detected)


def test_ezdxf_is_declared_permissive_and_optional_and_pymupdf_is_a_forbidden_guard():
    ezdxf = KNOWN["ezdxf"]
    assert ezdxf.optional and ezdxf.tier == TIER_PERMISSIVE and ezdxf.extra == "drawing" and ezdxf.domains == ("drawing",)
    guard = KNOWN["pymupdf"]
    assert guard.optional and guard.tier == TIER_FORBIDDEN and classify_license(guard.license) == TIER_FORBIDDEN
    assert violates_policy(guard) and not violates_policy(ezdxf)


@pytest.mark.parametrize("metadata", ["AGPL-3.0-only", "MIT", "GNU Affero General Public License v3"])
def test_the_doctor_flags_an_installed_pymupdf_as_a_policy_failure(monkeypatch, metadata):
    from eng_toolkit.gateway import server as srv

    real = srv.installed_license
    monkeypatch.setattr(srv, "installed_license", lambda dist: ("1.24.0", metadata) if dist == "pymupdf" else real(dist))
    report = getattr(srv.toolkit_doctor, "fn", srv.toolkit_doctor)(scan_env=False)
    row = next(d for d in report.data["dependencies"] if d["dist"] == "pymupdf")
    assert row["status"] == "policy-violation"
    assert report.ok is False and any("pymupdf" in f for f in report.data["failures"])


def test_environment_has_no_gpl_or_forbidden_distributions():
    rows = scan_environment()
    bad = [r for r in rows if r["tier"] in (TIER_GPL, TIER_FORBIDDEN)]
    assert bad == [], f"GPL/AGPL/forbidden distributions present: {bad}"
