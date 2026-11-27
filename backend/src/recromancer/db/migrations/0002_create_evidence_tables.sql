-- Verified pgvector availability (HNSW needs 0.5.0 or later). Command:
--   docker exec recromancer-db-1 psql -U recromancer -d recromancer -At -c "SELECT name, default_version FROM pg_available_extensions WHERE name = 'vector';"
-- Output:
--   vector|0.8.7
-- No CREATE INDEX CONCURRENTLY: this file runs in the runner's single transaction.

CREATE TABLE evidence_items (
    workspace_id     text        NOT NULL DEFAULT 'default',
    bundle_id        text        NOT NULL,
    item_id          text        NOT NULL,
    kind             text        NOT NULL,
    author           text        NOT NULL,
    subject_handle   text        NOT NULL,
    occurred_at      timestamptz NOT NULL,
    title            text        NOT NULL,
    body             text        NOT NULL,
    url              text,
    path             text,
    references_ids   text[]      NOT NULL DEFAULT '{}',
    content_hash     text        NOT NULL,
    ingest_signature text        NOT NULL,
    ingested_at      timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, bundle_id, item_id),
    CONSTRAINT evidence_items_kind_check
        CHECK (kind IN ('commit', 'pull_request', 'review', 'issue', 'doc')),
    CONSTRAINT evidence_items_locator_check
        CHECK ((url IS NULL) <> (path IS NULL))
);

CREATE TABLE evidence_chunks (
    workspace_id    text        NOT NULL DEFAULT 'default',
    bundle_id       text        NOT NULL,
    item_id         text        NOT NULL,
    chunk_id        text        NOT NULL,
    position        integer     NOT NULL,
    start_offset    integer     NOT NULL,
    end_offset      integer     NOT NULL,
    text            text        NOT NULL,
    embedding       vector(384) NOT NULL,
    embedding_model text        NOT NULL,
    PRIMARY KEY (workspace_id, bundle_id, chunk_id),
    FOREIGN KEY (workspace_id, bundle_id, item_id)
        REFERENCES evidence_items (workspace_id, bundle_id, item_id) ON DELETE CASCADE,
    CONSTRAINT evidence_chunks_span_check
        CHECK (position >= 0 AND start_offset >= 0 AND end_offset > start_offset)
);

CREATE INDEX evidence_chunks_item_idx ON evidence_chunks (workspace_id, bundle_id, item_id);
CREATE INDEX evidence_chunks_embedding_hnsw ON evidence_chunks USING hnsw (embedding vector_cosine_ops);
