"""Validation and generation runs, completeness, results and previews."""
from __future__ import annotations

import os
import re
import shutil
import time
from datetime import datetime
from typing import Any, Dict

from ..engine.bundle import build_bundle
from ..engine.conversion import (ConversionResult, feature_detail, preview_geojson, run_conversion)
from ..gdb.arcpy_enhancer import apply_subtypes
from ..gdb.filegdb_writer import write_filegdb
from ..gdb.verify import verify_filegdb
from ..reporting.excel import write_excel_report
from ..reporting.pdf import write_pdf_report

from .common import JobError, _now, _pipeline, _zip_dir


class PipelineOps:
    """Validation and generation runs, completeness, results and previews."""

    def _convert(self, job_id: str, cb) -> ConversionResult:
        doc = self.load_doc(job_id)
        st = self.state(job_id)
        schema = self.reg.get(st["schema_id"])
        mapping = self._read(job_id, "mapping.json")
        edits = self._read(job_id, "feature_edits.json")
        if edits:       # values entered per feature on the map editor (applied by the engine, after the automatic fill)
            mapping = {**mapping, "feature_edits": edits}
        from ..elevation.dem import DemSet
        from ..elevation.engine import options as elev_options
        dem = DemSet.load(os.path.join(self.jdir(job_id), "work", "dem")) if elev_options(mapping).get("enabled") else None
        result = run_conversion(doc, schema, mapping, progress=cb, reference=self._read(job_id, "work/reference_match.json"),
                                dem=dem)
        from .topology_edit import EXC, apply_exceptions
        apply_exceptions(result, self._read(job_id, EXC) or {})
        self._results[job_id] = result
        return result

    def _run_validation(self, job_id: str):
        from .assign import flush
        flush(self, job_id)      # map edits still waiting to be written
        stage = "validation"
        try:
            t0 = time.time()
            res = self._convert(job_id, self._progress(job_id, stage, 0.0, 0.9))
            bundle = build_bundle(job_id, self.state(job_id)["file"]["name"], res)
            self._attach_key(job_id, bundle)
            self._write(job_id, "results.json", bundle)
            self._write(job_id, "preview.geojson", preview_geojson(res))
            s = bundle["summary"]
            self.log(job_id, f"validation: {s['features_imported']} importable, {s['features_failed']} failed, "
                             f"{len(bundle['issues'])} issues ({time.time() - t0:.1f}s)")
            self._update(job_id, stage="validation", status="validated", progress=1.0,
                         message=f"{s['features_imported']} features ready, {s['features_failed']} failed, "
                                 f"{s['issues_by_severity']['error']} errors", validated_at=_now(),
                         validated_schema_version=self.reg.get(self.state(job_id)["schema_id"]).version)
        except Exception as e:  # noqa: BLE001
            self._fail(job_id, stage, e)

    def _write_template(self, job_id: str, part, feats, gdb_path: str, base: str, out_dir: str, res, label: str,
                        p0: float, p1: float) -> Dict[str, Any]:
        """Write the features of ONE template into a copy of that template geodatabase, then verify it on its own:
        counts, subtypes, Z, and the DMT QC replica against that template."""
        stage = "generate"
        w = write_filegdb(gdb_path, part, feats, progress=self._progress(job_id, stage, p0, p0 + (p1 - p0) * 0.6))
        for line in w["log"]:
            self.log(job_id, f"gdb{label}: {line}")
        for fw in w["failed_writes"]:
            res.issues.add("error", "failed", "write_failed", f"{fw['target']} #{fw['fid']} could not be written: {fw['error']}",
                           target_layer=fw["target"], feature_id=fw["fid"])
        subtypes = apply_subtypes(gdb_path, part)
        expected: Dict[str, int] = {}
        for f in feats:
            if not f.failed and f.write and f.geom is not None:
                expected[f.target] = expected.get(f.target, 0) + 1
        ver = verify_filegdb(gdb_path, part, expected, [r.name for r in part.relationships if r.cardinality != "many_to_many"])
        ver["checks"].insert(1, {"check": "Features written", "passed": sum(expected.values()) > 0,
                                 "detail": f"{sum(w['counts'].values())} written" if expected else
                                 "no feature could be written — resolve the blocking errors first", "severity": "error"})
        ver["passed"] = ver["passed"] and sum(expected.values()) > 0
        if subtypes.get("layers"):
            ver["checks"].append({"check": "Subtype definitions", "passed": bool(subtypes["applied"]),
                                  "detail": subtypes.get("note") or ", ".join(subtypes.get("errors") or []) or
                                  f"{len(subtypes['layers'])} feature classes", "severity": "warning"})
        from ..elevation.engine import verify_written_z
        ev = res.elevation or {}
        zv = verify_written_z(gdb_path, feats)
        if zv["checked"] or zv["missing"]:
            ver["checks"].append({"check": "Elevation (Z) written as computed", "passed": zv["passed"], "severity": "error",
                                  "detail": f"{zv['checked']} Z-enabled feature(s) re-read" +
                                            (f"; {zv['mismatched']} mismatched, {zv['missing']} not found: {zv['examples']}"
                                             if not zv["passed"] else ", all vertices match")})
            ver["passed"] = ver["passed"] and zv["passed"]
        zu = w.get("z_undefined") or {}
        if zu:
            n = sum(zu.values())
            ver["checks"].append({"check": "Elevation available for Z-enabled classes", "passed": False, "severity": "warning",
                                  "detail": f"{n} feature(s) have no elevation ({'DEM elevation is disabled' if not ev.get('enabled') else 'see issues'}); "
                                            f"the File Geodatabase format stores Z = 0 for them — it is not an elevation. "
                                            f"Enable DEM elevation in the Elevation step. Classes: " +
                                            ", ".join(f"{k} {v}" for k, v in sorted(zu.items())[:8])})
        qc, qc_csv = None, None
        tpl = part.template_path()
        if tpl:
            self._update(job_id, stage=stage, status="running", progress=p0 + (p1 - p0) * 0.7,
                         message=f"Running the DMT Data Quality Control checks{label}")
            from ..gdb.dmt_qc import run_dmt_qc, write_qc_csv
            names = {ly.name for ly in part.layers}
            topo_n = sum(1 for i in res.issues.items if i.category == "topology" and (i.target_layer in names or not i.target_layer))
            qc = run_dmt_qc(gdb_path, tpl, topo_n)
            struct_err = qc["by_check"]["Schema identical to template"] + qc["by_check"]["Topology present"]
            data_err = len(qc["errors"]) - struct_err
            ver["checks"].append({"check": "DMT QC: schema, domains and topology identical to the template",
                                  "passed": struct_err == 0, "severity": "error",
                                  "detail": f"{struct_err} difference(s)" if struct_err else f"identical to {qc['template']}"})
            ver["checks"].append({"check": "DMT QC: coded values, spatial rules, curve geometry",
                                  "passed": data_err == 0, "severity": "warning",
                                  "detail": (f"{data_err} error(s) the DMT tool will report — see QCErrors.csv"
                                             if data_err else "no errors") +
                                            (f"; {topo_n} topology finding(s) from the template rules" if topo_n else "")})
            ver["passed"] = ver["passed"] and struct_err == 0
            qc_csv = os.path.join(out_dir, f"{base}_QCErrors.csv")
            write_qc_csv(qc, qc_csv)
            self.log(job_id, f"DMT QC replica{label}: {len(qc['errors'])} error(s) {qc['by_check']}")
        zip_path = gdb_path + ".zip"
        _zip_dir(gdb_path, zip_path)
        return {"schema_id": part.schema_id, "template": part.source_template, "gdb_path": gdb_path,
                "name": os.path.basename(gdb_path), "zip": os.path.basename(zip_path), "size": os.path.getsize(zip_path),
                "counts": w["counts"], "domains": len(w["domains_created"]), "relationships": w["relationships_created"],
                "gdal": w["gdal_version"], "subtypes": subtypes, "ver": ver, "qc": qc, "qc_csv": qc_csv}

    def _run_generate(self, job_id: str):
        from .assign import flush
        flush(self, job_id)      # map edits still waiting to be written
        stage = "generate"
        try:
            st = self.state(job_id)
            schema = self.reg.get(st["schema_id"])
            from ..schema.composite import is_composite, part_of
            res = self._convert(job_id, self._progress(job_id, stage, 0.0, 0.35))
            out_dir = os.path.join(self.jdir(job_id), "output")
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            from .. import submission as sub
            proj = self.project_info(job_id)
            sub_name = sub.submission_name(proj)
            p_missing, p_errors = sub.check(proj)
            multi = is_composite(schema)

            def names_for(pproj, suffix):
                nm = sub.submission_name(pproj)
                if nm:   # DMT standard 3.6.1 — the delivered geodatabase keeps the exact submission name;
                    # a re-generation moves the previous delivery to output/_previous/<time>/ instead of renaming
                    prev = [x for x in os.listdir(out_dir) if x == f"{nm}.gdb" or x.startswith(f"{nm}.gdb.") or x == f"{nm}.zip"]
                    if prev:
                        arch = os.path.join(out_dir, "_previous", stamp)
                        os.makedirs(arch, exist_ok=True)
                        for x in prev:
                            shutil.move(os.path.join(out_dir, x), os.path.join(arch, x))
                    b_ = nm
                else:    # a re-generation never overwrites a delivery
                    b_ = re.sub(r"[^A-Za-z0-9_]", "_", os.path.splitext(st["file"]["name"])[0])[:60] + f"_{suffix}_{stamp}"
                return nm, b_

            # each feature class goes into ITS OWN template geodatabase, copied unchanged
            if multi:
                groups: Dict[str, list] = {}
                for f in res.features:
                    if f.target:
                        groups.setdefault(part_of(schema, f.target).schema_id, []).append(f)
                parts = [schema.parts[k] for k in sorted(groups) if any(not f.failed and f.write and f.geom is not None for f in groups[k])]
                if not parts:
                    raise JobError("no feature can be written — map CAD layers to feature classes and resolve the blocking errors")
            else:
                groups, parts = {schema.schema_id: res.features}, [schema]
            deliveries = []
            for i, part in enumerate(parts):
                code = sub.SCHEMA_ASSET.get(part.schema_id, part.schema_id)
                pname, pbase = names_for({**proj, "asset_type": code} if multi else proj, part.schema_id)
                lo = 0.35 + 0.5 * i / len(parts)
                hi = 0.35 + 0.5 * (i + 1) / len(parts)
                label = f" [{code}]" if multi else ""
                self._update(job_id, stage=stage, status="running", progress=lo,
                             message=f"Writing {part.source_template or part.display_name}" + (f" ({i + 1}/{len(parts)})" if multi else ""))
                d = self._write_template(job_id, part, groups[part.schema_id], os.path.join(out_dir, f"{pbase}.gdb"),
                                         pbase, out_dir, res, label, lo, hi)
                d.update(code=code, submission_name=pname, base=pbase)
                deliveries.append(d)
            # one verification: every check of every geodatabase (prefixed with its asset code when there are several)
            ver = {"passed": all(d["ver"]["passed"] for d in deliveries), "checks": []}
            for d in deliveries:
                for c in d["ver"]["checks"]:
                    ver["checks"].append({**c, "check": (f"[{d['code']}] " if multi else "") + c["check"]})
                for k, v in d["ver"].items():
                    if k not in ("passed", "checks") and k not in ver:
                        ver[k] = v
            if multi:
                ver["checks"].insert(0, {"check": "One geodatabase per template", "passed": True, "severity": "info",
                                         "detail": "; ".join(f"{d['name']} ({sum(d['counts'].values())} features in "
                                                             f"{sum(1 for v in d['counts'].values() if v)} classes)" for d in deliveries)})
            base = deliveries[0]["base"] if not multi else names_for(proj, "multi")[1]
            gdb_name = ", ".join(d["name"] for d in deliveries)
            gdb_path = deliveries[0]["gdb_path"]
            qcs = [d["qc"] for d in deliveries if d["qc"] is not None]
            if qcs:
                qc = {"template": ", ".join(q["template"] for q in qcs), "errors": [e for q in qcs for e in q["errors"]],
                      "by_check": {}}
                for q in qcs:
                    for k, v in q.items():
                        if k not in ("template", "errors", "by_check"):
                            qc.setdefault(k, v)
                    for k, v in q["by_check"].items():
                        qc["by_check"][k] = qc["by_check"].get(k, 0) + v
                qc_csvs = [d["qc_csv"] for d in deliveries if d["qc_csv"]]
                qc_csv = qc_csvs[0]
                if multi:      # one combined list as well, for the report and the download button
                    qc_csv = os.path.join(out_dir, f"{base}_QCErrors.csv")
                    from ..gdb.dmt_qc import write_qc_csv
                    write_qc_csv(qc, qc_csv)
            else:
                qc, qc_csvs = None, []
            # DMT standard 3.6.1 / 3.8 / 3.9 — submission name, metadata, project boundary
            no_pid = not str(proj.get("project_id") or "").strip()
            ver["checks"].append({"check": "Submission file name (DMT standard 3.6.1)",
                                  "passed": all(d["submission_name"] for d in deliveries) and not no_pid,
                                  "severity": "warning",
                                  "detail": (f"{gdb_name} — Project ID not entered yet (NOPID in the name, PROJECTID empty); "
                                             "enter it before the official submission") if sub_name and no_pid else
                                            gdb_name if all(d["submission_name"] for d in deliveries) else "project information incomplete — "
                                            "enter entity, asset type, stages, version and date"})
            ver["checks"].append({"check": "Project information & metadata (DMT standard 3.8)",
                                  "passed": not p_missing and not p_errors, "severity": "warning",
                                  "detail": "all mandatory elements present" if not p_missing and not p_errors else
                                            "; ".join(([f"missing: {', '.join(p_missing[:6])}{'…' if len(p_missing) > 6 else ''}"] if p_missing else []) + p_errors[:3])})
            bound_zip = None
            bnd = None
            try:
                geom, desc = sub.build_boundary(self.load_doc(job_id), res.features, _pipeline(self.get_mapping(job_id), schema),
                                                proj.get("boundary") or {})
                if geom is not None:
                    bpath = os.path.join(out_dir, f"{base}_PROJECT_BOUND.gdb")
                    bnd = sub.write_boundary_gdb(bpath, geom, schema.spatial_reference.epsg or 32640, proj, desc)
                    bound_zip = bpath + ".zip"
                    _zip_dir(bpath, bound_zip)
                ver["checks"].append({"check": "Project boundary PROJECT_BOUND (DMT standard 3.9)", "passed": geom is not None,
                                      "severity": "warning",
                                      "detail": (f"{bnd['parts']} polygon(s), {bnd['area_m2']:,.0f} m² — {desc}" if geom is not None else desc)
                                                + " (delivered as its own geodatabase; the template is not altered)"})
            except Exception as e:  # noqa: BLE001
                self.log(job_id, f"project boundary failed: {e}")
                ver["checks"].append({"check": "Project boundary PROJECT_BOUND (DMT standard 3.9)", "passed": False,
                                      "severity": "warning", "detail": str(e)})
            self._update(job_id, stage=stage, status="running", progress=0.88, message="Packaging and writing reports")
            d0 = deliveries[0]
            counts: Dict[str, int] = {}
            for d in deliveries:
                counts.update(d["counts"])
            gdb_info = {"name": gdb_name, "zip": d0["zip"], "size": sum(d["size"] for d in deliveries), "counts": counts,
                        "domains": sum(d["domains"] for d in deliveries), "relationships": d0["relationships"],
                        "gdal": d0["gdal"], "subtypes": d0["subtypes"],
                        "geodatabases": [{k: d[k] for k in ("name", "zip", "size", "template", "schema_id", "code", "counts")}
                                         for d in deliveries]}
            bundle = build_bundle(job_id, st["file"]["name"], res, gdb=gdb_info, verification=ver)
            if qc is not None:
                bundle["dmt_qc"] = {k: v for k, v in qc.items() if k != "errors"}
                bundle["dmt_qc"]["errors"] = qc["errors"][:5000]
            self._attach_key(job_id, bundle)
            self._write(job_id, "results.json", bundle)
            self._write(job_id, "preview.geojson", preview_geojson(res))
            xlsx = os.path.join(out_dir, f"{base}_{schema.schema_id}_{stamp}_report.xlsx")
            pdf = os.path.join(out_dir, f"{base}_{schema.schema_id}_{stamp}_report.pdf")
            bundle["project"] = {"name": sub_name, "info": {k: v for k, v in proj.items() if k != "boundary"},
                                 "missing": p_missing, "errors": p_errors, "boundary": bnd}
            self._write(job_id, "results.json", bundle)
            try:
                from ..knowledge.annex import compare as annex_compare
                from ..validation.topology import asset_code
                code = asset_code(schema)
                xl_bundle = {**bundle, "standard_annex": annex_compare(schema, code) if code else None}
            except Exception:  # noqa: BLE001 — informative sheet only
                xl_bundle = bundle
            write_excel_report(xl_bundle, xlsx)
            write_pdf_report(bundle, pdf)
            ev = (res.elevation or {})
            vertical = ("Ras Ghumays" if ev.get("ras_ghumays") else
                        ((ev.get("vertical") or {}).get("result") or "not set (no elevation source)"))
            meta_path = os.path.join(out_dir, f"{base}_metadata.xml")
            with open(meta_path, "w", encoding="utf-8") as fh:
                fh.write(sub.metadata_xml(proj, schema, bundle, base, (ev.get("dem") or {}).get("bbox_ll") or self._bbox_or_none(job_id),
                                          vertical))
            outputs = {"gdb_zip": d0["zip"], "xlsx": os.path.basename(xlsx), "pdf": os.path.basename(pdf),
                       "metadata": os.path.basename(meta_path)}
            if multi:
                for i, d in enumerate(deliveries, 1):
                    outputs[f"gdb_{i}"] = d["zip"]
            if qc is not None:
                outputs["qc_csv"] = os.path.basename(qc_csv)
            if bound_zip:
                outputs["boundary_zip"] = os.path.basename(bound_zip)
            # one package for the submission: geodatabase, PROJECT_BOUND, metadata, QC errors, reports
            pkg = os.path.join(out_dir, f"{base}_submission.zip")
            import zipfile as _zf
            with _zf.ZipFile(pkg, "w", _zf.ZIP_DEFLATED) as z:
                for root_dir in [d["gdb_path"] for d in deliveries] + ([bound_zip[:-4]] if bound_zip else []):
                    for r_, _, fs in os.walk(root_dir):
                        for fn in fs:
                            full = os.path.join(r_, fn)
                            z.write(full, os.path.relpath(full, out_dir))
                for extra in [meta_path, xlsx, pdf] + (list(dict.fromkeys(qc_csvs + [qc_csv])) if qc is not None else []):
                    z.write(extra, os.path.basename(extra))
            outputs["package"] = os.path.basename(pkg)
            total = sum(counts.values())
            if ver["passed"]:
                self.log(job_id, f"generated {gdb_name}: {total} features, verification passed")
                self._update(job_id, stage="done", status="completed", progress=1.0, outputs=outputs,
                             message=f"{gdb_name} — {total} features, verification passed", completed_at=_now())
            else:
                failed = [c["check"] for c in ver["checks"] if not c["passed"] and c["severity"] == "error"]
                self.log(job_id, f"verification FAILED: {failed}")
                self._update(job_id, stage="generate", status="failed", progress=1.0, outputs=outputs,
                             message="Output geodatabase failed verification: " + "; ".join(failed),
                             error={"stage": "verify", "message": "; ".join(failed)})
        except Exception as e:  # noqa: BLE001
            self._fail(job_id, stage, e)

    def _run_autofix(self, job_id: str, codes):
        from . import topology_fix as F
        stage = "validation"
        try:
            t0 = time.time()
            rep = F.run(self, job_id, codes, self._progress(job_id, stage, 0.0, 0.85))
            self.log(job_id, f"auto-fix: {rep['fixed_n']} corrected, {rep['reverted_n']} reverted after verification, "
                             f"findings {rep['before']} -> {rep['after']} ({time.time() - t0:.1f}s)")
        except Exception as e:  # noqa: BLE001
            self._fail(job_id, stage, e)
            return
        self._run_validation(job_id)

    def start_autofix(self, job_id: str, codes=None):
        """Auto-Fix in the background: analyse, fix, verify (revert what got worse), then the normal validation."""
        self._require(job_id, ["mapping", "validation", "done", "generate"])
        from .assign import flush
        flush(self, job_id)
        self._update(job_id, stage="validation", status="queued", progress=0.0, message="Auto-Fix queued")
        self.pool.submit(self._run_autofix, job_id, codes)
        return self.state(job_id)

    def start_validation(self, job_id: str):
        self._require(job_id, ["mapping", "validation", "done", "generate"])
        self._update(job_id, stage="validation", status="queued", progress=0.0, message="Queued for validation")
        self.pool.submit(self._run_validation, job_id)
        return self.state(job_id)

    def completeness(self, job_id: str) -> Dict[str, Any]:
        """The guarantee: nothing is generated while any CAD content is undecided or blocked."""
        m = self.get_mapping(job_id)
        open_groups = [{"group": k, "status": v.get("status"), "target": v.get("target"),
                        "suggested_role": v.get("suggested_role"), "reason": (v.get("evidence") or [""])[0]}
                       for k, v in m["layers"].items()
                       if v.get("role") != "ignore" and (not v.get("target") or v.get("status") in ("SUGGESTED", "MANUAL", "UNRESOLVED"))]
        res = self._read(job_id, "results.json")
        blocking = [i["message"] for i in (res or {}).get("issues", [])
                    if i.get("code") in ("not_null_unfilled", "elevation_missing", "elevation_dem_missing", "z_not_finite")]
        st = self.state(job_id)
        stale = st["status"] not in ("validated", "completed") and not (st["status"] == "failed" and st["stage"] == "generate")
        # the municipality's template was updated after this job was validated -> validate again on the new version
        try:
            schema = self.reg.get(st["schema_id"])
            cur_ver = schema.version
            for k, v in m["layers"].items():
                if v.get("target") and v.get("role") != "ignore" and schema.get_layer(v["target"]) is None:
                    open_groups.append({"group": k, "status": "TARGET_REMOVED", "target": v["target"], "suggested_role": None,
                                        "reason": f"'{v['target']}' is not in the updated template (version {cur_ver}) — choose a new target"})
        except KeyError:
            cur_ver = None
        template_changed = bool(st.get("validated_schema_version")) and cur_ver is not None and st["validated_schema_version"] != cur_ver
        if template_changed:
            blocking.append(f"The template was updated to version {cur_ver} after this job was validated "
                            f"(version {st['validated_schema_version']}) — run validation again")
        return {"complete": not open_groups and not blocking and not stale and res is not None and not template_changed,
                "open_groups": open_groups, "blocking_errors": blocking,
                "validation_current": not stale and res is not None and not template_changed,
                "template_version": cur_ver, "validated_template_version": st.get("validated_schema_version")}

    def start_generate(self, job_id: str):
        self._require(job_id, ["validation", "done", "generate"])
        c = self.completeness(job_id)
        if not c["complete"]:
            parts = []
            if c["open_groups"]:
                parts.append(f"{len(c['open_groups'])} CAD layer group(s) still need a decision "
                             f"({', '.join(g['group'] for g in c['open_groups'][:5])})")
            if c["blocking_errors"]:
                parts.append(f"{len(c['blocking_errors'])} blocking error(s): {c['blocking_errors'][0]}")
            if not c["validation_current"]:
                parts.append("validation must be re-run after the last change")
            raise JobError("Generation blocked — " + "; ".join(parts), 409)
        self._update(job_id, stage="generate", status="queued", progress=0.0, message="Queued for geodatabase generation",
                     outputs={})
        self.pool.submit(self._generate_and_notify, job_id)
        return self.state(job_id)

    def _generate_and_notify(self, job_id: str):
        self._run_generate(job_id)
        if self.on_generate_end:
            try:
                self.on_generate_end(job_id, self.state(job_id).get("status") == "completed")
            except Exception as e:  # noqa: BLE001
                self.log(job_id, f"post-generation hook failed: {e}")

    def results(self, job_id: str, full: bool = False) -> Dict[str, Any]:
        """The validation / delivery bundle. For the browser it is kept light on large drawings: repetitive
        per-feature information findings are summarised (errors and warnings are always listed in full) and the
        feature list carries only what the screens use. The file on disk (and the reports) keep everything."""
        from .assign import flush
        flush(self, job_id)
        r = self._read(job_id, "results.json")
        if r is None:
            raise JobError("no results yet — run validation first", 409)
        if full:
            return r
        return _light_bundle(r)

    def preview(self, job_id: str) -> Dict[str, Any]:
        from .assign import flush
        flush(self, job_id)
        r = self._read(job_id, "preview.geojson")
        if r is None:
            raise JobError("no preview yet — run validation first", 409)
        return r

    def feature(self, job_id: str, fid: int) -> Dict[str, Any]:
        res = self._results.get(job_id)
        if res is None:
            res = self._convert(job_id, None)
        d = feature_detail(res, fid)
        if d is None:
            raise JobError("feature not found", 404)
        return d


