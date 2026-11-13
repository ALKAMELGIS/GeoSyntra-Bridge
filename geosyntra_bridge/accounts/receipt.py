"""A PDF receipt for a purchase request approved by the owner (invoice / bank transfer — no online payment)."""
from __future__ import annotations

import io
from typing import Any, Dict
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INK = colors.HexColor("#16191D")
MUTED = colors.HexColor("#596170")


def money(amount: int, currency: str) -> str:
    return f"{(currency or 'aed').upper()} {amount / 100:,.2f}"


def receipt_pdf(req: Dict[str, Any], user: Dict[str, Any], plan: Dict[str, Any], settings: Dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm, bottomMargin=20 * mm,
                            title=f"Receipt {req['reference']}", author=settings.get("company_name") or "GeoSyntra")
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=22, alignment=0, textColor=INK, spaceAfter=2)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=10, leading=14, textColor=INK)
    sub = ParagraphStyle("s", parent=body, textColor=MUTED, fontSize=9)
    company = escape(settings.get("company_name") or "GeoSyntra")
    st = []
    st.append(Paragraph(f"{company} — Receipt", h1))
    st.append(Paragraph(f"Reference <b>{escape(req['reference'])}</b> · paid {escape((req.get('decided_at') or '')[:10])}", sub))
    st.append(Spacer(1, 10 * mm))
    st.append(Paragraph("<b>Billed to</b>", body))
    st.append(Paragraph(escape(user.get("name") or ""), body))
    if user.get("company"):
        st.append(Paragraph(escape(user["company"]), body))
    st.append(Paragraph(escape(user.get("email") or ""), sub))
    st.append(Spacer(1, 8 * mm))
    what = plan.get("name") or req["plan_id"]
    detail = f"{plan.get('credits', 0):,} credits" + (" — one month" if plan.get("kind") == "subscription" else "")
    t = Table([["Item", "Detail", "Amount"], [what, detail, money(int(req.get("amount") or 0), req.get("currency") or "aed")],
               ["", "Total", money(int(req.get("amount") or 0), req.get("currency") or "aed")]],
              colWidths=[60 * mm, 65 * mm, 45 * mm])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9), ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 10), ("FONT", (1, 2), (-1, 2), "Helvetica-Bold", 11),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, 0), 0.6, MUTED),
        ("LINEABOVE", (1, 2), (-1, 2), 0.6, INK), ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    st.append(t)
    st.append(Spacer(1, 10 * mm))
    st.append(Paragraph("Paid by bank transfer / invoice and confirmed by the account owner. The credits were added to the account above.", sub))
    if settings.get("support_email"):
        st.append(Paragraph(f"Questions: {escape(settings['support_email'])}", sub))
    doc.build(st)
    return buf.getvalue()
