"""
CAD clean-up before transfer — what engineers do in AutoCAD (OVERKILL, PEDIT/JOIN, deleting
zero-length objects) before moving a drawing to GIS. Every action is logged in cad.cleaning
and surfaced in the UI and the processing report; nothing disappears silently.

  1. zero-length / degenerate entities      -> removed (artifact "degenerate entities")
  2. exact duplicates (same layer, type,
     geometry, block and attributes)        -> removed (artifact "exact duplicates (overkill)")
  3. line fragments                          -> joined: chains of open lines on the same layer and
     drawing style that meet end-to-end at a node where nothing else happens (no third line,
     no block / point / junction symbol) become one polyline, like PEDIT > Join.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, List, Tuple

from ..geometry.spatial import GridIndex
from .model import ARTIFACT, LINE, POINT, POLYGON, CadDocument, CadEntity

NODE_TOL = 0.01     # metres: endpoints closer than this are the same node
SYMBOL_TOL = 0.30   # metres: a block / point this close to a node makes it a real network node


def _log(cad: CadDocument, action: str, ents: List[CadEntity], note: str = ""):
    if not ents:
        return
    by_layer: Dict[str, int] = defaultdict(int)
    for e in ents:
        by_layer[e.layer] += 1
    cad.cleaning.append({"action": action, "count": len(ents), "layers": dict(by_layer),
                         "handles": [e.handle for e in ents[:50]], "note": note})


def _kill(cad: CadDocument, e: CadEntity, reason: str):
    e.kind, e.artifact = ARTIFACT, reason
    cad.artifacts[reason] = cad.artifacts.get(reason, 0) + 1


def _style(e: CadEntity) -> Tuple:
    return (e.layer, (e.linetype or "").upper(), e.color, e.lineweight)


AUTO_TOL = 0.001    # metres: geometry closer than this is "the same" (safe automatic clean-up)


def _same(a, b, tol) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol and (len(a) < 3 or len(b) < 3 or abs(a[2] - b[2]) <= max(tol, 1e-6))


def _dedupe_vertices(coords, tol, closed: bool):
    out = [coords[0]]
    for c in coords[1:]:
        if not _same(c, out[-1], tol):
            out.append(c)
    if closed and len(out) > 1 and not _same(out[0], out[-1], tol):
        out.append(out[0])
    elif closed and len(out) > 1:
        out[-1] = out[0]
    return out


def geom_key(e: CadEntity, tol: float):
    """Shape of an entity snapped to the tolerance grid; direction and ring start do not matter."""
    q = lambda c: (round(c[0] / tol), round(c[1] / tol))  # noqa: E731
    if e.dxftype == "CIRCLE" and e.center is not None and e.radius is not None:
        return ("circle", q(e.center), round(e.radius / tol))
    if e.kind == POINT:
        return ("point", q(e.coords[0]), e.block_name, round(e.rotation or 0.0, 1))
    pts = [q(c) for c in e.coords]
    if e.kind == POLYGON:
        ring = pts[:-1] if len(pts) > 1 and pts[0] == pts[-1] else pts
        if not ring:
            return ("polygon",)
        k = ring.index(min(ring))
        fwd = ring[k:] + ring[:k]
        rev = list(reversed(ring)); k = rev.index(min(rev)); rev = rev[k:] + rev[:k]
        return ("polygon", tuple(min(fwd, rev)))
    return ("line", tuple(min(pts, list(reversed(pts)))))


def clean_drawing(cad: CadDocument, tol: float = AUTO_TOL) -> List[Dict[str, object]]:
    """Safe automatic clean-up: only removes what cannot change the design (exact repeats within the tolerance,
    repeated vertices, zero-length objects). Anything that could change the design is left to the advanced
    review (cad.cleanup.analyse)."""
    feats = [e for e in cad.entities if e.kind in (POINT, LINE, POLYGON)]

    # ---- 0. duplicate vertices (consecutive vertices closer than the tolerance)
    fixed, removed_v = [], 0
    for e in feats:
        if e.kind in (LINE, POLYGON) and len(e.coords) > 2 and e.dxftype != "CIRCLE":
            nc = _dedupe_vertices(e.coords, tol, e.kind == POLYGON)
            if len(nc) != len(e.coords):
                removed_v += len(e.coords) - len(nc)
                e.coords = nc
                fixed.append(e)
    if fixed:
        by_layer: Dict[str, int] = defaultdict(int)
        for e in fixed:
            by_layer[e.layer] += 1
        cad.cleaning.append({"action": f"Removed {removed_v:,} duplicate vertices", "count": len(fixed), "layers": dict(by_layer),
                             "handles": [e.handle for e in fixed[:50]], "note": f"consecutive vertices closer than {tol} m — shape unchanged",
                             "kind": "duplicate_vertices", "mode": "auto"})

    # ---- 1. degenerate / zero-length
    bad = []
    for e in feats:
        if e.kind == LINE and (len(e.coords) < 2 or e.length() <= tol):
            bad.append(e)
        elif e.kind == POLYGON and e.dxftype == "CIRCLE" and (e.radius or 0) <= tol:
            bad.append(e)
        elif e.kind == POLYGON and e.dxftype != "CIRCLE" and len({(round(c[0] / tol), round(c[1] / tol)) for c in e.coords}) < 3:
            bad.append(e)
    for e in bad:
        _kill(cad, e, "zero-length geometry")
    _log(cad, "Removed zero-length geometry", bad, f"lines shorter than {tol} m, circles with no radius, areas with < 3 vertices")
    if bad:
        cad.cleaning[-1].update(kind="zero_length", mode="auto")

    # ---- 2. exact duplicates within the tolerance (OVERKILL) — same layer, same type, same block and attributes
    seen: Dict[Tuple, CadEntity] = {}
    dups: Dict[str, List[CadEntity]] = defaultdict(list)
    for e in cad.entities:
        if e.kind not in (POINT, LINE, POLYGON):
            continue
        key = (e.layer, e.kind, e.dxftype if e.dxftype in ("CIRCLE",) else "", e.block_name, geom_key(e, tol),
               tuple(sorted(e.attributes.items())))
        if key in seen:
            t = "circles" if e.dxftype == "CIRCLE" else "arcs" if e.dxftype == "ARC" else "points / blocks" if e.kind == POINT \
                else "polylines" if "POLYLINE" in e.dxftype else "lines" if e.kind == LINE else "areas"
            dups[t].append(e)
        else:
            seen[key] = e          # the first one is kept with all its properties
    for t, ents in dups.items():
        for e in ents:
            _kill(cad, e, "exact duplicates (overkill)")
        _log(cad, f"Removed duplicate {t} (OVERKILL)", ents, f"same layer, geometry within {tol} m, same block and attributes — first kept")
        cad.cleaning[-1].update(kind="duplicate", mode="auto")

    # ---- 3. join line fragments (PEDIT > Join)
    lines = [e for e in cad.entities if e.kind == LINE and len(e.coords) >= 2]
    symbols = GridIndex(1.0)
    for e in cad.entities:
        if (e.kind == POINT or e.dxftype == "CIRCLE") and e.center:
            symbols.insert((e.center[0], e.center[1], e.center[0], e.center[1]), e)
    nodes: Dict[Tuple, List[Tuple[CadEntity, int]]] = defaultdict(list)

    def nk(p):
        return (round(p[0] / NODE_TOL), round(p[1] / NODE_TOL))

    for e in lines:
        nodes[nk(e.coords[0])].append((e, 0))
        nodes[nk(e.coords[-1])].append((e, 1))

    def joinable(node) -> bool:
        ends = nodes[node]
        if len(ends) != 2 or ends[0][0] is ends[1][0]:
            return False
        a, b = ends[0][0], ends[1][0]
        if _style(a) != _style(b):
            return False
        x, y = node[0] * NODE_TOL, node[1] * NODE_TOL
        return not any(math.hypot(s.center[0] - x, s.center[1] - y) <= SYMBOL_TOL for s in symbols.query(x, y, SYMBOL_TOL))

    used = set()
    joined_away: List[CadEntity] = []
    chains = 0
    for start in lines:
        if id(start) in used:
            continue
        # walk backwards to a chain end, then forwards collecting
        cur, end_idx = start, 0
        guard = 0
        while guard < 100000:
            guard += 1
            node = nk(cur.coords[0] if end_idx == 0 else cur.coords[-1])
            if not joinable(node):
                break
            nxt = next(x for x in nodes[node] if x[0] is not cur)
            if id(nxt[0]) in used or nxt[0] is start:
                break
            cur, end_idx = nxt[0], 1 - nxt[1]
        # cur is a chain end; its free end is end_idx -> orient so the chain starts there
        chain = []
        pts = list(cur.coords) if end_idx == 0 else list(reversed(cur.coords))
        chain.append(cur)
        used.add(id(cur))
        while True:
            node = nk(pts[-1])
            if not joinable(node):
                break
            nxt = next(x for x in nodes[node] if x[0] is not chain[-1])
            if id(nxt[0]) in used:
                break
            seg = list(nxt[0].coords) if nxt[1] == 0 else list(reversed(nxt[0].coords))
            pts.extend(seg[1:])
            chain.append(nxt[0])
            used.add(id(nxt[0]))
        if len(chain) > 1:
            head = chain[0]
            head.coords = pts
            head.dxftype = "POLYLINE (joined)"
            head.handle = head.handle + f"+{len(chain) - 1}"
            for e in chain[1:]:
                _kill(cad, e, "line fragments joined into polylines")
                joined_away.append(e)
            chains += 1
    _log(cad, f"Joined line fragments into {chains} polylines (PEDIT > Join)", joined_away,
         "consecutive segments of the same layer and style, no junction symbol at the joint")
    if joined_away:
        cad.cleaning[-1].update(kind="join", mode="auto")
    return cad.cleaning
