"""
"All ADM asset templates" — one drawing, several geodatabases.

A drawing often shows several asset types (paving, irrigation, lighting, storm water ...). The composite schema lets
one job map every CAD layer to any feature class of the seven approved templates; at delivery each feature class is
written into ITS OWN template geodatabase, copied unchanged — one geodatabase per template that received features
(five feature classes from five templates -> five geodatabases). No structure is merged, renamed or added.

Inside the job only, coded-value domains that two templates define differently under the same name are told apart
as "<SCHEMA>__<domain>" for validation; the delivered geodatabases keep the template's own names.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, field
from typing import Dict, List

from .models import NetworkRules, SchemaDefinition, ValidationRules

COMPOSITE_ID = "adm_all"


def is_composite(schema: SchemaDefinition) -> bool:
    return bool(getattr(schema, "parts", None))


def part_of(schema: SchemaDefinition, layer_name: str) -> SchemaDefinition:
    """The template schema a feature class belongs to (the schema itself when it is not composite)."""
    if not is_composite(schema):
        return schema
    ly = schema.get_layer(layer_name)
    return schema.parts[ly.template_id] if ly is not None and ly.template_id in schema.parts else schema


def build_composite(parts: List[SchemaDefinition]) -> SchemaDefinition:
    parts = sorted(parts, key=lambda s: s.schema_id)
    domains: Dict[str, object] = {}
    renames: Dict[str, Dict[str, str]] = {}
    for p in parts:
        code = p.schema_id.split("_")[1].upper() if "_" in p.schema_id else p.schema_id.upper()
        renames[p.schema_id] = {}
        for d in p.domains:
            have = domains.get(d.name)
            if have is None:
                domains[d.name] = copy.deepcopy(d)
            elif asdict(have) != asdict(d):
                nd = copy.deepcopy(d)
                nd.name = f"{code}__{d.name}"
                domains[nd.name] = nd
                renames[p.schema_id][d.name] = nd.name
    layers, fds, label_rules, layer_rules, topo, rels = [], {}, [], [], [], []
    seen_rules = set()
    unique: Dict[str, List[str]] = {}
    for p in parts:
        rn = renames[p.schema_id]
        for ly in p.layers:
            c = copy.deepcopy(ly)
            c.template_id = p.schema_id
            for fd in c.fields:
                if fd.domain in rn:
                    fd.domain = rn[fd.domain]
            for stp in c.subtypes:
                stp.field_domains = {k: rn.get(v, v) for k, v in stp.field_domains.items()}
            layers.append(c)
        for f in p.feature_datasets:
            fds.setdefault(f.name, copy.deepcopy(f))
        for kind, rules, dest in (("label", p.label_rules, label_rules), ("layer", p.layer_rules, layer_rules)):
            for r in rules:
                key = (kind, r.field, r.pattern, tuple(r.targets))
                if key not in seen_rules:
                    seen_rules.add(key)
                    dest.append(copy.deepcopy(r))
        topo += copy.deepcopy(p.topology_rules)
        rels += copy.deepcopy(p.relationships)
        unique.update(p.validation.unique_fields)
    nets = [p.network for p in parts if p.network.enabled]
    network = copy.deepcopy(next((n for n in nets if n.flow == "gravity"), nets[0])) if nets else NetworkRules()
    for n in nets:      # every template's network classes are checked for connectivity
        network.edges = list(dict.fromkeys(network.edges + n.edges))
        network.junctions = list(dict.fromkeys(network.junctions + n.junctions))
    base = parts[0]
    val = copy.deepcopy(base.validation)
    val.unique_fields = unique
    s = SchemaDefinition(
        schema_id=COMPOSITE_ID,
        display_name="All ADM asset templates — one geodatabase per template",
        version=" + ".join(sorted({str(p.version).split(" ")[0] for p in parts})),
        description=("Map a drawing that shows several asset types. Every feature class is delivered in its own "
                     "original template geodatabase: " + ", ".join(p.display_name for p in parts) + "."),
        status="approved", discipline="multi-asset", spatial_reference=copy.deepcopy(base.spatial_reference),
        xy_tolerance=min(p.xy_tolerance for p in parts), feature_datasets=list(fds.values()), layers=layers,
        domains=list(domains.values()), label_rules=label_rules, layer_rules=layer_rules, network=network,
        relationships=rels, topology_rules=topo, validation=val, repair=copy.deepcopy(base.repair),
        label_search_radius=max(p.label_search_radius for p in parts), source_file=base.source_file,
        source="composite", source_template="")
    s.parts = {p.schema_id: p for p in parts}
    return s


_ = (field, ValidationRules)
