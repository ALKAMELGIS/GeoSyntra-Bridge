"""
The single schema-driven conversion engine.

    CadDocument + SchemaDefinition + mapping  ->  ConversionResult

Every behaviour (target classes, fields, domains, subtypes, label rules,
network rules, repair/validation rules, CRS) comes from the schema JSON
and the user-edited mapping; nothing here is specific to one schema.
"""
from __future__ import annotations

import copy
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from shapely.geometry import mapping as shp_mapping
import shapely

from ..cad.discovery import group_key
from ..cad.model import CadDocument, CadEntity, FEATURE_KINDS, TEXT
from ..cad.reader import unit_scale_to_meters
from ..geometry.convert import CoordinatePipeline, convert_entity
from ..geometry.crs import suggest_source_crs, transformer, validate_epsg
from ..geometry.network import analyse_network
from ..labels.engine import associate_labels
from ..mapping.automap import auto_map, default_field_mapping
from ..schema.models import RepairRules, SchemaDefinition, SYSTEM_FIELDS, ValidationRules
from ..knowledge.plants import fill_names
from ..validation.attributes import apply_computed, check_missing, coerce_and_validate, fill_non_nullable, populate
from ..validation.topology import check_topology
from ..validation.checks import check_crs_extent, check_duplicate_geometries, check_unique_ids
from .model import CATEGORIES, IssueLog, TargetFeature

Progress = Optional[Callable[[float, str], None]]


# ------------------------------------------------------------- mapping setup
def default_options(schema: SchemaDefinition) -> Dict[str, Any]:
    return {
        "repair": copy.deepcopy(schema.repair.__dict__),
        "label_search_radius": schema.label_search_radius,
        "snap_tolerance": schema.network.snap_tolerance if schema.network.enabled else None,
        "duplicate_policy": "flag",          # flag | skip
        "non_nullable_policy": "placeholder",  # placeholder | reject
        "write_failed_report_only": True,
        "include_empty_classes": True,       # recreate the complete schema structure
    }


