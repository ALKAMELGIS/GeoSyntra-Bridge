"""Neutral, schema-agnostic CAD entity model produced by the CAD readers."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

Coord = Tuple[float, float, float]

# geometry kinds an entity can provide
POINT, LINE, POLYGON, TEXT = "point", "line", "polygon", "text"
ARTIFACT = "artifact"            # drawing artefact (hatch outline, fill duplicate) — never a feature
FEATURE_KINDS = (POINT, LINE, POLYGON)


@dataclass
class CadEntity:
    """One CAD entity, already in WCS coordinates.

    kind      : point | line | polygon | text
    coords    : point -> [pt]; line -> vertices; polygon -> exterior ring (closed)
    holes     : polygon interior rings (HATCH islands)
    center    : representative point (insert point / circle centre / centroid)
    """
    handle: str
    layer: str
    dxftype: str
    kind: str
    coords: List[Coord] = field(default_factory=list)
    holes: List[List[Coord]] = field(default_factory=list)
    closed: bool = False
    center: Optional[Coord] = None
    radius: Optional[float] = None
    text: str = ""
    text_height: float = 0.0
    rotation: float = 0.0                 # degrees, text / insert rotation
    block_name: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    color: Optional[int] = None
    linetype: str = ""
    elevation: Optional[float] = None
    leader_point: Optional[Coord] = None  # MLEADER/LEADER arrow head (what the label points at)
    pattern: str = ""                     # HATCH pattern name
    space: str = "model"                  # model | layout name
    lineweight: Optional[int] = None      # 1/100 mm, resolved (BYLAYER -> layer)
    group: str = ""                       # mapping group key (layer::kind[@style]) assigned after cleaning
    artifact: Optional[str] = None        # reason when kind == artifact
    issues: List[str] = field(default_factory=list)

    # ---- derived properties --------------------------------------------
    def rep_point(self) -> Optional[Tuple[float, float]]:
        if self.center is not None:
            return (self.center[0], self.center[1])
        if not self.coords:
            return None
        if self.kind == LINE and len(self.coords) >= 2:
            return _line_midpoint(self.coords)
        xs = [c[0] for c in self.coords]
        ys = [c[1] for c in self.coords]
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    def length(self) -> float:
        pts = self.coords
        total = 0.0
        for i in range(len(pts) - 1):
            total += math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        return total

    def bbox(self) -> Optional[Tuple[float, float, float, float]]:
        pts = self.coords or ([self.center] if self.center else [])
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def provides(self) -> List[str]:
        """Geometry kinds this entity can be converted to without guessing."""
        if self.kind == TEXT:
            return []
        if self.dxftype == "CIRCLE":
            return [POINT, POLYGON]
        if self.kind == POLYGON:
            return [POLYGON, LINE]
        return [self.kind]

    def source_ref(self) -> Dict[str, object]:
        rp = self.rep_point()
        return {"handle": self.handle, "layer": self.layer, "type": self.dxftype,
                "block": self.block_name or None, "text": self.text or None,
                "attributes": self.attributes or None,
                "x": rp[0] if rp else None, "y": rp[1] if rp else None}


def _line_midpoint(pts: List[Coord]) -> Tuple[float, float]:
    segs = [math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1)]
    total = sum(segs)
    if total == 0:
        return (pts[0][0], pts[0][1])
    half = total / 2
    acc = 0.0
    for i, s in enumerate(segs):
        if acc + s >= half and s > 0:
            t = (half - acc) / s
            return (pts[i][0] + t * (pts[i + 1][0] - pts[i][0]), pts[i][1] + t * (pts[i + 1][1] - pts[i][1]))
        acc += s
    return (pts[-1][0], pts[-1][1])


@dataclass
class CadLayerInfo:
    name: str
    color: Optional[int] = None
    linetype: str = ""
    is_off: bool = False
    is_frozen: bool = False
    lineweight: Optional[int] = None
    is_xref: bool = False


@dataclass
class CadDocument:
    source_name: str
    dxf_version: str = ""
    insunits: int = 0
    units_name: str = "unitless"
    entities: List[CadEntity] = field(default_factory=list)
    layers: Dict[str, CadLayerInfo] = field(default_factory=dict)
    skipped_types: Dict[str, int] = field(default_factory=dict)
    read_warnings: List[str] = field(default_factory=list)
    geodata_epsg: Optional[int] = None
    prj_epsg: Optional[int] = None          # from a .prj next to the drawing (or in the uploaded zip)
    converted_from_dwg: bool = False
    paper: List[CadEntity] = field(default_factory=list)       # layout (paper space) content: legends, title blocks
    artifacts: Dict[str, int] = field(default_factory=dict)    # reason -> count
    key_info: Dict[str, object] = field(default_factory=dict)  # where the drawing key was found (cascade result)
    keys: Optional[list] = None                                 # KeyEntry list (cad.keys.read_keys)
    layer_roles: Dict[str, Dict[str, str]] = field(default_factory=dict)  # layer -> {role, reason}
    cleaning: List[Dict[str, object]] = field(default_factory=list)       # CAD clean-up actions (logged)
    styles: Dict[str, str] = field(default_factory=dict)                  # group -> style label
    cleanup_proposals: List[Dict[str, object]] = field(default_factory=list)  # advanced clean-up, awaiting review
    recovered_types: Dict[str, int] = field(default_factory=dict)  # custom / proxy objects read through their drawn geometry

    @property
    def texts(self) -> List[CadEntity]:
        return [e for e in self.entities if e.kind == TEXT]

    @property
    def features(self) -> List[CadEntity]:
        return [e for e in self.entities if e.kind in FEATURE_KINDS]

    def extent(self) -> Optional[Tuple[float, float, float, float]]:
        boxes = [e.bbox() for e in self.entities]
        boxes = [b for b in boxes if b]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))
