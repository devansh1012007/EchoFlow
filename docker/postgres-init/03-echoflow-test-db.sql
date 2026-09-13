-- Provision a development database (echoflow_test) alongside the main
-- echoflow_db. Developers who set DATABASE_URL=...echoflow_test in their
-- .env (per AGENTS.md) get a clean separation from production data without
-- needing a second Docker compose override (docker-compose.test.yml).
--
-- The pgvector extension is installed on template1 (init-pgvector.sql)
-- BEFORE this script runs (alphabetical filename order), so CREATE DATABASE
-- already inherits the vector extension on its template.
--
-- Idempotent: the \gexec guard means re-running this script on an
-- already-initialized data directory is a no-op.
SELECT 'CREATE DATABASE echoflow_test OWNER echoflow'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'echoflow_test')\gexec
