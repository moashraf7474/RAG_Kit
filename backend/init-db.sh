#!/bin/bash
# Creates the auxiliary Langfuse database on first Postgres boot.
# Postgres runs everything in /docker-entrypoint-initdb.d once, at init time.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    SELECT 'CREATE DATABASE langfuse'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'langfuse')\gexec
EOSQL
