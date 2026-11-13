"""Dataset-level checks: duplicate geometries, duplicate identifiers, CRS area of use."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from shapely import wkb as swkb
from shapely import set_precision

from ..engine.model import IssueLog, TargetFeature
from ..geometry.crs import check_in_area_of_use
from ..schema.models import SchemaDefinition


def check_duplicate_geometries(features: List[TargetFeature], tol: float, issues: IssueLog, policy: str) -> int:
    seen: Dict[tuple, TargetFeature] = {}
    n = 0
    for f in features:
        if f.failed or f.geom is None:
            continue
        try:
            key = (f.target, swkb.dumps(set_precision(f.geom, max(tol, 1e-9)), hex=True))
        except Exception:  # noqa: BLE001
            continue
        first = seen.get(key)
        if first is None:
            seen[key] = f
            continue
        n += 1
        f.duplicate_of = first.fid
        skip = policy == "skip"
        it = issues.add("warning", "duplicate", "duplicate_geometry",
                        f"{f.target} #{f.fid} duplicates the geometry of #{first.fid}"
                        + (" — not written (duplicate policy: skip)" if skip else " — written and flagged"),
                        suggestion="delete the duplicate entity in CAD", target_layer=f.target, feature_id=f.fid,
                        location=f.location(), source=f.source.source_ref(), related=[first.fid])
        f.issue_ids.append(it.id)
        f.remarks.append(f"DUPLICATE of #{first.fid}")
        if skip:
            f.write = False
    return n


def check_unique_ids(schema: SchemaDefinition, features: List[TargetFeature], issues: IssueLog) -> int:
    by: Dict[tuple, List[TargetFeature]] = defaultdict(list)
    for f in features:
        if f.failed or not f.write:
            continue
        layer = schema.get_layer(f.target)
        fields = list(schema.validation.unique_fields.get(f.target, []))
        if layer and layer.id_field and layer.id_field not in fields:
            fields.append(layer.id_field)
        for fld in fields:
            v = f.attrs.get(fld)
            if v not in (None, ""):
                by[(f.target, fld, str(v).upper())].append(f)
    n = 0
    for (target, fld, v), fs in by.items():
        if len(fs) < 2:
            continue
        for f in fs[1:]:
            n += 1
            it = issues.add("error", "duplicate", "duplicate_id",
                            f"{target}.{fld} '{v}' is used by {len(fs)} features (#{', #'.join(str(x.fid) for x in fs[:6])})",
                            suggestion="identifiers must be unique — correct the CAD label/attribute",
                            target_layer=target, feature_id=f.fid, field=fld, location=f.location(),
                            source=f.source.source_ref(), related=[x.fid for x in fs if x is not f][:20])
            f.issue_ids.append(it.id)
            f.remarks.append(f"duplicate {fld} '{v}'")
    return n


def check_crs_extent(features: List[TargetFeature], epsg, issues: IssueLog) -> int:
    if not epsg:
        return 0
    outside = [f for f in features if not f.failed and f.geom is not None and f.location()
               and not check_in_area_of_use(f.location(), epsg)]
    if outside:
        f = outside[0]
        issues.add("error", "crs", "outside_crs_area",
                   f"{len(outside)} feature(s) fall outside the valid area of EPSG:{epsg} — the source coordinate system is probably wrong",
                   suggestion="re-check the source CRS / drawing units in the Source step",
                   target_layer=f.target, feature_id=f.fid, location=f.location(),
                   related=[x.fid for x in outside[1:50]])
    return len(outside)
