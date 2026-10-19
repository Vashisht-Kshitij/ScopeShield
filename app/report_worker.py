"""Credential-free, bounded PDF renderer. Input/output are pipes, never temp files."""
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pdf_worker import memory_limit


def render(snapshot, *, executive=False):
    from io import BytesIO
    import unicodedata
    from xml.sax.saxutils import escape
    import reportlab
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from pypdf import PdfReader, PdfWriter
    from app.report_content import document
    from app.project_intelligence import intelligence
    from app.report_brief import brief_pages

    fonts = Path(reportlab.__file__).parent / "fonts"
    pdfmetrics.registerFont(TTFont("Evidence", str(Path(__file__).parent / "assets/fonts/DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("EvidenceBold", str(fonts / "VeraBd.ttf")))
    glyphs = pdfmetrics.getFont("Evidence").face.charToGlyph
    navy, teal = colors.HexColor("#142B40"), colors.HexColor("#12726C")
    def safe(value, bold=False):
        result = []
        available = pdfmetrics.getFont("EvidenceBold").face.charToGlyph if bold else glyphs
        for char in value:
            if char == "\\": result.append("\\\\")
            elif char in "\n\t": result.append(char)
            elif ord(char) >= 0x1F000 or ord(char) not in available or unicodedata.category(char) in {"Cc", "Cf", "Cs", "Mn", "Mc", "Me"} or unicodedata.bidirectional(char) in {"R", "AL", "AN"}:
                result.append(f"[U+{ord(char):04X}]")
            else: result.append(char)
        return "".join(result)
    styles = {
        "title": ParagraphStyle("Title", fontName="EvidenceBold", fontSize=24, leading=30, textColor=navy, spaceAfter=12),
        "heading": ParagraphStyle("Section", fontName="EvidenceBold", fontSize=13, leading=18, textColor=teal, spaceBefore=18, spaceAfter=9, keepWithNext=True, wordWrap="CJK"),
        "text": ParagraphStyle("Body", fontName="Evidence", fontSize=9.2, leading=13.8, textColor=navy, spaceAfter=7, wordWrap="CJK", splitLongWords=True),
        "quote": ParagraphStyle("Quote", fontName="Evidence", fontSize=9, leading=14, textColor=navy, leftIndent=12, rightIndent=10, borderColor=colors.HexColor("#D4E5E1"), borderWidth=0.6, borderPadding=7, spaceBefore=5, spaceAfter=12, wordWrap="CJK"),
    }
    styles["notice"] = ParagraphStyle("Notice", parent=styles["text"], backColor=colors.HexColor("#FFF1D9"), borderPadding=6, spaceBefore=5, spaceAfter=10)
    styles["project"] = ParagraphStyle("Project", parent=styles["heading"], fontName="Evidence")
    if executive:
        for key in ("text", "notice", "quote"):
            styles[key].fontSize = 8.8; styles[key].leading = 12; styles[key].spaceAfter = 5
        styles["quote"].borderPadding = 5
        styles["heading"].spaceBefore = 11; styles["heading"].spaceAfter = 6
    def paragraph(value, kind="text"):
        # No user markup reaches the Paragraph parser; URLs are plain text.
        return Paragraph(escape(safe(value, kind in {"heading", "title"})).replace("\t", "&#160;" * 4).replace("\n", "<br/>"), styles[kind])
    content = document(snapshot)
    info = intelligence(snapshot)
    story = [paragraph("EVIDENCE PACK", "title"), paragraph("ScopeShield / A traceable basis for negotiation"), Spacer(1, 12)]
    if info["simulated"]:
        story.append(paragraph("SIMULATED - NO REAL PAYMENT / Includes simulated records; they do not establish real client consent or transactions.", "notice"))
    summary = [[paragraph("SOURCE REVISION"), paragraph(snapshot["source_revision"][:24])],
               [paragraph("GENERATED / UTC"), paragraph(snapshot["generated_at"])],
               [paragraph("RECORDS"), paragraph(f"{len(snapshot['contracts'])} agreements / {len(snapshot['requests'])} scope requests / {len(snapshot['orders'])} change orders")]]
    table = Table(summary, colWidths=[125, 360], hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0F6F5")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 8)]))
    story.extend([table, Spacer(1, 14)])
    for warning in content["warnings"]: story.append(paragraph("CAUTION / " + warning))
    characters = 0
    for section in content["sections"]:
        if section["title"].startswith(("01 /", "02 /", "05 /", "06 /", "07 /", "08 /")):
            story.append(PageBreak())
        if section["title"].startswith("01 /"):
            story.append(paragraph("DETAILED EVIDENCE AND AUDIT APPENDIX", "heading"))
        story.append(paragraph(section["title"], "heading"))
        for block in section["blocks"]:
            characters += len(block["text"])
            if characters > 600_000: raise ValueError("Report text limit")
            # Bounded flowables can split across pages even for unbroken messages.
            for start in range(0, len(block["text"]), 1200):
                story.append(paragraph(block["text"][start:start + 1200], block["kind"]))
    if executive:
        story = []
        for index, page in enumerate(brief_pages(snapshot, info, lambda text: len(safe(text)))):
            if index: story.append(PageBreak())
            story.extend(paragraph(b["text"], b["kind"]) for b in page)
    stream = BytesIO()
    class BoundedDocument(SimpleDocTemplate):
        def handle_pageBegin(self):
            if self.page >= (2 if executive else 160): raise ValueError("Report page limit")
            super().handle_pageBegin()
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D4E5E1")); canvas.line(48, 48, A4[0] - 48, 48)
        canvas.setFont("Evidence", 8); canvas.setFillColor(navy)
        canvas.drawString(48, 33, "ScopeShield / Private executive summary" if executive else "ScopeShield / Private negotiation record / " + snapshot["source_revision"][:12])
        canvas.drawRightString(A4[0] - 48, 33, f"Page {doc.page}")
        canvas.restoreState()
    doc = BoundedDocument(stream, pagesize=A4, rightMargin=50, leftMargin=50, topMargin=45, bottomMargin=64,
        title="ScopeShield Executive Summary" if executive else "ScopeShield Evidence Pack", author="ScopeShield", pageCompression=1)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    writer = PdfWriter(); writer.clone_document_from_reader(PdfReader(BytesIO(stream.getvalue())))
    writer.add_attachment("project-snapshot.json", json.dumps(snapshot, ensure_ascii=True, sort_keys=True, indent=2).encode("utf-8"))
    final = BytesIO(); writer.write(final)
    output = final.getvalue()
    if len(output) > 8 * 1024 * 1024: raise ValueError("Report byte limit")
    return output


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        job = memory_limit()
        raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024: raise ValueError("Snapshot limit")
        snapshot = json.loads(raw)
        if sys.argv[1:] == ["--summary"]:
            from app.report_content import document
            content = document(snapshot)
            if sum(len(b["text"]) for s in content["sections"] for b in s["blocks"]) > 600_000:
                raise ValueError("Report text limit")
            from app.project_intelligence import intelligence
            sys.stdout.write(json.dumps({"warnings": content["warnings"], "sections": [s["title"] for s in content["sections"]], "intelligence": intelligence(snapshot)}))
        elif sys.argv[1:] == ["--executive"]: sys.stdout.buffer.write(render(snapshot, executive=True))
        elif sys.argv[1:]: raise ValueError("Unsupported worker mode")
        else: sys.stdout.buffer.write(render(snapshot))
    except BaseException:
        sys.exit(1)
