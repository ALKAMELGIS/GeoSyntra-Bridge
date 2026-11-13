"""
DMT data dictionaries (Excel) -> machine-readable knowledge.

The dictionaries are the reference engineers use to understand every schema layer:

  ASSET_DESCRIPTION         what each feature class *is* (definition, geometry, feature dataset)
  *_ASSET_TYPES             the names the same asset has in other systems / older deliveries
                            (ADM, AAM, DRM, municipal legacy, PLI ...) -> layer-name vocabulary
  one sheet per class       every field: type, who fills it (CONTRACTOR / MUNICIPAL ENGINEER),
                            mandatory for the contractor or not, description with allowed values,
                            and the field's name in the other systems -> attribute aliases

Output: knowledge/dictionaries/<CODE>.json, read by mapping/semantics.py (class meaning + legacy names),
the attribute engine (aliases) and the completeness check (contractor-mandatory fields).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

GEOM_WORDS = {"POINT", "POINTZ", "POLYLINE", "POLYLINEZ", "LINE", "POLYGON", "POLYGONZ", "TABLE", "MULTIPATCH",
              "POINT Z", "POLYLINE Z", "POLYGON Z", "ANNOTATION"}
NOISE = {"EXISTING", "NEW", "RENAME", "DELETE", "MODIFIED", "OK", "N/A", "NA", "-", "YES", "NO", "MERGE", "MERGED"}

_ASSET_SHEET = re.compile(r"ASSET_TYPES$", re.I)


def _s(v: Any) -> str:
    return "" if v is None else str(v).replace("\xa0", " ").strip()


def _rows(ws) -> List[List[Any]]:
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _ffill(row: List[Any]) -> List[str]:
    out, cur = [], ""
    for v in row:
        if _s(v):
            cur = _s(v)
        out.append(cur)
    return out


def _geom(v: str) -> Optional[str]:
    u = v.upper().replace(" ", "")
    if "POINT" in u:
        return "point"
    if "LINE" in u:
        return "line"
    if "POLYGON" in u:
        return "polygon"
    if "TABLE" in u:
        return "table"
    return None


def _split_names(v: str) -> List[str]:
    out = []
    for part in re.split(r"[\n;]+|\s{2,}", v):
        p = re.sub(r"\s*\((?:PF|Public feature|Hard surface)\)\s*", " ", part, flags=re.I).strip(" .,-")
        p = re.sub(r"\s*(?:Convert|Covert)\b.*$", "", p, flags=re.I).strip()
        if re.match(r"^C\d+_|_point\d*(_\d+)?$|_polyline$|_region$", p, re.I):
            continue    # generic CAD-export layer names (C5_Points_font_point1_1) say nothing about the asset
        if p and p.upper() not in NOISE and p.upper() not in GEOM_WORDS and len(p) <= 80:
            out.append(p)
    return out


_VALUES = re.compile(r"\bvalues?\s*(?:are|is)?\s*[:\-]\s*(.+)$", re.I | re.S)


def parse_values(desc: str) -> Dict[str, Any]:
    """'Operational Status of Asset Values: Active (Default Value), Inactive' -> values + default."""
    m = _VALUES.search(desc or "")
    if not m:
        m2 = re.search(r":\s*(Yes\s*/\s*No)\b", desc or "", re.I)
        return {"values": ["Yes", "No"]} if m2 else {}
    body = m.group(1)
    body = re.split(r"\n\s*\n|(?<=\.)\s+(?=[A-Z][a-z]+\s)", body)[0]
    vals, default = [], None
    for raw in re.split(r",|\n|•|;|/(?=\s*[A-Z])", body):
        v = raw.strip(" .:-\t")
        if not v:
            continue
        if re.search(r"\(default(?: value)?\)", v, re.I):
            v = re.sub(r"\s*\(default(?: value)?\)", "", v, flags=re.I).strip()
            default = v
        v = re.sub(r"\s*\betc\b\.?$", "", v, flags=re.I).strip(" .")
        if v and len(v) <= 60 and not v.lower().startswith(("e.g", "i.e")):
            vals.append(v)
    out: Dict[str, Any] = {"values": vals} if vals else {}
    if default:
        out["default"] = default
    return out


# -------------------------------------------------------------------- sheets
def _asset_description(rows: List[List[Any]], into: Dict[str, Any]):
    hdr = next((i for i, r in enumerate(rows[:6]) if any("DESCRIPTION" in _s(v).upper() for v in r)), None)
    if hdr is None:
        return
    labels = [_s(v).upper() for v in rows[hdr]]
    ci_name = next((i for i, l in enumerate(labels) if "FEATURE NAME" in l or l.startswith("ASSET TYPE")), 1)
    ci_geom = next((i for i, l in enumerate(labels) if "GEOMETRY" in l), None)
    ci_desc = next((i for i, l in enumerate(labels) if "DESCRIPTION" in l and i != 0), None)
    ci_ds = next((i for i, l in enumerate(labels) if "DATASET" in l or "CATEGORY" in l), 0)
    ds = ""
    for r in rows[hdr + 1:]:
        if _s(r[ci_ds]) and ci_ds != ci_name:
            ds = _s(r[ci_ds])
        name = _s(r[ci_name]) if ci_name < len(r) else ""
        if not name or " " in name and not name.isupper():
            continue
        desc = _s(r[ci_desc]) if ci_desc is not None and ci_desc < len(r) else ""
        c = into.setdefault(name.upper(), {"name": name, "legacy_names": []})
        c["feature_dataset"] = c.get("feature_dataset") or ds
        if ci_geom is not None and ci_geom < len(r) and _geom(_s(r[ci_geom])):
            c["geometry"] = _geom(_s(r[ci_geom]))
        if desc:
            c["description"] = desc
            title = desc.split(":", 1)[0] if ":" in desc[:80] else ""
            for t in re.split(r"\s*\(or\s*|\)\s*|\s+or\s+|,", title):
                t = re.sub(r"^(?:known\s+)?(?:also\s+)?(?:known\s+)?(?:as\s+)?(?:an?\s+|the\s+)?", "", t.strip(" “”\"'"), flags=re.I).strip()
                if t and len(t) < 60 and t.upper() != name.upper():
                    c["legacy_names"].append(t)


def _asset_types(rows: List[List[Any]], into: Dict[str, Any], sheet: str):
    hdr = next((i for i, r in enumerate(rows[:6]) if any("ASSET TYPE" in _s(v).upper() or "FEATURE CLASS" in _s(v).upper()
                                                          for v in r) and any("GEOMETRY" in _s(v).upper() for v in r)), None)
    if hdr is None:
        return
    groups = _ffill(rows[hdr - 1]) if hdr > 0 else [""] * len(rows[hdr])
    labels = [_s(v).upper() for v in rows[hdr]]
    groups += [""] * (len(labels) - len(groups))
    ci_fc = next((i for i, l in enumerate(labels) if "UDM FEATURE CLASS" in l), None)
    if ci_fc is None:   # municipal tables: first ASSET TYPE column is the unified name
        ci_fc = next((i for i, l in enumerate(labels) if l == "ASSET TYPE"), None)
    if ci_fc is None:
        return
    ci_resp = next((i for i, l in enumerate(labels) if l == "RESPONSIBILITY"), None)
    ci_conf = next((i for i, l in enumerate(labels) if "CONFIGURE" in l), None)
    name_cols = [i for i, l in enumerate(labels) if i != ci_fc and (l in ("ASSET TYPE", "DATASET NAME") or "FEATURE CLASS" in l)
                 and "COMMENT" not in groups[i].upper()]
    for r in rows[hdr + 1:]:
        r = r + [None] * (len(labels) - len(r))
        fc = _s(r[ci_fc]).split("\n")[0].strip()
        if not fc or fc.upper() in NOISE:
            continue
        c = into.setdefault(fc.upper(), {"name": fc, "legacy_names": []})
        if ci_resp is not None and _s(r[ci_resp]):
            c["responsibility"] = _s(r[ci_resp])
        if ci_conf is not None and _s(r[ci_conf]):
            c["configure_in"] = _s(r[ci_conf])
        if ci_fc + 1 < len(r) and _geom(_s(r[ci_fc + 1])) and not c.get("geometry"):
            c["geometry"] = _geom(_s(r[ci_fc + 1]))
        for i in name_cols:
            entries = _split_names(_s(r[i]))
            for n in entries:
                if n.upper() == fc.upper():
                    continue
                code_like = "_" in n or n.isupper() or re.search(r"[a-z][A-Z]", n)
                if len(entries) > 4 and not code_like:
                    # a long list of plain words in one cell is a list of types (Bollard, Spot, Garden ...
                    # under lighting), not other names of the class
                    c.setdefault("type_values", []).append(n)
                    continue
                c["legacy_names"].append(n)
                c.setdefault("legacy_sources", {}).setdefault(n, groups[i] or sheet)
        # free-text comments are kept as notes (e.g. "Chamber polygon Covert to Point")
        notes = [_s(r[i]) for i, g in enumerate(groups) if "COMMENT" in g.upper() and _s(r[i]) and _s(r[i]).upper() not in NOISE]
        if notes:
            c.setdefault("notes", []).extend(notes)


def _field_sheet(rows: List[List[Any]], sheet: str) -> Optional[Dict[str, Any]]:
    hdr = next((i for i, r in enumerate(rows[:6]) if any(_s(v).upper() == "RESPONSIBILITY" for v in r)
                and any(_s(v).upper() in ("FIELD", "FIELD NAME") for v in r)), None)
    if hdr is None:
        return None
    labels = [_s(v).upper().replace("\n", " ") for v in rows[hdr]]
    groups = _ffill(rows[hdr - 1]) if hdr > 0 else [""] * len(labels)
    groups += [""] * (len(labels) - len(groups))
    ci_field = labels.index("FIELD") if "FIELD" in labels else labels.index("FIELD NAME")
    def col(pred):
        return next((i for i, l in enumerate(labels) if i > ci_field and pred(l)), None)
    ci_type = col(lambda l: l == "TYPE")
    ci_resp = col(lambda l: l == "RESPONSIBILITY")
    ci_mc = col(lambda l: l.startswith("MANDATORY") and ("CONTRACTOR" in l or l == "MANDATORY"))
    ci_mi = col(lambda l: l.startswith("MANDATORY") and "INSPECTION" in l)
    ci_mm = col(lambda l: l.startswith("MANDATORY") and "MUNICIPAL" in l)
    ci_vis = col(lambda l: l.startswith("VISIBLE"))
    ci_tab = col(lambda l: l.startswith("OSOOL TAB"))
    ci_src = col(lambda l: l == "SOURCE")
    ci_desc = col(lambda l: l == "DESCRIPTION")
    ci_status = next((i for i, l in enumerate(labels) if i < ci_field and "EXISTING" in (groups[i] + l).upper()), None)
    if ci_status is None and ci_field > 0:
        ci_status = ci_field - 1
    alias_cols = [i for i, l in enumerate(labels) if ci_desc is not None and i > ci_desc and l in ("FIELD NAME", "FIELD")
                  and "COMMENT" not in groups[i].upper()]
    title = _s(rows[hdr - 1][ci_field]) if hdr > 0 and ci_field < len(rows[hdr - 1]) else ""
    title = title or next((_s(v) for v in (rows[hdr - 1] if hdr > 0 else []) if "SCHEMA" in _s(v).upper()), "")
    m = re.search(r"-\s*([A-Z0-9_]+)\s*\(([^)]+)\)", title)
    fc = (m.group(1) if m else sheet).strip()
    out = {"sheet": sheet, "name": fc, "geometry": _geom(m.group(2)) if m else None, "fields": []}
    for r in rows[hdr + 1:]:
        r = r + [None] * (len(labels) - len(r))
        name = _s(r[ci_field])
        if not name or not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", name):
            continue
        desc = _s(r[ci_desc]) if ci_desc is not None else ""
        f: Dict[str, Any] = {
            "name": name,
            "type": _s(r[ci_type]) if ci_type is not None else "",
            "responsibility": _s(r[ci_resp]).upper() if ci_resp is not None else "",
            "mandatory_contractor": _s(r[ci_mc]).upper().startswith("YES") if ci_mc is not None else False,
            "mandatory_inspection": _s(r[ci_mi]).upper().startswith("YES") if ci_mi is not None else False,
            "municipal": _s(r[ci_mm]) if ci_mm is not None else "",
            "visible_in_osool": _s(r[ci_vis]) if ci_vis is not None else "",
            "osool_tab": _s(r[ci_tab]) if ci_tab is not None else "",
            "source": _s(r[ci_src]) if ci_src is not None else "",
            "status": _s(r[ci_status]) if ci_status is not None else "",
            "description": desc,
        }
        f.update(parse_values(desc))
        aliases = {}
        for i in alias_cols:
            a = _s(r[i])
            if a and a.upper() != name.upper() and re.match(r"^[A-Za-z][A-Za-z0-9_ ]*$", a):
                aliases.setdefault(a, groups[i] or "other system")
        if aliases:
            f["aliases"] = aliases
        out["fields"].append(f)
    return out


def parse_dictionary(path: str) -> Dict[str, Any]:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    classes: Dict[str, Any] = {}
    field_sheets: Dict[str, Any] = {}
    tables: Dict[str, Any] = {}
    code = None
    for ws in wb.worksheets:
        rows = _rows(ws)
        t = ws.title.strip()
        m = re.match(r"UDM_([A-Z]+)_ASSET_TYPES", t, re.I)
        if m:
            code = m.group(1).upper()
        if t.upper() == "ASSET_DESCRIPTION":
            _asset_description(rows, classes)
        elif _ASSET_SHEET.search(t):
            _asset_types(rows, classes, t)
        elif t.upper() == "LEGEND":
            continue
        else:
            fs = _field_sheet(rows, t)
            if fs:
                field_sheets[fs["name"].upper()] = fs
            else:
                tables[t] = [[_s(v) for v in r if _s(v)] for r in rows if any(_s(v) for v in r)][:120]
    for k, fs in field_sheets.items():
        c = classes.setdefault(k, {"name": fs["name"], "legacy_names": []})
        c["fields"] = fs["fields"]
        c["sheet"] = fs["sheet"]
        if fs.get("geometry") and not c.get("geometry"):
            c["geometry"] = fs["geometry"]
    # a name shared by several classes of the SAME geometry identifies none of them — unless it is one class's own name
    def _n(x):
        return re.sub(r"[^a-z0-9]", "", re.sub(r"^(HL|SL|IR|SW|RD|STL)_", "", x, flags=re.I).lower()).rstrip("s")
    owners: Dict[str, set] = {}
    for k, c in classes.items():
        for n in c.get("legacy_names", []):
            owners.setdefault(_n(n), set()).add(k)
    for k, c in classes.items():
        keep = []
        for n in c.get("legacy_names", []):
            o = owners.get(_n(n), set())
            geoms = [classes[x].get("geometry") for x in o]
            same_asset_other_geometry = len(o) > 1 and None not in geoms and len(set(geoms)) == len(geoms)
            # shared by classes that differ only in geometry (BRIDGE / BRIDGE_POINT): the name is right for
            # all of them and the drawn geometry decides — keep it
            if len(o) > 1 and _n(n) != _n(k) and not same_asset_other_geometry:
                own = [x for x in o if _n(x) == _n(n)]
                if not own or own[0] != k:
                    c.setdefault("type_values", []).append(n)
                    continue
            keep.append(n)
        c["legacy_names"] = keep
    for c in classes.values():
        seen, uniq = set(), []
        for n in c.get("legacy_names", []):
            if n.upper() not in seen:
                seen.add(n.upper())
                uniq.append(n)
        c["legacy_names"] = uniq
    code = {"ROADS": "RD"}.get(code, code)
    return {"code": code, "source_file": os.path.basename(path), "classes": classes, "tables": tables}


# -------------------------------------------------------------------- store
_CACHE: Dict[str, Any] = {}


def knowledge_dir() -> str:
    return os.environ.get("GSB_KNOWLEDGE_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "knowledge")


def load_all() -> Dict[str, Dict[str, Any]]:
    """{FEATURE_CLASS_UPPER: class entry (with 'dictionary' code)} over every dictionary json."""
    d = os.path.join(knowledge_dir(), "dictionaries")
    key = d
    if key in _CACHE:
        return _CACHE[key]
    out: Dict[str, Dict[str, Any]] = {}
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json"):
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    data = json.load(f)
                for k, c in data.get("classes", {}).items():
                    out.setdefault(k, {**c, "dictionary": data.get("code")})
    _CACHE[key] = out
    return out


def class_info(name: str) -> Optional[Dict[str, Any]]:
    """Exact class name, else a near-identical spelling (template PALMETREE <-> dictionary PALMTREE)."""
    allc = load_all()
    u = (name or "").upper()
    if u in allc:
        return allc[u]
    import difflib
    m = difflib.get_close_matches(u, list(allc), n=1, cutoff=0.9)
    return allc[m[0]] if m else None


def reset_cache():
    _CACHE.clear()


# -------------------------------------------------------------------- schema enrichment
_NUMERIC = ("INTEGER", "SMALLINTEGER", "DOUBLE", "FLOAT")


def apply_dictionary(schema) -> int:
    """Attach the dictionary's meaning to a loaded schema (in memory): class definition and legacy names,
    per field description / responsibility / contractor-mandatory flag / allowed values / default and the
    field's names in other systems (block-attribute aliases). Returns the number of classes enriched."""
    n = 0
    for layer in schema.layers:
        info = class_info(layer.name)
        if not info:
            continue
        n += 1
        layer.dictionary = info.get("dictionary")
        layer.legacy_names = list(info.get("legacy_names", []))
        if info.get("description") and not layer.description:
            layer.description = info["description"]
        if not layer.alias or layer.alias == layer.name:
            t = info.get("description", "").split(":", 1)[0].strip()
            if t and len(t) <= 50 and ":" in info.get("description", "")[:60]:
                layer.alias = re.sub(r"\s*\(.*$", "", t).strip() or layer.alias
        dfields = {f["name"].upper(): f for f in info.get("fields", [])}
        for fd in layer.fields:
            df = dfields.get(fd.name.upper())
            if not df:
                continue
            fd.description = df.get("description", "")
            fd.responsibility = df.get("responsibility", "")
            fd.contractor_mandatory = bool(df.get("mandatory_contractor")) and fd.responsibility.startswith("CONTRACTOR")
            fd.allowed_values = list(df.get("values", []))
            dv = df.get("default")
            if dv is not None and fd.default is None and fd.field_type.upper() not in _NUMERIC:
                # a value to fill, never a change of the template's field definition
                dom = schema.get_domain(fd.domain)
                if dom is None or dom.domain_type != "coded":
                    fd.value_default = dv
                else:
                    hit = next((c for c, d in dom.coded_values.items()
                                if str(c).lower() == dv.lower() or str(d).lower() == dv.lower()), None)
                    if hit is not None:
                        fd.value_default = hit
            al = [a.upper() for a in df.get("aliases", {})]
            if al:
                cur = layer.attribute_aliases.setdefault(fd.name, [])
                for a in al:
                    if a not in cur and a != fd.name.upper():
                        cur.append(a)
    return n
