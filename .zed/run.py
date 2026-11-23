"""Run one hosted source using the last successfully validated executable."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build/host"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024


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


def cache_directory(path, *, create=False):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError(f"run cache directory must be owned by the current user and protected from writes: {path}")


def open_cache_file(path, *, lock=False):
    flags = os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_RDWR | os.O_CREAT if lock else os.O_RDONLY)
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError(f"invalid run cache file: {path}")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def program_digest(source, path):
    before = os.fstat(source.fileno())
    fingerprint = hashlib.sha256()
    while data := source.read(1024 * 1024):
        fingerprint.update(data)
    after = os.fstat(source.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("cached executable changed while being read")
    return [str(path), after.st_size, fingerprint.hexdigest()]


def read_manifest(path):
    with open_cache_file(path) as source:
        data = source.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("run cache manifest is too large")
    return json.loads(data)


def cached_program(program, metadata, compiler, source, *, rerun):
    try:
        record = read_manifest(metadata)
        if not rerun and snapshot(record["manifest"], compiler, source) != record["fingerprint"]:
            return None
        executable = open_cache_file(program)
        try:
            if program_digest(executable, program) == record["program"]:
                return executable
        except BaseException:
            executable.close()
            raise
        executable.close()
    except (OSError, KeyError, TypeError, ValueError, RecursionError):
        pass
    return None


def run_program(executable, program, source):
    os.chdir(source.parent)
    log(f"---running {source.name}---")
    # The caller holds the cache lock until exec. On hosts with fexecve, run
    # the open file whose contents were checked, even if its name changes.
    if os.execve in os.supports_fd:
        os.execve(executable.fileno(), [str(program)], os.environ)
    os.execv(program, [str(program)])


def main(command, filename):
    if command not in ("run", "rerun"):
        raise ValueError("expected run or rerun")
    source = Path(filename).resolve()
    if source.suffix != ".vqr" or not source.is_file():
        raise ValueError("select an existing .vqr file")
    compiler = BUILD / "vaqc"
    if not compiler.is_file() and command != "rerun":
        subprocess.run(["bash", ROOT / ".zed/build.sh"], cwd=ROOT, check=True)
    key = hashlib.sha256(os.fsencode(source)).hexdigest()
    for directory in (ROOT, BUILD.parent, BUILD):
        cache_directory(directory)
    cache_directory(BUILD / "run", create=True)
    cache = BUILD / "run" / key
    cache_directory(cache, create=True)
    program = cache / "program"
    metadata = cache / "inputs.json"

    with open_cache_file(cache / "lock", lock=True) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        executable = cached_program(program, metadata, compiler, source, rerun=command == "rerun")
        if executable is not None:
            with executable:
                log("reusing last successful executable (source may have changed)" if command == "rerun"
                    else "cached executable; skipping compilation")
                run_program(executable, program, source)
        if command == "rerun":
            raise ValueError("no verified successful build exists for this file; run it once to rebuild")
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
            record = {"manifest": None, "fingerprint": None}
            try:
                manifest = read_manifest(dependencies)
                record.update(manifest=manifest, fingerprint=snapshot(manifest, compiler, source, started))
            except (OSError, KeyError, TypeError, ValueError, RecursionError):
                pass
            with open_cache_file(output) as executable:
                record["program"] = program_digest(executable, program)
                temporary = stage / "inputs.json"
                temporary.write_text(json.dumps(record), encoding="utf-8")
                temporary.chmod(0o600)
                os.replace(output, program)
                os.replace(temporary, metadata)
        with open_cache_file(program) as executable:
            run_program(executable, program, source)


if __name__ == "__main__":
    try:
        main(*sys.argv[1:])
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
    except (OSError, ValueError) as error:
        log(str(error))
        sys.exit(1)
