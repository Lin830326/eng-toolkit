# Electrical machines, power and drives

`electric` provides six steady-state tools. It uses NumPy, Pint and Python's
standard library. No tool starts an external simulator, evaluates a supplied
expression, calls an LLM or imports another domain's private modules. Discovery
uses `DOMAIN` in `eng_toolkit/domains/electric/__init__.py`.

All results use the shared contract and carry `review_status: "unreviewed"`.
Successful results include formulas, assumptions, validity and the standard
warning. Physical values in `values` have explicit units. CSV headers identify
units. Undefined efficiency, torque at zero speed with constant power loss, and
unbounded ratios are omitted or represented by null/empty CSV fields.

## Sources and method names

These are references for methods, not copied examples or calibration data. Tests
derive their expected numbers from arithmetic recorded beside each assertion.

| Reference | Methods used |
| --- | --- |
| Stephen J. Chapman, *Electric Machinery Fundamentals*, transformer, DC machine and induction-machine chapters | OC/SC test reduction, transformer regulation/efficiency, DC voltage equation, induction Thevenin torque |
| A. E. Fitzgerald, Charles Kingsley Jr. and Stephen D. Umans, *Electric Machinery* | Winding reference transformations, balanced power, equivalent circuits, electromechanical power balance |
| Paul C. Krause, Oleg Wasynczuk, Scott D. Sudhoff and Steven Pekarek, *Analysis of Electric Machinery and Drive Systems* | Amplitude-invariant dq equations and electromagnetic torque |
| Ion Boldea and Syed A. Nasar, *Electric Drives* and *The Induction Machine Handbook* | V/f regions, PM torque/current conventions, MTPA and field weakening |
| Ned Mohan, Tore M. Undeland and William P. Robbins, *Power Electronics: Converters, Applications, and Design* | CCM/DCM converter balance, component stresses and switching-loss estimates |
| Robert W. Erickson and Dragan Maksimovic, *Fundamentals of Power Electronics* | State-space averaged converter models, conduction boundaries and ideal control-to-output coefficients |
| James L. Kirtley Jr., MIT 6.061 notes, chapters 2–4 and 9–12 | Three-phase/sequence network conventions and independent machine-model reference |

