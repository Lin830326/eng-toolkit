---
name: eng-control-tuning
description: Use to tune and verify a PI/PID loop (servo, motor speed, temperature) with the eng-toolkit control_* tools - plant from datasheet numbers, tuning, step response, margins, saturation check, discretisation for a microcontroller. Covers unit traps and tool limits. Load eng-toolkit first.
---

# Control tuning workflow (eng-toolkit)

Load `eng-toolkit` first. The six `control_*` tools are **linear, continuous or discrete SISO** transfer-function tools: no saturation, no noise, no sampled-data simulation. Results are unreviewed starting points.

## 1. Pin down the problem (ask if it changes the answer)

Plant data (datasheet or step test), what the controller output is (volts, % of drive, amps) and what is fed back (rad/s, rpm, degC), the spec (rise time **10-90 % or 63 %?**, overshoot, settling, bandwidth), **actuator limits** (supply voltage, drive current limit, typical step size), loop rate and computation delay, whether load inertia or friction is included, and any disturbance to reject.

## 2. Choose the tool

| Question | Tool |
|---|---|
| Poles, damping, DC gain, stability of a G(s) or G(z) | `control_analyze` |
| PI/PID gains from a first-order-plus-delay model | `control_pid_tune` (`rule imc` or `simc`; `ziegler_nichols`, `cohen_coon` are aggressive) |
| Plant with controller (closed loop T, S, errors, Kv) | `control_feedback` |
| Step/ramp/impulse response, rise, overshoot, settling | `control_response` |
| Bode, gain/phase margin, delay margin of an OPEN loop | `control_frequency` |
| Controller to difference equation | `control_discretize` (`tustin`; `zoh` for plants) |

## 3. Build the inputs

- Polynomials are descending-power lists: `"[0.0302]"` over `"[1.10044e-8, 4.0127e-5, 9.113e-4]"`. PI/PID controller `C = (Kd s^2 + Kp s + Ki)/s` is numerator `"[0.0560, 1.273]"` over `"[1, 0]"`. Times carry units (`"1 ms"`), polynomials do not: keep seconds, rad/s, volts.
- **Unit trap**: datasheet `3.155 V/krpm` is not `V*s/rad`. `Ke = 60 / (2 pi x 1000) x 3.155 = 0.03013 V*s/rad`; a speed constant `317 rpm/V` gives `1/(317 x 2 pi / 60) = 0.03013`. In SI Ke equals Kt (`30.2 mN*m/A = 0.0302 N*m/A`) within rounding: use that as the check. Feedback in rad/s makes gains V per rad/s; with rpm feedback the gain per rpm is the gain per rad/s times `pi/30`.
- **DC motor, voltage in, speed out**: `J = J_rotor + J_load` (load reflected to the shaft; omitting it changes gains about tenfold), friction `b = Kt I0 / w0` from the no-load current and speed. Plant `w/V = Kt / (L J s^2 + (R J + L b) s + R b + Kt Ke)`. Check with `control_analyze`: DC gain 33.14 rad/s per V, dominant time constant 43.8 ms, electrical pole at 3624 rad/s (RE 40 example: R 0.299, L 0.082 mH, J 1.342e-4, b 5.21e-6).
- **From a measured step** (first-order with delay): `K = delta y / delta u`, `T = 1.5 (t63 - t28)`, `L = t63 - T`.
- `control_pid_tune` needs FOPDT with **delay > 0** and `L/T` in 0.01..100. A second-order plant is approximated: K = DC gain, T = dominant time constant, L = neglected lags plus computation delay (0.28 ms electrical + 1 ms sample delay, take `delay "1 ms"`). Closed-loop time constant `lambda_time` = wanted 10-90 % rise / 2.2 (50 ms gives `"22.7 ms"`). Gains come back as `controller_gain` and `integral_gain` (use `form "parallel"`), unit shown as dimensionless but they are V per rad/s.

## 4. Run, then verify (verified RE 40 case: 50 ms rise, no D term)

1. `control_pid_tune gain 33.14 time_constant "44 ms" delay "1 ms" controller PI rule imc lambda_time "22.7 ms" form parallel` gives Kp 0.0560, Ki 1.273, rise 49.8 ms, overshoot 0, phase margin 87.6.
2. Exact check with the REAL 2nd-order plant: `control_feedback` plant as above, controller `"[0.0560, 1.273]"`/`"[1, 0]"` gives closed loop `[0.0016912, 0.0384446]` over `[1.10044e-8, 4.0127e-5, 2.6025e-3, 3.84446e-2]`, stable, Kv 42.2. `control_response` on those with `t_final "0.3 s"`, `samples 3001`: rise 51.5 ms, overshoot 0 %, settling 92 ms (the FOPDT estimate said 49.8: report the exact-plant number; a "samples too coarse" warning is about the fast pole, the rise time is unchanged from 601 samples).
3. `control_frequency` on the open loop `[0.0016912, 0.0384446]` over `[1.10044e-8, 4.0127e-5, 9.113e-4, 0]`: phase margin 89.5 deg at 42.4 rad/s, delay margin 36.9 ms. **`guaranteed_phase_margin` (59.3) in `control_feedback` is a lower bound from the sensitivity peak, not the margin**: report the one from `control_frequency`.
4. **Actuator check by hand** (label it): at a speed step `dw` the first voltage is `Kp dw` and the first current `Kp dw / R`. 500 rpm = 52.4 rad/s gives 2.93 V and 9.8 A, right at a 10 A limit; 1000 rpm asks 19.6 A, so the drive saturates and the real rise is about 98 ms, not 50. Minimum rise when current-limited is `0.8 dw J / (Kt Imax)` (18.6 ms for 500 rpm). Tell the user the step size for which the linear result is valid; tools cannot simulate the limit.
5. Discretise: `control_discretize numerator "[0.0560, 1.273]" denominator "[1, 0]" sample_time "1 ms" method tustin` gives `y[k] = 0.0566365 u[k] - 0.0553635 u[k-1] + y[k-1]` (the pole at z = 1 is the integrator, "marginally stable" is expected). Frequency-response ratio at Nyquist/2 is within 1e-4. Sampled-loop check: extra delay about `1.5 Ts` (ZOH half sample plus one sample computation) against the 36.9 ms delay margin; anti-windup is the user's code (clamp the integrator).
6. Derivative: the electrical pole (3624 rad/s) is above Nyquist at 1 kHz (3142 rad/s) and encoder noise is amplified: no D term unless the plant has a slow second lag.

## 5. Outside the toolkit

Saturation and anti-windup simulation, sampled-data simulation with delay, noise, backlash, nonlinear friction, MIMO or cascade tuning, gain scheduling, state-space design, identification from noisy data. Say so and give the hand check instead.

## 6. Report

A step or Bode picture: `plot_xy` / `plot_panels` on `response.csv` / `bode.csv` with `format="png"` (hand over `data.png_path`; skill `eng-plot-results`). As in `eng-toolkit`: gains with units and the form (`C = Kp + Ki/s`), spec versus result (rise 10-90 %, overshoot, phase margin from `control_frequency`, delay margin), the valid step size and current check, the difference equation, assumptions (J, b, Ke source), limits (linear, no saturation, unreviewed).
