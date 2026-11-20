"""Run I/O v2 failure injection, file identity, and resumable I/O cases."""
import errno
import os
import unittest

from acceptance import CompilerCase


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


if __name__ == "__main__":
    unittest.main()
