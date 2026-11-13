"""
Attribute population (field mapping) and schema validation of values:
field types, lengths, coded/range domains, subtypes, required fields.
Invalid values are never written silently: the field is left empty, the
original value is preserved in REMARKS and an issue with a suggested
correction is logged.
"""
from __future__ import annotations

import ast
import difflib
import functools
import re
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from ..engine.model import IssueLog, TargetFeature
from ..schema.models import DomainDef, FieldDef, LayerDef, SchemaDefinition, SYSTEM_FIELDS

_NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%d/%m/%y", "%Y%m%d")
EMPTY = (None, "")


def _geometry_value(f: TargetFeature, what: str) -> Any:
    g = f.geom
    if g is None:
        return None
    if what == "x":
        return round(f.location()[0], 4)
    if what == "y":
        return round(f.location()[1], 4)
    if what in ("z", "start_z", "end_z", "dem_ground"):     # DEM elevation step (or real CAD levels)
        from ..elevation.engine import z_value
        return z_value(f, what)          # elevation step result; None (reported) when unknown — never 0
    if what == "length":
        return round(g.length, 3) if g.length else None
    if what == "area":
        return round(g.area, 3) if g.area else None
    return None


def _cad_value(f: TargetFeature, what: str) -> Any:
    e = f.source
    return {"layer": e.layer, "handle": e.handle, "color": e.color, "linetype": e.linetype,
            "block_name": e.block_name, "text": e.text, "rotation": e.rotation, "elevation": e.elevation,
            "entity_type": e.dxftype}.get(what)


def _resolve(f: TargetFeature, spec: Dict[str, Any], fname: str) -> Tuple[Any, str]:
    src = spec.get("source", "none")
    val = None
    if src == "label":
        vals = f.label_values.get(fname.upper())
        if vals:
            vals.sort(key=lambda v: v[1])
            val = vals[0][0]
    elif src == "block_attr":
        tag = (spec.get("value") or fname).upper()
        val = f.source.attributes.get(tag)
        if val is not None:
            val = val.strip()
    elif src == "constant":
        val = spec.get("value")
    elif src in ("geometry", "elevation"):
        val = _geometry_value(f, spec.get("value") or "")
    elif src == "cad":
        val = _cad_value(f, spec.get("value") or "layer")
    elif src == "today":
        val = date.today().isoformat()
    if val in EMPTY and spec.get("fallback"):
        fb = spec["fallback"]
        kind, _, arg = fb.partition(":")
        return _resolve(f, {"source": kind, "value": arg or None}, fname)[0], fb
    return val, src


def populate(f: TargetFeature, layer: LayerDef, field_map: Dict[str, Dict[str, Any]]):
    """Fill raw (uncoerced) values from the field mapping."""
    for fd in layer.fields:
        u = fd.name.upper()
        if u in SYSTEM_FIELDS:
            continue
        spec = field_map.get(fd.name) or field_map.get(u) or {"source": "none"}
        if spec.get("source") == "subtype":
            if f.subtype is not None:
                f.attrs[fd.name] = f.subtype
            continue
        if spec.get("source", "none") == "none" and fd.field_type == "DATE":
            spec = {"source": "today"}      # every date field the drawing does not fill = the day of the conversion
        val, _ = _resolve(f, spec, fd.name)
        if val not in EMPTY:
            f.attrs[fd.name] = val
    # subtype defaults, then field defaults
    st = next((s for s in layer.subtypes if s.code == f.subtype), None) if layer.subtypes else None
    if st:
        for k, v in st.defaults.items():
            if f.attrs.get(k) in EMPTY:
                f.attrs[k] = v
    for fd in layer.fields:
        if fd.default is not None and f.attrs.get(fd.name) in EMPTY and fd.name.upper() not in SYSTEM_FIELDS:
            f.attrs[fd.name] = fd.default
        elif fd.value_default is not None and f.attrs.get(fd.name) in EMPTY and fd.name.upper() not in SYSTEM_FIELDS:
            f.attrs[fd.name] = fd.value_default      # DMT data dictionary default (e.g. STATUS = Active)


def _to_number(v: Any, integer: bool) -> Optional[float]:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(round(v)) if integer else float(v)
    m = _NUM.search(str(v))
    if not m:
        return None
    n = float(m.group(0).replace(",", "."))
    return int(round(n)) if integer else n


