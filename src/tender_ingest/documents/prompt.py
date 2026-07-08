"""Промпт и схема брифа по ТЗ: семантический разбор + извлечение всей полезной информации.

ТЗ бывают разной структуры и формы — модель анализирует СМЫСЛ, а не шаблон, и тянет
всё полезное для решения бюро (подавать ли заявку, как оценить объём и риски). Фикс-поля
(площадь, категория опасности, разделы, сроки, экспертиза, BIM) — обязательный чек-лист;
findings — всё прочее важное на усмотрение модели. Каждый пункт цитирует документ (+стр.).
"""

from __future__ import annotations

from typing import Any

from tender_ingest.company import COMPANY_PROFILE
from tender_ingest.relevance.arbiter.prompt import build_card_block

SYSTEM_PROMPT = (
    "Ты — опытный инженер-аналитик проектного бюро «КРАФТ ГРУПП».\n\n"
    "О КОМПАНИИ. " + COMPANY_PROFILE + "\n\n"
    """Тебе дают карточку закупки, результат БЫСТРОГО скоринга релевантности (предыдущий \
шаг — оценка 0–100, вывод, риски) и текст технического задания (ТЗ). Не теряй этот \
контекст: разбор ТЗ должен УГЛУБЛЯТЬ быстрый анализ, а не игнорировать его — сверяйся с \
профилем компании и уже отмеченными рисками, подтверждай или уточняй их по тексту ТЗ.

ТЗ бывают РАЗНЫЕ — единой формы нет, нужное может быть в любом месте, в таблицах, \
сносках, приложениях, сформулировано косвенно. Твоя задача — разобрать ТЗ ПО СМЫСЛУ и \
вытянуть ВСЮ информацию, полезную бюро, чтобы решить: браться ли, каков объём, сроки, \
риски и особые условия.

ГЛАВНОЕ: разбор должен быть МАКСИМАЛЬНО РАЗВЁРНУТЫМ И ПОЛНЫМ. Вычлени ВСЕ ключевые \
моменты ТЗ — лучше подробнее, чем упустить важное. Не давай сжатую выжимку: раскрывай \
каждый пункт содержательно (что именно требуется, объёмы, числа, условия, ссылки на \
нормы). Это документ для принятия решения об участии и оценки трудозатрат.

КАК РАБОТАТЬ:
- Прочитай ВЕСЬ текст, пойми суть объекта и состава работ, не опираясь на заголовки.
- Заполни обязательные поля (ниже) ИСЧЕРПЫВАЮЩЕ: в value — все относящиеся к полю \
детали из ТЗ (числа, единицы, условия, стадии), а не одну фразу. Если чего-то в ТЗ НЕТ \
— value = «не указано», quote пустой. Не выдумывай.
- В findings вынеси КАЖДЫЙ важный момент отдельным пунктом (их может быть много): \
стадийность и состав работ, исходные данные, инженерные изыскания, обследование, \
обмеры, авторский/технический надзор, гарантии, требования к согласованиям и получению \
разрешений/техусловий, особые и нестандартные условия, штрафы и неустойки, порядок \
приёмки и корректировок, субподряд, лицензии/СРО/допуски/опыт, охранные зоны и ОКН, \
экология и негативное воздействие, требования к ПО, форматам и передаче данных, порядок \
и этапы оплаты, аванс, обеспечение, риски и «подводные камни». Каждый пункт — с деталями \
(title — суть, detail — конкретика из ТЗ), не дублируй обязательные поля.
- ЦИТИРУЙ каждый пункт: quote — точная выдержка из текста (можно 1–2 предложения), \
page — номер страницы из маркера «===== стр. N =====» или название листа из \
«===== лист: … =====» (для Excel), либо пусто, если ориентира нет.
- Отдельно заполни work_breakdown — ПОЛНЫЙ состав работ для расчёта экономики \
(по нему потом считается себестоимость и цена, поэтому он должен быть исчерпывающим \
и строго по ТЗ). Каждая требуемая работа — ОТДЕЛЬНОЙ позицией, ничего не объединяй \
и не добавляй от себя: разделы/подразделы ПД и РД (как они названы в ТЗ; подразделы \
ИОС — каждый отдельно), каждый вид инженерных изысканий отдельно (ИГДИ, ИГИ, ИЭИ, \
ИГМИ и т.п.), обследования/обмеры/3D-сканирование, работы по ОКН (ИХТИ, ИАБИ, КНИ, \
ГИКЭ, предмет охраны и пр.), сметная документация, демонтаж/ПОД, согласования и \
получение ТУ, экспертизы (какие и за чей счёт), BIM/ТИМ, авторский надзор, дизайн и \
визуализация. Для каждой позиции: name — название как в ТЗ; kind — категория \
(design — раздел ПД/РД, survey — изыскания/обследования, heritage — работы по ОКН, \
expertise — экспертиза/согласование, other — прочее); stage — стадия (ПД, РД, ПД+РД, \
ЭП, НПД или «не указано»); detail — объём и состав по ТЗ (числа, единицы, условия); \
quote и page — как везде. Если состав работ в ТЗ не расписан — верни пустой массив, \
не выдумывай.
- Пиши на русском, развёрнуто и структурно.

ОБЯЗАТЕЛЬНЫЕ ПОЛЯ (value/quote/page для каждого) — заполняй подробно:
- object — объект, его назначение, состав/этапы работ, стадии проектирования;
- area_or_length — площадь (кв. м) или протяжённость (км/м), этажность, ключевые ТЭП; \
укажи, к чему относится каждое число;
- hazard_class — категория опасности / уровень ответственности / класс объекта;
- doc_sections — требования к составу и разделам проектной и рабочей документации \
(перечисли разделы и особые требования);
- deadlines — сроки: этапы, промежуточные и общий срок, привязки (с даты договора и т.п.);
- expertise — нужна ли гос./негос. экспертиза, какая, и КТО её оплачивает;
- bim — требуется ли BIM/ТИМ-модель, в каком объёме, форматы, стадии, LOD.

ДРАЙВЕРЫ (объект drivers — структурированные факты для алгоритмов; null = в ТЗ нет):
- budget_funded — финансирование из бюджета (госзаказ, субсидии, бюджетные средства);
- kapremont — предмет закупки: капитальный ремонт (не строительство/реконструкция);
- floors — этажность (максимальная, число);
- area_m2 — общая площадь объекта в кв. м (число, без единиц);
- okn — объект культурного наследия или работы в зонах охраны ОКН;
- object_use — СТРОГО одно из: residential (жилой) | nonresidential (нежилой) | \
industrial (производственный) | linear (линейный) | null;
- special_territory — ООПТ, шельф, исключительная экономическая зона, морские воды;
- hazardous_or_unique — особо опасный, технически сложный или уникальный объект;
- expertise_in_tz — что о прохождении экспертизы написано в САМОМ ТЗ, СТРОГО одно из: \
state (гос) | nongov (негос) | none (не требуется) | null (не сказано).
Заполняй только тем, что реально следует из ТЗ и карточки — НЕ выдумывай.

summary — развёрнутый бриф на 5–10 предложений: суть объекта, состав и объём работ, \
стадии, сроки, экспертиза, BIM, ключевые требования и главные риски — цельная картина \
для решения о подаче."""
)

