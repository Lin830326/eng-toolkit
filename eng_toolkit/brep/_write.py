"""Writing a ``Solid`` as STEP or BREP and a ``Mesh`` as binary STL or GLB, byte for byte reproducible."""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Literal

import numpy as np

from eng_toolkit.brep import _occ
from eng_toolkit.brep._mesh import weld
from eng_toolkit.brep._types import Mesh, Solid
from eng_toolkit.errors import ToolFailure

STEP_TIMESTAMP = "1970-01-01T00:00:00"
STEP_SYSTEM = "eng-toolkit"
STL_HEADER = b"eng-toolkit binary STL, millimetres".ljust(80, b" ")  # never starts with "solid": readers would take it for ASCII
PRODUCT_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
GLB_MAGIC = 0x46546C67
GLB_JSON, GLB_BIN = 0x4E4F534A, 0x004E4942
FLOAT32, UINT32 = 5126, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
MM_PER_M = 1000.0


def write_step(solid: Solid, path: Path, *, product_name: str) -> None:
    """Write STEP AP214 through the XCAF writer, which names the product, with a fixed FILE_NAME header.

    The same shape gives the same bytes in one process and across processes: no timestamp, no per-process counter.
    The plain ``STEPControl_Writer`` carries both.
    """
    _occ.load()
    from OCP.APIHeaderSection import APIHeaderSection_MakeHeader
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString, TCollection_HAsciiString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    if not PRODUCT_NAME.fullmatch(product_name):
        raise ToolFailure("input.invalid", f"product name {product_name!r} must match [A-Za-z0-9_.-]{{1,64}}")
    with _occ.TRANSLATOR_LOCK:  # Interface_Static parameters are process-wide
        Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
        document = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
        label = XCAFDoc_DocumentTool.ShapeTool_s(document.Main()).AddShape(solid._handle, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(product_name))
        writer = STEPCAFControl_Writer()
        writer.SetNameMode(True)
        if not writer.Transfer(document, STEPControl_AsIs):
            raise ToolFailure("geometry.write_failed", "OpenCASCADE could not translate the shape to STEP")
        header = APIHeaderSection_MakeHeader(writer.ChangeWriter().Model())
        header.SetName(TCollection_HAsciiString(product_name))
        header.SetTimeStamp(TCollection_HAsciiString(STEP_TIMESTAMP))
        header.SetAuthorValue(1, TCollection_HAsciiString(""))
        header.SetOrganizationValue(1, TCollection_HAsciiString(""))
        header.SetOriginatingSystem(TCollection_HAsciiString(STEP_SYSTEM))
        header.SetAuthorisation(TCollection_HAsciiString(""))
        if writer.Write(str(path)) != IFSelect_RetDone:
            raise ToolFailure("geometry.write_failed", f"OpenCASCADE could not write STEP to {str(path)!r}", {"path": str(path)})


def write_brep(solid: Solid, path: Path) -> None:
    """Write OpenCASCADE's native BREP text format (millimetre numbers)."""
    _occ.load()
    from OCP.BRepTools import BRepTools

    if not BRepTools.Write_s(solid._handle, str(path)):
        raise ToolFailure("geometry.write_failed", f"OpenCASCADE could not write BREP to {str(path)!r}", {"path": str(path)})


def _stl_bytes(mesh: Mesh) -> bytes:
    triangles = np.asarray(mesh.triangles, dtype=np.int64).reshape(-1, 3)
    corners = np.asarray(mesh.vertices, dtype=np.float64).reshape(-1, 3)[triangles]  # (m, 3, 3)
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    normals = np.divide(normals, lengths[:, None], out=np.zeros_like(normals), where=lengths[:, None] > 0.0)
    record = np.dtype([("normal", "<f4", 3), ("corners", "<f4", (3, 3)), ("attribute", "<u2")])
    rows = np.zeros(len(triangles), dtype=record)
    rows["normal"] = normals
    rows["corners"] = corners
    return STL_HEADER + struct.pack("<I", len(triangles)) + rows.tobytes()


def _padded(data: bytes, fill: bytes) -> bytes:
    return data + fill * (-len(data) % 4)


def _glb_bytes(mesh: Mesh, name: str) -> bytes:
    """One mesh in metres, +Y up (glTF), converted from millimetres and +Z up by (x, y, z) -> (x, z, -y)."""
    welded = weld(mesh)
    vertices = np.asarray(welded.vertices, dtype=np.float64).reshape(-1, 3)
    positions = np.ascontiguousarray(np.stack([vertices[:, 0], vertices[:, 2], -vertices[:, 1]], axis=1) / MM_PER_M, dtype="<f4")
    indices = np.ascontiguousarray(np.asarray(welded.triangles).reshape(-1), dtype="<u4")
    index_bytes, position_bytes = indices.tobytes(), positions.tobytes()
    blob = _padded(index_bytes, b"\x00") + position_bytes
    document = {
        "asset": {"generator": "eng-toolkit", "version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 1}, "indices": 0, "mode": 4}]}],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(index_bytes), "target": ELEMENT_ARRAY_BUFFER},
            {"buffer": 0, "byteOffset": len(_padded(index_bytes, b"\x00")), "byteLength": len(position_bytes), "target": ARRAY_BUFFER},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": UINT32, "count": int(indices.size), "type": "SCALAR"},
            {
                "bufferView": 1,
                "componentType": FLOAT32,
                "count": int(len(positions)),
                "type": "VEC3",
                "min": [float(c) for c in positions.min(axis=0)] if len(positions) else [0.0, 0.0, 0.0],
                "max": [float(c) for c in positions.max(axis=0)] if len(positions) else [0.0, 0.0, 0.0],
            },
        ],
    }
    text = _padded(json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8"), b" ")
    blob = _padded(blob, b"\x00")
    total = 12 + 8 + len(text) + 8 + len(blob)
    return (
        struct.pack("<III", GLB_MAGIC, 2, total)
        + struct.pack("<II", len(text), GLB_JSON)
        + text
        + struct.pack("<II", len(blob), GLB_BIN)
        + blob
    )


def write_mesh(mesh: Mesh, path: Path, *, fmt: Literal["stl", "glb"], name: str) -> None:
    """Write binary STL (millimetres, a fixed 80-byte header) or GLB (metres, +Y up, nodes named ``name``)."""
    if fmt == "stl":
        payload = _stl_bytes(mesh)
    elif fmt == "glb":
        payload = _glb_bytes(mesh, name)
    else:
        raise ToolFailure("input.invalid", f"mesh format must be 'stl' or 'glb', got {fmt!r}", {"allowed": ["stl", "glb"]})
    Path(path).write_bytes(payload)
