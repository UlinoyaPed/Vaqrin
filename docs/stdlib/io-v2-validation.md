# I/O v2 validation

The comparison uses `main` at `1d6d5100959b64bf0390961116115506cc8489b1`
and the independent `feature/io-v2` worktree. Native validation ran on
GNU/Linux x86-64. The Vaqrin compiler used LLVM 20.1.8.

| Area | Evidence |
| --- | --- |
| File identity | Real same-path, hard-link and symlink aliases retain the source. Empty copy, overwrite, permission failures and a write to `/dev/full` run in the acceptance suite. |
| Recoverable I/O | Injected short writes, cancellation, would-block, interruption and disk errors verify cursor and pending-suffix recovery. A native signal also interrupts an actual epoll wait. |
| Epoll and TCP | Registration update/removal, timeout, descriptor reuse and register/close loops exercise native readiness. One worker handles 32 TCP round trips. Six rounds of 64 idle TCP connections are cancelled under a 256-fd limit. |
| Filesystem and buffers | The suite exercises atomic replacement and denied writes, temporary resource cleanup, bounded symlink-safe walking, consecutive lines, and a non-UTF-8 scanner token. |
| Compiler and previous features | The complete eight-suite CTest run covers compiler, I/O, containers, stdlib, HTTPS and editor regressions. The I/O suite also runs against an installation. |
| Native quality | 5,000 deterministic fuzz cases cover file and epoll operations using ASan and UBSan. A native task harness runs 100 readiness, timeout, cancellation and shutdown rounds under UBSan and compares descriptor and thread counts. |

Local LeakSanitizer failed to attach in the traced executor. The local
ASan run used `ASAN_OPTIONS=detect_leaks=0`. GitHub CI runs the native
harness with LeakSanitizer enabled by default and uploads gcov output.
The observed local coverage of the native harness was 29.47% of
`io_posix.cpp` lines and 100% of `reactor_linux.h` lines. This metric
covers the harness targets. It does not measure compiler or Vaqrin
stdlib coverage.

## Benchmark

Run `python3 compiler/tools/bench_io.py --compiler build/local/vaqc
--baseline /path/to/baseline-worktree` from the candidate checkout.
Each variant compiles at `-O2` with the same compiler and its own
runtime cache. Samples use a 16 MiB cached file with 64-byte lines.
The table reports seven-run medians except for the first build of each
variant. The host exposed nine logical CPU slots and ran Linux 6.18.57
with glibc 2.41.

| Measurement | Baseline | I/O v2 |
| --- | ---: | ---: |
| Buffered line reads | 159.22 MiB/s | 271.82 MiB/s |
| File copy | 591.39 MiB/s | 1927.73 MiB/s |
| First build | 757.79 ms | 729.01 ms |
| Warm build | 495.45 ms | 540.93 ms |

Copy numbers measure cached transport through the Reader/Writer path.
They provide no measure of durable writes or long-running disk
throughput. The single first-build sample and warm-build comparison
provide no evidence of a compiler speedup.

## Scope of this measurement

Task-aware TCP uses Linux epoll. TLS adapters can use the directional
readiness API to wait after WANT_READ or WANT_WRITE. The synchronous
HTTPS client keeps its existing TLS transport. Asynchronous DNS, UDP,
macOS and Windows runtime ports, and full Vaqrin-program sanitizer
coverage are outside the validated scope. The scheduler still uses a
global lock and a linear deadline scan.

The current native release matrix also checks installed macOS compilers on
x86-64 and AArch64. It exercises positioned I/O, file identity, atomic
replacement, temporary resources, traversal, broken stdout pipes, and
resumable HTTP Body and Scanner state. Linux runs the full I/O v2 suite,
including epoll, task-aware sockets, and asynchronous process pipes.
