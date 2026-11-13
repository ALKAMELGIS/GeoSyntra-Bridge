"""
Esri File Geodatabase writer (GDAL OpenFileGDB driver through the C API).

Creates a brand-new .gdb for every conversion:
  1. coded-value and range domains
  2. feature datasets + every feature class of the schema (empty ones too),
     with field types, lengths, aliases, nullability, defaults, domain links,
     spatial reference and XY tolerance
  3. features (geometry + attributes)
  4. relationship classes (1:1, 1:M)
Subtypes are not supported by GDAL; when ArcGIS `arcpy` is importable they
are applied afterwards by arcpy_enhancer, otherwise the subtype field values
are written and validated and the verification report says so.
"""
from __future__ import annotations

import ctypes
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from shapely import to_wkb

from ..engine.model import TargetFeature
from ..schema.models import DomainDef, FieldDef, LayerDef, SchemaDefinition
from . import gdal_c as G

Progress = Optional[Callable[[float, str], None]]

FIELD_TYPE = {
    "TEXT": (G.OFTString, G.OFSTNone), "INTEGER": (G.OFTInteger, G.OFSTNone),
    "SMALLINTEGER": (G.OFTInteger, G.OFSTInt16), "DOUBLE": (G.OFTReal, G.OFSTNone),
    "FLOAT": (G.OFTReal, G.OFSTFloat32), "DATE": (G.OFTDateTime, G.OFSTNone),
    "GUID": (G.OFTString, G.OFSTUUID),
}
GEOM_TYPE = {"point": G.wkbPoint, "line": G.wkbMultiLineString, "polygon": G.wkbMultiPolygon}
CARD = {"one_to_one": G.GRC_ONE_TO_ONE, "one_to_many": G.GRC_ONE_TO_MANY, "many_to_one": G.GRC_MANY_TO_ONE,
        "many_to_many": G.GRC_MANY_TO_MANY}
RELTYPE = {"association": G.GRT_ASSOCIATION, "composite": G.GRT_COMPOSITE, "aggregation": G.GRT_AGGREGATION}


class GdbWriteError(RuntimeError):
    pass


def _b(s: Optional[str]) -> Optional[bytes]:
    return None if s is None else str(s).encode("utf-8")


def _sql_default(fd: FieldDef) -> Optional[str]:
    if fd.default in (None, ""):
        return None
    if fd.field_type in ("INTEGER", "SMALLINTEGER", "DOUBLE", "FLOAT"):
        try:
            float(fd.default)
            return str(fd.default)
        except (TypeError, ValueError):
            return None
    if fd.field_type == "DATE":
        return None
    return "'" + str(fd.default).replace("'", "''") + "'"


def _create_domain(g, ds, d: DomainDef, log: List[str]) -> bool:
    ftype, fsub = FIELD_TYPE.get(d.field_type or "TEXT", FIELD_TYPE["TEXT"])
    if d.domain_type == "range":
        lo, hi = G.OGRFieldUnion(), G.OGRFieldUnion()
        if ftype == G.OFTInteger:
            lo.Integer = int(d.min_value if d.min_value is not None else -2**31)
            hi.Integer = int(d.max_value if d.max_value is not None else 2**31 - 1)
        else:
            lo.Real = float(d.min_value if d.min_value is not None else -1e308)
            hi.Real = float(d.max_value if d.max_value is not None else 1e308)
        h = g.OGR_RangeFldDomain_Create(_b(d.name), _b(d.description or d.name), ftype, fsub,
                                        ctypes.byref(lo), True, ctypes.byref(hi), True)
    else:
        items = list(d.coded_values.items())
        arr = (G.OGRCodedValue * (len(items) + 1))()
        keep = []
        for i, (code, desc) in enumerate(items):
            c, v = _b(code), _b(desc if desc is not None else code)
            keep += [c, v]
            arr[i].pszCode, arr[i].pszValue = c, v
        arr[len(items)].pszCode, arr[len(items)].pszValue = None, None
        h = g.OGR_CodedFldDomain_Create(_b(d.name), _b(d.description or d.name), ftype, fsub, arr)
    if not h:
        log.append(f"domain {d.name}: could not be created ({g.last_error()})")
        return False
    g.OGR_FldDomain_SetSplitPolicy(h, G.OFDSP_DEFAULT_VALUE)
    g.OGR_FldDomain_SetMergePolicy(h, G.OFDMP_DEFAULT_VALUE)
    err = ctypes.c_char_p()
    ok = g.GDALDatasetAddFieldDomain(ds, h, ctypes.byref(err))
    g.OGR_FldDomain_Destroy(h)  # GDALDatasetAddFieldDomain stores a clone
    if not ok:
        log.append(f"domain {d.name}: {err.value.decode() if err.value else g.last_error()}")
    return bool(ok)


