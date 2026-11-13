"""
Elevation step of the conversion: CAD → DEM elevation → geometry processing → GDB.

For every feature whose target class is Z-enabled in the template:
  1. CAD Z of each output vertex (matched to the drawing's own vertices; a drawing that is flat at Z = 0 has no Z)
  2. DEM Z sampled at each vertex (point, polyline vertices, polygon ring vertices) in the DEM's CRS,
     with the chosen interpolation and NoData detection
  3. vertical reference: DEM heights converted to the requested reference with PROJ geoid grids — only when the
     exact grid-based transformation is available (PROJ otherwise returns heights unchanged without telling);
     a constant offset (local datum) can be added
  4. mode  fill_missing (default): CAD Z is preserved, the DEM fills vertices without CAD Z
           replace             : the DEM replaces CAD Z (CAD Z is kept where the DEM has no data)
  5. gaps: vertices still without Z are interpolated along the feature from known vertices (recorded);
     a feature without any known Z is never given 0 — policy: block (default) | exclude | nodata_value
  6. statistics per feature (min / max / mean / start / end / DEM ground mean) for the report and Z fields
  7. validation of the Z-enabled geometry: finite values, plausible range, slope spikes, CAD-vs-DEM differences
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon

DEFAULT_OPTIONS: Dict[str, Any] = {
    "enabled": False,
    "source": None,                 # copernicus_glo30 | srtm_gl1 | alos_aw3d30 | upload
    "mode": "fill_missing",         # fill_missing | replace
    "interpolation": "bilinear",    # nearest | bilinear | bicubic
    "vertical_target": "ras_ghumays",   # ras_ghumays (DMT standard) | source | EPSG:3855 | EPSG:5773 | EPSG:4979
    "rg_offset": None,              # metres: Ras Ghumays height = DEM height + rg_offset (from a benchmark / the project surveyor)
    "dem_accuracy_m": None,         # declared vertical accuracy (std. dev.) of an uploaded DTM
    "vertical_offset": 0.0,         # metres added to DEM heights after the datum conversion
    "cad_zero_is_missing": True,    # a CAD entity drawn entirely at Z = 0 carries no elevation
    "interpolate_gaps": True,       # vertices without Z are interpolated along the feature from known vertices
    "missing_policy": "block",      # block | exclude | nodata_value
    "nodata_value": None,           # explicit value written only with missing_policy = nodata_value
    "fill_fields": True,            # ZCOORD / ZCOORD_START / ZCOORD_END from the 3D geometry (empty fields only)
    "ground_fields": True,          # GROUND_LEVEL / AVG_GLEVEL from the DEM (empty fields only)
    "max_slope_pct": 100.0,         # vertex-to-vertex slope above this is reported as a spike
    "cad_dem_tolerance": 5.0,       # |CAD Z - DEM| above this is reported
    "align_to_drawing": True,       # the drawing's surveyed levels govern: DEM aligned to them, gaps from nearby levels
    "drawing_radius_m": 100.0,      # surveyed levels within this distance interpolate a vertex without CAD Z
}

Z_FIELD_SOURCES = {"ZCOORD": "z", "ZCOORD_START": "start_z", "ZCOORD_END": "end_z",
                   "GROUND_LEVEL": "dem_ground", "AVG_GLEVEL": "dem_ground"}


def options(mapping: Dict[str, Any]) -> Dict[str, Any]:
    o = {**DEFAULT_OPTIONS, **(mapping.get("elevation") or {})}
    o["area_type"] = (mapping.get("project") or {}).get("area_type") or "city"     # DMT Table 2 accuracy class
    return o


# ------------------------------------------------------------------ vertical reference
_VT_CACHE: Dict[Tuple[str, str], Any] = {}


def vertical_transformer(src: str, dst: str):
    """-> (transformer | None, message). None means: not converted, and why."""
    if dst in (None, "", "source") or dst == src:
        return None, "kept as delivered by the DEM"
    if not src or src == "unknown":
        return None, "the DEM's vertical reference is unknown — heights kept as delivered"
    key = (src, dst)
    if key in _VT_CACHE:
        return _VT_CACHE[key]
    from pyproj import CRS, Transformer
    from pyproj.transformer import TransformerGroup
    import warnings

    def crs(v):
        return CRS("EPSG:4979") if v == "EPSG:4979" else CRS(f"EPSG:4326+{v.split(':')[1]}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tg = TransformerGroup(crs(src), crs(dst), always_xy=True)
        if not tg.best_available:
            try:
                import pyproj.network
                pyproj.network.set_network_enabled(True)       # fetch geoid grids from cdn.proj.org when allowed
                tg = TransformerGroup(crs(src), crs(dst), always_xy=True)
            except Exception:  # noqa: BLE001
                pass
    if not tg.best_available or not tg.transformers:
        grids = sorted({gr.short_name for op in tg.unavailable_operations for gr in op.grids if not gr.available})
        res = (None, f"conversion {src} → {dst} unavailable: PROJ geoid grid {', '.join(grids) or '?'} not installed "
                     f"(place it in the PROJ data folder or allow access to cdn.proj.org) — heights kept as {src}")
    else:
        res = (tg.transformers[0], f"converted {src} → {dst} with PROJ ({tg.transformers[0].description})")
    _VT_CACHE[key] = res
    return res


# ------------------------------------------------------------------ geometry helpers
def _parts(g) -> List[Tuple[str, List[Tuple[float, float]]]]:
    """(kind, coords) for every vertex sequence (points, line parts, polygon rings)."""
    if g is None or g.is_empty:
        return []
    if isinstance(g, Point):
        return [("pt", [(g.x, g.y)])]
    if isinstance(g, LineString):
        return [("ln", [c[:2] for c in g.coords])]
    if isinstance(g, MultiLineString):
        return [("ln", [c[:2] for c in p.coords]) for p in g.geoms]
    if isinstance(g, Polygon):
        return [("ring", [c[:2] for c in r.coords]) for r in [g.exterior, *g.interiors]]
    if isinstance(g, MultiPolygon):
        return [x for p in g.geoms for x in _parts(p)]
    return []


def _rebuild(g, zparts: List[List[float]]):
    it = iter(zparts)
    if isinstance(g, Point):
        z = next(it)[0]
        return Point(g.x, g.y, z)
    if isinstance(g, LineString):
        zs = next(it)
        return LineString([(c[0], c[1], z) for c, z in zip(g.coords, zs)])
    if isinstance(g, MultiLineString):
        return MultiLineString([[(c[0], c[1], z) for c, z in zip(p.coords, next(it))] for p in g.geoms])
    if isinstance(g, Polygon):
        rings = [[(c[0], c[1], z) for c, z in zip(r.coords, next(it))] for r in [g.exterior, *g.interiors]]
        return Polygon(rings[0], rings[1:])
    if isinstance(g, MultiPolygon):
        polys = []
        for p in g.geoms:
            rings = [[(c[0], c[1], z) for c, z in zip(r.coords, next(it))] for r in [p.exterior, *p.interiors]]
            polys.append(Polygon(rings[0], rings[1:]))
        return MultiPolygon(polys)
    return g


def _fill_gaps(xy: List[Tuple[float, float]], z: List[Optional[float]]) -> Tuple[List[Optional[float]], int]:
    """Linear interpolation by distance along the sequence between known values; ends take the nearest known
    value. Returns (values, number filled). All-unknown stays unknown."""
    known = [i for i, v in enumerate(z) if v is not None]
    if not known or len(known) == len(z):
        return z, 0
    d = [0.0]
    for i in range(1, len(xy)):
        d.append(d[-1] + math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]))
    out = list(z)
    n = 0
    for i, v in enumerate(z):
        if v is not None:
            continue
        lo = max((k for k in known if k < i), default=None)
        hi = min((k for k in known if k > i), default=None)
        if lo is not None and hi is not None and d[hi] > d[lo]:
            t = (d[i] - d[lo]) / (d[hi] - d[lo])
            out[i] = z[lo] + t * (z[hi] - z[lo])
        else:
            out[i] = z[lo if lo is not None else hi]
        n += 1
    return out, n


def _cad_z_lookup(f, cp, zero_is_missing: bool) -> Dict[Tuple[float, float], float]:
    e = f.source
    if not e.coords or len(e.coords[0]) < 3:
        return {}
    zs = [c[2] for c in e.coords]
    if zero_is_missing and all(abs(z or 0.0) < 1e-9 for z in zs):
        return {}
    pts = cp.xy(e.coords)
    sc = getattr(cp, "scale", 1.0) or 1.0
    return {(round(x, 3), round(y, 3)): z * sc for (x, y), z in zip(pts, zs) if z is not None and math.isfinite(z)}


# ------------------------------------------------------------------ main step
def apply_elevation(features, schema, cp, dem, opts: Dict[str, Any], issues, target_epsg: Optional[int],
                    log=lambda m: None) -> Dict[str, Any]:
    """Populate 3D geometry and f.z on every feature of a Z-enabled class. Returns the elevation report."""
    report: Dict[str, Any] = {"enabled": bool(opts.get("enabled")), "options": {k: opts.get(k) for k in DEFAULT_OPTIONS},
                              "dem": dict(dem.meta) if dem else None, "features": 0, "by_status": {}, "vertices": {},
                              "vertical": None, "by_class": {}}
    zf = [f for f in features if not f.failed and f.geom is not None and getattr(schema.get_layer(f.target), "has_z", True)]
    if not opts.get("enabled"):
        report["status"] = "disabled"
        vc: Counter = Counter()
        for f in zf:
            cad = _cad_z_lookup(f, cp, True)
            if cad:      # real CAD levels are always kept, even without a DEM
                _apply_vertices(f, {}, cad, opts, {**opts, "mode": "fill_missing"}, vc, dem_values=False)
            else:
                f.z = {"status": "none"}
        if opts.get("missing_policy") == "nodata_value" and opts.get("nodata_value") is not None:
            nd = float(opts["nodata_value"])        # explicit choice of the user, recorded per feature
            for f in zf:
                if not f.geom.has_z:
                    zp = f.zparts or [[None] * len(xy) for _, xy in _parts(f.geom)]
                    f.geom = _rebuild(f.geom, [[nd if v is None else v for v in part] for part in zp])
                    f.z = {**(f.z or {}), "status": (f.z or {}).get("status", "none") + "+nodata_value"}
                    f.remarks.append(f"no elevation (DEM disabled) — explicit no-data value {nd} written")
        report["vertices"] = dict(vc)
        report["by_status"] = dict(Counter(f.z["status"] for f in zf))
        return report
    if dem is None:
        report["status"] = "no_dem"
        issues.add("error", "geometry", "elevation_dem_missing",
                   "DEM elevation is enabled but no DEM is prepared — choose a source and prepare it in the Elevation step",
                   suggestion="Elevation step → select the DEM source → Prepare DEM")
        return report

    from pyproj import CRS, Transformer
    rg_pending = None
    method = opts.get("interpolation") or "bilinear"
    # 1. every vertex of every feature, in target CRS
    seqs = []                  # (feature, part index, xy list)
    xs, ys = [], []
    for f in zf:
        for pi, (_, xy) in enumerate(_parts(f.geom)):
            seqs.append((f, pi, xy))
            for x, y in xy:
                xs.append(x)
                ys.append(y)
    report["features"] = len(zf)
    if not xs:
        report["status"] = "nothing to do"
        return report
    X, Y = np.array(xs), np.array(ys)
    # 2. to DEM CRS and sample
    dem_crs = CRS.from_user_input(dem.crs())
    if target_epsg and CRS.from_epsg(target_epsg) != dem_crs:
        t = Transformer.from_crs(CRS.from_epsg(target_epsg), dem_crs, always_xy=True)
        DX, DY = t.transform(X, Y)
        DX, DY = np.asarray(DX), np.asarray(DY)
    else:
        DX, DY = X, Y
    vals, flags = dem.sample(DX, DY, method)
    # 3. vertical reference + offset (DEM values only)
    src_v = dem.meta.get("vertical") or "unknown"
    tgt_v = opts.get("vertical_target") or "source"
    ok = ~np.isnan(vals)
    if tgt_v == "ras_ghumays":
        # DMT standard 2.1 / 3.5: heights above the Ras Ghumays datum. No public EPSG transformation exists, so it is
        # reached only from what the user supplies: a DTM already in Ras Ghumays, or a benchmark offset.
        vt = None
        if src_v == "ras_ghumays":
            vmsg, final_v = "DTM delivered in Ras Ghumays — kept", "ras_ghumays"
        elif opts.get("rg_offset") not in (None, ""):
            rg = float(opts["rg_offset"])
            vals[ok] = vals[ok] + rg
            vmsg, final_v = f"converted to Ras Ghumays with the benchmark offset {rg:+.3f} m supplied (from {src_v})", "ras_ghumays"
        else:
            vmsg, final_v = (f"NOT Ras Ghumays: heights are {src_v} ({dem.meta.get('vertical_label', '')}). The DMT standard requires "
                             f"Ras Ghumays — enter the {src_v} → Ras Ghumays offset from a benchmark, or use a survey DTM in "
                             f"Ras Ghumays"), src_v
            rg_pending = vmsg
        report["ras_ghumays"] = final_v == "ras_ghumays"
    else:
        vt, vmsg = vertical_transformer(src_v, tgt_v)
        final_v = src_v
        report["ras_ghumays"] = src_v == "ras_ghumays" and tgt_v == "source"
    if vt is not None and ok.any():
        ll = Transformer.from_crs(dem_crs, CRS.from_epsg(4326), always_xy=True) if not dem_crs.is_geographic else None
        lon, lat = (ll.transform(DX[ok], DY[ok]) if ll else (DX[ok], DY[ok]))
        _, _, h2 = vt.transform(np.asarray(lon), np.asarray(lat), vals[ok])
        vals[ok] = np.asarray(h2)
        final_v = opts.get("vertical_target")
    elif vt is None and tgt_v not in ("source", src_v, "ras_ghumays"):
        issues.add("warning", "geometry", "vertical_datum_not_converted", f"Elevation: {vmsg}",
                   suggestion="install the PROJ geoid grid, or keep the DEM's vertical reference")
    off = float(opts.get("vertical_offset") or 0.0)
    if off:
        vals[ok] = vals[ok] + off
    report["vertical"] = {"source": src_v, "result": final_v, "message": vmsg, "offset_m": off}
    # 3b. the drawing's surveyed levels govern (DMT: survey accuracy over a global DEM)
    drawing = np.zeros(len(vals), dtype=bool)
    al = None
    if opts.get("align_to_drawing", True) and (opts.get("mode") or "fill_missing") != "replace" \
            and src_v != "ras_ghumays" and opts.get("rg_offset") in (None, ""):
        al = _align_to_drawing(zf, cp, opts, X, Y, vals, drawing)
    if al:
        report["drawing_levels"] = al
        report["ras_ghumays"] = None          # the drawing's own survey datum
        report["vertical"] = {"source": src_v, "result": "drawing levels", "offset_m": al["offset_m"],
                              "message": f"heights follow the drawing's {al['control_points']} surveyed levels; the DEM is "
                                         f"aligned to them ({al['offset_m']:+.2f} m, spread ±{al['spread_m']:.2f} m) and "
                                         f"{al['interpolated_vertices']} vertex(es) without a level are interpolated from "
                                         f"the nearest surveyed levels"}
        issues.add("info", "geometry", "elevation_from_drawing_levels", "Elevation: " + report["vertical"]["message"] +
                   " — Ras Ghumays when the drawing's levels are (as-built survey)",
                   suggestion="if the drawing's levels are not Ras Ghumays, enter the benchmark offset in the Elevation step")
    elif rg_pending:
        issues.add("warning", "geometry", "vertical_datum_not_ras_ghumays", f"Elevation: {rg_pending}",
                   suggestion="Field mapping → Elevation fields from DEM → Options → Ras Ghumays offset")
    report["interpolation"] = method
    # 4. per feature
    k = 0
    per_f: Dict[int, Dict[int, Tuple[List[Tuple[float, float]], np.ndarray, np.ndarray]]] = defaultdict(dict)
    n_drawing: Dict[int, int] = defaultdict(int)
    for f, pi, xy in seqs:
        n = len(xy)
        per_f[id(f)][pi] = (xy, vals[k:k + n], flags[k:k + n])
        n_drawing[id(f)] += int(drawing[k:k + n].sum())
        k += n
    vcount: Counter = Counter()
    status_count: Counter = Counter()
    by_class: Dict[str, Counter] = defaultdict(Counter)
    for f in zf:
        cad = _cad_z_lookup(f, cp, bool(opts.get("cad_zero_is_missing", True)))
        st = _apply_vertices(f, per_f.get(id(f), {}), cad, opts, opts, vcount, dem_values=True)
        nd = n_drawing.get(id(f), 0)
        if nd and f.z and f.z.get("n_dem"):
            used = min(nd, f.z["n_dem"])            # vertices whose value came from the surveyed levels, not the DEM
            f.z["n_drawing"] = used
            f.z["n_dem"] -= used
            st = f.z["status"] = st.replace("dem", "drawing") if not f.z["n_dem"] else st
            f.remarks = [r.replace("Z from DEM", "Z from drawing levels") if r.startswith("Z from") and not f.z["n_dem"] else r
                         for r in f.remarks]
        status_count[st] += 1
        by_class[f.target][st] += 1
    report["vertices"] = dict(vcount)
    report["by_status"] = dict(status_count)
    report["by_class"] = {c: dict(v) for c, v in by_class.items()}
    report["status"] = "done"
    dem_only = sum((f.z or {}).get("n_dem", 0) for f in zf)
    if al and not dem_only:
        report["accuracy"] = {"not_used": True, "note": "every height comes from the drawing's surveyed levels"}
    else:
        _accuracy_check(dem, opts, issues, report)
    _missing_policy(zf, opts, issues, report)
    _validate_z(zf, dem, opts, issues, report)
    zs = [f.z for f in zf if f.z and f.z.get("mean") is not None]
    report["z_range"] = {"min": round(min(z["min"] for z in zs), 3), "max": round(max(z["max"] for z in zs), 3),
                         "mean": round(float(np.mean([z["mean"] for z in zs])), 3)} if zs else None
    log(f"elevation: {report['by_status']} vertices {report['vertices']}")
    return report


def _align_to_drawing(zf, cp, opts, X, Y, vals, drawing) -> Optional[Dict[str, Any]]:
    """Surveyed levels in the drawing are the reference (DMT: as-built survey accuracy; a global DEM is ±metres and
    in another vertical datum). The DEM is shifted onto them by the median CAD - DEM difference, and every vertex
    without a CAD level gets the inverse-distance mean of the nearest surveyed levels (within drawing_radius_m)."""
    ctrl: Dict[Tuple[float, float], float] = {}
    for f in zf:
        ctrl.update(_cad_z_lookup(f, cp, bool(opts.get("cad_zero_is_missing", True))))
    if len(ctrl) < 10:
        return None
    keys = list(ctrl)
    C = np.array(keys, dtype=float)
    CZ = np.array([ctrl[k] for k in keys], dtype=float)
    from ..geometry.nearest import knn
    P = np.column_stack([X, Y])
    R = float(opts.get("drawing_radius_m") or 100.0)
    d0, i0 = knn(C, P, 1, 0.01)
    d0, i0 = d0[:, 0], np.minimum(i0[:, 0], len(C) - 1)
    at_ctrl = d0 < 0.0015                                  # vertices that carry a CAD level themselves
    ok = ~np.isnan(vals)
    pair = at_ctrl & ok
    if pair.sum() < 10:
        return None
    diff = CZ[i0[pair]] - vals[pair]
    off = float(np.median(diff))
    spread = float(1.4826 * np.median(np.abs(diff - off)))
    vals[ok] = vals[ok] + off                              # DEM in the drawing's datum (used where no level is near)
    need = ~at_ctrl
    n_interp = 0
    if need.any():
        d, idx = knn(C, P[need], min(8, len(C)), R)
        fin = np.isfinite(d)
        w = np.where(fin, 1.0 / np.maximum(d, 0.5) ** 2, 0.0)
        z = np.where(fin, CZ[np.minimum(idx, len(CZ) - 1)], 0.0)
        has = w.sum(axis=1) > 0
        est = np.where(has, (w * z).sum(axis=1) / np.where(has, w.sum(axis=1), 1.0), np.nan)
        pos = np.flatnonzero(need)[has]
        vals[pos] = est[has]
        drawing[pos] = True
        n_interp = int(has.sum())
    return {"control_points": len(ctrl), "paired_with_dem": int(pair.sum()), "offset_m": round(off, 3),
            "spread_m": round(spread, 3), "interpolated_vertices": n_interp, "radius_m": R}


def _apply_vertices(f, parts, cad: Dict[Tuple[float, float], float], opts, cfg, vcount, dem_values) -> str:
    """Combine CAD and DEM per vertex, interpolate gaps, rebuild the 3D geometry, store f.z."""
    mode = (cfg or {}).get("mode", "fill_missing") if isinstance(cfg, dict) else "fill_missing"
    zparts, all_z, dem_z = [], [], []
    n_cad = n_dem = n_interp = n_missing = n_nodata_neigh = 0
    cad_dem_diff = []
    for pi, (_, xy) in enumerate(_parts(f.geom)):
        dvals = parts.get(pi, (xy, np.full(len(xy), np.nan), np.zeros(len(xy))))[1] if dem_values else np.full(len(xy), np.nan)
        dflags = parts.get(pi, (xy, None, np.zeros(len(xy))))[2] if dem_values else np.zeros(len(xy))
        z: List[Optional[float]] = []
        for i, (x, y) in enumerate(xy):
            cz = cad.get((round(x, 3), round(y, 3)))
            dz = None if np.isnan(dvals[i]) else float(dvals[i])
            if dz is not None:
                dem_z.append(dz)
                if dflags[i]:
                    n_nodata_neigh += 1
            if cz is not None and dz is not None:
                cad_dem_diff.append(abs(cz - dz))
            if mode == "replace":
                v = dz if dz is not None else cz
                src = "dem" if dz is not None else ("cad" if cz is not None else None)
            else:
                v = cz if cz is not None else dz
                src = "cad" if cz is not None else ("dem" if dz is not None else None)
            n_cad += src == "cad"
            n_dem += src == "dem"
            z.append(v)
        if (cfg or {}).get("interpolate_gaps", True):
            z, filled = _fill_gaps(xy, z)
            n_interp += filled
        n_missing += sum(1 for v in z if v is None)
        zparts.append(z)
        all_z += [v for v in z if v is not None]
    total = n_cad + n_dem + n_interp + n_missing
    if isinstance(vcount, Counter):
        vcount.update({"cad": n_cad, "dem": n_dem, "interpolated": n_interp, "missing": n_missing,
                       "dem_nodata_neighbours": n_nodata_neigh})
    if n_missing:
        status = "missing" if n_missing == total else "partial"
    elif n_cad and (n_dem or n_interp):
        status = "mixed"
    elif n_cad:
        status = "cad"
    elif n_dem:
        status = "dem" if not n_interp else "dem+interpolated"
    else:
        status = "missing"
    info: Dict[str, Any] = {"status": status, "n_vertices": total, "n_cad": n_cad, "n_dem": n_dem,
                            "n_interpolated": n_interp, "n_missing": n_missing}
    if all_z:
        info.update({"min": round(min(all_z), 3), "max": round(max(all_z), 3), "mean": round(float(np.mean(all_z)), 3)})
        first = zparts[0]
        info["start"] = round(first[0], 3) if first and first[0] is not None else None
        info["end"] = round(first[-1], 3) if first and first[-1] is not None else None
    if dem_z:
        info["dem_mean"] = round(float(np.mean(dem_z)), 3)
    if cad_dem_diff:
        info["cad_dem_max_diff"] = round(max(cad_dem_diff), 3)
    f.z = info
    if n_missing == 0 and all_z:
        f.geom = _rebuild(f.geom, zparts)
        f.zparts = None
    else:
        f.zparts = zparts          # kept for the missing-value policy
    if status in ("dem", "mixed", "dem+interpolated") or n_interp:
        parts_txt = [p for p, c in (("DEM", n_dem), ("CAD", n_cad), ("interpolated", n_interp)) if c]
        f.remarks.append("Z from " + " + ".join(parts_txt))
    return status


def _accuracy_check(dem, opts, issues, report):
    """DMT standard 3.4, Table 2: std. dev. of heights for vector data by area type."""
    from ..submission import AREA_TYPES
    area = opts.get("area_type") or "city"
    need = AREA_TYPES.get(area, AREA_TYPES["city"])["vector_height"]
    acc = dem.meta.get("vertical_accuracy_m")
    if opts.get("dem_accuracy_m") not in (None, ""):
        acc = float(opts["dem_accuracy_m"])
    res = {"area_type": area, "required_std_m": need, "dem_std_m": acc,
           "meets": None if acc is None else acc <= need}
    report["accuracy"] = res
    if acc is None:
        issues.add("warning", "geometry", "elevation_accuracy_unknown",
                   f"Elevation: the vertical accuracy of {dem.meta.get('label')} is not declared — DMT Table 2 requires "
                   f"a height std. dev. below {need} m for {AREA_TYPES[area]['label']}",
                   suggestion="state the survey DTM accuracy in the elevation options")
    elif acc > need:
        issues.add("warning", "geometry", "elevation_accuracy_insufficient",
                   f"Elevation: {dem.meta.get('label')} has a vertical accuracy of about ±{acc:g} m (std. dev.) — DMT Table 2 "
                   f"requires below {need} m for {AREA_TYPES[area]['label']}. Use it only where no surveyed level exists, "
                   f"and prefer a survey DTM for as-built submission",
                   suggestion="upload the project's survey DTM (GeoTIFF) or provide levels in the drawing")


def _missing_policy(zf, opts, issues, report):
    pol = opts.get("missing_policy") or "block"
    nd = opts.get("nodata_value")
    affected = [f for f in zf if f.z and f.z.get("n_missing")]
    report["missing_features"] = len(affected)
    by_cls = Counter(f.target for f in affected)
    for f in affected:
        if pol == "nodata_value" and nd is not None:
            zp = [[float(nd) if v is None else v for v in part] for part in (f.zparts or [])]
            f.geom = _rebuild(f.geom, zp)
            f.z["status"] += "+nodata_value"
            f.remarks.append(f"elevation unavailable for {f.z['n_missing']} vertices — explicit no-data value {nd} written")
        elif pol == "exclude":
            f.write = False
            f.remarks.append("not written: elevation unavailable")
        loc = f.location()
        it = issues.add("error" if pol == "block" else "warning", "geometry",
                        "elevation_missing" if pol == "block" else "elevation_missing_handled",
                        f"{f.target} #{f.fid}: no elevation for {f.z['n_missing']} of {f.z['n_vertices']} vertices "
                        f"(outside the DEM or NoData; no CAD Z)" +
                        ("" if pol == "block" else (" — feature excluded" if pol == "exclude" else f" — no-data value {nd} written")),
                        suggestion="extend / change the DEM, upload a local DTM, add CAD levels, or choose how missing "
                                   "elevations are handled in the Elevation step",
                        target_layer=f.target, feature_id=f.fid, location=loc, source=f.source.source_ref())
        f.issue_ids.append(it.id)
    report["missing_by_class"] = dict(by_cls)
    report["missing_policy"] = pol


def _validate_z(zf, dem, opts, issues, report):
    """Z-enabled geometry checks run before the geodatabase is written (and before the DMT QC)."""
    lo_hi = (dem.meta.get("min"), dem.meta.get("max")) if dem else (None, None)
    max_slope = float(opts.get("max_slope_pct") or 100.0) / 100.0
    tol = float(opts.get("cad_dem_tolerance") or 5.0)
    spikes = out_of_range = nonfinite = 0
    diff_by_cls: Counter = Counter()
    for f in zf:
        if f.geom is None or not f.geom.has_z or not f.write:
            continue
        bad = False
        for kind, seq in _parts3(f.geom):
            zs = [c[2] for c in seq]
            if not all(math.isfinite(z) for z in zs):
                nonfinite += 1
                bad = True
                break
            if kind != "pt":
                for a, b in zip(seq, seq[1:]):
                    dxy = math.hypot(b[0] - a[0], b[1] - a[1])
                    if dxy > 0.5 and abs(b[2] - a[2]) / dxy > max_slope:
                        spikes += 1
                        it = issues.add("warning", "geometry", "z_spike",
                                        f"{f.target} #{f.fid}: elevation jumps {abs(b[2] - a[2]):.2f} m over {dxy:.2f} m "
                                        f"(slope {100 * abs(b[2] - a[2]) / dxy:.0f}% > {100 * max_slope:.0f}%)",
                                        suggestion="check the CAD level or the DEM at this vertex",
                                        target_layer=f.target, feature_id=f.fid, location=(b[0], b[1]),
                                        source=f.source.source_ref())
                        f.issue_ids.append(it.id)
                        break
        if bad:
            it = issues.add("error", "geometry", "z_not_finite", f"{f.target} #{f.fid}: non-finite Z value",
                            target_layer=f.target, feature_id=f.fid, source=f.source.source_ref())
            f.issue_ids.append(it.id)
            continue
        z = f.z or {}
        if lo_hi[0] is not None and z.get("n_dem") and (z["min"] < lo_hi[0] - 50 or z["max"] > lo_hi[1] + 50) \
                and opts.get("vertical_target") in (None, "", "source", "ras_ghumays") and not opts.get("vertical_offset") \
                and opts.get("rg_offset") in (None, ""):
            out_of_range += 1
            issues.add("warning", "geometry", "z_out_of_range",
                       f"{f.target} #{f.fid}: Z {z['min']}…{z['max']} outside the DEM range {lo_hi[0]}…{lo_hi[1]}",
                       target_layer=f.target, feature_id=f.fid, source=f.source.source_ref())
        if z.get("cad_dem_max_diff") is not None and z["cad_dem_max_diff"] > tol:
            diff_by_cls[f.target] += 1
    for cls, n in diff_by_cls.items():
        issues.add("info", "geometry", "z_cad_dem_difference",
                   f"{cls}: {n} feature(s) whose CAD levels differ from the DEM ground by more than {tol} m "
                   f"(expected for buried assets such as inverts; check for surface assets)", target_layer=cls)
    report["validation"] = {"spikes": spikes, "out_of_range": out_of_range, "non_finite": nonfinite,
                            "cad_dem_difference_features": int(sum(diff_by_cls.values()))}


def _parts3(g):
    if isinstance(g, Point):
        return [("pt", [g.coords[0]])]
    if isinstance(g, LineString):
        return [("ln", list(g.coords))]
    if isinstance(g, MultiLineString):
        return [("ln", list(p.coords)) for p in g.geoms]
    if isinstance(g, Polygon):
        return [("ring", list(r.coords)) for r in [g.exterior, *g.interiors]]
    if isinstance(g, MultiPolygon):
        return [x for p in g.geoms for x in _parts3(p)]
    return []


def z_value(f, what: str) -> Optional[float]:
    """Field values derived from the elevation step — never 0 for 'unknown'."""
    z = getattr(f, "z", None) or {}
    if z.get("status") in (None, "none", "missing", "partial") or "nodata_value" in z.get("status", ""):
        return None
    if what == "dem_ground" and z.get("no_ground"):
        return None
    if what != "dem_ground" and z.get("no_fields"):
        return None
    if what == "z":
        if f.geom is not None and f.geom.geom_type == "Point" and f.geom.has_z:
            return round(f.geom.z, 3)
        return z.get("mean")
    if what == "start_z":
        return z.get("start")
    if what == "end_z":
        return z.get("end")
    if what == "dem_ground":
        return z.get("dem_mean")
    return None


def verify_written_z(gdb_path: str, features) -> Dict[str, Any]:
    """Re-open the output and compare every written vertex Z with the computed one (runs before the DMT QC)."""
    from pyogrio import raw
    from shapely import from_wkb
    expected: Dict[str, Dict[int, Any]] = defaultdict(dict)
    for f in features:
        if not f.failed and f.write and f.geom is not None and f.geom.has_z and f.objectid is not None:
            expected[f.target][int(f.objectid)] = f.geom
    checked = mismatched = missing = 0
    examples: List[str] = []
    for fc, exp in expected.items():
        try:
            _, fids, geoms, _ = raw.read(gdb_path, layer=fc, columns=[], return_fids=True)
        except Exception as e:  # noqa: BLE001
            examples.append(f"{fc}: cannot read ({e})")
            missing += len(exp)
            continue
        got = {int(i): g for i, g in zip(fids, geoms)}
        for oid, g in exp.items():
            w = got.get(oid)
            if w is None:
                missing += 1
                continue
            wg = from_wkb(w)
            # the geodatabase may reorder ring vertices (orientation, start point): compare by position
            # vertices are matched within 1 cm: the geodatabase snaps XY to its resolution grid, so a coordinate on a
            # rounding boundary (…0.695 → 0.6950000003) must still find its computed vertex
            want = defaultdict(list)
            for _, seq in _parts3(g):
                for c in seq:
                    want[(round(c[0], 2), round(c[1], 2))].append(c)

            def z_of(x, y):
                kx, ky = round(x, 2), round(y, 2)
                for dx in (0, -0.01, 0.01):
                    for dy in (0, -0.01, 0.01):
                        for c in want.get((round(kx + dx, 2), round(ky + dy, 2)), ()):
                            if abs(c[0] - x) <= 0.01 and abs(c[1] - y) <= 0.01:
                                return c[2]
                return None
            have = [(c[0], c[1], c[2] if len(c) > 2 else None) for _, seq in _parts3(wg) for c in seq]
            checked += 1
            zs = [(h, z_of(h[0], h[1])) for h in have]
            bad = [h for h, z in zs if h[2] is None or (z is not None and abs(z - h[2]) > 0.002)]
            unmatched = sum(1 for _, z in zs if z is None)
            if bad or unmatched > max(1, len(have) // 10):
                mismatched += 1
                if len(examples) < 5:
                    examples.append(f"{fc} OBJECTID {oid}")
    return {"checked": checked, "mismatched": mismatched, "missing": missing, "examples": examples,
            "passed": mismatched == 0 and missing == 0}
