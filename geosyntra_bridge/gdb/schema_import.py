"""
Build a schema JSON from an approved template File Geodatabase (read-only).

Captured: feature datasets, feature classes, geometry types, fields (type,
length, alias, nullability, default, domain link), coded/range domains and
the spatial reference. CAD mapping hints are generated from class names and
can be refined afterwards in the JSON.
"""
from __future__ import annotations

import os
import re
import zipfile
from typing import Any, Dict, List, Optional

from . import gdal_c as G
from .verify import _catalog_paths

SKIP_FIELDS = {"OBJECTID", "SHAPE", "SHAPE_LENGTH", "SHAPE_AREA", "SHAPE.LEN", "SHAPE.AREA", "GLOBALID",
               "CREATED_USER", "CREATED_DATE", "LAST_EDITED_USER", "LAST_EDITED_DATE"}
_GEOM = {G.wkbPoint: "point", G.wkbMultiPoint: "point", G.wkbLineString: "line", G.wkbMultiLineString: "line",
         G.wkbPolygon: "polygon", G.wkbMultiPolygon: "polygon"}
_SKIP_CLASS = re.compile(r"^T_\d+_(DirtyAreas|PointErrors|LineErrors|PolyErrors)$|^GDB_|^N_\d+_", re.I)


def _ftype(t: int, sub: int) -> str:
    if t == G.OFTString:
        return "GUID" if sub == G.OFSTUUID else "TEXT"
    if t in (G.OFTInteger, G.OFTInteger64):
        return "SMALLINTEGER" if sub == G.OFSTInt16 else "INTEGER"
    if t == G.OFTReal:
        return "FLOAT" if sub == G.OFSTFloat32 else "DOUBLE"
    if t in (G.OFTDate, G.OFTDateTime):
        return "DATE"
    return "TEXT"


def _tokens(name: str) -> List[str]:
    s = re.sub(r"[_\-]+", " ", name)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    return [w.lower() for w in s.split() if len(w) > 1]


def extract_gdb_zip(zip_path: str, dest: str) -> str:
    """Safely extract an uploaded zipped .gdb; returns the .gdb folder path."""
    with zipfile.ZipFile(zip_path) as z:
        total = 0
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise ValueError("zip contains unsafe paths")
            total += info.file_size
            if total > 2 * 1024 ** 3:
                raise ValueError("zip expands beyond 2 GB")
        z.extractall(dest)
    for root, dirs, files in os.walk(dest):
        for d in dirs:
            if d.lower().endswith(".gdb"):
                return os.path.join(root, d)
        if "gdb" in files and any(f.endswith(".gdbtable") for f in files):
            return root
    raise ValueError("no .gdb folder found in the zip")


