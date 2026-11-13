"""Coordinate system identification and transformation (pyproj)."""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple


@lru_cache(maxsize=256)
def crs_info(epsg: int) -> Dict[str, Any]:
    from pyproj import CRS
    c = CRS.from_epsg(int(epsg))
    aou = c.area_of_use
    return {"epsg": int(epsg), "name": c.name, "is_geographic": c.is_geographic,
            "units": c.axis_info[0].unit_name if c.axis_info else "",
            "area_of_use": [aou.west, aou.south, aou.east, aou.north] if aou else None}


def validate_epsg(epsg: Any) -> Optional[Dict[str, Any]]:
    try:
        return crs_info(int(epsg))
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=64)
def _transformer(src: int, dst: int):
    from pyproj import Transformer
    return Transformer.from_crs(int(src), int(dst), always_xy=True)


@lru_cache(maxsize=32)
def _pipeline_transformer(op: str):
    from pyproj import Transformer
    return Transformer.from_pipeline(op)


def transformer_for(src: Optional[int], dst: Optional[int], op: Optional[str] = None):
    """Like transformer(), with a chosen datum transformation (a PROJ pipeline from transform_options)."""
    if not src or not dst or int(src) == int(dst):
        return None
    if op:
        try:
            return _pipeline_transformer(op)
        except Exception:  # noqa: BLE001 - an unusable pipeline falls back to PROJ's best choice
            pass
    return _transformer(int(src), int(dst))


def transform_options(src: int, dst: int) -> List[Dict[str, Any]]:
    """The datum transformations PROJ knows between two systems, best first (name, accuracy, usable here)."""
    from pyproj.transformer import TransformerGroup
    if int(src) == int(dst):
        return []
    g = TransformerGroup(int(src), int(dst), always_xy=True)
    out = []
    for i, t in enumerate(g.transformers):
        try:
            out.append({"id": t.definition, "name": t.description, "accuracy": t.accuracy if t.accuracy and t.accuracy > 0 else None,
                        "available": True, "best": i == 0})
        except Exception:  # noqa: BLE001
            continue
    for op in g.unavailable_operations[:5]:
        out.append({"id": None, "name": op.name, "accuracy": op.accuracy if op.accuracy and op.accuracy > 0 else None,
                    "available": False, "best": False})
    return out


def transformer(src: Optional[int], dst: Optional[int]):
    """Return a function (xs, ys) -> (xs, ys); identity when src == dst or either is None."""
    if not src or not dst or int(src) == int(dst):
        return None
    return _transformer(int(src), int(dst))


# Common CRSs offered for UAE / GCC drawings; extended by search in the UI.
COMMON_CRS = [32640, 32639, 3997, 4326, 27040, 27039, 20439, 3857]


def suggest_source_crs(extent: Optional[List[float]], geodata_epsg: Optional[int],
                       target_epsg: Optional[int]) -> Dict[str, Any]:
    """Heuristic CRS identification from drawing extent / embedded GEODATA."""
    reasons: List[str] = []
    cands: List[int] = []
    confidence = "none"
    if geodata_epsg:
        cands.append(int(geodata_epsg))
        reasons.append(f"drawing contains GEODATA referencing EPSG:{geodata_epsg}")
        confidence = "high"
    if extent:
        minx, miny, maxx, maxy = extent
        if -180 <= minx <= 180 and -180 <= maxx <= 180 and -90 <= miny <= 90 and -90 <= maxy <= 90 \
                and (maxx - minx) < 20:
            cands.append(4326)
            reasons.append("coordinates are within longitude/latitude ranges")
            confidence = confidence if confidence == "high" else "medium"
        elif 100_000 <= minx and maxx <= 900_000 and 0 <= miny and maxy <= 9_400_000:
            if target_epsg and validate_epsg(target_epsg) and _inside_utm_like(extent, target_epsg):
                cands.append(int(target_epsg))
                reasons.append(f"extent falls inside the valid area of the target CRS EPSG:{target_epsg}")
                confidence = confidence if confidence == "high" else "medium"
            if 2_300_000 <= miny <= 3_000_000:
                cands += [32640, 32639, 3997]
                reasons.append("easting/northing magnitudes match UTM zones 39N/40N (UAE)")
                confidence = confidence if confidence in ("high", "medium") else "low"
        else:
            reasons.append("coordinates look like a local/engineering grid — select the source CRS manually")
    seen, ordered = set(), []
    for c in cands:
        if c not in seen and validate_epsg(c):
            seen.add(c)
            ordered.append(c)
    return {"suggested": ordered[0] if ordered else None, "candidates": [crs_info(c) for c in ordered],
            "confidence": confidence, "reasons": reasons}


