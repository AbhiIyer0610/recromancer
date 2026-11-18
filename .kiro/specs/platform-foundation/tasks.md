# Implementation Plan: Platform Foundation

## Overview

This plan builds the foundation bottom-up so each task uses only what earlier tasks created and no
code is left unwired: scaffold and import contracts, then `domain`, `config`, `observability`,
`api`, the local database environment (`docker-compose.yml` and `.env.example`, task 8.0) with `db`, the composition root (`bootstrap.py`), Doctor, the secret scanner, CI
and repository hygiene. The implementation language is Python 3.11+ (as fixed by the design).

Conventions that apply to every task:

- All commands are Windows PowerShell, run from the repo root with the virtual environment active.
  Use `cd backend; <command>` for backend tooling.
- Each top-level task ends with `ruff check`, `ruff format --check`, `mypy`, `lint-imports` and
  `pytest` all passing, and with the coverage floor (`fail_under = 85`) holding. For a focused run
  use `cd backend; pytest --no-cov <path>`; the floor is checked on full runs only.
- No real network in tests. Time-dependent code takes an injected `Clock`. Tests never read the
  real environment except where a task says so (the database URL fixture).
- Database integration and contract tests for Postgres skip with the reason
  `no database URL configured (set RECROMANCER_DATABASE_URL)` when the variable is unset.
- Never put a complete secret-shaped token literal in any tracked file; build fake tokens at
  runtime (for example `"ghp_" + "a" * 36`).
- Library APIs, option names and versions in the design were stated from memory. Verify each
  against the installed version in the first task that uses it, and never guess a version or an
  action SHA.
- Property-based tests are required, not optional, because Requirement 10 demands them. They are
  sub-tasks of the code they test and are not marked with `*`.

## Tasks

- [x] 1. Scaffold the package and enforce module boundaries
  - [x] 1.1 Create the backend scaffold and prove the toolchain on it
    - Create `backend/pyproject.toml`: hatchling build backend; `requires-python = ">=3.11"`; wheel target `packages = ["src/recromancer"]`; runtime dependencies `fastapi`, `pydantic`, `pydantic-settings`, `httpx`, `structlog`, `psycopg[binary]`, `psycopg-pool`, plus `anyio` and `uvicorn` (design risk 8); `[dev]` extra with `pytest`, `pytest-cov`, `hypothesis`, `anyio`, `ruff`, `mypy`, `import-linter`. Choose lower bounds from the package index (for example `pip index versions <name>` or the resolved `pip install` output), not from memory.
    - Configure ruff (`line-length = 88`, `lint.select = ["E","F","I","UP","B"]`), mypy (`strict = true`, `python_version = "3.11"`, `files = ["src", "tests"]`, `mypy_path = ["../scripts"]`, no `exclude` for tests), pytest (`testpaths = ["tests"]`, `pythonpath = ["../scripts"]`, `addopts = "--cov=recromancer --cov-report=term-missing"`) and `[tool.coverage.report] fail_under = 85`. Declare no `pytest-asyncio`, no `anyio[trio]` and no `asyncio_mode`.
    - Create `backend/src/recromancer/__init__.py`, `py.typed`, and the ten sub-packages `domain`, `grounding`, `ingest`, `retrieval`, `llm`, `embed`, `db`, `service`, `api`, `mcp`, each with only an `__init__.py` (optional docstring).
    - Create `backend/tests/__init__.py`, the sub-directories `unit/`, `property/`, `integration/`, `contract/` each with `__init__.py`, and `backend/tests/conftest.py` with the `anyio_backend` fixture returning `"asyncio"` and the Hypothesis profiles `default` (100 examples, no deadline) and `ci` (1000 examples, no deadline) selected by `HYPOTHESIS_PROFILE`. Make sure `scripts/` exists (it may be empty).
    - Write `backend/tests/unit/test_scaffold.py`: parse `pyproject.toml` with `tomllib` and check the build backend, Python floor, dependency lists, ruff, mypy, pytest and coverage settings, the ten sub-packages and `py.typed`, the `tests/` tree, and that neither `pytest-asyncio`, `anyio[trio]` nor `asyncio_mode` appears in the dependency and configuration files only: `backend/pyproject.toml`, any `requirements*.txt`, `backend/tests/conftest.py` and `.github/workflows/*.yml`. The check must never scan docs, specs, `.kiro/` or the test file itself, because those legitimately name the forbidden strings.
    - Proof of toolchain: run `pip install -e "backend[dev]"` from the repo root, then `cd backend; ruff check .; ruff format --check .; mypy; pytest; lint-imports` and confirm all five run and exit 0. Check that `pytest` exits 0 with the coverage floor on a package with no executable statements (the `test_scaffold.py` test guarantees at least one test is collected); if coverage reports no data or fails the floor on the empty scaffold, record the cause and apply the smallest fix that keeps `fail_under = 85`.
    - Confirm `lint-imports` can find `recromancer` from the editable install and that a minimal `[tool.importlinter]` block (`root_package = "recromancer"`) with no contracts, or with one trivial contract, is accepted. If it cannot import the package, fall back to `$env:PYTHONPATH="src"` and note it for `docs/DEV_SETUP.md`.
    - Check the installed `import-linter` version (`pip show import-linter`) and its documentation for the contract option names the design states from memory: `type = "forbidden"`, `"independence"` and `"layers"`; `source_modules`, `forbidden_modules`, `allow_indirect_imports`, `include_external_packages`, `containers`, `layers` with the `|` sibling syntax, and how contracts treat modules that do not exist yet. Write the findings as a short comment block at the top of the `[tool.importlinter]` section.
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13, 2.7, 2.8_

  - [x] 1.2 Declare import-linter contracts C1 to C8
    - In `backend/pyproject.toml` set `root_package = "recromancer"` and `include_external_packages = true`, and add contracts C1 `domain-is-pure`, C2 `pure-logic-imports-domain-only`, C3 `config-is-narrow`, C4 `service-uses-ports-only`, C5 `delivery-skips-adapters` (`allow_indirect_imports = true`), C6 `only-bootstrap-wires-adapters`, C7 `adapters-are-independent` and C8 `layer-order` exactly as tabulated in the design, listing every module explicitly, using the option names confirmed in task 1.1.
    - If `import-linter` rejects contracts that name modules that do not exist yet (`config`, `observability`, `bootstrap`), create docstring-only stub modules `config.py`, `observability.py` and `bootstrap.py` in `backend/src/recromancer/`; later tasks replace the stubs. Otherwise create nothing extra.
    - Write `backend/tests/unit/test_import_contracts.py`: parse `pyproject.toml` and assert that C1 to C8 exist by name, C5 sets `allow_indirect_imports = true`, C2 covers `grounding` and `ingest`, C6 lists `llm`, `embed`, `retrieval` and `db` as forbidden, C7 independence covers the four adapters, and C8 layers equal the documented order. Add a negative check that runs `lint-imports` through `subprocess` (no shell) against a temporary copy or by asserting exit code 0 on the real tree, whichever the installed version supports reliably.
    - Verify the contracts catch a violation: temporarily add `import recromancer.service` to `domain/__init__.py`, confirm `lint-imports` fails and names the import, then remove it. Re-run the full toolchain.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

