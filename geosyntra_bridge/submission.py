"""
DMT As-Built Data Submission Standards V7 — common submission requirements (section 3) applied to every delivery:

  3.6.1  file naming   EntityName_AssetType_ProjectID_ProjectStage_SubmissionStageVersionNo_Date.gdb
                       e.g. ADM_SW_241LM2_HO_F001_20201116.gdb
  3.8    metadata      mandatory elements + project information (ISO 19115 based) -> <name>_metadata.xml
  3.9    project boundary  topologically clean polygon(s), feature class PROJECT_BOUND
                       (delivered as its own geodatabase: the official template is never altered)
  3.4    positional accuracy (Table 2) — used to judge the DEM used for elevations
  2.1/3.5 vertical datum Ras Ghumays

The project information entered once per job also fills PROJECTID, a NOT NULL, contractor-mandatory field of every
template class, wherever the drawing gives no value.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

ENTITIES = {"ADM": "Abu Dhabi Municipality", "AAM": "Al Ain Municipality", "DRM": "Al Dhafra Region Municipality",
            "DMT": "Department of Municipalities and Transport", "ITC": "Integrated Transport Center"}
ASSET_TYPES = {"RD": "Roads and/or Road Associated Features", "STL": "Streetlighting", "TCS": "Traffic Control Systems",
               "ITS": "Intelligent Transport System", "SW": "Stormwater", "IR": "Irrigation", "SL": "Soft Landscaping",
               "HL": "Hard Landscaping", "MULTI": "Several asset types — one geodatabase per template"}
PROJECT_STAGES = {"DS": "Initial Submission (Planning / Design)", "PC": "Project Completion",
                  "HO": "Final Handing Over", "OM": "Operation and Maintenance works"}
SUBMISSION_STAGES = {"D": "Draft", "F": "Final"}
CONTRACT_TYPES = ["New Infra Dev Project", "Misc. Works Project", "O&M Project"]
PROJECT_FUNCTIONS = ["Roads and Bridges", "Parks and Landscape", "Storm water", "Street Lighting", "Irrigation Network"]
AREA_TYPES = {  # Table 2 — std. dev. of heights: vector data / DEM spot heights (m)
    "city": {"label": "Area 1 — City", "vector_height": 0.30, "dem_spot": 0.25},
    "township": {"label": "Area 2 — Township", "vector_height": 0.35, "dem_spot": 0.35},
    "agricultural": {"label": "Area 3 — Agricultural", "vector_height": 0.35, "dem_spot": 0.35},
    "desert": {"label": "Area 4 — Desert & Mountain", "vector_height": 2.00, "dem_spot": 1.50},
}
SCHEMA_ASSET = {"adm_hl_hardlandscaping": "HL", "adm_sl_softlandscaping": "SL", "adm_ir_irrigation": "IR",
                "adm_rd_roads": "RD", "adm_stl_streetlighting": "STL", "adm_sw_stormwater": "SW", "adm_itc": "ITS",
                "adm_all": "MULTI"}
SCHEMA_FUNCTION = {"HL": "Parks and Landscape", "SL": "Parks and Landscape", "IR": "Irrigation Network",
                   "RD": "Roads and Bridges", "STL": "Street Lighting", "SW": "Storm water"}

# the form: key, label, kind, mandatory (M in 3.8 / needed for 3.6.1), choices
FIELDS: List[Dict[str, Any]] = [
    {"key": "entity", "label": "Entity (project owner)", "kind": "choice", "m": True, "choices": ENTITIES, "group": "naming"},
    {"key": "asset_type", "label": "Asset type", "kind": "choice", "m": True, "choices": ASSET_TYPES, "group": "naming"},
    {"key": "project_id", "label": "Project ID (optional for now)", "kind": "text", "m": False, "group": "naming",
     "placeholder": "e.g. 241LM2",
     "help": "The project number the municipality gave the contract (ADM: from ATS). Letters and digits only. "
             "Goes into the file name and the PROJECTID field of every feature; while empty the name uses "
             "'NOPID' — replace it before the official submission."},
    {"key": "project_stage", "label": "Project stage", "kind": "choice", "m": True, "choices": PROJECT_STAGES, "group": "naming"},
    {"key": "submission_stage", "label": "Submission stage", "kind": "choice", "m": True, "choices": SUBMISSION_STAGES, "group": "naming"},
    {"key": "version", "label": "Version no. (001–999)", "kind": "int", "m": True, "group": "naming"},
    {"key": "submission_date", "label": "Submission date", "kind": "date", "m": True, "group": "naming"},
    {"key": "project_name", "label": "Project name (as per contract)", "kind": "text", "m": True, "group": "project"},
    {"key": "contract_type", "label": "Contract type", "kind": "choice", "m": True, "choices": {c: c for c in CONTRACT_TYPES}, "group": "project"},
    {"key": "project_function", "label": "Project function", "kind": "choice", "m": True, "choices": {c: c for c in PROJECT_FUNCTIONS}, "group": "project"},
    {"key": "consultant_name", "label": "Consultant name", "kind": "text", "m": True, "group": "project"},
    {"key": "consultant_rep", "label": "Consultant representative (PM)", "kind": "text", "m": True, "group": "project"},
    {"key": "contractor_name", "label": "Contractor name", "kind": "text", "m": True, "group": "project"},
    {"key": "contractor_rep", "label": "Contractor representative (PM)", "kind": "text", "m": True, "group": "project"},
    {"key": "start_date", "label": "Start date", "kind": "date", "m": True, "group": "project"},
    {"key": "completion_date", "label": "Completion date", "kind": "date", "m": True, "group": "project"},
    {"key": "pac_date", "label": "Preliminary Acceptance Certificate date", "kind": "date", "m": True, "group": "project"},
    {"key": "fac_date", "label": "Final Acceptance Certificate date", "kind": "date", "m": True, "group": "project"},
    {"key": "operation_cost", "label": "Operation cost (AED / year)", "kind": "number", "m": True, "group": "project"},
    {"key": "maintenance_cost", "label": "Maintenance cost (AED / year)", "kind": "number", "m": True, "group": "project"},
    {"key": "total_cost", "label": "Total project cost (AED)", "kind": "number", "m": True, "group": "project"},
    {"key": "remarks", "label": "Remarks", "kind": "text", "m": False, "group": "project"},
    {"key": "metadata_contact", "label": "Metadata point of contact (name, organisation, e-mail)", "kind": "text", "m": True, "group": "metadata"},
    {"key": "dataset_title", "label": "Dataset title", "kind": "text", "m": True, "group": "metadata"},
    {"key": "dataset_abstract", "label": "Dataset abstract", "kind": "text", "m": True, "group": "metadata"},
    {"key": "dataset_language", "label": "Dataset language", "kind": "text", "m": True, "group": "metadata"},
    {"key": "usage", "label": "Usage information & conditions", "kind": "text", "m": True, "group": "metadata"},
    {"key": "security", "label": "Security constraints", "kind": "text", "m": True, "group": "metadata"},
    {"key": "area_type", "label": "Area type (Table 2 accuracy)", "kind": "choice", "m": True,
     "choices": {k: v["label"] for k, v in AREA_TYPES.items()}, "group": "metadata"},
]


def defaults(schema, source_file: str) -> Dict[str, Any]:
    at = SCHEMA_ASSET.get(schema.schema_id, "")
    return {"entity": "ADM" if schema.schema_id.startswith("adm_") else "", "asset_type": at,
            "project_stage": "HO", "submission_stage": "D", "version": 1, "submission_date": date.today().isoformat(),
            "project_function": SCHEMA_FUNCTION.get(at, ""), "dataset_language": "English", "area_type": "city",
            "dataset_title": f"{ASSET_TYPES.get(at, schema.display_name)} as-built data",
            "dataset_abstract": f"As-built {ASSET_TYPES.get(at, schema.display_name).lower()} assets converted from "
                                f"{os.path.basename(source_file)} into the {schema.display_name} template "
                                f"({schema.source_template or schema.schema_id}).",
            "boundary": {"mode": "derived", "buffer_m": 5.0}}


def _ymd(v: Any) -> Optional[str]:
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    return None


def check(info: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """-> (missing mandatory labels, format errors)"""
    missing, errors = [], []
    for f in FIELDS:
        v = info.get(f["key"])
        if f["m"] and (v is None or str(v).strip() == ""):
            missing.append(f["label"])
            continue
        if v in (None, ""):
            continue
        if f["kind"] == "choice" and str(v) not in f["choices"]:
            errors.append(f"{f['label']}: '{v}' is not one of {', '.join(f['choices'])}")
        if f["kind"] == "date" and not _ymd(v):
            errors.append(f"{f['label']}: '{v}' is not a date (YYYY-MM-DD)")
        if f["kind"] == "number":
            try:
                float(v)
            except (TypeError, ValueError):
                errors.append(f"{f['label']}: '{v}' is not a number")
    pid = str(info.get("project_id") or "")
    if pid and not re.fullmatch(r"[A-Za-z0-9\-]{1,40}", pid):
        errors.append("Project ID may only contain letters, digits and '-' (the '_' separates the name components)")
    try:
        if info.get("version") not in (None, "") and not 1 <= int(info["version"]) <= 999:
            errors.append("Version no. must be 1–999")
    except (TypeError, ValueError):
        errors.append("Version no. must be a number 1–999")
    return missing, errors


PID_PLACEHOLDER = "NOPID"   # Project ID is optional for now: the name keeps its 3.6.1 shape until the real ID is entered


def submission_name(info: Dict[str, Any]) -> Optional[str]:
    """3.6.1 name without extension, or None when a naming component is missing / invalid."""
    try:
        parts = [info["entity"], info["asset_type"], str(info.get("project_id") or "").strip() or PID_PLACEHOLDER, info["project_stage"],
                 f"{info['submission_stage']}{int(info['version']):03d}", _ymd(info["submission_date"])]
    except (KeyError, TypeError, ValueError):
        return None
    if not all(parts) or not re.fullmatch(r"[A-Za-z0-9\-]+", parts[2]):
        return None
    return "_".join(parts)


NAME_RX = re.compile(r"^(DMT|ADM|AAM|DRM|ITC)_(RD|STL|TCS|ITS|SW|IR|SL|HL|MULTI)_[A-Za-z0-9\-]+_(DS|PC|HO|OM)_[DF]\d{3}_\d{8}$")


# ------------------------------------------------------------------ project boundary (3.9)
BOUNDARY_LAYER = re.compile(r"project[\s_\-]*bound|limit[\s_\-]*of[\s_\-]*work|\blow\b|site[\s_\-]*bound|work[\s_\-]*limit|"
                            r"contract[\s_\-]*(?:limit|bound)|scope[\s_\-]*bound|PROJECT_BOUND", re.I)


def boundary_candidates(doc) -> List[Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for e in doc.entities:
        if e.kind in ("polygon", "line") and BOUNDARY_LAYER.search(e.layer):
            d = out.setdefault(e.layer, {"layer": e.layer, "polygons": 0, "lines": 0})
            d["polygons" if e.kind == "polygon" else "lines"] += 1
    return sorted(out.values(), key=lambda d: -(d["polygons"] * 10 + d["lines"]))


def build_boundary(doc, features, cp, cfg: Dict[str, Any]) -> Tuple[Any, str]:
    """-> (shapely polygon/multipolygon in target CRS, description). Topologically clean (unioned, valid)."""
    from shapely.geometry import MultiPolygon, Polygon
    from shapely.ops import unary_union
    from shapely.validation import make_valid
    mode = (cfg or {}).get("mode") or "derived"
    if mode == "layer" and cfg.get("layer"):
        polys = []
        for e in doc.entities:
            if e.layer != cfg["layer"] or len(e.coords) < 3:
                continue
            pts = cp.xy(e.coords)
            if e.kind == "polygon" or (e.kind == "line" and len(pts) >= 4 and
                                        abs(pts[0][0] - pts[-1][0]) + abs(pts[0][1] - pts[-1][1]) < 0.05):
                pg = Polygon(pts)
                polys.append(pg if pg.is_valid else make_valid(pg))
        if not polys:
            return None, f"layer '{cfg['layer']}' has no closed outline"
        g = unary_union(polys)
        desc = f"from CAD layer '{cfg['layer']}' ({len(polys)} outline(s))"
    else:
        buf = float((cfg or {}).get("buffer_m") or 5.0)
        geoms = [f.geom for f in features if not f.failed and f.write and f.geom is not None]
        if not geoms:
            return None, "no features to derive the boundary from"
        g = unary_union([x.buffer(buf, 8) for x in geoms]).convex_hull
        desc = f"derived: convex hull of all delivered features + {buf:g} m — verify against the actual work area"
    g = make_valid(g)
    if g.geom_type == "GeometryCollection":
        g = unary_union([x for x in g.geoms if x.geom_type in ("Polygon", "MultiPolygon")])
    if g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
        return None, "boundary could not be built as a polygon"
    return g, desc


def write_boundary_gdb(path: str, geom, epsg: int, info: Dict[str, Any], desc: str) -> Dict[str, Any]:
    """PROJECT_BOUND feature class in its own File Geodatabase."""
    import ctypes
    from shapely import to_wkb
    from shapely.geometry import MultiPolygon
    from .gdb import gdal_c as G
    g = G.gdal()
    drv = g.GDALGetDriverByName(b"OpenFileGDB")
    ds = g.GDALCreate(drv, path.encode("utf-8"), 0, 0, 0, 0, None)
    if not ds:
        raise RuntimeError(f"cannot create {path}: {g.last_error()}")
    srs = g.OSRNewSpatialReference(None)
    g.OSRImportFromEPSG(srs, int(epsg))
    g.OSRSetAxisMappingStrategy(srs, 0)
    try:
        lyr = g.GDALDatasetCreateLayer(ds, b"PROJECT_BOUND", srs, 6, g.cstrlist(["GEOMETRY_NAME=SHAPE"]))  # wkbMultiPolygon
        fields = [("PROJECTID", 4, 50), ("PROJECT_NAME", 4, 255), ("ENTITY", 4, 10), ("ASSET_TYPE", 4, 10),
                  ("SOURCE", 4, 255), ("AREA_M2", 2, 0)]
        for name, t, w in fields:
            h = g.OGR_Fld_Create(name.encode(), t)
            if w:
                g.OGR_Fld_SetWidth(h, w)
            g.OGR_L_CreateField(lyr, h, 1)
            g.OGR_Fld_Destroy(h)
        defn = g.OGR_L_GetLayerDefn(lyr)
        parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        f = g.OGR_F_Create(defn)
        vals = {"PROJECTID": info.get("project_id"), "PROJECT_NAME": info.get("project_name"),
                "ENTITY": info.get("entity"), "ASSET_TYPE": info.get("asset_type"), "SOURCE": desc}
        for k, v in vals.items():
            if v:
                g.OGR_F_SetFieldString(f, g.OGR_FD_GetFieldIndex(defn, k.encode()), str(v)[:255].encode("utf-8"))
        g.OGR_F_SetFieldDouble(f, g.OGR_FD_GetFieldIndex(defn, b"AREA_M2"), round(geom.area, 2))
        wkb = to_wkb(MultiPolygon(parts), output_dimension=2, byte_order=1)
        gh = G.P()
        g.OGR_G_CreateFromWkb(wkb, None, ctypes.byref(gh), len(wkb))
        g.OGR_F_SetGeometryDirectly(f, gh)
        if g.OGR_L_CreateFeature(lyr, f) != 0:
            raise RuntimeError(g.last_error())
        g.OGR_F_Destroy(f)
    finally:
        g.OSRRelease(srs)
        g.GDALClose(ds)
    return {"area_m2": round(geom.area, 2), "parts": len(parts), "source": desc}


# ------------------------------------------------------------------ metadata (3.8)
TOPIC = {"RD": "transportation", "STL": "utilitiesCommunication", "TCS": "transportation", "ITS": "transportation",
         "SW": "inlandWaters", "IR": "utilitiesCommunication", "SL": "environment", "HL": "structure"}


def metadata_xml(info: Dict[str, Any], schema, bundle: Dict[str, Any], name: str, bbox_ll: Optional[List[float]],
                 vertical: str) -> str:
    """ISO 19115-1 based metadata (ISO 19139 encoding) with the DMT mandatory elements and project information."""
    def t(v):
        return escape("" if v is None else str(v))

    def cs(v):
        return f"<gco:CharacterString>{t(v)}</gco:CharacterString>"
    today = date.today().isoformat()
    s = bundle.get("summary", {})
    qc = bundle.get("dmt_qc") or {}
    ver = bundle.get("verification") or {}
    el = bundle.get("elevation") or {}
    dem = (el.get("dem") or {}) if el.get("status") == "done" else {}
    fields_desc = []
    for l in schema.layers:
        if s.get("features_by_class", {}).get(l.name):
            for fd in l.fields:
                if fd.description:
                    fields_desc.append(f"{l.name}.{fd.name} ({fd.field_type}): {fd.description}"
                                       + (f" Values: {', '.join(fd.allowed_values[:12])}" if fd.allowed_values else ""))
    quality = (f"Logical consistency: DMT Data Quality Control replica {'passed' if qc.get('passed') else str(len(qc.get('errors') or [])) + ' error(s)'}; "
               f"output verification {'passed' if ver.get('passed') else 'see report'}. Completeness: {s.get('features_imported', 0)} "
               f"features delivered, {s.get('features_failed', 0)} not delivered. Positional accuracy: per CAD as-built survey. "
               + (f"Elevations: {dem.get('label')} ({dem.get('resolution_m')} m, {dem.get('vertical_label')}); output vertical "
                  f"reference {vertical}." if dem else "Elevations: CAD levels only."))
    lineage = (f"Converted by GeoSyntra Bridge from CAD drawing '{bundle.get('source_file')}' into the approved template "
               f"{schema.source_template or schema.schema_id}; CAD clean-up, layer mapping, label/attribute extraction, "
               f"validation and DMT QC replica as recorded in the processing report."
               + (f" Elevations sampled from {dem.get('label')} ({dem.get('provider')})." if dem else ""))
    west, south, east, north = (bbox_ll or [None] * 4)
    project = "".join(f"<dmt:{k}>{t(info.get(k))}</dmt:{k}>" for k in (
        "project_id", "contract_type", "project_name", "project_function", "consultant_name", "consultant_rep",
        "contractor_name", "contractor_rep", "start_date", "completion_date", "fac_date", "pac_date",
        "operation_cost", "maintenance_cost", "total_cost", "remarks"))
    attr = "".join(f"<gmd:attributeDescription>{cs(x)}</gmd:attributeDescription>" for x in fields_desc[:2000])
    extent = (f"<gmd:extent><gmd:EX_Extent><gmd:geographicElement><gmd:EX_GeographicBoundingBox>"
              f"<gmd:westBoundLongitude><gco:Decimal>{west}</gco:Decimal></gmd:westBoundLongitude>"
              f"<gmd:eastBoundLongitude><gco:Decimal>{east}</gco:Decimal></gmd:eastBoundLongitude>"
              f"<gmd:southBoundLatitude><gco:Decimal>{south}</gco:Decimal></gmd:southBoundLatitude>"
              f"<gmd:northBoundLatitude><gco:Decimal>{north}</gco:Decimal></gmd:northBoundLatitude>"
              f"</gmd:EX_GeographicBoundingBox></gmd:geographicElement></gmd:EX_Extent></gmd:extent>") if bbox_ll else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<gmd:MD_Metadata xmlns:gmd="http://www.isotc211.org/2005/gmd" xmlns:gco="http://www.isotc211.org/2005/gco"
  xmlns:dmt="urn:dmt:asbuilt:submission:v7">
  <gmd:fileIdentifier>{cs(name)}</gmd:fileIdentifier>
  <gmd:language>{cs(info.get('dataset_language') or 'English')}</gmd:language>
  <gmd:characterSet><gmd:MD_CharacterSetCode codeListValue="utf8">utf8</gmd:MD_CharacterSetCode></gmd:characterSet>
  <gmd:contact><gmd:CI_ResponsibleParty><gmd:individualName>{cs(info.get('metadata_contact'))}</gmd:individualName>
    <gmd:role><gmd:CI_RoleCode codeListValue="pointOfContact">pointOfContact</gmd:CI_RoleCode></gmd:role></gmd:CI_ResponsibleParty></gmd:contact>
  <gmd:dateStamp><gco:Date>{today}</gco:Date></gmd:dateStamp>
  <gmd:metadataStandardName>{cs('ISO 19115-1:2014 / DMT As-Built Data Submission Standards V7, section 3.8')}</gmd:metadataStandardName>
  <gmd:spatialRepresentationInfo><gmd:MD_VectorSpatialRepresentation><gmd:topologyLevel>
    <gmd:MD_TopologyLevelCode codeListValue="geometryOnly">geometryOnly</gmd:MD_TopologyLevelCode></gmd:topologyLevel>
  </gmd:MD_VectorSpatialRepresentation></gmd:spatialRepresentationInfo>
  <gmd:referenceSystemInfo><gmd:MD_ReferenceSystem><gmd:referenceSystemIdentifier><gmd:RS_Identifier>
    <gmd:code>{cs(f"EPSG:{schema.spatial_reference.epsg} (WGS 84 / UTM, ITRF2000.0); vertical: {vertical}")}</gmd:code>
  </gmd:RS_Identifier></gmd:referenceSystemIdentifier></gmd:MD_ReferenceSystem></gmd:referenceSystemInfo>
  <gmd:identificationInfo><gmd:MD_DataIdentification>
    <gmd:citation><gmd:CI_Citation><gmd:title>{cs(info.get('dataset_title'))}</gmd:title>
      <gmd:date><gmd:CI_Date><gmd:date><gco:Date>{t((_ymd(info.get('submission_date')) or '')[:4] + '-' + (_ymd(info.get('submission_date')) or '')[4:6] + '-' + (_ymd(info.get('submission_date')) or '')[6:])}</gco:Date></gmd:date>
        <gmd:dateType><gmd:CI_DateTypeCode codeListValue="creation">creation</gmd:CI_DateTypeCode></gmd:dateType></gmd:CI_Date></gmd:date>
    </gmd:CI_Citation></gmd:citation>
    <gmd:abstract>{cs(info.get('dataset_abstract'))}</gmd:abstract>
    <gmd:resourceSpecificUsage><gmd:MD_Usage><gmd:specificUsage>{cs(info.get('usage'))}</gmd:specificUsage></gmd:MD_Usage></gmd:resourceSpecificUsage>
    <gmd:resourceConstraints><gmd:MD_SecurityConstraints><gmd:userNote>{cs(info.get('security'))}</gmd:userNote></gmd:MD_SecurityConstraints></gmd:resourceConstraints>
    <gmd:spatialRepresentationType><gmd:MD_SpatialRepresentationTypeCode codeListValue="vector">vector</gmd:MD_SpatialRepresentationTypeCode></gmd:spatialRepresentationType>
    <gmd:language>{cs(info.get('dataset_language') or 'English')}</gmd:language>
    <gmd:topicCategory><gmd:MD_TopicCategoryCode>{TOPIC.get(info.get('asset_type'), 'structure')}</gmd:MD_TopicCategoryCode></gmd:topicCategory>
    {extent}
  </gmd:MD_DataIdentification></gmd:identificationInfo>
  <gmd:contentInfo><gmd:MD_FeatureCatalogueDescription>
    <gmd:featureCatalogueCitation><gmd:CI_Citation><gmd:title>{cs(f"{schema.display_name} — {schema.source_template}; DMT data dictionary")}</gmd:title></gmd:CI_Citation></gmd:featureCatalogueCitation>
    {attr}
  </gmd:MD_FeatureCatalogueDescription></gmd:contentInfo>
  <gmd:portrayalCatalogueInfo><gmd:MD_PortrayalCatalogueReference><gmd:portrayalCatalogueCitation><gmd:CI_Citation>
    <gmd:title>{cs('Symbology of the DMT template (no separate portrayal catalogue)')}</gmd:title></gmd:CI_Citation></gmd:portrayalCatalogueCitation>
  </gmd:MD_PortrayalCatalogueReference></gmd:portrayalCatalogueInfo>
  <gmd:applicationSchemaInfo><gmd:MD_ApplicationSchemaInformation><gmd:name><gmd:CI_Citation>
    <gmd:title>{cs(schema.source_template or schema.schema_id)}</gmd:title></gmd:CI_Citation></gmd:name>
    <gmd:schemaLanguage>{cs('Esri File Geodatabase')}</gmd:schemaLanguage><gmd:constraintLanguage>{cs('Esri topology rules, attribute domains')}</gmd:constraintLanguage>
  </gmd:MD_ApplicationSchemaInformation></gmd:applicationSchemaInfo>
  <gmd:dataQualityInfo><gmd:DQ_DataQuality><gmd:report><gmd:DQ_DomainConsistency><gmd:result><gmd:DQ_ConformanceResult>
    <gmd:explanation>{cs(quality)}</gmd:explanation><gmd:pass><gco:Boolean>{'true' if qc.get('passed') and ver.get('passed') else 'false'}</gco:Boolean></gmd:pass>
  </gmd:DQ_ConformanceResult></gmd:result></gmd:DQ_DomainConsistency></gmd:report>
    <gmd:lineage><gmd:LI_Lineage><gmd:statement>{cs(lineage)}</gmd:statement></gmd:LI_Lineage></gmd:lineage>
  </gmd:DQ_DataQuality></gmd:dataQualityInfo>
  <dmt:ProjectInformation entity="{t(info.get('entity'))}" assetType="{t(info.get('asset_type'))}" projectStage="{t(info.get('project_stage'))}"
    submissionStage="{t(info.get('submission_stage'))}" version="{t(info.get('version'))}">{project}</dmt:ProjectInformation>
</gmd:MD_Metadata>
"""
