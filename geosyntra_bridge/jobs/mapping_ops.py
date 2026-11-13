"""Layer / field mapping, project memory, required values, reference data, submission info."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from ..engine.conversion import (build_initial_mapping, refresh_field_mapping)

from .common import JobError, _now, safe_filename, _rmtree, _clean_elevation


class MappingOps:
    """Layer / field mapping, project memory, required values, reference data, submission info."""

    def update_mapping(self, job_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        self._require(job_id, ["mapping", "validation", "done", "generate"])
        schema = self.reg.get(self.state(job_id)["schema_id"])
        with self._lock(job_id):
            m = self.get_mapping(job_id)
            old_pid = (m.get("project") or {}).get("project_id")
            for key, upd in (patch.get("layers") or {}).items():
                if key not in m["layers"]:
                    raise JobError(f"unknown CAD layer group '{key}'")
                tgt = upd.get("target", m["layers"][key].get("target"))
                if tgt and not schema.get_layer(tgt):
                    raise JobError(f"'{tgt}' is not a feature class of {schema.schema_id}")
                cur = m["layers"][key]
                for k in ("target", "role", "subtype", "convert", "twins"):
                    if k in upd:
                        cur[k] = upd[k]
                if cur.get("role") not in ("feature", "ignore"):
                    raise JobError("role must be 'feature' or 'ignore'")
                cur["status"] = "USER"
                cur["confidence"] = "HIGH" if cur.get("target") else cur.get("confidence")
            if patch.get("layers"):
                # geometry twins follow the current targets; explicit on/off choices are kept
                from ..mapping.automap import assign_twins
                chosen = {k: {t["target"]: t.get("enabled") for t in v.get("twins") or []} for k, v in m["layers"].items()}
                for v in m["layers"].values():
                    v.pop("twins", None)
                assign_twins(schema, m["layers"])
                for k, v in m["layers"].items():
                    for t in v.get("twins") or []:
                        if t["target"] in chosen.get(k, {}) and chosen[k][t["target"]] is not None:
                            t["enabled"] = bool(chosen[k][t["target"]])
            fpatch = dict(patch.get("fields") or {})
            if "*" in fpatch:   # apply to every mapped feature class that has the field
                star = fpatch.pop("*")
                try:
                    self._remember(self.state(job_id).get("owner"), fields=star)
                except OSError:
                    pass
                mapped = {v.get("target") for v in m["layers"].values() if v.get("target") and v.get("role") != "ignore"}
                for fc in mapped:
                    layer = schema.get_layer(fc)
                    for fname, spec in star.items():
                        if layer and layer.get_field(fname):
                            fpatch.setdefault(fc, {})[layer.get_field(fname).name] = spec
            for fc, fields in fpatch.items():
                layer = schema.get_layer(fc)
                if not layer:
                    raise JobError(f"unknown feature class '{fc}'")
                for fname, spec in fields.items():
                    if not layer.get_field(fname):
                        raise JobError(f"{fc} has no field '{fname}'")
                    if spec.get("source") not in ("label", "block_attr", "constant", "geometry", "cad", "none", "subtype", "system", "network", "computed", "elevation", "today"):
                        raise JobError(f"invalid source '{spec.get('source')}'")
                    m["fields"].setdefault(fc, {})[fname] = {**spec, "user": True}
            if "value_maps" in patch:      # drawing text -> domain code, per field (e.g. 'COPERNICIA (WAX Palm)')
                try:
                    self._remember(self.state(job_id).get("owner"), value_maps=patch["value_maps"] or {})
                except OSError:
                    pass
                vm = m.setdefault("value_maps", {})
                for fname, pairs in (patch["value_maps"] or {}).items():
                    cur = vm.setdefault(str(fname), {})
                    for raw, code in (pairs or {}).items():
                        if code in (None, ""):
                            cur.pop(str(raw).strip().upper(), None)
                        else:
                            cur[str(raw).strip().upper()] = code
            if "label_layers" in patch:
                ll = patch["label_layers"]
                m["label_layers"] = None if ll is None else [str(x) for x in ll]
            if "source_crs" in patch:
                v = patch["source_crs"]
                if v not in (None, "", "same"):
                    from ..geometry.crs import validate_epsg
                    if not validate_epsg(v):
                        raise JobError(f"EPSG:{v} is not a valid coordinate system")
                    v = int(v)
                m["source_crs"] = v or None
            if "unit_scale" in patch:
                sc = float(patch["unit_scale"])
                if not 0 < sc <= 1e6:
                    raise JobError("unit scale must be positive")
                m["unit_scale"] = sc
                if "georef" not in patch:
                    m["georef"] = None          # plain units replace a georeferencing
            if "source_crs" in patch and "transform_op" not in patch:
                m["transform_op"] = None
            if "transform_op" in patch:
                op = patch["transform_op"]
                m["transform_op"] = str(op) if op else None
            if "georef" in patch:
                g = patch["georef"]
                if g:
                    try:
                        g = {"scale": float(g["scale"]), "rotation": float(g.get("rotation") or 0),
                             "offset": [float(g["offset"][0]), float(g["offset"][1])],
                             "points": g.get("points") or [], "rms": g.get("rms")}
                    except (KeyError, TypeError, ValueError, IndexError):
                        raise JobError("georeferencing needs scale, rotation and offset")
                    if not 0 < g["scale"] <= 1e6:
                        raise JobError("georeferencing scale must be positive")
                m["georef"] = g or None
            if "options" in patch:
                opts = m.setdefault("options", {})
                for k, v in patch["options"].items():
                    if k == "repair" and isinstance(v, dict):
                        opts.setdefault("repair", {}).update(v)
                    elif k in ("label_search_radius", "snap_tolerance", "duplicate_policy", "non_nullable_policy"):
                        opts[k] = v
            if "project" in patch:
                cur = dict(m.get("project") or {})
                for k, v in (patch["project"] or {}).items():
                    if k == "boundary":
                        if not isinstance(v, dict) or v.get("mode") not in ("layer", "derived"):
                            raise JobError("boundary must be {mode: layer|derived, layer?, buffer_m?}")
                    cur[k] = v
                m["project"] = cur
            if "elevation" in patch:
                m["elevation"] = _clean_elevation({**(m.get("elevation") or {}), **(patch["elevation"] or {})})
            refresh_field_mapping(m, schema, self._read(job_id, "discovery.json", {}))
            self._write(job_id, "mapping.json", m)
        st = self.state(job_id)
        only_project = set(patch) <= {"project"}
        pid_changed = "project_id" in (patch.get("project") or {}) and \
            str((patch.get("project") or {}).get("project_id") or "") != str(old_pid or "")
        if st["status"] in ("validated", "completed") and (not only_project or pid_changed):
            self._update(job_id, stage="mapping", status="ready", message="Mapping changed — validate again")
        return m

    def accept_suggestions(self, job_id: str, user: str, min_score: Optional[float] = None) -> Dict[str, Any]:
        """Confirm every proposed target and every proposed 'ignore' (explicit, logged decisions).
        With min_score (automatic conversion), a target below it is not converted: the group is set aside and
        reported, never pushed into a feature class on weak evidence."""
        m = self.get_mapping(job_id)
        layers = {}
        for k, v in m["layers"].items():
            if v.get("role") == "ignore" or v.get("status") in ("USER", "PROFILE"):
                continue
            if v.get("target") and (min_score is None or (v.get("score") or 0) >= min_score):
                layers[k] = {"target": v["target"]}
            elif v.get("target") or v.get("suggested_role") == "ignore":
                layers[k] = {"role": "ignore"}
        m = self.update_mapping(job_id, {"layers": layers}) if layers else m
        try:
            schema = self.reg.get(self.state(job_id)["schema_id"])
            with self._lock(job_id):
                m = self.get_mapping(job_id)
                n_mem = self._apply_memory(job_id, m, schema)
                if n_mem:
                    self._write(job_id, "mapping.json", m)
                    self.log(job_id, f"{n_mem} field value(s) reused from earlier drawings (project memory)")
        except OSError:
            pass
        self.log(job_id, f"{user} accepted {len(layers)} suggestions: " +
                 ", ".join(f"{k}->{v.get('target') or 'ignore'}" for k, v in layers.items()))
        return m

    def required_values(self, job_id: str) -> Dict[str, Any]:
        """What the drawing cannot provide and generation needs: NOT NULL fields with no value (asked once per field
        for every class that has it) and drawing texts that are not codes of the field's domain (mapped once)."""
        import difflib
        res = self._read(job_id, "results.json")
        schema = self.reg.get(self.state(job_id)["schema_id"])
        m = self.get_mapping(job_id)
        if not res:
            return {"missing": [], "unmapped_values": [], "validated": False}
        missing: Dict[str, Dict[str, Any]] = {}
        for i in res["issues"]:
            if i.get("code") != "not_null_unfilled" or not i.get("field"):
                continue
            layer = schema.get_layer(i.get("target_layer") or "")
            fd = layer.get_field(i["field"]) if layer else None
            if fd is None:
                continue
            from ..validation.attributes import ELEVATION_FIELDS
            e = missing.setdefault(fd.name, {"field": fd.name, "alias": fd.alias, "type": fd.field_type, "classes": [],
                                             "features": 0, "domain": fd.domain, "choices": None,
                                             "description": getattr(fd, "description", "") or "",
                                             # levels come from CAD Z or a DEM, never from a typed constant
                                             "elevation": fd.name.upper() in ELEVATION_FIELDS})
            e["classes"].append(layer.name)
            try:
                e["features"] += int(re.search(r"but (\d+) feature", i["message"]).group(1))
            except (AttributeError, ValueError):
                pass
            dom = schema.get_domain(fd.domain)
            if dom is not None and dom.domain_type == "coded":
                e["choices"] = dom.coded_values
        vals: Dict[tuple, Dict[str, Any]] = {}
        for i in res["issues"]:
            if i.get("code") != "domain_violation" or not i.get("field"):
                continue
            mm = re.search(r"= '(.*)' is not in domain '([^']+)'", i["message"])
            if not mm:
                continue
            raw, dname = mm.group(1), mm.group(2)
            key = (i["field"], raw.strip().upper())
            e = vals.get(key)
            if e is None:
                dom = schema.get_domain(dname)
                cands = []
                if dom is not None:
                    words = {w for w in re.split(r"[^A-Z0-9]+", raw.upper()) if len(w) > 2}
                    for code, desc in dom.coded_values.items():
                        txt = f"{code} {desc}".upper()
                        hit = sum(1 for w in words if w in txt)
                        ratio = difflib.SequenceMatcher(None, raw.upper(), str(desc).upper()).ratio()
                        if hit or ratio > 0.6:
                            cands.append((hit + ratio, code, desc))
                    cands.sort(reverse=True)
                # a clear best candidate is pre-selected (the user still confirms it)
                sug = cands[0][1] if cands and (len(cands) == 1 or cands[0][0] - cands[1][0] >= 0.25) and cands[0][0] >= 1.0 else None
                e = vals[key] = {"field": i["field"], "value": raw, "domain": dname, "classes": [], "features": 0,
                                 "suggested": sug,
                                 "candidates": [{"code": c, "label": d} for _, c, d in cands[:6]],
                                 "choices": dom.coded_values if dom is not None else {},
                                 "mapped_to": (m.get("value_maps") or {}).get(i["field"], {}).get(raw.strip().upper())}
            if i.get("target_layer") and i["target_layer"] not in e["classes"]:
                e["classes"].append(i["target_layer"])
            e["features"] += 1
        mem = self.memory(self.state(job_id).get("owner"))
        for e in missing.values():
            rec = mem["fields"].get(e["field"].upper())
            if rec and (not e["choices"] or str(rec["value"]) in {str(k) for k in e["choices"]}):
                e["remembered"] = rec["value"]
        return {"validated": True, "missing": sorted(missing.values(), key=lambda x: -x["features"]),
                "unmapped_values": sorted(vals.values(), key=lambda x: -x["features"])}

    def change_schema(self, job_id: str, schema_id: str) -> Dict[str, Any]:
        self._require(job_id, ["mapping", "validation", "done", "generate"])
        schema = self.reg.get(schema_id)
        doc = self.load_doc(job_id)
        disc = self._read(job_id, "discovery.json")
        mapping = build_initial_mapping(doc, schema, self.s.rules_dir, disc)
        self._write(job_id, "mapping.json", mapping)
        self.log(job_id, f"schema changed to {schema_id}")
        if os.path.isfile(os.path.join(self.jdir(job_id), "work", "reference_match.json")):
            self._update(job_id, schema_id=schema.schema_id, schema_name=schema.display_name)
            self._rematch_reference(job_id)
        return self._update(job_id, schema_id=schema.schema_id, schema_name=schema.display_name,
                            stage="mapping", status="ready", message="Schema changed — layers re-mapped")

    # ------------------------------------------------------------ reference GIS (spatial join)
    def attach_reference(self, job_id: str, filename: str, stream) -> Dict[str, Any]:
        self._require(job_id, ["mapping", "validation", "done", "generate"])
        name = safe_filename(filename or "reference.zip")
        if not name.lower().endswith((".zip", ".gpkg", ".geojson", ".json")):
            raise JobError("reference data must be a zipped File Geodatabase / shapefile, a GeoPackage or GeoJSON")
        d = os.path.join(self.jdir(job_id), "reference")
        _rmtree(d)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        limit, written = self.s.max_upload_mb * 1024 * 1024, 0
        with open(path, "wb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise JobError(f"file exceeds the {self.s.max_upload_mb} MB upload limit", 413)
                out.write(chunk)
        self._write(job_id, "work/reference_source.json", {"file": name, "size": written})
        self.log(job_id, f"reference GIS data attached: {name} ({written:,} bytes)")
        return self._rematch_reference(job_id)

    def _rematch_reference(self, job_id: str) -> Dict[str, Any]:
        from ..reference.spatial_join import ReferenceError, load_reference, match_drawing, unpack
        src = self._read(job_id, "work/reference_source.json")
        if not src:
            raise JobError("no reference data attached", 404)
        schema = self.reg.get(self.state(job_id)["schema_id"])
        doc = self.load_doc(job_id)
        m = self.get_mapping(job_id)
        src_crs = m.get("source_crs")
        drawing_epsg = int(src_crs) if src_crs not in (None, "", "same") else schema.spatial_reference.epsg
        try:
            ds = unpack(os.path.join(self.jdir(job_id), "reference", src["file"]), os.path.join(self.jdir(job_id), "work"))
            ref = load_reference(ds, schema, drawing_epsg, src["file"])
        except ReferenceError as e:
            raise JobError(str(e))
        groups: Dict[str, list] = {}
        for e in doc.features:
            groups.setdefault(e.group, []).append(e)
        match = match_drawing(doc, ref, groups)
        match["summary"] = ref.summary()
        self._write(job_id, "work/reference_match.json", match)
        # re-score the groups with the spatial-join evidence; decisions taken by people are kept
        from ..mapping.automap import auto_map
        from ..cad.keys import read_keys
        fresh = auto_map(doc, schema, self.s.rules_dir, keys=read_keys(doc), reference=match)
        with self._lock(job_id):
            m = self.get_mapping(job_id)
            changed = []
            for k, v in fresh.items():
                cur = m["layers"].get(k)
                if cur is None or cur.get("status") in ("USER", "PROFILE"):
                    continue
                if (cur.get("target"), cur.get("status")) != (v.get("target"), v.get("status")):
                    changed.append(f"{k}: {cur.get('target') or '—'} → {v.get('target') or '—'} ({v.get('status')})")
                m["layers"][k] = v
            m["reference"] = {"file": src["file"], "overlap": match["overlap"], "summary": match["summary"],
                              "matched_entities": len(match["entities"]),
                              "groups": {k: g for k, g in match["groups"].items() if g["classes"]}}
            refresh_field_mapping(m, schema, self._read(job_id, "discovery.json", {}) or {})
            self._write(job_id, "mapping.json", m)
        self.log(job_id, f"spatial join: {len(match['entities'])} CAD objects matched to reference features"
                         + (f"; re-mapped {len(changed)} group(s): " + "; ".join(changed[:10]) if changed else ""))
        if not match["overlap"]:
            self.log(job_id, "spatial join: the reference data does not overlap the drawing — check the source CRS")
        st = self.state(job_id)
        if st["status"] in ("validated", "completed"):
            self._update(job_id, stage="mapping", status="ready", message="Reference GIS applied — re-run validation")
        return m

    def remove_reference(self, job_id: str) -> Dict[str, Any]:
        self._require(job_id, ["mapping", "validation", "done", "generate"])
        for n in ("work/reference_match.json", "work/reference_source.json"):
            p = os.path.join(self.jdir(job_id), n)
            if os.path.isfile(p):
                os.remove(p)
        _rmtree(os.path.join(self.jdir(job_id), "reference"))
        with self._lock(job_id):
            m = self.get_mapping(job_id)
            m.pop("reference", None)
            self._write(job_id, "mapping.json", m)
        self.log(job_id, "reference GIS data removed")
        return m

    # ------------------------------------------------------------ project information (DMT standard 3.6.1 / 3.8 / 3.9)
    def project_info(self, job_id: str) -> Dict[str, Any]:
        from .. import submission as sub
        m = self.get_mapping(job_id)
        st = self.state(job_id)
        d = sub.defaults(self.reg.get(st["schema_id"]), st["file"]["name"])
        return {**d, **(m.get("project") or {})}

    def submission_status(self, job_id: str) -> Dict[str, Any]:
        from .. import submission as sub
        info = self.project_info(job_id)
        missing, errors = sub.check(info)
        try:
            cands = sub.boundary_candidates(self.load_doc(job_id))
        except Exception:  # noqa: BLE001
            cands = []
        return {"info": info, "fields": sub.FIELDS, "name": sub.submission_name(info), "missing": missing,
                "errors": errors, "boundary_candidates": cands}

    def apply_profile(self, job_id: str, name: str) -> Dict[str, Any]:
        self._require(job_id, ["mapping", "validation", "done", "generate"])
        st = self.state(job_id)
        prof = self.profiles.load(st["schema_id"], name)
        with self._lock(job_id):
            m = self.profiles.apply(prof, self.get_mapping(job_id))
            refresh_field_mapping(m, self.reg.get(st["schema_id"]), self._read(job_id, "discovery.json", {}))
            self._write(job_id, "mapping.json", m)
        self.log(job_id, f"profile '{name}' applied")
        return m

    def save_profile(self, job_id: str, name: str, user: str, update_rules: bool, authorized: bool):
        st = self.state(job_id)
        return self.profiles.save(st["schema_id"], name, self.get_mapping(job_id), user, update_rules, authorized)

    # ── project memory: values the user typed once are reused for the next drawings ───────────────────────────
    def _memory_file(self, owner: Optional[str]) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.@-]+", "_", owner or "local")[:80] or "local"
        d = os.path.join(os.path.dirname(self.s.jobs_dir.rstrip("/\\")), "memory")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, safe + ".json")

    def memory(self, owner: Optional[str]) -> Dict[str, Any]:
        p = self._memory_file(owner)
        try:
            with open(p, encoding="utf-8") as f:
                mem = json.load(f)
        except (OSError, ValueError):
            mem = {}
        mem.setdefault("fields", {}); mem.setdefault("value_maps", {})
        return mem

    def _remember(self, owner: Optional[str], fields: Dict[str, Any] = None, value_maps: Dict[str, Dict[str, str]] = None,
                  dem_source: Optional[str] = None):
        mem = self.memory(owner)
        if dem_source:
            mem["dem_source"] = dem_source
        for f, spec in (fields or {}).items():
            if spec.get("source") == "constant" and spec.get("value") not in (None, ""):
                mem["fields"][f.upper()] = {"value": spec["value"], "at": _now()}
            elif spec.get("source") == "none":
                mem["fields"].pop(f.upper(), None)
        for f, pairs in (value_maps or {}).items():
            cur = mem["value_maps"].setdefault(f.upper(), {})
            for raw, code in (pairs or {}).items():
                if code in (None, ""):
                    cur.pop(str(raw).strip().upper(), None)
                else:
                    cur[str(raw).strip().upper()] = code
        with open(self._memory_file(owner), "w", encoding="utf-8") as fh:
            json.dump(mem, fh, ensure_ascii=False, indent=1)

    def forget(self, owner: Optional[str], field: Optional[str] = None) -> Dict[str, Any]:
        mem = self.memory(owner)
        if field:
            mem["fields"].pop(field.upper(), None); mem["value_maps"].pop(field.upper(), None)
        else:
            mem = {"fields": {}, "value_maps": {}}
        with open(self._memory_file(owner), "w", encoding="utf-8") as fh:
            json.dump(mem, fh, ensure_ascii=False, indent=1)
        return mem

    def _apply_memory(self, job_id: str, mapping: Dict[str, Any], schema) -> int:
        """Fill fields the user answered in earlier drawings (only where the drawing gives nothing and the value
        is valid for the field's domain) and reuse the drawing-text -> domain-code choices."""
        mem = self.memory(self.state(job_id).get("owner"))
        if not mem["fields"] and not mem["value_maps"]:
            return 0
        n = 0
        mapped = {v.get("target") for v in mapping["layers"].values() if v.get("target") and v.get("role") != "ignore"}
        for fc in mapped:
            layer = schema.get_layer(fc)
            if not layer:
                continue
            for fd in layer.fields:
                rec = mem["fields"].get(fd.name.upper())
                if not rec:
                    continue
                cur = (mapping.get("fields") or {}).get(fc, {}).get(fd.name) or {}
                if cur.get("source") not in (None, "none") or cur.get("user"):
                    continue
                dom = schema.get_domain(fd.domain) if fd.domain else None
                if dom is not None and getattr(dom, "domain_type", "") == "coded" and str(rec["value"]) not in {str(k) for k in dom.coded_values}:
                    continue
                mapping.setdefault("fields", {}).setdefault(fc, {})[fd.name] = {"source": "constant", "value": rec["value"], "memory": True}
                n += 1
        if mem["value_maps"]:
            vm = mapping.setdefault("value_maps", {})
            for f, pairs in mem["value_maps"].items():
                for raw, code in pairs.items():
                    vm.setdefault(f, {}).setdefault(raw, code)
        return n
