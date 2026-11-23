# Design Document: Platform Foundation

## Overview

Platform Foundation is the backbone every later Recromancer spec builds on. It delivers a src-layout
Python package with strict tooling, import boundaries enforced by `import-linter`, typed
configuration, an error taxonomy, structured logging with request tracing, health endpoints, a
local Postgres + pgvector environment, a small forward-only SQL migration runner, CI, and repo
hygiene. It contains no product features.

Design principles, in priority order:

1. **Pure logic first.** Anything with a "for all inputs" property (request-id resolution, migration
   planning, secret redaction, secret scanning, error mapping) is a framework-free function so
   Hypothesis can drive it directly.
2. **Ports for every external dependency.** The database, the clock, id generation and the
   migration store are all injected. Tests use fakes and never touch a real network.
3. **Fail loudly, leak nothing.** Configuration and migration errors name fields and files, never
   values. Secrets are `SecretStr`, and a redaction processor is a second line of defence.
4. **Simplest robust tool.** Plain SQL files and a ~100-line runner instead of Alembic; one custom
   ASGI middleware instead of a stack; a tiny `.env` parser instead of an extra dependency.

### Requirement coverage map

| Requirement | Covered in |
|---|---|
| R1 Scaffolding | Module layout; pyproject sketch; Packaging |
| R2 Dependency rule | Layers diagram; Import-linter contracts |
| R3 Configuration | Configuration; Env_Loader; AST guard |
| R4 Error taxonomy | Error taxonomy |
| R5 Observability | Observability; Application factory; Health endpoints |
| R6 Local environment | Local environment; Doctor |
| R7 Migrations | Migrations |
| R8 CI | Continuous integration |
| R9 Hygiene | Repository hygiene; Secret scanner |
| R10 Testing | Correctness Properties; Testing Strategy |

### Decisions that go beyond the literal requirements

These are small, safe additions. Each is flagged again under Open Questions where it needs sign-off.

| Decision | Reason |
|---|---|
| `uvicorn` and `anyio` added as runtime dependencies | The app needs a server to run, and `api` imports `anyio` directly (it is only transitive today). |
| Sync `psycopg_pool.ConnectionPool` called through `anyio.to_thread`, not the async pool | Async psycopg does not support the default Windows `ProactorEventLoop`; a thread avoids a Windows-only failure. |
| `.gitattributes` forcing `*.sql text eol=lf` | Checksums are over file bytes; CRLF checkouts on Windows would otherwise differ from Linux CI. |
| `.sql` files that do not match the name pattern, or duplicate numeric prefixes, are errors | A typo such as `1_x.sql` would otherwise be silently ignored. |
| `observability.py` and `domain/secrets.py` added | `structure.md` has no home for logging setup or the log-key redaction rule. |
| `create_app(production=...)` disables `/docs`, `/redoc` and `/openapi.json` in production | Reduces attack surface in production. Not required by any requirement. The `ErrorBody` schema is then documented in OpenAPI only outside production. |

## Architecture

### Module layout

```text
Recromancer/
  .env.example  .gitattributes  .gitignore  docker-compose.yml  LICENSE  README.md
  .github/workflows/ci.yml
  docs/            DEV_SETUP.md  KIRO_USAGE.md  SCOPE_AND_DESIGN.md
  scripts/         doctor.py  secret_scan.py
  backend/
    pyproject.toml
    src/recromancer/
      __init__.py  py.typed
      config.py            Settings, ConfigError, Env_Loader, read_environment,
                           ADMIN_TOKEN_PLACEHOLDER
      observability.py     structlog setup, redaction, question_log_fields
      bootstrap.py         Composition_Root: the only importer of adapter packages
      domain/              errors.py (ErrorCode, RecromancerError)
                           secrets.py (is_sensitive_key: log-key redaction rule)
      grounding/  ingest/  retrieval/  llm/  embed/  mcp/     (__init__.py only in this spec)
                           # grounding and ingest are pure logic (domain only); ingest holds
                           # chunking, normalisation and job planning. Network-facing source
                           # connectors belong in a later spec's adapter package, not in ingest.
      service/             ports.py (ReadinessProbe, Clock, IdGenerator, DbDiagnostics)
      api/                 app.py  errors.py  middleware.py  request_id.py  routes/health.py
      db/                  runner.py  postgres.py  migrate.py  migrations/0001_*.sql
    tests/
      conftest.py
      unit/  property/  integration/  contract/
```

R1.4 asks that the scaffold create the ten sub-packages with only `__init__.py`. The files listed
above inside `domain`, `service`, `api` and `db` are added by the later tasks of this spec.

### Layers

```mermaid
flowchart TB
  subgraph entry[Entry points]
    BOOT[bootstrap.py<br/>Composition Root]
  end
  subgraph edge[Delivery]
    API[api]
    MCP[mcp]
  end
  SVC[service<br/>ports + pipelines]
  subgraph adapters[Adapters - independent siblings]
    LLM[llm]
    EMB[embed]
    RET[retrieval]
    DB[db]
  end
  subgraph pure[Pure pipeline logic - domain only]
    GRD[grounding]
    ING[ingest]
  end
  DOM[domain]
  CFG[config.py]
  OBS[observability.py]

  BOOT --> API
  BOOT --> MCP
  BOOT --> SVC
  BOOT --> adapters
  BOOT --> ING
  BOOT --> CFG
  BOOT --> OBS
  API --> SVC
  MCP --> SVC
  SVC --> GRD
  SVC --> DOM
  adapters --> DOM
  GRD --> DOM
  ING --> DOM
  CFG --> DOM
```

Arrows mean "may import". Everything else is forbidden. Adapters do not import `service`: they
satisfy the port `Protocol`s structurally, and `bootstrap.py` proves it by assigning an adapter to a
port-typed variable, which mypy checks. This keeps adapters below `service` in the layer order and
makes "never import upward" literally true.

`config.py` and `observability.py` sit beside the layer stack, not in it. `config.py` may import
only `domain`, the standard library and `pydantic`/`pydantic-settings`. `observability.py` may import
`domain`, the standard library and `structlog`. `db/migrate.py` (the CLI shell only) may import
`config`; the runner and stores take their configuration as arguments.

### Import-linter contracts

All contracts live in `backend/pyproject.toml`, with `root_package = "recromancer"` and
`include_external_packages = true` (required so contracts can forbid third-party modules).

| # | Name | Type | Source | Forbidden or rule |
|---|---|---|---|---|
| C1 | domain-is-pure | forbidden | `domain` | every other project module (`grounding`, `ingest`, `retrieval`, `llm`, `embed`, `db`, `service`, `api`, `mcp`, `bootstrap`, `config`, `observability`) and external `pydantic`, `fastapi`, `httpx`, `structlog`, `psycopg`, `psycopg_pool`, `anyio` |
| C2 | pure-logic-imports-domain-only | forbidden | `grounding`, `ingest` | same list as C1 minus `domain` (allowed by omission), `grounding` and `ingest`; the two are not forbidden from each other here |
| C3 | config-is-narrow | forbidden | `config` | every project module except `domain`; external `fastapi`, `httpx`, `structlog`, `psycopg`, `psycopg_pool`, `anyio` |
| C4 | service-uses-ports-only | forbidden | `service` | `llm`, `embed`, `retrieval`, `db`, `ingest`, `api`, `mcp`, `bootstrap`, `config`, `observability`; external `psycopg`, `psycopg_pool`, `httpx` |
| C5 | delivery-skips-adapters | forbidden, `allow_indirect_imports = true` | `api`, `mcp` | `llm`, `embed`, `retrieval`, `db`, `ingest`, `bootstrap`, `grounding`, `config`, `observability`; external `psycopg`, `psycopg_pool`, `httpx` |
| C6 | only-bootstrap-wires-adapters | forbidden | `grounding`, `ingest`, `service`, `api`, `mcp`, `config`, `observability` | `llm`, `embed`, `retrieval`, `db` (`ingest` stays a source: it is not an adapter and must not import one) |
| C7 | adapters-are-independent | independence | `llm`, `embed`, `retrieval`, `db` | none may import another |
| C8 | layer-order | layers | container `recromancer` | `bootstrap`, then `api` or `mcp`, then `service`, then `llm`, `embed`, `retrieval`, `db` as siblings, then `grounding` and `ingest` as siblings, then `domain` |

`ingest` is pure pipeline logic (chunking, normalisation, job planning), so it sits next to
`grounding` with the same restriction: it imports `domain` only. Ingest connectors that talk to the
network belong in a later spec's adapter package, not in `ingest`.

**Indirect imports.** By default a `forbidden` contract also fails on import chains, not only
direct imports. Each forbidden contract was reviewed for legal chains:

| Contract | `allow_indirect_imports` | Reason |
|---|---|---|
| C1 | default (false) | `domain` imports nothing from the project, so no legal chain starts there |
| C2 | default (false) | `grounding` and `ingest` reach only `domain`, which reaches nothing |
| C3 | default (false) | `config` reaches only `domain`, which reaches nothing |
| C4 | default (false) | `service` may reach only `domain` and `grounding`, which never lead to a forbidden module |
| C5 | **true** | `api` and `mcp` may legally reach `grounding` through `service` (`api -> service -> grounding`), so a chain-sensitive contract would fail on a legal layout. Direct imports are still forbidden, and the chains that matter are closed by C4 and C6 on `service` |
| C6 | default (false) | no legal chain from any source reaches an adapter; only `bootstrap` imports adapters and nothing imports `bootstrap` |

