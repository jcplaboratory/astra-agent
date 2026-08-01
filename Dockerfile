FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps ./apps
COPY packages ./packages
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --all-packages --no-dev

ENV PATH="/app/.venv/bin:$PATH"
CMD ["uvicorn", "astra_agent.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
