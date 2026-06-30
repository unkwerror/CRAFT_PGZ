"""Скоринг релевантности по правилам (CLAUDE.md Фаза 1).

Логика вердикта (recall-first — выгрузка Контура уже отфильтрована под профиль):
- НЕТ проектного сигнала (pos == 0) -> noise;
- СИЛЬНЫЙ проектный сигнал (pos >= STRONG_SIGNAL) и он перевешивает анти -> relevant;
- слабый/спорный сигнал, либо анти перебивает -> maybe (отдаём LLM-арбитру).

Важно: анти-слова (поставка/аренда/ГСМ/медоборудование...) описывают часто ОБЪЕКТ,
который бюро проектирует (склад ГСМ, медцентр, столовая). Поэтому сильный проектный
сигнал НЕ должен обнуляться природой объекта — анти решает только при слабом/нулевом
проектном сигнале.

score = clamp(pos − anti, 0..100) — для ранжирования и отображения.
Всё прозрачно: возвращаем какие слова сработали — это «причина» для аналитика.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

from tender_ingest.relevance.profile import ANTI, PROFILE, Keyword

# Порог «сильного» проектного сигнала: вес одного явного профильного слова
# (проект/изыскания/реставрация/благоустройство = 30..40).
STRONG_SIGNAL = 30

# Пороги нужны для семантики отображаемого score (ранжирование/фильтры UI).
RELEVANT_THRESHOLD = 35
NOISE_THRESHOLD = 5

Verdict = Literal["relevant", "maybe", "noise"]


class Match(NamedTuple):
    label: str
    weight: int


class RuleResult(NamedTuple):
    score: int
    verdict: Verdict
    matched: list[Match]  # сработавший профиль
    anti_matched: list[Match]  # сработавшие анти-слова


def _find(text: str, keywords: tuple[Keyword, ...]) -> list[Match]:
    out: list[Match] = []
    for kw in keywords:
        if kw.pattern.search(text):
            out.append(Match(kw.label, kw.weight))
    return out


def score_subject(subject: str | None) -> RuleResult:
    """Оценить предмет закупки. Пустой текст -> noise со score 0."""
    text = (subject or "").strip()
    if not text:
        return RuleResult(0, "noise", [], [])

    matched = _find(text, PROFILE)
    anti_matched = _find(text, ANTI)

    pos = sum(m.weight for m in matched)
    anti = sum(m.weight for m in anti_matched)
    score = max(0, min(100, pos - anti))

    verdict = _verdict(pos, anti)
    return RuleResult(score, verdict, matched, anti_matched)


def _verdict(pos: int, anti: int) -> Verdict:
    if pos == 0:
        return "noise"  # проектного сигнала нет вовсе
    if pos >= STRONG_SIGNAL and pos > anti:
        return "relevant"  # явный проектный сигнал, анти не доминирует
    return "maybe"  # слабый сигнал или анти перебивает -> арбитру
