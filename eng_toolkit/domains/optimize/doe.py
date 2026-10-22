"""Bounded experimental designs and response-surface diagnostics."""
from __future__ import annotations

import itertools
import math
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit.contract import ToolResult
from ._common import Budget, GRAMMAR, Seed, TimeLimit, array, choice, count, csv_text, invalid, json_value, named, number, records, result, run
from ._expr import Expression
from ._server import server
from ._stats import f_survival, t_quantile


def _factors(text):
    if isinstance(text, str) and text.lstrip().startswith("{"):
        raw = json_value(text, "factors")
        if not isinstance(raw, dict):
            invalid("factors", "expected JSON object of name:[lower,upper]")
    else:
        raw = {}
        for i, row in enumerate(records(text, "factors", 6)):
            parts = [p.strip() for p in row.split(",")]
            if len(parts) != 3 or parts[0] in raw:
                invalid(f"factors/{i}", "use unique name,lower,upper records")
            raw[parts[0]] = [named("v="+p,"factors")["v"] for p in parts[1:]]
    if not 1 <= len(raw) <= 6:
        invalid("factors", "provide 1..6 factor ranges", "input.out_of_range")
    Expression("0", tuple(raw), "factors")
    ranges = []
    for name, pair in raw.items():
        if not isinstance(pair, list) or len(pair) != 2:
            invalid("factors/"+name, "expected [lower,upper]")
        a,b = [number(v,"factors/"+name) for v in pair]
        if a >= b:
            invalid("factors/"+name, "lower must be smaller than upper")
        ranges.append([a,b])
    return list(raw), np.array(ranges)


def _cube(k):
    return np.array(list(itertools.product((-1.,1.), repeat=k)))


def _words(masks):
    """Complete defining contrast subgroup, indexed by output factor letters."""
    words = []
    k = len(masks)
    for subset in range(1, 1 << k):
        xor = 0
        for j in range(k):
            if subset & (1 << j):
                xor ^= masks[j]
        if xor == 0:
            words.append("".join(chr(65+j) for j in range(k) if subset & (1 << j)))
    return words


def _fraction(k, resolution, generators):
    if generators:
        parsed = {}
        for row in records(generators, "generators", 5):
            parts = row.replace(" ","").upper().split("=")
            if len(parts) != 2 or len(parts[0]) != 1 or parts[0] in parsed or not parts[1]:
                invalid("generators", "use positive regular generators, e.g. D=ABC;E=AB")
            parsed[parts[0]] = parts[1]
        letters = [chr(65+i) for i in range(k)]
        if set(parsed) - set(letters):
            invalid("generators", "generator factor must occur among factor letters A..F")
        base = [letter for letter in letters if letter not in parsed]
        if not base or len(base) == k:
            invalid("generators", "at least one dependent and one independent factor required")
        lookup = {letter: 1 << i for i,letter in enumerate(base)}
        for letter, term in parsed.items():
            if len(set(term)) != len(term) or any(c not in lookup or c in parsed for c in term):
                invalid("generators", "right sides must contain unique independent factor letters")
            mask = 0
            for c in term:
                mask ^= lookup[c]
            lookup[letter] = mask
        masks = [lookup[c] for c in letters]
        m = len(base)
    else:
        masks, m = None, None
        for candidate_m in range(2,k):
            candidates = [mask for mask in range(1,1<<candidate_m) if mask.bit_count() >= 2]
            for extra in itertools.combinations(candidates, k-candidate_m):
                current = [1 << i for i in range(candidate_m)] + list(extra)
                words = _words(current)
                if min(map(len,words),default=k+1) >= resolution:
                    masks,m = current,candidate_m
                    break
            if masks is not None:
                break
        if masks is None:
            invalid("design", f"no regular fractional design with {k} factors and resolution >= {resolution}; use a full factorial")
    words = _words(masks)
    actual = min(map(len,words),default=k+1)
    if actual < resolution:
        invalid("generators", f"specified generators have resolution {actual}, below requested {resolution}")
    base_cube = _cube(m)
    coded = np.column_stack([np.prod(base_cube[:,[i for i in range(m) if mask & (1 << i)]],axis=1) for mask in masks])
    aliases = {}
    for degree in (1,2):
        for subset in itertools.combinations(range(k),degree):
            mask = 0
            for j in subset:
                mask ^= masks[j]
            label = "".join(chr(65+j) for j in subset)
            aliases[label] = ["".join(chr(65+j) for j in range(k) if bits & (1<<j))
                              for bits in range(1,1<<k)
                              if _mask_xor(bits,masks) == mask]
    return coded, {"resolution": actual, "defining_words": ["I",*words], "alias_sets": aliases,
                   "factor_letters": [chr(65+i) for i in range(k)]}


