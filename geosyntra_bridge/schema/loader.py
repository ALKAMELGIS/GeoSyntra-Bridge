"""Load / lint / register schema definitions (JSON files, format v1 or v2)."""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import fields as dc_fields, is_dataclass
from typing import Any, Dict, List, Optional, Tuple, Type

from .models import (
    SchemaDefinition, LayerDef, FieldDef, DomainDef, ClassificationHints, SubtypeDef,
    LabelRule, NetworkRules, RelationshipDef, ValidationRules, RepairRules,
    SpatialReferenceDef, FeatureDatasetDef, TopologyRuleDef, GEOMETRY_TYPES, FIELD_TYPES,
)


class SchemaError(ValueError):
    pass


def _build(cls: Type, d: Optional[dict]):
    """Construct a flat dataclass from a dict, ignoring unknown keys."""
    if d is None:
        return cls()
    names = {f.name for f in dc_fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


def _layer_from_dict(d: dict) -> LayerDef:
    fields = [_build(FieldDef, f) for f in d.get("fields", [])]
    for f in fields:
        f.field_type = (f.field_type or "TEXT").upper()
    hints = _build(ClassificationHints, d.get("classification_hints"))
    # v2 convenience: cad_mapping.{layer_patterns,block_patterns} merge into hints
    cm = d.get("cad_mapping") or {}
    hints.layer_name_patterns = list(hints.layer_name_patterns) + list(cm.get("layer_patterns", []))
    hints.block_name_patterns = list(hints.block_name_patterns) + list(cm.get("block_patterns", []))
    hints.text_keywords = list(hints.text_keywords) + list(cm.get("keywords", []))
    if not hints.geometry_types:
        hints.geometry_types = [d["geometry_type"]]
    layer = LayerDef(
        name=d["name"],
        geometry_type=d["geometry_type"],
        category=d.get("category", "other"),
        alias=d.get("alias"),
        feature_dataset=d.get("feature_dataset"),
        description=d.get("description", ""),
        fields=fields,
        classification_hints=hints,
        subtype_field=d.get("subtype_field"),
        subtypes=[_build(SubtypeDef, s) for s in d.get("subtypes", [])],
        default_subtype=d.get("default_subtype"),
        id_field=d.get("id_field"),
        attribute_aliases=d.get("attribute_aliases", {}),
        network_role=d.get("network_role"),
        computed_fields=d.get("computed_fields", {}),
        has_z=bool(d.get("has_z", False)),
    )
    return layer


def schema_from_dict(d: dict, source_file: str = "") -> SchemaDefinition:
    if "schema_id" not in d:
        raise SchemaError("schema JSON is missing 'schema_id'")
    s = SchemaDefinition(
        schema_id=d["schema_id"],
        display_name=d.get("display_name", d["schema_id"]),
        version=str(d.get("version", "1.0")),
        description=d.get("description", ""),
        status=d.get("status", "approved"),
        discipline=d.get("discipline", ""),
        spatial_reference=_build(SpatialReferenceDef, d.get("spatial_reference")),
        xy_tolerance=float(d.get("xy_tolerance", 0.001)),
        feature_datasets=[_build(FeatureDatasetDef, x) for x in d.get("feature_datasets", [])],
        layers=[_layer_from_dict(l) for l in d.get("layers", [])],
        domains=[_build(DomainDef, x) for x in d.get("domains", [])],
        label_rules=[_build(LabelRule, x) for x in d.get("label_rules", [])],
        layer_rules=[_build(LabelRule, x) for x in d.get("layer_rules", [])],
        network=_build(NetworkRules, d.get("network")),
        relationships=[_build(RelationshipDef, x) for x in d.get("relationships", [])],
        topology_rules=[_build(TopologyRuleDef, x) for x in d.get("topology_rules", [])],
        validation=_build(ValidationRules, d.get("validation")),
        repair=_build(RepairRules, d.get("repair")),
        label_search_radius=float(d.get("label_search_radius", 5.0)),
        source_file=source_file,
        source=d.get("source", ""),
        source_template=d.get("source_template", ""),
    )
    _infer_domain_types(s)
    return s


def _infer_domain_types(s: SchemaDefinition):
    usage: Dict[str, str] = {}
    for l in s.layers:
        for f in l.fields:
            if f.domain and f.domain not in usage:
                usage[f.domain] = f.field_type
    for d in s.domains:
        if d.field_type:
            d.field_type = d.field_type.upper()
            continue
        if d.name in usage:
            d.field_type = usage[d.name]
        elif d.domain_type == "range":
            d.field_type = "DOUBLE"
        else:
            d.field_type = "TEXT"


def load_schema(path: str) -> SchemaDefinition:
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    s = schema_from_dict(d, source_file=path)
    try:
        from ..knowledge.dictionary import apply_dictionary
        apply_dictionary(s)
    except Exception:  # noqa: BLE001 — the dictionary enriches, it never prevents a schema from loading
        pass
    try:
        from ..knowledge.annex import apply_annex
        apply_annex(s)
    except Exception:  # noqa: BLE001 — same for the standard's annex
        pass
    return s


def lint_schema(s: SchemaDefinition) -> List[str]:
    """Return a list of structural problems (empty == valid)."""
    problems: List[str] = []
    dom_names = {d.name for d in s.domains}
    fds = {f.name for f in s.feature_datasets}
    layer_names = set()
    for l in s.layers:
        if l.name in layer_names:
            problems.append(f"duplicate feature class '{l.name}'")
        layer_names.add(l.name)
        if l.geometry_type not in GEOMETRY_TYPES:
            problems.append(f"{l.name}: invalid geometry_type '{l.geometry_type}'")
        if l.feature_dataset and l.feature_dataset not in fds:
            problems.append(f"{l.name}: feature_dataset '{l.feature_dataset}' is not declared")
        seen = set()
        for f in l.fields:
            if f.name.upper() in seen:
                problems.append(f"{l.name}: duplicate field '{f.name}'")
            seen.add(f.name.upper())
            if f.field_type not in FIELD_TYPES:
                problems.append(f"{l.name}.{f.name}: unknown field_type '{f.field_type}'")
            if f.domain and f.domain not in dom_names:
                problems.append(f"{l.name}.{f.name}: domain '{f.domain}' not defined")
        if l.subtype_field and l.get_field(l.subtype_field) is None:
            problems.append(f"{l.name}: subtype_field '{l.subtype_field}' is not a field")
        for st in l.subtypes:
            for fname, dname in st.field_domains.items():
                if dname not in dom_names:
                    problems.append(f"{l.name} subtype {st.code}: domain '{dname}' not defined")
            for p in st.cad_patterns:
                _check_regex(p, f"{l.name} subtype {st.code}", problems)
        for p in l.classification_hints.layer_name_patterns + l.classification_hints.block_name_patterns:
            _check_regex(p, l.name, problems)
    for r in s.label_rules + s.layer_rules:
        _check_regex(r.pattern, f"label rule for {r.field}", problems)
        for t in r.targets:
            if t not in layer_names:
                problems.append(f"label rule {r.field}: target '{t}' is not a feature class")
    n = s.network
    if n.enabled:
        for t in n.edges + n.junctions:
            if t not in layer_names:
                problems.append(f"network: '{t}' is not a feature class")
    for tr in s.topology_rules:
        for t in (tr.origin, tr.destination):
            if t and t not in layer_names:
                problems.append(f"topology rule {tr.rule}: '{t}' is not a feature class")
    for rel in s.relationships:
        for t in (rel.origin, rel.destination):
            if t not in layer_names:
                problems.append(f"relationship {rel.name}: '{t}' is not a feature class")
    return problems


def _check_regex(p: str, where: str, problems: List[str]):
    try:
        re.compile(p)
    except re.error as e:
        problems.append(f"{where}: invalid regex /{p}/ ({e})")


class SchemaRegistry:
    """Caches schema files from a directory, reloading on change."""

    def __init__(self, schemas_dir: str, extra_dirs: Optional[List[str]] = None):
        self.schemas_dir = schemas_dir
        self.extra_dirs = [d for d in (extra_dirs or []) if d]
        self._cache: Dict[str, Tuple[float, SchemaDefinition]] = {}
        self._lock = threading.Lock()

    def _files(self) -> List[str]:
        out: List[str] = []
        for d in [self.schemas_dir] + self.extra_dirs:
            if os.path.isdir(d):
                out += sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json"))
        return out

    def all(self) -> Dict[str, SchemaDefinition]:
        out: Dict[str, SchemaDefinition] = {}
        with self._lock:
            for path in self._files():
                mtime = os.path.getmtime(path)
                cached = self._cache.get(path)
                if cached is None or cached[0] != mtime:
                    try:
                        cached = (mtime, load_schema(path))
                    except Exception:  # noqa: BLE001 — a broken file must not break the others
                        continue
                    self._cache[path] = cached
                out[cached[1].schema_id] = cached[1]
            self._add_composite(out)
        return out

    def _add_composite(self, out: Dict[str, SchemaDefinition]):
        """'All ADM asset templates': built from the approved ADM template schemas, rebuilt when one changes."""
        from .composite import COMPOSITE_ID, build_composite
        parts = [s for sid, s in sorted(out.items()) if sid.startswith("adm_") and s.status == "approved"
                 and s.source == "template_gdb" and sid != COMPOSITE_ID]
        if len(parts) < 2:
            return
        key = tuple(id(p) for p in parts)
        if getattr(self, "_composite_key", None) != key:
            self._composite = build_composite(parts)
            self._composite_key = key
        out[COMPOSITE_ID] = self._composite

    def get(self, schema_id: str) -> SchemaDefinition:
        s = self.all().get(schema_id)
        if s is None:
            raise KeyError(f"schema '{schema_id}' not found")
        return s

    def save(self, d: dict, overwrite: bool = False) -> SchemaDefinition:
        s = schema_from_dict(d)
        problems = lint_schema(s)
        if problems:
            raise SchemaError("; ".join(problems[:20]))
        if not re.fullmatch(r"[A-Za-z0-9_\-]{2,64}", s.schema_id):
            raise SchemaError("schema_id may only contain letters, digits, '_' and '-'")
        path = os.path.join(self.schemas_dir, f"{s.schema_id}.json")
        if os.path.exists(path) and not overwrite:
            raise SchemaError(f"schema '{s.schema_id}' already exists (approved templates are never overwritten)")
        os.makedirs(self.schemas_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return self.get(s.schema_id)


# --- backwards compatibility with the v1 API --------------------------------
def load_all_schemas(schemas_dir: str) -> Dict[str, SchemaDefinition]:
    return SchemaRegistry(schemas_dir).all()
