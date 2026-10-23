"""Bounded two-phase simplex and integer branch and bound."""
from __future__ import annotations

import heapq
from fractions import Fraction
from typing import Annotated
import numpy as np
from pydantic import Field
from eng_toolkit.errors import ToolFailure
from ._common import Budget, GRAMMAR, TimeLimit, array, count, invalid, json_value, number, result, run
from ._server import server


def _feasible(x, au, bu, ae, be, bounds):
    """Allow only accumulated floating-point roundoff in original coordinates."""
    factor = 64 * np.finfo(float).eps * max(1, len(x))
    inequality_error = factor * (np.abs(au) @ np.abs(x) + np.abs(bu) + 1)
    equality_error = factor * (np.abs(ae) @ np.abs(x) + np.abs(be) + 1)
    return (np.all(au@x-bu <= inequality_error) and np.all(np.abs(ae@x-be) <= equality_error)
            and all((lo is None or x[i] >= lo-factor*(1+abs(lo)))
                    and (hi is None or x[i] <= hi+factor*(1+abs(hi)))
                    for i,(lo,hi) in enumerate(bounds)))


def _simplex(c, A, b, basis, budget, limit=20000):
    """Canonical tableau; Bland's entering and leaving indices prevent cycling."""
    original, initial_rhs = A.copy(), b.copy()
    rounding = 64*np.finfo(float).eps*max(1,len(basis))
    for _ in range(limit):
        budget.check()
        # Rebuild from the supplied rows so accumulated pivot cancellation cannot
        # turn an exact zero into a spurious limiting row.
        base = original[:,basis]
        try:
            inverse = np.linalg.solve(base,np.eye(len(basis)))
            A = np.linalg.solve(base,original)
            b = np.linalg.solve(base,initial_rhs)
            A += np.linalg.solve(base,original-base@A)
            b += np.linalg.solve(base,initial_rhs-base@b)
        except np.linalg.LinAlgError:
            invalid("inputs", "simplex basis is numerically singular", "optimize.not_converged")
        coefficient_error = np.abs(inverse)@(np.abs(original-base@A)
                             + rounding*(np.abs(original)+np.abs(base)@np.abs(A)))
        rhs_error = np.abs(inverse)@(np.abs(initial_rhs-base@b)
                    + rounding*(np.abs(initial_rhs)+np.abs(base)@np.abs(b)))
        b[(b<0)&(b>=-rhs_error)] = 0.
        if np.any(b < 0):
            invalid("inputs", "simplex lost primal feasibility", "optimize.not_converged")
        A[:,basis] = np.eye(len(basis))
        reduced = c - c[basis] @ A
        error = (rounding*(np.abs(c)+np.abs(c[basis])@np.abs(A))
                 + np.abs(c[basis])@coefficient_error)
        reduced[basis] = 0.
        entering = np.flatnonzero(reduced < -error)
        if not len(entering):
            return A, b, basis, None
        j = int(entering[0])
        # Resolve positivity relative to the column's solve error, not an absolute
        # pivot threshold. A small but accurately resolved entry still limits the ray.
        rows = np.flatnonzero(A[:, j] > coefficient_error[:,j])
        if not len(rows):
            ray = np.zeros(len(c)); ray[j] = 1
            ray[basis] = -A[:, j]
            ray[basis[np.abs(A[:,j])<=coefficient_error[:,j]]] = 0.
            return A, b, basis, ray
        ratios = b[rows] / A[rows, j]
        minimum = np.min(ratios)
        tied = rows[np.abs(ratios - minimum) <= 1e-10 * max(1., abs(minimum))]
        i = int(min(tied, key=lambda k: basis[k]))
        basis[i] = j
    invalid("inputs", "simplex pivot limit reached", "optimize.not_converged")


def _independent_rows(matrix, budget):
    """Select original equations supporting the retained basis after redundant rows drop."""
    work = matrix.copy()
    scales = np.max(np.abs(work),axis=0)
    if np.any(scales == 0):
        invalid("inputs", "unresolved basis after redundant-row removal", "optimize.not_converged")
    work /= scales
    rows = np.arange(len(work))
    for j in range(work.shape[1]):
        budget.check()
        pivot = j+int(np.argmax(np.abs(work[j:,j])))
        if abs(work[pivot,j]) <= 64*np.finfo(float).eps*max(1,len(work)):
            invalid("inputs", "unresolved independent constraint rows", "optimize.not_converged")
        work[[j,pivot]],rows[[j,pivot]] = work[[pivot,j]],rows[[pivot,j]]
        work[j] /= work[j,j]
        work[j+1:] -= work[j+1:,j,None]*work[j]
    return rows[:work.shape[1]].tolist()


