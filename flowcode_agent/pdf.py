"""Build a PDF of one report from its report.json data (what the report page shows)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

FONTS = Path(__file__).parent / "fonts"
INK, MUTED, GREEN, LINE, PANEL = (20, 20, 20), (95, 90, 84), (46, 115, 65), (226, 221, 214), (244, 242, 239)
AMBER_BG, GREEN_BG = (253, 243, 226), (220, 235, 223)
STATUS_TEXT = {"ongoing": "ongoing", "upcoming": "upcoming", "past": "past"}


# Japanese, Chinese and Korean characters, which DM Sans does not include.
CJK = re.compile(r"[\u3000-\u30ff\u3400-\u9fff\uac00-\ud7af\uff00-\uffef]")


def _title(text: str) -> str:
    return text.replace("_", " ").title().replace("Tv ", "TV ")


class ReportPDF(FPDF):
    def __init__(self, company: str, needs_cjk: bool = False):
        super().__init__(format="Letter")
        self.company = company
        self.add_font("DM", "", FONTS / "DMSans-Regular.ttf")
        self.add_font("DM", "B", FONTS / "DMSans-Bold.ttf")
        if needs_cjk:  # the CJK font is large, so only load it when a report needs it
            self.add_font("JP", "", FONTS / "NotoSansJP-Regular.ttf")
            self.set_fallback_fonts(["JP"], exact_match=False)
        self.set_margins(18, 16, 18)
        self.set_auto_page_break(True, margin=18)

    def multi_cell(self, *args, align: str = "L", **kwargs):
        """Left-aligned by default (fpdf2 justifies, which spreads short lines out)."""
        return super().multi_cell(*args, align=align, **kwargs)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("DM", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 5, f"Flowcode Research · {self.company}", new_x=XPos.LMARGIN)
        self.cell(0, 5, f"Page {self.page_no()}", align="R")

    # ---------- small building blocks ----------

    def text_block(self, text: str, size: float = 10, style: str = "", color=INK, line: float | None = None,
                   after: float = 2) -> None:
        self.set_font("DM", style, size)
        self.set_text_color(*color)
        self.multi_cell(0, line or size * 0.52, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(after)

    def label(self, text: str) -> None:
        self.set_font("DM", "B", 7.5)
        self.set_text_color(*MUTED)
        self.cell(0, 5, text.upper(), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def rule(self, gap: float = 4) -> None:
        self.set_draw_color(*LINE)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(gap)

    def keep(self, height: float) -> None:
        """Start a new page if fewer than `height` mm are left."""
        if self.get_y() + height > self.h - self.b_margin:
            self.add_page()

    def factor_rows(self, factors: dict, name_w: float = 42) -> None:
        for name, f in factors.items():
            self.keep(8)
            y = self.get_y()
            self.set_font("DM", "B", 9)
            self.set_text_color(*INK)
            self.set_xy(self.l_margin, y)
            self.cell(name_w, 5.5, _title(name))
            x = self.get_x()
            for i in range(5):  # five small bars like the page
                self.set_fill_color(*(GREEN if i < f["rating"] else LINE))
                self.rect(x + i * 5.2, y + 1.8, 4.4, 1.8, style="F")
            self.set_xy(x + 28, y)
            self.cell(6, 5.5, str(f["rating"]))
            self.set_font("DM", "", 8.5)
            self.set_text_color(*MUTED)
            self.multi_cell(0, 4.6, f["reason"], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_y(max(self.get_y(), y + 5.5) + 0.8)


def report_pdf(data: dict) -> bytes:
    """Render a report (the parsed report.json) as PDF bytes."""
    company, cs = data["company"], data["company_score"]
    pdf = ReportPDF(company["name"], needs_cjk=bool(CJK.search(json.dumps(data, ensure_ascii=False))))
    pdf.add_page()

    # header
    pdf.set_font("DM", "B", 8)
    pdf.set_text_color(*GREEN)
    pdf.cell(0, 5, "FLOWCODE RESEARCH", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)
    pdf.text_block(f"{company['name']}: where Flowcode could help", size=20, style="B", line=9, after=2)
    meta = [company.get("domain") or "", company.get("industry") or "", f"Run {data['run_date']}",
            f"Existing customer: {company.get('customer_tier') or 'no'}"]
    pdf.text_block("  ·  ".join(m for m in meta if m), size=9, color=MUTED, after=1)
    if data.get("channels_covered"):
        pdf.text_block("Channels: " + ", ".join(data["channels_covered"]), size=9, color=MUTED, after=4)

    # company score box
    top = pdf.get_y()
    pdf.set_fill_color(*PANEL)
    pdf.rect(pdf.l_margin, top, 38, 30, style="F")
    pdf.set_xy(pdf.l_margin + 4, top + 5)
    pdf.set_font("DM", "B", 26)
    pdf.set_text_color(*INK)
    pdf.cell(30, 12, f"{cs['score']:.2f}")
    pdf.set_xy(pdf.l_margin + 4, top + 18)
    pdf.set_font("DM", "", 7.5)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(32, 3.8, f"Company score · {len(data['opportunities'])} opportunities")
    pdf.set_left_margin(pdf.l_margin + 44)
    pdf.set_xy(pdf.l_margin, top + 1)
    pdf.factor_rows(cs["factors"], name_w=36)
    pdf.set_left_margin(18)
    pdf.set_y(max(pdf.get_y(), top + 32) + 3)

    if cs.get("snapshot"):
        pdf.text_block(cs["snapshot"], size=10.5, line=5.6, after=5)

    # opportunities
    pdf.rule(5)
    pdf.text_block("Opportunities", size=14, style="B", line=7, after=2)
    if not data["opportunities"]:
        pdf.text_block("No verified, current opportunities were found.", color=MUTED)
    for rank, o in enumerate(data["opportunities"], 1):
        m = o["moment"]
        pdf.keep(60)
        y = pdf.get_y()
        pdf.set_font("DM", "B", 12)
        pdf.set_text_color(*INK)
        pdf.set_xy(pdf.l_margin, y)
        pdf.multi_cell(pdf.epw - 24, 6, f"{rank}. {m['title']}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        end_title = pdf.get_y()
        pdf.set_fill_color(*GREEN_BG)
        pdf.set_text_color(*GREEN)
        pdf.set_xy(pdf.w - pdf.r_margin - 18, y)
        pdf.cell(18, 7, f"{o['score']:.2f}", align="C", fill=True)
        pdf.set_y(end_title + 1)
        timing = f"{m['timing'].get('date_text') or 'Date not stated'} · {STATUS_TEXT.get(m['timing']['status'], '')}"
        tags = [_title(m["type"]), timing] + ([f"Play: {_title(o['play_id'])}"] if o.get("play_id") else [])
        pdf.text_block("   |   ".join(tags), size=8.5, color=MUTED, after=3)

        pdf.label("What's happening")
        pdf.text_block(m["summary"], after=3)
        if m.get("physical_touchpoints"):
            pdf.label("Where a code goes")
            pdf.text_block(", ".join(m["physical_touchpoints"]), after=3)
        pdf.label("Why Flowcode fits")
        pdf.text_block(o["why_flowcode_fits"], after=3)

        pdf.keep(30)  # keep the pitch label and its box on one page
        pdf.label("Suggested first pitch")
        pdf.set_font("DM", "", 10)
        pdf.set_text_color(*INK)
        pdf.set_fill_color(*AMBER_BG)
        pdf.multi_cell(0, 5.2, f"“{o['suggested_first_pitch']}”", fill=True, padding=(2.5, 3, 2.5, 3),
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(3)

        story = o.get("similar_story")
        if story:
            pdf.label("Similar Flowcode story")
            pdf.set_font("DM", "B", 9.5)
            pdf.set_text_color(*GREEN)
            pdf.multi_cell(0, 5, f"{story['name']}: {story['result']}", link=story["url"],
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(2)
        if o.get("flowcode_products"):
            pdf.label("Products")
            pdf.text_block(", ".join(o["flowcode_products"]), size=9, after=3)

        if o.get("factor_ratings"):
            pdf.label("Score breakdown")
            pdf.factor_rows(o["factor_ratings"])
            pdf.ln(2)

        if o.get("sources"):
            pdf.label("Sources")
            for s in o["sources"]:
                pdf.keep(14)
                pdf.set_font("DM", "B", 8.5)
                pdf.set_text_color(*INK)
                pdf.multi_cell(0, 4.4, f"{s['publisher']} · {s.get('date') or 'date unknown'}",
                               new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_font("DM", "", 8.5)
                pdf.multi_cell(0, 4.4, f"“{s['quote']}”", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_text_color(*GREEN)
                pdf.multi_cell(0, 4.4, s["url"], link=s["url"], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.ln(1.5)
        pdf.ln(2)
        pdf.rule(5)
    return bytes(pdf.output())
