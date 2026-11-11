#!/usr/bin/env bash
#
# Back up the production stack: Postgres, the Qdrant collection, and the MinIO
# bucket. Produces a timestamped directory under backups/.
#
#   ./scripts/backup.sh [output-dir]
#
# The stack must be running — every step goes through the compose services.
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.prod.yml}
ENV_FILE=${ENV_FILE:-.env.prod}
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

if [[ ! -f $ENV_FILE ]]; then
    echo "error: $ENV_FILE not found. Copy .env.prod.template and fill it in." >&2
    exit 1
fi

# shellcheck disable=SC1090
set -a && source "$ENV_FILE" && set +a

DB_NAME=${DATABASE_NAME:-db}
DB_USER=${DATABASE_USER:-postgres}
BUCKET=${S3_BUCKET:-rag-documents}
COLLECTION=${VECTOR_DB_COLLECTION_NAME:-rag}

DEST_ROOT=${1:-backups}
STAMP=$(date +%Y%m%d-%H%M%S)
DEST="$DEST_ROOT/$STAMP"
mkdir -p "$DEST"

echo "==> Backing up to $DEST"

# --- Postgres ---------------------------------------------------------------
# Custom format (-Fc): compressed and restorable with pg_restore.
echo "==> [1/3] Postgres: dumping database '$DB_NAME'"
"${COMPOSE[@]}" exec -T db \
    pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$DEST/postgres.dump"
echo "    wrote postgres.dump ($(du -h "$DEST/postgres.dump" | cut -f1))"

# --- Qdrant -----------------------------------------------------------------
# The Qdrant image is distroless — no curl, no python3 — so the HTTP calls run
# from a throwaway curl container attached to the compose network instead.
echo "==> [2/3] Qdrant: snapshotting collection '$COLLECTION'"
NETWORK=$("${COMPOSE[@]}" ps --format json qdrant \
    | python3 -c 'import json,sys; d=json.loads(sys.stdin.readline()); print(d["Networks"])')

qcurl() {
    docker run --rm --network "$NETWORK" curlimages/curl:8.11.1 \
        -fsS -H "api-key: ${VECTOR_DB_API_KEY:-}" "$@"
}

SNAP_NAME=$(qcurl -X POST "http://qdrant:6333/collections/$COLLECTION/snapshots" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["name"])')
echo "    snapshot: $SNAP_NAME"

# `docker compose cp` reads the file out of the volume without needing a shell
# in the image.
"${COMPOSE[@]}" cp \
    "qdrant:/qdrant/storage/snapshots/$COLLECTION/$SNAP_NAME" "$DEST/qdrant.snapshot"

# Reclaim the space inside the volume; the copy on disk is the backup now.
qcurl -X DELETE \
    "http://qdrant:6333/collections/$COLLECTION/snapshots/$SNAP_NAME" >/dev/null
echo "    wrote qdrant.snapshot ($(du -h "$DEST/qdrant.snapshot" | cut -f1))"

# --- MinIO ------------------------------------------------------------------
# `mc` ships inside the MinIO image but `tar` does not, so mirror into a temp
# dir in the container and copy the directory out with `docker compose cp`.
echo "==> [3/3] MinIO: mirroring bucket '$BUCKET'"
"${COMPOSE[@]}" exec -T minio sh -c "
    set -e
    mc alias set local http://localhost:9000 \"\$MINIO_ROOT_USER\" \"\$MINIO_ROOT_PASSWORD\" >/dev/null
    rm -rf /tmp/backup-$STAMP
    mkdir -p /tmp/backup-$STAMP
    mc mirror --quiet local/$BUCKET /tmp/backup-$STAMP >/dev/null
"
"${COMPOSE[@]}" cp "minio:/tmp/backup-$STAMP" "$DEST/minio-objects"
"${COMPOSE[@]}" exec -T minio rm -rf "/tmp/backup-$STAMP"
echo "    wrote minio-objects/ ($(du -sh "$DEST/minio-objects" | cut -f1))"

cat > "$DEST/MANIFEST" <<EOF
timestamp=$STAMP
database=$DB_NAME
collection=$COLLECTION
bucket=$BUCKET
EOF

echo
echo "==> Backup complete: $DEST"
echo "    Restore with: ./scripts/restore.sh $DEST"
