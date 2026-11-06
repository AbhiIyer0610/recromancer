---
inclusion: always
---

# Repository structure (target)

```
backend/src/recromancer/
  domain/      entities, ids, errors              (depends on nothing)
  grounding/   text utils, verifier               (stdlib only)
  ingest/      chunking, normalisation, connectors, jobs
  retrieval/   rank fusion, retrievers (memory, postgres)
  llm/         gateway port, resilience, provider adapters, ModelRouter, fakes
  embed/       Embedder port and adapters
  db/          schema migrations, repositories (Postgres adapters)
  service/     ports, prompting, ask pipeline, profile service
  api/         FastAPI app factory, routes, SSE (thin)
  mcp/         MCP server exposing ask_ghost and search_evidence
  config.py    Settings
backend/tests/{unit,property,integration,contract}
frontend/      React app (see seance-ui spec)
docs/          SCOPE_AND_DESIGN.md, DEV_SETUP.md, KIRO_USAGE.md
scripts/       secret_scan.py, check_submission_readiness.py, doctor.py
.kiro/         specs, steering, hooks, agents, settings
```

## Dependency rule
`domain` and `grounding` import nothing from the project except `domain`. `service` may import
`domain`, `grounding`, port modules and its own `ports`. `api` and `mcp` may import `service`.
Adapters (`llm.providers`, `embed`, `retrieval`, `db`) implement ports and are wired only in the
composition root. Never import upward.

## Naming
Modules are nouns; functions are verbs; ports are protocols named for the capability
(`Retriever`, `GhostRepository`, `LLMClient`, `Embedder`, `SourceConnector`). Property invariants
live in `tests/property/invariants.py` and are reused by Hypothesis drivers. Contract tests run
the same suite against every implementation of a port.
