FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --upgrade pip && pip install .

# По умолчанию — справка CLI. Реальный запуск: tender ingest --file <path.xlsx>.
CMD ["tender", "--help"]
