"""
Label extraction and label-to-geometry association.

1. Each CAD text (TEXT / MTEXT / MULTILEADER) is parsed with the schema's
   label rules. A single label can yield several values
   (e.g. "WM-104 DN200 PVC L=45.3m" -> PIPE_ID, DIAMETER, MATERIAL, LENGTH).
2. Candidate features = features of the rules' target classes within the
   search radius of the label anchor (the leader arrow head when the label is
   a leader, otherwise the text insertion point).
3. Score = distance + orientation penalty (labels on pipes run parallel to
   the pipe) — lowest wins.
4. Detects: unmatched labels (no candidate), ambiguous labels (runner-up
   almost as close), conflicting values (two labels give one feature
   different values), duplicated labels and unrecognised text.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import Point

from ..cad.model import CadEntity
from ..engine.model import IssueLog, TargetFeature
from ..geometry.convert import CoordinatePipeline, line_bearing_at
from ..geometry.spatial import GridIndex
from ..schema.models import LabelRule, SchemaDefinition


@dataclass
class CompiledRule:
    rule: LabelRule
    rx: re.Pattern
    targets: Optional[set]


def compile_rules(schema: SchemaDefinition, active_layers: set) -> List[CompiledRule]:
    out = []
    for r in sorted(schema.label_rules, key=lambda r: -r.priority):
        try:
            rx = re.compile(r.pattern, re.IGNORECASE)
        except re.error:
            continue
        targets = set(r.targets) if r.targets else None
        layers = (targets or {l.name for l in schema.layers}) & active_layers
        layers = {l for l in layers if schema.get_layer(l) and schema.get_layer(l).get_field(r.field)}
        if layers:
            out.append(CompiledRule(r, rx, layers))
    return out


def coerce_label_value(raw: str, rule: LabelRule) -> Any:
    v = raw.strip()
    mapped = rule.value_map.get(v.upper()) if rule.value_map else None
    if mapped is not None:
        return mapped
    t = (rule.value_type or "text").lower()
    try:
        if t == "int":
            return int(round(float(v)))
        if t == "float":
            return float(v)
    except ValueError:
        return v
    if t == "upper":
        return re.sub(r"\s+", "", v.upper()) if re.fullmatch(r"[A-Za-z]+[-_ ]?\d+[A-Za-z]?", v) else v.upper()
    return v


def parse_label(text: str, rules: List[CompiledRule]) -> List[Tuple[CompiledRule, Any, Tuple[int, int]]]:
    """All (rule, value, span) hits for one label; one value per field (highest priority first)."""
    hits = []
    taken = set()
    for cr in rules:
        key = (cr.rule.field.upper(), frozenset(cr.targets))
        if key in taken:
            continue
        m = cr.rx.search(text)
        if not m:
            continue
        gd = m.groupdict()
        raw = next((gd[k] for k in ("v", "v2") if gd.get(k)), None)
        if raw is None:
            raw = next((g for g in m.groups() if g), None) if m.groups() else m.group(0)
        if not raw:
            continue
        hits.append((cr, coerce_label_value(raw, cr.rule), m.span()))
        taken.add(key)
    return hits


def associate_labels(schema: SchemaDefinition, features: List[TargetFeature], texts: List[CadEntity],
                     cp: CoordinatePipeline, radius: float, issues: IssueLog) -> Dict[str, Any]:
    active = {f.target for f in features if not f.failed}
    rules = compile_rules(schema, active)
    idx: Dict[str, GridIndex] = defaultdict(lambda: GridIndex(max(radius, 1e-6)))
    for f in features:
        if f.failed or f.geom is None:
            continue
        idx[f.target].insert(f.geom.bounds, f)

    stats = Counter()
    unrecognised: List[str] = []
    label_rows: List[Dict[str, Any]] = []
    for t in texts:
        if not t.text:
            continue
        stats["labels_total"] += 1
        hits = parse_label(t.text, rules)
        if not hits:
            stats["labels_unrecognised"] += 1
            if len(unrecognised) < 500:
                unrecognised.append(t.text)
            continue
        stats["labels_recognised"] += 1
        anchor_src = t.leader_point or t.center
        ax, ay = cp.point(anchor_src)
        tx, ty = cp.point(t.center)
        search_r = radius * (0.6 if t.leader_point else 1.0)
        if t.text_height:
            search_r = max(search_r, t.text_height * cp.scale * 3)
        layers = set()
        for cr, _, _ in hits:
            layers |= cr.targets
        cands: List[Tuple[float, float, TargetFeature]] = []
        p = Point(ax, ay)
        for lname in layers:
            for f in idx[lname].query(ax, ay, search_r):
                d = f.geom.distance(p)
                if d > search_r:
                    continue
                s = d
                if f.geom.geom_type.endswith("LineString"):
                    b = line_bearing_at(f.geom, ax, ay)
                    if b is not None and not t.leader_point:
                        diff = abs((t.rotation % 180.0) - b)
                        diff = min(diff, 180 - diff)
                        s += (diff / 90.0) * search_r * 0.35
                elif f.geom.geom_type.endswith("Polygon") and f.geom.contains(p):
                    s = 0.0
                cands.append((s, d, f))
        src_ref = {"handle": t.handle, "layer": t.layer, "type": t.dxftype, "text": t.text}
        row = {"handle": t.handle, "layer": t.layer, "text": t.text,
               "fields": {cr.rule.field: v for cr, v, _ in hits}, "feature_id": None, "target": None,
               "distance": None, "status": "matched"}
        if not cands:
            stats["labels_unmatched"] += 1
            row["status"] = "unmatched"
            issues.add("warning", "unmatched_label", "label_unmatched",
                       f"Label '{t.text}' ({', '.join(row['fields'])}) has no {'/'.join(sorted(layers))} feature within {search_r:.1f} m",
                       suggestion="Check the label layer/position or increase the label search radius; "
                                  "the value is not lost — it is listed in the label report.",
                       source=src_ref, location=(tx, ty))
            label_rows.append(row)
            continue
        cands.sort(key=lambda c: c[0])
        best_s, best_d, best = cands[0]
        others = [c for c in cands[1:] if c[2] is not best]
        ambiguous = bool(others) and others[0][0] - best_s < max(0.15 * search_r, 0.25 * max(best_s, 1e-6)) \
            and others[0][2].target == best.target
        row.update({"feature_id": best.fid, "target": best.target, "distance": round(best_d, 3)})
        if ambiguous:
            stats["labels_ambiguous"] += 1
            row["status"] = "ambiguous"
            alt = others[0][2]
            msg = f"Label '{t.text}' is ambiguous between feature #{best.fid} ({best_d:.2f} m) and #{alt.fid} ({others[0][1]:.2f} m)"
            it = issues.add("warning", "ambiguous_label", "label_ambiguous", msg,
                            suggestion="Assigned to the nearest/aligned feature — verify on the map.",
                            target_layer=best.target, feature_id=best.fid, source=src_ref, location=(tx, ty),
                            related=[alt.fid])
            best.issue_ids.append(it.id)
            best.remarks.append(f"uncertain label '{t.text}'")
        best.labels.append(t.text)
        for cr, value, _ in hits:
            if best.target not in cr.targets:
                continue
            best.label_values.setdefault(cr.rule.field.upper(), []).append((value, best_s, t.handle, t.text))
        stats["labels_matched"] += 1
        label_rows.append(row)

    # conflicts / duplicates per feature
    for f in features:
        for fld, vals in f.label_values.items():
            distinct = {str(v[0]).upper() for v in vals}
            if len(vals) > 1 and len(distinct) == 1:
                stats["labels_duplicated"] += 1
            if len(distinct) > 1:
                stats["label_conflicts"] += 1
                vals.sort(key=lambda v: v[1])
                chosen = vals[0][0]
                it = issues.add("warning", "ambiguous_label", "label_conflict",
                                f"{f.target} #{f.fid}: conflicting labels for {fld}: {', '.join(sorted(distinct))} — kept '{chosen}'",
                                suggestion="Confirm the correct value; the nearest label was used.",
                                target_layer=f.target, feature_id=f.fid, field=fld, location=f.location(),
                                source=f.source.source_ref())
                f.issue_ids.append(it.id)
                f.remarks.append(f"{fld} conflicting labels ({'/'.join(sorted(distinct))})")
    return {"stats": dict(stats), "unrecognised_samples": unrecognised[:200], "rows": label_rows}