The first implementation task verifies that `lint-imports` actually runs and passes on the scaffold.
The exact contract options (`allow_indirect_imports`, the `|` sibling syntax, external-package
handling) must be checked against the installed `import-linter` version, since this design states
them from memory.

How `config -> domain` coexists with "domain imports nothing":

- C1 and C2 constrain only the modules named in `source_modules`. `config` is never a source in
  those contracts, so `config` importing `domain` is not examined by them.
- C1 does list `config` as a forbidden target, so `domain` importing `config` fails the build. The
  one allowed direction is therefore `config -> domain`, never the reverse.
- C3 states the allowance positively: `config` may import `domain` and nothing else in the project.
- C8 does not list `config` or `observability`, so the layers contract neither allows nor forbids
  them; C1 to C6 do that work.

Sketch of the TOML (the final file lists every module explicitly; shortened here):

```toml
[tool.importlinter]
root_package = "recromancer"
include_external_packages = true

[[tool.importlinter.contracts]]
name = "domain-is-pure"
type = "forbidden"
source_modules = ["recromancer.domain"]
forbidden_modules = [
  "recromancer.config", "recromancer.bootstrap", "recromancer.observability",
  "recromancer.grounding", "recromancer.service", "recromancer.api", "recromancer.mcp",
  "recromancer.ingest", "recromancer.retrieval", "recromancer.llm", "recromancer.embed",
  "recromancer.db",
  "pydantic", "fastapi", "httpx", "structlog", "psycopg", "psycopg_pool", "anyio",
]

[[tool.importlinter.contracts]]
name = "pure-logic-imports-domain-only"
type = "forbidden"
source_modules = ["recromancer.grounding", "recromancer.ingest"]
forbidden_modules = [
  "recromancer.config", "recromancer.bootstrap", "recromancer.observability",
  "recromancer.service", "recromancer.api", "recromancer.mcp",
  "recromancer.retrieval", "recromancer.llm", "recromancer.embed", "recromancer.db",
  "pydantic", "fastapi", "httpx", "structlog", "psycopg", "psycopg_pool", "anyio",
]

[[tool.importlinter.contracts]]
name = "delivery-skips-adapters"
type = "forbidden"
source_modules = ["recromancer.api", "recromancer.mcp"]
forbidden_modules = [
  "recromancer.llm", "recromancer.embed", "recromancer.retrieval", "recromancer.db",
  "recromancer.ingest", "recromancer.bootstrap", "recromancer.grounding",
  "recromancer.config", "recromancer.observability",
  "psycopg", "psycopg_pool", "httpx",
]
allow_indirect_imports = true   # api -> service -> grounding is legal

[[tool.importlinter.contracts]]
name = "adapters-are-independent"
type = "independence"
modules = ["recromancer.llm", "recromancer.embed", "recromancer.retrieval", "recromancer.db"]

[[tool.importlinter.contracts]]
name = "layer-order"
type = "layers"
containers = ["recromancer"]
layers = [
  "bootstrap",
  "api | mcp",
  "service",
  "llm | embed | retrieval | db",
  "grounding | ingest",
  "domain",
]
```

A `forbidden` contract whose source and target overlap (for example `db` forbidding `db`) would
flag a package importing its own submodules, which is why the adapter packages are never forbidden
from themselves: C6 excludes them as sources and C7 covers cross-adapter imports.

### Composition Root

`bootstrap.py` is the only module that imports `db`, `llm`, `embed` and `retrieval`. In this spec
it wires exactly one adapter family:

```python
def build_app(settings: Settings, *, clock: Clock | None = None) -> FastAPI:
    configure_logging(level=settings.log_level, clock=clock or SystemClock(),
                      secret_values=settings.secret_values())
    pool = build_pool(settings.database_url)            # recromancer.db.postgres; open=False
    probe: ReadinessProbe = PostgresReadinessProbe(pool)  # port-typed: mypy checks the fit
    return create_app(readiness=probe, clock=clock or SystemClock(),
                      new_id=uuid4_str, lifespan=pool_lifespan(pool),
                      demo_mode=settings.demo_mode, production=settings.production)

def app_factory() -> FastAPI:                 # uvicorn recromancer.bootstrap:app_factory --factory
    load_dotenv()
    return build_app(Settings.from_env())

def build_db_diagnostics(settings: Settings) -> DbDiagnostics: ...   # used by scripts/doctor.py
```

**Pool lifecycle.** `build_pool` constructs `psycopg_pool.ConnectionPool(..., open=False)`, so
building the app never connects. `pool_lifespan(pool)` calls `pool.open(wait=False)` on startup,
which returns immediately and lets the pool fill in background workers, and `pool.close()` on
shutdown. The app therefore starts when the database is down: `/healthz` answers 200, and `/readyz`
reports 503 `unreachable` (the probe's `pool.connection(timeout=4.0)` raises `PoolTimeout`, which
counts as not ready) until the pool connects.

`scripts/` is outside the linted package, so `doctor.py` reaches Postgres only through
`bootstrap.build_db_diagnostics`, keeping `psycopg` inside `db`.

## Components and Interfaces

### Ports (`service/ports.py`)

Ports live in `service`, outside `db`, as `typing.Protocol` classes.

```python
class ReadinessProbe(Protocol):
    async def is_ready(self) -> bool: ...          # True when the database answers

class Clock(Protocol):
    def now(self) -> datetime: ...                 # timezone-aware, UTC
    def monotonic(self) -> float: ...

IdGenerator = Callable[[], str]                    # production: str(uuid.uuid4())

class DbDiagnostics(Protocol):                     # used by Doctor only
    def ping(self) -> None: ...                    # raises on failure
    def has_extension(self, name: str) -> bool: ...
```

`MigrationStore` is the one port that stays inside `db` (`db/runner.py`), because only the runner
and its Postgres store use it and adapters cannot import `service`.

### Application factory and middleware

```python
def create_app(*, readiness: ReadinessProbe, clock: Clock, new_id: IdGenerator,
               lifespan: Lifespan | None = None, demo_mode: bool = True,
               production: bool = False, readyz_timeout_s: float = 5.0) -> FastAPI: ...
```

When `production` is true, `create_app` constructs `FastAPI(docs_url=None, redoc_url=None,
openapi_url=None)`, so `/docs`, `/redoc` and `/openapi.json` are not routed and return the standard
404 `NOT_FOUND` JSON body through the same handler as any unknown route. Otherwise the defaults
apply. The reason is reduced attack surface in production. Consequence: the `ErrorBody` schema is
documented in OpenAPI only outside production.

`create_app` never reads configuration or imports an adapter. Tests call it with a fake probe, a
fake clock and a counter-based id generator. `readyz_timeout_s` defaults to the R5.11 value of 5
seconds; tests lower it so the timeout case does not wait.

Stack, outermost first:

| Order | Layer | Responsibility |
|---|---|---|
| 1 | Starlette `ServerErrorMiddleware` (built in) | Runs the catch-all `Exception` handler |
| 2 | `RequestContextMiddleware` (pure ASGI, ours) | Resolve Request_ID, store it in `scope["state"]`, bind structlog contextvars (`request_id`, `method`, `path`), add `X-Request-Id` to the response, emit one access log line in a `finally` block, clear contextvars |
| 3 | Starlette `ExceptionMiddleware` (built in) | Handlers for `RecromancerError`, `RequestValidationError`, `StarletteHTTPException` |
| 4 | Router | `/healthz`, `/readyz`; plus `/docs`, `/redoc`, `/openapi.json` unless `production` is true |

Why this order:

- The request id must exist before anything can fail, so it is resolved first inside layer 2. Binding
  the logging context happens in the same step, so every log line from layers 3 and 4 carries it.
- Error handlers for known errors sit inside layer 2 and therefore run with the context bound and
  get the `X-Request-Id` header added by the middleware.
- The catch-all `Exception` handler is run by layer 1, which is outside our middleware. By then the
  middleware has cleared contextvars and no longer wraps `send`. That handler therefore reads
  `request.state.request_id` (stored in the scope, which survives), re-binds it with
  `structlog.contextvars.bound_contextvars`, and sets the `X-Request-Id` header itself.
- The access log is written in a `finally` block, so every request produces exactly one line even
  when the app raises. The middleware records the status from the `http.response.start` message it
  forwards; if an exception propagates, the logged status is 500 (the catch-all handler in layer 1
  sends that 500), then the contextvars are cleared after the log call.
- Pure ASGI is used instead of `BaseHTTPMiddleware` to avoid its known contextvar and streaming
  quirks.
- Starlette re-raises after sending the 500. Uvicorn then logs the traceback through its own logger,
  which `configure_logging` routes through the same JSON and redaction pipeline. Tests use
  `TestClient(app, raise_server_exceptions=False)`.

### Error taxonomy

`domain/errors.py`:

```python
class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFIG_INVALID = "CONFIG_INVALID"
    MIGRATION_FAILED = "MIGRATION_FAILED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"

class RecromancerError(Exception):
    MAX_MESSAGE_LENGTH = 500
    def __init__(self, code: ErrorCode, message: str) -> None: ...   # ValueError if len > 500
```

Later specs add members; the totality property keeps the status map in step. A message over 500
characters raises `ValueError` (a programming error) whose text states only the length.

`api/errors.py` holds the only status map and the handlers:

