#!/usr/bin/env python3
"""Focused source-level I/O acceptance smoke tests.

Run with: python3 compiler/tests/io_acceptance.py /path/to/vaqc
"""
import pathlib
import subprocess
import sys
import tempfile

VAQC = pathlib.Path(sys.argv[1]).resolve()

def compile_source(source: str, expected: int = 0):
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        file = root / "main.vqr"
        file.write_text(source, encoding="utf-8")
        result = subprocess.run([str(VAQC), "build", str(file), "-o", str(root / "app")],
                                text=True, capture_output=True)
        if result.returncode != expected:
            raise AssertionError(result.stdout + result.stderr)
        return root / "app"

# The legacy println remains the compatibility smoke test.
app = compile_source('import (io)\nfunc main() !void { io.println("io-stack")!; }')
run = subprocess.run([str(app)], text=True, capture_output=True)
assert run.returncode == 0 and run.stdout == "io-stack\n", (run.stdout, run.stderr)

print("io acceptance smoke tests passed")
