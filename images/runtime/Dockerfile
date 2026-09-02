# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY agents ./agents
COPY services ./services
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir .

FROM python:3.12-slim
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
RUN groupadd --gid 65532 runtime && useradd --uid 65532 --gid runtime --no-create-home runtime
USER 65532:65532
EXPOSE 8001 8002 8100 8200 8201
ENTRYPOINT ["/opt/venv/bin/python", "-m", "platform_runtime.main"]
