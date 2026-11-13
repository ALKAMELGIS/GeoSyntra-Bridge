"""JSON-safe results bundle (drives the results page, Excel and PDF reports)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .conversion import ConversionResult, issue_locations_wgs84


def _jsonable(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, float) and v != v:
        return None
    return v


def build_bundle(job_id: str, source_file: str, result: ConversionResult, gdb: Optional[Dict[str, Any]] = None,
                 verification: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    oid = {f.fid: f.objectid for f in result.features}
    issues = issue_locations_wgs84(result)
    for i in issues:
        i["objectid"] = oid.get(i.get("feature_id")) if i.get("feature_id") else None
    feats = []
    for f in result.features:
        feats.append({"fid": f.fid, "objectid": f.objectid, "target": f.target, "subtype": f.subtype,
                      "status": "failed" if f.failed else ("skipped" if not f.write else "ok"),
                      "failure": f.failure, "layer": f.source.layer, "handle": f.source.handle,
                      "entity_type": f.source.dxftype, "labels": f.labels, "remarks": list(dict.fromkeys(f.remarks)),
                      "issue_count": len(f.issue_ids),
                      "z": {k: f.z.get(k) for k in ("status", "min", "max", "mean", "start", "end", "dem_mean",
                                                     "n_vertices", "n_cad", "n_dem", "n_interpolated", "n_missing")
                            if f.z.get(k) is not None} if f.z else None})
    return {
        "job_id": job_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": source_file,
        "schema": {"schema_id": result.schema.schema_id, "display_name": result.schema.display_name,
                   "version": result.schema.version, "status": result.schema.status},
        "summary": result.summary(),
        "mapping_rows": result.mapping_rows,
        "issues": issues,
        "features": feats,
        "labels": {"stats": result.label_report.get("stats", {}), "rows": result.label_report.get("rows", []),
                   "unrecognised_samples": result.label_report.get("unrecognised_samples", [])},
        "gdb": gdb,
        "verification": verification,
        "elevation": result.elevation or {},
    }
