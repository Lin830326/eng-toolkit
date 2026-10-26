"""Truth for S4: 2 m vertical stainless pipe (1 1/2 in NPS Sch 40, 48.26 x 3.68 mm, AISI 304) clamped at its foot with a 12 kg blower housing at the top,
next to a 50 Hz (3000 rpm) motor; first natural frequencies and an axial-load (Euler) buckling check.

Truth: exact transcendental frequency equation of a uniform cantilever with a tip point mass (Euler-Bernoulli), the Rayleigh closed form for the first mode,
and an independent CalculiX beam-element *FREQUENCY run (B32 pipe elements, own deck). Euler load of a fixed-free column pi^2 E I / (2 L)^2.
No eng-toolkit code. Needs ccx (conda env eng-eda) only for the cross-check; it is skipped with a note if ccx is not found.
Run: python uat/truth/S4_exhaust_stack_modes.py   (set ENG_EDA_BIN to the env Library/bin folder if not under ~/miniconda3)
"""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

E = 193e9            # Pa, AISI 304 stainless (typical 193 GPa)
RHO = 8000.0         # kg/m3
OD, T = 48.26e-3, 3.68e-3     # 1 1/2 in NPS Sch 40 (ASME B36.10)
L = 2.0              # m, clamp to the tip mass
MTIP = 12.0          # kg, point mass at the free end (no rotary inertia)
F_MOTOR = 50.0       # Hz
LOAD = 1.5e3         # N, axial design load the person wants the stack to carry (e.g. a lifting eye)
YIELD = 215e6        # Pa, 304 minimum yield (ASTM A312 seamless)

ID = OD - 2 * T
A = np.pi / 4 * (OD ** 2 - ID ** 2)
I = np.pi / 64 * (OD ** 4 - ID ** 4)
m = RHO * A          # kg/m


def exact_modes(n=4, mtip=None, od=None, wall=None):
    mtip = MTIP if mtip is None else mtip
    od = OD if od is None else od
    wall = T if wall is None else wall
    id_ = od - 2 * wall
    a_ = np.pi / 4 * (od ** 2 - id_ ** 2)
    i_ = np.pi / 64 * (od ** 4 - id_ ** 4)
    m = RHO * a_
    mu = mtip / (m * L)
    f = lambda x: 1 + np.cos(x) * np.cosh(x) + x * mu * (np.cos(x) * np.sinh(x) - np.sin(x) * np.cosh(x))
    xs = np.linspace(0.05, 25, 50000)
    v = f(xs)
    roots = [brentq(f, xs[i], xs[i + 1]) for i in range(len(xs) - 1) if v[i] * v[i + 1] < 0][:n]
    return [x ** 2 / (2 * np.pi) * np.sqrt(E * i_ / (m * L ** 4)) for x in roots], roots


def rayleigh():
    k = 3 * E * I / L ** 3
    meff = MTIP + 33 / 140 * m * L
    return 1 / (2 * np.pi) * np.sqrt(k / meff), 1 / (2 * np.pi) * np.sqrt(k / MTIP)


def calculix_modes():
    bin_dir = Path(os.environ.get("ENG_EDA_BIN", str(Path.home() / "miniconda3" / "envs" / "eng-eda" / "Library" / "bin")))
    ccx = bin_dir / "ccx.exe"
    if not ccx.exists():
        return {"skipped": "ccx not found"}
    n = 20     # B32 elements
    z = np.linspace(0, L * 1000, 2 * n + 1)
    lines = ["*NODE"] + [f"{i + 1}, 0., 0., {z[i]:.6f}" for i in range(2 * n + 1)]
    lines += ["*ELEMENT, TYPE=B32R, ELSET=BEAM"] + [f"{e + 1}, {2 * e + 1}, {2 * e + 2}, {2 * e + 3}" for e in range(n)]
    top = 2 * n + 1
    lines += [f"*ELEMENT, TYPE=MASS, ELSET=TIP", f"{n + 1}, {top}", "*MASS, ELSET=TIP", f"{MTIP / 1000.0:.9g}",
              "*MATERIAL, NAME=SS", "*ELASTIC", f"{E / 1e6:.6g}, 0.3", "*DENSITY", f"{RHO * 1e-12:.9g}",
              "*BEAM SECTION, ELSET=BEAM, MATERIAL=SS, SECTION=PIPE", f"{OD * 500:.6f}, {T * 1000:.6f}", "1., 0., 0.",
              "*BOUNDARY", "1, 1, 6", "*STEP", "*FREQUENCY", "6", "*END STEP"]
    work = Path(tempfile.mkdtemp(prefix="s4_ccx_"))
    (work / "p.inp").write_text("\n".join(lines) + "\n")
    env = dict(os.environ, PATH=os.pathsep.join([str(bin_dir), str(bin_dir.parent / "mingw-w64" / "bin"), os.environ["PATH"]]))
    proc = subprocess.run([str(ccx), "-i", "p"], cwd=work, env=env, capture_output=True, text=True)
    dat = work / "p.dat"
    if not dat.exists():
        return {"error": f"ccx exit {proc.returncode}", "tail": (proc.stdout + proc.stderr)[-600:]}
    freqs = []
    for ln in dat.read_text().splitlines():
        p = ln.split()
        if len(p) == 5 and re.fullmatch(r"\d+", p[0]) and re.fullmatch(r"[-+0-9.eE]+", p[3]):
            freqs.append(float(p[3]))      # columns: mode, eigenvalue, real freq (rad/s), cycles/time, imaginary
    return {"frequencies_Hz_one_per_bending_plane_pair": [round(f, 3) for f in freqs[::2]], "element": "B32R pipe, 20 elements, tip mass element; the pipe is axisymmetric so every bending mode appears twice (two planes)"}


