"""Exercise the compiler as installed, including independent native consumers."""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

VAQC = pathlib.Path(sys.argv.pop(1)).resolve()
REPOSITORY = pathlib.Path(__file__).resolve().parents[2]


class Acceptance(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vaqrin-test-")
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)

    def source(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def process(self, *args, good=True):
        result = subprocess.run(
            [str(arg) for arg in args], cwd=self.root, capture_output=True, timeout=25
        )
        if good:
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        return result

    def hosted(self, program, output, *flags):
        source = self.source("main.vqr", "package main;\nimport (io);\n" + program)
        self.process(VAQC, *flags, source, "-o", "program")
        self.assertEqual(self.process("./program").stdout.decode(), output)

    def test_syntax_sema(self):
        self.source("geometry/math.vqr", '''
            package geometry;
            pub func twice(x: i32) i32 { x * 2 }
        ''')
        source = self.source("root/main.vqr", '''
            package main;
            import (geometry as g, io);
            type Nested = !??i32;
            func id<T: Copyable>(x: T) T { x }
            func main() !void {
                café := [_]i32{g.twice(3), id<i32>(4),};
                var value: i32;
                if true { value = café[0]; } else { value = 1; }
                for i, n in enumerate(café[:]) {
                    io.println(usize(n)! + i)!;
                }
                for (i := 0; i < 2; i++) { io.println(i)!; }
                io.println(value)!;
            }
        ''')
        self.process(VAQC, "build", source.parent, "--package-path", self.root, "-o", "program")
        self.assertEqual(self.process("./program").stdout, b"6\n5\n0\n1\n6\n")
        cases = {
            "uninitialized": ("func f(c: bool) i32 { var x: i32; if c { x = 1; } x }", "E230"),
            "moved": ("func f() i32 { x := 1; y := move(x); x }", "E230"),
            "nfc": ("func f() void { café := 1; café := 2; }", "duplicate local"),
            "array": ("type T = [_]i32;", "E111"),
            "unknown": ("func f() void { @; }", "E104"),
            "unchecked": ("func f() !i32 { 1 } func g() void { f(); }", "E231"),
            "read-only": ("func f() void { const x = [_]i32{1}; x[0] = 4; }", "writable"),
        }
        for name, (snippet, diagnostic) in cases.items():
            with self.subTest(name=name):
                bad = self.source("bad.vqr", "package t;\n" + snippet)
                result = self.process(VAQC, "check", bad, good=False)
                self.assertIn(diagnostic, result.stderr.decode())

    def test_numerics_ctfe(self):
        self.hosted('''
            comptime func add(n: usize) usize {
                var s: usize = 0;
                for i in usize(1)..=n { s += i; }
                s
            }
            comptime const N = add(4);
            type Buffer = [N]u8;
            func narrow(x: i32) !i8 { i8(x) }
            func scale(comptime factor: i32, x: i32) i32 { factor * x }
            func length<T, comptime Len: usize>(x: *const [Len]T) usize { Len }
            func typedRem(x, y: i32) i32 { x % y }
            func main() !void {
                io.println(N)!;
                comptime const A: usize = 2;
                comptime const B: usize = add(A);
                a := [B]u8{4, 5, 6};
                io.println(length(&a))!;
                io.println(length<u8, (1 + 2)>(&a))!;
                io.println(scale(2 + 3, 8))!;
                io.println(add(5))!;
                io.println(u8(250) + u8(10))!;
                u := u8(250); u += 10;
                io.println(u)!;
                io.println(u8(250) +| 10)!;
                io.println(narrow(127)!)!;
                io.println(narrow(128) catch i8(7))!;
                io.println(typedRem(-2147483648, -1))!;
                io.println(typedRem(-7, 3))!;
                io.println(0 < 2 < 3)!;
                count := 0;
                for n in u8(254)..=u8(255) { count++; }
                io.println(count)!;
            }
        ''', "10\n3\n3\n40\n15\n260\n4\n255\n127\n7\n0\n-1\ntrue\n2\n", "-O2")
        for expression in ["i32(2147483647) + 1", "i32(1) << -1", "typed(0)", "1 / 0"]:
            with self.subTest(expression=expression):
                bad = self.source("bad.vqr", f'''
                    package test;
                    func typed(x: i32) i32 {{ 1 / x }}
                    const Bad = {expression};
                ''')
                self.assertIn("E2", self.process(VAQC, "check", bad, good=False).stderr.decode())
        for expression in ["a[-1]", "a[4294967296u64]", "a[3]", "i32(1) / z", "i32(2147483647) + one"]:
            with self.subTest(expression=expression):
                source = self.source("trap.vqr", f'''
                    package main;
                    func main() void {{
                        a := [_]i32{{1}};
                        z := 0;
                        one := 1;
                        {expression};
                    }}
                ''')
                self.process(VAQC, "-O2", source, "-o", "trap")
                self.assertLess(self.process("./trap", good=False).returncode, 0)

    def test_ownership(self):
        self.hosted('''
            class Probe {
                var number: i32;
            public:
                func init(n: i32) : number(n) {}
                func deinit() void { io.println(number) catch {}; }
            }
            error Stop { Failure }
            func fail() !Probe { Stop.Failure }
            func test() !void {
                a := Probe(10);
                a move= a;
                b := move(a);
                a = Probe(20);
                a = fail()!;
            }
            func main() !void {
                test() catch { io.println(99)!; };
                x := Probe(30);
                x = Probe(31);
            }
        ''', "20\n10\n99\n30\n31\n")

    def test_closures_and_boxes(self):
        self.hosted('''
            class Probe {
                var number: i32;
            public:
                func init(n: i32) : number(n) {}
                func init() : self(10) {}
                const func get() i32 { number }
                func deinit() void { io.println(number) catch {}; }
            }
            enum Pair { None, Both(Probe, Probe) }
            func main() !void {
                const sq: func(i32) i32 = n => n*n;
                io.println(sq(5))!;
                x := 1;
                f := [&x](n: i32) i32 => { x += n; x };
                io.println(f(8))!;
                a := new Probe()!;
                io.println(a.asPtr().get())!;
                ptr := Box.intoRaw(move(a));
                b := unsafe { Box<Probe>.fromRaw(ptr) };
                handler := [move(b)]() ^Probe => move(b);
                v := move(handler)();
                tmp := Pair.Both(Probe(20), Probe(30));
                switch move(tmp) {
                    Pair.Both(x, _) => io.println(x.get())!,
                    Pair.None => {},
                }
            }
        ''', "25\n9\n10\n20\n20\n30\n10\n")

    def test_pattern_results(self):
        self.hosted('''
            enum Item { Unit, Num(i32), }
            error Lookup { Unavailable }
            func lookup(i: i32) !??Item {
                if i < 0 { return Lookup.Unavailable; }
                if i == 0 { return null; }
                if i == 1 { return some(null); }
                some(some(Item.Num(i)))
            }
            func pretty(i: i32) !i32 {
                switch lookup(i)! {
                    null => 10,
                    some(null) => 20,
                    some(some(Item.Num(value))) => value,
                    some(some(Item.Unit)) => 0,
                }
            }
            func main() !void {
                io.println(pretty(-1) catch (e) {
                    switch e { Lookup.Unavailable => 99, else => 0 }
                })!;
                io.println(pretty(0)!)!;
                io.println(pretty(1)!)!;
                io.println(pretty(42)!)!;
            }
        ''', "99\n10\n20\n42\n")

    def test_standard_packages(self):
        self.hosted('''
            import (std/core, std/text, collections as seq);
            func twice(x: i32) i32 { x * 2 }
            func even(x: i32) bool { x % 2 == 0 }
            func seven() i32 { 7 }

            func main() !void {
                var values = seq.Vec<i32>();
                for i in 0..5 { values.push(i)!; }
                values[0] = 9;
                var copy = values.clone()!;
                copy[0] = 0;
                io.println(values[0])!;
                io.println(copy[0])!;
                var array = [_]i32{1, 2, 3};
                for i, value in seq.enumerate(array[:]) { io.println(value)!; }
                for value in seq.map(array[:], twice) { io.println(value)!; }
                for value in seq.filter(array[:], even) { io.println(value)!; }
                for x, y in seq.zip(array[:], copy.asSlice()) { io.println(x + y)!; }
                for value in seq.take(array[:], 2) { io.println(value)!; }
                for value in seq.skip(array[:], 2) { io.println(value)!; }
                var total = 0;
                for value in copy { total += value; }
                io.println(total)!;
                io.println(text.equal("中é🙂", "中é🙂"))!;
                io.println(text.equal("é", "é"))!;
                io.println(text.byteLen("中é🙂"))!;
                io.println(text.scalarCount("中é🙂"))!;
                var it = text.chars("中é🙂");
                io.println(it.next().unwrap() == '中')!;
                io.println(it.next().unwrap() == 'é')!;
                io.println(it.next().unwrap() == '🙂')!;
                var optional: ?i32 = 41;
                var result: !i32 = 42;
                io.println(core.isSome(&optional))!;
                io.println(core.isOk(&result))!;
                io.println(core.unwrapOr<i32>(null, seven))!;
                var box: core.Box<i32> = new i32(4)!;
                io.println(*box.asPtr())!;
            }
        ''', "9\n0\n1\n2\n3\n2\n4\n6\n2\n1\n3\n5\n1\n2\n3\n10\ntrue\nfalse\n9\n3\ntrue\ntrue\ntrue\ntrue\ntrue\n7\n4\n")

    def test_dynamic_text(self):
        self.hosted('''
            import (core, text);
            const greeting = "静态";
            comptime const scalars = text.scalarCount(greeting);
            comptime const exact = "é" != "é";
            func main() !void {
                io.println(greeting)!;
                io.println(scalars)!;
                io.println(exact)!;
                var builder = text.StringBuilder();
                builder.append("中")!;
                builder.append("🙂")!;
                var built = builder.build()!;
                var sibling = built;
                built = "replacement";
                builder.clear();
                builder.append("latest")!;
                io.println(sibling)!;
                io.println(sibling == "中🙂")!;
                io.println(sibling.byteLen())!;
                io.println(builder.build()!)!;
                var bytes = [_]u8{0xc3, 0xa9};
                var decoded = text.fromBytes(bytes[:])!;
                bytes[0] = 0x20;
                io.println(text.equal(decoded, "é"))!;
                var bad = [_]u8{0xed, 0xa0, 0x80};
                var invalid = text.fromBytes(bad[:]);
                io.println(core.isOk(&invalid))!;
            }
        ''', "静态\n2\ntrue\n中🙂\ntrue\n7\nlatest\ntrue\nfalse\n", "-O2")

    def test_vec_ownership_and_allocation_failure(self):
        self.hosted('''
            import (collections);
            class Probe {
                var id: i32;
            public:
                func init(id: i32) : id(id) {}
                const func get() i32 { id }
                func deinit() void { io.println(id + 100) catch {}; }
            }
            func main() !void {
                var items = collections.Vec<Probe>();
                for n in 0..9 { items.push(Probe(n))!; }
                io.println(items.len())!;
                items.reserve(~usize(0)) catch { io.println(items.len())!; };
                var previous = items.replace(0, Probe(20)).unwrap();
                io.println(previous.get())!;
                var last = items.pop().unwrap();
                io.println(last.get())!;
                items.clear();
            }
        ''', "9\n9\n0\n8\n107\n106\n105\n104\n103\n102\n101\n120\n108\n100\n", "-O2")

    def test_host_allocation_lifetime(self):
        source = self.source("allocation.vqr", """
            package allocation;
            import (core, collections, text);
            extern "C" func fail_next() void;
            extern "C" func dropped(id: c_int) void;
            class Probe {
                var id: i32;
            public:
                func init(id: i32) : id(id) {}
                func deinit() void { unsafe { dropped(id); } }
            }
            func check() !i32 {
                var vec = collections.Vec<Probe>();
                for id in 1..=4 { vec.push(Probe(id))!; }
                unsafe { fail_next(); }
                var push = vec.push(Probe(99));
                if core.isOk(&push) || vec.len() != 4 { return 1; }

                var builder = text.StringBuilder();
                builder.append("持久")!;
                unsafe { fail_next(); }
                var failed = builder.build();
                if core.isOk(&failed) { return 2; }
                var value = builder.build()!;
                var copied = value;
                value = "";
                if copied != "持久" { return 3; }
                0
            }
            export "C" func exercise() c_int { check() catch 4 }
        """)
        consumer = self.source("allocation.c", """
            #include <stddef.h>
            static int live, fail, rejected, drops, sum;
            void *__real_malloc(size_t);
            void *__real_aligned_alloc(size_t, size_t);
            void __real_free(void *);
            extern int exercise(void);
            void fail_next(void) { fail = 1; }
            void dropped(int id) { ++drops; sum += id; }
            static int reject(void) {
                if (!fail) return 0;
                fail = 0;
                ++rejected;
                return 1;
            }
            void *__wrap_malloc(size_t size) {
                if (reject()) return NULL;
                void *p = __real_malloc(size);
                if (p) ++live;
                return p;
            }
            void *__wrap_aligned_alloc(size_t align, size_t size) {
                if (reject()) return NULL;
                void *p = __real_aligned_alloc(align, size);
                if (p) ++live;
                return p;
            }
            void __wrap_free(void *p) {
                if (p) --live;
                __real_free(p);
            }
            int main(void) {
                int status = exercise();
                if (status) return status;
                return live == 0 && rejected == 2 && drops == 5 && sum == 109 ? 0 : 5;
            }
        """)
        self.process(VAQC, source, "--emit=obj", "-O2", "-o", "allocation.o")
        self.process("cc", consumer, "allocation.o", "-Wl,--wrap=malloc,--wrap=aligned_alloc,--wrap=free",
                     "-o", "allocation")
        self.process("./allocation")

    def test_freestanding_standard_packages(self):
        source = self.source("empty.vqr", """
            package empty;
            import (collections, io);
            export "C" func size() usize {
                var v = collections.Vec<u8>();
                v.len()
            }
        """)
        options = ["--target", "thumbv7m-none-eabi", "--cpu", "cortex-m3", "--profile", "freestanding"]
        self.process(VAQC, source, *options, "-O2", "--emit=obj", "-o", "empty.o")
        self.assertEqual(self.process("llvm-nm", "-u", "empty.o").stdout, b"")
        fail = self.source("fail.vqr", """
            package denied;
            import (collections);
            func allocate() !void {
                var v = collections.Vec<u8>();
                v.push(1)!;
            }
        """)
        diagnostic = self.process(VAQC, "check", fail, *options, good=False).stderr.decode()
        self.assertIn("no allocator for growing collections", diagnostic)

    def test_standard_intrinsics_require_standard_package(self):
        source = self.source("bad.vqr", """
            package collections;
            func steal(p: *i32) i32 { unsafe { __vqr_slot_take<i32>(p) } }
        """)
        result = self.process(VAQC, "check", source, good=False)
        self.assertIn(b"intrinsic is private to the Vaqrin standard library", result.stderr)

    def test_error_exit(self):
        source = self.source("bad.vqr", """
            package report;
            error Problem { Unavailable }
            func main() !void { Problem.Unavailable }
        """)
        self.process(VAQC, source, "-o", "program")
        result = self.process("./program", good=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"report.Problem.Unavailable", result.stderr)
        if not pathlib.Path("/dev/full").exists():
            return
        source = self.source("bad.vqr", """
            package report;
            import (io);
            func main() !void { io.println("write me")!; }
        """)
        self.process(VAQC, source, "-o", "program")
        with open("/dev/full", "wb") as device:
            result = subprocess.run(["./program"], cwd=self.root, stdout=device,
                                    stderr=subprocess.PIPE, timeout=25)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"IoError.WriteFailed", result.stderr)

    def test_ffi_debug(self):
        if not shutil.which("llvm-dwarfdump"):
            self.skipTest("llvm-dwarfdump is unavailable")
        lib = self.source("api.vqr", '''
            package api;
            extern "C" func plus(a, b: c_int) c_int;
            export "C" func twice(a: c_int) c_int {
                var result = unsafe { plus(a, a) };
                result
            }
        ''')
        c = self.source("caller.c", '''
            int plus(int a, int b) { return a + b; }
            extern int twice(int);
            int main(void) { return twice(21) != 42; }
        ''')
        self.process(VAQC, lib, "--emit=obj", "-g", "-o", "api.o")
        self.process("llvm-dwarfdump", "--verify", "api.o")
        dwarf = self.process("llvm-dwarfdump", "--debug-info", "api.o").stdout
        self.assertIn(b"twice", dwarf)
        self.assertIn(b"result", dwarf)
        self.assertIn(b"DW_TAG_formal_parameter", dwarf)
        self.process("cc", c, "api.o", "-o", "caller")
        self.process("./caller")

    def test_freestanding(self):
        if not shutil.which("arm-none-eabi-gcc"):
            self.skipTest("ARM GCC toolchain is unavailable")
        board = REPOSITORY / "boards" / "stm32f103c8"
        source = self.source("board.vqr", '''
            package board;
            var counter: u32 = 9;
            export "C" func app_write(reg: *u32, bits: u32) void {
                unsafe { volatileStore(reg, bits); }
            }
            export "C" func board_main() void {
                counter += 1;
            }
        ''')
        target = ["--target", "thumbv7m-none-eabi", "--cpu", "cortex-m3", "--profile", "freestanding"]
        self.process(VAQC, source, *target, "--emit=llvm-ir", "-o", "app.ll")
        self.assertIn("store volatile i32", (self.root / "app.ll").read_text())
        self.process("arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-c", board / "startup.S", "-o", "startup.o")
        self.process(VAQC, source, *target, "--cc", "arm-none-eabi-gcc", "--link-input", "startup.o",
                     "--linker-script", board / "board.ld", "-o", "firmware.elf")
        sections = self.process("arm-none-eabi-readelf", "-SW", "firmware.elf").stdout
        self.assertRegex(sections, rb"\.vectors\s+PROGBITS\s+08000000")
        self.assertEqual(self.process("arm-none-eabi-nm", "-u", "firmware.elf").stdout, b"")
        self.process("arm-none-eabi-objcopy", "-O", "binary", "firmware.elf", "firmware.bin")
        self.assertGreater((self.root / "firmware.bin").stat().st_size, 236)

    def test_build_artifacts(self):
        source = self.source("math.vqr", '''
            package math;
            export "C" func square(x: i32) i32 { x*x }
            func main() void {}
        ''')
        for kind, name in [("llvm-ir", "math.ll"), ("bc", "math.bc"), ("asm", "math.s"), ("obj", "math.o"),
                           ("staticlib", "libmath.a"), ("sharedlib", "libmath.so"), ("exe", "math")]:
            self.process(VAQC, source, f"--emit={kind}", "-o", name)
            self.assertTrue((self.root / name).stat().st_size)
        self.process("llvm-as", "math.ll", "-o", "checked.bc")
        self.process("llvm-dis", "math.bc", "-o", "checked.ll")
        self.process("./math")
        consumer = self.source("consumer.c", "extern int square(int); int main(void) { return square(7)!=49; }")
        for library in ("libmath.a", "libmath.so"):
            self.process("cc", consumer, f"./{library}", "-o", "consumer")
            self.process("./consumer")
        bad = self.source("bad.vqr", "package bad; func oops() i32 { false }")
        previous = (self.root / "math").read_bytes()
        self.process(VAQC, bad, "-o", "math", good=False)
        self.assertEqual((self.root / "math").read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
