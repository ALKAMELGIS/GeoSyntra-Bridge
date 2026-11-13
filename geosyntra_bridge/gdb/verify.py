"""
Output verification — the conversion is only declared successful when the
written geodatabase is re-opened (read-only) and matches the schema:
feature classes, geometry types, fields, field types, domains + domain links,
spatial reference, feature counts, feature datasets and relationships.
Feature-dataset placement is checked with the independent GDB_Items XML
reader (not GDAL) so the check does not rely on the writer's library.
"""
from __future__ import annotations

import ctypes
from typing import Any, Dict, List

from ..schema.models import SYSTEM_FIELDS, SchemaDefinition
from . import gdal_c as G
from .filegdb_writer import FIELD_TYPE

_GEOM_OK = {"point": {G.wkbPoint, G.wkbMultiPoint}, "line": {G.wkbLineString, G.wkbMultiLineString},
            "polygon": {G.wkbPolygon, G.wkbMultiPolygon}}


def verify_filegdb(path: str, schema: SchemaDefinition, expected_counts: Dict[str, int],
                   expect_relationships: List[str]) -> Dict[str, Any]:
    g = G.gdal()
    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "", severity: str = "error"):
        checks.append({"check": name, "passed": bool(ok), "detail": detail, "severity": severity})

    ds = g.GDALOpenEx(path.encode("utf-8"), G.GDAL_OF_VECTOR | G.GDAL_OF_READONLY, None, None, None)
    check("Geodatabase opens (read-only)", bool(ds), g.last_error() if not ds else f"GDAL {g.version()}")
    if not ds:
        return {"passed": False, "checks": checks}
    try:
        layers = {}
        for i in range(g.GDALDatasetGetLayerCount(ds)):
            h = g.GDALDatasetGetLayer(ds, i)
            layers[g.OGR_L_GetName(h).decode("utf-8")] = h
        missing = [l.name for l in schema.layers if l.name not in layers]
        check("All schema feature classes recreated", not missing,
              f"{len(schema.layers) - len(missing)}/{len(schema.layers)} present" + (f"; missing {missing[:10]}" if missing else ""))

        dnames = set(g.read_cstrlist(g.GDALDatasetGetFieldDomainNames(ds, None)))
        dmiss = [d.name for d in schema.domains if d.name not in dnames]
        check("Domains created", not dmiss, f"{len(schema.domains) - len(dmiss)}/{len(schema.domains)}" +
              (f"; missing {dmiss[:10]}" if dmiss else ""))
        bad_dom = []
        for d in schema.domains:
            if d.name in dnames and d.domain_type == "coded":
                h = g.GDALDatasetGetFieldDomain(ds, d.name.encode("utf-8"))
                n = 0
                if h:
                    enum = g.OGR_CodedFldDomain_GetEnumeration(h)
                    while enum and enum[n].pszCode:
                        n += 1
                if n != len(d.coded_values):
                    bad_dom.append(f"{d.name} ({n}/{len(d.coded_values)} codes)")
        check("Domain coded values complete", not bad_dom, "; ".join(bad_dom[:10]) or "all coded values present")

        epsg = schema.spatial_reference.epsg
        field_problems, geom_problems, srs_problems, count_problems, link_problems = [], [], [], [], []
        for l in schema.layers:
            h = layers.get(l.name)
            if not h:
                continue
            gt = g.OGR_L_GetGeomType(h) & 0xFF
            if gt not in _GEOM_OK[l.geometry_type]:
                geom_problems.append(f"{l.name}: type {gt}")
            if epsg:
                srs = g.OGR_L_GetSpatialRef(h)
                code = None
                if srs:
                    c = g.OSRGetAuthorityCode(srs, None)
                    if not c:
                        g.OSRAutoIdentifyEPSG(srs)
                        c = g.OSRGetAuthorityCode(srs, None)
                    code = int(c) if c and c.isdigit() else None
                if code != int(epsg):
                    srs_problems.append(f"{l.name}: {code}")
            defn = g.OGR_L_GetLayerDefn(h)
            actual = {}
            for i in range(g.OGR_FD_GetFieldCount(defn)):
                fh = g.OGR_FD_GetFieldDefn(defn, i)
                actual[g.OGR_Fld_GetNameRef(fh).decode("utf-8").upper()] = fh
            for fd in l.fields:
                fh = actual.get(fd.name.upper())
                if fh is None:
                    # REMARKS / source-tracking fields are added by this tool only where the template has them;
                    # the template copy is never altered, so their absence is correct
                    if fd.name.upper() in SYSTEM_FIELDS:
                        continue
                    field_problems.append(f"{l.name}.{fd.name} missing")
                    continue
                exp_t, _ = FIELD_TYPE.get(fd.field_type, FIELD_TYPE["TEXT"])
                at = g.OGR_Fld_GetType(fh)
                compatible = at == exp_t or (exp_t == G.OFTInteger and at == G.OFTInteger64) or \
                    (exp_t == G.OFTDateTime and at in (G.OFTDate, G.OFTDateTime))
                if not compatible:
                    field_problems.append(f"{l.name}.{fd.name} type {at}≠{exp_t}")
                if fd.domain:
                    dn = g.OGR_Fld_GetDomainName(fh)
                    if (dn.decode() if dn else None) != fd.domain:
                        link_problems.append(f"{l.name}.{fd.name}")
            exp = expected_counts.get(l.name, 0)
            got = g.OGR_L_GetFeatureCount(h, 1)
            if got != exp:
                count_problems.append(f"{l.name}: {got}≠{exp}")
        n_fields = sum(len(l.fields) for l in schema.layers)
        check("Geometry types match schema", not geom_problems, "; ".join(geom_problems[:10]) or "all match")
        check("Fields and field types match schema", not field_problems,
              "; ".join(field_problems[:10]) or f"{n_fields} fields verified")
        check("Fields linked to their domains", not link_problems,
              f"{len(link_problems)} unlinked: {', '.join(link_problems[:8])}" if link_problems else "all domain links present")
        if epsg:
            check(f"Spatial reference EPSG:{epsg}", not srs_problems, "; ".join(srs_problems[:10]) or "all feature classes")
        check("Feature counts match processed features", not count_problems,
              "; ".join(count_problems[:10]) or f"{sum(expected_counts.values())} features")

        if expect_relationships and g.GDALDatasetGetRelationshipNames is not None:
            rn = set(g.read_cstrlist(g.GDALDatasetGetRelationshipNames(ds, None)))
            rmiss = [r for r in expect_relationships if r not in rn]
            check("Relationship classes created", not rmiss, ", ".join(rmiss) or f"{len(expect_relationships)} created")
    finally:
        g.GDALClose(ds)

    fds_expected = {l.name: l.feature_dataset for l in schema.layers if l.feature_dataset}
    if fds_expected:
        paths = {p.upper() for p in _catalog_paths(path)}
        wrong = [n for n, fds in fds_expected.items() if f"\\{fds}\\{n}".upper() not in paths]
        check("Feature classes inside their feature datasets", not wrong,
              f"{len(fds_expected) - len(wrong)}/{len(fds_expected)} verified in GDB_Items catalog"
              + (f"; not found: {wrong[:6]}" if wrong else ""))

    passed = all(c["passed"] for c in checks if c["severity"] == "error")
    return {"passed": passed, "checks": checks}


def _catalog_paths(gdb_path: str) -> set:
    """CatalogPath values read straight from the GDB_Items system table (a00000004)."""
    import os
    import re
    out = set()
    f = os.path.join(gdb_path, "a00000004.gdbtable")
    if not os.path.isfile(f):
        return out
    with open(f, "rb") as fh:
        data = fh.read()
    for enc, rx in (("utf-8", rb"<CatalogPath>([^<]{1,512})</CatalogPath>"),
                    ("utf-16-le", re.escape("<CatalogPath>".encode("utf-16-le")) + rb"((?:[^<]\x00){1,512})" +
                     re.escape("</CatalogPath>".encode("utf-16-le")))):
        for m in re.finditer(rx, data):
            try:
                out.add(m.group(1).decode(enc))
            except UnicodeDecodeError:
                continue
    return out