def _to_date(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _domain_match(dom: DomainDef, v: Any) -> Tuple[bool, Any, Optional[str]]:
    """-> (ok, normalised value, suggestion)"""
    if dom.domain_type == "range":
        try:
            n = float(v)
        except (TypeError, ValueError):
            return False, v, None
        lo = dom.min_value if dom.min_value is not None else float("-inf")
        hi = dom.max_value if dom.max_value is not None else float("inf")
        return (lo <= n <= hi), v, f"value must be between {dom.min_value} and {dom.max_value}"
    codes = dom.coded_values
    sv = str(v).strip()
    if sv in codes:
        return True, v, None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        for c in codes:
            try:
                if float(c) == float(v):
                    return True, (int(float(c)) if dom.field_type in ("INTEGER", "SMALLINTEGER") else c), None
            except ValueError:
                continue
    up = sv.upper()
    for c, desc in codes.items():
        if c.upper() == up or str(desc).upper() == up:
            return True, (int(c) if dom.field_type in ("INTEGER", "SMALLINTEGER") and c.lstrip("-").isdigit() else c), None
    return False, v, _suggest(dom.name, tuple(codes.items()), sv)


@functools.lru_cache(maxsize=20000)
def _suggest(_dom: str, items: tuple, sv: str) -> Optional[str]:
    """Closest code of a coded domain — computed once per (domain, value), not once per feature."""
    codes = dict(items)
    up = sv.upper()
    pool = list(codes) + [str(d) for d in codes.values()]
    close = difflib.get_close_matches(sv, pool, n=1, cutoff=0.6) or \
        difflib.get_close_matches(up, [p.upper() for p in pool], n=1, cutoff=0.6)
    if not close:
        return None
    hit = close[0]
    code = next((c for c, d in codes.items() if c.upper() == hit.upper() or str(d).upper() == hit.upper()), hit)
    return f"did you mean '{code}' ({codes.get(code, '')})?"


def coerce_and_validate(f: TargetFeature, layer: LayerDef, schema: SchemaDefinition, issues: IssueLog):
    st = next((s for s in layer.subtypes if s.code == f.subtype), None) if layer.subtypes else None
    loc = f.location()
    sref = f.source.source_ref()

    def issue(sev, cat, code, msg, fld, sugg=""):
        it = issues.add(sev, cat, code, msg, suggestion=sugg, target_layer=f.target, feature_id=f.fid,
                        field=fld, location=loc, source=sref)
        f.issue_ids.append(it.id)

    if layer.subtype_field and layer.subtypes:
        codes = {s.code for s in layer.subtypes}
        if f.subtype is None:
            issue("warning", "domain", "subtype_missing",
                  f"{f.target} #{f.fid}: subtype could not be determined", layer.subtype_field,
                  f"set a subtype for CAD layer '{f.source.layer}' in the layer mapping ({', '.join(f'{s.code}={s.name}' for s in layer.subtypes)})")
        elif f.subtype not in codes:
            issue("error", "domain", "subtype_invalid", f"{f.target} #{f.fid}: subtype {f.subtype} is not defined",
                  layer.subtype_field)
            f.attrs.pop(layer.subtype_field, None)

    for fd in layer.fields:
        name = fd.name
        if name.upper() in SYSTEM_FIELDS:
            continue
        v = f.attrs.get(name)
        if v in EMPTY:
            f.attrs.pop(name, None)
            continue
        t = fd.field_type
        new: Any = v
        if t in ("INTEGER", "SMALLINTEGER"):
            new = _to_number(v, True)
            if new is not None and t == "SMALLINTEGER" and not -32768 <= new <= 32767:
                new = None
        elif t in ("DOUBLE", "FLOAT"):
            new = _to_number(v, False)
        elif t == "DATE":
            new = _to_date(v)
        elif t == "GUID":
            try:
                new = "{" + str(uuid.UUID(str(v))).upper() + "}"
            except ValueError:
                new = None
        else:
            new = str(v).strip()
            if fd.length and len(new) > fd.length:
                issue("warning", "domain", "value_truncated",
                      f"{f.target} #{f.fid}: {name} value '{new}' exceeds {fd.length} characters and was truncated", name,
                      "shorten the source text or extend the field length in the schema")
                f.remarks.append(f"{name} truncated (original '{new}')")
                new = new[:fd.length]
        if new is None:
            issue("error", "domain", "type_mismatch",
                  f"{f.target} #{f.fid}: {name} value '{v}' is not a valid {t}", name,
                  f"correct the CAD label/attribute to a {t.lower()} value")
            f.remarks.append(f"{name} invalid '{v}'")
            f.attrs.pop(name, None)
            continue
        dname = (st.field_domains.get(name) if st else None) or fd.domain
        dom = schema.get_domain(dname)
        if dom:
            ok, norm, sugg = _domain_match(dom, new)
            if not ok and "NAME" in name.upper():
                # names are taken as written in the drawing, listed in the template or not (names differ per project)
                issue("info", "domain", "name_not_in_list",
                      f"{f.target} #{f.fid}: {name} = '{new}' kept as written in the drawing (not in the list '{dom.name}')",
                      name, sugg or "")
                f.attrs[name] = new
                continue
            if not ok:
                issue("error", "domain", "domain_violation",
                      f"{f.target} #{f.fid}: {name} = '{new}' is not in domain '{dom.name}'", name,
                      sugg or f"use one of: {', '.join(list(dom.coded_values)[:8])}{'…' if len(dom.coded_values) > 8 else ''}"
                      if dom.domain_type == "coded" else (sugg or ""))
                f.remarks.append(f"{name} '{new}' not in domain {dom.name}")
                f.rejected[name] = new
                f.attrs.pop(name, None)
                continue
            new = norm
        f.attrs[name] = new



def check_missing(f: TargetFeature, layer: LayerDef, issues: IssueLog, expected_fields: List[str]):
    """Required / expected attributes — run after network derivation and computed fields."""
    loc = f.location()
    sref = f.source.source_ref()

    def issue(sev, code, msg, fld, sugg):
        it = issues.add(sev, "missing_attribute", code, msg, suggestion=sugg, target_layer=f.target,
                        feature_id=f.fid, field=fld, location=loc, source=sref)
        f.issue_ids.append(it.id)

    missing_req = [fd.name for fd in layer.fields if fd.required and f.attrs.get(fd.name) in EMPTY
                   and fd.name.upper() not in SYSTEM_FIELDS]
    for name in missing_req:
        issue("warning", "required_missing", f"{f.target} #{f.fid}: required field {name} has no value", name,
              "add the value as a CAD label/block attribute or map a source for it in Field Mapping")
        f.remarks.append(f"{name} missing")
    missing_exp = [n for n in expected_fields if f.attrs.get(n) in EMPTY and n not in missing_req and n not in f.rejected]
    if missing_exp:
        issue("info", "attribute_missing", f"{f.target} #{f.fid}: no value found for {', '.join(missing_exp)}",
              ",".join(missing_exp), "no matching label or block attribute was found near this feature")
        f.remarks.append("missing " + "/".join(missing_exp))


def check_contractor_mandatory(features, schema, issues: IssueLog) -> Dict[str, Dict[str, int]]:
    """DMT data dictionary: fields the contractor must deliver. One issue per class and field (not per feature);
    fields the municipal engineer / LRS / Osool tools fill later are never reported."""
    from collections import defaultdict
    total: Dict[str, int] = defaultdict(int)
    empty: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for f in features:
        if f.failed or not f.write:
            continue
        layer = schema.get_layer(f.target)
        total[f.target] += 1
        for fd in layer.fields:
            if fd.contractor_mandatory and fd.name.upper() not in SYSTEM_FIELDS and f.attrs.get(fd.name) in EMPTY:
                empty[f.target][fd.name] += 1
    out: Dict[str, Dict[str, int]] = {}
    for tgt, flds in sorted(empty.items()):
        out[tgt] = dict(flds)
        layer = schema.get_layer(tgt)
        for name, n in sorted(flds.items()):
            fd = layer.get_field(name)
            vals = f" Allowed values: {', '.join(fd.allowed_values[:8])}." if fd and fd.allowed_values else ""
            issues.add("warning", "missing_attribute", "contractor_mandatory_missing",
                       f"{tgt}.{name} is mandatory for the contractor (DMT data dictionary) — {n} of {total[tgt]} "
                       f"feature(s) have no value" + (f". {fd.description[:140]}" if fd and fd.description else ""),
                       suggestion="add it through CAD labels / block attributes, the CAD key, or a project constant "
                                  "in Field Mapping" + vals,
                       target_layer=tgt, field=name)
    return out


_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Constant, ast.Add, ast.Sub,
            ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd, ast.Call)