def _create_layer(g, ds, schema: SchemaDefinition, layer: LayerDef, srs, domain_ok: set, log: List[str]):
    opts = ["GEOMETRY_NAME=SHAPE", "FID=OBJECTID", "CREATE_SHAPE_AREA_AND_LENGTH_FIELDS=YES",
            f"XYTOLERANCE={schema.xy_tolerance}"]
    if layer.feature_dataset:
        opts.append(f"FEATURE_DATASET={layer.feature_dataset}")
    if layer.alias and layer.alias != layer.name:
        opts.append(f"LAYER_ALIAS={layer.alias}")
    if layer.description:
        opts.append("DOCUMENTATION=<metadata><dataIdInfo><idAbs>" +
                    layer.description.replace("<", "").replace(">", "")[:500] + "</idAbs></dataIdInfo></metadata>")
    copts = g.cstrlist(opts)
    g.CPLErrorReset()
    lyr = g.GDALDatasetCreateLayer(ds, _b(layer.name), srs, GEOM_TYPE[layer.geometry_type], copts)
    if not lyr:
        raise GdbWriteError(f"feature class {layer.name} could not be created: {g.last_error()}")
    index: Dict[str, int] = {}
    for fd in layer.fields:
        ftype, fsub = FIELD_TYPE.get(fd.field_type, FIELD_TYPE["TEXT"])
        h = g.OGR_Fld_Create(_b(fd.name), ftype)
        if fsub:
            g.OGR_Fld_SetSubType(h, fsub)
        if ftype == G.OFTString and fsub != G.OFSTUUID:
            g.OGR_Fld_SetWidth(h, int(fd.length or 255))
        g.OGR_Fld_SetNullable(h, 1 if fd.nullable else 0)
        if fd.alias:
            g.OGR_Fld_SetAlternativeName(h, _b(fd.alias))
        dflt = _sql_default(fd)
        if dflt is not None:
            g.OGR_Fld_SetDefault(h, _b(dflt))
        if fd.domain and fd.domain in domain_ok:
            g.OGR_Fld_SetDomainName(h, _b(fd.domain))
        rc = g.OGR_L_CreateField(lyr, h, 1)
        g.OGR_Fld_Destroy(h)
        if rc != 0:
            log.append(f"{layer.name}.{fd.name}: field not created ({g.last_error()})")
            continue
    defn = g.OGR_L_GetLayerDefn(lyr)
    for fd in layer.fields:
        i = g.OGR_FD_GetFieldIndex(defn, _b(fd.name))
        if i >= 0:
            index[fd.name] = i
    return lyr, index


def _copy_template(template: str, out_path: str):
    import shutil
    import tempfile
    from .schema_import import extract_gdb_zip
    src = template
    tmp = None
    if template.lower().endswith(".zip"):
        tmp = tempfile.mkdtemp(prefix="gsb_tpl_")
        src = extract_gdb_zip(template, tmp)
    try:
        shutil.copytree(src, out_path)
        for root, _, files in os.walk(out_path):      # zip members may be read-only
            for fn in files:
                os.chmod(os.path.join(root, fn), 0o644)
            if os.path.basename(root).lower().endswith(".lock"):
                pass
        for fn in os.listdir(out_path):
            if fn.lower().endswith(".lock"):
                os.remove(os.path.join(out_path, fn))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def _template_layer(g, ds, layer: LayerDef, log: List[str]):
    lyr = g.GDALDatasetGetLayerByName(ds, _b(layer.name))
    if not lyr:
        log.append(f"{layer.name}: not found in the template geodatabase")
        return None, {}
    defn = g.OGR_L_GetLayerDefn(lyr)
    names = {}
    for i in range(g.OGR_FD_GetFieldCount(defn)):
        names[g.OGR_Fld_GetNameRef(g.OGR_FD_GetFieldDefn(defn, i)).decode("utf-8").upper()] = i
    index = {}
    for fd in layer.fields:
        i = names.get(fd.name.upper())
        if i is None:
            log.append(f"{layer.name}.{fd.name}: not in the template — value not written")   # never add fields
        else:
            index[fd.name] = i
    return lyr, index


