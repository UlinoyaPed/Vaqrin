"""Loopback and reference implementation checks for the protocol packages."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
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

    def test_protobuf_reference(self):
        self.process(sys.executable, "-m", "grpc_tools.protoc",
                     f"-I{FIXTURES}", "--python_out=.", FIXTURES / "wire.proto")
        spec = importlib.util.spec_from_file_location("wire_pb2", self.root / "wire_pb2.py")
        schema = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(schema)
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


if __name__ == "__main__":
    unittest.main()
