"""Run one hosted source using the last successfully validated executable."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build/host"


def digest(path):
    if not path.is_file():
        return None
    with path.open("rb") as source:
        fingerprint = hashlib.sha256()
        while data := source.read(1024 * 1024):
            fingerprint.update(data)
    info = path.stat()
    return [str(path.resolve()), info.st_size, fingerprint.hexdigest()]


def snapshot(manifest, compiler, source, since=None):
    files = set(manifest["files"]) | {str(compiler), str(source), __file__}
    for name in ("clang", "ld", "ld.bfd", "as"):
        executable = shutil.which(name)
        if executable:
            files.add(executable)
    values = {}
    for path in map(Path, sorted(files)):
        if since and path.exists() and path.stat().st_mtime_ns > since:
            raise ValueError("source changed during compilation")
        values[str(path)] = digest(path)
    directories = {}
    for path in map(Path, manifest["directories"]):
        if not path.is_dir():
            directories[str(path)] = None
            continue
        if since and path.stat().st_mtime_ns > since:
            raise ValueError("directory changed during compilation")
        directories[str(path)] = [
            str(path.resolve()),
            sorted([entry.name, str(entry.resolve()), entry.is_file()]
                   for entry in path.iterdir() if entry.suffix == ".vqr"),
        ]
    toolchain = {}
    for path in map(Path, ("/usr/include", "/usr/lib", "/usr/lib64", "/lib", "/lib64",
                          "/usr/lib/gcc", "/etc/ld.so.cache", "/usr/lib/sysimage/rpm/rpmdb.sqlite",
                          "/var/lib/dpkg/status")):
        if path.exists():
            stat = path.stat()
            toolchain[str(path)] = [stat.st_dev, stat.st_ino, stat.st_mtime_ns]
    environment = {name: value for name, value in os.environ.items()
                   if name in ("PATH", "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "LIBRARY_PATH",
                               "COMPILER_PATH", "GCC_EXEC_PREFIX", "LD_LIBRARY_PATH", "SDKROOT",
                               "CLANG_CONFIG_FILE", "CLANG_CONFIG_FILE_SYSTEM_DIR", "CLANG_CONFIG_FILE_USER_DIR")}
    return [values, directories, toolchain, environment]


def log(message):
    print(f"vaqc: {message}", file=sys.stderr, flush=True)


def main(command, filename):
    source = Path(filename).resolve()
    if source.suffix != ".vqr" or not source.is_file():
        raise ValueError("select an existing .vqr file")
    compiler = BUILD / "vaqc"
    if not compiler.is_file() and command != "rerun":
        subprocess.run(["bash", ROOT / ".zed/build.sh"], cwd=ROOT, check=True)
    key = hashlib.sha256(os.fsencode(source)).hexdigest()
    cache = BUILD / "run" / key
    cache.mkdir(parents=True, exist_ok=True)
    program = cache / "program"
    metadata = cache / "inputs.json"

    with (cache / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if command == "rerun":
            if not program.is_file():
                raise ValueError("no successful build exists for this file")
            log("reusing last successful executable (source may have changed)")
        else:
            hit = False
            try:
                record = json.loads(metadata.read_text())
                hit = (program.is_file()
                       and snapshot(record["manifest"], compiler, source) == record["fingerprint"]
                       and digest(program) == record["program"])
            except (OSError, KeyError, TypeError, ValueError):
                pass
            if hit:
                log("cached executable; skipping compilation")
            else:
                log(f"building {source.name}")
                with tempfile.TemporaryDirectory(dir=cache) as directory:
                    stage = Path(directory)
                    dependencies = stage / "dependencies.json"
                    output = stage / "program"
                    started = time.time_ns()
                    environment = dict(os.environ, VAQRIN_STDLIB_DIR=str(ROOT / "stdlib"),
                                       VAQRIN_RUNTIME_DIR=str(ROOT / "compiler/runtime"))
                    subprocess.run([compiler, "build", "--timings", "--dependencies", dependencies,
                                    source, "-o", output], check=True, cwd=ROOT, env=environment)
                    os.replace(output, program)
                    metadata.unlink(missing_ok=True)
                    try:
                        manifest = json.loads(dependencies.read_text())
                        fingerprint = snapshot(manifest, compiler, source, started)
                        record = {"manifest": manifest, "fingerprint": fingerprint, "program": digest(program)}
                        temporary = stage / "inputs.json"
                        temporary.write_text(json.dumps(record))
                        os.replace(temporary, metadata)
                    except (OSError, KeyError, TypeError, ValueError):
                        pass
    os.chdir(source.parent)
    log(f"---running {source.name}---")
    os.execv(program, [str(program)])


if __name__ == "__main__":
    try:
        main(*sys.argv[1:])
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
    except (OSError, ValueError) as error:
        log(str(error))
        sys.exit(1)
