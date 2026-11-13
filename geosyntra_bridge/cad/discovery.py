"""CAD layer discovery: inventory of layers, geometry groups, blocks, labels, attribute tags."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List

from .model import CadDocument, TEXT


def group_key(layer: str, kind: str) -> str:
    return f"{layer}::{kind}"


def discover(doc: CadDocument) -> Dict[str, Any]:
    by_layer: Dict[str, List] = defaultdict(list)
    for e in doc.entities:
        by_layer[e.layer].append(e)

    layers = []
    groups = []
    for name in sorted(by_layer, key=lambda s: s.lower()):
        ents = by_layer[name]
        kinds = Counter(e.kind for e in ents)
        types = Counter(e.dxftype for e in ents)
        blocks = Counter(e.block_name for e in ents if e.block_name)
        texts = [e.text for e in ents if e.kind == TEXT and e.text]
        tags = Counter(t for e in ents for t in e.attributes)
        info = doc.layers.get(name)
        layers.append({
            "name": name,
            "color": info.color if info else None,
            "is_off": info.is_off if info else False,
            "is_frozen": info.is_frozen if info else False,
            "entity_count": len(ents),
            "kinds": dict(kinds),
            "entity_types": dict(types),
            "blocks": [{"name": b, "count": c} for b, c in blocks.most_common(12)],
            "attribute_tags": [t for t, _ in tags.most_common(30)],
            "text_samples": _samples(texts, 8),
            "text_count": len(texts),
        })
        layers[-1]["artifacts"] = sum(1 for e in ents if e.kind == "artifact")
        role = doc.layer_roles.get(name, {})
        layers[-1]["role"] = role.get("role", "asset")
        layers[-1]["role_reason"] = role.get("reason", "")
        gkeys = sorted({e.group or group_key(name, e.kind) for e in ents if e.kind in ("point", "line", "polygon")})
        for gk in gkeys:
            ge = [e for e in ents if (e.group or group_key(name, e.kind)) == gk and e.kind in ("point", "line", "polygon")]
            if not ge:
                continue
            kind = ge[0].kind
            lengths = [e.length() for e in ge if kind == "line"]
            gblocks = Counter(e.block_name for e in ge if e.block_name)
            gtags = Counter(t for e in ge for t in e.attributes)
            groups.append({
                "key": gk,
                "layer": name,
                "style": doc.styles.get(gk, ""),
                "role": role.get("role", "asset"),
                "geometry": kind,
                "count": len(ge),
                "entity_types": dict(Counter(e.dxftype for e in ge)),
                "blocks": [{"name": b, "count": c} for b, c in gblocks.most_common(8)],
                "attribute_tags": [t for t, _ in gtags.most_common(30)],
                "avg_length": round(sum(lengths) / len(lengths), 3) if lengths else None,
                "has_circles": any(e.dxftype == "CIRCLE" for e in ge),
            })
    ext = doc.extent()
    return {
        "source_name": doc.source_name,
        "dxf_version": doc.dxf_version,
        "units": doc.units_name,
        "insunits": doc.insunits,
        "converted_from_dwg": doc.converted_from_dwg,
        "entity_count": len(doc.entities),
        "feature_entity_count": len(doc.features),
        "text_count": len(doc.texts),
        "layer_count": len(layers),
        "extent": list(ext) if ext else None,
        "skipped_types": doc.skipped_types,
        "recovered_types": getattr(doc, "recovered_types", None) or {},
        "read_warnings": doc.read_warnings[:50],
        "geodata_epsg": doc.geodata_epsg,
        "artifacts": doc.artifacts,
        "key_info": doc.key_info,
        "cleaning": doc.cleaning,
        "layer_roles": doc.layer_roles,
        "paper_entity_count": len(doc.paper),
        "layers": layers,
        "groups": groups,
    }


def _samples(texts: List[str], n: int) -> List[str]:
    seen, out = set(), []
    for t in texts:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t[:80])
        if len(out) >= n:
            break
    return out
