"""Scalar bracket searches and damped square-system root solving."""

from typing import Annotated
import numpy as np
from pydantic import Field

from ._common import GRAMMAR, Budget, Seed, TimeLimit, Tolerance, array, bounds_array, choice, count, invalid, named, number, records, result, run
from ._expr import Expression, TrialError
from ._numerics import jacobian
from ._server import server


def _equilibrate(matrix):
    row = np.max(np.abs(matrix),axis=1)
    if np.any(row==0):
        raise TrialError("singular root Jacobian: zero equation derivative")
    normalized = matrix/row[:,None]
    column = np.max(np.abs(normalized),axis=0)
    if np.any(column==0):
        raise TrialError("singular root Jacobian: zero variable derivative")
    scaled = normalized/column
    singular = np.linalg.svd(scaled,compute_uv=False)
    if singular[-1]<=1e-10*singular[0]:
        raise TrialError("singular or ill-conditioned equilibrated root Jacobian")
    return scaled,row,column,float(singular[0]/singular[-1])


def _residual_scale(matrix,x,floor):
    """|J| @ (|x| + floor): a residual below tolerance*scale means a Newton correction
    below tolerance*|x| + tolerance*floor, i.e. relative accuracy with an absolute floor
    atol = tolerance*floor (floor = 1e-15*width/tolerance, so atol = 1e-15*width)."""
    return np.maximum(1e-100,np.abs(matrix)@(np.abs(x)+floor))


def _system(function, x, lo, hi, tolerance, iterations, budget, initial_scale=None, floor=None):
    x = np.clip(x, lo, hi)
    floor = np.ones(x.size) if floor is None else floor
    residual = function(x)
    original_scale = np.maximum(1.,np.abs(residual)) if initial_scale is None else initial_scale
    for iteration in range(iterations):
        budget.check()
        matrix = jacobian(function, x, lo, hi)
        # Test convergence before factorizing: an exact (possibly multiple) root at the
        # current point is a root even where the Jacobian is singular.
        scale = np.minimum(_residual_scale(matrix,x,floor),original_scale)
        if np.max(np.abs(residual)/scale) <= tolerance:
            return x, residual, iteration
        scaled,row,column,_ = _equilibrate(matrix)
        rhs = residual/row
        direction = np.linalg.solve(scaled, -rhs)/column
        accepted = False
        for damping in (0., 1e-6, 1e-3, 1., 100.):
            if damping:
                direction = np.linalg.solve(scaled.T@scaled+damping*np.eye(x.size), -scaled.T@rhs)/column
            for power in range(24):
                candidate = np.clip(x+direction*2.**(-power), lo, hi)
                try:
                    trial = function(candidate)
                except TrialError:
                    continue
                if np.linalg.norm(trial/row) < np.linalg.norm(rhs):
                    x, residual, accepted = candidate, trial, True
                    break
            if accepted:
                break
        if not accepted:
            raise TrialError("root line search stalled")
    raise TrialError("root iteration limit reached")


def _bisect(function, a, b, tolerance, iterations, budget, initial_scale=None, floor=None):
    floor = np.ones(1) if floor is None else floor
    fa, fb = function(np.array([a]))[0], function(np.array([b]))[0]
    if fa == 0:
        return np.array([a]), np.array([fa]), 0
    if fb == 0:
        return np.array([b]), np.array([fb]), 0
    if np.signbit(fa) == np.signbit(fb):
        raise TrialError("bracket does not change sign")
    # A shrinking interval alone cannot certify a root at a discontinuity.
    for iteration in range(iterations):
        budget.check()
        mid = a+(b-a)/2
        fm = function(np.array([mid]))[0]
        scale = max(1e-100,abs((fb-fa)/(b-a))*(abs(mid)+float(floor[0])))
        if initial_scale is not None:
            scale = min(scale,float(initial_scale[0]))
        local_matrix = jacobian(function,np.array([mid]))
        scale = min(scale,float(_residual_scale(local_matrix,np.array([mid]),floor)[0]))
        if abs(fm) <= tolerance*scale:
            return np.array([mid]), np.array([fm]), iteration+1
        if mid == a or mid == b:
            break
        if np.signbit(fm) == np.signbit(fa):
            a, fa = mid, fm
        else:
            b, fb = mid, fm
    raise TrialError("bracket did not produce a certified residual")


