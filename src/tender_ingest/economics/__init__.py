"""Экономика тендера: два дополняющих контура.

1. Расчёт цены по базе «Экономики» (canon/xlsx/store/engine/proposer/reviewer/service):
   детерминированная таблица себестоимости по медианам долей проектов-аналогов.
2. ИИ-экономист (advisor/cases/prompt): RAG-рекомендация по цене и участию на корпусе
   кейсов бюро (участие, исходы, заметки, фидбек) — docs/analytics-brief.md.
"""

from tender_ingest.economics.advisor import EconomicsAdvisor, create_economics_advisor
from tender_ingest.economics.cases import CaseCorpus, build_case_corpus

__all__ = ["CaseCorpus", "EconomicsAdvisor", "build_case_corpus", "create_economics_advisor"]
