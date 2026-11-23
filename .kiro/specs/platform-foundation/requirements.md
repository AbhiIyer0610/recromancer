# Requirements Document

## Introduction

Platform Foundation establishes the production-grade backbone that every other Recromancer feature
builds on. It covers: repository scaffolding and tooling, enforced module boundaries, typed
configuration, error taxonomy, structured observability, local development environment, database
migrations, property-based testing of the foundation, CI, and repository hygiene. No product
features (ingestion, retrieval, grounding, LLM clients, frontend) are in scope. All commands are
written for Windows PowerShell.

## Glossary

- **Scaffold**: The directory layout, build system, and tooling configuration for the backend package.
- **Settings**: The typed Pydantic v2 configuration object loaded exclusively via `Settings.from_env`.
- **SecretStr**: A Pydantic type that prevents a secret value from appearing in logs, reprs, or error messages.
- **ConfigError**: The exception raised when configuration loading fails; its message lists only field names and expected types, never values.
- **Env_Loader**: The dev-only helper in `config.py` that reads a `.env` file into the process environment; it is the only code that touches the environment besides `Settings.from_env`.
- **Demo_Mode**: The `demo_mode` Settings field (default `True`) that selects a seeded, read-only workspace with write endpoints disabled, served on the real code path using the real configured model client; Demo_Mode is allowed in production.
- **Secret_Name**: An environment variable name that contains `SECRET`, `PASSWORD`, `TOKEN` or `KEY`, matched case-insensitively.
- **RecromancerError**: The project's base exception class carrying a stable, machine-readable error code.
- **ErrorCode**: An enumerated string constant that uniquely identifies a class of error.
- **Request_ID**: The identifier bound to every request, defined once in Requirement 5: the incoming `X-Request-Id` header value when it matches `^[A-Za-z0-9._-]{8,64}$`, otherwise a freshly generated UUID4.
- **Healthz**: The liveness endpoint at `GET /healthz`; confirms the process is running.
- **Readyz**: The readiness endpoint at `GET /readyz`; confirms the database is reachable through the Readiness_Probe.
- **Readiness_Probe**: A Port that reports whether the database is reachable; its Postgres implementation lives in the `db` adapter package.
- **Migration**: An ordered, versioned SQL script applied to bring the database schema to a known state. Migrations are forward-only.
- **Migration_Runner**: The Python component that applies pending migrations in order, accessing the database only through the `db` adapter package.
- **Checksum**: The SHA-256 hex digest of a migration file's bytes.
- **Doctor**: The `scripts/doctor.py` script that checks tool versions, database connectivity, vector extension presence, and required environment variables without printing secret values.
- **Secret_Scanner**: The `scripts/secret_scan.py` script that scans git-tracked files for secret patterns.
- **CI**: The GitHub Actions continuous-integration workflow.
- **CI_Gate**: The final CI job that depends on every required job and fails if any of them failed.
- **Kiro_Guard**: The CI job that fails when any `.kiro/` path is ignored by git.
- **Port**: A Python `Protocol` that defines the interface for an external dependency so the implementation can be swapped without touching service code.
- **Adapter_Package**: One of the packages `llm`, `embed`, `retrieval` or `db`.
- **Composition_Root**: The module `backend/src/recromancer/bootstrap.py`, the only module allowed to import Adapter_Packages and wire them to Ports.
- **Import_Linter**: The `import-linter` tool that enforces layer contracts over the whole `recromancer` package.
- **Invariants_Module**: `backend/tests/property/invariants.py`, which holds property invariants reused by Hypothesis drivers.
- **pgvector**: The Postgres extension that enables vector similarity search.

---

## Requirements

### Requirement 1: Repository Scaffolding

**User Story:** As a developer, I want a correctly structured Python backend package with strict
quality tooling pre-configured, so that every contributor starts from a consistent, enforceable
baseline without manual setup.

#### Acceptance Criteria

