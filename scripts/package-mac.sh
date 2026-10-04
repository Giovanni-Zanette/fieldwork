#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo 'Source build requires uv (https://docs.astral.sh/uv/) and Xcode command-line tools.' >&2; exit 1; }
xcrun --find swiftc >/dev/null
uv python install 3.12.13
if [ ! -x .packaging-venv/bin/python ]; then uv venv --python 3.12.13 .packaging-venv; fi
uv pip install --python .packaging-venv/bin/python -r requirements.lock.txt -r desktop/requirements-build.lock.txt
exec .packaging-venv/bin/python scripts/package-mac.py "$@"
