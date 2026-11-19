#!/usr/bin/env python3
"""Static type-check for the hosted I/O packages; does not run the output program."""
import pathlib
import subprocess
import sys

compiler = pathlib.Path(sys.argv[1]).resolve()
root = pathlib.Path(__file__).resolve().parents[2]
example = root / "examples" / "io_stack.vqr"

if not compiler.is_file():
    raise SystemExit(f"vaqc not found: {compiler}")
if not example.is_file():
    raise SystemExit(f"I/O example not found: {example}")

result = subprocess.run(
    [str(compiler), "check", str(example)],
    cwd=root,
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
if result.returncode:
    print(result.stdout, end="")
    raise SystemExit(result.returncode)

print("Vaqrin I/O package static check passed.")