def main():
    f, roots = exact_modes()
    ray, ray_bare = rayleigh()
    ccx = calculix_modes()
    pcr_fixed_free = np.pi ** 2 * E * I / (2 * L) ** 2
    pcr_pinned = np.pi ** 2 * E * I / L ** 2
    r = np.sqrt(I / A)
    answers = {
        "pipe": {"OD_mm": OD * 1e3, "wall_mm": T * 1e3, "A_cm2": round(A * 1e4, 3), "I_cm4": round(I * 1e8, 3), "mass_kg_per_m": round(m, 3), "E_GPa": E / 1e9, "rho": RHO},
        "modes_Hz_exact": [round(x, 2) for x in f],
        "f1_Hz": round(f[0], 2),
        "f2_Hz": round(f[1], 2),
        "f1_Rayleigh_Hz": round(ray, 2),
        "f1_massless_beam_tip_mass_only_Hz": round(ray_bare, 2),
        "calculix_beam_modal_cross_check": ccx,
        "tip_mass_sweep_f1_f2_Hz": {str(k): [round(x, 2) for x in exact_modes(2, mtip=float(k))[0]] for k in (5, 8, 12, 15, 25, 40)},
        "other_pipes_f1_f2_Hz_12kg": {"2 in sch40 (60.3 x 3.91)": [round(x, 2) for x in exact_modes(2, od=60.3e-3, wall=3.91e-3)[0]],
                                      "1 1/2 in sch80 (48.3 x 5.08)": [round(x, 2) for x in exact_modes(2, od=48.3e-3, wall=5.08e-3)[0]]},
        "what_changes_f2": "the top mass hardly moves the second mode (8 kg about 50.3 Hz, 15 kg about 49.1 Hz: the tip is near a node of mode 2), so the quick fix she proposes does NOT help; "
                           "the stiffness-to-mass ratio of the pipe does: a 2 in sch 40 pipe puts f2 at about 63 Hz (26 % above 50 Hz); a lateral support or brace part way up the pipe is the other lever",
        "ratio_f2_over_motor": round(f[1] / F_MOTOR, 3),
        "ratio_motor_over_f1": round(F_MOTOR / f[0], 1),
        "verdict_vibration": "the FIRST mode (about 4 Hz) is far below the 50 Hz motor (frequency ratio about 12, no resonance with it), BUT the SECOND bending mode is at about "
                             "49.5 Hz, within 1 to 2 % of the motor frequency: a resonance risk for the 50 Hz imbalance force. A blower housing with its own rotary inertia "
                             "will shift it, which is why a tap test is advised; a person who only looks at the first mode would wrongly say it clears 50 Hz. The 3rd mode (about 156 Hz) is not near 50 or 100 Hz.",
        "verdict_buckling": {
            "fixed_free_K2_Pcr_kN": round(pcr_fixed_free / 1e3, 2),
            "pinned_pinned_wrong_Pcr_kN": round(pcr_pinned / 1e3, 2),
            "axial_load_kN": LOAD / 1e3,
            "safety_factor_against_Euler": round(pcr_fixed_free / LOAD, 1),
            "slenderness_KL_over_r": round(2 * L / r, 0),
            "axial_stress_MPa": round(LOAD / A / 1e6, 2),
            "note": "a cantilevered stack is a fixed-free column (K = 2); using K = 1 (pinned) overestimates the buckling load four times. Even so the stack is very safe against Euler buckling "
                    "(factor about 10 for 1.5 kN); the weight of the 12 kg blower is only 118 N. Buckling is not the problem here, the second mode is."},
        "caveats": "point mass with no rotary inertia and a rigid clamp; a real blower housing, a base plate that flexes, flow-induced loads and damping all move the modes; the person should measure with a tap test; 3000 rpm is a 50 Hz excitation (a 4-pole 1500 rpm motor would be 25 Hz, and 100 Hz electrical noise is the next harmonic)",
    }
    print(json.dumps({"answers": answers,
                      "how": "exact Euler-Bernoulli cantilever with tip mass: 1 + cos(x) cosh(x) + x mu (cos x sinh x - sin x cosh x) = 0, f = x^2/(2 pi) sqrt(EI/(m L^4)); Rayleigh closed form; CalculiX B32 beam modal run; Euler pi^2 E I/(K L)^2 with K = 2",
                      "tolerance": "f1 within 3 % (about 4.1 Hz; a massless-beam Rayleigh estimate of 4.4 or a lumped-mass value of 4.0 to 4.3 counts), f2 within 3 % (49.5 Hz; E = 200 GPa instead of 193 gives about 50.4 Hz, accept). "
                                   "The verdict must say the second mode sits at/near 50 Hz (resonance risk) if the assistant computed higher modes; an answer that only reports f1 is incomplete but not wrong. Buckling load within 3 % with K = 2; a K = 1 answer is wrong."},
                     indent=1, default=float))


if __name__ == "__main__":
    main()
