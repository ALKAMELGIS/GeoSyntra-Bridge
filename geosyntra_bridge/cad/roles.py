"""
Layer roles — engineers first separate what is being delivered from what is only background:

  asset       the design / as-built layers to transfer
  annotation  text / labels / tags carrying attribute information (TEXT, MTEXT, leaders, tag blocks)
  base map    existing / reference background: roads, plots, buildings, survey, contours, xrefs
  sheet       drawing furniture: title block, border, viewports, dimensions, grid, north arrow

Base-map and sheet layers are excluded automatically (a recorded decision, reversible in the UI),
unless the drawing key names them. Their text is not used as attribute labels.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Dict, List

from .model import ARTIFACT, FEATURE_KINDS, TEXT, CadDocument

_SEP = r"(?:^|[\s_\-.|])"
_END = r"(?:$|[\s_\-.|\d])"

SHEET = re.compile(_SEP + r"(defpoints|viewports?|vport|title|ttl|ttlb|titleblock|border|frame|sheet|dims?|dimension|"
                   r"north|scalebar|matchline|match|grid|keyplan|revision|rev|logo|stamp|legend-frame|a-anno-ttlb|"
                   r"x-?frame|cloud|clouds)" + _END, re.I)
BASE = re.compile(_SEP + r"(base|basemap|bm|ex|exist|existing|ext|survey|surv|topo|topographic|contour|contours|"
                  r"spot|levels?|road|roads|rd|carriageway|cway|plot|plots|parcel|parcels|cadastral|cadastre|affection|"
                  r"bldg|building|buildings|structure-ex|ref|reference|background|bg|ortho|image|aerial|"
                  r"sidewalk-ex|lot|lots|zoning|sector|community|district)" + _END, re.I)
# the same list without road words — used when the job delivers the Roads template, where road layers are assets
BASE_NO_ROAD = re.compile(BASE.pattern.replace("road|roads|rd|carriageway|cway|", ""), re.I)
EXISTING = re.compile(_SEP + r"(ex|exist|existing|ext)" + _END, re.I)
ANNOT = re.compile(_SEP + r"(text|txt|label|labels|lbl|anno|annotation|notes?|tags?|callouts?|keynotes?|mtext)" + _END, re.I)


def classify_layer_roles(cad: CadDocument, road_is_asset: bool = False) -> Dict[str, Dict[str, str]]:
    """road_is_asset: the Roads template is delivered, so road layers (not marked existing) are design assets."""
    by_layer: Dict[str, List] = defaultdict(list)
    for e in cad.entities:
        if e.kind != ARTIFACT:
            by_layer[e.layer].append(e)
    keyed = set()
    for k in cad.keys or []:
        keyed |= {lay for lay, sc in k.layers.items() if sc >= 0.45}
    roles: Dict[str, Dict[str, str]] = {}
    for layer, ents in by_layer.items():
        info = cad.layers.get(layer)
        kinds = Counter(e.kind for e in ents)
        n_feat = sum(kinds[k] for k in FEATURE_KINDS)
        n_text = kinds[TEXT]
        if layer in keyed:
            role, why = "asset", "named in the drawing key"
        elif info is not None and info.is_xref:
            role, why = "basemap", "external reference (xref) layer"
        elif SHEET.search(layer):
            role, why = "sheet", "drawing-sheet furniture (title, border, viewport, dimensions, grid…)"
        elif (BASE_NO_ROAD if road_is_asset else BASE).search(layer) or (road_is_asset and BASE.search(layer) and EXISTING.search(layer)):
            role, why = "basemap", "base-map / existing / reference layer name"
        elif n_feat == 0 and n_text > 0:
            role, why = "annotation", "text only — attribute information"
        elif ANNOT.search(layer) and n_text >= n_feat:
            role, why = "annotation", "label / annotation layer"
        elif info is not None and (info.is_off or info.is_frozen):
            role, why = "basemap", "layer is switched off / frozen in the drawing"
        else:
            role, why = "asset", "drawn content"
        roles[layer] = {"role": role, "reason": why}
    cad.layer_roles = roles
    return roles


def label_layers(cad: CadDocument) -> List[str]:
    """Layers whose text is attribute information (annotation + asset layers, never base map / sheet)."""
    return sorted(l for l, r in cad.layer_roles.items() if r["role"] in ("annotation", "asset")
                  and any(e.kind == TEXT and e.layer == l for e in cad.entities))
