"""
Convert the Narrator's markdown output into a professional, regulator-ready PDF
using reportlab. Parses headings, the findings table, bullet lists, bold runs,
and the certification block. Returns PDF as bytes.
"""
import io
import re

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

BLUE = HexColor("#185FA5")
BLUE_DARK = HexColor("#0C447C")
RED = HexColor("#C0392B")
RED_LIGHT = HexColor("#FDEDEC")
AMBER = HexColor("#E67E22")
AMBER_LIGHT = HexColor("#FEF9E7")
GREEN = HexColor("#27AE60")
GREEN_LIGHT = HexColor("#EAFAF1")
GRAY = HexColor("#7F8C8D")
LIGHT_GRAY = HexColor("#F8F9FA")
BORDER = HexColor("#E0E0E0")
TEXT = HexColor("#2C3E50")

SEV_BG = {"critical": RED_LIGHT, "moderate": AMBER_LIGHT, "minor": LIGHT_GRAY}
SEV_FG = {"critical": RED, "moderate": AMBER, "minor": GRAY}


def _styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("ag_title", parent=ss["Title"], textColor=BLUE,
                                fontName="Helvetica-Bold", fontSize=18,
                                spaceAfter=4, alignment=TA_LEFT),
        "meta": ParagraphStyle("ag_meta", parent=ss["Normal"], textColor=TEXT,
                               fontSize=9.5, leading=14),
        "h2": ParagraphStyle("ag_h2", parent=ss["Heading2"], textColor=BLUE,
                             fontName="Helvetica-Bold", fontSize=13,
                             spaceBefore=14, spaceAfter=6),
        "body": ParagraphStyle("ag_body", parent=ss["Normal"], textColor=TEXT,
                               fontSize=10, leading=15),
        "bullet": ParagraphStyle("ag_bullet", parent=ss["Normal"], textColor=TEXT,
                                 fontSize=9.5, leading=14, leftIndent=14,
                                 spaceAfter=4),
        "cell": ParagraphStyle("ag_cell", parent=ss["Normal"], textColor=TEXT,
                               fontSize=7.6, leading=9.5),
        "cell_h": ParagraphStyle("ag_cell_h", parent=ss["Normal"], textColor=white,
                                 fontSize=8, leading=10, fontName="Helvetica-Bold"),
        "cert": ParagraphStyle("ag_cert", parent=ss["Normal"], textColor=TEXT,
                               fontSize=9.5, leading=16, borderColor=BORDER,
                               borderWidth=1, borderPadding=10, backColor=LIGHT_GRAY),
    }


def _bold(text: str) -> str:
    """Convert **bold** and _italic_ markdown to reportlab markup."""
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", text)
    return text


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(GRAY)
    canvas.drawString(0.75 * inch, 0.5 * inch,
                      "AssedGuard AI — Confidential Audit Document")
    canvas.drawRightString(7.75 * inch, 0.5 * inch, f"Page {doc.page}")
    canvas.setStrokeColor(BORDER)
    canvas.line(0.75 * inch, 0.62 * inch, 7.75 * inch, 0.62 * inch)
    canvas.restoreState()


def _findings_table(lines, S):
    """Build a styled reportlab Table from markdown table lines."""
    rows = []
    for ln in lines:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        rows.append(cells)
    if len(rows) < 2:
        return None
    header, body = rows[0], rows[2:]  # rows[1] is the |---| separator

    sev_col = next((i for i, h in enumerate(header) if "sever" in h.lower()), 2)

    data = [[Paragraph(_bold(h), S["cell_h"]) for h in header]]
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    for r, cells in enumerate(body, start=1):
        sev = cells[sev_col].lower() if sev_col < len(cells) else ""
        data.append([Paragraph(_bold(c), S["cell"]) for c in cells])
        if r % 2 == 0:
            style.append(("BACKGROUND", (0, r), (-1, r), LIGHT_GRAY))
        if sev in SEV_BG:
            style.append(("BACKGROUND", (sev_col, r), (sev_col, r), SEV_BG[sev]))
            style.append(("TEXTCOLOR", (sev_col, r), (sev_col, r), SEV_FG[sev]))

    # Column widths tuned for: ID | Issue | Severity | Rows | Action | Reason
    total = 7.0 * inch
    ncol = len(header)
    if ncol == 6 and any("reason" in h.lower() for h in header):
        # Findings table: ID | Issue | Severity | Rows | Action | Reason
        widths = [0.5, 1.35, 0.7, 0.4, 0.95, 3.1]
        widths = [w * inch for w in widths]
    else:
        widths = [total / ncol] * ncol
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle(style))
    return t


def markdown_to_pdf(markdown_text: str, filename: str = "audit_narrative.pdf") -> bytes:
    S = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.8 * inch,
                            title="AssedGuard AI — Audit Narrative")
    flow = []
    lines = markdown_text.splitlines()
    i = 0
    in_cert = False
    cert_buf = []

    while i < len(lines):
        line = lines[i].rstrip()

        if line.startswith("# "):
            flow.append(Paragraph(_bold(line[2:]), S["title"]))
            flow.append(Spacer(1, 4))
        elif line.startswith("## "):
            in_cert = line[3:].strip().lower().startswith("certification")
            flow.append(Paragraph(_bold(line[3:]), S["h2"]))
        elif line.startswith("|"):
            # gather contiguous table block
            block = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                block.append(lines[i])
                i += 1
            t = _findings_table(block, S)
            if t is not None:
                flow.append(t)
            continue
        elif line.startswith("- "):
            flow.append(Paragraph("•&nbsp;&nbsp;" + _bold(line[2:]), S["bullet"]))
        elif line.startswith("**") and ":**" in line:
            flow.append(Paragraph(_bold(line), S["meta"]))
        elif in_cert and line.strip():
            cert_buf.append(line)
        elif line.strip():
            flow.append(Paragraph(_bold(line), S["body"]))
        else:
            if in_cert and cert_buf:
                flow.append(Paragraph("<br/>".join(_bold(c) for c in cert_buf),
                                      S["cert"]))
                cert_buf = []
            flow.append(Spacer(1, 4))
        i += 1

    if cert_buf:
        flow.append(Paragraph("<br/>".join(_bold(c) for c in cert_buf), S["cert"]))

    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
