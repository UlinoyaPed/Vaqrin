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
