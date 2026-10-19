#!/usr/bin/env bash
# Build a single-file executable at dist/speedtui with PyInstaller.
set -euo pipefail
cd "$(dirname "$0")"

if command -v uv >/dev/null 2>&1; then
    uv sync --extra build
    PY=(uv run python)
else
    [ -d .venv ] || python3 -m venv .venv
    .venv/bin/pip install -q -e '.[build]'
    PY=(.venv/bin/python)
fi

"${PY[@]}" -m PyInstaller \
    --onefile \
    --noconfirm \
    --clean \
    --workpath build \
    --specpath build \
    --name speedtui \
    --paths "$(pwd)/src" \
    --collect-submodules textual \
    --collect-data textual \
    --collect-submodules rich \
    --add-data "$(pwd)/src/speedtui/data:speedtui/data" \
    src/speedtui/__main__.py

echo "Built: $(pwd)/dist/speedtui"