def _mask_xor(bits,masks):
    value = 0
    for i,mask in enumerate(masks):
        if bits & (1<<i):
            value ^= mask
    return value


def _halton(n,k,rng):
    out = np.zeros((n,k))
    for j,base in enumerate((2,3,5,7,11,13)[:k]):
        indices = np.arange(1,n+1)
        factor = 1.
        while np.any(indices):
            factor /= base
            out[:,j] += factor*(indices % base)
            indices //= base
    return (out+rng.random(k)) % 1


def _sobol(n,k,rng):
    # Primitive-polynomial direction numbers for the first six dimensions.
    specs = [(0,0,()), (1,0,(1,)), (2,1,(1,3)), (3,1,(1,3,1)), (3,2,(1,1,1)), (4,1,(1,1,3,3))]
    bits = 32
    directions = np.zeros((k,bits),dtype=np.uint32)
    directions[0] = [1 << (31-j) for j in range(bits)]
    for d in range(1,k):
        s,a,initial = specs[d]
        for j in range(s):
            directions[d,j] = initial[j] << (31-j)
        for j in range(s,bits):
            value = directions[d,j-s] ^ (directions[d,j-s] >> s)
            for q in range(1,s):
                if (a >> (s-1-q)) & 1:
                    value ^= directions[d,j-q]
            directions[d,j] = value
    state = rng.integers(0, 2**32, size=k, dtype=np.uint32)
    output = np.empty((n,k))
    for i in range(n):
        output[i] = state.astype(float)/2**32
        bit = ((i+1) & -(i+1)).bit_length()-1
        state ^= directions[:,bit]
    return output


