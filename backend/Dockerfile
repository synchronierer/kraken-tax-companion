FROM python:3.12.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir -p /data /logs /exports \
    && chown -R app:app /app /data /logs /exports

COPY pyproject.toml README.md alembic.ini ./
COPY app ./app
COPY alembic ./alembic
COPY --chmod=755 docker-entrypoint.sh ./docker-entrypoint.sh

RUN pip install --no-cache-dir .

USER app

EXPOSE 8000

CMD ["./docker-entrypoint.sh"]
