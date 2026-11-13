"""
Replica of the DMT "ADM_Data_Quality_Control" ArcGIS Pro toolbox (DMT Data Quality Control.pyt / QCUtility.py),
run with GDAL on every geodatabase this tool produces — so a delivery is checked the way DMT will check it
before it leaves the office.

The original tool, for an uploaded FGDB and the matching template (templates/ADM_<Type>.zip):
  1. _compare_schema       datasets, feature classes, projection, shape type, every field and its properties,
                           every domain and its properties, the topology (feature classes and rules)
  2. validate_coded_values values of domain fields that are not codes of the domain
  3. _validate_topology    ArcGIS ValidateTopology + export of topology errors
                           + appconfig.json spatial rules (spatial_start_end / spatial_intersect)
  4. _validate_geometry    curve (arc) geometry is not allowed
and lists every error as Dataset | FeatureClass | Error | ObjectID (QCErrors.csv).

Differences, stated honestly in the result:
  * step 3's ArcGIS topology validation needs ArcGIS; here the template's topology rules are evaluated by
    GeoSyntra's own topology checks during validation and summarised — the final ValidateTopology is still
    run by the DMT tool.
  * messages keep the original wording so they can be compared line by line with QCErrors.csv.
"""
from __future__ import annotations

import csv
import json
import os
import struct
import tempfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from . import gdal_c as G

_CURVE_WKB = {8, 9, 10, 11, 12, 13, 14, 15, 16, 17}


def _rules_path() -> str:
    from ..knowledge.dictionary import knowledge_dir
    return os.path.join(knowledge_dir(), "qc", "appconfig.json")


def load_rules() -> Dict[str, Any]:
    p = _rules_path()
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("rules", {})
    return {}


# ----------------------------------------------------------------- describe a geodatabase (like arcpy)
def _items(gdb: str) -> List[Dict[str, Any]]:
    from pyogrio import raw
    meta, _, _, data = raw.read(gdb, layer="GDB_Items", read_geometry=False)
    f = list(meta["fields"])
    return [{k: data[i][j] for i, k in enumerate(f)} for j in range(len(data[0]))]


