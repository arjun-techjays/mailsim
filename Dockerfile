FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev
ENTRYPOINT ["uv", "run", "--no-dev", "sim"]
