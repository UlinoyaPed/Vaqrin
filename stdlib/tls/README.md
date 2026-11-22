# TLS

`TlsStream` owns a connected `net.TcpStream` and implements `io.Reader`,
`io.Writer` and `io.Closer`. Connect or accept the TCP stream first. Set TCP
read and write timeouts before handing it to TLS to bound blocking handshake I/O.
The same timeouts remain active after the handshake and can be changed on the TLS
stream. Explicit close is idempotent. Scope exit releases the TLS session and TCP
descriptor in that order.

`TlsConfig()` requires TLS 1.2 or later. Set `minimumVersion` to
`Version.Tls13` to require TLS 1.3. The client checks the certificate chain and
the requested DNS name or IP address and sends SNI for DNS names. It uses the
OpenSSL system trust locations by default. `setRootFile` replaces these
locations with a specific PEM trust bundle.

Use `addProtocol` to add opaque ALPN identifiers in preference order.
A server selects its first protocol offered by the client.
`requireProtocol = true` prevents either endpoint from returning a stream
without ALPN. A client that supports only HTTP/2 should offer only `h2` and
require negotiation. `negotiatedProtocol` returns an owned byte vector or
`null`.

A server creates a `TlsAcceptor.fromPem(cert, key, move(config))` from a PEM
chain file and an unencrypted PEM private key file. Each acceptance opens the
configured files and validates the identity before the handshake. Mutual TLS
and session resumption are future extensions. Configuration and native SSL
state are kept separate so a future credential mode can extend context setup
for either role. HTTP/1.x retains its existing TLS error names and uses the
same OpenSSL loader, verification and ownership logic.

Blocking TLS supports hosted GNU/Linux and macOS on x86-64 and AArch64.
Task-aware TLS requires GNU/Linux. Building with TLS requires OpenSSL 3
headers for the target. On macOS with Homebrew, set
`CPATH="$(brew --prefix openssl@3)/include"` when building with `vaqc`.
The runtime loads `libssl.so.3` on GNU/Linux and `libssl.3.dylib` on macOS,
including the standard Homebrew location. Client verification uses the CA
roots configured in OpenSSL or a PEM bundle supplied by the caller. Plain
HTTP works without OpenSSL.

[OpenSSL ALPN API](https://docs.openssl.org/3.5/man3/SSL_CTX_set_alpn_select_cb/)
