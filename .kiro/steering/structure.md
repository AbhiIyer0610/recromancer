---
inclusion: always
---

# Repository structure (target)

```
backend/src/recromancer/
  domain/      entities, ids, errors              (depends on nothing)
  grounding/   text utils, verifier               (stdlib only)
  ingest/      pure pipeline logic: chunking, normalisation, job planning (domain only)
  ingest/fixtures/  packaged sample bundles (data only, no __init__.py), e.g. ledgerline/
  retrieval/   rank fusion, retrievers (memory, postgres)
  llm/         gateway port, resilience, provider adapters, ModelRouter, fakes
  embed/       Embedder port and adapters
  db/          schema migrations, repositories (Postgres adapters)
  service/     ports, prompting, ask pipeline, profile service
  api/         FastAPI app factory, routes, SSE (thin)
  mcp/         MCP server exposing ask_ghost and search_evidence
  config.py    Settings
  ingest_cli.py  ingest command line and bundle loader; top of the layer order beside bootstrap
backend/tests/{unit,property,integration,contract}
frontend/      React app (see seance-ui spec)
docs/          SCOPE_AND_DESIGN.md, DEV_SETUP.md, KIRO_USAGE.md
scripts/       secret_scan.py, check_submission_readiness.py, doctor.py
.kiro/         specs, steering, hooks, agents, settings
```

## Dependency rule
`domain`, `grounding` and `ingest` import nothing from the project except `domain`. `service` may
import `domain`, `grounding`, port modules and its own `ports`. `api` and `mcp` may import
`service` and `domain`. Adapters (`llm`, `embed`, `retrieval`, `db`) implement ports structurally,
never import `service` or each other, and are wired only in `bootstrap.py` (the composition root).
Network-facing source connectors are adapters in their own package, added by a later spec, not
part of `ingest`. `config.py` and `observability.py` sit beside the stack. Never import upward.

Layer order, top to bottom: `ingest_cli` (command line entry, beside `bootstrap` at the top);
`bootstrap`; `api` | `mcp`; `service`; `llm` | `embed` | `retrieval` |
`db`; `grounding` | `ingest`; `domain`. Enforced by import-linter contracts in `backend/pyproject.toml`.

## Naming
Modules are nouns; functions are verbs; ports are protocols named for the capability
(`Retriever`, `GhostRepository`, `LLMClient`, `Embedder`, `SourceConnector`). Property invariants
live in `tests/property/invariants.py` and are reused by Hypothesis drivers. Contract tests run
the same suite against every implementation of a port.
