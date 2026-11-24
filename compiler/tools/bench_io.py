"""Compare equivalent I/O workloads using paired Vaqrin compilers and SDKs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import tempfile
import time


PROGRAM = r'''
import (bufio, env, fs, io, path)

func main() !void {
    if env.contains("VAQRIN_BENCH_COPY")! {
        var source = path.fromString("input.txt")!;
        var destination = path.fromString("copy.txt")!;
        io.println(fs.copy(source, destination)!)!;
        return;
    }
    var input = fs.openRead(path.fromString("input.txt")!)!;
    var reader = bufio.BufferedReader(INPUT_ADDRESS, 4096)!;
    var count: u64 = 0;
    for {
        var line = reader.readLine(128)! ?? { break; };
        count += u64(line.byteLen()) + 1;
    }
    io.println(count)!
}
'''


def timed(command, cwd, env):
    start = time.perf_counter()
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=90)
    elapsed = time.perf_counter() - start
    if result.returncode:
        details = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"{cwd.name}: {command[0]} failed ({result.returncode}): {details}")
    return elapsed, result.stdout


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def measure(runs, compiler, sdk, root, address, chunk, repetitions):
    root.mkdir()
    source = root / "main.vqr"
    source.write_text(PROGRAM.replace("INPUT_ADDRESS", address), encoding="utf-8")
    data = root / "input.txt"
    copied = root / "copy.txt"
    with data.open("wb") as stream:
        for _ in range(repetitions):
            stream.write(chunk)
    expected = len(chunk) * repetitions
    digest = sha256(data)

    # Use the case's own source, build output, runtime/AST caches and copy
    # destination. Do not inherit opt-in caches or workload mode.
    env = {
        **os.environ,
        "VAQRIN_STDLIB_DIR": str(sdk / "stdlib"),
        "VAQRIN_RUNTIME_DIR": str(sdk / "compiler/runtime"),
        "XDG_CACHE_HOME": str(root / "cache"),
    }
    for key in ("VAQRIN_AST_CACHE", "VAQRIN_MODULE_CACHE", "VAQRIN_BENCH_COPY"):
        env.pop(key, None)
    output = root / ("workload.exe" if os.name == "nt" else "workload")
    command = (compiler, "build", source, "-O2", "-o", output)
    cold, _ = timed(command, root, env)
    warm = [timed(command, root, env)[0] for _ in range(runs)]

    results = {
        "compiler_cold_ms": round(1000 * cold, 2),
        "compiler_warm_ms": round(1000 * statistics.median(warm), 2),
    }
    for case, copy in (("buffered_lines", False), ("file_copy", True)):
        samples = []
        runtime_env = {**env, "VAQRIN_BENCH_COPY": "1"} if copy else env
        for _ in range(runs + 1):
            if copy:
                copied.unlink(missing_ok=True)
            seconds, value = timed((output,), root, runtime_env)
            if value.strip() != str(expected).encode("ascii"):
                raise ValueError(f"{root.name}/{case}: expected {expected} bytes, got {value!r}")
            if copy:
                if not copied.is_file() or copied.stat().st_size != expected:
                    raise ValueError(f"{root.name}/{case}: output file is missing or has the wrong size")
                if sha256(copied) != digest:
                    raise ValueError(f"{root.name}/{case}: output file differs from the input")
            samples.append(seconds)
        results[f"{case}_mib_s"] = round(expected / (1024 * 1024 * statistics.median(samples[1:])), 2)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate"):
        parser.add_argument(f"--{name}-compiler", required=True, type=Path,
                            help=f"{name} vaqc executable")
        parser.add_argument(f"--{name}-sdk", required=True, type=Path,
                            help=f"{name} source/SDK root containing stdlib/ and compiler/runtime/")
    parser.add_argument("--runs", default=7, type=int)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    pairs = {}
    for name in ("baseline", "candidate"):
        compiler = getattr(args, f"{name}_compiler").resolve()
        sdk = getattr(args, f"{name}_sdk").resolve()
        if not compiler.is_file() or not os.access(compiler, os.X_OK):
            parser.error(f"--{name}-compiler is not an executable file: {compiler}")
        for relative in ("stdlib", "compiler/runtime"):
            if not (sdk / relative).is_dir():
                parser.error(f"--{name}-sdk is missing {relative}/: {sdk}")
        pairs[name] = compiler, sdk

    chunk = (b"x" * 63 + b"\n") * 16384
    repetitions = 16
    with tempfile.TemporaryDirectory(prefix="vaqrin-bench-") as scratch:
        root = Path(scratch)
        try:
            results = {
                name: measure(args.runs, *pairs[name], root / name, address, chunk, repetitions)
                for name, address in (("baseline", "&input"), ("candidate", "raw input"))
            }
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            parser.exit(1, f"{parser.prog}: {error}\n")
        cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
        print(json.dumps({
            "platform": platform.platform(),
            "logical_cpus": cpus,
            "input_mib": len(chunk) * repetitions / (1024 * 1024),
            "runs": args.runs,
            **results,
        }, indent=2))


if __name__ == "__main__":
    main()
