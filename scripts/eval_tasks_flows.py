"""Eval tasks for the flow skills (fatigue, motor/drive, optimize, plot, 3D view).

Every expected number comes from running the real tool and an independent calculation (comments say how).
Loaded by scripts/eval_plugin.py.
"""

TASKS = []

# --- eng-3d-view
TASKS += [
    dict(
        id="V1_plate_glb",
        prompt=(
            "Build this part with the eng-toolkit tools: a steel plate 100 mm x 50 mm x 20 mm (density 7850 kg/m^3) with a "
            "10 mm diameter hole drilled right through the 20 mm thickness at the centre of the 100 x 50 face, and R2 mm rounds "
            "on the four edges that run along the 20 mm thickness. Tell me its volume in mm^3, its total surface area in mm^2 "
            "and its mass in kg, and write a GLB coloured by face kind that I can open in a 3D viewer. Give me the path of the "
            "GLB file and say what the colours mean."
        ),
        nums=[([98360.5, 98360.53], 0.001), ([16395.7], 0.001), ([0.77213, 0.7721], 0.002)],
        words=[r"\.glb", r"plane|planar|cylind"],
        tools=True,
    ),
    dict(
        id="V2_fem_uz_map",
        prompt=(
            "A steel cantilever 100 mm x 10 mm x 10 mm (E = 210 GPa, nu = 0.3) is clamped on its x = 0 face and carries a 1 kN "
            "force in -z spread over its x = 100 mm face. Build the part, run the finite element chain with 2.5 mm elements and "
            "then with 1.25 mm elements, and make a GLB coloured by the vertical displacement uz. Report the tip deflection in mm "
            "from both meshes, the beam-theory value P L^3/(3 E I), the range of the colour bar in mm and which colour stands "
            "for zero, and say in one or two sentences what this picture does not prove."
        ),
        nums=[([1.9045, 1.9048, 1.9059], 0.01)],
        words=[r"gr[ae]y", r"diverging|symmetric|blue", r"converg|refin|finer|mesh", r"surface|linear|small|not (prove|show|predict|valid)|does not|doesn't|cannot"],
        tools=True,
    ),
    dict(
        id="V3_view_cap_refusal",
        prompt=(
            "Build a solid sphere of diameter 120 mm with geometry_make_part (STEP only), then ask geometry_view for a coloured GLB "
            "of it by face kind with mesh_deflection 0.001 mm. I only want to know whether that deflection is allowed: if the tool "
            "refuses, explain why, with the number of triangles it expects, the cap it applies, and the deflection it suggests "
            "instead. Do not mesh at the suggested value."
        ),
        nums=[([622581], 0.003), ([600000], 0.001), ([0.0012, 0.00125], 0.05)],
        words=[r"refus|exceed|too (many|fine)|not allowed|above the (cap|limit)|over the (cap|limit)"],
        tools=True,
    ),
]

# --- eng-fatigue-check
TASKS += [
    dict(
        id="F1_shaft_infinite_life",
        prompt=(
            "A solid round steel shaft, 30 mm diameter, machined surface, has Su = 690 MPa and Sy = 580 MPa. "
            "It carries a bending load that gives an alternating stress amplitude of 110 MPa on top of a "
            "constant mean stress of 60 MPa. Design reliability is 99 %, operating temperature factor 1. "
            "Use Shigley's Marin factors and the Goodman mean-stress line. What is the corrected endurance "
            "limit in MPa, the equivalent fully reversed amplitude in MPa, and the Goodman safety factor? "
            "Does the shaft have infinite life?"
        ),
        nums=[([193.06, 193.1, 193.0], 0.005), ([120.48, 120.5], 0.005), ([1.523, 1.52], 0.005)],
        words=["infinite|indefinite|endurance"],
        tools=True,
    ),
    dict(
        id="F2_rainflow_miner_plot",
        prompt=(
            "A bracket sees this stress history in MPa, one block, turning points in order: "
            "0, 300, -100, 250, 50, 350, -150, 200, 0. Count it by rainflow (ASTM E1049, treat the block as a "
            "single non-repeating history so the unclosed residue is counted as half cycles). The stress-life "
            "curve is a straight line on log-log axes through stress amplitude 400 MPa at 1000 cycles and "
            "200 MPa at 1,000,000 cycles, with no endurance limit and no mean-stress correction. "
            "What is the largest stress range counted in MPa, the Miner damage per block, and how many "
            "blocks to failure? Also give me a bar chart of the cycle count by stress range."
        ),
        nums=[([500.0], 0.001), ([6.9004e-6, 6.90e-6], 0.005), ([144919.0, 1.449e5, 144900.0], 0.005)],
        words=["svg|chart|plot"],
        tools=True,
    ),
    dict(
        id="F3_crack_growth_life",
        prompt=(
            "A steel plate 100 mm wide and 10 mm thick (yield 700 MPa, fracture toughness Kc = 60 MPa*sqrt(m)) "
            "has a central through crack of initial half-length a = 2 mm. The remote tensile stress cycles "
            "from 0 to 200 MPa. Use the finite-width correction Y = sqrt(sec(pi*a/W)) with a the half-length "
            "and W the full width, and Paris growth da/dN = C*(dK)^m with C = 1e-12 m/cycle/(MPa*sqrt(m))^3 "
            "and m = 3. What is the critical half-crack length in mm and how many cycles does the crack need "
            "to grow from 2 mm to failure?"
        ),
        nums=[([22.05, 22.047, 0.022047], 0.003), ([667800.0, 6.678e5, 667800.4], 0.003)],
        tools=True,
    ),
]

