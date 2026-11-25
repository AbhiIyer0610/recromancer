# Requirements Document

## Introduction

Evidence Ingestion gets a departed engineer's recorded work into Postgres as searchable chunks.
It defines an on-disk Evidence_Bundle format (commits, pull requests, reviews, issues as JSON; docs
as Markdown; one manifest), ships a deterministic synthetic bundle for a fictional engineer, splits
each item into stable chunks with pure logic, embeds the chunks through an `Embedder` port with a
local `fastembed` adapter, stores items and chunks idempotently in Postgres with a pgvector HNSW
index, and exposes the whole flow as a PowerShell-runnable CLI. All commands are written for
Windows PowerShell and run from `backend/` with the venv active.

This spec reuses the seams of platform-foundation and does not restate them: the hexagonal layers
and import-linter contracts C1-C8, the forward-only migration runner (platform-foundation R7),
`Settings.from_env` as the only configuration reader (R3), `observability.redact_text`, and the CLI
conventions of `db/migrate.py` (R7.12-R7.14).

**Scope out:** GitHub API connectors (a later spec adds a `SourceConnector` adapter package),
retrieval and answering (hybrid search, RRF, the ask pipeline), any UI, authentication, and
multi-tenant workspaces.

## Glossary

- **Evidence_Bundle**: A directory holding one Manifest and the item files the Manifest lists.
- **Manifest**: The file `manifest.json` at the root of an Evidence_Bundle.
- **Evidence_Item**: One recorded unit of work (commit, pull request, review, issue or doc) with the fields defined in Requirement 1.
- **Item_Id**: The identifier of an Evidence_Item, matching `^[a-z0-9][a-z0-9._-]{2,63}$`, unique within one Evidence_Bundle.
- **Item_Kind**: One of `commit`, `pull_request`, `review`, `issue`, `doc`.
- **Subject**: The departed engineer named in the Manifest by `handle` and `display_name`.
- **Bundle_Reader**: The pure component in `ingest` that validates raw file texts and returns Evidence_Items; it performs no file or network I/O.
- **Bundle_Loader**: The component outside `ingest` that reads the bundle directory from disk, enforces the path-safety rule of Requirement 1 before reading any file, and passes file texts to the Bundle_Reader.
- **Bundle_Error**: The `RecromancerError` raised for an invalid Evidence_Bundle; it names the file and the field and never includes body text.
- **Content_Hash**: The SHA-256 hex digest of the canonical form of an Evidence_Item: JSON with sorted keys, no insignificant whitespace, UTF-8 encoded, line endings normalised to LF.
- **Chunker**: The pure function in `ingest` that splits an Evidence_Item's Chunk_Source_Text into Chunks.
- **Chunk_Source_Text**: The item title, followed by a blank line and the body when the body is non-empty; the Chunker's only input text.
- **Chunk**: A span of Chunk_Source_Text with a Chunk_Id, the Item_Id, Item_Kind, author, timestamp and a Source_Locator.
- **Chunk_Id**: The string `<item_id>#<position>`, where position is the zero-based chunk index zero-padded to 4 digits.
- **Source_Locator**: The Item_Id, the start and end character offsets in Chunk_Source_Text, and the item's `url` or `path`.
- **Embedder**: The Port in `service/ports.py` that converts a sequence of texts to a sequence of float vectors of a fixed dimension.
- **Fastembed_Embedder**: The `embed` adapter that implements the Embedder with the `fastembed` library and a local model.
- **Fake_Embedder**: The deterministic test implementation of the Embedder that derives vectors from a hash of the text and counts its calls.
- **Evidence_Store**: The Port in `service/ports.py` for persisting Evidence_Items and Chunks; its Postgres implementation lives in `db` and an in-memory implementation is used in tests.
- **Ingestion_Service**: The service in `service/` that reads items through the Bundle_Reader output, decides what changed, calls the Embedder and writes through the Evidence_Store.
- **Ingest_Signature**: The string combining the Content_Hash, the chunk size, the chunk overlap and the embedding model id; two items with equal signatures need no re-embedding.
- **Ingest_Report**: The counts of items seen, chunks written, items skipped as unchanged and items absent from the bundle.
- **Ingest_CLI**: The module `recromancer.ingest_cli`, run as `python -m recromancer.ingest_cli <bundle-dir>`.
- **Ledgerline_Bundle**: The checked-in synthetic Evidence_Bundle for the fictional engineer Priya Raman and the fictional service Ledgerline.
- **Planted_Fact**: A fact in the Ledgerline_Bundle that a later spec's golden questions can answer from cited items.
- **Planted_Silence**: A topic that the Ledgerline_Bundle deliberately never mentions.
- **Mutation_Check**: A note naming a deliberate code change that a given property test must detect by failing.