1. THE Scaffold SHALL place all backend source code under `backend/src/recromancer/` using a PEP 517 src layout.
2. THE Scaffold SHALL use hatchling as the build backend, declared in `backend/pyproject.toml`.
3. THE Scaffold SHALL declare a minimum Python version of 3.11 in `backend/pyproject.toml`.
4. THE Scaffold SHALL create the sub-packages `domain`, `grounding`, `ingest`, `retrieval`, `llm`, `embed`, `db`, `service`, `api` and `mcp`, each containing only an `__init__.py` (with an optional module docstring), and SHALL place a `py.typed` marker file in the `recromancer` root package.
5. THE Scaffold SHALL declare in `backend/pyproject.toml` the runtime dependencies `fastapi`, `pydantic`, `pydantic-settings`, `httpx`, `structlog`, `psycopg[binary]` and `psycopg-pool`, and a `[dev]` optional dependency group that contains at least `pytest`, `pytest-cov`, `hypothesis`, `anyio`, `ruff`, `mypy` and `import-linter` and MAY contain additional test-support packages (for example `httpx2`, required by the Starlette test client).
6. THE Scaffold SHALL configure ruff with at minimum the `E`, `F`, `I`, `UP` and `B` rule sets enabled, and `line-length = 88`.
7. THE Scaffold SHALL configure mypy in `backend/pyproject.toml` with `strict = true` and `python_version = "3.11"`, and SHALL configure mypy to check both `src` and `tests` with no `exclude` entry for the `tests/` directory tree.
8. THE Scaffold SHALL configure pytest with `testpaths = ["tests"]` and `addopts = "--cov=recromancer --cov-report=term-missing"`, and SHALL set `fail_under = 85` under `[tool.coverage.report]`.
9. THE Scaffold SHALL organise tests under `backend/tests/` with an `__init__.py` in `tests/` itself and sub-directories `unit/`, `property/`, `integration/` and `contract/`, each containing an `__init__.py`.
10. THE Scaffold SHALL use `anyio` with its pytest plugin as the only async test framework, SHALL provide an `anyio_backend` fixture fixed to `"asyncio"` in `backend/tests/conftest.py`, and SHALL declare neither `pytest-asyncio` nor `anyio[trio]` nor any `asyncio_mode` setting in any dependency or configuration file (pyproject.toml, requirements files, conftest.py and CI workflow files).
11. WHEN the command `pip install -e "backend[dev]"` is run from the repo root in a PowerShell session, THE Scaffold SHALL install the package and all dev dependencies without error.
12. WHEN the command `cd backend; ruff check .` is run, THE Scaffold SHALL produce zero lint errors on the initial codebase.
13. WHEN the command `cd backend; mypy` is run, THE Scaffold SHALL produce zero type errors on the initial codebase, including the `tests/` tree.

---

### Requirement 2: Module Dependency Rule

**User Story:** As a developer, I want the dependency boundaries between layers enforced automatically,
so that no adapter or framework leaks into domain logic and the architecture stays hexagonal.

#### Acceptance Criteria

1. THE Scaffold SHALL ensure that `domain`, `grounding` and `ingest` import nothing from the `recromancer` package except `domain`.
2. THE Scaffold SHALL ensure that `service` imports only from `domain`, `grounding`, Port modules, and its own sub-packages.
3. THE Scaffold SHALL ensure that `api` and `mcp` import from `service` and `domain` only within the `recromancer` package, and never import an Adapter_Package.
4. THE Scaffold SHALL ensure that the Composition_Root `backend/src/recromancer/bootstrap.py` is the only module that imports Adapter_Packages (`llm`, `embed`, `retrieval`, `db`).
5. THE Scaffold SHALL ensure that `config.py` imports only from `domain`, the Python standard library and `pydantic` (including `pydantic-settings`).
6. THE Scaffold SHALL ensure that no Adapter_Package imports another Adapter_Package, and that no module imports upward against the layer order defined in `structure.md`.
7. THE Scaffold SHALL declare Import_Linter contracts in `backend/pyproject.toml` that mirror the dependency rule in `structure.md`, with `root_package = "recromancer"` so that the whole package is analysed.
8. WHEN the command `cd backend; lint-imports` is run, THE Import_Linter SHALL exit with code 0 on the initial codebase.
9. WHEN a developer introduces an import that violates an Import_Linter contract, THE CI SHALL fail the import-linter job and report the violating import.

---

### Requirement 3: Typed Configuration

