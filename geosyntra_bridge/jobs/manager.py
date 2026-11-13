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