# --- eng-motor-drive-sizing
TASKS += [
    dict(
        id="M1_gearmotor_profile",
        prompt=(
            "A rotary axis has a load inertia of 0.8 kg*m^2 and a constant friction torque of 3 N*m "
            "that always opposes motion. It is driven through a 10:1 reducer (the motor turns 10 times for "
            "one output turn) with a transmission efficiency of 0.92, and the motor rotor inertia is "
            "0.0004 kg*m^2. One move takes 3 s: the load speed ramps linearly from 0 to 12 rad/s in 0.6 s, "
            "stays at 12 rad/s for 1.8 s, then ramps linearly back to 0 in 0.6 s. For that move give the "
            "peak motor torque in N*m, the RMS motor torque over the 3 s, the peak motor speed in rpm and "
            "the energy regenerated during braking in J."
        ),
        nums=[
            ([2.145, 2.1452], 0.01),
            ([1.144, 1.1445], 0.01),
            ([1146.0, 1145.9], 0.005),
            ([45.9, 45.94], 0.01),
        ],
        tools=True,
    ),
    dict(
        id="M2_induction_load_point",
        prompt=(
            "A three-phase induction motor, 460 V line-to-line, 60 Hz, 4 poles, wye connected, has "
            "per-phase stator-referred parameters R1 = 0.641 ohm, X1 = 1.106 ohm, Xm = 26.3 ohm, "
            "R2 = 0.332 ohm, X2 = 0.464 ohm, no core-loss resistance, a constant friction and windage "
            "loss of 1100 W and no stray loss. It drives a constant 60 N*m shaft load. What are the "
            "speed in rpm, the line current in A and the efficiency? What is the largest constant shaft "
            "torque it can carry, and can it handle a 230 N*m load?"
        ),
        nums=[
            ([1758.2, 1758.18], 0.002),
            ([19.65, 19.648], 0.01),
            ([0.8383, 83.83, 83.8], 0.01),
            ([223.5, 223.50], 0.01),
        ],
        words=["cannot|can't|can not|not able|unable|exceed|stall|no stable|refus|no solution|too (high|large|much)|beyond"],
        tools=True,
    ),
    dict(
        id="M3_pmsm_field_weakening",
        prompt=(
            "A surface-mounted PMSM has 4 pole pairs, phase resistance 0.05 ohm, Ld = Lq = 0.2 mH, "
            "permanent-magnet flux linkage 0.02 Wb (peak phase value) and is fed by an inverter from a "
            "48 V DC bus with linear space-vector modulation (no overmodulation) and a 100 A peak phase "
            "current limit. At a mechanical speed of 400 rad/s it must deliver 5 N*m: what d-axis "
            "current and what peak phase current are needed? What is the maximum torque available at "
            "400 rad/s? Can it deliver 5 N*m at 1000 rad/s, and if not, what is the most it can deliver there?"
        ),
        nums=[
            ([-33.73, 33.73], 0.005),
            ([53.61, 53.608], 0.005),
            ([8.045, 8.0453], 0.005),
            ([3.378, 3.38], 0.01),
        ],
        words=["cannot|can't|can not|not possible|not able|unable|exceed|infeasible|refus|insufficient|not (be )?(deliver|reach|achiev)"],
        tools=True,
    ),
]