def _set_value(g, feat, i: int, v: Any, ftype: str):
    if v is None or v == "":
        g.OGR_F_SetFieldNull(feat, i)
        return
    if ftype in ("INTEGER", "SMALLINTEGER"):
        g.OGR_F_SetFieldInteger64(feat, i, int(v))
    elif ftype in ("DOUBLE", "FLOAT"):
        g.OGR_F_SetFieldDouble(feat, i, float(v))
    elif ftype == "DATE" and isinstance(v, datetime):
        g.OGR_F_SetFieldDateTime(feat, i, v.year, v.month, v.day, v.hour, v.minute, v.second, 0)
    else:
        g.OGR_F_SetFieldString(feat, i, _b(v))


def write_filegdb(out_path: str, schema: SchemaDefinition, features: List[TargetFeature],
                  progress: Progress = None) -> Dict[str, Any]:
    g = G.gdal()
    if not g.driver_can_create("OpenFileGDB"):
        raise GdbWriteError(f"GDAL {g.version()} at {g.path} cannot write File Geodatabases (OpenFileGDB driver)")
    if os.path.exists(out_path):
        raise GdbWriteError(f"output {out_path} already exists — every conversion writes a new geodatabase")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    log: List[str] = []
    template = schema.template_path() if hasattr(schema, "template_path") else None
    if template:
        # the approved template itself is the container: topology, field properties, domains, subtypes and
        # relationship classes stay exactly as delivered by DMT — features are only appended
        _copy_template(template, out_path)
        g.CPLErrorReset()
        ds = g.GDALOpenEx(_b(out_path), G.GDAL_OF_VECTOR | G.GDAL_OF_UPDATE, None, None, None)
        if not ds:
            raise GdbWriteError(f"cannot open the copied template for writing: {g.last_error()}")
        log.append(f"output built on a copy of the approved template {os.path.basename(template)}")
    else:
        drv = g.GDALGetDriverByName(b"OpenFileGDB")
        g.CPLErrorReset()
        ds = g.GDALCreate(drv, _b(out_path), 0, 0, 0, 0, None)
        if not ds:
            raise GdbWriteError(f"cannot create geodatabase: {g.last_error()}")
    srs = None
    counts: Dict[str, int] = {}
    z_undefined: Dict[str, int] = {}     # Z-enabled class, feature without elevation (format stores Z = 0)
    failed_writes: List[Dict[str, Any]] = []
    try:
        epsg = schema.spatial_reference.epsg
        if epsg:
            srs = g.OSRNewSpatialReference(None)
            if g.OSRImportFromEPSG(srs, int(epsg)) != 0:
                raise GdbWriteError(f"EPSG:{epsg} could not be loaded by GDAL")
            g.OSRSetAxisMappingStrategy(srs, 0)  # OAMS_TRADITIONAL_GIS_ORDER
        if template:
            domain_ok = set(g.read_cstrlist(g.GDALDatasetGetFieldDomainNames(ds, None)))
        else:
            if progress: progress(0.02, "Creating domains")
            domain_ok = {d.name for d in schema.domains if _create_domain(g, ds, d, log)}

        by_target: Dict[str, List[TargetFeature]] = {}
        for f in features:
            if not f.failed and f.write and f.geom is not None:
                by_target.setdefault(f.target, []).append(f)
        total = max(1, sum(len(v) for v in by_target.values()))
        done = 0
        for li, layer in enumerate(schema.layers):
            if progress: progress(0.05 + 0.85 * done / total, f"Writing {layer.name}")
            if template:
                lyr, index = _template_layer(g, ds, layer, log)
                if lyr is None:
                    counts[layer.name] = 0
                    for f in by_target.get(layer.name, []):
                        f.write = False
                        failed_writes.append({"fid": f.fid, "target": layer.name, "error": "class not in template"})
                    continue
            else:
                lyr, index = _create_layer(g, ds, schema, layer, srs, domain_ok, log)
            defn = g.OGR_L_GetLayerDefn(lyr)
            gt_ = g.OGR_L_GetGeomType(lyr)
            layer_z = bool(gt_ & 0x80000000) or (gt_ % 10000) // 1000 in (1, 3)
            ftypes = {fd.name: fd.field_type for fd in layer.fields}
            n = 0
            for f in by_target.get(layer.name, []):
                feat = g.OGR_F_Create(defn)
                try:
                    for name, i in index.items():
                        _set_value(g, feat, i, f.attrs.get(name), ftypes[name])
                    dim = 3 if (layer_z and f.geom.has_z) else 2
                    if layer_z and not f.geom.has_z:
                        z_undefined[layer.name] = z_undefined.get(layer.name, 0) + 1
                    wkb = to_wkb(f.geom, output_dimension=dim, byte_order=1)
                    gh = G.P()
                    if g.OGR_G_CreateFromWkb(wkb, None, ctypes.byref(gh), len(wkb)) != 0:
                        raise GdbWriteError("geometry could not be encoded")
                    g.OGR_F_SetGeometryDirectly(feat, gh)
                    g.CPLErrorReset()
                    if g.OGR_L_CreateFeature(lyr, feat) != 0:
                        raise GdbWriteError(g.last_error() or "CreateFeature failed")
                    f.objectid = int(g.OGR_F_GetFID(feat))
                    n += 1
                except GdbWriteError as e:
                    f.write = False
                    failed_writes.append({"fid": f.fid, "target": layer.name, "error": str(e)})
                finally:
                    g.OGR_F_Destroy(feat)
                done += 1
            counts[layer.name] = n

        if template:
            rels = [r.name for r in schema.relationships]      # carried by the template itself
        else:
            if progress: progress(0.92, "Creating relationship classes")
            rels = _create_relationships(g, ds, schema, log)
    finally:
        if srs:
            g.OSRRelease(srs)
        g.GDALClose(ds)
    return {"path": out_path, "counts": counts, "domains_created": sorted(domain_ok), "template": template,
            "relationships_created": rels, "log": log, "failed_writes": failed_writes, "z_undefined": z_undefined,
            "gdal_version": g.version()}


