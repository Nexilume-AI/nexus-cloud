#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${NEXUS_COMMUNITY_PYTHON:-python3}"
cd "$ROOT/nexus_server"
exec "$PYTHON" -m nexus_personal.linux_launcher "$@"