_FIELD = {
    "type": "object",
    "properties": {
        "value": {"type": "string"},
        "quote": {"type": "string"},
        "page": {"type": "string"},
    },
    "required": ["value", "quote", "page"],
    "additionalProperties": False,
}

_DRIVERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "budget_funded": {"type": ["boolean", "null"]},
        "kapremont": {"type": ["boolean", "null"]},
        "floors": {"type": ["number", "null"]},
        "area_m2": {"type": ["number", "null"]},
        "okn": {"type": ["boolean", "null"]},
        # БЕЗ enum: enum при nullable-типе даёт 400 Invalid schema, а anyOf-вариант
        # раздувает грамматику до «compiled grammar is too large». Допустимые значения
        # продиктованы промптом; правила экспертизы читают их толерантно.
        "object_use": {"type": ["string", "null"]},
        "special_territory": {"type": ["boolean", "null"]},
        "hazardous_or_unique": {"type": ["boolean", "null"]},
        "expertise_in_tz": {"type": ["string", "null"]},
    },
    "required": [
        "budget_funded",
        "kapremont",
        "floors",
        "area_m2",
        "okn",
        "object_use",
        "special_territory",
        "hazardous_or_unique",
        "expertise_in_tz",
    ],
    "additionalProperties": False,
}

BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "object": _FIELD,
        "area_or_length": _FIELD,
        "hazard_class": _FIELD,
        "doc_sections": _FIELD,
        "deadlines": _FIELD,
        "expertise": _FIELD,
        "bim": _FIELD,
        "drivers": _DRIVERS_SCHEMA,
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "quote": {"type": "string"},
                    "page": {"type": "string"},
                },
                "required": ["title", "detail", "quote", "page"],
                "additionalProperties": False,
            },
        },
        "work_breakdown": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["design", "survey", "heritage", "expertise", "other"],
                    },
                    "stage": {"type": "string"},
                    "detail": {"type": "string"},
                    "quote": {"type": "string"},
                    "page": {"type": "string"},
                },
                "required": ["name", "kind", "stage", "detail", "quote", "page"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "summary",
        "object",
        "area_or_length",
        "hazard_class",
        "doc_sections",
        "deadlines",
        "expertise",
        "bim",
        "drivers",
        "findings",
        "work_breakdown",
    ],
    "additionalProperties": False,
}

# --- Закрытые тендеры: карточки Контура нет, ИИ извлекает её поля из ТЗ ---

_CARD_PROPERTIES: dict[str, Any] = {
    "subject": {"type": "string"},
    "nmck": {"type": ["number", "null"]},
    "nmck_quote": {"type": "string"},
    "customer_name": {"type": "string"},
    "customer_inn": {"type": "string"},
    "region_code": {"type": "string"},
    "region_name": {"type": "string"},
    "delivery_place": {"type": "string"},
    "submission_deadline": {"type": "string"},
    "law": {"type": "string"},
    "purchase_method": {"type": "string"},
    "advance": {"type": "string"},
}