def build_initial_mapping(doc: CadDocument, schema: SchemaDefinition, rules_dir: Optional[str],
                          discovery: Dict[str, Any], reference: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from ..cad.keys import read_keys
    keys = read_keys(doc)
    layers = auto_map(doc, schema, rules_dir, keys=keys, reference=reference)
    crs = detect_for_doc(doc, schema, discovery)
    mapping = {
        "schema_id": schema.schema_id,
        "source_crs": crs["suggested"],
        "crs_suggestion": crs,
        "unit_scale": float(crs.get("suggested_scale") or 1.0),
        "drawing_units": doc.units_name,
        "drawing_units_scale_hint": unit_scale_to_meters(doc.insunits),
        "layers": layers,
        "keys": [k.to_dict() for k in keys],
        "label_layers": _default_label_layers(doc),   # annotation + asset layers (never base map / sheet)
        "fields": {},
        "options": default_options(schema),
    }
    refresh_field_mapping(mapping, schema, discovery)
    return mapping


def detect_for_doc(doc: CadDocument, schema: SchemaDefinition, discovery: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Automatic source-CRS and unit detection for a read drawing (see geometry.crs.detect_source_crs)."""
    from ..geometry.crs import detect_source_crs, robust_extent

    def pts():
        ents = [e for e in doc.entities if e.kind != "artifact"]
        step = max(1, len(ents) // 20000)
        for e in ents[::step]:
            cs = e.coords or ([e.center] if e.center else [])
            for c in cs[::max(1, len(cs) // 8)]:
                yield c[0], c[1]
    ext = robust_extent(pts()) or (discovery or {}).get("extent")
    import re
    lab = re.compile(r"\b(?:E|EAST(?:ING)?|X)\s*[=:]?\s*(\d{5,7}(?:\.\d+)?)[\s,;]+(?:N|NORTH(?:ING)?|Y)\s*[=:]?\s*(\d{6,8}(?:\.\d+)?)", re.I)
    labels = []
    for e in doc.entities:
        if e.text and len(labels) < 500:
            m = lab.search(e.text.replace(",", ""))
            if m:
                labels.append((float(m.group(1)), float(m.group(2))))
    return detect_source_crs(ext, doc.geodata_epsg, getattr(doc, "prj_epsg", None), schema.spatial_reference.epsg,
                             unit_scale_to_meters(doc.insunits), full_extent=(discovery or {}).get("extent"),
                             units_name=doc.units_name, labels=labels)


def _default_label_layers(doc: CadDocument):
    from ..cad.roles import label_layers
    if not doc.layer_roles:
        return None
    return label_layers(doc)


def refresh_field_mapping(mapping: Dict[str, Any], schema: SchemaDefinition, discovery: Dict[str, Any]):
    """Ensure every mapped target class has a field mapping (keeps user edits)."""
    tags_by_target: Dict[str, set] = defaultdict(set)
    gmap = {g["key"]: g for g in discovery.get("groups", [])}
    for key, m in mapping["layers"].items():
        if m.get("target") and m.get("role", "feature") == "feature":
            tags_by_target[m["target"]] |= set(gmap.get(key, {}).get("attribute_tags", []))
    for target, tags in tags_by_target.items():
        proposed = default_field_mapping(schema, target, sorted(tags))
        current = mapping["fields"].setdefault(target, {})
        for fname, spec in proposed.items():
            if fname not in current or (current[fname].get("source") == "none" and spec.get("source") != "none"
                                        and not current[fname].get("user")):
                current[fname] = spec


# ------------------------------------------------------------- result model
@dataclass
class ConversionResult:
    schema: SchemaDefinition
    features: List[TargetFeature]
    issues: IssueLog
    label_report: Dict[str, Any]
    network: Dict[str, Any]
    target_epsg: Optional[int]
    source_epsg: Optional[int]
    mapping_rows: List[Dict[str, Any]] = field(default_factory=list)
    unmapped_entities: int = 0
    elevation: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> Dict[str, Any]:
        written = [f for f in self.features if not f.failed and f.write]
        by_target = Counter(f.target for f in written)
        cat = Counter(i.category for i in self.issues.items)
        sev = Counter(i.severity for i in self.issues.items)
        feats_missing = len({i.feature_id for i in self.issues.items if i.category == "missing_attribute"})
        return {
            "features_total": len(self.features),
            "features_imported": len(written),
            "features_failed": sum(1 for f in self.features if f.failed),
            "features_skipped_duplicates": sum(1 for f in self.features if not f.failed and not f.write),
            "features_with_missing_attributes": feats_missing,
            "duplicates": cat.get("duplicate", 0),
            "unmatched_labels": cat.get("unmatched_label", 0),
            "ambiguous_labels": cat.get("ambiguous_label", 0),
            "uncertain_mappings": cat.get("uncertain_mapping", 0),
            "unmapped_cad_entities": self.unmapped_entities,
            "geometry_topology_errors": cat.get("geometry", 0) + cat.get("topology", 0) + cat.get("failed", 0),
            "network_issues": cat.get("network", 0),
            "domain_errors": cat.get("domain", 0),
            "contractor_mandatory_missing": self.network.get("contractor_mandatory_missing", {}),
            "elevation": {k: self.elevation.get(k) for k in ("status", "by_status", "vertices", "z_range",
                                                             "missing_features", "vertical") if k in self.elevation},
            "issues_by_category": {k: cat.get(k, 0) for k in CATEGORIES},
            "issues_by_severity": {k: sev.get(k, 0) for k in ("error", "warning", "info")},
            "features_by_class": dict(sorted(by_target.items())),
            "labels": self.label_report.get("stats", {}),
            "network": self.network,
            "source_epsg": self.source_epsg,
            "target_epsg": self.target_epsg,
        }


# ------------------------------------------------------------- engine
def run_conversion(doc: CadDocument, schema: SchemaDefinition, mapping: Dict[str, Any],
                   progress: Progress = None, reference: Optional[Dict[str, Any]] = None,
                   dem: Any = None) -> ConversionResult:
    def p(frac, msg):
        if progress:
            progress(frac, msg)

    opts = {**default_options(schema), **(mapping.get("options") or {})}
    repair = RepairRules(**{**schema.repair.__dict__, **(opts.get("repair") or {})})
    val: ValidationRules = schema.validation
    issues = IssueLog()

    target_epsg = schema.spatial_reference.epsg
    src_epsg = mapping.get("source_crs")
    if src_epsg in ("", "same"):
        src_epsg = None
    src_epsg = int(src_epsg) if src_epsg else None
    if src_epsg and not validate_epsg(src_epsg):
        raise ValueError(f"source CRS EPSG:{src_epsg} is not a valid EPSG code")
    if not src_epsg:
        issues.add("warning", "crs", "source_crs_assumed",
                   f"No source coordinate system selected — drawing coordinates are assumed to already be in "
                   f"{'EPSG:' + str(target_epsg) if target_epsg else 'the target system'}",
                   suggestion="select the drawing's coordinate system in the Source step")
    from ..geometry.convert import pipeline_for
    cp = pipeline_for(mapping, target_epsg, src_override=src_epsg or "")

    # ---- 0. CAD clean-up performed before transfer (logged, never silent)
    for act in doc.cleaning:
        cat = "duplicate" if "duplicate" in act["action"].lower() else "geometry"
        issues.add("info", cat, "cad_cleaning",
                   f"CAD clean-up: {act['action']} — {act['count']} entit{'y' if act['count'] == 1 else 'ies'} "
                   f"on {', '.join(f'{k} ({v})' for k, v in list(act['layers'].items())[:4])}",
                   suggestion=act.get("note", ""), source={"handles": act["handles"][:20]})

    # ---- 1. geometry
    p(0.05, "Converting geometries")
    groups: Dict[str, List[CadEntity]] = defaultdict(list)
    for e in doc.entities:
        if e.kind in FEATURE_KINDS:
            groups[e.group or group_key(e.layer, e.kind)].append(e)
    features: List[TargetFeature] = []
    fid = 0
    mapping_rows = []
    unmapped = 0
    total = max(1, sum(len(v) for v in groups.values()))
    done = 0
    for key, ents in sorted(groups.items()):
        m = mapping["layers"].get(key) or {"target": None, "role": "feature", "status": "UNRESOLVED"}
        from ..cad.styles import split_group
        layer_name, kind, style = split_group(key)
        target = schema.get_layer(m["target"]) if m.get("target") else None
        mapping_rows.append({"group": key, "layer": layer_name, "geometry": kind, "style": style, "count": len(ents),
                             "target": m.get("target"), "role": m.get("role", "feature"),
                             "status": m.get("status"), "confidence": m.get("confidence"),
                             "score": m.get("score"), "subtype": m.get("subtype")})
        if m.get("role") == "ignore":
            why = (m.get("evidence") or [""])[0] if m.get("status") == "EXCLUDED" else "excluded by decision"
            issues.add("info", "unmatched_feature", "group_ignored",
                       f"CAD layer '{layer_name}'{(' [' + style + ']') if style else ''} ({kind}, {len(ents)} entities) not transferred — {why}",
                       source={"layer": layer_name, "type": kind})
            done += len(ents)
            continue
        if target is None:
            unmapped += len(ents)
            loc = cp.point(ents[0].rep_point() + (0,)) if ents[0].rep_point() else None
            issues.add("warning", "unmatched_feature", "group_unmapped",
                       f"CAD layer '{layer_name}' ({kind}, {len(ents)} entities) is not mapped to any feature class — not converted",
                       suggestion="assign a target feature class in Layer Mapping or mark the layer as Ignore",
                       source={"layer": layer_name, "type": kind}, location=loc)
            done += len(ents)
            continue
        status = m.get("status", "")
        if status in ("SUGGESTED", "MANUAL"):
            first = ents[0].rep_point()
            issues.add("warning", "uncertain_mapping", "mapping_unconfirmed",
                       f"'{layer_name}' ({kind}) -> {target.name} was auto-suggested with {m.get('confidence', 'LOW')} "
                       f"confidence ({m.get('score', 0)}) and not confirmed",
                       suggestion="confirm or change the target in Layer Mapping", target_layer=target.name,
                       source={"layer": layer_name, "type": kind},
                       location=cp.point(first + (0,)) if first else None)
        conf = float(m.get("score") or (1.0 if status in ("USER", "PROFILE") else 0))
        for e in ents:
            fid += 1
            done += 1
            if done % 2000 == 0:
                p(0.05 + 0.35 * done / total, f"Converting geometries {done:,}/{total:,}")
            gr = convert_entity(e, target.geometry_type, cp, repair, val, m.get("convert"))
            f = TargetFeature(fid=fid, target=target.name, group_key=key, source=e, subtype=m.get("subtype"),
                              mapping_status=status or "USER", confidence=conf)
            if gr.geom is None:
                f.failed, f.failure, f.write = True, gr.failure, False
                rp = e.rep_point()
                try:
                    f.loc = cp.point((rp[0], rp[1], 0)) if rp else None
                except Exception:  # noqa: BLE001
                    f.loc = None
                cat = "failed"
                it = issues.add("error", cat, gr.failure_code or "geometry_failed",
                                f"{target.name}: CAD {e.dxftype} {e.handle} on '{e.layer}' not imported — {gr.failure}",
                                suggestion=_geom_suggestion(gr.failure_code), target_layer=target.name,
                                feature_id=fid, location=f.loc, source=e.source_ref())
                f.issue_ids.append(it.id)
            else:
                f.geom = gr.geom
                f.remarks.extend(gr.notes)
                if any(n.startswith("invalid polygon repaired") for n in gr.notes):
                    it = issues.add("info", "geometry", "geometry_repaired",
                                    f"{target.name} #{fid}: {'; '.join(gr.notes)}",
                                    suggestion="verify the repaired outline", target_layer=target.name,
                                    feature_id=fid, location=f.location(), source=e.source_ref())
                    f.issue_ids.append(it.id)
            features.append(f)

    # ---- 1a'. features added on the map (drawn, or the second part of a split) — "<class>|<new handle>" with added=True
    _edits = mapping.get("feature_edits") or {}
    if any(e.get("added") for e in _edits.values()):
        import dataclasses as _dc
        from shapely import wkt as _wkt
        from ..cad.model import CadEntity
        by_handle = {(f.target, f.source.handle): f for f in features}
        next_fid = max((f.fid for f in features), default=0)
        for k, e in _edits.items():
            if not e.get("added") or e.get("deleted") or not e.get("geometry") or "|" not in k:
                continue
            tname, handle = k.split("|", 1)
            if schema.get_layer(tname) is None:
                continue
            try:
                g = _wkt.loads(e["geometry"])
            except Exception:  # noqa: BLE001
                continue
            base = by_handle.get((tname, e.get("copy_of"))) if e.get("copy_of") else None
            kind = "point" if "Point" in g.geom_type else "line" if "Line" in g.geom_type else "polygon"
            if base is not None:
                src = _dc.replace(base.source, handle=handle)
                gk, sub, how = base.group_key, base.subtype, f"split from CAD {base.source.handle}"
            else:
                c = g.representative_point()
                src = CadEntity(handle=handle, layer=e.get("layer") or "(drawn on the map)", dxftype="DRAWN", kind=kind,
                                center=(c.x, c.y, 0.0))
                gk, sub, how = f"(drawn)::{tname}", None, "drawn on the map"
            next_fid += 1
            nf = TargetFeature(fid=next_fid, target=tname, group_key=gk, source=src, subtype=sub, mapping_status="USER", confidence=1.0)
            nf.geom = g
            nf.remarks.append(how)
            features.append(nf)
            by_handle[(tname, handle)] = nf

    # ---- 1a. geometry edited in the Topology step (target CRS, 2D) and features deleted there
    for f in features:
        e = (mapping.get("feature_edits") or {}).get(f"{f.target}|{f.source.handle}") or {}
        if e.get("deleted"):
            f.write, f.failure = False, "deleted in the Topology step"
            f.remarks.append("deleted in the Topology step")
        elif e.get("geometry"):
            try:
                from shapely import wkt as _wkt
                f.geom = _wkt.loads(e["geometry"])
                f.failed, f.write, f.failure = False, True, None
                f.remarks.append("geometry edited in the Topology step")
            except Exception:  # noqa: BLE001
                pass

    # ---- 1b. elevation (CAD Z / DEM) on the converted geometry, before labels, attributes and validation
    from ..elevation.engine import apply_elevation, options as elev_options
    eopts = elev_options(mapping)
    p(0.42, "Elevation (CAD Z / DEM)")
    elev_report = apply_elevation(features, schema, cp, dem, eopts, issues, target_epsg)
    if not eopts.get("fill_fields", True) or not eopts.get("ground_fields", True):
        for f in features:
            if f.z:
                f.z["no_fields"] = not eopts.get("fill_fields", True)
                f.z["no_ground"] = not eopts.get("ground_fields", True)

    # ---- 2a. values encoded in CAD layer / block names (schema.layer_rules)
    _apply_layer_rules(schema, features)

    # ---- 2. labels
    p(0.45, "Associating labels with features")
    label_layers = mapping.get("label_layers")
    texts = [t for t in doc.texts if label_layers is None or t.layer in set(label_layers)]
    label_report = associate_labels(schema, features, texts, cp, float(opts.get("label_search_radius") or schema.label_search_radius), issues)

    # ---- 3. attributes + validation
    p(0.65, "Populating and validating attributes")
    expected_by_target: Dict[str, List[str]] = {}
    key_attrs = _key_attributes(schema, mapping)
    feature_edits = mapping.get("feature_edits") or {}
    for f in features:
        if f.failed:
            continue
        layer = schema.get_layer(f.target)
        fmap = (mapping.get("fields") or {}).get(f.target) or default_field_mapping(schema, f.target, [])
        expected_by_target.setdefault(f.target, [n for n, s in fmap.items() if s.get("source") in ("label", "block_attr")])
        populate(f, layer, fmap)
        _reference_attributes(f, layer, fmap, reference)
        pid = (mapping.get("project") or {}).get("project_id")
        if pid:       # DMT 3.8 project information -> PROJECTID (NOT NULL, contractor-mandatory) where the drawing gives none
            fd = layer.get_field("PROJECTID")
            if fd is not None and f.attrs.get(fd.name) in (None, ""):
                f.attrs[fd.name] = str(pid)
        for fld, info in key_attrs.get(f.group_key, {}).items():
            spec = fmap.get(fld) or {}
            if f.attrs.get(fld) in (None, "") and not (spec.get("user") and spec.get("source") not in ("none",)):
                f.attrs[fld] = info["value"]
                f.remarks.append(f"{fld} from CAD key")
        for fld, vm in (mapping.get("value_maps") or {}).items():   # drawing text -> domain code (engineer's choice)
            fd = layer.get_field(fld)
            if fd is not None and f.attrs.get(fd.name) not in (None, ""):
                raw = str(f.attrs[fd.name]).strip().upper()
                code = vm.get(raw) if raw in vm else (vm.get(raw[:fd.length].strip()) if fd.length else None)
                if code is not None:
                    f.remarks.append(f"{fd.name} '{f.attrs[fd.name]}' -> '{code}'")
                    f.attrs[fd.name] = code
        fill_names(f, layer, schema)      # element names as written in the drawing (layer / block), Arabic plant names
        edit = feature_edits.get(f"{f.target}|{f.source.handle}")
        if edit:      # values the engineer entered for THIS feature on the map editor — they win over the automatic fill
            for fld, v in (edit.get("fields") or {}).items():
                fd = layer.get_field(fld)
                if fd is not None:
                    f.attrs[fd.name] = v
            f.remarks.append("edited on map")
        coerce_and_validate(f, layer, schema, issues)

    # ---- 3b. geometry twins (municipal requirement: same asset delivered as polygon AND point / centreline)
    twin_rows = []
    primaries = [f for f in features if not f.failed]
    for key, m in sorted(mapping["layers"].items()):
        if m.get("role") == "ignore" or not m.get("target"):
            continue
        for tw in m.get("twins") or []:
            if not tw.get("enabled"):
                continue
            tl = schema.get_layer(tw["target"])
            if tl is None:
                continue
            fmap_t = (mapping.get("fields") or {}).get(tl.name) or default_field_mapping(schema, tl.name, [])
            made = 0
            new_twins = []
            for pf in [f for f in primaries if f.group_key == key]:
                gr = convert_entity(pf.source, tl.geometry_type, cp, repair, val, tw.get("convert"))
                fid += 1
                tf = TargetFeature(fid=fid, target=tl.name, group_key=key, source=pf.source, subtype=None,
                                   mapping_status=pf.mapping_status, confidence=pf.confidence)
                if gr.geom is None:
                    tf.failed, tf.failure, tf.write = True, gr.failure, False
                    it = issues.add("error", "failed", gr.failure_code or "geometry_failed",
                                    f"{tl.name}: twin of {pf.target} #{pf.fid} not derived — {gr.failure}",
                                    target_layer=tl.name, feature_id=fid, source=pf.source.source_ref())
                    tf.issue_ids.append(it.id)
                    features.append(tf)
                    continue
                tf.geom = gr.geom
                tf.labels = list(getattr(pf, "labels", []) or [])
                new_twins.append((tf, pf))
            if new_twins:
                apply_elevation([t for t, _ in new_twins], schema, cp, dem, eopts, issues, target_epsg)
            for tf, pf in new_twins:
                populate(tf, tl, fmap_t)
                names = {fd.name.upper(): fd.name for fd in tl.fields}
                for k_, v_ in pf.attrs.items():
                    n_ = names.get(k_.upper())
                    if n_ and n_.upper() not in SYSTEM_FIELDS and tf.attrs.get(n_) in (None, "") and v_ not in (None, ""):
                        tf.attrs[n_] = v_
                tf.remarks.append(f"derived from {pf.target} #{pf.fid} "
                                  f"({'centre point' if tw.get('convert') == 'centroid' else 'centreline'}) — "
                                  f"asset required in both geometries")
                coerce_and_validate(tf, tl, schema, issues)
                features.append(tf)
                made += 1
            if made:
                twin_rows.append({"group": key, "from": m["target"], "to": tl.name, "count": made})
                issues.add("info", "geometry", "geometry_twin_derived",
                           f"{made} {tl.name} feature(s) derived from {m['target']} ('{key.split('::')[0]}') — the template "
                           f"defines this asset in both geometries", target_layer=tl.name)
    for r in twin_rows:
        mapping_rows.append({"group": r["group"], "layer": r["group"].split("::")[0], "geometry": "derived",
                             "style": f"twin of {r['from']}", "count": r["count"], "target": r["to"], "role": "feature",
                             "status": "DERIVED", "confidence": None, "score": None, "subtype": None})

    # ---- 4. duplicates, network, ids, CRS
    p(0.78, "Checking duplicates")
    if val.check_duplicates:
        check_duplicate_geometries(features, val.duplicate_tolerance, issues, opts.get("duplicate_policy", "flag"))
    p(0.82, "Analysing network connectivity")
    net = analyse_network(schema, features, issues, snap=bool(repair.snap_line_endpoints),
                          tol_override=opts.get("snap_tolerance"))
    for f in features:
        if not f.failed:
            layer = schema.get_layer(f.target)
            apply_computed(f, layer)
            check_missing(f, layer, issues, expected_by_target.get(f.target, []))
    from ..validation.attributes import check_contractor_mandatory
    net["contractor_mandatory_missing"] = check_contractor_mandatory(features, schema, issues)
    check_unique_ids(schema, features, issues)
    if schema.topology_rules:
        p(0.86, "Evaluating topology rules")
        std_topo: Dict[str, Any] = {}
        net["topology"] = check_topology(schema, features, issues, max(schema.xy_tolerance, val.duplicate_tolerance),
                                         standard_advisory=opts.get("standard_topology", True), report=std_topo)
        if std_topo:
            net["topology_standard"] = std_topo
    from ..validation.topology import check_dmt_spatial_rules
    net["dmt_spatial_rules"] = check_dmt_spatial_rules(schema, features, issues, max(schema.xy_tolerance, 0.001))
    check_crs_extent(features, target_epsg, issues)

    # ---- 5. NOT NULL fields
    nn = fill_non_nullable(features, schema, issues, opts.get("non_nullable_policy", "placeholder"),
                           elevation_nodata=eopts.get("nodata_value") if eopts.get("missing_policy") == "nodata_value" else None,
                           defaults={"REGION": (mapping.get("project") or {}).get("entity")})
    net["not_null"] = nn
    # "missing" findings raised before the NOT NULL fill: a field that was filled automatically is no longer
    # missing — it is an automatic value to review (information), so the real problems stand out
    by_fid = {f.fid: f for f in features}
    filled_cls: Dict[tuple, bool] = {}
    for f in features:
        if f.write and not f.failed:
            for k, v in f.attrs.items():
                key = (f.target, k)
                filled_cls[key] = filled_cls.get(key, True) and v not in (None, "")
    for it in issues.items:
        if it.severity != "warning" or not it.field:
            continue
        if it.code == "required_missing" and it.feature_id in by_fid:
            if by_fid[it.feature_id].attrs.get(it.field) not in (None, ""):
                it.severity = "info"
                it.message += " — filled automatically (review)"
        elif it.code == "contractor_mandatory_missing" and filled_cls.get((it.target_layer, it.field)):
            it.severity = "info"
            it.message += " — filled automatically (review)"
        elif it.code == "not_null_placeholder":
            it.severity = "info"

    # ---- 6. system fields
    p(0.9, "Finalising attributes")
    for f in features:
        layer = schema.get_layer(f.target)
        names = {fd.name.upper(): fd for fd in layer.fields}
        sysvals = {"SOURCE_LAYER": f.source.layer, "SOURCE_HANDLE": f.source.handle,
                   "MAPPING_STATUS": f.mapping_status, "CONFIDENCE": round(f.confidence, 3)}
        for k, v in sysvals.items():
            if k in names:
                f.attrs[names[k].name] = v
        if "REMARKS" in names:
            fd = names["REMARKS"]
            rem = "; ".join(dict.fromkeys(f.remarks))
            if rem:
                limit = fd.length or 1000
                f.attrs[fd.name] = rem[:limit]
    p(1.0, "Validation complete")
    return ConversionResult(schema=schema, features=features, issues=issues, label_report=label_report,
                            network=net, target_epsg=target_epsg, source_epsg=src_epsg,
                            mapping_rows=mapping_rows, unmapped_entities=unmapped, elevation=elev_report)


def _reference_attributes(f, layer, fmap, reference):
    """Spatial join: copy values of the matched reference GIS feature into empty fields of the same name."""
    if not reference:
        return
    hit = (reference.get("entities") or {}).get(f.source.handle)
    if not hit or (hit.get("target") and hit["target"] != f.target):
        return
    names = {fd.name.upper(): fd for fd in layer.fields}
    got = []
    for k, v in (hit.get("attrs") or {}).items():
        fd = names.get(str(k).upper())
        if fd is None or fd.name.upper() in ("SOURCE_LAYER", "SOURCE_HANDLE", "MAPPING_STATUS", "CONFIDENCE", "REMARKS"):
            continue
        spec = fmap.get(fd.name) or {}
        if f.attrs.get(fd.name) not in (None, "") or (spec.get("user") and spec.get("source") not in ("none", None)):
            continue
        f.attrs[fd.name] = v
        got.append(fd.name)
    if got:
        f.remarks.append(f"{', '.join(got[:6])} from reference GIS '{hit['ref_layer']}' (spatial join)")


def _key_attributes(schema: SchemaDefinition, mapping: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Attribute values implied by the CAD key of each mapped group, for its *current* target."""
    from collections import Counter as _C
    from ..mapping.semantics import SchemaSemantics, Thesaurus
    sem, th = SchemaSemantics.get(schema), Thesaurus.get()
    keys = mapping.get("keys") or []
    word_df = _C(w for k in keys for w in th.raw_words(k.get("description", "")))
    out: Dict[str, Dict[str, Any]] = {}
    for gk, m in (mapping.get("layers") or {}).items():
        if not m.get("target") or m.get("role") == "ignore":
            continue
        texts = [k["description"] for k in m.get("keys") or []] + [gk.rpartition("::")[0]]
        attrs = sem.attributes_from_text(m["target"], texts, word_df)
        for fld in m.get("attributes_rejected") or []:
            attrs.pop(fld, None)
        out[gk] = attrs
    return out


def _apply_layer_rules(schema: SchemaDefinition, features: List[TargetFeature]):
    import re
    from ..labels.engine import coerce_label_value
    cache: Dict[tuple, list] = {}
    for f in features:
        if f.failed:
            continue
        key = (f.target, f.source.layer, f.source.block_name)
        if key not in cache:
            hits = []
            for r in schema.rules_for(f.target, "layer"):
                for src in (f.source.layer, f.source.block_name):
                    m = re.search(r.pattern, src or "", re.IGNORECASE) if src else None
                    if m:
                        raw = m.groupdict().get("v") or (m.group(1) if m.groups() else m.group(0))
                        if raw:
                            hits.append((r.field.upper(), coerce_label_value(raw.strip(), r)))
                            break
            cache[key] = hits
        for fld, val in cache[key]:
            # score -1: names of the CAD layer are authoritative over nearby text
            f.label_values.setdefault(fld, []).append((val, -1.0, f.source.handle, f"layer '{f.source.layer}'"))


def _geom_suggestion(code: Optional[str]) -> str:
    return {
        "geometry_type_mismatch": "map this CAD layer to a feature class of matching geometry, or enable centroid conversion",
        "zero_length": "delete the zero-length entity in CAD or join it to the adjacent segment",
        "degenerate": "redraw the entity with distinct vertices",
        "unclosed_ring": "close the polyline in CAD (PEDIT > Close) or raise the ring-closure tolerance",
        "invalid_geometry": "fix the self-intersection in CAD or enable 'repair invalid polygons'",
        "sliver": "remove the sliver polygon or lower the minimum area rule",
        "crs_failure": "check the selected source coordinate system",
        "no_coordinates": "the CAD entity has no geometry",
    }.get(code or "", "inspect the source entity in CAD")


# ------------------------------------------------------------- preview output
def preview_geojson(result: ConversionResult, limit: int = 25000) -> Dict[str, Any]:
    """Features + issue locations in WGS84 (or raw coordinates when no CRS)."""
    to84 = transformer(result.target_epsg, 4326) if result.target_epsg else None
    sev_rank = {"error": 3, "warning": 2, "info": 1}
    issue_sev: Dict[int, str] = {}
    for it in result.issues.items:
        if it.feature_id is not None:
            cur = issue_sev.get(it.feature_id)
            if cur is None or sev_rank[it.severity] > sev_rank[cur]:
                issue_sev[it.feature_id] = it.severity

    def tf(g):
        if not to84:
            return g

        def fn(a):
            x, y = to84.transform(a[:, 0], a[:, 1])
            a[:, 0], a[:, 1] = x, y
            return a
        return shapely.transform(g, fn)

    feats = []
    for f in result.features[:limit]:
        props = {"fid": f.fid, "target": f.target, "layer": f.source.layer, "handle": f.source.handle,
                 "status": "failed" if f.failed else ("skipped" if not f.write else "ok"),
                 "severity": issue_sev.get(f.fid)}
        if f.z and f.z.get("mean") is not None:
            props.update({"z_mean": f.z["mean"], "z_min": f.z["min"], "z_max": f.z["max"], "z_status": f.z["status"]})
        elif f.z:
            props["z_status"] = f.z.get("status")
        if f.geom is not None:
            try:
                feats.append({"type": "Feature", "geometry": shp_mapping(tf(f.geom)), "properties": props})
            except Exception:  # noqa: BLE001
                continue
        elif f.loc:
            x, y = to84.transform(*f.loc) if to84 else f.loc
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]}, "properties": props})
    for it in result.issues.items:
        if it.feature_id is None and it.location and it.category == "unmatched_label":
            x, y = to84.transform(*it.location) if to84 else it.location
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]},
                          "properties": {"issue": it.id, "status": "label", "severity": it.severity,
                                         "text": (it.source or {}).get("text")}})
    return {"type": "FeatureCollection", "crs_is_geographic": bool(to84), "truncated": len(result.features) > limit,
            "features": feats}


