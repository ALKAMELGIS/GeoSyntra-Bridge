"""
Topology rule checks, driven by schema.topology_rules (imported from the
template geodatabase's topology). GDAL cannot create a geodatabase
topology, so the rules are evaluated during validation and every violation
is reported with its location; nothing is modified or discarded.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from shapely import STRtree
from shapely.geometry import MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from ..engine.model import IssueLog, TargetFeature
from ..schema.models import SchemaDefinition

ALIASES = {
    "esriTRTAreaNoOverlap": "area_no_overlap", "esriTRTAreaNoGaps": "area_no_gaps",
    "esriTRTLineNoOverlap": "line_no_overlap", "esriTRTLineNoSelfIntersect": "line_no_self_intersect",
    "esriTRTLineNoSelfOverlap": "line_no_self_overlap", "esriTRTLineNoMultipart": "line_no_multipart",
    "esriTRTPointDisjoint": "point_disjoint", "esriTRTLineNoIntersection": "line_no_intersect",
    "esriTRTAreaNoOverlapArea": "area_no_overlap_area",
    "esriTRTPointCoveredByLineEndpoint": "point_covered_by_line_endpoint",
    "esriTRTPointCoveredByLine": "point_covered_by_line",
    "esriTRTLineNoDangles": "line_no_dangles",
}
LABEL = {
    "area_no_overlap": "Must not overlap", "area_no_gaps": "Must not have gaps",
    "line_no_overlap": "Must not overlap", "line_no_self_intersect": "Must not self-intersect",
    "line_no_self_overlap": "Must not self-overlap", "line_no_multipart": "Must be single part",
    "point_disjoint": "Must be disjoint", "line_no_intersect": "Must not intersect",
    "area_no_overlap_area": "Must not overlap with",
    "point_covered_by_line_endpoint": "Must be covered by endpoint of",
    "point_covered_by_line": "Point must be covered by line",
    "line_no_dangles": "Must not have dangles",
}


def _norm(rule: str) -> str:
    r = ALIASES.get(rule, rule)
    return re.sub(r"[^a-z_]", "", r.lower())


ASSET_CODES = ("STL", "ITC", "RD", "SW", "IR", "SL", "HL")
EVALUATED = {"area_no_overlap", "area_no_gaps", "line_no_overlap", "line_no_self_intersect", "line_no_self_overlap",
             "line_no_multipart", "point_disjoint", "line_no_intersect", "area_no_overlap_area",
             "point_covered_by_line_endpoint", "point_covered_by_line", "line_no_dangles"}


def asset_code(schema: SchemaDefinition) -> Optional[str]:
    sid = schema.schema_id.lower()
    for c in ASSET_CODES:
        if re.match(rf"^adm_{c.lower()}(_|$)", sid):
            return c
    return None


def load_standard_rules() -> Dict[str, Any]:
    import json
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     "knowledge", "qc", "standard_topology.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {}


def standard_comparison(schema: SchemaDefinition) -> Optional[Dict[str, Any]]:
    """Section 4 of the DMT submission standard vs the rules encoded in the official template topology.
    -> {asset, section, rows: [{geometry, rule, label, in_standard, in_template, classes, note}], advisory: [TopologyRuleDef]}
    'advisory' = rule types the standard requires for a geometry but the template topology has for no class of that
    geometry; they are evaluated as information on the classes that already carry template rules of that geometry."""
    if getattr(schema, "parts", None):      # composite: each template compared with its own section
        rows, adv, secs = [], [], []
        for part in schema.parts.values():
            c = standard_comparison(part)
            if c:
                secs.append(f"{c['asset']} {c['section']}")
                rows += [{**r, "geometry": f"{c['asset']} {r['geometry']}"} for r in c["rows"]]
                adv += c["advisory"]
        return {"asset": "several", "section": ", ".join(secs), "rows": rows, "advisory": adv} if rows else None
    code = asset_code(schema)
    std = load_standard_rules()
    if not code or code not in std:
        return None
    from ..schema.models import TopologyRuleDef
    gt = {ly.name: ly.geometry_type for ly in schema.layers}
    tmpl: Dict[Tuple[str, str], set] = defaultdict(set)
    for tr in schema.topology_rules:
        tmpl[(gt.get(tr.origin, "?"), _norm(tr.rule))].add(tr.origin)
    rows, advisory = [], []
    notes = std.get("_notes", {})
    for geom in ("point", "line", "polygon"):
        rules_std = std[code].get(geom, [])
        rules_all = list(dict.fromkeys(rules_std + [r for (g, r) in tmpl if g == geom]))
        for r in rules_all:
            classes = sorted(tmpl.get((geom, r), set()))
            row = {"geometry": geom, "rule": r, "label": LABEL.get(r, r.replace("_", " ")), "in_standard": r in rules_std,
                   "in_template": bool(classes), "classes": classes, "note": notes.get(r, "")}
            if r in rules_std and not classes and r in EVALUATED and r not in notes and not r.startswith("point_covered"):
                carriers = sorted({c for (g, _), cs in tmpl.items() if g == geom for c in cs})
                for c in carriers:
                    advisory.append(TopologyRuleDef(r, c))
                row["note"] = (f"not in the template topology — evaluated as information on {len(carriers)} {geom} class(es)"
                               if carriers else "not in the template topology")
            elif r in rules_std and not classes and not row["note"]:
                row["note"] = "not in the template topology"
            elif not (r in rules_std) and classes:
                row["note"] = "template rule not listed in the standard table — evaluated (the template is what DMT QC uses)"
            rows.append(row)
    return {"asset": code, "section": std[code].get("section"), "rows": rows, "advisory": advisory}


def check_topology(schema: SchemaDefinition, features: List[TargetFeature], issues: IssueLog,
                   tol: float, standard_advisory: bool = True, report: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """-> {rule display name: violations}. When `report` is given it receives the section-4 comparison of the
    submission standard with the template topology, with the violations found per rule."""
    by: Dict[str, List[TargetFeature]] = defaultdict(list)
    for f in features:
        if not f.failed and f.write and f.geom is not None:
            by[f.target].append(f)
    out: Dict[str, int] = {}
    seen_rules = set()
    comp = standard_comparison(schema) if standard_advisory else None
    adv = comp["advisory"] if comp else []
    by_rule: Dict[Tuple[str, bool], int] = {}
    for tr in list(schema.topology_rules) + adv:
        advisory = tr in adv and tr not in schema.topology_rules
        rule = _norm(tr.rule)
        key = (rule, tr.origin, tr.destination)
        if key in seen_rules:
            continue
        seen_rules.add(key)
        feats = by.get(tr.origin, [])
        if not feats:
            continue
        name = ("[standard " + str(comp["section"]) + "] " if advisory else "") + f"{tr.origin}: {LABEL.get(rule, rule)}" + (f" {tr.destination}" if tr.destination and tr.destination != tr.origin
           and rule in ("area_no_overlap_area", "point_covered_by_line_endpoint", "point_covered_by_line") else "")
        n = 0

        def add(f: TargetFeature, msg: str, loc, related=(), shape=None):
            nonlocal n
            n += 1
            it = issues.add("info" if advisory else "warning", "topology_standard" if advisory else "topology", rule,
                            f"{tr.origin} #{f.fid}: {msg} (rule '{name}'"
                            + (", DMT standard only — not in the template topology)" if advisory else ")"),
                            suggestion=("required by the submission standard table; the template topology does not check it"
                                        if advisory else "correct the geometry in CAD; the template topology will flag it in ArcGIS too"),
                            target_layer=tr.origin, feature_id=f.fid, location=loc, source=f.source.source_ref(),
                            related=list(related), shape=shape if shape is not None else (Point(loc) if loc else None))
            f.issue_ids.append(it.id)
            f.remarks.append(f"topology: {LABEL.get(rule, rule).lower()}")

        if rule in ("area_no_overlap", "line_no_overlap", "point_disjoint", "line_no_intersect"):
            geoms = [f.geom for f in feats]
            tree = STRtree(geoms)
            for i, f in enumerate(feats):
                probe = f.geom.buffer(tol) if rule == "point_disjoint" else f.geom
                for j in tree.query(probe):
                    j = int(j)
                    if j <= i:
                        continue
                    o = feats[j]
                    if rule == "point_disjoint":
                        if f.geom.distance(o.geom) <= tol:
                            add(o, f"coincides with #{f.fid}", (o.geom.x, o.geom.y), [f.fid])
                    elif rule == "area_no_overlap":
                        inter = f.geom.intersection(o.geom)
                        if inter.area > max(tol * tol, 1e-6):
                            p = inter.representative_point()
                            add(o, f"overlaps #{f.fid} by {inter.area:.2f} m²", (p.x, p.y), [f.fid], inter)
                    elif rule == "line_no_overlap":
                        inter = f.geom.intersection(o.geom)
                        if inter.length > tol:
                            p = inter.representative_point()
                            add(o, f"overlaps #{f.fid} along {inter.length:.2f} m", (p.x, p.y), [f.fid], inter)
                    elif rule == "line_no_intersect":
                        if f.geom.crosses(o.geom):
                            p = f.geom.intersection(o.geom).representative_point()
                            add(o, f"intersects #{f.fid}", (p.x, p.y), [f.fid])
        elif rule == "area_no_gaps":
            u = unary_union([f.geom for f in feats])
            polys = list(u.geoms) if isinstance(u, MultiPolygon) else [u] if isinstance(u, Polygon) else []
            for poly in polys:
                for ring in poly.interiors:
                    gap = Polygon(ring)
                    if gap.area <= max(tol * tol, 1e-6):
                        continue
                    p = gap.representative_point()
                    near = min(feats, key=lambda f: f.geom.distance(p))
                    add(near, f"gap of {gap.area:.2f} m² enclosed by {tr.origin} polygons", (p.x, p.y), (), gap)
        elif rule in ("line_no_self_intersect", "line_no_self_overlap"):
            for f in feats:
                if not f.geom.is_simple:
                    add(f, "self-intersects" if rule == "line_no_self_intersect" else "self-overlaps", f.location())
        elif rule in ("point_covered_by_line_endpoint", "point_covered_by_line"):
            dest = by.get(tr.destination or "", [])
            targets = []
            for d in dest:
                for ln in getattr(d.geom, "geoms", [d.geom]):
                    if rule == "point_covered_by_line_endpoint":
                        targets += [Point(ln.coords[0][:2]), Point(ln.coords[-1][:2])]
                    else:
                        targets.append(ln)
            tree = STRtree(targets) if targets else None
            for f in feats:
                for pt in getattr(f.geom, "geoms", [f.geom]):
                    ok = tree is not None and any(targets[int(i)].distance(pt) <= tol for i in tree.query(pt.buffer(tol)))
                    if not ok:
                        what = "an end of" if rule == "point_covered_by_line_endpoint" else ""
                        add(f, f"not on {what} any {tr.destination} line".replace("  ", " ")
                            + ("" if dest else f" (no {tr.destination} features delivered)"), (pt.x, pt.y))
                        break
        elif rule == "line_no_dangles":
            ends = []
            for f in feats:
                for k, ln in enumerate(getattr(f.geom, "geoms", [f.geom])):
                    ends.append((f, Point(ln.coords[0][:2])))
                    ends.append((f, Point(ln.coords[-1][:2])))
            lines = [f.geom for f in feats]
            ltree = STRtree(lines)
            for f, p in ends:
                touching = [int(i) for i in ltree.query(p.buffer(tol)) if lines[int(i)].distance(p) <= tol]
                others = [i for i in touching if feats[i] is not f]
                # also a closed own geometry or own vertex touch is not a dangle
                own_ends = sum(1 for g, q in ends if g is f and q.distance(p) <= tol)
                if not others and own_ends < 2:
                    add(f, "dangling end (not connected to another line)", (p.x, p.y))
        elif rule == "line_no_multipart":
            for f in feats:
                if isinstance(f.geom, MultiLineString) and len(f.geom.geoms) > 1:
                    add(f, f"has {len(f.geom.geoms)} parts", f.location())
        else:
            issues.add("info", "topology", "rule_not_evaluated",
                       f"Topology rule '{tr.rule}' on {tr.origin} is not evaluated by the converter — validate it in ArcGIS",
                       target_layer=tr.origin)
            continue
        out[name] = n
        by_rule[(rule, advisory)] = by_rule.get((rule, advisory), 0) + n
    if comp is not None and report is not None:
        for row in comp["rows"]:
            v_t, v_a = by_rule.get((row["rule"], False)), by_rule.get((row["rule"], True))
            row["violations"] = v_t if v_t is not None else v_a
            row["advisory"] = v_a is not None and v_t is None
        report.update({k: comp[k] for k in ("asset", "section", "rows")})
    return out


def check_dmt_spatial_rules(schema: SchemaDefinition, features: List[TargetFeature], issues: IssueLog,
                            tol: float = 0.001) -> int:
    """The DMT QC tool's appconfig.json rules, evaluated before the geodatabase is written:
    spatial_start_end  (LIGHTCABLE must start/end on STREETLIGHT / CONTROLCABINET / JUNCTIONBOX / EARTHING)
    spatial_intersect  (every non-Solar STREETLIGHT must sit on a LIGHTCABLE end)."""
    from shapely.geometry import Point
    from shapely.strtree import STRtree
    try:
        from ..gdb.dmt_qc import load_rules
        rules = load_rules()
    except Exception:  # noqa: BLE001
        return 0
    by_fc: Dict[str, List[TargetFeature]] = {}
    for f in features:
        if not f.failed and f.write and f.geom is not None:
            by_fc.setdefault(f.target, []).append(f)
    ds_names = {fd.name for fd in schema.feature_datasets}
    n = 0

    def geoms(ref: str):
        return [f.geom for f in by_fc.get(ref.split(".")[-1], [])]

    for ds, fcs in rules.items():
        if ds not in ds_names:
            continue
        for fc, items in fcs.items():
            subj = by_fc.get(fc, [])
            if not subj:
                continue
            for ri in items:
                if "spatial_start_end" in ri:
                    r = ri["spatial_start_end"]
                    op = all if r.get("condition") == "all" else any
                    trees = {}
                    for end, refs in r["checks"].items():
                        gs = [g for ref in refs for g in geoms(ref)]
                        trees[end] = (STRtree(gs), gs) if gs else (None, [])
                    for f in subj:
                        lines = list(getattr(f.geom, "geoms", [f.geom]))
                        res = []
                        for end in r["checks"]:
                            c = lines[0].coords[0] if end == "START" else lines[-1].coords[-1]
                            p = Point(c[0], c[1])
                            tree, gs = trees[end]
                            res.append(bool(tree is not None and any(gs[i].distance(p) <= tol for i in tree.query(p.buffer(tol)))))
                        if not op(res):
                            n += 1
                            it = issues.add("error", "network", "dmt_spatial_rule",
                                            f"{fc} #{f.fid}: {r['error']} (DMT QC rule)",
                                            suggestion="snap the cable ends to the connected assets in the drawing",
                                            target_layer=fc, feature_id=f.fid, location=lines[0].coords[0][:2],
                                            source=f.source.source_ref())
                            f.issue_ids.append(it.id)
                if "spatial_intersect" in ri:
                    r = ri["spatial_intersect"]
                    ends = []
                    for ref in r["checks"]:
                        for g in geoms(ref):
                            for ln in getattr(g, "geoms", [g]):
                                ends += [Point(ln.coords[0][:2]), Point(ln.coords[-1][:2])]
                    tree = STRtree(ends) if ends else None
                    for f in subj:
                        u = f.attrs.get("UNITTYPE")
                        if u and str(u).strip().lower() == "solar":
                            continue
                        if tree is None or not any(ends[i].distance(f.geom) <= tol for i in tree.query(f.geom.buffer(tol))):
                            n += 1
                            c = f.geom.representative_point()
                            it = issues.add("error", "network", "dmt_spatial_rule",
                                            f"{fc} #{f.fid}: {r['error']} (DMT QC rule)",
                                            suggestion="connect the light to a cable end, or set UNITTYPE = Solar",
                                            target_layer=fc, feature_id=f.fid, location=(c.x, c.y),
                                            source=f.source.source_ref())
                            f.issue_ids.append(it.id)
    return n
