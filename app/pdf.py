"""PDF export of a stored report (ReportLab)."""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

GREEN = colors.HexColor("#173d34")


def render_report_pdf(report: dict, device_name: str, created_at: str) -> bytes:
    a = report["analysis"]
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm, title="OsteoCare AI report")
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=GREEN, alignment=0)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=GREEN)
    body = ss["BodyText"]
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.HexColor("#5b6660"))

    def bullets(items):
        return ListFlowable([ListItem(Paragraph(escape(i), body)) for i in items], bulletType="bullet")

    period = a.get("period", report.get("period", "")).capitalize()
    shares = a.get("zone_share_percent", {})
    means = a.get("mean_adc", {})
    table = Table(
        [["Zone", "Share of signal", "Mean ADC (0–4095)"]]
        + [[z.capitalize(), f"{shares.get(z, 0)}%", str(means.get(z, "—"))] for z in ("heel", "ball", "toe")],
        colWidths=[45 * mm, 50 * mm, 50 * mm],
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d3cc")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))

    story = [
        Paragraph(f"OsteoCare AI · {escape(period)} mobility report", h1),
        Paragraph(f"Insole: {escape(device_name)} · Generated {escape(created_at)}", body),
        Paragraph(
            f"Samples: {a.get('samples', 0):,} · Active minutes: {a.get('active_minutes', 0)} · "
            f"Estimated heel strikes: {a.get('steps_estimate', 0):,} · "
            f"Walking sessions: {a.get('sessions', 0)} ({a.get('session_minutes', 0)} min)",
            body,
        ),
        Spacer(1, 6 * mm),
        table,
        Paragraph("Observations", h2),
        bullets(a.get("observations", [])),
        Paragraph("Precautionary recommendations", h2),
        bullets(a.get("recommendations", [])),
    ]
    if a.get("truncated"):
        story.append(Paragraph("Analysis limited to the latest 50,000 samples.", small))
    story += [Spacer(1, 8 * mm), Paragraph(escape(a.get("disclaimer", "")), small)]
    doc.build(story)
    return buf.getvalue()
