"""
DEM sources: global open DEMs fetched by tile for the drawing's extent, or the user's own GeoTIFF.

| source            | data                                   | resolution          | vertical reference |
|-------------------|----------------------------------------|---------------------|--------------------|
| copernicus_glo30  | Copernicus DEM GLO-30 (DSM), AWS open  | 1 arc-second ~30 m  | EGM2008 (EPSG:3855)|
| srtm_gl1          | NASA SRTM GL1 void-filled, AWS 'skadi' | 1 arc-second ~30 m  | EGM96   (EPSG:5773)|
| alos_aw3d30       | JAXA ALOS AW3D30 via OpenTopography API| 1 arc-second ~30 m  | EGM96   (EPSG:5773)|
| upload            | user GeoTIFF / COG (survey, LiDAR ...) | as delivered        | chosen by the user |

Tiles are cached in <data_dir>/dem_cache and reused by every job. Base URLs can be redirected to an internal
mirror with GSB_DEM_URL_<SOURCE> (same {NS}{lat}{EW}{lon} placeholders); the OpenTopography key comes from the
request or GSB_OPENTOPOGRAPHY_KEY. Copernicus publishes no tiles over open sea — a missing tile is reported as
such, never filled.
"""
from __future__ import annotations

import math
import os
import shutil
from typing import Any, Callable, Dict, List, Optional, Tuple

from .dem import DemError, DemGrid, DemSet, clip, raster_info, read_hgt, read_window

SOURCES: Dict[str, Dict[str, Any]] = {
    "copernicus_glo30": {
        "label": "Copernicus DEM GLO-30", "provider": "ESA / Airbus via AWS Open Data", "surface": "DSM (includes buildings, trees)",
        "resolution": "1 arc-second (~30 m)", "resolution_m": 30, "vertical": "EPSG:3855", "vertical_label": "EGM2008 geoid heights",
        "vertical_accuracy_m": 2.4,   # spec < 4 m LE90 absolute -> ~2.4 m std. dev.
        "needs_key": False, "tiled": True,
        "url": "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM/"
               "Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM.tif",
        "license": "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the "
                   "European Union and ESA; all rights reserved"},
    "srtm_gl1": {
        "label": "NASA SRTM GL1", "provider": "NASA / USGS, AWS terrain tiles (skadi)", "surface": "DSM (C-band radar, 2000)",
        "resolution": "1 arc-second (~30 m)", "resolution_m": 30, "vertical": "EPSG:5773", "vertical_label": "EGM96 geoid heights",
        "vertical_accuracy_m": 9.7,   # mission spec 16 m LE90 -> ~9.7 m std. dev.
        "needs_key": False, "tiled": True,
        "url": "https://elevation-tiles-prod.s3.amazonaws.com/skadi/{NS}{lat:02d}/{NS}{lat:02d}{EW}{lon:03d}.hgt.gz",
        "license": "NASA SRTM, public domain"},
    "alos_aw3d30": {
        "label": "ALOS World 3D AW3D30", "provider": "JAXA via OpenTopography API (free API key)", "surface": "DSM (optical stereo)",
        "resolution": "1 arc-second (~30 m)", "resolution_m": 30, "vertical": "EPSG:5773", "vertical_label": "EGM96 geoid heights",
        "vertical_accuracy_m": 5.0,   # JAXA target accuracy 5 m (1 sigma)
        "needs_key": True, "tiled": False,
        "url": "https://portal.opentopography.org/API/globaldem?demtype=AW3D30&south={s}&north={n}&west={w}&east={e}"
               "&outputFormat=GTiff&API_Key={key}",
        "license": "© JAXA, ALOS World 3D-30m (AW3D30)"},
    "upload": {
        "label": "Uploaded GeoTIFF", "provider": "your file (survey DTM, LiDAR, municipal DEM ...)", "surface": "as delivered",
        "resolution": "from file", "resolution_m": None, "vertical": None, "vertical_label": "chosen by you",
        "vertical_accuracy_m": None,
        "needs_key": False, "tiled": False, "url": None, "license": ""},
}