def _inside_utm_like(extent, epsg) -> bool:
    try:
        t = transformer(epsg, 4326)
        if t is None:
            return True
        cx, cy = (extent[0] + extent[2]) / 2, (extent[1] + extent[3]) / 2
        lon, lat = t.transform(cx, cy)
        aou = crs_info(epsg)["area_of_use"]
        if not aou:
            return True
        return aou[0] - 1 <= lon <= aou[2] + 1 and aou[1] - 1 <= lat <= aou[3] + 1
    except Exception:  # noqa: BLE001
        return False


def check_in_area_of_use(xs_ys: Tuple[float, float], epsg: int) -> bool:
    """True if a point (in `epsg` coords) lies inside the CRS area of use (1° margin)."""
    info = validate_epsg(epsg)
    if not info or not info["area_of_use"]:
        return True
    t = transformer(epsg, 4326)
    lon, lat = (t.transform(*xs_ys) if t else xs_ys)
    w, s, e, n = info["area_of_use"]
    return w - 1 <= lon <= e + 1 and s - 1 <= lat <= n + 1


# ------------------------------------------------------------------ automatic source-CRS detection
import re as _re

UAE_BOX = (51.0, 22.5, 56.6, 26.3)
GCC_BOX = (34.0, 16.0, 60.5, 32.5)
# Projected systems used for drawings in the UAE and the Gulf (tried in addition to GEODATA / .prj / target).
REGION_POOL = [32640, 32639, 3997, 27040, 27039, 20439, 32638, 32637, 20438, 20437, 2932, 28600, 3857, 4326]
PRIOR = {32640: 8, 3997: 6, 32639: 4, 27040: -2, 27039: -2, 20439: -3}
SCALES = [1.0, 0.001, 0.01, 0.1, 0.3048, 0.3048006096012192]
_UTM_ALIAS = _re.compile(r"UTM(?:84|-?WGS-?84)?[-_ ]?(\d{1,2})([NS])\b", _re.I)


def epsg_from_text(text: str) -> Optional[int]:
    """EPSG from WKT / .prj / Map 3D XML text: explicit EPSG code, a WKT pyproj can match, or a UTM alias."""
    if not text:
        return None
    m = _re.search(r'(?:EPSG|AUTHORITY\["EPSG",\s*")\D{0,3}(\d{4,5})', text, _re.I)
    if "PROJCS" in text or "GEOGCS" in text or "PROJCRS" in text:
        try:
            from pyproj import CRS
            wkt = text[text.find("PROJCS") if "PROJCS" in text else text.find("GEOGCS"):] if "<" in text else text
            c = CRS.from_wkt(wkt.strip())
            e = c.to_epsg(min_confidence=70)
            if e:
                return int(e)
        except Exception:  # noqa: BLE001
            pass
    if m and validate_epsg(m.group(1)):
        return int(m.group(1))
    u = _UTM_ALIAS.search(text)
    if u:
        z = int(u.group(1))
        if 1 <= z <= 60:
            return (32600 if u.group(2).upper() == "N" else 32700) + z
    return None


def robust_extent(points, keep: float = 0.01):
    """Extent without stray objects far from the drawing (1st..99th percentile of the vertices)."""
    xs, ys = [], []
    for x, y in points:
        if x == x and y == y and abs(x) < 1e12 and abs(y) < 1e12:
            xs.append(x); ys.append(y)
    if not xs:
        return None
    xs.sort(); ys.sort()
    n = len(xs)
    lo, hi = int(n * keep), max(int(n * (1 - keep)) - 1, 0)
    if n < 50:
        lo, hi = 0, n - 1
    return [xs[lo], ys[lo], xs[hi], ys[hi]]


def _inside(lon, lat, box, m=0.0):
    return box[0] - m <= lon <= box[2] + m and box[1] - m <= lat <= box[3] + m


def _dist_km(a, b):
    import math
    dx = (a[0] - b[0]) * 111.3 * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot(dx, (a[1] - b[1]) * 111.3)


