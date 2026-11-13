"""CAD entity -> target geometry (Point / MultiLineString / MultiPolygon) with configurable repair."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.validation import explain_validity, make_valid

from ..cad.model import CadEntity
from ..schema.models import RepairRules, ValidationRules


@dataclass
class GeomResult:
    geom: Optional[object]               # shapely geometry in target CRS, None if failed
    notes: List[str] = field(default_factory=list)      # repairs applied (go to REMARKS)
    failure: Optional[str] = None        # reason when geom is None
    failure_code: Optional[str] = None


class CoordinatePipeline:
    """Drawing coordinates -> CRS: scale (units), optional rotation + shift (georeferencing), then CRS transform."""

    def __init__(self, scale: float = 1.0, transformer=None, offset: Tuple[float, float] = (0.0, 0.0),
                 rotation: float = 0.0):
        self.scale = float(scale or 1.0)
        self.t = transformer
        self.offset = (float(offset[0]), float(offset[1]))
        self.rot = math.radians(float(rotation or 0.0))
        self._c, self._s = math.cos(self.rot), math.sin(self.rot)

    def local(self, pts) -> List[Tuple[float, float]]:
        """Drawing coordinates -> the source CRS (no CRS transform)."""
        k, c, s_, (ox, oy) = self.scale, self._c, self._s, self.offset
        if not self.rot:
            return [(p[0] * k + ox, p[1] * k + oy) for p in pts]
        return [(k * (p[0] * c - p[1] * s_) + ox, k * (p[0] * s_ + p[1] * c) + oy) for p in pts]

    def xy(self, pts) -> List[Tuple[float, float]]:
        loc = self.local(pts)
        if self.t is None:
            return loc
        xs, ys = self.t.transform([p[0] for p in loc], [p[1] for p in loc])
        return list(zip(list(xs), list(ys)))

    def point(self, p) -> Tuple[float, float]:
        return self.xy([p])[0]


def pipeline_for(mapping: dict, dst_epsg=None, src_override=None) -> CoordinatePipeline:
    """The job's coordinate pipeline: units / georeferencing from the mapping, then source CRS -> `dst_epsg`
    (with the chosen datum transformation, if any). `dst_epsg=None` stops at the source CRS."""
    from .crs import transformer_for
    g = mapping.get("georef") or None
    src = src_override if src_override is not None else mapping.get("source_crs")
    src = int(src) if src not in (None, "", "same") else None
    t = transformer_for(src, dst_epsg, mapping.get("transform_op")) if (src and dst_epsg) else None
    if g:
        return CoordinatePipeline(scale=g.get("scale", 1.0), transformer=t, offset=tuple(g.get("offset") or (0, 0)),
                                  rotation=g.get("rotation", 0.0))
    return CoordinatePipeline(scale=float(mapping.get("unit_scale") or 1.0), transformer=t)


def _dedupe(pts: List[Tuple[float, float]], tol: float) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for p in pts:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > tol:
            out.append(p)
    return out


def _finite(pts) -> bool:
    return all(math.isfinite(x) and math.isfinite(y) for x, y in pts)


def convert_entity(e: CadEntity, target_geom: str, cp: CoordinatePipeline, repair: RepairRules,
                   val: ValidationRules, convert_mode: Optional[str] = None) -> GeomResult:
    notes: List[str] = []
    tol = max(val.duplicate_tolerance, 1e-9)

    # ---------------------------------------------------------- POINT
    if target_geom == "point":
        if e.kind == "point" or e.dxftype == "CIRCLE":
            src = e.center or (e.coords[0] if e.coords else None)
            if src is None:
                return GeomResult(None, failure="entity has no coordinates", failure_code="no_coordinates")
            x, y = cp.point(src)
            if e.dxftype == "CIRCLE":
                notes.append("circle centre used as point location")
        elif convert_mode == "centroid" and e.coords:
            pts = cp.xy(e.coords)
            g = Polygon(pts) if e.kind == "polygon" and len(pts) >= 4 else LineString(pts) if len(pts) >= 2 else Point(pts[0])
            c = g.centroid if not g.is_empty else Point(pts[0])
            x, y = c.x, c.y
            notes.append(f"{e.kind} converted to point (centroid)")
        else:
            return GeomResult(None, failure=f"{e.dxftype} ({e.kind}) cannot be stored as a point without a conversion rule",
                              failure_code="geometry_type_mismatch")
        if not (math.isfinite(x) and math.isfinite(y)):
            return GeomResult(None, failure="coordinate transformation produced invalid coordinates", failure_code="crs_failure")
        return GeomResult(Point(x, y), notes)

    if not e.coords:
        return GeomResult(None, failure="entity has no vertices", failure_code="no_coordinates")
    pts = cp.xy(e.coords)
    if not _finite(pts):
        return GeomResult(None, failure="coordinate transformation produced invalid coordinates", failure_code="crs_failure")
    if repair.remove_duplicate_vertices:
        before = len(pts)
        pts = _dedupe(pts, tol)
        if len(pts) < before and before - len(pts) > (1 if e.closed else 0):
            notes.append(f"removed {before - len(pts)} duplicate vertices")

    # ---------------------------------------------------------- LINE
    if target_geom == "line":
        if e.kind == "point":
            return GeomResult(None, failure=f"{e.dxftype} is a point and cannot be stored as a polyline",
                              failure_code="geometry_type_mismatch")
        if len(pts) < 2:
            return GeomResult(None, failure="degenerate polyline (fewer than 2 distinct vertices)", failure_code="degenerate")
        g = LineString(pts)
        if g.length < val.min_line_length:
            return GeomResult(None, failure=f"zero/short length line ({g.length:.4f} < {val.min_line_length})",
                              failure_code="zero_length")
        if e.kind == "polygon" and convert_mode == "centerline":
            cl = polygon_centerline(Polygon(pts))
            if cl is not None and cl.length >= val.min_line_length:
                return GeomResult(MultiLineString([cl]), notes + ["centerline derived from narrow outline"])
            notes.append("centerline could not be derived; outline stored as line")
        elif e.kind == "polygon":
            notes.append("closed polyline stored as line")
        if not g.is_simple:
            notes.append("self-intersecting polyline (kept as drawn)")
        return GeomResult(MultiLineString([g]), notes)

    # ---------------------------------------------------------- POLYGON
    if e.kind == "point":
        return GeomResult(None, failure=f"{e.dxftype} is a point and cannot be stored as a polygon",
                          failure_code="geometry_type_mismatch")
    if e.kind == "line":
        gap = math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) if len(pts) > 2 else float("inf")
        if repair.close_near_closed_rings and gap <= repair.ring_closure_tolerance:
            notes.append(f"open polyline closed (gap {gap:.4f})")
        else:
            return GeomResult(None, failure="open polyline cannot form a polygon (ring not closed)",
                              failure_code="unclosed_ring")
    if len(pts) >= 2 and pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    if len(set(pts)) < 3:
        return GeomResult(None, failure="degenerate polygon (fewer than 3 distinct vertices)", failure_code="degenerate")
    holes = []
    for h in e.holes:
        hp = cp.xy(h)
        if len(set(hp)) >= 3:
            holes.append(hp if hp[0] == hp[-1] else hp + [hp[0]])
    poly = Polygon(pts)
    parts: List[Polygon] = [poly]
    if holes:
        # HATCH islands: holes inside the outer boundary, separate parts otherwise
        inner, outer = [], []
        for h in holes:
            hp = Polygon(h)
            (inner if poly.contains(hp.representative_point()) else outer).append(h)
        poly = Polygon(pts, inner)
        parts = [poly] + [Polygon(h) for h in outer]
    g = MultiPolygon(parts) if len(parts) > 1 else poly
    if not g.is_valid:
        reason = explain_validity(g)
        if repair.make_valid_polygons:
            fixed = make_valid(g)
            polys = [p for p in getattr(fixed, "geoms", [fixed]) if isinstance(p, (Polygon, MultiPolygon))]
            flat: List[Polygon] = []
            for p in polys:
                flat.extend(list(p.geoms) if isinstance(p, MultiPolygon) else [p])
            flat = [p for p in flat if p.area > 0]
            if not flat:
                return GeomResult(None, failure=f"invalid polygon could not be repaired ({reason})", failure_code="invalid_geometry")
            g = MultiPolygon(flat)
            notes.append(f"invalid polygon repaired ({reason})")
        else:
            return GeomResult(None, failure=f"invalid polygon ({reason}); repair disabled", failure_code="invalid_geometry")
    if g.area < val.min_polygon_area:
        return GeomResult(None, failure=f"polygon area {g.area:.4f} below minimum {val.min_polygon_area}", failure_code="sliver")
    if isinstance(g, Polygon):
        g = MultiPolygon([g])
    # Esri rings: exterior clockwise; GDAL normalises orientation on write.
    return GeomResult(g, notes)


def line_bearing_at(geom, x: float, y: float) -> Optional[float]:
    """Bearing (degrees, 0..180) of the line segment nearest to (x, y)."""
    lines = list(getattr(geom, "geoms", [geom]))
    best, best_d = None, float("inf")
    p = Point(x, y)
    for ln in lines:
        if not isinstance(ln, LineString):
            continue
        coords = list(ln.coords)
        for i in range(len(coords) - 1):
            seg = LineString([coords[i], coords[i + 1]])
            d = seg.distance(p)
            if d < best_d:
                best_d = d
                dx, dy = coords[i + 1][0] - coords[i][0], coords[i + 1][1] - coords[i][1]
                best = math.degrees(math.atan2(dy, dx)) % 180.0
    return best


def polygon_centerline(poly: Polygon) -> Optional[LineString]:
    """Approximate medial axis of a narrow polygon (kerbs, edgers): Voronoi skeleton, longest path."""
    import shapely
    from shapely.ops import linemerge
    try:
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area <= 0:
            return None
        width = 2 * poly.area / poly.length
        dense = shapely.segmentize(poly.exterior, max(width / 2, 0.02))
        edges = shapely.voronoi_polygons(shapely.MultiPoint(list(dense.coords)), only_edges=True)
        inner = poly.buffer(-width * 0.05) if width > 0 else poly
        segs = [g for g in getattr(edges, "geoms", [edges]) if inner.contains(g)]
        if not segs:
            return None
        merged = linemerge(segs)
        lines = list(getattr(merged, "geoms", [merged]))
        # graph longest path approximation: take the longest merged chain
        best = max(lines, key=lambda l: l.length)
        return best.simplify(width * 0.25)
    except Exception:  # noqa: BLE001
        return None
