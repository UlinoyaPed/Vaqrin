"""Compare the same compiled I/O workload using two Vaqrin SDK source trees."""
import argparse
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
    var reader = bufio.BufferedReader(&input, 4096)!;
    var count: u64 = 0;
    for {
        var line = reader.readLine(128)! ?? { break; };
        count += u64(line.byteLen()) + 1;
    }
    io.println(count)!
}
'''


def timed(args, cwd, env):
    start = time.perf_counter()
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, check=True, timeout=90)
    return time.perf_counter() - start, result.stdout


def measure(args, source, compiler, name, sdk, root, expected):
    output = root / name
    # An independent cache approximates the first compile with the native runtime sources.
    env = {
        **os.environ, "VAQRIN_STDLIB_DIR": str(sdk / "stdlib"),
        "VAQRIN_RUNTIME_DIR": str(sdk / "compiler/runtime"),
        "XDG_CACHE_HOME": str(root / f"cache-{name}"),
    }
    command = (compiler, "build", source, "-O2", "-o", output)
    cold, _ = timed(command, root, env)
    warm = [timed(command, root, env)[0] for _ in range(args.runs)]

    results = {
        "compiler_cold_ms": round(1000 * cold, 2),
        "compiler_warm_ms": round(1000 * statistics.median(warm), 2),
    }
    for case, mode in (("buffered_lines", {}), ("file_copy", {"VAQRIN_BENCH_COPY": "1"})):
        samples = []
        env.pop("VAQRIN_BENCH_COPY", None)
        for _ in range(args.runs + 1):
            seconds, value = timed([output], root, {**env, **mode})
            if int(value) != expected:
                raise ValueError(f"{case}: expected {expected} bytes, got {value!r}")
            samples.append(seconds)
        results[f"{case}_mib_s"] = round(expected / (1024 * 1024 * statistics.median(samples[1:])), 2)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", required=True, type=lambda value: Path(value).resolve())
    parser.add_argument("--baseline", required=True, type=lambda value: Path(value).resolve())
    parser.add_argument("--candidate", default=Path(__file__).resolve().parents[2],
                        type=lambda value: Path(value).resolve())
    parser.add_argument("--runs", default=7, type=int)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    with tempfile.TemporaryDirectory(prefix="vaqrin-bench-") as scratch:
        root = Path(scratch)
        source = root / "main.vqr"
        source.write_text(PROGRAM, encoding="utf-8")
        lines = (b"x" * 63 + b"\n") * 16384
        size = 16 * len(lines)
        with (root / "input.txt").open("wb") as data:
            for _ in range(16):
                data.write(lines)
        baseline = measure(args, source, args.compiler, "baseline", args.baseline, root, size)
        candidate = measure(args, source, args.compiler, "candidate", args.candidate, root, size)
        print(json.dumps({
            "platform": platform.platform(),
            "logical_cpus": len(os.sched_getaffinity(0)),
            "input_mib": size / (1024 * 1024), "runs": args.runs,
            "baseline": baseline, "candidate": candidate,
        }, indent=2))


if __name__ == "__main__":
    main()
