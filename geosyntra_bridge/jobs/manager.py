"""
Asynchronous conversion jobs.

Each job lives in  data/jobs/<uuid>/
    input/<file>            uploaded CAD (read-only copy, never modified)
    work/                   DWG->DXF conversion, parsed-drawing cache
    output/                 <name>.gdb, <name>.gdb.zip, report.xlsx, report.pdf
    state.json  mapping.json  discovery.json  results.json  preview.geojson  job.log

Stages run on a thread pool; progress/state are persisted after every step so
the UI (and a restarted server) can always read the real state. Failures are
recorded with full tracebacks in job.log — there is no simulated success.

The JobManager is assembled from focused parts:
    store.py          folders, state, logs, locks, listing, expiry
    drawing.py        upload, reading (DWG / DXF / DGN / ZIP), clean-up, discovery
    mapping_ops.py    layer & field mapping, project memory, required values, reference data
    elevation_ops.py  DEM preparation
    pipeline.py       validation and generation runs, results
    editing.py        QA / QC attribute editing and Topology geometry editing
"""
from __future__ import annotations

from .common import JobError  # noqa: F401 — re-exported for the API
from .drawing import DrawingOps
from .editing import EditingOps
from .elevation_ops import ElevationOps
from .mapping_ops import MappingOps
from .pipeline import PipelineOps
from .store import JobStore


class JobManager(JobStore, DrawingOps, MappingOps, ElevationOps, PipelineOps, EditingOps):
    """Asynchronous conversion jobs (see the module docstring for the parts)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.recover_interrupted()

    def recover_interrupted(self) -> int:
        """A job marked queued/running when the server starts lost its worker with the old process (a restart or an
        update). Reading the drawing is resumed on its own; a check or a generation is marked interrupted so the user
        runs it again with one press instead of watching 'Working' forever."""
        import os
        n = 0
        for jid in os.listdir(self.s.jobs_dir):
            try:
                st = self.state(jid)
            except Exception:
                continue
            if st.get("status") not in ("queued", "running"):
                continue
            n += 1
            stage = st.get("stage") or "upload"
            if stage in ("upload", "discovery"):
                self._update(jid, status="queued", stage="upload", progress=0.0, message="Resuming after a restart")
                self.log(jid, "server restarted during reading: resumed")
                self.pool.submit(self._run_discovery, jid)
            else:
                msg = "Interrupted by a server restart — run this step again"
                self._update(jid, status="failed", message=msg, error={"stage": stage, "message": msg, "type": "Interrupted"})
                self.log(jid, f"server restarted during {stage}: marked interrupted")
        return n
