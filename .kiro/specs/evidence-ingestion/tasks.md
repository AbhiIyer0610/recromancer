# Implementation Plan: Evidence Ingestion

## Overview

This plan follows the design's "Walking skeleton order" in four groups. Each top-level group is one
Kiro execution request and leaves the repository green: nothing is left unwired and no group depends
on code a later group writes. The implementation language is Python 3.11+ (fixed by the design).

1. **Group 1, pure core, fakes and properties** (skeleton steps 1 to 3): domain types, Bundle_Reader,
   Chunker, the Ledgerline bundle, the ports, the fakes, the Ingestion_Service and every property and
   example test. It ends with the R8.8 test that ingests Ledgerline into the in-memory store.
2. **Group 2, Postgres** (skeleton step 5): migration 0002, `PostgresEvidenceStore`, the shared
   Postgres fixtures, the contract suite and the migration test.
3. **Group 3, configuration, composition root and CLI with fakes** (skeleton step 4, then the
   Postgres run of step 5): Settings, bootstrap helpers, `ingest_cli.main()`, DEV_SETUP, and an
   integration test that runs the CLI with the Fake_Embedder against real Postgres.
4. **Group 4, real model** (skeleton step 6): `fastembed` dependency, `FastembedEmbedder`,
   `build_embedder`, the real-model test, and one MANUAL user step for the first model download.

Placement choices (they keep the design intact):

- **`ingest_cli.py` is created in Group 1 with only `make_fetch` and `ledgerline_dir`** (no `main`).
  The design's file table puts both in `ingest_cli.py`, and Group 1 needs a real disk reader for the
  Ledgerline tests (LG, R8.8). Because the module then exists, the C8 line `"ingest_cli",`, the
  update to `test_c8_layers_match_documented_order` and the `.kiro/steering/structure.md` change are
  **also in Group 1** (task 1.4), so layering is enforced from the moment the module exists.
  Group 3 adds `main()` to the same file.
- **`build_embedder` is written in Group 4**, because it references `FastembedEmbedder`. Group 3
  writes `evidence_store()` and `build_ingestion_service()` in `bootstrap.py`; the latter takes an
  already built `Embedder`, so it needs no adapter. In Group 3 `main()` takes its embedder from the
  `embedder_factory` seam, and when no factory is injected it fails closed with one redacted stderr
  line `no embedder is configured` and exit code 1 (a tested behaviour, not dead code). Task 4.4
  replaces that default with `bootstrap.build_embedder`, so the default CLI path is wired only when
  the real adapter exists.
- The R4.11 fastembed findings go into `docs/DEV_SETUP.md` in Group 4. The pgvector output (R5.5),
  the cache directory example (decision 3) and the `import onnxruntime` venv check go in Group 3.

Conventions that apply to every task:

- All commands are Windows PowerShell, run from the repo root with the virtual environment active.
  Use `cd backend; <command>` for backend tooling. Every command must finish in under 60 seconds.
  Property tests use the default Hypothesis profile (100 examples); never run the `ci` profile in a
  task. The only exception to the 60 second rule is the first real model load, which is the MANUAL
  (user) step 4.7.
- Every top-level group ends with a final gate sub-task that runs
  `cd backend; ruff check .; ruff format --check .; mypy; lint-imports; pytest` twice: once with
  `Remove-Item Env:RECROMANCER_DATABASE_URL -ErrorAction SilentlyContinue` and once with
  `$env:RECROMANCER_DATABASE_URL="postgresql://recromancer:recromancer@127.0.0.1:5432/recromancer"`.
  The coverage floor (`fail_under = 85`) must hold at every group boundary. For a focused run use
  `cd backend; pytest --no-cov <path>`.
- Every sub-task lists its requirement ids, using the approved ids R1.1 to R8.9 (written `1.1`
  to `8.9` below). Every acceptance criterion appears in at least one task.
- No real network in tests. Time-dependent code takes an injected `Clock` and the id source is
  injected; default tests use the `FakeEmbedder` only and never import `fastembed`.
- Library facts in the design's Risks table are verified by the first task that uses them. Each
  such task names the risk numbers and records the result in the task notes.
- Never put body, title or chunk text into logs, errors or stderr. The named tests that prove this
  are `test_error_messages_never_contain_body_text` (R1.9, task 1.6),
  `test_logs_contain_no_title_body_or_chunk_text` (R6.9, task 1.10),
  `test_failure_prints_one_redacted_line_without_body` (R7.4, task 3.3),
  `test_errors_do_not_echo_input_text` (task 4.3) and `test_driver_error_has_no_conninfo` (task 2.3).
- Property-based tests are required sub-tasks of the code they test and are not marked with `*`
  (Requirement 8). Each property task applies its Mutation_Check(s), confirms the property fails,
  reverts the mutation, and records the result in the task notes (R8.6).
- Do not add anything beyond the approved requirements and design: no doctor changes, no README
  changes, no edits to `docs/SCOPE_AND_DESIGN.md`, no full-text search, no similarity search.
- Final-message requirement: the last message of each group's execution lists every file changed,
  the exact gate output lines (pass and skip counts, coverage percent, and the `lint-imports` line
  `Contracts: N kept, 0 broken.`), the mutation-check results, and anything skipped.

## Tasks

