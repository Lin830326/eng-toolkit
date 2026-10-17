"""License policy: the toolkit environment must contain no GPL/AGPL/forbidden distributions."""

import pytest

from eng_toolkit.licenses import (
    KNOWN,
    TIER_FORBIDDEN,
    TIER_GPL,
    TIER_LGPL,
    TIER_PERMISSIVE,
    TIER_UNKNOWN,
    classify_license,
    installed_license,
    scan_environment,
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


def test_every_known_engine_is_installed_and_permissive():
    for dist, engine in KNOWN.items():
        version, text = installed_license(dist)
        assert version is not None, f"{dist} not installed"
        assert engine.tier == TIER_PERMISSIVE
        assert classify_license(text) == TIER_PERMISSIVE, (dist, text)


def test_environment_has_no_gpl_or_forbidden_distributions():
    rows = scan_environment()
    bad = [r for r in rows if r["tier"] in (TIER_GPL, TIER_FORBIDDEN)]
    assert bad == [], f"GPL/AGPL/forbidden distributions present: {bad}"
