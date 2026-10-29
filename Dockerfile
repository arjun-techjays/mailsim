FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev
# run the installed venv directly: `uv run` would re-sync at startup, which needs PyPI and
# fails behind the egress block (docs/egress.md)
ENTRYPOINT ["/app/.venv/bin/sim"]
