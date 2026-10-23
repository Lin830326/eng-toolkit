"""Bounded nonlinear least squares with rank-checked local inference."""

import csv
import io
from typing import Annotated

import numpy as np
from pydantic import Field

from ._common import GRAMMAR, Budget, Seed, TimeLimit, Tolerance, array, bounds_array, count, csv_text, decimal, invalid, json_value, named, number, records, result, run
from ._expr import Expression, TrialError, identifier
from ._numerics import jacobian
from ._stats import t_quantile
from ._server import server


def _certificate(matrix,residual,point,lo,hi,tolerance):
    gradient = matrix.T@residual
    projected = gradient.copy()
    projected[(point<=lo)&(gradient>0)] = 0
    projected[(point>=hi)&(gradient<0)] = 0
    scale = np.maximum(1e-100,np.linalg.norm(matrix,axis=0))*max(1.,np.linalg.norm(residual))
    metric = float(np.max(np.abs(projected)/scale))
    return {"projected_gradient_scaled_inf":metric,"tolerance":tolerance,
            "gradient_scale":scale,"passed":metric<=tolerance}


def _check_curvature(function, point, residual, matrix, lo, hi):
    """Check the full objective Hessian, including residual curvature absent from J'J."""
    singular = np.linalg.svd(matrix,compute_uv=False)
    if singular[-1] <= 1e-10*singular[0]:
        raise TrialError("rank-deficient or ill-conditioned Jacobian; covariance and intervals are undefined", "model")
    gradient = matrix.T@residual
    gradient_error = np.sqrt(np.finfo(float).eps)*np.linalg.norm(matrix,axis=0)*max(1.,np.linalg.norm(residual))
    free = ~(((point<=lo)&(gradient>gradient_error)) | ((point>=hi)&(gradient<-gradient_error)))
    if not np.any(free):
        return {"minimum_eigenvalue": None, "free_parameters": 0}
    units = np.maximum(1.,np.abs(point))
    def objective_gradient(z):
        p = point+units*z
        r = function(p)
        return units*(jacobian(function,p,lo,hi).T@r)
    hessian = jacobian(objective_gradient,np.zeros(point.size),(lo-point)/units,(hi-point)/units)
    reduced = ((hessian+hessian.T)/2)[np.ix_(free,free)]
    eigenvalues = np.linalg.eigvalsh(reduced)
    rounding = 64*np.finfo(float).eps*float(np.linalg.norm(reduced,ord=2))
    if eigenvalues[0] < -rounding:
        raise TrialError("stationary fit has negative objective curvature; no local minimum certified", "model")
    if eigenvalues[0] <= rounding:
        raise TrialError("stationary fit has unresolved or rank-deficient objective curvature", "model")
    return {"minimum_eigenvalue": float(eigenvalues[0]), "free_parameters": int(np.sum(free))}


def _lm(function, initial, lo, hi, tolerance, iterations, budget):
    point = initial.copy()
    residual = function(point)
    damping = .001
    for iteration in range(iterations):
        budget.check()
        matrix = jacobian(function,point,lo,hi)
        gradient = matrix.T@residual
        certificate = _certificate(matrix,residual,point,lo,hi,tolerance)
        if certificate["passed"]:
            return point,residual,iteration
        diagonal = np.maximum(np.sum(matrix*matrix,axis=0),1e-20)
        accepted = False
        for _ in range(30):
            budget.check()
            step = np.linalg.solve(matrix.T@matrix+damping*np.diag(diagonal),-gradient)
            candidate = np.clip(point+step,lo,hi)
            trial = None
            try:
                trial = function(candidate)
                improve = float(residual@residual-trial@trial)
            except TrialError:
                improve = -1.
            if trial is not None and improve<=0 and improve>=-32*np.finfo(float).eps*max(1.,float(residual@residual)) and np.any(candidate!=point):
                trial_matrix = jacobian(function,candidate,lo,hi)
                trial_certificate = _certificate(trial_matrix,trial,candidate,lo,hi,tolerance)
                if trial_certificate["passed"]:
                    return candidate,trial,iteration+1
                if trial_certificate["projected_gradient_scaled_inf"]<.9*certificate["projected_gradient_scaled_inf"]:
                    point,residual = candidate,trial
                    accepted = True
                    damping = max(1e-15,damping/3)
                    break
            if improve > 0:
                point,residual = candidate,trial
                damping = max(1e-15,damping/3)
                accepted = True
                break
            damping *= 10
        if not accepted:
            raise TrialError("LM trust damping could not improve the residual")
    raise TrialError("fit iteration limit reached")


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


