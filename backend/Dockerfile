FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock alembic.ini ./
COPY app ./app
COPY migrations ./migrations

RUN pip install --no-cache-dir uv==0.12.5 \
    && uv sync --frozen --no-dev --no-editable

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
