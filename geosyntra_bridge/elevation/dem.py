"""
DEM grids: reading (GeoTIFF / COG through GDAL, SRTM .hgt), windowing, per-job storage and sampling.

A DemGrid is a float32 array (NaN = NoData) with its GDAL geotransform, horizontal CRS and vertical reference.
Only the window covering the drawing (plus a margin) is kept, so national-scale rasters stay cheap.

Sampling: nearest | bilinear | bicubic (Keys, a = -0.5). Interpolation never invents values across NoData:
bilinear renormalises over the valid neighbours and flags the sample, bicubic falls back to bilinear when its
4x4 support touches NoData, and a point whose support is entirely NoData returns NaN (reported as missing,
never as 0).
"""
from __future__ import annotations

import ctypes
import gzip
import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..gdb import gdal_c as G

GDT_FLOAT32 = 6


class DemError(Exception):
    pass


@dataclass
class DemGrid:
    data: np.ndarray                      # float32, NaN = NoData
    gt: Tuple[float, ...]                 # GDAL geotransform of data[0, 0]
    crs: str                              # horizontal CRS (WKT or "EPSG:xxxx")
    name: str = ""
    nodata_source: Optional[float] = None

    @property
    def shape(self):
        return self.data.shape

    def bounds(self) -> Tuple[float, float, float, float]:
        h, w = self.data.shape
        x0, y0 = self.gt[0], self.gt[3]
        x1, y1 = x0 + w * self.gt[1], y0 + h * self.gt[5]
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def contains(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        b = self.bounds()
        return (x >= b[0]) & (x <= b[2]) & (y >= b[1]) & (y <= b[3])

    def pixel_size(self) -> Tuple[float, float]:
        return abs(self.gt[1]), abs(self.gt[5])

    # ------------------------------------------------------------ sampling
    def sample(self, x: np.ndarray, y: np.ndarray, method: str = "bilinear") -> Tuple[np.ndarray, np.ndarray]:
        """-> (values with NaN where unavailable, flag 1 where interpolation had to skip NoData neighbours)"""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        a = self.data
        h, w = a.shape
        fx = (x - self.gt[0]) / self.gt[1]           # pixel-space coordinates (0 = left edge)
        fy = (y - self.gt[3]) / self.gt[5]
        out = np.full(x.shape, np.nan)
        flag = np.zeros(x.shape, dtype=np.int8)
        inside = (fx >= 0) & (fx <= w) & (fy >= 0) & (fy <= h)
        if not inside.any():
            return out, flag
        if method == "nearest":
            c = np.clip(np.floor(fx[inside]).astype(int), 0, w - 1)
            r = np.clip(np.floor(fy[inside]).astype(int), 0, h - 1)
            out[inside] = a[r, c]
            return out, flag
        # pixel-centre based interpolation
        cx, cy = fx[inside] - 0.5, fy[inside] - 0.5
        bil, bflag = _bilinear(a, cx, cy)
        if method == "bicubic":
            cub, ok = _bicubic(a, cx, cy)
            res = np.where(ok, cub, bil)
            out[inside] = res
            flag[inside] = np.where(ok, 0, bflag)
        else:
            out[inside] = bil
            flag[inside] = bflag
        return out, flag

    # ------------------------------------------------------------ storage
    def save(self, path: str):
        np.savez_compressed(path, data=self.data, gt=np.array(self.gt, dtype=float),
                            meta=np.array(json.dumps({"crs": self.crs, "name": self.name,
                                                      "nodata_source": self.nodata_source})))

    @classmethod
    def load(cls, path: str) -> "DemGrid":
        z = np.load(path, allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        return cls(z["data"], tuple(float(v) for v in z["gt"]), meta["crs"], meta.get("name", ""), meta.get("nodata_source"))


def _take(a: np.ndarray, r: np.ndarray, c: np.ndarray) -> np.ndarray:
    h, w = a.shape
    return a[np.clip(r, 0, h - 1), np.clip(c, 0, w - 1)]


def _bilinear(a: np.ndarray, cx: np.ndarray, cy: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    c0 = np.floor(cx).astype(int)
    r0 = np.floor(cy).astype(int)
    tx, ty = cx - c0, cy - r0
    vals = [_take(a, r0, c0), _take(a, r0, c0 + 1), _take(a, r0 + 1, c0), _take(a, r0 + 1, c0 + 1)]
    wts = [(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty]
    num = np.zeros(cx.shape)
    den = np.zeros(cx.shape)
    any_nan = np.zeros(cx.shape, dtype=bool)
    for v, wt in zip(vals, wts):
        ok = ~np.isnan(v)
        num += np.where(ok, v, 0.0) * wt
        den += np.where(ok, wt, 0.0)
        any_nan |= ~ok & (wt > 1e-12)
    res = np.where(den > 1e-12, num / np.where(den > 1e-12, den, 1.0), np.nan)
    return res, any_nan.astype(np.int8)


def _keys(t: np.ndarray, a: float = -0.5) -> List[np.ndarray]:
    def k(d):
        d = np.abs(d)
        return np.where(d <= 1, (a + 2) * d ** 3 - (a + 3) * d ** 2 + 1,
                        np.where(d < 2, a * d ** 3 - 5 * a * d ** 2 + 8 * a * d - 4 * a, 0.0))
    return [k(t + 1), k(t), k(1 - t), k(2 - t)]


def _bicubic(a: np.ndarray, cx: np.ndarray, cy: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    c0 = np.floor(cx).astype(int)
    r0 = np.floor(cy).astype(int)
    wx, wy = _keys(cx - c0), _keys(cy - r0)
    res = np.zeros(cx.shape)
    ok = np.ones(cx.shape, dtype=bool)
    for j in range(4):
        row = np.zeros(cx.shape)
        for i in range(4):
            v = _take(a, r0 + j - 1, c0 + i - 1)
            ok &= ~np.isnan(v)
            row += np.where(np.isnan(v), 0.0, v) * wx[i]
        res += row * wy[j]
    return res, ok


# ------------------------------------------------------------------ reading
def _epsg_of_wkt(wkt: str) -> Optional[int]:
    try:
        from pyproj import CRS
        return CRS.from_user_input(wkt).to_epsg()
    except Exception:  # noqa: BLE001
        return None


def raster_info(path: str) -> Dict[str, Any]:
    g = G.gdal()
    ds = g.GDALOpenEx(path.encode("utf-8"), 0x02, None, None, None)   # GDAL_OF_RASTER
    if not ds:
        raise DemError(f"cannot open raster {os.path.basename(path)}: {g.last_error()}")
    try:
        gt = (ctypes.c_double * 6)()
        g.GDALGetGeoTransform(ds, gt)
        band = g.GDALGetRasterBand(ds, 1)
        has = ctypes.c_int(0)
        nd = g.GDALGetRasterNoDataValue(band, ctypes.byref(has))
        wkt = (g.GDALGetProjectionRef(ds) or b"").decode("utf-8", "replace")
        return {"width": g.GDALGetRasterXSize(ds), "height": g.GDALGetRasterYSize(ds), "bands": g.GDALGetRasterCount(ds),
                "gt": tuple(gt), "crs": wkt, "epsg": _epsg_of_wkt(wkt) if wkt else None,
                "nodata": nd if has.value else None}
    finally:
        g.GDALClose(ds)


def read_window(path: str, bbox: Optional[Tuple[float, float, float, float]], name: str = "",
                crs_override: Optional[str] = None, margin_px: int = 3) -> Optional[DemGrid]:
    """Read band 1 inside bbox (raster CRS units) as a DemGrid; None when the bbox misses the raster."""
    info = raster_info(path)
    gt, W, H = info["gt"], info["width"], info["height"]
    if gt[2] or gt[4]:
        raise DemError("rotated rasters are not supported — warp the DEM to a north-up grid first")
    if bbox is None:
        c0, r0, c1, r1 = 0, 0, W, H
    else:
        xs = sorted([(bbox[0] - gt[0]) / gt[1], (bbox[2] - gt[0]) / gt[1]])
        ys = sorted([(bbox[1] - gt[3]) / gt[5], (bbox[3] - gt[3]) / gt[5]])
        c0, c1 = max(0, int(math.floor(xs[0])) - margin_px), min(W, int(math.ceil(xs[1])) + margin_px)
        r0, r1 = max(0, int(math.floor(ys[0])) - margin_px), min(H, int(math.ceil(ys[1])) + margin_px)
        if c0 >= c1 or r0 >= r1:
            return None
    w, h = c1 - c0, r1 - r0
    if w * h > 60_000_000:
        raise DemError(f"DEM window {w}x{h} pixels is too large — use a coarser DEM or a smaller area")
    g = G.gdal()
    ds = g.GDALOpenEx(path.encode("utf-8"), 0x02, None, None, None)
    try:
        band = g.GDALGetRasterBand(ds, 1)
        buf = np.empty((h, w), dtype=np.float32)
        rc = g.GDALRasterIO(band, 0, c0, r0, w, h, buf.ctypes.data_as(ctypes.c_void_p), w, h, GDT_FLOAT32, 0, 0)
        if rc != 0:
            raise DemError(f"reading {os.path.basename(path)} failed: {g.last_error()}")
    finally:
        g.GDALClose(ds)
    nd = info["nodata"]
    if nd is not None:
        buf[buf == np.float32(nd)] = np.nan
    buf[~np.isfinite(buf)] = np.nan
    ngt = (gt[0] + c0 * gt[1], gt[1], 0.0, gt[3] + r0 * gt[5], 0.0, gt[5])
    crs = crs_override or info["crs"]
    if not crs:
        raise DemError("the raster has no coordinate system — choose its EPSG code")
    return DemGrid(buf, ngt, crs, name or os.path.basename(path), nd)


def read_hgt(path: str, lat: int, lon: int, bbox: Optional[Tuple[float, float, float, float]], name: str = "") -> Optional[DemGrid]:
    """SRTM .hgt / .hgt.gz (big-endian int16, -32768 = void, pixel-is-point on whole-degree edges)."""
    raw = gzip.open(path, "rb").read() if path.endswith(".gz") else open(path, "rb").read()
    n = int(round(math.sqrt(len(raw) / 2)))
    if n * n * 2 != len(raw):
        raise DemError(f"{os.path.basename(path)} is not a valid SRTM tile")
    a = np.frombuffer(raw, dtype=">i2").reshape(n, n).astype(np.float32)
    a[a == -32768] = np.nan
    d = 1.0 / (n - 1)
    gt = (lon - d / 2, d, 0.0, lat + 1 + d / 2, 0.0, -d)
    grid = DemGrid(a, gt, "EPSG:4326", name or os.path.basename(path), -32768.0)
    return clip(grid, bbox) if bbox else grid


def clip(grid: DemGrid, bbox: Tuple[float, float, float, float], margin_px: int = 3) -> Optional[DemGrid]:
    h, w = grid.data.shape
    gt = grid.gt
    xs = sorted([(bbox[0] - gt[0]) / gt[1], (bbox[2] - gt[0]) / gt[1]])
    ys = sorted([(bbox[1] - gt[3]) / gt[5], (bbox[3] - gt[3]) / gt[5]])
    c0, c1 = max(0, int(math.floor(xs[0])) - margin_px), min(w, int(math.ceil(xs[1])) + margin_px)
    r0, r1 = max(0, int(math.floor(ys[0])) - margin_px), min(h, int(math.ceil(ys[1])) + margin_px)
    if c0 >= c1 or r0 >= r1:
        return None
    return DemGrid(np.ascontiguousarray(grid.data[r0:r1, c0:c1]),
                   (gt[0] + c0 * gt[1], gt[1], 0.0, gt[3] + r0 * gt[5], 0.0, gt[5]), grid.crs, grid.name, grid.nodata_source)


def write_geotiff(path: str, data: np.ndarray, gt: Sequence[float], epsg: int, nodata: Optional[float] = None):
    """Small helper (tests, exports): float32 single-band GeoTIFF."""
    g = G.gdal()
    drv = g.GDALGetDriverByName(b"GTiff")
    h, w = data.shape
    ds = g.GDALCreate(drv, path.encode("utf-8"), w, h, 1, GDT_FLOAT32, None)
    if not ds:
        raise DemError(g.last_error())
    try:
        arr = (ctypes.c_double * 6)(*gt)
        g.GDALSetGeoTransform(ds, arr)
        from pyproj import CRS
        g.GDALSetProjection(ds, CRS.from_epsg(epsg).to_wkt().encode())
        band = g.GDALGetRasterBand(ds, 1)
        if nodata is not None:
            g.GDALSetRasterNoDataValue(band, float(nodata))
        buf = np.ascontiguousarray(data, dtype=np.float32)
        g.GDALRasterIO(band, 1, 0, 0, w, h, buf.ctypes.data_as(ctypes.c_void_p), w, h, GDT_FLOAT32, 0, 0)
    finally:
        g.GDALClose(ds)


# ------------------------------------------------------------------ the set of grids prepared for a job
@dataclass
class DemSet:
    grids: List[DemGrid]
    meta: Dict[str, Any] = field(default_factory=dict)   # source, label, vertical, resolution, tiles, coverage ...

    def save(self, folder: str):
        os.makedirs(folder, exist_ok=True)
        for fn in os.listdir(folder):
            if fn.startswith("grid_") and fn.endswith(".npz"):
                os.remove(os.path.join(folder, fn))
        for i, g in enumerate(self.grids):
            g.save(os.path.join(folder, f"grid_{i}.npz"))
        with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(self.meta, f, indent=1)

    @classmethod
    def load(cls, folder: str) -> Optional["DemSet"]:
        mp = os.path.join(folder, "meta.json")
        if not os.path.isfile(mp):
            return None
        with open(mp, encoding="utf-8") as f:
            meta = json.load(f)
        grids = [DemGrid.load(os.path.join(folder, fn)) for fn in sorted(os.listdir(folder))
                 if fn.startswith("grid_") and fn.endswith(".npz")]
        return cls(grids, meta)

    def sample(self, x: np.ndarray, y: np.ndarray, method: str) -> Tuple[np.ndarray, np.ndarray]:
        """x, y in the grids' CRS (all grids of a set share one CRS)."""
        out = np.full(len(x), np.nan)
        flag = np.zeros(len(x), dtype=np.int8)
        for g in self.grids:
            todo = np.isnan(out) & g.contains(x, y)
            if todo.any():
                v, fl = g.sample(x[todo], y[todo], method)
                out[todo], flag[todo] = v, fl
        return out, flag

    def crs(self) -> str:
        return self.grids[0].crs if self.grids else "EPSG:4326"

    def stats(self) -> Dict[str, Any]:
        vals = [g.data[~np.isnan(g.data)] for g in self.grids]
        allv = np.concatenate(vals) if vals else np.array([])
        n = sum(g.data.size for g in self.grids)
        return {"min": round(float(allv.min()), 2) if allv.size else None,
                "max": round(float(allv.max()), 2) if allv.size else None,
                "mean": round(float(allv.mean()), 2) if allv.size else None,
                "nodata_pct": round(100.0 * (n - allv.size) / n, 2) if n else None}
