"""Public plate results checked against analytical coefficients and boundary identities."""

import math

import pytest

from eng_toolkit.domains.structures3d.plate import plate
from tests.structures3d_testing import call, close, ok, quantity, table, v


PLATE = dict(length="1 m", width="1 m", thickness="10 mm", youngs_modulus="200 GPa", pressure="1 kPa")
D = 200e9 * 0.01**3 / (12 * (1 - 0.3**2))


def test_uniform_square_coefficient_moments_and_convergence():
    result = ok(plate, **PLATE, terms=81)
    # Independent scalar Navier sums at x=y=a/2, using 151 odd indices per axis.
    # sin(m*pi/2)*sin(n*pi/2)=(-1)^((m+n-2)/2). Divide by p*a^4/D for alpha,
    # and by p*a^2 for beta. 81 production modes give <3e-5 relative moment error.
    odd = range(1, 302, 2)
    alpha = 16 / math.pi**6 * math.fsum((-1)**((m + n - 2) // 2) / (m * n * (m*m + n*n)**2) for m in odd for n in odd)
    beta = 16 / math.pi**4 * math.fsum((-1)**((m + n - 2) // 2) * (m*m + 0.3*n*n) / (m * n * (m*m + n*n)**2) for m in odd for n in odd)
    close(alpha, 0.00406235266, rel=1e-8)
    close(v(result, "centre_deflection", "m"), alpha * 1000 / D, rel=1e-7)
    close(v(result, "alpha"), alpha, rel=1e-7)
    close(v(result, "beta_x"), beta, rel=3e-5)
    close(v(result, "beta_y"), beta, rel=3e-5)
    close(v(result, "flexural_rigidity", "N*m"), D)
    centre = next(row for row in table(result) if float(row["x_m"]) == 0.5 and float(row["y_m"]) == 0.5)
    close(float(centre["Mx_N"]), beta * 1000, rel=3e-5)
    close(float(centre["My_N"]), beta * 1000, rel=3e-5)
    assert result.data["convergence"]["passed"]
    assert result.data["stress_available"]


@pytest.mark.parametrize("aspect", [0.2, 1, 5])
def test_navier_levy_agree_on_rectangles(aspect):
    arguments = {**PLATE, "length": f"{aspect} m", "terms": 101}
    navier = ok(plate, **arguments)
    levy = ok(plate, **arguments, method="levy")
    close(v(levy, "centre_deflection"), v(navier, "centre_deflection"), rel=3e-6)
    for nr, lr in zip(table(navier), table(levy)):
        close(float(lr["w_m"]), float(nr["w_m"]), rel=1e-5, abs=1e-9)


def test_clamped_levy_and_free_strip_limits():
    clamped = ok(plate, **PLATE, method="levy", edges="SCSC", terms=81)
    close(v(clamped, "centre_deflection"), 0.001917138 * 1000 / D, rel=1e-7)
    free = ok(plate, **{**PLATE, "poisson_ratio": 0.}, method="levy", edges="SFSF", terms=81)
    d = 200e9 * 0.01**3 / 12
    expected = 5 * 1000 / (384 * d)
    close(v(free, "centre_deflection"), expected, rel=1e-8)
    rows = table(free)
    mids = [row for row in rows if float(row["x_m"]) == 0.5]
    assert all(float(row["w_m"]) == pytest.approx(expected, rel=1e-8) for row in mids)
    assert all(abs(float(row["My_N"])) < 1e-8 for row in rows)


def test_free_edge_moment_condition_and_signed_load():
    result = ok(plate, **PLATE, method="levy", edges="SFSF")
    for row in table(result):
        if float(row["y_m"]) in (0., 1.):
            assert abs(float(row["My_N"])) < 1e-8
    negative = ok(plate, **{**PLATE, "pressure": "-1 kPa"}, method="levy", edges="SFSF")
    close(v(negative, "centre_deflection"), -v(result, "centre_deflection"))


def test_arbitrary_point_reciprocity_and_source_singularity():
    first = ok(plate, **{**PLATE, "pressure": "0 Pa"}, point_loads="0.3 m,0.4 m,100 N", terms=101)
    second = ok(plate, **{**PLATE, "pressure": "0 Pa"}, point_loads="0.65 m,0.55 m,100 N", terms=101)
    rows = table(first)
    source = next(row for row in rows if abs(float(row["x_m"]) - 0.3) < 1e-12 and abs(float(row["y_m"]) - 0.4) < 1e-12)
    assert source["Mx_N"] == source["My_N"] == source["Mxy_N"] == ""
    assert not first.data["stress_available"]
    assert "maximum_sampled_von_mises_stress" not in first.values
    assert quantity(first.data["point_load_deflections"][0]["w"], "m") > 0
    def sample(result, x, y):
        return float(next(row for row in table(result) if abs(float(row["x_m"]) - x) < 1e-12 and abs(float(row["y_m"]) - y) < 1e-12)["w_m"])
    close(sample(first, 0.65, 0.55), sample(second, 0.3, 0.4), rel=1e-10)


def test_point_uniform_superposition_and_unit_equivalence():
    point = ok(plate, **{**PLATE, "pressure": "0 Pa"}, point_loads="0.5 m,0.5 m,100 N")
    uniform = ok(plate, **PLATE)
    combined = ok(plate, **PLATE, point_loads="500 mm,500 mm,100 N", unit_system="imperial")
    # Adaptive truncations differ between loads; compare within discretization error.
    close(v(combined, "centre_deflection", "m"), v(point, "centre_deflection", "m") + v(uniform, "centre_deflection", "m"), rel=3e-5)
    metric = ok(plate, **PLATE, point_loads="500 mm,500 mm,100 N")
    close(v(combined, "centre_deflection", "m"), v(metric, "centre_deflection", "m"))
    assert "alpha" not in combined.values and "beta_x" not in combined.values


def test_central_point_load_deflection_coefficient():
    result = ok(plate, **{**PLATE, "pressure": "0 Pa"}, point_loads="0.5 m,0.5 m,100 N", terms=121)
    # At the central load, both sine factors square to one for odd indices.
    # alpha=4/pi^4*sum_odd[1/(m^2+n^2)^2], independently summed to index 301.
    # Truncating production at 121 converges from below; allow 5e-5 relative error.
    alpha = 4 / math.pi**4 * math.fsum(1 / (m*m + n*n)**2 for m in range(1, 302, 2) for n in range(1, 302, 2))
    close(alpha, 0.0116008, rel=1e-5)
    close(v(result, "centre_deflection", "m"), alpha * 100 / D, rel=5e-5)
    close(v(result, "alpha"), alpha, rel=5e-5)
    assert "beta_x" not in result.values and "beta_y" not in result.values


def test_near_coincident_point_sources_remain_reportable():
    result = ok(plate, **PLATE, point_loads="0.3 m,0.4 m,10 N;0.3000000000001 m,0.4 m,20 N")
    assert len(result.data["point_load_deflections"]) == 2
    sources = [row for row in table(result) if abs(float(row["x_m"]) - 0.3) < 1e-12 and float(row["y_m"]) == 0.4]
    assert len(sources) == 2
    assert all(row["Mx_N"] == "" for row in sources)


def test_large_deflection_and_nonconvergence_are_explicit():
    large = ok(plate, **{**PLATE, "pressure": "1 MPa"})
    assert not large.data["stress_available"]
    assert "maximum_sampled_von_mises_stress" not in large.values
    assert any("t/2" in warning for warning in large.warnings)
    coarse = ok(plate, **PLATE, terms=5, convergence_tolerance=1e-5)
    assert coarse.data["convergence"]["passed"]
    assert coarse.data["convergence"]["terms"] > 5


@pytest.mark.parametrize("changes", [
    {"thickness": "0.1 m"}, {"length": "1"}, {"terms": True}, {"terms": 122},
    {"grid_points": 20}, {"grid_points": 53}, {"poisson_ratio": 0.5},
    {"edges": "SCSC"}, {"method": "levy", "point_loads": "0.5 m,0.5 m,1 N"},
    {"point_loads": "1 m,0.5 m,1 N"}, {"point_loads": "0.5 m,0.5 m,1 N;"},
    {"point_loads": "0.5 m,0.5 m,1 N,extra"}, {"pressure": "nan Pa"},
])
def test_plate_rejects_invalid_boundaries(changes):
    assert not call(plate, **{**PLATE, **changes}).ok