- [x] 2. Implement the domain error taxonomy and log-key rule
  - [x] 2.1 Implement `domain/errors.py`
    - Define `ErrorCode(StrEnum)` with `VALIDATION_ERROR`, `NOT_FOUND`, `METHOD_NOT_ALLOWED`, `CONFIG_INVALID`, `MIGRATION_FAILED`, `DEPENDENCY_UNAVAILABLE`, `INTERNAL_ERROR`, values equal to names.
    - Define `RecromancerError(Exception)` with `MAX_MESSAGE_LENGTH = 500`, `code` and `message` attributes, and a constructor that raises `ValueError` (text states only the length) when `len(message) > 500`.
    - Use stdlib only; `lint-imports` must still pass.
    - _Requirements: 4.1, 4.2, 4.3, 2.1_

  - [x] 2.2 Write unit tests for the error taxonomy
    - Create `backend/tests/unit/test_errors.py`: members present, values equal names, 500-character message accepted, 501-character message rejected with a `ValueError` that does not contain the message text, `code` and `message` attributes preserved.
    - _Requirements: 4.1, 4.2, 4.3_

  - [x] 2.3 Implement `domain/secrets.py`
    - Implement `SECRET_SUBSTRINGS`, `SECRET_SEGMENTS`, `COUNTER_SUFFIX`, `name_segments` and `is_sensitive_key` exactly as specified in the design (allowlist on last segment `tokens` first, then substring markers, then whole-segment markers). Expose `is_sensitive_key` as the single public function.
    - _Requirements: 5.7, 2.1_

  - [x] 2.4 Write unit tests `test_redaction_keys.py`
    - Create `backend/tests/unit/test_redaction_keys.py`: redacted for `apikey`, `apiKey`, `API_KEY`, `api_key`, `accesskey`, `privatekey`, `signingkey`, `authkey`, `accesstoken`, `db_password`, `admin_token`, `authorization`, `session_cookie`; not redacted for `monkey`, `keyboard`, `input_tokens`, `output_tokens`, `max_tokens`; hyphen and upper-case variants behave the same as their snake_case forms.
    - _Requirements: 5.7_

- [ ] 3. Implement typed configuration and the Env_Loader
  - [x] 3.1 Implement `Settings`, `ConfigError` and `from_env` in `config.py`
    - Replace the stub (if any). Import only `domain`, the stdlib and `pydantic` / `pydantic-settings`. Define `DEFAULT_DATABASE_URL`, `ADMIN_TOKEN_PLACEHOLDER = "change-me-local-only"`, `ConfigError(RecromancerError)` with `ErrorCode.CONFIG_INVALID`, and frozen `Settings` with fields `production` (False), `demo_mode` (True), `database_url` (`SecretStr`, default the local URL), `admin_token` and `anthropic_api_key` (`SecretStr | None`), `log_level`. For the optional secret fields `admin_token` and `anthropic_api_key`, an empty or whitespace-only string is treated as unset (`None`); implement this in a `field_validator(mode="before")` so it runs before the production rules (in production an empty `admin_token` therefore counts as missing).
    - Before writing, verify against the installed `pydantic` and `pydantic-settings` versions the names the design states from memory: `SettingsConfigDict` keys (`env_prefix`, `extra`, `hide_input_in_errors`, `frozen`, `case_sensitive`), `settings_customise_sources`, `ValidationError.errors(include_input=False, include_url=False, include_context=False)`, and `model_fields_set` behaviour.
    - `Settings.from_env(environ=None)`: copy the process environment when `environ` is `None`; return only the init source from `settings_customise_sources`; explicitly compare `RECROMANCER_`-prefixed names with field names to find unknown variables; build kwargs; on `ValidationError` collect `(field, expected type)` from the annotation, never from `msg`; raise one `ConfigError` (`from None`) naming unknown variables and failing fields.
    - Add the `database_url` validator (scheme `postgresql` or `postgres`, host present, fixed message without the value) and the production rules that collect all violations before raising a single `ConfigError`: `database_url` unset or equal to the default, and `admin_token` missing, shorter than 32 characters or equal to `ADMIN_TOKEN_PLACEHOLDER` when `production` and not `demo_mode`. Name only the field, never the value or length.
    - Add `log_safe_summary()` (`SecretStr` fields become `***`, `database_url` becomes `host:port`, port 5432 when absent) and `secret_values()` (raw secret strings of at least 8 characters, for the log redactor).
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.18, 2.5_

  - [ ] 3.2 Implement `load_dotenv` and `read_environment` in `config.py`
    - `read_environment()` returns a copy of the process environment (the only legal reader besides `from_env`).
    - `load_dotenv(*, environ=None, start=None) -> Path | None`: read the production flag from the real or injected environment only; any value other than absent or `""`, `0`, `false`, `no`, `off` (case-insensitive) counts as production and returns `None` without opening any file; otherwise walk up from `start` to the first directory containing `.env.example`, read `.env` with `encoding="utf-8-sig"`, strip a trailing `\r` per line, skip blank and `#` lines, split on the first `=`, strip whitespace, remove one pair of matching quotes (keeping an inner `#`), use `setdefault` so existing variables win, and ignore `RECROMANCER_PRODUCTION` in the file.
    - After implementing `read_environment()`, change `backend/tests/conftest.py` to select the Hypothesis profile with `config.read_environment().get("HYPOTHESIS_PROFILE", "default")` and remove its `os` import, so no file other than `config.py` touches `os.environ` or `os.getenv`. Then confirm that `cd backend; pytest` still measures coverage for `recromancer` (no "module-not-imported" or "no data" warnings in the output, and the floor holds) and that the unit tests pass. Reason: `conftest.py` is imported by pytest before the coverage plugin and before test modules; importing `recromancer.config` there must not stop coverage from tracking the package, so check the warnings explicitly.
    - _Requirements: 3.2, 3.13, 3.14, 3.15, 3.16_

  - [x] 3.3 Write unit tests `test_config.py`
    - Create `backend/tests/unit/test_config.py`: defaults, prefix handling, case-insensitive names, unknown variable named in the error, invalid field named with its expected type and without its value, `hide_input_in_errors`, production rules (admin token missing, 31 characters, 32 characters, the placeholder; not required when `demo_mode` is true; `database_url` unset or equal to the default in production), several simultaneous violations in one `ConfigError` naming all fields, `production=True` with `demo_mode=True` is valid, `log_safe_summary` content, `secret_values` length filter, and that error text contains no value or length of `admin_token`. Also cover empty and whitespace-only values for `admin_token` and `anthropic_api_key`, which both yield `None`, and that an empty `RECROMANCER_ADMIN_TOKEN` with `production` true and `demo_mode` false fails the `admin_token` rule.
    - _Requirements: 3.1, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.18_

  - [ ] 3.4 Write unit tests `test_env_loader.py`
    - Create `backend/tests/unit/test_env_loader.py` using `tmp_path` and an injected `environ`: dev loads without overriding variables already set; production (including a typo value such as `maybe`) never opens the file (assert via a path that does not exist or a spy); `RECROMANCER_PRODUCTION` in the file is ignored; a BOM before the first key parses as `RECROMANCER_...` and not `\ufeffRECROMANCER_...`; CRLF and LF files parse identically and values carry no `\r`; single- and double-quoted values are unquoted and an inner `#` is preserved; comment and blank lines are ignored; discovery walks up to the directory holding `.env.example`.
    - _Requirements: 3.13, 3.14, 3.15_

  - [ ] 3.5 Write the `os.environ` AST guard test
    - Create `backend/tests/unit/test_no_environ_reads.py`: walk every `*.py` under the repo root (excluding `.venv`, `.git`, `node_modules`, `.hypothesis`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `build`, `dist`, `*.egg-info` and similar), skip `backend/src/recromancer/config.py`, parse with `ast`, and fail on any `ast.Attribute` with `value=ast.Name(id="os")` and `attr in {"environ", "getenv"}`; also flag `ImportFrom(module="os")` that imports `environ` or `getenv`. Add a self-test that the guard flags a synthetic `os.environ` and `os.getenv` snippet and ignores the string literal `"os.environ"`, a comment and a docstring. Read files as UTF-8 with BOM tolerance. Confirm the guard passes on the current tree.
    - No file is exempted from the guard except `backend/src/recromancer/config.py`; in particular `backend/tests/conftest.py` is scanned. Confirm the guard passes on the tree including `conftest.py`.
    - _Requirements: 3.2, 3.17_

