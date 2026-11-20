# gRPC

`grpc.dial("https://host:port", options)` negotiates `h2` over authenticated
TLS. `allowH2c` is false by default. Enable it explicitly with an `http://`
endpoint for cleartext prior knowledge. `ClientConn` reuses one HTTP/2
connection. Its methods are single-owner and a connection carries one active
application call at a time.

`ClientConn.unary(path, requestBytes, &callOptions)` sends one message. The
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
processing. Use `acceptTls(&listener, &acceptor, options)` or pass a connected
stream to `serveTls` or `serveH2c`. The server decodes each request envelope,
passes metadata and deadline to the handler, and encodes one response message
or an error status. Both roles use the five-byte gRPC envelope. The supported
compression format is identity.

The transport uses OpenSSL 3 and nghttp2 on hosted GNU/Linux. Reference
acceptance tests use Python grpcio for both directions plus a Vaqrin-to-Vaqrin
loopback with real Protobuf messages.

[gRPC over HTTP/2 protocol](https://grpc.github.io/grpc/core/md_doc__p_r_o_t_o_c_o_l-_h_t_t_p2.html)
