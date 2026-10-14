#!/bin/sh
# Shared by pre-commit / pre-push: run tools/check_private_data.py with the
# project venv if present, otherwise any Python on PATH (stdlib only).
cd "$(git rev-parse --show-toplevel)" || exit 1
for py in .venv/Scripts/python.exe .venv/bin/python; do
  [ -x "$py" ] && exec "$py" -X utf8 tools/check_private_data.py "$@"
done
for py in python3 python; do
  command -v "$py" >/dev/null 2>&1 && exec "$py" -X utf8 tools/check_private_data.py "$@"
done
echo "private-data check: no Python found -- refusing to continue" >&2
exit 1
