FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    CITI_API_HOST=0.0.0.0

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --extra api --extra agents --no-install-project
COPY src ./src
COPY ontology ./ontology
RUN uv sync --locked --no-dev --extra api --extra agents

CMD ["citi-api"]
