#!/usr/bin/env bash
set -e

build_dir="$PWD/build/host"
if [[ ! -f "$build_dir/CMakeCache.txt" || ! -f "$build_dir/CMakeFiles/rules.ninja" ]]; then
    llvm_dir="$(llvm-config --cmakedir)"
    cmake -S compiler -B "$build_dir" -G Ninja \
        -DCMAKE_CXX_COMPILER=clang++ -DLLVM_DIR="$llvm_dir" \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
else
    configure=()
    if grep -q '^CMAKE_BUILD_TYPE:STRING=$' "$build_dir/CMakeCache.txt"; then
        configure+=(-DCMAKE_BUILD_TYPE=Release)
    fi
    if [[ ! -f "$build_dir/compile_commands.json" ]]; then
        configure+=(-DCMAKE_EXPORT_COMPILE_COMMANDS=ON)
    fi
    if (( ${#configure[@]} )); then
        cmake -S compiler -B "$build_dir" "${configure[@]}"
    fi
fi

cmake --build "$build_dir" --target vaqc --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-3}"

case "${1:-}" in
    *.vqr) exec python3 .zed/run.py run "$1" ;;
esac
