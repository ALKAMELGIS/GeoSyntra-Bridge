"""Upload, reading the drawing(s), clean-up, discovery and drawing previews."""
from __future__ import annotations

import os
import pickle
import shutil
import time
import uuid
from typing import Any, Dict, List, Optional

from ..cad.discovery import discover
from ..cad.model import CadDocument
from ..cad.merge import merge_docs
from ..cad.reader import CadReadError, read_cad, sniff_cad_file
from ..engine.conversion import build_initial_mapping

from .common import ALLOWED_EXT, DRAWING_EXT, JobError, _now, safe_filename, _unzip_drawings


class DrawingOps:
    """Upload, reading the drawing(s), clean-up, discovery and drawing previews."""

    # ------------------------------------------------------------ create
    def create(self, user: str, schema_id: str, filename: str, stream, size_hint: Optional[int] = None) -> Dict[str, Any]:
        try:
            schema = self.reg.get(schema_id)
        except KeyError:
            raise JobError(f"unknown schema '{schema_id}'")
        name = safe_filename(filename)
        if not name.lower().endswith(ALLOWED_EXT):
            raise JobError("accepted: .dwg, .dxf, .dgn (MicroStation V7) or a .zip with the drawing and its xrefs")
        limit = self.s.max_upload_mb * 1024 * 1024
        job_id = uuid.uuid4().hex
        d = os.path.join(self.s.jobs_dir, job_id)
        for sub in ("input", "work", "output"):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
        path = os.path.join(d, "input", name)
        written = 0
        try:
            with open(path, "wb") as out:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > limit:
                        raise JobError(f"file exceeds the {self.s.max_upload_mb} MB upload limit", 413)
                    out.write(chunk)
            if written == 0:
                raise JobError("uploaded file is empty")
            extra: List[Dict[str, Any]] = []
            if name.lower().endswith(".zip"):
                # a package: the drawing with its xrefs / other sheets — every drawing in it is read together
                drawings = _unzip_drawings(path, os.path.join(d, "input"), limit)
                os.remove(path)
                main = drawings[0]
                extra = [{"name": x["name"], "size": x["size"], "format": x["format"]} for x in drawings[1:]]
                name, path, written = main["name"], os.path.join(d, "input", main["name"]), main["size"]
            try:
                kind = sniff_cad_file(path)
            except CadReadError as e:
                raise JobError(f"rejected: {e}")
            ext = os.path.splitext(name.lower())[1]
            if {"dwg": ".dwg", "dgn": ".dgn"}.get(kind, ".dxf") != ext:
                raise JobError(f"file content ({kind.upper()}) does not match its extension")
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise
        for f_ in [path] + [os.path.join(d, "input", x["name"]) for x in extra]:
            os.chmod(f_, 0o444)  # original drawings are read-only for the rest of the job
        st = {"job_id": job_id, "owner": user, "created_at": _now(), "updated_at": _now(),
              "schema_id": schema.schema_id, "schema_name": schema.display_name, "schema_version": schema.version,
              "file": {"name": name, "size": written, "format": kind}, **({"extra_files": extra} if extra else {}),
              "stage": "upload", "status": "queued", "progress": 0.0, "message": "Queued for discovery",
              "error": None, "outputs": {}}
        self._write(job_id, "state.json", st)
        self.log(job_id, f"created by {user}: {name} ({written:,} bytes, {kind}) schema={schema.schema_id}")
        self.pool.submit(self._run_discovery, job_id)
        return st

    def _input_path(self, job_id: str) -> str:
        st = self.state(job_id)
        return os.path.join(self.jdir(job_id), "input", st["file"]["name"])

    def _read_drawings(self, job_id: str, progress=None) -> CadDocument:
        """The job's drawing — or several sheets of one project (Softscape, Hardscape, Irrigation …) merged into one
        document: every layer keeps its name, handles are prefixed per sheet so they stay unique."""
        st = self.state(job_id)
        files = [st["file"]["name"]] + [f["name"] for f in st.get("extra_files") or []]
        work = os.path.join(self.jdir(job_id), "work")
        explode = bool(self.cleanup_state(job_id)["settings"].get("explode_blocks"))
        docs = []
        for i, name in enumerate(files):
            cb = None
            if progress:
                cb = (lambda f, m, i=i: progress((i + f) / len(files), f"{name}: {m}" if len(files) > 1 else m))
            sub = work if i == 0 else os.path.join(work, f"sheet{i}")
            os.makedirs(sub, exist_ok=True)
            docs.append(read_cad(os.path.join(self.jdir(job_id), "input", name), sub, progress=cb, explode_blocks=explode))
        doc = merge_docs(docs, files)
        self._advanced_cleanup(job_id, doc)
        return doc

    # ── advanced clean-up: proposals reviewed by the user, applied on every read ──────────────────────────────
    def cleanup_state(self, job_id: str) -> Dict[str, Any]:
        cs = self._read(job_id, "cleanup.json") or {}
        cs.setdefault("settings", {}); cs.setdefault("decisions", {})
        return cs

    def _advanced_cleanup(self, job_id: str, doc: CadDocument):
        from ..cad import cleanup as CU
        cs = self.cleanup_state(job_id)
        try:
            props = CU.analyse(doc, cs["settings"])
        except Exception as ex:  # noqa: BLE001 - never block reading the drawing
            doc.read_warnings.append(f"advanced clean-up analysis skipped: {ex}")
            return
        approved = {k for k, v in cs["decisions"].items() if v == "approved"}
        if approved:
            if CU.apply(doc, props, approved):
                from ..cad.styles import assign_groups
                assign_groups(doc)
        doc.cleanup_proposals = props

    def cad_cleanup(self, job_id: str) -> Dict[str, Any]:
        from ..cad import cleanup as CU
        doc = self.load_doc(job_id)
        cs = self.cleanup_state(job_id)
        props = doc.cleanup_proposals or []
        try:
            ll = self._to_ll(job_id)
        except Exception:  # noqa: BLE001
            ll = None
        for p in props:
            p["decision"] = cs["decisions"].get(p["id"], "pending")
            if ll:   # lon/lat copies for the satellite preview
                try:
                    for g in p.get("geom") or []:
                        g["ll"] = ll(g["c"])
                    if p.get("to"):
                        p["to_ll"] = ll([p["to"]])[0]
                except Exception:  # noqa: BLE001
                    pass
        return {"settings": {**CU.DEFAULTS, **cs["settings"]}, "auto": [c for c in doc.cleaning if c.get("mode", "auto") == "auto"],
                "reviewed": [c for c in doc.cleaning if c.get("mode") == "reviewed"],
                "summary": CU.summary(props, cs["decisions"]), "proposals": props}

    def set_cleanup(self, job_id: str, decisions: Dict[str, str], settings: Optional[Dict[str, Any]] = None,
                    reread: bool = True) -> Dict[str, Any]:
        """Record approve / reject decisions (and tolerances); re-reading applies them to the drawing."""
        self._require(job_id, ["mapping", "validation", "done"])
        cs = self.cleanup_state(job_id)
        for k, v in (decisions or {}).items():
            if v in ("approved", "rejected", "pending"):
                cs["decisions"][k] = v
        if settings:
            if "explode_blocks" in settings:
                cs["settings"]["explode_blocks"] = bool(settings["explode_blocks"])
            for k in ("review_tolerance", "gap_tolerance"):
                if k in settings and float(settings[k]) > 0:
                    cs["settings"][k] = min(float(settings[k]), 5.0)
        self._write(job_id, "cleanup.json", cs)
        n = sum(1 for v in cs["decisions"].values() if v == "approved")
        self.log(job_id, f"clean-up review saved: {n} change(s) approved")
        if not reread:
            return self.state(job_id)
        for f in ("results.json", "preview.geojson"):
            try:
                os.remove(os.path.join(self.jdir(job_id), f))
            except OSError:
                pass
        try:
            os.remove(os.path.join(self.jdir(job_id), "work", "drawing.pkl"))
        except OSError:
            pass
        self._docs.pop(job_id, None)
        st = self._update(job_id, stage="upload", status="queued", progress=0.0, message="Applying the clean-up", outputs={})
        self.pool.submit(self._run_discovery, job_id)
        return st

    def add_drawing(self, job_id: str, filename: str, stream) -> Dict[str, Any]:
        """Add another sheet of the same project to the job; the drawings are read again together."""
        st = self._require(job_id, ["mapping", "validation", "done"])
        name = safe_filename(filename)
        if not name.lower().endswith(DRAWING_EXT):
            raise JobError("add a .dwg, .dxf or .dgn drawing")
        existing = {st["file"]["name"]} | {f["name"] for f in st.get("extra_files") or []}
        if name in existing:
            raise JobError(f"{name} is already part of this conversion")
        path = os.path.join(self.jdir(job_id), "input", name)
        limit, written = self.s.max_upload_mb * 1024 * 1024, 0
        try:
            with open(path, "wb") as out:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > limit:
                        raise JobError(f"file exceeds the {self.s.max_upload_mb} MB upload limit", 413)
                    out.write(chunk)
            if written == 0:
                raise JobError("uploaded file is empty")
            try:
                kind = sniff_cad_file(path)
            except CadReadError as e:
                raise JobError(f"rejected: {e}")
        except Exception:
            try:
                os.remove(path)
            except OSError:
                pass
            raise
        os.chmod(path, 0o444)
        extra = list(st.get("extra_files") or []) + [{"name": name, "size": written, "format": kind}]
        for f in ("results.json", "preview.geojson"):
            try:
                os.remove(os.path.join(self.jdir(job_id), f))
            except OSError:
                pass
        try:
            os.remove(os.path.join(self.jdir(job_id), "work", "drawing.pkl"))
        except OSError:
            pass
        self._docs.pop(job_id, None)
        self.log(job_id, f"drawing added: {name} ({written:,} bytes, {kind})")
        st = self._update(job_id, extra_files=extra, stage="upload", status="queued", progress=0.0,
                          message=f"Reading {1 + len(extra)} drawings", outputs={})
        self.pool.submit(self._run_discovery, job_id)
        return st

    def load_doc(self, job_id: str) -> CadDocument:
        doc = self._docs.get(job_id)
        if doc is not None:
            return doc
        cache = os.path.join(self.jdir(job_id), "work", "drawing.pkl")
        if os.path.isfile(cache):
            with open(cache, "rb") as f:
                doc = pickle.load(f)  # written by this server only (job work dir)
            # a drawing read by an earlier version lacks the fields added since: give them their defaults
            import dataclasses
            for fd in dataclasses.fields(doc):
                if fd.name not in doc.__dict__:
                    setattr(doc, fd.name, fd.default_factory() if fd.default_factory is not dataclasses.MISSING
                            else (None if fd.default is dataclasses.MISSING else fd.default))
        else:
            doc = self._read_drawings(job_id)
        self._remember_doc(job_id, doc)
        return doc

    def _remember_doc(self, job_id: str, doc: CadDocument):
        with self._glock:
            self._docs[job_id] = doc
            while len(self._docs) > 4:
                self._docs.pop(next(iter(self._docs)))

    def _run_discovery(self, job_id: str):
        stage = "discovery"
        try:
            self._update(job_id, stage=stage, status="running", progress=0.01, message="Reading drawing")
            cb = self._progress(job_id, stage, 0.0, 0.85)
            t0 = time.time()
            prev = self._read(job_id, "mapping.json")       # decisions kept when a sheet is added
            doc = self._read_drawings(job_id, progress=cb)
            sch0 = self.reg.get(self.state(job_id)["schema_id"])
            tpl_ids = set((getattr(sch0, "parts", None) or {}).keys()) or {sch0.schema_id}
            if any(t.startswith("adm_rd") for t in tpl_ids):
                from ..cad.roles import classify_layer_roles
                classify_layer_roles(doc, road_is_asset=True)   # Roads template delivered: road layers are assets
            with open(os.path.join(self.jdir(job_id), "work", "drawing.pkl"), "wb") as f:
                pickle.dump(doc, f, protocol=pickle.HIGHEST_PROTOCOL)
            self._remember_doc(job_id, doc)
            self.log(job_id, f"read {len(doc.entities):,} entities in {time.time() - t0:.1f}s "
                             f"(skipped types: {doc.skipped_types or 'none'})")
            for w in doc.read_warnings[:20]:
                self.log(job_id, f"reader warning: {w}")
            disc = discover(doc)
            self._write(job_id, "discovery.json", disc)
            cb(0.92, "Matching CAD layers to the schema")
            schema = self.reg.get(self.state(job_id)["schema_id"])
            mapping = build_initial_mapping(doc, schema, self.s.rules_dir, disc)
            if prev:
                for k, v in (prev.get("layers") or {}).items():
                    if k in mapping["layers"] and v.get("status") == "USER":
                        mapping["layers"][k].update({x: v[x] for x in ("target", "role", "status", "confidence", "subtype") if x in v})
                for key in ("fields", "value_maps", "project", "elevation", "dem", "source_crs", "unit_scale", "georef", "transform_op"):
                    if prev.get(key):
                        mapping[key] = prev[key] if key != "fields" else {**mapping.get("fields", {}), **{
                            fc: {**mapping.get("fields", {}).get(fc, {}), **{f: sp for f, sp in fs.items() if sp.get("user")}}
                            for fc, fs in prev["fields"].items()}}
            else:                        # a saved projection for drawings in this place (same raw coordinates)
                try:
                    from ..geometry import presets as PR
                    sug = mapping.get("crs_suggestion") or {}
                    pre = PR.match_preset(self.s.data_dir, self.state(job_id).get("owner") or "_", sug.get("extent"),
                                          list(doc.layers))
                    if pre and (sug.get("confidence") != "high" or pre.get("georef")):
                        PR.apply_to(mapping, pre)
                        sug["preset"] = {"id": pre["id"], "name": pre["name"]}
                        sug["reasons"] = [f"saved setting “{pre['name']}” applied — an earlier drawing in the same "
                                          f"coordinates used it"] + list(sug.get("reasons") or [])
                        self.log(job_id, f"projection preset applied: {pre['name']}")
                except Exception as ex:  # noqa: BLE001
                    self.log(job_id, f"projection preset skipped: {ex}")
            disc["schema_ranking"] = self._rank_schemas(doc)
            try:
                from ..cad import cleanup as CU
                dec = self.cleanup_state(job_id)["decisions"]
                disc["cleanup"] = {"pending": sum(1 for p_ in doc.cleanup_proposals if p_["id"] not in dec),
                                   "summary": CU.summary(doc.cleanup_proposals, dec)}
            except Exception:  # noqa: BLE001
                pass
            self._write(job_id, "discovery.json", disc)
            self._write(job_id, "mapping.json", mapping)
            auto = sum(1 for m in mapping["layers"].values() if m["status"] == "AUTO")
            self.log(job_id, f"discovery: {disc['layer_count']} layers, {len(disc['groups'])} groups, {auto} auto-mapped")
            self._update(job_id, stage="mapping", status="ready", progress=1.0,
                         message=f"{disc['layer_count']} CAD layers discovered, {auto}/{len(mapping['layers'])} groups auto-mapped")
        except Exception as e:  # noqa: BLE001
            self._fail(job_id, stage, e)

    def _attach_key(self, job_id: str, bundle: Dict[str, Any]):
        try:   # which original template geodatabase each feature class belongs to (mapping & delivery report)
            sc = self.reg.get(self.state(job_id)["schema_id"])
            bundle["fc_template"] = {ly.name: (sc.parts[ly.template_id].source_template if ly.template_id and ly.template_id in sc.parts
                                               else sc.source_template or sc.display_name) for ly in sc.layers}
            bundle["fc_geometry"] = {ly.name: ly.geometry_type for ly in sc.layers}
        except Exception:  # noqa: BLE001
            pass
        disc = self._read(job_id, "discovery.json", {}) or {}
        m = self._read(job_id, "mapping.json", {}) or {}
        bundle["key_info"] = disc.get("key_info") or {}
        bundle["keys"] = m.get("keys") or []
        bundle["artifacts"] = disc.get("artifacts") or {}
        bundle["cleaning"] = disc.get("cleaning") or []
        bundle["layer_roles"] = disc.get("layer_roles") or {}
        if m.get("reference"):
            bundle["reference"] = m["reference"]

    def _rank_schemas(self, doc) -> List[Dict[str, Any]]:
        """How well each schema explains this drawing (share of feature entities mapped, weighted by confidence)."""
        from ..cad.keys import read_keys
        from ..mapping.automap import auto_map
        keys = read_keys(doc)
        counts = {}
        for e in doc.features:
            counts[e.group] = counts.get(e.group, 0) + 1
        out = []
        for sid, sc in self.reg.all().items():
            try:
                lm = auto_map(doc, sc, None, keys=keys)
            except Exception:  # noqa: BLE001
                continue
            excluded = sum(counts.get(k, 0) for k, v in lm.items() if v.get("status") == "EXCLUDED")
            covered = sum(counts.get(k, 0) * v["score"] for k, v in lm.items() if v.get("target"))
            total = max(1, len(doc.features) - excluded)
            item = {"schema_id": sid, "display_name": sc.display_name, "coverage": round(covered / total, 3),
                    "mapped_groups": sum(1 for v in lm.values() if v.get("target")), "groups": len(lm)}
            if getattr(sc, "parts", None):     # which templates the drawing's layers fall into
                spans: Dict[str, int] = {}
                for v in lm.values():
                    if v.get("target") and v.get("score", 0) >= 0.6:
                        ly = sc.get_layer(v["target"])
                        if ly is not None and ly.template_id:
                            spans[ly.template_id] = spans.get(ly.template_id, 0) + 1
                item["templates"] = spans
            out.append(item)
        return sorted(out, key=lambda x: -x["coverage"])

    def detect_crs(self, job_id: str) -> Dict[str, Any]:
        """Re-run the automatic source-CRS / unit detection (also stored on the mapping as crs_suggestion)."""
        from ..engine.conversion import detect_for_doc
        doc = self.load_doc(job_id)
        schema = self.reg.get(self.state(job_id)["schema_id"])
        r = detect_for_doc(doc, schema, self._read(job_id, "discovery.json") or {})
        try:
            m = self.get_mapping(job_id)
            m["crs_suggestion"] = r
            self._write(job_id, "mapping.json", m)
        except Exception:  # noqa: BLE001
            pass
        return r

    def georef_from_labels(self, job_id: str) -> Dict[str, Any]:
        """Control points from coordinate labels written in the drawing (E=… N=…): each label is tied to the drawing
        vertex it annotates, then shift / rotation / scale are fitted. Says so when the drawing is already in real
        coordinates."""
        import re
        import numpy as np
        from ..geometry.crs import fit_similarity
        from ..cad.reader import unit_scale_to_meters
        doc = self.load_doc(job_id)
        lab = re.compile(r"\b(?:E|EAST(?:ING)?|X)\s*[=:]?\s*(\d{5,7}(?:\.\d+)?)[\s,;]+(?:N|NORTH(?:ING)?|Y)\s*[=:]?\s*(\d{6,8}(?:\.\d+)?)", re.I)
        raw = []
        for e in doc.entities:
            if e.text:
                mm = lab.search(e.text.replace(",", ""))
                p = e.center or (e.coords[0] if e.coords else None)
                if mm and p:
                    raw.append((p[0], p[1], float(mm.group(1)), float(mm.group(2)), e.text.strip()[:80]))
        if len(raw) < 1:
            return {"points": [], "found": 0, "message": "no coordinate labels (E=… N=…) found in the drawing"}
        import math
        k = unit_scale_to_meters(doc.insunits) or 1.0
        T = np.array([(r[0], r[1]) for r in raw]); W = np.array([(r[2], r[3]) for r in raw])
        reach = 60.0 / k                 # a label sits near the point it describes
        rows = []                        # vertex x, y, anchor (a centre or an end point is what labels describe)
        for e in doc.entities:
            if e.kind == "text":
                continue
            cs = (e.coords or [])[:400]
            for i, c in enumerate(cs):
                rows.append((c[0], c[1], 1.0 if e.kind != "polygon" and i in (0, len(cs) - 1) else 0.0))
            if e.center:
                rows.append((e.center[0], e.center[1], 1.0))
        allv = np.array(rows, dtype=float).reshape(-1, 3)
        if len(allv):
            near = np.zeros(len(allv), bool)
            for tx, ty in T:
                near |= (np.abs(allv[:, 0] - tx) <= reach) & (np.abs(allv[:, 1] - ty) <= reach)
            sel = allv[near]
            sel = sel[np.lexsort((-sel[:, 2], sel[:, 1], sel[:, 0]))]
            keep = np.ones(len(sel), bool)
            keep[1:] = (np.diff(sel[:, 0]) != 0) | (np.diff(sel[:, 1]) != 0)
            V, A = sel[keep, :2], sel[keep, 2]
        else:
            V, A = np.zeros((0, 2)), np.zeros(0)

        def search(a_: float, b_: float) -> list:
            """Labels landing exactly (5 cm) on a vertex once the drawing is scaled / rotated by (a_, b_) and shifted;
            every vertex near a label proposes a shift, the one most labels agree on wins."""
            P = np.c_[a_ * V[:, 0] - b_ * V[:, 1], b_ * V[:, 0] + a_ * V[:, 1]] if len(V) else V
            Tt = np.c_[a_ * T[:, 0] - b_ * T[:, 1], b_ * T[:, 0] + a_ * T[:, 1]]
            cell = 0.05
            grid: Dict[tuple, list] = {}
            for i, (px, py) in enumerate(P):
                grid.setdefault((int(px // cell), int(py // cell)), []).append(i)

            def exact(off):
                hits = []
                for j in range(len(raw)):
                    qx, qy = W[j] - off
                    cx, cy = int(qx // cell), int(qy // cell)
                    hit = None
                    bd = 0.05
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            for i in grid.get((cx + dx, cy + dy), ()):
                                dd = math.hypot(P[i, 0] - qx, P[i, 1] - qy) - 0.001 * A[i]
                                if dd <= bd:
                                    hit, bd = i, dd
                    if hit is not None:
                        hits.append((j, hit))
                return hits

            def score(h):                # labels describe centres and end points: prefer those on a tie
                return len(h) + 0.1 * sum(A[i] for _, i in h) / max(1, len(h))
            best = exact(np.zeros(2))
            enough = max(3, 0.6 * len(raw))
            for j in range(min(len(raw), 8) if len(best) < enough else 0):
                if not len(P):
                    break
                d = np.hypot(P[:, 0] - Tt[j][0], P[:, 1] - Tt[j][1])
                for i in np.argsort(d)[:200]:
                    if d[i] > 60:
                        break
                    h = exact(W[j] - P[i])
                    if score(h) > score(best):
                        best = h
                if len(best) >= enough:
                    break
            return best

        best = search(k, 0.0)            # a plain shift (the usual case)
        rough = fit_similarity([r[:4] for r in raw], fixed_scale=None if len(raw) >= 3 else k)
        if len(best) < max(2, 0.5 * len(raw)) and len(raw) >= 3 and (abs(rough["rotation"]) > 0.01 or abs(rough["scale"] - k) > 1e-3 * k):
            # rotated or scaled: the text positions give rotation and scale (a label's offset from its point
            # only moves the shift), then the exact vertices are searched again in that frame
            rr = math.radians(rough["rotation"])
            alt = search(rough["scale"] * math.cos(rr), rough["scale"] * math.sin(rr))
            if len(alt) > len(best):
                best = alt
        if len(best) >= 2 or (len(best) == 1 and len(raw) == 1):
            pts = [{"x": float(V[i][0]), "y": float(V[i][1]), "E": raw[j][2], "N": raw[j][3], "text": raw[j][4]} for j, i in best]
            cp_ = [(p["x"], p["y"], p["E"], p["N"]) for p in pts]
            fit = fit_similarity(cp_, fixed_scale=k)          # a plain shift, unless it leaves real error
            if fit["rms"] > 0.05 and len(pts) >= 2:
                fit = fit_similarity(cp_)
        else:                            # no exact hits: tie each label to where its text sits (metres of error)
            pts = [{"x": r[0], "y": r[1], "E": r[2], "N": r[3], "text": r[4]} for r in raw]
            fit = rough
        for p, r in zip(pts, fit["residuals"]):
            p["residual"] = r
        already = abs(fit["rotation"]) < 1e-6 and abs(fit["offset"][0]) < 0.05 and abs(fit["offset"][1]) < 0.05
        # which system the labelled coordinates are in: the same detection, run on the real E / N values
        from ..geometry.crs import detect_source_crs
        es, ns = [p["E"] for p in pts], [p["N"] for p in pts]
        span = max(max(es) - min(es), max(ns) - min(ns), 1.0)
        try:
            schema = self.reg.get(self.state(job_id)["schema_id"])
            tgt = schema.spatial_reference.epsg
        except Exception:  # noqa: BLE001
            tgt = None
        crs = detect_source_crs([min(es), min(ns), min(es) + span, min(ns) + span], doc.geodata_epsg,
                                getattr(doc, "prj_epsg", None), tgt, 1.0, labels=[(e, n) for e, n in zip(es, ns)])
        return {"found": len(raw), "points": pts, "fit": fit, "already_real": already, "crs": crs,
                "message": ("the drawing is already in real coordinates — the labels match it" if already else
                            f"{len(pts)} labels tie the drawing to real coordinates (RMS {fit['rms']} m)")}

    def _to_ll(self, job_id: str):
        """(x, y) drawing coordinates -> [lon, lat] function, or None without a source CRS."""
        from ..geometry.convert import pipeline_for
        m = self.get_mapping(job_id)
        if not m.get("source_crs"):
            return None
        cp = pipeline_for(m, 4326)

        def f(pts):
            return [[round(a, 8), round(b, 8)] for a, b in cp.xy(pts)]
        return f

    def cad_preview(self, job_id: str, limit: int = 30000, epsg: Optional[int] = None,
                    scale: Optional[float] = None, local: bool = False) -> Dict[str, Any]:
        """The drawing as GeoJSON (lon/lat when a source CRS is set; `epsg` / `scale` try another one)."""
        from ..geometry.crs import validate_epsg
        from ..geometry.convert import pipeline_for
        doc = self.load_doc(job_id)
        m = dict(self.get_mapping(job_id))
        if epsg and validate_epsg(epsg):          # previewing another system: plain units, no georeferencing
            m["source_crs"] = int(epsg); m["transform_op"] = None
            if scale and scale > 0:
                m["unit_scale"] = float(scale); m["georef"] = None
        if local:                        # raw drawing coordinates (picking control points)
            m = {"unit_scale": 1.0}
        src = m.get("source_crs")
        cp = pipeline_for(m, 4326 if src else None)
        feats = []
        ents = [e for e in doc.entities if e.kind != "artifact"]
        step = max(1, len(ents) // limit) if (epsg or local) else 1     # a spread-out sample for a quick look
        for e in ents[::step][:limit]:
            if e.kind == "artifact":
                continue
            raw = e.coords if e.kind != "point" else [e.center or e.coords[0]]
            if not raw:
                continue
            pts = cp.xy(raw)
            if e.kind in ("point", "text"):
                geom = {"type": "Point", "coordinates": list(pts[0])}
            elif e.kind == "line" and len(pts) >= 2:
                geom = {"type": "LineString", "coordinates": [list(p) for p in pts]}
            elif e.kind == "polygon" and len(pts) >= 4:
                geom = {"type": "Polygon", "coordinates": [[list(p) for p in pts]]}
            else:
                continue
            feats.append({"type": "Feature", "geometry": geom,
                          "properties": {"layer": e.layer, "kind": e.kind, "handle": e.handle, "text": e.text or None}})
        return {"type": "FeatureCollection", "crs_is_geographic": bool(src), "truncated": len(doc.entities) > limit,
                "features": feats}

    def _bbox_or_none(self, job_id: str):
        try:
            return list(self.drawing_bbox_ll(job_id))
        except Exception:  # noqa: BLE001
            return None

    def _robust_extent(self, job_id: str):
        """Where the drawing's assets really are: the extent of the objects on connected layers with the stray far-off
        ones left out (a title block at 0,0 or a point placed by mistake 1,000 km away would otherwise blow the extent up)."""
        try:
            from ..cad.discovery import group_key
            doc = self.load_doc(job_id)
            m = self._read(job_id, "mapping.json") or {}
            layers = {k for k, v in (m.get("layers") or {}).items() if v.get("role") != "ignore" and v.get("target")}
            xs, ys = [], []
            for e in doc.entities:
                if layers and (e.group or group_key(e.layer, e.kind)) not in layers:
                    continue
                for c in (e.coords or ([e.center] if e.center else []))[:50]:
                    xs.append(c[0]); ys.append(c[1])
            if len(xs) < 2:
                return None
            xs.sort(); ys.sort()
            k = max(0, int(len(xs) * 0.005))           # drop 0.5 % at each end
            x0, x1, y0, y1 = xs[k], xs[-1 - k], ys[k], ys[-1 - k]
            reach = max(x1 - x0, y1 - y0, 1.0) * 2     # objects near the core still count; only the far strays go
            x0 = min(v for v in xs if v >= x0 - reach); x1 = max(v for v in xs if v <= x1 + reach)
            y0 = min(v for v in ys if v >= y0 - reach); y1 = max(v for v in ys if v <= y1 + reach)
            pad = max(x1 - x0, y1 - y0) * 0.02 or 1.0
            return [x0 - pad, y0 - pad, x1 + pad, y1 + pad]
        except Exception:  # noqa: BLE001 - fall back to the discovery extent
            return None

    # ------------------------------------------------------------ DEM elevation
    def drawing_bbox_ll(self, job_id: str):
        """Drawing extent (lon/lat) from the discovery extent, drawing units / georeferencing and the source CRS."""
        from ..geometry.convert import pipeline_for
        disc = self._read(job_id, "discovery.json") or {}
        ext = self._robust_extent(job_id) or disc.get("extent")
        if not ext:
            raise JobError("the drawing has no extent")
        m = dict(self.get_mapping(job_id))
        schema = self.reg.get(self.state(job_id)["schema_id"])
        m["source_crs"] = m.get("source_crs") or schema.spatial_reference.epsg
        if not m["source_crs"]:
            raise JobError("choose the drawing's coordinate system first (Source step)")
        ll = pipeline_for(m, 4326).xy([(ext[0], ext[1]), (ext[2], ext[1]), (ext[0], ext[3]), (ext[2], ext[3])])
        lon, lat = [p[0] for p in ll], [p[1] for p in ll]
        bb = (min(lon), min(lat), max(lon), max(lat))
        if not (-180 <= bb[0] <= 180 and -90 <= bb[1] <= 90) or bb[2] - bb[0] > 3 or bb[3] - bb[1] > 3:
            raise JobError(f"the drawing extent in lon/lat {tuple(round(v, 4) for v in bb)} is not plausible — "
                           f"check the source coordinate system and drawing units")
        return bb
