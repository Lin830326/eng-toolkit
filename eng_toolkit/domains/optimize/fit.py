"""Bounded nonlinear least squares with rank-checked local inference."""

import csv
import io
import time
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit.errors import ToolFailure
from ._common import GRAMMAR, Budget, Seed, TimeLimit, Tolerance, array, bounds_array, count, csv_text, decimal, invalid, json_value, named, number, records, result, run
from ._expr import Expression, TrialError, identifier
from ._numerics import jacobian
from ._stats import t_quantile
from ._server import server

DEFAULT_TIME_LIMIT = "10 s"
BUDGET_REASONS = ("optimize.time_budget_exceeded", "optimize.evaluation_budget_exceeded")


def _scales(initial):
    """Typical magnitude of each parameter: |initial|, or 1 for a zero initial value.

    The solver works in z = x/scale, so finite-difference steps, damping and the multistart
    spread follow the parameter's own size. Without it the step cbrt(eps)*max(1,|x|) was
    6e-6 for a Steinhart-Hart c of 1e-7, which moved the model across its pole."""
    return np.where(initial != 0, np.abs(initial), 1.)


def _equilibrated(matrix):
    """Column-normalised Jacobian: rank, covariance and curvature tests become independent
    of parameter units (van der Sluis: within sqrt(p) of the best diagonal scaling)."""
    columns = np.linalg.norm(matrix, axis=0)
    if not np.all(columns > 0):
        raise TrialError("rank-deficient or ill-conditioned Jacobian; covariance and intervals are undefined", "model")
    return matrix/columns, columns


EPS = float(np.finfo(float).eps)


def _norm(vector):
    """Euclidean norm without overflow for entries up to 1e100."""
    peak = float(np.max(np.abs(vector))) if vector.size else 0.
    return 0. if peak == 0 else peak*float(np.sqrt(np.sum((vector/peak)**2)))


def _residual_scale(residual, data_scale, tolerance):
    """max(||r||, 1000*eps*||y_w||/tol): the certificate is then a cosine test
    |J_j'r| <= tol*||J_j||*||r|| (MINPACK gtol), unless the projected gradient is already
    within 1000*eps of the weighted data norm, the rounding level of an exact fit.

    The old floor max(1,||r||) was one unit of y: with y ~ 1e-10 it certified the initial
    guess, and for y = 1/T (3e-3) it accepted a cosine of 0.025."""
    return max(_norm(residual), 1e3*EPS*data_scale/tolerance)


def _certificate(matrix,residual,point,lo,hi,tolerance,data_scale):
    gradient = matrix.T@residual
    projected = gradient.copy()
    projected[(point<=lo)&(gradient>0)] = 0
    projected[(point>=hi)&(gradient<0)] = 0
    residual_scale = _residual_scale(residual,data_scale,tolerance)
    scale = np.maximum(1e-100,np.linalg.norm(matrix,axis=0))*residual_scale
    metric = float(np.max(np.abs(projected)/scale))
    return {"projected_gradient_scaled_inf":metric,"tolerance":tolerance,
            "gradient_scale":scale,"residual_scale":residual_scale,"passed":metric<=tolerance}


