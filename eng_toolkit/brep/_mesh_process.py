"""Contain native freeform meshing, whose allocations cannot be bounded by a triangle estimate."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from eng_toolkit.errors import ToolFailure

MESH_MEMORY_BYTES = 1024**3
MESH_SECONDS = 30.0
MESH_TRIANGLES = 600_000


def needs_isolation(shape):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus
    from eng_toolkit.brep._query import _faces

    analytic = (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus)
    return any(BRepAdaptor_Surface(face).GetType() not in analytic for face in _faces(shape))


def isolated_mesh(shape, deflection, *, deadline=None):
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    with tempfile.TemporaryDirectory(prefix="eng-mesh-") as scratch:
        source, target = Path(scratch) / "input.brep", Path(scratch) / "mesh.brep"
        BRepTools.Write_s(shape, str(source))
        command = [sys.executable, "-m", "eng_toolkit.brep._mesh_process", str(source), str(target), str(deflection)]
        started = time.monotonic()
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError:
            raise ToolFailure("geometry.mesh_budget_exceeded", "cannot start the resource-limited mesher",
                              {"param": "/mesh_deflection"}) from None
        try:
            while process.poll() is None:
                if deadline is not None:
                    deadline("mesh")
                if time.monotonic() - started > MESH_SECONDS:
                    raise ToolFailure("geometry.mesh_budget_exceeded", "freeform meshing exceeded its wall-clock budget; use a coarser mesh_deflection",
                                      {"param": "/mesh_deflection", "seconds": MESH_SECONDS})
                time.sleep(0.05)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
        if process.returncode != 0 or not target.exists():
            raise ToolFailure("geometry.mesh_budget_exceeded", "freeform meshing failed within its memory/triangle budget; use a coarser mesh_deflection",
                              {"param": "/mesh_deflection", "memory_bytes": MESH_MEMORY_BYTES, "triangles": MESH_TRIANGLES})
        result = TopoDS_Shape()
        if not BRepTools.Read_s(result, str(target), BRep_Builder()):
            raise ToolFailure("geometry.mesh_failed", "could not read the isolated mesh", {"param": "/mesh_deflection"})
        return result


def _memory_limit():
    """Apply a hard OS allocation ceiling before importing OCCT or reading the model."""
    if os.name != "nt":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MESH_MEMORY_BYTES, MESH_MEMORY_BYTES))
        return None
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", wintypes.DWORD),
                    ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t), ("processes", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    limits.process_memory = MESH_MEMORY_BYTES
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise OSError("cannot establish mesher memory limit")
    if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise OSError("cannot attach mesher to memory limit")
    return job  # kept open until process exit


def _worker(source, target, deflection):
    job = _memory_limit()
    from OCP.BRep import BRep_Builder, BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS_Shape
    from eng_toolkit.brep._files import ANGULAR_DEFLECTION_RAD
    from eng_toolkit.brep._query import _faces

    shape = TopoDS_Shape()
    if not BRepTools.Read_s(shape, source, BRep_Builder()):
        raise ValueError("unreadable mesher input")
    BRepTools.Clean_s(shape)
    count = 0
    for face in _faces(shape):
        mesher = BRepMesh_IncrementalMesh(face, deflection, False, ANGULAR_DEFLECTION_RAD, False)
        mesh = BRep_Tool.Triangulation_s(face, TopLoc_Location())
        if not mesher.IsDone() or mesh is None:
            raise ValueError("face meshing failed")
        count += mesh.NbTriangles()
        if count > MESH_TRIANGLES:
            raise ValueError("mesh triangle budget exceeded")
    if not BRepTools.Write_s(shape, target):
        raise ValueError("mesh serialization failed")
    return job


if __name__ == "__main__":
    _worker(sys.argv[1], sys.argv[2], float(sys.argv[3]))