def _exact_ray(standard, rows, basis, ray, transform, c, au, ae, bounds, budget):
    """Reconstruct and verify a recession direction with exact rational arithmetic."""
    entering = next(j for j in range(len(ray)) if j not in basis and ray[j] != 0)
    system = [[Fraction(float(standard[i,j])) for j in basis]
              + [-Fraction(float(standard[i,entering]))] for i in rows]
    for j in range(len(basis)):
        budget.check()
        pivot = max(range(j,len(basis)),key=lambda i: abs(system[i][j]))
        if not system[pivot][j]:
            invalid("inputs", "unbounded ray basis is singular", "optimize.not_converged")
        system[j],system[pivot] = system[pivot],system[j]
        divisor = system[j][j]
        system[j] = [v/divisor for v in system[j]]
        for i in range(len(basis)):
            budget.check()
            if i != j and system[i][j]:
                factor = system[i][j]
                system[i] = [v-factor*w for v,w in zip(system[i],system[j])]
    exact = [Fraction(0) for _ in ray]
    exact[entering] = Fraction(1)
    for j,row in zip(basis,system):
        exact[j] = row[-1]
    direction = [sum((Fraction(float(v))*exact[j] for j,v in enumerate(row) if v),Fraction(0))
                 for row in transform]
    def dot(row):
        return sum((Fraction(float(v))*d for v,d in zip(row,direction)),Fraction(0))
    slope = dot(c)
    checked = (slope < 0 and all(dot(row) <= 0 for row in au) and all(dot(row) == 0 for row in ae)
               and all((lo is None or d >= 0) and (hi is None or d <= 0)
                       for d,(lo,hi) in zip(direction,bounds)))
    if not checked:
        invalid("inputs", "unbounded ray failed exact verification", "optimize.not_converged")
    budget.check()
    return {"ray": [float(d) for d in direction], "ray_exact": [str(d) for d in direction],
            "objective_slope": float(slope)}