def _create_relationships(g, ds, schema: SchemaDefinition, log: List[str]) -> List[str]:
    created: List[str] = []
    if not schema.relationships:
        return created
    if g.GDALDatasetAddRelationship is None:
        log.append("relationship classes skipped: GDAL < 3.6")
        return created
    for r in schema.relationships:
        if r.cardinality == "many_to_many":
            log.append(f"relationship {r.name}: many-to-many requires an attributed table — skipped")
            continue
        h = g.GDALRelationshipCreate(_b(r.name), _b(r.origin), _b(r.destination), CARD.get(r.cardinality, G.GRC_ONE_TO_MANY))
        if not h:
            log.append(f"relationship {r.name}: create failed ({g.last_error()})")
            continue
        try:
            g.GDALRelationshipSetLeftTableFields(h, g.cstrlist([r.origin_primary_key]))
            g.GDALRelationshipSetRightTableFields(h, g.cstrlist([r.origin_foreign_key]))
            g.GDALRelationshipSetType(h, RELTYPE.get(r.rel_type, G.GRT_ASSOCIATION))
            if r.forward_label:
                g.GDALRelationshipSetForwardPathLabel(h, _b(r.forward_label))
            if r.backward_label:
                g.GDALRelationshipSetBackwardPathLabel(h, _b(r.backward_label))
            g.GDALRelationshipSetRelatedTableType(h, b"features")
            err = ctypes.c_char_p()
            if g.GDALDatasetAddRelationship(ds, h, ctypes.byref(err)):
                created.append(r.name)
            else:
                log.append(f"relationship {r.name}: {err.value.decode() if err.value else g.last_error()}")
        finally:
            g.GDALDestroyRelationship(h)
    return created
