"""Truth for S3: 6 mm steel L bracket (80 mm wide, 100 mm tall leg clamped at its foot, 60 mm arm), 2 kN down on the arm's end face.

Independent of eng-toolkit: hand beam theory (a frame of two cantilever beams with the plate correction) and our own Gmsh + CalculiX
model (own geometry script with the 6 mm bend radius, own deck, own .frd reader) on three mesh sizes.
Needs gmsh and ccx (conda env eng-eda, see memory eng-toolkit-external-tools-installed). Run: python uat/truth/S3_l_bracket_fem.py
Set ENG_EDA_BIN to the env's Library/bin folder if it is not under the default miniconda path.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np

CONDA_BIN = Path(os.environ.get("ENG_EDA_BIN", str(Path.home() / "miniconda3" / "envs" / "eng-eda" / "Library" / "bin")))
GMSH = str(CONDA_BIN / "gmsh.exe")
CCX = str(CONDA_BIN / "ccx.exe")
E, NU = 210e3, 0.3          # MPa
T, W, H, A, R = 6.0, 80.0, 100.0, 60.0, 6.0   # mm: thickness, width (y), leg height (z), arm length (x), bend radius
P = 2000.0                  # N, downward on the arm end face x = 60


def hand_calc():
    a = A - T / 2            # lever from the leg mid-plane to the load line
    h = H - T / 2            # clamped foot to the arm mid-plane
    I = W * T ** 3 / 12
    out = {}
    for label, EI in (("plain_beam", E * I), ("wide_plate_cylindrical_bending", E / (1 - NU ** 2) * I)):
        drop = P * a ** 3 / (3 * EI) + P * a ** 2 * h / EI + P * h / (E * W * T)
        out[label] = {"tip_drop_mm": round(drop, 3), "tip_sideways_mm": round(P * a * h ** 2 / (2 * EI), 3)}
    M = P * a
    S = W * T ** 2 / 6
    out["moment_Nmm"] = M
    out["bending_stress_MPa"] = round(M / S, 1)
    out["axial_stress_MPa"] = round(P / (W * T), 2)
    out["vm_plane_strain_plate_MPa"] = round(M / S * np.sqrt(1 - NU + NU ** 2), 1)   # sigma_y = nu sigma_z at mid-width
    return out


def write_geo(h, path, bend_h=None):
    s = f"""SetFactory("OpenCASCADE");
Point(1) = {{0,0,0}}; Point(2) = {{{T},0,0}}; Point(3) = {{{T},0,{H - T - R}}}; Point(4) = {{{T + R},0,{H - T - R}}};
Point(5) = {{{T + R},0,{H - T}}}; Point(6) = {{{A},0,{H - T}}}; Point(7) = {{{A},0,{H}}}; Point(8) = {{0,0,{H}}};
Line(1) = {{1,2}}; Line(2) = {{2,3}}; Circle(3) = {{3,4,5}}; Line(4) = {{5,6}}; Line(5) = {{6,7}}; Line(6) = {{7,8}}; Line(7) = {{8,1}};
Curve Loop(1) = {{1,2,3,4,5,6,7}}; Plane Surface(1) = {{1}};
Extrude {{0,{W},0}} {{ Surface{{1}}; }}
Mesh.MeshSizeMin = {bend_h or h}; Mesh.MeshSizeMax = {h}; Mesh.ElementOrder = 2; Mesh.Algorithm3D = 1; Mesh.MshFileVersion = 2.2;
"""
    if bend_h:   # finer mesh in the bend region (a box around the fillet), coarse elsewhere
        s += f"""Field[1] = Box; Field[1].VIn = {bend_h}; Field[1].VOut = {h}; Field[1].XMin = -1; Field[1].XMax = {T + 2 * R + 6};
