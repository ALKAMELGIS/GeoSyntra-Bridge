"""
Network connectivity and flow direction (driven by schema.network rules).

* Endpoints of edge features are clustered into nodes (snap tolerance).
* Junction features are attached to nodes; optional snapping moves edge
  ends onto the junction (repair rule snap_line_endpoints).
* Reports: disconnected sub-networks, dangling ends (missing junction),
  orphan junctions, junctions on edge interiors, adverse slopes.
* Gravity networks: orients edges from high to low invert (junction levels
  or edge US/DS levels), reverses geometry digitised against flow, fills
  direction field, upstream/downstream node ids and missing invert levels.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import LineString, MultiLineString, Point

from ..engine.model import IssueLog, TargetFeature
from ..geometry.spatial import GridIndex
from ..schema.models import SchemaDefinition


class _UF:
    def __init__(self):
        self.p: Dict[int, int] = {}

    def find(self, a: int) -> int:
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _ends(g) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    lines = list(g.geoms) if isinstance(g, MultiLineString) else [g]
    return lines[0].coords[0][:2], lines[-1].coords[-1][:2]


def _replace_end(g, which: int, xy) -> MultiLineString:
    lines = [list(l.coords) for l in (g.geoms if isinstance(g, MultiLineString) else [g])]
    if which == 0:
        lines[0][0] = xy
    else:
        lines[-1][-1] = xy
    return MultiLineString([LineString(c) for c in lines])


def _reverse(g) -> MultiLineString:
    lines = [list(reversed(l.coords)) for l in reversed(list(g.geoms if isinstance(g, MultiLineString) else [g]))]
    return MultiLineString([LineString(c) for c in lines])


def analyse_network(schema: SchemaDefinition, features: List[TargetFeature], issues: IssueLog,
                    snap: bool, tol_override: Optional[float] = None) -> Dict[str, Any]:
    net = schema.network
    if not net.enabled:
        return {"enabled": False}
    tol = tol_override or net.snap_tolerance
    edges = [f for f in features if f.target in net.edges and not f.failed and f.geom is not None and f.write]
    juncs = [f for f in features if f.target in net.junctions and not f.failed and f.geom is not None and f.write]
    if not edges:
        return {"enabled": True, "edges": 0, "junctions": len(juncs), "components": 0}

    # ---- endpoint clustering into nodes
    ends: List[Tuple[float, float]] = []
    owner: List[Tuple[TargetFeature, int]] = []
    for f in edges:
        a, b = _ends(f.geom)
        ends += [a, b]
        owner += [(f, 0), (f, 1)]
    gi: GridIndex = GridIndex(max(tol, 1e-6))
    for i, (x, y) in enumerate(ends):
        gi.insert((x, y, x, y), i)
    uf = _UF()
    for i, (x, y) in enumerate(ends):
        uf.find(i)
        for j in gi.query(x, y, tol):
            if j != i and math.hypot(ends[j][0] - x, ends[j][1] - y) <= tol:
                uf.union(i, j)
    node_of = [uf.find(i) for i in range(len(ends))]
    node_pts: Dict[int, List[Tuple[float, float]]] = defaultdict(list)
    for i, n in enumerate(node_of):
        node_pts[n].append(ends[i])
    node_xy = {n: (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) for n, pts in node_pts.items()}
    degree = Counter(node_of)

    # ---- junctions -> nodes
    nidx: GridIndex = GridIndex(max(tol, 1e-6))
    for n, (x, y) in node_xy.items():
        nidx.insert((x, y, x, y), n)
    eidx: GridIndex = GridIndex(max(tol * 20, 1e-6))
    for f in edges:
        eidx.insert(f.geom.bounds, f)
    junction_at: Dict[int, TargetFeature] = {}
    stats = Counter()
    for j in juncs:
        x, y = j.geom.x, j.geom.y
        best, bd = None, float("inf")
        for n in nidx.query(x, y, tol):
            d = math.hypot(node_xy[n][0] - x, node_xy[n][1] - y)
            if d <= tol and d < bd:
                best, bd = n, d
        if best is not None:
            if best in junction_at:
                stats["stacked_junctions"] += 1
                it = issues.add("warning", "topology", "stacked_junction",
                                f"{j.target} #{j.fid} coincides with {junction_at[best].target} #{junction_at[best].fid}",
                                suggestion="remove the duplicate junction or move it to its true position",
                                target_layer=j.target, feature_id=j.fid, location=(x, y), source=j.source.source_ref(),
                                related=[junction_at[best].fid])
                j.issue_ids.append(it.id)
            else:
                junction_at[best] = j
                if snap:
                    node_xy[best] = (x, y)
            continue
        on_edge = next((e for e in eidx.query(x, y, tol) if e.geom.distance(Point(x, y)) <= tol), None)
        if on_edge is not None:
            stats["junction_on_edge_interior"] += 1
            it = issues.add("info", "network", "junction_mid_edge",
                            f"{j.target} #{j.fid} lies on {on_edge.target} #{on_edge.fid} but not at a vertex end",
                            suggestion="split the pipe at the junction in CAD if it is a real network node",
                            target_layer=j.target, feature_id=j.fid, location=(x, y), source=j.source.source_ref(),
                            related=[on_edge.fid])
        else:
            stats["orphan_junctions"] += 1
            it = issues.add("warning", "network", "orphan_junction",
                            f"{j.target} #{j.fid} is not connected to any {'/'.join(net.edges)}",
                            suggestion=f"check that a pipe ends within {tol} m of this junction",
                            target_layer=j.target, feature_id=j.fid, location=(x, y), source=j.source.source_ref())
        j.issue_ids.append(it.id)

    # ---- optional endpoint snapping (repair rule)
    if snap:
        for i, (f, which) in enumerate(owner):
            n = node_of[i]
            target_xy = node_xy[n]
            if math.hypot(ends[i][0] - target_xy[0], ends[i][1] - target_xy[1]) > 1e-9:
                f.geom = _replace_end(f.geom, which, target_xy)
                if "endpoints snapped" not in " ".join(f.remarks):
                    f.remarks.append(f"endpoints snapped to network node (tol {tol} m)")
                stats["snapped_endpoints"] += 1

    # ---- T-connections: a free line end touching another line's interior is connected
    edge_index = {id(f): k for k, (f, w) in enumerate(owner) if w == 0}
    tee_nodes: Dict[int, TargetFeature] = {}
    for i, n in enumerate(node_of):
        if degree[n] != 1:
            continue
        f, _ = owner[i]
        x, y = node_xy[n]
        pt = Point(x, y)
        for other in eidx.query(x, y, tol):
            if other is f or other.geom.distance(pt) > tol:
                continue
            tee_nodes[n] = other
            stats["tee_connections"] += 1
            it = issues.add("info", "network", "tee_connection",
                            f"{f.target} #{f.fid} connects to the interior of {other.target} #{other.fid} (T-connection, not split)",
                            suggestion="acceptable for pressure networks; split the main in CAD if a node is required",
                            target_layer=f.target, feature_id=f.fid, location=(x, y), source=f.source.source_ref(),
                            related=[other.fid])
            f.issue_ids.append(it.id)
            break

    # ---- connectivity components
    euf = _UF()
    for k in range(0, len(owner), 2):
        euf.union(node_of[k], node_of[k + 1])
    for n, other in tee_nodes.items():
        euf.union(n, node_of[edge_index[id(other)]])
    comp: Dict[int, List[TargetFeature]] = defaultdict(list)
    for k in range(0, len(owner), 2):
        comp[euf.find(node_of[k])].append(owner[k][0])
    comps = sorted(comp.values(), key=len, reverse=True)
    for c in comps[1:]:
        loc = c[0].location()
        it = issues.add("warning", "network", "disconnected_subnetwork",
                        f"{len(c)} {'/'.join(sorted({e.target for e in c}))} feature(s) are disconnected from the main network",
                        suggestion=f"connect them (gap > {tol} m) or confirm they are a separate system",
                        target_layer=c[0].target, feature_id=c[0].fid, location=loc,
                        source=c[0].source.source_ref(), related=[e.fid for e in c[1:50]])
        for e in c:
            e.issue_ids.append(it.id)
            e.remarks.append("disconnected from main network")

    # ---- dangles
    for i, n in enumerate(node_of):
        if degree[n] == 1 and n not in junction_at and n not in tee_nodes:
            f, which = owner[i]
            stats["dangles"] += 1
            sev = "warning" if net.require_junction_at_ends else "info"
            it = issues.add(sev, "network", "dangle",
                            f"{f.target} #{f.fid} {'start' if which == 0 else 'end'} is not connected to any feature",
                            suggestion="add the missing manhole/junction or extend the line to it" if net.require_junction_at_ends
                            else "verify this is a true network end", target_layer=f.target, feature_id=f.fid,
                            location=node_xy[n], source=f.source.source_ref())
            f.issue_ids.append(it.id)

    # ---- direction & node ids
    jl = net.junction_level_field
    usf, dsf = net.upstream_level_field, net.downstream_level_field
    reversed_n = undetermined = adverse = 0

    def jid(n: int) -> Optional[str]:
        j = junction_at.get(n)
        if not j:
            return None
        layer = schema.get_layer(j.target)
        idf = layer.id_field if layer else None
        v = j.attrs.get(idf) if idf else None
        return str(v) if v not in (None, "") else None

    def jlevel(n: int) -> Optional[float]:
        j = junction_at.get(n)
        if not j or not jl:
            return None
        v = j.attrs.get(jl)
        return float(v) if isinstance(v, (int, float)) else None

    for k in range(0, len(owner), 2):
        f = owner[k][0]
        a, b = node_of[k], node_of[k + 1]
        if net.flow == "gravity":
            la, lb = jlevel(a), jlevel(b)
            us = f.attrs.get(usf) if usf else None
            ds = f.attrs.get(dsf) if dsf else None
            flip = None
            if isinstance(us, (int, float)) and isinstance(ds, (int, float)) and us < ds:
                adverse += 1
                it = issues.add("warning", "network", "adverse_slope",
                                f"{f.target} #{f.fid}: upstream invert {us} is lower than downstream {ds}",
                                suggestion="check the invert labels — values may be swapped",
                                target_layer=f.target, feature_id=f.fid, location=f.location(), source=f.source.source_ref())
                f.issue_ids.append(it.id)
            if la is not None and lb is not None and la != lb:
                flip = la < lb
            elif la is not None and isinstance(us, (int, float)) and isinstance(ds, (int, float)):
                flip = abs(la - ds) < abs(la - us)
            elif lb is not None and isinstance(us, (int, float)) and isinstance(ds, (int, float)):
                flip = abs(lb - us) < abs(lb - ds)
            if flip is None:
                undetermined += 1
                if net.direction_field:
                    f.attrs[net.direction_field] = 0
                it = issues.add("info", "network", "flow_undetermined",
                                f"{f.target} #{f.fid}: flow direction could not be determined (no invert levels)",
                                suggestion="label invert levels on the pipe or its manholes",
                                target_layer=f.target, feature_id=f.fid, location=f.location(), source=f.source.source_ref())
                f.issue_ids.append(it.id)
            else:
                if flip:
                    f.geom = _reverse(f.geom)
                    a, b = b, a
                    la, lb = lb, la
                    reversed_n += 1
                    f.remarks.append("geometry reversed to follow flow direction")
                if net.direction_field:
                    f.attrs[net.direction_field] = 1
                if usf and f.attrs.get(usf) in (None, "") and la is not None:
                    f.attrs[usf] = la
                    f.remarks.append(f"{usf} taken from upstream junction")
                if dsf and f.attrs.get(dsf) in (None, "") and lb is not None:
                    f.attrs[dsf] = lb
                    f.remarks.append(f"{dsf} taken from downstream junction")
        if net.upstream_node_field and jid(a):
            f.attrs.setdefault(net.upstream_node_field, jid(a))
        if net.downstream_node_field and jid(b):
            f.attrs.setdefault(net.downstream_node_field, jid(b))

    return {"enabled": True, "flow": net.flow, "edges": len(edges), "junctions": len(juncs),
            "nodes": len(node_xy), "components": len(comps),
            "main_component_edges": len(comps[0]) if comps else 0,
            "dangles": stats["dangles"], "orphan_junctions": stats["orphan_junctions"],
            "junction_on_edge_interior": stats["junction_on_edge_interior"],
            "stacked_junctions": stats["stacked_junctions"], "tee_connections": stats["tee_connections"], "snapped_endpoints": stats["snapped_endpoints"],
            "reversed_edges": reversed_n, "direction_undetermined": undetermined if net.flow == "gravity" else None,
            "adverse_slopes": adverse}
