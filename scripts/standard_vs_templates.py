"""Workbook comparing the DMT As-Built Data Submission Standards V7 (annex attributes + section 4 topology) with the
seven official template geodatabases: python scripts/standard_vs_templates.py out.xlsx"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Font  # noqa: E402

from geosyntra_bridge.knowledge.annex import compare  # noqa: E402
from geosyntra_bridge.reporting.excel import _table, write_annex_sheets  # noqa: E402
from geosyntra_bridge.schema.loader import load_all_schemas  # noqa: E402
from geosyntra_bridge.validation.topology import asset_code, standard_comparison  # noqa: E402


def main(out: str):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schemas = {asset_code(s): s for s in load_all_schemas(os.path.join(root, "schemas")).values() if asset_code(s)}
    wb = Workbook()
    ws = wb.active
    ws.title = "Overview"
    ws["A1"] = "DMT As-Built Data Submission Standards V7 vs the official template geodatabases"
    ws["A1"].font = Font(bold=True, size=14)
    rows, topo = [], []
    for code in ("RD", "STL", "ITC", "SW", "IR", "SL", "HL"):
        s = schemas.get(code)
        if not s:
            continue
        c = compare(s, code)
        sm = c["summary"]
        kinds = {}
        for f in c["fields"]:
            k = f["issue"].split(" (")[0]
            kinds[k] = kinds.get(k, 0) + 1
        rows.append([code, s.schema_id, (s.source_template or ""), sm["template_classes"], sm["annex_classes"], sm["matched"],
                     sm["template_only"], sm["standard_only"],
                     ", ".join(f"{c2['template']} ({c2['status'].split(': ', 1)[-1]})" for c2 in c["classes"] if "geometry differs" in c2["status"]),
                     kinds.get("in standard, not in template", 0), kinds.get("in template, not in standard", 0),
                     kinds.get("data type differs", 0), kinds.get("text length shorter in template", 0),
                     kinds.get("mandatory in standard, nullable in template", 0)])
        write_annex_sheets(wb, c, prefix=f"{code} ")
        t = standard_comparison(s) or {}
        for r in t.get("rows", []):
            if not (r["in_standard"] and r["in_template"]):
                topo.append([code, t.get("section"), r["geometry"], r["label"], "yes" if r["in_standard"] else "no",
                             ", ".join(r["classes"]) or "no", r["note"]])
    _table(ws, ["Asset", "Schema", "Template", "Template classes", "Standard classes", "Matched", "Template only",
                "Standard only", "Geometry differs", "Fields only in standard", "Fields only in template",
                "Type differs", "Text shorter in template", "Mandatory in standard, nullable in template"],
           rows, [7, 26, 40, 10, 10, 10, 10, 10, 50, 12, 12, 10, 12, 14], start=3)
    ws2 = wb.create_sheet("Topology (section 4)", 1)
    _table(ws2, ["Asset", "Section", "Geometry", "Rule", "In standard", "Template classes", "How the converter handles it"],
           topo, [7, 9, 10, 32, 11, 60, 100])
    wb.save(out)
    print(out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Standard_vs_Templates.xlsx")
