"""Build a static LLVM SDK and package a native vaqc distribution."""

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
WINDOWS = os.name == "nt"
MSVC_RUNTIME = "MultiThreadedDLL"
WINDOWS_SMOKE_SOURCE = """import (coll, cmp, mem, random, sync, text, thread, time)

func heapWorks() !bool {
    var allocator = mem.GlobalAllocator();
    var layout = mem.Layout.fromSizeAlign(31, 64)!;
    var block = allocator.alloc(layout)!.unwrap();
    var aligned = mem.isAligned(block, 64);
    allocator.free(block, layout);
    var items = coll.Vec<u64>();
    for value in u64(0)..u64(96) { items.push(value)!; }
    var boxed = new u64(23)!;
    var owned = text.fromBytes("Windows heap".asBytes())!;
    var copy = owned;
    return aligned && items[95] == 95 && *boxed.asPtr() == 23 && copy == "Windows heap";
}

func clocksWork() !bool {
    var utc = time.now()!;
    var start = time.Instant.now()!;
    var delay = time.Duration.fromMillis(5);
    var end = start.checkedAdd(delay)!;
    var deadline = time.Deadline.at(end);
    time.sleepUntil(end)!;
    return utc.unixSeconds() > 1700000000 && deadline.expired()! &&
           start.elapsed()!.compare(delay) != cmp.Ordering.Less;
}

func wideDivisionWorks(value: i128, divisor: i128, unsignedValue: u128, unsignedDivisor: u128) bool {
    return value / divisor == -18446744072904245248i128 && value % divisor == -2415919235i128 &&
           unsignedValue / unsignedDivisor == 9223372036854775807u128 &&
           unsignedValue % unsignedDivisor == 9223372036854775812u128;
}

func threadsWork() !bool {
    var mainId = thread.currentId().value;
    var work = [mainId]() u32 => {
        if thread.currentId().value == mainId { return 0; }
        while !thread.stopRequested() { thread.yieldNow(); }
        42
    };
    var worker = thread.Thread<u32>.spawn(move(work))!;
    var id = worker.id().value;
    worker.requestStop();
    return worker.join()! == 42 && id != mainId && worker.id().value == id && thread.cpuCount() > 0;
}

class SyncProbe {
    pub var mutex: sync.Mutex<i32>;
    pub var ready: sync.CondVar;
    pub func init() : mutex(sync.Mutex<i32>.new(0)), ready(sync.CondVar.new()) {}
}

func synchronizationWorks() !bool {
    var probe = mem.Shared<SyncProbe>.new(SyncProbe())!;
    var local = probe.asPtr();
    var guard = local.mutex.lock()!;
    var shared = probe;
    var update = [move(shared)]() !void => {
        var remote = shared.asPtr();
        var held = remote.mutex.lock()!;
        *held.get() = 23;
        remote.ready.notifyOne();
    };
    var updater = thread.Thread<void>.spawn(move(update))!;
    var updated = (value: *i32) bool => *value == 23;
    var acquired = sync.waitUntil(raw local.ready, move(guard), updated)!;
    updater.join()!;

    var deadline = time.Deadline.after(time.Duration.fromMillis(5))!;
    for {
        next, elapsed := local.ready.waitUntilDeadline<i32>(move(acquired), deadline)!;
        acquired = move(next);
        if elapsed { break; }
    }
    if !deadline.expired()! || *acquired.get() != 23 { return false; }
    acquired.unlock();

    var group = mem.Shared<sync.WaitGroup>.new(sync.WaitGroup.new())!;
    group.asPtr().add(1)!;
    var participant = group;
    var finish = [move(participant)]() void => { participant.asPtr().done(); };
    var detached = thread.Thread<void>.spawn(move(finish))!;
    detached.detach();
    group.asPtr().wait()!;
    return group.asPtr().count() == 0;
}

export "C" func main() i32 {
    if !(heapWorks() catch { return 3; }) { return 4; }
    if !(clocksWork() catch { return 5; }) { return 6; }
    if !wideDivisionWorks(-((1i128 << 100) + 131), (1i128 << 36) + 3,
                          (1u128 << 127) + 3, (1u128 << 64) + 1) { return 7; }
    if !(threadsWork() catch { return 8; }) { return 9; }
    if !(synchronizationWorks() catch { return 10; }) { return 11; }
	var bytes = mem.zeroedBytes<16>();
    random.fillSystem(bytes[:]) catch { return 1; };
    for byte in bytes[:] { if byte != 0 { return 0; } }
    2
}
"""