**User Story:** As an operator, I want all configuration to flow through a single typed object loaded
from environment variables, so that misconfiguration is caught at startup and secrets never leak into
logs or error output.

#### Acceptance Criteria

1. THE Settings SHALL be defined as a Pydantic v2 `BaseSettings` subclass in `backend/src/recromancer/config.py`.
2. THE Settings SHALL be loadable exclusively via a `Settings.from_env()` classmethod, and no code outside `config.py` SHALL read `os.environ` or call `os.getenv`.
3. THE Settings SHALL use the environment variable prefix `RECROMANCER_` for all fields.
4. WHEN `Settings.from_env()` is called and the environment contains a `RECROMANCER_`-prefixed variable that does not correspond to a Settings field, THE Settings SHALL raise a ConfigError naming the unknown variable names; the check SHALL be performed explicitly by comparing environment variable names with field names and SHALL NOT rely on `extra="forbid"` alone.
5. THE Settings SHALL store all secret fields (database password, admin token, API keys) as `pydantic.SecretStr`.
6. IF validation fails while loading Settings, THEN THE Settings SHALL raise a ConfigError whose message lists only the failing field names and expected types, and SHALL set `hide_input_in_errors=True` so that no input value appears in the message.
7. THE Settings SHALL include a boolean `demo_mode` field that defaults to `True` and a boolean `production` field that defaults to `False`.
8. THE Settings SHALL provide a `database_url` field of type `SecretStr` with a default value of `postgresql://recromancer:recromancer@127.0.0.1:5432/recromancer` for local development.
9. WHILE `production` is `True`, THE Settings SHALL require `database_url` to be set explicitly and SHALL reject a `database_url` equal to the local default value.
10. WHILE `production` is `True` and `demo_mode` is `False`, THE Settings SHALL require `admin_token` to be set, to be at least 32 characters long, and to differ from the placeholder value used for the admin token in `.env.example`; a violation SHALL be reported by a ConfigError that names the `admin_token` field and never its value.
11. IF more than one cross-field production rule is violated, THEN THE Settings SHALL raise a single ConfigError that names all conflicting fields.
12. THE Settings SHALL provide a `log_safe_summary()` method that returns a mapping in which every SecretStr field is replaced by the text `***` and `database_url` is represented by its host and port only.
13. WHERE the `production` flag, read from the real process environment variable `RECROMANCER_PRODUCTION` and never from a `.env` file, is `False`, THE Env_Loader SHALL load variables from a `.env` file at the repo root into the process environment before `Settings.from_env()` reads them, without overriding variables that are already set.
14. WHEN THE Env_Loader reads a `.env` file, THE Env_Loader SHALL decode it as UTF-8 and tolerate a leading byte order mark (encoding `utf-8-sig`), and SHALL strip a trailing carriage return from each line so that files with CRLF line endings are parsed identically to files with LF line endings.
15. WHILE the `production` flag, read from the real process environment variable `RECROMANCER_PRODUCTION` and never from a `.env` file, is `True`, THE Env_Loader SHALL be disabled and SHALL NOT read any `.env` file.
16. THE application entrypoint and `scripts/doctor.py` SHALL obtain configuration only through the Env_Loader and `Settings.from_env()`.
17. THE Scaffold SHALL include a unit test under `backend/tests/unit/` that parses every Python file in the repository other than `backend/src/recromancer/config.py` with the `ast` module and fails IF any `ast.Attribute` node whose value is the name `os` and whose attribute is `environ` or `getenv` is found.
18. WHEN the value of an optional secret field (`admin_token` or `anthropic_api_key`) is an empty or whitespace-only string, THE Settings SHALL treat the field as unset (`None`); WHILE `production` is `True` and `demo_mode` is `False`, an empty or whitespace-only `admin_token` SHALL therefore count as missing under the `admin_token` rule of criterion 3.10.
19. IF the `.env` file read by the Env_Loader cannot be decoded as UTF-8 (for example a UTF-16 file written by PowerShell `>`), THEN THE Env_Loader SHALL raise a ConfigError with the fixed message ".env must be UTF-8 text. Re-save it as UTF-8 (PowerShell `>` writes UTF-16)." and the message SHALL NOT include file content, byte values or decoder details, and no variable from the file SHALL be loaded into the environment.

