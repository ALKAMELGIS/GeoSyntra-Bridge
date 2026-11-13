"""
One-click geometry corrections for topology findings (the Review screen's Snap / Clip / Fill / Merge buttons).

Each fix works in the target CRS on the geometry of the last validation (earlier edits included), checks the result
(type, validity, size), and stores it in feature_edits.json exactly like a hand edit — so it can be undone with
"restore", and the next validation re-runs every rule. A finding the converter cannot fix safely is reported, not
guessed: the engineer then edits it on the map.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import shapely
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree

from .assign import EDITS, AssignError, _lock, edit_key

SNAP_MAX = 1.0          # metres: furthest an end / point is moved to meet a line (larger gaps are drawn on the map)
ACTIONS = {
    "line_no_dangles": "Snap", "point_covered_by_line_endpoint": "Snap", "point_covered_by_line": "Snap",
    "area_no_overlap": "Clip", "area_no_overlap_area": "Clip", "area_no_gaps": "Fill gap",
    "point_disjoint": "Remove duplicate", "line_no_multipart": "Merge",
    "line_no_self_overlap": "Clean", "invalid_geometry": "Repair", "self_intersection": "Repair",
}


def _rule_dest(schema, origin: str, code: str) -> Optional[str]:
    from ..validation.topology import _norm
    for tr in schema.topology_rules:
        if tr.origin == origin and _norm(tr.rule) == code:
            return tr.destination
    return None


def _live(res, cls: str, exclude=()):
    cache = res.__dict__.setdefault("_gsb_live", {})
    if cls not in cache:
        cache[cls] = [f for f in res.features if f.target == cls and not f.failed and f.write and f.geom is not None]
    return [f for f in cache[cls] if f.fid not in exclude]


def _near(res, cls: str, at, exclude=(), endpoints=False):
    """Nearest geometry of a class to `at` within SNAP_MAX (an STRtree per class, kept on the result) -> (geom, dist)."""
    trees = res.__dict__.setdefault("_gsb_trees", {})
    key = (cls, endpoints)
    if key not in trees:
        fs = _live(res, cls)
        if endpoints:
            items = [(f.fid, Point(ln.coords[e][:2])) for f in fs for ln in getattr(f.geom, "geoms", [f.geom]) for e in (0, -1)]
        else:
            items = [(f.fid, shapely.force_2d(f.geom)) for f in fs]
        trees[key] = (STRtree([g for _, g in items]) if items else None, items)
    tree, items = trees[key]
    if tree is None:
        return None, None
    best = (None, None)
    for i in tree.query(at.buffer(SNAP_MAX)):
        fid, g = items[int(i)]
        if fid in exclude:
            continue
        d = g.distance(at)
        if best[1] is None or d < best[1]:
            best = (g, d)
    return best


BIG_GAP = 1.0           # m²: a larger enclosed hole is often a real opening (planter, tree pit) — filled only on request


def check(res, it) -> Tuple[Optional[str], str]:
    """(button label | None, why not) — shown next to the finding before anything is changed."""
    if it.code not in ACTIONS:
        return None, ""
    try:
        _fix_one(res, it, set())
        return ACTIONS[it.code], ""
    except ValueError as e:
        return None, str(e)


def _fix_one(res, it, deleted: set, cur: Optional[dict] = None) -> Tuple[Optional[Any], Optional[str], str]:
    """-> (feature, new geometry | None for delete, note) or raises ValueError with the reason it was not fixed."""
    by_fid = res.__dict__.get("_gsb_by_fid") or res.__dict__.setdefault("_gsb_by_fid", {f.fid: f for f in res.features})
    f = by_fid.get(it.feature_id)
    if f is None or f.geom is None:
        raise ValueError("feature not found")
    g = shapely.force_2d((cur or {}).get(f.fid, f.geom))
    code = it.code
    loc = Point(it.location) if it.location else None

    if code == "line_no_dangles":
        if loc is None:
            raise ValueError("no location")
        tgt, d = _near(res, f.target, loc, exclude={f.fid} | deleted)
        if tgt is None:
            raise ValueError(f"no other {f.target} line within {SNAP_MAX:g} m")
        to = nearest_points(loc, tgt)[1]
        parts = list(getattr(g, "geoms", [g]))
        best = None
        for k, ln in enumerate(parts):
            cs = list(ln.coords)
            for end in (0, -1):
                dd = Point(cs[end][:2]).distance(loc)
                if best is None or dd < best[0]:
                    best = (dd, k, end)
        _, k, end = best
        cs = [c[:2] for c in parts[k].coords]
        cs[end] = (to.x, to.y)
        parts[k] = LineString(cs)
        return f, (parts[0] if len(parts) == 1 else MultiLineString(parts)), f"end moved {d:.2f} m"

    if code in ("point_covered_by_line_endpoint", "point_covered_by_line"):
        dest = _rule_dest(res.schema, f.target, code)
        if not dest:
            raise ValueError("destination class unknown")
        tgt, d = _near(res, dest, g, exclude=deleted, endpoints=code == "point_covered_by_line_endpoint")
        if tgt is None:
            raise ValueError(f"no {dest} within {SNAP_MAX:g} m")
        to = tgt if code == "point_covered_by_line_endpoint" else nearest_points(g, tgt)[1]
        return f, Point(to.x, to.y), f"moved {d:.2f} m onto {dest}"

    if code in ("area_no_overlap", "area_no_overlap_area"):
        rel = [by_fid[r] for r in it.related if r in by_fid and r not in deleted]
        if not rel:
            raise ValueError("the overlapping feature is gone")
        cut = g.difference(unary_union([shapely.force_2d(r.geom) for r in rel]))
        cut = shapely.make_valid(cut)
        polys = [p for p in getattr(cut, "geoms", [cut]) if isinstance(p, Polygon) and p.area > 1e-6]
        if not polys:
            raise ValueError("nothing would remain of the polygon")
        out = polys[0] if len(polys) == 1 else MultiPolygon(polys)
        return f, out, f"clipped {g.area - out.area:.2f} m²"

    if code == "area_no_gaps":
        gap = it.shape
        if gap is None or gap.is_empty:
            raise ValueError("gap shape unknown")
        out = shapely.make_valid(g.union(gap.buffer(0.001)).buffer(0))
        if not isinstance(out, (Polygon, MultiPolygon)):
            raise ValueError("the result is not a polygon")
        return f, out, f"gap of {gap.area:.2f} m² filled"

    if code == "point_disjoint":
        return f, None, "duplicate removed"

    if code in ("line_no_multipart", "line_no_self_overlap"):
        merged = shapely.line_merge(unary_union(g) if code == "line_no_self_overlap" else g)
        if not isinstance(merged, LineString):
            raise ValueError("the parts do not join end to end — connect them on the map")
        return f, merged, "parts merged"

    if code in ("invalid_geometry", "self_intersection"):
        out = shapely.make_valid(g)
        if g.geom_type.endswith("Polygon"):
            polys = [p for p in getattr(out, "geoms", [out]) if isinstance(p, Polygon) and p.area > 1e-6]
            if not polys:
                raise ValueError("nothing valid remains")
            out = polys[0] if len(polys) == 1 else MultiPolygon(polys)
        return f, out, "repaired"

    raise ValueError("no automatic correction for this rule")


def autofix(mgr, job_id: str, keys: List[str], bulk: bool = False) -> Dict[str, Any]:
    from .topology_edit import error_key_map
    lk = _lock(job_id)
    if not lk.acquire(timeout=30):
        raise AssignError("another save is still running for this job", 409)
    try:
        res = mgr._results.get(job_id) or mgr._convert(job_id, None)
        kmap = error_key_map(res)
        want = set(keys)
        data = mgr._read(job_id, EDITS) or {}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        fixed, skipped, touched, deleted, current = [], [], set(), set(), {}
        for it in res.issues.items:
            k = kmap.get(it.id)
            if k not in want or it.code not in ACTIONS:
                continue
            if it.feature_id in deleted:
                continue
            if bulk and it.code == "area_no_gaps" and it.shape is not None and it.shape.area > BIG_GAP:
                skipped.append({"key": k, "reason": f"gap of {it.shape.area:.1f} m² may be a real opening — fill it on its own"})
                continue
            try:
                f, g, note = _fix_one(res, it, deleted, current)
            except ValueError as e:
                skipped.append({"key": k, "reason": str(e)})
                continue
            rec = dict(data.get(edit_key(f)) or {})
            rec.update(fid=f.fid, at=now, auto=ACTIONS[it.code])
            if g is None:
                rec["deleted"] = True
                rec.pop("geometry", None)
                deleted.add(f.fid)
            else:
                if g.is_empty or (isinstance(g, (Polygon, MultiPolygon)) and not g.is_valid):
                    skipped.append({"key": k, "reason": "the corrected shape is not valid"})
                    continue
                rec["geometry"] = g.wkt
                current[f.fid] = g
                rec.pop("deleted", None)
                touched.add(f.fid)
            data[edit_key(f)] = rec
            fixed.append({"key": k, "fid": f.fid, "note": note})
        if fixed:
            mgr._write(job_id, EDITS, data)
        return {"fixed": fixed, "skipped": skipped, "validate": bool(fixed)}
    finally:
        lk.release()


# ----------------------------------------------------------------------------------------------- guidance
GUIDE: Dict[str, Dict[str, str]] = {
    "line_no_dangles": {"why": "Line end touches nothing", "how": "Snap · or drag the end onto the line"},
    "area_no_overlap": {"why": "Areas overlap", "how": "Clip the shared part"},
    "area_no_overlap_area": {"why": "Overlaps another layer", "how": "Clip the shared part"},
    "area_no_gaps": {"why": "Hole between areas", "how": "Fill gap · or keep if it is a real opening"},
    "point_disjoint": {"why": "Duplicate point", "how": "Remove the copy"},
    "point_covered_by_line": {"why": "Point off its line", "how": "Snap onto the line"},
    "point_covered_by_line_endpoint": {"why": "Point off the line end", "how": "Snap onto the end"},
    "line_no_multipart": {"why": "Line in pieces", "how": "Merge · or Split"},
    "line_no_self_overlap": {"why": "Line runs over itself", "how": "Clean"},
    "line_no_self_intersect": {"why": "Line crosses itself", "how": "Move the looping vertex · or Split"},
    "line_no_overlap": {"why": "Lines on top of each other", "how": "Delete the duplicate"},
    "line_no_intersect": {"why": "Lines cross", "how": "Split at the crossing · or move one"},
    "invalid_geometry": {"why": "Invalid shape", "how": "Repair"},
    "self_intersection": {"why": "Outline crosses itself", "how": "Repair"},
    "z_spike": {"why": "Elevation jump", "how": "Check the level · or keep"},
}
DEFAULT_GUIDE = {"why": "Breaks a template rule", "how": "Edit on the map · or keep"}


def guide(code: str) -> Dict[str, str]:
    return GUIDE.get(code, DEFAULT_GUIDE)


# ----------------------------------------------------------------------------------------------- preview (dry run)
def preview(mgr, job_id: str, key: str) -> Dict[str, Any]:
    """What a fix would do, without changing anything: the corrected shape in the preview CRS."""
    from shapely.geometry import mapping as shp_mapping
    from .topology_edit import error_key_map
    from ..geometry.crs import transformer
    res = mgr._results.get(job_id) or mgr._convert(job_id, None)
    kmap = error_key_map(res)
    it = next((i for i in res.issues.items if kmap.get(i.id) == key and i.code in ACTIONS), None)
    if it is None:
        raise AssignError("no automatic correction for this finding", 404)
    try:
        f, g, note = _fix_one(res, it, set())
    except ValueError as e:
        return {"action": None, "reason": str(e)}
    pv = mgr._read(job_id, "preview.geojson") or {}
    out = g
    if g is not None and pv.get("crs_is_geographic") and res.target_epsg:
        fwd = transformer(res.target_epsg, 4326)

        def fn(a):
            x, y = fwd.transform(a[:, 0], a[:, 1])
            a[:, 0], a[:, 1] = x, y
            return a
        out = shapely.transform(g, fn)
    return {"action": ACTIONS[it.code], "fid": f.fid, "note": note, "delete": g is None,
            "geometry": shp_mapping(out) if out is not None else None}


# ----------------------------------------------------------------------------------------------- the full run
RUNS = "autofix_runs.json"        # [{at, snapshot (feature_edits before), report}] — newest last, for Undo
LEDGER = "autofix_ledger.json"    # {key: reason} fixes that failed verification — never tried again automatically
MAX_PASSES = 3


def _bad(res) -> Dict[int, Dict[str, int]]:
    """fid -> {code: count} of the geometry / topology findings on it (exceptions left out)."""
    from .topology_edit import CATS, GEOM_CODES
    out: Dict[int, set] = {}
    for it in res.issues.items:
        if it.feature_id is None or "marked as exception" in it.message:
            continue
        if it.category in CATS or (it.category == "geometry" and it.code in GEOM_CODES):
            d = out.setdefault(it.feature_id, {})
            d[it.code] = d.get(it.code, 0) + 1
    return out


def run(mgr, job_id: str, codes: Optional[List[str]] = None, progress=None) -> Dict[str, Any]:
    """Analyse → fix → verify → revert, up to MAX_PASSES.

    Every pass applies the fixes that are safe (bulk rules), re-runs the whole conversion and compares, feature by
    feature, the findings before and after: a fix that leaves the feature with a finding it did not have (a snap that
    creates an overlap, a clip that opens a gap...) or with more findings is reverted and written to the ledger, so it
    is not tried again. The drawing itself is never changed: fixes live in feature_edits.json, and the state before the
    run is kept so the whole run can be undone."""
    from .topology_edit import error_key_map
    p = progress or (lambda *_: None)
    edits0 = mgr._read(job_id, EDITS) or {}
    ledger = mgr._read(job_id, LEDGER) or {}
    res = mgr._results.get(job_id) or mgr._convert(job_id, None)
    before_total = sum(sum(v.values()) for v in _bad(res).values())
    by_rule0: Dict[str, int] = {}
    for it in res.issues.items:
        if it.code in ACTIONS and "marked as exception" not in it.message:
            by_rule0[it.code] = by_rule0.get(it.code, 0) + 1
    tried: set = set()
    report = {"passes": 0, "fixed": [], "reverted": [], "skipped": {}, "before": before_total}
    for n in range(MAX_PASSES):
        p(0.05 + 0.3 * n, f"Auto-Fix pass {n + 1}: analysing")
        kmap = error_key_map(res)
        keys = sorted({kmap[it.id] for it in res.issues.items
                       if it.code in ACTIONS and "marked as exception" not in it.message and (not codes or it.code in codes)
                       and not str(ledger.get(kmap.get(it.id), "")).startswith("reverted") and kmap.get(it.id) not in tried})
        if not keys:
            break
        bad_before = _bad(res)
        snap = mgr._read(job_id, EDITS) or {}
        r = autofix(mgr, job_id, keys, bulk=True)
        for s_ in r["skipped"]:
            tried.add(s_["key"])
            reason = s_["reason"].split(" (")[0]
            report["skipped"][reason] = report["skipped"].get(reason, 0) + 1
        if not r["fixed"]:
            break
        report["passes"] = n + 1
        p(0.15 + 0.3 * n, f"Auto-Fix pass {n + 1}: {len(r['fixed'])} corrected — verifying")
        res = mgr._convert(job_id, None)
        bad_after = _bad(res)
        cur = mgr._read(job_id, EDITS) or {}
        by_fid = {f.fid: f for f in res.features}
        reverted = []
        for fx in r["fixed"]:
            fid = fx["fid"]
            b, a = bad_before.get(fid, {}), bad_after.get(fid, {})
            new = sorted(set(a) - set(b))
            if new or sum(a.values()) > sum(b.values()):
                f = by_fid.get(fid)
                k = edit_key(f) if f else None
                if k:
                    if k in snap:
                        cur[k] = snap[k]
                    else:
                        cur.pop(k, None)
                ledger[fx["key"]] = f"reverted: it caused {', '.join(new or sorted(a))}"
                reverted.append({**fx, "why": new or sorted(a)})
            else:
                report["fixed"].append(fx)
        if reverted:
            mgr._write(job_id, EDITS, cur)
            report["reverted"].extend(reverted)
            res = mgr._convert(job_id, None)
    mgr._write(job_id, LEDGER, ledger)
    after = _bad(res)
    report["after"] = sum(sum(v.values()) for v in after.values())
    left: Dict[str, int] = {}
    kmap = error_key_map(res)
    for it in res.issues.items:
        if it.code in ACTIONS and "marked as exception" not in it.message and it.feature_id in after:
            left[it.code] = left.get(it.code, 0) + 1
    report["by_rule"] = {c: {"before": by_rule0.get(c, 0), "after": left.get(c, 0)} for c in sorted(set(by_rule0) | set(left))}
    report["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if report["fixed"] or report["reverted"]:
        runs = mgr._read(job_id, RUNS) or []
        runs.append({"at": report["at"], "snapshot": edits0, "report": {k: v for k, v in report.items() if k not in ("fixed", "reverted")}})
        mgr._write(job_id, RUNS, runs[-10:])
    report["fixed_n"], report["reverted_n"] = len(report["fixed"]), len(report["reverted"])
    report["fixed"], report["reverted"] = report["fixed"][:200], report["reverted"][:200]
    report["undo"] = bool(report["fixed_n"])
    mgr._write(job_id, "autofix_report.json", report)
    return report


def undo_last(mgr, job_id: str) -> Dict[str, Any]:
    """Back to the feature edits as they were before the last Auto-Fix run (hand edits made since are kept)."""
    runs = mgr._read(job_id, RUNS) or []
    if not runs:
        raise AssignError("no Auto-Fix run to undo", 404)
    last = runs.pop()
    cur = mgr._read(job_id, EDITS) or {}
    snap = last["snapshot"]
    out = dict(snap)
    for k, v in cur.items():                 # keep what was not made by the run (attribute values, later hand edits)
        if k not in snap and not v.get("auto"):
            out[k] = v
        elif k in snap and v.get("fields") != snap[k].get("fields"):
            out[k] = {**snap[k], "fields": v.get("fields")}
    mgr._write(job_id, EDITS, out)
    mgr._write(job_id, RUNS, runs)
    mgr._write(job_id, LEDGER, {})
    mgr._write(job_id, "autofix_report.json", {})
    return {"undone": last["at"], "left": len(runs)}