| ErrorCode | HTTP status |
|---|---|
| `VALIDATION_ERROR` | 422 |
| `NOT_FOUND` | 404 |
| `METHOD_NOT_ALLOWED` | 405 |
| `CONFIG_INVALID` | 500 |
| `MIGRATION_FAILED` | 500 |
| `DEPENDENCY_UNAVAILABLE` | 503 |
| `INTERNAL_ERROR` | 500 |

```python
ERROR_HTTP_STATUS: Mapping[ErrorCode, int] = MappingProxyType({...})
def http_status_for(code: ErrorCode) -> int: return ERROR_HTTP_STATUS[code]
```

Response body, for every error: `{"code": "...", "message": "...", "request_id": "..."}`, modelled
by a Pydantic `ErrorBody` so OpenAPI documents it.

| Raised | Status | Code | Message |
|---|---|---|---|
| `RecromancerError` | mapped | its own | its own message |
| `RequestValidationError` | 422 | `VALIDATION_ERROR` | fixed text `Request validation failed`; field locations may be listed, input values never |
| `StarletteHTTPException` 404 (R4.7) | 404 (its own) | `NOT_FOUND` | fixed text `Not found`, never `exc.detail` |
| `StarletteHTTPException` 405 (R4.7) | 405 (its own) | `METHOD_NOT_ALLOWED` | fixed text `Method not allowed`, never `exc.detail` |
| `StarletteHTTPException`, any other status (R4.8) | its original status | `INTERNAL_ERROR` | fixed text `An internal error occurred`, never `exc.detail` |
| anything else (R4.9) | 500 | `INTERNAL_ERROR` | fixed `An internal error occurred` |

Traceback logging (R4.10): the handler logs `exc_info` at ERROR with only `request_id`, `method`,
`path` and the exception class name as extra fields. It never logs the body or query string, and
the log redaction processor redacts any key matched by `is_sensitive_key` (see Observability).
Header values are never added to log events in the first place, so no header whose name contains
`authorization`, `token`, `secret` or `cookie` can reach a log line.

### Configuration (`config.py`)

`Settings` is a `pydantic_settings.BaseSettings` subclass.

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RECROMANCER_", case_sensitive=False, extra="forbid",
        hide_input_in_errors=True, frozen=True)
    production: bool = False
    demo_mode: bool = True
    database_url: SecretStr = SecretStr(DEFAULT_DATABASE_URL)
    admin_token: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
```

Other secret fields (Hugging Face token, GitHub token) are added by the specs that use them. The
Postgres password is part of `database_url`, which is why that field is a `SecretStr`.

**Loading.** `Settings.from_env(environ: Mapping[str, str] | None = None)`:

1. `environ` defaults to a copy of the real process environment. Reading it is legal because this is
   `config.py`. The parameter exists so tests inject a mapping instead of mutating process state.
2. `settings_customise_sources` is overridden to return only the init source, so pydantic-settings
   never reads the process environment behind our back.
3. Unknown-variable check: collect every key that upper-cases to start with `RECROMANCER_`, compare
   with `{"RECROMANCER_" + name.upper()}` for each field, and record the difference. `extra="forbid"`
   is a backstop only, since the environment source ignores unknown variables.
4. Build keyword arguments from the matching variables (prefix stripped, lower-cased) and call the
   constructor. Pydantic's lax mode turns `"true"` into `True` and numeric strings into numbers. No
   field is a complex type, so no JSON decoding is needed; a later spec adding one must revisit this.
5. On `ValidationError`, collect `(field, expected type)` from `exc.errors(include_input=False,
   include_url=False, include_context=False)` and read the type from the field annotation. The
   pydantic `msg` text is not used, so a custom validator can never smuggle a value into the message.
6. If anything is collected so far, raise one `ConfigError` (`from None`) that names every unknown
   variable and every failing field.
7. Production rules run only after validation succeeds and collect all violations before raising:

| Rule | Field named in the error |
|---|---|
| `production` and `database_url` not in `model_fields_set`, or equal to the default | `database_url` |
| `production` and not `demo_mode` and `admin_token` is missing, shorter than 32 characters, or equal to `ADMIN_TOKEN_PLACEHOLDER` (R3.10) | `admin_token` |

`ADMIN_TOKEN_PLACEHOLDER` (`"change-me-local-only"`) is defined once as a constant in `config.py`.
The validator compares against it, and the `.env.example` check uses it too: a unit test asserts
that the `RECROMANCER_ADMIN_TOKEN` value in `.env.example` equals the constant, so the file and the
rule cannot drift. The error names the field `admin_token` (once, however many sub-rules fail) and
never its value or length.

`ConfigError(RecromancerError)` carries `ErrorCode.CONFIG_INVALID`; this is the second place where
`config -> domain` is used. Example message:
`Invalid configuration. Unknown variables: RECROMANCER_FOO. Invalid fields: RECROMANCER_DEMO_MODE (expected bool).`

**Empty as unset (R3.18).** A `field_validator("admin_token", "anthropic_api_key", mode="before")`
converts an empty or whitespace-only string to `None` before type validation and before the
production rules run. So a blank `RECROMANCER_ADMIN_TOKEN` in production with `demo_mode` false is
a missing token and fails the `admin_token` rule (R3.10). The rule applies to these two optional
secret fields only, not to `database_url`: an empty `database_url` is invalid.

**Other validation.** `database_url` is checked by a field validator: scheme `postgresql` or
`postgres`, a host present. It raises a fixed message without the value.

**`log_safe_summary()`** returns a dict of every field. `SecretStr` fields become `"***"`, except
`database_url`, which becomes `"host:port"` (port 5432 when absent). Non-secret fields appear as
plain values. `secret_values()` returns the raw secret strings (at least 8 characters) and exists
only to seed the log redactor.

**Demo_Mode.** A seeded, read-only workspace served on the real code path with the real configured
model client. Write endpoints are disabled while it is on. It is valid in production, so no rule
forbids `production` together with `demo_mode`; a unit test pins that. This spec ships no write
endpoints. It exposes `demo_mode` through `create_app` and the startup log line so the later specs
can add the write guard in one place.

### Env_Loader

```python
def load_dotenv(*, environ: MutableMapping[str, str] | None = None,
                start: Path | None = None) -> Path | None
```

- It reads the production flag from the real environment only. Any value other than absent or one
  of `""`, `0`, `false`, `no`, `off` (case-insensitive) counts as production, so a typo disables
  `.env` loading rather than enabling it.
- When production, it returns `None` without opening any file (R3.15).
- Otherwise it finds `.env` by walking up from `start` (default: the current directory) to the first
  directory containing `.env.example`, then parses it with a small stdlib parser (R3.14):
  1. Read the file with `Path.read_text(encoding="utf-8-sig")`, so a UTF-8 byte order mark is
     consumed and the first key is `RECROMANCER_...`, not `\ufeffRECROMANCER_...`.
  2. Split into lines and strip a trailing `\r` from each, so CRLF and LF files parse identically
     and values never end in `\r`.
  3. Skip blank lines and lines whose first non-blank character is `#`.
  4. Split on the first `=` into `KEY` and `VALUE`, strip surrounding whitespace, and remove one
     pair of matching single or double quotes from the value. A `#` inside quotes is kept.
     There is no interpolation and no `export` prefix.
  Windows editors commonly save `.env` files with a BOM or CRLF line endings, which is why both are
  handled.
- Decode failure (R3.19): the file is decoded as a whole before any variable is set, so a decode
  failure leaves the target mapping unchanged. A `UnicodeDecodeError` is caught around the read and
  replaced with a `ConfigError` carrying the fixed message ".env must be UTF-8 text. Re-save it as
  UTF-8 (PowerShell `>` writes UTF-16)." The new exception is raised `from None`, so the decoder
  message (which contains byte offsets and values) is not chained. UTF-16 with a BOM fails because
  its first byte `0xFF` is not valid UTF-8.
- It uses `setdefault`, so variables already set are never overridden (R3.13).
- It ignores `RECROMANCER_PRODUCTION` if it appears in the file, so a file can never switch the
  process into or out of production mode.
- A tiny parser is used because `python-dotenv` is not an allowed import for `config.py` (R2.5).

Entrypoints (`app_factory`, `db/migrate.py`, `scripts/doctor.py`) call `load_dotenv()` then
`Settings.from_env()` (R3.16). `config.read_environment()` returns a copy of the process
environment for callers that must look at raw variables (Doctor, test conftest) without touching
`os.environ` themselves.

### The `os.environ` guard (R3.17)

`tests/unit/test_no_environ_reads.py` walks every `*.py` under the repo root (excluding `.venv`,
`.git`, `node_modules`, `.hypothesis` and other tool directories), skips `backend/src/recromancer/config.py`,
parses each file with `ast`, and fails on any `ast.Attribute` node where `node.value` is
`ast.Name(id="os")` and `node.attr in {"environ", "getenv"}`.

Only `ast.Attribute` is flagged. A string such as `"os.environ"`, a comment, or a docstring is an
`ast.Constant` or not in the tree at all, so it never trips the guard. The deliberate limit: `from
os import environ` and `import os as o` are not detected. A second small check flags
`ast.ImportFrom(module="os")` with `environ`/`getenv` aliases to close the first gap; the aliasing
case is documented and accepted.

### Observability (`observability.py`)

`configure_logging(*, level, clock, secret_values=(), stream=sys.stdout)` is idempotent.