Field[1].YMin = -1; Field[1].YMax = {W + 1}; Field[1].ZMin = {H - T - 2 * R - 6}; Field[1].ZMax = {H + 1}; Field[1].Thickness = 8;
Background Field = 1;
"""
    path.write_text(s)


def read_msh(path):
    lines = path.read_text().splitlines()
    i = lines.index("$Nodes") + 1
    n = int(lines[i])
    nodes = {}
    for ln in lines[i + 1:i + 1 + n]:
        p = ln.split()
        nodes[int(p[0])] = [float(p[1]), float(p[2]), float(p[3])]
    i = lines.index("$Elements") + 1
    m = int(lines[i])
    tets = []
    for ln in lines[i + 1:i + 1 + m]:
        p = ln.split()
        typ, nt = int(p[1]), int(p[2])
        ids = [int(x) for x in p[3 + nt:]]
        if typ == 11:      # 10-node tet; gmsh nodes 9 and 10 are swapped relative to CalculiX
            ids[8], ids[9] = ids[9], ids[8]
            tets.append((int(p[0]), ids))
    return nodes, tets


def run_case(h, work, bend_h=None):
    work.mkdir(parents=True, exist_ok=True)
    geo, msh = work / "b.geo", work / "b.msh"
    write_geo(h, geo, bend_h)
    subprocess.run([GMSH, str(geo), "-3", "-o", str(msh), "-format", "msh2"], check=True, capture_output=True)
    nodes, tets = read_msh(msh)
    eps = 1e-6
    foot = [k for k, v in nodes.items() if v[2] < eps]
    tipset = {k for k, v in nodes.items() if abs(v[0] - A) < eps}
    mid = {(0, 1): 4, (1, 2): 5, (2, 0): 6, (0, 3): 7, (1, 3): 8, (2, 3): 9}   # tet10 mid-side node index of each corner pair (CalculiX order)
    edge = {}
    for pair, q in mid.items():
        edge[pair] = q
        edge[pair[::-1]] = q
    traction = P / (T * W)      # N/mm2 over the 6 x 80 end face
    fz = {}
    nfaces = 0
    for eid, ids in tets:
        for (a, b, c) in ((0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)):
            if ids[a] in tipset and ids[b] in tipset and ids[c] in tipset:
                pa, pb, pc = (np.array(nodes[ids[q]]) for q in (a, b, c))
                area = 0.5 * np.linalg.norm(np.cross(pb - pa, pc - pa))
                for pair in ((a, b), (b, c), (c, a)):    # a quadratic triangle: consistent load = area/3 on each mid-side node, none on the corners
                    n = ids[edge[pair]]
                    fz[n] = fz.get(n, 0.0) - traction * area / 3.0
                nfaces += 1
    face_load = [None] * nfaces
    inp = ["*NODE"] + [f"{k}, {v[0]:.9g}, {v[1]:.9g}, {v[2]:.9g}" for k, v in nodes.items()]
    inp += ["*ELEMENT, TYPE=C3D10, ELSET=EALL"] + [f"{eid}, " + ", ".join(map(str, ids)) for eid, ids in tets]
    inp += ["*NSET, NSET=FOOT"] + [", ".join(map(str, foot[i:i + 8])) for i in range(0, len(foot), 8)]
    inp += ["*MATERIAL, NAME=S", "*ELASTIC", f"{E}, {NU}", "*SOLID SECTION, ELSET=EALL, MATERIAL=S",
            "*BOUNDARY", "FOOT, 1, 3", "*STEP", "*STATIC", "*CLOAD"]
    inp += [f"{n}, 3, {f:.9g}" for n, f in fz.items()]
    inp += ["*NODE FILE", "U", "*EL FILE", "S", "*END STEP"]
    (work / "b.inp").write_text("\n".join(inp) + "\n")
    env = dict(os.environ, PATH=os.pathsep.join([str(CONDA_BIN), str(CONDA_BIN.parent / "mingw-w64" / "bin"), os.environ["PATH"]]))
    proc = subprocess.run([CCX, "-i", "b"], cwd=work, env=env, capture_output=True, text=True)
    frd = work / "b.frd"
    if not frd.exists():
        raise RuntimeError(f"ccx failed (exit {proc.returncode}): " + proc.stdout[-800:] + proc.stderr[-500:])
    return nodes, len(tets), parse_frd(frd), len(face_load)


def parse_frd(path):
    u, s = {}, {}
    cur = None
    for ln in path.read_text().splitlines():
        if ln.startswith(" -4"):
            cur = ln.split()[1]
        elif ln.startswith(" -3"):
            cur = None
        elif ln.startswith(" -1") and cur in ("DISP", "STRESS"):
            node = int(ln[3:13])
            vals = [float(ln[13 + 12 * i:25 + 12 * i]) for i in range((len(ln) - 13) // 12)]
            (u if cur == "DISP" else s)[node] = vals
    return u, s


def vm(v):
    sxx, syy, szz, sxy, syz, szx = v[:6]
    return float(np.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2) + 3 * (sxy ** 2 + syz ** 2 + szx ** 2)))


def plane_strain_case(h):
    """Mid-width slice of the bracket as a plane-strain CPE6 model: a cheap way to see which stress peaks converge."""
    work = Path(tempfile.mkdtemp(prefix="s3_2d_"))
    geo = f"""SetFactory("OpenCASCADE");
