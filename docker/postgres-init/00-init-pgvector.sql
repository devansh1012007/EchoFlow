-- Install pgvector in the default database (POSTGRES_DB / echoflow_db).
-- This is the first init script alphabetically so Django migrations find
-- the extension when they reach `pgvector.django.vector.VectorField`.
--
-- The 01-init-pgvector-template1.sql script (which runs after this one)
-- then installs vector on template1 so any future CREATE DATABASE
-- (e.g. the echoflow_test provisioned by 02-echoflow-test-db.sql)
-- inherits the extension.
CREATE EXTENSION IF NOT EXISTS vector;
