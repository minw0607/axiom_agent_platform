# axiom platform image: the /v1 API and the worker share it (different commands).
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    AXIOM_CONFIG_DIR=/app/config \
    AXIOM_DATA_DIR=/app/data \
    AXIOM_PROMPTS_DIR=/app/prompts

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev
COPY config ./config
COPY prompts ./prompts
COPY data/synthetic ./data/synthetic
COPY data/recorded ./data/recorded
RUN useradd --create-home --uid 10001 axiom
USER axiom

EXPOSE 8000
CMD ["python", "-m", "axiom.api"]
