-- Install pgvector on template1 so every future CREATE DATABASE inherits it.
-- Must run AFTER 00-init-pgvector.sql (which installs in the default
-- POSTGRES_DB) but BEFORE 02-echoflow-test-db.sql (which creates the
-- echoflow_test database — that CREATE would otherwise copy template0,
-- which has no extensions).
\c template1
CREATE EXTENSION IF NOT EXISTS vector;
