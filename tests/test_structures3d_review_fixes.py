"""Review reproductions, with references derived independently of the solvers."""

from decimal import Decimal, localcontext
from fractions import Fraction
import math

import numpy as np
import pytest

from eng_toolkit.domains.structures3d._model import compile_frame, kinematic_space, parse_frame, solve_static
from eng_toolkit.domains.structures3d.buckling import _element_geometric, buckling
from eng_toolkit.domains.structures3d.foundation import foundation
from eng_toolkit.domains.structures3d.modal import assemble_mass, modal
from eng_toolkit.domains.structures3d.plate import plate
from eng_toolkit.domains.structures3d.plate_stability import plate_stability
from eng_toolkit.domains.structures3d.static import static
from tests.structures3d_testing import FRAME, call, ok, quantity, table, v


S = "200 GPa,80 GPa,1000 mm^2,1e6 mm^4,2e6 mm^4,1e6 mm^4"
SHORT = dict(nodes="a,0 m,0 m,0 m;b,1 m,0 m,0 m;c,1.00001 m,0 m,0 m",
             members=f"e,a,b,{S};f,b,c,{S}", supports="a,fixed")
P = dict(length="1 m", width="1 m", thickness="10 mm", youngs_modulus="200 GPa")
PI = Decimal("3.141592653589793238462643383279502884197169399375105820974944592307816406286")


def _determinant(matrix):
    a = [row.copy() for row in matrix]
    value = Decimal(1)
    for j in range(len(a)):
        pivot = max(range(j, len(a)), key=lambda i: abs(a[i][j]))
        if not a[pivot][j]:
            return Decimal(0)
        if pivot != j:
            a[j], a[pivot] = a[pivot], a[j]
            value = -value
        value *= a[j][j]
        for i in range(j+1, len(a)):
            ratio = a[i][j]/a[j][j]
            for k in range(j+1, len(a)):
                a[i][k] -= ratio*a[j][k]
    return value


def _bisect(function, low, high, iterations=180):
    f_low = function(low)
    assert f_low*function(high) < 0
    for _ in range(iterations):
        mid = (low+high)/2
        value = function(mid)
        if value*f_low <= 0:
            high = mid
        else:
            low, f_low = mid, value
    return (low+high)/2


def _short_column_decimal_factor():
    """Exact polynomial integration in Fractions, then a 70-digit determinant."""
    k = [[Fraction(0) for _ in range(6)] for _ in range(6)]
    g = [[Fraction(0) for _ in range(6)] for _ in range(6)]
    for element, length in enumerate((Fraction(1), Fraction(1, 100000))):
        # Hermite polynomials in t=x/L; physical derivatives divide by L.
        polynomials = [[0, 0, -3, 2], [0, length, -2*length, length],
                       [0, 0, 3, -2], [0, 0, -length, length]]
        first = [[Fraction(i)*p[i]/length for i in range(1, 4)] for p in polynomials]
        second = [[Fraction(i)*p[i]/length for i in range(1, 3)] for p in first]
        def integral(a, b):
            return length*sum(x*y/Fraction(i+j+1) for i, x in enumerate(a) for j, y in enumerate(b))
        for i in range(4):
            for j in range(4):
                k[2*element+i][2*element+j] += 200000*integral(second[i], second[j])
                g[2*element+i][2*element+j] += 1000*integral(first[i], first[j])
    with localcontext() as ctx:
        ctx.prec = 70
        def convert(matrix):
            return [[Decimal(x.numerator)/Decimal(x.denominator) for x in row[2:]] for row in matrix[2:]]
        kd, gd = convert(k), convert(g)
        return float(_bisect(lambda load: _determinant([[kd[i][j]-load*gd[i][j] for j in range(4)] for i in range(4)]),
                             Decimal(450), Decimal(550)))