Public primary references include Kirtley's [MIT course notes](https://ocw.mit.edu/courses/6-061-introduction-to-electric-power-systems-spring-2011/pages/readings/),
[symmetrical components chapter](https://ocw.mit.edu/courses/6-061-introduction-to-electric-power-systems-spring-2011/resources/mit6_061s11_ch4/),
and [PM/BLDC chapter](https://ocw.mit.edu/courses/6-061-introduction-to-electric-power-systems-spring-2011/resources/mit6_061s11_ch12/).
Erickson's [DC-DC Power Converters](https://www.eng.auburn.edu/~agrawvd/COURSE/READING/LOWP/Erikson_DC_2_DC.pdf),
Table 2, supplies the topology-specific ideal small-signal parameters. In
particular, the boost RHP zero is `R(1-D)^2/L`; buck-boost has the additional
`D` in the denominator. The natural frequency is `1/sqrt(LC)` for buck and
`(1-D)/sqrt(LC)` for boost/buck-boost.

## Input grammar, units and bounds

Every physical scalar is a string such as `"460 V"`, `"20 kVA"`, `"1500 rpm"`,
`"0.1 N*m/A"` or `"10 lbf*inch"`. Bare physical numbers are rejected.
Dimensionless fractions and counts are JSON numbers where the tool schema says
so. A dimensionless field inside a record is a numeric literal. `''` means
omitted only for documented optional parameters. The schema gives each parameter
its own description and example.

The grammar accepts one finite numeric literal followed by named units, products,
quotients and integer powers. It rejects function calls, parentheses and arithmetic
expressions. It validates units through `units.parse_quantity`. Quantities are
limited to 120 characters, unit text to 64, exponent powers to -99..99, and literal
magnitude to `1e100`. Default converted nonzero bounds are `1e-15..1e15` in the
requested SI unit; the tighter tool bounds below take precedence. Literal and
conversion underflow both refuse rather than silently producing zero. Zero is
allowed only where physically meaningful. Derived finite outputs are bounded by
`1e100`; unsupported arithmetic/conditioning returns `input.out_of_range`.

Record lists use commas within a record and semicolons or newlines between
records. Empty records and trailing separators are invalid. Total record text
is capped at 30,000 characters, each record at 1,000, and each list at 100 records
unless a smaller cap is stated. Parsers split bounded text and match short
literals; there is no general expression language or recursive parser.

Pint alone does not distinguish the physical use of W, VA and var. The electric
boundary therefore checks the unit spelling before dimensional conversion:

| Power family | Accepted unit tokens |
| --- | --- |
| Real | `W`, `mW`, `kW`, `MW`, `watt`, `watts`, `kilowatt`, `megawatt`, `hp`, `horsepower` |
| Apparent | `VA`, `mVA`, `kVA`, `MVA` |
| Reactive | `var`, `mvar`, `kvar`, `Mvar` |

Composite spellings such as `V*A` are deliberately rejected for a named power
family. A `kVA` input to `real_power` is an error with a W-family hint. VA/var
outputs preserve their labels; internal arithmetic uses watts dimensionally.
`hp` means Pint's mechanical horsepower. Cyclic frequencies require Hz-family
units; angular speed requires angular units such as rad/s or rpm.

Thermal inputs describe differences, not absolute temperatures. Parsed offset
units, including aliases such as `degree_Celsius` and `degree_Fahrenheit`, are
rejected both alone and in `K/W`-type products. Use K, delta_degC or delta_degF.
This domain has no absolute-temperature calculation.

All refusals use the contract with an error family and a JSON pointer in
`error.details.param`. Root solvers check their bracket, use finite residuals,
and refuse if they exhaust their fixed iteration budget. No caller can select
an unbounded number of samples or iterations.

## `electric_transformer`

Required inputs are total apparent `rating`, primary/secondary line voltages,
and either test records or a complete T equivalent. Connections are `single`,
`Y-Y`, `Y-Delta`, `Delta-Y`, `Delta-Delta`. Three-phase calculations use balanced
per-winding voltage/current, with `S=3 Vphase Iphase`. Y voltage is line voltage
divided by sqrt(3); delta winding current is line current divided by sqrt(3).

`mode="tests"` accepts exactly one record each:

```text
open_circuit = "8000 V,0.214 A,400 W"
short_circuit = "489 V,2.5 A,240 W"
```

Records contain measured line voltage, line current and total real power.
`oc_side` and `sc_side` independently select primary or secondary. The Chapman
OC method calculates `Ic=Pphase/Vphase`, `Im=sqrt((I-Ic)(I+Ic))`,
`Rc=Vphase/Ic`, `Xm=Vphase/Im`. SC reduction calculates
`Req=Pphase/Iphase^2`, `Zeq=Vphase/Iphase` and
`Xeq=sqrt((Zeq-Req)(Zeq+Req))`. Test power must be positive and below apparent
power. The method neglects OC series drop and SC excitation.

`mode="circuit"` accepts one record containing `Rc,Xm,R1,X1,R2,X2` in ohms.
All six values refer to `circuit_side`, including both winding series impedances.
Shunt values must be positive, series values nonnegative, and total winding
resistance positive. `a=V1phase/V2phase`; impedances multiply by `a^2` from
secondary to primary. Both referred circuits and invariant per-unit quantities
are returned. The per-winding impedance base is `Vphase^2/(S/3)` in three phase.

OC/SC tests cannot identify the individual winding split. `primary_fraction`,
default 0.5, assigns that fraction of total resistance and leakage reactance to
the primary. Results explicitly label the assumed split. The exact result is
exact algebra for this assumed linear T circuit, not an exact identification
from two tests.

At rated secondary terminal voltage and specified load current,
`I2=x Irated (pf-j sin(phi))`, with signed sin(phi) for leading/lagging:

```text
E = V2 + I2 Z2
I0 = E (1/Rc - j/Xm)
I1' = I2 + I0
V1' = E + I1' Z1
Vnl = V1' / (1 + Z1 Ym)
regulation_exact = (abs(Vnl)-V2)/V2
regulation_approximate = x Irated (Req pf + Xeq sin(phi))/V2
```

The calculation returns the required primary voltage for that loaded terminal
condition. It does not pretend both primary and secondary rated voltage remain
fixed under load. Exact core/copper losses use E, I1' and I2. Approximate
efficiency uses constant `Pcore` and `x^2 Pcu_full_load`. The unconstrained
maximum occurs at `x=sqrt(Pcore/Pcu_full_load)`; overload maxima carry a warning.
The stated validity range is current fraction 0..2 and is enforced: when
`xmax>2`, `maximum_efficiency_load_fraction` and `maximum_efficiency` are omitted
from `values`, `data.maximum_efficiency_outside_validity_range` is true, the
unconstrained numbers move to `data.unconstrained_maximum_efficiency_load_fraction`
and `data.unconstrained_maximum_efficiency`, and a warning names them. Because
efficiency rises monotonically up to `xmax`, the range-limited maximum is then
`efficiency_at_load_fraction_limit=2 S pf/(2 S pf+Pcore+4 Pcu_full_load)`.

All-day profiles use `duration,current_fraction,pf`, at most 48 rows, totaling
24 h. For example `"8 h,1,.8;16 h,0,1"`. Core loss continues during zero-load
periods. The tool integrates output/loss energy and emits `load_profile.csv`.
It does not model switching the transformer off.

Positive-sequence same-polarity winding EMFs define the reported nominal line phase
shift: Y-Y and Delta-Delta 0 degrees, Y-Delta -30, Delta-Y +30. This excludes
loaded impedance-drop angles. Connection names
alone do not identify every clock group; alternate polarity groups are outside
this interface. Additive autotransformer mode uses primary as common winding,
secondary as series winding and requires primary voltage >= secondary voltage
with matching connections. `Sauto=S2w(Vcommon+Vseries)/Vseries`, transformed power
is S2w and conducted power is the remainder. Insulation/fault rating is not inferred.

Bounds: rating <=1e12 VA, voltages <=1e7 V, ratio 1e-6..1e6, load fraction 0..2,
pf .001..1, test current <=1e9 A and power <=1e12 W. No inrush, saturation,
frequency extrapolation, harmonic or winding-temperature calculation.

## `electric_induction_motor`

`mode="equivalent"` requires per-winding stator-referred R1, X1, Xm, R2 and X2.
Optional Rc is parallel with jXm behind R1+jX1. Omitted Rc means zero core loss.
Mechanical and stray losses are optional constant powers, default zero.

Chapman/Fitzgerald Thevenin equations are
`Zm=1/(1/Rc+1/(jXm))`, `Vth=Vphase Zm/(R1+jX1+Zm)` and
`Zth=(R1+jX1)Zm/(R1+jX1+Zm)`. Synchronous speed is `120f/poles` rpm and slip is
`1-omega/omega_s`. Electromagnetic torque is
`3|Vth|^2(R2/s)/(omega_s[(Rth+R2/s)^2+(Xth+X2)^2])`.
Breakdown slip is `R2/hypot(Rth,Xth+X2)`. Mathematical breakdown outside slip
0..1 is distinguished from the accessible motoring maximum.

The full T-circuit node solve supplies currents and the loss budget:
`Pgap=3|I2|^2 R2/s`, rotor copper `s Pgap`, converted power `(1-s)Pgap`,
shaft power `Pconverted-Pmechanical-Pstray`. Torque equals shaft power/speed at
every valid operating point. Input power is the nonnegative sum
`Pin=Pstator_copper+Pcore+Pgap`, evaluated with compensated summation. This avoids
loss of the small real input-current component when reactive current dominates;
efficiency and power factor use this power in both results and curve CSVs.
`load_torque` is shaft torque and solves the stable
branch including fixed losses. The shaft-torque maximum is located before
bracketing the load root. Loads above it refuse, even when below electromagnetic
breakdown. Explicit speed/slip on an unstable constant-load branch receives a warning.

`slip=0` is an omitted-input sentinel, selecting speed, load torque, nameplate
slip or default .05. Selected motoring slip must be 1e-8..1-1e-8. The CSV curve
includes 0 and 1 circuit limits, with undefined efficiency left empty. Starting
torque is electromagnetic; constant power losses cannot define shaft torque at rest.
Kloss `2 Tmax/(s/sm+sm/s)` is labelled approximate and compared with exact torque
at each point. Stator resistance generally prevents exact agreement.

`mode="nameplate"` requires voltage, current, pf, shaft output, speed, frequency
and poles. One point does not identify a unique circuit. The documented fit
assumes infinite Rc, equal leakage reactances and a specified magnetizing-current
fraction, default .3. Set `Xm=Vphase/(fraction Iphase)` and
`R1=[Pin-(Pout+Pfixed)/(1-s)]/(3 Iphase^2)`. Form the measured complex input
impedance. For trial leakage x, solve
`Im(1/[1/(Zin-R1-jx)-1/(jXm)])=x`, then set R2 to slip times that rotor impedance's
real part. Invalid loss budgets or incompatible assumptions refuse. Matching
residuals and a nonuniqueness warning accompany the estimated starting/torque curve.

With `base_frequency`, voltage scales as `Vbase min(f/fbase,1)` and reactances
as `Xbase f/fbase`. R, Rc and fixed losses remain constant. The separate ideal V/f
envelope assumes constant flux/torque below base and constant power above base.
The computed circuit includes stator drop; no low-speed boost is inferred.
Changing rotor resistance shifts breakdown slip while preserving theoretical
breakdown torque for fixed other parameters.

Bounds: 3..201 CSV points, even poles 2..200, .1..10000 Hz, 1e-8..1e8 ohm
parameters, 1e-3..1e7 V, resistance multiplier .01..100, magnetizing fraction
.01...9. Bisections use at most 100 iterations. No skin effect, saturation,
transient acceleration, thermal rating or current-limited inverter model.

## `electric_pm_dc_motor`

Modes are `pm_dc`, `bldc_trapezoidal` and `pmsm`. All use constant resistance and
magnetic parameters with nonnegative motoring. PM DC also covers separately
excited DC machines at fixed field. Series/shunt field variation is not modeled.

The DC equations are `V=ke omega+IR`, `Te=kt I`. Coherent terminal SI constants
require ke=kt. A supplied back-EMF constant within 1e-3 relative of kt (rounded
datasheet values such as `5.236 V/krpm` for `0.05 N*m/A`) is accepted with a
warning and kt is used for both; a larger difference refuses. Pint reads `Hz` as
`1/s`, so `0.05 V/Hz` would silently be taken per radian instead of per
revolution per second (a 2 pi error). Any cyclic-rate unit factor (`Hz`, `kHz`,
`hertz`, `cps`) in `back_emf_constant` or `torque_constant` therefore refuses
with `units.dimension_mismatch`; use `V*s/rad`, `V*s/revolution`, `V/rpm` or `V/krpm`. `torque` is
electromagnetic torque. Shaft power subtracts the supplied fixed mechanical
loss, and shaft torque is returned when speed is positive. Voltage/current
limits are checked at the requested point. Theoretical stall is `I=V/R`,
`T=kt V/R`; ideal no-load speed is `V/ke`. Fixed mechanical loss produces a
separate corrected zero-shaft-load speed. Converted power reaches `V^2/(4R)` at
half theoretical stall torque and half ideal no-load speed. Maximum efficiency
with constant mechanical loss occurs at `I=sqrt(Pfixed/R)`. With zero fixed loss,
the value 1 is a zero-output limiting efficiency, explicitly labelled as such.

DC CSV rows cover the voltage-line torque/current relation and report whether
the point meets current and nonnegative-output limits. Lumped steady thermal
rise is `Pcopper theta`, with `Pcopper=I^2 R`; permitted temperature rise is a
difference in K or delta units. This does not model time constants or iron loss.

PMSM uses amplitude-invariant Clarke/Park, phase-peak dq values and mechanical
omega. Electrical speed is `omega_e=p omega`, where p is pole pairs:

```text
vd = R id - omega_e Lq iq
vq = R iq + omega_e (Ld id + psi_m)
Te = 1.5 p [psi_m iq + (Ld-Lq) id iq]
Pin = 1.5 (vd id + vq iq)
Pcopper = 1.5 R (id^2+iq^2)
```

The implementation evaluates real input power through the equivalent
`Te*omega+Pcopper` identity to avoid subtracting large reactive dq cross terms.

Linear SVPWM sets phase-peak `Vmax=Vdc/sqrt(3)`. No overmodulation is assumed.
For delta=Ld-Lq and current amplitude I, the rationalized MTPA solution is
`id=2 delta I^2/[psi+sqrt(psi^2+8 delta^2 I^2)]`, `iq=sqrt(I^2-id^2)`.
The angle is `atan2(iq,id)` from the d axis. Surface PM has id=0 below the voltage
boundary. Base speed is the positive root of the voltage quadratic at full-current
MTPA. A zero base speed means that full current already violates the resistive
voltage limit at standstill.

Field weakening maximizes positive torque over the current disk and resistive
voltage ellipse. Surface-PM equal-inductance cases use analytic circle candidates.
Salient cases use 129 bounded d-axis samples, at most eight local brackets and
up to 64 golden-section refinements, checking current-bracket convergence.
Requested torque uses `id=0, iq=T/(1.5 p psi)` directly for equal inductances.
For salient machines, the 64-step MTPA current bisection uses an upper bound of
`min(Imax, tau/psi, sqrt(2 tau/abs(Ld-Lq)))`, where `tau=T/(1.5 p)`.
The last two bounds come from feasible zero-d-axis and 45-degree current vectors,
respectively. They keep the bracket on the requested-current scale even when the
current limit is much larger. The computed torque must match the request within
`1e-10` relative error, with no absolute allowance; otherwise the tool refuses
`electric.nonconvergence` at `/torque`. When that point violates
the voltage limit, the field-weakening solve works on the exact torque hyperbola
`iq=T/(1.5 p s)`, `s=psi+(Ld-Lq) id>0`. Along it `id^2+iq^2` is strictly convex,
so the minimum-current point is a hyperbola/voltage-ellipse intersection.
Multiplying the voltage margin by `s^2` gives a polynomial of degree at most 4 in
id; all real roots (companion-matrix roots, Newton-polished) inside the voltage
limit are candidates and the least current wins. The result carries exactly the
requested torque. A one-sided check evaluates the envelope at 1e-6 less current
and refuses `electric.nonconvergence` if that already reaches the torque. Only
requests within 1e-6 of the envelope torque, where the two intersections merge,
fall back to the envelope point, with a warning. (The earlier fixed 48-step
current bisection could not resolve torque ~ sqrt(I-I0) near the first
current-circle/voltage-ellipse contact and falsely refused light loads.)
Impossible points refuse; unavailable envelope rows are marked false with empty
values. CSV rows include torque, id/iq, current, voltage and feasibility.

Ideal trapezoidal BLDC assumes 120-degree conduction with two phases carrying
terminal current. `kt=2 ke_phase_flat=ke_line_line_flat`, phase RMS current is
`I sqrt(2/3)`, and `Kv=60/(2pi ke_line_line_flat)` rpm/V. Supplied R is the sum
of the two conducting phase resistances. Sinusoidal and trapezoidal constants
must not be interchanged without converting these conventions.

Bounds: 2..100 automatic curve points, 100 explicit envelope speeds, pole pairs
1..100, PMSM speed <=1e7 rad/s, inductance 1e-12..1e3 H, flux 1e-12..1e6 Wb,
positive R 1e-12..1e9 ohm and current <=1e9 A. No dynamic machine simulation,
saturation, demagnetization, iron loss, inverter losses or thermal network.

## `electric_power_flow`

Modes are `balanced`, `feeder`, `per_unit`, `components` and `fault`.
Balanced/feeder sources are `power_pf`, `power_pq` or `phasors`.
P and Q are total three-phase powers. A supplied current angle references the
line-neutral phase-A voltage, even for a delta load; it is not an angle relative
to the line-line voltage. `S=sqrt(3) Vline conj(Iline)`, with positive Q inductive.
Load winding phase voltage/current are derived for wye or delta.

For a lagging load, capacitor correction to a higher target pf uses
`Qc=P[tan(acos(pf_old))-tan(acos(pf_target))]`. Per-branch capacitance is
`Qc/(3 omega Vbranch^2)`. It returns new current and the fractional `I^2R` loss
reduction. Leading-load requests or a worsening target refuse.

Feeder R and X are per conductor per length, and length is one way. With
`Z=(R_per_length+jX_per_length) length`, the exact RMS phasor is
`Vs_phase=Vr_phase+Iline Z`. Line magnitude drop is
`sqrt(3)(|Vs_phase|-|Vr_phase|)`; approximation is
`sqrt(3) I(R cos(phi)+X sin(phi))`. Copper loss is `3 I^2 R` and source power
includes it. Magnitude-drop arithmetic uses a rationalized difference. The
tool warns when phasor drop exceeds 10% of receiving phase voltage.

Per-unit inputs are one real,imag record in ohms, or in `dimensionless` units
when `impedance_basis="per_unit"`. Bases use total apparent power and line-line
voltage, giving `Zbase=Vline^2/S3phase` and `Ibase=S3phase/(sqrt(3)Vline)`.
Both old/new per-unit phasors and unchanged physical impedance are returned.

Components require exactly three records in V or A. `phase_to_sequence` takes
abc order and returns 012; `sequence_to_phase` reverses it. With `a=exp(j120deg)`,
the forward matrix is `[[1,1,1],[1,a,a^2],[1,a^2,a]]/3`. It returns real/imaginary
parts, magnitude/angle, the matrix, and negative/zero-to-positive unbalance.
Unresolved or zero positive sequence causes normalized factors to be omitted,
not fabricated.

Fault mode accepts passive `R,X` records in ohms for sequence Thevenin impedances.
Prefault `E=Vline/sqrt(3)` is positive sequence only. The sequence formulas are:

| Fault | Sequence currents |
| --- | --- |
| Three phase | I1=E/(Z1+Zf), I0=I2=0 |
| a-ground | I0=I1=I2=E/(Z1+Z2+Z0+3Zf) |
| b-c | I1=E/(Z1+Z2+Zf), I2=-I1, I0=0 |
| b-c-ground | B=Z2+Z0+3Zf; I1=E/[Z1+Z2(Z0+3Zf)/B]; I2=-I1(Z0+3Zf)/B; I0=-I1 Z2/B |

Zf is the b-c path for line-line and the common ground path for ground faults.
Outputs include all phase currents/voltages, ground current, maximum faulted
phase RMS current and apparent power. `short_circuit_apparent_power=Vline^2/|Z1|`
is the prospective bolted three-phase value, distinct from the selected fault.

Peak factor selects `half_cycle` or `iec_kappa`. `iec_kappa` follows IEC 60909-0:
`kappa=1.02+.98 exp(-3 R1/X1)` from the bolted three-phase (positive-sequence)
impedance only, without fault resistance, and the same kappa is reused for
line-line and earth faults; `ip=sqrt(2) kappa Ik`. It reports `kappa` and
`kappa_r_over_x`; X1=0 uses the limit 1.02. `half_cycle`,
`sqrt(2)[1+exp(-pi R/X)]`, is an approximate alternative that uses the whole
effective fault loop (for a-ground Z1+Z2+Z0+3Zf, including fault resistance).
`data.peak_factor_basis` states which basis applied. The reported `x_over_r` is
always the fault-loop ratio (`data.x_over_r_basis`). Zero loop R omits unbounded
X/R and uses the finite limiting factor. Naming the kappa formula does not claim
a complete IEC 60909 calculation (no voltage factor c, no meshed-network method B/C). No prefault load flow,
transient decrement, generator saturation, grounding design or protection settings.

Bounds: voltages <=1e7 V, powers <=1e12 W/VA/var, line current <=1e9 A,
sequence impedance >=1e-12 ohm for Z1, length <=1e7 m and at most three sequence
records. Feeder/fault impedances are passive; a singular sequence branch refuses.

## `electric_drive_sizing`

Models are rotary, unity-ratio rotary direct drive, belt, rack and screw.
Rotary inputs use load inertia, angle, angular speed and torque. Linear models
use mass, travel, speed and force. Belt/rack use effective radius r; screw uses
`r_effective=lead/(2pi)`. `gear_ratio` is motor revolutions per output revolution.
The supplied efficiency includes the transmission; it is not a screw-specific
efficiency table.

Provide a profile of up to 100
`duration,start_speed,end_speed,external_load_effort` records, for example
`"1 s,0 rad/s,4 rad/s,0 N*m;1 s,4 rad/s,0 rad/s,0 N*m"`.
Adjacent segment speeds must be continuous. Speed varies linearly and external
signed load effort stays constant within a row. Alternatively give signed
distance and total time for a rest-to-rest move, with acceleration time <=half
the duration. Omitted acceleration time creates a triangle; otherwise the middle
segment is constant speed. Peak load speed is `distance/(time-acceleration_time)`.
`cycle_time` (at least `move_time`) appends a zero-speed dwell of `cycle_time - move_time`
holding `dwell_load` (default zero; linear gravity always acts), so the RMS torque
covers the repeating cycle that a continuous rating refers to; without it the RMS
covers the move only and, when `rated_torque` or candidates are given, a warning says
the RMS is too high for a cycle with rests. With an explicit `profile`, add a
zero-speed row instead.

Load effort includes inertia, Coulomb friction, viscous friction and optional
gravity `m*9.80665*sin(angle)`. Standard gravity is the defined conventional
constant in m/s^2. Positive external effort resists positive travel; static
friction at rest is not inferred. Mechanical motor/load power balance multiplies
load-side power by 1/eta while motoring and by eta while regenerating. Rotor
acceleration torque is then added at the motor shaft.

The implementation splits records at speed and load-power sign changes. It
integrates polynomial torque squared, power squared and negative power exactly
within each resulting piece. It returns peak/RMS torque and power, regenerative
and net shaft energy with and without the selected rotor inertia. `drive_profile.csv`
contains segment endpoint speed/torque and energy, retaining acceleration sign.
The simple inertia-matching ratio is `sqrt(Jload_equivalent/Jrotor)` for ideal
fixed motor torque and no process load. Torque/speed limits, gear losses, friction
and process loads can make a different ratio preferable. No global constrained
gear optimization is claimed.

Available torque yields a conservative zero-to-peak-speed time based on the
largest resisting-load bound. Independent duty records `duration,shaft_torque`
compute a separate RMS torque. Continuous S1 and intermittent RMS comparisons
are screening checks; intermittent cooling and self-ventilation derating require
motor data and are not calculated.

Optional candidates use up to 100 records:
`name,inertia,rated_torque,max_torque,rated_speed,max_speed`.
Each candidate recomputes the profile using its own rotor inertia. Margin tables
and profile feasibility CSVs compare RMS/peak torque and speed against the stated
limits. A candidate fails when the profile RMS torque exceeds its rated (continuous)
torque, the peak torque exceeds its maximum torque, or the peak speed exceeds its
maximum speed. A peak speed above the rated speed but within the maximum speed is
allowed: the status is `pass within stated limits, above rated speed (<ratio> x): ...`,
`rated_speed_margin_rad_s` stays negative and a warning gives the speed ratio and asks
for the datasheet torque-speed curve at that speed (the continuous torque derates
there; this tool does not model it). It does not invent a manufacturer's
field-weakening curve. Candidate speeds accept rpm, rad/s or deg/s (`"3000 rpm"`);
margins are reported in rad/s. Reports say pass within stated limits or fail, never approved.
Candidate comparisons allow rounding error of at most `1e-10` times each stated
rating, with no absolute torque or speed allowance. Reported margins remain the
signed differences in SI units; the result and both candidate CSVs share the
same profile status.

Bounds: gear ratio 1e-6..1e6, efficiency 1e-6..1, segment times 1e-9..1e9 s,
load inertia/mass and effort <=1e12 in SI, speed <=1e9 in SI, 100 profile/duty/
candidate rows. Subdivision is bounded by speed and effort zero crossings.
No elasticity, jerk limit, peak-duration duty, bearing life, brake resistor,
inverter energy-recovery efficiency or thermal network is modeled.

## `electric_converter`

Topologies are buck, boost and inverting buck-boost, with a freewheel diode by default.
Inputs specify
positive output-voltage magnitude; buck-boost reports its signed negative output.
`mode="auto"` chooses CCM/DCM, while explicit ccm/dcm refuses incompatible points.
Supply L or target inductor ripple; when both are supplied, report the required
L separately and evaluate the actual supplied L. C and total output ripple have
the analogous optional sizing/evaluation roles.

The Mohan/Erickson average model uses constant diode drop, switch Rds(on) and
inductor DCR. Let Von be Vin-Vo for buck and Vin otherwise, and Voff be Vo+Vd
for buck/buck-boost or Vo+Vd-Vin for boost. Ron=Rds+DCR and Roff=DCR.
In CCM solve `D Von-(1-D)Voff=IL[D Ron+(1-D)Roff]`, with IL=Io for buck and
Io/(1-D) otherwise. Lossy boost-like stages can have two algebraic duty roots;
the bracket chooses the lower-current branch and rejects unavailable output power.

For DCM, triangular interval-mean drops give
`Ipk=Von D/(L f+Ron D/2)` and
`delta=L f Ipk/(Voff+Roff Ipk/2)`.
The load is `Ipk(D+delta)/2` for buck and `Ipk delta/2` otherwise. The third
interval has zero inductor current. The boundary has `D+delta=1`, and critical
load current and critical L are reported. L sizing follows volt-seconds; ideal
buck gives `L=(Vin-Vo)D/(f DeltaI)`.

Piecewise linear integration gives switch/diode/inductor average, peak and RMS
currents, input capacitor AC RMS current and output capacitor charge swing.
Input-capacitor variance is evaluated as
`a DeltaI^2/12 + a(1-a) Imean^2`, where `a` is the fraction of the period drawing
source current and `Imean` is the ramp midpoint. Boost uses the full conducting
interval; buck and buck-boost use the switch-on interval. In boost CCM this
reduces to `DeltaI^2/12`. The cycle retains `DeltaI` from volt-seconds separately
from its DC current so subtracting rounded endpoints cannot erase small ripple.
The ESR current swing uses that retained ripple for buck; boost and buck-boost
use the inductor peak because their output current switches between zero and
the diode current.
Ripple is the conservative sum `charge_swing/C + ESR*current_peak_to_peak`;
the two extrema need not occur together. Output ripple above 10% of DC voltage
refuses because the stiff-output assumption no longer holds. An ESR contribution
alone above that limit refuses `electric.model_limit` at `/capacitor_esr`, even
when capacitance is omitted. Below that limit, omitted capacitance still leaves
total ripple and the stiff-output assumption unverified. ESR-only ripple at or
above a target also refuses.

Conduction losses use RMS current squared and diode average current. Duty uses
interval-mean resistance drops, so ripple copper loss is a separately reported
correction. Both the averaged power-balance residual and RMS waveform residual
are reported; the latter is minus that ripple correction. Estimated input power
adds all RMS conduction, ESR, overlap, gate and recovery losses to output power.
Ripple copper correction above 5% of output power refuses. This is an explicitly
limited averaged approximation, not exact exponential switched-RL integration.

`synchronous=true` replaces the diode by a synchronous FET (the low-side FET of a
buck): in the off interval Voff loses the diode drop (`Voff=Vo`, or `Vo-Vin` for boost) and
Roff becomes `DCR + low_side_resistance`, so duty, ripple and the DCM boundary follow from
the same equations. The loss list gains `low_side_conduction_loss=R_ls D_off Irms_off^2`
and `dead_time_loss=Vf (Ipk+Imin) t_dead f` (Vf is `diode_drop`, now the body-diode drop;
for small ripple this is the usual `Vf Io 2 t_dead f`), and the diode conduction loss is zero.
Below the CCM boundary the stage is treated as diode emulation (no reverse inductor
current), so the DCM equations apply with the low-side resistance. `gate_charge` is the
sum of both FETs. Dead time is a loss only: it does not shift the duty. With nothing
but capacitor ESR to lose, the result carries the warning `Ideal: no loss inputs ...
not a prediction` and `data.losses_given=false`. The hand check against an independent
24 V to 5 V, 3 A, 500 kHz budget (UAT E4) reproduces the high-side 23 mW, low-side
42.8 mW, DCR 181 mW, gate 40 mW, dead-time 42 mW and ESR 0.27 mW terms exactly.

Overlap loss is `.5 Vstress (Imin tr+Ipk tf) f`; for small ripple this reduces to
`.5 V I (tr+tf)f`. Gate loss is `Qg Vg f`; hard-commutation diode recovery is
`Qrr Vstress f` in CCM and zero after the DCM zero-current interval. Device voltage
stresses are ideal blocking estimates plus the specified diode drop where relevant.
No parasitic overshoot is inferred.

With C supplied in CCM, the ideal voltage-mode plant returns DC gain, natural
frequency, Q, poles, RHP zero when present and numeric coefficient arrays. Let
`Rload=Vo/Io` and `D'=1-D`:

| Topology | DC gain dVo/dD | Natural frequency | Q | RHP zero |
| --- | --- | --- | --- | --- |
| Buck | Vin | 1/sqrt(LC) | Rload sqrt(C/L) | none |
| Boost | Vin/D'^2 | D'/sqrt(LC) | Rload D' sqrt(C/L) | Rload D'^2/L |
| Buck-boost | -Vin/D'^2 | D'/sqrt(LC) | Rload D' sqrt(C/L) | Rload D'^2/(D L) |

These ideal coefficients do not include the nonideal DC losses. They are omitted
in DCM, where the CCM plant is invalid. No frequency sweep or controller design
is performed.

Bounds: duty 1e-8..1-1e-8, voltage 1e-6..1e6 V, load current 1e-9..1e6 A,
frequency 1e-3..1e9 Hz, L 1e-12..1e3 H, C 1e-12..1e4 F, derived peak current
<=1e9 A. Switching overlap must fit within 10% of a period. Bisection uses at
most 100 iterations. No no-load model, synchronous rectification, core loss,
AC winding loss, Coss loss, thermal model or isolated topology.

## Verification and scope

The six gateway schemas are tested through `fastmcp.Client(gateway)`. The test
fails if descriptions are missing, longer than 140 characters, or repeated for
different parameters of one tool. Gateway tests cover successful calls, missing
inputs, wrong dimensions, zero/negative quantities, W/VA/var confusion, offset
temperature aliases, 1e308, underflow, NaN text, hostile 6000-character inputs,
conflicting modes, count caps and impossible operating points. Each successful
and failed result is checked for finite JSON and the standard contract.

Independent routes include transformer two-port matrix composition, motor
complex KCL/KVL solves, SciPy brentq, an abc-frame flux derivative and Park
transform, phase-domain fault network solves, exact motion integrals and
switched converter integration. SciPy is imported only inside tests via
`pytest.importorskip`. No test requires a network or external engineering program.

Hand cases include a 20 kVA 8000/240 V transformer, a four-pole 60 Hz induction
motor, a 24 V PM DC motor, a 400 V 12 kW/.8 pf load, a 60 A single-line-ground
fault, a 6 rad four-second trapezoidal move, and a 24-to-12 V 20 kHz buck with
150 uH for 2 A ripple. Default comparisons use 1e-9 relative and 1e-10 absolute
tolerance; tests state looser integration tolerances where needed.

Run with the existing virtualenv, preserving the dispatch environment. On
PowerShell, expand the test file list explicitly if wildcard expansion is not
provided by the invoking shell:

```powershell
.venv/Scripts/python.exe -m pytest -q (Get-ChildItem tests/test_electric_*.py).FullName
.venv/Scripts/python.exe -m eng_toolkit doctor
```

These tests verify mathematical implementation within the stated models. They
do not validate a physical motor, transformer, feeder, drive or power converter.
There is no transient/dynamic machine simulation in the tools, no harmonics,
saturation or thermal network, no isolated converters, cable ampacity tables,
protection coordination or electrical-code compliance claim.

Verification on 2026-10-22 with the supplied Windows virtualenv, after the
independent-review fixes (`tests/test_electric_review_fixes.py`: IEC kappa basis,
transformer maximum-efficiency range, PMSM light-load field weakening against a
seeded 200-case SciPy SLSQP comparison, Hz in EMF constants): 302 tests passed
in 41.58 seconds, with the optional SciPy checks executed. `python -m eng_toolkit
doctor` exited 0 and reported no failures. The repository-wide test suite was
not run. Changes are left uncommitted for coordinator review.

Verification on 2026-10-23 for second-review findings 1 through 5:
`tests/test_electric_review2.py` contains 32 gateway regressions, including the
six exact reported cases, 80-digit Decimal circuit and MTPA references, ripple
below the DC current's floating-point resolution, and candidate CSV checks.
All six reported cases failed before the fixes. The final run passed 374 tests
in 43.70 seconds using `.venv/Scripts/python.exe -m pytest -q -x -o addopts=''`
with all `tests/test_electric_*.py` files, `tests/test_surrogate_inputs.py`,
`tests/test_gateway.py` and `tests/test_contract.py`. The optional SciPy checks
executed. `git diff --check` passed. Finding 6 belongs to the separate shared
gateway fix; no shared files changed. The repository-wide suite was not run,
and changes remain uncommitted for the coordinator.
