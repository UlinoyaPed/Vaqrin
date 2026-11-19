# Host I/O packages

This branch adds the first synchronous hosted I/O layer.

- `io`: stream contracts, in-memory streams, adapters, host file descriptors,
  native error preservation, and standard descriptor accessors.
- `path`: UTF-8 lexical path views and basic joining/component operations.
- `fs`: host files, open options, metadata, basic directory and cwd operations.
- `bufio`: buffered reader/writer and bounded line reading.
- `fmt`: writer-oriented text/integer formatting primitives.

The initial native runtime is GNU/Linux only. The compiler rejects I/O-runtime
references for freestanding, Windows, macOS, and non-64-bit targets. This is
intentional: LLVM code generation support does not imply a host runtime port.

Native system errors are represented in `ErrorId` with bit 31 set and the
native errno in bits 0..30. Use `io.isSystemError` and `io.systemErrorCode`
to inspect them. Named `IoError` members remain ordinary language error IDs.

The runtime uses a small C ABI and does not expose `FILE*`, `DIR*`, file
descriptors, or POSIX structs in public package signatures. Files and directory
handles are non-copyable RAII owners. Explicit close reports failure; destructors
perform best-effort cleanup.

This is an initial implementation tranche. Buffered token scanning, robust
cross-buffer peek/unread semantics, recursive directory traversal, Windows/macOS
ports, and full dynamic format plans are not yet implemented.
