import json, struct, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

def load(path):
    b = open(path, "rb").read()
    jl = struct.unpack_from("<I", b, 12)[0]
    js = json.loads(b[20:20 + jl])
    off = 20 + jl
    bl = struct.unpack_from("<I", b, off)[0]
    binb = b[off + 8: off + 8 + bl]
    return js, binb

def acc(js, binb, i):
    a = js["accessors"][i]; bv = js["bufferViews"][a["bufferView"]]
    n = {"SCALAR":1,"VEC2":2,"VEC3":3,"VEC4":4}[a["type"]]
    dt = {5126:np.float32,5123:np.uint16,5125:np.uint32,5121:np.uint8}[a["componentType"]]
    start = bv.get("byteOffset",0)+a.get("byteOffset",0)
    arr = np.frombuffer(binb, dtype=dt, count=a["count"]*n, offset=start).reshape(a["count"], n)
    return arr

js, binb = load(sys.argv[1])
fig = plt.figure(figsize=(9, 5)); ax = fig.add_subplot(111, projection="3d")
allp = []

def srgb(c):
    c = np.clip(np.asarray(c, float)[:3], 0, 1)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)

def node_offset(js, mesh_index):
    shift = np.zeros(3)
    for node in js.get("nodes", []):
        if node.get("mesh") == mesh_index and "translation" in node:
            shift = np.array(node["translation"], float)
    return shift

for mesh_index, mesh in enumerate(js["meshes"]):
    shift = node_offset(js, mesh_index)
    for prim in mesh["primitives"]:
        if prim.get("mode", 4) != 4:
            continue
        pos = acc(js, binb, prim["attributes"]["POSITION"]).astype(float) + shift
        idx = acc(js, binb, prim["indices"]).reshape(-1, 3)
        if "COLOR_0" in prim["attributes"]:
            col = acc(js, binb, prim["attributes"]["COLOR_0"]).astype(float)
            if col.max() > 1.5:
                col = col / 255.0
        else:
            material = js.get("materials", [{}])[prim.get("material", 0)] if js.get("materials") else {}
            base = material.get("pbrMetallicRoughness", {}).get("baseColorFactor", [0.7, 0.7, 0.7, 1])
            col = np.tile(srgb(base), (len(pos), 1))
        # glTF (x,y,z)->plot (x, -z, y) i.e. back to model axes
        P = np.c_[pos[:, 0], -pos[:, 2], pos[:, 1]]
        tris = P[idx]
        tc = col[idx][:, :, :3].mean(axis=1)
        pc = Poly3DCollection(tris, facecolors=tc, edgecolors="none", linewidths=0)
        ax.add_collection3d(pc)
        allp.append(P)
P = np.vstack(allp); lo, hi = P.min(0), P.max(0); c = (lo+hi)/2; r = (hi-lo).max()/2
ax.set_xlim(c[0]-r, c[0]+r); ax.set_ylim(c[1]-r, c[1]+r); ax.set_zlim(c[2]-r, c[2]+r)
ax.view_init(elev=22, azim=-55); ax.set_axis_off()
print("extras legend:", {k: v for k, v in list(js["nodes"][0].get("extras", {}).items())[:6]})
fig.savefig(sys.argv[2], dpi=110, bbox_inches="tight"); print("saved")
