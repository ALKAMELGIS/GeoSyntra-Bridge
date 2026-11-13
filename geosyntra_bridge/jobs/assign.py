"""
Map-based feature assignment — the interactive layer on top of the automatic fill.

The engine is unchanged: it fills every feature as before. This module only
  * reports, per feature, what is still missing (status for the map colours),
  * saves the values the engineer enters for ONE feature:
      validate (same coerce_and_validate as the engine) -> write that row of the delivered
      geodatabase by OBJECTID (read back and compared) -> persist the edit -> update caches partially.

Edits are kept in feature_edits.json keyed "<feature class>|<CAD handle>", and the engine applies them on every
later validation / generation, so a re-generated geodatabase carries them too.
"""
from __future__ import annotations

import copy
import os
import re
import threading
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from ..engine.model import IssueLog, TargetFeature
from ..schema.models import SYSTEM_FIELDS
from ..validation.attributes import EMPTY, check_missing, coerce_and_validate

EDITS = "feature_edits.json"
_locks: Dict[str, threading.Lock] = {}
_glock = threading.Lock()
_pkg_timers: Dict[str, threading.Timer] = {}


class AssignError(Exception):
    def __init__(self, msg: str, status: int = 400, detail: Any = None):
        super().__init__(msg)
        self.status = status
        self.detail = detail


def _lock(job_id: str) -> threading.Lock:
    with _glock:
        return _locks.setdefault(job_id, threading.Lock())


def edit_key(f: TargetFeature) -> str:
    return f"{f.target}|{f.source.handle}"


def _placeholders(f: TargetFeature) -> List[str]:
    out: List[str] = []
    for r in f.remarks:
        if r.startswith("placeholders in NOT NULL fields:"):
            out += [x.strip() for x in r.split(":", 1)[1].split(",") if x.strip()]
    return out


def _read_only(layer, fd) -> bool:
    from ..validation.attributes import ELEVATION_FIELDS
    n = fd.name.upper()
    return (n in SYSTEM_FIELDS or n in ELEVATION_FIELDS        # levels come from the drawing / DEM, never typed or n in ("OBJECTID", "GLOBALID", "SHAPE", "SHAPE_LENGTH", "SHAPE_AREA")
            or fd.field_type == "GUID" or (layer.subtype_field and fd.name == layer.subtype_field))


def field_specs(schema, layer) -> List[Dict[str, Any]]:
    out = []
    for fd in layer.fields:
        if fd.name.upper() in SYSTEM_FIELDS:
            continue
        dom = schema.get_domain(fd.domain)
        out.append({"name": fd.name, "alias": fd.alias or fd.name, "type": fd.field_type, "length": fd.length,
                    "required": bool(fd.required or not fd.nullable or fd.contractor_mandatory),
                    "not_null": not fd.nullable, "read_only": _read_only(layer, fd),
                    "description": (fd.description or "")[:200],
                    "domain": [[c, d] for c, d in dom.coded_values.items()] if dom and dom.coded_values else None,
                    "range": [dom.min_value, dom.max_value] if dom and dom.domain_type == "range" else None})
    return out


_NEED: Dict[int, List[str]] = {}


def _needed(layer) -> List[str]:
    k = id(layer)
    if k not in _NEED:
        _NEED[k] = [fd.name for fd in layer.fields if fd.name.upper() not in SYSTEM_FIELDS and not _read_only(layer, fd)
                    and (fd.required or not fd.nullable or fd.contractor_mandatory)]
    return _NEED[k]


def _missing(f: TargetFeature, layer) -> List[str]:
    """Required fields that are really empty (automatic values are listed separately, to review)."""
    return [n for n in _needed(layer) if f.attrs.get(n) in EMPTY]


def _auto(f: TargetFeature, layer) -> List[str]:
    """Required fields holding an automatic value (N/A, 0, a default code): correct when you know better."""
    names = {fd.name for fd in layer.fields if not _read_only(layer, fd)}
    return [n for n in _placeholders(f) if n in names]