def test_short_member_static_and_buckling_are_refused_before_losing_digits():
    with localcontext() as ctx:
        ctx.prec = 70
        exact_tip = Decimal("1.00001")**3/(3*Decimal("200e9")*Decimal("2e-6"))
    assert float(exact_tip) == pytest.approx(8.333583335833342e-7, rel=1e-14)
    assert _short_column_decimal_factor() == pytest.approx(497.1822751056, rel=2e-12)
    cases = [(static, dict(loads="c,0 N,1 N,0 N,0 N*m,0 N*m,0 N*m")),
             (buckling, dict(loads="c,-1000 N,0 N,0 N,0 N*m,0 N*m,0 N*m", elements_per_member=1, modes_count=1)),
             (modal, dict(density="7800 kg/m^3", elements_per_member=1))]
    for tool, extra in cases:
        result = call(tool, **SHORT, **extra)
        assert not result.ok
        assert result.error.family == "input.out_of_range"
        assert result.error.details["param"] == "/members"
        assert "shortest member 'f'" in result.error.message
        assert "condition number" in result.error.message


@pytest.mark.parametrize("tool,extra", [
    (static, dict(loads="b,0 N,1 N,0 N,0 N*m,0 N*m,0 N*m")),
    (modal, dict(density="7800 kg/m^3")),
    (buckling, dict(loads="b,-1000 N,0 N,0 N,0 N*m,0 N*m,0 N*m")),
])
def test_accepted_frames_report_condition_and_error_estimate(tool, extra):
    result = ok(tool, **FRAME, **extra)
    check = result.data["conditioning"]
    assert 1 <= check["estimated_condition_number"] < 1e-8/np.finfo(float).eps
    assert check["relative_error_bound"] == pytest.approx(check["estimated_condition_number"]*np.finfo(float).eps)
    if tool is static:
        assert quantity(result.data["nodes"][1]["uy"]) == pytest.approx(2**3/(3*200e9*2e-6), rel=1e-12)
        assert result.data["stability"]["refinement_steps"] == 2


def _sssf_decimal(aspect, nu, half_waves=1):
    """Independent Taylor fundamental solutions of the fourth-order plate ODE."""
    with localcontext() as ctx:
        ctx.prec = 70
        nu = Decimal(str(nu))
        alpha2 = (half_waves*PI/Decimal(str(aspect)))**2
        def determinant(p):
            columns = []
            for slope, third in ((1, 0), (0, 1)):
                coefficients = [Decimal(0), Decimal(slope), Decimal(0), Decimal(third)/6]
                for n in range(196):
                    coefficients.append((2*alpha2*(n+2)*(n+1)*coefficients[n+2]
                                         +(p*alpha2-alpha2**2)*coefficients[n])/((n+4)*(n+3)*(n+2)*(n+1)))
                derivatives = [sum(c*math.prod(range(i-j+1, i+1)) for i, c in enumerate(coefficients) if i >= j)
                               for j in range(4)]
                w, dw, ddw, dddw = derivatives
                columns.append((ddw-nu*alpha2*w, dddw-(2-nu)*alpha2*dw))
            return columns[0][0]*columns[1][1]-columns[1][0]*columns[0][1]
        low = (1-nu**2)*alpha2+(1-nu)*PI**2/2
        high = alpha2+6*(1-nu)
        return float(_bisect(determinant, low, high, 120)/PI**2)


def test_sssf_exact_review_input_and_poisson_dependent_long_limit():
    result = ok(plate_stability, **{**P, "length": "100 m"}, edges="SSSF", poisson_ratio=.499)
    k = _sssf_decimal(100, .499)
    stress = k*math.pi**2*(200e9*.01**3/(12*(1-.499**2)))/.01
    assert stress/1e6 == pytest.approx(6.673071417, rel=1e-10)
    assert v(result, "critical_stress", "Pa") == pytest.approx(stress, rel=2e-10)
    for nu in (.3, .499):
        long_plate = ok(plate_stability, **{**P, "length": "100 m"}, edges="SSSF", poisson_ratio=nu)
        limit = 6*(1-nu)/math.pi**2
        assert v(long_plate, "buckling_coefficient") == pytest.approx(limit, rel=4e-4)
        assert long_plate.data["coefficient_method"]["long_wavelength_limit"] == pytest.approx(limit, rel=1e-14)
    # 0.3407 in the review is inconsistent with its own formula; the limit is 0.30457.
    assert 6*(1-.499)/math.pi**2 == pytest.approx(.304571478028867, rel=1e-13)