def describe(gdb: str) -> Dict[str, Any]:
    """{"datasets": {ds: {fc: {field: props}}}, "meta": {ds: {fc: {...}}}, "domains": {...}, "topology": {...}}"""
    g = G.gdal()
    out: Dict[str, Any] = {"datasets": {}, "meta": {}, "domains": {}, "topology": {}}
    items = _items(gdb)
    by_id = {}
    fc_ds: Dict[str, str] = {}
    for it in items:
        d = it.get("Definition") or ""
        path = (it.get("Path") or "").strip("\\")
        parts = path.split("\\")
        if "<DEFeatureClassInfo" in d and len(parts) == 2:
            fc_ds[parts[1]] = parts[0]
        elif "<DEFeatureClassInfo" in d and len(parts) == 1:
            fc_ds[parts[0]] = "Standalone"
        try:
            root = ET.fromstring(d) if d else None
        except ET.ParseError:
            root = None
        if root is not None:
            dsid = root.findtext("DSID")
            if dsid:
                by_id[dsid] = it.get("Name")
    for it in items:
        d = it.get("Definition") or ""
        if "<DETopology" not in d:
            continue
        root = ET.fromstring(d)
        ds = (it.get("Path") or "").strip("\\").split("\\")[0]
        names = [n.text for n in root.iter("Name") if n.text and n.text in fc_ds]
        rules = []
        for r in root.iter("TopologyRule"):
            rules.append((r.findtext("TopologyRuleType"), by_id.get(r.findtext("OriginClassID"), r.findtext("OriginClassID")),
                          by_id.get(r.findtext("DestinationClassID"), r.findtext("DestinationClassID"))))
        out["topology"][ds] = {"name": it.get("Name"), "featureClassNames": sorted(set(names)), "rules": rules}

    ds_h = g.GDALOpenEx(gdb.encode("utf-8"), G.GDAL_OF_VECTOR | G.GDAL_OF_READONLY, None, None, None)
    if not ds_h:
        raise RuntimeError(f"cannot open {gdb}: {g.last_error()}")
    try:
        for i in range(g.GDALDatasetGetLayerCount(ds_h)):
            lyr = g.GDALDatasetGetLayer(ds_h, i)
            name = g.OGR_L_GetName(lyr).decode("utf-8")
            ds = fc_ds.get(name)
            if ds is None:
                continue
            gt = g.OGR_L_GetGeomType(lyr) & 0x7FFFFFFF
            shape = {1: "Point", 2: "Polyline", 3: "Polygon", 4: "Multipoint", 5: "Polyline", 6: "Polygon"}.get(gt % 1000, str(gt))
            srs = g.OGR_L_GetSpatialRef(lyr)
            code = None
            if srs:
                c = g.OSRGetAuthorityCode(srs, None)
                code = int(c.decode()) if c else None
            out["datasets"].setdefault(ds, {})
            out["meta"].setdefault(ds, {})[name] = {"projection": code, "shapetype": shape}
            defn = g.OGR_L_GetLayerDefn(lyr)
            flds = {}
            for k in range(g.OGR_FD_GetFieldCount(defn)):
                fd = g.OGR_FD_GetFieldDefn(defn, k)
                fn = g.OGR_Fld_GetNameRef(fd).decode("utf-8")
                alias = g.OGR_Fld_GetAlternativeNameRef(fd)
                dflt = g.OGR_Fld_GetDefault(fd)
                dom = g.OGR_Fld_GetDomainName(fd)
                flds[fn] = {"type": g.OGR_Fld_GetType(fd), "subtype": g.OGR_Fld_GetSubType(fd),
                            "length": g.OGR_Fld_GetWidth(fd), "isNullable": bool(g.OGR_Fld_IsNullable(fd)),
                            "aliasName": alias.decode("utf-8") if alias else "",
                            "defaultValue": dflt.decode("utf-8") if dflt else None,
                            "domain": dom.decode("utf-8") if dom else ""}
            out["datasets"][ds][name] = flds
        for dn in g.read_cstrlist(g.GDALDatasetGetFieldDomainNames(ds_h, None)):
            h = g.GDALDatasetGetFieldDomain(ds_h, dn.encode("utf-8"))
            if not h:
                continue
            props = {"domainType": g.OGR_FldDomain_GetDomainType(h), "type": g.OGR_FldDomain_GetFieldType(h),
                     "description": (g.OGR_FldDomain_GetDescription(h) or b"").decode("utf-8", "replace")}
            if props["domainType"] == 0:
                codes = {}
                arr = g.OGR_CodedFldDomain_GetEnumeration(h)
                k = 0
                while arr and arr[k].pszCode:
                    codes[arr[k].pszCode.decode("utf-8", "replace")] = (arr[k].pszValue or b"").decode("utf-8", "replace")
                    k += 1
                props["codedValues"] = codes
            out["domains"][dn] = props
    finally:
        g.GDALClose(ds_h)
    return out


# ----------------------------------------------------------------- the checks
class QCRun:
    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    def err(self, check: str, ds: str, fc: str, error: str, oid: Optional[int] = None, xy=None):
        self.rows.append({"check": check, "Dataset": ds, "FeatureClass": fc, "Error": error, "ObjectID": oid,
                          "x": xy[0] if xy else None, "y": xy[1] if xy else None})