---

### Requirement 4: Error Taxonomy

**User Story:** As an API consumer, I want every error to carry a stable machine-readable code and a
predictable HTTP status, so that clients can handle errors programmatically without parsing message
strings.

#### Acceptance Criteria

1. THE Error_Taxonomy SHALL define a `RecromancerError` base exception class in `backend/src/recromancer/domain/errors.py`.
2. THE Error_Taxonomy SHALL define an `ErrorCode` enumeration in the same module with one member per distinct error class, including `INTERNAL_ERROR`, `VALIDATION_ERROR`, `NOT_FOUND` and `METHOD_NOT_ALLOWED`.
3. WHEN a `RecromancerError` is constructed, THE Error_Taxonomy SHALL require an `ErrorCode` member and a human-readable `message` string of at most 500 characters.
4. THE Error_Taxonomy SHALL define a single mapping from `ErrorCode` to HTTP status code, located in `backend/src/recromancer/api/` and nowhere else.
5. WHEN a `RecromancerError` reaches the FastAPI exception handler, THE API SHALL return the mapped HTTP status code and a JSON body `{"code": "<ErrorCode.value>", "message": "<message>", "request_id": "<Request_ID>"}`.
6. WHEN a `RequestValidationError` reaches the FastAPI exception handler, THE API SHALL return HTTP 422 and a JSON body of the same shape with code `VALIDATION_ERROR`.
7. WHEN a `StarletteHTTPException` with status 404 or 405 reaches the FastAPI exception handler, THE API SHALL return that exception's own HTTP status and a JSON body `{"code", "message", "request_id"}` with code `NOT_FOUND` for 404 or `METHOD_NOT_ALLOWED` for 405, and a fixed message that never includes the exception detail.
8. WHEN a `StarletteHTTPException` with any status other than 404 or 405 reaches the FastAPI exception handler, THE API SHALL return that exception's original HTTP status and a JSON body of the same shape with code `INTERNAL_ERROR` and a fixed message that never includes the exception detail.
9. IF an exception that is none of `RecromancerError`, `RequestValidationError` or `StarletteHTTPException` reaches the FastAPI exception handler, THEN THE API SHALL return HTTP 500 and a JSON body of the same shape with code `INTERNAL_ERROR` and a fixed generic message that never includes exception text.
10. WHEN THE API logs a traceback, THE API SHALL log it at ERROR level without the request body, without query parameters, and without any header whose name contains `authorization`, `token`, `secret` or `cookie` (case-insensitive).

---

### Requirement 5: Structured Observability

**User Story:** As an operator, I want every log line to be structured JSON and every request to be
traceable by a unique ID, so that I can search and correlate log events without parsing free-form text.

#### Acceptance Criteria

1. THE Observability SHALL configure the root Python logger to emit newline-delimited JSON via `structlog` or an equivalent stdlib-based JSON formatter.
2. THE Observability SHALL include in every JSON log record the fields `timestamp` (ISO-8601, UTC), `level`, `logger` and `message`.
3. WHEN a request is received with an `X-Request-Id` header whose value matches `^[A-Za-z0-9._-]{8,64}$`, THE API SHALL use that value as the Request_ID.
4. WHEN a request is received with no `X-Request-Id` header or with a value that does not match `^[A-Za-z0-9._-]{8,64}$`, THE API SHALL generate a UUID4 and use it as the Request_ID.
5. THE API SHALL bind the Request_ID to the logging context for the duration of the request and include it in every log entry emitted while handling that request.
6. THE API SHALL return the Request_ID in the `X-Request-Id` response header on every response, including error responses.
7. THE Observability SHALL exclude secret values from every log entry.
8. THE Observability SHALL exclude raw question text from every log entry.
9. WHEN question text must be referenced in a log entry, THE Observability SHALL log only its character length and the Request_ID.
10. WHEN `GET /healthz` is called, THE API SHALL return HTTP 200 with body `{"status": "ok"}` without performing database, network or filesystem I/O.
11. WHEN `GET /readyz` is called, THE API SHALL query the Readiness_Probe Port with a timeout of 5 seconds.
12. WHEN `GET /readyz` is called and the Readiness_Probe reports the database as reachable, THE API SHALL return HTTP 200 with body `{"status": "ok", "db": "ok"}`.
13. IF `GET /readyz` is called and the Readiness_Probe reports the database as unreachable or does not answer within 5 seconds, THEN THE API SHALL return HTTP 503 with body `{"status": "degraded", "db": "unreachable"}`.
14. THE Readiness_Probe Port SHALL be defined outside the `db` package, and its Postgres implementation SHALL live in the `db` adapter package and be wired only in the Composition_Root.

