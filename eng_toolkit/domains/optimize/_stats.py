"""Bounded incomplete-beta and Student/F distribution calculations."""

import math


def _fraction(a, b, x):
    tiny = 1e-300
    c = 1.
    d = 1.-(a+b)*x/(a+1.)
    d = 1./(d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 401):
        for term in (m*(b-m)*x/((a+2*m-1)*(a+2*m)),
                     -(a+m)*(a+b+m)*x/((a+2*m)*(a+2*m+1))):
            d = 1.+term*d
            c = 1.+term/c
            d = d if abs(d) > tiny else tiny
            c = c if abs(c) > tiny else tiny
            d = 1./d
            delta = d*c
            h *= delta
        if abs(delta-1.) < 3e-14:
            return h
    raise ValueError("incomplete beta did not converge")


def beta_regularized(x, a, b):
    if not all(math.isfinite(v) for v in (x,a,b)) or a <= 0 or b <= 0 or not 0 <= x <= 1:
        raise ValueError("invalid incomplete beta arguments")
    if x == 0 or x == 1:
        return float(x)
    factor = math.exp(math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)+a*math.log(x)+b*math.log1p(-x))
    value = factor*_fraction(a,b,x)/a if x < (a+1)/(a+b+2) else 1-factor*_fraction(b,a,1-x)/b
    return min(1., max(0., value))


def t_quantile(probability, df):
    if not math.isfinite(df) or df <= 0 or not 0 < probability < 1:
        raise ValueError("Student quantile needs 0<p<1 and positive degrees of freedom")
    if probability == .5:
        return 0.
    sign = 1. if probability > .5 else -1.
    tail = min(probability, 1-probability)
    lo, hi = 0., 1.
    def survival(t):
        return .5*beta_regularized(df/(df+t*t), df/2, .5)
    for _ in range(340):
        if survival(hi) <= tail:
            break
        hi *= 2
    else:
        raise ValueError("Student quantile outside supported range")
    for _ in range(100):
        mid = (lo+hi)/2
        if survival(mid) > tail:
            lo = mid
        else:
            hi = mid
    if hi-lo > 1e-12*max(1.,abs((lo+hi)/2)):
        raise ValueError("Student quantile bisection did not converge")
    return sign*(lo+hi)/2


def f_survival(f, df1, df2):
    if not all(math.isfinite(v) for v in (f,df1,df2)) or f < 0 or df1 <= 0 or df2 <= 0:
        raise ValueError("F survival needs nonnegative statistic and positive degrees of freedom")
    return beta_regularized(df2/(df2+df1*f), df2/2, df1/2)