Processor chain for structlog and for stdlib records (through `ProcessorFormatter`, so uvicorn and
psycopg logs get the same treatment):

1. `merge_contextvars` (brings in `request_id`, `method`, `path`)
2. add `logger` (name) and `level`
3. add `timestamp` from `clock.now()`, formatted ISO-8601 UTC with a `Z` suffix
4. rename structlog's `event` key to `message`
5. format `exc_info` into `exception`
6. **redaction processor**
7. `JSONRenderer` (one object per line)

Redaction processor, in order:

- Any key for which `domain.secrets.is_sensitive_key(key)` is true has its value replaced with
  `"***"`. `secret`, `password`, `token` and the compounds `apikey`, `accesskey`, `privatekey`,
  `signingkey` and `authkey` match as substrings; `key`, `authorization` and `cookie` match only as
  whole name segments (see below).
- Keys named `question`, `question_text` or `query_text` are replaced with `"***"`.
- Every remaining string value, including the formatted exception text, has each registered secret
  value replaced with `"***"` before rendering, so JSON escaping cannot hide a leak.
- `SecretStr` objects render as `"***"`.

**Log-key rule (`domain/secrets.py`).** This rule is for log keys only. Doctor does not use it
(see Doctor). `key` alone is matched only as a whole segment, because a substring match would redact
`monkey` and `keyboard`. `secret`, `password` and `token` are matched as substrings, and so are
five compounds that are specific enough to be safe as substrings: `apikey`, `accesskey`,
`privatekey`, `signingkey` and `authkey`. This catches `apikey`, camelCase `apiKey` and `API_KEY`
(all lower-case to a string containing `apikey` or segment `key`). The `tokens` allowlist keeps
counter keys such as `input_tokens` readable.

```python
SECRET_SUBSTRINGS = ("secret", "password", "token",
                     "apikey", "accesskey", "privatekey", "signingkey", "authkey")
SECRET_SEGMENTS = frozenset({"key", "authorization", "cookie"})
COUNTER_SUFFIX = "tokens"          # allowlist: input_tokens, output_tokens, max_tokens

def name_segments(name: str) -> tuple[str, ...]:   # split on "_" and "-", drop empties

def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    segments = name_segments(lowered)
    if segments and segments[-1] == COUNTER_SUFFIX:
        return False
    if any(marker in lowered for marker in SECRET_SUBSTRINGS):
        return True
    return any(segment in SECRET_SEGMENTS for segment in segments)
```

There is no `is_secret_name`; the single public function is `is_sensitive_key`. Order of
evaluation: allowlist first, then the substring markers, then the whole-segment markers.

Results for the rule as written:

| Key | Redacted | Why |
|---|---|---|
| `api_key`, `secret_key` | yes | segment `key` |
| `apikey`, `API_KEY` | yes | substring `apikey` (`API_KEY` also has segment `key`) |
| `apiKey` (camelCase) | yes | lower-cases to `apikey`, substring match |
| `accesskey`, `privatekey`, `signingkey`, `authkey` | yes | compound substring |
| `accesstoken`, `apiToken` | yes | substring `token` |
| `db_password`, `admin_token`, `authorization`, `session_cookie` | yes | substring or segment match |
| `monkey`, `keyboard` | no | `key` is only a substring, `key` is matched by segment, and none of the compound substrings occurs in either name |
| `input_tokens`, `output_tokens`, `max_tokens` | no | last segment is `tokens` (allowlist) |

- Because the allowlist is decided by the last segment alone, a name such as `secret_tokens` is also
  not redacted; this is accepted for counter-style names.
- Because `token` is a substring match, names such as `tokenizer` are redacted. This errs on the
  side of redaction and is accepted.
- Known limits: only the listed compounds are caught as substrings. Other compounds, such as
  `clientkey`, are not caught unless an underscore or hyphen makes `key` its own segment
  (`client_key` is caught). `monkey` and `keyboard` stay unredacted by design. Log keys in this
  project are snake_case, and the registered-secret-value replacement below still removes the
  value itself if it came from Settings.

`question_log_fields(text) -> {"question_len": len(text)}` is the one sanctioned way to refer to a
question in a log entry (R5.9). The request id is already on the event through contextvars.

`uvicorn.access` is disabled because the middleware writes one access event per request
(`method`, `path`, `status`, `duration_ms`, no query string).

### Request-id resolution (`api/request_id.py`)

```python
_ALLOWED = re.compile(r"[A-Za-z0-9._-]{8,64}")

def resolve_request_id(header: str | None, new_id: IdGenerator) -> str:
    if header is not None and _ALLOWED.fullmatch(header):
        return header
    return new_id()
```

`fullmatch` is used deliberately. A pattern written as `^...$` accepts a trailing newline because
`$` matches before a final `\n`, which would let `"abcdefgh\n"` through. Hypothesis finds this
within seconds, and the property oracle is written without regexes to catch it.

### Health endpoints

| Endpoint | Behaviour |
|---|---|
| `GET /healthz` | Always 200, `{"status": "ok"}`. The handler touches no database, network or file. |
| `GET /readyz` | Calls `readiness.is_ready()` inside `anyio.move_on_after(readyz_timeout_s)`. Ready gives 200 `{"status": "ok", "db": "ok"}`. `False`, an exception, or a timeout gives 503 `{"status": "degraded", "db": "unreachable"}`. |

An exception from the probe is logged with its class name only, because driver messages can include
connection details. The handler catches `Exception`, never `BaseException`, so cancellation still
propagates. `/readyz` returns its own two documented bodies, not the error shape.

`PostgresReadinessProbe.is_ready` runs `SELECT 1` on a pooled connection in a worker thread:

```python
async def is_ready(self) -> bool:
    return await anyio.to_thread.run_sync(self._ping, abandon_on_cancel=True)
```

`abandon_on_cancel=True` lets the 5 second timeout return promptly. The abandoned thread is bounded
by `pool.connection(timeout=4.0)` and `connect_timeout` in the connection info.

### Migrations (`db/`)

Chosen approach: plain SQL files plus a small runner. No Alembic, no SQLAlchemy.

```text
db/runner.py      pure planning + MigrationStore port + run()      (no psycopg import)
db/postgres.py    PostgresMigrationStore, PostgresReadinessProbe, PostgresDiagnostics, build_pool
db/migrate.py     CLI: python -m recromancer.db.migrate
db/migrations/    0001_create_vector_extension.sql
```

The module is named `runner.py` because a `migrations.py` next to a `migrations/` directory is
confusing for the import system and for readers.

```python
@dataclass(frozen=True)
class Migration:
    name: str          # 0001_create_vector_extension.sql
    sql: str
    checksum: str      # sha256 hex of the file bytes

class MigrationStore(Protocol):
    @contextmanager
    def locked(self) -> Iterator[None]: ...           # pg_advisory_lock for the whole run
    def ensure_table(self) -> None: ...               # CREATE TABLE IF NOT EXISTS _schema_migrations
    def applied(self) -> Mapping[str, str]: ...       # filename -> checksum
    def apply(self, migration: Migration, applied_at: datetime) -> None: ...  # SQL + record, one transaction
```

`run(store, migrations, clock, warn) -> RunReport` does:

```mermaid
flowchart TD
  A[locked: acquire advisory lock] --> B[ensure_table]
  B --> C[applied = store.applied]
  C --> D{Pre-flight 1: any recorded file<br/>with a different checksum?}
  D -- yes --> X[raise MigrationError naming the file<br/>nothing applied]
  D -- no --> D2{Pre-flight 2: any non-blank pending file numbered<br/>below the highest applied number? blank files exempt}
  D2 -- yes --> X2[raise MigrationError MIGRATION_FAILED<br/>naming the file; nothing applied]
  D2 -- no --> E[for each migration in filename order]
  E --> F{recorded?}
  F -- yes --> E
  F -- no --> G{sql blank?}
  G -- yes --> W[warn naming file; skip, not recorded] --> E
  G -- no --> H[store.apply: one transaction<br/>SQL + INSERT into _schema_migrations]
  H -- DB error --> Y[rollback; raise MigrationError with filename and original message]
  H -- ok --> E
  E --> Z[release lock; RunReport]
```

Details:

- **Pre-flight (R7.6, R7.7).** Both checks run on the whole set before the first `apply`, so a
  failure leaves the database untouched. (1) Checksum: every on-disk file that is recorded must
  match its stored checksum. (2) Order: let `highest` be the largest four-digit number among
  recorded filenames; any unrecorded NON-blank file numbered below `highest` raises
  `MigrationError` with code `MIGRATION_FAILED`, naming the file. The message tells the developer
  to renumber the file above `highest`, since migrations are forward-only. Pending files numbered
  above `highest` apply as usual. Blank files are exempt from check (2): they are skipped with a
  warning naming them and never recorded (R7.11), so a second run of the same set does not fail
  (Property 4).
- **Lock before table creation.** Two runners racing on `CREATE TABLE IF NOT EXISTS` can still fail
  with a unique violation in the catalog, so the lock is taken first and `ensure_table` runs under it.
- **Lock mechanics.** The Postgres store uses one connection in autocommit mode. It calls
  `pg_advisory_lock(key)` with a constant bigint key derived from the first 8 bytes of
  `sha256("recromancer.migrations")`, runs each migration inside `with conn.transaction():`, and in
  `finally` calls `pg_advisory_unlock`. A session-level lock is also released if the connection dies.
