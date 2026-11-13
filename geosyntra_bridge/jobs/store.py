"""Job folders, state, logs, locks, listing and expiry."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from ..cad.model import CadDocument
from ..config import Settings
from ..engine.conversion import ConversionResult
from ..mapping.profiles import ProfileStore
from ..schema.loader import SchemaRegistry

from .common import JobError, _now, _rmtree


class JobStore:
    """Job folders, state, logs, locks, listing and expiry."""

    def __init__(self, settings: Settings, registry: SchemaRegistry):
        self.s = settings
        self.reg = registry
        self.profiles = ProfileStore(settings.rules_dir)
        os.makedirs(settings.jobs_dir, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=max(1, settings.workers), thread_name_prefix="gsb-job")
        self._locks: Dict[str, threading.Lock] = {}
        self._docs: Dict[str, CadDocument] = {}
        self._results: Dict[str, ConversionResult] = {}
        self._glock = threading.Lock()
        # called after every generation with (job_id, succeeded) — billing refunds a failed generation
        self.on_generate_end: Optional[Callable[[str, bool], None]] = None
        self._stop = threading.Event()
        threading.Thread(target=self._cleanup_loop, daemon=True, name="gsb-cleanup").start()

    # ------------------------------------------------------------ paths / state
    def jdir(self, job_id: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id or ""):
            raise JobError("invalid job id", 404)
        d = os.path.join(self.s.jobs_dir, job_id)
        if not os.path.isdir(d):
            raise JobError("job not found", 404)
        return d

    def _lock(self, job_id: str) -> threading.Lock:
        with self._glock:
            return self._locks.setdefault(job_id, threading.Lock())

    def _read(self, job_id: str, name: str, default=None):
        p = os.path.join(self.jdir(job_id), name)
        if not os.path.isfile(p):
            return default
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def _write(self, job_id: str, name: str, data):
        p = os.path.join(self.jdir(job_id), name)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
        os.replace(tmp, p)

    def log(self, job_id: str, msg: str):
        try:
            with open(os.path.join(self.jdir(job_id), "job.log"), "a", encoding="utf-8") as f:
                f.write(f"{_now()}  {msg}\n")
        except (OSError, JobError):
            pass

    def state(self, job_id: str) -> Dict[str, Any]:
        st = self._read(job_id, "state.json")
        if st is None:
            raise JobError("job not found", 404)
        return st

    def _update(self, job_id: str, **kw) -> Dict[str, Any]:
        with self._lock(job_id):
            st = self.state(job_id)
            st.update(kw)
            st["updated_at"] = _now()
            self._write(job_id, "state.json", st)
            return st

    def check_owner(self, job_id: str, user: str, admin: bool = False):
        st = self.state(job_id)
        if not admin and st.get("owner") not in (user, None) and self.s.auth_mode != "none":
            raise JobError("job not found", 404)
        return st

    # ------------------------------------------------------------ runners
    def _progress(self, job_id: str, stage: str, lo: float, hi: float) -> Callable[[float, str], None]:
        last = [0.0]

        def cb(frac: float, msg: str):
            now = time.time()
            if now - last[0] < 0.4 and frac < 1.0:
                return
            last[0] = now
            self._update(job_id, stage=stage, status="running", progress=round(lo + (hi - lo) * frac, 3), message=msg)
        return cb

    def _fail(self, job_id: str, stage: str, e: BaseException):
        msg = str(e) or e.__class__.__name__
        self.log(job_id, f"{stage} FAILED: {msg}\n{traceback.format_exc()}")
        self._update(job_id, stage=stage, status="failed", message=msg, error={"stage": stage, "message": msg,
                                                                              "type": e.__class__.__name__})

    # ------------------------------------------------------------ public actions
    def _require(self, job_id: str, allowed: List[str]):
        st = self.state(job_id)
        if st["status"] == "running" or st["status"] == "queued":
            raise JobError(f"job is busy ({st['stage']})", 409)
        if st["stage"] not in allowed and st["status"] not in ("ready", "validated", "completed", "failed"):
            raise JobError(f"action not allowed at stage {st['stage']}", 409)
        if st["status"] == "failed" and st.get("error", {}).get("stage") == "discovery":
            raise JobError("drawing could not be read — upload a new file", 409)
        return st

    def get_mapping(self, job_id: str) -> Dict[str, Any]:
        m = self._read(job_id, "mapping.json")
        if m is None:
            raise JobError("mapping not available yet", 409)
        return m

    def output_path(self, job_id: str, kind: str) -> str:
        st = self.state(job_id)
        name = (st.get("outputs") or {}).get(kind)
        if not name:
            raise JobError("output not available", 404)
        p = os.path.join(self.jdir(job_id), "output", name)
        if not os.path.isfile(p):
            raise JobError("output file missing", 404)
        return p

    def log_text(self, job_id: str) -> str:
        p = os.path.join(self.jdir(job_id), "job.log")
        if not os.path.isfile(p):
            return ""
        with open(p, encoding="utf-8") as f:
            return f.read()[-200_000:]

    def list_jobs(self, user: str, admin: bool, limit: int = 30) -> List[Dict[str, Any]]:
        out = []
        for jid in os.listdir(self.s.jobs_dir):
            try:
                st = self.state(jid)
            except (JobError, ValueError, OSError):
                continue
            if self.s.auth_mode == "none" or admin or st.get("owner") == user:
                out.append({k: st.get(k) for k in ("job_id", "created_at", "updated_at", "schema_id", "schema_name",
                                                   "file", "extra_files", "stage", "status", "message")})
        out.sort(key=lambda s: s.get("created_at") or "", reverse=True)
        return out[:limit]

    def delete(self, job_id: str):
        d = self.jdir(job_id)
        st = self.state(job_id)
        if st["status"] in ("running", "queued"):
            raise JobError("job is busy", 409)
        self._docs.pop(job_id, None)
        self._results.pop(job_id, None)
        _rmtree(d)

    # ------------------------------------------------------------ cleanup
    def cleanup(self) -> int:
        ttl = self.s.job_ttl_hours * 3600
        n = 0
        now = time.time()
        for jid in os.listdir(self.s.jobs_dir):
            d = os.path.join(self.s.jobs_dir, jid)
            try:
                st_path = os.path.join(d, "state.json")
                mtime = os.path.getmtime(st_path) if os.path.isfile(st_path) else os.path.getmtime(d)
                if now - mtime > ttl:
                    st = {}
                    if os.path.isfile(st_path):
                        with open(st_path, encoding="utf-8") as f:
                            st = json.load(f)
                    if st.get("status") in ("running", "queued") and now - mtime < ttl * 2:
                        continue
                    _rmtree(d)
                    self._docs.pop(jid, None)
                    self._results.pop(jid, None)
                    n += 1
                else:
                    # work files (DWG conversions) are only needed while the job is active
                    for t in os.listdir(os.path.join(d, "work")) if os.path.isdir(os.path.join(d, "work")) else []:
                        if t.startswith("gsb_dwg_in_"):
                            _rmtree(os.path.join(d, "work", t))
            except (OSError, ValueError):
                continue
        return n

    def _cleanup_loop(self):
        while not self._stop.wait(1800):
            try:
                self.cleanup()
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self):
        self._stop.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
