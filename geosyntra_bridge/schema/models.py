"""
Schema-definition data model (format v2).

A schema is one of the approved target geodatabase structures (seven ship
with the tool). Every schema is a standalone JSON file in /schemas, so a new
schema is added by dropping a file — the conversion engine never changes.

v2 adds, on top of the original v1 format (which still loads unchanged):
  * spatial_reference / feature_datasets       -> GDB structure + CRS
  * per-field alias, length, nullable, domain
  * subtypes (subtype field, codes, per-subtype defaults/domains, CAD patterns)
  * label_rules       -> regexes that pull attribute values out of CAD text
  * attribute_aliases -> block-attribute tag synonyms per field
  * network rules     -> edges/junctions, snapping, gravity flow direction
  * relationships     -> relationship classes recreated in the output GDB
  * validation + repair rules (all configurable, nothing hard-coded)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


GEOMETRY_TYPES = ("point", "line", "polygon")
FIELD_TYPES = ("TEXT", "INTEGER", "SMALLINTEGER", "DOUBLE", "FLOAT", "DATE", "GUID")

# Bookkeeping fields the converter fills on every feature class it writes.
SYSTEM_FIELDS = ("SOURCE_LAYER", "SOURCE_HANDLE", "MAPPING_STATUS", "CONFIDENCE", "REMARKS")


@dataclass
class DomainDef:
    name: str
    domain_type: str = "coded"            # "coded" | "range"
    field_type: Optional[str] = None      # TEXT | INTEGER | DOUBLE ... (inferred when missing)
    description: str = ""
    coded_values: Dict[str, str] = field(default_factory=dict)  # code -> description
    min_value: Optional[float] = None
    max_value: Optional[float] = None


@dataclass
class FieldDef:
    name: str
    field_type: str = "TEXT"
    alias: Optional[str] = None
    length: Optional[int] = None
    required: bool = False
    nullable: bool = True
    domain: Optional[str] = None
    default: Optional[Any] = None
    # from the DMT data dictionary (runtime enrichment, not stored in the schema file)
    description: str = ""
    responsibility: str = ""              # CONTRACTOR | MUNICIPAL ENGINEER
    contractor_mandatory: bool = False
    standard_mandatory: bool = False      # "Mandatory: Yes" in the annex of the DMT submission standard V7
    allowed_values: List[str] = field(default_factory=list)
    value_default: Optional[Any] = None   # dictionary default: fills empty values, never changes the field definition


@dataclass
class ClassificationHints:
    """Multi-signal evidence used by the auto-mapper (never matched exclusively)."""
    layer_name_patterns: List[str] = field(default_factory=list)
    text_keywords: List[str] = field(default_factory=list)
    block_name_patterns: List[str] = field(default_factory=list)
    geometry_types: List[str] = field(default_factory=list)
    typical_length_range: Optional[List[float]] = None
    value_ranges: Dict[str, List[float]] = field(default_factory=dict)  # field -> [min, max] typical label values
    # how engineers draw it — compared with the other styles on the same CAD layer
    line_weight: Optional[str] = None          # "heavy" (mains, trunk) | "light" (laterals, services)
    linetype_patterns: List[str] = field(default_factory=list)   # e.g. ["continuous"] / ["dash", "hidden", "drip"]
    network_position: Optional[str] = None     # points: "on_network" (valves, manholes) | "off_network" (controllers)


@dataclass
class SubtypeDef:
    code: int
    name: str
    defaults: Dict[str, Any] = field(default_factory=dict)
    field_domains: Dict[str, str] = field(default_factory=dict)
    cad_patterns: List[str] = field(default_factory=list)  # layer/block regexes that imply this subtype


@dataclass
class LabelRule:
    """Extract one attribute from CAD label text.

    `pattern` is a case-insensitive regex with a named group `v`
    (or the first group). `targets` limits the rule to feature classes;
    empty = every class that has `field`.
    """
    field: str
    pattern: str
    targets: List[str] = field(default_factory=list)
    value_type: Optional[str] = None       # int | float | text | upper (coercion hint)
    value_map: Dict[str, str] = field(default_factory=dict)  # raw (upper) -> stored value
    priority: int = 0
    description: str = ""


@dataclass
class LayerDef:
    name: str
    geometry_type: str
    category: str = "other"
    alias: Optional[str] = None
    feature_dataset: Optional[str] = None
    description: str = ""
    fields: List[FieldDef] = field(default_factory=list)
    classification_hints: ClassificationHints = field(default_factory=ClassificationHints)
    subtype_field: Optional[str] = None
    subtypes: List[SubtypeDef] = field(default_factory=list)
    default_subtype: Optional[int] = None
    id_field: Optional[str] = None                  # asset identifier field (uniqueness checks)
    attribute_aliases: Dict[str, List[str]] = field(default_factory=dict)  # field -> block tag synonyms
    network_role: Optional[str] = None              # "edge" | "junction" | None
    computed_fields: Dict[str, str] = field(default_factory=dict)  # field -> arithmetic expression of other fields
    has_z: bool = False                                            # Z-enabled geometry in the template
    legacy_names: List[str] = field(default_factory=list)          # names in ADM / AAM / DRM / municipal data
    dictionary: Optional[str] = None                               # data dictionary code (HL, IR, SW ...)
    template_id: Optional[str] = None                              # composite schema: the template it is delivered in

    def get_field(self, name: str) -> Optional[FieldDef]:
        u = name.upper()
        for f in self.fields:
            if f.name.upper() == u:
                return f
        return None


@dataclass
class NetworkRules:
    enabled: bool = False
    flow: str = "none"                    # "gravity" | "pressure" | "none"
    edges: List[str] = field(default_factory=list)
    junctions: List[str] = field(default_factory=list)
    snap_tolerance: float = 0.05          # metres (target CRS units)
    upstream_level_field: Optional[str] = None
    downstream_level_field: Optional[str] = None
    junction_level_field: Optional[str] = None   # e.g. manhole invert level
    direction_field: Optional[str] = None
    upstream_node_field: Optional[str] = None    # edge field <- id of junction at upstream end
    downstream_node_field: Optional[str] = None  # edge field <- id of junction at downstream end
    require_junction_at_ends: bool = False


@dataclass
class RelationshipDef:
    name: str
    origin: str
    destination: str
    origin_primary_key: str
    origin_foreign_key: str
    cardinality: str = "one_to_many"      # one_to_one | one_to_many | many_to_many
    rel_type: str = "association"         # association | composite
    forward_label: str = ""
    backward_label: str = ""


@dataclass
class TopologyRuleDef:
    rule: str                          # esriTRT* name or short form (area_no_overlap, point_disjoint ...)
    origin: str
    destination: Optional[str] = None


@dataclass
class ValidationRules:
    min_line_length: float = 0.01
    min_polygon_area: float = 0.01
    duplicate_tolerance: float = 0.001
    check_duplicates: bool = True
    unique_fields: Dict[str, List[str]] = field(default_factory=dict)  # layer -> fields


@dataclass
class RepairRules:
    remove_duplicate_vertices: bool = True
    close_near_closed_rings: bool = True
    ring_closure_tolerance: float = 0.01
    make_valid_polygons: bool = True
    snap_line_endpoints: bool = False
    drop_zero_length: bool = False      # False = keep as failed feature in report (never silent)
    arc_segments: int = 32              # arc / circle densification


@dataclass
class SpatialReferenceDef:
    epsg: Optional[int] = None
    name: str = ""
    verified: bool = True               # False when the CRS was assumed, UI shows a warning


@dataclass
class FeatureDatasetDef:
    name: str
    alias: Optional[str] = None


@dataclass
class SchemaDefinition:
    schema_id: str
    display_name: str
    version: str = "1.0"
    description: str = ""
    status: str = "approved"            # approved | draft
    discipline: str = ""
    spatial_reference: SpatialReferenceDef = field(default_factory=SpatialReferenceDef)
    xy_tolerance: float = 0.001
    feature_datasets: List[FeatureDatasetDef] = field(default_factory=list)
    layers: List[LayerDef] = field(default_factory=list)
    domains: List[DomainDef] = field(default_factory=list)
    label_rules: List[LabelRule] = field(default_factory=list)
    layer_rules: List[LabelRule] = field(default_factory=list)   # same format, matched against CAD layer / block names
    network: NetworkRules = field(default_factory=NetworkRules)
    relationships: List[RelationshipDef] = field(default_factory=list)
    topology_rules: List[TopologyRuleDef] = field(default_factory=list)
    validation: ValidationRules = field(default_factory=ValidationRules)
    repair: RepairRules = field(default_factory=RepairRules)
    label_search_radius: float = 5.0    # drawing units (after CRS -> target units)
    source_file: str = ""
    source: str = ""                     # "template_gdb" when imported from an approved template geodatabase
    source_template: str = ""            # template .gdb name; its zip in schemas/templates/ is the output container

    parts: Dict[str, "SchemaDefinition"] = field(default_factory=dict, repr=False)   # composite: template schemas

    def template_path(self) -> Optional[str]:
        """The approved template geodatabase (zip) this schema was imported from, if it is available."""
        import os as _os
        if not self.source_template:
            return None
        base = _os.path.dirname(self.source_file) if self.source_file else ""
        for d in (_os.path.join(base, "templates"), _os.path.join(base, "..", "templates")):
            for n in (self.source_template + ".zip", self.source_template):
                p = _os.path.join(d, n)
                if _os.path.exists(p):
                    return _os.path.abspath(p)
        return None

    def get_layer(self, name: str) -> Optional[LayerDef]:
        for l in self.layers:
            if l.name == name:
                return l
        u = name.upper()
        for l in self.layers:
            if l.name.upper() == u:
                return l
        return None

    def geometry_twins(self) -> Dict[str, List[str]]:
        """Classes that are the same asset in another geometry (BRIDGE polygon / BRIDGE_POINT point,
        HL_TRACK_POLYGON / HL_TRACK_CENTERLINE). Both are municipal requirements and are kept as they are."""
        import re as _re

        def base(n: str) -> str:
            n = _re.sub(r"^(HL|SL|IR|SW|RD|STL|PS|ADR)_", "", n.upper())
            return _re.sub(r"_(POINT|POLYGON|LINE|CENTERLINE|CENTRELINE|AREA|POLY|PT|PNT)$", "", n)
        groups: Dict[tuple, List[LayerDef]] = {}
        for l in self.layers:     # twins live in the same template (composite schemas hold several)
            groups.setdefault((l.template_id, base(l.name)), []).append(l)
        out: Dict[str, List[str]] = {}
        for ls in groups.values():
            if len({l.geometry_type for l in ls}) < 2:
                continue
            for l in ls:
                out[l.name] = [o.name for o in ls if o.geometry_type != l.geometry_type]
        return out

    def get_domain(self, name: Optional[str]) -> Optional[DomainDef]:
        if not name:
            return None
        for d in self.domains:
            if d.name == name:
                return d
        return None

    def rules_for(self, layer_name: str, kind: str = "label") -> List[LabelRule]:
        layer = self.get_layer(layer_name)
        if layer is None:
            return []
        out = []
        for r in (self.label_rules if kind == "label" else self.layer_rules):
            if r.targets and layer_name not in r.targets:
                continue
            if layer.get_field(r.field) is None:
                continue
            out.append(r)
        return sorted(out, key=lambda r: -r.priority)

    def summary(self) -> Dict[str, Any]:
        geo = {"point": 0, "line": 0, "polygon": 0}
        for l in self.layers:
            geo[l.geometry_type] = geo.get(l.geometry_type, 0) + 1
        return {
            "schema_id": self.schema_id,
            "display_name": self.display_name,
            "version": self.version,
            "source_template": self.source_template,
            "parts": sorted(self.parts) if self.parts else [],
            "status": self.status,
            "discipline": self.discipline,
            "description": self.description,
            "spatial_reference": asdict(self.spatial_reference),
            "feature_dataset_count": len(self.feature_datasets),
            "feature_class_count": len(self.layers),
            "domain_count": len(self.domains),
            "subtype_layers": sum(1 for l in self.layers if l.subtypes),
            "label_rule_count": len(self.label_rules),
            "relationship_count": len(self.relationships),
            "topology_rule_count": len(self.topology_rules),
            "network_enabled": self.network.enabled,
            "network_flow": self.network.flow,
            "geometry_breakdown": geo,
        }

    def to_dict(self) -> Dict[str, Any]:
        parts, self.parts = self.parts, {}
        try:
            d = asdict(self)
        finally:
            self.parts = parts
        if parts:
            d["parts"] = sorted(parts)
        return d
