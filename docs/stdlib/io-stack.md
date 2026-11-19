# Vaqrin I/O stack

## Scope and platform contract

The first host port targets synchronous hosted GNU/Linux x86-64 and AArch64.
The native implementation is isolated in compiler/runtime/io_posix.cpp and exposes
only fixed-width scalars, size_t, byte pointers, and opaque directory handles through
a C ABI. POSIX descriptors and structs do not appear in public Vaqrin signatures.

Windows, macOS, non-Linux targets, and freestanding profiles are rejected when an
object references the host I/O runtime. LLVM target support alone does not imply
that an OS runtime port exists.

## Packages

- io: Reader, Writer, Seeker, Closer, SeekOrigin, IoError, IoProgress, memory streams,
  LimitedReader, CountingReader/Writer, MultiReader/Writer, TeeReader, exact reads,
  full writes, bounded reads, copying, standard streams and host handles.
- path: owned UTF-8 Path, PathBuf alias, lexical parent/name/extension/stem,
  component iteration, joining, normalization and extension replacement.
- fs: File, OpenOptions, FileType, Metadata, Permissions, Directory and DirectoryEntry,
  file helpers, metadata, rename/remove, mkdir/rmdir and directory reads.
- bufio: BufferedReader, BufferedWriter, Token, Scanner, peek/unreadByte,
  readUntil/readString/readLine, flush and finish.
- fmt: FormatOptions, FormatArg, integer and floating formatting, string/character/
  boolean formatting, width/alignment/fill, radix/precision, escaped braces and Writer output.

## Error representation

Named language errors retain normal ErrorId values. A native errno is encoded with
bit 31 set and the native error number in bits 0..30. Use io.isSystemError and
io.systemErrorCode to inspect it. This encoding is process-local and must not be
serialized as a stable error number. Linux EINTR and EAGAIN are recognized by the
progress helpers; other errno values are preserved unchanged.

## Ownership and buffering

HostFile and HostDirectory are non-copyable RAII owners. Explicit close reports an OS
error; deinit performs best-effort cleanup. Standard descriptors are borrowed and
are never closed by their wrapper.

BufferedReader preserves unread bytes when compacting its buffer. A peeked slice is
borrowed and may be invalidated by the next mutating reader operation. BufferedWriter
retains only the unwritten suffix after a short write or an error; already committed
bytes are never replayed by a later flush.

## Known limitations

- Path currently models POSIX-style UTF-8 paths; arbitrary non-UTF-8 directory names
  are reported as UTF-8 errors. Windows UTF-16 paths are not implemented.
- Metadata exposes size, file type, mode bits and modified Unix seconds. Creation time
  and richer ACL/security descriptors are not exposed.
- Directory traversal is one directory level; recursive walking and symlink policy
  helpers are not implemented.
- Scanner uses ASCII whitespace splitting. Custom split callbacks and Unicode
  whitespace tokenization are not implemented.
- Formatter supports a small brace-format subset; user-defined Display/DebugFormat
  dispatch, locale-aware formatting and cached FormatPlan are not implemented.
- Host I/O is synchronous. No asynchronous or network interfaces are added.
