"""
Advanced CAD clean-up — changes that COULD alter the design are only proposed, never applied automatically.
Each proposal has a stable id (kind + handles), a short explanation and the geometry involved, so it can be
reviewed on a map and approved or rejected. Approved proposals are applied every time the drawing is read.

Proposals
  cross_layer_duplicate  same geometry on two layers                         -> remove the copy
  near_duplicate         same layer and type, within the review tolerance    -> remove the copy
  overlap                a line lying completely on another line (same layer) -> remove the covered line
  open_gap               a line end stops just short of another line end     -> snap the end
  near_closed            a polyline whose ends almost meet                   -> close it as an area
  self_intersection      an area whose boundary crosses itself              -> repair (largest valid part)
  line_self_intersection a line that crosses itself                          -> review only
"""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from typing import Dict, List, Optional

from ..geometry.spatial import GridIndex
from .cleaning import _kill, geom_key
from .model import LINE, POINT, POLYGON, CadDocument, CadEntity

DEFAULTS = {"tolerance": 0.001, "review_tolerance": 0.05, "gap_tolerance": 0.5}
MAX_PROPOSALS = 3000

KIND_LABEL = {
    "cross_layer_duplicate": "Same geometry on two layers",
    "near_duplicate": "Near-duplicate objects",
    "overlap": "Overlapping lines",
    "open_gap": "Open gaps",
    "near_closed": "Almost-closed polylines",
    "self_intersection": "Self-intersecting areas",
    "line_self_intersection": "Self-intersecting lines",
}


def _pid(kind: str, handles: List[str]) -> str:
    return hashlib.sha1((kind + "|" + "|".join(sorted(handles))).encode()).hexdigest()[:12]


def _xy(e: CadEntity) -> List[List[float]]:
    return [[round(c[0], 3), round(c[1], 3)] for c in (e.coords or ([e.center] if e.center else []))]


def _prop(kind: str, action: str, ents: List[CadEntity], detail: str, keep: Optional[CadEntity] = None, **extra) -> Dict:
    handles = [e.handle for e in ents]
    p = {"id": _pid(kind, handles + ([keep.handle] if keep else [])), "kind": kind, "label": KIND_LABEL[kind], "action": action,
         "handles": handles, "layer": ents[0].layer, "layers": sorted({e.layer for e in ents} | ({keep.layer} if keep else set())),
         "detail": detail, "keep": keep.handle if keep else None,
         "geom": [{"h": e.handle, "k": e.kind, "c": _xy(e), "role": "change"} for e in ents]
                 + ([{"h": keep.handle, "k": keep.kind, "c": _xy(keep), "role": "keep"}] if keep else [])}
    p.update(extra)
    return p


