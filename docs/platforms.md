# Host platforms and releases

The release workflow builds `vaqc` for Linux, macOS and Windows on x86-64 and
AArch64. The compiler links against a statically built LLVM 20.1.8 SDK.
Each archive contains the compiler, standard packages and runtime sources.
The workflow checks that the installed compiler can format, parse, check,
emit a native object and run a linked smoke program.
`vaqc build --emit=staticlib` writes a deterministic archive in the target's
format. Use `--ar PROGRAM` to select an external archiver.

GNU/Linux is the reference host for files, sockets, HTTP, processes, tasks
and channels. The macOS build also supports POSIX files, environment
variables, synchronous process execution, monotonic and UTC clocks,
synchronous TCP, UDP, DNS, HTTP/1 and TLS, native threads, blocking
synchronization, the default allocator and system entropy.
The release matrix exercises these APIs using the installed compiler.
Task-aware sockets, asynchronous process pipes, epoll, tasks and channels
require GNU/Linux. The Windows matrix exercises the compiler, tools,
default allocator, owning text, UTC and monotonic clocks, 128-bit integer
division and `random.OsRng`. The Windows release smoke test links a COFF object
with the Visual Studio linker and reads system entropy through CNG. The LLVM SDK
and vaqc use the dynamic MSVC CRT.
Use a Visual Studio developer environment
and Clang when using the driver's native executable link mode on Windows.

Push a tag in the form `vMAJOR.MINOR.PATCH` on a commit that is in `main` to
publish a release. You can add a prerelease suffix, such as `-rc.1`.
Publishing waits for every build and attaches the six archives and
`SHA256SUMS`. A manual workflow run builds archives without publishing.
Pull requests that update the release script, CMake build, or release workflow
also run the native build matrix. Push a release-related change to a
`release-check/*` branch to run the matrix before opening a pull request.
The workflow saves a newly built LLVM SDK before running the installed smoke tests,
so a smoke-test failure can be retried with the same SDK.

Use `vaqc fmt FILE.vqr` to emit a formatted copy, `vaqc fmt --write FILE.vqr`
to edit in place and `vaqc fmt --check FILE.vqr` to check formatting. The
formatter uses four-space indentation, preserves line endings and adjusts
leading and trailing horizontal whitespace. `vaqc check --syntax-only
FILE.vqr` skips imports and type checking. `--diagnostics=json` produces an
LSP-compatible UTF-16 range when a diagnostic has a source location.
The Zed extension uses the same diagnostics and formats unsaved documents.


The Zed server checks open buffers through `vaqc check --overlay-map`.
Compiler symbol locations cover local bindings, top-level functions, global
values, named types and fields in the checked sources. Field locations
include member access, field literals and constructor initializer lists.
Rechecking a rename verifies the edited buffers' name resolution before
returning the edits.
