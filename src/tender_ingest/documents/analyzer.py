"""DocumentAnalyzer — бриф по ТЗ на Claude (Sonnet): семантический разбор + цитаты.

Вход — извлечённый текст ТЗ (с маркерами страниц), выход — структурный бриф (обязательные
поля + findings), строгий JSON. Системный промпт кэшируется (cache_control). Модель и ключ
берутся из конфигурации; экономия входа обеспечивается ограничением объёма в extract.py.
"""

from __future__ import annotations

import base64
import io
import json
import math
from typing import Any

import anthropic
import structlog
from pypdf import PdfReader, PdfWriter

from tender_ingest.config import MissingApiKeyError, Settings, get_settings
from tender_ingest.documents.prompt import (
    BRIEF_SCHEMA,
    SYSTEM_PROMPT,
    brief_schema_with_card,
    build_merge_message,
    build_message,
    build_pdf_message,
)

log = structlog.get_logger()

_MAX_TOKENS = 16000  # развёрнутый бриф: подробные поля + много findings с цитатами

# Лимиты нативного PDF у Claude: ~32 МБ на запрос (с base64-накладными) и ~100 страниц.
# Держим сырой PDF-кусок ≤ 20 МБ (base64 ≈ 27 МБ) и ≤ 100 стр. Всего страниц ограничиваем,
# чтобы не разориться на очень толстых сканах (можно поднять).
_MAX_PDF_BYTES = 20 * 1024 * 1024
_MAX_PDF_PAGES = 100
_MAX_TOTAL_PAGES = 300


def _split_pdf(data: bytes) -> tuple[list[bytes], int, bool]:
    """Разбить PDF на куски ≤ лимитов Claude. -> (куски, всего_страниц, обрезан_ли)."""
    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    used = min(total, _MAX_TOTAL_PAGES)
    truncated = total > _MAX_TOTAL_PAGES
    if used <= _MAX_PDF_PAGES and len(data) <= _MAX_PDF_BYTES and not truncated:
        return [data], used, False

    n_chunks = max(math.ceil(len(data) / _MAX_PDF_BYTES), math.ceil(used / _MAX_PDF_PAGES))
    per = math.ceil(used / n_chunks)
    chunks: list[bytes] = []
    for start in range(0, used, per):
        writer = PdfWriter()
        for i in range(start, min(start + per, used)):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        chunks.append(buf.getvalue())
    return chunks, used, truncated


class DocumentAnalyzer:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def _call(self, content: Any, label: str, extract_card: bool = False) -> dict[str, Any]:
        """Один structured-запрос к Claude по BRIEF_SCHEMA (кэш системного промпта).

        extract_card=True (закрытые тендеры) добавляет в схему объект card — поля
        карточки, извлечённые из ТЗ; системный промпт не меняется (кэш сохраняется).
        """
        schema = brief_schema_with_card() if extract_card else BRIEF_SCHEMA
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if resp.stop_reason == "max_tokens":
            raise RuntimeError(
                "Ответ ИИ обрезан лимитом токенов — бриф неполный, попробуйте ещё раз"
            )
        raw = "".join(getattr(block, "text", "") for block in resp.content)
        data: dict[str, Any] = json.loads(raw)
        u = resp.usage
        log.info(
            label,
            model=self._model,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read=getattr(u, "cache_read_input_tokens", 0),
        )
        return data

    def analyze(self, text: str, context: str = "", extract_card: bool = False) -> dict[str, Any]:
        """Разобрать ТЕКСТ ТЗ (из PDF с текстовым слоем / DOCX / XLSX) -> бриф по BRIEF_SCHEMA."""
        return self._call(
            build_message(context, text, extract_card), "document_analyzed", extract_card
        )

    def analyze_pdf(
        self, pdf_bytes: bytes, context: str = "", extract_card: bool = False
    ) -> dict[str, Any]:
        """Разобрать СКАН-PDF нативным движком Claude (большой файл — по частям + слияние)."""
        chunks, pages, truncated = _split_pdf(pdf_bytes)
        briefs = [
            self._analyze_pdf_chunk(chunk, context, i, len(chunks), extract_card)
            for i, chunk in enumerate(chunks)
        ]
        if len(briefs) == 1:
            result = briefs[0]
        else:
            result = self._merge_briefs(briefs, context, extract_card)
        if truncated:
            findings = result.setdefault("findings", [])
            if isinstance(findings, list):
                findings.insert(
                    0,
                    {
                        "title": "Разобрана часть документа",
                        "detail": f"ТЗ очень большое — разобраны первые {pages} страниц.",
                        "quote": "",
                        "page": "",
                    },
                )
        return result

    def _analyze_pdf_chunk(
        self, pdf_bytes: bytes, context: str, idx: int, total: int, extract_card: bool = False
    ) -> dict[str, Any]:
        note = f"(Часть {idx + 1} из {total} одного ТЗ.)" if total > 1 else ""
        b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
        content: list[dict[str, Any]] = [
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
            },
            {"type": "text", "text": build_pdf_message(context, note, extract_card)},
        ]
        return self._call(content, "document_pdf_chunk_analyzed", extract_card)

    def _merge_briefs(
        self, briefs: list[dict[str, Any]], context: str, extract_card: bool = False
    ) -> dict[str, Any]:
        briefs_json = [json.dumps(b, ensure_ascii=False) for b in briefs]
        return self._call(
            build_merge_message(briefs_json, context, extract_card),
            "document_briefs_merged",
            extract_card,
        )


def create_document_analyzer(settings: Settings | None = None) -> DocumentAnalyzer:
    """Собрать анализатор из конфигурации. Без ключа -> MissingApiKeyError."""
    cfg = settings or get_settings()
    if not cfg.anthropic_api_key:
        raise MissingApiKeyError("Нужен ANTHROPIC_API_KEY для разбора ТЗ (Claude)")
    return DocumentAnalyzer(api_key=cfg.anthropic_api_key, model=cfg.claude_model)