def _design(k, design, centers, alpha, samples, rng, candidates, generators, budget, levels=""):
    meta = {}
    if levels and design != "full_factorial":
        invalid("levels","factor levels apply only to full_factorial designs")
    if design == "full_factorial":
        level_counts = [2]*k
        if levels:
            raw = json_value(levels,"levels")
            if not isinstance(raw,list) or len(raw)!=k:
                invalid("levels","provide one integer level count per factor")
            level_counts = [count(v,f"levels/{i}",2,10) for i,v in enumerate(raw)]
        if math.prod(level_counts)+centers>4096:
            invalid("levels","factorial runs plus centers exceed 4096", "input.out_of_range")
        coded = np.array(list(itertools.product(*[np.linspace(-1,1,n) for n in level_counts])))
        meta["levels"] = level_counts
    elif design.startswith("fractional_"):
        resolution = {"fractional_iii":3,"fractional_iv":4,"fractional_v":5}[design]
        coded,meta = _fraction(k,resolution,generators)
    elif design == "box_behnken":
        if k < 3:
            invalid("factors", "Box-Behnken requires at least three factors")
        coded = []
        for i,j in itertools.combinations(range(k),2):
            for a,b in itertools.product((-1.,1.),repeat=2):
                row = np.zeros(k)
                row[i],row[j] = a,b
                coded.append(row)
        coded = np.array(coded)
    elif design.startswith("ccd_"):
        rotatable = 2**(k/4)
        if alpha == "rotatable":
            axial = rotatable
        else:
            if not isinstance(alpha,str):
                invalid("alpha","expected 'rotatable' or a numeric literal as text")
            axial = named("a="+alpha,"alpha")["a"]
            number(axial,"alpha",1,10)
        if design == "ccd_face":
            axial = 1.
        cube = _cube(k)
        stars = np.vstack([np.eye(k),-np.eye(k)])*axial
        coded = np.vstack([cube,stars])
        if design == "ccd_inscribed":
            coded /= axial
        meta.update(alpha=axial, rotatable=bool(abs(axial-rotatable)<1e-12),
                    range_note="Circumscribed axial points extend beyond natural factor ranges." if design == "ccd_circumscribed" else "Factor ranges bound the generated points.")
    else:
        n = count(samples,"samples",2,4096)
        if design == "lhs":
            attempts = count(candidates,"lhs_candidates",1,32)
            if n > 1024 and attempts > 1:
                invalid("samples", "maximin LHS is capped at 1024 rows; set lhs_candidates=1 for up to 4096", "input.out_of_range")
            best_score,best = -1.,None
            for _ in range(attempts):
                budget.consume(0,n*n*k if attempts > 1 else n*k)
                points = np.column_stack([(rng.permutation(n)+rng.random(n))/n for _ in range(k)])
                if attempts == 1:
                    best = points
                else:
                    # Row blocks limit memory at the stated 1024-row cap.
                    score = math.inf
                    for start in range(0,n,128):
                        distances = np.sum((points[start:start+128,None,:]-points[None,:,:])**2,axis=2)
                        distances[np.arange(len(distances)),np.arange(start,start+len(distances))] = np.inf
                        score = min(score,float(np.min(distances)))
                    if score > best_score:
                        best_score,best = score,points
            coded = 2*best-1
            meta.update(maximin_candidates=attempts, minimum_pair_distance=None if attempts==1 else math.sqrt(best_score))
        elif design == "sobol":
            coded = 2*_sobol(n,k,rng)-1
            meta["scramble"] = "seeded digital shift; balance guarantee requires a power-of-two sample count"
        else:
            coded = 2*_halton(n,k,rng)-1
            meta["scramble"] = "seeded Cranley-Patterson shift"
        centers = 0
    if centers:
        coded = np.vstack([coded,np.zeros((centers,k))])
    budget.consume(0,coded.size)
    return coded,meta


def _terms(k, fit):
    terms = [(), *[(j,) for j in range(k)]]
    if fit in ("interaction","quadratic"):
        terms += list(itertools.combinations(range(k),2))
    if fit == "quadratic":
        terms += [(j,j) for j in range(k)]
    return terms


def _matrix(points,terms):
    return np.column_stack([np.prod(points[:,term],axis=1) if term else np.ones(len(points)) for term in terms])


def _box_optimum(beta, terms, k, maximize, budget):
    """Enumerate all box faces and stationary points of quadratic restrictions."""
    g,h = np.zeros(k),np.zeros((k,k))
    for coefficient,term in zip(beta,terms):
        if len(term)==1:
            g[term[0]] = coefficient
        elif len(term)==2:
            i,j = term
            if i==j:
                h[i,i] = 2*coefficient
            else:
                h[i,j] = h[j,i] = coefficient
    best = None
    candidates = 0
    for face in itertools.product((-1.,0.,1.),repeat=k):
        budget.consume(0,k**3)
        x = np.array(face)
        free = np.where(x==0)[0]
        fixed = np.where(x!=0)[0]
        if len(free):
            hf = h[np.ix_(free,free)]
            rhs = -g[free]-h[np.ix_(free,fixed)]@x[fixed]
            solution,_,_,_ = np.linalg.lstsq(hf,rhs,rcond=None)
            if np.linalg.norm(hf@solution-rhs) > 1e-9*max(1.,np.linalg.norm(rhs)) or np.any(np.abs(solution)>1+1e-10):
                continue
            x[free] = np.clip(solution,-1,1)
        y = float((_matrix(x[None,:],terms)@beta)[0])
        candidates += 1
        if best is None or (y>best[0] if maximize else y<best[0]):
            best = (y,x.copy())
    return {"value":best[0],"coded":best[1],"candidate_count":candidates,
            "certificate":"Enumerated vertices and feasible stationary points on every box face of fitted quadratic; finite-precision result."},g,h