- [ ] 4. Checkpoint - Foundation core
  - Ensure all tests pass, ask the user if questions arise. Run `cd backend; ruff check .; ruff format --check .; mypy; lint-imports; pytest` and confirm the coverage floor holds.

- [ ] 5. Implement ports and structured observability
  - [ ] 5.1 Create `service/ports.py`
    - Define the `ReadinessProbe`, `Clock` and `DbDiagnostics` protocols and the `IdGenerator` alias exactly as in the design. Import only the stdlib and `domain`; `lint-imports` must still pass.
    - _Requirements: 5.14, 2.2_

  - [ ] 5.2 Implement `observability.py`
    - Replace the stub (if any). Import only `domain`, the stdlib and `structlog`; declare its own minimal local `Protocol` for the clock (`now() -> datetime`) so it does not import `service`; the `Clock` port satisfies it structurally.
    - Implement idempotent `configure_logging(*, level, clock, secret_values=(), stream=sys.stdout)` with the processor chain from the design (`merge_contextvars`, `logger` and `level`, UTC ISO-8601 `timestamp` with `Z`, `event` renamed to `message`, `exc_info` to `exception`, redaction, `JSONRenderer`), routed through `ProcessorFormatter` so stdlib and uvicorn records get the same treatment, and disable `uvicorn.access`.
    - Redaction processor: keys matched by `is_sensitive_key` become `***`; `question`, `question_text` and `query_text` become `***`; registered secret values are replaced in every remaining string including the exception text; `SecretStr` renders as `***`.
    - Implement `question_log_fields(text) -> {"question_len": len(text)}`.
    - Verify the structlog API names used (`merge_contextvars`, `ProcessorFormatter`, `JSONRenderer`, `bound_contextvars`) against the installed version.
    - _Requirements: 5.1, 5.2, 5.5, 5.7, 5.8, 5.9_

  - [ ] 5.3 Write unit tests `test_observability.py`
    - Create `backend/tests/unit/test_observability.py` with a `StringIO` stream and a fake clock: every record is one JSON line with `timestamp` (UTC, ISO-8601), `level`, `logger`, `message`; context variables appear on every record; sensitive keys, question keys and registered secret values (also inside exception text and JSON-escaped) are redacted; `SecretStr` renders `***`; `question_log_fields` returns only the length; calling `configure_logging` twice does not duplicate output.
    - _Requirements: 5.1, 5.2, 5.5, 5.7, 5.8, 5.9_

  - [ ] 5.4 Create the invariants module and write Property 1
    - Create `backend/tests/property/invariants.py` (no test functions) with `assert_secret_absent(secret, *texts)` that checks the raw, JSON-escaped and percent-encoded forms. Create `backend/tests/property/test_secret_leak_property.py::test_secret_never_appears`, tagged `# Feature: platform-foundation, Property 1: Secret values never appear in observable output`.
    - Strategy `secret_values()`: text of 8 to 64 characters without surrogates or NUL, paired with a field chosen from the three secret fields and an optional invalid co-field; use `assume` to drop secrets that are substrings of the same output produced for a fixed placeholder secret. Build settings through `Settings.from_env(environ=mapping)` and log through `configure_logging` with `stream=io.StringIO()` and the settings' `secret_values()`. Check `repr`, `str`, `log_safe_summary()`, `model_dump_json()`, any `ConfigError` message and the JSON log output. Build all fakes inside the test body (no function-scoped fixtures under `@given`).
    - **Property 1: Secret values never appear in observable output**
    - **Validates: Requirements 3.5, 3.6, 3.11, 5.7, 10.2**

