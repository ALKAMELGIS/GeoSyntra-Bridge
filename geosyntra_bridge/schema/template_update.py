"""
Updating an approved schema when the municipality publishes a new template geodatabase.

    stage  : upload the new template (.gdb.zip) -> a candidate schema is built from it, the curated CAD knowledge of
             the current schema (classification hints, aliases, label rules, validation / repair settings) is carried
             over, and a change report is produced (classes, geometry, fields, domains & coded values, topology,
             spatial reference) together with the impact on saved mapping profiles, learned mapping rules and open
             jobs, and suggested renames (a class or field that disappeared and a similar one that appeared).
    apply  : an administrator approves -> the current schema JSON and template are archived in
             schemas/_versions/<schema_id>/<version>/ (nothing is deleted), the new template becomes the output
             container, confirmed renames are carried into profiles and learned rules (backed up first). Jobs that were
             validated against the previous version must be validated again before they can be generated.
    restore: any archived version can be made current again (the current one is archived first).

The template itself is never modified: the uploaded zip is stored as received and copied as the output container.
"""
from __future__ import annotations

import difflib
import glob
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .loader import SchemaError, lint_schema, schema_from_dict

TEMPLATE_KEYS = {"name", "fields", "geometry_type", "feature_dataset", "has_z"}   # come from the new template
CURATED_TOP = ("display_name", "discipline", "status", "label_rules", "label_search_radius", "validation", "repair",
               "network", "relationships", "format")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def version_from_name(template_name: str) -> Optional[str]:
    """ADM_HL_Templates_Z40_V10_20012026.gdb -> '10 (20012026)' (same convention as the shipped schemas)."""
    m = re.search(r"_V(\d+)_(\d{8})", template_name or "")
    return f"{m.group(1)} ({m.group(2)})" if m else None


def _safe_ver(v: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(v)).strip("_") or "unversioned"