def detect_source_crs(extent: Optional[List[float]], geodata_epsg: Optional[int] = None, prj_epsg: Optional[int] = None,
                      target_epsg: Optional[int] = None, unit_hint: float = 1.0, full_extent: Optional[List[float]] = None,
                      units_name: str = "", labels: Optional[List[Tuple[float, float]]] = None) -> Dict[str, Any]:
    """Rank (CRS, unit scale) pairs that place the drawing somewhere plausible.

    Evidence, strongest first: GEODATA in the drawing, a .prj next to it, then the coordinate magnitudes tried
    against the target CRS and the systems used in the UAE / Gulf, at the drawing's own units and the usual
    alternatives (mm, cm, feet). A candidate must land inside its CRS's area of use with a believable size."""
    reasons: List[str] = []
    if not extent:
        return {"v": 2, "suggested": None, "suggested_scale": 1.0, "candidates": [], "confidence": "none",
                "reasons": ["the drawing has no coordinates"]}
    if full_extent and extent != full_extent:
        fw = max(full_extent[2] - full_extent[0], full_extent[3] - full_extent[1])
        rw = max(extent[2] - extent[0], extent[3] - extent[1], 1e-9)
        if fw > rw * 5:
            reasons.append("a few objects far from the drawing were ignored when locating it")
    src_bonus: Dict[int, tuple] = {}
    if geodata_epsg:
        src_bonus[int(geodata_epsg)] = (100, "stored in the drawing (GEODATA)")
    if prj_epsg and int(prj_epsg) not in src_bonus:
        src_bonus[int(prj_epsg)] = (90, ".prj file supplied with the drawing")
    if target_epsg and int(target_epsg) not in src_bonus:
        src_bonus[int(target_epsg)] = (10, "same as the template")
    pool = list(src_bonus) + [c for c in REGION_POOL if c not in src_bonus]
    scales = []
    for s in [unit_hint or 1.0] + SCALES:
        if s > 0 and not any(abs(s - t) < 1e-12 for t in scales):
            scales.append(s)
    cands: List[Dict[str, Any]] = []
    for epsg in pool:
        info = validate_epsg(epsg)
        if not info:
            continue
        aou = info["area_of_use"] or [-180, -90, 180, 90]
        for sc in (scales if not info["is_geographic"] else [1.0]):
            x0, y0, x1, y1 = (v * sc for v in extent)
            if info["is_geographic"]:
                if not (-180 <= x0 <= x1 <= 180 and -90 <= y0 <= y1 <= 90) or x1 - x0 > 5 or y1 - y0 > 5:
                    continue
                corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                size_m = max((x1 - x0), (y1 - y0)) * 111_000
            else:
                size_m = max(x1 - x0, y1 - y0)
                if not 0.5 <= size_m <= 500_000:
                    continue
                t = transformer(epsg, 4326)
                try:
                    lon, lat = t.transform([x0, x1, x1, x0], [y0, y0, y1, y1])
                except Exception:  # noqa: BLE001
                    continue
                corners = list(zip(lon, lat))
            if not all(c[0] == c[0] and abs(c[0]) <= 180 and abs(c[1]) <= 90 for c in corners):
                continue
            cx = sum(c[0] for c in corners) / 4
            cy = sum(c[1] for c in corners) / 4
            if not _inside(cx, cy, aou, 0.2):
                continue
            score, why = 0.0, []
            region = "UAE" if _inside(cx, cy, UAE_BOX) else ("Gulf" if _inside(cx, cy, GCC_BOX) else "")
            if epsg in src_bonus and (src_bonus[epsg][0] >= 90 or region == "UAE"):   # the template only counts in the UAE
                score += src_bonus[epsg][0]; why.append(src_bonus[epsg][1])
            if labels and not info["is_geographic"]:
                mx, my = (x1 - x0) * 0.1 + 5, (y1 - y0) * 0.1 + 5
                hit = sum(1 for e_, n_ in labels if x0 - mx <= e_ <= x1 + mx and y0 - my <= n_ <= y1 + my)
                if hit >= max(2, 0.6 * len(labels)):
                    score += 40; why.append(f"matches {hit} coordinate labels written in the drawing (E= N=)")
            if not region and epsg not in src_bonus:
                continue      # a regional guess must land in the region
            score += 25 if region == "UAE" else 6 if region == "Gulf" else 0
            score += PRIOR.get(epsg, 0)
            if abs(sc - (unit_hint or 1.0)) < 1e-12 and sc != 1.0:
                score += 6; why.append(f"drawing units are {units_name or 'set'} (×{sc:g})")
            elif sc == 1.0:
                score += 4
            else:
                score -= 2; why.append(f"coordinates read as {_unit_word(sc)} (×{sc:g})")
            if region:
                why.append(f"lands in the {region}")
            cands.append({**info, "scale": sc, "center": [round(cx, 6), round(cy, 6)],
                          "footprint": [[round(a, 7), round(b, 7)] for a, b in corners],
                          "size_m": round(size_m, 1), "region": region, "score": round(score, 1), "why": why})
    cands.sort(key=lambda c: -c["score"])
    # keep the best scale per CRS, at most 6 candidates
    seen, out = set(), []
    for c in cands:
        if c["epsg"] in seen or (out and c["score"] < out[0]["score"] - 20):
            continue
        seen.add(c["epsg"]); out.append(c)
        if len(out) == 5:
            break
    if not out:
        reasons.append("coordinates look like a local / engineering grid — no coordinate system places them; "
                       "choose one, or the coordinates are written as they are")
        return {"v": 2, "suggested": None, "suggested_scale": unit_hint or 1.0, "candidates": [], "confidence": "none",
                "reasons": reasons}
    best = out[0]
    rivals = [c for c in out[1:] if _dist_km(c["center"], best["center"]) > 2 and c["score"] >= best["score"] - 10]
    if best["score"] >= 90 or (best["score"] >= 60 and not rivals):
        conf = "high"
    elif best["region"] == "UAE" and not rivals:
        conf = "medium"
    else:
        conf = "low"
    reasons = [f"EPSG:{best['epsg']} — " + ", ".join(best["why"] or ["coordinates fit"])] + reasons
    if rivals:
        reasons.append("other systems also place it plausibly — check it against the satellite image")
    return {"v": 2, "suggested": best["epsg"], "suggested_scale": best["scale"], "candidates": out,
            "confidence": conf, "reasons": reasons}


