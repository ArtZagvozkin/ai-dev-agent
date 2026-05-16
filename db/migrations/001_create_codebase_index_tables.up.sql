BEGIN;

CREATE SCHEMA IF NOT EXISTS agent AUTHORIZATION agent;

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

SET search_path TO agent, public;


CREATE TABLE code_projects (
    id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),

    project_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,

    gitlab_project TEXT NOT NULL,
    default_branch TEXT NOT NULL DEFAULT 'master',
    local_repository_path TEXT NOT NULL,

    review_context_path TEXT,
    consultation_context_path TEXT,

    default_top_k INTEGER NOT NULL DEFAULT 10 CHECK (default_top_k >= 1 AND default_top_k <= 20),
    max_files INTEGER NOT NULL DEFAULT 7000 CHECK (max_files > 0),
    max_file_bytes INTEGER NOT NULL DEFAULT 200000 CHECK (max_file_bytes > 0),
    include_full_code_units BOOLEAN NOT NULL DEFAULT TRUE,

    current_index_id UUID
);


CREATE TABLE codebase_indexes (
    id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES code_projects(id) ON DELETE CASCADE,

    status TEXT NOT NULL CHECK (
        status IN ('building', 'ready', 'failed', 'archived')
    ),

    index_schema_version TEXT NOT NULL,

    embedding_provider TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_dimensions INTEGER NOT NULL CHECK (embedding_dimensions > 0),

    qdrant_collection_name TEXT NOT NULL,

    files_count INTEGER NOT NULL DEFAULT 0 CHECK (files_count >= 0),
    chunks_count INTEGER NOT NULL DEFAULT 0 CHECK (chunks_count >= 0),

    build_started_at TIMESTAMPTZ,
    build_finished_at TIMESTAMPTZ,
    error_message TEXT,

    UNIQUE (id, project_id)
);


ALTER TABLE code_projects
    ADD CONSTRAINT fk_code_projects_current_index
    FOREIGN KEY (current_index_id, id)
    REFERENCES codebase_indexes(id, project_id)
    ON DELETE SET NULL (current_index_id);


CREATE TABLE codebase_files (
    id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),
    index_id UUID NOT NULL REFERENCES codebase_indexes(id) ON DELETE CASCADE,

    path TEXT NOT NULL,
    language TEXT NOT NULL,

    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),

    chunks_count INTEGER NOT NULL DEFAULT 0 CHECK (chunks_count >= 0),

    UNIQUE (index_id, path)
);


CREATE TABLE code_chunks (
    id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),

    file_id UUID NOT NULL REFERENCES codebase_files(id) ON DELETE CASCADE,

    -- Логический id chunk из текущего code search pipeline.
    chunk_id TEXT NOT NULL,
    parent_chunk_id TEXT,

    -- file / class / method / object / file_window / ...
    chunk_type TEXT NOT NULL,

    start_line INTEGER NOT NULL CHECK (start_line > 0),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),

    symbol TEXT,
    ast_node_type TEXT,
    declaration_type TEXT,
    parent_symbol TEXT,

    content TEXT NOT NULL,
    contextualized_text TEXT NOT NULL,
    code_unit TEXT,

    keywords JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(keywords) = 'array'),
    imports JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(imports) = 'array'),
    referenced_symbols JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(referenced_symbols) = 'array'),
    top_level_symbols JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(top_level_symbols) = 'array'),

    UNIQUE (file_id, chunk_id)
);


CREATE INDEX idx_codebase_indexes_project_status
    ON codebase_indexes(project_id, status);

CREATE INDEX idx_codebase_files_index_id
    ON codebase_files(index_id);

CREATE INDEX idx_codebase_files_index_path
    ON codebase_files(index_id, path);

CREATE INDEX idx_code_chunks_file_id
    ON code_chunks(file_id);

CREATE INDEX idx_code_chunks_file_position
    ON code_chunks(file_id, start_line, end_line);

CREATE INDEX idx_code_chunks_symbol
    ON code_chunks(symbol)
    WHERE symbol IS NOT NULL;

COMMIT;
