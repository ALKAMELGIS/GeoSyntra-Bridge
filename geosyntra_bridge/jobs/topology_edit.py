"""
Topology step — error inspector and geometry editing (the converter's equivalent of ArcGIS Pro's Error Inspector).

Errors are the topology / spatial-rule / geometry findings of the last validation, each with its shape.
Geometry edits come back from the map as GeoJSON in the preview CRS (WGS84 or drawing units), are moved to the
target CRS, snapped back onto the original vertices they did not move (no drift), checked (type, validity, size)
and stored in feature_edits.json; the engine applies them on the next validation, which re-runs every rule.
Errors can be marked as exceptions (kept, reported as info), as in a geodatabase topology.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

import numpy as np
import shapely
from shapely.geometry import mapping as shp_mapping, shape as shp_shape

from .assign import EDITS, AssignError, _lock, edit_key
from .topology_fix import ACTIONS, LEDGER, check as fix_check, guide
import time as _time

EXC = "topology_exceptions.json"
CATS = ("topology", "topology_standard", "network")
GEOM_CODES = {"z_spike", "z_not_finite", "geometry_repaired", "self_intersection", "invalid_geometry"}
GEOM_LABEL = {"z_spike": "Elevation must not jump", "z_not_finite": "Elevation must be a number",
              "self_intersection": "Must not self-intersect", "invalid_geometry": "Geometry must be valid",
              "dmt_spatial_rule": "DMT spatial rule"}


def _key(it, by_fid) -> str:
    f = by_fid.get(it.feature_id)
    rel = sorted(by_fid[r].source.handle for r in (it.related or []) if r in by_fid)
    return f"{it.code}|{f.source.handle if f else ''}|{','.join(rel)}|{it.target_layer or ''}"


def error_key_map(result) -> Dict[int, str]:
    by_fid = {f.fid: f for f in result.features}
    return {it.id: _key(it, by_fid) for it in result.issues.items}


def apply_exceptions(result, exceptions: Dict[str, Any]):
    """Errors the engineer marked as exceptions stay listed, as information."""
    if not exceptions:
        return
    keys = error_key_map(result)
    for it in result.issues.items:
        if keys.get(it.id) in exceptions and (it.category in CATS or it.code in GEOM_CODES):
            it.severity = "info"
            if "exception" not in it.message:
                it.message += " — marked as exception"


def errors(mgr, job_id: str) -> Dict[str, Any]:
    from ..engine.conversion import issue_locations_wgs84
    res = mgr._results.get(job_id) or mgr._convert(job_id, None)
    exc = mgr._read(job_id, EXC) or {}
    keys = error_key_map(res)
    by_fid = {f.fid: f for f in res.features}
    locs = {d["id"]: d for d in issue_locations_wgs84(res)}
    rows: List[Dict[str, Any]] = []
    t0 = _time.monotonic()
    ledger = mgr._read(job_id, LEDGER) or {}
    for it in res.issues.items:
        if not (it.category in CATS or (it.category == "geometry" and it.code in GEOM_CODES)):
            continue
        if it.code in ("rule_not_evaluated", "geometry_repaired"):   # repaired on reading: information, not an error
            continue
        d = locs.get(it.id) or {}
        # checked up front while it is quick; past the time budget the button is offered and the fix reports what it could not do
        action, why = fix_check(res, it) if _time.monotonic() - t0 < 8 else (ACTIONS.get(it.code), "")
        f = by_fid.get(it.feature_id)
        rel = [r for r in (it.related or []) if r in by_fid]
        msg = it.message.split(": ", 1)[-1]
        rule = it.code
        if "(rule '" in msg:
            rule = msg.split("(rule '", 1)[1].split("'", 1)[0]
            msg = msg.split(" (rule '", 1)[0]
            rule = re.sub(r"^\[standard[^\]]*\]\s*", "", rule)
        rule = GEOM_LABEL.get(rule, rule)
        rows.append({"id": it.id, "key": keys[it.id], "rule": rule, "code": it.code, "severity": it.severity,
                     "message": msg, "class": it.target_layer, "fid": it.feature_id,
                     "handle": f.source.handle if f else None, "related": rel,
                     "related_class": by_fid[rel[0]].target if rel else None,
                     "lonlat": d.get("lonlat"), "shape": d.get("shape"),
                     "exception": keys[it.id] in exc, "standard": it.category == "topology_standard",
                     "action": action, "no_fix": why or None, "guide": guide(it.code), "tried": ledger.get(keys[it.id])})
    return {"errors": rows, "exceptions": len(exc),
            "counts": {"errors": sum(1 for r in rows if not r["exception"]), "exceptions": sum(1 for r in rows if r["exception"])}}


def set_exception(mgr, job_id: str, key: str, on: bool) -> Dict[str, Any]:
    exc = mgr._read(job_id, EXC) or {}
    if on:
        exc[key] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    else:
        exc.pop(key, None)
    mgr._write(job_id, EXC, exc)
    return {"key": key, "exception": on, "exceptions": len(exc)}


def _kind(g) -> str:
    t = g.geom_type
    return "point" if "Point" in t else "line" if "Line" in t else "polygon" if "Polygon" in t else t


def save_geometry(mgr, job_id: str, edits: List[Dict[str, Any]]) -> Dict[str, Any]:
    """All pending edits of the map in one save: [{fid, geometry (GeoJSON, preview CRS)} | {fid, delete: true}]."""
    from ..geometry.crs import transformer
    lk = _lock(job_id)
    if not lk.acquire(timeout=30):
        raise AssignError("another save is still running for this job", 409)
    try:
        res = mgr._results.get(job_id) or mgr._convert(job_id, None)
        schema = res.schema
        by_fid = {f.fid: f for f in res.features}
        pv = mgr._read(job_id, "preview.geojson") or {}
        back = transformer(4326, res.target_epsg) if pv.get("crs_is_geographic") and res.target_epsg else None
        prepared, problems = [], []
        adds = []
        for e in edits:
            if e.get("add"):                                  # a new feature drawn on the map
                tl = schema.get_layer(str(e.get("add")))
                if tl is None:
                    problems.append(f"unknown feature class {e.get('add')}")
                    continue
                try:
                    g = _to_target(shp_shape(e["geometry"]), back)
                except Exception as ex:  # noqa: BLE001
                    problems.append(f"new {tl.name}: unreadable geometry ({ex})")
                    continue
                err = _check(g, (tl.geometry_type or "").lower())
                if err:
                    problems.append(f"new {tl.name}: {err}")
                    continue
                adds.append((tl.name, g))
                continue
            f = by_fid.get(int(e.get("fid", -1)))
            if f is None or not f.target:
                problems.append(f"feature #{e.get('fid')} not found")
                continue
            if e.get("delete"):
                prepared.append((f, None, True))
                continue
            try:
                g = shp_shape(e["geometry"])
            except Exception as ex:  # noqa: BLE001
                problems.append(f"#{f.fid}: unreadable geometry ({ex})")
                continue
            g = shapely.force_2d(g)
            if back is not None:
                def fn(a):
                    x, y = back.transform(a[:, 0], a[:, 1])
                    a[:, 0], a[:, 1] = x, y
                    return a
                g = shapely.transform(g, fn)
            # vertices that were not moved go back to their exact original coordinates (no round-trip drift)
            if f.geom is not None:
                orig = shapely.get_coordinates(shapely.force_2d(f.geom))
                if len(orig):
                    from ..geometry.nearest import knn

                    def snap(a):
                        d, i = knn(orig, a[:, :2], 1, 0.01)
                        d, i = d[:, 0], np.minimum(i[:, 0], len(orig) - 1)
                        m = d < 0.002
                        a[m, 0], a[m, 1] = orig[i[m], 0], orig[i[m], 1]
                        return a
                    g = shapely.transform(g, snap)
            want = (schema.get_layer(f.target).geometry_type or "").lower()
            if g.is_empty:
                problems.append(f"#{f.fid}: empty geometry")
                continue
            if want and _kind(g) != want:
                problems.append(f"#{f.fid}: {_kind(g)} geometry for a {want} feature class")
                continue
            if want == "polygon" and not g.is_valid:
                problems.append(f"#{f.fid}: the polygon is not valid ({shapely.is_valid_reason(g)}) — move the vertex that "
                                "makes the outline cross itself")
                continue
            if want == "line" and g.length <= 0.001:
                problems.append(f"#{f.fid}: zero-length line")
                continue
            prepared.append((f, g, False))
        if problems:
            raise AssignError("; ".join(problems[:6]), 422, problems)
        data = mgr._read(job_id, EDITS) or {}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for tname, g in adds:
            data[f"{tname}|new-{uuid.uuid4().hex[:8]}"] = {"added": True, "geometry": g.wkt, "at": now}
        for f, g, delete in prepared:
            k = edit_key(f)
            rec = dict(data.get(k) or {})
            rec.update(fid=f.fid, at=now)
            if delete:
                rec["deleted"] = True
                rec.pop("geometry", None)
            else:
                rec["geometry"] = g.wkt
                rec.pop("deleted", None)
            data[k] = rec
        mgr._write(job_id, EDITS, data)
        return {"saved": len(prepared) + len(adds), "added": len(adds), "geometry": sum(1 for p in prepared if not p[2]),
                "deleted": sum(1 for p in prepared if p[2]), "validate": True}
    finally:
        lk.release()


def _to_target(g, back):
    g = shapely.force_2d(g)
    if back is not None:
        def fn(a):
            x, y = back.transform(a[:, 0], a[:, 1])
            a[:, 0], a[:, 1] = x, y
            return a
        g = shapely.transform(g, fn)
    return g


def _check(g, want: str) -> str:
    if g.is_empty:
        return "empty geometry"
    if want and _kind(g) != want:
        return f"{_kind(g)} geometry for a {want} feature class"
    if want == "polygon" and (not g.is_valid or g.area <= 0.001):
        return "the polygon is not valid"
    if want == "line" and g.length <= 0.001:
        return "zero-length line"
    return ""


def split(mgr, job_id: str, fid: int, cutter: Dict[str, Any]) -> Dict[str, Any]:
    """Split a line or polygon with a line drawn on the map: the first part keeps the feature (and its CAD handle),
    each other part becomes a new feature carrying the same CAD source, so it gets the same attributes."""
    from shapely.ops import split as shp_split
    from ..geometry.crs import transformer
    lk = _lock(job_id)
    if not lk.acquire(timeout=30):
        raise AssignError("another save is still running for this job", 409)
    try:
        res = mgr._results.get(job_id) or mgr._convert(job_id, None)
        f = next((x for x in res.features if x.fid == fid), None)
        if f is None or f.geom is None:
            raise AssignError("feature not found", 404)
        pv = mgr._read(job_id, "preview.geojson") or {}
        back = transformer(4326, res.target_epsg) if pv.get("crs_is_geographic") and res.target_epsg else None
        knife = _to_target(shp_shape(cutter), back)
        g = shapely.force_2d(f.geom)
        want = _kind(g)
        if want == "point":
            raise AssignError("points cannot be split", 422)
        parts = [p for p in shp_split(g, knife).geoms if (p.length if want == "line" else p.area) > 0.001]
        if len(parts) < 2:
            raise AssignError("the cut line does not cross the feature", 422)
        parts.sort(key=lambda p: -(p.length if want == "line" else p.area))
        data = mgr._read(job_id, EDITS) or {}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rec = dict(data.get(edit_key(f)) or {})
        rec.update(fid=f.fid, at=now, geometry=parts[0].wkt)
        rec.pop("deleted", None)
        data[edit_key(f)] = rec
        for p in parts[1:]:
            data[f"{f.target}|{f.source.handle}~{uuid.uuid4().hex[:6]}"] = {
                "added": True, "copy_of": f.source.handle, "geometry": p.wkt, "at": now,
                **({"fields": dict(rec["fields"])} if rec.get("fields") else {})}
        mgr._write(job_id, EDITS, data)
        return {"parts": len(parts), "validate": True}
    finally:
        lk.release()


def merge(mgr, job_id: str, fids: List[int]) -> Dict[str, Any]:
    """Merge selected features of one layer: the largest keeps its CAD source and values, the others are deleted."""
    from shapely.ops import unary_union, linemerge
    lk = _lock(job_id)
    if not lk.acquire(timeout=30):
        raise AssignError("another save is still running for this job", 409)
    try:
        res = mgr._results.get(job_id) or mgr._convert(job_id, None)
        by = {f.fid: f for f in res.features}
        fs = [by[i] for i in dict.fromkeys(int(x) for x in fids) if i in by and by[i].geom is not None]
        if len(fs) < 2:
            raise AssignError("select two or more features to merge", 422)
        if len({f.target for f in fs}) > 1:
            raise AssignError("merge works inside one layer — the selection spans several", 422)
        kind = _kind(fs[0].geom)
        if kind == "point":
            raise AssignError("points cannot be merged", 422)
        gs = [shapely.force_2d(f.geom) for f in fs]
        u = unary_union(gs)
        if kind == "line":
            u = linemerge(u) if u.geom_type == "MultiLineString" else u
        else:
            u = shapely.make_valid(u)
            if u.geom_type == "GeometryCollection":
                u = unary_union([g for g in u.geoms if g.geom_type.endswith("Polygon")])
        err = _check(u, kind)
        if err:
            raise AssignError(f"merged shape refused: {err}", 422)
        keep = max(fs, key=lambda f: f.geom.length if kind == "line" else f.geom.area)
        data = mgr._read(job_id, EDITS) or {}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rec = dict(data.get(edit_key(keep)) or {})
        rec.update(fid=keep.fid, at=now, geometry=u.wkt)
        data[edit_key(keep)] = rec
        for f in fs:
            if f is keep:
                continue
            r2 = dict(data.get(edit_key(f)) or {})
            if r2.get("added"):
                data.pop(edit_key(f), None)
            else:
                r2.update(fid=f.fid, at=now, deleted=True, merged_into=keep.fid)
                r2.pop("geometry", None)
                data[edit_key(f)] = r2
        mgr._write(job_id, EDITS, data)
        return {"kept": keep.fid, "merged": len(fs), "multipart": u.geom_type.startswith("Multi"), "validate": True}
    finally:
        lk.release()


def explode(mgr, job_id: str, fid: int) -> Dict[str, Any]:
    """A multipart feature becomes one feature per part (each keeps the CAD source, so the same values)."""
    lk = _lock(job_id)
    if not lk.acquire(timeout=30):
        raise AssignError("another save is still running for this job", 409)
    try:
        res = mgr._results.get(job_id) or mgr._convert(job_id, None)
        f = next((x for x in res.features if x.fid == fid), None)
        if f is None or f.geom is None:
            raise AssignError("feature not found", 404)
        parts = list(getattr(shapely.force_2d(f.geom), "geoms", []))
        if len(parts) < 2:
            raise AssignError("the feature has a single part", 422)
        kind = _kind(f.geom)
        parts.sort(key=lambda p: -(p.length if kind == "line" else p.area if kind == "polygon" else 0))
        data = mgr._read(job_id, EDITS) or {}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rec = dict(data.get(edit_key(f)) or {})
        rec.update(fid=f.fid, at=now, geometry=parts[0].wkt)
        data[edit_key(f)] = rec
        for p in parts[1:]:
            data[f"{f.target}|{f.source.handle}~{uuid.uuid4().hex[:6]}"] = {
                "added": True, "copy_of": f.source.handle, "geometry": p.wkt, "at": now,
                **({"fields": dict(rec["fields"])} if rec.get("fields") else {})}
        mgr._write(job_id, EDITS, data)
        return {"parts": len(parts), "validate": True}
    finally:
        lk.release()


def restore(mgr, job_id: str, fid: int) -> Dict[str, Any]:
    """Undo saved geometry edits / deletion of one feature (back to the drawing's geometry)."""
    res = mgr._results.get(job_id) or mgr._convert(job_id, None)
    f = next((x for x in res.features if x.fid == fid), None)
    if f is None:
        raise AssignError("feature not found", 404)
    data = mgr._read(job_id, EDITS) or {}
    rec = data.get(edit_key(f)) or {}
    if rec.get("added"):                       # a feature drawn / split off on the map: undoing it removes it
        data.pop(edit_key(f), None)
        mgr._write(job_id, EDITS, data)
        return {"restored": fid, "removed": True}
    rec.pop("geometry", None)
    rec.pop("deleted", None)
    data[edit_key(f)] = rec
    mgr._write(job_id, EDITS, data)
    return {"restored": fid}


__all__ = ["errors", "set_exception", "save_geometry", "restore", "split", "merge", "explode", "apply_exceptions", "np", "shp_mapping"]
