"""
Update the attributes of ONE row of a delivered File Geodatabase, found by its OBJECTID.

Only the given fields of that row are written; geometry and every other row stay untouched. The row is read
back afterwards and compared, so a save is reported as successful only when the geodatabase holds the values.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict

from .filegdb_writer import _set_value
from .gdal_c import gdal

OF_VECTOR, OF_UPDATE = 0x04, 0x01


class RowUpdateError(Exception):
    pass


def _norm(v: Any, ftype: str) -> str:
    if v is None or v == "":
        return ""
    if ftype in ("INTEGER", "SMALLINTEGER"):
        return str(int(float(v)))
    if ftype in ("DOUBLE", "FLOAT"):
        return f"{float(v):.6f}"
    if ftype == "DATE":
        s = v.isoformat() if hasattr(v, "isoformat") else str(v)
        return s.replace("T", " ").replace("/", "-")[:10]
    return str(v)


def _geom_digest(g, feat) -> str:
    ref = g.OGR_F_GetGeometryRef(feat) if g.OGR_F_GetGeometryRef else None
    if not ref or not g.OGR_G_ExportToIsoWkt:
        return ""
    out = __import__("ctypes").c_char_p()
    g.OGR_G_ExportToIsoWkt(ref, __import__("ctypes").byref(out))
    return hashlib.sha1(out.value or b"").hexdigest()


def update_row(gdb_path: str, layer_name: str, objectid: int, values: Dict[str, Any], ftypes: Dict[str, str]) -> Dict[str, Any]:
    g = gdal()
    if not (g.OGR_L_GetFeature and g.OGR_L_SetFeature and g.OGR_F_GetFieldAsString):
        raise RowUpdateError("this GDAL build cannot update existing rows")
    g.CPLErrorReset()
    ds = g.GDALOpenEx(gdb_path.encode("utf-8"), OF_VECTOR | OF_UPDATE, None, None, None)
    if not ds:
        raise RowUpdateError(f"cannot open the geodatabase for update: {g.last_error()}")
    try:
        lyr = g.GDALDatasetGetLayerByName(ds, layer_name.encode("utf-8"))
        if not lyr:
            raise RowUpdateError(f"{layer_name} not found in the geodatabase")
        feat = g.OGR_L_GetFeature(lyr, int(objectid))
        if not feat:
            raise RowUpdateError(f"{layer_name} has no row with OBJECTID {objectid}")
        try:
            before = _geom_digest(g, feat)
            defn = g.OGR_L_GetLayerDefn(lyr)
            for name, v in values.items():
                i = g.OGR_FD_GetFieldIndex(defn, name.encode("utf-8"))
                if i < 0:
                    raise RowUpdateError(f"{layer_name} has no field {name}")
                ft = ftypes.get(name, "TEXT")
                if ft == "DATE" and isinstance(v, str) and v:
                    v = datetime.fromisoformat(v[:19])
                _set_value(g, feat, i, v, ft)
            if g.OGR_L_SetFeature(lyr, feat) != 0:
                raise RowUpdateError(f"write refused: {g.last_error() or 'unknown GDAL error'}")
        finally:
            g.OGR_F_Destroy(feat)
    finally:
        g.GDALClose(ds)
    # read back (fresh, read-only) and compare
    ds = g.GDALOpenEx(gdb_path.encode("utf-8"), OF_VECTOR, None, None, None)
    try:
        lyr = g.GDALDatasetGetLayerByName(ds, layer_name.encode("utf-8"))
        feat = g.OGR_L_GetFeature(lyr, int(objectid))
        if not feat:
            raise RowUpdateError("row not found when reading back")
        try:
            defn = g.OGR_L_GetLayerDefn(lyr)
            bad = []
            for name, v in values.items():
                i = g.OGR_FD_GetFieldIndex(defn, name.encode("utf-8"))
                got = (g.OGR_F_GetFieldAsString(feat, i) or b"").decode("utf-8", "replace") \
                    if g.OGR_F_IsFieldSetAndNotNull(feat, i) else ""
                ft = ftypes.get(name, "TEXT")
                if _norm(got, ft) != _norm(v, ft):
                    bad.append(f"{name}: wrote {v!r}, read {got!r}")
            after = _geom_digest(g, feat)
        finally:
            g.OGR_F_Destroy(feat)
    finally:
        g.GDALClose(ds)
    if bad:
        raise RowUpdateError("read-back mismatch — " + "; ".join(bad))
    return {"objectid": int(objectid), "fields": list(values), "geometry_unchanged": before == after}


def read_row(gdb_path: str, layer_name: str, objectid: int, names) -> Dict[str, Any]:
    """The stored values of one row (as text) — what the geodatabase holds right now."""
    g = gdal()
    if not (g.OGR_L_GetFeature and g.OGR_F_GetFieldAsString):
        raise RowUpdateError("this GDAL build cannot read rows by OBJECTID")
    ds = g.GDALOpenEx(gdb_path.encode("utf-8"), OF_VECTOR, None, None, None)
    if not ds:
        raise RowUpdateError(f"cannot open {gdb_path}")
    try:
        lyr = g.GDALDatasetGetLayerByName(ds, layer_name.encode("utf-8"))
        if not lyr:
            raise RowUpdateError(f"{layer_name} not found in the geodatabase")
        feat = g.OGR_L_GetFeature(lyr, int(objectid))
        if not feat:
            raise RowUpdateError(f"{layer_name} has no row with OBJECTID {objectid}")
        try:
            defn = g.OGR_L_GetLayerDefn(lyr)
            out: Dict[str, Any] = {}
            for name in names:
                i = g.OGR_FD_GetFieldIndex(defn, name.encode("utf-8"))
                if i < 0:
                    continue
                out[name] = (g.OGR_F_GetFieldAsString(feat, i) or b"").decode("utf-8", "replace") \
                    if g.OGR_F_IsFieldSetAndNotNull(feat, i) else None
            return out
        finally:
            g.OGR_F_Destroy(feat)
    finally:
        g.GDALClose(ds)
