"""
Spatial join with existing GIS data — how GIS engineers settle what a CAD layer *is* when its name says
nothing: overlay the drawing on an approved geodatabase of the same area (as-built GIS, a previous
delivery, the utility's master data) and see which feature class the drawn objects fall on.

  points    : the nearest reference point within POINT_TOL           (valve on valve, manhole on manhole)
  lines     : share of the CAD line inside the LINE_TOL buffer of the reference lines of one class
              (>= LINE_SHARE)                                          (main on main, lateral on lateral)
  polygons  : intersection over union with a reference polygon (>= IOU)

Per CAD mapping group the result is "matched / total" per reference class; the auto-mapper turns it into
evidence (>= 80 % decisive, >= 50 % supporting). Per entity the matched reference feature is kept so the
conversion can transfer attribute values into empty fields of the same name (recorded in REMARKS).

Reference data is read with GDAL (pyogrio): a zipped File Geodatabase, GeoPackage, zipped shapefile or
GeoJSON. Its coordinates are transformed into the drawing's coordinate system before matching.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

POINT_TOL = 1.0     # metres
LINE_TOL = 0.75     # metres (buffer half-width)
LINE_SHARE = 0.6
IOU = 0.5
SKIP_FIELDS = {"OBJECTID", "FID", "SHAPE", "SHAPE_LENGTH", "SHAPE_AREA", "SHAPE_LEN", "GLOBALID", "SOURCE_LAYER",
               "SOURCE_HANDLE", "MAPPING_STATUS", "CONFIDENCE", "REMARKS"}


class ReferenceError(Exception):
    pass


@dataclass
class RefLayer:
    name: str
    geometry: str                    # point | line | polygon
    count: int
    target: Optional[str]            # schema feature class it represents
    how: str                         # why that class
    epsg: Optional[int] = None


@dataclass
class ReferenceSet:
    source_name: str
    layers: List[RefLayer] = field(default_factory=list)
    geoms: List[Any] = field(default_factory=list)            # shapely, in drawing coordinates
    feat_layer: List[int] = field(default_factory=list)       # index into layers
    attrs: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        return {"source": self.source_name, "features": len(self.geoms),
                "layers": [l.__dict__ for l in self.layers], "warnings": self.warnings}


# ----------------------------------------------------------------- reading
def _find_dataset(folder: str) -> str:
    for root, dirs, files in os.walk(folder):
        for d in dirs:
            if d.lower().endswith(".gdb"):
                return os.path.join(root, d)
        for f in files:
            if f.lower().endswith((".gpkg", ".geojson", ".json", ".shp", ".sqlite")):
                return os.path.join(root, f) if not f.lower().endswith(".shp") else root
    raise ReferenceError("no File Geodatabase (.gdb), GeoPackage, shapefile or GeoJSON found in the upload")


def unpack(upload_path: str, work_dir: str) -> str:
    if upload_path.lower().endswith(".zip"):
        dest = os.path.join(work_dir, "reference_data")
        shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(dest)
        with zipfile.ZipFile(upload_path) as z:
            for n in z.namelist():
                p = os.path.normpath(os.path.join(dest, n))
                if not p.startswith(os.path.abspath(dest)):
                    raise ReferenceError("unsafe path in zip")
            z.extractall(dest)
        return _find_dataset(dest)
    return upload_path


def _geom_kind(geom_type: str) -> Optional[str]:
    g = (geom_type or "").lower()
    if "point" in g:
        return "point"
    if "line" in g or "curve" in g:
        return "line"
    if "polygon" in g or "surface" in g:
        return "polygon"
    return None


def _target_for(layer_name: str, kind: str, schema) -> Tuple[Optional[str], str]:
    base = layer_name.split(".")[-1]
    lay = schema.get_layer(base)
    if lay is not None:
        return lay.name, "same feature class name"
    from ..mapping.automap import norm_name
    for l in schema.layers:
        if norm_name(l.name) == norm_name(base) or (l.alias and norm_name(l.alias) == norm_name(base)):
            return l.name, "same feature class name"
    from ..mapping.semantics import SchemaSemantics
    sem = SchemaSemantics.get(schema)
    cands = [l for l in schema.layers if l.geometry_type == kind] or schema.layers
    ranked = sem.rank(base, cands, 2)
    if ranked and ranked[0][1] >= 0.45 and (len(ranked) < 2 or ranked[0][1] - ranked[1][1] >= 0.1):
        return ranked[0][0], f"layer name means {ranked[0][0]}"
    return None, "no matching feature class"


def load_reference(path: str, schema, drawing_epsg: Optional[int], source_name: str = "") -> ReferenceSet:
    import numpy as np
    import pyogrio
    from pyogrio import raw
    from shapely import from_wkb, transform as sh_transform
    from pyproj import CRS, Transformer

    ref = ReferenceSet(source_name=source_name or os.path.basename(path))
    try:
        layers = pyogrio.list_layers(path)
    except Exception as e:  # noqa: BLE001
        raise ReferenceError(f"cannot open reference data: {e}")
    for name, gtype in layers:
        kind = _geom_kind(gtype or "")
        if kind is None:
            continue
        try:
            meta, _, wkb, data = raw.read(path, layer=name)
        except Exception as e:  # noqa: BLE001
            ref.warnings.append(f"layer {name}: {e}")
            continue
        geoms = from_wkb(wkb)
        epsg = None
        if meta.get("crs"):
            try:
                c = CRS.from_user_input(meta["crs"])
                epsg = c.to_epsg()
                if drawing_epsg and epsg and epsg != drawing_epsg:
                    tr = Transformer.from_crs(c, CRS.from_epsg(drawing_epsg), always_xy=True)
                    geoms = np.array([sh_transform(g, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])))
                                      if g is not None else None for g in geoms], dtype=object)
            except Exception as e:  # noqa: BLE001
                ref.warnings.append(f"layer {name}: coordinate system not usable ({e}) — used as is")
        tgt, how = _target_for(name, kind, schema)
        li = len(ref.layers)
        ref.layers.append(RefLayer(name, kind, int(len(geoms)), tgt, how, epsg))
        fields = list(meta.get("fields") if meta.get("fields") is not None else [])
        for i, g in enumerate(geoms):
            if g is None or g.is_empty:
                continue
            ref.geoms.append(g)
            ref.feat_layer.append(li)
            rec = {}
            for fi, fn in enumerate(fields):
                if fn.upper() in SKIP_FIELDS:
                    continue
                v = data[fi][i]
                if v is None or (isinstance(v, float) and v != v) or v == "":
                    continue
                rec[fn] = v.item() if hasattr(v, "item") else v
            ref.attrs.append(rec)
    if not ref.geoms:
        raise ReferenceError("the reference data has no features with geometry")
    return ref


# ----------------------------------------------------------------- matching
def _cad_geom(e):
    from shapely.geometry import LineString, Point, Polygon
    try:
        if e.kind == "point":
            p = e.rep_point()
            return Point(p[0], p[1]) if p else None
        if e.kind == "line" and len(e.coords) >= 2:
            return LineString([c[:2] for c in e.coords])
        if e.kind == "polygon":
            if e.dxftype == "CIRCLE" and e.center and e.radius:
                return Point(e.center[0], e.center[1]).buffer(e.radius, 32)
            if len(e.coords) >= 3:
                pg = Polygon([c[:2] for c in e.coords])
                return pg if pg.is_valid else pg.buffer(0)
    except Exception:  # noqa: BLE001
        return None
    return None


def match_drawing(doc, ref: ReferenceSet, groups: Dict[str, List[Any]]) -> Dict[str, Any]:
    """-> {"groups": {group: {"total": n, "classes": {cls: matched}}}, "entities": {handle: {...}}, "overlap": bool}"""
    from shapely.strtree import STRtree
    tree = STRtree(ref.geoms)
    kinds = [ref.layers[i].geometry for i in ref.feat_layer]
    out_groups: Dict[str, Any] = {}
    ents_out: Dict[str, Any] = {}
    any_hit = False
    for key, ents in groups.items():
        counts: Counter = Counter()
        n = 0
        for e in ents:
            g = _cad_geom(e)
            if g is None:
                continue
            n += 1
            best = None   # (score, idx)
            if e.kind == "point":
                for i in tree.query(g.buffer(POINT_TOL)):
                    if kinds[i] != "point":
                        continue
                    d = ref.geoms[i].distance(g)
                    if d <= POINT_TOL and (best is None or -d > best[0]):
                        best = (-d, int(i))
            elif e.kind == "line":
                L = g.length or 1e-9
                per_cls: Dict[Optional[str], float] = defaultdict(float)
                per_idx: Dict[int, float] = {}
                for i in tree.query(g.buffer(LINE_TOL)):
                    if kinds[i] != "line":
                        continue
                    share = g.intersection(ref.geoms[i].buffer(LINE_TOL, cap_style=2)).length / L
                    if share > 0.02:
                        per_idx[int(i)] = share
                        per_cls[ref.layers[ref.feat_layer[i]].name] += share
                if per_idx:
                    top_layer, share = max(per_cls.items(), key=lambda kv: kv[1])
                    if share >= LINE_SHARE:
                        i = max((i for i in per_idx if ref.layers[ref.feat_layer[i]].name == top_layer), key=per_idx.get)
                        best = (min(1.0, share), i)
            else:
                for i in tree.query(g):
                    if kinds[i] != "polygon":
                        continue
                    r = ref.geoms[i]
                    try:
                        inter = g.intersection(r).area
                        iou = inter / (g.union(r).area or 1e-9)
                    except Exception:  # noqa: BLE001
                        continue
                    if iou >= IOU and (best is None or iou > best[0]):
                        best = (iou, int(i))
            if best is None:
                continue
            any_hit = True
            rl = ref.layers[ref.feat_layer[best[1]]]
            counts[rl.target or f"?{rl.name}"] += 1
            ents_out[e.handle] = {"ref_layer": rl.name, "target": rl.target, "attrs": ref.attrs[best[1]],
                                  "fit": round(float(best[0]), 3)}
        out_groups[key] = {"total": n, "classes": dict(counts)}
    return {"groups": out_groups, "entities": ents_out, "overlap": any_hit}


def group_evidence(match: Optional[Dict[str, Any]], key: str) -> Dict[str, Tuple[float, int, int]]:
    """{class: (share, matched, total)} for one CAD group."""
    if not match:
        return {}
    g = match.get("groups", {}).get(key)
    if not g or not g["total"]:
        return {}
    return {c: (m / g["total"], m, g["total"]) for c, m in g["classes"].items() if not c.startswith("?")}