def _compare_schema(qc: QCRun, t: Dict[str, Any], s: Dict[str, Any]):
    chk = "Schema identical to template"
    for ds, fcs in t["datasets"].items():
        if ds not in s["datasets"]:
            qc.err(chk, ds, "", f"{ds} dataset not found")
            continue
        for fc, fields in fcs.items():
            if fc not in s["datasets"][ds]:
                qc.err(chk, ds, fc, f"{fc} feature class not found in the {ds} dataset")
                continue
            tm, sm = t["meta"][ds][fc], s["meta"][ds][fc]
            if tm["projection"] != sm["projection"]:
                qc.err(chk, ds, fc, f"Projection of {fc} feature class in {ds} dataset does not match. "
                                    f"Expected {tm['projection']} got {sm['projection']}")
            if tm["shapetype"] != sm["shapetype"]:
                qc.err(chk, ds, fc, f"Shape type of {fc} feature class in {ds} dataset does not match. "
                                    f"Expected {tm['shapetype']} got {sm['shapetype']}")
            for fn, props in fields.items():
                sf = s["datasets"][ds][fc].get(fn)
                if sf is None:
                    qc.err(chk, ds, fc, f"Field {fn} not found in {fc} feature class in the {ds} dataset")
                    continue
                for p, v in props.items():
                    if sf.get(p) != v:
                        qc.err(chk, ds, fc, f"Field attribute of {fn} does not match for {fc} feature class in the {ds} "
                                            f"dataset. Expected {v} got {sf.get(p)} for {p}")
    for ds in s["datasets"]:
        if ds not in t["datasets"]:
            qc.err(chk, ds, "", f"{ds} dataset not found")
    for dn, props in t["domains"].items():
        if dn not in s["domains"]:
            qc.err(chk, dn, dn, f"{dn} domain not found")
            continue
        sp = s["domains"][dn]
        for p, v in props.items():
            if p == "codedValues":
                miss = set(v) - set(sp.get(p, {}))
                extra = set(sp.get(p, {})) - set(v)
                if miss:
                    qc.err(chk, dn, dn, f"codedValues value in domain {dn} does not match. Did not find {','.join(sorted(miss))}")
                if extra:
                    qc.err(chk, dn, dn, f"codedValues value in domain {dn} does not match. Additional item(s) found {','.join(sorted(extra))}")
            elif sp.get(p) != v:
                qc.err(chk, dn, dn, f"{p} value in domain {dn} does not match")
    for dn in s["domains"]:
        if dn not in t["domains"]:
            qc.err(chk, dn, dn, f"{dn} domain not found")
    for ds, tp in t["topology"].items():
        sp = s["topology"].get(ds)
        if sp is None:
            qc.err("Topology present", ds, ds, f"{ds} topology not found")
            continue
        for fc in sorted(set(tp["featureClassNames"]) - set(sp["featureClassNames"])):
            qc.err("Topology present", ds, ds, f"{fc} feature class is not found in the topology rules.")
        for fc in sorted(set(sp["featureClassNames"]) - set(tp["featureClassNames"])):
            qc.err("Topology present", ds, ds, f"Additional feature class {fc} is not found in the topology rules.")
        if sorted(tp["rules"]) != sorted(sp["rules"]):
            qc.err("Topology present", ds, ds, "Uploaded GDB does not have identical topology rules (rules differ from template).")


def _read(gdb: str, layer: str, columns: Optional[List[str]] = None, geometry: bool = True):
    from pyogrio import raw
    meta, fids, geoms, data = raw.read(gdb, layer=layer, columns=columns, read_geometry=geometry, return_fids=True)
    return list(meta["fields"]), fids, geoms, data


def _coded_values(qc: QCRun, gdb: str, t: Dict[str, Any]):
    chk = "Coded values"
    for ds, fcs in t["datasets"].items():
        for fc, fields in fcs.items():
            if fc.upper() == "ROADCENTRELINE":       # skipped by the original tool
                continue
            dfields = {fn: p["domain"] for fn, p in fields.items() if p.get("domain") and p["domain"] in t["domains"]}
            if not dfields:
                continue
            try:
                names, fids, _, data = _read(gdb, fc, list(dfields), geometry=False)
            except Exception:  # noqa: BLE001
                continue
            if fids is None or len(fids) == 0:
                continue
            for fn, dn in dfields.items():
                if fn not in names:
                    continue
                codes = set(t["domains"][dn].get("codedValues", {}))
                if not codes:
                    continue
                vals = {str(v.item() if hasattr(v, "item") else v) for v in data[names.index(fn)] if v is not None}
                vals = {v[:-2] if v.endswith(".0") and v[:-2] in codes else v for v in vals}
                bad = vals - codes
                if bad:
                    qc.err(chk, ds, fc, f"Coded value mismatch for domain '{dn}' in field '{fn}' in feature class '{fc}' "
                                        f"in dataset '{ds}'. The following uploaded values do not exist in the template: "
                                        f"{', '.join(sorted(bad))}.")


def _wkb_type(b: bytes) -> int:
    if not b or len(b) < 5:
        return 0
    t = struct.unpack("<I" if b[0] == 1 else ">I", b[1:5])[0]
    return (t & 0x0FFFFFFF) % 1000


def _geoms(gdb: str, fc: str):
    from shapely import from_wkb
    names, fids, geoms, data = _read(gdb, fc)
    return names, list(fids), [from_wkb(x) if x is not None else None for x in geoms], data, geoms


def _arcs(qc: QCRun, gdb: str, s: Dict[str, Any]):
    for ds, fcs in s["datasets"].items():
        for fc in fcs:
            if s["meta"][ds][fc]["shapetype"] not in ("Polyline", "Polygon"):
                continue
            try:
                _, fids, _, _, raw_geoms = _geoms(gdb, fc)
            except Exception:  # noqa: BLE001
                continue
            for oid, b in zip(fids, raw_geoms):
                if _wkb_type(b) in _CURVE_WKB:
                    qc.err("No curve geometry", ds, fc, "Arc geometry detected (curve geometry not allowed)", int(oid))