def _check_curvature(function, point, residual, matrix, lo, hi, residual_scale):
    """Check the full objective Hessian, including residual curvature absent from J'J.

    The Hessian is scaled by the Jacobian column norms before the eigenvalue test, so the
    Gauss-Newton part has a unit diagonal and the decision does not depend on parameter
    units; the sign of every eigenvalue is unchanged by that diagonal scaling."""
    equilibrated, columns = _equilibrated(matrix)
    singular = np.linalg.svd(equilibrated,compute_uv=False)
    if singular[-1] <= 1e-10*singular[0]:
        raise TrialError("rank-deficient or ill-conditioned Jacobian; covariance and intervals are undefined", "model")
    gradient = matrix.T@residual
    gradient_error = np.sqrt(EPS)*columns*residual_scale
    free = ~(((point<=lo)&(gradient>gradient_error)) | ((point>=hi)&(gradient<-gradient_error)))
    if not np.any(free):
        return {"minimum_eigenvalue": None, "free_parameters": 0}
    units = np.maximum(1.,np.abs(point))
    def objective_gradient(z):
        p = point+units*z
        r = function(p)
        return units*(jacobian(function,p,lo,hi).T@r)
    hessian = jacobian(objective_gradient,np.zeros(point.size),(lo-point)/units,(hi-point)/units)
    weights = 1/(units*columns)
    hessian = hessian*np.outer(weights,weights)
    reduced = ((hessian+hessian.T)/2)[np.ix_(free,free)]
    eigenvalues = np.linalg.eigvalsh(reduced)
    rounding = 64*np.finfo(float).eps*float(np.linalg.norm(reduced,ord=2))
    if eigenvalues[0] < -rounding:
        raise TrialError("stationary fit has negative objective curvature; no local minimum certified", "model")
    if eigenvalues[0] <= rounding:
        raise TrialError("stationary fit has unresolved or rank-deficient objective curvature", "model")
    return {"minimum_eigenvalue": float(eigenvalues[0]), "free_parameters": int(np.sum(free))}


def _lm(function, initial, lo, hi, tolerance, iterations, budget, progress, data_scale):
    """Levenberg-Marquardt; returns (point, residual, iterations, Jacobian at point).

    `progress` keeps the lowest-residual iterate so a budget stop can report it.
    `data_scale` is the norm of the weighted observations (1 if they are all zero)."""
    point = initial.copy()
    residual = function(point)
    progress.update(point=point, sse=float(residual@residual))
    damping = .001
    for iteration in range(iterations):
        budget.check()
        matrix = jacobian(function,point,lo,hi)
        certificate = _certificate(matrix,residual,point,lo,hi,tolerance,data_scale)
        if certificate["passed"]:
            return point,residual,iteration,matrix
        # Marquardt damping diag(J'J) on the column-equilibrated Jacobian, solved through its
        # SVD instead of the normal equations: the step is independent of parameter units and
        # the condition number is not squared.
        columns = np.linalg.norm(matrix,axis=0)
        columns[columns==0] = 1.  # a parameter without effect here gets no step
        left,singular,right = np.linalg.svd(matrix/columns,full_matrices=False)
        projected = left.T@residual
        accepted = False
        for _ in range(30):
            budget.check()
            step = -(right.T@(singular*projected/(singular*singular+damping)))/columns
            candidate = np.clip(point+step,lo,hi)
            trial = None
            try:
                trial = function(candidate)
                improve = float(residual@residual-trial@trial)
            except TrialError:
                improve = -1.
            # A rounding-level increase (|r| changes by about eps*|y| per row) may still be
            # taken when it improves the certificate; the bound scales with the data.
            rounding = 32*EPS*max(float(residual@residual),_norm(residual)*data_scale)
            if trial is not None and improve<=0 and improve>=-rounding and np.any(candidate!=point):
                trial_matrix = jacobian(function,candidate,lo,hi)
                trial_certificate = _certificate(trial_matrix,trial,candidate,lo,hi,tolerance,data_scale)
                if trial_certificate["passed"]:
                    return candidate,trial,iteration+1,trial_matrix
                if trial_certificate["projected_gradient_scaled_inf"]<.9*certificate["projected_gradient_scaled_inf"]:
                    point,residual = candidate,trial
                    accepted = True
                    damping = max(1e-15,damping/3)
                    break
            if improve > 0:
                point,residual = candidate,trial
                damping = max(1e-15,damping/3)
                accepted = True
                progress.update(point=point, sse=float(residual@residual))
                break
            damping *= 10
        if not accepted:
            raise TrialError("LM trust damping could not improve the residual")
    raise TrialError("fit iteration limit reached")