def schema_from_gdb(gdb_path: str, schema_id: str, display_name: str, discipline: str = "") -> Dict[str, Any]:
    g = G.gdal()
    ds = g.GDALOpenEx(gdb_path.encode("utf-8"), G.GDAL_OF_VECTOR | G.GDAL_OF_READONLY, None, None, None)
    if not ds:
        raise ValueError(f"not a readable File Geodatabase: {g.last_error()}")
    try:
        paths = _catalog_paths(gdb_path)
        domains = []
        for dn in g.read_cstrlist(g.GDALDatasetGetFieldDomainNames(ds, None)):
            h = g.GDALDatasetGetFieldDomain(ds, dn.encode("utf-8"))
            if not h:
                continue
            dtype = g.OGR_FldDomain_GetDomainType(h)
            ftype = _ftype(g.OGR_FldDomain_GetFieldType(h), 0)
            desc = (g.OGR_FldDomain_GetDescription(h) or b"").decode("utf-8", "replace")
            if dtype == G.OFDT_CODED:
                vals = {}
                enum = g.OGR_CodedFldDomain_GetEnumeration(h)
                i = 0
                while enum and enum[i].pszCode:
                    vals[enum[i].pszCode.decode("utf-8", "replace")] = (enum[i].pszValue or enum[i].pszCode).decode("utf-8", "replace")
                    i += 1
                domains.append({"name": dn, "domain_type": "coded", "field_type": ftype, "description": desc,
                                "coded_values": vals})
            elif dtype == G.OFDT_RANGE:
                domains.append({"name": dn, "domain_type": "range", "field_type": ftype, "description": desc})
        layers, fds, epsg = [], set(), None
        for i in range(g.GDALDatasetGetLayerCount(ds)):
            h = g.GDALDatasetGetLayer(ds, i)
            name = g.OGR_L_GetName(h).decode("utf-8")
            gtype = g.OGR_L_GetGeomType(h)
            geom = _GEOM.get(gtype & 0xFF)
            has_z = bool(gtype & 0x80000000) or (gtype % 10000) // 1000 in (1, 3)
            if geom is None or _SKIP_CLASS.match(name):
                continue
            fdsn: Optional[str] = None
            for p in paths:
                parts = [x for x in p.split("\\") if x]
                if len(parts) == 2 and parts[1].upper() == name.upper():
                    fdsn = parts[0]
            if fdsn:
                fds.add(fdsn)
            srs = g.OGR_L_GetSpatialRef(h)
            if srs and epsg is None:
                c = g.OSRGetAuthorityCode(srs, None)
                if not c:
                    g.OSRAutoIdentifyEPSG(srs)
                    c = g.OSRGetAuthorityCode(srs, None)
                epsg = int(c) if c and c.isdigit() else None
            defn = g.OGR_L_GetLayerDefn(h)
            fields = []
            for k in range(g.OGR_FD_GetFieldCount(defn)):
                fh = g.OGR_FD_GetFieldDefn(defn, k)
                fname = g.OGR_Fld_GetNameRef(fh).decode("utf-8")
                if fname.upper() in SKIP_FIELDS:
                    continue
                fd: Dict[str, Any] = {"name": fname, "field_type": _ftype(g.OGR_Fld_GetType(fh), g.OGR_Fld_GetSubType(fh))}
                w = g.OGR_Fld_GetWidth(fh)
                if fd["field_type"] == "TEXT" and w:
                    fd["length"] = w
                alias = g.OGR_Fld_GetAlternativeNameRef(fh)
                if alias and alias.decode("utf-8") != fname:
                    fd["alias"] = alias.decode("utf-8")
                if not g.OGR_Fld_IsNullable(fh):
                    fd["nullable"] = False
                dom = g.OGR_Fld_GetDomainName(fh)
                if dom:
                    fd["domain"] = dom.decode("utf-8")
                dflt = g.OGR_Fld_GetDefault(fh)
                if dflt:
                    v = dflt.decode("utf-8").strip("'")
                    fd["default"] = v
                fields.append(fd)
            if not any(f["name"].upper() == "REMARKS" for f in fields):
                fields.append({"name": "REMARKS", "field_type": "TEXT", "length": 1000})
            pat = r"[_\-\s]?".join(re.escape(t) for t in _tokens(name)) or re.escape(name)
            layers.append({"name": name, "geometry_type": geom, "feature_dataset": fdsn, "category": "asset", "has_z": has_z,
                           "fields": fields,
                           "classification_hints": {"layer_name_patterns": [pat], "block_name_patterns": [pat],
                                                    "text_keywords": _tokens(name), "geometry_types": [geom]}})
    finally:
        g.GDALClose(ds)
    topo = topology_rules_from_catalog(gdb_path, {l["name"] for l in layers})
    return {"format": 2, "schema_id": schema_id, "display_name": display_name, "version": "1.0",
            "status": "approved", "discipline": discipline,
            "description": f"Imported from template geodatabase '{os.path.basename(gdb_path)}': "
                           f"{len(layers)} feature classes, {len(domains)} domains.",
            "spatial_reference": {"epsg": epsg, "verified": bool(epsg)}, "xy_tolerance": 0.001,
            "feature_datasets": [{"name": n} for n in sorted(fds)], "domains": domains, "layers": layers,
            "label_rules": [], "network": {"enabled": False}, "topology_rules": topo,
            "source": "template_gdb", "source_template": os.path.basename(gdb_path.rstrip("/\\"))}


def topology_rules_from_catalog(gdb_path: str, layer_names: set) -> List[Dict[str, Any]]:
    """Topology rules stored in the GDB_Items catalog XML (class ids resolved to names)."""
    f = os.path.join(gdb_path, "a00000004.gdbtable")
    if not os.path.isfile(f):
        return []
    with open(f, "rb") as fh:
        d = fh.read().decode("utf-8", "replace")
    ids = {m.group(3): m.group(2) for m in re.finditer(
        r"<CatalogPath>([^<]*)</CatalogPath><Name>([^<]*)</Name>.{0,800}?<DSID>(\d+)</DSID>", d, re.S)}
    out, seen = [], set()
    for blk in re.findall(r"<TopologyRule [^>]*>(.*?)</TopologyRule>", d, re.S):
        t = re.search(r"<TopologyRuleType>(\w+)</TopologyRuleType>", blk)
        o = re.search(r"<OriginClassID>(\d+)</OriginClassID>", blk)
        de = re.search(r"<DestinationClassID>(\d+)</DestinationClassID>", blk)
        if not t or not o:
            continue
        origin = ids.get(o.group(1))
        dest = ids.get(de.group(1)) if de else None
        if origin not in layer_names:
            continue
        key = (t.group(1), origin, dest)
        if key not in seen:
            seen.add(key)
            out.append({"rule": t.group(1), "origin": origin, "destination": dest})
    return out