# --- eng-optimize
TASKS += [
    dict(
        id="O1_feed_blend_lp",
        prompt=(
            "A feed mill mixes a 1000 kg batch from corn, soybean meal and wheat bran. Per kg: corn costs 0.25 USD and "
            "contains 8% protein, 3.4 Mcal energy and 2.5% fibre; soybean meal costs 0.48 USD, 44% protein, 2.5 Mcal and "
            "7% fibre; wheat bran costs 0.18 USD, 15% protein, 1.9 Mcal and 11% fibre. The batch must hold at least "
            "180 kg protein and at least 2800 Mcal energy, at most 50 kg fibre, and at most 300 kg wheat bran. "
            "(a) What is the minimum batch cost in USD and how many kg of corn, soybean meal and bran does it use? "
            "(b) By how many USD does the minimum cost rise per extra kg of protein demanded (the shadow price of the "
            "protein requirement)? (c) The customer now asks for 300 kg protein per batch instead of 180. Can the mill "
            "do it? Explain, and give the highest protein content in kg per batch that this recipe sheet allows."
        ),
        nums=[
            ([295.08, 295.1], 0.002),
            ([590.2, 590.16], 0.003),
            ([245.9], 0.003),
            ([163.9, 163.93], 0.003),
            ([0.827, 0.8270, 0.82696], 0.01),
            ([280.0], 0.003),
        ],
        words=["infeasible|not possible|cannot|can't|impossible|no feasible|not feasible"],
        tools=True,
    ),
    dict(
        id="O2_cooling_fit",
        prompt=(
            "A cast part is quenched and its surface temperature is read at times t = 0, 2, 4, 6, 8, 10, 14, 18, 24, "
            "30 min: T = 100.0, 87.8, 77.5, 69.0, 62.7, 57.1, 49.9, 45.6, 39.9, 37.4 degC. Fit T = Tinf + A*exp(-t/tau) "
            "by ordinary unweighted least squares with Tinf, A and tau all unknown. Give Tinf in degC, tau in min, and "
            "the 95% confidence interval of tau, taking the noise from the residual scatter (Student t, n-3 degrees "
            "of freedom)."
        ),
        nums=[
            ([35.10, 35.097], 0.003),
            ([9.389, 9.39], 0.003),
            ([8.918, 8.92], 0.003),
            ([9.860, 9.86], 0.003),
        ],
        tools=True,
    ),
    dict(
        id="O3_tube_strut",
        prompt=(
            "Size a thin-walled round steel tube (E = 200 GPa, density 7850 kg/m^3) as a pin-ended strut of length "
            "1.2 m carrying 40 kN compression. Minimise its mass by choosing the outer diameter D (between 20 and "
            "100 mm) and the wall thickness t (between 1.5 and 8 mm), subject to: compressive stress F/A at most "
            "150 MPa; Euler buckling load pi^2*E*I/L^2 at least 3 times the 40 kN; and D/t at most 20, where "
            "A = pi/4*(D^2 - d^2), I = pi/64*(D^4 - d^4) and d = D - 2t. What are D and t in mm, the tube mass in kg, "
            "and which constraints are active at the optimum?"
        ),
        nums=[
            ([47.72, 47.7], 0.005),
            ([2.386, 2.39], 0.005),
            ([3.201, 3.20], 0.005),
        ],
        words=["buckl", r"D\s*/\s*t|diameter.{0,15}thickness|slender"],
        tools=True,
    ),
]

# --- eng-plot-results
TASKS += [
    dict(id="P1_rainflow_histogram",
         prompt="A strain-gauge channel gave this stress history in MPa (turning points in order, first and last value 0): "
                "[0, 130, -20, 90, -40, 50, -140, 80, -20, 100, -30, 110, -30, 120, -80, 0]. Count the cycles with the "
                "ASTM E1049 rainflow method and draw a histogram of the cycle ranges with 4 equal-width bins over the data "
                "range, using the rainflow table as it comes (one entry per table row). Give me the three inner bin edges "
                "in MPa, how many entries fall in each of the 4 bins, and the total number of entries.",
         nums=[([127.5], 0.001), ([175.0], 0.001), ([222.5], 0.001), ([10.0], 0.001), ([4.0], 0.001), ([3.0], 0.001)],
         words=["bin|histogram"], tools=True),
    dict(id="P2_sn_loglog",
         prompt="Fatigue test failures (stress amplitude, cycles to failure): 450 MPa 2e4, 380 MPa 6e4, 320 MPa 2e5, "
                "270 MPa 7e5, 230 MPa 2.5e6, 195 MPa 9e6. Fit a Basquin S-N curve with the ASTM E739 regression (log life on "
                "log stress) and draw the fitted curve as an SVG chart with both axes logarithmic, default chart size, "
                "using the tool's default curve table. Report the Basquin coefficient a in MPa, the exponent b, how many "
                "points were drawn, and the upper limit of the cycles axis.",
         nums=[([1710.1, 1710.0, 1710.12], 0.003), ([-0.1363, -0.13631], 0.005), ([61.0], 0.001),
               ([1e9, 1000000000.0], 0.001)],
         words=["log"], tools=True),
    dict(id="P3_bode_log_refusal",
         prompt="For a 4th-order Butterworth low-pass filter (sampling rate 8 kHz, cutoff 1 kHz) I want a Bode plot "
                "(magnitude and phase) of its frequency response table with a logarithmic frequency axis. If that cannot be "
                "drawn, tell me exactly why. Then draw the magnitude only on a linear frequency axis from 0 to 2000 Hz with "
                "the gain axis from -80 to 5 dB, and tell me how many points the filter response table has and how many of "
                "them lie outside the plotted window.",
         nums=[([512.0], 0.001), ([256.0], 0.001)],
         words=["0 Hz|zero|non-positive|not positive|positive", "refus|cannot|can't|not possible|not allowed|fail"], tools=True),
]