---

## Requirements

### Requirement 1: Evidence Bundle Format

**User Story:** As an operator, I want a precisely specified bundle format with strict validation,
so that recorded work enters the system in a known shape and malformed input is rejected with a
useful message.

#### Acceptance Criteria

1. THE Manifest SHALL be a JSON object with exactly these fields: `bundle_id` (string matching the Item_Id pattern), `schema_version` (integer, currently `1`), `subject` (object with string fields `handle` and `display_name`), `fictional` (boolean), and `files` (array of file entries).
2. THE Manifest SHALL define each file entry as an object with `path` (bundle-relative, forward slashes, no `..` segment, no absolute path) and `kind` (an Item_Kind); for `doc` entries the object SHALL also carry `id`, `author`, `timestamp` and `title`.
3. THE Bundle_Reader SHALL require each JSON item file to contain one JSON object with exactly these fields: `id` (Item_Id), `kind` (Item_Kind equal to the Manifest entry's kind), `author` (handle matching `^[A-Za-z0-9][A-Za-z0-9-]{0,38}$`), `timestamp` (ISO-8601 UTC of the form `YYYY-MM-DDTHH:MM:SSZ`), `title` (non-empty string), `body` (string, may be empty), exactly one of `url` (string with scheme `https`) or `path` (repo-relative string), and `references` (array of Item_Ids, may be empty).
4. THE Bundle_Reader SHALL take the body of a `doc` item from the Markdown file and the fields `id`, `author`, `timestamp` and `title` from the Manifest entry, and SHALL require such an item to carry `path` equal to the entry's `path`.
5. THE Bundle_Reader SHALL require every Item_Id to be unique within the Evidence_Bundle and every id in `references` to name an Evidence_Item in the same Evidence_Bundle.
6. THE Bundle_Reader SHALL require every Evidence_Item whose `author` differs from the Subject's `handle` to have kind `review` and to reference at least one Evidence_Item authored by the Subject.
7. IF a Manifest or item file contains a field that this requirement does not define, THEN THE Bundle_Reader SHALL raise a Bundle_Error naming the file and the unknown field.
8. THE Bundle_Reader SHALL enforce these limits: Manifest at most 262144 bytes, each item file at most 1048576 bytes, at most 5000 files, `title` at most 300 characters, `body` at most 200000 characters, `references` at most 100 entries.
9. IF a file is missing, exceeds a limit, is not valid UTF-8, starts with a UTF-8 byte order mark, is not valid JSON where JSON is required, or has a field that violates this requirement, THEN THE Bundle_Reader SHALL raise a Bundle_Error that names the file and the field and contains no part of any `body` or Markdown text.
10. THE Bundle_Reader SHALL normalise CRLF and lone CR line endings in every text field to LF before the Content_Hash is computed.
11. THE Bundle_Reader SHALL return the Evidence_Items in the order of the Manifest `files` array.
12. IF a Manifest `path` is absolute, contains a `..` segment, contains a backslash, or resolves outside the bundle root, THEN THE Bundle_Loader SHALL raise a Bundle_Error naming the Manifest and the offending `path` before opening that file, with the path rule validated by the Bundle_Reader on the Manifest and enforced by the Bundle_Loader before any read.
13. IF a Bundle_Reader input violates more than one rule, THEN THE Bundle_Reader SHALL raise a single Bundle_Error for the first violation found, processing the Manifest before item files and item files in `files` order.

---

### Requirement 2: Ledgerline Synthetic Bundle

**User Story:** As a developer, I want a deterministic, checked-in synthetic bundle with planted facts
and planted silences, so that later specs can test grounded answers and abstention on stable data.

#### Acceptance Criteria

1. THE Repository SHALL contain the Ledgerline_Bundle as plain files under `backend/src/recromancer/ingest/fixtures/ledgerline/`, committed to git and never generated at test time.
2. THE Ledgerline_Bundle SHALL contain between 30 and 32 Evidence_Items inclusive, with at least 2 Evidence_Items of each of the five Item_Kinds, and the Subject `priya-raman` (display name "Priya Raman", staff backend engineer who left in October 2026, owner of the fictional payments reconciliation service "Ledgerline").
3. THE Ledgerline_Bundle SHALL set `fictional` to `true` in the Manifest, label itself as fictional in a `README.md` inside the bundle directory, and use only `https` URLs whose host is `example.invalid`.
4. THE Ledgerline_Bundle SHALL contain the Planted_Fact "why the Redis cache was removed" as a pull request authored by the Subject and at least one review thread on that pull request, where the pull request or thread body states the reason for removal.
5. THE Ledgerline_Bundle SHALL contain the Planted_Fact "why idempotency keys were added" as an incident issue whose body states the reason and a commit whose `references` include the issue's Item_Id.
6. THE Ledgerline_Bundle SHALL contain the Planted_Fact "a design review where the Subject objected to a proposal" as a doc and a review item authored by the Subject whose `references` include the doc's Item_Id and whose body states the objection and its reason.
7. THE Ledgerline_Bundle SHALL contain no mention of a Kubernetes migration and no mention of hiring; a unit test SHALL fail IF any case-insensitive match of `kubernetes`, `k8s`, `helm`, `hire`, `hiring`, `recruit`, `interview`, `candidate` or `headcount` appears in any title or body.
8. THE Ledgerline_Bundle SHALL contain no real person or company name; a unit test SHALL fail IF any URL host other than `example.invalid` appears in any item.
9. WHEN the Bundle_Reader reads the Ledgerline_Bundle twice, on the same or different operating systems (Windows and Linux), THE Bundle_Reader SHALL return identical Evidence_Items in identical order and identical Content_Hashes.
10. THE Repository SHALL store every file of the Ledgerline_Bundle as UTF-8 without a byte order mark, and `.gitattributes` SHALL force LF line endings for `backend/src/recromancer/ingest/fixtures/**` so that checkouts on Windows and Linux are byte-identical.
11. THE package build SHALL include every Ledgerline_Bundle file in the installed `recromancer` package, so that the Bundle_Loader run against the installed package returns the same Evidence_Items as run from the source tree.

---

### Requirement 3: Deterministic Chunking

**User Story:** As a developer, I want pure, deterministic chunking with stable ids and traceable
source locators, so that every retrieved passage can be cited back to its exact place in the record.

#### Acceptance Criteria

1. THE Chunker SHALL be a pure function in `backend/src/recromancer/ingest/` that imports only `domain` within the project and performs no I/O (contract C2).
2. THE Chunker SHALL measure size and overlap in Unicode code points, with a chunk size of 1000 and an overlap of 150, both passed as parameters and set in the code only as defaults.
3. WHEN the Chunk_Source_Text is non-empty and no longer than the chunk size, THE Chunker SHALL return exactly one Chunk with start offset 0 and end offset equal to the length of the Chunk_Source_Text.
4. WHEN the remaining text from a Chunk's start offset is longer than the chunk size, THE Chunker SHALL end that Chunk at an offset E in the range [start + size - window, start + size], where window = min(200, size - overlap - 1); E SHALL be the offset immediately after the last paragraph break ("\n\n") lying entirely inside that range, else immediately after the last line break, else immediately after the last Unicode whitespace character in the range, else start + size.
5. WHEN a Chunk ends at an offset E before the end of the Chunk_Source_Text, THE Chunker SHALL start the next Chunk at E - overlap, and WHEN the remaining text from a Chunk's start offset is no longer than the chunk size, THE Chunker SHALL end that Chunk, the final Chunk, at the end of the Chunk_Source_Text; start offsets SHALL strictly increase and no Chunk SHALL start after the previous Chunk's end.
6. THE Chunker SHALL assign each Chunk the Chunk_Id formed from the Item_Id and the zero-based position zero-padded to 4 digits, so that equal inputs yield equal Chunk_Ids across runs; IF an item would produce more than 10000 Chunks, THEN THE Chunker SHALL raise an error and return no Chunks.
7. THE Chunker SHALL attach to each Chunk the Item_Id, Item_Kind, author, timestamp and a Source_Locator holding the Item_Id, the start and end offsets in Chunk_Source_Text, and the item's `url` or `path`.
8. FOR ALL Evidence_Items, the text of each Chunk SHALL equal the slice of Chunk_Source_Text between its start and end offsets, the union of all Chunk spans SHALL equal [0, length of Chunk_Source_Text) with no gap, and consecutive Chunks SHALL overlap by exactly the overlap parameter, so that no text is lost and none is duplicated beyond the overlap.
9. FOR ALL Evidence_Items, the Chunk_Ids of one item SHALL be pairwise distinct, and the Chunk_Ids of distinct items in one Evidence_Bundle SHALL be pairwise distinct.
10. THE Chunker SHALL return Chunks without altering, trimming or normalising any character of Chunk_Source_Text.
11. WHEN the Chunk_Source_Text is empty, THE Chunker SHALL return an empty list of Chunks and raise no error.
12. IF the chunk size is less than 1, or the overlap is negative, or the overlap is not less than the chunk size, THEN THE Chunker SHALL raise a validation error and return no Chunks.

---

### Requirement 4: Embedder Port and Local Adapter

**User Story:** As a developer, I want embeddings behind a swappable port with a local model adapter,
so that ingestion runs without a hosted service and tests never need a model.

#### Acceptance Criteria

1. THE Embedder Port SHALL be defined in `backend/src/recromancer/service/ports.py` with an async method that takes a sequence of texts and returns one vector per text in the same order, and with read-only `model_id` and `dimensions` properties; WHEN the sequence is empty, THE Embedder implementations SHALL return an empty list without loading the model or making a network call.
2. THE Settings SHALL provide the fields `embedding_model_id` (default `BAAI/bge-small-en-v1.5`), `embedding_cache_dir` (optional path) and `embedding_offline` (bool, default false), all read through `Settings.from_env` with the prefix `RECROMANCER_`, and `.env.example` SHALL list each new variable.
3. THE Fastembed_Embedder SHALL live in `backend/src/recromancer/embed/`, import `fastembed` only inside that package, receive its model id and cache directory from Settings through the Composition_Root, and not hard-code a model id.
4. THE Fastembed_Embedder SHALL run the synchronous `fastembed` call through `anyio.to_thread` so that the event loop is not blocked, and SHALL embed at most 64 texts per model call, preserving input order across batches.
5. IF the vector length returned by the model differs from the `dimensions` property of the Fastembed_Embedder, THEN THE Fastembed_Embedder SHALL raise a RecromancerError naming both values.
6. THE Composition_Root SHALL be the only module that wires the Fastembed_Embedder to the Embedder Port.
7. THE Fake_Embedder SHALL return a vector of the dimension passed to the Fake_Embedder at construction with Euclidean norm 1.0 (tolerance 1e-6) that depends only on the input text, and SHALL record the number of texts it was asked to embed.
8. THE default test run SHALL use the Fake_Embedder only, SHALL make no network call and SHALL download no model.
9. WHERE the model named by `embedding_model_id` is present in the local cache, THE Test_Suite SHALL run one test that embeds two sample texts with the Fastembed_Embedder in offline-only mode and asserts two vectors of 384 dimensions with Euclidean norm 1.0 (tolerance 1e-3); otherwise that test SHALL skip with the reason "embedding model not available locally".
10. WHEN a dependency is added to `backend/pyproject.toml` by this spec, THE implementer SHALL choose its lower bound from the output of `pip index versions <name>` run on the day of the change, not from memory, and SHALL record the command output in the task notes.
11. THE implementer SHALL verify, against the installed `fastembed` version, the model's download source, its default cache location, the cache-directory parameter name, the offline-only option and whether returned vectors are L2-normalised, and SHALL record the findings in `docs/DEV_SETUP.md`.
12. WHEN the Ingest_CLI runs with a model that is not yet cached and offline-only mode is not set, THE Fastembed_Embedder SHALL download the model once, in a single attempt with no retries and no backoff, into `embedding_cache_dir` or the library's default cache location; IF the model is not cached and the download fails or offline-only mode is set, THEN THE Fastembed_Embedder SHALL raise a RecromancerError naming the model id.
13. THE Fastembed_Embedder SHALL return L2-normalised vectors (Euclidean norm 1.0, tolerance 1e-3), normalising the model output itself if the library does not.

---

### Requirement 5: Evidence Storage and Migration

**User Story:** As a developer, I want evidence items and chunks stored in Postgres with a vector
index, so that a later retrieval spec can run similarity search over them.

#### Acceptance Criteria

1. THE migration `backend/src/recromancer/db/migrations/0002_create_evidence_tables.sql` SHALL create the tables `evidence_items` and `evidence_chunks` and SHALL leave `0001_create_vector_extension.sql` unmodified.
2. THE `evidence_items` table SHALL have the columns `workspace_id` (text, not null, default `'default'`), `bundle_id`, `item_id`, `kind`, `author`, `subject_handle`, `occurred_at` (timestamptz), `title`, `body`, `url`, `path`, `references_ids` (text array), `content_hash`, `ingest_signature` and `ingested_at` (timestamptz), with the primary key `(workspace_id, bundle_id, item_id)`, a check constraint that limits `kind` to the five Item_Kinds, and a check constraint that requires exactly one of `url` and `path` to be non-NULL.
3. THE `evidence_chunks` table SHALL have the columns `workspace_id`, `bundle_id`, `item_id`, `chunk_id`, `position`, `start_offset`, `end_offset`, `text`, `embedding` of type `vector(384)`, and `embedding_model`, with the primary key `(workspace_id, bundle_id, chunk_id)` and a foreign key `(workspace_id, bundle_id, item_id)` to `evidence_items` with `ON DELETE CASCADE`.
4. THE migration SHALL create an HNSW index on `evidence_chunks.embedding` using `vector_cosine_ops` with plain `CREATE INDEX`, not `CREATE INDEX CONCURRENTLY`, so that the file runs inside the Migration_Runner's single transaction.
5. WHEN the implementer begins writing the migration, THE implementer SHALL first run `SELECT name, default_version FROM pg_available_extensions WHERE name = 'vector';` against the compose `db` container, confirm `default_version` is 0.5.0 or later, and record the command and its output in a comment at the top of the migration file and in `docs/DEV_SETUP.md`.
6. WHEN the Migration_Runner applies `0002_create_evidence_tables.sql` to a database that already holds migration 0001, THE Migration_Runner SHALL record the migration and leave the database with both tables and the HNSW index.
7. THE Postgres Evidence_Store SHALL live in `backend/src/recromancer/db/`, implement the Evidence_Store Port structurally and import neither `service` nor `embed`.
8. THE Evidence_Store SHALL provide an operation that replaces one Evidence_Item and all of its Chunks in a single transaction, deleting the old Chunks and inserting the new Chunks and the item row together, so that no concurrent reader sees a partial item.
9. IF a Chunk's embedding length differs from the column dimension (384), THEN THE Evidence_Store SHALL reject the write with a RecromancerError and leave the stored data unchanged.
10. THE Evidence_Store SHALL run the same contract test suite against its in-memory implementation and, WHERE `RECROMANCER_DATABASE_URL` is configured, against the Postgres implementation, and SHALL skip the Postgres run with the reason "no database URL is configured" otherwise.
11. THE migration file SHALL be UTF-8 without a byte order mark with LF line endings (platform-foundation R7.16).
12. IF the `dimensions` property of the wired Embedder differs from the fixed column dimension 384, defined as a constant in the `db` package, THEN THE Composition_Root SHALL fail at startup with a RecromancerError naming both values and SHALL NOT write any Chunk.

---

### Requirement 6: Idempotent Ingestion Service

**User Story:** As an operator, I want re-ingesting a bundle to change only what changed, so that
repeated runs are cheap, safe and predictable.

#### Acceptance Criteria

1. THE Ingestion_Service SHALL live in `backend/src/recromancer/service/`, depend only on the Embedder and Evidence_Store Ports, a Clock Port and injected pure callables for reading and chunking, and import neither `ingest`, `embed` nor `db` (contract C4).
2. THE Composition_Root SHALL wire the Bundle_Reader, the Chunker, the Fastembed_Embedder and the Postgres Evidence_Store into the Ingestion_Service.
3. WHEN an Evidence_Item is not stored, THE Ingestion_Service SHALL chunk it, embed its Chunks in one batch and write the item and its Chunks through the Evidence_Store replace operation, and SHALL count the item and its Chunks as written in the Ingest_Report.
4. WHEN an Evidence_Item is stored with an Ingest_Signature equal to the newly computed Ingest_Signature, THE Ingestion_Service SHALL neither call the Embedder nor write to the Evidence_Store for that item, and SHALL count the item as skipped as unchanged.
5. WHEN an Evidence_Item is stored with a different Ingest_Signature, THE Ingestion_Service SHALL replace the item and all of its Chunks in one Evidence_Store transaction, so that a reader sees either all old Chunks or all new Chunks of that item and never a mixture.
6. IF the Embedder or the Evidence_Store fails for one Evidence_Item, THEN THE Ingestion_Service SHALL process no further Evidence_Items in that run, leave that item's stored state exactly as it was before the run, keep every item committed earlier in the same run, and raise a RecromancerError naming the Item_Id and the Ingest_Report counts reached so far.
7. WHEN an Evidence_Item stored for the same `bundle_id` is absent from the supplied Evidence_Bundle, THE Ingestion_Service SHALL keep the stored item and its Chunks and SHALL count the item as absent in the Ingest_Report. Evidence_Items stored under a different `bundle_id` SHALL be neither counted as absent nor modified.
8. THE Ingestion_Service SHALL take the `ingested_at` timestamp from the injected Clock and SHALL generate no random values.
9. THE Ingestion_Service SHALL write log entries that contain Item_Ids, counts and character lengths and no title, body or Chunk text.
10. THE Ingestion_Service SHALL store only Evidence_Items that the Bundle_Reader accepted, so that no item outside the validated Evidence_Bundle is written.

---

### Requirement 7: Ingestion CLI

**User Story:** As an operator, I want one PowerShell command to ingest a bundle, so that I can load
the demo corpus or a new bundle without writing code.

#### Acceptance Criteria

1. THE Ingest_CLI SHALL be the module `backend/src/recromancer/ingest_cli.py`, invoked as `python -m recromancer.ingest_cli <bundle-dir>` from `backend/`, and SHALL NOT be a `__main__` module inside the `recromancer.ingest` package. Reason for deviating from the requested `python -m recromancer.ingest <bundle-dir>`: `recromancer.ingest` is pure logic under import-linter contract C2 (domain-only imports), and a `__main__` there would import the database, the Embedder and the Composition_Root, which breaks that contract; the reviewer may overrule this by exempting that one module from C2.
2. THE Ingest_CLI SHALL follow the conventions of platform-foundation R7.12-R7.14: a `main()` with injectable `environ`, store factory, embedder factory, `stdout`, `stderr` and clock, configuration through `load_dotenv` and `Settings.from_env`, and collaborators obtained only from the Composition_Root.
3. WHEN the ingestion completes, THE Ingest_CLI SHALL print exactly one line `items=<n> chunks=<n> unchanged=<n> absent=<n>` to standard output and exit with code 0, where `items` counts every item read from the Evidence_Bundle in this run, including unchanged items.
4. IF the bundle directory is missing, the Evidence_Bundle is invalid, the configuration is invalid, or the database or the Embedder fails, THEN THE Ingest_CLI SHALL print exactly one line to standard error that passes through `observability.redact_text` and contains no item body text, SHALL print nothing to standard output, and SHALL exit with code 1.
5. IF the Ingest_CLI is invoked with zero arguments or with more than one argument, THEN THE Ingest_CLI SHALL print a one-line usage message to standard error, make no database or Embedder call, and exit with code 1.
6. THE Ingest_CLI SHALL count `chunks` as the number of Chunks written in this run, so that a second run on an unchanged Evidence_Bundle prints `chunks=0`.
7. THE Ingest_CLI SHALL resolve a relative `<bundle-dir>` against the current working directory, SHALL accept absolute paths and Windows backslash paths (for example `C:\data\bundle` and `src\recromancer\ingest\fixtures\ledgerline`), SHALL read every bundle file as UTF-8, and SHALL leave the process environment unchanged other than through `load_dotenv`.
8. THE Ingest_CLI tests SHALL run with an in-memory Evidence_Store and the Fake_Embedder and SHALL make no network call.
9. THE Repository SHALL document in a new section of `docs/DEV_SETUP.md` the commands `python -m recromancer.db.migrate` followed by `python -m recromancer.ingest_cli src\recromancer\ingest\fixtures\ledgerline` run from `backend/`.

---

### Requirement 8: Property-Based Verification

**User Story:** As a maintainer, I want the ingestion guarantees verified by property-based tests that
are proven able to fail, so that chunking and idempotency cannot regress silently.

#### Acceptance Criteria

1. THE Test_Suite SHALL place the ingestion properties in `backend/tests/property/` and define their reusable invariants in the Invariants_Module.
2. FOR ALL generated Chunk_Source_Texts (including empty-body items, multi-byte characters, whitespace-only text, and LF-only text of 5000 to 20000 characters) and valid chunk size and overlap parameters, THE Test_Suite SHALL verify the lossless-coverage invariant of criterion 3.8, the Chunk_Id uniqueness of criterion 3.9, and that two runs on equal input yield equal Chunks.
3. FOR ALL generated Evidence_Bundles, THE Test_Suite SHALL verify that ingesting the same Evidence_Bundle twice leaves the Evidence_Store content equal to the content after the first run, and that the second run calls the Fake_Embedder zero times (idempotency property).
4. FOR ALL valid Evidence_Items, THE Test_Suite SHALL verify that the Content_Hash is unchanged by CRLF versus LF line endings in the source text, and changes when any of `title`, `body`, `author`, `timestamp` or `references` changes.
5. THE Test_Suite SHALL record a Mutation_Check for each property as a comment beside it, naming the deliberate change that must make the property fail: for lossless coverage, dropping the final character of the last Chunk, or starting the next Chunk at E + 1 instead of E - overlap so that a character between Chunks is skipped; for Chunk_Id stability and uniqueness, building the Chunk_Id from the position alone; for idempotency, adding the `ingested_at` value to the Ingest_Signature.
6. THE implementer SHALL run each Mutation_Check once before the task is marked complete, SHALL confirm that the named property fails, and SHALL record the result in the task notes.
7. THE Test_Suite SHALL inject the clock and the id source into every test of the Ingestion_Service, SHALL use the Fake_Embedder, and SHALL make no real network call.
8. THE Test_Suite SHALL include a test that reads the Ledgerline_Bundle, ingests it into the in-memory Evidence_Store and asserts that the Planted_Facts of Requirement 2 criteria 2.4-2.6 resolve to existing Item_Ids and Chunks.
9. THE Test_Suite SHALL generate Evidence_Bundles only from strategies that satisfy Requirement 1 (valid Item_Ids, every reference resolving within the bundle, non-subject authors only on review items), and SHALL include a generator-validity test that passes every generated bundle through the Bundle_Reader.

---

## Decisions and open questions

Each entry gives the question, the proposed default (already written into the requirements above), and the reason.

1. **Embedding model versus SCOPE_AND_DESIGN.** The design document (D4, section 6) names BGE-M3 at 1024 dimensions with `vector(1024)`; this spec uses `BAAI/bge-small-en-v1.5` at 384 dimensions as instructed. Default: 384 now, `docs/SCOPE_AND_DESIGN.md` updated by the owner later. Reason: a small local model runs without a hosted service; a model change needs a new migration and a re-embed because the column dimension is fixed, and the Ingest_Signature includes the model id so a re-run re-embeds.
2. **Unknown fields.** Default: reject (criterion 1.7). Reason: silent ignoring hides typos and lets unreviewed content reach the store.
3. **Markdown doc metadata.** Default: the Manifest entry supplies `id`, `author`, `timestamp` and `title` (criterion 1.4). Reason: it avoids a YAML front-matter parser and a new dependency, and keeps docs plain Markdown.
4. **Reviews and items by other authors.** Default: ingest an item by another author only when it is a `review` that references a Subject-authored item (criterion 1.6). Reason: the Redis thread needs the other reviewers' replies for context, while product principle 3 limits scope to the Subject's work. Alternative: Subject-authored items only, which loses the review context.
5. **Fixture location.** Default: inside the package at `backend/src/recromancer/ingest/fixtures/ledgerline/`. Reason: demo mode is seeded data on the real code path, so the bundle must ship with the installed package; the cost is data files inside a pure-logic package and a larger wheel. A repo-level `fixtures/` directory keeps the package clean but cannot be loaded from an installed package.
6. **Chunk size, overlap and boundary rule.** Default: 1000 code points, 150 overlap (R3). Each non-final Chunk ends at an offset E within [start + size - window, start + size], where window = min(200, size - overlap - 1), snapped to just after the last paragraph break, else line break, else whitespace, else start + size; the next Chunk starts at E - overlap. Reason: code points need no tokenizer, so `ingest` stays stdlib-only; the window bound guarantees the start offset strictly increases; bge-small has a 512-token window, and 1000 characters is about 250 tokens of English, which fits. Verify the token fit with the installed model before the design is final.
7. **Title in Chunk_Source_Text.** Default: the title is the first line of Chunk_Source_Text, so title-only commits remain searchable and offsets stay consistent. Reason: one text per item keeps the lossless invariant simple.
8. **Items that disappear from a bundle.** Default: keep and report as `absent` (criterion 6.7). Reason: deletion is irreversible and a partial bundle must not erase data. A purge for consent (product principle 3) is a separate, explicit operation for a later spec.
9. **Default `workspace_id`.** Default: include `workspace_id text not null default 'default'` on both tables now, with no multi-tenant logic. Reason: SCOPE_AND_DESIGN section 7 wants it on every table so row-level security is additive later, and adding it afterwards needs a rewrite of the primary keys.
10. **Where the CLI and the orchestration live (layering).** `recromancer.ingest` cannot hold a database-touching `__main__` (C2), and `service` cannot import `ingest` (C4). Default: the CLI is `recromancer.ingest_cli`; the Ingestion_Service takes the Bundle_Reader and Chunker as injected callables over `domain` types; `bootstrap.py` wires them. Confirm in design that import-linter C6 and C8 accept `ingest_cli` as a top-layer module beside `bootstrap`, and add it to the contracts if they do not.
11. **Items-table `subject_handle` and `bundle_id` keys.** Default: items are keyed by `(workspace_id, bundle_id, item_id)`. Reason: one Item_Id may repeat across bundles; later specs scope retrieval by bundle or subject.
12. **Full-text search column.** Default: not created here. Reason: lexical search belongs to the retrieval spec, which can add a generated `tsvector` column and GIN index in a new migration.
13. **First-run model download.** Default: the CLI downloads the model on first real run (criterion 4.12); tests never do. Reason: an offline-only mode needs the installed `fastembed` version checked first (criterion 4.11).
14. **Embedding dimension coupling.** The `vector(384)` column fixes the dimension and there is no dimension setting. Default: the `db` package defines the constant 384 and the Composition_Root compares it with `Embedder.dimensions` at startup, failing on mismatch (R5.12); a different model needs a new migration and a re-embed. Reason: pgvector columns have a fixed dimension.
15. **Vector normalisation.** Default: L2-normalised vectors (R4.13). Reason: cosine distance with the HNSW `vector_cosine_ops` index; normalised vectors also make inner product equivalent if a later spec switches operator class. Verify whether fastembed normalises (R4.11).
16. **Embedding batch size.** Default: 64 texts per call (R4.4), proposed, to be checked against model memory use on a laptop.
17. **Retry policy (R4.12).** Default: one download attempt, no retries, no backoff. Reason: a retry loop needs injected sleep and adds little for a one-time 67 MB download that the operator can rerun.
