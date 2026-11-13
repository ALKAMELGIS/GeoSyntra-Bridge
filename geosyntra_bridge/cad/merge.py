"""Several sheets of one project (Softscape, Hardscape, Irrigation …) read as one drawing."""
from __future__ import annotations

from typing import List

from .model import CadDocument


def merge_docs(docs: List[CadDocument], names: List[str]) -> CadDocument:
    if len(docs) == 1:
        return docs[0]
    base = docs[0]
    base.source_name = " + ".join(names)
    for i, d in enumerate(docs[1:], start=1):
        tag = f"S{i}:"
        for e in d.entities:
            e.handle = tag + str(e.handle)
        for e in d.paper:
            e.handle = tag + str(e.handle)
        base.entities.extend(d.entities)
        base.paper.extend(d.paper)
        for k, v in d.layers.items():
            base.layers.setdefault(k, v)
        for k, v in d.skipped_types.items():
            base.skipped_types[k] = base.skipped_types.get(k, 0) + v
        for k, v in (getattr(d, "recovered_types", None) or {}).items():
            base.recovered_types[k] = base.recovered_types.get(k, 0) + v
        for k, v in d.artifacts.items():
            base.artifacts[k] = base.artifacts.get(k, 0) + v
        base.read_warnings.extend(f"{names[i]}: {w}" for w in d.read_warnings)
        base.cleaning.extend(d.cleaning)
        base.styles.update({k: v for k, v in d.styles.items() if k not in base.styles})
        for k, v in d.layer_roles.items():
            base.layer_roles.setdefault(k, v)
        if d.keys:
            base.keys = list(base.keys or []) + list(d.keys)
        base.converted_from_dwg = base.converted_from_dwg or d.converted_from_dwg
        base.geodata_epsg = base.geodata_epsg or d.geodata_epsg
        if not base.insunits and d.insunits:
            base.insunits, base.units_name = d.insunits, d.units_name
    return base