def _secant(function, point, lo, hi, tolerance, iterations, budget, initial_scale=None, floor=None):
    floor = np.ones(1) if floor is None else floor
    a = float(point[0])
    b = min(hi[0],a+1e-3*max(1.,abs(a)))
    if b == a:
        b = max(lo[0],a-1e-3*max(1.,abs(a)))
    fa,fb = function([a])[0],function([b])[0]
    for iteration in range(iterations):
        budget.check()
        scale = max(1e-100,abs((fb-fa)/(b-a))*(abs(b)+float(floor[0]))) if b!=a else 1e-100
        if initial_scale is not None:
            scale = min(scale,float(initial_scale[0]))
        local_matrix = jacobian(function,np.array([b]),lo,hi)
        scale = min(scale,float(_residual_scale(local_matrix,np.array([b]),floor)[0]))
        if abs(fb)<=tolerance*scale:
            return np.array([b]),np.array([fb]),iteration
        if fa == fb or a == b:
            raise TrialError("secant has zero slope")
        direction = -fb*((b-a)/(fb-fa))
        accepted = False
        for power in range(24):
            candidate = float(np.clip(b+direction*2.**(-power),lo[0],hi[0]))
            try:
                fc = function([candidate])[0]
            except TrialError:
                continue
            if abs(fc)<abs(fb):
                a,fa,b,fb = b,fb,candidate,fc
                accepted = True
                break
        if not accepted:
            raise TrialError("secant line search stalled")
    raise TrialError("secant iteration limit reached")


