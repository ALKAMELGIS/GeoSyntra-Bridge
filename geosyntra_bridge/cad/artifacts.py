"""
Drawing artefacts — geometry that is part of how a drawing is *drawn*, not an asset.

Learned from real ADM landscape drawings:
  * associative / exploded HATCH boundaries saved as thousands of tiny SPLINE / LINE
    fragments on the hatch layer (e.g. 3,348 segments, mean length 3 cm, all on the
    boundary of the gravel hatches) -> "hatch outline segments"
  * a HATCH fill drawn on top of a closed polyline with the same outline on the same
    layer (the polyline is the asset, the hatch only colours it) -> "hatch fills duplicating outlines"

Such entities get kind = "artifact"; they are counted and reported, never converted
and never silently lost.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

import shapely
from shapely.geometry import LineString, Polygon
from shapely.strtree import STRtree

from .model import ARTIFACT, LINE, POLYGON, CadDocument, CadEntity

TOL = 0.05            # metres: segment must lie within this distance of a hatch boundary
SHORT = 1.0           # segments shorter than this on a hatch boundary are outline fragments
IOU_DUP = 0.97        # hatch vs closed polyline overlap ratio to call the hatch a fill duplicate


def _poly(e: CadEntity):
    try:
        p = Polygon([c[:2] for c in e.coords], [[c[:2] for c in h] for h in e.holes])
        return p if p.is_valid else p.buffer(0)
    except Exception:  # noqa: BLE001
        return None


def classify_artifacts(cad: CadDocument) -> Dict[str, int]:
    by_layer: Dict[str, List[CadEntity]] = defaultdict(list)
    for e in cad.entities:
        by_layer[e.layer].append(e)
    counts: Dict[str, int] = defaultdict(int)
    for layer, ents in by_layer.items():
        hatches = [e for e in ents if e.dxftype == "HATCH" and e.kind == POLYGON and len(e.coords) >= 4]
        if not hatches:
            continue
        lines = [e for e in ents if e.kind == LINE and len(e.coords) >= 2]
        # ---- hatch outline fragments
        if lines:
            bounds = []
            for h in hatches:
                p = _poly(h)
                if p is not None and not p.is_empty:
                    bounds.append(p.boundary)
            fills = [p for p in (_poly(h) for h in hatches) if p is not None and not p.is_empty]
            ftree = STRtree(fills) if fills else None
            if bounds and ftree is not None:
                tree = STRtree(bounds)
                fragmented = len(lines) > 5 * len(hatches)
                for e in lines:
                    g = LineString([c[:2] for c in e.coords])
                    if g.length == 0:
                        continue
                    if g.length < 0.1 and any(fills[int(i)].contains(g) for i in ftree.query(g)):
                        e.kind, e.artifact = ARTIFACT, "hatch interior fragments"
                        counts[e.artifact] += 1
                        continue
                    if g.length >= SHORT and not fragmented:
                        continue
                    near = [bounds[int(i)] for i in tree.query(g.buffer(TOL))]
                    if not near:
                        continue
                    covered = g.intersection(shapely.union_all([b.buffer(TOL) for b in near])).length
                    if covered >= 0.9 * g.length:
                        e.kind, e.artifact = ARTIFACT, "hatch outline segments"
                        counts[e.artifact] += 1
        # ---- hatch fills duplicating a closed polyline outline
        outlines = [e for e in ents if e.kind == POLYGON and e.dxftype != "HATCH" and len(e.coords) >= 4]
        if outlines:
            polys = [_poly(o) for o in outlines]
            valid = [(o, p) for o, p in zip(outlines, polys) if p is not None and not p.is_empty and p.area > 0]
            if valid:
                tree = STRtree([p for _, p in valid])
                for h in hatches:
                    hp = _poly(h)
                    if hp is None or hp.is_empty or hp.area == 0:
                        continue
                    for i in tree.query(hp):
                        op = valid[int(i)][1]
                        inter = hp.intersection(op).area
                        if inter / max(hp.union(op).area, 1e-12) >= IOU_DUP:
                            h.kind, h.artifact = ARTIFACT, "hatch fills duplicating closed outlines"
                            counts[h.artifact] += 1
                            break
    cad.artifacts = dict(counts)
    return cad.artifacts
