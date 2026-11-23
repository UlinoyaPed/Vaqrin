"""Loopback and reference implementation checks for the protocol packages."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from queue import Queue
import select
import socket
import ssl
import subprocess
import sys
import unittest

from acceptance import CompilerCase, REPOSITORY, VAQC


FIXTURES = REPOSITORY / "compiler/tests/fixtures/stack"


class ProtocolAcceptance(CompilerCase):
    def compile_fixture(self, name, executable):
        source = self.source("main.vqr", (FIXTURES / name).read_text(encoding="utf-8"))
        self.process(VAQC, source, "-o", executable)

    def reference_schema(self):
        self.process(sys.executable, "-m", "grpc_tools.protoc",
                     f"-I{FIXTURES}", "--python_out=.", FIXTURES / "wire.proto")
        spec = importlib.util.spec_from_file_location("wire_pb2", self.root / "wire_pb2.py")
        schema = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(schema)
        return schema

    def test_protobuf_reference(self):
        schema = self.reference_schema()
        canonical = schema.Envelope(
            signed32=-(1 << 31), signed64=-(1 << 63), unsigned32=(1 << 32) - 1,
            unsigned64=(1 << 64) - 1, zig32=-(1 << 31), zig64=-(1 << 63),
            fixed32_value=0xFEABCDEF, fixed64_value=0xFEDCBA9876543210,
            sfixed32_value=-42, sfixed64_value=-43, yes=True, mode=23,
            float_value=1.25, double_value=-2.5, title="互操作", payload=b"vaqrin",
            sequence=[-1, 0, 1], scores={"key": -6}, code=-1, count=0,
        )
        canonical.child.left = 5
        canonical.child.right = 7
        first = schema.Envelope()
        first.CopyFrom(canonical)
        first.child.ClearField("right")
        first.label = "overwritten"
        first.scores["key"] = 6
        first.count = 3
        last = schema.Envelope(code=-1, count=0, scores={"key": -6})
        last.child.right = 7
        # One unpacked sint32 occurrence of a field whose other values are packed.
        unpacked = bytes.fromhex("900103")
        # Two unknown fields, including a matching legacy group.
        unknown = bytes.fromhex("a00601ab060801ac06")
        merged = first.SerializeToString() + last.SerializeToString() + unpacked + unknown
        reference = schema.Envelope.FromString(merged)
        self.assertEqual((reference.child.left, reference.child.right), (5, 7))
        self.assertEqual(list(reference.sequence), [-1, 0, 1, -2])
        self.assertEqual(reference.WhichOneof("selection"), "code")
        self.assertTrue(reference.HasField("count"))
        self.assertEqual(reference.count, 0)
        self.assertEqual(reference.scores["key"], -6)
        (self.root / "python.bin").write_bytes(merged)
        self.compile_fixture("wire.vqr", "program")
        self.process("./program")
        self.assertEqual(schema.Envelope.FromString((self.root / "vaqrin.bin").read_bytes()), canonical)
        self.assertEqual((self.root / "vaqrin.bin").read_bytes(), canonical.SerializeToString())
        self.assertEqual((self.root / "unknown.bin").read_bytes(), unknown)

    def make_certificate(self):
        cert = self.root / "certificate.pem"
        key = self.root / "key.pem"
        self.process("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                     "-days", "1", "-subj", "/CN=Vaqrin test",
                     "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
                     "-keyout", key, "-out", cert)
        return cert, key

    def test_greeter_example(self):
        project = REPOSITORY / "examples/projects/grpc"
        for role in ("server", "client"):
            # Match the single-file entry used by Zed's run code task.
            self.process(VAQC, "build", project / role / "main.vqr", "-o", role)
        certificate, key = self.make_certificate()
        env = dict(os.environ, VAQRIN_GRPC_CERT=str(certificate), VAQRIN_GRPC_KEY=str(key), VAQRIN_GRPC_PORT="0")
        with subprocess.Popen([self.root / "server"], cwd=self.root, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as server:
            try:
                ready, _, _ = select.select([server.stdout], [], [], 10)
                self.assertTrue(ready, "Greeter server did not report a port")
                port = server.stdout.readline().strip()
                self.assertTrue(port.isdigit(), server.stderr.read() if server.poll() is not None else port)
                self.assertTrue(0 < int(port) <= 65535)
                env["VAQRIN_GRPC_PORT"] = port.decode("ascii")
                result = subprocess.run([self.root / "client"], cwd=self.root, env=env, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, b"Hello, Vaqrin\n")
                _, errors = server.communicate(timeout=10)
                self.assertEqual(server.returncode, 0, errors)
            finally:
                if server.poll() is None:
                    server.kill()

    def test_async_http_stream_and_connection_reuse(self):
        with socket.create_server(("127.0.0.1", 0)) as listener:
            listener.settimeout(5)
            url = json.dumps(f"http://127.0.0.1:{listener.getsockname()[1]}")
            program = self.source("stream.vqr", r"""
                import (http, io)
                class Count {
                    #implements: http.ResponseConsumer
                    pub var total: usize;
                    pub func consume(head: *const http.Response, body: *io.Reader) !void {
                        if head.status().value() != 200 { panic("bad head"); }
                        var buf = [3]u8{0,0,0};
                        for {
                            var got = io.read(body, buf[:])!;
                            if got == 0 { break; }
                            for byte in buf[0..got] { if byte != 120 { panic("bad payload"); } }
                            total += got;
                        }
                    }
                }
                class Payload {
                    #implements: io.Reader
                    pub var remaining: usize;
                    pub func read(dst: []u8) !usize {
                        var count = if remaining < dst.len() { remaining } else { dst.len() };
                        for i in usize(0)..count { dst[i] = 121; }
                        remaining -= count;
                        count
                    }
                }
                func main() !void {
                    var client = http.AsyncClient();
                    var connection = client.connect(URL)!;
                    var first = http.Request.new(http.Method.POST, "/one")!;
                    var count = Count{total: 0};
                    var upload = Payload{remaining: 9005};
                    connection.sendWithBodyStream(&first, &upload, &count)!;
                    io.println(connection.isOpen() && count.total == 5005)!;
                    var second = http.Request.new(http.Method.GET, "/two")!;
                    var response = connection.send(&second)!;
                    io.println(response.status().value() == 204 && !connection.isOpen())!;
                }
            """.replace("URL", url))
            self.process(VAQC, program, "-o", "stream")

            def serve():
                peer, _ = listener.accept()
                with peer, peer.makefile("rb") as requests:
                    peer.settimeout(5)
                    for path in (b"/one", b"/two"):
                        method = b"POST" if path == b"/one" else b"GET"
                        self.assertEqual(requests.readline(), method + b" " + path + b" HTTP/1.1\r\n")
                        headers = {}
                        for line in requests:
                            if line == b"\r\n":
                                break
                            name, value = line.split(b":", 1)
                            headers[name.lower()] = value.strip()
                        if path == b"/one":
                            self.assertEqual(headers[b"transfer-encoding"], b"chunked")
                            for chunk_size in (8192, 813, 0):
                                self.assertEqual(int(requests.readline().strip(), 16), chunk_size)
                                self.assertEqual(requests.read(chunk_size), b"y" * chunk_size)
                                self.assertEqual(requests.read(2), b"\r\n")
                            peer.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
                            for chunk in (b"5\r\nxxxxx\r\n", b"1388\r\n" + b"x" * 5000 + b"\r\n"):
                                peer.sendall(chunk)
                            peer.sendall(b"0\r\nTrailer-Value: done\r\n\r\n")
                        else:
                            peer.sendall(b"HTTP/1.1 204 No Content\r\nConnection: upgrade, Close\r\n\r\n")

            with ThreadPoolExecutor(max_workers=1) as worker:
                served = worker.submit(serve)
                self.assertEqual(self.process("./stream").stdout, b"true\n" * 2)
                served.result(timeout=6)

    def test_async_http_pool_reuses_and_evicts_connections(self):
        with socket.create_server(("127.0.0.1", 0)) as first, \
             socket.create_server(("127.0.0.1", 0)) as second:
            program = self.source("pool.vqr", r"""
                import (http, io, result, time)
                func main() !void {
                    var client = http.AsyncClient();
                    var pool = http.AsyncPool.new(move(client), 1, time.Duration.fromSeconds(15))!;
                    var first = pool.get(URL_ONE)!;
                    var reused = pool.get(URL_TWO)!;
                    var other = pool.get(URL_THREE)!;
                    var reopened = pool.get(URL_FOUR)!;
                    io.println(first.status().value() == 204 && reused.status().value() == 204 &&
                               other.status().value() == 204 && reopened.status().value() == 204)!;
                    pool.close();
                    var closed = pool.get(FIRST);
                    io.println(result.errorOf(&closed).unwrap() == io.IoError.Closed)!;
                }
            """.replace('URL_ONE', json.dumps(f"http://127.0.0.1:{first.getsockname()[1]}/one"))
                .replace('URL_TWO', json.dumps(f"http://127.0.0.1:{first.getsockname()[1]}/two"))
                .replace('URL_THREE', json.dumps(f"http://127.0.0.1:{second.getsockname()[1]}/three"))
                .replace('URL_FOUR', json.dumps(f"http://127.0.0.1:{first.getsockname()[1]}/four"))
                .replace("FIRST", json.dumps(f"http://127.0.0.1:{first.getsockname()[1]}")))
            self.process(VAQC, program, "-o", "pool")

            def serve(listener, connections):
                listener.settimeout(8)
                for paths in connections:
                    peer, _ = listener.accept()
                    peer.settimeout(8)
                    with peer, peer.makefile("rb") as reader:
                        for path in paths:
                            self.assertEqual(reader.readline(), b"GET " + path + b" HTTP/1.1\r\n")
                            while (line := reader.readline()) != b"\r\n":
                                self.assertTrue(line)
                            peer.sendall(b"HTTP/1.1 204 No Content\r\n\r\n")
                        self.assertEqual(reader.read(1), b"")  # eviction or explicit pool close

            with ThreadPoolExecutor(max_workers=2) as executor:
                pending = [executor.submit(serve, first, ((b"/one", b"/two"), (b"/four",))),
                           executor.submit(serve, second, ((b"/three",),))]
                self.assertEqual(self.process("./pool").stdout, b"true\ntrue\n")
                for task in pending:
                    task.result(timeout=9)

    def test_async_http_server_streams_request_body(self):
        program = self.source("upload.vqr", r"""
            import (http, io, net)
            class Upload {
                #implements: http.RequestConsumer
                pub func consume(head: *const http.Request, body: *io.Reader) !http.Response {
                    if head.target() != "/upload" { panic("unexpected target"); }
                    var storage = [2]u8{0, 0};
                    var count = usize(0);
                    for {
                        var got = io.read(body, storage[:])!;
                        if got == 0 { break; }
                        for byte in storage[0..got] {
                            if byte != 120 { panic("unexpected byte"); }
                        }
                        count += got;
                    }
                    if count != 5005 { panic("unexpected length"); }
                    http.Response.new(204)
                }
            }

            func main() !void {
                var server = http.AsyncServer.bind(net.parseSocketAddr("127.0.0.1:0")!)!;
                io.println(server.localAddr()!.port())!;
                var handler = Upload{};
                server.serveOneStreaming(&handler, null)!;
            }
        """)
        self.process(VAQC, program, "-o", "upload")
        with subprocess.Popen([self.root / "upload"], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as server:
            try:
                with ThreadPoolExecutor(max_workers=1) as worker:
                    port = int(worker.submit(server.stdout.readline).result(timeout=5))
                with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
                    client.sendall(b"POST /upload HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n"
                                   b"5\r\nxxxxx\r\n1388\r\n" + b"x" * 5000 + b"\r\n0\r\n\r\n")
                    with client.makefile("rb") as reply:
                        self.assertTrue(reply.readline().startswith(b"HTTP/1.1 204 "))
                        self.assertTrue(reply.read().endswith(b"\r\n\r\n"))
                out, err = server.communicate(timeout=5)
                self.assertEqual(server.returncode, 0, (out, err))
            finally:
                if server.poll() is None:
                    server.kill()

    def test_async_https_one_worker_and_handshake_deadline(self):
        cert, key = self.make_certificate()
        program = self.source("async_https.vqr", r"""
            import (bytes, fmt, http, io, net, result, task, text, time, tls)

            class Echo {
                #implements: http.Handler
                pub func handle(request: http.Request) !http.Response {
                    var reply = http.Response.new(201)!;
                    reply.setBody(http.Body.fromBytes(request.bodyBytes())!);
                    move(reply)
                }
            }

            func url(address: net.SocketAddr) !string {
                var builder = text.StringBuilder();
                builder.append("https://localhost:")!;
                var port = fmt.formatUint(address.port(), 10)!;
                builder.append(port)!;
                builder.append("/echo")!;
                builder.build()
            }

            func main() !void {
                var group = task.TaskGroup.withWorkers(1)!;
                var endpoint = net.parseSocketAddr("127.0.0.1:0")!;
                var listener = http.AsyncServer.bind(endpoint)!;
                var target = url(listener.localAddr()!)!;
                var serve = [move(listener)]() !void => {
                    var config = tls.TlsConfig();
                    var acceptor = tls.TlsAcceptor.fromPem(CERT, KEY, move(config))!;
                    var server = move(listener);
                    var handler = Echo{};
                    var deadline = some(time.Deadline.after(time.Duration.fromSeconds(4))!);
                    server.serveOneTls(&handler, &acceptor, deadline)
                };
                var call = [move(target)]() !bool => {
                    var client = http.AsyncClient();
                    client.setRootFile(CERT)!;
                    var request = http.Body.fromBytes("payload".asBytes())!;
                    var reply = client.post(target, move(request))!;
                    reply.status().value() == 201 && bytes.equal(reply.bodyBytes(), "payload".asBytes())
                };
                var serverJob = group.spawn<void>(move(serve))!;
                var clientJob = group.spawn<bool>(move(call))!;
                io.println(clientJob.join()!)!;
                serverJob.join()!;

                var idle = net.AsyncTcpListener.bind(endpoint)!;
                var idleTarget = url(idle.localAddr()!)!;
                var stall = [move(idle)]() !void => {
                    var listener = move(idle);
                    socket, peer := listener.accept(null)!;
                    task.sleep(time.Duration.fromMillis(300))!;
                };
                var request = [move(idleTarget)]() !bool => {
                    var client = http.AsyncClient();
                    client.setRootFile(CERT)!;
                    client.setTimeout(time.Duration.fromMillis(100));
                    var outcome = client.get(idleTarget);
                    result.errorOf(&outcome).unwrap() == io.IoError.DeadlineExceeded
                };
                var stalled = group.spawn<void>(move(stall))!;
                var timed = group.spawn<bool>(move(request))!;
                io.println(timed.join()!)!;
                stalled.join()!;
            }
        """.replace("CERT", json.dumps(str(cert))).replace("KEY", json.dumps(str(key))))
        self.process(VAQC, program, "-o", "async-https")
        self.assertEqual(self.process("./async-https").stdout, b"true\n" * 2)

    def test_tls_client_alpn(self):
        cert, key = self.make_certificate()
        enabled = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        enabled.minimum_version = ssl.TLSVersion.TLSv1_3
        enabled.load_cert_chain(cert, key)
        enabled.set_alpn_protocols(["h2", "http/1.1"])
        unavailable = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        unavailable.load_cert_chain(cert, key)

        with socket.create_server(("127.0.0.1", 0)) as listener:
            listener.settimeout(5)
            address = json.dumps(f"127.0.0.1:{listener.getsockname()[1]}")
            program = self.source("client.vqr", f"""
                import (bytes, io, net, result, time, tls)
                func dial() !net.TcpStream {{
                    var timeout = time.Duration.fromSeconds(2);
                    var stream = net.TcpStream.connectTimeout(net.parseSocketAddr({address})!, timeout)!;
                    stream.setReadTimeout(some(timeout))!;
                    stream.setWriteTimeout(some(timeout))!;
                    move(stream)
                }}
                func main() !void {{
                    var config = tls.TlsConfig();
                    config.minimumVersion = tls.Version.Tls13;
                    config.requireProtocol = true;
                    config.setRootFile({json.dumps(str(cert))})!;
                    config.addProtocol("h2".asBytes())!;
                    var active = tls.TlsStream.connect(dial()!, "localhost", &config)!;
                    var chosen = active.negotiatedProtocol()!.unwrap();
                    io.println(bytes.equal(chosen.asConstSlice(), "h2".asBytes()))!;
                    io.writeString(&active, "ping")!;
                    var reply = [4]u8{{0, 0, 0, 0}};
                    io.readExact(&active, reply[:])!;
                    io.println(bytes.equal(reply[:], "pong".asBytes()))!;
                    active.close()!;
                    active.close()!;
                    var closed = active.read(reply[:]);
                    io.println(result.errorOf(&closed).unwrap() == io.IoError.Closed)!;
                    var rejected = tls.TlsStream.connect(dial()!, "localhost", &config);
                    io.println(result.errorOf(&rejected).unwrap() == tls.TlsError.MissingProtocol)!;
                    var wrongHost = tls.TlsStream.connect(dial()!, "wrong.example", &config);
                    io.println(result.errorOf(&wrongHost).unwrap() == tls.TlsError.Certificate)!;
                }}
            """)
            self.process(VAQC, program, "-o", "client")

            def serve():
                connection, _ = listener.accept()
                with connection, enabled.wrap_socket(connection, server_side=True) as stream:
                    stream.settimeout(4)
                    self.assertEqual(stream.selected_alpn_protocol(), "h2")
                    with stream.makefile("rb") as data:
                        self.assertEqual(data.read(4), b"ping")
                    stream.sendall(b"pong")
                    stream.unwrap().close()
                connection, _ = listener.accept()
                with connection, unavailable.wrap_socket(connection, server_side=True) as stream:
                    self.assertIsNone(stream.selected_alpn_protocol())
                connection, _ = listener.accept()
                with connection, self.assertRaises(ssl.SSLError):
                    with enabled.wrap_socket(connection, server_side=True):
                        pass

            with ThreadPoolExecutor(max_workers=1) as worker:
                served = worker.submit(serve)
                self.assertEqual(self.process("./client").stdout, b"true\n" * 5)
                served.result(timeout=6)

    def test_tls_server_alpn(self):
        cert, key = self.make_certificate()
        program = self.source("server.vqr", f"""
            import (fmt, io, net, result, time, tls)
            func take(listener: *net.TcpListener) !net.TcpStream {{
                var connection = listener.accept()!;
                connection.0.setReadTimeout(some(time.Duration.fromSeconds(2)))!;
                connection.0.setWriteTimeout(some(time.Duration.fromSeconds(2)))!;
                stream, _ := move(connection);
                move(stream)
            }}
            func main() !void {{
                var listener = net.listenTcp(net.parseSocketAddr("127.0.0.1:0")!)!;
                var digits = fmt.formatUint(listener.localAddr()!.port(), 10)!;
                io.print(digits)!;
                io.print("\\n")!;
                var config = tls.TlsConfig();
                config.addProtocol("h2".asBytes())!;
                config.requireProtocol = true;
                var acceptor = tls.TlsAcceptor.fromPem({json.dumps(str(cert))}, {json.dumps(str(key))}, move(config))!;
                var rejected = acceptor.accept(take(&listener)!);
                io.println(result.errorOf(&rejected).unwrap() == tls.TlsError.Handshake)!;
                var missing = acceptor.accept(take(&listener)!);
                io.println(result.errorOf(&missing).unwrap() == tls.TlsError.MissingProtocol)!;
                var active = acceptor.accept(take(&listener)!)!;
                var chosen = active.negotiatedProtocol()!.unwrap();
                io.writeAll(&active, chosen.asConstSlice())!
            }}
        """)
        self.process(VAQC, program, "-o", "server")
        with subprocess.Popen([self.root / "server"], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as server:
            try:
                with ThreadPoolExecutor(max_workers=1) as worker:
                    port = int(worker.submit(server.stdout.readline).result(timeout=5))
                context = ssl.create_default_context(cafile=str(cert))
                context.set_alpn_protocols(["http/1.1"])
                with socket.create_connection(("127.0.0.1", port), timeout=4) as client:
                    with self.assertRaises(ssl.SSLError):
                        with context.wrap_socket(client, server_hostname="localhost"):
                            pass
                context = ssl.create_default_context(cafile=str(cert))
                with socket.create_connection(("127.0.0.1", port), timeout=4) as client:
                    with context.wrap_socket(client, server_hostname="localhost") as stream:
                        self.assertIsNone(stream.selected_alpn_protocol())
                context.set_alpn_protocols(["http/1.1", "h2"])
                with socket.create_connection(("127.0.0.1", port), timeout=4) as client:
                    with context.wrap_socket(client, server_hostname="localhost") as stream:
                        self.assertEqual(stream.selected_alpn_protocol(), "h2")
                        with stream.makefile("rb") as data:
                            self.assertEqual(data.read(2), b"h2")
                out, err = server.communicate(timeout=5)
                self.assertEqual(server.returncode, 0, err.decode(errors="replace"))
                self.assertEqual(out, b"true\ntrue\n")
            except (OSError, ValueError) as error:
                if server.poll() is None:
                    server.kill()
                out, err = server.communicate(timeout=5)
                raise AssertionError(f"server exited {server.returncode}: {out!r} {err!r}") from error
            finally:
                if server.poll() is None:
                    server.kill()

    def test_http2_client_tls(self):
        from h2.config import H2Configuration
        from h2.connection import H2Connection
        from h2.events import ConnectionTerminated, DataReceived, RequestReceived, StreamEnded

        cert, key = self.make_certificate()
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        tls.set_alpn_protocols(["h2"])
        self.compile_fixture("h2_client.vqr", "client")
        with socket.create_server(("127.0.0.1", 0)) as listener:
            listener.settimeout(5)
            address = f"127.0.0.1:{listener.getsockname()[1]}"

            def serve():
                connection, _ = listener.accept()
                with connection, tls.wrap_socket(connection, server_side=True) as stream:
                    stream.settimeout(5)
                    self.assertEqual(stream.selected_alpn_protocol(), "h2")
                    h2 = H2Connection(H2Configuration(client_side=False, header_encoding="utf-8"))
                    h2.initiate_connection()
                    stream.sendall(h2.data_to_send())
                    preface = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
                    incoming = b""
                    while len(incoming) < len(preface):
                        fragment = stream.recv(len(preface) - len(incoming))
                        self.assertTrue(fragment)
                        incoming += fragment
                    self.assertEqual(incoming, preface)
                    received = {}
                    pending = {}
                    finished = False
                    while not finished:
                        for event in h2.receive_data(incoming):
                            if isinstance(event, RequestReceived):
                                self.assertEqual(dict(event.headers)["x-hpack"], "a-repeated-dynamic-header-value")
                                self.assertEqual(dict(event.headers)[":path"], "/one" if event.stream_id == 1 else "/two")
                                received[event.stream_id] = bytearray()
                            elif isinstance(event, DataReceived):
                                received[event.stream_id].extend(event.data)
                                h2.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                            elif isinstance(event, StreamEnded):
                                h2.send_headers(event.stream_id, [(":status", "200"), ("x-hpack", "a-repeated-dynamic-header-value")])
                                pending[event.stream_id] = b"a" * 90000 if event.stream_id == 1 else b"b" * 111
                            elif isinstance(event, ConnectionTerminated):
                                self.assertEqual(event.error_code, 0)
                                finished = True

                        for stream_id, data in list(pending.items()):
                            while data:
                                size = min(len(data), h2.local_flow_control_window(stream_id), h2.max_outbound_frame_size)
                                if size <= 0:
                                    break
                                h2.send_data(stream_id, data[:size])
                                data = data[size:]
                            if not data:
                                h2.send_headers(stream_id, [("x-end", "done")], end_stream=True)
                                del pending[stream_id]
                            else:
                                pending[stream_id] = data
                        outgoing = h2.data_to_send()
                        if outgoing:
                            stream.sendall(outgoing)
                        if not finished:
                            incoming = stream.recv(8192)
                            self.assertTrue(incoming)
                    self.assertEqual(received, {1: b"A" * 70000, 3: b"two"})

            with ThreadPoolExecutor(max_workers=1) as worker:
                served = worker.submit(serve)
                process = subprocess.run(
                    [self.root / "client"], cwd=self.root, capture_output=True, timeout=18,
                    env={**os.environ, "VAQRIN_H2_ADDRESS": address, "VAQRIN_H2_CERT": str(cert)},
                )
                served.result(timeout=6)
                self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace"))

    def test_http2_server_h2c(self):
        from h2.config import H2Configuration
        from h2.connection import H2Connection
        from h2.events import DataReceived, ResponseReceived, StreamEnded, StreamReset, TrailersReceived

        self.compile_fixture("h2_server.vqr", "server")
        with subprocess.Popen([self.root / "server"], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as server:
            try:
                with ThreadPoolExecutor(max_workers=1) as worker:
                    port = int(worker.submit(server.stdout.readline).result(timeout=5))
                with socket.create_connection(("127.0.0.1", port), timeout=5) as peer:
                    h2 = H2Connection(H2Configuration(client_side=True, header_encoding="utf-8"))
                    h2.initiate_connection()
                    base = [(":method", "POST"), (":scheme", "http"), (":authority", "localhost")]
                    h2.send_headers(1, base + [(":path", "/request")])
                    h2.send_headers(3, base + [(":path", "/reset"), ("x-reset", "yes")])
                    h2.send_headers(5, base + [(":path", "/large"), ("x-large", "a" * 33000)])
                    peer.sendall(h2.data_to_send())
                    left = 82000
                    body = bytearray()
                    headers, trailers = {}, {}
                    ended = reset = over_limit = False
                    while not (ended and reset and over_limit):
                        wire = peer.recv(4096)
                        self.assertTrue(wire)
                        for event in h2.receive_data(wire):
                            if isinstance(event, ResponseReceived):
                                headers.update(event.headers)
                            elif isinstance(event, TrailersReceived):
                                trailers.update(event.headers)
                            elif isinstance(event, DataReceived):
                                body.extend(event.data)
                                h2.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                            elif isinstance(event, StreamEnded):
                                self.assertEqual(event.stream_id, 1)
                                ended = True
                            elif isinstance(event, StreamReset):
                                if event.stream_id == 3:
                                    self.assertEqual(event.error_code, 8)
                                    reset = True
                                else:
                                    self.assertEqual(event.stream_id, 5)
                                    self.assertNotEqual(event.error_code, 0)
                                    over_limit = True
                        while left:
                            size = min(left, h2.local_flow_control_window(1), h2.max_outbound_frame_size)
                            if size <= 0:
                                break
                            left -= size
                            h2.send_data(1, b"x" * size, end_stream=left == 0)
                        output = h2.data_to_send()
                        if output:
                            peer.sendall(output)
                    self.assertEqual(headers, {":status": "200", "x-server": "Vaqrin"})
                    self.assertEqual(trailers, {"x-end": "done"})
                    self.assertEqual(body, b"hello from Vaqrin")
                    h2.close_connection()
                    peer.sendall(h2.data_to_send())
                out, err = server.communicate(timeout=5)
                self.assertEqual(server.returncode, 0, (out, err))
            finally:
                if server.poll() is None:
                    server.kill()

    def test_http2_frame_validation(self):
        self.hosted(r"""
            import (http2, result)
            func main() !void {
                var short = [2]u8{0, 0};
                var rejected = http2.FrameHeader.parse(short[:]);
                io.println(result.errorOf(&rejected).unwrap() == http2.Http2Error.Protocol)!;
                var header = [9]u8{0, 1, 2, 10, 1, 128, 0, 0, 7};
                var frame = http2.FrameHeader.parse(header[:])!;
                io.println(frame.length == 258 && frame.stream == 7 && frame.kind == 10 && frame.flags == 1)!;
                var connection = http2.Connection.open(http2.Role.Server, http2.Settings())!;
                var bad = connection.ingest("invalid HTTP/2 client connection preface".asBytes());
                io.println(result.errorOf(&bad).unwrap() == http2.Http2Error.Protocol)!;
                var invalidOptions = http2.Settings();
                invalidOptions.initialStreamWindow = 0xffffffffu32;
                var badSize = http2.Connection.open(http2.Role.Client, invalidOptions);
                io.println(result.errorOf(&badSize).unwrap() == http2.Http2Error.InvalidArgument)!;
            }
        """, "true\n" * 4)

    def test_grpc_python_server(self):
        import time
        import grpc

        cert, key = self.make_certificate()
        schema = self.reference_schema()
        self.compile_fixture("grpc_client.vqr", "client")

        def twice(request, context):
            self.assertEqual(dict(context.invocation_metadata())["proof-bin"], b"binary\x00")
            self.assertEqual(dict(context.invocation_metadata())["x-request"], "client")
            self.assertIsNotNone(context.time_remaining())
            context.send_initial_metadata((("x-initial", "open"),))
            context.set_trailing_metadata((("proof-bin", b"tail\x00"),))
            return schema.Child(left=request.left * 2)

        def missing(request, context):
            context.set_trailing_metadata((("grpc-status-details-bin", b"details"),))
            context.abort(grpc.StatusCode.NOT_FOUND, "缺失 % field")

        def wait(request, context):
            time.sleep(0.25)
            return schema.Child()

        def unary(callback):
            return grpc.unary_unary_rpc_method_handler(
                callback, request_deserializer=schema.Child.FromString,
                response_serializer=schema.Child.SerializeToString,
            )

        with ThreadPoolExecutor(max_workers=2) as worker:
            server = grpc.server(worker)
            server.add_generic_rpc_handlers((
                grpc.method_handlers_generic_handler("interop.Echo", {
                    "Double": unary(twice), "Missing": unary(missing), "Wait": unary(wait),
                }),
            ))
            credentials = grpc.ssl_server_credentials(((key.read_bytes(), cert.read_bytes()),))
            port = server.add_secure_port("127.0.0.1:0", credentials)
            server.start()
            try:
                process = subprocess.run(
                    [self.root / "client"], cwd=self.root, capture_output=True, timeout=20,
                    env={**os.environ, "VAQRIN_RPC_ADDRESS": f"https://localhost:{port}",
                         "VAQRIN_RPC_CERT": str(cert)},
                )
                self.assertEqual(process.returncode, 0, (process.stdout, process.stderr))
            finally:
                server.stop(0).wait(5)

    def test_grpc_python_client(self):
        import grpc

        cert, key = self.make_certificate()
        schema = self.reference_schema()
        self.compile_fixture("grpc_server.vqr", "server")
        environment = {**os.environ, "VAQRIN_RPC_CERT": str(cert), "VAQRIN_RPC_KEY": str(key)}
        with subprocess.Popen([self.root / "server"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=environment) as server:
            try:
                with ThreadPoolExecutor(max_workers=1) as worker:
                    port = int(worker.submit(server.stdout.readline).result(timeout=5))
                creds = grpc.ssl_channel_credentials(root_certificates=cert.read_bytes())
                with grpc.secure_channel(f"localhost:{port}", creds) as channel:
                    def unary(name):
                        return channel.unary_unary(
                            f"/interop.Echo/{name}", request_serializer=schema.Child.SerializeToString,
                            response_deserializer=schema.Child.FromString,
                        )

                    answer, call = unary("Double").with_call(
                        schema.Child(left=14), timeout=4, metadata=(("proof-bin", b"binary\x00"),),
                    )
                    self.assertEqual(answer.left, 28)
                    self.assertEqual(dict(call.initial_metadata())["x-initial"], "open")
                    self.assertEqual(dict(call.trailing_metadata())["proof-bin"], b"tail\x00")
                    with self.assertRaises(grpc.RpcError) as rejected:
                        unary("Missing")(schema.Child(), timeout=4)
                    error = rejected.exception
                    self.assertEqual(error.code(), grpc.StatusCode.NOT_FOUND)
                    self.assertEqual(error.details(), "缺失 % field")
                    self.assertEqual(dict(error.trailing_metadata())["grpc-status-details-bin"], b"details")
                    with self.assertRaises(grpc.RpcError) as unmatched:
                        unary("Unknown")(schema.Child(), timeout=4)
                    self.assertEqual(unmatched.exception.code(), grpc.StatusCode.UNIMPLEMENTED)
                out, err = server.communicate(timeout=6)
                self.assertEqual(server.returncode, 0, (out, err))
            finally:
                if server.poll() is None:
                    server.kill()

    def test_grpc_vaqrin_to_vaqrin(self):
        cert, key = self.make_certificate()
        self.compile_fixture("grpc_server.vqr", "server")
        self.compile_fixture("grpc_client.vqr", "client")
        environment = {**os.environ, "VAQRIN_RPC_CERT": str(cert), "VAQRIN_RPC_KEY": str(key)}
        with subprocess.Popen([self.root / "server"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=environment) as server:
            try:
                with ThreadPoolExecutor(max_workers=1) as worker:
                    port = int(worker.submit(server.stdout.readline).result(timeout=5))
                process = subprocess.run(
                    [self.root / "client"], capture_output=True, timeout=15,
                    env={**environment, "VAQRIN_RPC_ADDRESS": f"https://localhost:{port}"},
                )
                self.assertEqual(process.returncode, 0, (process.stdout, process.stderr))
                out, err = server.communicate(timeout=6)
                self.assertEqual(server.returncode, 0, (out, err))
            finally:
                if server.poll() is None:
                    server.kill()

    def test_grpc_streaming_python_server(self):
        from threading import Event
        import grpc

        cert, key = self.make_certificate()
        schema = self.reference_schema()
        self.compile_fixture("grpc_stream_client.vqr", "client")
        cancelled = Event()

        def broadcast(request, context):
            for value in range(request.left, request.left + 3):
                yield schema.Child(left=value)

        def sum_messages(requests, context):
            return schema.Child(left=sum(request.left for request in requests))

        def echo(requests, context):
            for request in requests:
                if request.left == 5:
                    context.add_callback(cancelled.set)
                yield schema.Child(left=request.left * 2)

        codec = dict(request_deserializer=schema.Child.FromString, response_serializer=schema.Child.SerializeToString)
        with ThreadPoolExecutor(max_workers=2) as pool:
            server = grpc.server(pool)
            server.add_generic_rpc_handlers((
                grpc.method_handlers_generic_handler("interop.Stream", {
                    "Broadcast": grpc.unary_stream_rpc_method_handler(broadcast, **codec),
                    "Sum": grpc.stream_unary_rpc_method_handler(sum_messages, **codec),
                    "Echo": grpc.stream_stream_rpc_method_handler(echo, **codec),
                }),
            ))
            credentials = grpc.ssl_server_credentials(((key.read_bytes(), cert.read_bytes()),))
            port = server.add_secure_port("127.0.0.1:0", credentials)
            server.start()
            try:
                process = subprocess.run(
                    [self.root / "client"], capture_output=True, timeout=15,
                    env={**os.environ, "VAQRIN_RPC_ADDRESS": f"https://localhost:{port}",
                         "VAQRIN_RPC_CERT": str(cert)},
                )
                self.assertEqual(process.returncode, 0, (process.stdout, process.stderr))
                self.assertTrue(cancelled.wait(3), "the peer did not observe cancellation")
            finally:
                server.stop(0).wait(5)

    def test_grpc_streaming_python_client(self):
        from threading import Event
        import grpc

        cert, key = self.make_certificate()
        schema = self.reference_schema()
        self.compile_fixture("grpc_stream_server.vqr", "server")
        environment = {**os.environ, "VAQRIN_RPC_CERT": str(cert), "VAQRIN_RPC_KEY": str(key)}
        with subprocess.Popen([self.root / "server"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=environment) as server:
            try:
                with ThreadPoolExecutor(max_workers=1) as worker:
                    port = int(worker.submit(server.stdout.readline).result(timeout=5))
                creds = grpc.ssl_channel_credentials(root_certificates=cert.read_bytes())
                with grpc.secure_channel(f"localhost:{port}", creds) as channel:
                    codec = dict(request_serializer=schema.Child.SerializeToString,
                                 response_deserializer=schema.Child.FromString)
                    broadcast = channel.unary_stream("/interop.Stream/Broadcast", **codec)
                    call = broadcast(schema.Child(left=10), timeout=4)
                    self.assertEqual([frame.left for frame in call], [10, 11, 12])
                    self.assertEqual(dict(call.initial_metadata())["x-start"], "vaqrin")
                    self.assertEqual(dict(call.trailing_metadata())["x-end"], "vaqrin")
                    add = channel.stream_unary("/interop.Stream/Sum", **codec)
                    values = (schema.Child(left=value) for value in (2, 3, 4))
                    self.assertEqual(add(values, timeout=4).left, 9)
                    echo = channel.stream_stream("/interop.Stream/Echo", **codec)
                    first_reply = Event()

                    def requests():
                        yield schema.Child(left=15)
                        if not first_reply.wait(3):
                            raise TimeoutError("bidirectional reply did not arrive")
                        yield schema.Child(left=16)

                    call = echo(requests(), timeout=4)
                    self.assertEqual(next(call).left, 30)
                    first_reply.set()
                    self.assertEqual([frame.left for frame in call], [32])
                out, err = server.communicate(timeout=6)
                self.assertEqual(server.returncode, 0, (out, err))
            finally:
                if server.poll() is None:
                    server.kill()

    def test_protoc_vaqrin_plugin(self):
        self.process(sys.executable, "-m", "grpc_tools.protoc",
                     f"-I{FIXTURES}", "--python_out=.", "wire.proto", "typed.proto")
        sys.path.insert(0, str(self.root))
        try:
            spec = importlib.util.spec_from_file_location("typed_pb2", self.root / "typed_pb2.py")
            schema = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(schema)
        finally:
            sys.path.pop(0)
        initial = schema.Input(count=12, numbers=[-1, 0, 40], mode=99, type="keyword", extra=b"")
        initial.scores["key"].left = 100
        initial.raw = b"replaced"
        initial.child.left = 50
        initial.items.add(right=80)
        initial.foreign.left = -1
        initial.branch.count = 3
        middle = schema.Input(count=0)
        middle.scores["key"].left = 200
        middle.nested.left = 3
        final = schema.Input()
        final.nested.right = 4
        # A wrong wire type for field 1 and one unknown fixed32 field remain opaque.
        opaque = bytes.fromhex("0a017fa506ffffffff")
        raw = initial.SerializeToString() + middle.SerializeToString() + final.SerializeToString() + opaque
        (self.root / "generated-input.bin").write_bytes(raw)
        plugin = VAQC.parent / "protoc-gen-vaqrin"
        if not plugin.is_file():
            plugin = REPOSITORY / "compiler/tools/protoc-gen-vaqrin"
        options = (sys.executable, "-m", "grpc_tools.protoc", f"-I{FIXTURES}",
                   f"--plugin=protoc-gen-vaqrin={plugin}")
        self.process(*options, "--vaqrin_out=.", "wire.proto", "typed.proto")
        repeat = self.root / "repeat"
        repeat.mkdir()
        self.process(*options, "--vaqrin_out=repeat", "typed.proto", "wire.proto")
        for subpath in ("pb_interop/wire_pb.vqr", "pb_demo_ddeep/typed_pb.vqr"):
            self.assertEqual((self.root / subpath).read_bytes(), (repeat / subpath).read_bytes())
        greeter = REPOSITORY / "examples/projects/grpc"
        self.process(sys.executable, "-m", "grpc_tools.protoc", f"-I{greeter}",
                     f"--plugin=protoc-gen-vaqrin={plugin}", "--vaqrin_out=.", "greeter.proto")
        binding = "pb_example/greeter_pb.vqr"
        self.assertEqual((self.root / binding).read_bytes(), (greeter / binding).read_bytes())
        test = self.source("main.vqr", (FIXTURES / "typed.vqr").read_text(encoding="utf-8"))
        self.process(VAQC, test, "--package-path", self.root, "-o", "typed")
        self.process("./typed")
        output = (self.root / "generated-output.bin").read_bytes()
        self.assertEqual(schema.Input.FromString(output), schema.Input.FromString(raw))
        self.assertTrue(output.endswith(opaque))

        cert, key = self.make_certificate()
        environment = {**os.environ, "VAQRIN_RPC_CERT": str(cert), "VAQRIN_RPC_KEY": str(key)}
        executable = self.root / "typed"
        with subprocess.Popen([executable], env={**environment, "VAQRIN_RPC_SERVER": "1"},
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as server:
            try:
                with ThreadPoolExecutor(max_workers=1) as worker:
                    port = int(worker.submit(server.stdout.readline).result(timeout=5))
                process = subprocess.run(
                    [executable], capture_output=True, timeout=15,
                    env={**environment, "VAQRIN_RPC_ADDRESS": f"https://localhost:{port}"},
                )
                self.assertEqual(process.returncode, 0, (process.stdout, process.stderr))
                out, err = server.communicate(timeout=5)
                self.assertEqual(server.returncode, 0, (out, err))
            finally:
                if server.poll() is None:
                    server.kill()

    def test_generated_concurrent_service_interoperates_with_grpcio(self):
        import grpc
        from google.protobuf import descriptor_pool, message_factory
        from google.protobuf.descriptor_pb2 import FileDescriptorSet

        plugin = VAQC.parent / "protoc-gen-vaqrin"
        if not plugin.is_file():
            plugin = REPOSITORY / "compiler/tools/protoc-gen-vaqrin"
        self.process(sys.executable, "-m", "grpc_tools.protoc", f"-I{FIXTURES}",
                     f"--plugin=protoc-gen-vaqrin={plugin}", "--vaqrin_out=.",
                     "--descriptor_set_out=descriptors.pb", "--include_imports",
                     "wire.proto", "typed.proto")
        descriptors = FileDescriptorSet.FromString((self.root / "descriptors.pb").read_bytes())
        pool = descriptor_pool.DescriptorPool()
        for file in descriptors.file:
            pool.Add(file)
        request = message_factory.GetMessageClass(pool.FindMessageTypeByName("demo.deep.Input"))
        child = message_factory.GetMessageClass(pool.FindMessageTypeByName("interop.Child"))
        source = self.source("main.vqr", (FIXTURES / "typed_concurrent.vqr").read_text(encoding="utf-8"))
        self.process(VAQC, source, "--package-path", self.root, "-o", "generated-concurrent")
        with subprocess.Popen([self.root / "generated-concurrent"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as server, ThreadPoolExecutor(max_workers=1) as worker:
            def line():
                return worker.submit(server.stdout.readline).result(timeout=5)

            try:
                port = int(line())
                with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                    serialize = request.SerializeToString
                    mirror = channel.stream_stream("/demo.deep.Echo/Mirror",
                                                   request_serializer=serialize,
                                                   response_deserializer=child.FromString)
                    double = channel.unary_unary("/demo.deep.Echo/Double",
                                                 request_serializer=serialize,
                                                 response_deserializer=child.FromString)
                    requests = Queue()
                    requests.put(request(count=12))
                    streamed = mirror(iter(requests.get, None), timeout=5)
                    try:
                        self.assertEqual(next(streamed).left, 12)
                        self.assertEqual(double(request(count=20), timeout=2).left, 40)
                        self.assertTrue(streamed.cancel())
                        self.assertEqual(line(), b"typed call cancelled\n")
                        self.assertEqual([item.left for item in mirror(iter([request(count=3), request(count=5)]),
                                                                       timeout=2)], [3, 5])
                    finally:
                        requests.put(None)
                        streamed.cancel()
                stdout, stderr = server.communicate(timeout=5)
                self.assertEqual(server.returncode, 0, (stdout, stderr))
            finally:
                if server.poll() is None:
                    server.kill()

    def test_grpc_concurrent_unary_calls_and_cancellation(self):
        self.compile_fixture("grpc_concurrent.vqr", "concurrent")
        self.check_grpc_concurrent()
        self.check_grpc_concurrent(self.make_certificate())

    def test_grpc_concurrent_bidi_with_one_worker(self):
        self.compile_fixture("grpc_concurrent.vqr", "concurrent")
        self.check_grpc_concurrent(check=self.check_streams)
        self.check_grpc_concurrent(self.make_certificate(), check=self.check_streams)

    def check_grpc_concurrent(self, certificate=None, check=None, short_drain=False):
        import grpc

        environment = os.environ.copy()
        environment.pop("VAQRIN_RPC_CERT", None)
        environment.pop("VAQRIN_RPC_SHORT_DRAIN", None)
        if short_drain:
            environment["VAQRIN_RPC_SHORT_DRAIN"] = "1"
        if certificate:
            cert, key = certificate
            environment.update(VAQRIN_RPC_CERT=str(cert), VAQRIN_RPC_KEY=str(key))
        with subprocess.Popen([self.root / "concurrent"], stdout=subprocess.PIPE, env=environment,
                              stderr=subprocess.PIPE) as server, ThreadPoolExecutor(max_workers=1) as reader:
            def read_line():
                return reader.submit(server.stdout.readline).result(timeout=6)

            try:
                port = int(read_line())
                endpoint = f"127.0.0.1:{port}"
                if certificate:
                    credentials = grpc.ssl_channel_credentials(root_certificates=cert.read_bytes())
                    connection = grpc.secure_channel(endpoint, credentials)
                else:
                    connection = grpc.insecure_channel(endpoint)
                with connection as channel:
                    (check or self.check_unary_calls)(channel, read_line)
                out, err = server.communicate(timeout=6)
                self.assertEqual(server.returncode, int(short_drain), (out, err))
                if short_drain:
                    self.assertIn(b"DeadlineExceeded", err)
            finally:
                if server.poll() is None:
                    server.kill()

    def check_unary_calls(self, channel, read_line):
        import grpc

        wait = channel.unary_unary("/interop.Concurrent/Wait")
        fast = channel.unary_unary("/interop.Concurrent/Fast")
        slow = wait.future(b"slow", timeout=5)
        self.assertEqual(read_line(), b"waiting\n")
        response, call = fast.with_call(b"fast", timeout=1)
        self.assertEqual(response, b"fast")
        self.assertEqual(dict(call.initial_metadata())["x-worker"], "ready")
        self.assertFalse(slow.done())
        self.assertTrue(slow.cancel())
        self.assertEqual(read_line(), b"cancelled\n")

        slow = wait.future(b"deadline", timeout=0.4)
        self.assertEqual(read_line(), b"waiting\n")
        with self.assertRaises(grpc.RpcError) as deadline:
            slow.result()
        self.assertEqual(deadline.exception.code(), grpc.StatusCode.DEADLINE_EXCEEDED)
        self.assertEqual(read_line(), b"cancelled\n")
        self.assertEqual(fast(b"reused", timeout=2), b"reused")


    def check_streams(self, channel, read_line):
        import grpc

        echo = channel.stream_stream("/interop.Concurrent/Echo")
        fast = channel.unary_unary("/interop.Concurrent/Fast")
        requests = Queue()
        requests.put(b"first")
        call = echo(iter(requests.get, None), timeout=8)
        try:
            self.assertEqual(next(call), b"first")
            self.assertEqual(dict(call.initial_metadata())["x-stream"], "ready")
            self.assertEqual(fast(b"same connection", timeout=1), b"same connection")
            large = bytes(range(256)) * 640
            other = echo(iter([large, b"last"]), timeout=4)
            self.assertEqual(list(other), [large, b"last"])
            self.assertEqual(dict(other.trailing_metadata())["x-stream-end"], "received")
            compressed = echo(iter([b"compressible" * 160]), timeout=3, compression=grpc.Compression.Gzip)
            self.assertEqual(list(compressed), [b"compressible" * 160])
            self.assertTrue(call.cancel())
            with self.assertRaises(grpc.RpcError) as cancelled:
                next(call)
            self.assertEqual(cancelled.exception.code(), grpc.StatusCode.CANCELLED)
            self.assertEqual(read_line(), b"stream cancelled\n")
            self.assertEqual(fast(b"after cancellation", timeout=2), b"after cancellation")
        finally:
            requests.put(None)
            call.cancel()

    def test_grpc_graceful_shutdown_drains_active_streams(self):
        self.compile_fixture("grpc_concurrent.vqr", "concurrent")
        self.check_grpc_concurrent(check=self.check_graceful_shutdown)
        self.check_grpc_concurrent(self.make_certificate(), check=self.check_graceful_shutdown)

    def check_graceful_shutdown(self, channel, read_line):
        import grpc

        echo = channel.stream_stream("/interop.Concurrent/Echo")
        stop = channel.unary_unary("/interop.Concurrent/Stop")
        requests = Queue()
        requests.put(b"first")
        stream = echo(iter(requests.get, None), timeout=6)
        try:
            self.assertEqual(next(stream), b"first")
            self.assertEqual(stop(b"stop", timeout=2), b"stop")
            requests.put(b"last")
            requests.put(None)
            self.assertEqual(list(stream), [b"last"])
            self.assertEqual(stream.code(), grpc.StatusCode.OK)
        finally:
            requests.put(None)
            stream.cancel()

    def test_grpc_graceful_shutdown_is_bounded(self):
        self.compile_fixture("grpc_concurrent.vqr", "concurrent")

        def expired(channel, read_line):
            import grpc

            echo = channel.stream_stream("/interop.Concurrent/Echo")
            stop = channel.unary_unary("/interop.Concurrent/Stop")
            requests = Queue()
            requests.put(b"first")
            stream = echo(iter(requests.get, None), timeout=5)
            try:
                self.assertEqual(next(stream), b"first")
                self.assertEqual(stop(b"stop", timeout=2), b"stop")
                with self.assertRaises(grpc.RpcError) as drained:
                    next(stream)
                self.assertEqual(drained.exception.code(), grpc.StatusCode.UNAVAILABLE)
                self.assertEqual(read_line(), b"stream cancelled\n")
            finally:
                requests.put(None)
                stream.cancel()

        self.check_grpc_concurrent(check=expired, short_drain=True)

    def test_grpc_concurrent_bidi_respects_stream_flow_control(self):
        from h2.connection import H2Connection
        from h2.events import DataReceived, ResponseReceived, StreamEnded, TrailersReceived
        from h2.settings import SettingCodes

        self.compile_fixture("grpc_concurrent.vqr", "concurrent")
        env = os.environ.copy()
        env.pop("VAQRIN_RPC_CERT", None)
        server = subprocess.Popen([self.root / "concurrent"], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env)
        with server, ThreadPoolExecutor(max_workers=1) as reader:
            def read_stdout():
                return reader.submit(server.stdout.readline).result(timeout=6)

            try:
                endpoint = ("127.0.0.1", int(read_stdout()))
                with socket.create_connection(endpoint, timeout=3) as wire:
                    connection = H2Connection()
                    connection.initiate_connection()
                    connection.update_settings({SettingCodes.INITIAL_WINDOW_SIZE: 1024})

                    def start(stream, method, data):
                        connection.send_headers(stream, [
                            (":method", "POST"), (":scheme", "http"),
                            (":authority", "localhost"), (":path", f"/interop.Concurrent/{method}"),
                            ("content-type", "application/grpc"), ("te", "trailers"),
                        ])
                        connection.send_data(stream, b"\x00" + len(data).to_bytes(4, "big") + data,
                                             end_stream=True)

                    start(1, "Echo", b"pressure")
                    start(3, "Fast", b"still writable")
                    wire.sendall(connection.data_to_send())
                    received = {1: bytearray(), 3: bytearray()}
                    ended = set()
                    status = {}

                    def read_events():
                        data = wire.recv(16384)
                        self.assertTrue(data)
                        for event in connection.receive_data(data):
                            if isinstance(event, DataReceived):
                                received[event.stream_id].extend(event.data)
                            if isinstance(event, (ResponseReceived, TrailersReceived)):
                                code = dict(event.headers).get(b"grpc-status")
                                if code is not None:
                                    status[event.stream_id] = code
                            if isinstance(event, StreamEnded):
                                ended.add(event.stream_id)
                        wire.sendall(connection.data_to_send())

                    while len(received[1]) < 1024 or 3 not in ended:
                        read_events()
                    self.assertEqual(len(received[1]), 1024)
                    self.assertNotIn(1, ended)
                    self.assertEqual(received[3], b"\x00\x00\x00\x00\x0estill writable")
                    connection.increment_flow_control_window(131072, stream_id=1)
                    connection.increment_flow_control_window(131072)
                    wire.sendall(connection.data_to_send())
                    while 1 not in ended:
                        read_events()
                    self.assertEqual(received[1], b"\x00\x00\x02\x00\x00" + bytes(131072))
                    self.assertEqual(read_stdout(), b"pressure drained\n")
                    self.assertEqual(status, {1: b"0", 3: b"0"})

                    connection.send_headers(5, [
                        (":method", "POST"), (":scheme", "http"),
                        (":authority", "localhost"), (":path", "/interop.Concurrent/Echo"),
                        ("content-type", "application/grpc"), ("te", "trailers"),
                        ("grpc-timeout", "350m"),
                    ])
                    wire.sendall(connection.data_to_send())
                    while 5 not in ended:
                        read_events()
                    self.assertEqual(status[5], b"4")
                    self.assertEqual(read_stdout(), b"stream cancelled\n")

                _, err = server.communicate(timeout=6)
                self.assertEqual(server.returncode, 0, err)
            finally:
                if server.poll() is None:
                    server.kill()

    def test_grpc_gzip_python_interoperability(self):
        import grpc

        self.compile_fixture("grpc_gzip.vqr", "echo")
        with ThreadPoolExecutor(max_workers=2) as worker:
            server = grpc.server(worker, compression=grpc.Compression.Gzip)
            server.add_generic_rpc_handlers((
                grpc.method_handlers_generic_handler("interop.Raw", {
                    "Echo": grpc.unary_unary_rpc_method_handler(lambda request, context: request),
                }),
            ))
            port = server.add_insecure_port("127.0.0.1:0")
            server.start()
            try:
                process = subprocess.run(
                    [self.root / "echo"], capture_output=True, timeout=10,
                    env={**os.environ, "VAQRIN_RPC_ADDRESS": f"http://127.0.0.1:{port}"},
                )
                self.assertEqual(process.returncode, 0, (process.stdout, process.stderr))
            finally:
                server.stop(0).wait(5)

        environment = {k: v for k, v in os.environ.items() if k != "VAQRIN_RPC_ADDRESS"}
        with subprocess.Popen([self.root / "echo"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=environment) as process:
            try:
                with ThreadPoolExecutor(max_workers=1) as worker:
                    port = int(worker.submit(process.stdout.readline).result(timeout=5))
                with grpc.insecure_channel(f"127.0.0.1:{port}", compression=grpc.Compression.Gzip) as channel:
                    request = b"b" * 8192
                    self.assertEqual(channel.unary_unary("/interop.Raw/Echo")(request, timeout=5), request)
                out, err = process.communicate(timeout=6)
                self.assertEqual(process.returncode, 0, (out, err))
            finally:
                if process.poll() is None:
                    process.kill()

    def test_grpc_gzip_malformed_and_bounded_messages(self):
        import gzip

        encoded = gzip.compress(b"a" * 32768, mtime=0)
        corrupt = encoded[:-2] + b"!!"
        def vector(data):
            encoded_frame = bytes([1]) + len(data).to_bytes(4, "big") + data
            return ", ".join(str(byte) for byte in encoded_frame)
        self.hosted(f"""
            import (grpc, result)
            func main() !void {{
                var wire = [_]u8{{{vector(encoded)}}};
                var bounded = grpc.MessageReader(64);
                bounded.useCompression(grpc.Compression.Gzip);
                bounded.feed(wire[:])!;
                var oversized = bounded.next();
                io.println(result.errorOf(&oversized).unwrap() == grpc.RpcError.MessageTooLarge)!;

                var broken = grpc.MessageReader(32768);
                broken.useCompression(grpc.Compression.Gzip);
                var corrupt = [_]u8{{{vector(corrupt)}}};
                broken.feed(corrupt[:])!;
                var malformed = broken.next();
                io.println(result.errorOf(&malformed).unwrap() == grpc.RpcError.Protocol)!;

                var full = grpc.MessageReader(32768);
                full.useCompression(grpc.Compression.Gzip);
                full.feed(wire[:])!;
                var decoded = full.next()!.unwrap();
                io.println(decoded.len() == 32768 && decoded[32767] == 97)!;
                full.finish()!;
            }}
        """, "true\n" * 3)

    def test_grpc_message_boundaries(self):
        self.hosted(r"""
            import (bytes, grpc, result)
            func main() !void {
                var first = grpc.frame("a".asBytes(), 10)!;
                var second = grpc.frame("bc".asBytes(), 10)!;
                var small = first.asConstSlice();
                var reader = grpc.MessageReader(10);
                reader.feed(small[0..2])!;
                io.println(reader.next()! == null)!;
                var packed = bytes.join([_] []const u8 {small[2..small.len()], second.asConstSlice()}[:], "".asBytes())!;
                reader.feed(packed.asConstSlice())!;
                var a = reader.next()!.unwrap();
                var b = reader.next()!.unwrap();
                io.println(bytes.equal(a.asConstSlice(), "a".asBytes()))!;
                io.println(bytes.equal(b.asConstSlice(), "bc".asBytes()))!;
                reader.finish()!;
                var invalid = [5]u8{1, 0, 0, 0, 0};
                reader.feed(invalid[:])!;
                var unsupported = reader.next();
                io.println(result.errorOf(&unsupported).unwrap() == grpc.RpcError.UnsupportedEncoding)!;
                var size = [5]u8{0, 0, 0, 0, 11};
                var bounded = grpc.MessageReader(10);
                bounded.feed(size[:])!;
                var tooLong = bounded.next();
                io.println(result.errorOf(&tooLong).unwrap() == grpc.RpcError.MessageTooLarge)!;
            }
        """, "true\n" * 5)


if __name__ == "__main__":
    unittest.main()
