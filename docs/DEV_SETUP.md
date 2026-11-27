# Development setup checklist

Everything needed on your machine before coding starts. Tick as you go.
Versions are minimums; newer is fine unless noted.

## Required tools

| Done | Tool | Version | Why | Check |
|---|---|---|---|---|
| [ ] | Git | 2.40+ | Version control | `git --version` |
| [ ] | Python | 3.11+ (3.12 recommended) | Backend (`requires-python >=3.11`) | `python --version` |
| [ ] | uv (or pip + venv) | latest | Fast installs and lockfile | `uv --version` |
| [ ] | Node.js LTS | 20 or 22 | Frontend build, MCP Inspector | `node --version` |
| [ ] | npm (ships with Node) or pnpm | 10+ | Frontend packages | `npm --version` |
| [ ] | Docker Desktop (or Docker Engine) | recent | Runs Postgres with pgvector without a native install | `docker --version` |
| [ ] | PostgreSQL client tools (`psql`) | 16 or 17 | Quick SQL from the terminal | `psql --version` |
| [ ] | pgAdmin 4 | latest | GUI for the database | open the app |
| [ ] | Kiro IDE | latest | Specs, steering, hooks, agents, MCP | open and sign in |
| [ ] | GitHub CLI (`gh`) | latest | Create the public repo, manage PRs | `gh auth status` |
| [ ] | Vercel CLI | latest | Deploy in week 5 (`npm i -g vercel`) | `vercel --version` |

## Database: the simplest path

Use Docker for Postgres, because pgvector is the awkward part to install natively (especially on Windows).
We will add a `docker-compose.yml` using a Postgres image that includes pgvector. You then connect pgAdmin to
`127.0.0.1` on the port we configure. If you prefer a native Postgres install, you must also install the
`pgvector` extension for your Postgres version and run `CREATE EXTENSION vector;`.

pgAdmin setup once the container runs: Add New Server, host `127.0.0.1`, port from compose,
user and password from your `.env`.

## Accounts and keys (create now, store only in `.env`, never commit)

| Done | Item | Needed for |
|---|---|---|
| [ ] | Anthropic API key | Answer generation and repair |
| [ ] | Hugging Face account + access token (read) | Embeddings and small models via hosted inference |
| [ ] | GitHub fine-grained personal access token, read-only on the synthetic repo | Live ingestion demo |
| [ ] | Public GitHub repo for the project | Submission requirement |
| [ ] | Vercel account (Hobby is free) | Deployment |
| [ ] | Managed Postgres free tier (Neon or Supabase) | Deployed demo database |
| [ ] | YouTube account | Demo video upload |
| [ ] | Kiro free credits approval | Kiro usage |

## Python packages (installed by `pip install -e "backend[dev]"`)

fastapi, httpx, pydantic, pytest, pytest-cov, hypothesis, anyio, ruff, mypy. Later additions, decided in specs:
psycopg (v3) with a pool, pgvector client, the official MCP Python SDK, an SSE helper.

## Frontend packages (added when the UI is scaffolded)

React, Vite, TypeScript, Vitest, Playwright (plus browsers), axe, fast-check, a charting approach chosen in the UI spec.

## Optional but useful

| Tool | Why |
|---|---|
| MCP Inspector (`npx @modelcontextprotocol/inspector`) | Manually exercise the MCP server |
| `make` or `just` | Short commands for lint, test, run |
| DBeaver | Alternative to pgAdmin |
| ffmpeg and a screen recorder (OBS) | Demo video |

## Verification script (to be added)

A `scripts/doctor.py` will check tool versions, database connectivity, extension presence, and that required
environment variables are set (without printing their values).

## What to send me when done

Your OS (Windows, macOS, Linux), the output of the "Check" commands for the tools you installed, and which
accounts and keys are ready (yes or no only; never paste a key).

## Evidence ingestion

Run both commands from `backend/` with the venv active. The first applies the schema migrations, the second
ingests the packaged Ledgerline sample bundle:

```powershell
python -m recromancer.db.migrate
python -m recromancer.ingest_cli src\recromancer\ingest\fixtures\ledgerline
```

The ingest command prints one line, `items=<n> chunks=<n> unchanged=<n> absent=<n>`. A second run over the same
bundle prints `chunks=0`, because nothing changed.

Until the real embedding adapter lands (Group 4), the CLI without an injected embedder exits 1 with
`no embedder is configured`. This is intentional: ingestion fails closed instead of storing chunks without vectors.

### pgvector version check

The HNSW index needs pgvector 0.5.0 or later. Check the version the container offers:

```powershell
docker exec recromancer-db-1 psql -U recromancer -d recromancer -At -c "SELECT name, default_version FROM pg_available_extensions WHERE name = 'vector';"
```

Recorded output: `vector|0.8.7`.

### Embedding cache directory

By default `RECROMANCER_EMBEDDING_CACHE_DIR` is unset, and fastembed then uses its own cache under the temp
directory, which Windows may clean. Set a stable absolute path instead, for example
`C:\Users\<you>\.cache\recromancer`. In PowerShell:

```powershell
$env:RECROMANCER_EMBEDDING_CACHE_DIR = "C:\Users\<you>\.cache\recromancer"
```

or put the same key in `.env`. A related setting, `RECROMANCER_EMBEDDING_OFFLINE`, is described with the
embedding adapter in Group 4.

### Venv check

```powershell
cd backend; python -c "import onnxruntime"
```

This must exit 0. If it crashes with an access violation, rebuild `.venv` from a python.org CPython 3.11. The
miniconda base ships an older C++ runtime.
