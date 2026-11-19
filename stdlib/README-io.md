# Host I/O packages

`io`, `fs`, `path`, `bufio` and `fmt` provide synchronous streams, POSIX files and
directories, owned lexical UTF-8 paths, buffered scanning and writer-oriented
formatting. The host runtime targets GNU/Linux x86-64 and AArch64. File and
directory owners support explicit close and RAII cleanup. Standard descriptors
remain borrowed.

See the [I/O implementation inventory](../docs/stdlib/io-stack.md) for public
entries, error encoding, ownership, the x86-64 execution tests and remaining gaps.
