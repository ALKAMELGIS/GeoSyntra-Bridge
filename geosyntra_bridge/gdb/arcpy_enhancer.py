"""
Optional post-processing with ArcGIS `arcpy` (only when running inside an
ArcGIS Pro Python environment): applies subtypes, subtype defaults and
subtype-specific domains, which GDAL cannot create. Without arcpy the
subtype field values are still written and validated, and the
verification report states that the subtype definitions were not applied.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from ..schema.models import SchemaDefinition


def arcpy_available() -> bool:
    if os.environ.get("GSB_DISABLE_ARCPY") == "1":
        return False
    try:
        import arcpy  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def apply_subtypes(gdb_path: str, schema: SchemaDefinition) -> Dict[str, Any]:
    layers = [l for l in schema.layers if l.subtype_field and l.subtypes]
    if not layers:
        return {"applied": True, "layers": [], "note": "schema defines no subtypes"}
    if not arcpy_available():
        return {"applied": False, "layers": [l.name for l in layers],
                "note": "subtype definitions require ArcGIS Pro (arcpy); subtype codes were written to "
                        f"{', '.join(sorted({l.subtype_field for l in layers}))} and validated"}
    import arcpy  # type: ignore
    done: List[str] = []
    errors: List[str] = []
    for l in layers:
        fc = os.path.join(gdb_path, l.feature_dataset, l.name) if l.feature_dataset else os.path.join(gdb_path, l.name)
        try:
            arcpy.management.SetSubtypeField(fc, l.subtype_field)
            for st in l.subtypes:
                arcpy.management.AddSubtype(fc, st.code, st.name)
            if l.default_subtype is not None:
                arcpy.management.SetDefaultSubtype(fc, l.default_subtype)
            for st in l.subtypes:
                for fname, dom in st.field_domains.items():
                    arcpy.management.AssignDomainToField(fc, fname, dom, f"{st.code}: {st.name}")
                for fname, val in st.defaults.items():
                    arcpy.management.AssignDefaultToField(fc, fname, val, f"{st.code}: {st.name}")
            done.append(l.name)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{l.name}: {e}")
    return {"applied": not errors, "layers": done, "errors": errors}
