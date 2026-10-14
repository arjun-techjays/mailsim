# mailsim - project conventions

Deterministic email simulation engine. Source of truth: `email-agent-testing-simulator-spec.md`.
Phase 1 (core engine, no HTTP) only; Gmail/Graph/IMAP/SMTP/MCP/REST/LLM are later phases.

## Commands (uv, Python 3.12)
- Setup: `uv sync` then `docker compose up -d postgres` (PostgreSQL 16 on 127.0.0.1:54329)
- Tests + coverage (bar 95%, branch): `uv run pytest --cov=mailsim --cov-branch`
- Lint/format: `uv run ruff check . && uv run ruff format --check .`
- Types: `uv run mypy`
- Layering: `uv run lint-imports`
- Migrations: `uv run alembic upgrade head` (model/migration drift is tested)

## Rules
- Layering (enforced by import-linter): domain <- mime/threads/providers <- store <- scenario <- engine <- cli.
- Never add SMTP/socket/HTTP client imports to `src/` (ruff banned-api). External delivery must be impossible.
- Inside `engine/` time comes only from the virtual clock and randomness from the seeded `IdGenerator`.
- State changes go through events (append-only `events` table) and the projector; never write projection tables directly.
- Scenario versions and events are immutable (DB triggers). Use `yaml.safe_load` only.
- Tests: real PostgreSQL (no SQLite). Each test uses its own tenant/run; never truncate events.

## Local quirk (macOS)
If `import mailsim` fails after `uv sync`/`uv run`, the editable `.pth` got the hidden flag:
`chflags nohidden .venv/lib/python3.12/site-packages/*.pth`. pytest is unaffected (`pythonpath`).
