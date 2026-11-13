"""Shared imports, constants and helpers of the job modules."""
from __future__ import annotations

import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List


ALLOWED_EXT = (".dxf", ".dwg", ".dgn", ".zip")
DRAWING_EXT = (".dxf", ".dwg", ".dgn")
_SAFE = re.compile(r"[^A-Za-z0-9_.\- ]")


class JobError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_filename(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/"))
    base = _SAFE.sub("_", base).strip(" .") or "drawing.dxf"
    return base[:120]


def _rmtree(path: str):
    def onerr(func, p, exc):
        try:
            os.chmod(p, 0o666)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=onerr)


def _pipeline(mapping: Dict[str, Any], schema):
    """The coordinate pipeline of a job (drawing units / georeferencing -> target CRS), as used by the conversion."""
    from ..geometry.convert import pipeline_for
    return pipeline_for(mapping, schema.spatial_reference.epsg)


def _unzip_drawings(zip_path: str, out_dir: str, limit: int) -> List[Dict[str, Any]]:
    """Drawings of a .zip (main drawing first: the largest one), extracted flat and safely (no paths, size limit)."""
    from ..cad.reader import sniff_cad_file as _sniff
    try:
        z = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise JobError(f"not a valid zip file: {e}")
    items = [i for i in z.infolist() if not i.is_dir() and os.path.splitext(i.filename.lower())[1] in DRAWING_EXT
             and not os.path.basename(i.filename).startswith(("._", "~"))]
    if not items:
        raise JobError("the zip contains no .dwg / .dxf / .dgn drawing")
    if sum(i.file_size for i in items) > limit * 4:
        raise JobError("the drawings in the zip are too large once extracted")
    out, seen = [], set()
    for i in sorted(items, key=lambda x: -x.file_size)[:20]:
        nm = safe_filename(os.path.basename(i.filename))
        while nm in seen:
            nm = "_" + nm
        seen.add(nm)
        p = os.path.join(out_dir, nm)
        with z.open(i) as src, open(p, "wb") as dst:
            shutil.copyfileobj(src, dst)
        try:
            fmt = _sniff(p)
        except Exception:  # noqa: BLE001
            os.remove(p)
            continue
        out.append({"name": nm, "size": os.path.getsize(p), "format": fmt})
    if not out:
        raise JobError("no readable drawing in the zip")
    for i in z.infolist():   # coordinate-system sidecars (.prj) travel with the drawings
        if not i.is_dir() and i.filename.lower().endswith(".prj") and i.file_size < 100_000:
            nm = safe_filename(os.path.basename(i.filename))
            with z.open(i) as src, open(os.path.join(out_dir, nm), "wb") as dst:
                shutil.copyfileobj(src, dst)
    return out


def _clean_elevation(d: Dict[str, Any]) -> Dict[str, Any]:
    from ..elevation.engine import DEFAULT_OPTIONS
    out = {k: d[k] for k in DEFAULT_OPTIONS if k in d}
    if out.get("mode") not in (None, "fill_missing", "replace"):
        raise JobError("elevation mode must be fill_missing or replace")
    if out.get("interpolation") not in (None, "nearest", "bilinear", "bicubic"):
        raise JobError("interpolation must be nearest, bilinear or bicubic")
    if out.get("missing_policy") not in (None, "block", "exclude", "nodata_value"):
        raise JobError("missing_policy must be block, exclude or nodata_value")
    if out.get("missing_policy") == "nodata_value":
        try:
            out["nodata_value"] = float(out.get("nodata_value"))
        except (TypeError, ValueError):
            raise JobError("enter the explicit no-data elevation value (e.g. -9999)")
        if out["nodata_value"] == 0:
            raise JobError("0 cannot be used as the no-data elevation — it is a real elevation")
    for k in ("rg_offset", "dem_accuracy_m"):
        if out.get(k) in ("", None):
            out[k] = None
        elif k in out:
            out[k] = float(out[k])
    for k in ("vertical_offset", "max_slope_pct", "cad_dem_tolerance"):
        if k in out and out[k] is not None:
            out[k] = float(out[k])
    return out


def _zip_dir(src_dir: str, zip_path: str):
    base = os.path.basename(src_dir.rstrip("/\\"))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(src_dir):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.join(base, os.path.relpath(full, src_dir)))