def _dist_pt_seg(p, a, b) -> float:
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _on_line(pts, other, tol) -> bool:
    """Every vertex and segment midpoint of `pts` lies on polyline `other` (within tol)."""
    probe = list(pts) + [((pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2) for i in range(len(pts) - 1)]
    for p in probe:
        if min(_dist_pt_seg(p, other[i], other[i + 1]) for i in range(len(other) - 1)) > tol:
            return False
    return True


def analyse(cad: CadDocument, settings: Optional[Dict] = None, role_of=None) -> List[Dict]:
    s = {**DEFAULTS, **(settings or {})}
    tol, rtol, gtol = float(s["tolerance"]), float(s["review_tolerance"]), float(s["gap_tolerance"])
    ents = [e for e in cad.entities if e.kind in (POINT, LINE, POLYGON) and (e.coords or e.center)]
    roles = role_of or (lambda layer: (cad.layer_roles.get(layer) or {}).get("role", "asset"))
    out: List[Dict] = []

    # ---- same geometry on different layers (keep the copy on an asset layer)
    by_geom: Dict = defaultdict(list)
    for e in ents:
        by_geom[(e.kind, e.block_name, geom_key(e, tol))].append(e)
    for group in by_geom.values():
        layers = {e.layer for e in group}
        if len(layers) < 2:
            continue
        group = sorted(group, key=lambda e: (roles(e.layer) != "asset", e.layer))
        keep = group[0]
        copies = [e for e in group[1:] if e.layer != keep.layer]
        if copies:
            out.append(_prop("cross_layer_duplicate", "remove", copies,
                             f"Also drawn on {keep.layer} — keep that one, remove the copy on {', '.join(sorted({c.layer for c in copies}))}", keep))

    # ---- near-duplicates (same layer & type, within the review tolerance but not identical)
    idx = GridIndex(max(rtol * 4, 0.5))
    for e in ents:
        b = e.bbox()
        if b:
            idx.insert(b, e)
    done = set()
    for e in ents:
        if id(e) in done or e.kind == POLYGON and e.dxftype != "CIRCLE" and len(e.coords) > 200:
            continue
        p0 = e.coords[0] if e.coords else e.center
        if not p0:
            continue
        for o in idx.query(p0[0], p0[1], rtol):
            if o is e or id(o) in done or o.layer != e.layer or o.kind != e.kind or o.block_name != e.block_name:
                continue
            if geom_key(o, tol) == geom_key(e, tol):
                continue
            if e.kind == POINT:
                d = math.hypot(o.coords[0][0] - e.coords[0][0], o.coords[0][1] - e.coords[0][1])
                same = d <= rtol
            elif e.dxftype == "CIRCLE" and o.dxftype == "CIRCLE" and e.center and o.center:
                d = math.hypot(o.center[0] - e.center[0], o.center[1] - e.center[1])
                same = d <= rtol and abs((o.radius or 0) - (e.radius or 0)) <= rtol
            elif len(o.coords) == len(e.coords) and len(e.coords) <= 200:
                fw = max(math.hypot(a[0] - c[0], a[1] - c[1]) for a, c in zip(e.coords, o.coords))
                bw = max(math.hypot(a[0] - c[0], a[1] - c[1]) for a, c in zip(e.coords, reversed(o.coords)))
                d = min(fw, bw)
                same = d <= rtol
            else:
                same = False
            if same:
                done.add(id(o))
                out.append(_prop("near_duplicate", "remove", [o], f"{d * 1000:.0f} mm from an identical {e.dxftype.lower()} on the same layer", e))
                if len(out) > MAX_PROPOSALS:
                    break

    # ---- overlapping lines (one completely on another, same layer)
    lines = [e for e in ents if e.kind == LINE and 2 <= len(e.coords) <= 500 and e.length() < 2000]
    if len(lines) <= 20000:
        lidx = GridIndex(5.0)
        for e in lines:
            lidx.insert(e.bbox(), e)
        covered = set()
        for e in lines:
            b = e.bbox()
            for o in lidx.query((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, max(b[2] - b[0], b[3] - b[1]) / 2 + tol):
                if o is e or o.layer != e.layer or id(o) in covered or id(e) in covered:
                    continue
                if geom_key(o, tol) == geom_key(e, tol):
                    continue
                ob = o.bbox()
                if ob[0] - tol > b[0] or ob[1] - tol > b[1] or ob[2] + tol < b[2] or ob[3] + tol < b[3]:
                    continue
                if o.length() + tol < e.length():
                    continue
                if _on_line(e.coords, o.coords, tol * 10):
                    covered.add(id(e))
                    out.append(_prop("overlap", "remove", [e], f"{e.length():.2f} m line lies completely on a longer line of the same layer", o))
                    break

    # ---- open gaps between line ends, and almost-closed polylines
    ends = []
    for e in lines:
        ends.append((e, 0, e.coords[0]))
        ends.append((e, -1, e.coords[-1]))
    eidx = GridIndex(max(gtol * 2, 0.5))
    for t in ends:
        eidx.insert((t[2][0], t[2][1], t[2][0], t[2][1]), t)
    seen_pairs = set()
    for e, i, p in ends:
        if e.kind == LINE and len(e.coords) >= 4 and i == 0:
            gap = math.hypot(e.coords[0][0] - e.coords[-1][0], e.coords[0][1] - e.coords[-1][1])
            if tol < gap <= gtol:
                out.append(_prop("near_closed", "close", [e], f"Ends {gap:.3f} m apart — close it as an area?"))
                continue
        near = [t for t in eidx.query(p[0], p[1], gtol) if t[0] is not e and t[0].layer == e.layer]
        touching = [t for t in near if math.hypot(t[2][0] - p[0], t[2][1] - p[1]) <= tol]
        if touching:
            continue
        cand = [(math.hypot(t[2][0] - p[0], t[2][1] - p[1]), t) for t in near]
        cand = [c for c in cand if tol < c[0] <= gtol]
        if not cand:
            continue
        d, t = min(cand, key=lambda c: c[0])
        pair = tuple(sorted([(e.handle, i), (t[0].handle, t[1])]))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        out.append(_prop("open_gap", "snap", [e], f"Line end {d:.3f} m short of another {e.layer} line end", t[0],
                         end=i, to=[t[2][0], t[2][1]]))

    # ---- self-intersections
    try:
        from shapely.geometry import LineString, Polygon
        from shapely.validation import explain_validity
        for e in ents:
            if e.kind == POLYGON and e.dxftype != "CIRCLE" and len(e.coords) >= 4:
                pg = Polygon([(c[0], c[1]) for c in e.coords])
                if not pg.is_valid:
                    out.append(_prop("self_intersection", "repair", [e], explain_validity(pg)))
            elif e.kind == LINE and 4 <= len(e.coords) <= 2000:
                ls = LineString([(c[0], c[1]) for c in e.coords])
                if not ls.is_simple:
                    out.append(_prop("line_self_intersection", "review", [e], "The line crosses itself — check it in CAD"))
    except Exception:  # noqa: BLE001 - shapely missing: skip the check
        pass
    return out[:MAX_PROPOSALS]


def apply(cad: CadDocument, proposals: List[Dict], approved: set) -> List[Dict]:
    """Apply approved proposals; returns cleaning-log entries."""
    by_h = {e.handle: e for e in cad.entities}
    done: Dict[str, List[CadEntity]] = defaultdict(list)
    for p in proposals:
        if p["id"] not in approved:
            continue
        targets = [by_h[h] for h in p["handles"] if h in by_h and by_h[h].kind in (POINT, LINE, POLYGON)]
        for e in targets:
            if p["action"] == "remove":
                _kill(cad, e, "removed in reviewed clean-up")
            elif p["action"] == "snap" and e.kind == LINE:
                to = p.get("to")
                i = p.get("end", 0)
                c = list(e.coords)
                z = c[i][2] if len(c[i]) > 2 else 0.0
                c[i] = (to[0], to[1], z)
                e.coords = c
            elif p["action"] == "close" and e.kind == LINE:
                e.coords = list(e.coords) + [e.coords[0]]
                e.kind, e.closed = POLYGON, True
            elif p["action"] == "repair" and e.kind == POLYGON:
                try:
                    from shapely.geometry import Polygon
                    from shapely.validation import make_valid
                    g = make_valid(Polygon([(c[0], c[1]) for c in e.coords]))
                    polys = [x for x in getattr(g, "geoms", [g]) if x.geom_type == "Polygon"]
                    if polys:
                        best = max(polys, key=lambda x: x.area)
                        z = e.coords[0][2] if len(e.coords[0]) > 2 else 0.0
                        e.coords = [(x, y, z) for x, y in best.exterior.coords]
                except Exception:  # noqa: BLE001
                    continue
            else:
                continue
            done[p["kind"]].append(e)
    log = []
    for k, es in done.items():
        by_layer: Dict[str, int] = defaultdict(int)
        for e in es:
            by_layer[e.layer] += 1
        log.append({"action": f"Reviewed clean-up: {KIND_LABEL[k]}", "count": len(es), "layers": dict(by_layer),
                    "handles": [e.handle for e in es[:50]], "note": "approved by the user", "kind": k, "mode": "reviewed"})
    cad.cleaning.extend(log)
    return log


def summary(proposals: List[Dict], decisions: Dict[str, str]) -> Dict:
    by: Dict[str, Dict[str, int]] = {}
    for p in proposals:
        d = by.setdefault(p["kind"], {"label": KIND_LABEL[p["kind"]], "total": 0, "approved": 0, "rejected": 0})
        d["total"] += 1
        st = decisions.get(p["id"])
        if st in ("approved", "rejected"):
            d[st] += 1
    return by
