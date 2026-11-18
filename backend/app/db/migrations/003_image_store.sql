-- backend/app/db/migrations/003_image_store.sql
-- Phase 1: image_store for extracted figures + table crop link. Additive only.
SET search_path TO manthan_rag_working, public;

CREATE TABLE IF NOT EXISTS manthan_rag_working.image_store (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id    UUID NOT NULL REFERENCES manthan_rag_working.document_registry(id) ON DELETE CASCADE,
    image_index    INT  NOT NULL,
    page_number    INT,
    bbox           JSONB,
    storage_path   TEXT NOT NULL,
    storage_bucket TEXT NOT NULL,
    mime_type      TEXT DEFAULT 'image/png',
    width          INT,
    height         INT,
    caption        TEXT,
    ocr_text       TEXT,
    embedding      vector(1024),
    image_metadata JSONB DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_image_store_embedding
    ON manthan_rag_working.image_store
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 128);

CREATE INDEX IF NOT EXISTS idx_image_store_document_id
    ON manthan_rag_working.image_store (document_id);

ALTER TABLE manthan_rag_working.table_store
    ADD COLUMN IF NOT EXISTS image_storage_path TEXT;