@pytest.mark.parametrize("aspect,nu", [(.05, .499), (.2, -.5), (1, .3), (10, .499), (10, -.999)])
def test_sssf_finite_aspects_and_auxetic_half_wave_search(aspect, nu):
    result = ok(plate_stability, **{**P, "length": f"{aspect} m", "thickness": "1 mm"}, edges="SSSF", poisson_ratio=nu)
    m = result.data["critical_mode"]["m"]
    assert v(result, "buckling_coefficient") == pytest.approx(_sssf_decimal(aspect, nu, m), rel=2e-9)
    if nu == -.999:
        assert m == 5
        assert v(result, "buckling_coefficient") < _sssf_decimal(aspect, nu, 1)


@pytest.mark.parametrize("edges,analysis", [("SSSC", "uniaxial"), ("SCSC", "uniaxial"), ("SSSS", "shear"), ("SSSS", "bending")])
def test_tabulated_coefficients_disclose_precision_and_source(edges, analysis):
    results = [ok(plate_stability, **{**P, "length": "10 m"}, edges=edges, analysis=analysis, poisson_ratio=nu)
               for nu in (-.5, .499)]
    assert v(results[0], "buckling_coefficient") == v(results[1], "buckling_coefficient")
    assert "tabulated" in results[0].data["coefficient_method"]["type"]
    assert results[0].data["coefficient_method"]["source"].startswith("https://")


def test_axial_stress_torsional_buckling_review_input_and_flexural_limit():
    member = "e,a,b,200 GPa,80 GPa,2e-4 m^2,8.333333333333e-8 m^4,8.333333333333e-8 m^4,6.666666666667e-11 m^4"
    args = dict(nodes=FRAME["nodes"], members=member, supports="a,ux+uy+uz+rx;b,uy+uz",
                loads="b,-1000 N,0 N,0 N,0 N*m,0 N*m,0 N*m", elements_per_member=10, modes_count=1)
    with localcontext() as ctx:
        ctx.prec = 70
        factor = Decimal("80e9")*Decimal("6.666666666667e-11")*Decimal("2e-4")/(1000*2*Decimal("8.333333333333e-8"))
    assert float(factor) == pytest.approx(6.4, rel=1e-12)
    result = ok(buckling, **args)
    assert v(result, "first_load_factor") == pytest.approx(float(factor), rel=2e-10)
    flexural = ok(buckling, **{**args, "members": FRAME["members"]})
    assert v(flexural, "first_load_factor") == pytest.approx(math.pi**2*200e9*1e-6/(1000*2**2), rel=2e-5)
    assert "zero warping" in result.data["assumptions"]


@pytest.mark.parametrize("member_loads,average_force", [
    ("e,uniform,local,-1000 N/m,0 N/m,0 N/m", -1000),
    ("e,point,local,0.5 m,-1000 N,0 N,0 N", -250),
])
def test_torsion_geometric_term_integrates_nonconstant_axial_force(member_loads, average_force):
    mesh = compile_frame(parse_frame(**FRAME, member_loads=member_loads))
    displacement, _, _ = solve_static(mesh)
    geometric, _, _ = _element_geometric(mesh.elements[0], displacement)
    # Linear twist: integral N(x) dx * (Ip/A)/L^2 = mean(N)*Ip/(A L).
    expected = average_force*(3e-6/1e-3)/2*np.array([[1., -1.], [-1., 1.]])
    np.testing.assert_allclose(geometric[np.ix_([3, 9], [3, 9])], expected, rtol=1e-13, atol=1e-13)


