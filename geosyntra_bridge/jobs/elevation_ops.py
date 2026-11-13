"""DEM preparation, upload and preview."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional


from .common import JobError, safe_filename, _rmtree, _clean_elevation


class ElevationOps:
    """DEM preparation, upload and preview."""

    def prepare_dem(self, job_id: str, source: str, api_key: Optional[str] = None, upload_name: Optional[str] = None,
                    upload_crs: Optional[str] = None, upload_vertical: Optional[str] = None) -> Dict[str, Any]:
        from ..elevation.dem import DemError
        from ..elevation.sources import prepare
        self._require(job_id, ["mapping", "validation", "done", "generate"])
        bb = self.drawing_bbox_ll(job_id)
        up = os.path.join(self.jdir(job_id), "work", "dem_upload", upload_name) if upload_name else None
        try:
            ds = prepare(source, bb, os.path.join(self.s.data_dir, "dem_cache"), api_key=api_key, upload_path=up,
                         upload_crs=upload_crs or None, upload_vertical=upload_vertical or None,
                         log=lambda msg: self.log(job_id, f"dem: {msg}"))
        except DemError as e:
            raise JobError(str(e))
        ds.save(os.path.join(self.jdir(job_id), "work", "dem"))
        with self._lock(job_id):
            m = self.get_mapping(job_id)
            m["elevation"] = _clean_elevation({**(m.get("elevation") or {}), "enabled": True, "source": source})
            m["dem"] = ds.meta
            self._write(job_id, "mapping.json", m)
        if source != "upload":
            try:
                self._remember(self.state(job_id).get("owner"), dem_source=source)
            except OSError:
                pass
        self.log(job_id, f"DEM prepared: {ds.meta['label']} {ds.meta['resolution_m']} m, vertical {ds.meta['vertical']}, "
                         f"range {ds.meta.get('min')}…{ds.meta.get('max')}, NoData {ds.meta.get('nodata_pct')}%")
        st = self.state(job_id)
        if st["status"] in ("validated", "completed"):
            self._update(job_id, stage="mapping", status="ready", message="Elevation changed — validate again")
        return m

    def upload_dem(self, job_id: str, filename: str, stream, crs: Optional[str], vertical: Optional[str]) -> Dict[str, Any]:
        self._require(job_id, ["mapping", "validation", "done", "generate"])
        name = safe_filename(filename or "dem.tif")
        if not name.lower().endswith((".tif", ".tiff")):
            raise JobError("upload a GeoTIFF (.tif / .tiff)")
        d = os.path.join(self.jdir(job_id), "work", "dem_upload")
        _rmtree(d)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        limit, written = self.s.max_upload_mb * 1024 * 1024, 0
        with open(path, "wb") as out:
            while True:
                chunk = stream.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise JobError(f"file exceeds the {self.s.max_upload_mb} MB upload limit", 413)
                out.write(chunk)
        self.log(job_id, f"DEM GeoTIFF uploaded: {name} ({written:,} bytes)")
        return self.prepare_dem(job_id, "upload", upload_name=name, upload_crs=crs, upload_vertical=vertical)

    def disable_dem(self, job_id: str) -> Dict[str, Any]:
        self._require(job_id, ["mapping", "validation", "done", "generate"])
        with self._lock(job_id):
            m = self.get_mapping(job_id)
            m["elevation"] = _clean_elevation({**(m.get("elevation") or {}), "enabled": False})
            self._write(job_id, "mapping.json", m)
        self.log(job_id, "DEM elevation disabled")
        return m

    def dem_preview(self, job_id: str, limit: int = 4000) -> Dict[str, Any]:
        """DEM footprint + elevation sampled at the CAD features (before conversion), in lon/lat."""
        import numpy as np
        from pyproj import CRS, Transformer
        from ..elevation.dem import DemSet
        ds = DemSet.load(os.path.join(self.jdir(job_id), "work", "dem"))
        if ds is None:
            raise JobError("no DEM prepared", 404)
        m = self.get_mapping(job_id)
        schema = self.reg.get(self.state(job_id)["schema_id"])
        doc = self.load_doc(job_id)
        src = int(m.get("source_crs") or schema.spatial_reference.epsg)
        from ..geometry.convert import pipeline_for
        cp = pipeline_for(m, None)
        excluded = {k for k, v in m["layers"].items() if v.get("role") == "ignore"}
        pts, meta = [], []
        for e in doc.features:
            if e.group in excluded:
                continue
            p = e.rep_point()
            if p:
                pts.append(cp.local([p])[0])
                meta.append((e.layer, (m["layers"].get(e.group) or {}).get("target")))
        step = max(1, len(pts) // limit)
        pts, meta = pts[::step], meta[::step]
        feats = []
        if pts:
            dem_crs = CRS.from_user_input(ds.crs())
            t = Transformer.from_crs(src, dem_crs, always_xy=True)
            x, y = t.transform([p[0] for p in pts], [p[1] for p in pts])
            v, fl = ds.sample(np.asarray(x), np.asarray(y), (m.get("elevation") or {}).get("interpolation") or "bilinear")
            to_ll = Transformer.from_crs(src, 4326, always_xy=True)
            lon, lat = to_ll.transform([p[0] for p in pts], [p[1] for p in pts])
            for i in range(len(pts)):
                z = None if np.isnan(v[i]) else round(float(v[i]), 2)
                feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon[i], lat[i]]},
                              "properties": {"layer": meta[i][0], "target": meta[i][1], "z_mean": z,
                                             "z_status": "dem" if z is not None else "missing"}})
        t2 = Transformer.from_crs(CRS.from_user_input(ds.crs()), 4326, always_xy=True)
        for g in ds.grids:
            b = g.bounds()
            xs, ys = t2.transform([b[0], b[2], b[2], b[0], b[0]], [b[1], b[1], b[3], b[3], b[1]])
            feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [list(zip(xs, ys))]},
                          "properties": {"dem_footprint": g.name, "status": "dem"}})
        zs = [f["properties"]["z_mean"] for f in feats if f["properties"].get("z_mean") is not None]
        return {"type": "FeatureCollection", "crs_is_geographic": True, "truncated": step > 1, "features": feats,
                "stats": {"sampled": len(pts), "with_value": len(zs), "missing": len(pts) - len(zs),
                          "min": min(zs) if zs else None, "max": max(zs) if zs else None,
                          "mean": round(sum(zs) / len(zs), 2) if zs else None}, "dem": ds.meta}