- [ ] 6. Implement the API layer
  - [ ] 6.1 Implement `api/request_id.py`
    - `resolve_request_id(header, new_id)` using `re.fullmatch` on `[A-Za-z0-9._-]{8,64}` (not `^...$`), returning the header unchanged when it matches and `new_id()` otherwise.
    - _Requirements: 5.3, 5.4_

  - [ ] 6.2 Write Property 3 for request-id resolution
    - Add `assert_request_id_valid(header, result)` to `invariants.py` using a regex-free oracle (length 8 to 64 and every character in `A-Z a-z 0-9 . _ -`). Create `backend/tests/property/test_request_id_property.py::test_request_id_resolution` with strategies for `None`, valid ids, boundary lengths 7, 8, 64 and 65, trailing `\n`, non-ASCII text and arbitrary text; generator is a counter-based UUID4-shaped fake. Assert the header is returned if and only if the oracle accepts it, otherwise the generated UUID4.
    - **Property 3: Request-id resolution is safe and total**
    - **Validates: Requirements 5.3, 5.4, 10.4**

  - [ ] 6.3 Implement `api/errors.py`
    - Define `ErrorBody` (Pydantic: `code`, `message`, `request_id`), the single `ERROR_HTTP_STATUS` `MappingProxyType` (422, 404, 405, 500, 500, 503, 500 as tabulated) and `http_status_for`.
    - Implement the handlers: `RecromancerError` (mapped status, own code and message), `RequestValidationError` (422, `VALIDATION_ERROR`, fixed text, locations only, never input values), `StarletteHTTPException` 404 and 405 (own status, `NOT_FOUND` or `METHOD_NOT_ALLOWED`, fixed text, never `exc.detail`), any other status (original status, `INTERNAL_ERROR`, fixed text), and the catch-all `Exception` handler (500, fixed message). The catch-all reads `request.state.request_id`, re-binds it with `bound_contextvars`, and sets the `X-Request-Id` header itself.
    - Traceback logging at ERROR with only `request_id`, `method`, `path` and the exception class name as extra fields; never body, query string or header values.
    - Verify the Starlette and FastAPI exception class names and handler registration against the installed versions.
    - _Requirements: 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10, 5.6_

  - [ ] 6.4 Implement `api/middleware.py`
    - Pure ASGI `RequestContextMiddleware`: resolve the Request_ID via `resolve_request_id` with the injected id generator, store it in `scope["state"]`, bind `request_id`, `method` and `path` with structlog contextvars, add `X-Request-Id` to every `http.response.start`, emit exactly one access log line (`method`, `path`, `status`, `duration_ms`, no query string) in a `finally` block using the injected `Clock` for timing (status 500 when an exception propagates), then clear the contextvars. Pass non-HTTP scopes through unchanged.
    - _Requirements: 5.3, 5.4, 5.5, 5.6_

  - [ ] 6.5 Implement `api/routes/health.py`
    - `GET /healthz` returns 200 `{"status": "ok"}` and touches no database, network or filesystem. `GET /readyz` calls `readiness.is_ready()` inside `anyio.move_on_after(readyz_timeout_s)`; ready gives 200 `{"status": "ok", "db": "ok"}`; `False`, an exception (catch `Exception` only, log the class name only) or a timeout gives 503 `{"status": "degraded", "db": "unreachable"}`. Create `api/routes/__init__.py`.
    - _Requirements: 5.10, 5.11, 5.12, 5.13, 5.14_

  - [ ] 6.6 Implement `create_app` in `api/app.py`
    - `create_app(*, readiness, clock, new_id, lifespan=None, demo_mode=True, production=False, readyz_timeout_s=5.0)`. Never read configuration or import an adapter. When `production` is true construct `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`, otherwise use the defaults. Register the exception handlers, add `RequestContextMiddleware`, include the health router, and expose `demo_mode` on `app.state`. Ensure the middleware order matches the design (ServerErrorMiddleware outermost, then ours, then ExceptionMiddleware, then the router).
    - _Requirements: 4.5, 5.3, 5.4, 5.5, 5.6, 5.10, 5.11, 5.12, 5.13, 5.14_

  - [ ] 6.7 Write API unit tests
    - Create `backend/tests/unit/test_api_errors.py`: using `TestClient(app, raise_server_exceptions=False)` with a fake probe, fake clock and counter id generator, test every error path (`RecromancerError` for each code, 422 validation, 404, 405, an HTTP status such as 401 or 418 that keeps its status with `INTERNAL_ERROR`, catch-all 500), body shape `{"code","message","request_id"}`, `X-Request-Id` on every response including errors, a valid inbound id echoed and an invalid one replaced, exception detail never in the body, one access log line per request, and traceback logging that contains no body, query string or header named like `authorization`, `token`, `secret` or `cookie`.
    - Create `backend/tests/unit/test_health.py`: `/healthz` never calls the probe; `/readyz` ready, not ready, raising and hanging (use `readyz_timeout_s=0.05` and `@pytest.mark.anyio` or `TestClient`) with the exact bodies and statuses.
    - Create `backend/tests/unit/test_docs_disabled_in_production.py`: with `production=True`, `GET /docs`, `/redoc` and `/openapi.json` return 404 with code `NOT_FOUND` and the standard body; with `production=False` all three return 200.
    - _Requirements: 4.5, 4.6, 4.7, 4.8, 4.9, 4.10, 5.3, 5.4, 5.5, 5.6, 5.10, 5.11, 5.12, 5.13_

  - [ ] 6.8 Write Property 2 for the error-status mapping
    - Add `assert_mapping_total(mapping, codes)` to `invariants.py`. Create `backend/tests/property/test_error_mapping_property.py::test_mapping_is_total` using `st.sampled_from(list(ErrorCode))` plus an explicit check that the key set of `ERROR_HTTP_STATUS` equals the set of `ErrorCode` members, and that each status is an integer in the 400 to 599 range and a defined HTTP status.
    - **Property 2: The error-to-status mapping is total and valid**
    - **Validates: Requirements 4.4, 10.3**

- [ ] 7. Checkpoint - API layer
  - Ensure all tests pass, ask the user if questions arise. Run `cd backend; ruff check .; ruff format --check .; mypy; lint-imports; pytest` and confirm the coverage floor holds.

