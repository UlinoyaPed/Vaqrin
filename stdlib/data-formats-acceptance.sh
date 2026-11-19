#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -n "${VAQC:-}" ]; then
    COMPILER=$VAQC
elif [ -x "$ROOT/build/host/vaqc" ]; then
    COMPILER="$ROOT/build/host/vaqc"
else
    COMPILER=vaqc
fi

OUT=$(mktemp -d "${TMPDIR:-/tmp}/vaqrin-data-formats-XXXXXXXX")
trap 'rm -rf "$OUT"' 0 HUP INT TERM

cd "$ROOT"

"$COMPILER" build stdlib/json/examples/json_smoke.vqr -o "$OUT/json-smoke"
"$OUT/json-smoke"

"$COMPILER" build stdlib/csv/examples/csv_smoke.vqr -o "$OUT/csv-smoke"
"$OUT/csv-smoke"

"$COMPILER" build stdlib/toml/examples/toml_smoke.vqr -o "$OUT/toml-smoke"
"$OUT/toml-smoke"

"$COMPILER" build stdlib/serde/examples/manual_codec.vqr -o "$OUT/serde-codec"
"$OUT/serde-codec"
