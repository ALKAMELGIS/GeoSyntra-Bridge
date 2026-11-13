"""
Feature-attribute annexes of the DMT As-Built Data Submission Standards V7 (knowledge/annex/<ASSET>.json, built by
scripts/import_standard_annex.py) compared with the official template geodatabase schema.

The template stays the delivery schema (the DMT QC tool compares the output with it); the annex is used to
  * report where the standard and the template differ (feature classes, fields, types, text lengths, mandatory),
  * mark a field as mandatory in the standard when the template leaves it nullable (reported, never altered).
"""
from __future__ import annotations

import difflib
import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

from ..schema.models import SchemaDefinition

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ANNEX_DIR = os.path.join(ROOT, "knowledge", "annex")
SYSTEM = {"OBJECTID", "SHAPE", "SHAPE_LENGTH", "SHAPE_AREA", "GLOBALID", "SHAPE_LEN", "OBJECT_ID",
          "CREATED_USER", "CREATED_DATE", "LAST_EDITED_USER", "LAST_EDITED_DATE"}
TYPE_MAP = {"TEXT": "string", "STRING": "string", "DOUBLE": "double", "FLOAT": "double", "LONG": "long", "INTEGER": "long",
            "SHORT": "short", "SMALLINTEGER": "short", "DATE": "date", "GUID": "guid", "GLOBALID": "globalid"}
NUMERIC = {"double", "long", "short"}
SECTION = {"RD": "Annex RD", "STL": "Annex STL", "ITC": "Annex TCS/ITS", "SW": "Annex SW", "IR": "Annex IR",
           "SL": "Annex SL", "HL": "Annex HL"}


@lru_cache(maxsize=None)
def load_annex(asset: str) -> Dict[str, Any]:
    p = os.path.join(ANNEX_DIR, f"{asset}.json")
    if not os.path.isfile(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f).get("features", {})


def _key(n: str, asset: str) -> str:
    n = n.upper().split("#")[0]
    n = re.sub(rf"^({asset}|HL|RD|SW|IR|SL|STL|TR)_", "", n)
    return re.sub(r"[^A-Z0-9]", "", n)


def match_features(schema: SchemaDefinition, annex: Dict[str, Any], asset: str) -> Dict[str, Optional[str]]:
    """template layer -> annex feature name (same geometry; exact, prefix-stripped, plural or close spelling)."""
    out: Dict[str, Optional[str]] = {}
    used = set()
    keys = {k: _key(k, asset) for k in annex}
    for pass_ in (0, 1, 2, 3):
        for ly in schema.layers:
            if ly.name in out and out[ly.name]:
                continue
            lk = _key(ly.name, asset)
            cands = [k for k, v in annex.items() if k not in used and (pass_ == 3 or v["geometry"].split()[0] == ly.geometry_type)]
            hit = None
            if pass_ == 0:
                hit = next((k for k in cands if k.split("#")[0] == ly.name), None)
            elif pass_ == 1:
                hit = next((k for k in cands if keys[k] in (lk, lk + "S", lk + "ES") or lk in (keys[k] + "S",)), None)
            elif pass_ == 3:     # same name, different geometry (the template is newer than the standard V7)
                hit = next((k for k in cands if keys[k] == lk), None)
            else:
                close = difflib.get_close_matches(lk, [keys[k] for k in cands], n=1, cutoff=0.85)
                hit = next((k for k in cands if close and keys[k] == close[0]), None)
            if hit:
                out[ly.name] = hit
                used.add(hit)
            else:
                out.setdefault(ly.name, None)
    return out


