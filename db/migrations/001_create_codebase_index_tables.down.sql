BEGIN;

SET search_path TO agent, public;

ALTER TABLE IF EXISTS code_projects
    DROP CONSTRAINT IF EXISTS fk_code_projects_current_index;

DROP TABLE IF EXISTS code_chunks;
DROP TABLE IF EXISTS codebase_files;
DROP TABLE IF EXISTS codebase_indexes;
DROP TABLE IF EXISTS code_projects;

DROP SCHEMA IF EXISTS agent;

COMMIT;
