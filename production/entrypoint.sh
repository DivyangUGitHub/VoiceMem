#!/bin/sh
# Mounted volumes (Render disks, Docker named volumes) can arrive owned by root.
# Fix ownership of the writable data directories, then drop privileges.
set -eu

DATA="${VOICEMEM_DATA_DIR:-/var/lib/voicemem}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA/voicemem_memoryspace" "$DATA/logs" "$DATA/huggingface" "$DATA/models"
    # Not recursive on models/ or huggingface/: they can hold many GB and are read-mostly.
    chown voicemem:voicemem "$DATA" "$DATA/models" "$DATA/huggingface"
    chown -R voicemem:voicemem "$DATA/voicemem_memoryspace" "$DATA/logs"
    exec gosu voicemem "$@"
fi

exec "$@"