- **One transaction per file.** `apply` executes the migration SQL and the
  `INSERT INTO _schema_migrations (filename, checksum, applied_at)` in the same transaction, so a
  file is either fully applied and recorded or not at all.
- **`CREATE EXTENSION` inside a transaction.** This is allowed in Postgres, unlike
  `CREATE INDEX CONCURRENTLY`, `VACUUM` or `CREATE DATABASE`, so `0001` needs no special case. Two
  caveats: the role needs permission to create the extension (superuser, or the extension marked
  trusted; the Docker image's default user is a superuser, and managed providers need checking), and
  statements that cannot run in a transaction are unsupported in migrations. Both are documented in
  the README.
- **Multi-statement SQL.** psycopg 3 executes a string containing several statements only when no
  parameters are passed, so migration SQL is run with `conn.execute(sql)` and no parameters. The
  `INSERT` is a separate parameterised call in the same transaction.
- **mypy strict and psycopg.** `execute` expects `LiteralString`. The single place that passes file
  text uses `cast(LiteralString, migration.sql)` with a comment explaining why it is safe: the text
  comes from packaged migration files, not user input.
- **Checksums.** `hashlib.sha256(file_bytes).hexdigest()`. `.gitattributes` pins `*.sql` to LF.
- **Discovery.** `discover(directory: Traversable) -> tuple[Migration, ...]` reads names matching
  `[0-9]{4}_[a-z0-9_]+\.sql`, sorted by filename. It raises `MigrationError` for a missing directory,
  for a `.sql` file with a non-matching name, and for duplicate numeric prefixes. Non-`.sql` files
  are ignored. The default directory is `importlib.resources.files("recromancer.db") / "migrations"`,
  which works from an editable install and from a wheel.
- **Error wrapping.** The store converts `psycopg.Error` into a plain `StoreError(message)` so the
  runner never imports psycopg. The runner raises `MigrationError(MIGRATION_FAILED)` with
  `"<filename>: <original message>"`, truncated to the 500 character message limit.
- **Time.** `applied_at` comes from the injected `Clock`, so the fake store is deterministic.
- **Empty files.** Blank or whitespace-only SQL is skipped with a warning to stderr and is not
  recorded, and is exempt from the out-of-order pre-flight (R7.11). Adding content later makes it
  a non-blank pending file: it applies normally if numbered above `highest`, and raises
  `MIGRATION_FAILED` naming the file if numbered below it.
- **Forward-only.** There is no down path. Fixes are new files.

CLI (`python -m recromancer.db.migrate`, run from `backend/`): `load_dotenv()`, `Settings.from_env()`,
connect, run. Output: one line per applied file, or `no migrations pending`. Exit 0 on success. Exit
1 on a `ConfigError`, a connection failure, a missing directory or a `MigrationError`, with a
descriptive message on stderr that has passed through the secret redactor. `main(argv, *, environ,
store_factory, stdout, stderr, clock)` takes everything injectable so unit tests run it without
Postgres.

`0001_create_vector_extension.sql` contains exactly `CREATE EXTENSION IF NOT EXISTS vector;`.

### Packaging (hatchling)

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/recromancer"]
```

Hatchling includes every non-ignored file in a listed package, so `.sql` files and `py.typed` ship
without extra configuration. Nothing in `.gitignore` matches them. Two checks protect this:

- a unit test asserts `importlib.resources` finds `0001_create_vector_extension.sql` and that the
  pyproject wheel target lists `src/recromancer`;
- a CI step builds a wheel with `pip wheel --no-deps backend` and fails if the archive lacks the
  `.sql` file or `py.typed`.

### `pyproject.toml` highlights

| Section | Content |
|---|---|
| `[project]` | `requires-python = ">=3.11"`; runtime deps `fastapi`, `pydantic`, `pydantic-settings`, `httpx`, `structlog`, `psycopg[binary]`, `psycopg-pool`, plus `anyio` and `uvicorn` |
| `[project.optional-dependencies] dev` | `pytest`, `pytest-cov`, `hypothesis`, `anyio`, `ruff`, `mypy`, `import-linter` |
| `[tool.ruff]` | `line-length = 88`; `lint.select = ["E","F","I","UP","B"]` |
| `[tool.mypy]` | `strict = true`, `python_version = "3.11"`, `files = ["src", "tests"]`, `mypy_path = ["../scripts"]`, no `exclude` for tests |
| `[tool.pytest.ini_options]` | `testpaths = ["tests"]`, `pythonpath = ["../scripts"]`, `addopts = "--cov=recromancer --cov-report=term-missing"` |
| `[tool.coverage.report]` | `fail_under = 85` |
| `[tool.importlinter]` | contracts C1 to C8 |

Dependencies are pinned in `pyproject.toml` with lower bounds and a lock is out of scope; the exact
versions are chosen when the file is written, from the package indexes, not from memory. Neither
`pytest-asyncio`, `anyio[trio]` nor `asyncio_mode` appears anywhere. `tests/conftest.py` defines
`anyio_backend` returning `"asyncio"` and registers the Hypothesis profiles `default` (100
examples, no deadline) and `ci` (1000 examples, no deadline), selected by `HYPOTHESIS_PROFILE`.

Ruff and mypy cover `scripts/` too: mypy follows the imports from tests via `mypy_path`; ruff runs as
`ruff check --config backend/pyproject.toml scripts` from the repo root in CI.

### Local environment

`docker-compose.yml`:

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: recromancer
      POSTGRES_PASSWORD: recromancer
      POSTGRES_DB: recromancer
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - recromancer-pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U recromancer -d recromancer"]
      interval: 10s
      timeout: 5s
      retries: 5
volumes:
  recromancer-pgdata:
```

The credentials are hard-coded, local-only and bound to loopback. They are not interpolated from
`.env` because any `RECROMANCER_*` name in `.env` must be a real Settings field, and
`.env.example` is restricted to `RECROMANCER_` variables. A unit test checks that the compose
credentials and the Settings default URL agree.

`.env.example` (placeholders only; the default `database_url` is a local development value):

```dotenv
# Copy to .env. Never commit .env. Placeholders only.
RECROMANCER_PRODUCTION=false
RECROMANCER_DEMO_MODE=true
RECROMANCER_DATABASE_URL=postgresql://recromancer:recromancer@localhost:5432/recromancer
RECROMANCER_ADMIN_TOKEN=change-me-local-only
RECROMANCER_ANTHROPIC_API_KEY=
RECROMANCER_LOG_LEVEL=INFO
```

Secret placeholders are blank or obviously non-secret text, never token-shaped values, so the
secret scanner (which scans `.env.example`) does not flag the file. A blank
`RECROMANCER_ANTHROPIC_API_KEY` loads as `None` (R3.18).

A unit test asserts that every name in `.env.example` is a Settings field and that every Settings
field has an entry, so the file cannot drift.

### Doctor (`scripts/doctor.py`)

```mermaid
flowchart LR
  M[main] --> L[load_dotenv + read_environment]
  L --> C1[python version]
  L --> C2[docker available]
  L --> C3[config loads]
  C3 --> C4[postgres reachable]
  C4 --> C5[vector extension]
  L --> C6[.env.example vars set]
  C1 & C2 & C3 & C4 & C5 & C6 --> R[print all, exit 0 or 1]
```

| Check | Passes when | Hint on failure |
|---|---|---|
| python | 3.11 or newer | install Python 3.11+ |
| docker | `docker` on PATH and `docker version` answers within 10 s | start Docker Desktop |
| config | `Settings.from_env` succeeds | the ConfigError text (names only) |
| postgres | `DbDiagnostics.ping()` succeeds | run `docker compose up -d`, then re-run |
| vector | `has_extension("vector")` is true | run `python -m recromancer.db.migrate` from `backend/` |
| env vars | every name in `.env.example` is set, meaning present in the process environment or the `.env` file; a blank value counts as present (R6.6) | copy `.env.example` to `.env` |

Design rules:

- Each check is `Callable[[], CheckResult]` returning `ok`, `name`, `detail`, `hint`. `main` runs
  every check inside `try/except Exception`; an exception becomes a FAIL line. It prints all lines
  and exits 1 if any failed (R6.9). Checks that depend on a failed one report `FAIL ... skipped:
  config invalid`.
- Output is `OK <name> <detail>` or `FAIL <name> <hint>`.
- A variable listed in `.env.example` counts as set when it is present in the process environment
  or the `.env` file, and a blank value counts as present (R6.6). For a blank optional secret (a
  Secret_Name such as `RECROMANCER_ANTHROPIC_API_KEY`) Doctor prints an OK line whose value is
  shown as `***`. Only a variable absent from both sources is a FAIL.
- Masking is by variable name and follows R6.10 literally, with its own function in
  `scripts/doctor.py` (not in `domain/secrets.py`):

  ```python
  MASKED_SUBSTRINGS = ("secret", "password", "token", "key")

  def is_masked_env_name(name: str) -> bool:
      lowered = name.lower()
      return any(marker in lowered for marker in MASKED_SUBSTRINGS)
  ```

  A name is masked when its lower-cased form contains `secret`, `password`, `token` or `key` as a
  substring. There is no segment rule and no `tokens` allowlist, so `MONKEY` and `MAX_TOKENS` are
  masked. That is intentional: Doctor errs on the side of masking. The function lives in the
  script because the rule is a Doctor-only reading of R6.10 and is deliberately different from the
  log-key rule in `domain/secrets.py`; keeping it out of `domain` stops the two from being merged
  by accident. Masked values print `***`. A name ending in `DATABASE_URL` prints host and port only
  (R6.11; the name does not match the masking rule, so this is a separate branch checked first).
  Any other value prints as is, capped at 80 characters.
- Every line passes through `to_ascii()` (`encode("ascii", "backslashreplace")`). No Unicode check
  marks are used.
- It never reads `os.environ`: it calls `config.load_dotenv()`, `config.read_environment()` and
  `Settings.from_env()`. The database checks go through `bootstrap.build_db_diagnostics`.
- Secret values never reach any output, including exception text, which passes through the redactor
  seeded with `settings.secret_values()` when settings loaded.

### Continuous integration (`.github/workflows/ci.yml`)

Triggers: `push` (all branches) and `pull_request`. A `concurrency` group keyed on the ref cancels
superseded runs. Workflow `permissions: contents: read`. All jobs run on `ubuntu-latest` with Python 3.11.

| Job | Command | Notes |
|---|---|---|
| `lint` | `ruff check .` in `backend`; `ruff check --config backend/pyproject.toml scripts` | |
| `format` | `ruff format --check .` and the same for `scripts` | |
| `typecheck` | `mypy` in `backend` | |
| `tests` | `pytest` in `backend` | Postgres service container; env `RECROMANCER_DATABASE_URL`, `HYPOTHESIS_PROFILE=ci`; then the wheel contents check |
| `secret-scan` | `python scripts/secret_scan.py` | no install needed |
| `kiro-guard` | `git check-ignore` loop | see below |
| `import-linter` | `lint-imports` in `backend` | |
| `frontend` | lint, typecheck, test, build | runs only when `frontend/package.json` exists; not in the gate |
| `gate` | fails unless every required job succeeded | `needs` the seven required jobs |

Action pinning: every third-party action (at least `actions/checkout` and `actions/setup-python`)
must be referenced by a full commit SHA or an exact version tag, never `v4` or `main`. The exact
values must be looked up on the action's release page when the workflow is written; this design
does not guess them. A comment beside each reference records the version that the SHA corresponds to.

Other points:

- **Pip cache.** `actions/setup-python` with `cache: pip` and
  `cache-dependency-path: backend/pyproject.toml`, which keys the cache on that file's hash.
- **Postgres service.** `image: pgvector/pgvector:pg16`, the three `POSTGRES_*` variables, port
  `5432:5432`, and `--health-cmd pg_isready --health-interval 10s --health-timeout 5s
  --health-retries 5` so steps start only once it is ready. The job passes
  `postgresql://recromancer:recromancer@localhost:5432/recromancer` as `RECROMANCER_DATABASE_URL`.
- **Kiro guard.** Checks a fixed list of representative paths (`.kiro/steering/product.md`,
  `.kiro/specs/example/requirements.md`, `.kiro/hooks/example.json`, `.kiro/settings/mcp.json`)
  with `git check-ignore -q`. Exit 0 means ignored, so the step fails with an explanatory
  message for any path that is. It also fails if `git ls-files .kiro` is empty. `check-ignore` works
  on paths that do not exist yet.
- **Frontend condition.** `hashFiles` is not available in a job-level `if`, and the checkout has not
  happened yet. The job therefore always starts, checks out, runs a `detect` step that writes
  `present=true` when `frontend/package.json` exists, and guards every later step with
  `if: steps.detect.outputs.present == 'true'`. When absent the job succeeds without doing anything.
- **Gate.** `if: always()`, `needs` the seven required jobs, one step that exits 1 when any result
  is `failure`, `cancelled` or `skipped`.

### Repository hygiene

| File | Content |
|---|---|
| `LICENSE` | Standard MIT text, `Copyright (c) 2026 The Recromancer Authors` |
| `README.md` | At most three sentences describing the product, then `## Quick Start` with PowerShell commands: `git clone`, `python -m venv .venv`, `.\.venv\Scripts\Activate.ps1`, `pip install -e "backend[dev]"`, `Copy-Item .env.example .env`, `docker compose up -d`, `cd backend; python -m recromancer.db.migrate`, `cd backend; pytest`; a short troubleshooting note for a port 5432 clash with a native Postgres |
| `docs/KIRO_USAGE.md` | First heading `# Kiro Usage Journal`, then `## IDE / CLI / Web / Crew`, `## Custom agents`, `## Hooks`, `## Specs vs vibe coding`, `## Steering`, `## MCP`, `## Property-based testing`, `## Powers`, `## Session Log` |
| `.gitattributes` | `*.sql text eol=lf` and `*.py text eol=lf` |

Under `## Session Log`, one placeholder list item: `- YYYY-MM-DD: placeholder entry`.

### Secret scanner (`scripts/secret_scan.py`)

Pure core, thin shell:

```python
@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    pattern: str            # the pattern name, never the match

def scan_text(path: str, text: str) -> list[Finding]: ...
def tracked_files() -> list[str]: ...            # subprocess ["git", "ls-files", "-z"], no shell
def main(argv=None, *, list_files=tracked_files, read_file=..., out=sys.stdout) -> int: ...
```

| Name | Pattern | Minimum length source |
|---|---|---|
| `aws-access-key-id` | `AKIA[0-9A-Z]{16}` | R9.9 |
| `private-key-header` | the PEM private key header line, with optional algorithm word | R9.9 |
| `bearer-token` | `Bearer\s+[A-Za-z0-9._~+/=-]{20,}` | R9.9 gives no length; 20 chosen here |
| `github-classic-token` | `ghp_[A-Za-z0-9]{36,}` | R9.9 |
| `github-fine-grained-token` | `github_pat_[A-Za-z0-9_]{22,}` | R9.9 |
| `anthropic-api-key` | `sk-ant-[A-Za-z0-9_-]{20,}` | R9.9 |
| `huggingface-token` | `hf_[A-Za-z0-9]{30,}` | R9.9 |

Behaviour:

- Scans only the paths printed by `git ls-files`, which includes `.env.example`.
- Excludes exactly `scripts/secret_scan.py` by normalised POSIX path.
- Skips files containing a NUL byte and decodes the rest as UTF-8 with replacement.
- Prints one line per finding: `path:line: pattern-name`. It never prints the matched text or the
  line. Exits 1 if there is any finding, else 0. A summary line prints counts only.
- None of the patterns matches its own regex source text, so this design, the requirements and the
  scanner's tests do not trigger it. The PEM header is not written out in any document for the same
  reason.
- Tests build fake tokens at runtime, for example `"AKIA" + "A" * 16` or `"ghp_" + "a" * 36`, and
  feed them to `scan_text` or to `main` with an injected file list and reader, so no complete token
  literal exists in any tracked file.

## Data Models

| Model | Location | Shape |
|---|---|---|
| `ErrorCode` | `domain/errors.py` | `StrEnum`, values equal names |
| `RecromancerError` | `domain/errors.py` | `code: ErrorCode`, `message: str` (at most 500 characters) |
| `ConfigError` | `config.py` | subclass of `RecromancerError`, `CONFIG_INVALID` |
| `MigrationError` | `db/runner.py` | subclass of `RecromancerError`, `MIGRATION_FAILED` |
| `Settings` | `config.py` | frozen; fields as above |
| `ErrorBody` | `api/errors.py` | `{code: str, message: str, request_id: str}` |
| `Migration`, `RunReport` | `db/runner.py` | frozen dataclasses; `RunReport(applied, skipped, empty)` as tuples of names |
| `Finding`, `CheckResult` | scripts | frozen dataclasses |

`_schema_migrations`:

```sql
CREATE TABLE IF NOT EXISTS _schema_migrations (
    filename   text        PRIMARY KEY,
    checksum   text        NOT NULL,
    applied_at timestamptz NOT NULL
);
```

The in-memory `FakeMigrationStore` (in `tests/`) models the schema as the ordered tuple of executed
SQL texts plus a dict `filename -> (checksum, applied_at)`. A recorded file and an executed
statement always change together, mirroring the transaction. Its `apply` raises `StoreError` when
the SQL starts with `-- fail`, and records nothing in that case.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

Most requirements in this spec are configuration, wiring or one-shot behaviour, and are covered by
example, smoke and integration tests instead. Five properties survive the prework and reflection
step. Several criteria collapsed into one property: 3.5, 3.6, 3.11, 5.7 and 10.2 all say "secrets do
not leak", 4.4 and 10.3 both say "the status map is total", and 5.3, 5.4 and 10.4 all describe
request-id resolution. Criterion 7.5 is the same statement as 10.5, so one idempotency property
covers it.

### Property 1: Secret values never appear in observable output

*For any* secret string of at least 8 characters supplied as the value of any `SecretStr` setting
(`database_url` password, `admin_token`, `anthropic_api_key`), and *for any* otherwise valid or
invalid configuration it is combined with, the secret appears in none of: `repr(settings)`,
`str(settings)`, `settings.log_safe_summary()`, `settings.model_dump_json()`, the message of any
`ConfigError` raised while loading, and the JSON log output produced when the settings object and a
message that embeds the secret are logged through the configured logger. "Appears" means the raw
value, its JSON-escaped form, and for the `database_url` password its percent-encoded form. Secrets
that are substrings of the same output produced for a fixed placeholder secret are excluded by an
`assume`, so a short secret cannot match a field name.

**Validates: Requirements 3.5, 3.6, 3.11, 5.7, 10.2**

### Property 2: The error-to-status mapping is total and valid

*For any* member of `ErrorCode`, `http_status_for(member)` returns an integer that is a defined HTTP
status in the 400 to 599 range, and the set of keys of `ERROR_HTTP_STATUS` equals the set of
`ErrorCode` members, so adding a member without a mapping fails the test.

**Validates: Requirements 4.4, 10.3**

### Property 3: Request-id resolution is safe and total

*For any* header value that is absent or an arbitrary string (including strings of length 7, 8, 64
and 65, strings with a trailing newline, and non-ASCII text), `resolve_request_id(header, new_id)`
returns the header unchanged if and only if it has 8 to 64 characters, all from `A-Z a-z 0-9 . _ -`
(decided by a regex-free oracle). Otherwise it returns the generated UUID4. In every case the result
is one of those two allowed forms.

**Validates: Requirements 5.3, 5.4, 10.4**

### Property 4: Applying a migration set twice is idempotent

*For any* valid migration set (distinct, ordered names matching the file pattern, arbitrary SQL text
including blank files), running the runner a second time against the same store leaves the
executed-SQL history and the `_schema_migrations` contents identical to the state after the first
run, and the second report lists no applied files.

**Validates: Requirements 7.5, 10.5**

### Property 5: Prefix-then-rest equals all-at-once

*For any* valid migration set of size n and *for any* k from 0 to n, running the first k migrations
and then running the full set yields the same executed-SQL history and the same recorded set in
`_schema_migrations` as running the full set once on a fresh store.

**Validates: Requirements 10.6**

## Error Handling

| Situation | Behaviour | Surfaced as |
|---|---|---|
| Unknown `RECROMANCER_*` variable, invalid field, production rule violated | One `ConfigError` naming variables and fields, no values; process exits 1 in the CLI entrypoints | `CONFIG_INVALID` |
| `RecromancerError` raised in a request | Mapped status and the standard JSON body | its own code |
| Request validation failure | 422 with `VALIDATION_ERROR`; input values are never echoed | `VALIDATION_ERROR` |
| Unknown route or wrong method (`StarletteHTTPException` 404 or 405) | Own status, standard JSON body, fixed message | `NOT_FOUND`, `METHOD_NOT_ALLOWED` |
| `StarletteHTTPException` with any other status | Original status, standard JSON body, fixed message | `INTERNAL_ERROR` |
| Any exception that is not `RecromancerError`, `RequestValidationError` or `StarletteHTTPException` | 500, fixed message, traceback logged at ERROR with the allowed fields only | `INTERNAL_ERROR` |
| Database down or slow on `/readyz` | 503 `{"status": "degraded", "db": "unreachable"}`; cause logged by class name only | not an error body |
| Migration checksum mismatch | Pre-flight failure naming the file; nothing applied; CLI exit 1 | `MIGRATION_FAILED` |
| Non-blank pending migration numbered below the highest applied one (blank files are only skipped with a warning) | Pre-flight failure naming the file; nothing applied; CLI exit 1 | `MIGRATION_FAILED` |
| Database down at API startup | App starts (pool opened with `wait=False`); `/healthz` 200, `/readyz` 503 until reachable | not an error body |
| Exception propagating through the middleware | Access log line still written in `finally`, with status 500 | `INTERNAL_ERROR` |
| Migration SQL error | Transaction rolled back; error names the file and the original message, truncated to 500 characters | `MIGRATION_FAILED` |
| Database unreachable or migrations directory missing in the CLI | Descriptive stderr message, exit 1 | `MIGRATION_FAILED` |
| Another runner holds the lock | The second runner waits on `pg_advisory_lock` and then finds nothing pending | none |
| `.env` absent in development | Not an error; defaults apply and Doctor reports missing variables | none |
| Doctor check raises | That check becomes a FAIL line; remaining checks still run | exit code 1 |
| Secret scanner finds a match | Path, line number and pattern name printed; exit 1 | none |

Cross-cutting rule: error text built from external input (driver messages, exception text) passes
through the redactor before it is logged or printed, and nothing built from request bodies or
question text is placed in an exception message.

## Testing Strategy

### Approach

No PBT-from-scratch: Hypothesis supplies the properties. The split follows the prework: pure
logic gets properties plus a few examples, wiring and configuration get examples and smoke checks,
and anything that needs Postgres gets integration or contract tests. PBT is not used for the I/O
layer, CI configuration, compose, README or Doctor output.

All commands are Windows PowerShell, from the repo root with the virtual environment active:

```powershell
pip install -e "backend[dev]"
cd backend; ruff check --fix .; ruff format .
cd backend; mypy
cd backend; lint-imports
cd backend; pytest
$env:HYPOTHESIS_PROFILE="ci"; cd backend; pytest
cd backend; pytest --no-cov tests/unit/test_config.py
```

The last line matters: `fail_under = 85` applies to any run that collects coverage, so running a
single file with coverage fails the 85 percent floor. Use `--no-cov` for focused runs.

### Test layout

```text
backend/tests/
  conftest.py               anyio_backend="asyncio"; Hypothesis profiles; postgres_url fixture
  unit/                     example tests, fakes only
  property/
    invariants.py           reusable invariants, no test functions
    strategies.py           migration_sets() and other shared strategies
    test_secret_leak_property.py
    test_error_mapping_property.py
    test_request_id_property.py
    test_migration_properties.py
  integration/              need RECROMANCER_DATABASE_URL; skipped otherwise
  contract/                 one suite run against every MigrationStore implementation
```

### Property tests (Hypothesis)

Each is a single `@given` test, at least 100 examples (`default` profile) and 1000 under the `ci`
profile, tagged with `# Feature: platform-foundation, Property N: ...`. Invariants live in
`tests/property/invariants.py` and the drivers import them.

| Test | Property | Invariant function | Strategy |
|---|---|---|---|
| `test_secret_leak_property.py::test_secret_never_appears` | 1 | `assert_secret_absent(secret, *texts)` checks raw, JSON-escaped and percent-encoded forms | `secret_values()`: text of 8 to 64 characters without surrogates or NUL, paired with a field chosen from the three secret fields and an optional invalid co-field |
| `test_error_mapping_property.py::test_mapping_is_total` | 2 | `assert_mapping_total(mapping, codes)` | `st.sampled_from(list(ErrorCode))` plus a key-set equality check |
| `test_request_id_property.py::test_request_id_resolution` | 3 | `assert_request_id_valid(header, result)` using a character-set oracle | `st.none()`, valid ids, boundary lengths 7, 8, 64, 65, trailing `\n`, arbitrary text |
| `test_migration_properties.py::test_apply_twice_is_idempotent` | 4 | `assert_same_state(a, b)` | `migration_sets()` composite: 0 to 8 migrations, names `NNNN_word.sql` strictly increasing, SQL from text including blanks |
| `test_migration_properties.py::test_prefix_equivalence` | 5 | `assert_same_state(a, b)` | the same strategy, with `k = draw(integers(0, len(set)))` |

Notes:

- Property 1 builds settings through `Settings.from_env(environ=mapping)` and configures logging
  with `stream=io.StringIO()`, so no process state or real stream is touched.
- Properties 4 and 5 run against `FakeMigrationStore` with a fake clock. The same behaviours are
  also exercised on real Postgres, as three example tests, not as property tests.
- Do not use function-scoped pytest fixtures inside `@given` tests; Hypothesis flags that with a
  health check. Build the fakes inside the test body.
- mypy strict applies to tests. Hypothesis ships type hints, so `@given`, `st.composite` with
  `st.DrawFn` and `st.sampled_from` type-check; this is verified by the first CI run (see risks).
- `migration_sets()` lives in `tests/property/strategies.py`. It must never generate SQL starting
  with `-- fail`, because that marker is reserved for the fake store's failure injection (a
  generated file would randomly fail and break the idempotency and prefix properties). SQL is built
  from a safe alphabet that excludes `-` (so no SQL comment can be formed), with a `.filter` on
  `not sql.lstrip().startswith(FAIL_MARKER)` as a second guard. `FAIL_MARKER = "-- fail"` is
  defined once in the fake store module and imported by the strategy. A second strategy,
  `migration_sets_with_failures()`, deliberately includes one such file and is used only by the
  failing-run test that complements Property 4.
