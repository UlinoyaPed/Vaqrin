#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
output="$1"
mkdir -p "$output"
output="$(cd "$output" && pwd)"
cd "$output"
cxx="${CXX:-g++}"
cc="${CC:-gcc}"
gcov_tool="${GCOV:-gcov}"
diagnostics=(-g -O1 -Wall -Wextra -Werror -fno-omit-frame-pointer -fno-sanitize-recover=all)

"$cxx" -std=c++20 "${diagnostics[@]}" -fno-exceptions -fno-rtti -pthread \
    -fsanitize=address,undefined --coverage -c "$repo_root/compiler/runtime/io_posix.cpp" -o io_posix.o
"$cxx" -std=c++20 "${diagnostics[@]}" -pthread -fsanitize=address,undefined --coverage -no-pie \
    -DVAQRIN_STANDALONE_FUZZ "$repo_root/compiler/tests/io_runtime_fuzz.cpp" io_posix.o -o io-fuzz
rm -f io_posix.gcda io-fuzz-io_runtime_fuzz.gcda
ASAN_OPTIONS="${ASAN_OPTIONS:-detect_leaks=1}" ./io-fuzz
"$gcov_tool" -b -c io_posix.gcno > gcov.log
cat gcov.log

# swapcontext uses separate stacks. UBSan checks the task/reactor path without
# depending on ASan's optional fiber-switching hooks.
"$cc" -std=c11 "${diagnostics[@]}" -pthread -fsanitize=undefined \
    "$repo_root/compiler/runtime/tasks.c" "$repo_root/compiler/tests/task_io_native.c" -o task-io
./task-io
