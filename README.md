# tender-ingest (Фазы 0–1)

Система анализа закупок для архитектурного бюро. Реализовано:

- **Фаза 0 — приём данных.** Источник — Excel-выгрузка из Контур.Закупок
  (пользователь жмёт «Выгрузить в Excel»). Парсим, нормализуем, кладём в PostgreSQL.
- **Фаза 1 — релевантность.** Оценка каждой закупки профилю бюро (проектные/
  изыскательские работы, реставрация ОКН, благоустройство): правила + LLM-арбитр.

```
Контур → .xlsx → ExcelSource → нормализация → Postgres → analysis_queue
                                                              ↓
                                  правила (score) ──relevant/noise──→ tender_relevance
                                       └─ maybe ─→ LLM-арбитр (mock|yandex) ─→ tender_relevance
```

Границы фаз — в [`CLAUDE.md`](./CLAUDE.md). Архитектура (адаптеры источника и
арбитра, скоринг) — в [`docs/architecture.md`](./docs/architecture.md).

## Архитектура

Два сменных адаптера за общими интерфейсами — чтобы менять источник и LLM, не
переписывая остальное:

- `sources/` — `SourceAdapter.fetch() -> Iterable[RawTender]`; v1 — `ExcelSource`.
  Pipeline работает только с `RawTender`, про Excel не знает.
- `relevance/arbiter/` — `RelevanceArbiter.decide(subject) -> ArbiterVerdict`;
  `MockArbiter` (по умолчанию, без ключа) и `YandexGPTArbiter` (prod).

Модули:

- `normalize.py` — даты (Excel-serial + guard), обеспечения/аванс (₽/%), регион
  (код + имя), номер строкой.
- `relevance/profile.py` — ключевые слова профиля (+вес) и анти-слова (−вес);
  **аналитик правит этот файл**.
- `relevance/rules.py` — скоринг по тексту предмета; вердикт relevant / maybe / noise.
- `relevance/scorer.py` — оркестрация: правила → (maybe) арбитр → `tender_relevance`.
- `db/` — модели, движок сессий, репозитории (upsert по номеру, очередь, журнал).

## Быстрый старт

```bash
cp .env.example .env           # источник v1 без секретов; арбитр по умолчанию mock
docker compose up -d postgres  # поднять БД

python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

alembic upgrade head           # схема (5 таблиц)
```

## Использование

```bash
# 1) загрузить выгрузку
tender ingest --file "tests/fixtures/Контур.Закупки_30.06.2026.xlsx"
# → rows=160 upserted=160 failures=0 status=success

# 2) оценить релевантность всего, что в очереди
tender score
# → total=160 relevant=147 maybe_to_llm=4 noise=13
```

Обе команды **идемпотентны**: повторная загрузка того же файла не плодит дубли
(upsert по `reestr_number`); `score` берёт только закупки со статусом `pending` в
очереди и переводит их в `scored`.

### LLM-арбитр

По умолчанию `ARBITER_PROVIDER=mock` — детерминированная заглушка без ключа и сети
(годится для разработки и тестов). Для боевого арбитра на YandexGPT:

```env
ARBITER_PROVIDER=yandex
YANDEX_API_KEY=...
YANDEX_FOLDER_ID=...
```

Арбитр вызывается **только** на спорных (maybe) закупках — это экономит вызовы.
При сбое сети закупка не теряется (остаётся «не релевантной» с пометкой ошибки).

## Что лежит в БД

| Таблица | Назначение |
|---|---|
| `tenders` | нормализованная закупка (PK `reestr_number`); обеспечения и результат — JSONB |
| `tender_raw` | сырьё строки целиком (JSONB) — ничего не теряем при смене формата |
| `analysis_queue` | очередь: `pending` → `scored` |
| `tender_relevance` | `score`, `verdict`, `decided_by` (rules/mock/yandex), `matched`, `llm_reason` |
| `ingestion_runs` | журнал прогонов: файл, строк, upsert, ошибок, статус |

Прозрачность: по каждой закупке видно `score`, какие слова сработали (`matched`/
`anti_matched`) и кто принял решение (`decided_by` + `llm_reason`).

## Контроль качества

```bash
ruff check . && ruff format --check .
mypy
pytest                          # нормализация, парсер на фикстуре, правила, арбитр
```

## Логи

`structlog` в JSON: `ingest_done` со счётчиками, `score_done` с распределением
вердиктов, `tender_upsert_failed` по плохим строкам (строка не валит весь файл —
SAVEPOINT на строку).

## Статус

| Фаза | Что | Статус |
|---|---|---|
| 0 | Приём Excel: парсер, нормализация, БД, идемпотентный upsert, CLI `ingest` | ✅ |
| 1 | Релевантность: правила + LLM-арбитр (mock/yandex), CLI `score` | ✅ |
| — | Карточка закупки (LLM), скоринг участия | следующая фаза |
| — | Веб-интерфейс (2–5 пользователей бюро, общая БД) | следующая фаза |
| — | Источники Email/DaMIA/ЕИС, документы (ТЗ) | следующие фазы |