@pytest.mark.parametrize("supports,releases,zeros", [
    ("a,fixed", "e,start,rx+ry+rz", 3),
    ("a,pinned", "", 3),
    ("a,fixed", "e,start,rx", 1),
    ("a,fixed", "e,start,ry+rz", 2),
    ("a,fixed", "e,end,rx+ry+rz", 0),
    ("a,fixed;b,uy+uz", "e,start,rx;e,end,rx", 1),
])
def test_released_rigid_clusters_match_scipy_stiffness_nullity(supports, releases, zeros):
    scipy = pytest.importorskip("scipy.linalg")
    args = {**FRAME, "supports": supports, "releases": releases}
    result = ok(modal, **args, density="7800 kg/m^3", elements_per_member=1)
    mesh = compile_frame(parse_frame(**args))
    free, _, _ = kinematic_space(mesh)
    k = mesh.k[np.ix_(free, free)]
    scale = 1/np.sqrt(np.diag(k))
    independently_counted = scipy.null_space(scale[:, None]*k*scale[None, :], rcond=1e-11).shape[1]
    assert result.data["zero_mode_count"] == independently_counted == zeros
    assert all(quantity(mode["frequency"]) == 0 for mode in result.data["modes"][:zeros])


def test_two_members_can_rotate_as_separate_released_clusters():
    scipy = pytest.importorskip("scipy.linalg")
    args = dict(nodes="a,0 m,0 m,0 m;b,2 m,0 m,0 m;c,2 m,2 m,0 m",
                members=f"e,a,b,{S};f,b,c,{S}", supports="a,fixed", releases="e,end,rx+ry+rz;f,start,rx+ry+rz")
    result = ok(modal, **args, density="7800 kg/m^3", elements_per_member=1)
    mesh = compile_frame(parse_frame(**args))
    free, _, _ = kinematic_space(mesh)
    k = mesh.k[np.ix_(free, free)]
    scale = 1/np.sqrt(np.diag(k))
    assert result.data["zero_mode_count"] == scipy.null_space(k*np.outer(scale, scale), rcond=1e-11).shape[1] == 3


def test_modal_effective_mass_keeps_restrained_consistent_mass_coupling():
    result = ok(modal, **{**FRAME, "supports": "a,fixed;b,uy+uz+rx+ry+rz"},
                mass_per_length="7.8 kg/m", elements_per_member=1, modes_count=1)
    # phi=(0,1), r=(1,1), M=(mu L/6)*[[2,1],[1,2]]. Scale cancels.
    expected = Fraction(3)**2/(Fraction(2)*Fraction(6))
    assert expected == Fraction(3, 4)
    assert result.data["modes"][0]["effective_mass_fraction_x"] == pytest.approx(float(expected), rel=1e-13)
    assert result.data["cumulative_effective_mass_fractions"]["x"] == pytest.approx(.75, rel=1e-13)
    assert "carried by the support" in result.data["mass_participation"]


def test_cantilever_mass_fractions_approach_one_and_free_frame_carries_all_mass():
    previous = np.zeros(3)
    for subdivisions in (1, 2, 5):
        result = ok(modal, **FRAME, mass_per_length="7.8 kg/m", elements_per_member=subdivisions, modes_count=30)
        mesh = compile_frame(parse_frame(**FRAME), subdivisions)
        mass = assemble_mass(mesh, np.array([7.8]), np.zeros(12))
        free = np.arange(6, len(mass))
        totals = []
        for axis in range(3):
            r = np.zeros(len(mass))
            r[axis::6] = 1
            force = (mass@r)[free]
            expected = force@np.linalg.solve(mass[np.ix_(free, free)], force)/(r@mass@r)
            actual = sum(mode["effective_mass_fraction_"+"xyz"[axis]] for mode in result.data["modes"])
            assert actual == pytest.approx(expected, rel=2e-10)
            totals.append(actual)
        assert np.all(np.array(totals) > previous)
        previous = np.array(totals)
    assert np.all(previous > .94)
    free_frame = ok(modal, **{**FRAME, "supports": ""}, mass_per_length="7.8 kg/m", modes_count=6)
    for axis in "xyz":
        assert free_frame.data["cumulative_effective_mass_fractions"][axis] == pytest.approx(1, rel=1e-10)


