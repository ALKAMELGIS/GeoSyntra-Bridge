"""Shared runtime model for one conversion run."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional, Tuple

from ..cad.model import CadEntity

# Issue categories map 1:1 to the results dashboard tiles
CATEGORIES = {
    "failed": "Failed features",
    "missing_attribute": "Missing attributes",
    "duplicate": "Duplicate records",
    "unmatched_label": "Unmatched labels",
    "ambiguous_label": "Ambiguous / conflicting labels",
    "uncertain_mapping": "Uncertain feature mappings",
    "unmatched_feature": "Unmapped CAD features",
    "geometry": "Geometry errors",
    "topology": "Topology errors",
    "network": "Network connectivity",
    "domain": "Domain / value errors",
    "crs": "Coordinate system",
}


@dataclass
class Issue:
    id: int
    severity: str                 # error | warning | info
    category: str                 # key of CATEGORIES
    code: str
    message: str
    suggestion: str = ""
    target_layer: Optional[str] = None
    feature_id: Optional[int] = None
    field: Optional[str] = None
    source: Optional[Dict[str, Any]] = None       # CAD entity reference
    location: Optional[Tuple[float, float]] = None  # target CRS coordinates
    related: List[int] = dc_field(default_factory=list)  # other feature ids
    shape: Any = None             # error geometry (shapely, target CRS): overlap area, gap, dangle point ...

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "severity": self.severity, "category": self.category, "code": self.code,
                "message": self.message, "suggestion": self.suggestion, "target_layer": self.target_layer,
                "feature_id": self.feature_id, "field": self.field, "source": self.source,
                "location": list(self.location) if self.location else None, "related": self.related}


class IssueLog:
    def __init__(self):
        self.items: List[Issue] = []
        self._ids = itertools.count(1)

    def add(self, severity: str, category: str, code: str, message: str, **kw) -> Issue:
        it = Issue(id=next(self._ids), severity=severity, category=category, code=code, message=message, **kw)
        self.items.append(it)
        return it


@dataclass
class TargetFeature:
    fid: int
    target: str
    group_key: str
    source: CadEntity
    geom: Any = None                       # shapely, target CRS
    attrs: Dict[str, Any] = dc_field(default_factory=dict)
    subtype: Optional[int] = None
    remarks: List[str] = dc_field(default_factory=list)
    failed: bool = False
    failure: Optional[str] = None
    duplicate_of: Optional[int] = None
    mapping_status: str = ""
    confidence: float = 0.0
    label_values: Dict[str, List[Tuple[Any, float, str, str]]] = dc_field(default_factory=dict)  # field -> (value, score, handle, text)
    labels: List[str] = dc_field(default_factory=list)
    issue_ids: List[int] = dc_field(default_factory=list)
    write: bool = True
    loc: Optional[Tuple[float, float]] = None   # target-CRS location used when geometry failed
    objectid: Optional[int] = None              # OBJECTID assigned in the output geodatabase
    rejected: Dict[str, Any] = dc_field(default_factory=dict)  # field -> value refused by domain/type validation
    z: Optional[Dict[str, Any]] = None          # elevation result: status, min/max/mean, start/end, DEM ground ...
    zparts: Optional[List[List[Optional[float]]]] = None   # per-vertex Z while some are still unknown

    def location(self) -> Optional[Tuple[float, float]]:
        if self.geom is None or self.geom.is_empty:
            return self.loc
        p = self.geom.representative_point() if self.geom.geom_type != "Point" else self.geom
        return (p.x, p.y)
