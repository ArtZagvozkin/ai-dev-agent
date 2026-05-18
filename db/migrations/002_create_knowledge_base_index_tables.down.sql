BEGIN;

SET search_path TO agent, public;

ALTER TABLE IF EXISTS knowledge_bases
    DROP CONSTRAINT IF EXISTS fk_knowledge_bases_current_index;

DROP TABLE IF EXISTS knowledge_base_chunk_images;
DROP TABLE IF EXISTS knowledge_base_images;
DROP TABLE IF EXISTS knowledge_base_chunks;
DROP TABLE IF EXISTS knowledge_base_documents;
DROP TABLE IF EXISTS knowledge_base_indexes;
DROP TABLE IF EXISTS knowledge_bases;

COMMIT;