- Migration sets are generated with strictly increasing names, and the prefix-then-rest run in
  Property 5 only ever adds higher-numbered files, so the out-of-order pre-flight (R7.7) never
  fires inside the properties. Blank files are exempt from that pre-flight and never recorded, so
  Property 4 holds for sets that contain them. The rule is covered by the `test_runner.py`
  fake-store unit tests.

### Unit tests (`tests/unit/`)

| File | Covers |
|---|---|
| `test_scaffold.py` | R1: parses `pyproject.toml` with `tomllib`; checks directories, `py.typed`, dependencies, tool settings, no `asyncio_mode` or `pytest-asyncio` anywhere |
| `test_import_contracts.py` | R2: contracts C1 to C8 exist, C5 sets `allow_indirect_imports = true`, C2 covers `grounding` and `ingest`, C8 layers match the documented order; `lint-imports` itself runs in CI |
| `test_config.py` | R3: defaults, prefix, unknown variables, validation messages, production rules (including R3.10: admin token missing, 31 characters, 32 characters, the placeholder; not required when `demo_mode` is true) and combined errors, `production` with `demo_mode`, `log_safe_summary`; R3.18: empty and whitespace-only values for `admin_token` and `anthropic_api_key` yield `None`, an empty `RECROMANCER_ADMIN_TOKEN` fails the `admin_token` rule in production with `demo_mode` false, and an empty `database_url` is still invalid |
| `test_env_loader.py` | R3.13 to 3.15, using `tmp_path` and an injected `environ` (no real environment): dev loads without overriding variables that are already set; production never opens the file; `RECROMANCER_PRODUCTION` in the file is ignored; R3.14: a BOM before the first key parses as `RECROMANCER_...` and not `\ufeffRECROMANCER_...`; CRLF line endings leave no trailing `\r` on values and parse identically to LF; single and double quoted values are stripped and an inner `#` is preserved; comment lines and blank lines are ignored; R3.19: a UTF-16 file with BOM and invalid UTF-8 bytes both raise `ConfigError` with the fixed message and leave `environ` unchanged |
| `test_no_environ_reads.py` | R3.17 AST guard over the whole repo, plus a self-test that the guard flags a synthetic `os.environ` and ignores a string literal |
| `test_errors.py` | R4.1 to 4.3: 500 and 501 character boundary |
| `test_api_errors.py` | R4.5 to 4.10 and R5.6: all error paths (404, 405, another HTTP status such as 401 or 418 keeping its status with `INTERNAL_ERROR`, catch-all 500), header on every response, generic message, traceback logging contains no body, query string or sensitive header |
| `test_redaction_keys.py` | R5.7: `is_sensitive_key` redacts `apikey`, `apiKey`, `API_KEY`, `api_key`, `accesskey`, `privatekey`, `signingkey`, `authkey`, `accesstoken`, `db_password`, `admin_token`, `authorization`, `session_cookie`; does not redact `monkey`, `keyboard`, `input_tokens`, `output_tokens`, `max_tokens` (none of the five compound substrings occurs in these names); hyphen and upper-case variants behave the same |
| `test_doctor_masking.py` | R6.10, R6.11: `is_masked_env_name` is true for `RECROMANCER_ADMIN_TOKEN`, `RECROMANCER_ANTHROPIC_API_KEY`, `MONKEY`, `MAX_TOKENS` and a lower-case name such as `db_password`; false for `RECROMANCER_DATABASE_URL`, which the rendering code prints as host and port only; a rendered line for a masked name never contains the value; R6.6: a blank `RECROMANCER_ANTHROPIC_API_KEY` yields an OK line with `***`, and a variable absent from both the environment and `.env` yields FAIL |
| `test_docs_disabled_in_production.py` | Production attack surface: with `production=True`, `GET /docs`, `/redoc` and `/openapi.json` return 404 with code `NOT_FOUND` and the standard body; with `production=False` all three return 200 |
| `test_migration_strategy.py` | `migration_sets()` output never starts with `-- fail` (checked on the stripped text of every generated file, 100+ examples); blank files, whitespace-only files, non-contiguous numbers and the empty set all occur; every `migration_sets_with_failures()` example contains a failing file |
| `test_observability.py` | R5.1 to 5.2, 5.5, 5.7 to 5.9: required fields, context binding, redaction, `question_log_fields` |
| `test_health.py` | R5.10 to 5.13: `/healthz` calls no probe; `/readyz` ready, not ready, raising, hanging with `readyz_timeout_s=0.05` |
| `test_runner.py` | R7: discovery rules, pre-flight checksum mismatch (R7.6), out-of-order non-blank pending file (R7.7: with the fake store, record `0003`, offer pending `0002`, assert `MIGRATION_FAILED` naming `0002`, nothing applied and the store unchanged; a pending file above the highest applied number still applies), blank file `0002` with `0003` recorded (R7.11: no exception, warning naming `0002`, store unchanged, and a second identical run also succeeds), the same `0002` later filled with SQL (R7.7: `MIGRATION_FAILED` naming `0002`, nothing applied), empty file warning, error wrapping, CLI `main` exit codes and messages |
| `test_packaging.py` | R1 and R7.15: `.sql` found through `importlib.resources`; wheel target configured; initial migration text |
| `test_doctor.py` | R6.6 to 6.12: injected checks, run-all-then-exit, ASCII-only output (masking rule itself is in `test_doctor_masking.py`) |
| `test_secret_scan.py` | R9.7 to 9.13: one runtime-built token per pattern, near-miss below minimum length, self-exclusion, output has no matched text, exit codes |
| `test_repo_hygiene.py` | R6.1 to 6.5, R8, R9.1 to 9.6: compose text checks, `.env.example` versus Settings fields, the `.env.example` admin token placeholder equals `config.ADMIN_TOKEN_PLACEHOLDER` (R3.10), compose credentials versus default URL, CI workflow text (permissions, no floating tags, gate `needs`), LICENSE, README, KIRO_USAGE headings |

