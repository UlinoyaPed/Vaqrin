#!/usr/bin/env python3
"""Compile and run hosted I/O cases in an independent temporary directory."""
import unittest

from acceptance import CompilerCase, REPOSITORY, VAQC


class IoAcceptance(CompilerCase):
    def run_source(self, source, *, input=None):
        file = self.source("io_case.vqr", source)
        self.process(VAQC, "build", file, "-O2", "-o", "app")
        return self.process("./app", input=input)

    def test_chained_token_and_consecutive_lines(self):
        result = self.run_source(r'''
            import (bufio, io, parse)
            func main() !void {
                var input = io.MemoryReader("123 -7\r\n42".asBytes());
                var scanner = bufio.newScanner(raw input)!;
                io.println(parse.parseInt<i32>(scanner.next()!.unwrap().text())!)!;
                io.println(scanner.readInt<i32>()!)!;
                io.println(scanner.readInt<i32>()!)!;
                io.println(scanner.nextInt<i32>()! == null)!;
                var lines = io.MemoryReader("a\r\n\nb".asBytes());
                io.println(bufio.readLine(raw lines, 10)!.unwrap())!;
                io.println(bufio.readLine(raw lines, 10)!.unwrap())!;
                io.println(bufio.readLine(raw lines, 10)!.unwrap())!;
                io.println(bufio.readLine(raw lines, 10)! == null)!;
            }
        ''')
        self.assertEqual(result.stdout, b"123\n-7\n42\ntrue\na\n\nb\ntrue\n")

    def test_temporary_receivers_cleanup_at_expression_end(self):
        result = self.run_source(r'''
            import (io)
            error Stop { Now }
            class Owner {
                pub var id: i32;
                pub const func view() str { "use" }
                func deinit() void { io.println(id) catch {}; }
            }
            func combine(a: str, b: str) !void { io.println(a)!; }
            func abort() !str { Stop.Now }
            func stopped() !void { combine(Owner{id: 3}.view(), abort()!)!; }
            func main() !void {
                combine(Owner{id: 1}.view(), Owner{id: 2}.view())!;
                stopped() catch { io.println("caught")!; };
                if false && (Owner{id: 4}).view() == "use" { panic("short circuit"); }
            }
        ''')
        self.assertEqual(result.stdout, b"use\n2\n1\n3\ncaught\n")
        source = self.source("escape.vqr", '''
            class Owner { pub const func view() str { "view" } }
            func f() str { Owner().view() }
        ''')
        self.assertIn(b"borrow from a temporary", self.process(VAQC, "check", source, good=False).stderr)

    def test_p1102_counts_and_input_errors(self):
        self.process(VAQC, "build", REPOSITORY / "examples/contests/p1102.vqr", "-o", "p1102")
        for data, expected in (
            (b"4 1\n1 1 2 3\n", b"3\n"), (b"4 1\n1 1 2 2\n", b"4\n"),
            (b"3 9\n2 2 2\n", b"0\n"),
            (b"6 2\n1 1 3 5 5 7\n", b"6\n"),
            (b"100000 1\n" + b"1 " * 50000 + b"2 " * 50000, b"2500000000\n"),
        ):
            self.assertEqual(self.process("./p1102", input=data).stdout, expected)
        for data, error in ((b"4 1\n1 1 2", b"UnexpectedEof"), (b"4 1\n1 1 2 x", b"InvalidSyntax")):
            result = self.process("./p1102", input=data, good=False)
            self.assertEqual(result.stdout, b"")
            self.assertIn(error, result.stderr)

    def test_scanner_owns_temporary_and_moved_host_files(self):
        result = self.run_source(r'''
            import (bufio, fs, io)
            extern "C" func fcntl(fd: c_int, command: c_int) c_int;

            func main() !void {
                {
                    var direct: bufio.Scanner = bufio.newScanner(io.stdin())!;
                    io.println(direct.readInt<i32>()!)!;
                    var moved = move(direct);
                    io.println(moved.readInt<i32>()!)!;
                    io.println(moved.nextInt<i32>()! == null)!;
                }
                if unsafe { fcntl(0, 1) } < 0 { panic("scanner closed stdin"); }
                fs.writeFile("scanner.txt", "19 23".asBytes())!;
                var closed: i32;
                {
                    var owned = io.HostFile.open("scanner.txt".asBytes(), 1, 0)!;
                    closed = owned.systemDescriptor()!;
                    var scanner = bufio.newScanner(move(owned))!;
                    io.println(scanner.readInt<i32>()!)!;
                }
                if unsafe { fcntl(closed, 1) } >= 0 { panic("scanner left owned file open"); }
                var file = io.HostFile.open("scanner.txt".asBytes(), 1, 0)!;
                var descriptor = file.systemDescriptor()!;
                var scanner = bufio.newScanner(move(file))!;
                io.println(scanner.readInt<i32>()! + scanner.readInt<i32>()!)!;
                var memory = io.MemoryReader("7".asBytes());
                scanner.reset(raw memory);
                io.println(scanner.readInt<i32>()!)!;
                if unsafe { fcntl(descriptor, 1) } >= 0 { panic("reset left owned file open"); }
                var pair = (io.MemoryReader("8".asBytes()), 0);
                var borrowed = bufio.newScanner(raw pair.0)!;
                io.println(borrowed.readInt<i32>()!)!;
            }
        ''', input=b"11 22")
        self.assertEqual(result.stdout, b"11\n22\ntrue\n19\n42\n7\n8\n")

    def test_bufio_hostfile_transfer_requires_move_or_a_temporary(self):
        result = self.run_source(r"""
            import (bufio, io)
            func main() !void {
                var file = io.stdin();
                var scanner = bufio.newScanner(move(file))!;
                io.println(scanner.readInt<i32>()!)!;
            }
        """, input=b"17")
        self.assertEqual(result.stdout, b"17\n")

        for factory in ("bufio.newScanner(file)", "bufio.Scanner(file)",
                        "bufio.BufferedReader(file, 32)"):
            with self.subTest(factory=factory):
                source = self.source("named_owner.vqr", """
                    import (bufio, io)
                    func main() !void {
                        var file = io.stdin();
                        var owner = """ + factory + """!;
                    }
                """)
                result = self.process(VAQC, "check", source, good=False)
                self.assertIn(b"move parameter requires move(object) or an owning temporary", result.stderr)

    def test_filesystem_wrappers_require_transfer_of_host_handles(self):
        result = self.run_source(r"""
            import (fs, io, path)
            extern "C" func fcntl(fd: c_int, command: c_int) c_int;
            func main() !void {
                fs.writeFile("owned.txt", "19".asBytes())!;
                var descriptor: i32;
                {
                    var original = io.HostFile.open("owned.txt".asBytes(), 1, 0)!;
                    descriptor = original.systemDescriptor()!;
                    var file = fs.File(move(original));
                    var bytes = [2]u8{0, 0};
                    io.println(file.read(bytes[:])! == 2)!;
                }
                io.println(unsafe { fcntl(descriptor, 1) } < 0)!;
                var file = fs.File(io.HostFile.open("owned.txt".asBytes(), 1, 0)!);
                file.close()!;

                var base = path.fromString(".")!;
                var original = io.HostDirectory.open(".".asBytes())!;
                var directory = fs.Directory(move(original), base);
                io.println(directory.next()! != null)!;
                directory.close()!;
                var temporary = fs.Directory(io.HostDirectory.open(".".asBytes())!, base);
                temporary.close()!;
            }
        """)
        self.assertEqual(result.stdout, b"true\ntrue\ntrue\n")

        for handle, wrapper in (
            ('io.HostFile.open("owned.txt".asBytes(), 1, 0)!', 'fs.File(handle)'),
            ('io.HostDirectory.open(".".asBytes())!',
             'fs.Directory(handle, path.fromString(".")!)'),
        ):
            with self.subTest(wrapper=wrapper):
                source = self.source("named_fs_handle.vqr", f"""
                    import (fs, io, path)
                    func main() !void {{
                        var handle = {handle};
                        var resource = {wrapper};
                    }}
                """)
                result = self.process(VAQC, "check", source, good=False)
                self.assertIn(b"move parameter requires move(object) or an owning temporary", result.stderr)

    def test_standard_streams_and_legacy_println(self):
        result = self.run_source(r'''
            import (io)
            func main() !void {
                var input = io.stdin();
                var received = io.readToString(raw input, 32)!;
                input.close()!;
                io.println("io-stack")!;
                io.print(received)!;
                io.eprintln("diagnostic")!;
            }
        ''', input=b"pipe input\n")
        self.assertEqual(result.stdout, b"io-stack\npipe input\n")
        self.assertEqual(result.stderr, b"diagnostic\n")

    def test_files_seek_append_and_positional_io(self):
        result = self.run_source(r'''
            import (core, fs, io, path)
            func main() !void {
                fs.writeFile("sequence.txt", "long-old-content".asBytes())!;
                fs.writeFile("sequence.txt", "abc".asBytes())!;
                fs.appendFile(path.Path("sequence.txt"), "xyz".asBytes())!;
                var options = fs.OpenOptions();
                options.read(true);
                options.write(true);
                var file = options.open("sequence.txt")!;
                var result = [3]u8{0, 0, 0};
                io.readExactAt(raw file, 3, result[:])!;
                if result[0] != 120 || result[2] != 122 { panic("incorrect positioned read"); }
                io.writeAllAt(raw file, 1, "YZ".asBytes())!;
                if file.seek(0, io.SeekOrigin.Current)! != 0 { panic("position changed"); }
                if file.seek(-1, io.SeekOrigin.End)! != 5 { panic("incorrect seek"); }
                if file.read(result[:])! != 1 || result[0] != 122 { panic("incorrect final byte"); }
                if file.read(result[:])! != 0 { panic("missing EOF"); }
                file.syncData()!;
                file.close()!;
                file.close()!;
                var failure = file.read(result[:]) catch (reason) {
                    if reason != io.IoError.Closed { return reason; }
                    usize(0)
                };
                io.println(fs.readText("sequence.txt", 6)!)!;
            }
        ''')
        self.assertEqual(result.stdout, b"aYZxyz\n")
        self.assertEqual((self.root / "sequence.txt").read_bytes(), b"aYZxyz")

    def test_directories_metadata_and_errno(self):
        result = self.run_source(r'''
            import (fs, io, path)
            func main() !void {
                fs.mkdir("records")!;
                fs.writeFile("records/one", "data".asBytes())!;
                fs.rename("records/one", "records/two")!;
                if fs.exists("records/one") || !fs.exists("records/two") { panic("rename failed"); }
                var directory = fs.readDir("records")!;
                var entry = directory.next()!.unwrap();
                io.println(entry.name())!;
                io.println(entry.metadata()!.len())!;
                var end = directory.next()!;
                if end != null { panic("directory iterator included dot entries"); }
                directory.close()!;
                fs.remove("records/two")!;
                fs.removeDir("records")!;
                var missing = fs.stat("records/two") catch (reason) {
                    io.println(io.systemErrorCode(reason).unwrap())!;
                    if reason == io.IoError.Closed { panic("errno tag collided"); }
                    return;
                };
                panic("removed file was found");
            }
        ''')
        self.assertEqual(result.stdout, b"two\n4\n2\n")
        self.assertFalse((self.root / "records").exists())

    def test_buffered_short_progress_and_scanner(self):
        result = self.run_source(r'''
            import (bufio, io)
            class InterruptWriter {
                #implements: io.Writer
                var steps: usize;
                var sink: io.MemoryWriter;
                pub func init() : steps(0), sink(io.MemoryWriter()) {}
                pub func write(src: []const u8) !usize {
                    steps++;
                    if steps == 2 { return io.IoError.WouldBlock; }
                    var chunk = if src.len() > 2 { usize(2) } else { src.len() };
                    sink.write(src[0..chunk])
                }
                pub func flush() !void {}
                pub const func bytes() []const u8 { sink.asSlice() }
            }
            func main() !void {
                var native = InterruptWriter();
                var writer = bufio.newWriter(raw native, 4)!;
                if writer.write("abcde".asBytes())! != 4 { panic("incorrect short progress"); }
                writer.writeString("e")!;
                writer.finish()!;
                var source = io.MemoryReader(native.bytes());
                io.println(io.readToString(raw source)!)!;
                var words = io.MemoryReader(" a\r\n 中\t last ".asBytes());
                var scanner = bufio.newScanner(raw words)!;
                for index in 0..3 {
                    var token = scanner.next()!.unwrap();
                    io.println(token.text())!;
                }
                if scanner.next()! != null { panic("missing scanner EOF"); }
                var sourceBytes = io.MemoryReader("abcd".asBytes());
                var buffered = bufio.newReader(raw sourceBytes, 2)!;
                if buffered.readByte()!.unwrap() != 97 { panic("read byte"); }
                buffered.unreadByte(97)!;
                if buffered.peek(2)![0] != 97 { panic("peek after unread"); }
                if buffered.discard(3)! != 3 || buffered.readByte()!.unwrap() != 100 {
                    panic("discard across refill");
                }
            }
        ''')
        self.assertEqual(result.stdout.decode(), "abcde\na\n中\nlast\n")

    def test_lexical_paths_and_owned_format_arguments(self):
        result = self.run_source(r'''
            import (fmt, io, path)
            func main() !void {
                var name = path.Path("/a//中/../b/./.hidden.ext").normalize()!;
                io.println(name.asString())!;
                io.println(name.extension().unwrap())!;
                io.println(name.stem().unwrap())!;
                var parent = name.parent()!.unwrap();
                io.println(parent.asString())!;
                var joined = path.join(parent, path.Path("/other"))!;
                io.println(joined.asString())!;
                var changed = path.Path("file.old").withExtension("new")!;
                if changed.asString() != "file.new" { panic("relative path changed"); }
                var trailing = path.Path("file.").withExtension("ext")!;
                if trailing.asString() != "file.ext" { panic("trailing extension dot"); }
                var minimum: i64 = -9223372036854775807i64 - 1;
                var arguments = [4]fmt.FormatArg{
                    fmt.arg(fmt.format("owned")!),
                    fmt.arg(u64(255)),
                    fmt.arg(minimum),
                    fmt.arg(1.25f64),
                };
                io.println(fmt.format("{0} {1:04X} {2:d} {3:.1f}", arguments[:])!)!;
            }
        ''')
        self.assertEqual(result.stdout.decode(), "/a/b/.hidden.ext\next\n.hidden\n/a/b\n/other\nowned 00FF -9223372036854775808 1.2\n")

    def test_example_links_and_executes(self):
        self.process(VAQC, "build", REPOSITORY / "examples/stdlib/file_operations.vqr", "-O2", "-o", "io-example")
        before = set(self.root.iterdir())
        result = self.process("./io-example")
        self.assertEqual(result.stdout, b"read: Vaqrin I/O\n11\nhex=ff\nio-stack.txt\n")
        self.assertEqual(set(self.root.iterdir()), before)

    def test_float_specials_radix_and_signed_padding(self):
        result = self.run_source(r'''
            import (fmt, io)
            func main() !void {
                var pattern = [3]fmt.FormatArg{fmt.arg(i32(-42)), fmt.arg(u32(42)), fmt.arg(1.25f64)};
                io.println(fmt.format("{:06d} {:+06d} {:+07.2f}", pattern[:])!)!;
                var stdout = io.stdout();
                var bytes = [3]u8{0, 15, 255};
                fmt.writeHex(raw stdout, bytes[:], true)!;
                io.println("")!;
                var nan = bitCast<f64>(0x7ff8000000000000u64);
                var infinity = bitCast<f64>(0x7ff0000000000000u64);
                io.println(fmt.formatFloat(nan, fmt.FloatStyle.General, null)!)!;
                io.println(fmt.formatFloat(infinity, fmt.FloatStyle.General, null)!)!;
                io.println(fmt.formatFloat(-0.0f64, fmt.FloatStyle.Fixed, some(usize(1)))!)!;
            }
        ''')
        self.assertEqual(result.stdout, b"-00042 +00042 +001.25\n000FFF\nnan\ninf\n-0.0\n")

    def test_file_raii_and_static_runtime_link(self):
        source = self.source("descriptor.vqr", r'''
            import (fs)
            func exercise() !void {
                { var automatic = fs.create("auto")!; }
                { var manual = fs.create("manual")!; manual.close()!; manual.close()!; }
            }
            export "C" func run() c_int {
                exercise() catch { return 1; };
                0
            }
        ''')
        consumer = self.source("descriptor.c", """
            extern int run(void);
            extern int __real_close(int);
            static int closed;
            int __wrap_close(int descriptor) { ++closed; return __real_close(descriptor); }
            int main(void) { return run() || closed != 2; }
        """)
        self.process(VAQC, source, "-O2", "--emit=staticlib", "-o", "libdescriptor.a")
        self.process("cc", consumer, "libdescriptor.a", "-Wl,--wrap=close", "-o", "descriptor")
        self.process("./descriptor")
        self.assertTrue((self.root / "auto").is_file())
        self.assertTrue((self.root / "manual").is_file())

    def test_task_and_io_runtimes_link_together(self):
        result = self.run_source(r'''
            import (io, task)
            func main() !void {
                var group = task.TaskGroup.withWorkers(1)!;
                var job = () !void => io.print("task I/O\n");
                var started = group.spawn<void>(move(job))!;
                started.join()!;
            }
        ''')
        self.assertEqual(result.stdout, b"task I/O\n")

    def test_host_io_reports_unsupported_target_at_check_time(self):
        source = self.source("unsupported.vqr", '''
            import (io)
            func f() !void { io.print("no POSIX runtime"); }
        ''')
        result = self.process(VAQC, "check", source, "--target", "thumbv7m-none-eabi",
                              "--profile", "freestanding", good=False)
        self.assertIn(b"host I/O requires hosted GNU/Linux", result.stderr)

    def test_borrowed_scanner_keeps_freestanding_io_independent(self):
        source = self.source("borrowed.vqr", '''
            import (bufio, io, core)
            func first(reader: *io.Reader) core.ResultOptional<bufio.ByteToken> {
                var scanner = bufio.newScanner(reader)!;
                scanner.nextBytes()
            }
        ''')
        self.process(VAQC, "build", "--emit=llvm-ir", "--target", "thumbv7m-none-eabi",
                     "--profile", "freestanding", source, "-o", "bare.ll")
        self.assertNotIn("__vqr_io_", (self.root / "bare.ll").read_text())


if __name__ == "__main__":
    unittest.main()
