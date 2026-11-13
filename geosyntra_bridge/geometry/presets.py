"""Saved projection settings ("presets"): a drawing's coordinate system, units / georeferencing and datum
transformation, kept per account under a name (a project, a consultant's site grid) and re-used — applied
automatically to a new drawing whose raw coordinates fall in the same place as the one it was saved from."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
KEYS = ("source_crs", "unit_scale", "georef", "transform_op")


def _path(data_dir: str) -> str:
    return os.path.join(data_dir, "crs_presets.json")


def _load(data_dir: str) -> Dict[str, List[Dict[str, Any]]]:
    try:
        with open(_path(data_dir), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(data_dir: str, d: Dict[str, Any]) -> None:
    os.makedirs(data_dir, exist_ok=True)
    tmp = _path(data_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(data_dir))


def list_presets(data_dir: str, owner: str) -> List[Dict[str, Any]]:
    return sorted(_load(data_dir).get(owner or "_", []), key=lambda p: -p.get("created", 0))


def save_preset(data_dir: str, owner: str, name: str, mapping: Dict[str, Any], extent: Optional[List[float]],
                layers: Optional[List[str]] = None) -> Dict[str, Any]:
    if not mapping.get("source_crs"):
        raise ValueError("define the projection first")
    p = {"id": uuid.uuid4().hex[:10], "name": (name or "").strip()[:80] or f"EPSG:{mapping['source_crs']}",
         "created": time.time(), "extent": extent, "layers": sorted(set(layers or []))[:300],
         **{k: mapping.get(k) for k in KEYS}}
    with _LOCK:
        d = _load(data_dir)
        lst = [x for x in d.get(owner or "_", []) if x["name"].lower() != p["name"].lower()]   # same name replaces
        d[owner or "_"] = [p] + lst[:99]
        _save(data_dir, d)
    return p


def delete_preset(data_dir: str, owner: str, pid: str) -> bool:
    with _LOCK:
        d = _load(data_dir)
        lst = d.get(owner or "_", [])
        keep = [x for x in lst if x["id"] != pid]
        d[owner or "_"] = keep
        _save(data_dir, d)
    return len(keep) < len(lst)


def get_preset(data_dir: str, owner: str, pid: str) -> Optional[Dict[str, Any]]:
    return next((x for x in list_presets(data_dir, owner) if x["id"] == pid), None)


def match_preset(data_dir: str, owner: str, extent: Optional[List[float]],
                 layers: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """The newest preset saved from a drawing in the same raw-coordinate area (centre inside its grown extent).
    Small local grids near 0,0 are common to unrelated sites, so there the layer names must match too."""
    if not extent:
        return None
    cx, cy = (extent[0] + extent[2]) / 2, (extent[1] + extent[3]) / 2
    w = max(extent[2] - extent[0], extent[3] - extent[1])
    for p in list_presets(data_dir, owner):
        e = p.get("extent")
        if not e:
            continue
        pad = max(e[2] - e[0], e[3] - e[1], w, 1e-9) * 1.0
        if e[0] - pad <= cx <= e[2] + pad and e[1] - pad <= cy <= e[3] + pad:
            # sizes must be comparable too (a 100 m site grid is not a 50 km survey)
            pw = max(e[2] - e[0], e[3] - e[1], 1e-9)
            if not 0.05 <= w / pw <= 20:
                continue
            if max(abs(cx), abs(cy)) < 50_000:          # a local grid: same office / project layer names as well
                a, b = {x.lower() for x in (layers or [])}, {x.lower() for x in (p.get("layers") or [])}
                if not a or not b or len(a & b) / len(a | b) < 0.4:
                    continue
            return p
    return None


def apply_to(mapping: Dict[str, Any], p: Dict[str, Any]) -> None:
    for k in KEYS:
        mapping[k] = p.get(k)
    mapping["unit_scale"] = float(mapping.get("unit_scale") or 1.0)