Async tests use `@pytest.mark.anyio` with the `anyio_backend` fixture. API tests use Starlette's
`TestClient(app, raise_server_exceptions=False)`; where async is needed, `httpx.AsyncClient` with
`ASGITransport`, which makes no network call. Time-dependent code takes an injected `Clock`; the
foundation has no retry or backoff, so no sleep function exists yet, and none is added speculatively.

### Integration tests (`tests/integration/`)

Run when `RECROMANCER_DATABASE_URL` is present in the real process environment; otherwise a
fixture calls `pytest.skip("no database URL configured (set RECROMANCER_DATABASE_URL)")`. The
conftest does not load `.env`, so a developer opts in explicitly:

```powershell
docker compose up -d
$env:RECROMANCER_DATABASE_URL="postgresql://recromancer:recromancer@localhost:5432/recromancer"
cd backend; pytest tests/integration tests/contract
```

Each test works in a throwaway schema (`CREATE SCHEMA t_<hex>`, dropped afterwards), and
`PostgresMigrationStore` takes a validated `schema` argument. Cases: real `0001` applies and the
`vector` extension exists; re-running reports `no migrations pending`; a second connection's
`pg_try_advisory_lock` fails while the runner holds the lock; a failing migration leaves neither its
effects nor a record; `PostgresReadinessProbe` returns true.