- [x] 1. Group 1: pure core, Ledgerline bundle, ports, fakes, service and properties
  - [x] 1.1 Create the domain types and error classes
    - Create `backend/src/recromancer/domain/evidence.py` with the frozen stdlib dataclasses and aliases from the design: `ItemKind`, `DEFAULT_WORKSPACE_ID`, `FetchBytes`, `Subject`, `EvidenceItem`, `Bundle`, `SourceLocator`, `Chunk`, `EmbeddedChunk`, `ItemWrite`, `StoredItem`, `StoredChunk`. Stdlib only (contract C1).
    - Add `BundleError` and `ChunkingError` to `domain/errors.py`, both `ErrorCode.VALIDATION_ERROR`, each constructor truncating its message to 500 characters so the base-class `ValueError` is never raised. No new `ErrorCode`.
    - Extend `backend/tests/unit/test_errors.py` (or add `test_evidence_errors.py`) for the truncation and the code.
    - _Requirements: 1.9, 3.1, 3.6, 3.12_

  - [x] 1.2 Implement the Bundle_Reader and `content_hash`
    - Create `backend/src/recromancer/ingest/bundle.py` with `read_bundle(fetch)`, `content_hash(item)`, the limit constants (`MANIFEST_MAX_BYTES`, `ITEM_MAX_BYTES`, `MAX_FILES`, `TITLE_MAX`, `BODY_MAX`, `REFERENCES_MAX`) and the patterns, imports limited to `domain` and the stdlib (contract C2). The reader performs no I/O itself.
    - Implement algorithm A1 in the stated order: manifest first, then every `files[].path` rule (R1.12), then item files in manifest order, then cross-item checks. First violation wins (R1.13). Per object: unknown field, missing field, then field checks. Use `re.fullmatch` with explicit `[0-9]` ranges and `type(v) is int` for integers.
    - Implement every R1 rule: manifest shape (R1.1), file entries and `doc` entry fields (R1.2), JSON item fields (R1.3), doc bodies from Markdown with `path` equal to the entry path (R1.4), unique ids and resolving references (R1.5), non-subject authors only on `review` items that reference a subject-authored item (R1.6), unknown fields rejected (R1.7), the limits (R1.8), BOM, UTF-8, JSON and size checks (R1.9), CRLF and CR normalisation before hashing (R1.10), manifest order (R1.11).
    - Implement the decision 11 fail-closed rules: duplicate JSON keys and `NaN`/`Infinity` rejected; duplicate manifest `path` values rejected; `NUL` and lone surrogates rejected in text fields; `subject.handle` must match the author pattern; the manifest path rule also applies to an item's `path`; `url` needs a non-empty hostname; an empty `files` array is allowed; length limits are measured after normalisation; files the manifest does not list are never fetched.
    - Error text is `file: field name: fixed reason`; field names that came from a file are shown through `ascii(name)[:40]`; nothing from any value, title, body or Markdown ever appears (R1.9).
    - Implement `content_hash` exactly as algorithm A2 (sorted keys, no whitespace, `ensure_ascii=False`, `null` for the absent one of `url`/`path`, `bundle_id` and subject not hashed).
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13_

  - [x] 1.3 Implement the Chunker
    - Create `backend/src/recromancer/ingest/chunking.py` with `DEFAULT_CHUNK_SIZE = 1000`, `DEFAULT_CHUNK_OVERLAP = 150`, `MAX_CHUNKS_PER_ITEM = 10_000`, `source_text(item)` and `chunk_item(item, size, overlap)` per algorithm A3. Imports limited to `domain` and the stdlib (contract C2); no I/O.
    - Validate parameters first (`size >= 1`, `overlap >= 0`, `overlap < size`, else `ChunkingError`, no chunks). Empty source text returns `()`. Offsets count code points. Raise `ChunkingError` inside the loop as soon as the chunk count exceeds 10000. Chunk id is `<item_id>#<position:04d>`; each chunk carries item id, kind, author, timestamp and a `SourceLocator` with the same offsets and the item's `url` or `path`. Never alter, trim or normalise any character.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.10, 3.11, 3.12_

  - [x] 1.4 Create `ingest_cli.py` with the Bundle_Loader only, and enforce it in C8
    - Create `backend/src/recromancer/ingest_cli.py` containing only `ledgerline_dir()` (`importlib.resources.files("recromancer.ingest") / "fixtures" / "ledgerline"`) and `make_fetch(root: Traversable) -> FetchBytes`. No `main` yet (added in task 3.3). The module imports only `domain` and the stdlib here.
    - `make_fetch` rejects an absolute path, a backslash, an empty or `..` segment (defence in depth next to the reader), joins segments with `Traversable.joinpath`, for real `Path` roots checks `resolve()` stays under `root.resolve()` (symlink escape), reads at most `max_bytes + 1` bytes in `"rb"`, and maps `FileNotFoundError`, `IsADirectoryError`, `PermissionError` and `OSError` to `BundleError(f"cannot read bundle file {path}")` with no OS text.
    - In `backend/pyproject.toml`, insert `"ingest_cli",` as the first entry of the `layers` list of contract `layer-order` (C8), before `"bootstrap"`. Update `tests/unit/test_import_contracts.py::test_c8_layers_match_documented_order` to the new exact list. Update `.kiro/steering/structure.md`: add `ingest_cli.py` beside `bootstrap` at the top of the layer order and add `ingest/fixtures/` to the layout.
    - Write `backend/tests/unit/test_ingest_cli_loader.py` (BL): path rejections (a fetch spy shows no read happens for an unsafe path, R1.12), the size cap, error mapping without OS text, a backslash path under `tmp_path` that does not touch the environment (R7.7 part), a symlink escape case that skips when symlink creation is not permitted, and a `Traversable` that is not a `Path`. Verify design Risk 10 (`Path.resolve()` containment on Windows drive and UNC paths) and Risk 2 (`importlib.resources.files("recromancer.ingest")` joins a data directory without `__init__.py` on the editable install) and record the results.
    - Run `cd backend; lint-imports` and record `Contracts: 8 kept, 0 broken.`
    - _Requirements: 1.12, 2.11, 7.7_
    - Notes (task 1.4):
      - Risk 2 verified: with a probe directory `ingest/_risk2_probe/x.txt` and no `__init__.py`, `importlib.resources.files("recromancer.ingest") / "_risk2_probe"` returned a `WindowsPath` (`is_dir()` True) and `joinpath("x.txt").read_text()` returned `probe-ok` on the editable install. The probe was deleted; `git status --short src` shows no residue.
      - Risk 10 verified (Python 3.11.9, Windows): with `root = tmp.resolve()` (drive form `C:\...`), `resolve()` plus `is_relative_to(root)` is True for an existing child and for a missing child, and False for `a/../../zz` and for drive-relative `C:foo` (resolves to the current directory). A differently cased drive path resolved to the same canonical case and was True. For the `\\?\C:\...` form only (no network UNC was tried, to avoid a possible hang), `resolve()` keeps the `\\?\` prefix, so it does not equal the plain drive form and `is_relative_to` against a plain-drive root is False. Because `make_fetch` resolves both the root and every joined node from the same root object, the forms always match and containment works; a `\\?\` root compared with itself is True.
      - `lint-imports`: `Contracts: 8 kept, 0 broken.` (C8 now lists `ingest_cli` first.)
      - Symlink escape test skipped on this machine (symlink creation is not permitted without privilege); the test skips with a stated reason rather than failing.

  - [x] 1.5 Create the Ledgerline bundle
    - Create the files under `backend/src/recromancer/ingest/fixtures/ledgerline/`: `manifest.json`, `README.md` (states that everything is fictional), and `commits/`, `pull_requests/`, `reviews/`, `issues/`, `docs/` with 31 items (8 commits, 7 pull requests, 8 reviews, 4 issues, 4 docs), subject `priya-raman` ("Priya Raman", staff backend engineer who left in October 2026, owner of the fictional payments reconciliation service "Ledgerline"), `fictional: true`, manifest order by timestamp, ids such as `pr-0142-remove-redis-cache`, URLs only `https://example.invalid/ledgerline/...`. No `__init__.py`. UTF-8, LF, no BOM.
    - Plant the facts: the Redis removal (a pull request authored by the subject plus two review items on it, one by another author and one reply by the subject, one of them stating the reason); the idempotency keys (an incident issue stating the reason and a commit whose `references` include the issue id); the design review (a doc plus a subject-authored review referencing the doc and stating the objection and its reason). Non-subject items are only `review` items referencing a subject-authored item.
    - Plant the silences: no title or body matches `kubernetes|k8s|helm|hire|hiring|recruit|interview|candidate|headcount` (case-insensitive). No real person or company name.
    - Add `backend/src/recromancer/ingest/fixtures/** text eol=lf` to `.gitattributes` (R2.10).
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.10_

  - [x] 1.6 Write Bundle_Reader tests including EX-VIOLATIONS
    - Create `backend/tests/unit/test_bundle_reader.py` using hand-written in-memory bundles served through a fake `fetch`. Cover a valid bundle, the doc path (R1.4), order (R1.11) and the limits at the limit and at limit plus one (R1.8), the two-violation cases that prove first-violation order (R1.13), and the decision 11 rules with one example case per rule (duplicate keys, `NaN`, duplicate manifest path, `NUL`, lone surrogate, bad subject handle, item `path` rule, empty hostname, empty `files`, normalised length, unlisted file never fetched).
    - **EX-VIOLATIONS** (parametrized): one case per R1 violation (unknown manifest field, unknown item field, wrong `kind`, bad Item_Id, bad author, bad timestamp, both or neither of `url` and `path`, non-https url, duplicate id, dangling reference, non-subject author on a non-review or without a subject reference, missing file, BOM, invalid UTF-8, invalid JSON, unsafe `path`, title too long, too many references). Each case mutates one file of a valid bundle that contains a body sentinel and a Markdown sentinel, and asserts `BundleError`, that the message names the file and the field, and that it contains neither sentinel.
    - Named test `test_error_messages_never_contain_body_text` (R1.9).
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13_

  - [x] 1.7 Write the Ledgerline tests and the golden hash file
    - Create `backend/tests/unit/test_ledgerline.py` (LG), reading through `make_fetch(ledgerline_dir())`: 30 to 32 items, at least 2 of each kind, subject and `fictional` flag, README presence and fictional label, only `https` URLs with host `example.invalid`, the three planted facts resolve by id with the reason keywords in the stated items, the word scan fails on any case-insensitive match of the R2.7 list in any title or body, no real names, twice-read equality of items and hashes, no BOM and no CR byte in any fixture file, and that `.gitattributes` contains the LF line.
    - Create `backend/tests/unit/data/ledgerline_hashes.json` (item id to Content_Hash) and compare against it, with the regeneration PowerShell one-liner documented in the test file docstring (decision 17).
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10_

  - [x] 1.8 Write the package-data wheel test PK
    - Create `backend/tests/integration/test_wheel_package_data.py`: skip with a stated reason when `hatchling` is not importable; otherwise run `python -m hatchling build -t wheel -d <tmp>` for `backend/` through `subprocess` (no shell, under 60 seconds), open the zip, and assert every path in the Ledgerline manifest plus the manifest and README exist under `recromancer/ingest/fixtures/ledgerline/`. Verify design Risk 1 (hatchling includes non-`.py` files and honours `.gitignore`) and record the result.
    - _Requirements: 2.11_
    - **Notes (task 1.8)**
      - Per user override the test builds the wheel with `pip wheel --no-deps <backend>` via `subprocess` (no shell, 50 s timeout) and does not import hatchling. `test_packaging.py` has no wheel build to reuse; the command comes from the platform-foundation design. pip isolation downloads hatchling, so the test needs the package index.
      - Risk 1 result: yes. The wheel holds 33 entries under `recromancer/ingest/fixtures/` and the source tree holds 33 files (31 manifest `files[]` entries plus `manifest.json` and `README.md`). Every entry is byte-identical to its source file and no `__init__.py` was added.
      - `.gitignore` point: `git check-ignore -v` reports nothing for the fixture files. The root `.gitignore` has no pattern that matches them (its only data-like entry is `.pgdata/`). The other `.gitignore` files found are tool caches (`.hypothesis`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.import_linter_cache`, `.idea`).

  - [x] 1.9 Add the ports and the fakes
    - Add the `Embedder` and `EvidenceStore` protocols to `backend/src/recromancer/service/ports.py` exactly as in the design (`model_id`, `dimensions`, async `embed`; `signatures`, `replace_item`, `get_item`, `get_chunks`). Imports `domain` only.
    - Add `FakeEmbedder` and `FakeEvidenceStore` to `backend/tests/fakes.py` per the design: blake2b-derived deterministic unit vectors with call and text counters and `fail_on_call`; dictionaries keyed like the Postgres primary key, atomic swap on `replace_item`, dimension validation, failure injection by constructor argument (`fail_on_item_ids`), deterministic `snapshot()`.
    - Write `backend/tests/unit/test_fake_embedder.py` (FK): dimension, norm 1.0 within 1e-6, dependence on text only, `embed([])` returns `[]` and records the call, counters, small Hypothesis checks.
    - _Requirements: 4.1, 4.7, 4.8, 5.7, 6.1_

  - [x] 1.10 Implement the IngestionService and its tests including EX-REINGEST
    - Create `backend/src/recromancer/service/ingestion.py` with `IngestReport`, `IngestError` (`ErrorCode.DEPENDENCY_UNAVAILABLE`, message `ingest failed at item <id>: items=<n> chunks=<n> unchanged=<n> absent=<n>`), `ingest_signature` (`v1|<hash>|<size>|<overlap>|<model_id>`) and `IngestionService` per algorithm A4. Imports `domain`, `service.ports`, the stdlib and `structlog` only (contract C4); `read` and `chunk` are injected callables; `ingested_at` comes from the injected `Clock`; no `random` or `uuid`.
    - Behaviour: one embedder batch per item; skip when the stored signature equals the new one; replace atomically otherwise; on any `Exception` stop, keep earlier commits, leave that item's old state, raise `IngestError` from `None`; stored items absent from the bundle are kept and counted; other `bundle_id`s are untouched; one INFO summary on success, per-item DEBUG events with ids and lengths only; an invalid bundle writes nothing.
    - Write `backend/tests/unit/test_ingestion_service.py` (IS) with `FakeClock`, `FakeEmbedder`, `FakeEvidenceStore`: write, skip, replace, failure injection with counts and earlier items kept (R6.6), absent counting, other-bundle isolation, stored `ingested_at` equals the fake clock, an AST check that the module imports no `random` or `uuid`, invalid bundle leaves the store empty.
    - **EX-REINGEST**: ingest several items, edit one (longer body so it gains chunks), re-ingest, and assert the store equals a fresh store that ingested only the edited bundle (ignoring `ingested_at`), no stale chunk remains, only the edited item was embedded in the second run, others are `unchanged`; a second case shrinks the item and asserts surplus chunks are gone.
    - Named test `test_logs_contain_no_title_body_or_chunk_text` (R6.9): sentinel text in titles and bodies, scan the `log_stream` output at DEBUG for success and for an injected failure.
    - _Requirements: 6.1, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10_

  - [x] 1.11 Add property strategies, invariants and the generator-validity test
    - Extend the existing `backend/tests/property/strategies.py` and `backend/tests/property/invariants.py` (keep their current contents) (no test functions in `invariants.py`) with every shared helper the four properties need: `assert_lossless_cover`, `assert_boundary_rule` (an independent oracle scanning with explicit loops from `hi` downward, not `str.rfind`), chunk-id invariants, a CRLF rewrite helper, and a store-snapshot comparison helper.
    - Strategies follow the design's shared generator rules: text mixed with `"\n"`, `"\n\n"`, space, tab, `"\u00e9"`, `"\U0001F680"` and excluding surrogates and `NUL`; a composite that draws `size` then `overlap` in `[0, size - 1]`; a constructive bundle composite (unique Item_Ids, subject-authored items first, non-subject items only as `review` with a subject reference, `https://example.invalid/...` urls or paths, whole-second UTC timestamps) serialised with `json.dumps` and served through a fake `fetch`. Failure injection is by constructor argument, never by text.
    - Create `backend/tests/property/test_bundle_reader_properties.py` (PR) with the generator-validity test: every generated bundle passes `read_bundle` and keeps manifest order (R1.11, R8.9). Verify design Risk 12 (Hypothesis health checks on 5000 to 20000 character texts) here and record any needed `suppress_health_check`.
    - _Requirements: 1.11, 8.1, 8.9_
    - Notes (task 1.11):
      - Risk 12 verified: `test_long_lf_texts_generate_without_health_check_errors` draws `long_lf_texts()` (5000 to 20000 characters, no CR) under the default profile (100 examples) with no `suppress_health_check`, and it passes. `long_lf_texts()` draws a target length, up to 6 short pieces and up to 6 separators, then repeats them in a loop, so no single large text draw occurs (no `large_base_example` or `too_slow`). The bundle validity test also needs no suppression.
      - Oracle check: a one-off run of 300 examples of `evidence_items()` with `chunk_params()` and bodies from `chunk_texts()` or `long_lf_texts()` through `chunk_item` passed `assert_lossless_cover`, `assert_boundary_rule`, `assert_chunk_ids` and `assert_chunk_metadata`. R3.4 and `chunking.py` agree; no disagreement found.
      - `chunk_texts()` also excludes `\r` and `\ufeff` (a leading U+FEFF in a Markdown body would encode to a BOM and be rejected).

  - [x] 1.12 Write chunking tests, EX-PARAMS and Property 1
    - Create `backend/tests/unit/test_chunking.py` (CH) with fixed boundary examples (empty, length `size`, length `size + 1`, only `"\n"`, `"\n\n"` straddling `lo` and `hi`, `overlap = 0`, `overlap = size - 1`, `size = 1`, `size = 2`) and **EX-PARAMS** (parametrized): `size = 0`, `size = -1`, `overlap = -1`, `overlap = size`, `overlap = size + 1` raise `ChunkingError` and return nothing; `size = 1, overlap = 0` and `size = 5, overlap = 4` return chunks; `size = 2, overlap = 1` with a 10002-character item raises `ChunkingError`.
    - Create `backend/tests/property/test_chunking_properties.py::test_chunks_cover_text_without_loss`, tagged `# Feature: evidence-ingestion, Property 1: Chunking covers the text without loss and follows the boundary rule`. It uses `assert_lossless_cover` and `assert_boundary_rule` over texts including empty, whitespace-only, multi-byte and LF-only 5000 to 20000 character inputs.
    - **Mutation_Checks** (R8.5, R8.6), recorded as a comment beside the property: (a) drop the final character of the last chunk; (b) start the next chunk at `E + 1` instead of `E - overlap`; (c) use `rfind(..., lo, hi + 1)`. For each: apply it to `chunking.py`, confirm Property 1 fails, revert, and record the result in the task notes. Verify design Risk 11 (`str.isspace()` agreement with the oracle).
    - **Property 1: Chunking covers the text without loss and follows the boundary rule**
    - **Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.7, 3.8, 3.10, 3.11, 8.2**
    - _Requirements: 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.10, 3.11, 3.12, 8.2, 8.5, 8.6_
    - Notes (task 1.12):
      - Original SHA256 of `chunking.py`: `3BEC3C415C62D1B00326DE1DC8644C344AC0790445BF3A776211615654F8305F`. After every restore the hash was the same (match: yes), and the property passed after the restores of (a) and (c); the final hash after all work also matches. For (b) the post-restore pass is covered by the final run of both files (39 passed).
      - Mutation (a): in the final branch `end = n` became `end = n - 1`. Failed with `AssertionError: last end 1 != text length 2` (also `chunk 0: bad offsets 0..0`). Shrunk example: a two-character whitespace text in one chunk. Restored: yes, hash match yes, property passes.
      - Mutation (b): `start = end - overlap` became `start = end + 1`. Failed with `AssertionError: chunk 1: start 2 != previous end 1 - overlap 0` (also `chunk 1: bad offsets 2..2`). Shrunk example: title `'   '` (three spaces), overlap 0. Restored: yes, hash match yes. Procedure slip: I copied the mutated file over `$env:TEMP\chunking.py.orig` once, so I reverted this edit by hand; the hash then equalled the original before the backup was taken again. The mutated hash was `2F648DBD1B172E1D143DF549EB006731AA55A9F12FDD690B551EC2F465E8B88D`.
      - Mutation (c): `rfind("\n\n", lo, hi)` and `rfind("\n", lo, hi)` both changed to `hi + 1` (two edits applied before the single run). Failed with `AssertionError: chunk 0: end 2 != expected 1` (also `chunk 1: bad offsets 2..2`). Shrunk example: title `' \n '`. Restored: yes, hash match yes, property passes.
      - Generator strengthening: built in from the start rather than added after a surviving mutation. The property draws from `chunk_texts`, whitespace-only text, empty text, `long_lf_texts` and a local `_dense_texts` (short texts of `\n`, `\n\n`, space, tab, `\u00e9`, emoji and letters), with extra weight on sizes 1 to 30 so breaks fall at every offset around the window edge. Long texts use `size` 20 to 1000 and `overlap <= size // 2`. A `ChunkingError` is accepted only when the text is longer than 10000 characters. I did not run (c) against the weaker generator, so I cannot say whether it was needed.
      - Risk 11 verified: the oracle and `chunking.py` both use `str.isspace()`. `"\x1c"`, `"\x85"`, `"\u2028"` and `"\u3000"` are all `isspace()` and `test_whitespace_snap_uses_str_isspace` shows the whitespace snap ends the chunk right after each. No disagreement.
      - EX-PARAMS size 2, overlap 1: a 10002-character source gives chunk starts 0..10000, 10001 chunks, so `ChunkingError`; 10001 characters give exactly 10000 chunks and are accepted (both tested; the files run in under 1 s).

  - [x] 1.13 Write Property 2: chunk ids
    - In `backend/tests/property/test_chunking_properties.py` add `test_chunk_ids_are_deterministic_and_unique`, tagged `# Feature: evidence-ingestion, Property 2: Chunk ids are deterministic, well formed and unique`. Assert `<item_id>#<position:04d>` format, pairwise distinct ids within an item and across a bundle, and equal chunk tuples on a second run. Boundary cases: items whose ids are prefixes of each other (`abc`, `abc.1`), single-chunk and multi-chunk items.
    - **Mutation_Check** (R8.5, R8.6): build the chunk id from the position alone; confirm the uniqueness assertion fails, revert, record the result in the task notes.
    - **Property 2: Chunk ids are deterministic, well formed and unique**
    - **Validates: Requirements 3.6, 3.9, 8.2**
    - _Requirements: 3.6, 3.9, 8.2, 8.5, 8.6_
    - Notes (task 1.13):
      - Mutation: in `chunking.py` the id became `chunk_id=f"{position:04d}"` (position alone). Failing assertion: the cross-item uniqueness assertion (`chunk ids collide across items`, `assert 8 == 4`), which the test checks before the within-item and format assertions. Falsifying example: the explicit `@example` with items `abc` (`a b c d e`) and `abc.1` (`f g h i j`), size 3, overlap 1; ids `0000..0003` appear twice.
      - Original SHA256 of `chunking.py`: `3BEC3C415C62D1B00326DE1DC8644C344AC0790445BF3A776211615654F8305F`. Restored SHA256: `3BEC3C415C62D1B00326DE1DC8644C344AC0790445BF3A776211615654F8305F` (match: yes). After the restore the property passes (2 passed in the file).
      - Generator: 2 to 6 items with distinct ids drawn from prefix-related ids (`abc`, `abc.1`, `abc.1.1`, `abc-1`, `a`, `a.1`) or a generated id; titles from `_dense_texts` with sizes 1 to 30, so items give zero, one or many chunks. The property also asserts determinism (a second `chunk_item` run returns an equal tuple).

  - [x] 1.14 Write Property 3: content hash
    - Create `backend/tests/property/test_content_hash_properties.py` with `test_content_hash_ignores_line_endings_and_reacts_to_changes`, tagged `# Feature: evidence-ingestion, Property 3: Content_Hash ignores line-ending style and reacts to every content field`. Serialise each valid item with LF and with CRLF or lone CR in `title` and `body` (and Markdown bytes for docs), read both, and assert equal items and hashes; for a single change to `title`, `body`, `author`, `timestamp` or `references` that gives a different normalised value (drawn with `.filter(lambda v: v != original)`) assert the hash differs. Boundary cases: empty body, text ending in `\r`, `\r\r\n`.
    - **Mutation_Checks** (R8.5, R8.6): (a) skip line-ending normalisation before hashing, which must fail the CRLF half; (b) hash only `title` and `body`, which must fail the sensitivity half for `author`. For each: apply, confirm failure, revert, record in the task notes.
    - **Property 3: Content_Hash ignores line-ending style and reacts to every content field**
    - **Validates: Requirements 1.10, 8.4**
    - _Requirements: 1.10, 8.4, 8.5, 8.6_
    - Notes (task 1.14):
      - Generator: a commit (JSON), an issue (JSON) and a doc (manifest entry plus Markdown bytes) in one bundle; the subject handle equals the drawn author, so every item stays subject-authored and an author change stays valid. Texts mix `a b é`, LF and CR, with weighted boundary values (empty, `\r`, `a\r`, `\r\r\n`, `a\r\r\nb`, `\r\n`, `\n\r`). Line-ending half: the LF form is read against CRLF, lone CR and raw forms. Sensitivity half: `st.sampled_from` over the five fields, new value drawn with `.filter` on the normalised value (timestamp and author on the raw value); asserts the commit hash differs, and the issue and doc hashes differ too unless the field is `references`. Default profile, about 0.8 s.
      - Mutation (a): in `bundle.py` `_normalise` became `return s`. Failing assertion: `AssertionError: items differ between LF and other line endings` (the item equality assertion, `title='\n'` versus `title='\r\n'`). Falsifying example: `change=(_Content(title='\r', body='', author='1', timestamp=2000-01-01T00:00Z, references=()), 'author', <same with author='0'>), style='crlf'`. No other validation rejected the CRLF input first.
      - Mutation (b): `payload` in `content_hash` became `{"title": _normalise(item.title), "body": _normalise(item.body)}`. Failing assertion: `AssertionError: commit hash ignores author`. Falsifying example: `change=(_Content(title='a', body='', author='8', timestamp=2000-01-01T00:00Z, references=()), 'author', <same with author='0'>), style='crlf'`. Hashes were equal (`20459e94...bdd8`). The field was drawn from the five-field `sampled_from` and author was found on the first failing run.
      - Original SHA256 of `bundle.py`: `191FAE5E739AA74511A5FFD3470BEBC2E0E24416626B7B2E0A523E2EA540800B`. Restored SHA256 after (a) and after (b): `191FAE5E739AA74511A5FFD3470BEBC2E0E24416626B7B2E0A523E2EA540800B` (match: yes, both). After each restore the property passes (1 passed).

  - [x] 1.15 Write Property 4: idempotent ingestion
    - Create `backend/tests/property/test_ingestion_properties.py` with `test_ingesting_twice_changes_nothing`, tagged `# Feature: evidence-ingestion, Property 4: Ingesting a bundle twice changes nothing`. Use `anyio` with the `FakeClock` advanced between runs, the `FakeEmbedder` and `FakeEvidenceStore`: after the second run `snapshot()` equals the first (including `ingested_at` and signatures), the second report has `chunks == 0` and `unchanged == items`, and the embedder counters did not change. Boundary cases: zero items, one item, one chunk and many chunks.
    - **Mutation_Check** (R8.5, R8.6): add `ingested_at` to the Ingest_Signature; confirm the property fails (second run re-embeds), revert, record the result in the task notes.
    - **Property 4: Ingesting a bundle twice changes nothing**
    - **Validates: Requirements 6.3, 6.4, 6.8, 8.3**
    - _Requirements: 6.3, 6.4, 6.8, 8.3, 8.5, 8.6, 8.7_
    - Notes (task 1.15):
      - Test: synchronous `@given(bundles(), params)` calling `anyio.run` twice, with `FakeClock.advance(hours=1)` between runs. `params` is `chunk_params()` or the defaults (1000, 150). An `@example` covers the zero-item bundle; one-item and many-chunk cases come from the generators (0 to 6 items, sizes down to 1). Default profile, about 2 s.
      - Mutation: in `ingestion.py` `ingest()` the signature became `ingest_signature(...) + "|" + self._clock.now().isoformat()`. The property failed at its first assertion, `assert store.snapshot() == snapshot` (`AssertionError`), because run 2 saw a different signature, re-embedded and rewrote the item with a new signature and `ingested_at` (logs show `ingest_item_written` twice per bundle, `unchanged=0`). Falsifying example: one commit item `a00` (bundle `a00`, subject `a`), params `(1000, 150)`. The zero-item example cannot fail under this mutation, and Hypothesis found the non-empty case.
      - Original SHA256 of `ingestion.py`: `A279C82F03984975BAC09B9CA4042566D92DEA5774B86FDD12D4D5B76753A535`. Restored SHA256: `A279C82F03984975BAC09B9CA4042566D92DEA5774B86FDD12D4D5B76753A535` (match: yes). After the restore the property passes (1 passed).

  - [x] 1.16 Write the Ledgerline planted-facts ingestion test (R8.8)
    - Add to `backend/tests/unit/test_ledgerline.py` (or a new `test_ledgerline_ingest.py`) a test that reads the Ledgerline bundle through `make_fetch(ledgerline_dir())`, builds an `IngestionService` directly with `read_bundle`, `functools.partial(chunk_item, size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_CHUNK_OVERLAP)`, the `FakeEmbedder`, a `FakeEvidenceStore` and a `FakeClock`, ingests it, and asserts that the planted facts of R2.4, R2.5 and R2.6 resolve to existing Item_Ids in the store with at least one stored chunk each, and that the report counts equal the item count and `unchanged == 0`.
    - This is the last functional sub-task of the group; the gate follows.
    - _Requirements: 2.4, 2.5, 2.6, 8.8_

  - [x] 1.17 Group 1 gate
    - Run the gate twice as described under Conventions (database URL unset, then set). Group 1 has no Postgres tests, so both runs must pass with the same counts.
    - Confirm `lint-imports` prints `Contracts: 8 kept, 0 broken.` and the coverage floor holds. Report the final-message items (files changed, gate lines, mutation results, skips).
    - _Requirements: 3.1, 5.7, 6.1, 8.1, 8.7_

- [ ] 2. Group 2: Postgres migration, store, shared fixtures and contract tests
  - [ ] 2.1 Write migration 0002 after re-verifying pgvector
    - First run `docker exec recromancer-db-1 psql -U recromancer -d recromancer -At -c "SELECT name, default_version FROM pg_available_extensions WHERE name = 'vector';"`, confirm `default_version` is at least 0.5.0, and paste the command and its output into the header comment of the migration. Record the same text in the task notes for the Group 3 DEV_SETUP task. If the container is not running, start it with `docker compose up -d db` (from the repo root) and wait for readiness.
    - Create `backend/src/recromancer/db/migrations/0002_create_evidence_tables.sql` exactly as in the design's Data Models section: `evidence_items`, `evidence_chunks` with `vector(384)`, the check and foreign-key constraints, `evidence_chunks_item_idx`, and the HNSW index with `vector_cosine_ops` using plain `CREATE INDEX`. Leave `0001_create_vector_extension.sql` unmodified. UTF-8, LF, no BOM, no full-text column.
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.11_

  - [ ] 2.2 Update the Postgres test fixtures and the migration store search path
    - Change `PostgresMigrationStore` so that its search path is `<schema>, public` (decision 14); first grep the tests and confirm only the substring `search_path` is asserted, and update any test that matches the old form.
    - In `backend/tests/conftest.py` add the session-scoped fixture `vector_extension_in_public`, depending on `postgres_url`: run `CREATE EXTENSION IF NOT EXISTS vector SCHEMA public` once, then assert `SELECT extnamespace::regnamespace FROM pg_extension WHERE extname = 'vector'` returns `public`; if not, call `pytest.fail` (never skip). Make `pg_schema` depend on this fixture. Verify design Risk 9 (the role can create the extension in `public`, and dropping a throwaway schema with `CASCADE` leaves the extension in place) and record the result.
    - Change the skip text of the `postgres_url` fixture to `no database URL is configured` (decision 16); grep for any test asserting the old text first.
    - Confirm the existing migration and readiness tests still pass with and without `RECROMANCER_DATABASE_URL`.
    - _Requirements: 5.10_

  - [ ] 2.3 Implement `PostgresEvidenceStore`
    - Create `backend/src/recromancer/db/evidence_store.py` with `EMBEDDING_DIMENSIONS = 384` and `PostgresEvidenceStore(pool, *, workspace_id=DEFAULT_WORKSPACE_ID)` implementing the `EvidenceStore` port structurally. Imports `domain`, `db.postgres` private helpers, `psycopg`, `psycopg_pool`, `anyio` only; never `service` or `embed` (contracts C4, C7). Every method runs a sync function through `anyio.to_thread.run_sync`. Verify design Risk 4 (the installed `anyio.to_thread.run_sync` signature) and record it.
    - `replace_item`: validate every vector for length 384 and finite floats before connecting (`RecromancerError(VALIDATION_ERROR)` naming the chunk id and both lengths); then in one transaction upsert the item row, delete the item's chunks and insert the new chunks with `executemany`, vectors written as a `'[...]'` text literal built from `repr(float)` and cast with `%s::vector`; map driver errors to `DEPENDENCY_UNAVAILABLE` through `_safe_message`, `from None`. All queries filter on `workspace_id`. Reads parse `embedding::text` back to floats. Verify design Risks 3 and 8 here (untyped text parameter with `::vector` and `executemany`; `options` search path on pooled connections) and record the results.
    - Write `backend/tests/unit/test_evidence_store_errors.py` with `test_driver_error_has_no_conninfo` (mirrors the existing adapter test) and unit tests for the vector literal builder and the dimension and finiteness validation that need no database.
    - _Requirements: 5.7, 5.8, 5.9_

  - [ ] 2.4 Write the Evidence_Store contract suite CT
    - Create `backend/tests/contract/test_evidence_store_contract.py`, parametrized over `fake` and `postgres` as in `test_migration_store_contract.py`. Cases: empty bundle has no signatures; replace then `signatures`, `get_item`, `get_chunks` agree (vectors compared with relative tolerance 1e-6); replacing with fewer chunks leaves only the new chunks; a wrong vector length raises and leaves the old state; a failure injected after the delete (Postgres: a bad row in the batch; fake: `fail_on_item_ids`) leaves the old state; two bundle ids with the same item id do not interfere; another `workspace_id` sees nothing.
    - The Postgres variant uses `pg_schema`, a pool created with `kwargs={"options": f"-c search_path={schema},public"}`, and applies migrations 0001 and 0002 through the real runner. It skips with `no database URL is configured` when the variable is unset.
    - _Requirements: 5.8, 5.9, 5.10, 6.5_

  - [ ] 2.5 Write the migration test MG
    - Create `backend/tests/integration/test_migration_0002.py`: apply 0001 then 0002 through `run(PostgresMigrationStore(url, schema=...), discover(...))`; assert both are recorded, both tables exist, `pg_indexes` lists the HNSW index with `vector_cosine_ops`; assert inserts violating the kind check, the url/path exclusive-or check, the vector dimension and the foreign key are rejected; assert deleting an item cascades to its chunks; a bytes check that the migration file has no BOM and no `\r`; a check that 0001 is unmodified. Skip with `no database URL is configured` when unset (the bytes check runs without a database).
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.6, 5.10, 5.11_

  - [ ] 2.6 Group 2 gate
    - Run the gate twice as described under Conventions. The run with the URL set must execute the Postgres contract and migration tests with no skips; the run without it must skip them with the reason `no database URL is configured`.
    - Confirm `lint-imports` (C4 and C7 hold for `db/evidence_store.py`) and the coverage floor. Report the final-message items.
    - _Requirements: 5.7, 5.10_

- [ ] 3. Group 3: Settings, composition root and CLI with fakes
  - [ ] 3.1 Add the Settings fields and `redaction_values`
    - In `backend/src/recromancer/config.py` add `embedding_model_id` (default `BAAI/bge-small-en-v1.5`, 1 to 200 characters, no whitespace), `embedding_cache_dir` (`Path | None`, absolute when set, blank means `None`) and `embedding_offline` (bool, default false), all through `Settings.from_env` with the `RECROMANCER_` prefix, and `redaction_values()` returning `secret_values()` plus the database password.
    - Replace the private `_redaction_seed` in `db/migrate.py` with `Settings.redaction_values()` (decision 15) and keep the existing migrate tests green.
    - Add `RECROMANCER_EMBEDDING_MODEL_ID=BAAI/bge-small-en-v1.5`, `RECROMANCER_EMBEDDING_CACHE_DIR=` (blank) and `RECROMANCER_EMBEDDING_OFFLINE=false` to `.env.example`.
    - Extend `backend/tests/unit/test_config.py`: defaults, a relative cache dir rejected, blank cache dir is `None`, whitespace in the model id rejected, `redaction_values` content; and a test that every new variable appears in `.env.example`.
    - _Requirements: 4.2, 7.2_

  - [ ] 3.2 Add the bootstrap helpers for the store and the service
    - In `backend/src/recromancer/bootstrap.py` add `evidence_store(settings)` (a context manager that opens and closes the pool, `pool.open(wait=True, timeout=10)`, mapping `PoolTimeout` to `RecromancerError(DEPENDENCY_UNAVAILABLE, "database unavailable")`) and `build_ingestion_service(settings, *, store, embedder, clock)`. The latter binds `read_bundle` and `functools.partial(chunk_item, size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_CHUNK_OVERLAP)` and the same two constants into the service, and raises `RecromancerError(CONFIG_INVALID, "embedder dimensions A != column dimensions 384")` naming both numbers before returning, so nothing is written (R5.12). Typed assignments let mypy prove the structural port match.
    - `build_embedder` is not written here (it needs `FastembedEmbedder`, task 4.3 and 4.4).
    - Write `backend/tests/unit/test_bootstrap_ingestion.py` (BS): the dimension mismatch fails before any store call (the fake store shows no write), a matching embedder builds a working service that ingests a small bundle, and the helper wires the real `read_bundle` and `chunk_item`.
    - Run `lint-imports` and record that C6 and C8 hold.
    - _Requirements: 5.12, 6.2_

  - [ ] 3.3 Implement `ingest_cli.main()` and its tests
    - Extend `backend/src/recromancer/ingest_cli.py` (created in task 1.4) with `main(argv, *, environ, store_factory, embedder_factory, stdout, stderr, clock, dotenv_start)` and an `if __name__ == "__main__"` guard. Order of work: argument count first (usage line `usage: python -m recromancer.ingest_cli <bundle-dir>` on stderr, return 1, no factory called); `load_dotenv`; `Settings.from_env`; `configure_logging(stream=err, secret_values=settings.redaction_values())`; bundle directory check; `with store_factory(settings) as store`; `embedder_factory(settings)`; `build_ingestion_service`; `asyncio.run(service.ingest(make_fetch(root)))`; print exactly `items=<n> chunks=<n> unchanged=<n> absent=<n>` to stdout and return 0.
    - Failure contract: exactly one stderr line built as `" ".join(redact_text(message, settings.redaction_values()).split())`, nothing on stdout, return 1; unexpected exceptions print `unexpected error: <ClassName>`. Defaults for `store_factory` come from `bootstrap.evidence_store`. When no `embedder_factory` is injected, `main` fails closed with `no embedder is configured` (one line, exit 1) until task 4.4 wires `bootstrap.build_embedder`. `ingest_cli` imports `bootstrap`, `config`, `observability`, `service.ports` and `domain` only, never an adapter or `ingest` directly.
    - Write `backend/tests/unit/test_ingest_cli.py` (CLI) with fakes only: success line and exit 0, second run prints `chunks=0` (R7.6), relative path resolved against the current working directory, absolute and backslash paths, zero or two arguments print usage and call no factory, missing directory, invalid bundle, invalid settings, store failure whose message contains the database password, embedder failure, no embedder configured, the environment snapshot unchanged other than through `load_dotenv`, and `python -m recromancer.ingest_cli` importability (R7.1).
    - Named test `test_failure_prints_one_redacted_line_without_body` (R7.4), parametrized over invalid bundle with a sentinel body, store failure containing the password, embedder failure, missing directory and invalid settings: exactly one stderr line, empty stdout, exit code 1, and neither sentinel nor password appears.
    - _Requirements: 4.8, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8_

  - [ ] 3.4 Write the DEV_SETUP section
    - Add a new section to `docs/DEV_SETUP.md` containing: the commands `python -m recromancer.db.migrate` followed by `python -m recromancer.ingest_cli src\recromancer\ingest\fixtures\ledgerline`, both run from `backend/` (R7.9); the pgvector query and its recorded output from task 2.1 (R5.5); the cache directory example `C:\Users\<you>\.cache\recromancer` set through `RECROMANCER_EMBEDDING_CACHE_DIR` with the PowerShell form `$env:RECROMANCER_EMBEDDING_CACHE_DIR = "C:\Users\<you>\.cache\recromancer"` (decision 3); and the venv check `cd backend; python -c "import onnxruntime"` that must exit 0 (design V0). Leave the existing localhost lines untouched. The R4.11 fastembed findings are added in task 4.6.
    - _Requirements: 5.5, 7.9_

  - [ ] 3.5 Write the CLI-to-Postgres integration test with the Fake_Embedder
    - Create `backend/tests/integration/test_ingest_cli_postgres.py`. It skips with `no database URL is configured` when the variable is unset. It creates a throwaway schema through the shared fixtures (`pg_schema`, which also provides the `vector` type from `public`), applies migrations 0001 and 0002, builds an `environ` mapping with the database URL pointing at that schema through the search path, and calls `main([<ledgerline path>], environ=environ, embedder_factory=lambda s: FakeEmbedder(), stdout=..., stderr=...)`. `FakeEmbedder` lives in `tests/fakes.py`, so this is a test, not a CLI command.
    - Assert exit code 0 and stdout exactly `items=N chunks=M unchanged=0 absent=0` with `N` equal to the Ledgerline item count and `M` equal to the stored chunk count; run again and assert `chunks=0 unchanged=N`; assert nothing was written to stderr. Verify design Risk 14 (`asyncio.run` inside `main` on Windows with sync psycopg in worker threads) and record the result.
    - _Requirements: 4.8, 5.8, 6.2, 7.3, 7.6, 7.8_

  - [ ] 3.6 Group 3 gate
    - Run the gate twice as described under Conventions. The run with the URL set must execute `test_ingest_cli_postgres.py` without skipping; the run without it must skip with `no database URL is configured`.
    - Confirm `lint-imports` and the coverage floor. Report the final-message items, and state that the default CLI path (no injected embedder) is intentionally closed until Group 4.
    - _Requirements: 7.1, 7.8_

- [ ] 4. Group 4: real embedding model (prerequisite: `.venv` rebuilt from python.org CPython 3.11)
  - [ ] 4.1 Verify the prerequisite and stop if it fails
    - Run `cd backend; python -c "import onnxruntime"` with the venv active and check the exit code. If it exits non-zero (the access violation of design V0), stop the whole group, tell the user the `.venv` must be rebuilt from a python.org CPython 3.11, and do not run any later sub-task. If it exits 0, record `python -c "import sys; print(sys.version)"` in the task notes.
    - _Requirements: 4.3_

  - [ ] 4.2 Add the `fastembed` dependency and verify the library facts
    - Run `pip index versions fastembed` and record the output in the task notes (R4.10); add `fastembed>=0.9.1` (or the lower bound the output supports) to `[project].dependencies` in `backend/pyproject.toml`. Add no direct `onnxruntime` or `numpy` dependency (open question 2). Run `pip install -e "backend[dev]"` from the repo root and confirm it completes.
    - Verify against the installed version, without loading a model: the `TextEmbedding.__init__` parameter names (`cache_dir`, `local_files_only` through kwargs), `embed(..., batch_size=...)` returning a generator of ndarrays, and the `list_supported_models()` keys (`model`, `dim`) for `BAAI/bge-small-en-v1.5` (design Risks 5 and 6: run `cd backend; mypy` once the adapter exists in 4.3). Record the findings for task 4.6.
    - _Requirements: 4.10, 4.11_

  - [ ] 4.3 Implement `FastembedEmbedder` and its unit tests FE
    - Create `backend/src/recromancer/embed/fastembed_embedder.py` per the design: `fastembed` imported only inside `_default_model_factory` and `_lookup_dimensions` (never at module level), `model_factory` test seam, `model_id` and `dimensions` properties (an unknown model id fails at first use naming the id), `embed([])` returns `[]` without touching the factory, `anyio.to_thread.run_sync` with the default `abandon_on_cancel=False`, lazy model construction under a `threading.Lock`, batches of at most 64 with `batch_size=64`, `.tolist()` conversion, dimension check naming both numbers, normalisation per algorithm A5 (re-normalise when the norm is off by more than 1e-4, error when zero or non-finite), `cache_dir` passed explicitly and `local_files_only=True` when offline, one attempt and no retry loop, every load or embed failure mapped to `RecromancerError(DEPENDENCY_UNAVAILABLE)` naming the model id, `from None`. Mark the two real-library functions `# pragma: no cover`. Imports `domain.errors`, the stdlib, `anyio` only (contracts C6, C7).
    - Write `backend/tests/unit/test_fastembed_embedder.py` (FE) with a fake model factory and no `fastembed` or `onnxruntime` import: 150 texts give batches 64, 64, 22 in order and run in a worker thread; empty input never calls the factory; dimension mismatch names both numbers; a failing factory and the offline flag (`local_files_only` passed); a Hypothesis check that any non-zero finite vector comes back with norm 1.0 within 1e-3; an AST check that `fastembed` is imported only under `embed/` and never at module level; named test `test_errors_do_not_echo_input_text`.
    - Run `cd backend; mypy` and confirm `--strict` accepts the adapter (Risk 6); record the result.
    - _Requirements: 4.1, 4.3, 4.4, 4.5, 4.8, 4.12, 4.13_

  - [ ] 4.4 Add `build_embedder` and wire the default CLI path
    - In `bootstrap.py` add `build_embedder(settings) -> Embedder` constructing `FastembedEmbedder` from `embedding_model_id`, `embedding_cache_dir` and `embedding_offline`, assigned to a typed `Embedder` variable so mypy proves the structural match. Replace the Group 3 fail-closed default in `ingest_cli.main` with `bootstrap.build_embedder` (remove the `no embedder is configured` branch and its test, keep the injected seam).
    - Extend `test_bootstrap_ingestion.py` and `test_ingest_cli.py` to check that `build_embedder` returns an object with the settings' model id and, without calling `embed`, that the default factory is `build_embedder`; no test downloads or loads a model. Run `lint-imports`; only `bootstrap` may import `embed` (C6) and the count of contracts must be unchanged.
    - _Requirements: 4.6, 6.2, 7.2_

  - [ ] 4.5 Write the real-model test RM
    - Create `backend/tests/integration/test_real_embedder.py`. It runs a subprocess (`sys.executable -c ...`) so an import crash or a `ValueError` from an empty cache cannot kill pytest. The child builds `FastembedEmbedder(offline=True, cache_dir=<RECROMANCER_EMBEDDING_CACHE_DIR or None>)`, embeds two sample texts and prints `{"n": 2, "dims": 384, "norms": [...]}`. A non-zero exit or any error output skips with the reason `embedding model not available locally`; otherwise the test asserts two vectors, 384 dimensions and norm 1.0 within 1e-3. The test never downloads a model and must finish within 60 seconds. Pass the environment to the child through an explicit mapping, never by reading the real environment in the test body outside `config`.
    - _Requirements: 4.8, 4.9_

  - [ ] 4.6 Record the fastembed findings in DEV_SETUP
    - Add to `docs/DEV_SETUP.md` the R4.11 findings recorded in 4.2 and the design's V1 section: the model download source (`Qdrant/bge-small-en-v1.5-onnx-Q`, a quantized export), the default cache location (`%TEMP%\fastembed_cache`, override `FASTEMBED_CACHE_PATH`), the cache-directory parameter name, the offline option, the L2-normalisation result, the one-time first download (about 64 MiB, over 60 seconds) and the Hugging Face warnings that are expected on Windows. Reference the manual step 4.7.
    - _Requirements: 4.11_

  - [ ] 4.7 MANUAL (user): first real ingest of Ledgerline
    - **MANUAL (user). The agent must not run these commands**, because the first model load took 64.3 seconds, over the 60 second cap. The agent stops here, tells the user the commands, and waits for the user to report the output.
    - From `backend/` with the venv active and the compose database running:
      `$env:RECROMANCER_EMBEDDING_CACHE_DIR = "C:\Users\<you>\.cache\recromancer"`
      `python -m recromancer.db.migrate`
      `python -m recromancer.ingest_cli src\recromancer\ingest\fixtures\ledgerline`
    - Expected: exactly one stdout line `items=<n> chunks=<n> unchanged=0 absent=0` (the first run downloads about 64 MiB). Then run the same ingest command a second time (fast, cached model) and expect `items=<n> chunks=0 unchanged=<n> absent=0`.
    - _Requirements: 4.12, 6.2, 7.3, 7.6, 7.9_

  - [ ] 4.8 Re-run the real-model test and the final gate
    - After the user confirms 4.7, run `cd backend; pytest --no-cov tests/integration/test_real_embedder.py` with `RECROMANCER_EMBEDDING_CACHE_DIR` set to the same path; it must now run (not skip) and pass. If it still skips, report why.
    - Run the gate twice as described under Conventions (database URL unset, then set), keeping the same cache directory variable. Confirm `lint-imports` prints `Contracts: 8 kept, 0 broken.` (or the current count) and the coverage floor holds. Report the final-message items, including the user's step 4.7 output and the RM result.
    - _Requirements: 4.9, 8.7_

## Notes

- No task is marked optional (`*`): the design marks nothing optional, property tests are required by Requirement 8, and the real-model test is required and only skips when the cache is absent.
- Group 1 creates `ingest_cli.py` early so the Ledgerline tests and the R8.8 test read the fixture through the production loader; the C8 contract change is therefore in task 1.4, not Group 3.
- Group 3 leaves the default CLI path closed (`no embedder is configured`) and Group 4 opens it, so no group leaves unwired or failing code.
- Mutation_Check results for P1 (three mutations), P2, P3 (two) and P4 are recorded in the task notes of tasks 1.12 to 1.15.
- Tasks that verify design Risks: 1.4 (2, 10), 1.8 (1), 1.11 (12), 1.12 (11), 2.2 (9), 2.3 (3, 4, 8), 3.5 (14), 4.2 and 4.3 (5, 6). Risk 7 and 17 are observed during task 4.7 and noted in the final message. Risks 13 is checked at every gate. Risks 15 and 16 belong to the next spec.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["1.4", "1.5", "1.9"] },
    { "id": 3, "tasks": ["1.6", "1.7", "1.8", "1.10", "1.11"] },
    { "id": 4, "tasks": ["1.12", "1.14", "1.15"] },
    { "id": 5, "tasks": ["1.13", "1.16"] },
    { "id": 6, "tasks": ["1.17"] },
    { "id": 7, "tasks": ["2.1"] },
    { "id": 8, "tasks": ["2.2", "2.3"] },
    { "id": 9, "tasks": ["2.4", "2.5"] },
    { "id": 10, "tasks": ["2.6"] },
    { "id": 11, "tasks": ["3.1"] },
    { "id": 12, "tasks": ["3.2"] },
    { "id": 13, "tasks": ["3.3", "3.4"] },
    { "id": 14, "tasks": ["3.5"] },
    { "id": 15, "tasks": ["3.6"] },
    { "id": 16, "tasks": ["4.1"] },
    { "id": 17, "tasks": ["4.2"] },
    { "id": 18, "tasks": ["4.3"] },
    { "id": 19, "tasks": ["4.4", "4.5", "4.6"] },
    { "id": 20, "tasks": ["4.7"] },
    { "id": 21, "tasks": ["4.8"] }
  ]
}
```