---

### Requirement 6: Local Development Environment

**User Story:** As a developer on Windows, I want a single `docker compose up` to give me a working
Postgres instance with pgvector, and a doctor script that tells me what is wrong before I open the
first issue, so that local setup takes minutes and not hours.

#### Acceptance Criteria

1. THE Local_Environment SHALL provide a `docker-compose.yml` at the repo root that declares a service using the image `pgvector/pgvector:pg16`.
2. THE Local_Environment SHALL publish the Postgres port as `127.0.0.1:5432:5432` so that the database is reachable from the local machine only.
3. THE Local_Environment SHALL configure the Postgres service with a named Docker volume so that data persists across container restarts.
4. THE Local_Environment SHALL configure a Docker healthcheck on the Postgres service using `pg_isready` with `interval: 10s`, `timeout: 5s` and `retries: 5`.
5. THE Local_Environment SHALL provide a `.env.example` file at the repo root listing every `RECROMANCER_` environment variable required for local development with placeholder values and no real secrets.
6. WHEN `python scripts/doctor.py` is run, THE Doctor SHALL check: Python version (3.11 or newer), Docker availability, Postgres connectivity using the configured `RECROMANCER_DATABASE_URL`, presence of the `vector` extension in the target database, and that every variable listed in `.env.example` is set, where "set" means present in the process environment or in the `.env` file, and a blank value counts as present.
7. WHEN a Doctor check passes, THE Doctor SHALL print one line per check beginning with `OK` followed by the check name and the resolved non-secret value.
8. WHEN a Doctor check fails, THE Doctor SHALL print one line beginning with `FAIL` followed by the check name and a human-readable remediation hint.
9. THE Doctor SHALL run every check before exiting, and SHALL exit with a non-zero exit code if at least one check failed.
10. THE Doctor SHALL decide whether to mask a value by the variable name, masking every variable that is a Secret_Name, and SHALL never print the value of a Secret_Name variable to standard output or standard error.
11. THE Doctor SHALL display the database URL as host and port only.
12. THE Doctor SHALL use only ASCII characters in its output.

---

### Requirement 7: Database Migrations

**User Story:** As a developer, I want database schema changes tracked as ordered, versioned SQL
files applied by a simple runner, so that every environment (local, CI, production) reaches the
same schema state without depending on a heavy migration framework.

#### Acceptance Criteria

