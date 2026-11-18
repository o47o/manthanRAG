-- Manthan RAG — install schema (generated 2026-11-18 from the live engine tables).
-- Safe to re-run: every statement is IF NOT EXISTS / OR REPLACE and touches only schema manthan_rag.
-- Requires extensions vector, pg_trgm (and gen_random_uuid, core since PG13).

CREATE SCHEMA IF NOT EXISTS manthan_rag;
SET search_path = manthan_rag, public, extensions;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE OR REPLACE FUNCTION manthan_rag.immutable_tsv(text)
 RETURNS tsvector
 LANGUAGE plpgsql
 IMMUTABLE PARALLEL SAFE
AS $function$
BEGIN RETURN to_tsvector('english'::regconfig, $1); END;
$function$;

CREATE OR REPLACE FUNCTION manthan_rag.set_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$function$;

CREATE OR REPLACE FUNCTION manthan_rag.set_updated_date()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN NEW.updated_date = now(); RETURN NEW; END;
$function$;

-- ── Product tables ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS manthan_rag.collections (
    id serial PRIMARY KEY,
    name text NOT NULL UNIQUE,
    description text,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone
);

CREATE TABLE IF NOT EXISTS manthan_rag.api_keys (
    id serial PRIMARY KEY,
    name text NOT NULL,
    key_prefix varchar(16) NOT NULL,            -- first chars, shown in the admin UI
    key_hash varchar(64) NOT NULL UNIQUE,       -- sha256 hex of the full key; the key itself is never stored
    is_admin boolean NOT NULL DEFAULT false,
    collection_ids integer[] NOT NULL DEFAULT '{}',   -- empty + not admin = no access
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    last_used_at timestamp with time zone,
    expires_at timestamp with time zone,
    revoked_at timestamp with time zone
);

