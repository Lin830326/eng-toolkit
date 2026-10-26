---
name: eng-circuit-sim
description: Use to simulate an analog circuit from a description with ngspice (spice_simulate, spice_check_netlist, spice_measure), for example amplifier bias, gain, Bode, converter waveforms, filters; and to size a buck or boost with electric_converter. Covers netlists, model limits, corners, ripple.
---

# Circuit simulation workflow (eng-toolkit)

Load `eng-toolkit` first. ngspice is a separate program (`toolkit_doctor`). Results are numerical estimates that are only as good as the `.model` parameters: unreviewed.

## 1. Pin down the problem (ask before the first run, one short list)

Supply (range: worst case for a converter), every resistor and capacitor value including source impedance, coupling and bypass capacitors, the load, signal size and frequency, the transistor or MOSFET data the user really has (datasheet fT, Cobo, Cibo, Rds(on), Qg, rise/fall, dead time, inductor DCR and saturation current, capacitor ESR) and what number is wanted (bias point, gain, one bandwidth, ripple, efficiency). Do not invent parts and then report their numbers as the user's.

## 2. Tool choice

| Need | Tool |
|---|---|
| Parse and sanity-check a netlist, no ngspice | `spice_check_netlist` |
| Bias point `op`, sweep `dc`, small-signal `ac`, `tran` | `spice_simulate` |
| Number from a result table | `spice_measure` (`value_at`, `steady_state`, `rise_time`, `overshoot`, `settling_time`, `bandwidth_3db`) |
| Formula sizing of buck / boost / buck-boost (L, C, ripple, stresses, losses) | `electric_converter` |
| Chart of `result.csv` | `plot_xy` / `plot_panels` (skill `eng-plot-results`) |

## 3. Write the netlist (you write it from the words; there is no schematic reader)

First line is a title, ground is node `0`, `.model` lines are part of the text. **`.include`, `.lib`, `.control`, `.write`, A and N devices are refused**: open the model file with Read and paste its `.model` line into the netlist. The analysis comes from the tool arguments, not from dot-commands. Source for ac: `Vin src 0 DC 0 AC 1 SIN(0 10m 1k)` (the AC 1 is for `ac`, the SIN for `tran`).

Verified common-emitter amplifier (12 V, 47k/10k bias, Rc 2.2k, Re 470 bypassed by 100u, 600 ohm source, 1u in, 10u out, 10k load):
```
CE amplifier
Vcc vcc 0 12
Vin src 0 DC 0 AC 1 SIN(0 10m 1k)
Rs src a 600
Cin a b 1u
R1 vcc b 47k
R2 b 0 10k
Q1 c b e Q2N2222
Rc vcc c 2.2k
Re e 0 470
Ce e 0 100u
Cout c out 10u
RL out 0 10k
.model Q2N2222 npn (bf=200)
```
`op` with `outputs="v(b),v(e),v(c),i(vcc)"` gives 2.000 / 1.202 / 6.402 V and -2.757 mA (i of a source is negative when it supplies). `ac` with `start="1 Hz" stop="100 MHz" points=50 sweep="dec"` writes `result.csv` (`frequency, v(out).re, .im, .mag, .db, .phase_deg`); gain at 1 kHz 42.11 dB.

## 4. Pitfalls that change the answer

- **A model with only `bf=` has no capacitances**: gain stays flat to 100 MHz and any upper bandwidth is meaningless. Say so. With the user's datasheet add `cje` (from Cibo), `cjc` (from Cobo) and `tf = 1/(2 pi fT)`: `.model Q2N2222 npn (bf=200 cje=25p cjc=8p tf=0.53n)` gives an upper corner of about 419 kHz; label it a datasheet-based estimate and add a Miller hand check.
- **`bandwidth_3db` handles low-pass only** (reference = first sample). A band-pass or AC-coupled amplifier has no single bandwidth: take the peak gain (`v(out).max` of the ac result, 20 log10), the level is peak minus 3.0103 dB, then find each crossing with `spice_measure value_at column="v(out).db" at="..."` brackets and interpolate in log f (lower corner about 186 Hz, upper about 419 kHz here). Report both corners.
- **`value_at` needs the full column name** (`v(out).db`, not `v(out)`), and it labels a dB or degree value with unit `volt`: read the number as dB or degrees.
- **Phase is wrapped** to +-180 deg; an inverting stage jumps in mid-band. Say it is 180 deg plus the lag, or unwrap by hand.
- **Short `tran` runs include start-up** (coupling and bypass capacitors need many time constants). Use `start` to keep only the settled window (`start="100 ms" stop="110 ms" step="20 us"`) and compare the amplitude `(max - min)/2` with the ac gain times the input amplitude (1.264 V against 127 x 10 mV). For a converter use `use_initial_conditions=true` with `ic=` on the inductor and capacitor and a window of whole switching periods.
- A switch-mode netlist needs a body diode and dead time: `S` switches with `.model sw sw(ron=.. roff=1meg vt=2.5 vh=0)`, PULSE gate drives with a gap, `D` across the low side; without it the switch node spikes.

## 5. Converter sizing with `electric_converter`

It is **non-synchronous only**. Synchronous buck: give `diode_drop` = Io x Rds(on) of the low side, `gate_charge` = both FETs together with `gate_voltage`; dead-time loss `Vf Io 2 t_dead f` is not included, add it by hand. With no loss inputs it returns about 99.99 % efficiency (only ESR loss): never quote that. Size L and ripple at the **maximum** Vin. Example (28 V to 5 V, 3 A, 500 kHz): `inductance="10 uH" output_capacitance="47 uF" capacitor_esr="5 mohm" target_output_ripple="20 mV" switch_resistance="12 mohm" inductor_resistance="20 mohm" diode_drop="0.018 V" rise_time="10 ns" fall_time="10 ns" gate_charge="16 nC" gate_voltage="5 V"` gives duty 0.181, ripple 0.831 A pp. Hand check: `dI = (Vin - Vo) D / (f L)` with `D = Vo/Vin`, `C = dI / (8 f dV)`. A simulated 24 V synchronous buck (10 uH, 500 kHz, ripple window at 1.4 ms) gives 0.794 A pp against 0.792 A by formula.

## 6. Coverage limits and report

Outside the toolkit: efficiency from ideal-switch simulation, EMI, thermal, magnetics saturation, loop stability of a real controller, noise and Monte Carlo on parts, vendor models that need `.include` (inline them). Report as in `eng-toolkit`: the netlist or its list of values, assumptions the user did not give, the numbers with units, the hand check, and what the model cannot say.
