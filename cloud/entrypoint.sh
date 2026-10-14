#!/bin/sh
# Runner image entrypoint: make the baked models visible to PaddleX, then run
# the command. PaddleX needs a writable cache home (it writes locks/ and temp/
# there), but Lambda's image filesystem is read-only apart from /tmp. So the
# models are baked read-only into /opt/calls-log/models (tools/bake_models.py)
# and linked into the cache home here, once per container start.
set -eu
: "${PADDLE_PDX_CACHE_HOME:=/tmp/paddlex}"
mkdir -p "$PADDLE_PDX_CACHE_HOME"
if [ ! -e "$PADDLE_PDX_CACHE_HOME/official_models" ]; then
    ln -s /opt/calls-log/models "$PADDLE_PDX_CACHE_HOME/official_models"
fi
exec "$@"