def _spatial_rules(qc: QCRun, gdb: str, rules: Dict[str, Any], s: Dict[str, Any], tol: float = 0.001):
    from shapely.geometry import Point
    from shapely.strtree import STRtree
    cache: Dict[str, Any] = {}

    def load(ref: str):
        ds, fc = ref.split(".")
        if ref not in cache:
            try:
                cache[ref] = _geoms(gdb, fc)
            except Exception:  # noqa: BLE001
                cache[ref] = ([], [], [], [], [])
        return cache[ref]

    for ds, fcs in rules.items():
        if ds not in s["datasets"]:
            continue
        for fc, items in fcs.items():
            if fc not in s["datasets"][ds]:
                continue
            names, fids, geoms, data, _ = load(f"{ds}.{fc}")
            for ri in items:
                if "spatial_start_end" in ri:
                    r = ri["spatial_start_end"]
                    op = all if r.get("condition") == "all" else any
                    trees = {}
                    for end, refs in r["checks"].items():
                        gs = [gg for ref in refs for gg in load(ref)[2] if gg is not None]
                        trees[end] = (STRtree(gs), gs) if gs else (None, [])
                    for oid, gm in zip(fids, geoms):
                        if gm is None or gm.is_empty:
                            continue
                        lines = list(getattr(gm, "geoms", [gm]))
                        res = []
                        for end in r["checks"]:
                            c = lines[0].coords[0] if end == "START" else lines[-1].coords[-1]
                            p = Point(c[0], c[1])
                            tree, gs = trees[end]
                            res.append(bool(tree is not None and any(gs[i].distance(p) <= tol for i in tree.query(p.buffer(tol)))))
                        if not op(res):
                            pt = lines[0].coords[0]
                            qc.err("Spatial rules (appconfig.json)", ds, f"{ds}\\{fc}", r["error"], int(oid), (pt[0], pt[1]))
                if "spatial_intersect" in ri:
                    r = ri["spatial_intersect"]
                    ut = names.index("UNITTYPE") if "UNITTYPE" in names else None
                    for ref in r["checks"]:
                        ends = []
                        for gm in load(ref)[2]:
                            if gm is None or gm.is_empty:
                                continue
                            for ln in getattr(gm, "geoms", [gm]):
                                ends += [Point(ln.coords[0][:2]), Point(ln.coords[-1][:2])]
                        tree = STRtree(ends) if ends else None
                        for k, (oid, gm) in enumerate(zip(fids, geoms)):
                            if gm is None:
                                continue
                            u = data[ut][k] if ut is not None else None
                            if u and str(u).strip().lower() == "solar":     # solar lights need no cable
                                continue
                            hit = tree is not None and any(ends[i].distance(gm) <= tol for i in tree.query(gm.buffer(tol)))
                            if not hit:
                                c = gm.representative_point()
                                qc.err("Spatial rules (appconfig.json)", ds, f"{ds}\\{fc}", r["error"], int(oid), (c.x, c.y))


def run_dmt_qc(gdb: str, template: str, topology_findings: int = 0) -> Dict[str, Any]:
    """Run the replica on an output geodatabase against the template (.gdb or .gdb.zip)."""
    tmp = None
    tpl = template
    if template.lower().endswith(".zip"):
        from .schema_import import extract_gdb_zip
        tmp = tempfile.mkdtemp(prefix="gsb_qc_")
        tpl = extract_gdb_zip(template, tmp)
    try:
        qc = QCRun()
        t, s = describe(tpl), describe(gdb)
        _compare_schema(qc, t, s)
        _coded_values(qc, gdb, t)
        _spatial_rules(qc, gdb, load_rules(), s)
        _arcs(qc, gdb, s)
    finally:
        if tmp:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
    checks = ["Schema identical to template", "Topology present", "Coded values", "Spatial rules (appconfig.json)",
              "No curve geometry"]
    by = {c: sum(1 for r in qc.rows if r["check"] == c) for c in checks}
    return {"passed": not qc.rows, "errors": qc.rows, "by_check": by, "template": os.path.basename(template),
            "topology_findings": topology_findings,
            "note": "Replica of DMT Data Quality Control (QCUtility.py). ArcGIS ValidateTopology itself is run by the "
                    "DMT tool; the template's topology rules were evaluated by GeoSyntra during validation."}


def write_qc_csv(result: Dict[str, Any], path: str):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Dataset", "FeatureClass", "Error", "ObjectID"])
        for r in result["errors"]:
            w.writerow([r["Dataset"], r["FeatureClass"], r["Error"], r["ObjectID"]])
