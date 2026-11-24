#!/usr/bin/env bash
# Empaqueta la API para AWS Lambda (Python 3.12, arm64) en build/api.zip.
# Las dependencias se descargan como wheels de Linux arm64, asi que sirve desde Windows, macOS o Linux.
# boto3 NO se incluye: ya viene en el runtime de Lambda.
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"
OUT=build/lambda

rm -rf "$OUT" build/api.zip
mkdir -p "$OUT"

"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet \
  --target "$OUT" \
  --platform manylinux2014_aarch64 --implementation cp --python-version 3.12 --abi cp312 \
  --only-binary=:all: \
  -r requirements/api.txt

cp -r src/kid "$OUT/kid"
find "$OUT" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$OUT" -type d -name "*.dist-info" -prune -exec rm -rf {} +
find "$OUT" -type d -name "tests" -prune -exec rm -rf {} +
rm -rf "$OUT/bin"

"$PYTHON" scripts/make_zip.py "$OUT" build/api.zip
