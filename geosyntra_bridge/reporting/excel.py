"""Excel processing report (openpyxl) built from a results bundle."""
from __future__ import annotations

from typing import Any, Dict, List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

NAVY = "0F2341"
GREEN = "2E9E6B"
SEV_FILL = {"error": "FDE2E1", "warning": "FFF2D6", "info": "E8F0FB"}
THIN = Side(style="thin", color="D9DEE7")


def _header(ws, row: int, cols: List[str]):
    for i, c in enumerate(cols, 1):
        cell = ws.cell(row=row, column=i, value=c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = Border(bottom=THIN)


def _table(ws, cols: List[str], rows: List[List[Any]], widths: List[int], start: int = 1, sev_col: int = -1):
    _header(ws, start, cols)
    # one style object of each kind, shared by every cell; very long tables (tens of thousands of features) are
    # written plain — per-cell styling made the report take minutes on large drawings
    styled = len(rows) <= 3000
    a_top, a_wrap, border = Alignment(vertical="top"), Alignment(vertical="top", wrap_text=True), Border(bottom=THIN)
    fills = {k: PatternFill("solid", fgColor=v) for k, v in SEV_FILL.items()}
    for r, row in enumerate(rows, start + 1):
        for c, v in enumerate(row, 1):
            if isinstance(v, (list, dict)):
                v = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
            cell = ws.cell(row=r, column=c, value=v)
            if styled:
                cell.alignment = a_wrap if isinstance(v, str) and len(v) > 40 else a_top
                cell.border = border
        if sev_col >= 0 and row[sev_col] in fills:
            ws.cell(row=r, column=sev_col + 1).fill = fills[row[sev_col]]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(row=start + 1, column=1)
    if rows:
        ws.auto_filter.ref = f"A{start}:{get_column_letter(len(cols))}{start + len(rows)}"


def write_excel_report(bundle: Dict[str, Any], path: str):
    wb = Workbook()
    s = bundle["summary"]
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "GeoSyntra Bridge — CAD to GDB Conversion Report"
    ws["A1"].font = Font(bold=True, size=15, color=NAVY)
    meta = [
        ("Job ID", bundle["job_id"]), ("Generated", bundle["generated_at"]), ("Source file", bundle["source_file"]),
        ("Target schema", f"{bundle['schema']['display_name']} ({bundle['schema']['schema_id']} v{bundle['schema']['version']})"),
        ("Source CRS", f"EPSG:{s['source_epsg']}" if s.get("source_epsg") else "assumed = target"),
        ("Target CRS", f"EPSG:{s['target_epsg']}" if s.get("target_epsg") else "undefined"),
        ("Output geodatabase", (bundle.get("gdb") or {}).get("name", "not generated (validation run)")),
        ("Drawing key source", (bundle.get("key_info") or {}).get("label", "—")),
        ("Verification", "PASSED" if (bundle.get("verification") or {}).get("passed") else
         ("FAILED" if bundle.get("verification") else "not run")),
    ]
    for i, (k, v) in enumerate(meta, 3):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws.cell(row=i, column=2, value=v)
    r = len(meta) + 4
    kpis = [("Features imported", s["features_imported"]), ("Features failed", s["features_failed"]),
            ("Duplicates skipped", s["features_skipped_duplicates"]),
            ("Features with missing attributes", s["features_with_missing_attributes"]),
            ("Duplicate records", s["duplicates"]), ("Unmatched labels", s["unmatched_labels"]),
            ("Ambiguous / conflicting labels", s["ambiguous_labels"]), ("Uncertain mappings", s["uncertain_mappings"]),
            ("Unmapped CAD entities", s["unmapped_cad_entities"]),
            ("Geometry / topology errors", s["geometry_topology_errors"]), ("Network issues", s["network_issues"]),
            ("Domain / value errors", s["domain_errors"])]
    _header(ws, r, ["Metric", "Value"])
    for i, (k, v) in enumerate(kpis, r + 1):
        ws.cell(row=i, column=1, value=k)
        ws.cell(row=i, column=2, value=v)
    r += len(kpis) + 2
    ver = bundle.get("verification") or {}
    if ver.get("checks"):
        _header(ws, r, ["Verification check", "Result", "Detail"])
        for i, c in enumerate(ver["checks"], r + 1):
            ws.cell(row=i, column=1, value=c["check"])
            cell = ws.cell(row=i, column=2, value="PASS" if c["passed"] else "FAIL")
            cell.font = Font(bold=True, color=GREEN if c["passed"] else "C0392B")
            ws.cell(row=i, column=3, value=c["detail"])
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 60
    ws.column_dimensions["C"].width = 70

    write_delivery_sheet(wb, bundle)
    ws = wb.create_sheet("Layer Mapping")
    roles = bundle.get("layer_roles") or {}
    _table(ws, ["CAD layer", "Drawing style", "Layer role", "Geometry", "Entities", "Target feature class", "Role", "Status",
                "Confidence", "Score", "Subtype"],
           [[m["layer"], m.get("style") or "", (roles.get(m["layer"]) or {}).get("role", ""), m["geometry"], m["count"], m.get("target") or "—", m.get("role"), m.get("status"),
             m.get("confidence"), m.get("score"), m.get("subtype")] for m in bundle["mapping_rows"]],
           [34, 22, 12, 10, 10, 30, 10, 13, 12, 8, 9])
    if bundle.get("cleaning"):
        ws = wb.create_sheet("CAD Clean-up")
        _table(ws, ["Action", "Entities", "Layers", "Note", "CAD handles (first 50)"],
               [[c["action"], c["count"], ", ".join(f"{l} ({n})" for l, n in c["layers"].items()), c.get("note", ""),
                 ", ".join(c.get("handles") or [])] for c in bundle["cleaning"]], [50, 10, 50, 50, 60])
    if roles:
        ws = wb.create_sheet("Layer Roles")
        _table(ws, ["CAD layer", "Role", "Reason"], [[l, r["role"], r["reason"]] for l, r in sorted(roles.items())], [40, 14, 70])
    ref = bundle.get("reference")
    if ref:
        ws = wb.create_sheet("Reference GIS")
        _table(ws, ["Reference layer", "Geometry", "Features", "Represents", "Why"],
               [[l["name"], l["geometry"], l["count"], l.get("target") or "—", l.get("how")] for l in ref["summary"]["layers"]],
               [36, 10, 10, 30, 40])

    if bundle.get("keys"):
        ws = wb.create_sheet("CAD Key")
        _table(ws, ["Code", "Description", "Source", "Linked CAD layer", "Link score"],
               [[k["code"], k["description"], k["source"], k.get("best_layer") or "—",
                 (k.get("layers") or {}).get(k.get("best_layer") or "", None)] for k in bundle["keys"]],
               [10, 50, 18, 34, 10])
    qc = bundle.get("dmt_qc")
    if qc:
        ws = wb.create_sheet("DMT QC")
        ws["A1"] = f"DMT Data Quality Control replica — template {qc.get('template')}: " + ("PASSED" if qc.get("passed") else
                                                                                         f"{len(qc.get('errors') or [])} error(s)")
        ws["A1"].font = Font(bold=True, color=GREEN if qc.get("passed") else "C0392B")
        _table(ws, ["Check", "Dataset", "FeatureClass", "Error", "ObjectID"],
               [[e["check"], e["Dataset"], e["FeatureClass"], e["Error"], e["ObjectID"]] for e in qc.get("errors") or []],
               [30, 22, 30, 110, 10], start=3)
    el = bundle.get("elevation") or {}
    if el:
        ws = wb.create_sheet("Elevation")
        dem = el.get("dem") or {}
        vert = el.get("vertical") or {}
        rows = [["Status", el.get("status")],
                ["DEM source", dem.get("label", "— (DEM elevation off)")], ["Provider", dem.get("provider")],
                ["Surface", dem.get("surface")], ["Resolution (m)", " × ".join(map(str, dem.get("resolution_m") or []))],
                ["DEM CRS", dem.get("crs")], ["DEM vertical reference", dem.get("vertical_label")],
                ["Output vertical reference", f"{vert.get('result')} — {vert.get('message')}" if vert else None],
                ["Vertical offset (m)", vert.get("offset_m")], ["Interpolation", el.get("interpolation")],
                ["Tiles", ", ".join(f"{t['tile']} ({t['status']})" for t in dem.get("tiles") or [])],
                ["DEM range in area (m)", f"{dem.get('min')} … {dem.get('max')}" if dem else None],
                ["DEM NoData in area (%)", dem.get("nodata_pct")],
                ["Features by elevation source", ", ".join(f"{k} {v}" for k, v in (el.get("by_status") or {}).items())],
                ["Vertices", ", ".join(f"{k} {v}" for k, v in (el.get("vertices") or {}).items())],
                ["Z range min / mean / max (m)", " / ".join(str((el.get("z_range") or {}).get(k)) for k in ("min", "mean", "max")) if el.get("z_range") else None],
                ["Features without elevation", f"{el.get('missing_features', 0)} (policy: {el.get('missing_policy')})"],
                ["Z checks", ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in (el.get("validation") or {}).items())],
                ["Licence", dem.get("license")]]
        _table(ws, ["Elevation", "Value"], rows, [32, 110])
        zrows = [[f["fid"], f.get("objectid"), f["target"], f["status"], (f.get("z") or {}).get("status"),
                  *[(f.get("z") or {}).get(k) for k in ("min", "mean", "max", "start", "end", "dem_mean",
                                                        "n_vertices", "n_cad", "n_dem", "n_interpolated", "n_missing")]]
                 for f in bundle["features"] if f.get("z")]
        if zrows:
            _table(ws, ["Feature #", "OBJECTID", "Target class", "Write status", "Z source", "Z min", "Z mean", "Z max",
                        "Z start", "Z end", "DEM ground mean", "Vertices", "CAD", "DEM", "Interpolated", "Missing"],
                   zrows, [10, 10, 26, 11, 16, 9, 9, 9, 9, 9, 13, 9, 7, 7, 11, 9], start=len(rows) + 3)
    ws = wb.create_sheet("Features by Class")
    _table(ws, ["Feature class", "Features written"],
           [[k, v] for k, v in s["features_by_class"].items()], [40, 18])

    ws = wb.create_sheet("Issues")
    _table(ws, ["#", "Severity", "Category", "Code", "Message", "Suggested correction", "Feature class", "Feature #",
                "OBJECTID", "Field", "CAD layer", "CAD handle", "X", "Y"],
           [[i["id"], i["severity"], i["category"], i["code"], i["message"], i["suggestion"], i.get("target_layer"),
             i.get("feature_id"), i.get("objectid"), i.get("field"), (i.get("source") or {}).get("layer"),
             (i.get("source") or {}).get("handle"),
             round(i["location"][0], 3) if i.get("location") else None,
             round(i["location"][1], 3) if i.get("location") else None] for i in bundle["issues"]],
           [6, 9, 18, 22, 70, 50, 22, 9, 9, 14, 24, 10, 13, 13], sev_col=1)

    ws = wb.create_sheet("Failed Features")
    _table(ws, ["Feature #", "Target class", "CAD layer", "CAD handle", "Entity type", "Reason"],
           [[f["fid"], f["target"], f["layer"], f["handle"], f["entity_type"], f.get("failure")]
            for f in bundle["features"] if f["status"] == "failed"], [10, 26, 30, 12, 14, 80])

    ws = wb.create_sheet("Features")
    _table(ws, ["Feature #", "OBJECTID", "Target class", "Status", "Subtype", "CAD layer", "CAD handle", "Labels", "REMARKS"],
           [[f["fid"], f.get("objectid"), f["target"], f["status"], f.get("subtype"), f["layer"], f["handle"],
             " | ".join(f.get("labels") or []), "; ".join(f.get("remarks") or [])] for f in bundle["features"]],
           [10, 10, 26, 9, 8, 30, 12, 50, 80])

    ws = wb.create_sheet("Labels")
    _table(ws, ["CAD handle", "Layer", "Text", "Extracted values", "Status", "Feature #", "Target", "Distance (m)"],
           [[l["handle"], l["layer"], l["text"], ", ".join(f"{k}={v}" for k, v in l["fields"].items()), l["status"],
             l.get("feature_id"), l.get("target"), l.get("distance")] for l in bundle["labels"]["rows"]],
           [11, 22, 50, 50, 12, 10, 24, 12])
    un = bundle["labels"].get("unrecognised_samples") or []
    if un:
        ws2 = wb.create_sheet("Unrecognised Text")
        _table(ws2, ["Text not matching any label rule"], [[t] for t in un], [90])

    net = s.get("network") or {}
    if net.get("enabled"):
        ws = wb.create_sheet("Network")
        _table(ws, ["Network metric", "Value"], [[k.replace("_", " "), v] for k, v in net.items()
                                                 if k not in ("enabled", "contractor_mandatory_missing", "not_null", "topology", "dmt_spatial_rules", "topology_standard")], [36, 16])
    ts = net.get("topology_standard") or {}
    if ts.get("rows"):
        ws = wb.create_sheet("Topology Rules")
        ws["A1"] = (f"Topology — DMT submission standard section {ts.get('section')} ({ts.get('asset')}) "
                    f"compared with the official template topology")
        ws["A1"].font = Font(bold=True)
        _table(ws, ["Geometry", "Rule", "In standard", "In template", "Template classes", "Violations", "Evaluated as", "Note"],
               [[r["geometry"], r["label"], "yes" if r["in_standard"] else "no", "yes" if r["in_template"] else "no",
                 ", ".join(r["classes"]), r.get("violations") if r.get("violations") is not None else "—",
                 "information (standard only)" if r.get("advisory") else ("template rule" if r["in_template"] else "not evaluated"),
                 r["note"]] for r in ts["rows"]],
               [10, 34, 12, 12, 60, 11, 24, 90], start=3)
    sa = bundle.get("standard_annex")
    if sa:
        write_annex_sheets(wb, sa)
    cm = s.get("contractor_mandatory_missing") or {}
    if cm:
        ws = wb.create_sheet("Contractor Mandatory")
        _table(ws, ["Feature class", "Field (mandatory for contractor — DMT data dictionary)", "Features without value",
                    "Features in class"],
               [[fc, fld, n, s["features_by_class"].get(fc)] for fc, flds in cm.items() for fld, n in flds.items()],
               [28, 50, 22, 18])
    wb.save(path)


