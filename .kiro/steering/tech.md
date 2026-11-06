---
inclusion: always
---

# Technology

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic v2, httpx; pure domain logic uses stdlib dataclasses |
| Frontend | React + TypeScript (strict) + Vite |
| Data | Postgres with pgvector and full-text search; hybrid retrieval fused by RRF |
| Local DB | Postgres + pgvector in Docker; pgAdmin for inspection |
| Models | `ModelRouter` maps task to client: Claude for `generate` and `repair`; small open models (Hugging Face) for `rewrite` and `entail`; an `Embedder` port for vectors |
| Hosting | Local-first; deploy frontend and API to Vercel Hobby, managed Postgres for the deployed demo |
| Quality | ruff, mypy `--strict`, pytest, Hypothesis, anyio; Vitest, Playwright, axe on the frontend |

## Commands (Windows PowerShell, from the repo root with the venv active)
- Install: `pip install -e "backend[dev]"`
- Lint and format: `cd backend; ruff check --fix .; ruff format .`
- Types: `cd backend; mypy`
- Tests: `cd backend; pytest` (long property runs: `$env:HYPOTHESIS_PROFILE="ci"; pytest`)

## Rules
- Configuration comes from `Settings.from_env`; never read `os.environ` elsewhere.
- Secrets are `SecretStr`; never log or echo secrets or question text (log lengths and ids).
- Async code takes injected clock, sleep and rng so tests are deterministic. No real network in tests.
- Model ids and provider URLs are configuration, never hard-coded.
- Never import a provider SDK or HTTP client outside the adapter packages.
- Pure logic stays framework-free so it can be property-tested.
- Every external dependency sits behind a port so it can be swapped without touching services.
