FROM python:3.12-slim AS test-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HOME=/models

RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /service

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./

RUN groupadd --gid 10001 stt \
    && useradd --uid 10001 --gid stt --no-create-home --shell /usr/sbin/nologin stt \
    && mkdir -p /models \
    && chown -R stt:stt /service /models

FROM test-base AS test

USER root
COPY requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements-test.txt
COPY tests ./tests
CMD ["pytest", "-q", "tests"]

FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HOME=/models \
    DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install --no-install-recommends -y python3 python3-venv libgomp1 \
    && python3 -m venv /opt/venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /service

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./

RUN groupadd --gid 10001 stt \
    && useradd --uid 10001 --gid stt --no-create-home --shell /usr/sbin/nologin stt \
    && mkdir -p /models \
    && chown -R stt:stt /service /models

USER stt
EXPOSE 8766
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8766", "--no-access-log"]
