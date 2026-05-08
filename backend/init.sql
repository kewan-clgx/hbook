-- HOA Compliance AI — Database Schema
-- Run once on container initialization via docker-entrypoint-initdb.d

-- ─── HOA Registry ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS hoas (
    hoa_id     TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    timezone   TEXT NOT NULL DEFAULT 'UTC',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Users ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── HOA Membership & Roles ──────────────────────────────────────────────────
-- role: manager (full control), board (read + approve), homeowner (read only)

CREATE TABLE IF NOT EXISTS hoa_members (
    user_id   UUID NOT NULL REFERENCES users(user_id)  ON DELETE CASCADE,
    hoa_id    TEXT NOT NULL REFERENCES hoas(hoa_id)    ON DELETE CASCADE,
    role      TEXT NOT NULL CHECK (role IN ('manager', 'board', 'homeowner')),
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, hoa_id)
);

-- ─── Document Registry ───────────────────────────────────────────────────────
-- Source of truth for the raw PDF ↔ doc_id relationship and pipeline status.
-- Chunk content lives in Chroma (vector) and BM25 (keyword) — not here.

CREATE TABLE IF NOT EXISTS documents (
    doc_id            UUID PRIMARY KEY,
    hoa_id            TEXT NOT NULL REFERENCES hoas(hoa_id),
    original_filename TEXT NOT NULL,
    document_type     TEXT NOT NULL
                      CHECK (document_type IN ('state_statute','ccr','articles','bylaws','rules','amendment')),
    effective_date    DATE,
    supersedes_doc_id UUID REFERENCES documents(doc_id) DEFERRABLE INITIALLY DEFERRED,
    page_count        INTEGER,
    status            TEXT NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued','parsed','tagged','indexed','failed')),
    raw_path          TEXT NOT NULL,
    uploaded_by       UUID REFERENCES users(user_id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Indexes ─────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_documents_hoa        ON documents(hoa_id);
CREATE INDEX IF NOT EXISTS idx_documents_status     ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_type       ON documents(hoa_id, document_type);
CREATE INDEX IF NOT EXISTS idx_hoa_members_hoa      ON hoa_members(hoa_id);

-- ─── updated_at auto-maintenance ─────────────────────────────────────────────

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
