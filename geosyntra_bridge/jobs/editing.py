"""Map editing: per-feature attributes (QA / QC) and geometry (Topology)."""
from __future__ import annotations

from typing import Any, Dict


from .common import JobError


class EditingOps:
    """Map editing: per-feature attributes (QA / QC) and geometry (Topology)."""

    # ---- map-based feature assignment (jobs/assign.py)
    def _assign(self, fn, *a):
        from . import assign as A
        try:
            return getattr(A, fn)(self, *a)
        except A.AssignError as e:
            raise JobError(str(e), e.status) from e

    def _topo(self, fn, *a):
        from . import topology_edit as T
        from .assign import AssignError
        try:
            return getattr(T, fn)(self, *a)
        except AssignError as e:
            raise JobError(str(e), e.status) from e

    def topology_errors(self, job_id: str) -> Dict[str, Any]:
        return self._topo("errors", job_id)

    def topology_exception(self, job_id: str, key: str, on: bool) -> Dict[str, Any]:
        return self._topo("set_exception", job_id, key, on)

    def save_geometry(self, job_id: str, edits) -> Dict[str, Any]:
        r = self._topo("save_geometry", job_id, edits)
        self.log(job_id, f"topology editing: {r['geometry']} geometry edit(s), {r['deleted']} deletion(s) saved")
        self._update(job_id, stage="mapping", status="ready", message="Geometry edited — validate again")
        return r

    def merge_features(self, job_id: str, fids) -> Dict[str, Any]:
        r = self._topo("merge", job_id, list(fids))
        self.log(job_id, f"{r['merged']} features merged into #{r['kept']} on the map")
        self._update(job_id, stage="mapping", status="ready", message="Features merged — validate again")
        return r

    def explode_feature(self, job_id: str, fid: int) -> Dict[str, Any]:
        r = self._topo("explode", job_id, fid)
        self.log(job_id, f"feature #{fid} exploded into {r['parts']} features")
        self._update(job_id, stage="mapping", status="ready", message="Feature exploded — validate again")
        return r

    def split_feature(self, job_id: str, fid: int, cutter) -> Dict[str, Any]:
        from . import topology_edit as T
        from .assign import AssignError
        try:
            r = T.split(self, job_id, fid, cutter)
        except AssignError as e:
            raise JobError(str(e), e.status) from e
        self.log(job_id, f"feature #{fid} split into {r['parts']} on the map")
        self._update(job_id, stage="mapping", status="ready", message="Feature split — validate again")
        return r

    def autofix_preview(self, job_id: str, key: str) -> Dict[str, Any]:
        from . import topology_fix as F
        from .assign import AssignError
        try:
            return F.preview(self, job_id, key)
        except AssignError as e:
            raise JobError(str(e), e.status) from e

    def autofix_report(self, job_id: str) -> Dict[str, Any]:
        return self._read(job_id, "autofix_report.json") or {}

    def autofix_undo(self, job_id: str) -> Dict[str, Any]:
        from . import topology_fix as F
        from .assign import AssignError
        try:
            r = F.undo_last(self, job_id)
        except AssignError as e:
            raise JobError(str(e), e.status) from e
        self.log(job_id, f"auto-fix run of {r['undone']} undone")
        return self.start_validation(job_id)

    def topology_autofix(self, job_id: str, keys, bulk: bool = False) -> Dict[str, Any]:
        from . import topology_fix as F
        from .assign import AssignError
        try:
            r = F.autofix(self, job_id, list(keys), bulk)
        except AssignError as e:
            raise JobError(str(e), e.status) from e
        if r["fixed"]:
            self.log(job_id, f"topology: {len(r['fixed'])} finding(s) corrected automatically, {len(r['skipped'])} left for the map")
            self._update(job_id, stage="mapping", status="ready", message="Geometry corrected — validate again")
        return r

    def restore_geometry(self, job_id: str, fid: int) -> Dict[str, Any]:
        return self._topo("restore", job_id, fid)

    def assign_overview(self, job_id: str) -> Dict[str, Any]:
        return self._assign("overview", job_id)

    def assign_detail(self, job_id: str, fid: int) -> Dict[str, Any]:
        return self._assign("detail", job_id, fid)

    def assign_save_many(self, job_id: str, fids, fields: Dict[str, Any]) -> Dict[str, Any]:
        r = self._assign("save_many", job_id, list(fids), fields)
        self.log(job_id, f"map edit: {', '.join(fields)} applied to {r['saved']} feature(s)"
                 + (f", {len(r['failed'])} refused" if r["failed"] else ""))
        return r

    def assign_values(self, job_id: str, target: str, field: str) -> Dict[str, Any]:
        return self._assign("unique_values", job_id, target, field)

    def assign_save(self, job_id: str, fid: int, fields: Dict[str, Any], status: str) -> Dict[str, Any]:
        r = self._assign("save", job_id, fid, fields, status)
        self.log(job_id, f"map edit: {r['target']} #{fid}" + (f" OBJECTID {r['objectid']} written to {r['gdb']['gdb']}"
                                                            if r.get("gdb") else " (written on the next Generate)")
                 + f" -> {r['state']}")
        return r