def feature_state(f: TargetFeature, layer, edits: Dict[str, Any], err_fids: set) -> Dict[str, Any]:
    e = edits.get(edit_key(f)) or {}
    missing = _missing(f, layer)
    if e.get("status") == "skipped":
        st = "skipped"
    elif f.failed or f.fid in err_fids:
        st = "error"
    elif e.get("status") == "completed" or not missing:
        st = "completed"
    else:
        st = "incomplete"
    return {"state": st, "missing": missing, "auto": _auto(f, layer), "edited": bool(e), "saved_at": e.get("at")}


def _err_fids(res) -> set:
    return {i.feature_id for i in res.issues.items if i.feature_id is not None and i.severity == "error"}


_RCACHE: Dict[str, tuple] = {}


def _results_file(mgr, job_id: str) -> Dict[str, Any]:
    """results.json, read once per change (it can be tens of MB on large drawings)."""
    p = os.path.join(mgr.jdir(job_id), "results.json")
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return {}
    hit = _RCACHE.get(job_id)
    if hit and (hit[0] == mt or (hit[0] is None and job_id in _PENDING)):
        return hit[1]
    r = mgr._read(job_id, "results.json") or {}
    _RCACHE[job_id] = (mt, r)
    return r


def _oid_index(mgr, job_id: str) -> Dict[str, int]:
    """OBJECTIDs of the delivered geodatabase(s), from the last generation (survives a restart)."""
    r = _results_file(mgr, job_id)
    if not (r.get("gdb") or {}).get("geodatabases") and not (r.get("gdb") or {}).get("name"):
        return {}
    return {f"{x['target']}|{x['handle']}": x["objectid"] for x in r.get("features") or [] if x.get("objectid")}


def _gdb_for(mgr, job_id: str, schema, target: str) -> Optional[str]:
    from ..schema.composite import part_of
    r = _results_file(mgr, job_id)
    g = r.get("gdb") or {}
    out = os.path.join(mgr.jdir(job_id), "output")
    dbs = g.get("geodatabases") or ([{"name": g["name"], "schema_id": schema.schema_id}] if g.get("name") else [])
    sid = part_of(schema, target).schema_id
    for d in dbs:
        if d.get("schema_id") == sid or len(dbs) == 1:
            p = os.path.join(out, d["name"])
            if os.path.isdir(p):
                return p
    return None


def overview(mgr, job_id: str) -> Dict[str, Any]:
    res = mgr._results.get(job_id) or mgr._convert(job_id, None)
    schema = res.schema
    edits = mgr._read(job_id, EDITS) or {}
    oids = _oid_index(mgr, job_id)
    errs = _err_fids(res)
    rows, classes = [], {}
    counts = {"completed": 0, "incomplete": 0, "error": 0, "skipped": 0}
    for f in res.features:
        if not f.target:
            continue
        layer = schema.get_layer(f.target)
        if layer is None:
            continue
        if f.target not in classes:
            classes[f.target] = {"alias": layer.alias or layer.name, "geometry": layer.geometry_type,
                                 "dataset": layer.feature_dataset, "fields": field_specs(schema, layer)}
        s = feature_state(f, layer, edits, errs)
        s.pop("auto", None)          # per-feature detail only — keeps the list light on large drawings
        if not s.get("saved_at"):
            s.pop("saved_at", None)
        counts[s["state"]] += 1
        rows.append({"fid": f.fid, "objectid": oids.get(edit_key(f)), "target": f.target, "layer": f.source.layer,
                     "handle": f.source.handle, **s})
    gdbs = sorted({os.path.basename(p) for p in (_gdb_for(mgr, job_id, schema, t) for t in classes) if p})
    return {"features": rows, "classes": classes, "counts": {**counts, "total": len(rows)},
            "gdb": {"generated": bool(oids), "names": gdbs}}