- [ ] 8. Implement the local database environment, migration system and Postgres adapters
  - [ ] 8.0 Create `docker-compose.yml` and `.env.example`
    - `docker-compose.yml` at the repo root with service `db` using `pgvector/pgvector:pg16`, `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB` all `recromancer`, port `"127.0.0.1:5432:5432"`, the named volume `recromancer-pgdata`, and a healthcheck `pg_isready -U recromancer -d recromancer` with `interval: 10s`, `timeout: 5s`, `retries: 5`.
    - `.env.example` with `RECROMANCER_` variables only, exactly those in the design (`PRODUCTION`, `DEMO_MODE`, `DATABASE_URL`, `ADMIN_TOKEN`, `ANTHROPIC_API_KEY`, `LOG_LEVEL`). Secret placeholders are blank or obviously non-secret text such as `change-me-local-only`, never a token-shaped value: `RECROMANCER_ADMIN_TOKEN` equals `ADMIN_TOKEN_PLACEHOLDER` (the design requires a test that they match, see task 11.5) and `RECROMANCER_ANTHROPIC_API_KEY` is blank (`RECROMANCER_ANTHROPIC_API_KEY=`), which `Settings` reads as unset (task 3.1).
    - Verify in PowerShell: `docker compose up -d`, then `docker compose ps` shows the `db` service `healthy`. If Docker is unavailable, skip only this verification, state the reason in the task notes, and do not block creation of the two files.
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 3.18_

  - [ ] 8.1 Implement `db/runner.py`
    - Define `Migration` (frozen: `name`, `sql`, `checksum`), `RunReport(applied, skipped, empty)`, `StoreError`, `MigrationError(RecromancerError)` with `ErrorCode.MIGRATION_FAILED`, the `MigrationStore` protocol (`locked`, `ensure_table`, `applied`, `apply`), `discover(directory: Traversable)` and `run(store, migrations, clock, warn)`. Do not import `psycopg`, `service` or any other adapter; type the clock with a small local protocol.
    - `discover`: names matching `[0-9]{4}_[a-z0-9_]+\.sql`, sorted by filename; error for a missing directory, a `.sql` file with a non-matching name, and duplicate numeric prefixes; non-`.sql` files ignored; SHA-256 over file bytes.
    - `run`: acquire the lock, `ensure_table` under it, pre-flight 1 (checksum mismatch names the file) and pre-flight 2 (a pending or blank file numbered below the highest recorded number raises `MIGRATION_FAILED` naming the file), both before the first `apply`; skip recorded files; blank SQL skipped with a warning naming the file and not recorded; apply the rest in order with `applied_at` from the injected clock; wrap `StoreError` as `MigrationError` with `"<filename>: <original message>"` truncated to 500 characters; forward-only, no down path.
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11_

  - [ ] 8.2 Create the in-memory `FakeMigrationStore`
    - Create `backend/tests/fakes.py` with `FAIL_MARKER = "-- fail"` defined once and `FakeMigrationStore` modelling the executed SQL history as an ordered tuple and the recorded set as a dict `filename -> (checksum, applied_at)`, changed together; `apply` raises `StoreError` when the SQL starts with `FAIL_MARKER` and records nothing; `locked` is a context manager that tracks exclusivity; also a deterministic `FakeClock`.
    - _Requirements: 7.4, 7.8, 7.9, 10.8_

  - [ ] 8.3 Add the initial migration, `.gitattributes` and packaging test
    - Create `backend/src/recromancer/db/migrations/0001_create_vector_extension.sql` containing exactly `CREATE EXTENSION IF NOT EXISTS vector;`. Create the repo-root `.gitattributes` with `*.sql text eol=lf` and `*.py text eol=lf`. Check that `.gitignore` ignores neither `.sql` files, `py.typed` nor any `.kiro/` path.
    - Create `backend/tests/unit/test_packaging.py`: `importlib.resources` finds the `.sql` file in the package, its text equals the required statement, and the pyproject wheel target lists `src/recromancer`. Confirm with `pip wheel --no-deps backend -w $env:TEMP\wheelcheck` that the wheel contains the `.sql` file and `py.typed`, then delete the temporary directory.
    - _Requirements: 7.1, 7.15, 1.1, 1.4_

  - [ ] 8.4 Write runner unit tests `test_runner.py`
    - Create `backend/tests/unit/test_runner.py` using `FakeMigrationStore` and `FakeClock`: discovery rules (ordering, bad names, duplicate prefixes, missing directory, ignoring non-`.sql` files); skipping recorded migrations with matching checksums; checksum mismatch names the file and applies nothing; out-of-order pending file (record `0003`, offer pending `0002`) raises `MIGRATION_FAILED` naming `0002`, applies nothing and leaves the store unchanged, while a pending file above the highest still applies; empty file warning without recording, then a later non-empty version applies; error wrapping includes filename and original message and is truncated to 500 characters; lock acquired before `ensure_table` and held for the whole run; `applied_at` comes from the injected clock; a failing migration records nothing.
    - _Requirements: 7.1, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11_

  - [ ] 8.5 Create the migration strategy and its guard test
    - Create `backend/tests/property/strategies.py` with `migration_sets()`: a composite producing 0 to 8 migrations with strictly increasing `NNNN_word.sql` names and SQL drawn from a safe alphabet that excludes `-` (so no comment can form), including blank files, plus `.filter(lambda sql: not sql.lstrip().startswith(FAIL_MARKER))` as a second guard; import `FAIL_MARKER` from `tests/fakes.py`.
    - Create `backend/tests/unit/test_migration_strategy.py`: draw at least 100 examples and assert that no generated file starts with `-- fail` on its stripped text, names are valid and strictly increasing.
    - _Requirements: 10.1, 10.5, 10.6_

  - [ ] 8.6 Write Property 4 for migration idempotency
    - Add `assert_same_state(a, b)` to `invariants.py` (executed-SQL history and recorded `_schema_migrations` contents, ignoring nothing else). Create `backend/tests/property/test_migration_properties.py::test_apply_twice_is_idempotent` running the runner twice on a fresh `FakeMigrationStore` with a `FakeClock`; assert the state after the second run equals the state after the first and the second report lists no applied files.
    - **Property 4: Applying a migration set twice is idempotent**
    - **Validates: Requirements 7.5, 10.5**

  - [ ] 8.7 Write Property 5 for prefix equivalence
    - In `test_migration_properties.py` add `test_prefix_equivalence`: draw a migration set and `k = draw(integers(0, len(set)))`; run the first `k` migrations and then the full set on one store, run the full set once on a fresh store, and assert `assert_same_state` holds, including the `k = 0` and `k = n` boundaries.
    - **Property 5: Prefix-then-rest equals all-at-once**
    - **Validates: Requirements 10.6**

  - [ ] 8.8 Implement the Postgres adapters in `db/postgres.py`
    - Before writing, check the installed `psycopg` and `psycopg_pool` APIs against the design (`ConnectionPool(open=False)`, `pool.open(wait=False)`, `pool.connection(timeout=...)`, `conn.transaction()`, `LiteralString` typing for `execute`) and confirm in the psycopg documentation that the async pool is unsupported on the default Windows event loop, so the sync pool plus `anyio.to_thread` is used.
    - Implement `build_pool(database_url: SecretStr)`, `PostgresMigrationStore` (one autocommit connection; `pg_advisory_lock` with the key exposed as the module constant `MIGRATION_LOCK_KEY = int.from_bytes(hashlib.sha256(b"recromancer.migrations").digest()[:8], "big", signed=True)`, a signed 64-bit integer that always fits the Postgres bigint range; `pg_advisory_unlock` in `finally`; `ensure_table` creating `_schema_migrations (filename text primary key, checksum text not null, applied_at timestamptz not null)`; each migration and its record in one `with conn.transaction():`; multi-statement SQL executed with no parameters and one documented `cast(LiteralString, ...)`; `psycopg.Error` converted to `StoreError`; a validated `schema` argument for test isolation), `PostgresReadinessProbe` (`SELECT 1` through `anyio.to_thread.run_sync(..., abandon_on_cancel=True)` with `pool.connection(timeout=4.0)`) and `PostgresDiagnostics` (`ping`, `has_extension`). `psycopg` is imported only inside `db`; no import of `service` or another adapter.
    - Add `backend/tests/unit/test_migration_lock_key.py` (no database needed; importing the constant must not open a connection): assert `-2**63 <= MIGRATION_LOCK_KEY <= 2**63 - 1`, that it equals the expression above recomputed in the test, and that it is deterministic (the same value on repeated reads and after `importlib.reload` of the module).
    - _Requirements: 7.2, 7.3, 7.4, 7.8, 7.9, 5.14_

  - [ ] 8.9 Implement the migrate CLI in `db/migrate.py`
    - `main(argv, *, environ, store_factory, stdout, stderr, clock)` with all dependencies injectable, plus the `python -m recromancer.db.migrate` entry (`if __name__ == "__main__"`). Flow: `load_dotenv()`, `Settings.from_env()`, build the store, `discover` the packaged `migrations` directory via `importlib.resources.files("recromancer.db") / "migrations"`, `run`. Print one line per applied file, or a line containing `no migrations pending`; exit 0 on success. Exit 1 with a descriptive message on stderr (passed through the redactor seeded with `settings.secret_values()`) on `ConfigError`, connection failure, missing directory or `MigrationError`. Only `db/migrate.py` may import `config` within `db`; confirm `lint-imports` still passes (adjust C7 or add an explicit allowance only if the installed version flags it, and record why).
    - _Requirements: 7.12, 7.13, 7.14, 3.16_

  - [ ] 8.10 Write CLI unit tests
    - Extend `backend/tests/unit/test_runner.py` (or add `test_migrate_cli.py` if the file grows unwieldy) with `main` driven by fakes: exit 0 and the line `no migrations pending` when nothing is pending, one line per applied file, exit 1 with a descriptive stderr message for `ConfigError`, connection failure, missing directory and `MigrationError`, and that stderr never contains the database password or admin token.
    - _Requirements: 7.12, 7.13, 7.14_

  - [ ] 8.11 Add the database-URL fixture, contract tests and integration tests
    - All database integration and contract tests in this task run against the compose database from task 8.0, with `RECROMANCER_DATABASE_URL` pointing at it, and still skip with the stated reason when the variable is unset.
    - In `backend/tests/conftest.py` add a `postgres_url` fixture that reads `RECROMANCER_DATABASE_URL` from the real environment through `config.read_environment()` (never `os.environ`, never `.env`) and calls `pytest.skip("no database URL configured (set RECROMANCER_DATABASE_URL)")` when it is absent; add a throwaway-schema fixture (`CREATE SCHEMA t_<hex>`, dropped afterwards).
    - Create `backend/tests/contract/test_migration_store_contract.py`: one suite (`ensure_table` idempotent, `applied` reflects `apply`, failing SQL records nothing, lock exclusive within a process) parametrised over `FakeMigrationStore` and `PostgresMigrationStore`, with a `failing_sql` fixture (`-- fail` for the fake, `SELECT 1/0;` for Postgres); the Postgres parameter skips with the reason above.
    - Create `backend/tests/integration/test_postgres_migrations.py` and `test_postgres_probe.py`: real `0001` applies and the `vector` extension exists; a second run reports `no migrations pending`; a second connection's `pg_try_advisory_lock` fails while the runner holds the lock; a failing migration leaves neither its effects nor a record; applying twice and prefix-then-rest equivalence on real Postgres as example tests; `PostgresReadinessProbe` returns true. All skip with the reason above when no URL is set.
    - Run the suite both without and with `docker compose up -d` and `$env:RECROMANCER_DATABASE_URL="postgresql://recromancer:recromancer@localhost:5432/recromancer"` (the compose file from task 8.0; if Docker is unavailable, confirm the skip path only and state why) and confirm the coverage floor holds. If the floor fails only when the database is absent, keep the adapter thin and report it as design risk 1; do not lower `fail_under` and do not add `# pragma: no cover` without telling the user.
    - _Requirements: 10.1, 10.7, 10.9, 10.10, 7.3, 7.4, 7.8, 7.9_