def _fit(coded,y,names,fit,budget):
    k,n = len(names),len(y)
    terms = _terms(k,fit)
    matrix = _matrix(coded,terms)
    budget.consume(0,n*len(terms)**2)
    beta,_,rank,singular = np.linalg.lstsq(matrix,y,rcond=None)
    if rank < len(terms):
        invalid("fit", "response model is rank deficient for this design; choose a simpler fit or a richer design", rank=int(rank), columns=len(terms))
    if singular[-1] <= singular[0]*1e-12:
        invalid("fit","response model is too ill-conditioned", "input.out_of_range")
    predictions = matrix@beta
    residuals = y-predictions
    sse = float(residuals@residuals)
    sst = float(np.sum((y-np.mean(y))**2))
    df_error,df_model = n-len(terms),len(terms)-1
    mse = sse/df_error if df_error>0 else None
    inverse = np.linalg.inv(matrix.T@matrix)
    leverage = np.einsum("ij,jk,ik->i",matrix,inverse,matrix)
    press = None if np.any(1-leverage < 1e-10) else float(np.sum((residuals/(1-leverage))**2))
    numerical_zero = 1e-24*max(1.,float(y@y))
    f = max(0.,(sst-sse)/df_model)/mse if df_model and mse is not None and mse>numerical_zero else None
    coefficients = []
    critical = t_quantile(.975,df_error) if df_error>0 else None
    for i,term in enumerate(terms):
        label = "intercept" if not term else "*".join(names[j] for j in term)
        se = math.sqrt(max(0.,mse*inverse[i,i])) if mse is not None else None
        coefficients.append({"term":label,"coefficient":float(beta[i]),"standard_error":se,
                             "confidence_interval_95":None if se is None else [float(beta[i]-critical*se),float(beta[i]+critical*se)]})
    groups = {}
    for i,row in enumerate(coded):
        groups.setdefault(tuple(row),[]).append(float(y[i]))
    pure_df = sum(len(v)-1 for v in groups.values())
    pure_sse = sum(sum((a-float(np.mean(v)))**2 for a in v) for v in groups.values())
    lof_df = df_error-pure_df
    lof_sse = max(0.,sse-pure_sse)
    lof_f = (lof_sse/lof_df)/(pure_sse/pure_df) if lof_df>0 and pure_df>0 and pure_sse>numerical_zero else None
    minimum,g,h = _box_optimum(beta,terms,k,False,budget)
    maximum,_,_ = _box_optimum(beta,terms,k,True,budget)
    eigenvalues,eigenvectors = np.linalg.eigh(h)
    threshold = 1e-9*max(1.,float(np.max(np.abs(eigenvalues))))
    stationary = None
    if np.min(np.abs(eigenvalues)) > threshold:
        location = np.linalg.solve(h,-g)
        nature = "minimum" if np.all(eigenvalues>0) else "maximum" if np.all(eigenvalues<0) else "saddle"
        stationary = {"coded":location,"value":float((_matrix(location[None,:],terms)@beta)[0]),"nature":nature,"inside_box":bool(np.all(np.abs(location)<=1))}
    return {"model":fit,"coefficients":coefficients,"rank":int(rank),"condition_number":float(singular[0]/singular[-1]),
        "SSE":sse,"SST":sst,"R_squared":None if sst==0 else 1-sse/sst,
        "adjusted_R_squared":None if sst==0 or df_error<=0 else 1-(sse/df_error)/(sst/(n-1)),
        "PRESS":press,"predicted_R_squared":None if press is None or sst==0 else 1-press/sst,
        "ANOVA":{"model_df":df_model,"error_df":df_error,"total_df":n-1,"model_SS":max(0.,sst-sse),"error_SS":sse,
                 "MSE":mse,"F":f,"p_value":None if f is None else f_survival(f,df_model,df_error),
                 "undefined_reason": "Zero residual degrees of freedom or numerically zero residual variance" if f is None else None},
        "lack_of_fit":{"pure_error_SS":pure_sse,"pure_error_df":pure_df,"lack_of_fit_SS":lof_sse,"lack_of_fit_df":lof_df,
                       "F":lof_f,"p_value":None if lof_f is None else f_survival(lof_f,lof_df,pure_df),
                       "undefined_reason":"Replicates with positive pure error and positive lack-of-fit degrees of freedom are required" if lof_f is None else None},
        "main_effects":{names[t[0]]:2*float(b) for b,t in zip(beta,terms) if len(t)==1},
        "interaction_effects":{"*".join(names[j] for j in t):2*float(b) for b,t in zip(beta,terms) if len(t)==2 and t[0]!=t[1]},
        "effect_definition":"Two-level factorial effect is twice the coded coefficient; main effects are conditional on other coded factors zero.",
        "stationary":stationary,"stationary_note":"A singular Hessian has no unique stationary point" if stationary is None else None,
        "hessian_eigenvalues":eigenvalues,"hessian_eigenvectors":eigenvectors,"box_minimum":minimum,"box_maximum":maximum,
        "residuals":residuals,"predictions":predictions,"leverage":leverage}


