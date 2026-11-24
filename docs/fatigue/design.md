# Fatigue domain

The five tools are `fatigue_sn`, `fatigue_rainflow`, `fatigue_strain_life`,
`fatigue_fracture`, and `fatigue_material`. They use NumPy and Pint at runtime.
SciPy is an optional independent test reference, never a runtime engine.
Every result has `review_status: "unreviewed"`. Successful results include the
equations, assumptions, validity limits, units, provenance and a standing warning.

This domain estimates uniaxial material response. It does not implement
multiaxial fatigue, weld S-N classes, thermo-mechanical fatigue, creep-fatigue,
variable-amplitude crack-growth retardation, crack closure, environmental
degradation, NASGRO, probabilistic survival design or component certification.
Naming an ASTM method does not assert compliance with its complete testing standard.

## Input and numerical contract

Physical scalars are strings such as `"600 MPa"`, `"0.1 inch"`,
`"50 ksi sqrt(inch)"`, `"0.005 dimensionless"`, or `"1e6 cycle"`.
They cross `eng_toolkit.units.parse_quantity` before calculation. Decimal literals,
named unit products and quotients, numeric powers and at most three levels of unit
parentheses are accepted. `sqrt(unit)` is shorthand for a unit power of one half.
No caller expression is evaluated as Python. Quantities are limited to 180
characters and unit expressions to 55 tokens. Empty strings omit optional inputs.
Counts in S-N records are dimensionless cycle counts, so they use plain decimal
literals. A scale carrying units is mandatory for numeric history lists.

A fatigue cycle is a pure count with factor one. Pint, however, defines `cycle`
as an alias of `turn` = 2 pi rad, so a naive conversion of `"1e5 dimensionless"`
to cycles divides by 2 pi. The domain therefore never converts counts through
Pint's `cycle`. Count inputs (`cycles`, `knee_cycles`, `reference_life`) accept
exactly the unit spellings `cycle`, `cycles`, `count`, `dimensionless` and `1`,
all meaning the stated number of cycles; a unit is still required, as for every
quantity string. Angular units (`rad`, `deg`, `turn`, `revolution`, prefixed
cycle units such as `kilocycle`) and any other unit are refused with
`units.dimension_mismatch`. In composite units, notably the Paris/Forman
coefficient, every `cycle`/`cycles` word is rewritten to Pint's factor-one
`count` before conversion. The per-cycle factor may be written or omitted
(`m/cycle/(MPa*m^0.5)^3` and `m/(MPa*m^0.5)^3` are the same coefficient), but
it may not appear with any other power, and angular units are refused there too.
Count outputs (`life`, `knee_cycles`, `cycles_to_limit`, `cycles_to_failure`,
`weighted_cycles`) carry the unit `count`, never `cycle`, so a downstream Pint
conversion of the result cannot reintroduce the 2 pi factor. The domain has no
frequency or rotational-speed inputs.

Canonical calculation/output units are MPa, m, N, cycle counts, dimensionless strain,
MPa sqrt(m), hours and kelvin. Quantity outputs state their units; CSV headers
state theirs. Imperial inputs are converted before magnitude checks. Nonzero
values that underflow on parsing or conversion are refused. There is no infinity
sentinel: an endurance plateau or threshold arrest has a null life with an
explicit status, and no unbounded numeric value.

| Quantity | Supported nonzero canonical magnitude |
|---|---|
| Stress or modulus | 1e-12 to 1e7 MPa |
| Length | 1e-12 to 1e4 m |
| Force | 1e-12 to 1e12 N |
| Strain | 1e-15 to 10; individual methods impose narrower limits |
| Cycle quantity | 1e-12 to 1e18 cycles (count, factor one); S-N queries require at least one |
| Larson-Miller parameter | 1e-9 to 1e7 K, in K or degR only |
| Stress intensity | 1e-12 to 1e6 MPa sqrt(m) |
| Time | 1e-12 to 1e12 hours |
| Absolute temperature | above zero through 5000 K |

These are numerical bounds, not material validity claims. A method can impose
stricter limits. Negative mean stresses and history values are allowed; dimensions,
strengths and amplitudes must be positive. All returned numbers are finite and
at most 1e100 in magnitude. Exponentiated positive results are restricted to
1e-100 through 1e100 rather than silently becoming zero. Every refusal has an
error family and a JSON-pointer `details.param`. Magnitude refusals use
`input.out_of_range`; exhausted iterations use `numerical.no_convergence`.

Record lists use semicolons or newlines, comma-separated fields, no headers,
no empty records and no trailing separator. Limits are 2000 records, 600
characters per record and 60000 characters in total. Flat numeric histories
have separate limits of 1000000 points, 24000000 inline characters or 48000000
CSV bytes. Nested JSON, booleans, NaN, Infinity and numeric expressions are refused.
CSV input is copied through `JobHandle.input_file`, size checked and hashed.
The caller's original file is never opened directly by the domain.

Scalar roots use bisection on a bracket in a linear or logarithmic coordinate,
with a relative bracket-width tolerance of 2e-11 and at most 160 iterations.
An absent bracket is a refusal. Strain life brackets log reversals between
log(1) and log(1e18). Growth critical-size roots instead use the requested life
tolerance and the remaining crack interval, as described below. Crack growth
uses adaptive Simpson after the substitution u=log(a/a0), includes its
Jacobian, and checks the requested tolerance. It has a
global 50000-function-evaluation cap and depth cap of 24. Resource caps also
cover histogram cells and output stations.

## Stress life: fatigue_sn

Modes are `fit`, `curve`, `estimate`, `correction`, and `notch`.

`fit` takes records `stress,cycles[,failure|runout]`, for example
`400 MPa,1000;200 MPa,1000000;180 MPa,2000000,runout`.
The default flag is failure. Runouts are listed in the result and excluded
from the fit; they are not treated as failures or as evidence for an endurance
limit. `fit_method` selects the regression direction:

* `e739` (default) follows ASTM E739 and Dowling: life is the dependent
  variable, so centered least squares fits log10(N)=c0+c1 log10(Sa) and the
  result is inverted exactly to Sa=A N^b with b=1/c1 and A=10^(-c0/c1). At
  least two failures at distinct stress levels are needed and c1 must be
  negative. The residual standard error is reported in log10 life as
  `standard_error_log10_life`, with `life_scatter_factor_2se`.