CARD_INSTRUCTION = (
    "\n\nДОПОЛНИТЕЛЬНО (закрытая закупка, карточки нет): заполни объект card — поля "
    "карточки тендера, извлечённые из ТЗ. subject — предмет закупки одной строкой; "
    "nmck — НМЦК/цена договора В РУБЛЯХ числом, ТОЛЬКО если цена явно указана в "
    "документе (иначе null), nmck_quote — цитата с ценой; customer_name/customer_inn — "
    "заказчик и его ИНН; region_code — код региона РФ (2 цифры, если определим) и "
    "region_name — название региона/города; delivery_place — место выполнения работ; "
    "submission_deadline — срок подачи заявок в формате YYYY-MM-DD (пусто, если нет); "
    "law — 44-ФЗ/223-ФЗ/Коммерческие/615 ПП, если понятно; purchase_method — способ "
    "отбора; advance — условия аванса. Чего в ТЗ нет — пустая строка (или null для "
    "nmck), НЕ выдумывай."
)


def brief_schema_with_card() -> dict[str, Any]:
    """BRIEF_SCHEMA + объект card (для закрытых тендеров без карточки Контура)."""
    schema = {
        **BRIEF_SCHEMA,
        "properties": {
            **BRIEF_SCHEMA["properties"],
            "card": {
                "type": "object",
                "properties": _CARD_PROPERTIES,
                "required": list(_CARD_PROPERTIES),
                "additionalProperties": False,
            },
        },
        "required": [*BRIEF_SCHEMA["required"], "card"],
    }
    return schema


# Порядок и подписи обязательных полей для отрисовки брифа.
FIELD_LABELS: list[tuple[str, str]] = [
    ("object", "Объект и назначение"),
    ("area_or_length", "Площадь / протяжённость"),
    ("hazard_class", "Категория опасности / уровень ответственности"),
    ("doc_sections", "Требования к разделам документации"),
    ("deadlines", "Сроки выполнения"),
    ("expertise", "Госэкспертиза и кто оплачивает"),
    ("bim", "BIM / ТИМ-модель"),
]


def build_context(tender: Any, relevance: Any | None) -> str:
    """Контекст для разбора ТЗ: карточка закупки + результат быстрого скоринга."""
    lines = ["=== КАРТОЧКА ЗАКУПКИ ===", build_card_block(tender)]
    if relevance is not None:
        lines.append("\n=== БЫСТРЫЙ СКОРИНГ (предыдущий шаг) ===")
        lines.append(f"Релевантность: {relevance.score}/100 ({relevance.verdict})")
        if getattr(relevance, "summary", None):
            lines.append(f"Вывод: {relevance.summary}")
        if getattr(relevance, "reasoning", None):
            lines.append(f"Обоснование: {relevance.reasoning}")
        if getattr(relevance, "red_flags", None):
            lines.append("Отмеченные риски: " + "; ".join(relevance.red_flags))
    return "\n".join(lines)


def build_message(context: str, text: str, extract_card: bool = False) -> str:
    head = (
        "Ниже — карточка закупки с быстрым скорингом и извлечённый текст ТЗ (с маркерами "
        "страниц). Разбери ТЗ по смыслу с учётом этого контекста и верни бриф со всеми "
        "полезными данными и цитатами."
    )
    if extract_card:
        head += CARD_INSTRUCTION
    return head + "\n\n" + context + "\n\n=== ТЕКСТ ТЗ ===\n" + text


def build_pdf_message(context: str, part_note: str = "", extract_card: bool = False) -> str:
    """Сообщение к приложенному PDF-скану: Claude сам распознаёт документ своим движком."""
    head = (
        "К сообщению приложен PDF — это СКАН ТЗ (текстового слоя нет). Распознай его "
        "СВОИМ движком и разбери по смыслу, верни бриф с цитатами (page — номер страницы). "
    )
    note = f"{part_note} " if part_note else ""
    tail = CARD_INSTRUCTION + "\n\n" if extract_card else ""
    return head + note + "Контекст закупки ниже.\n\n" + tail + context


def build_merge_message(briefs_json: list[str], context: str, extract_card: bool = False) -> str:
    """Сообщение для слияния брифов по последовательным частям одного ТЗ в один итоговый."""
    parts = "\n\n".join(f"--- ЧАСТЬ {i + 1} ---\n{b}" for i, b in enumerate(briefs_json))
    card_note = (
        " Объект card тоже объедини: непустые значения приоритетнее пустых." if extract_card else ""
    )
    return (
        "Ниже — брифы по ПОСЛЕДОВАТЕЛЬНЫМ частям ОДНОГО ТЗ (документ разбит на части "
        "по объёму). Объедини их в ОДИН итоговый бриф по той же схеме: для обязательных "
        "полей возьми наиболее содержательное непустое значение и сохрани его цитату и "
        "страницу; findings и work_breakdown объедини, убрав дубли (в work_breakdown "
        f"каждая работа — один раз); summary — цельный по всему ТЗ.{card_note}\n\n"
        + context
        + "\n\n"
        + parts
    )