Point(1) = {{0,0,0}}; Point(2) = {{{T},0,0}}; Point(3) = {{{T},0,{H - T - R}}}; Point(4) = {{{T + R},0,{H - T - R}}};
Point(5) = {{{T + R},0,{H - T}}}; Point(6) = {{{A},0,{H - T}}}; Point(7) = {{{A},0,{H}}}; Point(8) = {{0,0,{H}}};
Line(1) = {{1,2}}; Line(2) = {{2,3}}; Circle(3) = {{3,4,5}}; Line(4) = {{5,6}}; Line(5) = {{6,7}}; Line(6) = {{7,8}}; Line(7) = {{8,1}};
Curve Loop(1) = {{1,2,3,4,5,6,7}}; Plane Surface(1) = {{1}};
Mesh.MeshSizeMin = {h}; Mesh.MeshSizeMax = {h}; Mesh.ElementOrder = 2; Mesh.MshFileVersion = 2.2;
"""
    (work / "b.geo").write_text(geo)
    subprocess.run([GMSH, str(work / "b.geo"), "-2", "-o", str(work / "b.msh"), "-format", "msh2"], check=True, capture_output=True)
    lines = (work / "b.msh").read_text().splitlines()
    i = lines.index("$Nodes") + 1
    n = int(lines[i])
    nodes = {}
    for ln in lines[i + 1:i + 1 + n]:
        p = ln.split()
        nodes[int(p[0])] = (float(p[1]), float(p[3]))      # (x, z) of the profile
    i = lines.index("$Elements") + 1
    m = int(lines[i])
    tris = []
    for ln in lines[i + 1:i + 1 + m]:
        p = ln.split()
        if int(p[1]) == 9:                                     # 6-node triangle
            tris.append((int(p[0]), [int(x) for x in p[3 + int(p[2]):]]))
    tip = sorted([k for k, v in nodes.items() if abs(v[0] - A) < 1e-6], key=lambda k: nodes[k][1])
    foot = [k for k, v in nodes.items() if v[1] < 1e-6]
    q = P / W                                                  # N per mm of width
    fz = {k: 0.0 for k in tip}
    edge_len = nodes[tip[-1]][1] - nodes[tip[0]][1]
    for j in range(0, len(tip) - 2, 2):                        # consistent nodal loads of a quadratic edge: 1/6, 4/6, 1/6
        c0, mid_, c1 = tip[j], tip[j + 1], tip[j + 2]
        seg = nodes[c1][1] - nodes[c0][1]
        w = q / edge_len
        fz[c0] -= w * seg / 6
        fz[mid_] -= w * 4 * seg / 6
        fz[c1] -= w * seg / 6
    inp = ["*NODE"] + [f"{k}, {v[0]:.9g}, {v[1]:.9g}" for k, v in nodes.items()]
    inp += ["*ELEMENT, TYPE=CPE6, ELSET=EALL"] + [f"{e}, " + ", ".join(map(str, ids)) for e, ids in tris]
    inp += ["*NSET, NSET=FOOT"] + [", ".join(map(str, foot[a:a + 8])) for a in range(0, len(foot), 8)]
    inp += ["*MATERIAL, NAME=S", "*ELASTIC", f"{E}, {NU}", "*SOLID SECTION, ELSET=EALL, MATERIAL=S", "*BOUNDARY", "FOOT, 1, 2", "*STEP", "*STATIC", "*CLOAD"]
    inp += [f"{k}, 2, {f:.9g}" for k, f in fz.items()] + ["*NODE FILE", "U", "*EL FILE", "S", "*END STEP"]
    (work / "b.inp").write_text("\n".join(inp) + "\n")
    env = dict(os.environ, PATH=os.pathsep.join([str(CONDA_BIN), str(CONDA_BIN.parent / "mingw-w64" / "bin"), os.environ["PATH"]]))
    proc = subprocess.run([CCX, "-i", "b"], cwd=work, env=env, capture_output=True, text=True)
    if not (work / "b.frd").exists():
        raise RuntimeError(f"ccx 2D failed (exit {proc.returncode})")
    u, s = parse_frd(work / "b.frd")
    vmv = {k: vm(v) for k, v in s.items()}

    def peak(sel):
        k = max((k for k in vmv if sel(nodes[k])), key=lambda k: vmv[k])
        return round(vmv[k], 1), [round(c, 2) for c in nodes[k]]
    tipk = [k for k, v in nodes.items() if abs(v[0] - A) < 1e-6]
    return {"element_size_mm": h, "tri6_elements": len(tris), "tip_drop_mm": round(-float(np.mean([u[k][1] for k in tipk])), 3),
            "mid_leg_von_mises_MPa": round(float(np.mean([vmv[k] for k, v in nodes.items() if abs(v[0] - T) < 1e-6 and 30 < v[1] < 70])), 1),
            "peak_at_clamped_foot_MPa": peak(lambda v: v[1] < 15), "peak_at_inside_of_bend_MPa": peak(lambda v: v[1] > 80 and v[0] < 20)}


def main():
    base = Path(tempfile.mkdtemp(prefix="s3_truth_"))
    rows = []
    for h in (4.0, 2.5, 1.8):
        nodes, ne, (u, s), nload = run_case(h, base / f"h{h}")
        tipn = [k for k, v in nodes.items() if abs(v[0] - A) < 1e-6]
        uz = float(np.mean([u[k][2] for k in tipn]))
        ux = float(np.mean([u[k][0] for k in tipn]))
        mag = max(float(np.linalg.norm(u[k][:3])) for k in u)
        vmax = {k: vm(s[k]) for k in s}
        kmax = max(vmax, key=vmax.get)
        far = [k for k in vmax if nodes[k][2] > 15]            # away from the clamped foot
        kfar = max(far, key=lambda k: vmax[k])
        mid = [k for k in vmax if abs(nodes[k][0] - T) < 1e-6 and 25 < nodes[k][2] < 70 and 30 < nodes[k][1] < 50]
        rows.append({"element_size_mm": h, "tet10_elements": ne, "loaded_faces": nload, "tip_drop_mm": round(-uz, 3), "tip_sideways_mm": round(ux, 3),
                     "max_displacement_magnitude_mm": round(mag, 3),
                     "max_von_mises_MPa": round(vmax[kmax], 1), "where_max_xyz": [round(c, 1) for c in nodes[kmax]],
                     "max_von_mises_away_from_foot_z_gt_15_MPa": round(vmax[kfar], 1), "where_far_xyz": [round(c, 1) for c in nodes[kfar]],
                     "mid_leg_inner_face_von_mises_MPa": round(float(np.mean([vmax[k] for k in mid])), 1)})
    hc = hand_calc()
    plane = [plane_strain_case(h) for h in (1.0, 0.5, 0.25)]
    answers = {
        "hand_calc": hc,
        "fine_mesh_runs_3d": rows,
        "plane_strain_slice_runs": plane,
        "plane_strain_slice_note": "an extra finer slice at 0.125 mm (139k elements, run once and not repeated here) gave: clamped-foot peak 351.5 MPa, inside-of-bend peak 256.7 MPa, mid-leg 214.8 MPa",
        "tip_vertical_deflection_mm": rows[-1]["tip_drop_mm"],
        "tip_total_displacement_magnitude_mm": rows[-1]["max_displacement_magnitude_mm"],
        "verdict_deflection": "the tip drops about 2.2 mm (hand: 2.27 with the wide-plate factor, 2.49 plain beam) and ALSO moves sideways about 1.7 mm (the leg bends); "
                              "the displacement magnitude a tool reports (about 2.9 mm) is not the vertical deflection and must be labelled as magnitude",
        "verdict_stress": "three different stress levels: (1) the leg away from the ends carries a nominal bending stress of 237 MPa (hand), von Mises about 220 MPa in the 3D model (215 plane strain), "
                          "converged and stable with the mesh; (2) the inside of the 6 mm bend, where the von Mises peaks at about 255 to 275 MPa (the plane-strain slice converges to 257 MPa, "
                          "the 3D tet meshes of 4, 2.5 and 1.8 mm give 257, 267, 276 MPa and a coarse 8 mm mesh would under-read it, 248 MPa) so this peak is real but only known to about plus or minus 8 %; "
                          "(3) the clamped foot (a fully fixed face), where the value is a support singularity that grows without bound as the mesh is refined (plane strain: 215, 238, 288, 352 MPa at 1, 0.5, 0.25, 0.125 mm) and means nothing for the part. "
                          "Against S235 (yield 235 MPa): the leg is at 94 % of yield and the bend is 10 to 18 % above yield in a linear model: the bracket yields locally at the inside of the bend, so it is NOT acceptable for a 2 kN load without a thicker plate or a gusset.",
        "convergence": "tip vertical deflection converged (2.20, 2.21, 2.21 mm over 4, 2.5, 1.8 mm); mid-leg stress converged (220 MPa); maximum von Mises is NOT converged: the peak keeps rising with refinement and its location depends on the mesh "
                       "(foot at coarse sizes, inside of the bend at fine sizes). A fair answer reports the deflection and the leg stress with confidence, the maximum von Mises as 'about 260 to 275 MPa at the bend, not converged', and the foot value as an artefact.",
        "limits": "linear elastic: at or above yield the real part deforms plastically, so these numbers would not hold; a bolted or welded mounting is not a rigid fixed face",
    }
    print(json.dumps({"answers": answers,
                      "how": "hand frame formulas plus own Gmsh (OpenCASCADE profile with 6 mm bend radius) and CalculiX C3D10 runs at 4, 2.5 and 1.8 mm, and a plane-strain CPE6 slice at 1, 0.5, 0.25 mm; foot face z=0 fully fixed; end face x=60 loaded by a 2 kN traction in -z; nodal stresses read from the .frd",
                      "tolerance": "tip vertical deflection within 5 % of 2.2 mm (hand 2.27 with the plate factor, 2.49 plain beam); 2.9 mm is acceptable only if labelled the displacement magnitude. Stress in the leg within 8 % of 220 MPa von Mises (237 MPa bending hand value). A maximum von Mises of 250 to 280 MPa from the fem tool is acceptable only if the answer says whether it is converged (it is not: it keeps rising with the mesh) and where it sits (foot or inside of the bend); an assistant that quotes one number as the converged maximum, or that says the model is converged because the deflection is, is wrong. The yield judgement (leg near yield, bend above it, bracket marginal or unacceptable for S235) must match; plasticity cannot be predicted by the toolkit and should be said."},
                     indent=1, default=float))


if __name__ == "__main__":
    main()
