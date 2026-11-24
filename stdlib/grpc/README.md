# gRPC

`grpc.dial("https://host:port", options)` negotiates `h2` over authenticated
TLS. `allowH2c` is false by default. Enable it explicitly with an `http://`
endpoint for cleartext prior knowledge. `ClientConn` reuses one HTTP/2
connection. The caller owns the connection and can interleave multiple active
calls with the low-level API. Each active stream has its own message decoder.
`DialOptions.maxConcurrentCalls` bounds the number of active calls. Read or
cancel each call to release its slot.

`ClientConn.unary(path, requestBytes, raw callOptions)` sends one message. The
path uses the fully qualified service and method, such as
`/example.Greeter/SayHello`. `UnaryResponse` contains the server status,
initial metadata, trailers and optional body. Encode and decode the body with
`protobuf.Codec<T>` or supply your own byte codec. A non-OK remote response
retains its gRPC status, message and raw status details. Local transport and
protocol errors use Vaqrin error results.

Set `CallOptions.deadline` to a monotonic deadline. The client emits
`grpc-timeout` and bounds its socket I/O by the remaining time. A peer may
return a remote `StatusCode.DeadlineExceeded` before the local deadline fires.
The local deadline uses `RpcError.DeadlineExceeded`. The low-level client
call API provides `start`, `sendMessage`, `finishSend`, `recvMessage`,
`cancel` and `Call.complete`. Cancel sends RST_STREAM.

On the server, create `grpc.Server()` and register each path with a
`UnaryHandler`. Keep each handler alive for the duration of registration and
processing. Use `acceptTls(raw listener, raw acceptor, options)` or pass a connected
stream to `serveTls` or `serveH2c`. The server decodes each request envelope,
passes metadata and deadline to the handler, and encodes one response message
or an error status. Both roles use the five-byte gRPC envelope and accept
identity or gzip messages. Set `CallOptions.compression` to
`Compression.Gzip` to request gzip encoding. A server with
`ServeOptions.compressResponses` enabled uses it when the peer advertises
support. Each message has an independent gzip member. Both wire and decoded
sizes are bounded by `maxMessageBytes`.

Use `serveConcurrentH2c` or `acceptConcurrentTls` with read-only,
shareable handlers. Register a `ConcurrentUnaryHandler` with
`registerConcurrent<H>` or a `ConcurrentStreamingHandler` with
`registerConcurrentStreaming<H>`. Both unsafe registrations borrow the
handler, which must stay alive at a stable address until the server no
longer uses it. `ServeOptions.applicationWorkers` bounds the worker count
and `maxConcurrentStreams` bounds the number of outstanding calls.

A concurrent streaming handler reads, writes and sets metadata through
`ConcurrentServerStream`. Return a `Status` to finish the call. Request
half-close returns `null` from `recvMessage`. The connection owns the
HTTP/2 session and processes other calls while a handler waits for a
request or peer flow-control credit. A peer reset or local deadline
cancels the corresponding task. A cancelled handler releases its slot
when it finishes.

Pass a `Shutdown.open()` token to the concurrent serving methods for
graceful shutdown of an established connection. Calling `request()` sends
GOAWAY and lets already accepted calls finish. The server closes the
connection once they drain. If the drain exceeds
`ServeOptions.shutdownTimeout`, the server cancels the remaining tasks
and returns `RpcError.DeadlineExceeded`. Keep the shutdown token alive
until all serving calls using it return. Listener acceptance and TLS
handshake retain their own deadlines.

For streaming, register a `StreamingHandler` with `registerStreaming`.
A `ServerStream` reads and writes individual messages and exposes initial
and trailing metadata. End of the sending half returns `null` from
`recvMessage` while the responding half remains usable. Return a `Status`
to finish the call. The APIs support server-streaming, client-streaming
and bidirectional calls. Streaming handlers and handlers registered with `register` execute synchronously.
While a handler reads its stream, events for other streams are deferred
within a fixed bound and dispatched when the handler returns.

Generate application types with `protoc-gen-vaqrin`. The generated
client encodes input, invokes the RPC, and decodes the typed result. For
streaming methods it exposes typed send, receive and half-close operations.
Implement the generated service interface and keep its generated server
adapter at a stable address while registered. The adapter owns the
individual method registrations. For concurrent dispatch, implement the
generated `ConcurrentService_<Name>` interface with const methods, put the
shareable implementation in `mem.Shared`, and pass it to
`ConcurrentServer_<Name>`. Streaming methods receive the corresponding
`ConcurrentStream_<Name>_<Method>` wrapper. Register the adapter in an
unsafe block and keep it at a stable address for the lifetime of its routes.
See the [Greeter example](../../examples/projects/grpc/README.md).

The transport uses OpenSSL 3, nghttp2 and zlib on hosted GNU/Linux. Building
gRPC applications requires zlib development headers (`zlib1g-dev` on
Debian and Ubuntu). Reference
acceptance tests use Python grpcio for both directions plus a Vaqrin-to-Vaqrin
loopback with real Protobuf messages.

[gRPC over HTTP/2 protocol](https://grpc.github.io/grpc/core/md_doc__p_r_o_t_o_c_o_l-_h_t_t_p2.html)