def _unit_word(sc: float) -> str:
    return {0.001: "millimetres", 0.01: "centimetres", 0.1: "decimetres", 0.3048: "feet",
            0.3048006096012192: "US survey feet"}.get(sc, "drawing units")


# ------------------------------------------------------------------ georeferencing from control points
def fit_similarity(pairs: List[Tuple[float, float, float, float]], fixed_scale: Optional[float] = None) -> Dict[str, Any]:
    """Best shift + rotation + scale taking drawing (x, y) to real (E, N) for control points [(x, y, E, N)].
    One point: shift only (scale = fixed_scale or 1). Returns scale, rotation (deg), offset and per-point residuals."""
    import math
    n = len(pairs)
    if n == 0:
        raise ValueError("no control points")
    k0 = fixed_scale or 1.0
    if n == 1 or fixed_scale:
        ox = sum(E - k0 * x for x, y, E, N in pairs) / n
        oy = sum(N - k0 * y for x, y, E, N in pairs) / n
        a, b = k0, 0.0
    else:
        mx = sum(p[0] for p in pairs) / n; my = sum(p[1] for p in pairs) / n
        mE = sum(p[2] for p in pairs) / n; mN = sum(p[3] for p in pairs) / n
        sxx = sum((x - mx) ** 2 + (y - my) ** 2 for x, y, _, _ in pairs)
        if sxx <= 0:
            raise ValueError("the control points are all at the same place")
        a = sum((x - mx) * (E - mE) + (y - my) * (N - mN) for x, y, E, N in pairs) / sxx
        b = sum((x - mx) * (N - mN) - (y - my) * (E - mE) for x, y, E, N in pairs) / sxx
        ox = mE - (a * mx - b * my); oy = mN - (b * mx + a * my)
    res = [math.hypot(a * x - b * y + ox - E, b * x + a * y + oy - N) for x, y, E, N in pairs]
    return {"scale": math.hypot(a, b), "rotation": math.degrees(math.atan2(b, a)), "offset": [ox, oy],
            "residuals": [round(r, 4) for r in res], "rms": round(math.sqrt(sum(r * r for r in res) / n), 4)}