def write_annex_sheets(wb, sa: Dict[str, Any], prefix: str = ""):
    """Annex of the DMT submission standard V7 vs the official template: classes and field differences."""
    sm = sa["summary"]
    ws = wb.create_sheet(f"{prefix}Std Annex - Classes"[:31])
    ws["A1"] = (f"{sa['section']} of the DMT As-Built Data Submission Standards V7 vs the official template: "
                f"{sm['matched']} classes matched, {sm['template_only']} only in the template, "
                f"{sm['standard_only']} only in the standard. The template is what is delivered (DMT QC compares with it).")
    ws["A1"].font = Font(bold=True)
    _table(ws, ["Template class", "Standard class", "Geometry", "Annex page", "Status", "Fields (template)",
                "Fields (standard)", "In standard only", "In template only", "Type differs", "Length differs",
                "Mandatory in standard, nullable in template"],
           [[c.get("template") or "—", c.get("annex") or "—", c.get("geometry"), c.get("page"), c["status"],
             c.get("fields_template"), c.get("fields_annex"), c.get("missing_in_template"), c.get("not_in_annex"),
             c.get("type"), c.get("length"), c.get("mandatory_nullable")] for c in sa["classes"]],
           [30, 30, 10, 10, 50, 10, 10, 10, 10, 10, 10, 14], start=3)
    ws = wb.create_sheet(f"{prefix}Std Annex - Fields"[:31])
    _table(ws, ["Template class", "Standard class", "Field", "Difference", "Standard", "Template"],
           [[f["template"], f["annex"], f["field"], f["issue"], f["standard"], f["template_def"]] for f in sa["fields"]],
           [30, 30, 32, 60, 26, 26])


