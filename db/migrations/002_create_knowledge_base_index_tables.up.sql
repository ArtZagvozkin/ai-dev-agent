BEGIN;

CREATE SCHEMA IF NOT EXISTS agent AUTHORIZATION agent;

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

SET search_path TO agent, public;


CREATE TABLE knowledge_bases (
    id UUID PRIMARY KEY DEFAULT public.gen_random_uuid(),

    kb_key TEXT NOT NULL UNIQUE CHECK (kb_key ~ '^[a-z0-9][a-z0-9_-]*$'),
    display_name TEXT NOT NULL,

    -- MVP: local_json.
    -- В дальнейшем можно добавить confluence/kbpublisher/api/import.
    source_type TEXT NOT NULL DEFAULT 'local_json' CHECK (
        source_type IN ('local_json', 'confluence', 'kbpublisher', 'manual')
    ),

    -- Для MVP: путь к clean_data/entries или аналогичной директории.
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

    -- Например article_id из KBPublisher / Confluence page id / имя файла.
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

    -- Логический id chunk из pipeline базы знаний.
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
