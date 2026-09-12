#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"

docker compose -f "$COMPOSE_FILE" up -d minio minio-init

deadline=$(( $(date +%s) + 90 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if curl -fsS "http://127.0.0.1:9000/minio/health/ready" >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" run --rm minio-init
    echo "MinIO is ready and bucket nexus-datasets exists."
    exit 0
  fi
  sleep 2
done

echo "Timed out waiting for MinIO readiness." >&2
exit 1
