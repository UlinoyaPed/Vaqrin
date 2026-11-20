"""Loopback and reference implementation checks for the protocol packages."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import socket
import ssl
import subprocess
import sys
import unittest

from acceptance import CompilerCase, REPOSITORY, VAQC


FIXTURES = REPOSITORY / "compiler/tests/fixtures/stack"


class ProtocolAcceptance(CompilerCase):
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
        self.process(VAQC, FIXTURES / "wire.vqr", "-o", "program")
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


if __name__ == "__main__":
    unittest.main()