class _Share:
    """Narrow the shared budget to one start's fair share of what is left.

    10% of the time stays for the final stage; unused time passes on to later starts.
    A start that slow-converges or wanders then cannot spend the other starts' time.
    2% of each count cap is held back too, so the one call that overshoots a share cannot
    also overshoot the call's own cap."""

    def __init__(self, budget, reserve, left):
        self.budget, self.reserve, self.left = budget, reserve, left

    def __enter__(self):
        b, left = self.budget, self.left
        self.saved = (b.deadline, b.max_evaluations, b.max_rows, b.max_work)
        now = time.monotonic()
        b.deadline = min(b.deadline, now + max(0., (b.deadline - now - self.reserve)/left))
        def share(cap, used):
            return used + max(0, (cap - max(1, cap//50) - used)//left)
        b.max_evaluations = share(b.max_evaluations, b.evaluations)
        b.max_work = share(b.max_work, b.work)
        if b.max_rows is not None:
            b.max_rows = share(b.max_rows, b.rows)
        return self

    def __exit__(self, kind, exc, trace):
        b = self.budget
        b.deadline, b.max_evaluations, b.max_rows, b.max_work = self.saved
        return False


def _exhausted(budget):
    """True when the whole call's budget, not only one start's share, is spent."""
    if (budget.evaluations > budget.max_evaluations or budget.work > budget.max_work
            or (budget.max_rows is not None and budget.rows > budget.max_rows)):
        return True
    try:
        budget.check()
    except ToolFailure:
        return True
    return False


def _columns(text, path, independent, dependent, handle):
    if bool(text) == bool(path):
        invalid("data","provide exactly one of data or data_file")
    names = [p.strip() for p in independent.split(",")]
    if not 1 <= len(names) <= 12 or any(not identifier(p) for p in names) or len(set(names)) != len(names):
        invalid("independent","expected 1..12 distinct comma-separated column names")
    if not identifier(dependent) or dependent in names:
        invalid("dependent","dependent column must be a distinct name")
    if path:
        content = handle.input_file(path,param="data_file",extensions=(".csv",),max_bytes=4000000).read_text(encoding="utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))
        if reader.fieldnames is None or len(set(reader.fieldnames)) != len(reader.fieldnames):
            invalid("data_file","CSV needs unique column headers")
        rows = list(reader)
        if len(rows)>20000:
            invalid("data_file","at most 20000 rows are supported","input.out_of_range")
        raw = {}
        for key in names+[dependent]:
            if key not in reader.fieldnames:
                invalid("data_file","missing column "+key)
            raw[key] = []
            for i,row in enumerate(rows):
                cell = row[key]
                if not isinstance(cell,str):
                    invalid(f"data_file/{i}/{key}","expected numeric CSV cell")
                # Same strict decimal grammar as the rest of the domain, not float().
                raw[key].append(number(decimal(cell,f"data_file/{i}/{key}"),f"data_file/{i}/{key}"))
    else:
        raw = json_value(text,"data")
        if isinstance(raw,list):
            if not raw or any(not isinstance(row,list) or len(row)!=len(names)+1 for row in raw):
                invalid("data","rows need independent columns followed by the dependent column")
            raw = dict(zip(names+[dependent],zip(*raw)))
        if not isinstance(raw,dict):
            invalid("data","expected column dictionary or numeric row list")
    columns = {}
    for key in names+[dependent]:
        if key not in raw or not isinstance(raw[key],(list,tuple)):
            invalid("data/"+key,"required numeric column missing")
        columns[key] = np.array([number(v,f"data/{key}/{i}") for i,v in enumerate(raw[key])])
    sizes = {len(v) for v in columns.values()}
    if len(sizes)!=1 or not 2 <= len(columns[dependent]) <= 20000:
        invalid("data","columns need equal lengths of 2..20000")
    return names,columns


@server.tool(description="Fit nonlinear models with bounded LM, up to 12 parameters, 20000 rows and 64 starts. Each parameter is scaled by its initial value, so give initial values of the expected size. Reports rank-checked local covariance and t95 intervals. "+GRAMMAR)
def fit(
    model: Annotated[str, Field(description="Model expression, e.g. 'a*exp(-b*x)'; parameters and independent names are allowed.")] = "",
    initial: Annotated[str, Field(description="Parameter initial records, e.g. 'a=2;b=0.5'; at most 12; a nonzero value also sets the parameter's scale.")] = "",
    data: Annotated[str, Field(description="JSON column lists or rows, e.g. '{\"x\":[0,1,2],\"y\":[2,1,0.5]}'.")] = "",
    data_file: Annotated[str, Field(description="Optional CSV path, e.g. 'observations.csv', with numeric named columns; use instead of data.")] = "",
    independent: Annotated[str, Field(description="Comma-separated independent column names, e.g. 'x' or 'x,z'.")] = "x",
    dependent: Annotated[str, Field(description="Dependent data column name, e.g. 'y'.")] = "y",
    sigma: Annotated[str, Field(description="Optional JSON positive standard deviations, e.g. '[0.1,0.2,0.1]', one per row; blank means unit weights.")] = "",
    bounds: Annotated[str, Field(description="Parameter JSON bound pairs in initial order, e.g. '[[0,5],[0,2]]'; null means unbounded.")] = "",
    absolute_sigma: Annotated[bool, Field(description="Known-sigma covariance flag, e.g. true; false scales covariance by reduced chi-square.")] = False,
    multistart: Annotated[int, Field(description="Seeded fit start count, e.g. 8; 1..64, best converged weighted least squares is retained.")] = 1,
    tolerance: Tolerance = 1e-8,
    max_iterations: Annotated[int, Field(description="LM iteration cap per start, e.g. 500; 1..2000.")] = 500,
    seed: Seed = 0,
    time_limit: TimeLimit = DEFAULT_TIME_LIMIT,
):
    inputs = dict(locals())
    def guarded(handle):
        # The clock refusal names /time_limit only when the caller chose the limit; with the
        # default, the fit (model, initial values, starts) is what did not finish.
        try:
            return compute(handle)
        except ToolFailure as exc:
            if exc.details.get("reason") != BUDGET_REASONS[0] or exc.details.get("param") != "/time_limit" or time_limit != DEFAULT_TIME_LIMIT:
                raise
            raise ToolFailure(exc.family, "optimize.time_budget_exceeded: the fit did not finish within the default time_limit of "
                              + DEFAULT_TIME_LIMIT + "; give initial values of the expected size and sign, fewer starts or a longer time_limit (at most 60 s)",
                              {**exc.details, "param": "/model", "time_limit": DEFAULT_TIME_LIMIT}) from None
    def compute(handle):
        # One evaluation per vectorised model call (all rows at once); row evaluations
        # and node work have their own caps so the 20000-row limit is reachable.
        budget = Budget(time_limit).count_calls(max_rows=200000000, max_work=2000000000)
        reserve = .1*(budget.deadline-time.monotonic())
        tol = number(tolerance,"tolerance",1e-12,.01)
        iterations = count(max_iterations,"max_iterations",1,2000)
        starts = count(multistart,"multistart",1,64)
        if not isinstance(absolute_sigma,bool):
            invalid("absolute_sigma","expected a boolean")
        rng = np.random.default_rng(count(seed,"seed",0,4294967295))
        values = named(initial,cap=12)
        names,x = tuple(values),np.array(list(values.values()))
        lo,hi = bounds_array(bounds,names,x)
        if np.any(lo==hi):
            invalid("bounds","fixed parameters must be substituted as constants in the model")
        independent_names,columns = _columns(data,data_file,independent,dependent,handle)
        if set(names)&set(independent_names) or len(names)+len(independent_names)>16:
            invalid("initial","parameter/independent names must be distinct and total at most 16")
        expression = Expression(model,tuple(independent_names)+names,"model")
        observed = columns[dependent]
        n,p = len(observed),len(names)
        if n<=p:
            invalid("data","more observations than parameters required for covariance")
        deviations = np.ones(n) if not sigma else array(sigma,"sigma")
        if deviations.size!=n or np.any(deviations<=0):
            invalid("sigma","one strictly positive sigma per observation is required")
        sigma_scale = float(np.min(deviations))
        relative_deviations = deviations/sigma_scale
        def predict(point):
            bindings = {key:columns[key] for key in independent_names}
            bindings.update(zip(names,point))
            prediction = np.asarray(expression.evaluate(bindings,budget),dtype=float)
            if prediction.ndim==0:
                prediction = np.full(n,float(prediction))
            if prediction.shape!=(n,):
                raise TrialError("model did not produce one prediction per observation")
            return prediction
        def function(point):
            return (predict(point)-observed)/relative_deviations
        # The solver works in z = x/scale. Bounds map exactly: a z on a scaled bound is the
        # supplied bound in x, so bound-held parameters stay on the bound after mapping.
        scales = _scales(x)
        zlo = np.where(lo<=-1e100,lo,lo/scales)
        zhi = np.where(hi>=1e100,hi,hi/scales)
        def to_x(z):
            point = np.clip(z*scales,lo,hi)
            return np.where(z<=zlo,lo,np.where(z>=zhi,hi,point))
        def scaled(z):
            return function(to_x(z))
        data_scale = _norm(observed/relative_deviations) or 1.
        best = None
        failures,stopped,converged,closest = [],[],0,None
        for start in range(starts):
            point = x.copy() if start==0 else np.array([rng.uniform(a,b) if max(abs(a),abs(b))<1e99 else v+rng.normal()*s for a,b,v,s in zip(lo,hi,x,scales)])
            point = np.clip(point,lo,hi)
            progress = {}
            try:
                with _Share(budget,reserve,starts-start):
                    z,residual,steps,matrix = _lm(scaled,np.clip(point/scales,zlo,zhi),zlo,zhi,tol,iterations,budget,progress,data_scale)
                    converged += 1
                    # Only a start below the best certified residual can replace it, so only
                    # such a start needs the (costly) curvature check; the outcome is unchanged.
                    if best is None or residual@residual < best[1]@best[1]:
                        curvature = _check_curvature(scaled,z,residual,matrix,zlo,zhi,_residual_scale(residual,data_scale,tol))
                        best = (z,residual,steps,matrix,curvature,predict(to_x(z)))
            except TrialError as exc:
                failures.append(f"start {start}: {exc}")
            except ToolFailure as exc:
                reason = exc.details.get("reason")
                if reason not in BUDGET_REASONS or _exhausted(budget):
                    raise
                stopped.append(reason)
                failures.append(f"start {start}: stopped at its share of the "+("time limit" if reason==BUDGET_REASONS[0] else "evaluation budget"))
            if progress and (closest is None or progress["sse"] < closest["sse"]):
                closest = progress
        if best is None:
            details = {}
            if closest is not None and closest["sse"]/sigma_scale/sigma_scale < 1e100:
                details["closest_uncertified"] = {"parameters": dict(zip(names,to_x(closest["point"]))),
                                                  "chi_square": closest["sse"]/sigma_scale/sigma_scale}
            if stopped:
                timed = BUDGET_REASONS[0] in stopped
                invalid("time_limit" if timed and time_limit!=DEFAULT_TIME_LIMIT else "model",
                        f"fit did not converge: {len(stopped)} of {starts} starts stopped at their budget share"
                        +(f" (time_limit {time_limit})" if timed else "")+"; "+"; ".join(failures[-3:])
                        +"; give initial values of the expected size and sign, fewer starts or a longer time_limit (at most 60 s)",
                        "optimize.not_converged",reason=BUDGET_REASONS[0] if timed else stopped[0],**details)
            invalid("model","fit did not converge: "+"; ".join(failures[-3:]),"optimize.not_converged",**details)
        z,residual,steps,matrix,best_curvature,fitted = best
        point = to_x(z)
        certificate = _certificate(matrix,residual,z,zlo,zhi,tol,data_scale)
        if not certificate["passed"]:
            invalid("model","final projected-gradient convergence certificate failed","optimize.not_converged")
        # The scaled metric is invariant under z = x/scale; report the column scale in x units.
        certificate["gradient_scale"] = certificate["gradient_scale"]/scales
        certificate["objective_curvature"] = best_curvature
        try:
            equilibrated,norms = _equilibrated(matrix)
        except TrialError as exc:
            invalid("model",str(exc),"input.out_of_range")
        _,singular,vt = np.linalg.svd(equilibrated,full_matrices=False)
        if singular[-1]<=1e-10*singular[0]:
            invalid("model","rank-deficient or ill-conditioned Jacobian; covariance and intervals are undefined","input.out_of_range")
        # cov = (J'J)^-1 in x units, from the equilibrated SVD: J_x = J_z/scale, columns/norms.
        unit = scales/norms
        covariance = ((vt.T/singular**2)@vt)*np.outer(unit,unit)
        normalized_chi2 = float(residual@residual)
        chi2 = normalized_chi2/sigma_scale/sigma_scale
        if not absolute_sigma:
            covariance *= normalized_chi2/(n-p)
        else:
            covariance *= sigma_scale*sigma_scale
        stderr = np.sqrt(np.maximum(0.,np.diag(covariance)))
        denominator = np.outer(stderr,stderr)
        correlation = np.divide(covariance,denominator,out=np.zeros_like(covariance),where=denominator>0)
        correlation_reason = None
        if np.all(stderr == 0):
            correlation = None
            correlation_reason = "Correlation is undefined when estimated covariance is exactly zero."
        critical = t_quantile(.975,n-p)
        raw_residual = observed-fitted
        sse = float(raw_residual@raw_residual)
        centered = observed-np.mean(observed)
        total = float(centered@centered)
        r2 = 1-sse/total if total>0 else None
        dw = float(np.diff(raw_residual)@np.diff(raw_residual)/sse) if sse>0 else None
        intervals = {key:[v-critical*s,v+critical*s] for key,v,s in zip(names,point,stderr)}
        warnings = []
        condition = float(singular[0] / singular[-1])
        if condition > 1e6:
            warnings.append("The weighted Jacobian is nearly rank deficient; parameters may not be individually identifiable.")
        if np.any(point-lo<=tol*np.maximum(scales,abs(point))) or np.any(hi-point<=tol*np.maximum(scales,abs(point))):
            warnings.append("A fitted parameter is on a bound; symmetric local covariance intervals may be unreliable.")
        if stopped:
            warnings.append(f"{len(stopped)} of {starts} starts stopped at their share of the time or evaluation budget; "
                            f"the result is the best of the {converged} converged starts.")
        rows = [[columns[key][i] for key in independent_names]+[observed[i],fitted[i],raw_residual[i]] for i in range(n)]
        artifact = handle.write_text("fit.csv",csv_text(independent_names+[dependent,"fitted","residual"],rows),fmt="csv")
        worst = int(np.argmax(np.abs(raw_residual)))
        diagnostics = {"jacobian_condition": condition, "max_absolute_residual": float(abs(raw_residual[worst])),
                       "max_residual_index": worst, "max_residual_coordinates": {key: columns[key][worst] for key in independent_names},
                       "correlation_reason": correlation_reason, "parameter_scales": dict(zip(names,scales)),
                       "multistart": {"starts": starts, "converged": converged, "stopped_at_budget_share": len(stopped)}}
        budget.check()
        return result({"parameters":dict(zip(names,point)),"certificate":certificate,"covariance":covariance,"correlation":correlation,"standard_errors":dict(zip(names,stderr)),"confidence_intervals_95":intervals,"degrees_of_freedom":n-p,"chi_square":chi2,"reduced_chi_square":chi2/(n-p),"rms":np.sqrt(sse/n),"r_squared":r2,"adjusted_r_squared":None if r2 is None else 1-(1-r2)*(n-1)/(n-p),"durbin_watson":dw,"fitted":fitted,"residuals":raw_residual,"iterations":steps,"evaluations":budget.evaluations,"row_evaluations":budget.rows,"seed":seed,"absolute_sigma":absolute_sigma,"converged":True, **diagnostics},"rms",np.sqrt(sse/n),formulas=["min sum(((model-y)/sigma)^2)","cov=(J'J)^-1 times 1 or chi-square/(n-p)","95% CI = parameter +/- t(.975,n-p)*standard error"],assumptions=["Independent residuals; local linear approximation for covariance","Student intervals assume approximately normal residual errors"],validity=["Rank-deficient covariance is refused","R squared uses unweighted residuals; Durbin-Watson follows input row order","Budget: 200000 vectorised model calls, 2e8 row evaluations and 2e9 node-row operations per call, plus the time limit"],artifacts=[artifact],warnings=warnings)
    return run("fit",inputs,guarded)