def test_plate_review_input_automatically_converges_to_independent_navier_sum():
    result = ok(plate, **P, pressure="1 kPa", terms=5, convergence_tolerance=1e-5)
    odd = range(1, 1602, 2)
    d = 200e9*.01**3/(12*(1-.3**2))
    w = 16000/(d*math.pi**6)*math.fsum((-1)**((m+n-2)//2)/(m*n*(m*m+n*n)**2) for m in odd for n in odd)
    mx = 16000/math.pi**4*math.fsum((-1)**((m+n-2)//2)*(m*m+.3*n*n)/(m*n*(m*m+n*n)**2) for m in odd for n in odd)
    assert w == pytest.approx(.000221804455273, rel=2e-11)
    assert mx == pytest.approx(47.8863800174, rel=1e-8)
    assert v(result, "centre_deflection", "m") == pytest.approx(w, rel=1e-8)
    centre = next(row for row in table(result) if float(row["x_m"]) == float(row["y_m"]) == .5)
    assert float(centre["Mx_N"]) == pytest.approx(mx, rel=1e-6)
    convergence = result.data["convergence"]
    assert convergence["passed"]
    assert 5 < convergence["terms"] <= convergence["maximum_terms"]
    assert convergence["relative_moment_change_away_from_sources"] <= 1e-5


def test_unresolved_near_source_plate_moments_are_refused_at_series_cap():
    result = call(plate, **P, point_loads="0.500001 m,0.5 m,100 N", terms=121,
                  grid_points=5, convergence_tolerance=1e-5)
    assert not result.ok
    assert result.error.family == "input.out_of_range"
    assert result.error.details["param"] == "/terms"


def test_foundation_review_input_refines_from_no_free_coordinates():
    result = ok(foundation, length="10 m", flexural_rigidity="2e7 N*m^2", foundation_stiffness="1e6 N/m^2",
                left_end="fixed", right_end="fixed", uniform_load="10 kN/m", elements=1)
    # Symmetry about midspan: w=q/k+A cosh(beta*x)cos(beta*x)+B sinh(beta*x)sin(beta*x).
    # At x=L/2 solve w=w'=0; at the centre w=q/k+A.
    beta = (1e6/(4*2e7))**.25
    z = 5*beta
    c, s = math.cosh(z)*math.cos(z), math.sinh(z)*math.sin(z)
    dc = beta*(math.sinh(z)*math.cos(z)-math.cosh(z)*math.sin(z))
    ds = beta*(math.cosh(z)*math.sin(z)+math.sinh(z)*math.cos(z))
    centre = .01-.01*ds/(c*ds-s*dc)
    assert centre == pytest.approx(.00644002119, rel=1e-9)
    assert v(result, "maximum_abs_deflection", "m") == pytest.approx(centre, rel=2e-5)
    assert result.data["convergence"]["converged"]
    assert result.data["elements"] > 1


def test_foundation_refuses_nonconvergence_at_mesh_cap():
    result = call(foundation, length="100 m", flexural_rigidity="1 N*m^2", foundation_stiffness="4 N/m^2",
                  left_end="fixed", right_end="fixed", uniform_load="1 N/m", elements=200, convergence_tolerance=1e-6)
    assert not result.ok
    assert result.error.family == "input.out_of_range"
    assert result.error.details["param"] == "/elements"
