# Vaqrin I/O stack

## Scope and platform contract

The first host port targets synchronous hosted GNU/Linux x86-64 and AArch64.
The native implementation is isolated in compiler/runtime/io_posix.cpp and exposes
only fixed-width scalars, size_t, byte pointers, and opaque directory handles through
a C ABI. POSIX descriptors and structs do not appear in public Vaqrin signatures.

Calls that require the native runtime are checked against the hosted GNU/Linux
LP64 target contract during type checking. Portable stream adapters and memory
streams can be used separately from the POSIX runtime.

## Packages

- io: Reader, Writer, Seeker, Closer, SeekOrigin, IoError, IoProgress, memory streams, positional ReaderAt/WriterAt, LimitedReader, CountingReader/Writer, MultiReader/Writer, TeeReader, exact reads,
  full writes, bounded reads, copying, standard streams and host handles.
- path: owned UTF-8 Path, PathBuf alias, lexical parent/name/extension/stem,
  component iteration, joining, normalization and extension replacement.
- fs: File, OpenOptions, FileType, Metadata, Permissions, Directory and DirectoryEntry; File supports seek and positional readAt/writeAt,
  file helpers, metadata, rename/remove, mkdir/rmdir and directory reads.
- bufio: BufferedReader, BufferedWriter, Token, Scanner, peek/unreadByte,
  readUntil/readString/readLine, flush and finish.
- fmt: FormatOptions, FormatArg, integer and floating formatting, string/character/
  boolean formatting, width/alignment/fill, radix/precision, escaped braces and Writer output.

## Error representation

Named language errors retain normal ErrorId values. A native errno is encoded with marker bits 01 in bits 31..30 and the native
error number in bits 0..29. This avoids collisions with the compiler's reserved
high-valued error IDs for allocation, numeric conversion and legacy output errors. Use io.isSystemError and
io.systemErrorCode to inspect it. This encoding is process-local and must not be
serialized as a stable error number. Linux EINTR and EAGAIN are recognized by the
progress helpers. The POSIX open/read/write boundary retries EINTR. Abstract
stream adapters report interruptions and would-block without an indefinite retry.
Other errno values retain their original number.

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


## Public API inventory

The following is the first implemented API surface. It is intentionally narrower than
the full package-matrix proposal; items listed here correspond to declarations in the
current source files.

### io

Interfaces: Reader, Writer, Seeker, ReaderAt, WriterAt, Closer.

Types: SeekOrigin(Start, Current, End), ReadStatus(Bytes, Eof, WouldBlock, Interrupted),
IoProgress(processed, status), IoError(WriteFailed, UnexpectedEof, NoProgress,
WouldBlock, Interrupted, InvalidCount, PositionOutOfRange, LimitExceeded, Closed,
InvalidArgument, Unsupported), MemoryReader, MemoryWriter, FixedWriter, LimitedReader,
CountingReader, CountingWriter, MultiReader, MultiWriter, HostFile,
HostMetadata, HostDirEntry, HostDirectory, TeeReader.

Functions:
- read(reader, dst), write(writer, src)
- readExact(reader, dst), readExactProgress(reader, dst), readAtLeast(reader, dst, min)
- readAt(reader, offset, dst), writeAt(writer, offset, src), readExactAt(reader, offset, dst), writeAllAt(writer, offset, src)
- writeAll(writer, src), writeAllProgress(writer, src), writeString(writer, value)
- readToEnd(reader, limit), readToEnd(reader), readToString(reader, limit), readToString(reader)
- copy(reader, writer), copy(reader, writer, scratch), copyN(reader, writer, n, scratch),
  copyAll(reader, writer), discard(reader, n, scratch)
- limit(reader, n), tee(reader, writer)
- stdin(), stdout(), stderr(), print(str), println<T>(value), eprint(str), eprintln(str),
  flushStdout()
- systemError(errno), systemErrorCode(error), isSystemError(error),
  isInterrupted(error), isWouldBlock(error)
- hostStat(pathBytes, follow), hostMkdir(pathBytes, mode), hostRemove(pathBytes),
  hostRemoveDir(pathBytes), hostRename(fromBytes, toBytes), hostChmod(pathBytes, mode),
  currentDir(), setCurrentDir(pathBytes)

HostFile methods: open(pathBytes, flags, mode), read, write, readAt, writeAt, seek,
flush, syncAll, syncData, truncate, metadata, setMode, close. Standard descriptor
constructors are exposed through stdin/stdout/stderr. readAt/writeAt do not change the
shared file cursor; writeAt rejects append-mode handles.