### Contract tests (`tests/contract/`)

One suite of `MigrationStore` behaviours (`ensure_table` idempotent, `applied` reflects `apply`,
failing SQL records nothing, lock is exclusive within a process) parametrised over
`FakeMigrationStore` and `PostgresMigrationStore`. A fixture supplies implementation-specific
`failing_sql` (`-- fail` for the fake, `SELECT 1/0;` for Postgres). The Postgres parameter skips
without a database URL.

### Smoke and CI-only checks

Compose, `.env.example`, the workflow file and the README are checked as text. Whether the workflow
actually runs, whether `lint-imports` catches a deliberate violation, and whether the wheel contains
the `.sql` file are verified by CI runs, not by local unit tests.

## Risks and Open Questions

| # | Item | Why it matters | Proposed handling |
|---|---|---|---|
| 1 | Coverage floor of 85 percent with a small codebase | A few untested lines move the percentage a lot, and a run without Postgres skips the adapter tests, which can drop coverage below the floor locally | Keep the Postgres adapter thin and the logic in pure modules; accept that a local run without a database may need `--no-cov`; revisit if CI cannot meet 85 |
| 2 | `fail_under` and subset runs | Any single-file run with coverage fails | Documented `--no-cov` command |
| 3 | import-linter on a src layout and contract options | It imports the package, so `recromancer` must be importable (editable install does this). `allow_indirect_imports` and the exact option names are stated from memory | The first implementation task verifies `lint-imports` runs and passes on the scaffold and checks every contract option against the installed version; fall back to `PYTHONPATH=src` if needed |
| 4 | `|` sibling syntax in `layers` | I believe siblings separated by `|` are independent, which also makes `grounding` and `ingest` independent in C8 although C2 does not require it | C7 states adapter independence explicitly, so that rule holds either way; if `grounding` and `ingest` ever need each other, split them into two layers |
| 5 | mypy strict on tests with Hypothesis and psycopg | `LiteralString` in psycopg and decorator typing in Hypothesis can need casts | One documented cast in the store; fix others as they appear |
| 6 | Windows and async psycopg | The async pool is not supported on the default Windows event loop | Sync pool in a worker thread; confirm in the psycopg docs when writing `postgres.py` |
| 7 | Extension privileges | `CREATE EXTENSION vector` needs superuser or a trusted extension; managed Postgres may differ | Fine for the Docker image; check Neon or Supabase during deployment |
| 8 | Added runtime dependencies | R1.5 does not list `uvicorn` or `anyio` | Confirm, or move them elsewhere |
| 9 | Bearer pattern length | R9.9 gives no minimum | 20 characters chosen; confirm |
| 10 | Redaction of registered secret values | Covers values loaded from Settings only, not arbitrary secrets in third-party text; also the only defence for names the key rule does not catch, such as `clientkey` | Secret-named keys and exception text are also redacted; this is defence in depth, not a guarantee |
| 11 | `.env.example` limited to `RECROMANCER_` variables | Compose cannot interpolate Postgres credentials from it | Credentials hard-coded for loopback-only local use |
| 12 | Action versions | Exact SHAs for checkout and setup-python are not known to this design | Look them up on the release pages when writing `ci.yml`; do not guess |
| 13 | Pydantic and pydantic-settings API details (`hide_input_in_errors`, `settings_customise_sources`) | Names and behaviour are from memory | Confirm against the installed version when implementing `config.py` |