def detail(mgr, job_id: str, fid: int) -> Dict[str, Any]:
    from ..engine.conversion import feature_detail
    res = mgr._results.get(job_id) or mgr._convert(job_id, None)
    d = feature_detail(res, fid)
    if d is None:
        raise AssignError("feature not found", 404)
    f = next(x for x in res.features if x.fid == fid)
    layer = res.schema.get_layer(f.target)
    edits = mgr._read(job_id, EDITS) or {}
    d.update(feature_state(f, layer, edits, _err_fids(res)))
    d["placeholders"] = _placeholders(f)
    oid = _oid_index(mgr, job_id).get(edit_key(f))
    d["objectid"] = oid
    d["gdb"] = None
    gdb = _gdb_for(mgr, job_id, res.schema, f.target) if oid else None
    if gdb:      # the row as stored in the delivered geodatabase — the source of truth once generated
        from ..gdb.row_update import RowUpdateError, read_row
        try:
            d["gdb"] = {"name": os.path.basename(gdb), "objectid": oid,
                        "values": read_row(gdb, f.target, oid, [x["name"] for x in field_specs(res.schema, layer)])}
        except RowUpdateError as e:
            d["gdb"] = {"name": os.path.basename(gdb), "objectid": oid, "error": str(e)}
    return d


def _json_val(v):
    return v.isoformat() if isinstance(v, (datetime, date)) else v