INFO_PER_CODE = 200
FEATURE_KEYS = ("fid", "objectid", "target", "subtype", "status", "failure", "layer", "handle", "issue_count")


def _light_bundle(r: Dict[str, Any]) -> Dict[str, Any]:
    issues = r.get("issues") or []
    feats = r.get("features") or []
    if len(issues) <= 3000 and len(feats) <= 5000:
        return r
    kept, extra = [], {}
    seen: Dict[tuple, int] = {}
    for i in issues:
        if i.get("severity") != "info":
            kept.append(i)
            continue
        k = (i.get("code"), i.get("target_layer"))
        seen[k] = seen.get(k, 0) + 1
        if seen[k] <= INFO_PER_CODE:
            kept.append(i)
        else:
            extra[k] = extra.get(k, 0) + 1
    next_id = max([i.get("id") or 0 for i in issues] + [0]) + 1
    for (code, layer), n in extra.items():
        kept.append({"id": next_id, "severity": "info", "category": "summary", "code": f"{code}_more",
                     "message": f"{layer or 'all classes'}: {n:,} more '{code}' finding(s) of the same kind — listed in "
                                f"the Excel report", "suggestion": "", "target_layer": layer, "feature_id": None,
                     "field": None, "source": None, "location": None, "related": [], "lonlat": None, "objectid": None})
        next_id += 1
    return {**r, "issues": kept, "issues_total": len(issues),
            "features": [{k: f.get(k) for k in FEATURE_KEYS} for f in feats]}