def solve_lp(c, au, bu, ae, be, bounds, budget):
    """Solve a continuous minimization; return finite proof/diagnostic data."""
    n = len(c); offset = np.zeros(n); columns = []; upper = []
    for i, (lo, hi) in enumerate(bounds):
        if lo is not None and hi is not None and lo > hi:
            return {"status": "infeasible", "proof": {"conflicting_bound": i}}
        if lo is not None and hi == lo:
            offset[i] = lo; continue
        col = np.zeros(n)
        if lo is not None:
            offset[i] = lo; col[i] = 1
            if hi is not None: upper.append((len(columns), hi-lo))
        elif hi is not None:
            offset[i] = hi; col[i] = -1
        else:
            col[i] = 1; columns.append(col.copy()); col[i] = -1
        columns.append(col)
    T = np.array(columns).T if columns else np.zeros((n, 0))
    k = T.shape[1]
    U = au @ T; ub = bu-au@offset
    if upper:
        extra = np.zeros((len(upper), k))
        for i, (j, _) in enumerate(upper): extra[i, j] = 1
        U = np.vstack((U, extra)); ub = np.r_[ub, [v for _, v in upper]]
    E = ae@T; eb = be-ae@offset
    m = len(ub)+len(eb)
    original = np.zeros((m, k+len(ub)))
    original[:, :k] = np.vstack((U, E))
    original[:len(ub), k:] = np.eye(len(ub))
    rhs = np.r_[ub, eb]
    signs = np.where(rhs < 0, -1., 1.)
    original *= signs[:, None]; rhs *= signs
    unscaled_original = original.copy()
    scale = np.maximum(np.max(np.abs(original), axis=1, initial=0), 1)
    original /= scale[:, None]; rhs /= scale
    width = original.shape[1]
    costs = np.r_[c@T, np.zeros(len(ub))]
    augmented = np.column_stack((original, np.eye(m)))
    tab = augmented.copy()
    initial_rhs = rhs.copy()
    basis = np.arange(width, width+m)
    phase = np.r_[np.zeros(width), np.ones(m)]
    tab, rhs, basis, _ = _simplex(phase, tab, rhs, basis, budget)
    # Iterative refinement: recompute the basic values from the original rows instead
    # of trusting accumulated tableau updates, then correct once with the residual.
    base = augmented[:, basis]
    feasibility_tolerance = 0.
    if m:
        try:
            refined = np.linalg.solve(base, initial_rhs)
            refined += np.linalg.solve(base, initial_rhs - base @ refined)
            if (np.all(np.isfinite(refined)) and np.linalg.norm(base @ refined - initial_rhs, ord=np.inf)
                    <= np.linalg.norm(base @ rhs - initial_rhs, ord=np.inf)):
                rhs = refined
        except np.linalg.LinAlgError:
            pass
        # Forward-error bound of the refined basic solve: residuals below it are roundoff
        # of an exactly feasible basis; anything larger needs a proof or is refused.
        with np.errstate(all="ignore"):
            try:
                condition = float(np.linalg.cond(base))
            except np.linalg.LinAlgError:
                condition = float("inf")
            magnitude = max(float(np.max(np.abs(initial_rhs), initial=0)), float(np.max(np.abs(base) @ np.abs(rhs), initial=0)))
            feasibility_tolerance = 64 * m * np.finfo(float).eps * condition * magnitude
        if not np.isfinite(feasibility_tolerance):
            feasibility_tolerance = 0.
        feasibility_tolerance = min(feasibility_tolerance, 1e-9 * m * max(1., float(np.max(np.abs(initial_rhs), initial=0))))
    residual = float(phase[basis]@rhs)
    if residual > 0:
        # A verified Farkas certificate proves infeasibility even for tiny residuals.
        try:
            certificate=np.linalg.solve(base.T,phase[basis])
        except np.linalg.LinAlgError:
            certificate=None
        if certificate is not None and np.all(np.isfinite(certificate)):
            margin=float(certificate@initial_rhs)
            rounding = 64*np.finfo(float).eps*max(1,m)*max(1.,float(np.sum(np.abs(certificate))))
            separation_error = rounding * max(1.,float(np.max(np.abs(initial_rhs),initial=0)))
            proved = margin > separation_error and np.max(certificate@original,initial=0) <= rounding
        else:
            margin, separation_error, proved = None, None, False
        if proved:
            budget.check()
            return {"status": "infeasible", "proof": {"phase1_residual": residual,
                    "standard_form_multipliers":certificate.tolist(),"certificate_margin":margin,
                    "coordinates":"normalized nonnegative standard form"}}
        if residual > feasibility_tolerance:
            invalid("inputs","phase I residual is numerically ambiguous; rescale constraints",
                    "optimize.not_converged",phase1_residual=residual,certificate_margin=margin,
                    separation_error=separation_error,feasibility_tolerance=feasibility_tolerance)
    # Basic artificials are zero up to roundoff; the original-coordinate check below
    # still verifies the final point.
    rhs[basis >= width] = 0.
    rows = list(range(m))
    i = 0
    while i < len(basis):
        budget.check()
        if basis[i] >= width:
            candidates = [j for j in range(width) if j not in basis and abs(tab[i,j]) > 1e-9]
            if not candidates:
                tab = np.delete(tab, i, axis=0); rhs = np.delete(rhs, i)
                basis = np.delete(basis, i); rows.pop(i); continue
            j = candidates[0]; tab[i] /= tab[i,j]
            # Artificial basic values are zero after phase I.
            rhs[i] = 0
            factors = tab[:,j].copy(); factors[i] = 0
            tab -= factors[:,None]*tab[i]; basis[i] = j
        i += 1
    tab = tab[:, :width]
    if len(rows) < m:
        rows = _independent_rows(original[:,basis],budget)
    tab, rhs, basis, ray = _simplex(costs, original[rows], initial_rhs[rows], basis, budget)
    z = np.zeros(width); z[basis] = rhs
    x = offset+T@z[:k]
    if not _feasible(x,au,bu,ae,be,bounds):
        invalid("inputs", "candidate failed original-coordinate feasibility check", "optimize.not_converged")
    if ray is not None:
        proof = _exact_ray(unscaled_original,rows,basis,ray,T,c,au,ae,bounds,budget)
        return {"status":"unbounded", "proof":{"point":x.tolist(), **proof}}
    dual = np.zeros(m)
    ranges = None
    if len(rows):
        base = original[rows][:,basis]
        multipliers = np.linalg.solve(base.T, costs[basis])
        multipliers += np.linalg.solve(base.T,costs[basis]-base.T@multipliers)
        residual_cost = costs - multipliers @ original[rows]
        rounding = 64*np.finfo(float).eps*max(1,len(rows))
        solve_error = (np.abs(costs[basis]-base.T@multipliers)
                       + rounding*(np.abs(costs[basis])+np.abs(base.T)@np.abs(multipliers)))
        dual_error = np.abs(np.linalg.solve(base.T,np.eye(len(rows))))@solve_error
        cost_error = (rounding*(np.abs(costs)+np.abs(multipliers)@np.abs(original[rows]))
                      + dual_error@np.abs(original[rows]))
        if np.any(residual_cost < -cost_error):
            invalid("inputs", "optimal basis failed dual feasibility check", "optimize.not_converged",
                    reduced_costs=residual_cost, rounding_bounds=cost_error)
        dual[rows] = multipliers * signs[rows]/scale[rows]
        if len(rows) == m:
            inverse = np.linalg.solve(base, np.eye(m))
            ranges=[]
            for j in list(range(len(bu)))+list(range(len(ub),m)):
                direction=inverse[:,j]*signs[j]/scale[j]
                positive=direction>1e-10; negative=direction < -1e-10
                lower=float(np.max(-rhs[positive]/direction[positive])) if np.any(positive) else None
                upper=float(np.min(-rhs[negative]/direction[negative])) if np.any(negative) else None
                ranges.append({"row_type":"inequality" if j<len(bu) else "equality",
                               "row":j if j<len(bu) else j-len(ub),"delta_lower":lower,"delta_upper":upper})
    du, de = dual[:len(bu)], dual[len(ub):]
    reduced = c-au.T@du-ae.T@de
    budget.check()
    return {"status":"optimal", "x":x.tolist(), "objective":float(c@x), "duals_ub":du.tolist(),
            "duals_eq":de.tolist(), "shadow_prices": {"inequalities":du.tolist(), "equalities":de.tolist()},
            "reduced_costs":reduced.tolist(), "slacks":(bu-au@x).tolist(),
            "equality_residuals":(ae@x-be).tolist(), "rhs_ranges":ranges}


