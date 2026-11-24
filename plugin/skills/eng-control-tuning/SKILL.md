---
name: eng-control-tuning
description: Use FIRST for "tune a PID or PI", velocity or temperature loop, step response, Bode and margins, or discretising a controller for a microcontroller (control_* tools). Plant from datasheet numbers, unit traps, saturation check, tool limits.
---

# Control tuning workflow (eng-toolkit)

Load `eng-toolkit` too. The six `control_*` tools are **linear, continuous or discrete SISO** transfer-function tools: no saturation, no noise, no sampled-data simulation. Results are unreviewed starting points.

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

- Polynomials are descending-power lists, e.g. `"[0.045]"` over `"[1.125e-8, 5.4e-5, 2.03e-3]"`. PI/PID controller `C = (Kd s^2 + Kp s + Ki)/s` is numerator `"[Kp, Ki]"` over `"[1, 0]"` (PI). Times carry units (`"1 ms"`), polynomials do not: keep seconds, rad/s, volts.
- **Unit trap**: a datasheet `x V/krpm` is not `V*s/rad`: `Ke = 60 / (2 pi x 1000) x x = 0.009549 x` (4.71 V/krpm gives 0.0450); a speed constant `n rpm/V` gives `Ke = 60/(2 pi n)` (212 rpm/V gives 0.0450). In SI Ke equals Kt (`45 mN*m/A = 0.045 N*m/A`) within rounding: use that as the check. Feedback in rad/s makes gains V per rad/s; with rpm feedback the gain per rpm is the gain per rad/s times `pi/30`.
- **DC motor, voltage in, speed out**: `J = J_rotor + J_load` (load reflected to the shaft; omitting it changes gains several-fold), friction `b = Kt I0 / w0` from the no-load current and speed. Plant `w/V = Kt / (L J s^2 + (R J + L b) s + R b + Kt Ke)`. Check with `control_analyze` (DC gain, dominant time constant, electrical pole). Example inputs, not a datasheet: R 1.2 ohm, L 0.25 mH, Kt = Ke 0.045, J 4.5e-5 kg*m^2, I0 80 mA at 5000 rpm gives b 6.88e-6, DC gain 22.13 rad/s per V, dominant time constant 26.3 ms, electrical pole 4762 rad/s.
- **From a measured step** (first-order with delay): `K = delta y / delta u`, `T = 1.5 (t63 - t28)`, `L = t63 - T`.
- `control_pid_tune` needs FOPDT with **delay > 0** and `L/T` in 0.01..100. A second-order plant is approximated: K = DC gain, T = dominant time constant, L = neglected lags plus computation delay (electrical lag 0.21 ms + one 0.5 ms sample = `delay "0.7 ms"`). Closed-loop time constant `lambda_time` = wanted 10-90 % rise / 2.2 (80 ms gives `"36.4 ms"`). Gains come back as `controller_gain` and `integral_gain` (use `form "parallel"`), shown as dimensionless but they are V per rad/s. The rule behind it: `Kp = T / (K (lambda + L))`, `Ki = Kp / T`.

## 4. Run, then verify (example: 80 ms rise, 2 kHz loop, PI, no D term)

1. `control_pid_tune gain 22.13 time_constant "26.3 ms" delay "0.7 ms" controller PI rule imc lambda_time "36.4 ms" form parallel` gives Kp 0.03203, Ki 1.218, rise 80.0 ms, overshoot 0, phase margin 88.9. Hand check: `T / (K (lambda + L)) = 0.0263 / (22.13 x 0.0371)`.
2. Exact check with the REAL 2nd-order plant: `control_feedback` (plant as above, controller `"[0.03203, 1.218]"`/`"[1, 0]"`) gives closed loop `[0.00144135, 0.05481]` over `[1.125e-8, 5.40017e-5, 0.0034746, 0.05481]`, stable. `control_response` on those with `t_final "0.4 s"`, `samples 4001`: rise 81.0 ms, overshoot 0 %, settling 144 ms. Report the exact-plant number, not the FOPDT estimate.
3. `control_frequency` on the open loop `[0.00144135, 0.05481]` over `[1.125e-8, 5.40017e-5, 0.00203325, 0]`: phase margin 89.6 deg at 26.9 rad/s, delay margin 58.1 ms. **`guaranteed_phase_margin` in `control_feedback` is a lower bound from the sensitivity peak, not the margin**: report the one from `control_frequency`.
4. **Actuator check by hand** (label it): at a speed step `dw` the first voltage is `Kp dw` and the first current `Kp dw / R`. 2000 rpm = 209 rad/s gives 6.7 V and 5.6 A, inside a 6 A limit; 3000 rpm asks 8.4 A, so the drive saturates and the real rise is longer. Minimum rise when current-limited is `0.8 dw J / (Kt Imax)` (42 ms for 3000 rpm). Tell the user the step size for which the linear result is valid; the tools cannot simulate the limit.
5. Discretise: `control_discretize numerator "[0.03203, 1.218]" denominator "[1, 0]" sample_time "0.5 ms" method tustin` gives `y[k] = 0.0323345 u[k] - 0.0317255 u[k-1] + y[k-1]` (`Kp + Ki Ts/2`; the pole at z = 1 is the integrator, "marginally stable" is expected). Sampled-loop check: extra delay about `1.5 Ts` against the delay margin; anti-windup is the user's code (clamp the integrator).
6. Derivative: with a fast electrical pole and encoder quantisation noise, D adds noise and nothing the PI needs: no D term unless the plant has a slow second lag.
7. **After ANY gain change, rerun steps 2-3 and replot step and Bode for the final gains** (put the gains in the picture title); a picture made with earlier gains next to new numbers is a wrong deliverable.

## 5. Outside the toolkit

Saturation and anti-windup simulation, sampled-data simulation with delay, noise, backlash, nonlinear friction, MIMO or cascade tuning, gain scheduling, state-space design, identification from noisy data. Say so and give the hand check instead.

## 6. Report

A step or Bode picture: `plot_xy` / `plot_panels` on `response.csv` / `bode.csv` with `format="png"` (hand over `data.png_path`; skill `eng-plot-results`). As in `eng-toolkit`: gains with units and the form (`C = Kp + Ki/s`), spec versus result (rise 10-90 %, overshoot, phase margin from `control_frequency`, delay margin), the valid step size and current check, the difference equation, assumptions (J, b, Ke source), limits (linear, no saturation, unreviewed).
