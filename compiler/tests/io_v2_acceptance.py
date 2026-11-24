"""Run I/O v2 failure injection, file identity, and resumable I/O cases."""
import errno
import os
import resource
import subprocess
import sys
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
        full_device = ""
        if sys.platform == "linux":
            full_device = f'''
                    var disk = fs.copy("source", "/dev/full");
                    io.println(result.errorOf(raw disk).unwrap() == io.systemError({errno.ENOSPC}))!;
            '''
        try:
            self.hosted(f"""
                import (fs, result)
                func main() !void {{
                    var aliases = [3]str{{"source", "hard", "soft"}};
                    for alias in aliases[:] {{
                        var copy = fs.copy("source", alias);
                        io.println(result.errorOf(raw copy).unwrap() == fs.FsError.SameFile)!;
                        io.println(fs.sameFile("source", alias)!)!;
                    }}
                    io.println(fs.copy("source", "target")! == 14)!;
                    io.println(fs.copy("empty", "new-empty")! == 0)!;
                    io.println(!fs.sameFile("source", "target")!)!;
                    io.println(!fs.tryExists("missing-link")!)!;
                    io.println(!fs.tryExists("source/absent")!)!;
                    var loop = fs.tryExists("loop-a");
                    io.println(result.errorOf(raw loop).unwrap() == io.systemError({errno.ELOOP}))!;
                    var denied = fs.tryExists("denied/file");
                    io.println(result.errorOf(raw denied).unwrap() == io.systemError({errno.EACCES}))!;
                    var permission = fs.copy("source", "denied/rejected");
                    io.println(result.errorOf(raw permission).unwrap() == io.systemError({errno.EACCES}))!;
                    {full_device}
                }}
            """, "true\n" * (15 if full_device else 14))
        finally:
            denied.chmod(0o700)
        self.assertEqual(original.read_bytes(), b"binary\x00payload")
        self.assertEqual((self.root / "target").read_bytes(), original.read_bytes())
        self.assertEqual((self.root / "new-empty").read_bytes(), b"")
        self.assertFalse((denied / "rejected").exists())

    def test_body_and_scanner_preserve_partial_progress(self):
        self.hosted(r"""
            import (bufio, bytes, http, result)
            class SplitWriter {
                #implements: io.Writer
                pub var sink: io.MemoryWriter;
                pub var calls: i32;
                pub func write(src: []const u8) !usize {
                    calls++;
                    switch calls {
                        1 => sink.write(src[0..2]),
                        2 => io.IoError.WouldBlock,
                        _ => sink.write(src),
                    }
                }
                pub func flush() !void {}
            }
            class SplitReader {
                #implements: io.Reader
                pub var source: io.MemoryReader;
                pub var calls: i32;
                pub func read(dst: []u8) !usize {
                    calls++;
                    switch calls {
                        1 => source.read(dst[0..2]),
                        2 => io.IoError.WouldBlock,
                        _ => source.read(dst),
                    }
                }
            }
            func main() !void {
                var writer = SplitWriter{sink: io.MemoryWriter(), calls: 0};
                var body = http.Body.fromBytes("payload".asBytes())!;
                var interrupted = body.writeTo(raw writer);
                io.println(result.errorOf(raw interrupted).unwrap() == io.IoError.WouldBlock)!;
                io.println(body.len() == 5 && body.writeTo(raw writer)! == 5 && body.isComplete())!;
                io.println(bytes.equal(writer.sink.asSlice(), "payload".asBytes()))!;

                var input = SplitReader{source: io.MemoryReader(" payload rest".asBytes()), calls: 0};
                var scanner = bufio.Scanner(raw input)!;
                var partial = scanner.next();
                io.println(result.errorOf(raw partial).unwrap() == io.IoError.WouldBlock)!;
                var token = scanner.next()!.unwrap();
                io.println(token.text() == "payload" && token.start() == 1 && token.end() == 8)!;
                io.println(scanner.next()!.unwrap().text() == "rest")!;

                var lines = SplitReader{source: io.MemoryReader("payload\nsecond".asBytes()), calls: 0};
                var buffered = bufio.BufferedReader(raw lines, 4)!;
                var interruptedLine = buffered.readLine(7);
                io.println(result.errorOf(raw interruptedLine).unwrap() == io.IoError.WouldBlock)!;
                io.println(buffered.readLine(7)!.unwrap() == "payload")!;
                io.println(buffered.readLine(7)!.unwrap() == "second")!;

                var first = io.MemoryWriter();
                var second = SplitWriter{sink: io.MemoryWriter(), calls: 0};
                var targets = [2]*io.Writer{raw first, raw second};
                var fanout = io.MultiWriter(targets[:]);
                var payload = "payload".asBytes();
                var interruptedWrite = fanout.write(payload);
                io.println(result.errorOf(raw interruptedWrite).unwrap() == io.IoError.WouldBlock)!;
                var wrong = fanout.write("changed".asBytes());
                io.println(result.errorOf(raw wrong).unwrap() == io.IoError.InvalidArgument)!;
                io.println(fanout.write(payload)! == 7)!;
                io.println(bytes.equal(first.asSlice(), payload) && bytes.equal(second.sink.asSlice(), payload))!;

                var teeInput = io.MemoryReader(payload);
                var mirror = SplitWriter{sink: io.MemoryWriter(), calls: 0};
                var tee = io.tee(raw teeInput, raw mirror);
                var dst = [7]u8{0, 0, 0, 0, 0, 0, 0};
                var interruptedRead = tee.read(dst[:]);
                io.println(result.errorOf(raw interruptedRead).unwrap() == io.IoError.WouldBlock)!;
                io.println(tee.read(dst[0..1])! == 1 && tee.read(dst[1..7])! == 6)!;
                io.println(bytes.equal(dst[:], payload) && bytes.equal(mirror.sink.asSlice(), payload))!;
            }
        """, "true\n" * 16)

    def test_stdout_broken_pipe_is_catchable(self):
        source = self.source("broken_pipe.vqr", f"""
            import (io)
            func main() i32 {{
                io.print("payload") catch (reason) {{
                    return if reason == io.systemError({errno.EPIPE}) {{ 0 }} else {{ 2 }};
                }};
                1
            }}
        """)
        self.process(VAQC, "build", source, "-o", "broken_pipe")
        read_end, write_end = os.pipe()
        os.close(read_end)
        try:
            process = subprocess.run([self.root / "broken_pipe"], stdout=write_end, timeout=5)
            self.assertEqual(process.returncode, 0)
        finally:
            os.close(write_end)

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
                var first = io.writeAll(raw writer, data, raw sent);
                io.println(sent == 2 && result.errorOf(raw first).unwrap() == io.IoError.WouldBlock)!;
                var second = io.writeAll(raw writer, data, raw sent);
                io.println(sent == 2 && result.errorOf(raw second).unwrap() == io.IoError.Interrupted)!;
                var cancelled = io.writeAll(raw writer, data, raw sent);
                io.println(sent == 2 && result.errorOf(raw cancelled).unwrap() == io.IoError.Cancelled)!;
                var timeout = io.writeAllProgress(raw writer, data[sent..data.len()])!;
                io.println(timeout.processed == 0 && timeout.status == io.ReadStatus.DeadlineExceeded)!;
                var disk = io.writeAll(raw writer, data, raw sent);
                io.println(sent == 2 && result.errorOf(raw disk).unwrap() == io.systemError(28))!;
                io.writeAll(raw writer, data, raw sent)!;
                io.println(sent == 7 && bytes.equal(writer.sink.asSlice(), data))!;

                var reader = FaultyReader{source: io.MemoryReader(data), calls: 0};
                var dst = [7]u8{0, 0, 0, 0, 0, 0, 0};
                var run = io.readExactProgress(raw reader, dst[:])!;
                io.println(run.processed == 2 && run.status == io.ReadStatus.Cancelled)!;
                var received = run.processed;
                io.readExact(raw reader, dst[:], raw received)!;
                io.println(received == 7 && bytes.equal(dst[:], data))!;
                var eof = io.readExactProgress(raw reader, dst[:])!;
                io.println(eof.processed == 0 && eof.status == io.ReadStatus.Eof)!;

                var atLeast = FaultyReader{source: io.MemoryReader(data), calls: 0};
                var offset: usize = 0;
                var partial = io.readAtLeast(raw atLeast, dst[:], 4, raw offset);
                io.println(offset == 2 && result.errorOf(raw partial).unwrap() == io.IoError.Cancelled)!;
                io.println(io.readAtLeast(raw atLeast, dst[:], 4, raw offset)! == 7 && offset == 7)!;
            }
        """, "true\n" * 11)

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
                var stopped = cursor.transfer(raw reader, raw writer, 9, scratch[:]);
                io.println(result.errorOf(raw stopped).unwrap() == io.IoError.Cancelled)!;
                io.println(cursor.copied() == 2 && cursor.pending() == 3 && reader.remaining() == 4)!;
                var total = cursor.transfer(raw reader, raw writer, 9, scratch[:])!;
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
                var writer = bufio.BufferedWriter(raw target, 8)!;
                io.writeString(raw writer, "payload")!;
                var buffered = writer.finish();
                io.println(writer.buffered() == 5 && result.errorOf(raw buffered).unwrap() == io.IoError.WouldBlock)!;
                var failed = writer.finish();
                io.println(writer.buffered() == 0 && result.errorOf(raw failed).unwrap() == io.IoError.WriteFailed)!;
                writer.finish()!;
                writer.finish()!;
                io.println(target.flushes == 2 && bytes.equal(target.memory.asSlice(), "payload".asBytes()))!;
                var closed = writer.write("!".asBytes());
                io.println(result.errorOf(raw closed).unwrap() == io.IoError.Closed)!;
                var replacement = io.MemoryWriter();
                writer.reset(raw replacement)!;
                io.writeString(raw writer, "next")!;
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
                    io.println(result.errorOf(raw missing).unwrap() == reactor.ReactorError.UnknownWatch)!;
                    io.println(r.wait(some(time.Duration.fromMillis(10)))! == null)!;
                    var writer = r.register({next_write}, reactor.Interest.write())!;
                    var ready = r.wait(some(time.Duration.zero()))!.unwrap();
                    io.println(ready.watch.value == writer.value && ready.writable)!;
                    r.update(writer, reactor.Interest.read())!;
                    var byte: u8 = 65;
                    io.println(unsafe {{ write({next_write}, raw byte, 1) }} == 1)!;
                    var next = r.wait(some(time.Duration.fromMillis(50)))!.unwrap();
                    io.println(next.watch.value == replacement.value && next.readable)!;
                    r.close();
                    r.close();
                    var closed = r.wait(null);
                    io.println(result.errorOf(raw closed).unwrap() == io.IoError.Closed)!;

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

    def test_async_udp_and_process_pipes(self):
        self.hosted(r"""
            import (bytes, net, path, process, task, time)
            func main() !void {
                var group = task.TaskGroup.withWorkers(1)!;
                var local = net.parseSocketAddr("127.0.0.1:0")!;
                var receiver = net.AsyncUdpSocket.bind(local)!;
                var address = receiver.localAddr()!;
                var collect = [move(receiver)]() !bool => {
                    var receiver = move(receiver);
                    var scratch = [3]u8{0, 0, 0};
                    var deadline = some(time.Deadline.after(time.Duration.fromSeconds(2))!);
                    var packet = receiver.recvFrom(scratch[:], deadline)!;
                    packet.len == 3 && packet.truncated && bytes.equal(scratch[:], "abc".asBytes())
                };
                var deliver = [local, address]() !void => {
                    var sender = net.AsyncUdpSocket.bind(local)!;
                    var count = sender.sendTo("abcdef".asBytes(), address, null)!;
                    if count != 6 { panic("UDP datagram was split"); }
                };
                var recv = group.spawn<bool>(move(collect))!;
                var send = group.spawn<void>(move(deliver))!;
                io.println(recv.join()!)!;
                send.join()!;

                var echo = () !bool => {
                    var command = process.Command.new(path.fromString("/bin/cat")!);
                    command.stdin(process.Stdio.Piped);
                    command.stdout(process.Stdio.Piped);
                    var child = command.spawn()!;
                    var deadline = some(time.Deadline.after(time.Duration.fromSeconds(2))!);
                    {
                        var input = child.stdin().unwrap();
                        if input.writeAsync("abc".asBytes(), deadline)! != 3 { return false; }
                        input.close()!;
                    }
                    var dst = [3]u8{0, 0, 0};
                    {
                        var output = child.stdout().unwrap();
                        var count: usize = 0;
                        while count < dst.len() {
                            var n = output.readAsync(dst[count..dst.len()], deadline)!;
                            if n == 0 { return false; }
                            count += n;
                        }
                        var eof = [1]u8{0};
                        if output.readAsync(eof[:], deadline)! != 0 { return false; }
                    }
                    child.wait()!.success() && bytes.equal(dst[:], "abc".asBytes())
                };
                var child = group.spawn<bool>(move(echo))!;
                io.println(child.join()!)!;
            }
        """, "true\n" * 2)

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
                        io.readExact(raw socket, data[:])!;
                        if bytes.equal(data[:], "ping".asBytes()) { count++; }
                        io.writeString(raw socket, "pong")!;
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
                        io.writeString(raw socket, "ping")!;
                        var result = [4]u8{0, 0, 0, 0};
                        io.readExact(raw socket, result[:])!;
                        if !bytes.equal(result[:], "pong".asBytes()) { return false; }
                        socket.read(result[:])! == 0
                    };
                    calls.push(group.spawn<bool>(move(call))!)!;
                }
                var count: i32 = 0;
                while calls.len() != 0 {
                    var current = calls.pop().unwrap();
                    if current.join()! { count++; }
                }
                io.println(count == 32 && server.join()! == 32)!;

                var waiting = net.AsyncTcpListener.bind(net.parseSocketAddr("127.0.0.1:0")!)!;
                var timeout = [move(waiting)] () !bool => {
                    var deadline = time.Deadline.after(time.Duration.fromMillis(40))!;
                    var expired = waiting.accept(some(deadline));
                    result.errorOf(raw expired).unwrap() == io.IoError.DeadlineExceeded
                };
                var timer = group.spawn<bool>(move(timeout))!;
                io.println(timer.join()!)!;

                var outside = net.AsyncTcpListener.bind(net.parseSocketAddr("127.0.0.1:0")!)!;
                var deadline = time.Deadline.after(time.Duration.fromMillis(10))!;
                var expired = outside.accept(some(deadline));
                io.println(result.errorOf(raw expired).unwrap() == io.IoError.DeadlineExceeded)!;
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
                        io.readExact(raw peer, marker[:])!;
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
                        io.writeString(raw socket, "a")!;
                        var data = [1]u8{0};
                        var cancelled = socket.read(data[:]);
                        result.errorOf(raw cancelled).unwrap() == io.IoError.Cancelled
                    };
                    clients.push(group.spawn<bool>(move(wait))!)!;
                }
                var input = io.stdin();
                var signal = [1]u8{0};
                io.readExact(raw input, signal[:])!;
                group.cancel();
                var count: i32 = 0;
                while clients.len() != 0 {
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
                    io.writeString(raw file, "temp")!;
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
                    io.println(result.errorOf(raw failed).unwrap() == io.systemError(21))!;

                    var unreadable = fs.atomicWrite("unreadable/file", "invalid".asBytes());
                    io.println(result.errorOf(raw unreadable).unwrap() == io.systemError(13))!;
                    var slash = fs.atomicWrite("atomic/", "invalid".asBytes());
                    io.println(result.errorOf(raw slash).unwrap() == fs.FsError.InvalidPath)!;
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
                    fs.walkDir("tree", fs.WalkLimits(), raw visitor)!;
                    io.println(visitor.count == 5 && visitor.links == 2 && visitor.lowest == 2)!;
                    var limits = fs.WalkLimits();
                    limits.maxEntries = 1;
                    var over = fs.walkDir("tree", limits, raw visitor);
                    io.println(result.errorOf(raw over).unwrap() == fs.FsError.WalkLimit)!;
                    var noAccess = fs.walkDir("unreadable", fs.WalkLimits(), raw visitor);
                    io.println(result.errorOf(raw noAccess).unwrap() == io.systemError(13))!;
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
                var one = bufio.readLine(raw raw, 6)!.unwrap();
                io.println(one == "first")!;
                var two = bufio.readLine(raw raw, 6)!.unwrap();
                io.println(two == "second")!;
                var tail = [4]u8{0, 0, 0, 0};
                io.readExact(raw raw, tail[:])!;
                io.println(bytes.equal(tail[:], "last".asBytes()))!;

                var source = io.MemoryReader("abc世界\r\n\nlast".asBytes());
                var reader = bufio.BufferedReader(raw source, 4)!;
                var unicode = bufio.readLine(raw reader, 10)!.unwrap();
                var empty = reader.readLine(0)!.unwrap();
                var line = reader.readLine(4)!.unwrap();
                io.println(unicode == "abc世界" && empty == "" && line == "last")!;
                io.println(reader.readLine(4)! == null)!;

                var wire = [6]u8{255, 0, 44, 44, 65, 66};
                var input = io.MemoryReader(wire[:]);
                var scanner = bufio.Scanner(raw input)!;
                scanner.setSplitMode(bufio.SplitMode.Delimiter(44));
                var first = scanner.nextBytes()!.unwrap();
                io.println(first.start == 0 && first.end == 2 && bytes.equal(first.bytes(), wire[0..2]))!;
                scanner.setMaxToken(1);
                var tooLong = scanner.nextBytes();
                io.println(result.errorOf(raw tooLong).unwrap() == bufio.BufferError.TokenTooLong)!;
            }
        """, "true\n" * 7)


if __name__ == "__main__":
    unittest.main()
