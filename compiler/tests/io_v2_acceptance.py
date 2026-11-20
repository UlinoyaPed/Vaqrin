"""Run I/O v2 failure injection, file identity, and resumable I/O cases."""
import errno
import os
import resource
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor

from acceptance import CompilerCase, VAQC


class IoV2Acceptance(CompilerCase):
    def test_copy_uses_open_file_identity_and_reports_failures(self):
        original = self.root / "source"
        original.write_bytes(b"binary\x00payload")
        os.link(original, self.root / "hard")
        (self.root / "soft").symlink_to("source")
        (self.root / "missing-link").symlink_to("absent")
        (self.root / "loop-a").symlink_to("loop-b")
        (self.root / "loop-b").symlink_to("loop-a")
        (self.root / "target").write_bytes(b"x" * 64)
        (self.root / "empty").touch()
        denied = self.root / "denied"
        denied.mkdir()
        (denied / "file").write_bytes(b"hidden")
        denied.chmod(0)
        try:
            self.hosted(f"""
                import (fs, result)
                func main() !void {{
                    var aliases = [3]str{{"source", "hard", "soft"}};
                    for alias in aliases[:] {{
                        var copy = fs.copy("source", alias);
                        io.println(result.errorOf(&copy).unwrap() == fs.FsError.SameFile)!;
                        io.println(fs.sameFile("source", alias)!)!;
                    }}
                    io.println(fs.copy("source", "target")! == 14)!;
                    io.println(fs.copy("empty", "new-empty")! == 0)!;
                    io.println(!fs.sameFile("source", "target")!)!;
                    io.println(!fs.tryExists("missing-link")!)!;
                    io.println(!fs.tryExists("source/absent")!)!;
                    var loop = fs.tryExists("loop-a");
                    io.println(result.errorOf(&loop).unwrap() == io.systemError({errno.ELOOP}))!;
                    var denied = fs.tryExists("denied/file");
                    io.println(result.errorOf(&denied).unwrap() == io.systemError({errno.EACCES}))!;
                    var permission = fs.copy("source", "denied/rejected");
                    io.println(result.errorOf(&permission).unwrap() == io.systemError({errno.EACCES}))!;
                    var disk = fs.copy("source", "/dev/full");
                    io.println(result.errorOf(&disk).unwrap() == io.systemError({errno.ENOSPC}))!;
                }}
            """, "true\n" * 15)
        finally:
            denied.chmod(0o700)
        self.assertEqual(original.read_bytes(), b"binary\x00payload")
        self.assertEqual((self.root / "target").read_bytes(), original.read_bytes())
        self.assertEqual((self.root / "new-empty").read_bytes(), b"")
        self.assertFalse((denied / "rejected").exists())

    def test_progress_and_resumption(self):
        self.hosted(r"""
            import (bytes, result)

            class FaultyWriter {
                #implements: io.Writer
                pub var sink: io.MemoryWriter;
                pub var calls: i32;
                pub func write(src: []const u8) !usize {
                    calls++;
                    switch calls {
                        1 => sink.write(src[0..2]),
                        2 => io.IoError.WouldBlock,
                        3 => io.IoError.Interrupted,
                        4 => io.IoError.Cancelled,
                        5 => io.IoError.DeadlineExceeded,
                        6 => io.systemError(28),
                        _ => sink.write(src),
                    }
                }
                pub func flush() !void {}
            }

            class FaultyReader {
                #implements: io.Reader
                pub var source: io.MemoryReader;
                pub var calls: i32;
                pub func read(dst: []u8) !usize {
                    calls++;
                    if calls == 1 { return source.read(dst[0..2]); }
                    if calls == 2 { return io.IoError.Cancelled; }
                    source.read(dst)
                }
            }

            func main() !void {
                var writer = FaultyWriter{sink: io.MemoryWriter(), calls: 0};
                var data = "payload".asBytes();
                var sent: usize = 0;
                var first = io.writeAll(&writer, data, &sent);
                io.println(sent == 2 && result.errorOf(&first).unwrap() == io.IoError.WouldBlock)!;
                var second = io.writeAll(&writer, data, &sent);
                io.println(sent == 2 && result.errorOf(&second).unwrap() == io.IoError.Interrupted)!;
                var cancelled = io.writeAll(&writer, data, &sent);
                io.println(sent == 2 && result.errorOf(&cancelled).unwrap() == io.IoError.Cancelled)!;
                var timeout = io.writeAllProgress(&writer, data[sent..data.len()])!;
                io.println(timeout.processed == 0 && timeout.status == io.ReadStatus.DeadlineExceeded)!;
                var disk = io.writeAll(&writer, data, &sent);
                io.println(sent == 2 && result.errorOf(&disk).unwrap() == io.systemError(28))!;
                io.writeAll(&writer, data, &sent)!;
                io.println(sent == 7 && bytes.equal(writer.sink.asSlice(), data))!;

                var reader = FaultyReader{source: io.MemoryReader(data), calls: 0};
                var dst = [7]u8{0, 0, 0, 0, 0, 0, 0};
                var run = io.readExactProgress(&reader, dst[:])!;
                io.println(run.processed == 2 && run.status == io.ReadStatus.Cancelled)!;
                var received = run.processed;
                io.readExact(&reader, dst[:], &received)!;
                io.println(received == 7 && bytes.equal(dst[:], data))!;
                var eof = io.readExactProgress(&reader, dst[:])!;
                io.println(eof.processed == 0 && eof.status == io.ReadStatus.Eof)!;
            }
        """, "true\n" * 9)

    def test_copy_resumes_its_pending_scratch(self):
        self.hosted(r"""
            import (bytes, result)

            class Sink {
                #implements: io.Writer
                pub var output: io.MemoryWriter;
                pub var attempts: i32;
                pub func write(data: []const u8) !usize {
                    attempts++;
                    if attempts == 1 { return output.write(data[0..2]); }
                    if attempts == 2 { return io.IoError.Cancelled; }
                    output.write(data)
                }
                pub func flush() !void {}
            }

            func main() !void {
                var data = "abcdefghi".asBytes();
                var reader = io.MemoryReader(data);
                var writer = Sink{output: io.MemoryWriter(), attempts: 0};
                var scratch = [5]u8{0, 0, 0, 0, 0};
                var cursor = io.CopyCursor();
                var stopped = cursor.transfer(&reader, &writer, 9, scratch[:]);
                io.println(result.errorOf(&stopped).unwrap() == io.IoError.Cancelled)!;
                io.println(cursor.copied() == 2 && cursor.pending() == 3 && reader.remaining() == 4)!;
                var total = cursor.transfer(&reader, &writer, 9, scratch[:])!;
                io.println(total == 9 && bytes.equal(writer.output.asSlice(), data))!;
            }
        """, "true\n" * 3)

    def test_finish_retries_pending_suffix_and_seals_writer(self):
        self.hosted(r"""
            import (bufio, bytes, result)

            class Sink {
                #implements: io.Writer
                pub var memory: io.MemoryWriter;
                pub var writes: i32;
                pub var flushes: i32;
                pub func write(src: []const u8) !usize {
                    writes++;
                    if writes == 1 { return memory.write(src[0..2]); }
                    if writes == 2 { return io.IoError.WouldBlock; }
                    memory.write(src)
                }
                pub func flush() !void {
                    flushes++;
                    if flushes == 1 { return io.IoError.WriteFailed; }
                }
            }

            func main() !void {
                var target = Sink{memory: io.MemoryWriter(), writes: 0, flushes: 0};
                var writer = bufio.BufferedWriter(&target, 8)!;
                io.writeString(&writer, "payload")!;
                var buffered = writer.finish();
                io.println(writer.buffered() == 5 && result.errorOf(&buffered).unwrap() == io.IoError.WouldBlock)!;
                var failed = writer.finish();
                io.println(writer.buffered() == 0 && result.errorOf(&failed).unwrap() == io.IoError.WriteFailed)!;
                writer.finish()!;
                writer.finish()!;
                io.println(target.flushes == 2 && bytes.equal(target.memory.asSlice(), "payload".asBytes()))!;
                var closed = writer.write("!".asBytes());
                io.println(result.errorOf(&closed).unwrap() == io.IoError.Closed)!;
                var replacement = io.MemoryWriter();
                writer.reset(&replacement)!;
                io.writeString(&writer, "next")!;
                writer.finish()!;
                io.println(bytes.equal(replacement.asSlice(), "next".asBytes()))!;
            }
        """, "true\n" * 5)

    def test_reactor_update_timeout_fd_reuse_and_close(self):
        first_read, first_write = os.pipe()
        next_read, next_write = os.pipe()
        try:
            os.write(first_write, b"x")
            source = self.source("main.vqr", f"""
                import (io, reactor, result, time)
                extern "C" func dup2(source: i32, target: i32) i32;
                extern "C" func write(fd: i32, data: *const u8, length: usize) isize;
                func main() !void {{
                    var r = reactor.Reactor.open()!;
                    var old = r.register({first_read}, reactor.Interest.read())!;
                    io.println(unsafe {{ dup2({next_read}, {first_read}) }} == {first_read})!;
                    var replacement = r.register({first_read}, reactor.Interest.read())!;
                    var event = r.wait(some(time.Duration.zero()))!.unwrap();
                    io.println(event.watch.value == old.value && event.readable)!;
                    r.remove(old)!;
                    var missing = r.update(old, reactor.Interest.read());
                    io.println(result.errorOf(&missing).unwrap() == reactor.ReactorError.UnknownWatch)!;
                    io.println(r.wait(some(time.Duration.fromMillis(10)))! == null)!;
                    var writer = r.register({next_write}, reactor.Interest.write())!;
                    var ready = r.wait(some(time.Duration.zero()))!.unwrap();
                    io.println(ready.watch.value == writer.value && ready.writable)!;
                    r.update(writer, reactor.Interest.read())!;
                    var byte: u8 = 65;
                    io.println(unsafe {{ write({next_write}, &byte, 1) }} == 1)!;
                    var next = r.wait(some(time.Duration.fromMillis(50)))!.unwrap();
                    io.println(next.watch.value == replacement.value && next.readable)!;
                    r.close();
                    r.close();
                    var closed = r.wait(null);
                    io.println(result.errorOf(&closed).unwrap() == io.IoError.Closed)!;

                    for iteration in 0..256 {{
                        var cleanup = reactor.Reactor.open()!;
                        cleanup.register({next_read}, reactor.Interest.read())!;
                    }}
                }}
            """)
            self.process(VAQC, source, "-o", "probe")
            proc = subprocess.run(
                [self.root / "probe"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
                pass_fds=(first_read, next_read, next_write),
                preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64)),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, b"true\n" * 8)
        finally:
            for fd in (first_read, first_write, next_read, next_write):
                os.close(fd)

    def test_async_tcp_one_worker_and_deadlines(self):
        self.hosted(r"""
            import (bytes, coll, net, result, task, time)

            func main() !void {
                var listener = net.AsyncTcpListener.bind(net.parseSocketAddr("127.0.0.1:0")!)!;
                var address = listener.localAddr()!;
                var group = task.TaskGroup.withWorkers(1)!;
                var serve = [move(listener)] () !i32 => {
                    var count: i32 = 0;
                    for index in 0..32 {
                        var deadline = time.Deadline.after(time.Duration.fromSeconds(4))!;
                        socket, _ := listener.accept(some(deadline))!;
                        socket.setReadDeadline(some(deadline));
                        var data = [4]u8{0, 0, 0, 0};
                        io.readExact(&socket, data[:])!;
                        if bytes.equal(data[:], "ping".asBytes()) { count++; }
                        io.writeString(&socket, "pong")!;
                        socket.shutdownWrite()!;
                    }
                    count
                };
                var server = group.spawn<i32>(move(serve))!;
                var calls = coll.Vec<task.Task<bool>>();
                for index in 0..32 {
                    var call = [address] () !bool => {
                        var deadline = time.Deadline.after(time.Duration.fromSeconds(4))!;
                        var socket = net.AsyncTcpStream.connect(address, some(deadline))!;
                        socket.setReadDeadline(some(deadline));
                        socket.setWriteDeadline(some(deadline));
                        io.writeString(&socket, "ping")!;
                        var result = [4]u8{0, 0, 0, 0};
                        io.readExact(&socket, result[:])!;
                        if !bytes.equal(result[:], "pong".asBytes()) { return false; }
                        socket.read(result[:])! == 0
                    };
                    calls.push(group.spawn<bool>(move(call))!)!;
                }
                var count: i32 = 0;
                for calls.len() != 0 {
                    var current = calls.pop().unwrap();
                    if current.join()! { count++; }
                }
                io.println(count == 32 && server.join()! == 32)!;

                var waiting = net.AsyncTcpListener.bind(net.parseSocketAddr("127.0.0.1:0")!)!;
                var timeout = [move(waiting)] () !bool => {
                    var deadline = time.Deadline.after(time.Duration.fromMillis(40))!;
                    var expired = waiting.accept(some(deadline));
                    result.errorOf(&expired).unwrap() == io.IoError.DeadlineExceeded
                };
                var timer = group.spawn<bool>(move(timeout))!;
                io.println(timer.join()!)!;

                var outside = net.AsyncTcpListener.bind(net.parseSocketAddr("127.0.0.1:0")!)!;
                var deadline = time.Deadline.after(time.Duration.fromMillis(10))!;
                var expired = outside.accept(some(deadline));
                io.println(result.errorOf(&expired).unwrap() == io.IoError.DeadlineExceeded)!;
            }
        """, "true\n" * 3)

    def test_async_tcp_idle_connections_cancel_without_fd_leaks(self):
        source = self.source("main.vqr", r"""
            import (coll, io, net, result, task, time)

            func round() !i32 {
                var listener = net.AsyncTcpListener.bind(net.parseSocketAddr("127.0.0.1:0")!)!;
                var address = listener.localAddr()!;
                var group = task.TaskGroup.withWorkers(1)!;
                var accept = [move(listener)] () !void => {
                    var peers = coll.Vec<net.AsyncTcpStream>();
                    for index in 0..64 {
                        var deadline = time.Deadline.after(time.Duration.fromSeconds(5))!;
                        peer, _ := listener.accept(some(deadline))!;
                        var marker = [1]u8{0};
                        peer.setReadDeadline(some(deadline));
                        io.readExact(&peer, marker[:])!;
                        peers.push(move(peer))!;
                    }
                    io.println("ready")!;
                    task.sleep(time.Duration.fromSeconds(20)) catch (reason) {
                        if reason != task.TaskError.Cancelled { return reason; }
                    };
                };
                var server = group.spawn<void>(move(accept))!;
                var clients = coll.Vec<task.Task<bool>>();
                for index in 0..64 {
                    var wait = [address] () !bool => {
                        var socket = net.AsyncTcpStream.connect(address, null)!;
                        io.writeString(&socket, "a")!;
                        var data = [1]u8{0};
                        var cancelled = socket.read(data[:]);
                        result.errorOf(&cancelled).unwrap() == io.IoError.Cancelled
                    };
                    clients.push(group.spawn<bool>(move(wait))!)!;
                }
                var input = io.stdin();
                var signal = [1]u8{0};
                io.readExact(&input, signal[:])!;
                group.cancel();
                var count: i32 = 0;
                for clients.len() != 0 {
                    var client = clients.pop().unwrap();
                    if client.join()! { count++; }
                }
                server.join()!;
                count
            }

            func main() !void {
                for iteration in 0..6 { io.println(round()! == 64)!; }
            }
        """)
        self.process(VAQC, source, "-o", "async-probe")
        with subprocess.Popen(
            [self.root / "async-probe"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256)),
        ) as app:
            try:
                with ThreadPoolExecutor(max_workers=1) as worker:
                    for _ in range(6):
                        signal = worker.submit(app.stdout.readline)
                        try:
                            self.assertEqual(signal.result(timeout=7), b"ready\n")
                        finally:
                            if not signal.done():
                                app.kill()
                        app.stdin.write(b"x")
                        app.stdin.flush()
                        self.assertEqual(worker.submit(app.stdout.readline).result(timeout=7), b"true\n")
                self.assertEqual(app.wait(timeout=5), 0, app.stderr.read())
            finally:
                if app.poll() is None:
                    app.kill()

    def test_atomic_write_walk_and_temporary_resources(self):
        old = self.root / "atomic"
        old.write_bytes(b"old")
        os.link(old, self.root / "old-link")
        (self.root / "external").mkdir()
        (self.root / "external" / "sentinel").write_bytes(b"untouched")
        (self.root / "external-link").symlink_to("external/sentinel")
        (self.root / "directory").mkdir()
        denied = self.root / "unreadable"
        denied.mkdir()
        denied.chmod(0)
        try:
            self.hosted(r"""
                import (fs, path, result)

                class Visitor {
                    #implements: fs.WalkVisitor
                    pub var count: usize;
                    pub var links: usize;
                    pub var lowest: usize;
                    pub func visit(entry: fs.DirectoryEntry, depth: usize) !void {
                        count++;
                        if entry.fileType() == fs.FileType.Symlink { links++; }
                        if depth > lowest { lowest = depth; }
                    }
                }

                func temporaryFile() !path.Path {
                    var file = fs.TempFile.inDir(".")!;
                    io.writeString(&file, "temp")!;
                    file.location()
                }

                func temporaryDirectory() !path.Path {
                    var dir = fs.TempDir.inDir(".")!;
                    var location = dir.location()!;
                    var child = path.join(location, path.fromString("deep/sub")!)!;
                    fs.mkdirAll(child)!;
                    var file = path.join(child, path.fromString("file")!)!;
                    fs.writeFile(file, "data".asBytes())!;
                    location
                }

                func main() !void {
                    fs.atomicWrite("atomic", "new".asBytes())!;
                    fs.atomicWrite("external-link", "replacement".asBytes())!;
                    var failed = fs.atomicWrite("directory", "invalid".asBytes());
                    io.println(result.errorOf(&failed).unwrap() == io.systemError(21))!;

                    var unreadable = fs.atomicWrite("unreadable/file", "invalid".asBytes());
                    io.println(result.errorOf(&unreadable).unwrap() == io.systemError(13))!;
                    var slash = fs.atomicWrite("atomic/", "invalid".asBytes());
                    io.println(result.errorOf(&slash).unwrap() == fs.FsError.InvalidPath)!;
                    var file = temporaryFile()!;
                    io.println(!fs.tryExists(file)!)!;
                    var dir = temporaryDirectory()!;
                    io.println(!fs.tryExists(dir)!)!;

                    fs.mkdirAll("tree/nested/deep")!;
                    fs.mkdirAll("tree/nested/deep")!;
                    fs.writeFile("tree/nested/value", "test".asBytes())!;
                    fs.symlink("../external", "tree/out")!;
                    fs.symlink("..", "tree/nested/cycle")!;
                    var link = fs.readLink("tree/out")!;
                    io.println(link.asString() == "../external")!;

                    var visitor = Visitor{count: 0, links: 0, lowest: 0};
                    fs.walkDir("tree", fs.WalkLimits(), &visitor)!;
                    io.println(visitor.count == 5 && visitor.links == 2 && visitor.lowest == 2)!;
                    var limits = fs.WalkLimits();
                    limits.maxEntries = 1;
                    var over = fs.walkDir("tree", limits, &visitor);
                    io.println(result.errorOf(&over).unwrap() == fs.FsError.WalkLimit)!;
                    var noAccess = fs.walkDir("unreadable", fs.WalkLimits(), &visitor);
                    io.println(result.errorOf(&noAccess).unwrap() == io.systemError(13))!;
                    fs.removeAll("tree/out/")!;
                    io.println(fs.tryExists("external/sentinel")!)!;
                    fs.removeAll("tree")!;
                    fs.removeAll("tree")!;
                    io.println(!fs.tryExists("tree")!)!;
                }
            """, "true\n" * 11)
        finally:
            denied.chmod(0o700)
        self.assertEqual(old.read_bytes(), b"new")
        self.assertEqual((self.root / "old-link").read_bytes(), b"old")
        self.assertEqual((self.root / "external-link").read_bytes(), b"replacement")
        self.assertFalse((self.root / "external-link").is_symlink())
        self.assertEqual((self.root / "external" / "sentinel").read_bytes(), b"untouched")
        self.assertFalse(list(self.root.glob(".vaqrin-*")))

    def test_buffered_lines_and_binary_scanner(self):
        self.hosted(r"""
            import (bufio, bytes, result)

            func main() !void {
                var raw = io.MemoryReader("first\r\nsecond\nlast".asBytes());
                var one = bufio.readLine(&raw, 6)!.unwrap();
                io.println(one == "first")!;
                var two = bufio.readLine(&raw, 6)!.unwrap();
                io.println(two == "second")!;
                var tail = [4]u8{0, 0, 0, 0};
                io.readExact(&raw, tail[:])!;
                io.println(bytes.equal(tail[:], "last".asBytes()))!;

                var source = io.MemoryReader("abc世界\r\n\nlast".asBytes());
                var reader = bufio.BufferedReader(&source, 4)!;
                var unicode = bufio.readLine(&reader, 10)!.unwrap();
                var empty = reader.readLine(0)!.unwrap();
                var line = reader.readLine(4)!.unwrap();
                io.println(unicode == "abc世界" && empty == "" && line == "last")!;
                io.println(reader.readLine(4)! == null)!;

                var wire = [6]u8{255, 0, 44, 44, 65, 66};
                var input = io.MemoryReader(wire[:]);
                var scanner = bufio.Scanner(&input)!;
                scanner.setSplitMode(bufio.SplitMode.Delimiter(44));
                var first = scanner.nextBytes()!.unwrap();
                io.println(first.start == 0 && first.end == 2 && bytes.equal(first.bytes(), wire[0..2]))!;
                scanner.setMaxToken(1);
                var tooLong = scanner.nextBytes();
                io.println(result.errorOf(&tooLong).unwrap() == bufio.BufferError.TokenTooLong)!;
            }
        """, "true\n" * 7)


if __name__ == "__main__":
    unittest.main()
