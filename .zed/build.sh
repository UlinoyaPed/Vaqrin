#!/usr/bin/env bash
set -e

build_dir="$PWD/build/host"
if [[ ! -f "$build_dir/CMakeCache.txt" ]]; then
    llvm_dir="$(llvm-config --cmakedir)"
    cmake -S compiler -B "$build_dir" -G Ninja \
        -DCMAKE_CXX_COMPILER=clang++ -DLLVM_DIR="$llvm_dir" \
        -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
elif [[ ! -f "$build_dir/compile_commands.json" ]]; then
    cmake -S compiler -B "$build_dir" -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
fi

cmake --build "$build_dir" --target vaqc --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-3}"

case "${1:-}" in
    *.vqr)
        program="$build_dir/current-vqr"
        "$build_dir/vaqc" build "$1" -o "$program"
        cd -- "$(dirname -- "$1")"
        exec "$program"
        ;;
esac
