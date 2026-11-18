"""Build a PDF of one report from its report.json data (what the report page shows)."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

FONTS = Path(__file__).parent / "fonts"
INK, MUTED, GREEN, LINE, PANEL = (20, 20, 20), (95, 90, 84), (46, 115, 65), (226, 221, 214), (244, 242, 239)
AMBER_BG, GREEN_BG, AMBER, PILL = (253, 243, 226), (220, 235, 223), (242, 194, 122), (232, 228, 222)
STATUS_TEXT = {"ongoing": "ongoing", "upcoming": "upcoming", "past": "past"}


# Japanese, Chinese and Korean characters, which DM Sans does not include.
CJK = re.compile(r"[\u3000-\u30ff\u3400-\u9fff\uac00-\ud7af\uff00-\uffef]")


# Lucide icons (the same ones the web report uses), drawn as SVG.
ICONS = {
    "pin": '<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"/>'
           '<circle cx="12" cy="10" r="3"/>',
    "book": '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 '
            '1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
    "link": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
}


def _icon(name: str, color=INK) -> io.BytesIO:
    hex_color = "#%02x%02x%02x" % color
    return io.BytesIO(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
        f'stroke="{hex_color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>'
        .encode())


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
        self.set_char_spacing(0.9)
        self.cell(0, 6, text.upper(), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_char_spacing(0)

    def chips(self, items: list[str], icon: str | None = None, after: float = 4) -> None:
        """Rounded pills that wrap onto new lines, like the web report's chips."""
        self.set_font("DM", "", 9.5)
        self.set_text_color(*INK)
        h, pad, gap, icon_w = 7, 3.2, 2.2, (3.2 if icon else 0)
        x, y = self.l_margin, self.get_y()
        self.keep(h)
        y = self.get_y()
        for item in items:
            text_w = min(self.get_string_width(item), self.epw - 2 * pad - icon_w - 1.5)
            w = 2 * pad + text_w + (icon_w + 1.5 if icon else 0)
            if x + w > self.w - self.r_margin and x > self.l_margin:
                x, y = self.l_margin, y + h + gap
                if y + h > self.h - self.b_margin:
                    self.add_page()
                    y = self.get_y()
            self.set_fill_color(*PILL)
            self.rect(x, y, w, h, style="F", round_corners=True, corner_radius=h / 2 - 0.01)  # fpdf2 shrinks r >= h/2
            tx = x + pad
            if icon:
                self.image(_icon(icon), tx, y + (h - icon_w) / 2, w=icon_w)
                tx += icon_w + 1.5
            self.set_xy(tx, y)
            self.cell(text_w, h, item)
            x += w + gap
        self.set_xy(self.l_margin, y + h)
        self.ln(after)

    def pitch(self, text: str) -> None:
        """Amber box with a darker bar on the left."""
        self.set_font("DM", "", 10.5)
        line, pad, bar = 5.6, 4, 1.2
        width = self.epw - bar - 2 * pad
        lines = self.multi_cell(width, line, text, dry_run=True, output="LINES")
        height = len(lines) * line + 2 * pad
        self.keep(height + 2)
        x, y = self.l_margin, self.get_y()
        self.set_fill_color(*AMBER_BG)
        self.rect(x, y, self.epw, height, style="F")
        self.set_fill_color(*AMBER)
        self.rect(x, y, bar, height, style="F")
        self.set_text_color(*INK)
        self.set_xy(x + bar + pad, y + pad)
        self.multi_cell(width, line, text)
        self.set_xy(self.l_margin, y + height)
        self.ln(4)

    def story(self, story: dict) -> None:
        """Grey panel with a book icon, the story name and result, and a link icon."""
        height, pad = 14, 4
        self.keep(height + 2)
        x, y = self.l_margin, self.get_y()
        self.set_fill_color(*PANEL)
        self.rect(x, y, self.epw, height, style="F")
        self.image(_icon("book"), x + pad, y + (height - 5.5) / 2, w=5.5)
        self.image(_icon("link"), self.w - self.r_margin - pad - 4, y + (height - 4) / 2, w=4)
        text_x, text_w = x + pad + 9, self.epw - pad * 2 - 9 - 7
        self.set_xy(text_x, y + 2.3)
        self.set_font("DM", "B", 10.5)
        self.set_text_color(*INK)
        self.cell(text_w, 5, f"Similar story: {story['name']}")
        self.set_xy(text_x, y + 7.3)
        self.set_font("DM", "", 9)
        self.set_text_color(*MUTED)
        self.cell(text_w, 4.5, story.get("result") or "")
        self.link(x, y, self.epw, height, story["url"])
        self.set_xy(self.l_margin, y + height)
        self.ln(4)

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
            pdf.chips(m["physical_touchpoints"], icon="pin")
        pdf.label("Why Flowcode fits")
        pdf.text_block(o["why_flowcode_fits"], after=3)

        pdf.keep(30)  # keep the pitch label and its box on one page
        pdf.label("Suggested first pitch")
        pdf.pitch(f"“{o['suggested_first_pitch']}”")

        if o.get("similar_story"):
            pdf.story(o["similar_story"])
        if o.get("flowcode_products"):
            pdf.label("Products")
            pdf.chips(o["flowcode_products"])

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
