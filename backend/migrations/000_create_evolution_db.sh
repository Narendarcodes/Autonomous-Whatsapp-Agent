#!/bin/bash
# Creates the Evolution API database on first Postgres boot.
# Runs once from /docker-entrypoint-initdb.d — idempotent.
set -e
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     <<-EOSQL
SELECT 'CREATE DATABASE evolution_api'
WHERE NOT EXISTS (
    SELECT FROM pg_database WHERE datname = 'evolution_api'
)\gexec
EOSQL