-- ── pipeline_runs ──
CREATE TABLE IF NOT EXISTS manthan_rag.pipeline_runs (

    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(255),
    source character varying(50) DEFAULT 'local'::character varying,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    status character varying(50) DEFAULT 'running'::character varying,
    total_files_found integer DEFAULT 0 NOT NULL,
    processed_files integer DEFAULT 0 NOT NULL,
    skipped_files integer DEFAULT 0 NOT NULL,
    failed_files integer DEFAULT 0 NOT NULL,
    invalid_files integer DEFAULT 0 NOT NULL,
    run_summary jsonb,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by integer,
    updated_at timestamp with time zone,
    description text,
    domain text,
    sub_domain text,
    category text,
    sub_category text,
    CONSTRAINT rag_ingestion_runs_pkey PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_rag_runs_created_by ON manthan_rag.pipeline_runs USING btree (created_by);

-- ── document_registry ──
CREATE TABLE IF NOT EXISTS manthan_rag.document_registry (

    id uuid DEFAULT gen_random_uuid() NOT NULL,
    original_filename character varying(500),
    storage_path text NOT NULL,
    relative_path text,
    file_hash character varying(64),
    file_type character varying(50),
    mime_type character varying(255),
    file_size_bytes bigint NOT NULL,
    page_count integer,
    domain_id integer,
    sub_domain_id integer,
    category_id integer,
    sub_category_id integer,
    subcategory_path text,
    version_no integer DEFAULT 1,
    status character varying(50) DEFAULT 'processing'::character varying,
    extraction_method character varying(100),
    embedding_model character varying(255),
    embedding_dimension integer,
    file_metadata jsonb,
    is_active boolean DEFAULT true,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by integer,
    updated_at timestamp with time zone,
    description text,
    document_type text,
    document_subtype text,
    router_confidence double precision,
    router_reasoning text,
    uploaded_at timestamp with time zone,
    parsed_at timestamp with time zone,
    chunked_at timestamp with time zone,
    embedded_at timestamp with time zone,
    stored_at timestamp with time zone,
    completed_at timestamp with time zone,
    error_stage text,
    error_message text,
    retry_count integer DEFAULT 0,
    word_count integer,
    has_tables boolean DEFAULT false,
    has_images boolean DEFAULT false,
    language_detected text,
    doc_title text,
    doc_author text,
    doc_date date,
    doc_summary text,
    doc_metadata jsonb,
    pipeline_run_id uuid,
    filename text,
    storage_bucket text DEFAULT 'manthan-rag-docs'::text,
    geography_id integer,
    supersedes uuid,
    superseded_by uuid,
    CONSTRAINT rag_files_pkey PRIMARY KEY (id),
    collection_id integer REFERENCES manthan_rag.collections(id)
);
CREATE INDEX IF NOT EXISTS idx_rag_files_status ON manthan_rag.document_registry USING btree (status);
CREATE INDEX IF NOT EXISTS idx_rag_files_sub_domain_id ON manthan_rag.document_registry USING btree (sub_domain_id);
CREATE INDEX IF NOT EXISTS idx_rag_files_relative_path ON manthan_rag.document_registry USING btree (relative_path);
CREATE INDEX IF NOT EXISTS idx_rag_files_file_hash ON manthan_rag.document_registry USING btree (file_hash);
CREATE INDEX IF NOT EXISTS idx_rag_files_is_active ON manthan_rag.document_registry USING btree (is_active);
CREATE INDEX IF NOT EXISTS idx_rag_files_domain_id ON manthan_rag.document_registry USING btree (domain_id);
CREATE INDEX IF NOT EXISTS idx_rag_files_category_id ON manthan_rag.document_registry USING btree (category_id);
CREATE INDEX IF NOT EXISTS ix_rag_files_document_type ON manthan_rag.document_registry USING btree (document_type);
CREATE INDEX IF NOT EXISTS ix_rag_files_status ON manthan_rag.document_registry USING btree (status);
CREATE INDEX IF NOT EXISTS ix_docreg_identity ON manthan_rag.document_registry USING btree (original_filename, domain_id) WHERE is_active;
CREATE INDEX IF NOT EXISTS ix_docreg_file_hash ON manthan_rag.document_registry USING btree (file_hash);
CREATE INDEX IF NOT EXISTS ix_docreg_collection ON manthan_rag.document_registry USING btree (collection_id) WHERE is_active;

-- ── vector_store ──
CREATE TABLE IF NOT EXISTS manthan_rag.vector_store (

    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_id uuid NOT NULL,
    chunk_index integer,
    page_number integer,
    chunk_text text NOT NULL,
    token_count integer,
    content_type character varying(100),
    chunk_metadata jsonb,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by integer,
    updated_date timestamp with time zone,
    embedding vector(1024),
    chunk_word_count integer,
    chunk_char_count integer,
    page_numbers integer[],
    bbox jsonb,
    section_title text,
    section_level integer,
    semantic_type text,
    keywords text[],
    from_image_store boolean DEFAULT false,
    source_image_id uuid,
    domain_id integer,
    chunk_text_tsv tsvector GENERATED ALWAYS AS (manthan_rag.immutable_tsv(COALESCE(chunk_text, ''::text))) STORED,
    CONSTRAINT rag_document_chunks_pkey PRIMARY KEY (id),
    CONSTRAINT rag_document_chunks_file_id_fkey FOREIGN KEY (document_id) REFERENCES manthan_rag.document_registry(id)
);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_file_id ON manthan_rag.vector_store USING btree (document_id);
CREATE INDEX IF NOT EXISTS ix_rag_doc_chunks_tsv ON manthan_rag.vector_store USING gin (chunk_text_tsv);
CREATE INDEX IF NOT EXISTS ix_rag_doc_chunks_keywords ON manthan_rag.vector_store USING gin (keywords);
CREATE INDEX IF NOT EXISTS ix_rag_doc_chunks_domain ON manthan_rag.vector_store USING btree (domain_id);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding ON manthan_rag.vector_store USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64');
CREATE INDEX IF NOT EXISTS ix_rag_doc_chunks_hnsw ON manthan_rag.vector_store USING hnsw (embedding vector_cosine_ops);

-- ── ingestion_jobs ──
CREATE TABLE IF NOT EXISTS manthan_rag.ingestion_jobs (

    id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_id uuid,
    document_id uuid,
    storage_uri text,
    job_type character varying(50) DEFAULT 'new_file'::character varying,
    status character varying(50) DEFAULT 'queued'::character varying,
    error_message text,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_by integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by integer,
    updated_at timestamp with time zone,
    celery_task_id text,
    current_stage text,
    stage_progress integer,
    total_chunks integer,
    processed_chunks integer,
    queued_at timestamp with time zone,
    duration_seconds double precision,
    stage_timings jsonb,
    stage_detail jsonb,
    error_traceback text,
    is_retryable boolean,
    worker_id text,
    code_version text,
    CONSTRAINT rag_ingestion_jobs_pkey PRIMARY KEY (id),
    CONSTRAINT rag_ingestion_jobs_file_id_fkey FOREIGN KEY (document_id) REFERENCES manthan_rag.document_registry(id),
    CONSTRAINT rag_ingestion_jobs_run_id_fkey FOREIGN KEY (run_id) REFERENCES manthan_rag.pipeline_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_rag_jobs_run_id ON manthan_rag.ingestion_jobs USING btree (run_id);
CREATE INDEX IF NOT EXISTS idx_rag_jobs_status ON manthan_rag.ingestion_jobs USING btree (status);
CREATE INDEX IF NOT EXISTS idx_rag_jobs_file_id ON manthan_rag.ingestion_jobs USING btree (document_id);

-- ── table_store ──
CREATE TABLE IF NOT EXISTS manthan_rag.table_store (

    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_id uuid NOT NULL,
    table_index integer,
    table_title text,
    page_number integer,
    bbox jsonb,
    raw_text text,
    markdown_text text,
    csv_data text,
    json_data jsonb,
    row_count integer,
    col_count integer,
    has_numeric_data boolean,
    has_currency boolean,
    has_percentages boolean,
    detected_units text[],
    context_before text,
    context_after text,
    table_summary text,
    embedding vector(1024),
    fiscal_year text,
    reporting_period text,
    currency text,
    table_category text,
    table_metadata jsonb,
    image_storage_path text,
    from_image_store boolean DEFAULT false,
    table_text_tsv tsvector GENERATED ALWAYS AS (manthan_rag.immutable_tsv(((((COALESCE(table_summary, ''::text) || ' '::text) || COALESCE(raw_text, ''::text)) || ' '::text) || COALESCE(markdown_text, ''::text)))) STORED,
    source_image_id uuid,
    extraction_method text,
    extraction_quality text,
    source_confidence real,
    provenance jsonb,
    structured_content text,
    structured_content_embedding vector(1024),
    chunk_count integer DEFAULT 0,
    domain_id integer,
    is_active boolean DEFAULT true,
    created_by integer,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT rag_table_store_pkey PRIMARY KEY (id),
    CONSTRAINT rag_table_store_document_id_fkey FOREIGN KEY (document_id) REFERENCES manthan_rag.document_registry(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_rag_table_store_doc ON manthan_rag.table_store USING btree (document_id);
CREATE INDEX IF NOT EXISTS ix_rag_table_store_cat ON manthan_rag.table_store USING btree (table_category);
CREATE INDEX IF NOT EXISTS ix_rag_table_store_json ON manthan_rag.table_store USING gin (json_data);
CREATE INDEX IF NOT EXISTS ix_rag_table_store_tsv ON manthan_rag.table_store USING gin (table_text_tsv);
CREATE INDEX IF NOT EXISTS ix_rag_table_store_hnsw ON manthan_rag.table_store USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ix_rag_table_store_sc_hnsw ON manthan_rag.table_store USING hnsw (structured_content_embedding vector_cosine_ops);
CREATE OR REPLACE TRIGGER trg_rag_table_store_updated BEFORE UPDATE ON manthan_rag.table_store FOR EACH ROW EXECUTE FUNCTION manthan_rag.set_updated_at();

-- ── table_chunk_store ──
CREATE TABLE IF NOT EXISTS manthan_rag.table_chunk_store (

    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_id uuid NOT NULL,
    table_id uuid NOT NULL,
    table_index integer,
    chunk_index integer,
    row_start integer,
    row_end integer,
    serialized_text text,
    page_number integer,
    embedding vector(1024),
    chunk_metadata jsonb,
    structured_content text,
    structured_content_embedding vector(1024),
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT rag_table_chunk_store_pkey PRIMARY KEY (id),
    CONSTRAINT rag_table_chunk_store_document_id_fkey FOREIGN KEY (document_id) REFERENCES manthan_rag.document_registry(id) ON DELETE CASCADE,
    CONSTRAINT rag_table_chunk_store_table_id_fkey FOREIGN KEY (table_id) REFERENCES manthan_rag.table_store(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_rag_tcs_doc ON manthan_rag.table_chunk_store USING btree (document_id);
CREATE INDEX IF NOT EXISTS ix_rag_tcs_table ON manthan_rag.table_chunk_store USING btree (table_id);
CREATE INDEX IF NOT EXISTS ix_rag_tcs_hnsw ON manthan_rag.table_chunk_store USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ix_rag_tcs_sc_hnsw ON manthan_rag.table_chunk_store USING hnsw (structured_content_embedding vector_cosine_ops);

-- ── table_row_store ──
CREATE TABLE IF NOT EXISTS manthan_rag.table_row_store (

    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_id uuid NOT NULL,
    table_id uuid NOT NULL,
    row_index integer NOT NULL,
    row_data jsonb,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT rag_table_row_store_table_id_row_index_key UNIQUE (table_id, row_index),
    CONSTRAINT rag_table_row_store_pkey PRIMARY KEY (id),
    CONSTRAINT rag_table_row_store_document_id_fkey FOREIGN KEY (document_id) REFERENCES manthan_rag.document_registry(id) ON DELETE CASCADE,
    CONSTRAINT rag_table_row_store_table_id_fkey FOREIGN KEY (table_id) REFERENCES manthan_rag.table_store(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_rag_trs_table ON manthan_rag.table_row_store USING btree (table_id);
CREATE INDEX IF NOT EXISTS ix_rag_trs_doc ON manthan_rag.table_row_store USING btree (document_id);

-- ── table_cell_store ──
CREATE TABLE IF NOT EXISTS manthan_rag.table_cell_store (

    id bigint GENERATED BY DEFAULT AS IDENTITY NOT NULL,
    document_id uuid NOT NULL,
    table_id uuid NOT NULL,
    row_index integer NOT NULL,
    column_index integer NOT NULL,
    column_name text NOT NULL,
    value_text text,
    value_numeric numeric,
    value_raw text,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT rag_table_cell_store_table_id_row_index_column_name_key UNIQUE (table_id, row_index, column_name),
    CONSTRAINT rag_table_cell_store_pkey PRIMARY KEY (id),
    CONSTRAINT rag_table_cell_store_document_id_fkey FOREIGN KEY (document_id) REFERENCES manthan_rag.document_registry(id) ON DELETE CASCADE,
    CONSTRAINT rag_table_cell_store_table_id_fkey FOREIGN KEY (table_id) REFERENCES manthan_rag.table_store(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_rag_cell_num ON manthan_rag.table_cell_store USING btree (table_id, column_name, value_numeric);
CREATE INDEX IF NOT EXISTS ix_rag_cell_txt ON manthan_rag.table_cell_store USING btree (table_id, column_name, value_text);
CREATE INDEX IF NOT EXISTS ix_rag_cell_trgm ON manthan_rag.table_cell_store USING gin (value_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_rag_cell_tbl ON manthan_rag.table_cell_store USING btree (table_id);
CREATE INDEX IF NOT EXISTS ix_rag_cell_doc ON manthan_rag.table_cell_store USING btree (document_id);

-- ── clause_store ──
CREATE TABLE IF NOT EXISTS manthan_rag.clause_store (

    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_id uuid NOT NULL,
    clause_index integer,
    clause_number text,
    clause_title text,
    clause_text text NOT NULL,
    clause_word_count integer,
    clause_type text,
    clause_subtype text,
    risk_level text,
    risk_rationale text,
    obligor text,
    obligee text,
    parties_mentioned text[],
    key_dates jsonb,
    monetary_values jsonb,
    conditions text[],
    page_number integer,
    page_numbers integer[],
    section_path text[],
    embedding vector(1024),
    references_clauses text[],
    referenced_by text[],
    clause_metadata jsonb,
    from_image_store boolean DEFAULT false,
    clause_text_tsv tsvector GENERATED ALWAYS AS (manthan_rag.immutable_tsv(COALESCE(clause_text, ''::text))) STORED,
    source_image_id uuid,
    domain_id integer,
    is_active boolean DEFAULT true,
    created_by integer,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT rag_clause_store_pkey PRIMARY KEY (id),
    CONSTRAINT rag_clause_store_document_id_fkey FOREIGN KEY (document_id) REFERENCES manthan_rag.document_registry(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_rag_clause_doc ON manthan_rag.clause_store USING btree (document_id);
CREATE INDEX IF NOT EXISTS ix_rag_clause_type ON manthan_rag.clause_store USING btree (clause_type);
CREATE INDEX IF NOT EXISTS ix_rag_clause_risk ON manthan_rag.clause_store USING btree (risk_level);
CREATE INDEX IF NOT EXISTS ix_rag_clause_parties ON manthan_rag.clause_store USING gin (parties_mentioned);
CREATE INDEX IF NOT EXISTS ix_rag_clause_tsv ON manthan_rag.clause_store USING gin (clause_text_tsv);
CREATE INDEX IF NOT EXISTS ix_rag_clause_hnsw ON manthan_rag.clause_store USING hnsw (embedding vector_cosine_ops);
CREATE OR REPLACE TRIGGER trg_rag_clause_store_updated BEFORE UPDATE ON manthan_rag.clause_store FOR EACH ROW EXECUTE FUNCTION manthan_rag.set_updated_at();

-- ── document_store ──
CREATE TABLE IF NOT EXISTS manthan_rag.document_store (

    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_id uuid NOT NULL,
    chunk_index integer,
    chunk_text text,
    chunk_type text,
    citation_key text,
    source_title text,
    source_authors text[],
    source_year integer,
    source_doi text,
    source_url text,
    source_journal text,
    source_confidence double precision,
    page_number integer,
    section_title text,
    section_type text,
    contains_hypothesis boolean,
    contains_finding boolean,
    contains_method boolean,
    entities_mentioned text[],
    embedding vector(1024),
    chunk_metadata jsonb,
    from_image_store boolean DEFAULT false,
    chunk_text_tsv tsvector GENERATED ALWAYS AS (manthan_rag.immutable_tsv(COALESCE(chunk_text, ''::text))) STORED,
    source_image_id uuid,
    domain_id integer,
    is_active boolean DEFAULT true,
    created_by integer,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT rag_document_store_pkey PRIMARY KEY (id),
    CONSTRAINT rag_document_store_document_id_fkey FOREIGN KEY (document_id) REFERENCES manthan_rag.document_registry(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_rag_docstore_doc ON manthan_rag.document_store USING btree (document_id);
CREATE INDEX IF NOT EXISTS ix_rag_docstore_type ON manthan_rag.document_store USING btree (chunk_type);
CREATE INDEX IF NOT EXISTS ix_rag_docstore_doi ON manthan_rag.document_store USING btree (source_doi);
CREATE INDEX IF NOT EXISTS ix_rag_docstore_tsv ON manthan_rag.document_store USING gin (chunk_text_tsv);
CREATE INDEX IF NOT EXISTS ix_rag_docstore_hnsw ON manthan_rag.document_store USING hnsw (embedding vector_cosine_ops);
CREATE OR REPLACE TRIGGER trg_rag_document_store_updated BEFORE UPDATE ON manthan_rag.document_store FOR EACH ROW EXECUTE FUNCTION manthan_rag.set_updated_at();

-- ── image_store ──
CREATE TABLE IF NOT EXISTS manthan_rag.image_store (

    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_id uuid NOT NULL,
    image_index integer,
    page_number integer,
    bbox jsonb,
    storage_path text,
    storage_bucket text,
    mime_type text,
    width integer,
    height integer,
    ocr_text text,
    image_metadata jsonb,
    content_type text,
    stored_in text,
    structured_content text,
    detected_store text,
    vlm_ocr_text text,
    processing_status text,
    skip_reason text,
    filter_stage text,
    image_type text,
    asset_role text,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT rag_image_store_pkey PRIMARY KEY (id),
    CONSTRAINT rag_image_store_document_id_fkey FOREIGN KEY (document_id) REFERENCES manthan_rag.document_registry(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_rag_image_doc ON manthan_rag.image_store USING btree (document_id);

-- ── parse_staging ──
CREATE TABLE IF NOT EXISTS manthan_rag.parse_staging (

    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_id uuid NOT NULL,
    storage_bucket text,
    blob_path text,
    status text DEFAULT 'staged'::text,
    page_count integer,
    block_count integer,
    table_count integer,
    image_count integer,
    bytes_size bigint,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT rag_parse_staging_document_id_key UNIQUE (document_id),
    CONSTRAINT rag_parse_staging_pkey PRIMARY KEY (id),
    CONSTRAINT rag_parse_staging_document_id_fkey FOREIGN KEY (document_id) REFERENCES manthan_rag.document_registry(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_rag_parse_staging_doc ON manthan_rag.parse_staging USING btree (document_id);
CREATE OR REPLACE TRIGGER trg_rag_parse_staging_updated BEFORE UPDATE ON manthan_rag.parse_staging FOR EACH ROW EXECUTE FUNCTION manthan_rag.set_updated_at();

-- ── document_records ──
CREATE TABLE IF NOT EXISTS manthan_rag.document_records (

    id bigint GENERATED BY DEFAULT AS IDENTITY NOT NULL,
    document_id uuid NOT NULL,
    chunk_id uuid,
    page_number integer,
    record_type text NOT NULL,
    record_type_key text NOT NULL,
    record_id text,
    fields jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_text text,
    confidence real,
    extracted_by text,
    extracted_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT document_records_pkey PRIMARY KEY (id),
    CONSTRAINT document_records_document_id_fkey FOREIGN KEY (document_id) REFERENCES manthan_rag.document_registry(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_document_records_document ON manthan_rag.document_records USING btree (document_id);
CREATE INDEX IF NOT EXISTS ix_document_records_type ON manthan_rag.document_records USING btree (record_type_key);
CREATE INDEX IF NOT EXISTS ix_document_records_record_id ON manthan_rag.document_records USING btree (record_id);
CREATE INDEX IF NOT EXISTS ix_document_records_fields ON manthan_rag.document_records USING gin (fields);

-- ── rag_ingestion_errors ──
CREATE TABLE IF NOT EXISTS manthan_rag.rag_ingestion_errors (

    error_id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_id uuid NOT NULL,
    job_id uuid,
    file_path text NOT NULL,
    error_type character varying(100),
    error_message text NOT NULL,
    created_by integer NOT NULL,
    created_date timestamp with time zone DEFAULT now() NOT NULL,
    updated_by integer,
    updated_date timestamp with time zone,
    CONSTRAINT rag_ingestion_errors_pkey PRIMARY KEY (error_id),
    CONSTRAINT rag_ingestion_errors_job_id_fkey FOREIGN KEY (job_id) REFERENCES manthan_rag.ingestion_jobs(id),
    CONSTRAINT rag_ingestion_errors_run_id_fkey FOREIGN KEY (run_id) REFERENCES manthan_rag.pipeline_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_rag_errors_run_id ON manthan_rag.rag_ingestion_errors USING btree (run_id);
CREATE INDEX IF NOT EXISTS idx_rag_errors_job_id ON manthan_rag.rag_ingestion_errors USING btree (job_id);

-- ── Views read by the API (engine's original definitions, plus collection/version columns) ──
CREATE OR REPLACE VIEW manthan_rag.document_overview AS
SELECT
    d.id,
    d.original_filename,
    d.document_type,
    d.document_subtype,
    d.status,
    d.page_count,
    d.word_count,
    d.router_confidence,
    d.doc_title,
    d.doc_summary,
    COALESCE(vs.n, 0)  AS vector_chunks,
    COALESCE(ts.n, 0)  AS table_count,
    COALESCE(cs.n, 0)  AS clause_count,
    COALESCE(ds.n, 0)  AS research_chunks,
    d.completed_at,
    d.created_at,
    d.error_message,
    d.collection_id,
    d.is_active,
    d.version_no
FROM manthan_rag.document_registry d
LEFT JOIN (
    SELECT document_id, COUNT(*) AS n
    FROM manthan_rag.vector_store
    GROUP BY document_id
) vs ON vs.document_id = d.id
LEFT JOIN (
    SELECT document_id, COUNT(*) AS n
    FROM manthan_rag.table_store
    GROUP BY document_id
) ts ON ts.document_id = d.id
LEFT JOIN (
    SELECT document_id, COUNT(*) AS n
    FROM manthan_rag.clause_store
    GROUP BY document_id
) cs ON cs.document_id = d.id
LEFT JOIN (
    SELECT document_id, COUNT(*) AS n
    FROM manthan_rag.document_store
    GROUP BY document_id
) ds ON ds.document_id = d.id;
CREATE OR REPLACE VIEW manthan_rag.pipeline_run_overview AS
SELECT
    p.id,
    p.name,
    p.description,
    p.source,
    p.domain,
    p.sub_domain,
    p.category,
    p.sub_category,
    p.started_at,
    p.created_at,
    COUNT(d.id)                                                        AS files_found,
    COUNT(*) FILTER (WHERE d.status = 'completed')                     AS files_processed,
    COUNT(*) FILTER (WHERE d.status = 'failed')                        AS files_failed,
    CASE
        WHEN COUNT(d.id) = 0 THEN 'empty'
        WHEN COUNT(*) FILTER (WHERE d.status NOT IN ('completed', 'failed')) > 0 THEN 'running'
        WHEN COUNT(*) FILTER (WHERE d.status = 'failed') = COUNT(d.id) THEN 'failed'
        ELSE 'completed'
    END                                                               AS status
FROM manthan_rag.pipeline_runs p
LEFT JOIN manthan_rag.document_registry d ON d.pipeline_run_id = p.id
GROUP BY p.id;