# --------------------------------------------------------------------------------------------- build / merge
def merge_curated(cur: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """Candidate schema = structure of the new template + curated CAD knowledge of the current schema."""
    out = dict(new)
    out["schema_id"] = cur["schema_id"]
    for k in CURATED_TOP:
        if k in cur:
            out[k] = cur[k]
    out["source"] = "template_gdb"
    v = version_from_name(new.get("source_template", ""))
    out["version"] = v or _bump(cur.get("version", "1.0"))
    m = re.match(r"(\d+)", out["version"])
    if m and re.search(r"\bV\d+\b", out.get("display_name", "")):          # "... - Template V10" -> "V11"
        out["display_name"] = re.sub(r"\bV\d+\b", f"V{m.group(1)}", out["display_name"])
    out["description"] = (f"Imported from the approved template {new.get('source_template')}: "
                          f"{len(new.get('feature_datasets', []))} feature dataset(s), {len(new['layers'])} feature classes, "
                          f"{len(new.get('domains', []))} domains, {len(new.get('topology_rules', []))} topology rules.")
    old_layers = {l["name"].upper(): l for l in cur.get("layers", [])}
    field_names = set()
    for ly in out["layers"]:
        names = {f["name"].upper() for f in ly["fields"]}
        field_names |= names
        o = old_layers.get(ly["name"].upper())
        if not o:
            continue
        for k, val in o.items():
            if k in TEMPLATE_KEYS:
                continue
            if k == "classification_hints":
                val = dict(val)
                val["geometry_types"] = [ly["geometry_type"]]
            elif k == "attribute_aliases":
                val = {f: a for f, a in val.items() if f.upper() in names}
            elif k == "id_field" and str(val).upper() not in names:
                continue
            ly[k] = val
    # label rules / unique fields pointing at fields that no longer exist anywhere are dropped (reported in the diff)
    out["label_rules"] = [r for r in out.get("label_rules", []) if str(r.get("field", "")).upper() in field_names]
    val = dict(out.get("validation") or {})
    if val.get("unique_fields"):
        lay = {l["name"]: {f["name"].upper() for f in l["fields"]} for l in out["layers"]}
        val["unique_fields"] = {k: [f for f in v if f.upper() in lay.get(k, set())]
                                for k, v in val["unique_fields"].items() if k in lay}
        out["validation"] = val
    return out


def _bump(v: str) -> str:
    m = re.match(r"^(\d+)(?:\.(\d+))?", str(v))
    return f"{int(m.group(1))}.{int(m.group(2) or 0) + 1}" if m else "1.1"


# --------------------------------------------------------------------------------------------- diff
def _fsig(f: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": f.get("field_type"), "length": f.get("length"), "nullable": f.get("nullable", True),
            "domain": f.get("domain"), "default": f.get("default"), "alias": f.get("alias")}


def diff_schemas(cur: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    cl = {l["name"]: l for l in cur.get("layers", [])}
    nl = {l["name"]: l for l in new.get("layers", [])}
    changes: List[Dict[str, Any]] = []

    def add(kind, obj, detail, severity="info", **kw):
        changes.append({"kind": kind, "object": obj, "detail": detail, "severity": severity, **kw})

    added = sorted(set(nl) - set(cl))
    removed = sorted(set(cl) - set(nl))
    for n in added:
        add("class_added", n, f"new {nl[n]['geometry_type']} class ({len(nl[n]['fields'])} fields)"
            + (f" in {nl[n].get('feature_dataset')}" if nl[n].get("feature_dataset") else ""))
    for n in removed:
        add("class_removed", n, f"{cl[n]['geometry_type']} class no longer in the template", "breaking")
    for n in sorted(set(cl) & set(nl)):
        a, b = cl[n], nl[n]
        if a["geometry_type"] != b["geometry_type"]:
            add("geometry_changed", n, f"{a['geometry_type']} -> {b['geometry_type']}", "breaking")
        if bool(a.get("has_z")) != bool(b.get("has_z")):
            add("z_changed", n, "now Z-enabled" if b.get("has_z") else "no longer Z-enabled", "warning")
        if (a.get("feature_dataset") or "") != (b.get("feature_dataset") or ""):
            add("dataset_changed", n, f"{a.get('feature_dataset')} -> {b.get('feature_dataset')}", "warning")
        fa = {f["name"].upper(): f for f in a["fields"]}
        fb = {f["name"].upper(): f for f in b["fields"]}
        for k in sorted(set(fb) - set(fa)):
            sev = "warning" if fb[k].get("nullable") is False and fb[k].get("default") in (None, "") else "info"
            add("field_added", f"{n}.{fb[k]['name']}", f"{fb[k].get('field_type')}"
                + (f"({fb[k]['length']})" if fb[k].get("length") else "")
                + (" NOT NULL — a value is required" if fb[k].get("nullable") is False else "")
                + (f", domain {fb[k]['domain']}" if fb[k].get("domain") else ""), sev, layer=n, field=fb[k]["name"])
        for k in sorted(set(fa) - set(fb)):
            add("field_removed", f"{n}.{fa[k]['name']}", "no longer in the template", "breaking", layer=n, field=fa[k]["name"])
        for k in sorted(set(fa) & set(fb)):
            sa, sb = _fsig(fa[k]), _fsig(fb[k])
            for p in ("type", "length", "nullable", "domain", "default", "alias"):
                if sa[p] != sb[p]:
                    sev = {"type": "breaking", "length": "warning" if (sb["length"] or 0) < (sa["length"] or 0) else "info",
                           "nullable": "warning" if sb["nullable"] is False else "info", "domain": "warning"}.get(p, "info")
                    label = {"nullable": "NOT NULL" if sb["nullable"] is False else "nullable"}.get(p)
                    add(f"field_{p}_changed", f"{n}.{fb[k]['name']}",
                        f"{p}: {sa[p]} -> {sb[p]}" + (f" (now {label})" if label else ""), sev, layer=n, field=fb[k]["name"])
    # domains
    da = {d["name"]: d for d in cur.get("domains", [])}
    db = {d["name"]: d for d in new.get("domains", [])}
    for k in sorted(set(db) - set(da)):
        add("domain_added", k, f"{len(db[k].get('coded_values') or {})} coded values" if db[k].get("domain_type") == "coded" else "range domain")
    for k in sorted(set(da) - set(db)):
        add("domain_removed", k, "no longer in the template", "warning")
    for k in sorted(set(da) & set(db)):
        va, vb = da[k].get("coded_values") or {}, db[k].get("coded_values") or {}
        plus = [c for c in vb if c not in va]
        minus = [c for c in va if c not in vb]
        relabel = [c for c in vb if c in va and va[c] != vb[c]]
        if plus:
            add("values_added", k, ", ".join(f"{c}={vb[c]}" if c != vb[c] else c for c in plus[:30])
                + (f" … (+{len(plus) - 30})" if len(plus) > 30 else ""), values=plus)
        if minus:
            add("values_removed", k, ", ".join(minus[:30]) + (f" … (+{len(minus) - 30})" if len(minus) > 30 else ""),
                "breaking", values=minus)
        if relabel:
            add("values_relabelled", k, ", ".join(f"{c}: {va[c]} -> {vb[c]}" for c in relabel[:20]), "info")
        if da[k].get("domain_type") == "range" and (da[k].get("min_value"), da[k].get("max_value")) != (db[k].get("min_value"), db[k].get("max_value")):
            add("range_changed", k, f"{da[k].get('min_value')}..{da[k].get('max_value')} -> {db[k].get('min_value')}..{db[k].get('max_value')}", "warning")
    # topology
    ta = {(t["rule"], t["origin"], t.get("destination")) for t in cur.get("topology_rules", [])}
    tb = {(t["rule"], t["origin"], t.get("destination")) for t in new.get("topology_rules", [])}
    for t in sorted(tb - ta, key=str):
        add("topology_added", t[1], t[0].replace("esriTRT", "") + (f" -> {t[2]}" if t[2] else ""))
    for t in sorted(ta - tb, key=str):
        add("topology_removed", t[1], t[0].replace("esriTRT", "") + (f" -> {t[2]}" if t[2] else ""))
    ea = (cur.get("spatial_reference") or {}).get("epsg")
    eb = (new.get("spatial_reference") or {}).get("epsg")
    if ea != eb:
        add("crs_changed", "spatial reference", f"EPSG:{ea} -> EPSG:{eb}", "breaking")
    # labels / unique fields lost through field removals
    keep = {r.get("field") for r in new.get("label_rules", [])}
    for r in cur.get("label_rules", []):
        if r.get("field") not in keep:
            add("label_rule_dropped", r.get("field"), f"label rule '{r.get('description') or r.get('pattern')}' — field no longer exists", "warning")

    renames = suggest_renames(cl, nl, added, removed)
    counts: Dict[str, int] = {}
    for c in changes:
        counts[c["kind"]] = counts.get(c["kind"], 0) + 1
    return {"changes": changes, "counts": counts, "renames": renames,
            "breaking": sum(1 for c in changes if c["severity"] == "breaking"),
            "identical": not changes}


def suggest_renames(cl, nl, added, removed) -> Dict[str, List[Dict[str, Any]]]:
    """Classes / fields that look renamed: same geometry and most fields shared, or a near-identical name."""
    pairs = []
    for r in removed:
        fr = {f["name"].upper() for f in cl[r]["fields"]}
        for a in added:
            if nl[a]["geometry_type"] != cl[r]["geometry_type"]:
                continue
            fa = {f["name"].upper() for f in nl[a]["fields"]}
            jac = len(fr & fa) / max(1, len(fr | fa))
            name = difflib.SequenceMatcher(None, r.upper(), a.upper()).ratio()
            if (jac >= 0.75 and name >= 0.4) or name >= 0.85:
                pairs.append((0.6 * jac + 0.4 * name, r, a, jac, name))
    cls, used_r, used_a = [], set(), set()
    for score, r, a, jac, name in sorted(pairs, reverse=True):      # one-to-one, best pairs first
        if r in used_r or a in used_a:
            continue
        used_r.add(r)
        used_a.add(a)
        cls.append({"old": r, "new": a, "score": round(score, 2), "shared_fields": round(jac, 2),
                    "name_similarity": round(name, 2)})
    flds = []
    for n in set(cl) & set(nl):
        fa = {f["name"].upper(): f for f in cl[n]["fields"]}
        fb = {f["name"].upper(): f for f in nl[n]["fields"]}
        gone = [k for k in fa if k not in fb]
        new = [k for k in fb if k not in fa]
        for g in gone:
            cands = [k for k in new if fb[k].get("field_type") == fa[g].get("field_type")]
            m = difflib.get_close_matches(g, cands, n=1, cutoff=0.8)
            if m:
                flds.append({"layer": n, "old": fa[g]["name"], "new": fb[m[0]]["name"],
                             "name_similarity": round(difflib.SequenceMatcher(None, g, m[0]).ratio(), 2)})
    return {"classes": cls, "fields": flds}


# --------------------------------------------------------------------------------------------- impact
def impact(schema_id: str, d: Dict[str, Any], rules_dir: str, jobs_root: Optional[str]) -> Dict[str, Any]:
    removed_cls = {c["object"] for c in d["changes"] if c["kind"] == "class_removed"}
    removed_fld = {(c["layer"], c["field"]) for c in d["changes"] if c["kind"] == "field_removed"}
    out: Dict[str, Any] = {"learned_rules": [], "profiles": [], "jobs": []}
    rp = os.path.join(rules_dir, f"{schema_id}.rules.json")
    if os.path.isfile(rp):
        try:
            with open(rp, encoding="utf-8") as f:
                rules = json.load(f).get("rules", [])
            out["learned_rules"] = [{"fingerprint": r.get("fingerprint"), "target": r.get("target_layer")}
                                    for r in rules if r.get("target_layer") in removed_cls]
        except (OSError, ValueError):
            pass
    for p in glob.glob(os.path.join(rules_dir, "profiles", re.sub(r"[^A-Za-z0-9_\- ]", "", schema_id), "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                prof = json.load(f)
        except (OSError, ValueError):
            continue
        tg = sorted({v.get("target") for v in prof.get("layers", {}).values() if v.get("target") in removed_cls})
        fl = sorted({f"{lay}.{fld}" for lay, flds in (prof.get("fields") or {}).items() if isinstance(flds, dict)
                     for fld in flds if (lay, fld) in removed_fld})
        if tg or fl:
            out["profiles"].append({"profile": prof.get("name", os.path.basename(p)[:-5]), "classes": tg, "fields": fl})
    if jobs_root and os.path.isdir(jobs_root):
        for sp in glob.glob(os.path.join(jobs_root, "*", "state.json")):
            try:
                with open(sp, encoding="utf-8") as f:
                    st = json.load(f)
            except (OSError, ValueError):
                continue
            if st.get("schema_id") == schema_id and st.get("status") not in ("completed",):
                out["jobs"].append({"job_id": st.get("job_id"), "file": (st.get("file") or {}).get("name"),
                                    "status": st.get("status")})
    return out


# --------------------------------------------------------------------------------------------- store
class TemplateUpdates:
    def __init__(self, registry, data_dir: str, rules_dir: str, jobs_root: Optional[str] = None):
        self.reg = registry
        self.root = os.path.join(data_dir, "template_updates")
        self.rules_dir = rules_dir
        self.jobs_root = jobs_root

    def _dir(self, schema_id: str, uid: Optional[str] = None) -> str:
        d = os.path.join(self.root, re.sub(r"[^A-Za-z0-9_\-]", "", schema_id))
        return os.path.join(d, uid) if uid else d

    def _versions_dir(self, schema_id: str) -> str:
        return os.path.join(self.reg.schemas_dir, "_versions", schema_id)

    def _current(self, schema_id: str) -> Tuple[Dict[str, Any], str]:
        sc = self.reg.get(schema_id)
        with open(sc.source_file, encoding="utf-8") as f:
            return json.load(f), sc.source_file

    # ---- stage
    def stage(self, schema_id: str, zip_path: str, filename: str, user: str) -> Dict[str, Any]:
        from ..gdb.schema_import import extract_gdb_zip, schema_from_gdb
        cur, _ = self._current(schema_id)
        uid = datetime.now().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
        d = self._dir(schema_id, uid)
        os.makedirs(d, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix="gsb_tu_", dir=d)
        try:
            gdb = extract_gdb_zip(zip_path, tmp)
            gdb_name = os.path.basename(gdb.rstrip("/\\"))
            if not gdb_name.lower().endswith(".gdb"):
                gdb_name = re.sub(r"\.zip$", "", os.path.basename(filename), flags=re.I)
                gdb_name = gdb_name if gdb_name.lower().endswith(".gdb") else gdb_name + ".gdb"
            new = schema_from_gdb(gdb, schema_id, cur.get("display_name", schema_id), cur.get("discipline", ""))
            new["source_template"] = gdb_name
            cand = merge_curated(cur, new)
            sc = schema_from_dict(cand)
            problems = lint_schema(sc)
            if problems:
                raise SchemaError("the new template cannot be used: " + "; ".join(problems[:10]))
            # the template is stored exactly as uploaded: it becomes the output container once applied
            shutil.copyfile(zip_path, os.path.join(d, gdb_name + ".zip"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        df = diff_schemas(cur, cand)
        same_name = gdb_name == cur.get("source_template")
        meta = {"update_id": uid, "schema_id": schema_id, "status": "pending", "uploaded_by": user, "uploaded_at": _now(),
                "file": filename, "template": gdb_name, "current_template": cur.get("source_template"),
                "current_version": cur.get("version"), "new_version": cand.get("version"),
                "same_template_name": same_name, "summary": _summary(cur, cand, df)}
        imp = impact(schema_id, df, self.rules_dir, self.jobs_root)
        for name, obj in (("schema.json", cand), ("diff.json", df), ("impact.json", imp), ("meta.json", meta)):
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                json.dump(obj, f, indent=1, ensure_ascii=False)
        return self.get(schema_id, uid)

    def get(self, schema_id: str, uid: str) -> Dict[str, Any]:
        d = self._dir(schema_id, uid)
        if not os.path.isfile(os.path.join(d, "meta.json")):
            raise KeyError("update not found")
        out = {}
        for k in ("meta", "diff", "impact"):
            with open(os.path.join(d, f"{k}.json"), encoding="utf-8") as f:
                out[k] = json.load(f)
        return {**out["meta"], "diff": out["diff"], "impact": out["impact"]}

    def list(self, schema_id: str) -> Dict[str, Any]:
        ups = []
        for m in sorted(glob.glob(os.path.join(self._dir(schema_id), "*", "meta.json")), reverse=True):
            with open(m, encoding="utf-8") as f:
                ups.append(json.load(f))
        vers = []
        for m in sorted(glob.glob(os.path.join(self._versions_dir(schema_id), "*", "version.json")), reverse=True):
            with open(m, encoding="utf-8") as f:
                vers.append(json.load(f))
        try:
            cur, _ = self._current(schema_id)
            current = {"version": cur.get("version"), "template": cur.get("source_template"),
                       "applied_at": cur.get("applied_at"), "applied_by": cur.get("applied_by")}
        except KeyError:
            current = None
        return {"current": current, "updates": ups, "versions": vers}

    def _set_meta(self, schema_id: str, uid: str, **kw):
        p = os.path.join(self._dir(schema_id, uid), "meta.json")
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
        m.update(kw)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=1, ensure_ascii=False)
        return m

    def reject(self, schema_id: str, uid: str, user: str) -> Dict[str, Any]:
        m = self.get(schema_id, uid)
        if m["status"] != "pending":
            raise ValueError(f"update is {m['status']}")
        return self._set_meta(schema_id, uid, status="rejected", decided_by=user, decided_at=_now())

    # ---- archive / apply / restore
    def _archive_current(self, schema_id: str, reason: str, user: str) -> str:
        cur, path = self._current(schema_id)
        ver = _safe_ver(cur.get("version") or "unversioned")
        dest = os.path.join(self._versions_dir(schema_id), ver)
        if os.path.exists(dest):
            dest = f"{dest}__{datetime.now().strftime('%Y%m%d%H%M%S')}"
        os.makedirs(dest)
        shutil.copyfile(path, os.path.join(dest, "schema.json"))
        sc = self.reg.get(schema_id)
        tpl = sc.template_path()
        if tpl and os.path.isfile(tpl):
            shutil.copyfile(tpl, os.path.join(dest, os.path.basename(tpl)))
        info = {"version": cur.get("version"), "template": cur.get("source_template"), "archived_at": _now(),
                "archived_by": user, "reason": reason, "folder": os.path.basename(dest),
                "layers": len(cur.get("layers", [])), "domains": len(cur.get("domains", []))}
        with open(os.path.join(dest, "version.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, indent=1)
        return dest

    def _install(self, schema_id: str, schema_dict: Dict[str, Any], template_zip: str, path: str):
        tdir = os.path.join(os.path.dirname(path), "templates")
        os.makedirs(tdir, exist_ok=True)
        dst = os.path.join(tdir, schema_dict["source_template"] + ".zip")
        tmpz = dst + ".tmp"
        shutil.copyfile(template_zip, tmpz)
        os.replace(tmpz, dst)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(schema_dict, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    def apply(self, schema_id: str, uid: str, user: str, renames: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        m = self.get(schema_id, uid)
        if m["status"] != "pending":
            raise ValueError(f"update is {m['status']}")
        d = self._dir(schema_id, uid)
        with open(os.path.join(d, "schema.json"), encoding="utf-8") as f:
            cand = json.load(f)
        _, path = self._current(schema_id)
        archived = self._archive_current(schema_id, f"replaced by {cand.get('source_template')}", user)
        cand["applied_at"], cand["applied_by"] = _now(), user
        self._install(schema_id, cand, os.path.join(d, cand["source_template"] + ".zip"), path)
        carried = self._carry_renames(schema_id, renames or {}, archived) if renames else {}
        return self._set_meta(schema_id, uid, status="applied", decided_by=user, decided_at=_now(),
                              archived_as=os.path.basename(archived), renames_applied=carried)

    def restore(self, schema_id: str, folder: str, user: str) -> Dict[str, Any]:
        src = os.path.join(self._versions_dir(schema_id), os.path.basename(folder))
        if not os.path.isfile(os.path.join(src, "schema.json")):
            raise KeyError("archived version not found")
        with open(os.path.join(src, "schema.json"), encoding="utf-8") as f:
            old = json.load(f)
        zips = glob.glob(os.path.join(src, "*.zip"))
        _, path = self._current(schema_id)
        archived = self._archive_current(schema_id, f"replaced by restoring {old.get('version')}", user)
        old["applied_at"], old["applied_by"] = _now(), user
        if zips:
            self._install(schema_id, old, zips[0], path)
        else:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(old, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        return {"restored": old.get("version"), "template": old.get("source_template"),
                "previous_archived_as": os.path.basename(archived)}

    def _carry_renames(self, schema_id: str, renames: Dict[str, Any], backup_dir: str) -> Dict[str, int]:
        """Confirmed renames ({'classes': {old: new}, 'fields': {'LAYER.OLD': 'NEW'}}) into learned rules and profiles."""
        cls = renames.get("classes") or {}
        fld = renames.get("fields") or {}
        n = {"learned_rules": 0, "profiles": 0}
        bdir = os.path.join(backup_dir, "mapping_backup")
        rp = os.path.join(self.rules_dir, f"{schema_id}.rules.json")
        if cls and os.path.isfile(rp):
            os.makedirs(bdir, exist_ok=True)
            shutil.copyfile(rp, os.path.join(bdir, os.path.basename(rp)))
            with open(rp, encoding="utf-8") as f:
                data = json.load(f)
            for r in data.get("rules", []):
                if r.get("target_layer") in cls:
                    r["target_layer"] = cls[r["target_layer"]]
                    n["learned_rules"] += 1
            with open(rp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        for p in glob.glob(os.path.join(self.rules_dir, "profiles", re.sub(r"[^A-Za-z0-9_\- ]", "", schema_id), "*.json")):
            with open(p, encoding="utf-8") as f:
                prof = json.load(f)
            changed = False
            for v in prof.get("layers", {}).values():
                if v.get("target") in cls:
                    v["target"] = cls[v["target"]]
                    changed = True
            flds = prof.get("fields") or {}
            for lay in list(flds):
                new_lay = cls.get(lay, lay)
                if new_lay != lay:
                    flds[new_lay] = flds.pop(lay)
                    changed = True
                if isinstance(flds.get(new_lay), dict):
                    for old in list(flds[new_lay]):
                        nn = fld.get(f"{lay}.{old}") or fld.get(f"{new_lay}.{old}")
                        if nn:
                            flds[new_lay][nn] = flds[new_lay].pop(old)
                            changed = True
            if changed:
                os.makedirs(os.path.join(bdir, "profiles"), exist_ok=True)
                shutil.copyfile(p, os.path.join(bdir, "profiles", os.path.basename(p)))
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(prof, f, indent=2, ensure_ascii=False)
                n["profiles"] += 1
        return n


def _summary(cur, cand, df) -> Dict[str, Any]:
    c = df["counts"]
    return {"classes": f"{len(cur.get('layers', []))} -> {len(cand['layers'])}",
            "classes_added": c.get("class_added", 0), "classes_removed": c.get("class_removed", 0),
            "fields_added": c.get("field_added", 0), "fields_removed": c.get("field_removed", 0),
            "fields_changed": sum(v for k, v in c.items() if k.startswith("field_") and k.endswith("_changed")),
            "domain_values_added": sum(len(x.get("values") or []) for x in df["changes"] if x["kind"] == "values_added"),
            "domain_values_removed": sum(len(x.get("values") or []) for x in df["changes"] if x["kind"] == "values_removed"),
            "topology_changes": c.get("topology_added", 0) + c.get("topology_removed", 0),
            "breaking": df["breaking"], "identical": df["identical"]}