def save(mgr, job_id: str, fid: int, fields: Dict[str, Any], status: str = "completed", _rezip_gdb: bool = True,
         _locked: bool = False) -> Dict[str, Any]:
    if status not in ("completed", "skipped", "draft"):
        raise AssignError("status must be completed, skipped or draft")
    lk = _lock(job_id)
    if not _locked and not lk.acquire(timeout=30):
        raise AssignError("another save is still running for this job", 409)
    try:
        res = mgr._results.get(job_id) or mgr._convert(job_id, None)
        schema = res.schema
        f = next((x for x in res.features if x.fid == fid), None)
        if f is None or not f.target:
            raise AssignError("feature not found", 404)
        layer = schema.get_layer(f.target)
        specs = {s["name"]: s for s in field_specs(schema, layer)}
        bad = [k for k in fields if k not in specs or specs[k]["read_only"]]
        if bad:
            raise AssignError(f"not editable for {f.target}: {', '.join(bad)}", 422)
        clean = {k: (None if v is None or (isinstance(v, str) and not v.strip()) else (v.strip() if isinstance(v, str) else v))
                 for k, v in fields.items()}

        # 1. validate on a copy, with the engine's own coercion + domain rules
        tmp = copy.copy(f)
        tmp.attrs = {**f.attrs, **clean}
        tmp.issue_ids, tmp.rejected, tmp.remarks = [], {}, []
        log = IssueLog()
        coerce_and_validate(tmp, layer, schema, log)
        problems = [{"field": i.field, "message": i.message, "suggestion": i.suggestion}
                    for i in log.items if i.field in clean and i.severity in ("error", "warning")
                    and i.code in ("type_mismatch", "domain_violation", "value_truncated")]
        if problems:
            raise AssignError("; ".join(p["message"].split(": ", 1)[-1] for p in problems), 422, problems)
        ph = set(_placeholders(f)) - set(clean)
        if status == "completed":
            still = [n for n in _missing(tmp, layer) if n not in ph or n in clean]
            still = [n for n in still if tmp.attrs.get(n) in EMPTY]
            if still:
                raise AssignError("required: " + ", ".join(specs[n]["alias"] for n in still), 422,
                                  [{"field": n, "message": "required"} for n in still])
        values = {k: tmp.attrs.get(k) for k in clean}

        # 2. the delivered geodatabase, when there is one — the row is located by OBJECTID and read back
        written = None
        key = edit_key(f)
        oid = _oid_index(mgr, job_id).get(key)
        gdb = _gdb_for(mgr, job_id, schema, f.target) if oid else None
        if gdb and values:
            from ..gdb.row_update import RowUpdateError, update_row
            ftypes = {fd.name: fd.field_type for fd in layer.fields}
            try:
                written = update_row(gdb, f.target, oid, values, ftypes)
            except RowUpdateError as e:
                raise AssignError(f"not saved to {os.path.basename(gdb)}: {e}", 500)
            if not written.get("geometry_unchanged", True):
                raise AssignError("geometry changed during the attribute update — save refused", 500)
            written["gdb"] = os.path.basename(gdb)
            written["_path"] = gdb
            if _rezip_gdb:
                _rezip(mgr, job_id, gdb)

        # 3. persist the edit (only after the geodatabase accepted it)
        edits = mgr._read(job_id, EDITS) or {}
        prev = edits.get(key) or {}
        e = {**prev, "fid": fid, "fields": {**(prev.get("fields") or {}), **{k: _json_val(v) for k, v in values.items()}},
             "status": status if status != "draft" else prev.get("status"),
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        edits[key] = e
        mgr._write(job_id, EDITS, edits)

        # 4. this feature only, in memory and in the cached results — no full re-run
        for k, v in values.items():
            if v in EMPTY:
                f.attrs.pop(k, None)
            else:
                f.attrs[k] = v
            f.rejected.pop(k, None)
        f.remarks = [r for r in f.remarks if not r.startswith("placeholders in NOT NULL fields:")]
        if ph:
            f.remarks.append("placeholders in NOT NULL fields: " + ", ".join(sorted(ph)))
        if "edited on map" not in f.remarks:
            f.remarks.append("edited on map")
        if f.failed and (f.failure or "").startswith("NOT NULL") and f.geom is not None and \
                not [fd.name for fd in layer.fields if not fd.nullable and fd.name.upper() not in SYSTEM_FIELDS
                     and f.attrs.get(fd.name) in EMPTY]:
            f.failed, f.write, f.failure = False, True, None
        old = set(f.issue_ids)
        res.issues.items = [i for i in res.issues.items if i.id not in old]
        f.issue_ids = []
        coerce_and_validate(f, layer, schema, res.issues)
        check_missing(f, layer, res.issues, [])
        state = feature_state(f, layer, edits, _err_fids(res))
        _patch_caches(mgr, job_id, res, f, old, state)
        if written and _rezip_gdb:
            written.pop("_path", None)
        return {"ok": True, "fid": fid, "objectid": oid, "target": f.target, "values": {k: _json_val(v) for k, v in values.items()},
                "gdb": written, "pending_generate": written is None,
                **state}
    finally:
        if not _locked:
            lk.release()


def save_many(mgr, job_id: str, fids: List[int], fields: Dict[str, Any]) -> Dict[str, Any]:
    """The same values for several features (a layer, or a multi-selection) — Attributes pane, layer / multi mode.
    Each feature is validated like a single save; the geodatabase is re-packed once at the end."""
    if not fields:
        raise AssignError("no values to apply")
    lk = _lock(job_id)
    if not lk.acquire(timeout=60):
        raise AssignError("another save is still running for this job", 409)
    gdbs, out, failed = set(), [], []
    try:
        for fid in fids:
            try:
                r = save(mgr, job_id, int(fid), fields, "draft", _rezip_gdb=False, _locked=True)
            except AssignError as e:
                failed.append({"fid": fid, "error": str(e)})
                if e.status == 422 and len(failed) == len(fids):
                    raise
                continue
            if r.get("gdb") and r["gdb"].get("_path"):
                gdbs.add(r["gdb"].pop("_path"))
            out.append({"fid": r["fid"], "state": r["state"], "missing": r["missing"]})
        for g in gdbs:
            _rezip(mgr, job_id, g)
        return {"saved": len(out), "features": out, "failed": failed}
    finally:
        lk.release()


def unique_values(mgr, job_id: str, target: str, field: str, limit: int = 60) -> Dict[str, Any]:
    """Values already used in a field of one feature class (Attributes pane → Unique values), most frequent first."""
    from collections import Counter
    res = mgr._results.get(job_id) or mgr._convert(job_id, None)
    cnt = Counter(str(_json_val(f.attrs.get(field))) for f in res.features
                  if f.target == target and f.attrs.get(field) not in EMPTY)
    return {"field": field, "values": [{"value": v, "count": n} for v, n in cnt.most_common(limit)], "distinct": len(cnt)}


_PENDING: Dict[str, Dict[str, Any]] = {}
_FLUSH: Dict[str, threading.Timer] = {}


def _pending(mgr, job_id: str, name: str):
    """The cached copy of preview.geojson / results.json being patched (loaded once, written after a pause)."""
    p = _PENDING.setdefault(job_id, {})
    if name not in p:
        p[name] = mgr._read(job_id, name)
    return p[name]


def flush(mgr, job_id: str):
    """Write the patched files (also called before anything reads them in full, e.g. generation)."""
    with _glock:
        docs = _PENDING.pop(job_id, None)
        t = _FLUSH.pop(job_id, None)
    if t:
        t.cancel()
    for name, data in (docs or {}).items():
        if data is not None:
            mgr._write(job_id, name, data)


def _patch_caches(mgr, job_id, res, f, old_issue_ids, state):
    """This feature only, in the cached preview and results — written once the engineer pauses (2 s), so
    Save & Next stays instant on drawings with tens of thousands of features."""
    from ..geometry.crs import transformer
    status = "failed" if f.failed else ("skipped" if not f.write else "ok")
    sev = None
    rank = {"error": 3, "warning": 2, "info": 1}
    mine = [i for i in res.issues.items if i.feature_id == f.fid]
    for i in mine:
        if sev is None or rank[i.severity] > rank[sev]:
            sev = i.severity
    pv = _pending(mgr, job_id, "preview.geojson")
    if pv:
        for ft in pv.get("features") or []:
            p = ft.get("properties") or {}
            if p.get("fid") == f.fid:
                p.update(status=status, severity=sev)
    rb = _pending(mgr, job_id, "results.json")
    if rb:
        oid = None
        for x in rb.get("features") or []:
            if x.get("fid") == f.fid:
                x.update(status=status, failure=f.failure, remarks=list(dict.fromkeys(f.remarks)), issue_count=len(f.issue_ids))
                oid = x.get("objectid")
        to84 = transformer(res.target_epsg, 4326) if res.target_epsg else None
        new = []
        for it in mine:
            d = it.to_dict()
            try:
                d["lonlat"] = list(to84.transform(*it.location)) if (to84 and it.location) else (list(it.location) if it.location else None)
            except Exception:  # noqa: BLE001
                d["lonlat"] = None
            d["objectid"] = oid
            new.append(d)
        rb["issues"] = [i for i in rb.get("issues") or [] if i.get("id") not in old_issue_ids] + new
        _RCACHE[job_id] = (None, rb)          # what the other screens of this process read until the flush
    with _glock:
        t = _FLUSH.pop(job_id, None)
        if t:
            t.cancel()
        t = threading.Timer(2.0, flush, args=(mgr, job_id))
        t.daemon = True
        _FLUSH[job_id] = t
        t.start()


def _rezip(mgr, job_id: str, gdb: str):
    from .common import _zip_dir
    _zip_dir(gdb, gdb + ".zip")
    # the full submission package is rebuilt once the engineer pauses (not on every save)
    t = _pkg_timers.pop(job_id, None)
    if t:
        t.cancel()
    t = threading.Timer(4.0, _repack, args=(mgr, job_id))
    t.daemon = True
    _pkg_timers[job_id] = t
    t.start()


def _repack(mgr, job_id: str):
    import zipfile
    try:
        st = mgr.state(job_id)
        pkg = (st.get("outputs") or {}).get("package")
        if not pkg:
            return
        out = os.path.join(mgr.jdir(job_id), "output")
        path = os.path.join(out, pkg)
        if not os.path.exists(path):
            return
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
        gdb_dirs = sorted({n.split("/")[0] for n in names if re.match(r"[^/]+\.gdb/", n)})
        tmp = path + ".tmp"
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
            for n in names:
                if n.split("/")[0] in gdb_dirs:
                    continue
                dst.writestr(src.getinfo(n), src.read(n))
            for d in gdb_dirs:
                root = os.path.join(out, d)
                for r_, _, fs in os.walk(root):
                    for fn in fs:
                        full = os.path.join(r_, fn)
                        dst.write(full, os.path.relpath(full, out))
        os.replace(tmp, path)
        mgr.log(job_id, "submission package refreshed after map edits")
    except Exception as e:  # noqa: BLE001
        mgr.log(job_id, f"package refresh failed: {e}")