### path

Types: Path, PathBuf (alias of Path), PathComponents, Component(Root, Current, Parent,
Normal(str)), PathError(Empty, InvalidComponent, Capacity).

Construct with `Path(string)` or `Path.fromString(str) !Path`. Path methods include
asString, isAbsolute, isRelative, filename, parent, extension, stem, components,
normalize and withExtension. The `parent() !?Path` method can allocate. Filename,
stem and extension return borrowed text. Components hold a shared owning string.

Package functions include fromString, join, normalize, isAbsolute, isRelative,
components and withExtension. The filename, parent, extension and stem wrappers
take a pointer to the path owner.

### fs

Types: File, OpenOptions, FileType(File, Directory, Symlink, Device, Socket, Other),
Metadata, Permissions, Directory, DirectoryEntry, FsError(InvalidOptions, NotFound,
NotDirectory, IsDirectory).

OpenOptions methods: read, write, append, create, createNew, truncate, mode, open.
File methods: read, write, readAt, writeAt, seek, flush, close, syncAll, syncData,
truncate, metadata, setPermissions.
Metadata methods: len, isFile, isDir, isSymlink, fileType, permissions,
modifiedUnixSeconds. Permissions methods: mode, readonly, setReadonly.
Directory methods: next, close. DirectoryEntry methods: name, fileType, path, metadata.

Functions: open(path, options), create(path), openRead(path), openWrite(path),
readFile(path, limit), readText(path, limit), writeFile(path, bytes), appendFile(path,
bytes), copy(source, destination), exists(path), stat(path), lstat(path), chmod(path,
permissions), mkdir(path), remove(path), removeDir(path), rename(from, to), readDir(path),
currentDir(), setCurrentDir(path).

### bufio

Types: BufferedReader, BufferedWriter, Scanner, Token, BufferError(InvalidCapacity,
BufferFull, UnreadUnavailable, TokenTooLong).

BufferedReader methods: init(reader, capacity), fill, read, readByte, unreadByte, peek,
discard, readUntil(delimiter, maxBytes), readString(delimiter, maxBytes), readLine(maxBytes),
bufferLen, reset.
BufferedWriter methods: init(writer, capacity), write, writeByte, writeString, buffered,
available, flush, finish, reset.
Scanner methods: init(reader), setMaxToken, next, nextString, position, reset.
Token methods: text, start, end.
Functions: newReader(reader, capacity), newWriter(writer, capacity), newScanner(reader),
readLine(reader, maxBytes).

### fmt

Types: Alignment(Left, Right, Center), FloatStyle(General, Fixed, Scientific),
FormatOptions(radix, width, precision, fill, alignment, uppercase, showSign, floatStyle),
FormatArg(Text, OwnedText, Signed, Unsigned, Float, Boolean, Character), FormatError(InvalidRadix,
InvalidPrecision, WidthTooLarge, UnsupportedType, InvalidFormat).

Functions: formatInt(i8/i16/i32/i64/i128, radix), formatUint(u8/u16/u32/u64/u128,
radix), formatFloat(value, style, precision), arg(value), writeString(writer, value,
options), writeBool, writeChar, writeFloat, writeSigned, writeUnsigned, writeInt,
writeUint, writeHex, writeTo(writer, value), writeTo(writer, pattern, args),
format(value), format(pattern, args), print, println, eprint, eprintln.

The brace formatter supports automatic or explicitly indexed positional placeholders,
escaped braces, integer radices b/o/d/x/X, float styles f/e/g, sign, width, alignment
and precision. A format uses one indexing mode throughout. OwnedText holds the
string until the argument is destroyed. Float conversion uses the C numeric locale.
The FormatOptions value can also specify an arbitrary Unicode fill character.


## Local acceptance

The host suite executes binaries for standard streams, positional file I/O, directory
operations, short progress, path ownership and formatting. It also links an I/O
static archive from C to count descriptor closures and links both bundled runtimes
in one executable. x86-64 is the executed host target in this integration.

`Scanner.nextInt<T>() !?T` 以十进制读取下一个完整 Token，EOF 返回空。
`Scanner.readInt<T>() !T` 把 EOF 转换为 `io.IoError.UnexpectedEof`。
两者原样传播输入错误和溢出。包级 `bufio.readLine` 每次只消费一行；
连续高吞吐读取可复用同一 `BufferedReader`。