def delivery_rows(bundle: Dict[str, Any]) -> list:
    """One row per CAD layer (and drawing style): where it went — geodatabase, feature class — and what happened."""
    fc_tpl = bundle.get("fc_template") or {}
    fc_geom = bundle.get("fc_geometry") or {}
    delivered = {}
    for g in ((bundle.get("gdb") or {}).get("geodatabases") or []):
        for fc, n in (g.get("counts") or {}).items():
            if n:
                delivered[fc] = g["name"]
    if bundle.get("gdb") and not delivered:
        for fc, n in ((bundle.get("gdb") or {}).get("counts") or {}).items():
            if n:
                delivered[fc] = bundle["gdb"]["name"]
    stats: Dict[tuple, Dict[str, int]] = {}
    for f in bundle.get("features") or []:
        d = stats.setdefault((f["layer"], f["target"]), {"ok": 0, "failed": 0, "skipped": 0})
        d[f["status"]] = d.get(f["status"], 0) + 1
    rows, seen = [], set()
    for m in bundle.get("mapping_rows") or []:
        tgt = m.get("target") if m.get("role") != "ignore" else None
        key = (m["layer"], tgt)
        repeat = key in seen and tgt
        st = stats.get(key, {}) if key not in seen else {}
        seen.add(key)
        if not tgt:
            status = "Not converted — " + ("set aside (base map / sheet / annotation)" if m.get("status") == "EXCLUDED"
                                           else "no matching feature class" if not m.get("target")
                                           else "set aside — no confident match, review in Layers")
        elif repeat:
            status = "Included in the row above (same layer and feature class)"
        elif st.get("ok") and not st.get("failed"):
            status = "Delivered" if bundle.get("gdb") else "Ready"
        elif st.get("ok"):
            status = f"Partly — {st['failed']} failed"
        elif st.get("failed"):
            status = f"Blocked — {st['failed']} failed (see Failed Features)"
        else:
            status = "Mapped"
        rows.append([m["layer"], m.get("style") or "", m["geometry"], m["count"],
                     (delivered.get(tgt) or fc_tpl.get(tgt) or "—") if tgt else "—", tgt or "—",
                     fc_geom.get(tgt, "") if tgt else "", st.get("ok", 0), st.get("failed", 0), status,
                     m.get("confidence") or "", m.get("status") or ""])
    return rows


def write_delivery_sheet(wb, bundle: Dict[str, Any]):
    ws = wb.create_sheet("Mapping & Delivery", 1)
    ws["A1"] = ("Every CAD layer: the original geodatabase and feature class it is delivered in, and the processing "
                "status. Each feature class stays in its own template geodatabase; the structure is not changed.")
    ws["A1"].font = Font(bold=True)
    _table(ws, ["Source CAD layer", "Drawing style", "CAD geometry", "CAD objects", "Target geodatabase",
                "Target feature class", "Feature class geometry", "Features written", "Failed", "Processing status",
                "Confidence", "Mapping"],
           delivery_rows(bundle), [40, 18, 11, 10, 46, 28, 12, 10, 8, 36, 11, 12], start=3)