VERTICAL_REFS = {
    "EPSG:3855": "EGM2008 geoid heights", "EPSG:5773": "EGM96 geoid heights",
    "EPSG:4979": "WGS 84 ellipsoidal heights", "ras_ghumays": "Ras Ghumays (Abu Dhabi height datum, MSL)",
    "unknown": "unknown / local (no conversion possible)",
}


def _url(source: str, **kw) -> str:
    tpl = os.environ.get(f"GSB_DEM_URL_{source.upper()}") or SOURCES[source]["url"]
    return tpl.format(**kw)


def tiles_for(bbox_ll: Tuple[float, float, float, float]) -> List[Tuple[int, int]]:
    w, s, e, n = bbox_ll
    return [(lat, lon) for lat in range(math.floor(s), math.floor(n) + 1)
            for lon in range(math.floor(w), math.floor(e) + 1)]


def _hemi(lat: int, lon: int) -> Dict[str, Any]:
    return {"NS": "N" if lat >= 0 else "S", "lat": abs(lat), "EW": "E" if lon >= 0 else "W", "lon": abs(lon)}


def _download(url: str, dest: str, log: Callable[[str], None], timeout: int = 120) -> Optional[str]:
    """-> dest, or None when the server has no such tile (404). Raises DemError on network failure."""
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return dest
    import requests
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    safe = url.split("API_Key=")[0] + ("API_Key=***" if "API_Key=" in url else "")
    try:
        with requests.get(url, stream=True, timeout=timeout) as r:
            if r.status_code in (403, 404):
                log(f"no tile at {safe} (HTTP {r.status_code})")
                return None
            if r.status_code != 200:
                raise DemError(f"{safe}: HTTP {r.status_code} {r.text[:200] if r.headers.get('content-type', '').startswith('text') else ''}")
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            os.replace(tmp, dest)
    except DemError:
        raise
    except Exception as e:  # noqa: BLE001
        raise DemError(f"cannot download {safe}: {e} — the server needs internet access to this DEM provider, "
                       f"or upload a GeoTIFF instead")
    log(f"downloaded {safe} ({os.path.getsize(dest) / 1e6:.1f} MB)")
    return dest