@server.tool(description="Solve scalar roots by bisection, Newton or secant and square systems by damped Newton/LM. Up to 12 variables and 64 starts; residual certificates required. "+GRAMMAR)
def root(
    expression: Annotated[str, Field(description="Residual expression, or semicolon-separated square-system residuals, e.g. 'x^2-2'.")] = "",
    initial: Annotated[str, Field(description="Initial variable records, e.g. 'x=1' or 'x=1;y=2'; at most 12 names.")] = "",
    bracket: Annotated[str, Field(description="Optional scalar JSON interval [lower,upper], e.g. '[0,2]'.")] = "",
    bounds: Annotated[str, Field(description="Variable JSON bound pairs in initial order, e.g. '[[0,2]]'; null is unbounded.")] = "",
    method: Annotated[str, Field(description="Root algorithm: auto, bisection, newton or secant, e.g. 'auto'; systems use damped Newton.")] = "auto",
    scan: Annotated[int, Field(description="Scalar sign-change scan intervals, e.g. 100; 0 disables, 2..2000 needs bracket.")] = 0,
    multistart: Annotated[int, Field(description="Seeded initial-point count, e.g. 8; 1..64, finite bounds recommended.")] = 1,
    tolerance: Tolerance = 1e-8,
    max_iterations: Annotated[int, Field(description="Iteration cap per local root solve, e.g. 500; 1..2000.")] = 500,
    seed: Seed = 0,
    time_limit: TimeLimit = "10 s",
):
    inputs = dict(locals())
    def compute(handle):
        budget = Budget(time_limit)
        tol = number(tolerance,"tolerance",1e-12,.01)
        iterations = count(max_iterations,"max_iterations",1,2000)
        starts = count(multistart,"multistart",1,64)
        scan_count = count(scan,"scan",0,2000)
        if scan_count == 1:
            invalid("scan","scan must be zero or at least two")
        rng = np.random.default_rng(count(seed,"seed",0,4294967295))
        selected = choice(method,"method",("auto","bisection","newton","secant"))
        values = named(initial,cap=12)
        names, x = tuple(values), np.array(list(values.values()))
        lo, hi = bounds_array(bounds,names,x)
        expressions = [Expression(s,names,f"expression/{i}") for i,s in enumerate(records(expression,"expression",12))]
        if len(expressions) != len(names):
            invalid("expression","one residual per variable is required")
        def function(point):
            bindings = dict(zip(names,point))
            return np.array([e.evaluate(bindings,budget) for e in expressions])
        initial_scale = np.maximum(1.,np.abs(function(x)))
        solutions, failures = [], []
        def add(answer):
            point, residual, steps = answer
            if not any(np.linalg.norm(point-s[0]) <= 10*tol*max(1.,np.linalg.norm(point)) for s in solutions):
                solutions.append((point,residual,steps))
        interval = None
        if bracket:
            if len(names) != 1:
                invalid("bracket","brackets apply only to scalar equations")
            interval = array(bracket,"bracket")
            if interval.size != 2 or interval[0] >= interval[1]:
                invalid("bracket","expected strictly increasing [lower,upper]")
            if interval[0] < lo[0] or interval[1] > hi[0]:
                invalid("bracket","bracket lies outside bounds")
        if scan_count and interval is None:
            invalid("scan","scan requires a scalar bracket")
        # Problem width per variable: the bracket, else a finite bound interval, else
        # max(1,|x0|). Accuracy target |dx| <= tol*|x| + 1e-15*width.
        width = np.where(np.isfinite(hi-lo) & (np.abs(lo) < 1e99) & (np.abs(hi) < 1e99), hi-lo, np.maximum(1.,np.abs(x)))
        if interval is not None:
            width = np.array([float(interval[1]-interval[0])])
        absolute_floor = 1e-15*width
        floor = absolute_floor/tol
        if len(names)>1 and selected in ("secant","bisection"):
            invalid("method","scalar method cannot solve a system")
        if scan_count:
            grid = np.linspace(*interval,scan_count+1)
            previous = None
            for point in grid:
                budget.check()
                try:
                    value = function([point])[0]
                except TrialError:
                    previous = None
                    continue
                if value == 0:
                    add((np.array([point]),np.array([value]),0))
                if previous is not None and np.signbit(value) != np.signbit(previous[1]):
                    try:
                        add(_bisect(function,previous[0],point,tol,iterations,budget,initial_scale,floor))
                    except TrialError as exc:
                        failures.append(str(exc))
                previous = point,value
        elif len(names)==1 and (interval is not None or selected in ("auto","bisection")):
            if interval is None:
                width = max(1.,abs(x[0])*.1)
                for _ in range(60):
                    a,b = max(lo[0],x[0]-width),min(hi[0],x[0]+width)
                    try:
                        fa,fb = function([a])[0],function([b])[0]
                        if fa == 0 or fb == 0 or np.signbit(fa) != np.signbit(fb):
                            interval = (a,b)
                            break
                    except TrialError:
                        pass
                    width *= 2
            if interval is not None:
                try:
                    add(_bisect(function,*interval,tol,iterations,budget,initial_scale,floor))
                except TrialError as exc:
                    failures.append(str(exc))
            elif selected == "bisection":
                invalid("bracket","automatic search found no sign-changing bracket","optimize.not_converged")
        if not scan_count and not (interval is not None and not solutions) and (not solutions or starts>1):
            for start in range(starts):
                point = x.copy() if start == 0 else np.array([rng.uniform(a,b) if max(abs(a),abs(b)) < 1e99 else v+rng.normal()*max(1.,abs(v)) for a,b,v in zip(lo,hi,x)])
                try:
                    if selected == "secant":
                        add(_secant(function,point,lo,hi,tol,iterations,budget,initial_scale,floor))
                    else:
                        add(_system(function,point,lo,hi,tol,iterations,budget,initial_scale,floor))
                except TrialError as exc:
                    failures.append(str(exc))
        if not solutions:
            invalid("expression","no certified root: "+("; ".join(failures[-3:]) or "no sign change found"),"optimize.not_converged")
        rows = []
        warnings = []
        for p,r,k in solutions:
            matrix = jacobian(function,p,lo,hi)
            scale = _residual_scale(matrix,p,floor)
            scaled_residual = float(np.max(np.abs(r)/scale))
            initial_scaled_residual = float(np.max(np.abs(r)/initial_scale))
            if scaled_residual>tol or initial_scaled_residual>tol:
                continue
            singular = np.linalg.svd(matrix,compute_uv=False)
            raw_condition = float(singular[0])/float(singular[-1]) if singular[-1]>0 else None
            if raw_condition is not None and (not np.isfinite(raw_condition) or raw_condition>1e100):
                raw_condition = None
            try:
                _,_,_,scaled_condition = _equilibrate(matrix)
            except TrialError:
                # Kept only because the residual itself is certified (e.g. an exact root at
                # the start); the root may be multiple or the equations dependent.
                scaled_condition = None
                note = "Jacobian is singular at root "+", ".join(f"{n}={v:.17g}" for n,v in zip(names,p))+": a multiple root or dependent equations; the root may not be isolated."
                if note not in warnings:
                    warnings.append(note)
            # One Newton correction estimates the remaining error of the returned root.
            correction = np.linalg.lstsq(matrix,-r,rcond=1e-12)[0] if np.any(r!=0) else np.zeros(p.size)
            relative_error = float(np.max(np.abs(correction)/np.maximum(np.abs(p),absolute_floor)))
            rows.append({"solution":dict(zip(names,p)),"residual":r,"iterations":k,
                         "certificate":{"scaled_residual_inf":scaled_residual,"tolerance":tol,"residual_scale":scale,
                                        "initial_scaled_residual_inf":initial_scaled_residual,"initial_residual_scale":initial_scale,
                                        "absolute_floor":absolute_floor,"estimated_relative_error":relative_error,
                                        "jacobian_condition_raw":raw_condition,"jacobian_condition_scaled":scaled_condition}})
        if not rows:
            invalid("expression","no root passed final scaled-residual certification","optimize.not_converged")
        return result({"roots":rows,"solution":rows[0]["solution"],"residual":rows[0]["residual"],"evaluations":budget.evaluations,"seed":seed,"method":selected,"converged":True},"root",rows[0]["solution"][names[0]],formulas=["F(x)=0; Richardson finite-difference Newton with damped line search","Residual scale=max(1e-100, abs(J) @ (abs(x)+1e-15*width/tolerance)): a Newton correction below tolerance*abs(x)+1e-15*width","Estimated relative error=max(abs(J^-1 F)/max(abs(x),1e-15*width))"],assumptions=["Square systems; locally independent equations"],validity=["Roots require residual certification; sign-change scans can miss tangent roots","Multistart does not establish completeness","Raw Jacobian condition is unavailable if singular or outside finite output range","width is the bracket, a finite bound interval, or max(1,abs(initial))"],warnings=warnings)
    return run("root",inputs,compute)