1. THE Migration_Runner SHALL discover migration files from `backend/src/recromancer/db/migrations/` whose names match `[0-9]{4}_[a-z0-9_]+\.sql`, ordered lexicographically by filename.
2. THE Migration_Runner SHALL access the database only through the `db` adapter package, using the psycopg 3 driver, and the psycopg dependency SHALL be imported only inside the `db` package.
3. THE Migration_Runner SHALL create the `_schema_migrations` table if it does not exist, with columns for the migration filename, its Checksum, and the UTC timestamp of application.
4. WHEN the Migration_Runner starts applying migrations, THE Migration_Runner SHALL acquire a Postgres advisory lock with `pg_advisory_lock` and hold it for the whole run, so that concurrent runners serialise.
5. WHEN a migration is already recorded in `_schema_migrations` and its current Checksum equals the stored Checksum, THE Migration_Runner SHALL skip it without error.
6. IF an already-applied migration file has a Checksum that differs from the stored Checksum, THEN THE Migration_Runner SHALL fail with an error naming that file and exit with a non-zero code without applying further migrations.
7. IF a non-empty pending (not yet recorded) migration file has a number lower than the highest numbered migration already recorded in `_schema_migrations`, THEN THE Migration_Runner SHALL fail with an error having code `MIGRATION_FAILED` that names the file, and SHALL apply no further migrations.
8. WHEN a pending migration file is applied, THE Migration_Runner SHALL execute it, together with its `_schema_migrations` record, in one transaction.
9. IF a migration raises a database error, THEN THE Migration_Runner SHALL roll back that migration's transaction and raise an exception that includes the migration filename and the original database error message.
10. THE Migration_Runner SHALL treat migrations as forward-only: it SHALL provide no down migrations, and schema fixes SHALL be made by adding new migration files.
11. WHEN a migration file is empty, THE Migration_Runner SHALL skip it without recording it, write a warning naming the file to standard error, and exclude it from the out-of-order check of criterion 7.
12. THE Migration_Runner SHALL be invokable as `python -m recromancer.db.migrate` from the `backend/` directory.
13. IF the database connection fails or the migrations directory is missing, THEN THE Migration_Runner SHALL print a descriptive error and exit with code 1.
14. WHEN the Migration_Runner is run and no migrations are pending, THE Migration_Runner SHALL print a message containing `no migrations pending` and exit with code 0.
15. THE initial migration file SHALL be named `0001_create_vector_extension.sql` and SHALL contain `CREATE EXTENSION IF NOT EXISTS vector;`.
16. IF a migration file starts with a UTF-8 byte order mark or cannot be decoded as UTF-8, THEN THE Migration_Runner SHALL reject it with a MigrationError that names the file and contains no decoder details.

---

### Requirement 8: Continuous Integration

**User Story:** As a contributor, I want every push to run the full quality suite automatically, so
that regressions and secret leaks are caught before they reach the main branch.

#### Acceptance Criteria

1. THE CI SHALL provide a GitHub Actions workflow file at `.github/workflows/ci.yml` that triggers on `push` to any branch and on `pull_request`.
2. THE CI SHALL declare workflow-level `permissions: contents: read`.
3. THE CI SHALL reference every third-party action pinned to a full commit SHA or an exact version tag (for example `v4.2.2`), and SHALL NOT use floating tags such as `v4` or `main`.
4. THE CI SHALL define these jobs and run them in parallel: lint (`ruff check`), format check (`ruff format --check`), type check (`mypy`), tests (`pytest`), secret scan (`python scripts/secret_scan.py`), Kiro_Guard, and import-linter (`lint-imports`).
5. THE CI SHALL define a CI_Gate job that depends on all jobs listed in criterion 4 and fails IF any of them failed or was cancelled.
6. WHEN any CI job fails, THE CI SHALL fail the workflow and report the failing job name and its output.
7. THE CI SHALL run on `ubuntu-latest` with Python 3.11.
8. THE CI SHALL start a Postgres service container using the image `pgvector/pgvector:pg16` for the tests job and SHALL pass its connection URL to the integration tests through `RECROMANCER_DATABASE_URL`.
9. THE CI SHALL set the `HYPOTHESIS_PROFILE` environment variable to `ci` when running pytest.
10. THE CI SHALL cache pip dependencies keyed on the hash of `backend/pyproject.toml`.
11. WHEN the Kiro_Guard job runs, THE Kiro_Guard SHALL run `git check-ignore` on `.kiro/` paths (for example `.kiro/steering/product.md`) and SHALL fail with an explanatory message IF any of those paths is ignored.
12. WHERE the file `frontend/package.json` exists, THE CI SHALL run a frontend job (lint, typecheck, test, build); the frontend job SHALL NOT be a required dependency of the CI_Gate until the seance-ui spec is implemented.

---

### Requirement 9: Repository Hygiene

**User Story:** As a submitter, I want the repository to have an MIT license, a clear README, a
KIRO_USAGE journal, and a working secret scanner in place from the first commit, so that the project
meets hackathon submission requirements and is welcoming to evaluators.

#### Acceptance Criteria