def run(*args, **options):
    return subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True, **options)


def build_llvm():
    output = BUILD / "llvm-release"
    prefix = BUILD / "llvm-sdk"
    options = {
        "CMAKE_BUILD_TYPE": "Release",
        "CMAKE_INSTALL_PREFIX": prefix.as_posix(),
        "LLVM_TARGETS_TO_BUILD": "X86;AArch64;ARM",
        "LLVM_ENABLE_ZLIB": "OFF",
        "LLVM_ENABLE_ZSTD": "OFF",
        "LLVM_ENABLE_LIBXML2": "OFF",
        "LLVM_ENABLE_TERMINFO": "OFF",
        "LLVM_ENABLE_LIBEDIT": "OFF",
        "LLVM_ENABLE_CURL": "OFF",
        "LLVM_ENABLE_BINDINGS": "OFF",
        "LLVM_BUILD_TOOLS": "OFF",
        "LLVM_INCLUDE_TESTS": "OFF",
        "LLVM_INCLUDE_EXAMPLES": "OFF",
        "LLVM_INCLUDE_BENCHMARKS": "OFF",
        "LLVM_INCLUDE_DOCS": "OFF",
        "LLVM_PARALLEL_LINK_JOBS": "1",
    }
    if WINDOWS:
        options["CMAKE_POLICY_DEFAULT_CMP0091"] = "NEW"
        options["CMAKE_MSVC_RUNTIME_LIBRARY"] = MSVC_RUNTIME
    run("cmake", "-S", BUILD / "llvm-project/llvm", "-B", output, "-G", "Ninja",
        *(f"-D{key}={value}" for key, value in options.items()))
    run("cmake", "--build", output, "--parallel", "2")
    run("cmake", "--install", output)
    shutil.copy2(BUILD / "llvm-project/llvm/LICENSE.TXT", prefix / "LLVM-LICENSE")


def smoke(compiler, prefix):
    source = BUILD / "release-smoke.vqr"
    program = WINDOWS_SMOKE_SOURCE if WINDOWS else "func main() i32 {\n\treturn 0;  \n}\n"
    source.write_text(program, encoding="utf-8")
    run(compiler, "fmt", "--write", source)
    run(compiler, "fmt", "--check", source)
    run(compiler, "check", "--syntax-only", "--diagnostics=json", source)
    run(compiler, "check", source)
    obj = BUILD / ("release-smoke.obj" if WINDOWS else "release-smoke.o")
    run(compiler, "build", "--emit=obj", source, "-o", obj)
    exe = BUILD / ("release-smoke.exe" if WINDOWS else "release-smoke")
    if WINDOWS:
        run("link.exe", "/nologo", f"/out:{exe}", obj, "msvcrt.lib", "vcruntime.lib", "ucrt.lib",
            "kernel32.lib", "bcrypt.lib", "synchronization.lib")
    else:
        run(compiler, "build", source, "-o", exe)
    run(exe)
    if WINDOWS:
        smoke_windows_executable(compiler)
    source.write_text("func main(\n", encoding="utf-8")
    failed = subprocess.run([str(compiler), "check", "--syntax-only", "--diagnostics=json", str(source)],
                            cwd=ROOT, capture_output=True, text=True)
    if failed.returncode != 1 or not json.loads(failed.stderr)["code"].startswith("E1"):
        raise RuntimeError("installed syntax checker did not diagnose invalid source")
    if not (prefix / "bin/protoc-gen-vaqrin").is_file():
        raise RuntimeError("installed SDK is missing protoc-gen-vaqrin")
    if not (prefix / "share/vaqrin/stdlib/linalg/linalg.vqr").is_file():
        raise RuntimeError("installed standard library is missing linalg")
    if sys.platform in ("darwin", "linux"):
        smoke_posix_io(compiler)


def smoke_windows_executable(compiler):
    source = BUILD / "release-windows-entry.vqr"
    source.write_text('''import (io, mem, random, sync)
error SmokeError { ExpectedFailure }
func main() !void {
    var bytes = mem.zeroedBytes<16>();
    random.fillSystem(bytes[:])!;
    var lock = sync.Mutex<i32>.new(42);
    var guard = lock.lock()!;
    io.println(*guard.get())!;
    return SmokeError.ExpectedFailure;
}
''', encoding="utf-8")
    executable = BUILD / "release-windows-entry.exe"
    run(compiler, "build", source, "-o", executable)
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
    if result.returncode != 1 or result.stdout != "42\n" or "SmokeError.ExpectedFailure" not in result.stderr:
        raise RuntimeError(f"Windows hosted entry returned {result.returncode}: "
                           f"{result.stdout!r} {result.stderr!r}")