def _integer(c, au, bu, ae, be, bounds, indices, budget, node_limit):
    bounds=list(bounds)
    for i in indices:
        lo,hi=bounds[i]
        lo=None if lo is None else float(np.ceil(lo))
        hi=None if hi is None else float(np.floor(hi))
        if lo is not None and hi is not None and lo>hi:
            return {"status":"infeasible","proof":{"empty_integer_bound":i,"integer_lower":lo,"integer_upper":hi}}
        bounds[i]=(lo,hi)
    queue = [(float('-inf'), 0, bounds)]; serial = 1; nodes = 0; incumbent = None
    best_bound = None
    try:
        while queue:
            budget.check()
            if nodes >= node_limit: invalid("node_limit", "branch and bound node limit", "optimize.not_converged")
            bound, _, box = heapq.heappop(queue); best_bound = bound if np.isfinite(bound) else None
            if incumbent is not None and bound >= incumbent["objective"]-1e-8: continue
            nodes += 1
            lp = solve_lp(c,au,bu,ae,be,box,budget)
            if lp["status"] == "infeasible": continue
            if lp["status"] == "unbounded":
                invalid("integer", "unbounded relaxation does not certify integer unboundedness", "optimize.not_converged", relaxation=lp)
            bound = lp["objective"]; best_bound = bound
            if incumbent is not None and bound >= incumbent["objective"]-1e-8: continue
            fractional = [i for i in indices if abs(lp["x"][i]-round(lp["x"][i])) > 1e-7]
            if not fractional:
                rounded=np.asarray(lp["x"]).copy(); rounded[indices]=np.round(rounded[indices])
                if (not _feasible(rounded,au,bu,ae,be,box)
                        or any((lo is not None and rounded[i]<lo) or (hi is not None and rounded[i]>hi)
                               for i in indices for lo,hi in [box[i]])):
                    invalid("integer","rounded integer candidate failed feasibility","optimize.not_converged")
                lp["x"]=rounded.tolist(); lp["objective"]=float(c@rounded)
                lp["slacks"]=(bu-au@rounded).tolist(); lp["equality_residuals"]=(ae@rounded-be).tolist()
                incumbent = lp; continue
            i = fractional[0]; value = lp["x"][i]; lo, hi = box[i]
            for pair in ((lo, min(hi, np.floor(value)) if hi is not None else np.floor(value)),
                         (max(lo, np.ceil(value)) if lo is not None else np.ceil(value), hi)):
                child = list(box); child[i] = pair
                heapq.heappush(queue,(bound,serial,child)); serial += 1
    except ToolFailure as exc:
        lower = min([v[0] for v in queue]+([best_bound] if best_bound is not None else []), default=None)
        if lower is not None and not np.isfinite(lower): lower=None
        progress = dict(nodes=nodes, incumbent=incumbent, bound=lower,
                        gap=None if incumbent is None or lower is None else max(0.,incumbent["objective"]-lower))
        time_cap=exc.details.get("reason")=="optimize.time_budget_exceeded"
        if time_cap or exc.message == "branch and bound node limit":
            invalid("time_limit" if time_cap else "node_limit", "integer search incomplete",
                    "input.out_of_range" if time_cap else "optimize.not_converged",
                    **progress, **({"reason":"optimize.time_budget_exceeded"} if time_cap else {}))
        if exc.family == "optimize.not_converged":
            # A node LP or candidate check refused: keep its cause and pointer.
            details = {k: v for k, v in exc.details.items() if k not in progress}
            raise ToolFailure(exc.family, "integer search stopped: " + exc.message, {**details, **progress}) from None
        raise
    if incumbent is None: return {"status":"infeasible", "proof":{"exhausted_nodes":nodes}}
    incumbent.update(nodes=nodes, bound=incumbent["objective"], gap=0., integer_indices=indices,
                     dual_scope="final node LP relaxation; not integer sensitivity")
    return incumbent


