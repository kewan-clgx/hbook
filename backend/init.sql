-- HOA Compliance AI — Database initialization
-- Creates the schema for document and chunk metadata

CREATE TABLE IF NOT EXISTS documents (
    doc_id          UUID PRIMARY KEY,
    hoa_id          UUID NOT NULL,
    hoa_name        TEXT NOT NULL DEFAULT '',
    document_type   TEXT NOT NULL,
    effective_date  DATE,
    supersedes_doc_id UUID,
    page_count      INTEGER,
    status          TEXT NOT NULL DEFAULT 'queued',
    raw_path        TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id          TEXT PRIMARY KEY,
    doc_id            UUID NOT NULL REFERENCES documents(doc_id),
    hoa_id            UUID NOT NULL,
    document_type     TEXT NOT NULL,
    authority_rank    SMALLINT NOT NULL CHECK (authority_rank BETWEEN 1 AND 5),
    section           TEXT NOT NULL,
    parent_section    TEXT NOT NULL,
    ancestor_chain    TEXT[] NOT NULL,
    chunk_type        TEXT NOT NULL,
    defined_terms     TEXT[],
    referenced_terms  TEXT[],
    effective_date    DATE,
    supersedes_doc_id UUID,
    last_updated      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    page              INTEGER NOT NULL,
    bounding_box      REAL[4] NOT NULL,
    token_count       INTEGER NOT NULL,
    text              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_hoa_rank   ON chunks(hoa_id, authority_rank);
CREATE INDEX IF NOT EXISTS idx_chunks_doc        ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_section    ON chunks(hoa_id, section);
CREATE INDEX IF NOT EXISTS idx_chunks_terms_gin  ON chunks USING GIN(referenced_terms);
CREATE INDEX IF NOT EXISTS idx_documents_hoa     ON documents(hoa_id);
