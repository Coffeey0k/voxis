#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
python -m pip install -r requirements-client.txt -r requirements-packaging.txt
pyinstaller --noconfirm --clean --name Voxis --windowed --onefile -m voxis_client.main
echo "Desktop binary: dist/Voxis"
