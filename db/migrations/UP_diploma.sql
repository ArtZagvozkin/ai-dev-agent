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

    default_max_files INTEGER NOT NULL DEFAULT 7000 CHECK (default_max_files > 0),
    default_max_file_bytes INTEGER NOT NULL DEFAULT 200000 CHECK (default_max_file_bytes > 0),

    default_top_k INTEGER NOT NULL DEFAULT 10,
    default_include_full_code_units BOOLEAN NOT NULL DEFAULT TRUE,

    current_index_id UUID
);


CREATE TABLE developers (
    id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),

    display_name TEXT NOT NULL,

    gitlab_user_id INTEGER,
    gitlab_username TEXT,
    gitlab_web_url TEXT,

    email TEXT,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (gitlab_user_id),
    UNIQUE (gitlab_username)
);


CREATE TABLE code_review_error_types (
    id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),

    error_type_key TEXT NOT NULL UNIQUE CHECK (error_type_key ~ '^[a-z0-9][a-z0-9_-]*$'),
    display_name TEXT NOT NULL,
    description TEXT,

    default_severity TEXT,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


INSERT INTO code_review_error_types (
    error_type_key,
    display_name,
    description,
    default_severity
)
VALUES
    (
        'unknown',
        'Unknown',
        'Fallback type for comments that were not classified yet.',
        NULL
    )
ON CONFLICT (error_type_key) DO NOTHING;


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

    chunk_id TEXT NOT NULL,
    parent_chunk_id TEXT,

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


CREATE TABLE code_review_runs (
    id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),

    project_id UUID NOT NULL REFERENCES code_projects(id) ON DELETE CASCADE,
    developer_id UUID NOT NULL REFERENCES developers(id) ON DELETE RESTRICT,

    codebase_index_id UUID,

    jira_issue_key TEXT,

    gitlab_project TEXT NOT NULL,
    gitlab_mr_iid INTEGER NOT NULL CHECK (gitlab_mr_iid > 0),
    gitlab_mr_url TEXT,

    source_branch TEXT,
    target_branch TEXT,
    source_commit_sha TEXT,
    target_commit_sha TEXT,

    status TEXT NOT NULL DEFAULT 'running' CHECK (
        status IN ('running', 'completed', 'failed', 'cancelled')
    ),

    llm_model TEXT,
    error_message TEXT,

    request_payload JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(request_payload) = 'object'),
    result_payload JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(result_payload) = 'object'),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),

    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,

    FOREIGN KEY (codebase_index_id, project_id)
    REFERENCES codebase_indexes(id, project_id)
    ON DELETE SET NULL (codebase_index_id)
);


CREATE TABLE code_review_comments (
    id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),

    review_run_id UUID NOT NULL REFERENCES code_review_runs(id) ON DELETE CASCADE,
    error_type_id UUID NOT NULL REFERENCES code_review_error_types(id) ON DELETE RESTRICT,

    comment_order INTEGER NOT NULL CHECK (comment_order > 0),

    scope TEXT NOT NULL,

    file_path TEXT,
    old_path TEXT,

    start_line INTEGER CHECK (start_line IS NULL OR start_line > 0),
    end_line INTEGER CHECK (end_line IS NULL OR end_line > 0),

    comment_text TEXT NOT NULL,

    severity TEXT,
    category TEXT,
    title TEXT,

    publication_mode TEXT NOT NULL DEFAULT 'not_published',

    comment_url TEXT,

    gitlab_discussion_id TEXT,
    gitlab_note_id TEXT,

    publish_error TEXT,

    issue_payload JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(issue_payload) = 'object'),
    publication_payload JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(publication_payload) = 'object'),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (review_run_id, comment_order)
);


CREATE TABLE knowledge_bases (
    id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),

    kb_key TEXT NOT NULL UNIQUE CHECK (kb_key ~ '^[a-z0-9][a-z0-9_-]*$'),
    display_name TEXT NOT NULL,

    source_type TEXT NOT NULL DEFAULT 'local_json' CHECK (
        source_type IN ('local_json', 'confluence', 'kbpublisher', 'manual')
    ),

    source_path TEXT,

    default_top_k INTEGER NOT NULL DEFAULT 6 CHECK (default_top_k > 0),
    default_max_context_chars INTEGER NOT NULL DEFAULT 10000 CHECK (default_max_context_chars > 0),

    current_index_id UUID,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE knowledge_base_indexes (
    id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,

    status TEXT NOT NULL CHECK (
        status IN ('building', 'ready', 'failed', 'archived')
    ),

    index_schema_version TEXT NOT NULL,

    embedding_provider TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_dimensions INTEGER NOT NULL CHECK (embedding_dimensions > 0),

    qdrant_collection_name TEXT NOT NULL,

    documents_count INTEGER NOT NULL DEFAULT 0 CHECK (documents_count >= 0),
    chunks_count INTEGER NOT NULL DEFAULT 0 CHECK (chunks_count >= 0),

    build_started_at TIMESTAMPTZ,
    build_finished_at TIMESTAMPTZ,
    error_message TEXT,

    UNIQUE (id, knowledge_base_id),
    UNIQUE (qdrant_collection_name)
);


ALTER TABLE knowledge_bases
    ADD CONSTRAINT fk_knowledge_bases_current_index
    FOREIGN KEY (current_index_id, id)
    REFERENCES knowledge_base_indexes(id, knowledge_base_id)
    ON DELETE SET NULL (current_index_id);