@server.tool(description="Generate factorial/fractional, Box-Behnken, central composite, LHS, Sobol or Halton designs; fit coded response surfaces with ANOVA and bounded quadratic extrema. " + GRAMMAR)
def doe(
    factors: Annotated[str, Field(description='One to six factor ranges as JSON {"x":[0,10],"y":[1,3]} or records x,0,10;y,1,3.')] = "",
    design: Annotated[str, Field(description="Design, e.g. 'full_factorial'; also fractional_iii/iv/v, box_behnken, ccd_face/circumscribed/inscribed, lhs, sobol, halton.")] = "full_factorial",
    levels: Annotated[str, Field(description="Full-factorial integer levels per factor, e.g. '[3,4]'; 2..10 each, at most 4096 runs; '' uses two each.")] = "",
    generators: Annotated[str, Field(description="Positive fractional generators on letters A..F, e.g. 'D=ABC'; '' chooses the smallest fraction meeting resolution.")] = "",
    center_points: Annotated[int, Field(description="Center replicates for factorial/response-surface designs, e.g. 3; 0..100. Space-filling designs ignore this setting.")] = 1,
    alpha: Annotated[str, Field(description="CCD axial distance, e.g. 'rotatable' or '1.5'; numeric range 1..10. Face-centered always uses one.")] = "rotatable",
    samples: Annotated[int, Field(description="Space-filling row count 2..4096. Sobol balance requires power-of-two rows.")] = 16,
    seed: Seed = 0,
    model: Annotated[str, Field(description="Model expression in natural factor units, e.g. 'x*x+y'; '' omits simulation. Exclusive with response inputs.")] = "",
    responses: Annotated[str, Field(description="Optional JSON response number list, exactly one value per generated run in original order.")] = "",
    responses_file: Annotated[str, Field(description="UTF-8 one-column CSV/TXT path, e.g. 'response.csv'; optional header response. '' omits; workspace read policy applies.")] = "",
    fit: Annotated[str, Field(description="Response fit: linear, interaction or quadratic, e.g. 'quadratic'; insufficient-rank designs return a structured refusal.")] = "quadratic",
    lhs_candidates: Annotated[int, Field(description="Maximin Latin hypercube candidates, e.g. 8; integer 1..32. Comparisons count against the shared work budget.")] = 8,
    time_limit: TimeLimit = "10 s",
) -> ToolResult:
    inputs = dict(factors=factors,design=design,levels=levels,generators=generators,center_points=center_points,alpha=alpha,samples=samples,seed=seed,
                  model=model,responses=responses,responses_file=responses_file,fit=fit,lhs_candidates=lhs_candidates,time_limit=time_limit)

    def compute(job):
        budget = Budget(time_limit)
        names,ranges = _factors(factors)
        k = len(names)
        kind = choice(design,"design",("full_factorial","fractional_iii","fractional_iv","fractional_v","box_behnken","ccd_face","ccd_circumscribed","ccd_inscribed","lhs","sobol","halton"))
        if generators and not kind.startswith("fractional_"):
            invalid("generators","generators apply only to fractional factorial designs")
        centers = count(center_points,"center_points",0,100)
        seed_value = count(seed,"seed",0,4294967295)
        coded,metadata = _design(k,kind,centers,alpha,samples,np.random.default_rng(seed_value),lhs_candidates,generators,budget,levels)
        midpoint = ranges[:,0]/2+ranges[:,1]/2
        half = ranges[:,1]/2-ranges[:,0]/2
        natural = midpoint+coded*half
        data = {"design":kind,"names":names,"runs":len(coded),"seed":seed_value,"coded":coded,"natural":natural,
                "factor_ranges":ranges,"metadata":metadata,"center_points":centers if kind not in ("lhs","sobol","halton") else 0}
        warnings = []
        if kind == "sobol" and len(coded)&(len(coded)-1):
            warnings.append("Sobol sample count is not a power of two; digital-net balance is not guaranteed.")
        if sum(bool(v) for v in (model,responses,responses_file)) > 1:
            invalid("responses","supply exactly one of model, responses, responses_file")
        y = None
        if model:
            exp = Expression(model,names,"model")
            y = np.broadcast_to(exp.evaluate(dict(zip(names,natural.T)),budget),len(coded)).copy()
        elif responses:
            y = array(responses,"responses",cap=4096)
        elif responses_file:
            if not isinstance(responses_file,str):
                invalid("responses_file","expected a file path as text")
            path = job.input_file(responses_file,param="responses_file",extensions=(".csv",".txt"),max_bytes=1000000)
            text = path.read_text(encoding="utf-8-sig")
            lines = text.splitlines()
            if lines and lines[0].strip().lower() == "response":
                lines = lines[1:]
            if len(lines)>4096 or any(not line.strip() for line in lines):
                invalid("responses_file","expected at most 4096 nonempty numeric rows")
            y = np.array([named("r="+line.strip(),"responses_file")["r"] for line in lines])
        if y is not None:
            if len(y)!=len(coded):
                invalid("responses","response count must equal generated run count")
            fit_kind = choice(fit,"fit",("linear","interaction","quadratic"))
            fitted = _fit(coded,y,names,fit_kind,budget)
            for label in ("box_minimum","box_maximum","stationary"):
                if fitted[label] is not None:
                    fitted[label]["natural"] = midpoint+np.asarray(fitted[label]["coded"])*half
            data["fit"] = fitted
            data["responses"] = y
            warnings.append("Response-surface inference assumes independent equal-variance errors. Fitted box extrema describe the fitted model, not a validated physical optimum.")
        header = ["run"]+["coded_"+n for n in names]+names+(["response"] if y is not None else [])
        rows = [[i+1,*coded[i],*natural[i],*([float(y[i])] if y is not None else [])] for i in range(len(coded))]
        artifact = job.write_text("design.csv",csv_text(header,rows),fmt="csv")
        budget.check()
        data["evaluations"] = budget.evaluations
        return result(data,"runs",len(coded),formulas={"coding":"natural=midpoint+half_range*coded","least_squares":"beta=(X^T X)^-1 X^T y","PRESS":"sum((residual/(1-leverage))^2)"},
                      assumptions="Factor ranges use consistent caller-selected numeric units. Fractional generators use positive defining contrasts.",
                      validity="Design generation and finite-precision least squares. Rank and conditioning checked; undefined inferential statistics are null with reasons.",artifacts=[artifact],warnings=warnings)
    return run("doe",inputs,compute)
