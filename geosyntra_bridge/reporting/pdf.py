"""PDF processing report (reportlab) built from a results bundle."""
from __future__ import annotations

from typing import Any, Dict, List
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY = colors.HexColor("#0F2341")
GREEN = colors.HexColor("#2E9E6B")
GRID = colors.HexColor("#D9DEE7")
SEV = {"error": colors.HexColor("#C0392B"), "warning": colors.HexColor("#B9770E"), "info": colors.HexColor("#2E5C9A")}


def _p(text: Any, style) -> Paragraph:
    return Paragraph(escape("" if text is None else str(text)), style)


def _tbl(rows: List[List[Any]], widths, style, head=True) -> Table:
    data = [[c if isinstance(c, Paragraph) else _p(c, style) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1 if head else 0, hAlign="LEFT")
    ts = [("GRID", (0, 0), (-1, -1), 0.25, GRID), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if head:
        ts += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
    t.setStyle(TableStyle(ts))
    return t


def write_pdf_report(bundle: Dict[str, Any], path: str, max_issues: int = 400):
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=7.5, leading=9)
    head = ParagraphStyle("h", parent=body, textColor=colors.white, fontName="Helvetica-Bold")
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=17, textColor=NAVY, alignment=0, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11, textColor=NAVY, spaceBefore=8, spaceAfter=4)
    sub = ParagraphStyle("s", parent=body, fontSize=9, textColor=colors.HexColor("#5A6478"))

    s = bundle["summary"]
    ver = bundle.get("verification") or {}
    doc = SimpleDocTemplate(path, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm, title="CAD to GDB Conversion Report",
                            author="GeoSyntra Bridge")
    el: List[Any] = [Paragraph("CAD to GDB Conversion Report", h1),
                     _p(f"{bundle['schema']['display_name']}  ·  {bundle['source_file']}  ·  {bundle['generated_at'][:19].replace('T', ' ')} UTC", sub),
                     Spacer(1, 6)]

    def hrow(cols):
        return [Paragraph(escape(c), head) for c in cols]

    meta = [hrow(["Item", "Value"]),
            ["Job ID", bundle["job_id"]],
            ["Schema", f"{bundle['schema']['schema_id']} v{bundle['schema']['version']} ({bundle['schema']['status']})"],
            ["Source CRS / target CRS", f"{'EPSG:' + str(s['source_epsg']) if s.get('source_epsg') else 'assumed = target'}  →  "
                                        f"{'EPSG:' + str(s['target_epsg']) if s.get('target_epsg') else 'undefined'}"],
            ["Output geodatabase", (bundle.get("gdb") or {}).get("name", "not generated (validation run)")],
            ["Drawing key source", (bundle.get("key_info") or {}).get("label", "—")],
            ["Verification", "PASSED" if ver.get("passed") else ("FAILED" if ver else "not run")]]
    kp = [hrow(["Result", "Count"]),
          ["Features imported", s["features_imported"]], ["Features failed", s["features_failed"]],
          ["Missing attributes (features)", s["features_with_missing_attributes"]], ["Duplicate records", s["duplicates"]],
          ["Unmatched labels", s["unmatched_labels"]], ["Ambiguous / conflicting labels", s["ambiguous_labels"]],
          ["Uncertain mappings", s["uncertain_mappings"]], ["Unmapped CAD entities", s["unmapped_cad_entities"]],
          ["Geometry / topology errors", s["geometry_topology_errors"]], ["Network issues", s["network_issues"]],
          ["Domain / value errors", s["domain_errors"]]]
    pair = Table([[_tbl(meta, [45 * mm, 95 * mm], body), _tbl(kp, [60 * mm, 22 * mm], body)]],
                 colWidths=[145 * mm, 90 * mm], hAlign="LEFT")
    pair.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (0, 0), 0)]))
    el.append(pair)

    if ver.get("checks"):
        el.append(Paragraph("Geodatabase verification", h2))
        rows = [hrow(["Check", "Result", "Detail"])]
        for c in ver["checks"]:
            res = Paragraph(f'<font color="{"#2E9E6B" if c["passed"] else "#C0392B"}"><b>{"PASS" if c["passed"] else "FAIL"}</b></font>', body)
            rows.append([c["check"], res, c["detail"]])
        el.append(_tbl(rows, [80 * mm, 18 * mm, 170 * mm], body))
    sub_note = (bundle.get("gdb") or {}).get("subtypes")
    if sub_note and sub_note.get("note"):
        el.append(Spacer(1, 3))
        el.append(_p("Subtypes: " + sub_note["note"], sub))

    el.append(Paragraph("Features by feature class", h2))
    fc = [hrow(["Feature class", "Written"])] + [[k, v] for k, v in s["features_by_class"].items()]
    el.append(_tbl(fc if len(fc) > 1 else fc + [["—", 0]], [90 * mm, 25 * mm], body))

    if bundle.get("keys"):
        el.append(Paragraph("CAD key read from the drawing", h2))
        rows = [hrow(["Code", "Description", "Linked CAD layer"])] + \
            [[k["code"], k["description"], k.get("best_layer") or "—"] for k in bundle["keys"]]
        el.append(_tbl(rows, [25 * mm, 120 * mm, 80 * mm], body))
    ev = bundle.get("elevation") or {}
    if ev:
        dem = ev.get("dem") or {}
        vert = ev.get("vertical") or {}
        ev_rows = [hrow(["Item", "Value"]),
                   ["Status", ev.get("status")], ["DEM", dem.get("label", "off")],
                   ["Resolution / CRS", f"{' x '.join(map(str, dem.get('resolution_m') or []))} m / {dem.get('crs')}" if dem else "-"],
                   ["Vertical reference", f"{vert.get('result')} - {vert.get('message')}" if vert else "-"],
                   ["Features by source", ", ".join(f"{k} {v}" for k, v in (ev.get("by_status") or {}).items())],
                   ["Vertices", ", ".join(f"{k} {v}" for k, v in (ev.get("vertices") or {}).items())],
                   ["Z range min / mean / max (m)", " / ".join(str((ev.get("z_range") or {}).get(k)) for k in ("min", "mean", "max")) if ev.get("z_range") else "-"],
                   ["Without elevation", f"{ev.get('missing_features', 0)} ({ev.get('missing_policy')})"]]
        if ev.get("validation"):
            ev_rows.append(["Z checks", ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in ev["validation"].items())])
        el.append(Paragraph("Elevation (DEM)", h2))
        el.append(_tbl(ev_rows, [55 * mm, 190 * mm], body))
    qc = bundle.get("dmt_qc")
    if qc:
        el.append(Paragraph("DMT Data Quality Control (replica of ADM_Data_Quality_Control)", h2))
        rows = [hrow(["Check", "Errors"])] + [[k, v] for k, v in (qc.get("by_check") or {}).items()]
        el.append(_tbl(rows, [120 * mm, 25 * mm], body))
        el.append(_p(qc.get("note", ""), body))
    from .excel import delivery_rows
    drows = delivery_rows(bundle)
    if drows:
        el.append(Paragraph("Mapping and delivery — CAD layer → original geodatabase → feature class", h2))
        rows = [hrow(["CAD layer", "Geom.", "Target geodatabase", "Feature class", "Written", "Status"])] + \
            [[_p(r[0] + (f" ({r[1]})" if r[1] else ""), body), r[2], _p(r[4], body), r[5], r[7], _p(r[9], body)] for r in drows]
        el.append(_tbl(rows, [62 * mm, 14 * mm, 62 * mm, 42 * mm, 16 * mm, 52 * mm], body))
    ts = (s.get("network") or {}).get("topology_standard") or {}
    if ts.get("rows"):
        el.append(Paragraph(f"Topology rules — submission standard {ts.get('section')} vs template topology", h2))
        rows = [hrow(["Geometry", "Rule", "Standard", "Template", "Violations", "Note"])] + \
            [[r["geometry"], r["label"], "yes" if r["in_standard"] else "no",
              f"{len(r['classes'])} class(es)" if r["in_template"] else "no",
              "—" if r.get("violations") is None else (f"{r['violations']} (info)" if r.get("advisory") else r["violations"]),
              _p(r["note"], body)] for r in ts["rows"]]
        el.append(_tbl(rows, [18 * mm, 48 * mm, 18 * mm, 22 * mm, 22 * mm, 120 * mm], body))
    cm = s.get("contractor_mandatory_missing") or {}
    if cm:
        el.append(Paragraph("Contractor-mandatory fields still empty (DMT data dictionary)", h2))
        rows = [hrow(["Feature class", "Field", "Without value", "Features"])] + \
            [[fc, fld, n, s["features_by_class"].get(fc, "")] for fc, flds in cm.items() for fld, n in sorted(flds.items())][:120]
        el.append(_tbl(rows, [55 * mm, 70 * mm, 28 * mm, 25 * mm], body))
    if bundle.get("cleaning"):
        el.append(Paragraph("CAD clean-up before transfer", h2))
        rows = [hrow(["Action", "Entities", "Layers"])] + \
            [[c["action"], c["count"], ", ".join(f"{l} ({n})" for l, n in c["layers"].items())] for c in bundle["cleaning"]]
        el.append(_tbl(rows, [110 * mm, 20 * mm, 120 * mm], body))
    ref = bundle.get("reference")
    if ref:
        el.append(Paragraph("Spatial join with reference GIS", h2))
        el.append(_p(f"{ref.get('file')}: {ref.get('matched_entities', 0)} CAD objects matched to reference features"
                     + ("" if ref.get("overlap") else " — no overlap with the drawing"), body))
    el.append(Paragraph("Layer mapping", h2))
    rows = [hrow(["CAD layer", "Geometry", "Entities", "Target", "Status", "Confidence"])]
    for m in bundle["mapping_rows"]:
        rows.append([m["layer"] + (f" [{m['style']}]" if m.get("style") else ""), m["geometry"], m["count"], m.get("target") or ("ignored" if m.get("role") == "ignore" else "— unmapped"),
                     m.get("status"), f"{m.get('confidence') or ''} {m.get('score') or ''}"])
    el.append(_tbl(rows, [75 * mm, 20 * mm, 18 * mm, 60 * mm, 28 * mm, 30 * mm], body))

    net = s.get("network") or {}
    if net.get("enabled"):
        el.append(Paragraph("Network analysis", h2))
        rows = [hrow(["Metric", "Value"])] + [[k.replace("_", " "), v] for k, v in net.items() if k not in ("enabled", "contractor_mandatory_missing", "not_null", "topology", "dmt_spatial_rules", "topology_standard")]
        el.append(_tbl(rows, [70 * mm, 30 * mm], body))

    el.append(PageBreak())
    issues = bundle["issues"]
    order = {"error": 0, "warning": 1, "info": 2}
    issues = sorted(issues, key=lambda i: (order.get(i["severity"], 3), i["category"], i["id"]))
    el.append(Paragraph(f"Issues ({len(issues)} total{', first ' + str(max_issues) + ' shown — see Excel report for all' if len(issues) > max_issues else ''})", h2))
    rows = [hrow(["#", "Severity", "Category", "Message", "Suggested correction", "CAD ref"])]
    for i in issues[:max_issues]:
        sv = Paragraph(f'<font color="{SEV[i["severity"]].hexval().replace("0x", "#")}"><b>{i["severity"]}</b></font>', body)
        src = i.get("source") or {}
        rows.append([i["id"], sv, i["category"].replace("_", " "), i["message"], i["suggestion"],
                     f"{src.get('layer') or ''} {('#' + str(src['handle'])) if src.get('handle') else ''}"])
    el.append(_tbl(rows, [10 * mm, 16 * mm, 28 * mm, 110 * mm, 70 * mm, 38 * mm], body))

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#7A8499"))
        canvas.drawString(12 * mm, 7 * mm, f"GeoSyntra Bridge · CAD to GDB Schema Converter · job {bundle['job_id']}")
        canvas.drawRightString(landscape(A4)[0] - 12 * mm, 7 * mm, f"Page {d.page}")
        canvas.restoreState()

    doc.build(el, onFirstPage=footer, onLaterPages=footer)