1. THE Repository SHALL contain a `LICENSE` file at the repo root with the MIT license text, the copyright holder `The Recromancer Authors`, and the year 2026.
2. THE Repository SHALL contain a `README.md` at the repo root.
3. THE README SHALL describe what Recromancer does in three sentences or fewer before the Quick Start section.
4. THE README SHALL include a Quick Start section with Windows PowerShell commands covering: cloning the repo, creating a virtual environment, running `pip install -e "backend[dev]"`, copying `.env.example` to `.env`, starting Postgres with `docker compose up -d`, running migrations with `python -m recromancer.db.migrate` from `backend/`, and running the test suite with `cd backend; pytest`.
5. THE Repository SHALL contain a `docs/KIRO_USAGE.md` file whose first heading is `# Kiro Usage Journal` and which contains the section headings `## IDE / CLI / Web / Crew`, `## Custom agents`, `## Hooks`, `## Specs vs vibe coding`, `## Steering`, `## MCP`, `## Property-based testing`, `## Powers` and `## Session Log`.
6. THE `## Session Log` section SHALL contain one placeholder entry formatted as a Markdown list item that starts with a `YYYY-MM-DD` placeholder.
7. THE Secret_Scanner SHALL be provided as `scripts/secret_scan.py` and SHALL scan only the files listed by `git ls-files`.
8. THE Secret_Scanner SHALL scan `.env.example` and SHALL NOT exclude it.
9. THE Secret_Scanner SHALL detect these patterns: `AKIA[0-9A-Z]{16}`, a PEM private key header, a `Bearer` token header, `ghp_[A-Za-z0-9]{36,}`, `github_pat_[A-Za-z0-9_]{22,}`, `sk-ant-[A-Za-z0-9_-]{20,}` and `hf_[A-Za-z0-9]{30,}`.
10. THE Secret_Scanner SHALL exclude its own file from scanning so that its pattern definitions do not trigger a match.
11. IF the Secret_Scanner finds at least one match, THEN THE Secret_Scanner SHALL exit with a non-zero code.
12. WHEN THE Secret_Scanner reports a match, THE Secret_Scanner SHALL print only the file path, the line number and the pattern name, and SHALL NOT print the matched text.
13. THE Secret_Scanner tests SHALL construct fake tokens at runtime (for example by string concatenation or repetition) so that no complete token literal appears in any git-tracked file, including the test files.

---

### Requirement 10: Foundation Testing

**User Story:** As a maintainer, I want the foundation's safety guarantees verified by property-based
and deterministic tests, so that secret handling, error mapping, request tracing and migrations
cannot regress silently.

#### Acceptance Criteria

1. THE Test_Suite SHALL place Hypothesis property tests in `backend/tests/property/` and define their reusable invariants in the Invariants_Module.
2. FOR ALL arbitrary secret string values supplied to a SecretStr setting, THE Test_Suite SHALL verify that the value appears in no captured log record, no `repr` or `log_safe_summary()` output, and no ConfigError message.
3. FOR ALL members of `ErrorCode`, THE Test_Suite SHALL verify that the error-to-HTTP-status mapping returns a valid HTTP status code, by enumerating the enumeration so that the mapping is total.
4. FOR ALL arbitrary `X-Request-Id` header strings, THE Test_Suite SHALL verify that the resulting Request_ID either equals the header when it matches `^[A-Za-z0-9._-]{8,64}$` or is a UUID4 otherwise, and that the resulting Request_ID always matches an allowed form.
5. FOR ALL migration sets, THE Test_Suite SHALL verify that applying the full migration set twice leaves the schema and the `_schema_migrations` contents unchanged after the second run (idempotency property).
6. FOR ALL prefix lengths k from 0 to the number of migrations, THE Test_Suite SHALL verify that applying the first k migrations and then applying the remaining migrations yields the same schema and the same recorded set in `_schema_migrations` as applying all migrations at once (prefix-equivalence property).
7. THE Test_Suite SHALL make no real network calls.
8. THE Test_Suite SHALL give every test of time-dependent code an injected clock and sleep function so that results are deterministic.
9. WHERE `RECROMANCER_DATABASE_URL` is configured, THE Test_Suite SHALL run database integration tests against that Postgres instance, which in CI is the Postgres service container.
10. IF `RECROMANCER_DATABASE_URL` is not configured, THEN THE Test_Suite SHALL skip database integration tests and report a skip reason stating that no database URL is configured.