- [ ] 9. Checkpoint - Database layer
  - Ensure all tests pass, ask the user if questions arise. Run `cd backend; ruff check .; ruff format --check .; mypy; lint-imports; pytest`.

- [ ] 10. Wire the Composition Root
  - [ ] 10.1 Implement `bootstrap.py`
    - Replace the stub (if any). Define `SystemClock`, `uuid4_str`, `pool_lifespan(pool)` (`pool.open(wait=False)` on startup, `pool.close()` on shutdown), `build_app(settings, *, clock=None)` (configure logging with `settings.secret_values()`, `build_pool`, assign `PostgresReadinessProbe` to a `ReadinessProbe`-typed variable so mypy checks the fit, call `create_app` with `demo_mode` and `production` from settings), `app_factory()` (`load_dotenv()` then `Settings.from_env()`; used as `uvicorn recromancer.bootstrap:app_factory --factory`) and `build_db_diagnostics(settings)` returning a `PostgresDiagnostics`. `bootstrap.py` is the only module that imports `db`, `llm`, `embed` or `retrieval`; confirm `lint-imports` passes.
    - _Requirements: 2.4, 2.6, 5.14, 3.16_

  - [ ] 10.2 Write composition-root tests
    - Create `backend/tests/unit/test_bootstrap.py`: `build_app` with injected settings and a fake clock constructs the app without opening the pool or touching the network; `GET /healthz` returns 200 through `TestClient` without entering the lifespan; the app disables docs when `production=True`; `build_db_diagnostics` returns an object satisfying `DbDiagnostics`; `pool_lifespan` calls `open(wait=False)` then `close()` on a fake pool.
    - _Requirements: 2.4, 5.10, 5.14_

