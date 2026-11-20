#!/usr/bin/env bash
set -euo pipefail

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd -- "$source_dir/../.." && pwd)
output="$repo/build/zed-extension"
grammar="$output/grammar"

mkdir -p "$grammar" "$output/languages"
cp -R "$source_dir/grammar/." "$grammar/"
mkdir -p "$output/src"
cp "$source_dir/Cargo.toml" "$output/"
if [[ -f "$source_dir/Cargo.lock" ]]; then cp "$source_dir/Cargo.lock" "$output/"; fi
cp "$source_dir/src/lib.rs" "$source_dir/src/server.py" "$output/src/"
cp -R "$source_dir/languages/." "$output/languages/"
git -C "$grammar" init --quiet
git -C "$grammar" add -- grammar.js tree-sitter.json src
if ! git -C "$grammar" diff --cached --quiet; then
    git -C "$grammar" -c user.name=Vaqrin -c user.email=local@vaqrin.invalid \
        commit --quiet -m 'Build local Vaqrin grammar'
fi

url=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).as_uri())' "$grammar")
revision=$(git -C "$grammar" rev-parse HEAD)
cat "$source_dir/extension.toml" > "$output/extension.toml"
printf '\n[grammars.vaqrin]\nrepository = "%s"\nrev = "%s"\n' "$url" "$revision" >> "$output/extension.toml"
printf '%s\n' "$output"
