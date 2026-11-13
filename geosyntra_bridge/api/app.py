"""
REST API + web UI host for the CAD to GDB Schema Converter.

Run:  python -m geosyntra_bridge.server          (see README for options)
All routes live under  <GSB_BASE_PATH>/api ; the built React UI is served at <GSB_BASE_PATH>/.
"""
from __future__ import annotations

import os
import re
import shutil
from contextlib import asynccontextmanager
import tempfile
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..cad.reader import dwg_converter
from ..config import Settings, settings as default_settings
from ..gdb.arcpy_enhancer import arcpy_available
from ..gdb.gdal_c import gdal_status
from ..geometry.crs import COMMON_CRS, crs_info, validate_epsg
from ..jobs.manager import JobError, JobManager
from ..schema.loader import SchemaError, SchemaRegistry, lint_schema
from .auth import Principal, make_auth

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def create_app(s: Optional[Settings] = None) -> FastAPI:
    s = s or default_settings
    os.makedirs(s.data_dir, exist_ok=True)
    try:                                   # ODA package dropped into tools/oda → unpack it once, in the background
        import threading
        from ..cad.reader import unpack_bundled_oda
        threading.Thread(target=unpack_bundled_oda, daemon=True).start()
    except Exception:  # noqa: BLE001
        pass
    reg = SchemaRegistry(s.schemas_dir, s.extra_schema_dirs)
    jobs = JobManager(s, reg)
    from ..accounts.routes import Accounts, build_router as accounts_router
    from ..accounts import billing as acc_billing
    acc = Accounts(s)
    auth = make_auth(s, acc)

    def _after_generate(job_id: str, ok: bool):
        if not ok:
            n = acc_billing.refund_generation(acc.store, job_id, "generation failed — refunded")
            if n:
                jobs.log(job_id, f"billing: {n} credits refunded (generation did not complete)")
    jobs.on_generate_end = _after_generate
    @asynccontextmanager
    async def lifespan(_app):
        yield
        jobs.shutdown()

    app = FastAPI(title="GeoSyntra Bridge — CAD to GDB Schema Converter", version=__version__, lifespan=lifespan,
                  docs_url=f"{s.base_path}/api/docs", openapi_url=f"{s.base_path}/api/openapi.json")
    app.state.jobs = jobs
    app.state.registry = reg
    app.state.accounts = acc
    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith(f"{s.base_path}/api"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.exception_handler(JobError)
    async def job_error(_: Request, e: JobError):
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    r = APIRouter(prefix=f"{s.base_path}/api")

    def owned(job_id: str, p: Principal) -> str:
        jobs.check_owner(job_id, p.user, p.admin)
        return job_id

    # ---------------------------------------------------------------- updates from GitHub
    @r.get("/system/update")
    def update_status(p: Principal = Depends(auth)):
        from .. import updater
        return updater.status

    @r.post("/system/update")
    def update_now(p: Principal = Depends(auth)):
        if not p.admin:
            raise HTTPException(403, "only the owner can update")
        from .. import updater
        return updater.check_and_update()

    # ---------------------------------------------------------------- system
    from ..server import _code_stamp as _stamp_of
    _pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _code_stamp():
        return _stamp_of(_pkg)
    _LOADED_STAMP = _code_stamp()

    @r.get("/health")
    def health():
        """For load balancers / monitoring: no sign-in, no personal data."""
        gs = gdal_status()
        busy = 0
        try:
            busy = sum(1 for j in jobs.list_jobs("", True, 500) if j.get("status") in ("queued", "running"))
        except Exception:  # noqa: BLE001
            pass
        return {"status": "ok", "version": __version__, "filegdb": bool(gs.get("available") and gs.get("filegdb_write")),
                "dwg": bool(dwg_converter()), "workers": s.workers, "jobs_running": busy}

    @r.get("/system/capabilities")
    def capabilities(p: Principal = Depends(auth)):
        gs = gdal_status()
        oda = dwg_converter()
        u = acc.store.user(p.uid) if p.uid else None
        return {"version": __version__, "user": p.email or p.user, "admin": p.admin, "role": p.role or ("owner" if p.admin else "user"),
                "credits": u["credits"] if u else None, "gdal": gs,
                "filegdb": bool(gs.get("available") and gs.get("filegdb_write")),
                "dwg": {"available": bool(oda), "converter": oda},
                "arcpy": arcpy_available(), "max_upload_mb": s.max_upload_mb, "auth_mode": s.auth_mode,
                # newer program files on disk than the running server -> the app asks for a restart
                "update_pending": _code_stamp() > _LOADED_STAMP + 1}

    # ---------------------------------------------------------------- schemas
    @r.get("/schemas")
    def list_schemas(p: Principal = Depends(auth)):
        return [sc.summary() for sc in sorted(reg.all().values(), key=lambda x: (x.status != "approved", x.display_name))]

    @r.get("/schemas/{schema_id}/standard-comparison")
    def standard_comparison(schema_id: str, p: Principal = Depends(auth)):
        """Annex of the DMT submission standard V7 vs the official template (classes, fields) + section-4 topology."""
        try:
            sc = reg.get(schema_id)
        except KeyError:
            raise HTTPException(404, "schema not found")
        from ..knowledge.annex import compare as annex_compare
        from ..validation.topology import asset_code, standard_comparison as topo_cmp
        code = asset_code(sc)
        if not code:
            raise HTTPException(404, "this schema is not one of the seven DMT asset templates")
        topo = topo_cmp(sc) or {}
        return {"annex": annex_compare(sc, code),
                "topology": {k: topo.get(k) for k in ("asset", "section", "rows")}}

    @r.get("/schemas/{schema_id}")
    def get_schema(schema_id: str, p: Principal = Depends(auth)):
        try:
            sc = reg.get(schema_id)
        except KeyError:
            raise HTTPException(404, "schema not found")
        out = sc.summary()
        out["spatial_reference"]["details"] = validate_epsg(sc.spatial_reference.epsg) if sc.spatial_reference.epsg else None
        out["feature_datasets"] = [f.name for f in sc.feature_datasets]
        out["layers"] = [{"name": l.name, "alias": l.alias, "geometry_type": l.geometry_type,
                          "feature_dataset": l.feature_dataset, "field_count": len(l.fields),
                          "id_field": l.id_field, "network_role": l.network_role,
                          "subtype_field": l.subtype_field, "description": l.description,
                          "legacy_names": l.legacy_names, "dictionary": l.dictionary,
                          "template": (sc.parts[l.template_id].source_template if l.template_id and l.template_id in sc.parts else None),
                          "asset": ({"adm_hl_hardlandscaping": "HL", "adm_sl_softlandscaping": "SL", "adm_ir_irrigation": "IR",
                                     "adm_rd_roads": "RD", "adm_stl_streetlighting": "STL", "adm_sw_stormwater": "SW",
                                     "adm_itc": "ITC"}.get(l.template_id or "") if l.template_id else None),
                          "subtypes": [{"code": st.code, "name": st.name} for st in l.subtypes],
                          "label_fields": sorted({rl.field for rl in sc.rules_for(l.name)} | {rl.field for rl in sc.rules_for(l.name, "layer")}),
                          "fields": [{"name": f.name, "alias": f.alias, "type": f.field_type, "length": f.length,
                                      "domain": f.domain, "required": f.required, "nullable": f.nullable, "default": f.default,
                                      "description": f.description, "responsibility": f.responsibility,
                                      "contractor_mandatory": f.contractor_mandatory, "allowed_values": f.allowed_values}
                                     for f in l.fields]} for l in sc.layers]
        out["domains"] = [{"name": d.name, "type": d.domain_type, "field_type": d.field_type,
                           "values": d.coded_values if d.domain_type == "coded" else None,
                           "min": d.min_value, "max": d.max_value} for d in sc.domains]
        out["label_rules"] = [{"field": lr.field, "pattern": lr.pattern, "targets": lr.targets,
                               "description": lr.description} for lr in sc.label_rules]
        out["network"] = sc.network.__dict__
        out["relationships"] = [rel.__dict__ for rel in sc.relationships]
        out["lint"] = lint_schema(sc)
        return out

    @r.post("/schemas/import")
    async def import_schema(file: UploadFile = File(...), schema_id: str = Form(...), display_name: str = Form(...),
                            discipline: str = Form(""), overwrite: bool = Form(False), p: Principal = Depends(auth)):
        if not p.admin:
            raise HTTPException(403, "only administrators can add schemas")
        from ..gdb.schema_import import extract_gdb_zip, schema_from_gdb
        tmp = tempfile.mkdtemp(prefix="gsb_schema_", dir=s.data_dir)
        try:
            zp = os.path.join(tmp, "upload.zip")
            with open(zp, "wb") as f:
                shutil.copyfileobj(file.file, f, 1024 * 1024)
            try:
                gdb = extract_gdb_zip(zp, os.path.join(tmp, "x"))
                d = schema_from_gdb(gdb, schema_id, display_name, discipline)
                sc = reg.save(d, overwrite=overwrite)
            except (ValueError, SchemaError) as e:
                raise HTTPException(400, str(e))
            return sc.summary()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ------------------------------------------------ template updates (municipality publishes a new template)
    from ..schema.template_update import TemplateUpdates
    tupd = TemplateUpdates(reg, s.data_dir, s.rules_dir, s.jobs_dir)
    app.state.template_updates = tupd

    def _sid(schema_id: str):
        try:
            reg.get(schema_id)
        except KeyError:
            raise HTTPException(404, "schema not found")

    @r.post("/schemas/{schema_id}/template-updates")
    async def stage_template_update(schema_id: str, file: UploadFile = File(...), p: Principal = Depends(auth)):
        """Upload the new template (.gdb zipped) -> change report + impact. Nothing changes until it is applied."""
        if not p.admin:
            raise HTTPException(403, "only administrators can update templates")
        _sid(schema_id)
        if not (file.filename or "").lower().endswith(".zip"):
            raise HTTPException(400, "upload the template geodatabase as a .zip of the .gdb folder")
        tmp = tempfile.mkdtemp(prefix="gsb_tu_up_", dir=s.data_dir)
        try:
            zp = os.path.join(tmp, "upload.zip")
            with open(zp, "wb") as f:
                shutil.copyfileobj(file.file, f, 1024 * 1024)
            try:
                return tupd.stage(schema_id, zp, file.filename, p.user)
            except (ValueError, SchemaError) as e:
                raise HTTPException(400, str(e))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @r.get("/schemas/{schema_id}/template-updates")
    def list_template_updates(schema_id: str, p: Principal = Depends(auth)):
        _sid(schema_id)
        return tupd.list(schema_id)

    @r.get("/schemas/{schema_id}/template-updates/{uid}")
    def get_template_update(schema_id: str, uid: str, p: Principal = Depends(auth)):
        try:
            return tupd.get(schema_id, uid)
        except KeyError:
            raise HTTPException(404, "update not found")

    @r.post("/schemas/{schema_id}/template-updates/{uid}/apply")
    def apply_template_update(schema_id: str, uid: str, body: Dict[str, Any] = Body(default={}),
                              p: Principal = Depends(auth)):
        """body: {"renames": {"classes": {old: new}, "fields": {"LAYER.OLD": "NEW"}}} — confirmed renames only."""
        if not p.admin:
            raise HTTPException(403, "only administrators can update templates")
        try:
            return tupd.apply(schema_id, uid, p.user, (body or {}).get("renames"))
        except KeyError:
            raise HTTPException(404, "update not found")
        except ValueError as e:
            raise HTTPException(409, str(e))

    @r.post("/schemas/{schema_id}/template-updates/{uid}/reject")
    def reject_template_update(schema_id: str, uid: str, p: Principal = Depends(auth)):
        if not p.admin:
            raise HTTPException(403, "only administrators can update templates")
        try:
            return tupd.reject(schema_id, uid, p.user)
        except KeyError:
            raise HTTPException(404, "update not found")
        except ValueError as e:
            raise HTTPException(409, str(e))

    @r.post("/schemas/{schema_id}/versions/{folder}/restore")
    def restore_template_version(schema_id: str, folder: str, p: Principal = Depends(auth)):
        if not p.admin:
            raise HTTPException(403, "only administrators can update templates")
        try:
            return tupd.restore(schema_id, folder, p.user)
        except KeyError:
            raise HTTPException(404, "archived version not found")

    @r.get("/schemas/{schema_id}/profiles")
    def profiles(schema_id: str, p: Principal = Depends(auth)):
        return jobs.profiles.list(schema_id)

    # ---------------------------------------------------------------- CRS
    @r.get("/crs")
    def crs_search(q: str = "", p: Principal = Depends(auth)):
        if not q:
            return [crs_info(c) for c in COMMON_CRS]
        if q.strip().isdigit():
            info = validate_epsg(int(q))
            return [info] if info else []
        from pyproj.database import query_crs_info
        ql = q.lower()
        hits = [c for c in query_crs_info(auth_name="EPSG") if ql in c.name.lower()][:25]
        return [{"epsg": int(c.code), "name": c.name, "is_geographic": c.type.name.startswith("GEOGRAPHIC"),
                 "units": "", "area_of_use": None} for c in hits]

    # ---------------------------------------------------------------- jobs
    @r.post("/jobs", status_code=201)
    def create_job(file: UploadFile = File(...), schema_id: str = Form(...), p: Principal = Depends(auth)):
        return jobs.create(p.user, schema_id, file.filename or "drawing.dxf", file.file)

    @r.get("/jobs")
    def list_jobs(p: Principal = Depends(auth)):
        return jobs.list_jobs(p.user, p.admin)

    @r.get("/jobs/{job_id}")
    def job_state(job_id: str, p: Principal = Depends(auth)):
        return jobs.check_owner(job_id, p.user, p.admin)

    @r.delete("/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str, p: Principal = Depends(auth)):
        jobs.delete(owned(job_id, p))
        return Response(status_code=204)

    @r.get("/jobs/{job_id}/discovery")
    def job_discovery(job_id: str, p: Principal = Depends(auth)):
        d = jobs._read(owned(job_id, p), "discovery.json")
        if d is None:
            raise HTTPException(409, "discovery not finished")
        return d

    @r.get("/jobs/{job_id}/mapping")
    def get_mapping(job_id: str, p: Principal = Depends(auth)):
        return jobs.get_mapping(owned(job_id, p))

    @r.patch("/jobs/{job_id}/mapping")
    def patch_mapping(job_id: str, patch: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        return jobs.update_mapping(owned(job_id, p), patch)

    @r.post("/jobs/{job_id}/mapping/accept-suggestions")
    def accept_suggestions(job_id: str, min_score: Optional[float] = None, p: Principal = Depends(auth)):
        return jobs.accept_suggestions(owned(job_id, p), p.user, min_score)

    @r.get("/jobs/{job_id}/delivery")
    def delivery(job_id: str, p: Principal = Depends(auth)):
        """Mapping & delivery table: CAD layer -> original geodatabase -> feature class -> status."""
        from ..reporting.excel import delivery_rows
        b = jobs.results(owned(job_id, p), full=True)
        cols = ["layer", "style", "cad_geometry", "cad_objects", "geodatabase", "feature_class", "fc_geometry",
                "written", "failed", "status", "confidence", "mapping"]
        return [dict(zip(cols, r)) for r in delivery_rows(b)]

    @r.get("/jobs/{job_id}/required-values")
    def required_values(job_id: str, p: Principal = Depends(auth)):
        """Values the drawing does not contain (NOT NULL fields) and texts that are not domain codes."""
        return jobs.required_values(owned(job_id, p))

    @r.post("/jobs/{job_id}/drawings")
    def add_drawing(job_id: str, file: UploadFile = File(...), p: Principal = Depends(auth)):
        """Add another sheet of the same project; all drawings are read and mapped together."""
        return jobs.add_drawing(owned(job_id, p), file.filename or "drawing.dxf", file.file)

    @r.get("/jobs/{job_id}/cleanup")
    def cleanup(job_id: str, p: Principal = Depends(auth)):
        """Automatic clean-up done on reading, and the advanced proposals awaiting review."""
        return jobs.cad_cleanup(owned(job_id, p))

    @r.post("/jobs/{job_id}/cleanup")
    def set_cleanup(job_id: str, body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        return jobs.set_cleanup(owned(job_id, p), body.get("decisions") or {}, body.get("settings"), bool(body.get("apply", True)))

    @r.get("/memory")
    def memory(p: Principal = Depends(auth)):
        """Project memory: field values and drawing-text -> code choices reused for the next drawings."""
        return jobs.memory(p.user)

    @r.delete("/memory")
    def forget_all(p: Principal = Depends(auth)):
        return jobs.forget(p.user)

    @r.delete("/memory/{field}")
    def forget(field: str, p: Principal = Depends(auth)):
        return jobs.forget(p.user, field)

    # ---------------------------------------------------------------- DEM elevation
    @r.get("/jobs/{job_id}/submission")
    def submission_status(job_id: str, p: Principal = Depends(auth)):
        return jobs.submission_status(owned(job_id, p))

    @r.get("/elevation/sources")
    def elevation_sources():
        from ..elevation.engine import DEFAULT_OPTIONS
        from ..elevation.sources import SOURCES, VERTICAL_REFS
        return {"sources": {k: {kk: vv for kk, vv in v.items() if kk != "url"} for k, v in SOURCES.items()},
                "vertical_refs": VERTICAL_REFS, "defaults": DEFAULT_OPTIONS,
                "opentopography_key_configured": bool(os.environ.get("GSB_OPENTOPOGRAPHY_KEY"))}

    @r.post("/jobs/{job_id}/elevation/prepare")
    def elevation_prepare(job_id: str, body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        return jobs.prepare_dem(owned(job_id, p), str(body.get("source") or ""), api_key=body.get("api_key") or None)

    @r.post("/jobs/{job_id}/elevation/upload")
    def elevation_upload(job_id: str, file: UploadFile = File(...), crs: str = Form(""), vertical: str = Form("unknown"),
                         p: Principal = Depends(auth)):
        return jobs.upload_dem(owned(job_id, p), file.filename or "dem.tif", file.file, crs or None, vertical or "unknown")

    @r.delete("/jobs/{job_id}/elevation")
    def elevation_disable(job_id: str, p: Principal = Depends(auth)):
        return jobs.disable_dem(owned(job_id, p))

    @r.get("/jobs/{job_id}/elevation/preview")
    def elevation_preview(job_id: str, p: Principal = Depends(auth)):
        return jobs.dem_preview(owned(job_id, p))

    @r.post("/jobs/{job_id}/reference")
    def attach_reference(job_id: str, file: UploadFile = File(...), p: Principal = Depends(auth)):
        return jobs.attach_reference(owned(job_id, p), file.filename or "reference.zip", file.file)

    @r.delete("/jobs/{job_id}/reference")
    def remove_reference(job_id: str, p: Principal = Depends(auth)):
        return jobs.remove_reference(owned(job_id, p))

    @r.put("/jobs/{job_id}/schema")
    def put_schema(job_id: str, body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        try:
            return jobs.change_schema(owned(job_id, p), str(body.get("schema_id")))
        except KeyError:
            raise HTTPException(404, "schema not found")

    @r.post("/jobs/{job_id}/profiles")
    def save_profile(job_id: str, body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "profile name required")
        try:
            return jobs.save_profile(owned(job_id, p), name, p.user, bool(body.get("update_rules", True)),
                                     bool(body.get("authorized", False)) and p.admin)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @r.post("/jobs/{job_id}/profiles/{name}/apply")
    def apply_profile(job_id: str, name: str, p: Principal = Depends(auth)):
        try:
            return jobs.apply_profile(owned(job_id, p), name)
        except KeyError as e:
            raise HTTPException(404, str(e))

    @r.post("/jobs/{job_id}/validate", status_code=202)
    def validate(job_id: str, p: Principal = Depends(auth)):
        return jobs.start_validation(owned(job_id, p))

    @r.get("/jobs/{job_id}/completeness")
    def completeness(job_id: str, p: Principal = Depends(auth)):
        return jobs.completeness(owned(job_id, p))

    @r.post("/jobs/{job_id}/generate", status_code=202)
    def generate(job_id: str, request: Request, p: Principal = Depends(auth)):
        owned(job_id, p)
        charge = None
        if s.auth_mode == "accounts" and p.uid:
            u = acc.store.user(p.uid)
            if acc.store.settings().get("require_email_verification") and not u["email_verified"]:
                raise HTTPException(403, "confirm your e-mail address before generating (Account → resend confirmation)")
            c = jobs.completeness(job_id)      # fail fast before charging
            if not c["complete"]:
                return jobs.start_generate(job_id)       # raises the detailed 409, nothing charged
            feats = int(jobs.results(job_id)["summary"]["features_imported"])
            try:
                charge = acc_billing.charge_generation(acc.store, p.uid, job_id, feats)
            except acc_billing.InsufficientCredits as e:
                raise HTTPException(402, f"Not enough credits: this geodatabase needs {e.needed} credits and you have "
                                         f"{e.balance}. Top up or subscribe in Account & billing.")
        try:
            st = jobs.start_generate(job_id)
        except Exception:
            if charge and charge.get("charged"):
                acc_billing.refund_generation(acc.store, job_id, "generation could not start — refunded")
            raise
        if charge and charge.get("charged"):
            jobs.log(job_id, f"billing: {charge['charged']} credits charged (balance {charge['balance']})")
            # low balance: once, when this charge crossed below the cost of a typical geodatabase (~2,000 features)
            low = acc_billing.quote(acc.store, 2000)
            if charge["balance"] < low <= charge["balance"] + charge["charged"]:
                from ..accounts.integrations import send_mail
                import threading
                u = acc.store.user(p.uid) or {}
                threading.Thread(target=send_mail, daemon=True, args=(
                    u.get("email", ""), "Credits running low",
                    f"Hello {u.get('name') or ''},\n\n{charge['balance']} credits left — a typical geodatabase needs about {low}.\n"
                    f"Top up in Account & billing: {acc.base_url(request)}/#/account", acc.outbox)).start()
        return {**st, "charge": charge}

    @r.get("/jobs/{job_id}/results")
    def results(job_id: str, p: Principal = Depends(auth)):
        return jobs.results(owned(job_id, p))

    @r.get("/jobs/{job_id}/preview")
    def preview(job_id: str, p: Principal = Depends(auth)):
        return jobs.preview(owned(job_id, p))

    @r.get("/crs/transforms")
    def crs_transforms(src: int, dst: int, p: Principal = Depends(auth)):
        from ..geometry.crs import transform_options
        if not validate_epsg(src) or not validate_epsg(dst):
            raise HTTPException(400, "unknown EPSG code")
        return transform_options(src, dst)

    @r.post("/crs/parse")
    def crs_parse(body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        """A .prj / WKT / PROJ string or an EPSG code -> the EPSG coordinate system it defines."""
        from ..geometry.crs import epsg_from_text
        text = str(body.get("text") or "")[:50000]
        e = epsg_from_text(text)
        if not e:
            try:
                from pyproj import CRS
                e = CRS.from_user_input(text.strip()).to_epsg(min_confidence=60)
            except Exception:  # noqa: BLE001
                e = None
        if not e or not validate_epsg(e):
            raise HTTPException(400, "this definition does not match an EPSG coordinate system — search the list instead")
        return crs_info(int(e))

    @r.post("/jobs/{job_id}/crs/check")
    def crs_check(job_id: str, body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        """One drawing point through the job's pipeline: source CRS, delivered (template) CRS and lon/lat."""
        from ..geometry.convert import pipeline_for
        jid = owned(job_id, p)
        m = jobs.get_mapping(jid)
        tgt = jobs.reg.get(jobs.state(jid)["schema_id"]).spatial_reference.epsg
        try:
            pt = (float(body["x"]), float(body["y"]))
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "give x and y")
        out = {"source": list(pipeline_for(m, None).local([pt])[0]), "source_epsg": m.get("source_crs"), "target_epsg": tgt}
        if m.get("source_crs"):
            out["target"] = list(pipeline_for(m, tgt).point(pt)) if tgt else None
            out["lonlat"] = list(pipeline_for(m, 4326).point(pt))
        return out

    @r.post("/crs/project")
    def crs_project(body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        """lon/lat points -> a projected system (the real E / N of a point picked on the satellite image)."""
        from ..geometry.crs import transformer
        try:
            epsg = int(body["epsg"]); pts = [(float(a), float(b)) for a, b in body.get("points") or []]
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "give epsg and points [[lon, lat], …]")
        if not validate_epsg(epsg):
            raise HTTPException(400, "unknown EPSG code")
        t = transformer(4326, epsg)
        if t is None:
            return [list(q) for q in pts]
        xs, ys = t.transform([q[0] for q in pts], [q[1] for q in pts])
        return [[float(a), float(b)] for a, b in zip(xs, ys)]

    @r.get("/crs/presets")
    def crs_presets(p: Principal = Depends(auth)):
        from ..geometry import presets as PR
        return PR.list_presets(s.data_dir, p.user)

    @r.post("/jobs/{job_id}/crs/presets")
    def crs_preset_save(job_id: str, body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        from ..geometry import presets as PR
        jid = owned(job_id, p)
        m = jobs.get_mapping(jid)
        try:
            disc = jobs._read(jid, "discovery.json") or {}
            lay = [x.get("name") if isinstance(x, dict) else str(x) for x in (disc.get("layers") or [])]
            return PR.save_preset(s.data_dir, p.user, str(body.get("name") or ""), m,
                                  (m.get("crs_suggestion") or {}).get("extent"), [x for x in lay if x])
        except ValueError as e:
            raise HTTPException(400, str(e))

    @r.post("/jobs/{job_id}/crs/presets/{pid}/apply")
    def crs_preset_apply(job_id: str, pid: str, p: Principal = Depends(auth)):
        from ..geometry import presets as PR
        jid = owned(job_id, p)
        pre = PR.get_preset(s.data_dir, p.user, pid)
        if not pre:
            raise HTTPException(404, "saved setting not found")
        return jobs.update_mapping(jid, {k: pre.get(k) for k in PR.KEYS})

    @r.delete("/crs/presets/{pid}")
    def crs_preset_delete(pid: str, p: Principal = Depends(auth)):
        from ..geometry import presets as PR
        if not PR.delete_preset(s.data_dir, p.user, pid):
            raise HTTPException(404, "saved setting not found")
        return {"ok": True}

    @r.get("/jobs/{job_id}/georef/labels")
    def georef_labels(job_id: str, p: Principal = Depends(auth)):
        return jobs.georef_from_labels(owned(job_id, p))

    @r.post("/georef/fit")
    def georef_fit(body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        from ..geometry.crs import fit_similarity
        try:
            pts = [(float(q["x"]), float(q["y"]), float(q["E"]), float(q["N"])) for q in body.get("points") or []]
            return fit_similarity(pts, float(body["fixed_scale"]) if body.get("fixed_scale") else None)
        except (KeyError, TypeError, ValueError) as e:
            raise HTTPException(400, f"control points: {e}")

    @r.get("/jobs/{job_id}/crs-detect")
    def crs_detect(job_id: str, p: Principal = Depends(auth)):
        return jobs.detect_crs(owned(job_id, p))

    @r.get("/jobs/{job_id}/cad-preview")
    def cad_preview(job_id: str, epsg: Optional[int] = None, scale: Optional[float] = None, limit: int = 30000,
                    local: bool = False, p: Principal = Depends(auth)):
        return jobs.cad_preview(owned(job_id, p), limit=max(100, min(limit, 30000)), epsg=epsg, scale=scale, local=local)

    @r.get("/jobs/{job_id}/features/{fid}")
    def feature(job_id: str, fid: int, p: Principal = Depends(auth)):
        return jobs.feature(owned(job_id, p), fid)

    @r.get("/jobs/{job_id}/topology")
    def topology_errors(job_id: str, p: Principal = Depends(auth)):
        """Error inspector: topology / spatial-rule / geometry errors of the last validation, with their shapes."""
        return jobs.topology_errors(owned(job_id, p))

    @r.post("/jobs/{job_id}/topology/exception")
    def topology_exception(job_id: str, body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        return jobs.topology_exception(owned(job_id, p), str(body.get("key") or ""), bool(body.get("exception", True)))

    @r.post("/jobs/{job_id}/geometry/merge")
    def merge_features(job_id: str, body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        """Merge features of one layer: {fids: [...]}; the largest keeps its values."""
        return jobs.merge_features(owned(job_id, p), body.get("fids") or [])

    @r.post("/jobs/{job_id}/geometry/{fid}/explode")
    def explode_feature(job_id: str, fid: int, p: Principal = Depends(auth)):
        """One feature per part of a multipart feature."""
        return jobs.explode_feature(owned(job_id, p), fid)

    @r.post("/jobs/{job_id}/geometry/{fid}/split")
    def split_feature(job_id: str, fid: int, body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        """Split a line / polygon by a line drawn on the map: {cutter: GeoJSON LineString in the preview CRS}."""
        return jobs.split_feature(owned(job_id, p), fid, body.get("cutter") or {})

    @r.post("/jobs/{job_id}/autofix/run")
    def autofix_run(job_id: str, body: Dict[str, Any] = Body(default={}), p: Principal = Depends(auth)):
        """Full Auto-Fix in the background (analyse → fix → verify → revert what got worse → validate): {codes?: [...]}."""
        return jobs.start_autofix(owned(job_id, p), (body or {}).get("codes"))

    @r.get("/jobs/{job_id}/autofix/report")
    def autofix_report(job_id: str, p: Principal = Depends(auth)):
        return jobs.autofix_report(owned(job_id, p))

    @r.post("/jobs/{job_id}/autofix/undo")
    def autofix_undo(job_id: str, p: Principal = Depends(auth)):
        """Undo the last Auto-Fix run, then validate again."""
        return jobs.autofix_undo(owned(job_id, p))

    @r.get("/jobs/{job_id}/autofix/preview")
    def autofix_preview(job_id: str, key: str, p: Principal = Depends(auth)):
        """The shape a fix would give, without changing anything."""
        return jobs.autofix_preview(owned(job_id, p), key)

    @r.post("/jobs/{job_id}/topology/autofix")
    def topology_autofix(job_id: str, body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        """One-click corrections (snap dangles / points, clip overlaps, fill gaps, merge parts, repair): {keys: [...]}."""
        return jobs.topology_autofix(owned(job_id, p), body.get("keys") or [], bool(body.get("bulk")))

    @r.post("/jobs/{job_id}/geometry")
    def save_geometry(job_id: str, body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        """Save the pending geometry edits of the map: [{fid, geometry} | {fid, delete: true}] — then validate again."""
        return jobs.save_geometry(owned(job_id, p), body.get("edits") or [])

    @r.post("/jobs/{job_id}/geometry/{fid}/restore")
    def restore_geometry(job_id: str, fid: int, p: Principal = Depends(auth)):
        return jobs.restore_geometry(owned(job_id, p), fid)

    @r.get("/jobs/{job_id}/assign")
    def assign_overview(job_id: str, p: Principal = Depends(auth)):
        """Every feature with its fill status (completed / incomplete / error / skipped) and its OBJECTID."""
        return jobs.assign_overview(owned(job_id, p))

    @r.post("/jobs/{job_id}/assign-many")
    def assign_save_many(job_id: str, body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        """The same values for several features: {fids: [...], fields: {...}}."""
        return jobs.assign_save_many(owned(job_id, p), body.get("fids") or [], body.get("fields") or {})

    @r.get("/jobs/{job_id}/assign-values")
    def assign_values(job_id: str, target: str, field: str, p: Principal = Depends(auth)):
        """Values already used in a field of a feature class, most frequent first."""
        return jobs.assign_values(owned(job_id, p), target, field)

    @r.get("/jobs/{job_id}/assign/{fid}")
    def assign_detail(job_id: str, fid: int, p: Principal = Depends(auth)):
        """One feature: attributes, missing fields, and the row read from the delivered geodatabase."""
        return jobs.assign_detail(owned(job_id, p), fid)

    @r.post("/jobs/{job_id}/assign/{fid}")
    def assign_save(job_id: str, fid: int, body: Dict[str, Any] = Body(...), p: Principal = Depends(auth)):
        """Save the values of ONE feature: validated, written to its geodatabase row by OBJECTID and read back."""
        return jobs.assign_save(owned(job_id, p), fid, body.get("fields") or {}, body.get("status") or "completed")

    @r.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
    def log(job_id: str, p: Principal = Depends(auth)):
        return jobs.log_text(owned(job_id, p))

    @r.get("/jobs/{job_id}/download/{kind}")
    def download(job_id: str, kind: str, p: Principal = Depends(auth)):
        owned(job_id, p)
        media = {"gdb_zip": "application/zip", "pdf": "application/pdf", "qc_csv": "text/csv",
                 "boundary_zip": "application/zip", "package": "application/zip", "metadata": "application/xml",
                 "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        if re.fullmatch(r"gdb_\d{1,2}", kind):        # one geodatabase per template (multi-template delivery)
            media[kind] = "application/zip"
        if kind not in media:
            raise HTTPException(404, "unknown output")
        path = jobs.output_path(job_id, kind)
        return FileResponse(path, media_type=media[kind], filename=os.path.basename(path))

    app.include_router(r)
    app.include_router(accounts_router(s, acc, jobs, auth))

    # ---------------------------------------------------------------- web UI
    if os.path.isdir(STATIC_DIR):
        app.mount(f"{s.base_path}/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

        @app.get(f"{s.base_path}/" if s.base_path else "/", include_in_schema=False)
        @app.get(f"{s.base_path}/{{path:path}}", include_in_schema=False)
        def spa(path: str = ""):
            if path.startswith("api/"):
                raise HTTPException(404)
            f = os.path.join(STATIC_DIR, path)
            if path and os.path.isfile(f) and os.path.realpath(f).startswith(os.path.realpath(STATIC_DIR)):
                return FileResponse(f)
            return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers={"Cache-Control": "no-cache"})
    return app
