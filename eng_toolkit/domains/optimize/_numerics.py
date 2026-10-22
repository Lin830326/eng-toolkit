"""Finite differences shared by the domain's solvers."""

import numpy as np

from ._expr import TrialError


def jacobian(function, x, lo=None, hi=None):
    """Differentiate a budget-charged function, respecting bounds and domain failures."""
    x = np.asarray(x, dtype=float)
    lower = np.full_like(x, -1e100) if lo is None else np.asarray(lo)
    upper = np.full_like(x, 1e100) if hi is None else np.asarray(hi)
    base = np.atleast_1d(function(x))
    matrix = np.zeros((base.size, x.size))
    for j in range(x.size):
        step = np.cbrt(np.finfo(float).eps) * max(1., abs(x[j]))
        for _ in range(12):
            plus, minus = min(step, upper[j]-x[j]), min(step, x[j]-lower[j])
            fp = fm = None
            if plus > 0:
                trial = x.copy()
                trial[j] += plus
                try:
                    fp = np.atleast_1d(function(trial))
                except TrialError:
                    pass
            if minus > 0:
                trial = x.copy()
                trial[j] -= minus
                try:
                    fm = np.atleast_1d(function(trial))
                except TrialError:
                    pass
            if fp is not None and fm is not None:
                # Unequal distances occur beside bounds.
                matrix[:, j] = (minus**2*(fp-base)+plus**2*(base-fm))/(plus*minus*(plus+minus))
                if plus == minus:
                    half = plus/2
                    right,left = x.copy(),x.copy()
                    right[j] += half
                    left[j] -= half
                    try:
                        finer = (np.atleast_1d(function(right))-np.atleast_1d(function(left)))/(2*half)
                        matrix[:,j] = (4*finer-matrix[:,j])/3
                    except TrialError:
                        pass
                break
            # One-sided at a bound or domain edge: the second-order three-point formula
            # (-3f0+4f(h)-f(2h))/(2h) when the 2h point is valid, else first order.
            if fp is not None:
                matrix[:, j] = (fp-base)/plus
                if 2*plus <= upper[j]-x[j]:
                    trial = x.copy()
                    trial[j] += 2*plus
                    try:
                        matrix[:, j] = (4*fp-3*base-np.atleast_1d(function(trial)))/(2*plus)
                    except TrialError:
                        pass
                break
            if fm is not None:
                matrix[:, j] = (base-fm)/minus
                if 2*minus <= x[j]-lower[j]:
                    trial = x.copy()
                    trial[j] -= 2*minus
                    try:
                        matrix[:, j] = (3*base-4*fm+np.atleast_1d(function(trial)))/(2*minus)
                    except TrialError:
                        pass
                break
            if plus == minus == 0:
                break
            step *= .25
        else:
            raise TrialError("no valid finite-difference neighborhood")
    if not np.all(np.isfinite(matrix)):
        raise TrialError("nonfinite finite-difference Jacobian")
    return matrix


def gradient(function, x, lo=None, hi=None):
    return jacobian(function, x, lo, hi)[0]