def prepare(source: str, bbox_ll: Tuple[float, float, float, float], cache_dir: str,
            api_key: Optional[str] = None, upload_path: Optional[str] = None, upload_crs: Optional[str] = None,
            upload_vertical: Optional[str] = None, log: Callable[[str], None] = lambda m: None) -> DemSet:
    """Fetch / read the DEM covering bbox_ll (lon/lat: west, south, east, north) and clip it to that area."""
    if source not in SOURCES:
        raise DemError(f"unknown DEM source '{source}'")
    info = SOURCES[source]
    m = 0.002   # ~200 m margin so interpolation at the drawing edge has neighbours
    bb = (bbox_ll[0] - m, bbox_ll[1] - m, bbox_ll[2] + m, bbox_ll[3] + m)
    grids: List[DemGrid] = []
    tiles_meta: List[Dict[str, Any]] = []
    if source == "copernicus_glo30":
        for lat, lon in tiles_for(bb):
            h = _hemi(lat, lon)
            url = _url(source, **h)
            p = _download(url, os.path.join(cache_dir, source, os.path.basename(url)), log)
            tiles_meta.append({"tile": f"{h['NS']}{h['lat']:02d}{h['EW']}{h['lon']:03d}", "status": "ok" if p else "no tile (open sea)"})
            if p:
                g = read_window(p, bb, name=os.path.basename(p))
                if g is not None:
                    grids.append(g)
    elif source == "srtm_gl1":
        for lat, lon in tiles_for(bb):
            h = _hemi(lat, lon)
            url = _url(source, **h)
            p = _download(url, os.path.join(cache_dir, source, os.path.basename(url)), log)
            tiles_meta.append({"tile": f"{h['NS']}{h['lat']:02d}{h['EW']}{h['lon']:03d}", "status": "ok" if p else "no tile"})
            if p:
                g = read_hgt(p, lat, lon, bb, name=os.path.basename(p))
                if g is not None:
                    grids.append(g)
    elif source == "alos_aw3d30":
        key = api_key or os.environ.get("GSB_OPENTOPOGRAPHY_KEY")
        if not key:
            raise DemError("ALOS AW3D30 is served by OpenTopography and needs a free API key "
                           "(portal.opentopography.org → My Account → Request API key)")
        r6 = lambda v: f"{v:.6f}"  # noqa: E731
        url = _url(source, s=r6(bb[1]), n=r6(bb[3]), w=r6(bb[0]), e=r6(bb[2]), key=key)
        name = f"AW3D30_{r6(bb[0])}_{r6(bb[1])}_{r6(bb[2])}_{r6(bb[3])}.tif".replace("-", "m")
        p = _download(url, os.path.join(cache_dir, source, name), log, timeout=300)
        if p is None:
            raise DemError("OpenTopography returned no AW3D30 data for this area (check the API key)")
        tiles_meta.append({"tile": name, "status": "ok"})
        g = read_window(p, bb, name="AW3D30")
        if g is not None:
            grids.append(g)
    else:   # upload
        if not upload_path:
            raise DemError("no GeoTIFF uploaded")
        ri = raster_info(upload_path)
        crs = upload_crs or ri["crs"]
        if not crs:
            raise DemError("the uploaded GeoTIFF has no coordinate system — choose its EPSG code")
        from pyproj import CRS, Transformer
        t = Transformer.from_crs(4326, CRS.from_user_input(crs), always_xy=True)
        xs, ys = t.transform([bb[0], bb[2], bb[0], bb[2]], [bb[1], bb[1], bb[3], bb[3]])
        g = read_window(upload_path, (min(xs), min(ys), max(xs), max(ys)), name=os.path.basename(upload_path),
                        crs_override=upload_crs)
        tiles_meta.append({"tile": os.path.basename(upload_path), "status": "ok" if g is not None else "does not cover the drawing"})
        if g is not None:
            grids.append(g)
    if not grids:
        raise DemError(f"{info['label']}: no elevation data covers the drawing extent "
                       f"({bbox_ll[0]:.4f}, {bbox_ll[1]:.4f}) – ({bbox_ll[2]:.4f}, {bbox_ll[3]:.4f})")
    px = grids[0].pixel_size()
    crs = grids[0].crs
    try:
        from pyproj import CRS
        c = CRS.from_user_input(crs)
        geographic = c.is_geographic
        epsg = c.to_epsg()
    except Exception:  # noqa: BLE001
        geographic, epsg = False, None
    res_m = (px[0] * 111320 * math.cos(math.radians((bbox_ll[1] + bbox_ll[3]) / 2)), px[1] * 110540) if geographic else px
    vertical = upload_vertical if source == "upload" else info["vertical"]
    ds = DemSet(grids, {
        "source": source, "label": info["label"] if source != "upload" else f"Uploaded GeoTIFF ({tiles_meta[0]['tile']})",
        "provider": info["provider"], "surface": info["surface"], "license": info["license"],
        "vertical_accuracy_m": info.get("vertical_accuracy_m"),
        "vertical": vertical or "unknown", "vertical_label": VERTICAL_REFS.get(vertical or "unknown", vertical),
        "crs": f"EPSG:{epsg}" if epsg else "custom", "pixel_size": [px[0], px[1]],
        "resolution_m": [round(res_m[0], 2), round(res_m[1], 2)], "tiles": tiles_meta,
        "bbox_ll": list(bbox_ll)})
    ds.meta.update(ds.stats())
    return ds
