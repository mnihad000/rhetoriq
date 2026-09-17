#!/bin/sh
set -eu

# A single standalone JobManager has no external HA coordinator. Restore the
# most recent completed checkpoint from the shared durable volume when its
# container restarts. _metadata is published only for a completed checkpoint.
if [ "${1:-}" = "standalone-job" ]; then
    latest=$(python3.12 -c 'from pathlib import Path; paths=list(Path("/opt/flink/checkpoints").glob("*/chk-*/_metadata")); print(str(max(paths,key=lambda p:p.stat().st_mtime).parent) if paths else "")')
    if [ -n "$latest" ]; then
        set -- "$@" --fromSavepoint "$latest"
    fi
fi
exec /docker-entrypoint.sh "$@"