- [ ] 11. Add the local development environment and Doctor
  - [ ] 11.1 Check `.gitignore` for the environment files
    - `docker-compose.yml` and `.env.example` already exist (task 8.0). Check that `.gitignore` ignores `.env` and does not ignore `.env.example` or `docker-compose.yml`, and fix `.gitignore` if needed (`git check-ignore -q .env` succeeds; `git check-ignore -q .env.example` fails).
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ] 11.2 Implement `scripts/doctor.py`
    - Checks as `Callable[[], CheckResult]` (`ok`, `name`, `detail`, `hint`) for python version (3.11+), docker (`docker version` within 10 s, injected runner), config (`Settings.from_env`), postgres (`DbDiagnostics.ping`), vector extension (`has_extension("vector")`), and every name in `.env.example` set in the environment after `load_dotenv`; a blank optional secret (for example `RECROMANCER_ANTHROPIC_API_KEY=`) counts as set-but-empty and prints an OK line whose value is shown as `***`. `main` runs every check inside `try/except Exception`, prints `OK <name> <detail>` or `FAIL <name> <hint>` lines, prints dependent checks as `FAIL ... skipped: config invalid` when config fails, and exits 1 if any check failed.
    - `is_masked_env_name` lives in this script (lower-case name contains `secret`, `password`, `token` or `key`; no segment rule, no allowlist); masked values print `***`; a name ending in `DATABASE_URL` prints host and port only (checked first); other values print as is, capped at 80 characters; every line passes through `to_ascii()` (`encode("ascii", "backslashreplace")`); exception text passes through the redactor seeded with `settings.secret_values()`.
    - Obtain configuration only through `config.load_dotenv()`, `config.read_environment()` and `Settings.from_env()` and reach Postgres only through `bootstrap.build_db_diagnostics`. Confirm `ruff check --config backend/pyproject.toml scripts`, `mypy` and `test_no_environ_reads` pass with the new script.
    - _Requirements: 6.6, 6.7, 6.8, 6.9, 6.10, 6.11, 6.12, 3.16_

  - [ ] 11.3 Write unit tests `test_doctor_masking.py`
    - Create `backend/tests/unit/test_doctor_masking.py`: `is_masked_env_name` is true for `RECROMANCER_ADMIN_TOKEN`, `RECROMANCER_ANTHROPIC_API_KEY`, `MONKEY`, `MAX_TOKENS` and a lower-case `db_password`; false for `RECROMANCER_DATABASE_URL`, which the renderer prints as host and port only; a rendered line for a masked name never contains the value. Add a doctor test here: with `RECROMANCER_ANTHROPIC_API_KEY` set to the empty string, the `.env.example` variables check passes and prints an `OK` line showing `***` for that variable.
    - _Requirements: 6.10, 6.11_

  - [ ] 11.4 Write unit tests `test_doctor.py`
    - Create `backend/tests/unit/test_doctor.py` with injected checks and fake diagnostics: `OK` and `FAIL` line formats, all checks run even after a failure, a raising check becomes a `FAIL` line, exit code 1 when any check fails and 0 otherwise, dependent checks skipped when config is invalid, output is ASCII-only (including a non-ASCII value in the environment), and no secret value from the environment appears in captured stdout or stderr.
    - _Requirements: 6.6, 6.7, 6.8, 6.9, 6.10, 6.11, 6.12_

  - [ ] 11.5 Start `test_repo_hygiene.py` with environment-file checks
    - Create `backend/tests/unit/test_repo_hygiene.py` (reads files from the repo root as UTF-8 with BOM tolerance): checks of the files created in task 8.0: compose text checks (image, loopback port mapping, named volume, healthcheck values), every name in `.env.example` is a `Settings` field and every field has an entry, the `.env.example` admin token equals `config.ADMIN_TOKEN_PLACEHOLDER`, compose credentials agree with `DEFAULT_DATABASE_URL`, and `.env.example` holds no real secret pattern.
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 3.10_

- [ ] 12. Implement the secret scanner
  - [ ] 12.1 Implement `scripts/secret_scan.py`
    - Frozen `Finding(path, line, pattern)`; `scan_text(path, text)`; `tracked_files()` using `subprocess.run(["git", "ls-files", "-z"])` without a shell; `main(argv=None, *, list_files=tracked_files, read_file=..., out=sys.stdout) -> int`. Patterns by name: `aws-access-key-id`, `private-key-header` (PEM header with optional algorithm word, built so the source text does not match itself), `bearer-token` (`Bearer\s+[A-Za-z0-9._~+/=-]{20,}`; length 20 chosen, design risk 9), `github-classic-token`, `github-fine-grained-token`, `anthropic-api-key`, `huggingface-token` with the exact minimum lengths from Requirement 9.9.
    - Scan only `git ls-files` output (including `.env.example`), exclude exactly `scripts/secret_scan.py` by normalised POSIX path, skip files containing a NUL byte, decode as UTF-8 with replacement, print `path:line: pattern-name` only (never the match or the line) plus a counts-only summary, and exit 1 on any finding, else 0. Run it on the repo and confirm no false positive in existing files (including the spec documents).
    - _Requirements: 9.7, 9.8, 9.9, 9.10, 9.11, 9.12_

  - [ ] 12.2 Write unit tests `test_secret_scan.py`
    - Create `backend/tests/unit/test_secret_scan.py`: for each pattern one fake token built at runtime (for example `"AKIA" + "A" * 16`, `"ghp_" + "a" * 36`) and a near miss below the minimum length that is not flagged; the PEM header built by concatenation; self-exclusion of `scripts/secret_scan.py`; `.env.example` is scanned; output contains path, line and pattern name and never the matched text; exit codes 0 and 1 via injected `list_files` and `read_file`; binary (NUL) files skipped. Confirm no complete token literal appears in the test file (run the scanner over it).
    - _Requirements: 9.7, 9.8, 9.9, 9.10, 9.11, 9.12, 9.13_