def smoke_posix_io(compiler):
    source = """
import (env, fs, io, mem, random)
func main() !i32 {
    fs.writeFile("sample.txt", "installed runtime".asBytes())!;
    var data = fs.readText("sample.txt", 128)!;
    var executable = env.currentExe()!;
    var seed = mem.zeroedBytes<16>();
    random.fillSystem(seed[:])!;
    fs.remove("sample.txt")!;
    if data != "installed runtime" { return 1; }
    io.println(data)!;
    0
}
"""
    with tempfile.TemporaryDirectory(prefix="release-io-", dir=BUILD) as workspace:
        root = Path(workspace)
        entry = root / "posix.vqr"
        entry.write_text(source, encoding="utf-8")
        executable = root / "posix"
        run(compiler, "build", entry, "-o", executable)
        tested = subprocess.run([str(executable)], cwd=root, text=True, capture_output=True, check=True)
        if tested.stdout != "installed runtime\n" or (root / "sample.txt").exists():
            raise RuntimeError("installed POSIX runtime smoke test failed")


def smoke_version(compiler, llvm_version):
    version = (ROOT / "compiler" / "VERSION").read_text(encoding="utf-8").strip()
    expected = f"vaqc {version} (Vaqrin {version}, LLVM {llvm_version})\n"
    result = subprocess.run([str(compiler), "--version"], capture_output=True, text=True, check=True)
    if result.stdout != expected or result.stderr:
        raise RuntimeError(f"unexpected installed compiler version: expected {expected!r}, "
                           f"got stdout={result.stdout!r}, stderr={result.stderr!r}")
    print(result.stdout, end="")


def package():
    platform_id = os.environ["VAQRIN_RELEASE_PLATFORM"]
    reported = platform.machine().lower()
    machine = {"amd64": "x86_64", "arm64": "aarch64"}.get(reported, reported)
    system = platform.system().lower().replace("darwin", "macos")
    if platform_id != f"{system}-{machine}":
        raise RuntimeError(f"unexpected runner architecture for {platform_id}: {reported}")
    output = BUILD / "release-compiler"
    prefix = BUILD / "release-root" / "vaqrin"
    options = [
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DLLVM_DIR={(BUILD / 'llvm-sdk/lib/cmake/llvm').as_posix()}",
        "-DVAQRIN_REQUIRE_BASELINE=ON",
        "-DVAQRIN_USE_LLVM_DYLIB=OFF",
        "-DBUILD_TESTING=OFF",
    ]
    if WINDOWS:
        options.append(f"-DCMAKE_MSVC_RUNTIME_LIBRARY={MSVC_RUNTIME}")
    run("cmake", "-S", ROOT / "compiler", "-B", output, "-G", "Ninja", *options)
    run("cmake", "--build", output, "--parallel", "2")
    run("cmake", "--install", output, "--prefix", prefix)
    compiler = prefix / "bin" / ("vaqc.exe" if WINDOWS else "vaqc")
    smoke_version(compiler, "20.1.8")  # Required by VAQRIN_REQUIRE_BASELINE.
    smoke(compiler, prefix)
    shutil.copy2(ROOT / "README.md", prefix)
    shutil.copy2(ROOT / "LICENSE", prefix)
    shutil.copy2(BUILD / "llvm-sdk/LLVM-LICENSE", prefix)
    shutil.copy2(ROOT / "docs/platforms.md", prefix)
    assets = BUILD / "release-assets"
    assets.mkdir(exist_ok=True)
    shutil.make_archive(str(assets / f"vaqrin-{platform_id}"),
                        "zip" if WINDOWS else "gztar", root_dir=prefix.parent, base_dir=prefix.name)


if __name__ == "__main__":
    if sys.argv[1:] == ["llvm"]:
        build_llvm()
    elif sys.argv[1:] == ["package"]:
        package()
    elif len(sys.argv) == 3 and sys.argv[1] == "smoke":
        prefix = Path(sys.argv[2]).resolve()
        compiler = prefix / "bin" / ("vaqc.exe" if WINDOWS else "vaqc")
        smoke(compiler, prefix)
    else:
        sys.exit("usage: release.py llvm|package|smoke PREFIX")