@server.tool(description="Fit nonlinear models with bounded LM, up to 12 parameters, 20000 rows and 64 starts. Reports rank-checked local covariance and t95 intervals. "+GRAMMAR)
def fit(
    model: Annotated[str, Field(description="Model expression, e.g. 'a*exp(-b*x)'; parameters and independent names are allowed.")] = "",
    initial: Annotated[str, Field(description="Parameter initial records, e.g. 'a=2;b=0.5'; at most 12 parameters.")] = "",
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
    time_limit: TimeLimit = "10 s",
):
    inputs = dict(locals())
    def compute(handle):
        # One evaluation per vectorised model call (all rows at once); row evaluations
        # and node work have their own caps so the 20000-row limit is reachable.
        budget = Budget(time_limit).count_calls(max_rows=200000000, max_work=2000000000)
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
        best = None
        failures = []
        for start in range(starts):
            point = x.copy() if start==0 else np.array([rng.uniform(a,b) if max(abs(a),abs(b))<1e99 else v+rng.normal()*max(1.,abs(v)) for a,b,v in zip(lo,hi,x)])
            point = np.clip(point,lo,hi)
            try:
                answer = _lm(function,point,lo,hi,tol,iterations,budget)
                final_matrix = jacobian(function,answer[0],lo,hi)
                curvature = _check_curvature(function,answer[0],answer[1],final_matrix,lo,hi)
                score = answer[1]@answer[1]
                if best is None or score < best[1]@best[1]:
                    best = answer
                    best_curvature = curvature
            except TrialError as exc:
                failures.append(str(exc))
        if best is None:
            invalid("model","fit did not converge: "+"; ".join(failures[-3:]),"optimize.not_converged")
        point,residual,steps = best
        matrix = jacobian(function,point,lo,hi)
        certificate = _certificate(matrix,residual,point,lo,hi,tol)
        if not certificate["passed"]:
            invalid("model","final projected-gradient convergence certificate failed","optimize.not_converged")
        certificate["objective_curvature"] = best_curvature
        _,singular,vt = np.linalg.svd(matrix,full_matrices=False)
        if singular[-1]<=1e-10*singular[0]:
            invalid("model","rank-deficient or ill-conditioned Jacobian; covariance and intervals are undefined","input.out_of_range")
        covariance = (vt.T/singular**2)@vt
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
        fitted = predict(point)
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
        if np.any(point-lo<=tol*np.maximum(1.,abs(point))) or np.any(hi-point<=tol*np.maximum(1.,abs(point))):
            warnings.append("A fitted parameter is on a bound; symmetric local covariance intervals may be unreliable.")
        rows = [[columns[key][i] for key in independent_names]+[observed[i],fitted[i],raw_residual[i]] for i in range(n)]
        artifact = handle.write_text("fit.csv",csv_text(independent_names+[dependent,"fitted","residual"],rows),fmt="csv")
        worst = int(np.argmax(np.abs(raw_residual)))
        diagnostics = {"jacobian_condition": condition, "max_absolute_residual": float(abs(raw_residual[worst])),
                       "max_residual_index": worst, "max_residual_coordinates": {key: columns[key][worst] for key in independent_names},
                       "correlation_reason": correlation_reason}
        budget.check()
        return result({"parameters":dict(zip(names,point)),"certificate":certificate,"covariance":covariance,"correlation":correlation,"standard_errors":dict(zip(names,stderr)),"confidence_intervals_95":intervals,"degrees_of_freedom":n-p,"chi_square":chi2,"reduced_chi_square":chi2/(n-p),"rms":np.sqrt(sse/n),"r_squared":r2,"adjusted_r_squared":None if r2 is None else 1-(1-r2)*(n-1)/(n-p),"durbin_watson":dw,"fitted":fitted,"residuals":raw_residual,"iterations":steps,"evaluations":budget.evaluations,"row_evaluations":budget.rows,"seed":seed,"absolute_sigma":absolute_sigma,"converged":True, **diagnostics},"rms",np.sqrt(sse/n),formulas=["min sum(((model-y)/sigma)^2)","cov=(J'J)^-1 times 1 or chi-square/(n-p)","95% CI = parameter +/- t(.975,n-p)*standard error"],assumptions=["Independent residuals; local linear approximation for covariance","Student intervals assume approximately normal residual errors"],validity=["Rank-deficient covariance is refused","R squared uses unweighted residuals; Durbin-Watson follows input row order","Budget: 200000 vectorised model calls, 2e8 row evaluations and 2e9 node-row operations per call, plus the time limit"],artifacts=[artifact],warnings=warnings)
    return run("fit",inputs,compute)