- [ ] 13. Add continuous integration
  - [ ] 13.1 Create `.github/workflows/ci.yml`
    - Triggers `push` (all branches) and `pull_request`; `concurrency` keyed on the ref; workflow-level `permissions: contents: read`; all jobs on `ubuntu-latest` with Python 3.11. Jobs: `lint` (`ruff check .` in `backend`, plus `ruff check --config backend/pyproject.toml scripts`), `format` (`ruff format --check` for both), `typecheck` (`mypy`), `tests` (`pytest` with `HYPOTHESIS_PROFILE=ci`, a `pgvector/pgvector:pg16` service with the three `POSTGRES_*` variables, port `5432:5432`, `--health-cmd pg_isready --health-interval 10s --health-timeout 5s --health-retries 5`, `RECROMANCER_DATABASE_URL` set to the container URL, then a step that builds a wheel with `pip wheel --no-deps backend` and fails if the archive lacks the `.sql` file or `py.typed`), `secret-scan`, `kiro-guard`, `import-linter` (`lint-imports` in `backend`), the optional `frontend` job guarded by a `detect` step (not in the gate), and `gate` (`if: always()`, `needs` the seven required jobs, fails on `failure`, `cancelled` or `skipped`).
    - `kiro-guard` runs `git check-ignore -q` on `.kiro/steering/product.md`, `.kiro/specs/example/requirements.md`, `.kiro/hooks/example.json` and `.kiro/settings/mcp.json`, fails with an explanatory message if any is ignored, and also fails if `git ls-files .kiro` is empty. Run the same loop locally in PowerShell to confirm it passes.
    - Action versions: look up the exact current release and commit SHA of `actions/checkout` and `actions/setup-python` (and any other third-party action) on each action's releases page or via the GitHub API before writing the file; pin by full commit SHA or an exact version tag with a comment recording the version. Never use a floating tag such as `v4` or `main` and never guess a SHA. Use `actions/setup-python` with `cache: pip` and `cache-dependency-path: backend/pyproject.toml`.
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11, 8.12, 2.9_

  - [ ] 13.2 Add CI workflow text tests
    - Extend `backend/tests/unit/test_repo_hygiene.py` (parse the workflow with a YAML reader if one is already a dependency, otherwise with text checks; do not add a dependency just for this): triggers, `permissions: contents: read`, every `uses:` reference is a 40-character SHA or an exact version tag (no `@v4`, `@main`), the seven required jobs exist, `gate` `needs` all seven and uses `if: always()`, the tests job sets `HYPOTHESIS_PROFILE` to `ci` and `RECROMANCER_DATABASE_URL`, uses the `pgvector/pgvector:pg16` image, pip cache keyed on `backend/pyproject.toml`, and the frontend job is not in the gate's `needs`.
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.7, 8.8, 8.9, 8.10, 8.12_

- [ ] 14. Add repository hygiene files
  - [ ] 14.1 Create `LICENSE` and `README.md`
    - `LICENSE`: standard MIT text with `Copyright (c) 2026 The Recromancer Authors`.
    - `README.md`: at most three sentences describing the product, then `## Quick Start` with PowerShell commands in order: `git clone`, `python -m venv .venv`, `.\.venv\Scripts\Activate.ps1`, `pip install -e "backend[dev]"`, `Copy-Item .env.example .env`, `docker compose up -d`, `cd backend; python -m recromancer.db.migrate`, `cd backend; pytest`. Add a short troubleshooting note for a port 5432 clash with a native Postgres, a note about `pytest --no-cov` for focused runs, and the migration caveats (extension privileges, no statements that cannot run in a transaction). Do not modify `docs/DEV_SETUP.md` or `docs/SCOPE_AND_DESIGN.md`.
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [ ] 14.2 Create `docs/KIRO_USAGE.md`
    - First heading `# Kiro Usage Journal`, then the headings `## IDE / CLI / Web / Crew`, `## Custom agents`, `## Hooks`, `## Specs vs vibe coding`, `## Steering`, `## MCP`, `## Property-based testing`, `## Powers`, `## Session Log`; under `## Session Log` one placeholder list item `- YYYY-MM-DD: placeholder entry`.
    - _Requirements: 9.5, 9.6_

  - [ ] 14.3 Finish `test_repo_hygiene.py` with licence, README and journal checks
    - Extend `backend/tests/unit/test_repo_hygiene.py`: `LICENSE` has the MIT text, holder `The Recromancer Authors` and year 2026; `README.md` has at most three sentences before `## Quick Start` and every required PowerShell command appears in order; `docs/KIRO_USAGE.md` first heading and all nine section headings, and the placeholder session-log item starting with `YYYY-MM-DD`.
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

- [ ] 15. Final checkpoint - Full verification
  - Run from the repo root with the venv active and confirm every command passes, then ask the user if questions arise:
    - `cd backend; ruff check .; ruff format --check .`
    - `ruff check --config backend/pyproject.toml scripts; ruff format --check --config backend/pyproject.toml scripts`
    - `cd backend; mypy`
    - `cd backend; lint-imports`
    - `cd backend; pytest` (coverage at least 85 percent), then with the database: `docker compose up -d` and `$env:RECROMANCER_DATABASE_URL="postgresql://recromancer:recromancer@localhost:5432/recromancer"; cd backend; pytest`
    - `$env:HYPOTHESIS_PROFILE="ci"; cd backend; pytest`
    - `python scripts/secret_scan.py`
    - `python scripts/doctor.py` (after `Copy-Item .env.example .env`, `docker compose up -d` and `cd backend; python -m recromancer.db.migrate`)
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- No task is marked optional (`*`): Requirement 10 requires the property-based tests and the quality checklist requires every task to end with passing tests, so they are all required sub-tasks of the code they test.
- Each task references specific requirement clauses for traceability; Properties 1 to 5 map to tasks 5.4, 6.8, 6.2, 8.6 and 8.7.
- Checkpoints ensure incremental validation at every layer boundary, and the coverage floor holds at each top-level task boundary.
- Task numbers run in dependency order, so executing them sequentially is always safe; the graph below only groups independent leaf tasks that write different files.
- `docker-compose.yml` and `.env.example` are created in task 8.0 so the database tasks can run against the compose database; task 11.1 only checks `.gitignore`. Docker-dependent verification is skipped with a stated reason when Docker is unavailable.
- Open design items to confirm with the user when reached: extra runtime dependencies `uvicorn` and `anyio` (task 1.1), Bearer minimum length of 20 (task 12.1).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["2.1", "2.3", "5.1"] },
    { "id": 3, "tasks": ["2.2", "2.4", "3.1", "5.2", "6.1", "8.1", "12.1"] },
    { "id": 4, "tasks": ["3.2", "5.3", "5.4", "6.3", "6.4", "6.5", "8.0", "8.2", "8.3", "8.8", "12.2"] },
    { "id": 5, "tasks": ["3.3", "3.4", "3.5", "6.2", "6.6", "8.4", "8.5", "8.9", "11.1", "11.5"] },
    { "id": 6, "tasks": ["6.7", "6.8", "8.10", "8.11", "10.1"] },
    { "id": 7, "tasks": ["8.6", "10.2", "11.2"] },
    { "id": 8, "tasks": ["8.7", "11.3", "11.4"] },
    { "id": 9, "tasks": ["13.1", "14.1", "14.2"] },
    { "id": 10, "tasks": ["13.2"] },
    { "id": 11, "tasks": ["14.3"] }
  ]
}
```
