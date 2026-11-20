# net

`Ipv4Addr`, `Ipv6Addr` and `SocketAddr` hold address values. Use `parseIp` for an
IP literal and `parseSocketAddr` for an address and port. IPv6 endpoints use
brackets and accept a numeric scope ID such as `[fe80::1%7]:80`. Port zero is
valid for binding an ephemeral port. `localAddr` reports the port assigned by
the operating system. `resolveHost` and `resolveAddr` use the system resolver.

`TcpStream` implements `io.Reader` and `io.Writer`. It provides connection and
I/O timeouts, peer and local addresses, half-close, TCP_NODELAY and descriptor
duplication. `TcpListener` supports blocking and nonblocking accept. Socket
resources close when their owning handles are dropped. The I/O methods block
by default and return native errors through `io.systemErrorCode`.

`UdpSocket` supports bound and connected datagrams, send-to and receive-from.
`Datagram.len` counts bytes copied to the caller's buffer, and
`Datagram.truncated` identifies a larger message. A zero-length datagram
preserves its message boundary.

These APIs target hosted GNU/Linux x86-64 and AArch64. Unix-domain sockets,
multicast membership, reverse DNS and an asynchronous I/O integration remain
pending.

## Task-aware TCP

`AsyncTcpListener.bind` and `AsyncTcpStream.connect` use nonblocking
sockets. `AsyncTcpStream.fromTcp` consumes an existing TCP stream and
enables nonblocking mode. Use a separate socket owner for each connection.
The async stream implements the same Reader and Writer interfaces as
`TcpStream`.

Within a TaskGroup, a would-block operation registers a one-shot epoll
watch and suspends the task. Each group starts one readiness thread when
its first descriptor wait is registered. The scheduler continues to run
other tasks on its workers while the readiness thread waits. A task waits
on one descriptor at a time. Outside a task, the same API uses a blocking
readiness wait with the supplied monotonic deadline.

Give connect and accept an optional `time.Deadline`, and set independent
read and write deadlines on the stream. Deadline expiry and cooperative
cancellation return distinct I/O errors. An orderly receive half-close
returns zero after all bytes have been read. Use the progress variants of
`io.readExact` and `io.writeAll` when retrying a partially completed transfer.

TLS adapters can use `task.waitDescriptor` with the read or write interest
matching their transport's WANT_READ or WANT_WRITE result. TLS handshake
state remains the responsibility of the TLS adapter.
