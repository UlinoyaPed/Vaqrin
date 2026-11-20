# http

The package reads HTTP/1.0 and HTTP/1.1 messages and writes HTTP/1.1.
`parseRequest`, `parseResponse` and `parseResponseFor` accept an `io.Reader`.
Use `parseResponseFor` when the request method affects whether a body is
present. The reader handles fixed-length, chunked and close-delimited
responses. Headers have case-insensitive lookup and preserve repeated
fields other than framing and Host fields. Header names and values are
validated, and message size is bounded by `HttpLimits`.

The writer sends a fixed-length body. `http.get`, `http.head`, and `http.post`
accept both `http://` and `https://` URLs. Use
`Client.request(address, method, body)` to choose another method and have the
client build the request target from the URL path and query. Use
`Client.send(address, &request)` to supply a request target and custom headers.
The client sets `Host` from the URL and uses `Connection: close`. Set
`Client.setTimeout` to change the timeout for connection attempts and socket I/O,
including the TLS handshake.

HTTPS uses TLS 1.2 or newer. The client verifies the server certificate chain
and the DNS name or IP address from the URL. It sends SNI for DNS names. The
default CA locations are supplied by OpenSSL. Set `SSL_CERT_FILE` to a PEM CA
bundle or `SSL_CERT_DIR` to an OpenSSL-hashed CA directory for a private CA.
`TlsError.Certificate` reports chain or identity verification failures.

HTTP client and server networking currently targets hosted GNU/Linux x86-64
and AArch64. HTTPS needs OpenSSL 3 development headers when building an
application with `vaqc`, and `libssl.so.3` when issuing HTTPS requests. For Debian
and Ubuntu, install `libssl-dev` to build and `ca-certificates` for the system
trust store. Plain HTTP works without OpenSSL. HTTPS reports
`TlsError.Unavailable` if the TLS backend could not be built or loaded.

`Server.serveOne` accepts one plain HTTP connection and invokes an
`http.Handler`. Both client and server use one connection per exchange and
buffer the body within `HttpLimits`. Redirect responses are returned to the
caller. Connection pooling, streaming bodies and protocol upgrades remain pending.

See [RFC 9112](https://www.rfc-editor.org/rfc/rfc9112.html) for the wire framing.