_FUNCS = {"abs": abs, "round": round, "min": min, "max": max}


def _safe_eval(expr: str, env: Dict[str, Any]) -> Optional[float]:
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"unsupported expression element {type(node).__name__}")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _FUNCS):
            raise ValueError("only abs/round/min/max calls are allowed")
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - set(_FUNCS)
    vals = {}
    for n in names:
        v = env.get(n)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return None
        vals[n] = v
    try:
        return eval(compile(tree, "<computed>", "eval"), {"__builtins__": {}}, {**_FUNCS, **vals})  # noqa: S307
    except ZeroDivisionError:
        return None


def apply_computed(f: TargetFeature, layer: LayerDef):
    for fname, expr in layer.computed_fields.items():
        if f.attrs.get(fname) not in EMPTY:
            continue
        try:
            v = _safe_eval(expr, f.attrs)
        except (ValueError, SyntaxError):
            continue
        if v is None:
            continue
        fd = layer.get_field(fname)
        if fd and fd.field_type in ("INTEGER", "SMALLINTEGER"):
            v = int(round(v))
        else:
            v = round(float(v), 3)
        f.attrs[fd.name if fd else fname] = v
        f.remarks.append(f"{fname} computed ({expr})")


NEUTRAL_CODES = ("N/A", "NA", "Undefined", "Unknown", "Other", "None", "Not Applicable")