def issue_locations_wgs84(result: ConversionResult) -> List[Dict[str, Any]]:
    to84 = transformer(result.target_epsg, 4326) if result.target_epsg else None
    out = []
    for it in result.issues.items:
        d = it.to_dict()
        if it.shape is not None:
            try:
                g = it.shape
                if to84:
                    def fn(a):
                        x, y = to84.transform(a[:, 0], a[:, 1])
                        a[:, 0], a[:, 1] = x, y
                        return a
                    g = shapely.transform(g, fn)
                d["shape"] = shp_mapping(g)
            except Exception:  # noqa: BLE001
                d["shape"] = None
        if it.location:
            try:
                x, y = to84.transform(*it.location) if to84 else it.location
                d["lonlat"] = [x, y]
            except Exception:  # noqa: BLE001
                d["lonlat"] = None
        out.append(d)
    return out


def feature_detail(result: ConversionResult, fid: int) -> Optional[Dict[str, Any]]:
    f = next((x for x in result.features if x.fid == fid), None)
    if f is None:
        return None
    attrs = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in f.attrs.items()}
    return {"fid": f.fid, "target": f.target, "subtype": f.subtype, "status": "failed" if f.failed else ("skipped" if not f.write else "ok"),
            "failure": f.failure, "attributes": attrs, "remarks": f.remarks, "labels": f.labels,
            "source": {**f.source.source_ref(), "color": f.source.color, "linetype": f.source.linetype,
                       "vertex_count": len(f.source.coords), "length": round(f.source.length(), 3) if f.source.kind == "line" else None,
                       "kind": f.source.kind, "z": f.source.elevation if f.source.elevation is not None else
                       (f.source.coords[0][2] if f.source.coords and len(f.source.coords[0]) > 2 else None),
                       "rotation": f.source.rotation or None, "lineweight": f.source.lineweight,
                       "area": round(f.geom.area, 3) if f.geom is not None and f.geom.geom_type.endswith("Polygon") else None},
            "label_values": {k: [{"value": (v[0].isoformat() if hasattr(v[0], "isoformat") else v[0]), "text": v[3], "handle": v[2]} for v in vs[:2]]
                             for k, vs in (f.label_values or {}).items() if vs},
            "rejected": {k: str(v) for k, v in (f.rejected or {}).items()},
            "geometry_type": (f.geom.geom_type + (" Z" if f.geom.has_z else "")) if f.geom is not None else None,
            "elevation": {k: v for k, v in (f.z or {}).items() if k not in ("no_fields", "no_ground")},
            "issues": [i.to_dict() for i in result.issues.items if i.id in set(f.issue_ids)]}