CREATE TABLE knowledge_base_documents (
    id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),
    index_id UUID NOT NULL REFERENCES knowledge_base_indexes(id) ON DELETE CASCADE,

    external_id TEXT NOT NULL,

    source_url TEXT,
    title TEXT,
    full_title TEXT,

    source_revision TEXT,
    updated_at_src TEXT,

    content_hash TEXT NOT NULL,
    content TEXT,

    chunks_count INTEGER NOT NULL DEFAULT 0 CHECK (chunks_count >= 0),

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),

    UNIQUE (index_id, external_id)
);


CREATE TABLE knowledge_base_chunks (
    id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),

    document_id UUID NOT NULL REFERENCES knowledge_base_documents(id) ON DELETE CASCADE,

    chunk_id TEXT NOT NULL,
    chunk_ord INTEGER NOT NULL CHECK (chunk_ord > 0),

    section_path JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(section_path) = 'array'),
    block_types JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(block_types) = 'array'),

    text TEXT NOT NULL,
    contextualized_text TEXT NOT NULL,

    char_len INTEGER NOT NULL CHECK (char_len >= 0),
    chunk_hash TEXT NOT NULL,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),

    UNIQUE (document_id, chunk_id),
    UNIQUE (document_id, chunk_ord)
);


CREATE TABLE knowledge_base_images (
    id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),

    document_id UUID NOT NULL REFERENCES knowledge_base_documents(id) ON DELETE CASCADE,

    local_path TEXT NOT NULL,
    source_url TEXT,
    alt TEXT,
    caption TEXT,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),

    UNIQUE (document_id, local_path)
);


CREATE TABLE knowledge_base_chunk_images (
    chunk_id UUID NOT NULL REFERENCES knowledge_base_chunks(id) ON DELETE CASCADE,
    image_id UUID NOT NULL REFERENCES knowledge_base_images(id) ON DELETE CASCADE,

    ord INTEGER NOT NULL CHECK (ord > 0),

    PRIMARY KEY (chunk_id, ord),
    UNIQUE (chunk_id, image_id)
);


CREATE INDEX idx_developers_gitlab_user_id
    ON developers(gitlab_user_id)
    WHERE gitlab_user_id IS NOT NULL;

CREATE INDEX idx_developers_gitlab_username
    ON developers(gitlab_username)
    WHERE gitlab_username IS NOT NULL;

CREATE INDEX idx_developers_email
    ON developers(email)
    WHERE email IS NOT NULL;


CREATE INDEX idx_code_review_error_types_key
    ON code_review_error_types(error_type_key);


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


CREATE INDEX idx_code_review_runs_project_id
    ON code_review_runs(project_id);

CREATE INDEX idx_code_review_runs_developer_id
    ON code_review_runs(developer_id);

CREATE INDEX idx_code_review_runs_project_developer_started_at
    ON code_review_runs(project_id, developer_id, started_at DESC);

CREATE INDEX idx_code_review_runs_project_started_at
    ON code_review_runs(project_id, started_at DESC);

CREATE INDEX idx_code_review_runs_gitlab_mr
    ON code_review_runs(gitlab_project, gitlab_mr_iid);

CREATE INDEX idx_code_review_runs_jira_issue_key
    ON code_review_runs(jira_issue_key)
    WHERE jira_issue_key IS NOT NULL;

CREATE INDEX idx_code_review_runs_status
    ON code_review_runs(status);


CREATE INDEX idx_code_review_comments_review_run_id
    ON code_review_comments(review_run_id);

CREATE INDEX idx_code_review_comments_error_type_id
    ON code_review_comments(error_type_id);

CREATE INDEX idx_code_review_comments_review_run_error_type
    ON code_review_comments(review_run_id, error_type_id);

CREATE INDEX idx_code_review_comments_file_path
    ON code_review_comments(file_path)
    WHERE file_path IS NOT NULL;

CREATE INDEX idx_code_review_comments_comment_url
    ON code_review_comments(comment_url)
    WHERE comment_url IS NOT NULL;

CREATE INDEX idx_code_review_comments_gitlab_discussion_id
    ON code_review_comments(gitlab_discussion_id)
    WHERE gitlab_discussion_id IS NOT NULL;


CREATE INDEX idx_knowledge_bases_key
    ON knowledge_bases(kb_key);

CREATE INDEX idx_knowledge_base_indexes_kb_status
    ON knowledge_base_indexes(knowledge_base_id, status);

CREATE INDEX idx_knowledge_base_indexes_status
    ON knowledge_base_indexes(status);

CREATE INDEX idx_knowledge_base_documents_index_id
    ON knowledge_base_documents(index_id);

CREATE INDEX idx_knowledge_base_documents_index_external_id
    ON knowledge_base_documents(index_id, external_id);

CREATE INDEX idx_knowledge_base_documents_source_url
    ON knowledge_base_documents(source_url)
    WHERE source_url IS NOT NULL;

CREATE INDEX idx_knowledge_base_chunks_document_id
    ON knowledge_base_chunks(document_id);

CREATE INDEX idx_knowledge_base_chunks_document_ord
    ON knowledge_base_chunks(document_id, chunk_ord);

CREATE INDEX idx_knowledge_base_chunks_section_path_gin
    ON knowledge_base_chunks USING GIN(section_path);

CREATE INDEX idx_knowledge_base_chunks_block_types_gin
    ON knowledge_base_chunks USING GIN(block_types);

CREATE INDEX idx_knowledge_base_images_document_id
    ON knowledge_base_images(document_id);

CREATE INDEX idx_knowledge_base_chunk_images_image_id
    ON knowledge_base_chunk_images(image_id);


COMMIT;