ELEVATION_FIELDS = {"ZCOORD", "ZCOORD_START", "ZCOORD_END", "GROUND_LEVEL", "AVG_GLEVEL", "COVER_LEVEL",
                    "INVERT_LEVEL", "BOTTOM_LEVEL", "TOP_LEVEL", "BASE_LEVEL", "Z", "ELEVATION"}


# NOT NULL coded fields whose domain has no neutral code: the value an ADM as-built submission normally carries.
# Written automatically (reported as a default to review) instead of blocking the feature.
FIELD_DEFAULTS = {
    "DATA_SOURCE": ("AS BUILT", "ASBUILT"),
    "REGION": ("ADM",),
    "IR_WATER_POINT": ("TSE Water", "TSE"),
    "PLANTTYPE": ("Ornamental",),
}


def fill_non_nullable(features: List[TargetFeature], schema: SchemaDefinition, issues: IssueLog,
                      policy: str = "placeholder", elevation_nodata: Optional[float] = None,
                      defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fields declared NOT NULL in the schema must receive a value or the GDB rejects the feature.
    policy 'placeholder': keep a domain-refused source value if any, else write a neutral placeholder
    (neutral domain code, else the usual as-built value of FIELD_DEFAULTS / 'N/A' / 0 / today's date), recorded in
    REMARKS and the report.
    If no neutral value is valid (coded domain without N/A, Other ...), or policy is 'reject',
    the feature is not written and reported as failed — the user can set a constant in Field Mapping."""
    agg: Dict[tuple, int] = {}
    blocked: Dict[tuple, List[int]] = {}
    kept: Dict[tuple, int] = {}
    for f in features:
        if f.failed or not f.write:
            continue
        layer = schema.get_layer(f.target)
        filled = []
        for fd in layer.fields:
            if fd.nullable or fd.name.upper() in SYSTEM_FIELDS or f.attrs.get(fd.name) not in EMPTY:
                continue
            if fd.name in f.rejected:
                f.attrs[fd.name] = f.rejected[fd.name]
                kept[(f.target, fd.name)] = kept.get((f.target, fd.name), 0) + 1
                f.remarks.append(f"{fd.name} out-of-domain value kept (field is NOT NULL)")
                continue
            val = None
            if fd.name.upper() in ELEVATION_FIELDS:
                # an elevation is never invented: the explicit no-data value chosen by the user, or blocked
                if elevation_nodata is not None:
                    f.attrs[fd.name] = elevation_nodata
                    filled.append(fd.name)
                    agg[(f.target, fd.name, str(elevation_nodata))] = agg.get((f.target, fd.name, str(elevation_nodata)), 0) + 1
                    continue
                blocked.setdefault((f.target, fd.name), []).append(f.fid)
                continue
            if policy == "placeholder":
                dom = schema.get_domain(fd.domain)
                if dom and dom.domain_type == "coded":
                    up = {c.upper(): c for c in dom.coded_values}
                    up.update({str(v).upper(): c for c, v in dom.coded_values.items()})
                    val = next((up[n.upper()] for n in NEUTRAL_CODES if n.upper() in up), None)
                    if val is None:      # no neutral code: the usual value for an as-built submission
                        wanted = ([defaults[fd.name.upper()]] if defaults and defaults.get(fd.name.upper()) else []) + \
                                 list(FIELD_DEFAULTS.get(fd.name.upper(), ()))
                        val = next((up[str(n).upper()] for n in wanted if str(n).upper() in up), None)
                        if val is None and dom.coded_values:   # never block a feature: first value of the list, to review
                            val = next(iter(dom.coded_values))
                    if val is not None and fd.field_type in ("INTEGER", "SMALLINTEGER"):
                        val = int(val) if str(val).lstrip("-").isdigit() else None
                elif fd.field_type in ("INTEGER", "SMALLINTEGER", "DOUBLE", "FLOAT"):
                    val = 0
                elif fd.field_type == "DATE":      # dates are filled with the day of the conversion
                    val = datetime.combine(date.today(), datetime.min.time())
                elif fd.field_type == "GUID":
                    val = "{" + str(uuid.uuid4()).upper() + "}"
                else:
                    val = "N/A"[: fd.length or 3]
            if val is None:
                blocked.setdefault((f.target, fd.name), []).append(f.fid)
                continue
            f.attrs[fd.name] = val
            filled.append(fd.name)
            agg[(f.target, fd.name, str(val))] = agg.get((f.target, fd.name, str(val)), 0) + 1
        if filled:
            f.remarks.append(f"placeholders in NOT NULL fields: {', '.join(filled)}")
    blocked_fids = set()
    for (target, fld), fids in blocked.items():
        blocked_fids |= set(fids)
        dom = schema.get_domain(schema.get_layer(target).get_field(fld).domain)
        choices = ", ".join(list(dom.coded_values)[:8]) if dom and dom.coded_values else ""
        if fld.upper() in ELEVATION_FIELDS:
            issues.add("error", "missing_attribute", "not_null_unfilled",
                       f"{target}.{fld} is NOT NULL in the template but {len(fids)} feature(s) have no elevation "
                       f"— 0 is never written as an elevation",
                       suggestion="Elevation step: prepare a DEM (Copernicus / SRTM / ALOS / GeoTIFF), or choose an "
                                  "explicit no-data elevation value",
                       target_layer=target, field=fld, related=fids[:50])
            continue
        issues.add("error", "missing_attribute", "not_null_unfilled",
                   f"{target}.{fld} is NOT NULL in the schema but {len(fids)} feature(s) have no value"
                   + (" and the domain has no neutral code" if policy == "placeholder" else ""),
                   suggestion=f"set a constant for {fld} in Field Mapping" + (f" (e.g. {choices})" if choices else ""),
                   target_layer=target, field=fld, related=fids[:50])
    for f in features:
        if f.fid in blocked_fids:
            f.write = False
            f.failed = True
            f.failure = "NOT NULL field(s) without value: " + ", ".join(
                sorted(fld for (t, fld), ids in blocked.items() if t == f.target and f.fid in ids))
    for (target, fld, val), n in sorted(agg.items()):
        issues.add("warning", "missing_attribute", "not_null_placeholder",
                   f"{target}.{fld}: '{val}' filled automatically for {n} feature(s) (field is NOT NULL, the drawing has no value) — review",
                   suggestion=f"map a label/attribute or a constant for {fld} in Field Mapping to replace the placeholder",
                   target_layer=target, field=fld)
    for (target, fld), n in kept.items():
        issues.add("warning", "domain", "not_null_out_of_domain_kept",
                   f"{target}.{fld}: {n} out-of-domain source value(s) kept because the field is NOT NULL",
                   suggestion="add the value to the domain or correct the CAD source", target_layer=target, field=fld)
    return {"placeholders": sum(agg.values()), "blocked_features": len(blocked_fids), "kept_out_of_domain": sum(kept.values())}