def compare(schema: SchemaDefinition, asset: str) -> Optional[Dict[str, Any]]:
    annex = load_annex(asset)
    if not annex:
        return None
    m = match_features(schema, annex, asset)
    classes, fields = [], []
    for ly in schema.layers:
        a = m.get(ly.name)
        tf = {f.name.upper().rstrip("_"): f for f in ly.fields if f.name.upper() not in SYSTEM}   # GROUP_ = GROUP
        if not a:
            classes.append({"template": ly.name, "geometry": ly.geometry_type, "annex": None,
                            "status": "template only", "fields_template": len(tf)})
            continue
        af = {f["name"].upper().rstrip("_"): f for f in annex[a]["fields"] if f["name"].upper() not in SYSTEM}
        diff = {"missing_in_template": 0, "not_in_annex": 0, "type": 0, "length": 0, "mandatory_nullable": 0}
        for n, f in af.items():
            t = tf.get(n)
            if t is None:
                diff["missing_in_template"] += 1
                fields.append({"template": ly.name, "annex": a, "field": n, "issue": "in standard, not in template",
                               "standard": _fmt(f), "template_def": "", "mandatory": f.get("mandatory")})
                continue
            tt = TYPE_MAP.get(str(t.field_type).upper(), str(t.field_type).lower())
            if f.get("type") and f["type"] != tt and not (f["type"] in NUMERIC and tt in NUMERIC and _num_ok(f["type"], tt)):
                diff["type"] += 1
                fields.append({"template": ly.name, "annex": a, "field": n, "issue": "data type differs",
                               "standard": _fmt(f), "template_def": _tfmt(t, tt), "mandatory": f.get("mandatory")})
            elif f.get("type") == "string" and tt == "string" and f.get("length") and t.length and f["length"] != t.length:
                diff["length"] += 1
                fields.append({"template": ly.name, "annex": a, "field": n,
                               "issue": "text length " + ("shorter" if t.length < f["length"] else "longer") + " in template",
                               "standard": _fmt(f), "template_def": _tfmt(t, tt), "mandatory": f.get("mandatory")})
            if f.get("mandatory") and t.nullable:
                diff["mandatory_nullable"] += 1
                fields.append({"template": ly.name, "annex": a, "field": n,
                               "issue": "mandatory in standard, nullable in template (value expected, not enforced)",
                               "standard": _fmt(f), "template_def": _tfmt(t, tt), "mandatory": True})
        for n, t in tf.items():
            if n not in af:
                diff["not_in_annex"] += 1
                tt = TYPE_MAP.get(str(t.field_type).upper(), str(t.field_type).lower())
                fields.append({"template": ly.name, "annex": a, "field": n, "issue": "in template, not in standard",
                               "standard": "", "template_def": _tfmt(t, tt), "mandatory": None})
        classes.append({"template": ly.name, "geometry": ly.geometry_type, "annex": a, "page": annex[a].get("page"),
                        "status": "matched" + ("" if a.split("#")[0] == ly.name else " (name differs)")
                        + ("" if annex[a]["geometry"].split()[0] == ly.geometry_type else
                           f" — geometry differs: template {ly.geometry_type}, standard {annex[a]['geometry']}"),
                        "fields_template": len(tf), "fields_annex": len(af), **diff})
    matched = {v for v in m.values() if v}
    for a, v in annex.items():
        if a not in matched:
            classes.append({"template": None, "geometry": v["geometry"], "annex": a, "page": v.get("page"),
                            "status": "standard only", "fields_annex": len(v["fields"])})
    return {"asset": asset, "section": SECTION.get(asset), "classes": classes, "fields": fields,
            "summary": {"template_classes": len(schema.layers), "annex_classes": len(annex),
                        "matched": len(matched),
                        "template_only": sum(1 for c in classes if c["status"] == "template only"),
                        "standard_only": sum(1 for c in classes if c["status"] == "standard only"),
                        "field_differences": len(fields)}}


def mandatory_fields(schema: SchemaDefinition, asset: str) -> Dict[str, List[str]]:
    """template layer -> fields the standard marks mandatory (existing template fields only)."""
    annex = load_annex(asset)
    if not annex:
        return {}
    out: Dict[str, List[str]] = {}
    for ly, a in match_features(schema, annex, asset).items():
        if not a:
            continue
        layer = schema.get_layer(ly)
        names = {f.name.upper(): f.name for f in layer.fields}
        out[ly] = [names[f["name"].upper()] for f in annex[a]["fields"]
                   if f.get("mandatory") and f["name"].upper() in names and f["name"].upper() not in SYSTEM]
    return out


def _num_ok(a: str, t: str) -> bool:
    # the annex writes "Number"/"Integer" loosely: a template integer for an annex integer is fine, as is double for double
    return (a == "long" and t in ("long", "short")) or (a == "short" and t in ("short", "long")) or (a == "double" and t == "double")


def _fmt(f: Dict[str, Any]) -> str:
    return f"{f.get('type') or '?'}" + (f"({f['length']})" if f.get("length") else "") + \
        (" mandatory" if f.get("mandatory") else " optional" if f.get("mandatory") is False else "")


def _tfmt(t, tt: str) -> str:
    return tt + (f"({t.length})" if tt == "string" and t.length else "") + ("" if t.nullable else " NOT NULL")


@lru_cache(maxsize=1)
def municipal_fields() -> frozenset:
    """Field names every DMT data dictionary assigns to the municipal engineer (ROADID, SEG_RW_ID, LRS ...)."""
    import glob
    resp: Dict[str, set] = {}
    for p in glob.glob(os.path.join(ROOT, "knowledge", "dictionaries", "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        cls = d.get("classes", [])
        for c in (cls.values() if isinstance(cls, dict) else cls):
            for fd in c.get("fields", []):
                r = str(fd.get("responsibility") or "").upper()
                if r:
                    resp.setdefault(fd["name"].upper(), set()).add(r)
    return frozenset(n for n, rs in resp.items() if rs == {"MUNICIPAL ENGINEER"})


def apply_annex(schema: SchemaDefinition) -> int:
    """Enrich the loaded schema (never the template) with the standard's annex: fields the annex marks mandatory get
    standard_mandatory; where the data dictionary says nothing about who fills a field (e.g. ITC, which has no
    dictionary) and the field is not one the municipal engineer fills elsewhere, it becomes contractor-mandatory.
    Empty descriptions are taken from the annex. -> number of fields changed."""
    from ..validation.topology import asset_code
    asset = asset_code(schema)
    annex = load_annex(asset) if asset else {}
    if not annex:
        return 0
    muni = municipal_fields()
    n = 0
    for ly_name, a in match_features(schema, annex, asset).items():
        if not a:
            continue
        layer = schema.get_layer(ly_name)
        af = {f["name"].upper(): f for f in annex[a]["fields"]}
        for fd in layer.fields:
            f = af.get(fd.name.upper()) or af.get(fd.name.upper().rstrip("_"))
            if not f or fd.name.upper() in SYSTEM:
                continue
            if not fd.description and f.get("description"):
                fd.description = f["description"]
                n += 1
            if f.get("mandatory"):
                fd.standard_mandatory = True
                if not fd.responsibility and not fd.contractor_mandatory and fd.name.upper() not in muni:
                    fd.contractor_mandatory = True
                    fd.responsibility = "CONTRACTOR (standard annex)"
                    n += 1
    return n
