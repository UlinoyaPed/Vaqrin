# HTTP/2

The HTTP/2 transport uses nghttp2 via a C ABI for the connection preface,
frame parsing, HPACK static and dynamic tables and Huffman coding, peer
stream and connection states, multiplexing, and flow control. Vaqrin
handles transport I/O, the public header and event types, and application
scheduling. A native Vaqrin implementation would have to maintain all of
these interacting state machines and their malformed-input handling.
Using nghttp2 preserves their protocol behavior while keeping the C ABI
independent of application protocols.

`Connection.open(Role.Client, Settings())` or `Role.Server` configures
an HTTP/2 session. The caller passes its own `io.Reader`/Writer to `receive`
and `flush` or supplies bytes via `ingest`. The caller can use
`openTls(role, raw transport, settings)` with a `tls.TlsStream`; it checks that
ALPN selected `h2`. Use `TlsConfig.requireProtocol` and offer only `h2` for
a dedicated HTTP/2 endpoint. Opening the low-level connection on an
unencrypted stream is explicit prior knowledge. TLS and cleartext never
fall back to HTTP/1.1.

Use `request` to submit HTTP headers and obtain the assigned stream ID.
A server reads `EventKind.Headers` and responds with `respond`.
The `end` flag closes the outbound half immediately. Otherwise,
`sendData` queues body bytes and `sendTrailers` or `sendData(..., true)`
ends the outbound half. A combined end event and header event are delivered
in order. The connection allows interleaved progress on independent stream
IDs. Keep each connection on one I/O owner or serialize its access externally.

`receive` reads one bounded chunk. Drain `next` for headers, DATA,
end-of-stream, closure status, connection settings, PINGs or GOAWAY.
`consume(stream, count)` credits consumed DATA at both flow-control
levels. Call it once for each processed payload, and flush again to deliver
WINDOW_UPDATE and ACK frames. For each header block, use `event.headers()`
while the owning event is live. Header bytes may be opaque. Pass
`sensitive = true` to `addBytes` to disable indexing for credentials.

Local settings cap concurrent peer streams, decoded header list size, HPACK
tables, stream receive windows and pending input or output. The backend uses
the protocol's default 16 KiB inbound frame size, caps CONTINUATION chains,
and limits queued ACKs. Server push is disabled. GOAWAY, RST_STREAM and
ordinary stream end expose separate events. Callers decide how to finish
active streams and handle transport timeouts.

Applications using HTTP/2 require nghttp2 development headers and
the runtime library (Debian/Ubuntu package `libnghttp2-dev`).
`vaqc` links it on demand. When linking an emitted static library separately,
supply `-lnghttp2`. Native networking targets hosted GNU/Linux x86-64 and
AArch64. Tests use Python `h2` over loopback with both TLS and explicit h2c.

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113) ·
[nghttp2 C API](https://nghttp2.org/documentation/programmers-guide.html)
