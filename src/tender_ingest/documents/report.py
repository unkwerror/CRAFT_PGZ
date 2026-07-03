"""PDF-отчёт «Разбор ТЗ»: собираем читаемый PDF из брифа (summary + поля + находки + цитаты).

Claude отдаёт структурированные данные (BRIEF_SCHEMA) — здесь рендерим их в PDF на
reportlab. Кириллица через встроенный в пакет шрифт DejaVu (без системных зависимостей).
"""

from __future__ import annotations

import datetime as dt
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

_FONTS_DIR = Path(__file__).parent / "fonts"
_FONT = "DejaVuSans"
_FONT_BOLD = "DejaVuSans-Bold"
_MUTED = HexColor("#64748b")
_LINE = HexColor("#e2e8f0")


def _register_fonts() -> None:
    if _FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(_FONT, str(_FONTS_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont(_FONT_BOLD, str(_FONTS_DIR / "DejaVuSans-Bold.ttf")))


_register_fonts()

_TITLE = ParagraphStyle("t", fontName=_FONT_BOLD, fontSize=16, leading=20, spaceAfter=6)
_META = ParagraphStyle(
    "m", fontName=_FONT, fontSize=8.5, leading=12, textColor=_MUTED, spaceAfter=2
)
_H2 = ParagraphStyle(
    "h", fontName=_FONT_BOLD, fontSize=11, leading=14, spaceBefore=10, spaceAfter=3
)
_BODY = ParagraphStyle(
    "b", fontName=_FONT, fontSize=10, leading=15, spaceAfter=3, alignment=TA_LEFT
)
_BODYB = ParagraphStyle("bb", fontName=_FONT_BOLD, fontSize=10, leading=14, spaceAfter=1)
_QUOTE = ParagraphStyle(
    "q", fontName=_FONT, fontSize=9, leading=13, leftIndent=10, textColor=_MUTED, spaceAfter=6
)


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text or ""), style)


def _cite(quote: str, page: str) -> str:
    return f"«{quote}»" + (f" — стр. {page}" if page else "")


def build_analysis_pdf(
    *,
    brief: dict[str, Any],
    filename: str,
    reestr: str,
    subject: str | None,
    model: str,
    created_at: dt.datetime | None,
    field_labels: list[tuple[str, str]],
) -> bytes:
    """Собрать PDF-отчёт разбора ТЗ из брифа. Возвращает байты PDF."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Разбор ТЗ — {filename}",
    )
    story: list[Any] = [_p("Разбор технического задания", _TITLE)]
    if subject:
        story.append(_p(subject, _META))
    story.append(_p(f"Файл: {filename} · Тендер № {reestr}", _META))
    stamp = created_at.strftime(" · %d.%m.%Y %H:%M") if created_at is not None else ""
    story.append(_p(f"Сгенерировано ИИ ({model}){stamp}", _META))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", color=_LINE, thickness=0.5))
    story.append(Spacer(1, 6))

    summary = str(brief.get("summary") or "")
    if summary:
        story.append(_p("Краткий бриф", _H2))
        story.append(_p(summary, _BODY))

    for key, label in field_labels:
        f = brief.get(key) or {}
        value = str(f.get("value") or "")
        quote = str(f.get("quote") or "")
        if value or quote:
            story.append(_p(label, _H2))
            story.append(_p(value or "—", _BODY))
            if quote:
                story.append(_p(_cite(quote, str(f.get("page") or "")), _QUOTE))

    findings = brief.get("findings") or []
    if isinstance(findings, list) and findings:
        story.append(_p("Прочие важные находки", _H2))
        for fnd in findings:
            if not isinstance(fnd, dict):
                continue
            story.append(_p("• " + str(fnd.get("title") or ""), _BODYB))
            detail = str(fnd.get("detail") or "")
            if detail:
                story.append(_p(detail, _BODY))
            quote = str(fnd.get("quote") or "")
            if quote:
                story.append(_p(_cite(quote, str(fnd.get("page") or "")), _QUOTE))

    story.append(Spacer(1, 10))
    story.append(
        _p(
            "Отчёт сформирован ИИ по тексту/скану ТЗ. Проверяйте ключевые данные по оригиналу.",
            _META,
        )
    )
    doc.build(story)
    return buf.getvalue()
