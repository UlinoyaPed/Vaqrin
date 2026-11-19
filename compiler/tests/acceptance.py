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
        source = self.source("main.vqr", "package main\nimport (io)\n" + program)
        self.process(VAQC, *flags, source, "-o", "program")
        self.assertEqual(self.process("./program").stdout.decode(), output)

    def test_syntax_sema(self):
        self.source("geometry/math.vqr", '''
            package geometry
            pub func twice(x: i32) i32 { x * 2 }
        ''')
        source = self.source("root/main.vqr", '''
            package main
            import (geometry as g, io)
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
                bad = self.source("bad.vqr", "package t\n" + snippet)
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
                    package test
                    func typed(x: i32) i32 {{ 1 / x }}
                    const Bad = {expression};
                ''')
                self.assertIn("E2", self.process(VAQC, "check", bad, good=False).stderr.decode())
        for expression in ["a[-1]", "a[4294967296u64]", "a[3]", "i32(1) / z", "i32(2147483647) + one"]:
            with self.subTest(expression=expression):
                source = self.source("trap.vqr", f'''
                    package main
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
                a = move(a);
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
            import (std/core, std/text, coll as seq, iter)
            func twice(x: i32) i32 { x * 2 }
            func even(&x: *const i32) bool { x % 2 == 0 }
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
                for i, value in iter.enumerate<iter.SliceIter<i32>, i32>(iter.iter(array[:])) { io.println(value)!; }
                for value in iter.map<iter.SliceIter<i32>, i32, i32>(iter.iter(array[:]), twice) { io.println(value)!; }
                for value in iter.filter<iter.SliceIter<i32>, i32>(iter.iter(array[:]), even) { io.println(value)!; }
                for x, y in iter.zip<iter.SliceIter<i32>, iter.SliceIter<i32>, i32, i32>(iter.iter(array[:]), copy.iter()) { io.println(x + y)!; }
                for value in iter.take<iter.SliceIter<i32>, i32>(iter.iter(array[:]), 2) { io.println(value)!; }
                for value in iter.skip<iter.SliceIter<i32>, i32>(iter.iter(array[:]), 2) { io.println(value)!; }
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

    def test_optional_payloads_and_collection_aliases(self):
        self.hosted('''
            import (std/opt, coll, std/coll as seq, iter)
            func missing(x: i32) ?i32 { null }
            func positive(x: i32) ?i32 {
                if x > 0 { return x; }
                null
            }
            func tag(x: ??i32) i32 {
                switch x { null => 0, some(null) => 1, some(some(_)) => 2 }
            }
            func sequenceLen(v: *seq.Vec<?i32>) usize { v.len() }
            func main() !void {
                var v = coll.Vec<?i32>();
                v.push(null)!;
                io.println(sequenceLen(&v))!;
                var cursor = v.iter();
                io.println(tag(cursor.next()))!;
                io.println(tag(cursor.next()))!;
                io.println(tag(v.pop()))!;
                io.println(tag(v.pop()))!;
                var nested = opt.map<i32, ?i32>(7, missing);
                io.println(tag(nested))!;
                io.println(opt.flatten<i32>(nested) ?? 9)!;
                io.println(opt.andThen<i32, i32>(7, missing) ?? 8)!;
                var data = [_]i32{0, 3};
                var mapped = iter.map<iter.SliceIter<i32>, i32, ?i32>(iter.iter(data[:]), positive);
                io.println(tag(mapped.next()))!;
                io.println(tag(mapped.next()))!;
                var kept = iter.filterMap<iter.SliceIter<i32>, i32, i32>(iter.iter(data[:]), positive);
                io.println(kept.next() ?? -1)!;
                io.println(kept.next() ?? -1)!;
            }
        ''', "1\n1\n0\n1\n0\n1\n9\n8\n1\n2\n3\n-1\n")

    def test_dynamic_text(self):
        self.hosted('''
            import (core, text)
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
            import (coll)
            class Probe {
                var id: i32;
            public:
                func init(id: i32) : id(id) {}
                const func get() i32 { id }
                func deinit() void { io.println(id + 100) catch {}; }
            }
            func main() !void {
                var items = coll.Vec<Probe>();
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
            package allocation
            import (core, coll, text)
            extern "C" func fail_next() void;
            extern "C" func dropped(id: c_int) void;
            class Probe {
                var id: i32;
            public:
                func init(id: i32) : id(id) {}
                func deinit() void { unsafe { dropped(id); } }
            }
            func check() !i32 {
                var vec = coll.Vec<Probe>();
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
            package empty
            import (coll, io)
            export "C" func size() usize {
                var v = coll.Vec<u8>();
                v.len()
            }
        """)
        options = ["--target", "thumbv7m-none-eabi", "--cpu", "cortex-m3", "--profile", "freestanding"]
        self.process(VAQC, source, *options, "-O2", "--emit=obj", "-o", "empty.o")
        self.assertEqual(self.process("llvm-nm", "-u", "empty.o").stdout, b"")
        fail = self.source("fail.vqr", """
            package denied
            import (coll)
            func allocate() !void {
                var v = coll.Vec<u8>();
                v.push(1)!;
            }
        """)
        diagnostic = self.process(VAQC, "check", fail, *options, good=False).stderr.decode()
        self.assertIn("no allocator for growing collections", diagnostic)

    def test_standard_intrinsics_require_standard_package(self):
        source = self.source("bad.vqr", """
            package coll
            func steal(p: *i32) i32 { unsafe { __vqr_slot_take<i32>(p) } }
        """)
        result = self.process(VAQC, "check", source, good=False)
        self.assertIn(b"intrinsic is private to the Vaqrin standard library", result.stderr)

    def test_error_exit(self):
        source = self.source("bad.vqr", """
            package report
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
            package report
            import (io)
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
            package api
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
            package board
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

    def test_file_entries_and_discovery(self):
        a = self.source("a.vqr", "func main() i32 { twice(solve()) } func solve() i32 { 5 }")
        b = self.source("b.vqr", "func main() !i32 { 22 } func solve() i32 { 99 }")
        self.source("helpers.vqr", "func twice(n: i32) i32 { n * 2 }")
        self.source("unfinished.vqr", "func unfinished() i32 { @; }")
        self.source("build/ignored.vqr", "func main() void {}")
        def status(code, *inputs):
            self.process(VAQC, "build", *inputs, "-o", "app")
            self.assertEqual(self.process("./app", good=code == 0).returncode, code)
            symbols = self.process("llvm-nm", "--defined-only", "app").stdout.splitlines()
            self.assertEqual(sum(line.endswith(b" T main") for line in symbols), 1)
        status(10, a)
        status(22, b)
        error = self.process(VAQC, self.root, good=False).stderr
        for filename in (b"a.vqr", b"b.vqr"):
            self.assertIn(filename, error)
        self.assertNotIn(b"ignored.vqr", error)
        self.source("package.toml", "[package]\nname = 'test'\n[build]\nentry = 'a.vqr' # default\n")
        status(10, self.root)
        status(22, self.root, "--entry", "b.vqr")
        status(22, a, b, "--entry", b)
        self.assertIn(b"explicitly listed", self.process(VAQC, a, "--entry", b, good=False).stderr)
        self.assertIn(b"inside", self.process(VAQC, self.root, "--entry", "../outside.vqr", good=False).stderr)
        c = self.source("c.vqr", "func main() i32 { solve() }")
        self.assertIn(b"ambiguous declaration", self.process(VAQC, c, good=False).stderr)
        c.write_text("func main() i32 { b.solve() }")
        status(99, c)
        a.write_text("package demo func main() i32 { shared() }")
        b.write_text("package demo func shared() i32 { 17 } func main() void {}")
        status(17, a)
        status(17, a, b, "--entry", a)
        c.write_text("func main() i32 { shared() }")
        self.assertIn(b"shared", self.process(VAQC, c, good=False).stderr)

    def test_file_imports_and_recursive_entry(self):
        entry = self.source("src/root.vqr", 'import ("lib/helper-tools.vqr" as h, "lib/./helper-tools.vqr" as j) '
                            "func main() i32 { h.answer() + j.answer() }")
        dep = self.source("src/lib/helper-tools.vqr", "pub func answer() i32 { secret() } func secret() i32 { 3 }")
        self.process(VAQC, self.root, "-o", "app")
        self.assertEqual(self.process("./app", good=False).returncode, 6)
        entry.write_text('import ("lib/helper-tools.vqr" as h) func main() i32 { h.secret() }')
        self.assertIn(b"private", self.process(VAQC, entry, good=False).stderr)
        dep.write_text("package demo pub func secret() i32 { 3 }")
        self.assertIn(b"logical import", self.process(VAQC, entry, good=False).stderr)
        dep.write_text('import ("../root.vqr" as root) pub func secret() i32 { 3 }')
        self.assertIn(b"cyclic", self.process(VAQC, entry, good=False).stderr)
        entry.write_text("func main() i32 { even(4) }")
        self.source("src/e.vqr", "func even(n: i32) i32 { if (n == 0) 1 else odd(n-1) }")
        self.source("src/o.vqr", "func odd(n: i32) i32 { if (n == 0) 0 else even(n-1) }")
        self.process(VAQC, entry, "-o", "app")
        self.assertEqual(self.process("./app", good=False).returncode, 1)

    def test_module_identity_and_unused_code(self):
        self.source("a/utils/one.vqr", "package utils pub func value() i32 { 1 }")
        self.source("b/utils/two.vqr", "package utils pub func value() i32 { 2 }")
        self.source("invalid_entry.vqr", "func main() bool { true }")
        self.source("app.vqr", '''
            import (a/utils as a, b/utils as b)
            extern "C" func missing() void;
            pub func unused() void { unsafe { missing(); } }
            func main() i32 { a.value() + b.value() }
        ''')
        self.process(VAQC, self.root, "-o", "app")
        self.assertEqual(self.process("./app", good=False).returncode, 3)

    def test_unavailable_hosted_packages_have_target_diagnostics(self):
        packages = {"task": "var group = task.TaskGroup();",
                    "channel": "var ends = channel.bounded<i32>(1) catch { return; };", "net": ""}
        for package, operation in packages.items():
            with self.subTest(package=package):
                source = self.source("target.vqr", f"import ({package}) func f() void {{ {operation} }}")
                result = self.process(VAQC, "check", source, "--target", "thumbv7m-none-eabi",
                                      "--profile", "freestanding", good=False)
                self.assertIn(b"E290", result.stderr)
                self.assertIn(package.encode(), result.stderr)

    def test_revision_diagnostics(self):
        c_entry = self.source("native.vqr", 'export "C" func main() i32 { 0 }')
        self.process(VAQC, c_entry, "--emit=obj", "-o", "native.o")
        self.assertIn(b" T main", self.process("llvm-nm", "native.o").stdout)
        self.assertIn(b"C symbol main", self.process(VAQC, c_entry, good=False).stderr)
        for code, diagnostic in (
            ("package x; func main() void {}", "semicolon"),
            ("import (io); func main() void {}", "semicolon"),
            ("func main() void {} func main(x: i32) void {}", "file-private"),
            ("pub func main() void {}", "file-private"),
            ("func main(a: i32) void {}", "no parameters"),
            ("func main() bool { true }", "main must return"),
            ("func f() (i32,i32) { return 1, 2; }", "tuple"),
            ("func f(x: i32) void { x move= x; }", "expected"),
            ("func f() i32 { if (true) 7 }", "else"),
            ("func f() never {}", "never"),
            ("func f(p: *i32) void { &x := p; &x := p; }", "duplicate local"),
            ("func f(p: *const i32) void { &x := p; x = 2; }", "writable"),
            ("func f(p: *i32) void { &x := p; y := move(x); }", "cannot be moved"),
            ("func f() void { var &x: *i32; }", "initialized pointer"),
            ("func f(p: ?*i32) void { &x := p; }", "initialized pointer"),
            ("func f(p: !*i32) void { &x := p; }", "initialized pointer"),
            ("func f(p: *i32) void { var &x: i32 = p; }", "i32"),
            ("func f(&x: *i32) void {} func f(p: *i32) void {}", "duplicate overload"),
            ("func f(p: *const ?i32) void { switch *p { some(&x) => { x++; }, null => {} } }", "writable"),
            ("func f(v: ?i32) void { switch v { some(&x) => { a := move(v); }, null => {} } }", "cannot be moved"),
            ("enum E { A(i32), B(i32) } func f(v: E) i32 { switch v { E.A(x), E.B(&x) => x } }", "alternative"),
        ):
            with self.subTest(source=code):
                f = self.source("test.vqr", code)
                self.assertIn(diagnostic.encode(), self.process(VAQC, "check", f, good=False).stderr)

    def test_borrowing_and_control_flow(self):
        self.hosted("""
            enum Event { Link(*i32), Num(i32) }
            func inc(&x: *i32) void { x++; }
            func pair(x: i32, p: *i32) (i32, *i32) { return (x, p); }
            class C { private: pub var n: i32; pub const func value() !i32 { n } }
            func abort() never { panic("stop") }
            func liveValue(okay: bool) i32 { if okay { 7 } else { abort() } }
            func main() !void {
                a := 1; b := 8; p := &a;
                &x := p;
                var &readOnly: *const i32 = p;
                f := [&x]() void => { x++; };
                p = &b; f(); inc(&x);
                pp := &p; &slot := pp; slot = &a;
                n, &z := pair(4, p); z += n;
                event := Event.Link(&a);
                switch event {
                    Event.Link(&ptr) => { &v := ptr; v++; },
                    Event.Num(&v) => { v++; },
                }
                for (&v in [_]*i32{&a, &b}) { v++; }
                io.println(readOnly)!; io.println(b)!;
                c := C{n: 9}; maybe := some(c);
                var nested: ?!i32 = maybe?.value();
                io.println(nested.unwrap()!)!;
                i := 0;
                for (i < 3) { i++; }
                io.println(if (i == 3) liveValue(true) else 0)!;
            }
        """, "9\n9\n9\n7\n")

    def test_optional_core_and_iterator_contracts(self):
        self.hosted("""
            import (core, opt, iter, coll)
            error Broken { Stop }
            func bumped(&x: *const i32) bool { x > 3 }
            func sum(x, y: i32) i32 { x + y }
            func seven() i32 { 7 }
            func noReturn(message: str) never { panic(message) }
            func wouldTrap() i32 { unsafe { core.unreachableUnchecked() } }
            func main() !void {
                a := 18; b := 2;
                core.swap(&a, &b); core.swap(&a, &a);
                io.println(core.replace(&b, core.identity(a)))!;
                core.drop(b);
                var o: ?i32 = null;
                io.println(opt.isNone(&o))!;
                var &value = opt.getOrInsert(&o, 3);
                value += 2;
                opt.getOrInsertWith(&o, seven);
                io.println(*opt.asPtr(&o).unwrap())!;
                io.println(opt.replace(&o, 8).unwrap())!;
                io.println(opt.take(&o).unwrap())!;
                io.println(opt.isNone(&o))!;
                &newValue := opt.insert(&o, 9);
                newValue--;
                io.println(opt.contains(&o, &newValue))!;
                seed := 3;
                transform := [seed](n: i32) ?i32 => { if (n > seed) some(n) else null };
                io.println(opt.andThen<i32, i32>(o, move(transform)).unwrap())!;
                filtered := opt.filter(o, bumped);
                seen := 0;
                observe := [&seen](p: *const i32) void => { seen = *p; };
                opt.inspect(filtered, move(observe));
                io.println(seen)!;
                x, y := opt.unzip<i32, i32>(opt.zip<i32, i32>(o, some(4)));
                io.println(opt.zipWith<i32, i32, i32>(x, y, sum).unwrap())!;
                io.println(opt.unwrapOrElse<i32>(opt.xor<i32>(o, null), seven))!;
                io.println(opt.unwrapOr<i32>(null, 2))!;
                io.println(opt.expect(o, "expected present"))!;
                var bad: ?!i32 = some(Broken.Stop);
                io.println((opt.transpose(bad) catch some(23)) ?? 0)!;
                var nested: ?!i32 = some(ok(11));
                io.println(opt.transpose(nested)!.unwrap())!;
                var items = opt.toVec(o)!;
                items.push(9)!; items.push(10)!;
                for (index, &item in enumerate(items.iterMut())) {
                    item += i32(index)!;
                }
                var cursor = items.iterRef();
                io.println(*cursor.nextBack().unwrap())!;
                io.println(cursor.remaining())!;
                io.println(*cursor.next().unwrap())!;
                var once = opt.intoIter<i32>(opt.take(&o));
                io.println(once.next().unwrap())!;
                var exhausted = once.next(); io.println(opt.isNone(&exhausted))!;
                var listed = iter.iter(items.asConstSlice());
                io.println(listed.sizeHint().lower)!;
                for index, &count in iter.enumerate<iter.SliceIterMut<i32>, *i32>(items.iterMut()) {
                    count += i32(index)!;
                }
                io.println(*items.last().unwrap())!;
                io.println(core.isNone(&o))!;
            }
        """, "18\ntrue\n5\n5\n8\ntrue\ntrue\n8\n8\n12\n8\n2\n8\n23\n11\n12\n2\n8\n8\ntrue\n3\n14\ntrue\n")

    def test_result_contracts(self):
        self.hosted("""
            import (result, core, iter)
            error Problem { Missing }
            func plus(x: i32) i32 { x + 1 }
            func twice(x: i32) !i32 { x * 2 }
            func recover(e: ErrorId) !i32 { if (e == Problem.Missing) 10 else 0 }
            func recoverValue(e: ErrorId) i32 { if (e == Problem.Missing) 11 else 0 }
            func main() !void {
                var original: !i32 = 2;
                var failed: !i32 = Problem.Missing;
                io.println(result.isOk(&original))!;
                io.println(result.isErr(&failed))!;
                io.println(result.errorOf(&failed).unwrap() == Problem.Missing)!;
                io.println(*result.okValue(&original).unwrap())!;
                io.println(result.map<i32, i32>(original, plus)!)!;
                io.println(result.andThen<i32, i32>(original, twice)!)!;
                io.println(result.orElse(failed, recover)!)!;
                io.println(result.unwrapOr(failed, 6))!;
                io.println(result.unwrapOrElse(failed, recoverValue))!;
                io.println(result.expect(original, "failed"))!;
                seen := 0;
                observe := [&seen](p: *const i32) void => { seen += *p; };
                onError := [&seen](e: ErrorId) void => { if e == Problem.Missing { seen += 7; } };
                result.inspect(original, move(observe))!;
                var checked = result.inspectErr(failed, move(onError));
                io.println(result.isErr(&checked))!;
                io.println(seen)!;
                var missing: !?i32 = null;
                var absent = result.transpose(missing);
                io.println(core.isNone(&absent))!;
                var present: !?i32 = some(9);
                io.println(result.transpose(present).unwrap()!)!;
                var rejected: !?i32 = Problem.Missing;
                io.println(result.transpose(rejected).unwrap() catch 13)!;
                values := [_]!i32{4, 5};
                var results = iter.iter(values[:]);
                var collected = result.collect<i32>(move(results))!;
                io.println(collected[0] + collected[1])!;
            }
        """, "true\ntrue\ntrue\n2\n3\n4\n10\n6\n11\n2\ntrue\n9\ntrue\n9\n13\n9\n")

    def test_layout_ordering_and_vec_contracts(self):
        self.hosted("""
            import (cmp, meta, coll, core, iter)
            const width = meta.bitWidth<u24>();
            const stride = meta.strideOf<[3]u32>();
            const typeLabel = meta.typeName<i64>();
            func shorter(&a: *const i32, &b: *const i32) bool { a < b }
            func main() !void {
                io.println(width)!; io.println(stride)!; io.println(typeLabel)!;
                io.println(meta.alignOf<u32>())!;
                io.println(meta.isTriviallyDrop<i32>())!;
                io.println(meta.isCopyable<coll.Vec<i32>>())!;
                io.println(meta.isMovable<coll.Vec<i32>>())!;
                io.println(meta.isZeroSized<[0]u8>())!;
                a := -0.0f64; b := 0.0f64;
                io.println(cmp.totalCmp(&a, &b) == cmp.Ordering.Less)!;
                c := bitCast<f64>(0x7ff8000000000001u64);
                var unordered = cmp.partialCmp(&b, &c);
                io.println(core.isNone(&unordered))!;
                io.println(cmp.totalCmp(&b, &c) == cmp.Ordering.Less)!;
                io.println(cmp.minBy(3, 2, shorter))!;
                io.println(cmp.clamp(8, 2, 5))!;
                var input = [_]i32{1, 2, 3};
                io.println(meta.arrayLen(&input))!;
                var v = coll.Vec<i32>.fromSlice(input[:])!;
                v.reserveAdditional(5)!;
                v.insert(1, 9)!;
                v.insert(v.len(), 8)!;
                io.println(v.remove(1).unwrap())!;
                io.println(v.swapRemove(0).unwrap())!;
                v.reverse();
                io.println(v[0])!;
                io.println(v[2])!;
                v.truncate(2);
                v.shrinkToFit()!;
                io.println(v.capacity())!;
                v.clear();
                v.shrinkToFit()!;
                io.println(v.capacity())!;
                v.push(15)!;
                io.println(v[0])!;
                var source = iter.iter(input[:]);
                var copy = coll.Vec<i32>.fromIter(move(source))!;
                io.println(copy[1])!;
            }
        """, "24\n12\ni64\n4\ntrue\nfalse\ntrue\ntrue\ntrue\ntrue\ntrue\n2\n5\n3\n9\n1\n3\n8\n2\n0\n15\n2\n")

    def test_bits_and_compile_time_word_size(self):
        self.hosted("""
            import (bits, core)
            const count = bits.countOnes(0xabc123u24);
            const leading = bits.leadingZeros(0x123u24);
            const rev = bits.reverseBits(1u7);
            const rotated = bits.rotateLeft(0x81u8, 9);
            func main() !void {
                io.println(count)!; io.println(leading)!;
                io.println(rev)!; io.println(rotated)!;
                io.println(bits.trailingZeros(0u7))!;
                io.println(bits.countZeros(1u7))!;
                io.println(bits.reverseBytes(0x123456u24))!;
                io.println(bits.byteSwap(0x1234u16))!;
                io.println(bits.rotateRight(1u7, 1))!;
                io.println(bits.firstSet(128u8).unwrap())!;
                io.println(bits.lastSet(130u8).unwrap())!;
                var empty = bits.firstSet(0u8);
                io.println(core.isNone(&empty))!;
                io.println(bits.clearBit(128u8, 7))!;
                io.println(bits.toggleBit(0u8, 7))!;
                io.println(bits.testBit(128u8, 7))!;
                io.println(bits.maskLow<u24>(24))!;
                io.println(bits.extractBits(0x124u16, 4, 4))!;
                io.println(bits.extractBits(0x124u16, 16, 0))!;
                io.println(bits.insertBits(0x102u16, 0xffu16, 4, 4))!;
                io.println(bits.nextPowerOfTwo(4u8)!)!;
                io.println(bits.nextPowerOfTwo(5u8)!)!;
                io.println(bits.nextPowerOfTwo(127i8) catch 22i8)!;
                io.println(bits.alignUp(17, 16)!)!;
                io.println(bits.alignDown(17, 16))!;
                var data = [_]u8{0, 0};
                var writer = bits.BitWriter(data[:], bits.BitOrder.MsbFirst);
                writer.writeBits(5, 3)!;
                var failed = writer.writeBits(0, 14);
                io.println(core.isErr(&failed))!;
                writer.alignByte(true)!;
                writer.writeBits(1, 8)!;
                io.println(writer.writtenBytes())!;
                io.println(data[0])!;
                var reader = bits.BitReader(data[:], bits.BitOrder.MsbFirst);
                io.println(reader.peekBits(3)!.unwrap())!;
                io.println(reader.position())!;
                io.println(reader.readBits(3)!.unwrap())!;
                reader.alignByte();
                io.println(reader.readBits(8)!.unwrap())!;
                var eof = reader.readBits(1)!;
                io.println(core.isNone(&eof))!;
                var little = bits.BitReader(data[:], bits.BitOrder.LsbFirst);
                io.println(little.readBits(3)!.unwrap())!;
            }
        """, "11\n15\n64\n3\n7\n6\n5649426\n13330\n64\n7\n7\ntrue\n0\n128\ntrue\n16777215\n2\n0\n498\n4\n8\n22\n32\n16\ntrue\n2\n191\n5\n0\n5\n1\ntrue\n7\n")

    def test_slice_search_and_heap(self):
        self.hosted("""
            import (algo, core)
            func less(&a, &b: *const i32) bool { a < b }
            func equal(&a, &b: *const i32) bool { a == b }
            func positive(&value: *const i32) bool { value > 0 }
            func main() !void {
                var values = [_]i32{7, 3, 2, 3, 4};
                var key = 3;
                io.println(algo.allOf(values[:], positive))!;
                io.println(algo.anyOf(values[:], positive))!;
                io.println(algo.noneOf(values[:], positive))!;
                io.println(algo.countIf(values[:], positive))!;
                io.println(algo.count(values[:], &key))!;
                io.println(algo.find(values[:], &key).unwrap())!;
                io.println(algo.findLast(values[:], &key).unwrap())!;
                io.println(algo.findIf(values[:], positive).unwrap())!;
                var missing = algo.findIfNot(values[:], positive);
                io.println(core.isNone(&missing))!;
                algo.heapify(values[:], less);
                io.println(algo.isHeap(values[:], less))!;
                algo.popHeap(values[:], less);
                io.println(values[4])!;
                algo.pushHeap(values[:], less);
                io.println(values[0])!;
                algo.sortHeap(values[:], less);
                io.println(algo.isSorted(values[:], less))!;
                lo, hi := algo.equalRange(values[:], &key, less);
                io.println(lo)!; io.println(hi)!;
                io.println(algo.lowerBound(values[:], &key, less))!;
                io.println(algo.upperBound(values[:], &key, less))!;
                io.println(algo.binarySearch(values[:], &key, less).unwrap())!;
                io.println(algo.adjacentFind(values[:], equal).unwrap())!;
                algo.rotate(values[:], 2);
                io.println(values[4])!;
                algo.reverse(values[:]);
                io.println(values[0])!;
                offset := -1;
                descending := [offset](&a, &b: *const i32) bool => a * offset < b * offset;
                algo.sortBy(values[:], move(descending));
                io.println(values[0])!;
                algo.sort(values[:]);
                io.println(values[0])!;
            }
        """, "true\ntrue\nfalse\n5\n2\n1\n3\n0\ntrue\ntrue\n7\n7\ntrue\n1\n3\n1\n3\n1\n1\n3\n3\n7\n2\n")

    def test_atomic_orders_and_target_capability(self):
        source = self.source("atomics.vqr", """
            import (atomic, core, io)
            const supported = atomic.isAlwaysLockFree<u32>();
            func main() !void {
                var value = atomic.Atomic<i32>.new(1);
                io.println(supported)!;
                io.println(value.fetchAdd(2, atomic.Order.AcqRel))!;
                io.println(value.load(atomic.Order.Acquire))!;
                var exchanged = value.compareExchange(3, 7, atomic.Order.AcqRel, atomic.Order.Acquire);
                io.println(exchanged.old)!; io.println(exchanged.exchanged)!;
                var rejected = value.compareExchange(3, 8);
                io.println(rejected.old)!; io.println(rejected.exchanged)!;
                io.println(value.fetchSub(1))!;
                io.println(value.fetchOr(8))!;
                io.println(value.fetchAnd(11))!;
                io.println(value.fetchXor(1))!;
                io.println(value.fetchMax(-5))!;
                io.println(value.fetchMin(-4))!;
                io.println(value.load())!;
                var flag = atomic.AtomicFlag(false);
                io.println(flag.testAndSet())!;
                io.println(flag.test())!;
                flag.clear(atomic.Order.Release);
                io.println(flag.test(atomic.Order.Acquire))!;
                n := 19;
                var slot = atomic.AtomicPtr<i32>.new(null);
                var empty = slot.swap(&n);
                io.println(core.isNone(&empty))!;
                io.println(*slot.load().unwrap())!;
                var removed = slot.compareExchange(&n, null);
                io.println(*removed.old.unwrap())!; io.println(removed.exchanged)!;
                atomic.compilerFence(atomic.Order.Acquire);
                atomic.fence(atomic.Order.SeqCst);
                io.println(atomic.supportedWidths().len())!;
            }
        """)
        self.process(VAQC, source, "-o", "atomic-test")
        self.assertEqual(self.process("./atomic-test").stdout.decode(),
                         "true\n1\n3\n3\ntrue\n7\nfalse\n7\n6\n14\n10\n11\n11\n-4\nfalse\ntrue\nfalse\ntrue\n19\n19\ntrue\n4\n")
        bare = self.source("bare.vqr", """
            import (atomic)
            export "C" func update(p: *atomic.Atomic<u32>) u32 {
                p.fetchAdd(1, atomic.Order.SeqCst)
            }
        """)
        flags = ("--target", "thumbv7m-none-eabi", "--cpu", "cortex-m3", "--profile", "freestanding")
        self.process(VAQC, bare, *flags, "--emit=obj", "-O2", "-o", "bare.o")
        self.assertEqual(self.process("llvm-nm", "-u", "bare.o").stdout, b"")
        bare.write_text("import (atomic) func unsupported() void { x := atomic.Atomic<u64>(0); }")
        result = self.process(VAQC, "check", bare, *flags, good=False)
        self.assertIn(b"does not support native atomics", result.stderr)
        invalid = self.source("invalid.vqr", """
            import (atomic)
            func main() i32 {
                var x = atomic.Atomic<i32>(1);
                x.load(atomic.Order.Release)
            }
        """)
        self.process(VAQC, invalid, "-o", "invalid")
        self.assertLess(self.process("./invalid", good=False).returncode, 0)

    def test_binary_endianness_and_cursor(self):
        self.hosted("""
            import (binary, core)
            func main() !void {
                var bytes = [32]u8{0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0};
                var writer = binary.Writer(bytes[:], binary.Endian.Big);
                writer.writeU16(258)!;
                writer.writeI32(-6)!;
                writer.writeF64(-0.5)!;
                writer.writeVarUint(300)!;
                writer.writeVarInt(-3)!;
                writer.writeU8(3)!;
                var payload = [_]u8{31, 32, 33};
                writer.writeBytes(payload[:])!;
                io.println(writer.position())!;
                var reader = binary.Reader(writer.written(), binary.Endian.Big);
                io.println(bytes[0])!; io.println(bytes[1])!;
                io.println(reader.readU16()!)!;
                io.println(reader.readI32()!)!;
                io.println(reader.readF64()!)!;
                io.println(reader.readVarUint()!)!;
                io.println(reader.readVarInt()!)!;
                var rejected = reader.readLengthPrefixed(2);
                io.println(core.isErr(&rejected))!;
                io.println(reader.position())!;
                var payloadRead = reader.readLengthPrefixed(3)!;
                io.println(payloadRead[2])!;
                var end = reader.readU8();
                io.println(core.isErr(&end))!;
                reader.seek(0)!;
                io.println(reader.readI8()!)!;
                var small = [_]u8{8};
                var failedWrite = binary.writeVarUint(300, small[:]);
                io.println(core.isErr(&failedWrite))!;
                io.println(small[0])!;
                var invalid = [_]u8{128, 0};
                var bad = binary.readVarUint(invalid[:]);
                io.println(core.isErr(&bad))!;
                var value = binary.u32ToBytes(0x01020304u32, binary.Endian.Little);
                io.println(value[0])!;
                io.println(binary.readU32(value[:], binary.Endian.Little)!)!;
                binary.writeF32(value[:], 1.25f32, binary.Endian.Native)!;
                io.println(binary.readF32(value[:], binary.Endian.Native)!)!;
                io.println(binary.zigZagDecode(binary.zigZagEncode(bitCast<i64>(0x8000000000000000u64))))!;
            }
        """, "21\n1\n2\n258\n-6\n-0.5\n300\n-3\ntrue\n17\n33\ntrue\n1\ntrue\n8\ntrue\n4\n16909060\n1.25\n-9223372036854775808\n")

    def test_comptime_nominal_types_and_members(self):
        self.hosted("""
            import (core)
            comptime const size: usize = 3;
            type Buffer<T, comptime N: usize> = [N]T;
            class Sized<T, comptime N: usize> {
            public:
                var data: Buffer<T, N>;
                func init(data: Buffer<T, N>) : data(move(data)) {}
                const func len() usize { N }
                const func tail() [1]T { [1]T{data[N - 1]} }
                comptime if N == 3 {
                    const func marker() i32 { 3 }
                } else {
                    const func marker() i32 { 1 }
                    var different: MissingType = invalid;
                }
            }
            class Selection<comptime Enabled: bool> {
                comptime if Enabled {
                    pub static func selected() i32 { 10 }
                } else {
                    pub static func selected() i32 { 20 }
                }
            }
            enum Code<comptime N: usize> {
                Well = N,
                Next,
            }
            func length<T, comptime N: usize>(value: *const Sized<T, N>) usize { N }
            func main() !void {
                var one = Sized<i32, size>([_]i32{1, 2, 3});
                var two = Sized<i32, (1 + 2)>([_]i32{6, 7, 8});
                one = move(two);
                io.println(length(&one))!;
                io.println(one.len())!;
                io.println(one.marker())!;
                io.println(one.tail()[0])!;
                io.println(Selection<true>.selected())!;
                io.println(Selection<false>.selected())!;
                var code = Code<4>.fromInt(5);
                var missing = Code<4>.fromInt(6);
                io.println(core.isSome(&code))!;
                io.println(core.isNone(&missing))!;
            }
        """, "3\n3\n3\n8\n10\n20\ntrue\ntrue\n")

    def test_raw_storage_and_fixed_vector(self):
        self.hosted("""
            import (coll, core, mem, meta)
            class Probe {
                var number: i32;
            public:
                func init(number: i32) : number(number) {}
                const func id() i32 { number }
                func deinit() void { io.println(number) catch {}; }
            }
            func even(&n: *const i32) bool { n % 2 == 0 }
            func cleanup<F: Movable>(callback: F) mem.ScopeGuard<F> {
                mem.ScopeGuard<F>(move(callback))
            }
            func main() !void {
                io.println(meta.sizeOf<mem.MaybeUninit<[5]u32>>() == meta.sizeOf<[5]u32>())!;
                io.println(meta.alignOf<mem.MaybeUninit<u64>>() == meta.alignOf<u64>())!;
                var empty = coll.FixedVec<i32, 0>();
                switch empty.tryPush(3) {
                    coll.PushResult.Full(value) => io.println(value)!,
                    else => panic("zero capacity"),
                }
                var numbers = coll.FixedVec<i32, 4>();
                numbers.push(1); numbers.push(2); numbers.push(4);
                numbers.insert(2, 6);
                numbers.retain(even);
                io.println(numbers.len())!;
                io.println(numbers.asConstSlice()[1])!;
                &alias := numbers.getMut(2).unwrap();
                alias = 8;
                var cursor = coll.FixedVec<i32, 4>.intoIter(move(numbers));
                io.println(cursor.nextBack().unwrap())!;
                io.println(cursor.next().unwrap())!;
                io.println(cursor.next().unwrap())!;
                var end = cursor.next();
                io.println(core.isNone(&end))!;
                {
                    var probes = coll.FixedVec<Probe, 2>();
                    probes.push(Probe(11)); probes.push(Probe(12));
                    switch probes.push(Probe(13)) {
                        coll.PushResult.Full(value) => io.println(value.id())!,
                        else => panic("capacity"),
                    }
                    var first = probes.remove(0).unwrap();
                    io.println(first.id())!;
                }
                var slot = mem.MaybeUninit<Probe>.uninit();
                unsafe { slot.write(Probe(21)); slot.assumeInitDrop(); }
                var manually = mem.ManuallyDrop<Probe>.new(Probe(22));
                core.drop<Probe>(mem.ManuallyDrop<Probe>.intoInner(move(manually)));
                var ran = 0;
                {
                    var callback = [&ran]() void => { ran++; };
                    var guard = cleanup(move(callback));
                    io.println(guard.isArmed())!;
                    guard.runNow();
                    guard.dismiss();
                }
                io.println(ran)!;
            }
        """, "true\ntrue\n3\n3\n6\n8\n2\n6\ntrue\n13\n13\n11\n11\n12\n21\n22\ntrue\n1\n")

    def test_memory_layout_transferability_and_mmio(self):
        self.hosted("""
            import (core, coll, cmp, mem, meta, mmio)
            class Node {
                var next: ?^Node;
            }
            func inspect<T: Sendable & Shareable>(value: T) bool { true }
            func main() !void {
                var byte = mem.Layout.fromSizeAlign(1, 1)!;
                var wordLayout = mem.layout<u32>();
                var layout = byte.extend(wordLayout)!;
                io.println(layout.size())!;
                io.println(layout.align())!;
                var empty = mem.Layout.array<u64>(0)!;
                io.println(empty.size())!;
                var wrong = mem.Layout.fromSizeAlign(6, 3);
                io.println(core.isErr(&wrong))!;
                var overflow = mem.Layout.array<u64>(~usize(0));
                io.println(core.isErr(&overflow))!;
                var bytes = [_]u8{1, 2, 3, 4, 5, 6};
                mem.moveBytes(bytes[0..4], bytes[1..5])!;
                io.println(bytes[1])!;
                io.println(bytes[4])!;
                mem.copyBytes(bytes[1..5], bytes[0..4])!;
                io.println(bytes[0])!;
                io.println(bytes[3])!;
                io.println(mem.compareBytes(bytes[0..2], bytes[0..3]) == cmp.Ordering.Less)!;
                mem.zeroBytes(bytes[0..2]);
                io.println(bytes[1])!;
                io.println(inspect(1))!;
                io.println(meta.isSendable<^Node>())!;
                io.println(meta.isShareable<coll.FixedVec<u8, 2>>())!;
                io.println(meta.isSendable<coll.Vec<*i32>>())!;
                io.println(meta.isShareable<str>())!;
                var word: u32 = 3;
                var lhs = mmio.MmioRegion.new(mmio.addressOf(&word), 4);
                unsafe {
                    var reg = lhs.registerAt<u32>(0)!;
                    reg.setBits(4);
                    reg.clearBits(2);
                    io.println(reg.read())!;
                    var invalid = lhs.registerAt<u32>(1);
                    io.println(core.isErr(&invalid))!;
                    var flag = mmio.Volatile<u32>.new(&word);
                    flag.store(9);
                    io.println(flag.load())!;
                }
            }
        """, "8\n4\n0\ntrue\ntrue\n1\n4\n1\n4\ntrue\n0\ntrue\nfalse\ntrue\nfalse\nfalse\n5\ntrue\n9\n")
        source = self.source("mmio.vqr", """
            import (mmio)
            export "C" func update(register: *u32) void {
                unsafe { mmio.volatileStore(register, 7); }
                mmio.compilerBarrier();
                mmio.memoryBarrier();
                mmio.instructionBarrier();
            }
        """)
        self.process(VAQC, source, "--target", "thumbv7m-none-eabi", "--cpu", "cortex-m3",
                     "--profile", "freestanding", "--emit=asm", "-O2", "-o", "mmio.s")
        assembly = (self.root / "mmio.s").read_text()
        self.assertRegex(assembly, r"\bdmb\b")
        self.assertRegex(assembly, r"\bisb\b")

    def test_interfaces_and_embedded_receivers(self):
        self.hosted("""
            import (meta)
            interface Readable<T> { const func read() T; }
            interface Writable { func write(value: i32) void; }
            interface Comparable { const func same(other: *const Self) bool; }
            class Cell {
                #implements: Readable<i32>, Writable, Comparable
                var number: i32;
            public:
                func init(number: i32) : number(number) {}
                const func read() i32 { number }
                func write(value: i32) void { number = value; }
                const func same(other: *const Self) bool { number == other.number }
            }
            class Wrapper {
                #implements: Readable<i32>, Writable
            public:
                embed cell: Cell;
                func init(n: i32) : cell(Cell(n)) {}
                const func read() i32 { cell.read() + 1 }
                func write(flag: bool) void { cell.write(if (flag) 1 else 0); }
            }
            class Outer {
                #implements: Readable<i32>
            public:
                embed Wrapper;
                func init(n: i32) : Wrapper(Wrapper(n)) {}
            }
            class Private {
                embed cell: Cell;
            public:
                func init(n: i32) : cell(Cell(n)) {}
                const func inside() i32 { read() }
            }
            func statically<T: Readable<i32> & Copyable>(target: *const T) i32 { target.read() }
            func dynamically(target: *const Readable<i32>) i32 {
                &view := target;
                var restored = &view;
                var call = [&view]() i32 => view.read();
                if (target == restored) call() else 0
            }
            func mutate(target: *Writable) void { target.write(88); }
            func main() !void {
                var wrapper = Wrapper(11);
                io.println(statically(&wrapper))!;
                io.println(dynamically(&wrapper))!;
                wrapper.write(44);
                io.println(wrapper.cell.read())!;
                wrapper.write(false);
                io.println(wrapper.read())!;
                mutate(&wrapper);
                io.println(wrapper.read())!;
                var outer = Outer(31);
                io.println(outer.read())!;
                io.println(dynamically(&outer))!;
                var secret = Private(9);
                io.println(secret.inside())!;
                var a = Cell(5);
                var b = Cell(5);
                io.println(a.same(&b))!;
                io.println(meta.hasInterface<Outer, Readable<i32>>())!;
                io.println(meta.hasInterface<Private, Readable<i32>>())!;
            }
        """, "12\n12\n44\n1\n89\n32\n32\n9\ntrue\ntrue\nfalse\n")
        cases = (
            ("interface Read { func read() i32; } class Missing { #implements: Read\n }",
             "public implementation"),
            ("interface Read { const func read() i32; } class Cell { #implements: Read\n "
             "pub func read() i32 { 1 } }", "public implementation"),
            ("class Base { pub func value() i32 { 1 } } class Bad { pub embed base: Base; "
             "pub func value() bool { true } }", "conflicts with an embedded method"),
            ("class Base { pub func value() i32 { 1 } } class Bad { pub embed first: Base; "
             "pub embed second: Base; } func f(x: *Bad) i32 { x.value() }", "ambiguous overload"),
            ("class Base { pub func value() i32 { 1 } } class Bad { embed privateBase: Base; } "
             "func f(x: *Bad) i32 { x.value() }", "no accessible overload"),
            ("interface Change { func go() void; } func f(x: *const Change) void { x.go(); }",
             "no accessible overload"),
            ("interface Compare { const func eq(other: *const Self) bool; } "
             "func f(a, b: *const Compare) bool { a.eq(b) }", "concrete interface"),
            ("interface Change { func go() void; } func f(x: Change) void {}", "no by-value"),
        )
        for program, error in cases:
            with self.subTest(source=program):
                src = self.source("bad.vqr", program)
                self.assertIn(error.encode(), self.process(VAQC, "check", src, good=False).stderr)

    def test_custom_box_allocator_and_raw_parts(self):
        self.hosted("""
            import (core, mem)
            error Trouble { Refused, Constructor }
            class Tracker {
                #implements: mem.Allocator
                var defaultAllocator: mem.GlobalAllocator;
            public:
                var releases: i32;
                var refuse: bool;
                func init() : defaultAllocator(mem.GlobalAllocator()), releases(0), refuse(false) {}
                func alloc(layout: mem.Layout) !?*u8 {
                    if refuse { return Trouble.Refused; }
                    defaultAllocator.alloc(layout)
                }
                func free(ptr: *u8, layout: mem.Layout) void {
                    releases++;
                    defaultAllocator.free(ptr, layout);
                }
            }
            class Guarded {
                var number: i32;
            public:
                func init(value: i32) !void : number(value) {
                    if value < 0 { return Trouble.Constructor; }
                }
                const func get() i32 { number }
                func deinit() void { io.println(number) catch {}; }
            }
            func check(ref: mem.AllocatorRef) !i32 {
                var value = new(ref) i32(33)!;
                *value.asPtr()
            }
            func main() !void {
                var heap = Tracker();
                {
                    var first = new(heap) Guarded(19)!;
                    ptr, context := Box.intoRawParts(move(first));
                    var restored = unsafe { Box<Guarded>.fromRawParts(ptr, move(context)) };
                    io.println(restored.asPtr().get())!;
                    io.println(heap.releases)!;
                }
                io.println(heap.releases)!;
                var failed = new(&heap) Guarded(-1);
                io.println(core.isErr(&failed))!;
                io.println(heap.releases)!;
                io.println(check(&heap)!)!;
                io.println(heap.releases)!;
                heap.refuse = true;
                var disabled = new(heap) i32(4);
                var refused = move(disabled) catch (e) {
                    io.println(e == Trouble.Refused)!;
                    return;
                };
                panic("allocator unexpectedly succeeded");
            }
        """,
                    "19\n0\n19\n1\ntrue\n2\n33\n3\ntrue\n")
        bare = self.source("fixed.vqr", """
            import (mem)
            export "C" func allocate(buffer: *[64]u8) c_int {
                var arena = mem.FixedBufferAllocator((*buffer)[:]);
                var value = new(arena) i32(97) catch { return -1; };
                *value.asPtr()
            }
        """)
        flags = ("--target", "thumbv7m-none-eabi", "--cpu", "cortex-m3", "--profile", "freestanding")
        self.process(VAQC, bare, *flags, "--emit=obj", "-O2", "-o", "fixed.o")
        self.assertEqual(self.process("llvm-nm", "-u", "fixed.o").stdout, b"")

    def test_simd_scalar_lanes(self):
        self.hosted("""
            import (core, simd)
            func main() !void {
                var input = [4]i32{1, 4, 3, 2};
                var a = simd.load<i32, 4>(input[:])!;
                var three = simd.Simd<i32, 4>.splat(3);
                var mask = a.compareLt(three);
                io.println(mask.countTrue())!;
                io.println(mask.any())!;
                io.println(mask.all())!;
                io.println(mask.and(mask.not()).none())!;
                io.println(mask.or(mask.not()).all())!;
                io.println(mask.xor(mask.not()).countTrue())!;
                var selected = simd.select(mask, a, three);
                io.println(simd.reduceAdd(selected))!;
                io.println(simd.reduceMul(selected))!;
                io.println(simd.reduceMin(selected))!;
                io.println(simd.reduceMax(selected))!;
                io.println(a.add(three).sub(three).mul(three).div(three).extract(1))!;
                io.println(a.min(three).reduceAdd())!;
                io.println(a.max(three).reduceAdd())!;
                var indices = [4]usize{3, 2, 1, 0};
                io.println(simd.shuffle(a, indices).extract(0))!;
                var combined = [4]usize{0, 1, 4, 6};
                io.println(simd.permute(a, three, combined).reduceAdd())!;
                var gathered = simd.gather(input[:], indices)!;
                io.println(gathered.extract(1))!;
                var badIndices = [4]usize{1, 0, 2, 4};
                var rejected = simd.scatter(input[:], badIndices, gathered);
                io.println(core.isErr(&rejected))!;
                io.println(input[1])!;
                simd.store(input[:], gathered)!;
                io.println(input[0])!;
                io.println(simd.horizontalSum(input[:]))!;
                var bytes = [_]u8{0, 0, 0, 0, 0};
                unsafe {
                    var address = ptrCast<*u16>(&bytes[1]);
                    simd.storeUnaligned<u16, 2>(address, simd.Simd<u16, 2>.splat(1000));
                    io.println(simd.loadUnaligned<u16, 2>(address).reduceAdd())!;
                }
            }
        """, "2\ntrue\nfalse\ntrue\ntrue\n4\n9\n18\n1\n3\n4\n9\n13\n2\n11\n3\ntrue\n4\n2\n10\n2000\n")

    def test_stream_protocols_and_partial_io(self):
        self.hosted("""
            import (core)
            class ShortReader {
                #implements: io.Reader
                var remaining: usize;
            public:
                func init() : remaining(2) {}
                func read(dst: []u8) !usize {
                    if dst.len() == 0 { return 0; }
                    if remaining == 0 { return io.IoError.WouldBlock; }
                    dst[0] = 55;
                    remaining--;
                    1
                }
            }
            class ShortWriter {
                #implements: io.Writer
                var remaining: usize;
            public:
                func init() : remaining(3) {}
                func write(src: []const u8) !usize {
                    if src.len() == 0 { return 0; }
                    if remaining == 0 { return io.IoError.Interrupted; }
                    remaining--;
                    1
                }
                func flush() !void {}
            }
            func main() !void {
                var source = [_]u8{2, 4, 6, 9};
                var scratch = [_]u8{0, 0, 0, 0};
                var reader = io.MemoryReader(source[:]);
                io.println(reader.seek(-2, io.SeekOrigin.End)!)!;
                var progress = io.readExactProgress(&reader, scratch[:])!;
                io.println(progress.processed)!;
                io.println(progress.status == io.ReadStatus.Eof)!;
                io.println(scratch[1])!;
                reader.seek(0, io.SeekOrigin.Start)!;
                var limited = io.limit(&reader, 3);
                var counted = io.CountingReader(&limited);
                io.println(io.readAtLeast(&counted, scratch[:], 2)!)!;
                io.println(counted.count())!;
                io.println(limited.remaining())!;
                io.println(io.read(&limited, scratch[:])!)!;
                var fixed = io.FixedWriter(scratch[0..2]);
                io.println(io.write(&fixed, source[:])!)!;
                var full = io.write(&fixed, source[:]);
                io.println(core.isErr(&full))!;
                io.println(fixed.written()[1])!;
                var short = ShortReader();
                var partial = io.readExactProgress(&short, scratch[:])!;
                io.println(partial.processed)!;
                io.println(partial.status == io.ReadStatus.WouldBlock)!;
                var narrow = ShortWriter();
                var interrupted = io.writeAllProgress(&narrow, source[:])!;
                io.println(interrupted.processed)!;
                io.println(interrupted.status == io.ReadStatus.Interrupted)!;
                var first = io.MemoryReader(source[0..2]);
                var second = io.MemoryReader(source[2..4]);
                var readers = [2]*io.Reader{&first, &second};
                var multi = io.MultiReader(readers[:]);
                var output = io.MemoryWriter();
                var writer = io.CountingWriter(&output);
                io.println(io.copyAll(&multi, &writer)!)!;
                io.println(writer.count())!;
                io.writeAll(&output, output.asSlice())!;
                var list = io.MemoryWriter.intoBytes(move(output));
                io.println(list.len())!;
                io.println(list[7])!;
                var tiny = io.MemoryReader(source[:]);
                var oversize = io.readToEnd(&tiny, 3);
                io.println(core.isErr(&oversize))!;
                var chars = "中文".asBytes();
                var text = io.MemoryReader(chars);
                var decoded = io.readToString(&text, 6)!;
                io.println(decoded)!;
            }
        """, "2\n2\ntrue\n9\n3\n3\n0\n0\n2\ntrue\n4\n2\ntrue\n3\ntrue\n4\n4\n8\n9\ntrue\n中文\n")

    def test_numeric_conversion_facade(self):
        self.hosted("""
            import (convert, core)
            comptime const floor = convert.floorToInt<i32>(-5.75) catch 0;
            comptime const ceil = convert.ceilToInt<i32>(-5.75) catch 0;
            func main() !void {
                io.println(floor)!;
                io.println(ceil)!;
                var x = 5.75f32;
                io.println(convert.truncToInt<i32>(x)!)!;
                io.println(convert.floorToInt<i32>(-x)!)!;
                io.println(convert.ceilToInt<i32>(x)!)!;
                var half = 2.5f64;
                io.println(convert.roundToInt<i32>(half)!)!;
                io.println(convert.roundToInt<i32>(half + 1.0)!)!;
                var nan = convert.bitCast<f64>(0x7ff8000000000001u64);
                var rejected = convert.floorToInt<i32>(nan);
                io.println(core.isErr(&rejected))!;
                var max = 255u32;
                io.println(convert.narrow<u8>(max)!)!;
                io.println(convert.widen<u32>(17u8))!;
                var missing = convert.checkedCast<u8>(max + 1)!;
                io.println(core.isNone(&missing))!;
                io.println(convert.toFloat<f64>(max) == 255.0)!;
                io.println(convert.fromChar('中'))!;
                io.println(convert.toChar(20013)! == '中')!;
                var surrogate = convert.toChar(0xd800);
                io.println(core.isErr(&surrogate))!;
                io.println(convert.toBool(1)!)!;
                var invalidBool = convert.toBool(2);
                io.println(core.isErr(&invalidBool))!;
            }
        """, "-6\n-5\n5\n-6\n6\n2\n4\ntrue\n255\n17\ntrue\ntrue\n20013\ntrue\ntrue\ntrue\ntrue\n")

    def test_byte_slices_patterns_and_buffer(self):
        self.hosted("""
            import (bytes, cmp, core, text)
            func main() !void {
                var source = "  aa,b,cc,  ".asBytes();
                var spaces = " ".asBytes();
                var cut = bytes.trim(source, spaces);
                io.println(bytes.startsWith(cut, "aa".asBytes()))!;
                io.println(bytes.endsWith(cut, ",".asBytes()))!;
                io.println(bytes.equal(cut, "aa,b,cc,".asBytes()))!;
                io.println(bytes.compare(cut, "z".asBytes()) == cmp.Ordering.Less)!;
                io.println(bytes.compareConstantTime(cut, "aa,b,cc,".asBytes()))!;
                io.println(bytes.compareConstantTime(cut, "ba,b,cc,".asBytes()))!;
                io.println(bytes.count(cut, 44))!;
                io.println(bytes.find(cut, 44).unwrap())!;
                io.println(bytes.findLast(cut, 44).unwrap())!;
                var pattern = bytes.BytePattern.compile("aba".asBytes())!;
                io.println(pattern.find("abbaba".asBytes()).unwrap())!;
                var fields = bytes.split(cut, ",".asBytes())!;
                for field in fields { io.println(field.len())!; }
                var split = bytes.splitOnce(cut, ",".asBytes()).unwrap();
                io.println(bytes.contains(cut, "cc".asBytes()))!;
                var repeated = bytes.repeat("hi".asBytes(), 2)!;
                var replaced = bytes.replace(repeated.asConstSlice(), "i".asBytes(), "o".asBytes())!;
                io.println(text.fromBytes(replaced.asConstSlice())!)!;
                var buffer = bytes.Buffer.withCapacity(2)!;
                buffer.write("AB".asBytes())!;
                buffer.write(buffer.asSlice())!;
                io.println(buffer.readByte().unwrap())!;
                io.println(buffer.discard(1))!;
                buffer.compact();
                io.println(buffer.len())!;
                io.println(text.fromBytes(buffer.asSlice())!)!;
                buffer.writeByte(67)!;
                var first = buffer.peek(2);
                io.println(first[1])!;
                var copy = bytes.Buffer.intoVec(move(buffer));
                var read = bytes.ByteReader(copy.asConstSlice());
                io.println(read.readByte().unwrap())!;
                var short = [_]u8{0, 0};
                var out = bytes.ByteWriter(short[:]);
                io.println(out.write(read.peek(5)))!;
                io.println(out.writeByte(90))!;
                out.reset();
                io.println(out.writeByte(92))!;
                io.println(out.written()[0])!;
                io.println(bytes.isAscii("é".asBytes()))!;
            }
        """, "true\ntrue\ntrue\ntrue\ntrue\nfalse\n3\n2\n7\n3\n2\n1\n2\n0\ntrue\nhoho\n65\n1\n2\nAB\n66\n65\n2\nfalse\ntrue\n92\nfalse\n", "-O2")

    def test_standard_byte_encodings(self):
        self.hosted("""
            import (core, encoding, meta, text)
            func main() !void {
                var bytes = [_]u8{0, 31, 255};
                io.println(encoding.hexEncode(bytes[:])!)!;
                var decoded = encoding.hexDecode("00Ffff")!;
                io.println(decoded[1])!;
                var odd = encoding.hexDecode("aba");
                io.println(core.isErr(&odd))!;
                var malformed = encoding.hexDecode("0g");
                io.println(core.isErr(&malformed))!;
                io.println(encoding.percentEncode("a+/ 😀".asBytes(), "/".asBytes())!)!;
                var unescaped = encoding.percentDecode("%E4%B8%AD+%2F")!;
                io.println(text.fromBytes(unescaped.asConstSlice())!)!;
                var high = [_]u8{0xe9};
                io.println(encoding.latin1Decode(high[:])!)!;
                var wrongAscii = encoding.asciiDecode(high[:]);
                io.println(core.isErr(&wrongAscii))!;
                var latin = encoding.latin1Encode("é")!;
                io.println(latin[0])!;
                var utf16 = encoding.utf16Encode("中😀", meta.Endian.Little)!;
                io.println(utf16.len())!;
                io.println(utf16[0])!;
                io.println(encoding.utf16Decode(utf16.asConstSlice(), meta.Endian.Little)!)!;
                var big = encoding.utf16Encode("😀", meta.Endian.Big)!;
                io.println(big[0])!;
                io.println(encoding.utf16Decode(big.asConstSlice(), meta.Endian.Big)!)!;
                var broken = [_]u8{0xd8, 0x00};
                var rejected = encoding.utf16Decode(broken[:], meta.Endian.Big);
                io.println(core.isErr(&rejected))!;
                var overlong = [_]u8{0xc0, 0x80};
                var invalid = encoding.utf8Validate(overlong[:]);
                io.println(core.isErr(&invalid))!;
                var opts = encoding.Base64Options{alphabet: encoding.Base64Alphabet.Standard,
                    padding: true, lineWidth: 0, ignoreWhitespace: false};
                io.println(encoding.base64EncodedLen(4)!)!;
                io.println(encoding.base64Encode("f".asBytes(), opts)!)!;
                io.println(encoding.base64Encode("fo".asBytes(), opts)!)!;
                io.println(encoding.base64Encode("foo".asBytes(), opts)!)!;
                var base = encoding.base64Decode("Zm9v", opts)!;
                io.println(text.fromBytes(base.asConstSlice())!)!;
                var noncanonical = encoding.base64Decode("Zh==", opts);
                io.println(core.isErr(&noncanonical))!;
                opts.alphabet = encoding.Base64Alphabet.UrlSafe;
                opts.padding = false;
                opts.lineWidth = 0;
                var sample = [_]u8{255};
                io.println(encoding.base64Encode(sample[:], opts)!)!;
                var url = encoding.base64Decode("_w", opts)!;
                io.println(url[0])!;
                var streaming = encoding.Utf8Decoder();
                var leading = [_]u8{0xe4};
                var suffix = [_]u8{0xb8, 0xad};
                streaming.push(leading[:])!;
                var unfinished = streaming.finish();
                io.println(core.isErr(&unfinished))!;
                streaming.push(suffix[:])!;
                streaming.finish()!;
                io.println(streaming.offset())!;
                streaming.reset();
                io.println(streaming.offset())!;
                var decoder = encoding.Utf16Decoder(meta.Endian.Little);
                for i in usize(0)..utf16.len() {
                    decoder.feed(utf16.asConstSlice()[i..i+1])!;
                }
                io.println(decoder.finish()!)!;
                opts.alphabet = encoding.Base64Alphabet.Standard;
                opts.padding = true; opts.lineWidth = 4; opts.ignoreWhitespace = true;
                var wrapped = encoding.base64Encode("foobar".asBytes(), opts)!;
                io.println(wrapped)!;
                var recovered = encoding.base64Decode(wrapped, opts)!;
                io.println(text.fromBytes(recovered.asConstSlice())!)!;
            }
        """, "001fff\n255\ntrue\ntrue\na%2B/%20%F0%9F%98%80\n中+/\né\ntrue\n233\n6\n45\n中😀\n216\n😀\ntrue\ntrue\n8\nZg==\nZm8=\nZm9v\nfoo\ntrue\n_w\n255\ntrue\n3\n0\n中😀\nZm9v\nYmFy\nfoobar\n")

    def test_parsing_boundaries_and_utf8_cursor(self):
        self.hosted("""
            import (convert, parse)
            func wrapped(value: str) !u16 { parse.parseHex<u16>(value) }
            func main() !void {
                io.println(parse.parseInt<i8>("-128", 10)!)!;
                io.println(parse.parseInt<i8>("128", 10) catch i8(91))!;
                io.println(parse.parseInt<i8>("-129", 10) catch i8(92))!;
                io.println(parse.parseUint<u8>("-0", 10) catch u8(93))!;
                io.println(parse.parseInt<i64>("-9223372036854775808", 10)!)!;
                var top = parse.parseUint<u128>("340282366920938463463374607431768211455", 10)!;
                var bottom = parse.parseInt<i128>("-170141183460469231731687303715884105728", 10)!;
                io.println(top == ~u128(0))!;
                io.println(convert.bitCast<u128, i128>(bottom) == (1u128 << 127))!;
                var overflow = parse.parseUint<u128>("340282366920938463463374607431768211456", 10)
                               catch u128(73);
                io.println(overflow == 73)!;
                var prefix = parse.parseIntPrefix<i32>("+Z0𐐷", 36)!;
                io.println(prefix.value)!;
                io.println(prefix.consumed)!;
                io.println(parse.parseInt<i32>("1a", 10) catch i32(94))!;
                io.println(parse.parseInt<i32>("1", 1) catch i32(95))!;
                io.println(parse.parseWith<u16, func(str) !u16>("0x1a", wrapped)!)!;
                io.println(parse.parseBinary<u16>("0B1011")!)!;
                io.println(parse.parseOctal<i16>("-0o17")!)!;
                io.println(parse.isInt("+Aabc", 16))!;
                io.println(parse.isInt(" 3", 10))!;
                io.println(parse.parseBool("true")!)!;
                io.println(convert.fromChar(parse.parseChar("𐐷")!))!;
                var cursor = parse.Cursor("\u2003中𝄞+abc");
                cursor.skipWhitespace();
                io.println(cursor.offset())!;
                var mark = cursor.mark();
                io.println(cursor.consumeString("中𝄞"))!;
                io.println(cursor.remaining())!;
                cursor.reset(mark);
                io.println(convert.fromChar(cursor.next().unwrap()))!;
                io.println(cursor.offset())!;
                for field in parse.splitFields("  α\u00a0 β \t中 ") { io.println(field)!; }
            }
        """, "-128\n91\n92\n93\n-9223372036854775808\ntrue\ntrue\ntrue\n1260\n3\n94\n95\n26\n11\n-15\ntrue\nfalse\ntrue\n66615\n3\ntrue\n+abc\n20013\n6\nα\nβ\n中\n")
        source = self.source("bare.vqr", """
            import (parse)
            export "C" func read_number(data: u8, radix: u8) i32 {
                if data == 0 { return 0; }
                parse.parseInt<i32>("ABC123!", radix) catch i32(-1)
            }
        """)
        self.process(VAQC, source, "--target", "thumbv7m-none-eabi", "--cpu", "cortex-m3",
                     "--profile", "freestanding", "--emit=obj", "-O2", "-o", "bare.o")
        self.assertEqual(self.process("llvm-nm", "-u", "bare.o").stdout, b"")

    def test_cells_and_borrow_guards(self):
        self.hosted("""
            import (cell, core, meta)
            error Failure { Once }
            class Pair {
                pub var number: i32;
                pub var marker: i32;
            }
            func view(ptr: *const Pair) *const i32 { &ptr.number }
            func edit(ptr: *Pair) *i32 { &ptr.number }
            func twelve() i32 { 12 }
            func makeLazy<F: Movable>(callback: F) cell.Lazy<i32, F> { cell.Lazy<i32, F>(move(callback)) }
            func main() !void {
                var small = cell.Cell<i32>.new(2);
                var triple = (value: i32) i32 => value * 3;
                small.update(triple);
                io.println(small.replace(4))!;
                io.println(cell.Cell<i32>.intoInner(move(small)))!;
                var storage = cell.RefCell<Pair>.new(Pair{number: 7, marker: 9});
                {
                    var borrowed = storage.borrow()!;
                    var leaf = borrowed.map<i32>(view);
                    io.println(*leaf.get())!;
                    var conflict = storage.borrowMut();
                    io.println(core.isErr(&conflict))!;
                }
                {
                    var borrowed = storage.borrowMut()!;
                    var leaf = cell.BorrowMut<Pair>.map<i32>(move(borrowed), edit);
                    &x := leaf.get();
                    x = 20;
                    var conflict = storage.tryBorrow();
                    io.println(core.isNone(&conflict))!;
                }
                var pair = cell.RefCell<Pair>.intoInner(move(storage));
                io.println(pair.number)!;
                var once = cell.OnceCell<i32>.new();
                io.println(*once.getOrInit(twelve))!;
                io.println(once.set(17).unwrap())!;
                io.println(once.take().unwrap())!;
                var tried = 0;
                var callback = [&tried]() !i32 => {
                    tried++;
                    if tried == 1 { return Failure.Once; }
                    21
                };
                var failed = once.getOrTryInit(callback);
                io.println(core.isErr(&failed))!;
                io.println(*once.getOrTryInit(callback)!)!;
                var lazy = makeLazy(move(callback));
                io.println(*lazy.force()!)!;
                io.println(*lazy.force()!)!;
                io.println(tried)!;
                var locked = cell.OnceLock<i32>.new();
                var callbackFail = () !i32 => Failure.Once;
                var lockFailure = locked.getOrTryInit(callbackFail);
                io.println(core.isErr(&lockFailure))!;
                io.println(*locked.getOrInit(twelve))!;
                io.println(locked.set(4).unwrap())!;
                io.println(*locked.get().unwrap())!;
                io.println(meta.isShareable<cell.OnceLock<i32>>())!;
                io.println(meta.isShareable<cell.RefCell<i32>>())!;
                var raw = cell.UnsafeCell<i32>.new(5);
                unsafe { *raw.get() = 11; io.println(*raw.get())!; }
            }
        """,
                    "6\n4\n7\ntrue\ntrue\n20\n12\n17\n12\ntrue\n21\n21\n21\n3\ntrue\n12\n4\n12\ntrue\nfalse\n11\n")

    def test_once_lock_publishes_once_across_native_threads(self):
        source = self.source("once.vqr", '''
            import (atomic, cell, core)
            class State {
                pub var hits: atomic.Atomic<i32>;
                pub var once: cell.OnceLock<i32>;
                pub func init() : hits(atomic.Atomic<i32>(0)), once(cell.OnceLock<i32>.new()) {}
            }
            export "C" func make_state() *State {
                var state = new State() catch { panic("allocation failed"); };
                Box.intoRaw(move(state))
            }
            export "C" func read_state(state: *State) c_int {
                var initializer = [state]() i32 => {
                    state.hits.fetchAdd(1);
                    97
                };
                *state.once.getOrInit(move(initializer))
            }
            export "C" func init_count(state: *State) c_int { state.hits.load() }
            export "C" func free_state(state: *State) void {
                var handle = unsafe { Box<State>.fromRaw(state) };
                core.drop(move(handle));
            }
        ''')
        client = self.source("once.c", '''
            #include <pthread.h>
            #include <stdatomic.h>
            #include <sched.h>
            struct State;
            extern struct State *make_state(void);
            extern int read_state(struct State *);
            extern int init_count(struct State *);
            extern void free_state(struct State *);
            static _Atomic int start;
            static void *worker(void *ptr) {
                while (!atomic_load_explicit(&start, memory_order_acquire)) sched_yield();
                if (read_state(ptr) == 97) return 0;
                return ptr;
            }
            int main(void) {
                struct State *state = make_state();
                pthread_t threads[8];
                for (int i = 0; i != 8; ++i)
                    if (pthread_create(&threads[i], 0, worker, state)) return 1;
                atomic_store_explicit(&start, 1, memory_order_release);
                for (int i = 0; i != 8; ++i) {
                    void *failed = 0;
                    if (pthread_join(threads[i], &failed) || failed) return 2;
                }
                int count = init_count(state);
                free_state(state);
                return count != 1;
            }
        ''')
        self.process(VAQC, source, "--emit=obj", "-O2", "-o", "once.o")
        self.process("cc", client, "once.o", "-pthread", "-o", "thread-client")
        self.process("./thread-client")

    def test_native_thread_move_join_and_cooperative_stop(self):
        self.hosted('''
            import (meta, thread)
            error WorkError { Rejected }
            class Record {
                #copy: disabled
                pub var value: i32;
            }
            func main() !void {
                var mainId = thread.currentId().value;
                var worker = [mainId]() !i32 => {
                    if thread.currentId().value == mainId { return WorkError.Rejected; }
                    for !thread.stopRequested() { thread.yieldNow(); }
                    42
                };
                var job = thread.Thread<i32>.spawn(move(worker))!;
                io.println(job.id().value != mainId)!;
                io.println(job.isFinished())!;
                job.requestStop();
                io.println(job.join()!)!;
                io.println(job.join() catch i32(-2))!;
                var transfer = Record{value: 17};
                var owner = [move(transfer)]() Record => move(transfer);
                var ownership = thread.Thread<Record>.spawn(move(owner))!;
                var result = ownership.join()!;
                io.println(result.value)!;
                var fail = () !i32 => WorkError.Rejected;
                var failure = thread.Thread<i32>.spawn(fail)!;
                io.println(failure.join() catch (reason) {
                    if reason == WorkError.Rejected { i32(-3) } else { i32(-4) }
                })!;
                const noResult: func() void = () void => {};
                var finished = thread.spawn<void, func() void>(noResult)!;
                finished.join()!;
                var abandoned = thread.Thread<void>.spawn(noResult)!;
                abandoned.detach();
                io.println(thread.cpuCount() > 0)!;
                io.println(meta.isSendable<thread.Thread<Record>>())!;
                io.println(thread.stopRequested())!;
            }
        ''', 'true\nfalse\n42\n-2\n17\n-3\ntrue\ntrue\nfalse\n')
        source = self.source("borrowed.vqr", '''
            import (thread)
            func launch() !void {
                var counter = 0;
                var callback = [&counter]() i32 => counter;
                var worker = thread.Thread<i32>.spawn(move(callback))!;
            }
        ''')
        result = self.process(VAQC, "check", source, good=False)
        self.assertIn(b"error[E202]: no accessible overload", result.stderr)
        source = self.source("no-host.vqr", '''
            import (thread)
            func run() void { thread.yieldNow(); }
        ''')
        result = self.process(VAQC, "check", source, "--profile", "freestanding", good=False)
        self.assertIn(b"native threads require a hosted", result.stderr)

    def test_native_thread_failed_start_and_detached_cleanup(self):
        source = self.source("ownership.vqr", '''
            import (thread)
            extern "C" func record_drop(id: c_int) void;
            extern "C" func await_permission() void;
            class Probe {
                #copy: disabled
                pub var id: c_int;
                func deinit() void { unsafe { record_drop(id); } }
            }
            export "C" func try_start(which: c_int) c_int {
                var resource = Probe{id: which};
                var callback = [move(resource)]() Probe => {
                    unsafe { await_permission(); }
                    move(resource)
                };
                var threadHandle = thread.Thread<Probe>.spawn(move(callback)) catch (reason) {
                    if reason == thread.ThreadError.StartFailed { return 1; }
                    return 2;
                };
                threadHandle.detach();
                0
            }
        ''')
        client = self.source("ownership.c", '''
            #include <errno.h>
            #include <pthread.h>
            #include <sched.h>
            #include <stdatomic.h>

            extern int try_start(int);
            extern int __real_pthread_create(pthread_t *, const pthread_attr_t *,
                                              void *(*)(void *), void *);
            static int reject = 1;
            static _Atomic int proceed, freed[2];
            int __wrap_pthread_create(pthread_t *id, const pthread_attr_t *attributes,
                                      void *(*work)(void *), void *context) {
                if (reject) return EAGAIN;
                return __real_pthread_create(id, attributes, work, context);
            }
            void record_drop(int id) { atomic_fetch_add(&freed[id], 1); }
            void await_permission(void) { while (!atomic_load(&proceed)) sched_yield(); }
            int main(void) {
                if (try_start(0) != 1 || atomic_load(&freed[0]) != 1) return 1;
                reject = 0;
                if (try_start(1) != 0 || atomic_load(&freed[1])) return 2;
                atomic_store(&proceed, 1);
                while (!atomic_load(&freed[1])) sched_yield();
                return atomic_load(&freed[1]) != 1;
            }
        ''')
        self.process(VAQC, source, "--emit=obj", "-O2", "-o", "ownership.o")
        self.process("cc", client, "ownership.o", "-Wl,--wrap=pthread_create", "-pthread", "-o", "ownership")
        self.process("./ownership")

    def test_mutex_guards_and_once(self):
        self.hosted('''
            import (cell, core, meta, sync)
            error Failure { Retry }
            class Pair {
                #copy: disabled
                pub var value: i32;
                pub var other: i32;
            }
            func choose(pair: *Pair) *i32 { &pair.other }
            func main() !void {
                var lock = sync.Mutex<Pair>.new(Pair{value: 13, other: 2});
                {
                    var first = lock.lock()!;
                    var unavailable = lock.tryLock();
                    io.println(core.isNone(&unavailable))!;
                    var entry = sync.MutexGuard<Pair>.map<i32>(move(first), choose);
                    &number := entry.get();
                    number *= 4;
                    entry.unlock();
                }
                var observe = (pair: *Pair) !void => io.println(pair.other);
                sync.withLock(&lock, observe)!;
                var value = sync.Mutex<Pair>.intoInner(move(lock));
                io.println(value.value)!;
                io.println(meta.isShareable<sync.Mutex<cell.Cell<i32>>>())!;
                io.println(meta.isSendable<sync.MutexGuard<Pair>>())!;
                var once = sync.Once.new();
                var retries = 0;
                var fail = [&retries]() !void => { retries++; Failure.Retry };
                once.callOnce(fail) catch {};
                io.println(once.isDone())!;
                var finish = [&retries]() void => { retries++; };
                once.callOnce(finish)!;
                once.callOnce(finish)!;
                io.println(once.isDone())!;
                io.println(retries)!;
            }
        ''', 'true\n8\n13\ntrue\nfalse\nfalse\ntrue\n2\n')

    def test_mutex_condition_and_wait_group_across_threads(self):
        source = self.source("synchronization.vqr", '''
            import (sync)
            class Counter {
                pub var value: u32;
                pub var waiting: bool;
            }
            class State {
                pub var counter: sync.Mutex<Counter>;
                pub var pending: sync.WaitGroup;
                pub var condition: sync.CondVar;
            public:
                func init() : counter(sync.Mutex<Counter>.new(Counter{value: 0, waiting: false})),
                              pending(sync.WaitGroup.new()), condition(sync.CondVar.new()) {}
            }
            export "C" func make_state() *State {
                var value = new State() catch { panic("state allocation failed"); };
                value.asPtr().pending.add(6) catch { panic("add failed"); };
                Box.intoRaw(move(value))
            }
            export "C" func worker(state: *const State) void {
                for index in 0..2000 {
                    var guard = state.counter.lock() catch { panic("lock failed"); };
                    var value = guard.get();
                    value.value++;
                }
                state.pending.done();
                state.condition.notifyAll();
            }
            export "C" func monitor(state: *const State) void {
                var guard = state.counter.lock() catch { panic("lock failed"); };
                guard.get().waiting = true;
                var threshold = (value: *Counter) bool => value.value == 12000;
                var final = sync.waitUntil(&state.condition, move(guard), threshold)
                            catch { panic("condition failed"); };
            }
            export "C" func is_waiting(state: *const State) c_int {
                var guard = state.counter.lock() catch { panic("lock failed"); };
                if guard.get().waiting { 1 } else { 0 }
            }
            export "C" func finish(state: *State) u32 {
                state.pending.wait() catch { panic("wait failed"); };
                var guard = state.counter.lock() catch { panic("lock failed"); };
                guard.get().value
            }
            export "C" func free_state(state: *State) void {
                var owned = unsafe { Box<State>.fromRaw(state) };
            }
        ''')
        client = self.source("synchronization.c", '''
            #include <pthread.h>
            #include <sched.h>
            #include <stdint.h>

            extern void *make_state(void);
            extern void worker(const void *), monitor(const void *), free_state(void *);
            extern int is_waiting(const void *);
            extern uint32_t finish(void *);
            static void *run_worker(void *value) { worker(value); return 0; }
            static void *run_monitor(void *value) { monitor(value); return 0; }
            int main(void) {
                void *state = make_state();
                pthread_t watcher, threads[6];
                if (pthread_create(&watcher, 0, run_monitor, state)) return 1;
                while (!is_waiting(state)) sched_yield();
                for (int i = 0; i < 6; ++i)
                    if (pthread_create(&threads[i], 0, run_worker, state)) return 2;
                if (finish(state) != 12000) return 3;
                for (int i = 0; i < 6; ++i)
                    if (pthread_join(threads[i], 0)) return 4;
                if (pthread_join(watcher, 0)) return 5;
                free_state(state);
                return 0;
            }
        ''')
        self.process(VAQC, source, "--emit=obj", "-O2", "-o", "sync.o")
        self.process("cc", client, "sync.o", "-pthread", "-o", "sync-client")
        self.process("./sync-client")

    def test_shared_weak_ownership_and_native_transfer(self):
        self.hosted('''
            import (cell, core, mem, meta, sync, thread)
            class Probe {
                #copy: disabled
                pub var number: i32;
                func deinit() void { io.println(number + 100) catch {}; }
            }
            func main() !void {
                var original = mem.Shared<Probe>.new(Probe{number: 1})!;
                var unique = original.getMut().unwrap();
                unique.number = 7;
                var observer = original.downgrade();
                var aliased = original.getMut();
                io.println(core.isNone<*Probe>(&aliased))!;
                {
                    var duplicate = original;
                    var weakCopy = observer;
                    io.println(observer.ptrEq(&weakCopy))!;
                    io.println(duplicate.ptrEq(&original))!;
                    io.println(duplicate.strongCount())!;
                    io.println(duplicate.weakCount())!;
                    var upgraded = observer.upgrade();
                    io.println(original.strongCount())!;
                    var failure = mem.Shared<Probe>.tryUnwrap(move(duplicate));
                    io.println(core.isErr<Probe>(&failure))!;
                }
                {
                    var final = mem.Shared<Probe>.tryUnwrap(move(original))!;
                    io.println(final.number)!;
                    var dead = observer.upgrade();
                    io.println(core.isNone<mem.Shared<Probe>>(&dead))!;
                    io.println(observer.weakCount())!;
                }
                var common = mem.Shared<sync.Mutex<u32>>.new(sync.Mutex<u32>.new(5))!;
                var weak = common.downgrade();
                var job = [move(weak)]() !u32 => {
                    var clone = weak.upgrade();
                    var owned = move(clone).unwrap();
                    var guard = owned.asPtr().lock()!;
                    &value := guard.get();
                    value += 9;
                    value
                };
                var handle = thread.Thread<u32>.spawn(move(job))!;
                io.println(handle.join()!)!;
                var guard = common.asPtr().lock()!;
                io.println(*guard.get())!;
                io.println(common.strongCount())!;
                io.println(common.weakCount())!;
                io.println(meta.isShareable<mem.Shared<sync.Mutex<cell.Cell<i32>>>>())!;
                io.println(meta.isSendable<mem.Shared<cell.Cell<i32>>>())!;
            }
        ''', 'true\ntrue\ntrue\n2\n2\n3\ntrue\n7\ntrue\n1\n107\n14\n14\n1\n0\ntrue\nfalse\n')

    def test_duration_calendar_and_monotonic_deadlines(self):
        self.hosted('''
            import (cmp, core, time)
            func main() !void {
                var negative = time.Duration.fromMillis(-1501);
                io.println(negative.seconds())!;
                io.println(negative.nanosPart())!;
                io.println(negative.isNegative())!;
                io.println(negative.abs()!.toMillis()!)!;
                io.println(negative.mul(-3)!.div(2)!.toNanos()!)!;
                var large = time.Duration.fromSeconds(9223372036854775807i64);
                var overflow = large.mul(2);
                io.println(core.isErr<time.Duration>(&overflow))!;
                var underflow = time.Duration.fromSeconds(-9223372036854775807i64 - 1).abs();
                io.println(core.isErr<time.Duration>(&underflow))!;
                io.println(time.Duration.fromDays(9223372036854775807i64).seconds())!;

                var epoch = time.Date.new(1970, 1, 1)!;
                io.println(epoch.toOrdinal())!;
                io.println(epoch.dayOfWeek() == time.Weekday.Thursday)!;
                var before = time.Date.fromOrdinal(-1)!;
                io.println(before.year())!;
                io.println(before.month())!;
                io.println(before.day())!;
                var leap = time.Date.new(2000, 2, 29)!;
                io.println(leap.dayOfYear())!;
                io.println(leap.isLeapYear())!;
                var skippedLeap = time.Date.new(1900, 2, 29);
                io.println(core.isErr<time.Date>(&skippedLeap))!;
                var early = time.Date.new(-400, 2, 29)!;
                io.println(time.Date.fromOrdinal(early.toOrdinal())!.year())!;
                var extreme = time.Date.new(2147483647, 12, 31)!;
                var impossible = extreme.addDays(1);
                io.println(core.isErr<time.Date>(&impossible))!;
                var end = time.Date.new(2023, 1, 31)!;
                io.println(end.addMonths(1, time.MonthPolicy.Clamp)!.day())!;
                io.println(end.addMonths(1, time.MonthPolicy.Carry)!.month())!;
                io.println(end.addMonths(1, time.MonthPolicy.Carry)!.day())!;

                var old = time.UtcDateTime.fromUnixSeconds(-1, 999000000)!;
                io.println(old.time().hour())!;
                io.println(old.time().minute())!;
                io.println(old.time().second())!;
                io.println(old.date().toOrdinal())!;
                var next = old.add(time.Duration.fromMillis(1))!;
                io.println(next.unixSeconds())!;
                io.println(next.nanosecond())!;
                var local = time.LocalDateTime.new(epoch, time.TimeOfDay.new(0, 0, 0, 0)!);
                io.println(local.add(time.Duration.fromNanos(-1))!.time().nanosecond())!;
                io.println(time.now()!.date().year() >= 1970)!;

                var beginning = time.Instant.now()!;
                var span = time.Duration.fromMillis(4);
                var endpoint = beginning.checkedAdd(span)!;
                var limit = time.Deadline.at(endpoint);
                time.sleepUntil(endpoint)!;
                io.println(beginning.elapsed()!.compare(span) != cmp.Ordering.Less)!;
                io.println(limit.expired()!)!;
                io.println(limit.remaining()!.isZero())!;
                var timer = time.Timer.after(time.Duration.zero())!;
                timer.cancel();
                var cancelled = timer.wait();
                io.println(core.isErr<void>(&cancelled))!;
                timer.reset(time.Duration.zero())!;
                timer.wait()!;
                var ticker = time.Ticker.every(time.Duration.fromMillis(1))!;
                var first = ticker.next()!;
                var second = ticker.next()!;
                io.println(second.durationSince(first)!.toNanos()!)!;
            }
        ''', '-1\n-501000000\ntrue\n1501\n2251500000\ntrue\ntrue\n9223372036854775807\n0\ntrue\n'
             '1969\n12\n31\n60\ntrue\ntrue\n-400\ntrue\n28\n3\n3\n23\n59\n59\n-1\n0\n0\n999999999\n'
             'true\ntrue\ntrue\ntrue\ntrue\n1000000\n')

    def test_channel_buffered_ownership_close_and_optional_values(self):
        self.hosted('''
            import (channel, core, meta)
            class Probe {
                #copy: disabled
                pub var marker: i32;
                func deinit() void { io.println(marker + 100) catch {}; }
            }
            func main() !void {
                tx, rx := channel.bounded<Probe>(2)!;
                tx.send(Probe{marker: 1})!;
                tx.send(Probe{marker: 2})!;
                switch tx.trySend(Probe{marker: 3}) {
                    channel.TrySendResult.Full(recovered) => { io.println(recovered.marker)!; },
                    _ => { panic("expected a full channel"); },
                }
                rx.close();
                switch tx.sendPreserving(Probe{marker: 4}) {
                    channel.SendResult.Closed(recovered) => { io.println(recovered.marker)!; },
                    _ => { panic("expected a closed channel"); },
                }
                for item in rx.drain() { io.println(item.marker)!; }
                var empty = rx.recv()!;
                io.println(core.isNone<Probe>(&empty))!;

                inner, outer := channel.Channel<?i32>.bounded(1)!;
                inner.send(null)!;
                inner.close();
                var nested = outer.recv()!;
                var payload = nested.unwrap();
                io.println(core.isSome<?i32>(&nested))!;
                io.println(core.isNone<i32>(&payload))!;
                var terminal = outer.recv()!;
                io.println(core.isNone<?i32>(&terminal))!;

                fast, slow := channel.unbounded<i32>()!;
                for n in 0..4 { fast.send(n)!; }
                for n in 0..2 { if slow.recv()!.unwrap() != n { panic("initial order"); } }
                for n in 4..100 { fast.send(n)!; }
                core.drop<channel.Sender<i32>>(move(fast));
                for n in 2..100 { if slow.recv()!.unwrap() != n { panic("growing queue order"); } }
                var end = slow.recv()!;
                io.println(core.isNone<i32>(&end))!;
                io.println(meta.isSendable<channel.Receiver<i32>>())!;
                io.println(meta.isShareable<channel.Receiver<i32>>())!;
            }
        ''', '3\n103\n4\n104\n1\n101\n2\n102\ntrue\ntrue\ntrue\ntrue\ntrue\ntrue\nfalse\n')

    def test_channel_native_rendezvous_and_multiple_producers(self):
        self.hosted('''
            import (channel, core, thread)
            func receiveReady(receiver: *channel.Receiver<i32>) i32 {
                for {
                    switch receiver.tryRecv() {
                        channel.TryRecv.Value(item) => return item,
                        channel.TryRecv.Empty => { thread.yieldNow(); },
                        channel.TryRecv.Closed => { panic("channel closed before rendezvous"); },
                    }
                }
            }
            func main() !void {
                tx, rx := channel.bounded<i32>(0)!;
                switch tx.trySend(10) {
                    channel.TrySendResult.Full(item) => { io.println(item)!; },
                    _ => { panic("without a receiver, trySend must fail"); },
                }
                var producer = [move(tx)]() !i32 => { tx.send(15)!; 17 };
                var worker = thread.Thread<i32>.spawn(move(producer))!;
                io.println(receiveReady(&rx))!;
                io.println(worker.join()!)!;
                var finished = rx.recv()!;
                io.println(core.isNone<i32>(&finished))!;

                out, incoming := channel.bounded<i32>(0)!;
                var consumer = [move(incoming)]() !i32 => {
                    var reader = move(incoming);
                    reader.recv()!.unwrap()
                };
                var partner = thread.Thread<i32>.spawn(move(consumer))!;
                var sent = false;
                for !sent {
                    switch out.trySend(28) {
                        channel.TrySendResult.Sent => { sent = true; },
                        channel.TrySendResult.Full(item) => { thread.yieldNow(); },
                        _ => { panic("channel closed during pairing"); },
                    }
                }
                io.println(partner.join()!)!;

                source, sink := channel.bounded<i32>(8)!;
                var another = source;
                var putFirst = [move(source)]() !void => {
                    for n in 0..1000 { source.send(n)!; }
                };
                var putSecond = [move(another)]() !void => {
                    for n in 1000..2000 { another.send(n)!; }
                };
                var first = thread.Thread<void>.spawn(move(putFirst))!;
                var second = thread.Thread<void>.spawn(move(putSecond))!;
                var low = 0;
                var high = 1000;
                for {
                    switch sink.recv()! {
                        some(item) => {
                            if item < 1000 {
                                if item != low { panic("first sender reordered"); }
                                low++;
                            } else {
                                if item != high { panic("second sender reordered"); }
                                high++;
                            }
                        },
                        null => break,
                    }
                }
                first.join()!;
                second.join()!;
                io.println(low)!;
                io.println(high)!;
            }
        ''', '10\n15\n17\ntrue\n28\n1000\n2000\n', '-O2')

    def test_channel_and_condition_deadlines(self):
        self.hosted('''
            import (channel, cmp, sync, thread, time)
            func main() !void {
                var delay = time.Duration.fromMillis(4);
                first, last := channel.bounded<i32>(0)!;
                var start = time.Instant.now()!;
                switch first.sendTimeout(57, delay) {
                    channel.SendResult.Cancelled(value) => { io.println(value)!; },
                    _ => { panic("unpaired send must time out"); },
                }
                io.println(start.elapsed()!.compare(delay) != cmp.Ordering.Less)!;
                switch last.tryRecv() {
                    channel.TryRecv.Empty => {},
                    _ => { panic("timed out offer was retained"); },
                }
                var expired = last.recvTimeout(delay) catch (reason) {
                    if reason != channel.ChannelError.Timeout { return reason; }
                    io.println(1)!;
                    null
                };
                var writer = first.clone();
                var startSend = [move(writer)]() i32 => {
                    switch writer.sendPreserving(76) {
                        channel.SendResult.Closed(value) => value,
                        _ => { panic("closing receiver must return unsent value"); },
                    }
                };
                var handle = thread.Thread<i32>.spawn(move(startSend))!;
                last.close();
                io.println(handle.join()!)!;

                var mutex = sync.Mutex<i32>.new(9);
                var cv = sync.CondVar.new();
                var guard = mutex.lock()!;
                acquired, elapsed := cv.waitTimeout<i32>(move(guard), delay)!;
                io.println(elapsed)!;
                io.println(*acquired.get())!;
            }
        ''', '57\ntrue\n1\n76\ntrue\n9\n', '-O2')

    def test_channel_preserves_value_on_queue_allocation_failure(self):
        source = self.source("allocation.vqr", '''
            import (channel)
            extern "C" func fail_next() void;
            class Probe {
                #copy: disabled
                pub var marker: c_int;
            }
            func run() !c_int {
                tx, rx := channel.unbounded<Probe>()!;
                unsafe { fail_next(); }
                var recovered = switch tx.sendPreserving(Probe{marker: 29}) {
                    channel.SendResult.OutOfMemory(item) => move(item),
                    _ => { return 1; },
                };
                switch rx.tryRecv() { channel.TryRecv.Empty => {}, _ => { return 2; }, }
                tx.send(move(recovered))!;
                var received = rx.recv()!;
                var value = move(received).unwrap();
                if value.marker == 29 { 0 } else { 3 }
            }
            export "C" func exercise() c_int { run() catch 4 }
        ''')
        client = self.source("allocation.c", '''
            #include <stddef.h>
            void *__real_malloc(size_t), *__real_aligned_alloc(size_t, size_t);
            extern int exercise(void);
            static int fail;
            void fail_next(void) { fail = 1; }
            void *__wrap_malloc(size_t size) {
                if (fail) { fail = 0; return NULL; }
                return __real_malloc(size);
            }
            void *__wrap_aligned_alloc(size_t alignment, size_t size) {
                if (fail) { fail = 0; return NULL; }
                return __real_aligned_alloc(alignment, size);
            }
            int main(void) { return exercise(); }
        ''')
        self.process(VAQC, source, "--emit=obj", "-O2", "-o", "queue.o")
        self.process("cc", client, "queue.o", "-Wl,--wrap=malloc,--wrap=aligned_alloc", "-o", "allocation")
        self.process("./allocation")

    def test_lightweight_tasks_yield_sleep_cancel_and_join(self):
        self.hosted('''
            import (atomic, core, mem, task, time)
            class Resource {
                #copy: disabled
                pub var code: i32;
                func deinit() void { io.println(code) catch {}; }
            }
            func main() !void {
                var group = task.TaskGroup.withWorkers(1)!;
                var definite = mem.Shared<atomic.Atomic<u8>>.new(atomic.Atomic<u8>(0))!;
                var other = definite.clone();
                var waitForOther = [move(other)]() !i32 => {
                    var id = task.currentId();
                    if core.isNone<task.TaskId>(&id) { panic("missing task ID"); }
                    for other.asPtr().load(atomic.Order.Acquire) < 1 { task.yieldNow()!; }
                    44
                };
                var first = group.spawn<i32>(move(waitForOther))!;
                var source = [move(definite)]() !void => {
                    var moved = move(definite);
                    var atomic = moved.asPtr();
                    // This fixture has a unique writer and the cell operations are atomic.
                    unsafe { constCast<*atomic.Atomic<u8>>(atomic).store(1, atomic.Order.Release); }
                };
                var signal = group.spawn<void>(move(source))!;
                io.println(first.join()!)!;
                signal.join()!;
                io.println(first.join() catch (reason) {
                    if reason == task.TaskError.AlreadyJoined { 1 } else { -1 }
                })!;

                var waiting = () !i32 => { task.sleep(time.Duration.fromSeconds(30))!; 0 };
                var sleeper = group.spawn<i32>(waiting)!;
                var available = sleeper.tryJoin()!;
                io.println(core.isNone<i32>(&available))!;
                sleeper.cancel();
                io.println(sleeper.join() catch (reason) {
                    if reason == task.TaskError.Cancelled { 2 } else { -2 }
                })!;
                var timer = () !i32 => { task.sleep(time.Duration.fromMillis(3))!; 8 };
                var tick = group.spawn<i32>(timer)!;
                io.println(tick.join()!)!;
                var carried = Resource{code: 71};
                var make = [move(carried)]() Resource => move(carried);
                var unclaimed = group.spawn<Resource>(move(make))!;
                group.joinAll()!;
                io.println(group.activeCount())!;
                group.close()!;
                var ignored = unclaimed.join() catch (reason) {
                    if reason == task.TaskError.GroupClosed { io.println(3)!; }
                    return;
                };
            }
        ''', '44\n1\ntrue\n2\n8\n0\n71\n3\n', '-O2')

    def test_build_artifacts(self):
        source = self.source("math.vqr", '''
            package math
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
        bad = self.source("bad.vqr", "package bad func oops() i32 { false }")
        previous = (self.root / "math").read_bytes()
        self.process(VAQC, bad, "-o", "math", good=False)
        self.assertEqual((self.root / "math").read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
