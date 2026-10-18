"""Geometry code must not need Open3D (231 MB) or OCP to weld and classify a mesh: proven in a fresh interpreter."""

import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent(
    """
    import sys

    sys.modules["open3d"] = None  # an import of it now raises ImportError
    sys.modules["OCP"] = None

    import numpy as np

    from eng_toolkit import brep

    corners = np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], dtype=float)
    faces = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]
    unwelded = brep.Mesh(corners[np.array(faces)].reshape(-1, 3), np.arange(12, dtype=np.int32).reshape(4, 3), 0.0)
    welded = brep.weld(unwelded)
    topology = brep.mesh_topology(welded)
    assert (len(welded.vertices), len(welded.triangles)) == (4, 4), welded
    assert topology.watertight, topology
    assert sys.modules["open3d"] is None and sys.modules["OCP"] is None
    print("ok")
    """
)


def test_welding_and_the_topology_test_need_neither_open3d_nor_ocp():
    done = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"
