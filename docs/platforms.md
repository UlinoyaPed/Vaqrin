# Host platforms and releases

The release workflow builds `vaqc` for Linux, macOS and Windows on x86-64 and
AArch64. The compiler links against a statically built LLVM 20.1.8 SDK.
Each archive contains the compiler, standard packages and runtime sources.
The workflow checks that the installed compiler can format, parse, check,
emit a native object and run a linked smoke program.

GNU/Linux is the reference host for files, sockets, HTTP, processes, tasks
and channels. The macOS build also supports the POSIX file and environment
backend, the default allocator and system entropy. Its release smoke test
builds and runs a program that uses these APIs. Sockets, processes, epoll,
tasks and channels still require GNU/Linux. The Windows matrix exercises
the compiler and tools. On Windows the release smoke test links the emitted COFF object
with the Visual Studio linker. The LLVM SDK and vaqc use the dynamic MSVC CRT.
Use a Visual Studio developer environment
and Clang when using the driver's native executable link mode on Windows.

Push a tag in the form `vMAJOR.MINOR.PATCH` on a commit that is in `main` to
publish a release. You can add a prerelease suffix, such as `-rc.1`.
Publishing waits for every build and attaches the six archives and
`SHA256SUMS`. A manual workflow run builds archives without publishing.

Use `vaqc fmt FILE.vqr` to emit a formatted copy, `vaqc fmt --write FILE.vqr`
to edit in place and `vaqc fmt --check FILE.vqr` to check formatting. The
formatter uses four-space indentation, preserves line endings and adjusts
leading and trailing horizontal whitespace. `vaqc check --syntax-only
FILE.vqr` skips imports and type checking. `--diagnostics=json` produces an
LSP-compatible UTF-16 range when a diagnostic has a source location.
The Zed extension uses the same diagnostics and formats unsaved documents.


The Zed server checks open buffers through `vaqc check --overlay-map`.
Compiler symbol locations cover local bindings, top-level functions and global
values in the checked sources. Rechecking a rename verifies the edited buffers'
name resolution before returning the edits.
