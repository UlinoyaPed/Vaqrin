# Host I/O packages

`io`, `fs`, `path`, `bufio` and `fmt` provide synchronous streams, POSIX files and
directories, owned lexical UTF-8 paths, buffered scanning and writer-oriented
formatting. The POSIX file backend targets GNU/Linux and macOS on x86-64
and AArch64. Reactor and task-backed I/O target GNU/Linux. File and
directory owners support explicit close and RAII cleanup. Standard descriptors
remain borrowed.

See the [I/O implementation inventory](../docs/stdlib/io-stack.md) for public
entries, error encoding, ownership, the x86-64 execution tests and remaining gaps.


### Retry after a partial operation

`http.Body.writeTo` advances by bytes accepted before an error. A retry writes
the remaining body. `Scanner.next` retains the partial token. A buffered
delimiter read resumes with the same delimiter and a limit that covers its
retained prefix. Reset the buffered reader to start reading a new source.

After a `MultiWriter` error, retry `write` with the same unacknowledged
prefix. Completed destinations and partial writes are tracked independently.
`TeeReader` retains data until the mirror acknowledges it and supports a
smaller buffer on retry. POSIX file output, including stdout connected to a
closed pipe, surfaces `EPIPE` as `io.systemError`.

### Asynchronous HTTP

`http.AsyncClient` uses a monotonic deadline for DNS, connection establishment,
TLS negotiation and socket I/O in a one-shot request. A connection created
with `connect` can serve sequential HTTP/1.1 exchanges. `sendStreaming` hands
the response body to a reader callback. `sendWithBodyStream` additionally sends
a source reader as bounded HTTP/1.1 chunks. Each chunk is written before the
next source read, so transmit backpressure limits buffering. An exchange
with an unread response body closes its connection.

`parseRequestStream` and `parseResponseStream` return the head and a body
reader that borrows the input. `AsyncServer.serveOneStreaming` and
`serveOneStreamingTls` keep that reader alive for the request callback.

`http.AsyncPool` owns one configured client and caches reusable connections
by scheme and authority. Capacity bounds the idle list. The next checkout
drops expired connections, and capacity pressure evicts the least recently
returned one. A failed exchange closes its connection. The pool does not
replay any request. The owning task can call `close` to release idle sockets.
