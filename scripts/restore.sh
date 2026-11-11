#!/usr/bin/env bash
#
# Restore a backup produced by scripts/backup.sh.
#
#   ./scripts/restore.sh backups/20250101-120000
#
# DESTRUCTIVE: replaces the current database, vector collection and bucket
# contents. Prompts before doing anything (skip with FORCE=1).
set -euo pipefail

cd "$(dirname "$0")/.."

SRC=${1:-}
if [[ -z $SRC ]]; then
    echo "usage: $0 <backup-dir>" >&2
    exit 1
fi
if [[ ! -d $SRC ]]; then
    echo "error: $SRC is not a directory" >&2
    exit 1
fi

for f in postgres.dump qdrant.snapshot; do
    if [[ ! -f "$SRC/$f" ]]; then
        echo "error: $SRC/$f is missing — not a complete backup" >&2
        exit 1
    fi
done
if [[ ! -d "$SRC/minio-objects" ]]; then
    echo "error: $SRC/minio-objects is missing — not a complete backup" >&2
    exit 1
fi

COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.prod.yml}
ENV_FILE=${ENV_FILE:-.env.prod}
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

if [[ ! -f $ENV_FILE ]]; then
    echo "error: $ENV_FILE not found." >&2
    exit 1
fi

# shellcheck disable=SC1090
set -a && source "$ENV_FILE" && set +a

DB_NAME=${DATABASE_NAME:-db}
DB_USER=${DATABASE_USER:-postgres}
BUCKET=${S3_BUCKET:-rag-documents}
COLLECTION=${VECTOR_DB_COLLECTION_NAME:-rag}

echo "About to restore from: $SRC"
echo
echo "  This DESTROYS and replaces:"
echo "    - Postgres database '$DB_NAME'"
echo "    - Qdrant collection '$COLLECTION'"
echo "    - contents of MinIO bucket '$BUCKET'"
echo

if [[ ${FORCE:-0} != 1 ]]; then
    read -r -p "Type 'restore' to continue: " reply
    if [[ $reply != restore ]]; then
        echo "Aborted."
        exit 1
    fi
fi

# Stop the app tier first so nothing writes while we swap the data underneath.
echo "==> Stopping api, worker and beat"
"${COMPOSE[@]}" stop api worker beat >/dev/null

# --- Postgres ---------------------------------------------------------------
# --clean --if-exists drops each object before recreating it, so this works
# against a populated database.
echo "==> [1/3] Postgres: restoring '$DB_NAME'"
"${COMPOSE[@]}" exec -T db \
    pg_restore -U "$DB_USER" -d "$DB_NAME" --clean --if-exists --no-owner \
    < "$SRC/postgres.dump"

# --- Qdrant -----------------------------------------------------------------
# The Qdrant image is distroless (no shell, no curl), so push the file in with
# `docker compose cp` and drive the API from a throwaway curl container.
echo "==> [2/3] Qdrant: restoring collection '$COLLECTION'"
NETWORK=$("${COMPOSE[@]}" ps --format json qdrant \
    | python3 -c 'import json,sys; d=json.loads(sys.stdin.readline()); print(d["Networks"])')

"${COMPOSE[@]}" cp "$SRC/qdrant.snapshot" \
    "qdrant:/qdrant/snapshots-restore.snapshot"

# priority=snapshot makes the snapshot's data win over whatever is live.
docker run --rm --network "$NETWORK" curlimages/curl:8.11.1 \
    -fsS -X PUT \
    -H "api-key: ${VECTOR_DB_API_KEY:-}" \
    "http://qdrant:6333/collections/$COLLECTION/snapshots/recover?wait=true" \
    -H 'Content-Type: application/json' \
    -d '{"location":"file:///qdrant/snapshots-restore.snapshot","priority":"snapshot"}' \
    >/dev/null

# --- MinIO ------------------------------------------------------------------
# No `tar` in the MinIO image, so copy the directory in and mirror from it.
echo "==> [3/3] MinIO: restoring bucket '$BUCKET'"
"${COMPOSE[@]}" exec -T minio rm -rf /tmp/restore
"${COMPOSE[@]}" cp "$SRC/minio-objects" "minio:/tmp/restore"
"${COMPOSE[@]}" exec -T minio sh -c "
    set -e
    mc alias set local http://localhost:9000 \"\$MINIO_ROOT_USER\" \"\$MINIO_ROOT_PASSWORD\" >/dev/null
    mc mb --ignore-existing local/$BUCKET >/dev/null
    mc mirror --overwrite --quiet /tmp/restore local/$BUCKET >/dev/null
    rm -rf /tmp/restore
"

echo "==> Restarting api, worker and beat"
"${COMPOSE[@]}" start api worker beat >/dev/null

echo
echo "==> Restore complete."
echo "    Check health: docker compose --env-file $ENV_FILE -f $COMPOSE_FILE ps"
