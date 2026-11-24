---
name: eng-circuit-sim
description: Use FIRST for "simulate this amplifier or filter", bias point, gain, Bode plot, bandwidth, converter waveform or ripple (ngspice, spice_* tools), and to size a buck or boost with electric_converter. Netlists, model limits, corners.
---

# Circuit simulation workflow (eng-toolkit)

Load `eng-toolkit` too. ngspice is a separate program (`toolkit_doctor`). Results are numerical estimates that are only as good as the `.model` parameters: unreviewed.

## 1. Pin down the problem (ask before the first run, one short list)

Supply (range: worst case for a converter), every resistor and capacitor value **including source impedance, coupling and bypass capacitors and the load (ask them; if you must pick one, label it "assumed")**, signal size and frequency, the transistor or MOSFET data the user really has (datasheet fT, Cobo, Cibo, Rds(on), Qg, rise/fall, dead time, inductor DCR and saturation current, capacitor ESR) and what number is wanted (bias point, gain, one bandwidth, ripple, efficiency). Do not invent parts and then report their numbers as the user's.

## 2. Tool choice

| Need | Tool |
|---|---|
| Parse and sanity-check a netlist, no ngspice | `spice_check_netlist` |
| Bias point `op`, sweep `dc`, small-signal `ac`, `tran` | `spice_simulate` |
| Number from a result table | `spice_measure` (`value_at`, `steady_state`, `rise_time`, `overshoot`, `settling_time`, `bandwidth_3db` low-pass, `bandpass_3db`) |
| Formula sizing of buck / boost / buck-boost (L, C, ripple, stresses, losses) | `electric_converter` |
| Chart of `result.csv` | `plot_xy` / `plot_panels` as PNG, `format="png"` (skill `eng-plot-results`) |

## 3. Write the netlist (you write it from the words; there is no schematic reader)

First line is a title, ground is node `0`, `.model` lines are part of the text. **`.include`, `.lib`, `.control`, `.write`, A and N devices are refused**: open the model file with Read and paste its `.model` line into the netlist. The analysis comes from the tool arguments, not from dot-commands. Source for ac: `Vin src 0 DC 0 AC 1 SIN(0 10m 1k)` (the AC 1 is for `ac`, the SIN for `tran`).

Verified common-emitter amplifier (example values: 15 V, 68k/12k bias, Rc 3.3k, Re 680 bypassed by 220u, 50 ohm source, 2.2u in, 4.7u out, 22k load):
```
CE amplifier
Vcc vcc 0 15
Vin src 0 DC 0 AC 1 SIN(0 5m 2k)
Rs src a 50
Cin a b 2.2u
R1 vcc b 68k
R2 b 0 12k
Q1 c b e Q1M
Rc vcc c 3.3k
Re e 0 680
Ce e 0 220u
Cout c out 4.7u
RL out 0 22k
.model Q1M npn (bf=250 cje=18p cjc=6p tf=0.4n)
```
`op` with `outputs="v(b),v(e),v(c),i(vcc)"` gives 2.168 / 1.375 / 8.352 V and -2.203 mA (i of a source is negative when it supplies). `ac` with `start="1 Hz" stop="100 MHz" points=50 sweep="dec"` writes `result.csv` (`frequency, v(out).re, .im, .mag, .db, .phase_deg`); gain at 2 kHz 46.80 dB (219 V/V).

## 4. Pitfalls that change the answer

- **A model with only `bf=` has no capacitances**: gain stays flat to 100 MHz and any upper bandwidth is meaningless. Say so. With the user's datasheet add `cje` (from Cibo), `cjc` (from Cobo) and `tf = 1/(2 pi fT)` (fT 400 MHz gives 0.40 ns); the model in the example gives an upper corner of 3.75 MHz; label it a datasheet-based estimate and add a Miller hand check. Without the user's datasheet say the model is generic.
- **`bandwidth_3db` is for a low-pass response** (reference = first sample); on an AC-coupled or band-pass amplifier it answers `measure.not_found` that names **`bandpass_3db`**: it takes the peak gain, finds the -3.0103 dB corner on each side and returns the bandwidth `f_upper - f_lower` with `lower_corner_3db`, `upper_corner_3db`, `peak_frequency`, `peak_gain_db` in `values` (example above: 80.9 Hz and 3.75 MHz, peak 46.81 dB at 17.4 kHz). Report both corners; a corner outside the sweep is `measure.not_found`, so widen `start`/`stop`.
- **Units follow the column**: `v(out).db` is decibel, `.phase_deg` degree, `v(out)` volt or ampere; on an ac table a bare `v(out)` means `v(out).mag` (a V/V gain for a 1 V source, labelled volt) for every measurement.
- **Phase is wrapped** to +-180 deg: the example reads -177.6 deg at 2 kHz and +164.9 deg at 1 MHz (that is -195 deg). Use an unwrapped phase column if the table has one, else unwrap by hand, and say an inverting stage is 180 deg plus the lag.
- **Short `tran` runs include start-up** (coupling and bypass capacitors need many time constants). Use `start` to keep only the settled window (`start="100 ms" stop="101 ms" step="5 us"`) and compare the amplitude `(max - min)/2` with the ac gain times the input amplitude (1.091 V against 219 x 5 mV). For a converter use `use_initial_conditions=true` with `ic=` on the inductor and capacitor and a window of whole switching periods.
- A switch-mode netlist needs a body diode and dead time: `S` switches with `.model sw sw(ron=.. roff=1meg vt=2.5 vh=0)`, PULSE gate drives with a gap, `D` across the low side; without it the switch node spikes.

## 5. Converter sizing with `electric_converter`

The default stage has a freewheel diode. For a synchronous buck or boost give `synchronous=true`, `low_side_resistance` (Rds(on) of the low-side FET), `dead_time` per edge (the body diode, `diode_drop`, conducts: loss `Vf Io 2 t f`, in the result as `dead_time_loss`) and `gate_charge` for both FETs with `gate_voltage`; below the CCM boundary it acts as diode emulation. With no loss input the result warns "Ideal" (`data.losses_given` false, efficiency 100 %): never quote that. Size L and ripple at the **maximum** Vin. **Ask the person's MOSFET and inductor data, or label it "assumed"**; give `gate_charge` as the SUM of both FETs. Example (14 V to 3.3 V, 2 A, 400 kHz): `synchronous=true inductance="6.8 uH" output_capacitance="44 uF" capacitor_esr="4 mohm" target_output_ripple="15 mV" switch_resistance="18 mohm" low_side_resistance="9 mohm" inductor_resistance="25 mohm" diode_drop="0.7 V" dead_time="25 ns" rise_time="8 ns" fall_time="8 ns" gate_charge="15 nC" gate_voltage="5 V"` gives duty 0.241, ripple 0.940 A pp (hand `(Vin - Vo) D/(f L)` with D = Vo/Vin: 0.927), peak 2.47 A, dead-time loss 28 mW, efficiency 95.7 %, total ripple 10.4 mV and **`minimum_output_capacitance` 26.1 uF: always report it next to the capacitor on hand**. Hand check `C = dI / (8 f dV)`. A simulated steady-state window of whole switching periods should agree with the formula ripple within a few percent: show both numbers.

## 6. Coverage limits and report

Outside the toolkit: efficiency from ideal-switch simulation, EMI, thermal, magnetics saturation, loop stability of a real controller, noise and Monte Carlo on parts, vendor models that need `.include` (inline them). Report as in `eng-toolkit`: the netlist or its list of values, assumptions the user did not give, the numbers with units, the hand check, and what the model cannot say.
