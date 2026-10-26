"""How a KiCad report is summarised: library warnings apart from real findings, and counts at kicad-cli's own listing limit.
No KiCad needed here; tests/test_pcb_kicad.py repeats the cases against the real program."""

from eng_toolkit.domains.pcb import compute


def rows(kind, severity, n):
    return [{"type": kind, "severity": severity, "description": "d", "items": []} for _ in range(n)]


def test_library_warnings_are_separable_and_still_counted_in_the_totals():
    violations = rows("lib_footprint_issues", "warning", 94) + rows("silk_overlap", "warning", 3) + rows("clearance", "error", 2)
    s = compute._report_summary(violations, 100)
    assert s["total"] == 99 and s["errors"] == 2 and s["warnings"] == 97  # KiCad's own counts are unchanged
    assert s["library_warnings"]["count"] == 94 and [r["type"] for r in s["library_warnings"]["by_type"]] == ["lib_footprint_issues"]
    assert s["design_warnings"] == 3
    assert {r["type"]: r["group"] for r in s["by_type"]} == {"clearance": "design", "silk_overlap": "design", "lib_footprint_issues": "library"}
    # the listing puts errors first and the library noise after the real warnings
    assert [v["type"] for v in s["list"]][:5] == ["clearance", "clearance", "silk_overlap", "silk_overlap", "silk_overlap"]
    assert all(v["group"] == "library" for v in s["list"][5:])


def test_a_count_at_the_kicad_limit_is_flagged_as_a_lower_bound():
    limits = compute.KICAD_REPORT_LIMITS
    assert limits == (199, 499)
    s = compute._report_summary(rows("clearance", "error", 499) + rows("track_width", "error", 199) + rows("silk_overlap", "warning", 61), 10, limits)
    assert {(r["type"], r["count"]) for r in s["at_kicad_limit"]} == {("clearance", 499), ("track_width", 199)}
    flags = {r["type"]: r["at_kicad_limit"] for r in s["by_type"]}
    assert flags == {"clearance": True, "track_width": True, "silk_overlap": False}
    below = compute._report_summary(rows("clearance", "error", 498), 10, limits)
    assert below["at_kicad_limit"] == []


def test_the_erc_summary_does_not_assume_the_drc_limits():
    s = compute._report_summary(rows("pin_not_connected", "error", 499), 10)
    assert s["at_kicad_limit"] == []