* `s_on_n` fits log10(Sa)=log10(A)+b log10(N), stress dependent, as earlier
  versions did. At least two distinct lives are needed. Its residual standard
  error is `standard_error_log10_stress`, with `stress_scatter_factor_2se`.

The two directions give the same line only for exact power-law data; with
scatter E739 gives a steeper curve in S-N coordinates. Example (tests): failures
400 MPa at 1e3 and 3e3, 300 at 2e4 and 8e4, 250 at 1e5 and 5e5, 200 at 1e6 and
4e6 cycles, plus a runout. E739 gives A=841.17 MPa, b=-.098578 and a life of
2.2156e5 cycles at 250 MPa; s_on_n gives A=780.12 MPa, b=-.091862 and
2.3991e5 cycles. The resulting slope b must be -2 through -1e-5. Outputs also
include R-squared (identical for both directions), the regression slope and
intercept in the stated coordinates, and the descriptive multiplicative band
10^(+/-2 standard errors) in the stated response. Two points have no estimable
residual standard error; those fields are null. The band is not a confidence
interval, tolerance interval or survival probability, and runouts are not
treated with E739's censored-data extensions.

`curve` takes `basquin_a` and `basquin_b`. Both modes support a life-at-amplitude
query and a strength-at-life query. Optional `endurance_limit` and `knee_cycles`
define a horizontal plateau. If one is supplied the other follows from Basquin;
if both are supplied they must agree with the curve within 1e-6 relative.
An endurance limit is never inferred merely from a straight log-log fit.
Fitted cycle bounds are reported; extrapolation is warned. A 61-point curve
CSV spans 1e3 through 1e9 cycles and is explicitly a model curve, including any
extrapolated portion.

`estimate` implements the approximate Shigley/Juvinall steel construction,
a straight log-log line from a 1e3-cycle anchor to the corrected Se at 1e6
cycles. The anchor depends on the load type, following Juvinall's generalized
S-N table: bending 0.9 Su, axial 0.75 Su, and torsion 0.9 Ssu. The shear
ultimate is Shigley's Ssu=0.67 Su unless the caller supplies
`shear_ultimate_strength` (positive, at most Su; torsion estimates only).
The endurance estimate itself is not changed by the anchor: the Marin load
factor kc already distinguishes the load types. For Su=600 MPa the 1e3-cycle
amplitudes are 540, 450 and .9*402=361.8 MPa shear. It requires
400<=Su<=2000 MPa and refuses predicted lives below 1000 cycles.
The unmodified endurance is min(0.5 Su,700 MPa), including the 1400 MPa knee.
The construction uses Se=ka kb kc kd ke Se'. This is an empirical estimate,
not alloy data. Source: Budynas and Nisbett, *Shigley's Mechanical Engineering
Design*, 11th edition, chapter 6, S-N approximation, Marin factors and tables
6-2 and 6-5, and Ssu=0.67 Sut for torsion; Juvinall and Marshek,
*Fundamentals of Machine Component Design*, fatigue chapter, generalized S-N
curves (10^3-cycle strengths 0.9 Su bending, 0.75 Su axial, 0.9 Sus torsion).
Juvinall's own steel shear ultimate (about 0.8 Su) differs from Shigley's 0.67;
the default uses Shigley and the caller may supply a measured value. The fixed
fractions are the preliminary construction, not Shigley's more detailed
strength-dependent fatigue ratio f.

| Finish | ka, with Su in MPa |
|---|---|
| Polished | 1 |
| Ground | 1.58 Su^-0.085 |
| Machined/cold drawn | 4.51 Su^-0.265 |
| Hot rolled | 57.7 Su^-0.718 |
| As forged | 272 Su^-0.995 |

Surface factors are capped at one. The effective round diameter must be
2.79..254 mm for automatic bending/torsion kb: 1.24 d^-0.107 through 51 mm,
then 1.51 d^-0.157. Alternatively supply kb directly, greater than zero and
at most two. Axial kb defaults to one. Load factors are bending 1, axial .85,
and pure shear torsion .59. Torsion amplitudes use a shear basis; they are not
von Mises inputs. In a torsion estimate every strength in the shear state is a
shear strength: Goodman and Gerber use Ssu, Soderberg and the elastic peak
check use the distortion-energy shear yield Ssy=0.577 Sy (refused if it exceeds
Ssu), and both are reported. With Sa=Sm=100 MPa shear and Su=600 MPa, Goodman
gives 100/(1-100/402)=133.11 MPa. Morrow's coefficient is taken as supplied,
so for torsion the caller must give the shear coefficient tau_f'. The caller
supplies kd, greater than zero through two;
no temperature-factor extrapolation is hidden in the tool. The total factor
must keep Se below the 1e3-cycle anchor. A gateway test cross-checks bending
against `machine_shaft`; pure-shear torsion intentionally has a different
stress basis from that tool's von Mises combined-loading calculation.

The reliability table is 50/90/95/99/99.9/99.99 percent with
ke=1/.897/.868/.814/.753/.702. These are the rounded Shigley values, assuming
8 percent endurance scatter. Tests compare them with 1-.08 z using published
normal quantiles 0, 1.2815515655, 1.6448536270, 2.3263478740, 3.0902323062,
3.7190164855 and the standard-library normal inverse CDF. Rounding explains
differences under .0006. No interpolation to an unsupported percentile occurs.

Mean stress methods, also used per rainflow cycle, are:

| Method | Equivalent fully reversed amplitude |
|---|---|
| None | Sa |
| Goodman | Sa/(1-Sm/Su) |
| Gerber | Sa/(1-(Sm/Su)^2) |
| Soderberg | Sa/(1-Sm/Sy) |
| Morrow | Sa/(1-Sm/sigma_f') |
| Smith-Watson-Topper | sqrt(Sa(Sa+Sm)) |
| Walker | Sa^gamma (Sa+Sm)^(1-gamma), 0<=gamma<=1 |

Here Sm is max(signed mean,0): no compressive-mean improvement is claimed.
Any supplied yield strength checks both signed stress peaks. Mean stresses
at or above the relevant denominator strength are refused. Goodman/Gerber
require Su, Soderberg requires Sy, and Morrow requires sigma_f'. Walker's
gamma=.5 is SWT. These are empirical uniaxial elastic criteria; the Morrow
coefficient is a fatigue strength coefficient, not ultimate strength.
Sources: Dowling, *Mechanical Behavior of Materials*, 4th edition, fatigue
chapters; Smith, Watson and Topper, “A stress-strain function for the fatigue
of metals,” *Journal of Materials* 5, 767-778, 1970; Walker, ASTM STP 462,
1970. Gerber's denominator uses (1-r)(1+r) for r=Sm/Su.

Safety is a proportional-load multiplier that scales Sa and Sm together.
This is the one assumption that makes it unsafe to use blindly (judge M3-1, the bolt trap): a preloaded bolt keeps its
mean stress (the preload) fixed and only the alternating part grows with the load, so the factor from a proportional
load line through the origin is several times too low (a bolt with Sa 3.08 MPa, Sm 447 MPa, Se 129 MPa, Su 800 MPa
gets 1.7 from the tool against about 12 from the preloaded-bolt form n = Se (Su - Si)/(Su Sa + Se (Sm - Si)) with Si the
preload stress). The tool does not refuse and does not model the constant-preload load line. Since round 3 the tool
description, `data.assumptions` and a warning (always in `correction` mode, in the other modes whenever a positive mean
is given with an amplitude and a safety factor is computed) say that the factor assumes proportional loading and is NOT
valid where the mean stays fixed; the warning adds the mean/amplitude ratio when the mean is more than 20 times the
amplitude (the preloaded-joint pattern). A `correction`-mode refusal now names the offending argument (`/cycles`,
`/points`, `/basquin_a`, `/knee_cycles`) instead of `/mode`.
For Goodman it is 1/(Sa/Sf+Sm/Su); Gerber solves
n Sa/Sf+(n Sm/Su)^2=1 using the stable positive root
2/(A+hypot(A,2B)). Soderberg and Morrow substitute their limiting strengths.
For SWT/Walker, n=Sf/equivalent amplitude. Sf is the curve strength at the
requested life, otherwise the endurance limit. Correction-only mode uses a
supplied endurance amplitude for Sf. This multiplier does not supersede
a separate static-yield check.

Notch options are Peterson q=1/(1+a/r) and Neuber q=1/(1+sqrt(a_N/r)), with
Kf=1+q(Kt-1). A caller's measured material length can be used at any strength.
Otherwise the automatic steel bending/axial estimates have method-specific ranges:

* Peterson a[mm]=.0254(2070/Su[MPa])^1.8, the commonly used rounded SI
  Peterson correlation, requires Su=550..1500 MPa. Dowling, *Mechanical
  Behavior of Materials*, notch-sensitivity section, states this steel fit
  (imperial form a=.001(300 ksi/Su)^1.8 in) for Su>=550 MPa (80 ksi); weaker
  steels are refused rather than extrapolated. Source: Pilkey and Pilkey,
  *Peterson's Stress Concentration Factors*, 3rd edition, fatigue notch
  sensitivity; the 2070 MPa rounded convention is also documented in the nCode
  DesignLife Theory Guide, “Notch sensitivity.” It is not the distinct 2079 MPa
  convention. The 1500 MPa upper limit is a conservative policy.
* Neuber sqrt(a_N)[sqrt(mm)]=1.24-.00225 Su+1.60e-6 Su^2-4.11e-10 Su^3 requires
  Su=345..1500 MPa. Source: Shigley, chapter 6, steel bending/axial Neuber
  constant fit, stated for 50..250 kpsi (340..1700 MPa); its kpsi form
  sqrt(a)=.246-3.08e-3 Su+1.51e-5 Su^2-2.67e-8 Su^3 [sqrt(in)] is the same
  fit. It is a Neuber constant, not a Peterson a, so it is never substituted
  for the Peterson method.

For torsion, `load_type="torsion"` selects Shigley's separate Neuber fit,
sqrt(a_s)[sqrt(in)]=.190-.00251 Su+.0000135 Su^2-.0000000267 Su^3, with
Su in ksi. The implementation converts tensile Su from MPa to ksi and squares
the resulting constant before converting inches to meters. It uses the same
conservative Su=345..1500 MPa range. Source: Shigley, chapter 6, torsional
notch-sensitivity correlation, equation 6-35; equation 6-34 is the bending/axial
fit. The load type selects the automatic correlation in both `estimate` and
`notch` modes. A measured `notch_material_length` overrides either fit.
Automatic Peterson torsion is not supplied; torsion with `notch_method="peterson"`
requires a measured length and otherwise returns `input.missing` at
`/notch_material_length`.

For Su=600 MPa, r=.1 mm and Kt=3, the torsional fit gives q=.5274613644 and
Kfs=2.054922729. With the default polished surface, kb=1 and a nominal shear
amplitude of 100 MPa, the estimated life is about 236422.49 cycles. The prior
bending/axial substitution gave q=.4560198438 and about 474345.98 cycles.

The fatigue amplitude is multiplied by Kf, the mean by Kt. The elastic yield
screen uses Kt(Sa+abs(Sm)), not Kf. Kt must lie in 1..100. Notch geometry
charts are not included.

## Cycle counting: fatigue_rainflow

Supply either `history` as a flat JSON number list or `history_file` as a
numeric UTF-8 CSV. CSV has one sample column or two columns
`time,value`, with an optional first line of column names (every field a name such as
`time_s,strain_ue`, never a number or code): it is skipped when the next line is numeric
and reported as `data.header_skipped`. Any other non-numeric content is refused with
`line N` in the message. Time must increase, but constant sampling intervals are not
needed for cycle counting. `sample_scale` supplies physical units, for example
`1 MPa` or `1 ksi`. `history_kind="force"` permits force counting with a scale
such as `1 kN`, but refuses fatigue damage until the caller converts loads to
stress. At least two nonconstant samples are required.

Inline numeric overflow, nonzero underflow, NaN and Infinity return
`input.out_of_range` at `/history`. Deliberate numeric-parser refusals retain
their family; only malformed JSON is reported as `input.invalid`.

Reversal extraction removes duplicate values and monotonic interior points.
An optional directional hysteresis gate suppresses interior excursions smaller
than the threshold, while retaining endpoints. It is a preprocessing choice,
not a modification of the ASTM counting rule. Its range identities apply to
the filtered history. Endpoint excursions smaller than the gate can remain.
Until the first excursion reaches the gate, the running minimum and maximum are
both tracked; when their spread reaches the gate, the older of the two becomes
the first reversal (unless it is the first sample) and the newer is the current
extreme. So `[5,3.5,10]` with a 2 MPa gate keeps the 3.5 valley and counts the
true 6.5 range, plus the retained 1.5 endpoint half cycle. Equivalently, a
retained interior reversal is a peak (valley) from which the signal falls
(rises) by at least the gate on both sides before exceeding it, where one side
may instead be the record boundary if the other side has such an excursion.
A randomized test compares the gate with a brute-force implementation of that
definition.

The implementation follows the three-point range comparison in
[ASTM E1049-85, section 5.4.4](https://store.astm.org/e1049-85r23.html).
A closed interior range counts one full cycle. A range containing the starting
point counts one half cycle, and adjacent pairs in the final residue each
count one half cycle. Equal successive ranges close the older range.
`closed=true` means a periodic block: close the history, rotate it to a global
maximum, count, and pair identical residue halves into full cycles, consistent
with section 5.4.5. Open-history repetitions do not implicitly close a history.
The `repeats` multiplier applies to the selected block convention.

`rainflow_cycles.csv` gives range, mean, full-cycle occurrences, half-cycle
occurrences, weighted count, life and damage. Full and half occurrences remain
separate; weighted count is full+half/2. Results include a 50-row preview, a
range histogram, and a range-mean matrix. Range bins are 1..256 and mean bins
1..128. Damage uses the exact counted cycles before binning. For a closed
unfiltered history, sum(range*count)=total variation/2. Randomized tests check
this identity, cyclic-permutation invariance and an independent circular
four-point reference counter.

Plausibility checks of the record (round 3, judges M2-3 and M2-14). `data.history_min`, `data.history_max`, `data.history_range`
(unit in `data.history_unit`; the history as given, before `closed` rotates it) and `data.history_extremes` (value and 0-based
sample index of the minimum and maximum, plus the time of a two-column file with `time_column`) are always returned; `values.history_max`
and `values.history_min` repeat them with units. `data.spikes` lists isolated single-sample spikes: an interior sample more than 6 robust sigmas from
BOTH neighbours, on the same side of both (a one-sample excursion), where sigma = 1.4826 x the median absolute sample-to-sample step (the
standard deviation of a step for white noise; floored at 1e-6 of the range so a held record is not all spikes). At most 5 are listed
(largest first: index, time if there is one, value, neighbours, size in sigmas) with the total in `count`, and a warning names them;
records below 5 samples are not judged. A turning point of a smooth or triangular record is not a spike (its step is as large as the
typical step). With damage, `data.dominant_cycle` names the single cycle with the largest damage (range, mean, 0-based row of
`rainflow_cycles.csv`, number of occurrences in that row, its share of the total damage and the share of the whole row, which is the
class of identical cycles) and a warning appears when one cycle carries more than 50 % of the damage (and the record has more than one
row). A glitch usually shows as two flanks of similar size, so the larger flank can land just above or below 50 %: the spike
list is the reliable detector, the dominance warning the consequence. With `yield_strength` the existing refusal of a cycle whose peak
exceeds yield stays: it is the strongest spike detector.

The E1049 Fig. 6 history is `[-2,1,-3,5,-1,3,-4,4,-2]`. Its range/mean/count
tuples are (3,-.5,.5), (4,-1,.5), (4,1,1), (8,1,.5), (9,.5,.5), (8,0,.5),
(6,1,.5). Total weighted count is 4 and sum(range*count)=23. The published
figure and table X1.3 are available in the
[ASTM preview](https://cdn.standards.iteh.ai/samples/116808/c47ecd7e82134f898d91e789c6c0915b/ASTM-E1049-85-2023-.pdf).

For damage, provide A,b or exactly two failure points. The common S-N
mean-stress correction is applied to each counted cycle. Palmgren-Miner damage
is D=sum(n_i/N_i); total damage is repeats*D and block repeats to failure is 1/D.
Sources: Miner, “Cumulative damage in fatigue,” *Journal of Applied Mechanics*
12, A159-A164, 1945; Bannantine, Comer and Handrock, *Fundamentals of Metal
Fatigue Analysis*, 1990, variable-amplitude loading chapters.

For a supplied endurance knee, `below_knee` chooses `none`, the default
horizontal cutoff; `elementary`, extending m=-1/b unchanged; or `haibach`,
using 2m-1 below the knee. Haibach uses N=Nk(Se/Sa)^(2m-1).
Source: Haibach, *Betriebsfestigkeit*, 3rd edition, 2006, modified S-N curve
for variable-amplitude fatigue. Rainflow damage supports -1<=b<-1e-5, that is
m>=1, so Haibach's 2m-1 is at least the elementary m and remains positive; for
m<1 it would be smaller than m, inverting the intended conservatism. The limit
applies identically to a direct `basquin_b` and to the slope implied by
`sn_points` (refused at `/sn_points`), with or without a knee. Zero damage has
no finite repeat count.

`reference_life` asks for the fully reversed constant-amplitude range that,
applied for Nref cycles, creates the same damage as one block. Without a knee,
the answer is 2A(Nref/D)^b. The same piecewise slope is inverted with a knee.
A positive-damage request landing on an ideal horizontal plateau is refused;
zero damage has no unique equivalent range. This reference-life definition
does not assert equal energy or equal crack-growth damage.

## Local strain life: fatigue_strain_life

Modes are `life`, `cyclic`, `notch`, `block`, and `estimate`. Inputs use total
strain amplitude and stress amplitude, unless notch `range_input=true`.
The cyclic Ramberg-Osgood relation is
eps_a=sigma_a/E+(sigma_a/K')^(1/n'), with measured K', n' preferred.
The Coffin-Manson-Basquin equation is
eps_a=(sigma_f'/E)(2N)^b+eps_f'(2N)^c. Require c<b<0, .02<=n'<=1,
and local amplitude <=.1. The transition is
2Nt=(eps_f' E/sigma_f')^(1/(b-c)).

Morrow subtracts max(mean stress,0) from sigma_f' in the elastic term;
mean>=sigma_f' is refused. SWT solves
sigma_max eps_a E=sigma_f'^2(2N)^(2b)+sigma_f' eps_f' E(2N)^(b+c).
Supply local sigma_max or infer local amplitude from the cyclic curve and
add the positive part of prescribed local mean. The positive terms are summed
in log space, avoiding overflowing or underflowing intermediate powers.
Reported life is N=half the solved reversal count. References: Dowling,
strain-life and cyclic stress-strain chapters; Bannantine-Comer-Handrock,
local strain approach; Smith-Watson-Topper, 1970.

When K',n' are omitted but CMB constants exist, the compatible-curve estimate
n'=b/c, K'=sigma_f'/eps_f'^n' follows by eliminating 2N between the elastic
stress and plastic-strain power laws. It is labeled an inference; independently
measured cyclic and fatigue constants need not satisfy it exactly.

Monotonic estimation requires Su/E in .001.. .02 and reduction of area RA in
.01.. .95, used as D=-ln(1-RA). These are conservative supported input bounds,
not a claim that every metal inside them obeys the correlation. Neither method
is used automatically without `estimate_method`.

* Manson Universal Slopes starts with the original strain-range/cycle form
  Delta eps=3.5(Su/E)N^-.12+D^.6 N^-.6. The exact convention conversion gives
  sigma_f'=1.75*2^.12 Su, eps_f'=.5*2^.6 D^.6, b=-.12 and c=-.6. It avoids
  mixing the often-rounded 1.9/.76 coefficients with a different life convention.
  Source: Manson and Hirschberg; NASA's
  [historical account and original equation](https://ntrs.nasa.gov/api/citations/19830003877/downloads/19830003877.pdf), figure 7.
* Modified Universal Slopes uses sigma_f'=.623 E(Su/E)^.832,
  eps_f'=.0196 D^.155(Su/E)^-.53, b=-.09 and c=-.56, in amplitude/reversal
  convention. Source: Muralidharan and Manson, “A Modified Universal Slopes
  Equation for Estimation of Fatigue Characteristics of Metals,” *Journal of
  Engineering Materials and Technology* 110, 55-58, 1988,
  [DOI 10.1115/1.3226010](https://doi.org/10.1115/1.3226010).

These estimates concern ductile steels, aluminum and titanium alloys below the
creep regime. They are rough, with factor-of-three life discrepancies plausible;
that caution is not a statistical coverage promise. No brittle/cast-iron or
cryogenic variant is supplied.

For an elastic nominal amplitude S and Kt, Neuber solves
sigma eps=(Kt S)^2/E simultaneously with cyclic Ramberg-Osgood.
Glinka equates the elastic energy density to
sigma^2/(2E)+sigma(sigma/K')^(1/n')/(n'+1).
Sources: Neuber, *Journal of Applied Mechanics* 28, 544-550, 1961;
Glinka, “Energy density approach to calculation of inelastic strain-stress near
notches and cracks,” *Engineering Fracture Mechanics* 22, 485-508, 1985,
[DOI 10.1016/0013-7944(85)90148-1](https://doi.org/10.1016/0013-7944(85)90148-1).
These scalar notch rules assume a localized plastic zone with an elastic
surrounding body. General yielding and three-dimensional constraint require
another model. The tool permits the caller to use an effective Kf in place
of Kt but does not claim they are physically interchangeable.

For a stress range, the doubled Masing curve is
Delta eps=Delta sigma/E+2(Delta sigma/(2K'))^(1/n'). The tool solves on half
the nominal range and doubles the resulting local amplitudes. `block` takes
up to 2000 nominal stress levels, such as `100 MPa;-100 MPa;100 MPa`, rainflow
counts them with open residue, solves each local range, and sums count/N.
The CSV reports nominal means but does not infer local mean relaxation or
residual-stress history. All cycles use the caller's prescribed stabilized
local mean stress. This is an independent-range damage summary, not a
constitutive loading-history simulation.

## Linear elastic fracture: fatigue_fracture

Modes are `evaluate`, `critical`, and `growth`. Plate inputs use remote maximum
tensile stress; SENB and CT inputs use maximum force. Growth can instead take
a range and R, with maximum=range/(1-R). Require 0<=R<=.95; compression and
closure are out of scope. A central crack has total length 2a. W is always
the full plate width, not its half-width. All other a values are crack depths.

The following handbook approximations implement K=Y sigma sqrt(pi a).
Sources: Tada, Paris and Irwin, *The Stress Analysis of Cracks Handbook*,
3rd edition, 2000, center- and edge-cracked tension plates; Anderson,
*Fracture Mechanics: Fundamentals and Applications*, 4th edition, 2017,
Mode-I stress-intensity solutions. The numerical limits below are enforced.

| Geometry | Factor and limit |
|---|---|
| Infinite center through crack | Y=1, exact ideal infinite-plate solution |
| Finite center, Feddersen/Isida | Y=sqrt(sec(pi a/W)), a/W<=.45 |
| Finite center, Tada correction | Y=(1-.025 z^2+.06 z^4)sqrt(sec(pi z/2)), z=2a/W<=.9 |
| Small single edge | Y=1.12, a/W<=.05 |
| Single edge, Tada | Y=1.122-.231 x+10.55 x^2-21.71 x^3+30.382 x^4, x=a/W<=.6 |
| Symmetric double edge | Y=(1.122-.561 z-.205 z^2+.471 z^3-.190 z^4)/sqrt(1-z), z=2a/W<=.8 |

For SENB, K=P S f/(B W^1.5) and
f=3 sqrt(x)[1.99-x(1-x)(2.15-3.93x+2.7x^2)]/[2(1+2x)(1-x)^1.5].
S/W must equal four. For CT, K=P f/(B sqrt(W)) and
f=(2+x)(.886+4.64x-13.32x^2+14.72x^3-5.6x^4)/(1-x)^1.5.
Both permit .2<=x=a/W<=.8 as handbook K calculations. This wider formula
range does not certify an E399 specimen, whose dimensional and test-validity
requirements are separate. At x=.5, SENB f=2.6625 and CT f=9.659078631.
Source: [ASTM E399](https://store.astm.org/standards/e399), bend and compact
specimen calibrations; Anderson, specimen stress-intensity solutions.

### Newman-Raju surface tension

For an elliptical surface crack define r=a/c, d=a/t, and full width W=2b.
The 1981 tension expressions are:

```
Q = 1 + 1.464 r^1.65
M1 = 1.13 - .09 r
M2 = -.54 + .89/(.2+r)
M3 = .5 - 1/(.65+r) + 14(1-r)^24
fw = sqrt(sec(pi*c/W*sqrt(d)))
f_phi = (r^2*cos(phi)^2 + sin(phi)^2)^.25
g = 1 + (.1+.35*d^2)*(1-sin(phi))^2
F = (M1+M2*d^2+M3*d^4)*fw*f_phi*g
K = sigma*sqrt(pi*a/Q)*F
```

The tool evaluates phi=0, the surface point, and phi=pi/2, the deepest point.
Its returned `geometry_factor` is Y=F/sqrt(Q), so it multiplies
sigma sqrt(pi a), unlike the tabulated F normalization. Source:
[Newman and Raju, 1981, DOI 10.1016/0013-7944(81)90116-8](https://ntrs.nasa.gov/citations/19820030778);
[NASA TP-1578](https://ntrs.nasa.gov/api/citations/19800005257/downloads/19800005257.pdf),
equation development and finite-element Table I, printed page 20. The related
[NASA TM-83200](https://ntrs.nasa.gov/api/citations/19810023035/downloads/19810023035.pdf)
provides the broader family of finite-body crack equations.

Supported r is 1e-4..1, d<=.8 and 2c/W<.5. Values d>.8 are refused because
the reported accuracy is not established there, even though the algebraic
equation is defined for d<1. Values r<.2 carry a warning about the limiting
extension. Long-plate, remote uniform tension and isolated crack assumptions
apply. Bending, interacting cracks and finite plate-height corrections are
not implemented. The four Table I checks at r=1 compare against the published
F values within the equation's 5% approximation tolerance, not machine precision.
The selected `surface_point` controls critical-size and growth calculations;
both endpoint K values are reported for initial assessment. Growing surface
cracks preserve a/c. This does not predict front-shape evolution or determine
which crack-front location physically initiates fracture.

### Validity and growth

Irwin's first-order plastic-zone radius is reported as `ry_plane_stress`
=(K/Sy)^2/(2 pi) and `ry_plane_strain`=(K/Sy)^2/(6 pi). It is the distance
from the crack tip to the elastic-plastic boundary on the crack plane, half the
corrected zone size. The plastic zone size, its full extent ahead of the tip
measured along the crack plane, is `rp_plane_stress`=2 ry=(K/Sy)^2/pi and
`rp_plane_strain`=(K/Sy)^2/(3 pi). Sources: Anderson, Irwin approach (r_y and
r_p=2r_y); Dowling, plastic zone 2r_o. For K=34.557 MPa sqrt(m) and Sy=500 MPa
these are r_y=.7602 mm and r_p=1.5205 mm in plane stress. Earlier versions
labelled r_y as the plastic zone. Supplied KIc receives the size screen
B,a>=2.5(KIc/Sy)^2; SENB/CT additionally check remaining ligament. A missing
thickness or failed screen is reported and warned, without claiming full E399
validity.

The peak elastic net-section stress (membrane plus bending) above .8 supplied
yield is refused, including at the growth endpoint. This .8 factor is a
conservative screening policy, not an ASTM acceptance criterion.
`net_section_stress` is that peak; `net_section_membrane_stress` is the
resultant divided by the net area. The plate solutions assume ends free to
rotate (as the single-edge handbook factor does), so the remote resultant
acts at the gross centroid:

* Central and symmetric double-edge cracks are symmetric: no bending, peak
  = membrane = sigma/(1-2a/W).
* Single-edge (both `edge` and `edge_small`): the ligament W-a carries
  M=sigma W B a/2 in addition to the force, so the extreme-fibre peak is
  sigma W/(W-a) (1+3a/(W-a)). For W=100 mm, a=50 mm and sigma=120 MPa the
  membrane value is 240 MPa but the peak is 960 MPa; with Sy=400 MPa it is
  refused. Earlier versions screened only the 240 MPa membrane value.
* Surface crack: the gross section W t minus the semi-ellipse of area pi a c/2
  with centroid 4a/(3 pi) below the cracked face. The net centroid moves away
  from the cracked face, and the peak at that face is
  P/A_n+P e y_n/I_n with P=sigma W t, e the centroid shift and I_n the net
  second moment. A test checks it by strip integration. Restrained-bending
  plates would be less severe; the free-end screen is the conservative choice.
* SENB uses 3PS/[2B(W-a)^2] (pure bending). CT uses
  P/[B(W-a)]+3P(W+a)/[B(W-a)^2]=2P(2W+a)/[B(W-a)^2].

Missing yield strength is explicitly warned, so the tool does not silently
claim small-scale yielding was checked.

Critical size solves K(a)=Kc within the geometry bounds. A root beyond them
is refused. Growth integrates from initial a to an explicit limit, or to
critical a if the limit is omitted. An explicit limit is shortened if Kmax
instability occurs first. A finite limit receives `cycles_to_limit`; only an
instability endpoint receives `cycles_to_failure`. The CSV has 2..101
log-spaced crack stations and cumulative cycles.

For growth, constant-Y critical sizes use a_c=(Kc/(Y sigma_max))^2/pi.
Variable geometries use a shrinking bisection bracket in crack length, with
at most 160 iterations. The endpoint error must be at most one eighth of
`integration_tolerance` times the remaining interval from a0 to the lower
root bound. A 64-ulp crack-length allowance guards against floating-point
roundoff, including a computed zero residual. K increases over the supported
geometry intervals, so dN/da decreases for both growth laws. Consequently,
endpoint life error divided by life is bounded by endpoint length error
divided by that remaining interval. This avoids accepting a small error
relative to a_c when it is large relative to a_c-a0.

If the root bracket or floating-point resolution cannot meet that budget,
growth returns `numerical.no_convergence` at `/integration_tolerance`.
For example, a0=1 mm, sigma_max=100 MPa, Kc=5.6049912166781786 MPa sqrt(m),
C=1e-20 m/(MPa sqrt(m))^3 and tolerance=1e-10 are refused. A 70-digit
closed-form calculation gives a_c=.0010000000001000001213 m and
56790.5033 cycles; the remaining interval is too small for double precision
to verify the requested relative life accuracy. The former result of
33300.5133 cycles integrated to an inaccurate critical endpoint.

Integration uses half the requested tolerance. Its coordinate endpoint is
log1p((a_f-a0)/a0), avoiding subtraction of nearly equal logarithms. Only an
endpoint obtained from the instability solve receives `cycles_to_failure`;
an explicit limit below instability remains `specified_crack_limit`.

Paris uses da/dN=C DeltaK^m. Forman divides that rate by
(1-R)Kc-DeltaK and requires toughness. Geometry is recomputed at each
integration evaluation. Threshold arrest at the initial crack returns no
finite failure life; a threshold crossing during integration is refused.
Source: Paris and Erdogan, “A critical analysis of crack propagation laws,”
*Journal of Basic Engineering* 85, 528-533, 1963;
Forman, Kearney and Engle, “Numerical analysis of crack propagation in cyclic-loaded
structures,” *Journal of Basic Engineering* 89, 459-464, 1967;
[ASTM E647](https://store.astm.org/standards/e647), fatigue-crack-growth
measurement method. Paris is a calibrated intermediate-regime model;
Forman is an empirical extension toward instability, not a universal material law.

Require .5<=m<=8 and explicit composite coefficient units:

* Paris, m=3: `1e-12 m/cycle/(MPa*m^0.5)^3`.
* Forman, m=3: `1e-10 m/cycle/(MPa*m^0.5)^2` because its denominator adds
  one stress-intensity dimension.
* Imperial Paris, m=3: `1e-8 inch/cycle/(ksi sqrt(inch))^3`.

The numeric exponent in the coefficient units must match the chosen law and
m. The target exponent retains full floating-point precision, and dimensions
are compared as Pint dimensionality objects without display-format conversion.
For m=3.123456789, Paris units raised to 3.123456789 are accepted; a rounded
3.12346 is refused with `units.dimension_mismatch` at `/growth_c`. Forman
uses m-1 with the same validation. Internally C uses m per cycle (a count, factor one) with DeltaK in
MPa sqrt(m); converting to SI does not mistake a number calibrated in
ksi sqrt(in) for the same number in MPa sqrt(m). The `/cycle` factor is optional
and never converted as an angle: `1e-12 m/(MPa*m^0.5)^3`,
`1e-12 m/cycle/(MPa*m^0.5)^3` and `.../count/...` all give 7,766,344 cycles
for an infinite plate, a0=1 mm, af=10 mm, Delta sigma=100 MPa (before the fix
the first gave 1,236,052, a 2 pi error). Tests check these conversions,
including the imperial `inch/cycle` form with and without `/cycle`. No sensitivity of life to C or m is
claimed. Load sequences, overload retardation and variable R are not modeled.

For constant Y, tests use the independent closed-form integral
N=[af^(1-m/2)-a0^(1-m/2)]/[C(Y Delta_sigma sqrt(pi))^m(1-m/2)], and its
logarithmic limit for m=2. Variable-geometry tests compare adaptive integration
with SciPy quadrature and an independent log-spaced RK4 accumulation.

## Material reductions: fatigue_material

Modes are `convert`, `fit`, `yield`, `hardness`, `larson_miller`, and `typical`.

Conversion uses true stress=s(1+e), true strain=log1p(e), with inverse
engineering strain=expm1(true strain). `uniform_strain` is always an engineering
strain limit, including inverse conversion. Exceeding it is refused because
the uniform-section assumption fails after necking. Source: Dowling, tensile
behavior and true/engineering stress-strain relations. Compression and
post-necking area reconstruction are not offered.

Fit records are `stress,strain`. `hollomon` expects true plastic strain and
fits sigma=K eps_pl^n. `ramberg_osgood` expects true total strain and subtracts
sigma/E using caller-supplied E before the same log-log fit. Require positive
stress/plastic strain and at least one decade of plastic strain. Unresolved
elastic subtraction is refused instead of fitting numerical noise. Outputs
include K,n and descriptive log-scatter statistics of log10 stress on log10
plastic strain (`standard_error_log10`, `scatter_factor_2se`, in log10 stress).
Sources: Hollomon, “Tensile deformation,” *Transactions AIME* 162, 268-290,
1945; Ramberg and Osgood, *Description of stress-strain curves by three
parameters*, NACA TN 902, 1943; Dowling, power-law hardening. The fitted form
is the usual power-law plastic-strain parameterization.

Yield records use engineering stress and strain, begin at the origin and have
strictly increasing strain. At least three points up to `elastic_fit_max`,
default .001, fit the initial modulus and intercept. The fitted range is
reported; R-squared must be at least .999 and intercept within 2% of its largest
stress. The first table intersection with the .002-strain offset line is
linearly interpolated. A table not reaching that crossing is refused.
Source: [ASTM E8/E8M](https://store.astm.org/standards/e8), offset yield method;
the numerical fit window and linearity tolerances are implementation choices.
Resilience is Sy^2/(2E). Composite trapezoids give work per initial volume;
the output is called toughness only when the caller states the table reaches
fracture. No curve tail or fracture endpoint is invented.

Hardness supports only the steel correlation Su[MPa] approximately 3.45 HB
and its inverse for HB=100..400. Source: Shigley, chapter 2, hardness-strength
correlation. The displayed +/-10% band is explicitly an illustrative screening
band, not a sourced accuracy guarantee. No HV, HRC or other ASTM E140 hardness
conversion table is supplied because a sufficiently broad authenticated table
was not reproduced.

Larson-Miller uses P=T[K](C+log10(t[hours])), or its inverse for time. Absolute
temperature T goes through Pint, including celsius/degree_Celsius and
degree_Fahrenheit aliases. Delta-temperature units and temperatures at or below
absolute zero are refused. The parameter P is a scaled quantity (an absolute
temperature times a dimensionless factor), so an offset conversion is
meaningless: `larson_parameter` accepts only K and degR, and degC, celsius,
degF, fahrenheit and delta units are refused. For example `32008.41 degR` is
17782.45 K and gives 1000 h at 500 degC, while `32008.41 degF` would have
been converted as a temperature to 18037.8 K (2139 h). The output P is in K;
convert it only to degR (factor 9/5), never to degC or degF. Source: Larson and Miller, “A time-temperature
relationship for rupture and creep stresses,” *Transactions ASME* 74, 765-775,
1952. Default C=20 is a preliminary convention requiring calibration at the
same material, stress and mechanism. The tool computes the parameter; it does
not derive a creep rupture strength or a safe operating life.

### Small typical-property table

`typical` returns eight entries labeled **typical, not guaranteed, not for
design**. The values never populate another tool automatically. Each entry
contains condition, units, property spans, source and interpretation. Spans
cover specified source conditions or reference product values; they are not
statistical ranges. Equal endpoints mean a nominal datum, not zero uncertainty.
Published product minima are identified as reference minima and are not passed
off as measured population means. This distinction matters for the broad
casting heat-treatment spans below.

| Material and source condition | E, GPa | nu | Density, kg/m3 | Yield, MPa | Ultimate, MPa | Elongation, % |
|---|---:|---:|---:|---:|---:|---:|
| 1020 investment cast, annealed to normalized/drawn | 207 | .29 | 7850 | 258.55..310.26 | 396.45..448.16 | 20..28 |
| 1045 investment cast, annealed to quenched/drawn | 207 | .29 | 7850 | 275.79..655.00 | 517.11..965.27 | 7..20 |
| 4140 investment cast, annealed to quenched/tempered | 207 | .29 | 7850 | 413.69..1378.95 | 620.53..1516.85 | 4..17 |
| 304 annealed flat products | 200 | .30 | 7900 | 210..230 | 520..750 | 45 reference minimum |
| 6061-T6/T6511 extrusions <=25 mm | 69 | .33 | 2700 | 240 reference minimum | 260 reference minimum | 8..10 reference minima |
| 7075-T6/T651 typical sheet/plate | 71 | .33 | 2800 | 503 nominal | 572 nominal | 11 nominal |
| Ti-6Al-4V, annealed through solution treated/aged | 107..122 | .31 | 4420 | 827..1068 | 895..1137 | 10 reference minimum |
| Grey iron, Dura-Bar G2 | 124 nominal | unavailable | 7060..7340 | not applicable | 275.79 reference minimum | not applicable |

The three cast-steel strength/elongation spans come directly from
[Acracast's alloy table](https://www.acracast.com/alloy-properties.html),
with ksi converted to MPa. They must not be used as wrought-bar properties.
Generic steel/aluminum physical constants and nominal Poisson ratios are
rounded handbook values from Shigley Table A-5 and ASM *Metals Handbook*,
Desk Edition, physical properties of engineering alloys; they are not lot data.
The other mechanical-property sources are
[Outokumpu Core range](https://www.outokumpu.com/en/products/product-ranges/core),
[Hydro 6061](https://www.hydro.com/Document/Index?id=560718&name=Alloy+6061.pdf),
[Kaiser 7075](https://online.kaiseraluminum.com/depot/PublicProductInformation/Document/1017/Kaiser_Aluminum_7075_Sheet_Coil_and_Plate.pdf),
[TIMET TMC-0150 Tables 2-3](https://www.timet.com/documents/datasheets/alpha-and-beta-alloys/timetal-6-4.pdf),
and [Dura-Bar G2](https://www.dura-bar.com/products/gray-iron/g2).
Grey iron's conventional tensile yield, elongation and an authenticated
Poisson ratio are deliberately null rather than invented. The G2 physical
reference is the manufacturer's supplied data, also reproduced in its
[material datasheet listing](https://apm.matweb.com/search/DataSheet.aspx?MatGUID=05f0795918314796a689d72341c96e90).

## Verification and remaining limits

Tests exercise the real tool functions and the FastMCP gateway. Gateway checks
list all five tools, require compact descriptions and reject duplicate parameter
descriptions. They test bare/incorrect units, nonfinite/absurd magnitudes,
post-conversion underflow, missing/conflicting data, invalid geometries, caps,
and 6000-character hostile inputs under one second after dispatch warm-up.
Every tested result is serialized with `allow_nan=False`; errors must carry
a pointer and cannot use `engine.failure`.

Hand-derived checks include A=800 MPa through (400 MPa,1000) and
(200 MPa,1e6), Goodman 100/(1-.2)=125 MPa, Gerber 100/.96 MPa,
SWT sqrt(20000) MPa, the E1049 counts, CMB 10000 reversals=5000 cycles,
the Neuber stress-strain product, K=17.72453851 MPa sqrt(m) for a 10 mm
infinite-plate crack under 100 MPa, the CT/SENB factors, Paris closed forms,
0.2% yield=311.111111 MPa from the test table, and Larson-Miller
P=773.15*(20+3)=17782.45 K. `tests/test_fatigue_review_fixes.py` adds the
review regressions: count spellings (316.228 MPa at 1e5 cycles, 46.883 MPa
equivalent range, 7,766,344 Paris cycles), torsion anchors and shear Goodman
133.11 MPa, the E739 fit, the 960 MPa single-edge peak, surface-section strip
integration, Larson-Miller units, the rainflow slope limit, the gate reference,
r_p=2 r_y and the Peterson range. Each numerical test states its arithmetic and tolerance.

`tests/test_fatigue_review2.py` exercises the second review's findings through
the in-process gateway. It checks the torsional Neuber constant and life with
Decimal arithmetic, measured notch overrides, full-precision growth exponents,
and rainflow error families. Critical-size regressions check both Paris and
Forman against 70-digit antiderivatives, a Decimal edge-crack root with optional
SciPy quadrature, and explicit refusals when the remaining interval cannot
meet the requested accuracy.

Optional SciPy comparisons use `pytest.importorskip` for fitted slopes,
strain-life/notch roots and variable-geometry Paris/Forman integration.
Independent periodic four-point counting and log-spaced integration provide
additional routes. Root and integration work-cap tests verify refusals.
Numerical agreement does not validate caller material properties or the
empirical methods for a particular component. No experimental component-life
validation, material certification or exhaustive calibration across alloys
was performed. Handbook correlations remain empirical even when their
implementation matches arithmetic exactly.

On PowerShell, expand the test glob before passing it to Python:

```powershell
.venv/Scripts/python.exe -m pytest -q (Get-ChildItem tests/test_fatigue_*.py | ForEach-Object { $_.FullName })
.venv/Scripts/python.exe -m eng_toolkit doctor
```

The tests and CLI preserve the dispatch environment, including `ENG_TOOLKIT_WS`,
temporary directories and `PYTEST_ADDOPTS`. No dependency synchronization or
git-writing commands are needed.
