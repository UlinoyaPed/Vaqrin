"""Check acceptance and rejection of the TOML 1.0.0 syntax fixtures."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile


CHECKER = """
import (io, toml)
func main() i32 {
    var input = io.stdin();
    var document = toml.parseReader(&input, 8 * 1024 * 1024) catch { return 2; };
    0
}
"""


def check(compiler, fixtures):
    manifest = fixtures / "files-toml-1.0.0"
    documents = [Path(name) for name in manifest.read_text(encoding="utf-8").splitlines()
                 if name.endswith(".toml")]
    with tempfile.TemporaryDirectory(prefix="vaqrin-toml-") as directory:
        source = Path(directory) / "checker.vqr"
        executable = source.with_suffix("")
        source.write_text(CHECKER, encoding="utf-8")
        subprocess.run([compiler, "build", "-O2", source, "-o", executable], check=True, timeout=60)

        def run(document):
            path = (fixtures / document).resolve()
            if not path.is_relative_to(fixtures) or document.parts[0] not in ("valid", "invalid"):
                raise ValueError(f"unexpected fixture path {document}")
            expected = 0 if document.parts[0] == "valid" else 2
            result = subprocess.run([executable], input=path.read_bytes(), capture_output=True, timeout=10)
            if result.returncode != expected:
                return f"{document} returned {result.returncode}, expected {expected}\n{result.stderr.decode(errors='replace')}"
            return None

        with ThreadPoolExecutor(max_workers=4) as workers:
            failures = [failure for failure in workers.map(run, documents) if failure]
    if failures:
        raise SystemExit("\n".join(failures))
    if not documents:
        raise SystemExit("TOML fixture manifest is empty")
    print(f"Passed {len(documents)} TOML 1.0.0 syntax fixtures")


if __name__ == "__main__":
    check(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