@server.tool(description="LP accepts JSON coefficients: two-phase simplex with Bland pivots and integer branch and bound. Caps: 200 variables/rows, 30 integer indices, 10000 nodes. Returns checked primal/dual diagnostics and basis RHS ranges; ambiguous certificates refuse. " + GRAMMAR)
def linprog(
    c: Annotated[str, Field(description="Required objective coefficient JSON vector, e.g. '[1,2]'; 1..200 variables; '' refuses.")] = "",
    A_ub: Annotated[str, Field(description="Inequality matrix JSON, e.g. '[[1,2]]', for A_ub*x<=b_ub; '[]' omits; <=200 total rows.")] = "[]",
    b_ub: Annotated[str, Field(description="Inequality RHS JSON vector, e.g. '[3]'; '[]' omits; one value per A_ub row.")] = "[]",
    A_eq: Annotated[str, Field(description="Equality matrix JSON, e.g. '[[1,1]]', for A_eq*x=b_eq; '[]' omits; <=200 total rows.")] = "[]",
    b_eq: Annotated[str, Field(description="Equality RHS JSON vector, e.g. '[2]'; '[]' omits; one value per A_eq row.")] = "[]",
    bounds: Annotated[str, Field(description="JSON [lower,upper] pairs, e.g. '[[0,5],[null,null]]'; null is unbounded; '' defaults to nonnegative.")] = "",
    maximize: Annotated[bool, Field(description="Objective direction, e.g. true maximizes c*x; false minimizes and is the default.")] = False,
    integer: Annotated[str, Field(description="JSON zero-based integer-variable indices, e.g. '[0,2]'; at most 30; '[]' uses continuous variables.")] = "[]",
    node_limit: Annotated[int, Field(description="Branch and bound cap, e.g. 1000 nodes; integer 1..10000; cap exhaustion refuses.")] = 1000,
    time_limit: TimeLimit = "10 s",
):
    inputs = dict(c=c,A_ub=A_ub,b_ub=b_ub,A_eq=A_eq,b_eq=b_eq,bounds=bounds,maximize=maximize,integer=integer,node_limit=node_limit,time_limit=time_limit)
    def compute(handle):
        budget=Budget(time_limit); objective=array(c,"c",cap=200)
        n=len(objective)
        if not 1<=n<=200: invalid("c","expected 1..200 coefficients")
        def rows(text, rhs, name):
            raw=json_value(text,name)
            mat=np.zeros((0,n)) if raw==[] else array(text,name,ndim=2,cap=40000)
            vec=array(rhs,"b"+name[1:],cap=200)
            if mat.shape != (len(vec),n): invalid(name,"matrix dimensions do not match objective and RHS")
            return mat,vec
        au,bu=rows(A_ub,b_ub,"A_ub"); ae,be=rows(A_eq,b_eq,"A_eq")
        if len(bu)+len(be)>200: invalid("A_ub","at most 200 original constraints")
        raw=json_value(bounds,"bounds") if bounds else [[0,None] for _ in range(n)]
        if not isinstance(raw,list) or len(raw)!=n: invalid("bounds","one bound pair per variable required")
        box=[]
        for i,pair in enumerate(raw):
            if not isinstance(pair,list) or len(pair)!=2: invalid(f"bounds/{i}","expected [lower,upper]")
            pair=tuple(None if v is None else number(v,f"bounds/{i}/{j}",-1e8,1e8) for j,v in enumerate(pair))
            if pair[0] is not None and pair[1] is not None and pair[0]>pair[1]: invalid(f"bounds/{i}","lower bound exceeds upper bound")
            box.append(pair)
        idx=json_value(integer,"integer")
        if not isinstance(idx,list) or len(idx)>30: invalid("integer","expected at most 30 integer indices")
        idx=[count(v,f"integer/{i}",0,n-1) for i,v in enumerate(idx)]
        if len(set(idx))!=len(idx): invalid("integer","duplicate integer index")
        cap=count(node_limit,"node_limit",1,10000)
        if not isinstance(maximize,bool): invalid("maximize","expected boolean")
        values=np.abs(np.r_[objective,au.ravel(),bu,ae.ravel(),be]); nonzero=values[values>0]
        if len(nonzero) and (max(nonzero)>1e8 or min(nonzero)<1e-8 or max(nonzero)/min(nonzero)>1e12):
            invalid("inputs","rescale LP coefficients to 1e-8..1e8 with dynamic range <=1e12","input.out_of_range")
        sign=-1 if maximize else 1
        try:
            answer=(_integer(sign*objective,au,bu,ae,be,box,idx,budget,cap) if idx else solve_lp(sign*objective,au,bu,ae,be,box,budget))
        except ToolFailure as exc:
            if exc.details.get("reason") == "optimize.time_budget_exceeded":
                details={"incumbent":None,"bound":None,"gap":None,**exc.details}
                exc=ToolFailure("input.out_of_range","LP wall-clock limit reached",details)
            if maximize and (exc.family == "optimize.not_converged" or exc.details.get("reason")=="optimize.time_budget_exceeded"):
                details=dict(exc.details)
                if details.get("bound") is not None: details["bound"]=-details["bound"]
                if details.get("incumbent") is not None:
                    incumbent=dict(details["incumbent"])
                    incumbent["objective"]=-incumbent["objective"]
                    for key in ("duals_ub","duals_eq","reduced_costs"):
                        incumbent[key]=[-v for v in incumbent[key]]
                    incumbent["shadow_prices"]={"inequalities":incumbent["duals_ub"],"equalities":incumbent["duals_eq"]}
                    details["incumbent"]=incumbent
                raise ToolFailure(exc.family,exc.message,details) from None
            raise exc
        if answer["status"]!="optimal":
            if maximize and answer["status"]=="unbounded":
                answer["proof"]["objective_slope"]*=-1
            invalid("inputs", "LP is "+answer["status"], "optimize."+answer["status"], **answer)
        for key in ("objective","bound"):
            if key in answer: answer[key]*=sign
        for key in ("duals_ub","duals_eq","reduced_costs"):
            answer[key]=[sign*v for v in answer[key]]
        answer["shadow_prices"]={"inequalities":answer["duals_ub"],"equalities":answer["duals_eq"]}
        return result(answer,"objective",answer["objective"],formulas=["min/max c^T x subject to linear rows and bounds"],
                      assumptions=["Floating-point simplex with Bland pivot selection", "Integer gap is absolute; LP duals do not give integer sensitivity"],
                      validity="Original-coordinate feasibility and improving rays checked. RHS ranges vary one row at a time, hold the basis fixed, and are absent for redundant rows.")
    return run("linprog",inputs,compute)
