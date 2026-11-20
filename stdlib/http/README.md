# http

The package reads HTTP/1.0 and HTTP/1.1 messages and writes HTTP/1.1.
`parseRequest`, `parseResponse` and `parseResponseFor` accept an `io.Reader`.
Use `parseResponseFor` when the request method affects whether a body is
present. The reader handles fixed-length, chunked and close-delimited
responses. Headers have case-insensitive lookup and preserve repeated
fields other than framing and Host fields. Header names and values are
validated, and message size is bounded by `HttpLimits`.

The writer sends a fixed-length body. `Client` connects to an `http://` URL
using `net`, with connect and socket timeouts. `Server.serveOne` accepts one
client and invokes an `http.Handler`. Both use a connection per exchange.
Body data is buffered within the configured limit. TLS, connection pooling,
streaming bodies and HTTP upgrades remain pending.

See [RFC 9112](https://www.rfc-editor.org/rfc/rfc9112.html) for the wire framing.
