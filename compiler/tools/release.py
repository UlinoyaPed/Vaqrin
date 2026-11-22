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
WINDOWS_SMOKE_SOURCE = """import (mem, random)
export "C" func main() i32 {
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
        run("link.exe", "/nologo", f"/out:{exe}", obj, "msvcrt.lib", "vcruntime.lib", "ucrt.lib", "kernel32.lib", "bcrypt.lib")
    else:
        run(compiler, "build", source, "-o", exe)
    run(exe)
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
    run(compiler, "--version")
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
