"""Exercise the compiler as installed, including independent native consumers."""
import pathlib
import hashlib
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

VAQC = pathlib.Path(sys.argv.pop(1)).resolve()
REPOSITORY = pathlib.Path(__file__).resolve().parents[2]


class CompilerCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vaqrin-test-")
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)

    def source(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def process(self, *args, good=True, input=None):
        result = subprocess.run(
            [str(arg) for arg in args], cwd=self.root, input=input, capture_output=True, timeout=25
        )
        if good:
            self.assertEqual(result.returncode, 0, "stderr:\n" + result.stderr.decode(errors="replace") +
                             "\nstdout:\n" + result.stdout.decode(errors="replace"))
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        return result

    def hosted(self, program, output, *flags):
        source = self.source("main.vqr", "package main\nimport (io)\n" + program)
        self.process(VAQC, *flags, source, "-o", "program")
        self.assertEqual(self.process("./program").stdout.decode(), output)


class Acceptance(CompilerCase):
    def test_v020_parameter_modes_and_reference_sources(self):
        source = self.source("modes.vqr", '''
            package main;
            import (io);

            func add(value: i32) i32 { value++; value }
            func inspect<T: Copyable + Movable>(value: in T) T { value }
            func increment(value: mut i32) void { value++; }
            func first(a: in i32, b: in i32) ref i32 from a { a }
            func take(owner: move ^i32) i32 { owner.intoInner() }
            func identity(x: in i32) ref i32 { x }

            func main() !void {
                var value = 10;
                io.println(add(value))!;
                io.println(inspect(value))!;
                {
                    ref mut alias = value;
                    var update: func(mut i32) void = increment;
                    update(alias);
                    var inspectAgain: func(in i32) i32 = (x: in i32) i32 => x;
                    io.println(inspectAgain(alias))!;
                }
                {
                    var pick: func(left: in i32, right: in i32) ref i32 from left = first;
                    var selected: ref i32 = pick(value, 2);
                    var pointer: *const i32 = selected.toRaw();
                    io.println(unsafe { *pointer })!;
                }
                io.println(*identity(42))!;
                var pointer = raw value;
                unsafe { increment(*pointer); }
                var box = new i32(value)!;
                increment(*box);
                var consume: func(move ^i32) i32 = take;
                io.println(consume(move(box)))!;
                box = new i32(14)!;
                io.println(take(move(box)))!;
            }
        ''')
        for optimization in ("-O0", "-O2"):
            with self.subTest(optimization=optimization):
                self.process(VAQC, "build", optimization, "-g", source, "-o", "modes")
                self.assertEqual(self.process("./modes").stdout, b"11\n10\n11\n11\n42\n13\n14\n")

    def test_v020_rejects_invalid_argument_modes_and_borrows(self):
        prefix = '''
            func read(n: in i32) i32 { n }
            func edit(n: mut i32) void { n++; }
            func mixed(a: in i32, b: mut i32) void { b += a; }
            func same(a: mut i32, b: mut i32) void { a++; b++; }
            func identity(n: in i32) ref i32 { n }
            func take(box: move ^i32) void {}
        '''
        cases = {
            "temporary_mut": ("edit(1);", "borrow requires"),
            "const_mut": ("const n = 1; edit(n);", "read-only"),
            "shared_mut": ("var n = 1; var r = ref n; edit(r);", "no accessible overload"),
            "implicit_pointer": ("var n = 1; var p = raw n; read(p);", "no accessible overload"),
            "raw_access": ("var n = 1; var p = raw n; edit(*p);", "requires unsafe"),
            "implicit_box": ("var b = new i32(1)!; edit(b);", "no accessible overload"),
            "read_and_write": ("var n = 1; mixed(n, n);", "active borrow"),
            "two_writes": ("var n = 1; same(n, n);", "active borrow"),
            "reference_escapes": ("var r = identity(1);", "borrow from a temporary"),
            "implicit_move": ("var b = new i32(1)!; take(b);", "move"),
            "moved_value": ("var b = new i32(1)!; take(move(b)); read(*b);", "not initialized"),
            "live_box_borrow": ("var b = new i32(1)!; var r = ref *b; b.intoInner();", "active borrow"),
            "shared_pattern": ("var n = some(1); if let some(ref r) = n { r++; }", "writable"),
            "exclusive_pattern": ("const n = some(1); if let some(ref mut r) = n {}", "writable storage"),
            "temporary_pattern_escape": ("""
                var base = 0;
                var escaped: ref i32 = switch some(1) {
                    some(ref r) => ref r,
                    null => ref base,
                };
            """, "outlive its owner"),
        }
        for name, (body, message) in cases.items():
            with self.subTest(case=name):
                source = self.source(name + ".vqr", prefix + "func f() !void { " + body + " }")
                self.assertIn(message.encode(), self.process(VAQC, "check", source, good=False).stderr)
        source = self.source("bad_return.vqr", "func f() ref i32 { var n = 1; ref n }")
        self.assertIn(b"borrow source", self.process(VAQC, "check", source, good=False).stderr)
        source = self.source("bad_move.vqr", "func f(n: move i32) void {}")
        self.assertIn(b"non-Copyable", self.process(VAQC, "check", source, good=False).stderr)

    def test_v020_consuming_captured_box_requires_moving_the_closure(self):
        setup = """
            var owner = new i32(9)!;
            var take = [move(owner)]() i32 => owner.intoInner();
        """
        source = self.source("repeat.vqr", "func main() !void { " + setup + "take(); take(); }")
        self.assertIn(b"closure must be consumed with move",
                      self.process(VAQC, "check", source, good=False).stderr)
        self.hosted("func main() !void { " + setup + """
            io.println(move(take)())!;
        }""", "9\n", "build", "-O2")
        source = self.source("again.vqr", "func main() !void { " + setup +
                             "move(take)(); move(take)(); }")
        self.assertIn(b"not initialized", self.process(VAQC, "check", source, good=False).stderr)

    def test_v020_if_let_and_labeled_loops(self):
        self.hosted('''
            func main() !void {
                var n = 0;
                outer: while n < 4 {
                    n++;
                    for i in 0..3 {
                        if n == 2 { continue outer; }
                        if n == 3 { break outer; }
                        if let some(value) = some(n + i) { io.println(value)!; }
                    }
                }
                var missing: ?i32 = null;
                if let some(value) = missing { io.println(value)!; } else { io.println(n)!; }
                var item = some(4);
                if let some(ref mut value) = item { value++; }
                if let some(ref value) = item { io.println(value)!; }
                if let some(ref value) = some(6) {
                    var saved = ref value;
                    io.println(saved)!;
                }
            }
        ''', "1\n2\n3\n3\n5\n6\n")

    def test_v020_deferred_calls_capture_and_unwind(self):
        program = """
            class Probe {
                var id: i32;
                pub func init(id: i32) : id(id) {}
                pub const func report() void { record(id); }
                func deinit() void { record(id + 100); }
            }
            error Stop { Failure }
            func record(value: i32) void { io.println(value) catch {}; }
            func other(value: i32) void { record(value + 1000); }
            func pair(left: i32, right: i32) void { record(left); record(right); }
            func observe(value: i32) i32 { record(value); value }
            func readBorrow(value: in i32) void { record(value); }
            func consume(box: move ^i32) void { record(box.intoInner()); }
            func failArgument() !i32 { Stop.Failure }
            func pendingFailure() !void {
                defer record(30);
                defer record(failArgument()!);
            }
            func laterFailure() !void {
                defer record(33);
                var failed = failArgument()!;
            }
            func destroy(probe: move ^Probe, value: i32) void { record(value); }
            func failedAfterMove() !void {
                defer record(37);
                var owned = new Probe(38)!;
                defer destroy(move(owned), failArgument()!);
            }
            func optionalFailure() ?i32 {
                defer record(35);
                var missing: ?i32 = null;
                missing?
            }
            func returnCase() i32 {
                var first = Probe(1);
                var value = 2;
                defer record(observe(value));
                value = 3;
                var second = Probe(4);
                defer record(5);
                return observe(6);
            }
            func main() !void {
                record(returnCase());
                {
                    var target: func(i32) void = record;
                    defer target(7);
                    target = other;
                    var probe = Probe(8);
                    defer probe.report();
                    var box = new i32(9)!;
                    defer consume(move(box));
                    record(10);
                }
                var source = 11;
                { defer readBorrow(source); }
                source = 12;
                record(source);
                {
                    defer pair(observe(13), observe(14));
                    record(15);
                }
                pendingFailure() catch { record(31); };
                laterFailure() catch { record(34); };
                if optionalFailure() == null { record(36); }
                failedAfterMove() catch { record(39); };
                outer: for n in 0..4 {
                    defer record(n + 40);
                    for m in 0..1 {
                        defer record(45);
                        if n == 0 { continue outer; }
                        if n == 2 { break outer; }
                    }
                    record(49);
                }
            }
        """
        expected = ("2\n6\n5\n104\n2\n101\n6\n10\n9\n8\n108\n7\n11\n12\n"
                    "13\n14\n15\n13\n14\n30\n31\n33\n34\n35\n36\n138\n37\n39\n"
                    "45\n40\n45\n49\n41\n45\n42\n")
        for optimization in ("-O0", "-O2"):
            with self.subTest(optimization=optimization):
                self.hosted(program, expected, optimization)

    def test_v020_deferred_call_diagnostics(self):
        prefix = """
            class Probe {
                pub func init() {}
                pub const func report() void {}
                pub const func withArg(value: i32) void {}
                func deinit() void {}
            }
            func record(x: i32) void {}
            func read(x: in i32) void {}
            func borrowProbe(x: in Probe) void {}
            func mutate(x: mut i32) void {}
            func mixed(x: (ref i32, str)) void {}
            func fallible() !void {}
            func value() i32 { 1 }
        """
        cases = {
            "non_call": ("defer 1;", "defer requires a call"),
            "missing_semicolon": ("defer record(1)", "expected ';'"),
            "temporary": ("defer read(1);", "borrow from a temporary"),
            "overwritten": ("var x = 1; defer read(x); x = 2;", "active borrow"),
            "exclusive": ("var x = 1; defer mutate(x); record(x);", "active borrow"),
            "moved": ("var x = Probe(); defer borrowProbe(x); var y = move(x);", "active borrow"),
            "method_receiver": ("var x = Probe(); defer x.report(); var y = move(x);", "active borrow"),
            "receiver_argument_replaces_owner": (
                "var box = new Probe()!; defer (*box).withArg({ box = new Probe()!; 0 });",
                "active borrow",
            ),
            "mixed_aggregate": (
                'var x = 1; var pair: (ref i32, str) = (ref x, "view"); defer mixed(pair);',
                "untracked borrows",
            ),
            "unhandled_error": ("defer fallible();", "defer requires a runtime void call"),
            "non_void": ("defer value();", "defer requires a runtime void call"),
        }
        for name, (body, message) in cases.items():
            with self.subTest(case=name):
                source = self.source(name + ".vqr", prefix + "func main() !void { " + body + " }")
                self.assertIn(message.encode(), self.process(VAQC, "check", source, good=False).stderr)

    def test_v020_deferred_blocks_capture_lexical_bindings(self):
        program = """
            error BlockError { Failed }
            class Receiver {
                var id: i32;
                pub func init(id: i32) : id(id) {}
                pub const func reportLater() void {
                    defer { record(id); }
                    record(61);
                }
            }
            func record(value: i32) void { io.println(value) catch {}; }
            func cleanup() !void { BlockError.Failed }
            func formerExit() !i32 {
                defer {
                    cleanup() catch (reason) {
                        if reason == BlockError.Failed { record(100); }
                    };
                }
                return 101;
            }
            func preservedError() !void {
                defer { cleanup() catch { record(102); }; }
                cleanup()!;
            }
            func main() !void {
                var outside = 7;
                {
                    defer {
                        record(outside);
                        var local = 3;
                        defer { record(local); }
                        for i in 0..4 {
                            if i == 0 { continue; }
                            if i == 2 { break; }
                            record(40 + i);
                        }
                    }
                    var outside = 8;
                    record(outside);
                }
                var state = 1;
                {
                    defer { state += 3; }
                    defer record(55);
                    record(50);
                }
                record(state);
                var free: i32;
                {
                    defer { var free = 10; record(free); }
                    free = 9;
                    record(free);
                }
                var receiver = Receiver(62);
                receiver.reportLater();
                record(formerExit()!);
                preservedError() catch (reason) {
                    if reason == BlockError.Failed { record(103); }
                };
            }
        """
        expected = "8\n7\n41\n3\n50\n55\n4\n9\n10\n61\n62\n100\n101\n102\n103\n"
        for optimization in ("-O0", "-O2"):
            with self.subTest(optimization=optimization):
                self.hosted(program, expected, optimization)

    def test_v020_deferred_block_rejects_escaping_control_and_invalid_captures(self):
        prefix = """
            error BlockError { Failed }
            func cleanup() !void { BlockError.Failed }
            func show(n: i32) void {}
        """
        cases = {
            "returns": ("defer { return; }", "deferred cleanup cannot return"),
            "loop_exit": ("for { defer { break; } break; }", "deferred cleanup cannot jump"),
            "propagated_error": ("defer { cleanup()!; }", "deferred cleanup cannot propagate"),
            "propagated_absence": (
                "defer { var missing: ?i32 = null; var value = missing?; }",
                "deferred cleanup cannot propagate",
            ),
            "ignored_error": ("defer { cleanup(); }", "unused error result"),
            "bound_but_ignored_error": (
                "defer { var unused = cleanup(); }",
                "handle the error before binding",
            ),
            "empty_catch": ("defer { cleanup() catch {}; }", "empty catch discards"),
            "write_after_read_capture": ("var x = 1; defer { show(x); } x = 2;", "active borrow"),
            "read_after_write_capture": ("var x = 1; defer { x = 2; } show(x);", "active borrow"),
            "uninitialized": ("var x: i32; defer { show(x); } x = 1;", "not initialized"),
            "move_capture": (
                "var box = new i32(1)!; defer { var moved = move(box); }",
                "source cannot be moved",
            ),
            "deferred_raw": (
                "var x = 1; var y = raw x; defer { unsafe { show(*y); } }",
                "untracked borrows",
            ),
        }
        for name, (body, message) in cases.items():
            with self.subTest(case=name):
                source = self.source(name + ".vqr", prefix + "func main() !void { " + body + " }")
                self.assertIn(message.encode(), self.process(VAQC, "check", source, good=False).stderr)

    def test_v020_deferred_closures_keep_their_environment(self):
        program = """
            error Stop { Failure }
            class Probe {
                var id: i32;
                pub func init(id: i32) : id(id) {}
                func deinit() void { record(id + 100); }
            }
            func record(value: i32) void { io.println(value) catch {}; }
            func failed() !i32 { Stop.Failure }
            func abandon() !void {
                var resource = new Probe(24)!;
                var callback = [move(resource)] (value: i32) void => { record(value); };
                defer move(callback)(failed()!);
            }
            func main() !void {
                {
                    var base = 20;
                    var callback = [base] (amount: i32) void => { base += amount; record(base); };
                    defer callback(2);
                    base = 99;
                    record(21);
                }
                {
                    var owned = new i32(23)!;
                    var consuming = [move(owned)] () void => { var box = move(owned); record(box.intoInner()); };
                    defer move(consuming)();
                    record(22);
                }
                abandon() catch { record(25); };
            }
        """
        for optimization in ("-O0", "-O2"):
            with self.subTest(optimization=optimization):
                self.hosted(program, "21\n22\n22\n23\n124\n25\n", optimization)

        invalid = self.source("moved_callback.vqr", """
            func main() void {
                var number = 1;
                var callback = [number] () void => { number++; };
                defer callback();
                var moved = move(callback);
            }
        """)
        self.assertIn(b"conflicts with active borrow", self.process(VAQC, "check", invalid, good=False).stderr)
        invalid = self.source("mutable_callback_capture.vqr", """
            func main() void {
                var number = 1;
                var callback = [number] () void => { number++; };
                defer { callback(); }
                callback();
            }
        """)
        self.assertIn(b"conflicts with active borrow", self.process(VAQC, "check", invalid, good=False).stderr)

    def test_v020_closure_environment_mutation_preserves_shared_referents(self):
        self.hosted("""
            class View {
                #borrowed: scoped
                pub var number: ref i32;
            }
            func record(value: i32) void { io.println(value) catch {}; }
            func main() void {
                var referent = 10;
                {
                    var view = View{number: ref referent};
                    var counter = 0;
                    var first = [view, counter]() void => {
                        counter++;
                        record(*view.number + counter);
                    };
                    var second = first;
                    first();
                    defer first();
                    defer { second(); }
                }
                referent++;
                record(referent);
            }
        """, "11\n11\n12\n11\n")

        invalid = self.source("closure_environment_reserved.vqr", """
            class View {
                #borrowed: scoped
                pub var number: ref i32;
            }
            func main() void {
                var referent = 1;
                var view = View{number: ref referent};
                var counter = 0;
                var update = [view, counter]() void => { counter += *view.number; };
                defer update();
                update();
            }
        """)
        self.assertIn(b"active borrow of 'update'", self.process(VAQC, "check", invalid, good=False).stderr)

    def test_v020_borrow_projections(self):
        prelude = '''
            class Pair {
                pub var left: i32;
                pub var right: i32;
                pub func init() : left(1), right(2) {}
            }
            func adjust(a: mut i32, b: mut i32) void { a++; b += 10; }
            func clear(pair: mut Pair) void { pair.left = 0; pair.right = 0; }
        '''
        self.hosted(prelude + '''
            func main() !void {
                var pair = Pair();
                adjust(pair.left, pair.right);
                {
                    ref mut left = pair.left;
                    pair.right++;
                    left++;
                }
                io.println(pair.left)!;
                io.println(pair.right)!;
            }
        ''', "3\n13\n")
        for name, body in {
            "same_field": "var p = Pair(); adjust(p.left, p.left);",
            "whole_owner": "var p = Pair(); ref mut r = p.left; clear(p);",
            "dynamic_indices": "var a = [2]i32{1, 2}; var i = 1; adjust(a[0], a[i]);",
        }.items():
            with self.subTest(case=name):
                source = self.source(name + ".vqr", prelude + "func f() void { " + body + " }")
                self.assertIn(b"active borrow", self.process(VAQC, "check", source, good=False).stderr)

    def test_v020_receiver_reservation_preserves_order_and_nested_reads(self):
        source = self.source("receiver-order.vqr", """
            package main;
            import (coll, io);
            var order: i32 = 0;
            func step(expected: i32) i32 {
                if order != expected { panic("incorrect operand evaluation order"); }
                order++;
                expected
            }
            interface Reader { const func readMore(extra: i32) i32; }
            class Counter {
                #implements: Reader
                pub var value: i32;
                pub func init() : value(4) {}
                pub const func read() i32 { value }
                pub const func readMore(extra: i32) i32 { value + extra }
                pub func add(a: i32, b: i32) i32 { value += a + b; value }
            }
            func selectCounter(counter: mut Counter) ref mut Counter from counter {
                step(0);
                counter
            }
            func main() !void {
                var counter = Counter();
                io.println(selectCounter(counter).add(step(1), step(2)))!;
                io.println(counter.add(counter.read(), if order == 3 { counter.read() } else { 0 }))!;
                var reader: ref Reader = counter;
                io.println(reader.readMore(reader.readMore(1)))!;
                var data = coll.Vec<usize>();
                data.push(999)!;
                data.push(data.len())!;
                data.push(data[1] + 2)!;
                io.println(data.len())!;
                io.println(*data.get(2).unwrap())!;
            }
        """)
        for level in ("-O0", "-O2"):
            with self.subTest(optimization=level):
                self.process(VAQC, "build", level, source, "-o", "receiver-order")
                self.assertEqual(self.process("./receiver-order").stdout, b"7\n21\n43\n3\n3\n")

    def test_v020_receiver_reservation_rejects_invalidation_and_overlap(self):
        prelude = """
            package main;
            class Item {
                pub var tag: i32;
                pub func init(n: i32) : tag(n) {}
                pub const func read(extra: i32) i32 { tag + extra }
                pub func write(extra: in i32) i32 { tag += extra; tag }
            }
            func consume(value: move ^Item) i32 {
                var owned = value.intoInner();
                owned.tag
            }
        """
        cases = {
            "replace_owner": "var box = new Item(1)!; (*box).read({ box = new Item(2)!; 0 });",
            "move_owner": "var box = new Item(1)!; (*box).read(consume(move(box)));",
            "overlapping_arg": "var item = Item(1); item.write(item.tag);",
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                source = self.source(name + "/main.vqr", prelude + "func main() !void { " + body + " }")
                self.assertIn(b"active borrow", self.process(VAQC, "check", source, good=False).stderr)

    def test_v020_run_single_file(self):
        source = self.source("test run.vqr", '''
            import (bufio, io)
            func main() !i32 {
                var scan = bufio.newScanner(io.stdin())!;
                io.println(scan.readInt<i32>()!)!;
                7
            }
        ''')
        result = self.process(VAQC, "run", source, "--", "--any-program-argument",
                              input=b"18\n", good=False)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (7, b"18\n", b""))
        self.assertEqual(list(self.root.iterdir()), [source])
        result = self.process(VAQC, "run", "--emit=obj", source, good=False)
        self.assertIn(b"temporary host executable", result.stderr)

    def test_dependency_manifest_preserves_other_links_and_build_outputs(self):
        source = self.source("main.vqr", "func main() i32 { 0 }\n")
        original = self.source("original.json", '{"existing":true}\n')
        manifest = self.root / "dependencies.json"
        os.link(original, manifest)
        self.process(VAQC, "check", "--dependencies", manifest, source)
        self.assertEqual(original.read_text(encoding="utf-8"), '{"existing":true}\n')
        self.assertIn(str(source), json.loads(manifest.read_bytes())["files"])

        unchanged = manifest.read_bytes()
        result = self.process(VAQC, "build", "--dependencies", manifest, source,
                              "-o", manifest, good=False)
        self.assertIn(b"--dependencies must name a separate .json file", result.stderr)
        self.assertEqual(manifest.read_bytes(), unchanged)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux runtime cache")
    def test_runtime_cache_requires_a_protected_directory(self):
        source = self.source("main.vqr", 'import (io)\nfunc main() !void { io.print("safe")!; }\n')
        cache = self.root / "cache"
        runtime = cache / "vaqrin/runtime"
        runtime.mkdir(parents=True)
        with patch.dict(os.environ, XDG_CACHE_HOME=str(cache)):
            runtime.chmod(0o777)
            first = self.process(VAQC, "build", "--timings", source, "-o", "program")
            self.assertIn(b"io_posix.cpp uncached", first.stderr)
            self.assertEqual(list(runtime.iterdir()), [])
            runtime.chmod(0o700)
            second = self.process(VAQC, "build", "--timings", source, "-o", "program")
            self.assertIn(b"io_posix.cpp cache miss", second.stderr)
            third = self.process(VAQC, "build", "--timings", source, "-o", "program")
            self.assertIn(b"io_posix.cpp cache hit", third.stderr)
            self.assertEqual(self.process("./program").stdout, b"safe")

    @unittest.skipUnless(os.name == "posix", "Zed run task uses POSIX file locking")
    def test_zed_rerun_verifies_successful_artifacts(self):
        runner = self.root / ".zed/run.py"
        runner.parent.mkdir()
        shutil.copyfile(REPOSITORY / ".zed/run.py", runner)
        build = self.root / "build/host"
        build.mkdir(parents=True)
        (build / "vaqc").symlink_to(VAQC)
        source = self.source("main.vqr", "func main() i32 { 0 }\n")
        arguments = (sys.executable, runner)
        self.assertIn(b"building main.vqr", self.process(*arguments, "run", source).stderr)
        self.assertIn(b"skipping compilation", self.process(*arguments, "run", source).stderr)
        source.write_text("func main() i32 { 17 }\n", encoding="utf-8")
        self.assertIn(b"reusing last successful", self.process(*arguments, "rerun", source).stderr)

        program, = (build / "run").glob("*/program")
        previous = program.with_name("previous")
        program.rename(previous)
        program.symlink_to(previous)
        rejected = self.process(*arguments, "rerun", source, good=False)
        self.assertIn(b"no verified successful build", rejected.stderr)
        updated = self.process(*arguments, "run", source, good=False)
        self.assertEqual(updated.returncode, 17, updated.stderr)
        self.assertFalse(program.is_symlink())

    def test_shipped_packages_load_together(self):
        packages = sorted({source.parent.name for source in (REPOSITORY / "stdlib").glob("*/*.vqr")})
        program = self.source("packages.vqr", "import (" + ", ".join(packages) + ")\nfunc f() void {}")
        self.process(VAQC, "check", program)

    def test_persistent_standard_library_syntax_cache(self):
        source = self.source("run.vqr", "import (fixture)\nfunc main() i32 { fixture.value() }\n")
        package = self.source("stdlib/fixture/module.vqr",
                              "package fixture\n" + " // cached source\n" * 2200 +
                              "pub func value() i32 { 19 }\n")
        cache = self.root / "syntax"
        with patch.dict(os.environ, VAQRIN_AST_CACHE=str(cache),
                        VAQRIN_STDLIB_DIR=str(package.parents[1]),
                        VAQRIN_RUNTIME_DIR=str(REPOSITORY / "compiler/runtime")):
            self.process(VAQC, source, "-o", "from-source")
            self.assertEqual(self.process("./from-source", good=False).returncode, 19)
            entry, = cache.glob("*.ast")
            signature = entry.stat().st_ino, entry.stat().st_mtime_ns
            self.process(VAQC, source, "-o", "from-cache")
            self.assertEqual(self.process("./from-cache", good=False).returncode, 19)
            self.assertEqual((entry.stat().st_ino, entry.stat().st_mtime_ns), signature)

            entry.write_bytes(b"broken syntax record")
            self.process(VAQC, source, "-o", "after-corruption")
            self.assertEqual(self.process("./after-corruption", good=False).returncode, 19)
            self.assertGreater(entry.stat().st_size, 64)

            package.write_text(package.read_text().replace("{ 19 }", "{ 27 }"), encoding="utf-8")
            self.process(VAQC, source, "-o", "after-edit")
            self.assertEqual(self.process("./after-edit", good=False).returncode, 27)
            self.assertEqual(len(list(cache.glob("*.ast"))), 2)

    def test_compiled_standard_library_integer_tables(self):
        program = self.source("main.vqr", """
            import (fixture)
            func main() i32 {
                if fixture.wide[511] != 4294967296u64 { return 1; }
                if fixture.table[1000] == 19u32 { return 19; }
                if fixture.table[1000] == 31u32 { return 31; }
                2
            }
        """)
        base = "package fixture\n"
        base += "pub const table = [_]u32{" + ",".join(["19u32"] * 1024) + "};\n"
        base += "pub const wide = [_]u64{" + ",".join(["4294967296u64"] * 1024) + "};\n"
        module = self.source("stdlib/fixture/data.vqr", base)
        cache = self.root / "cache"
        with patch.dict(os.environ, VAQRIN_AST_CACHE=str(cache),
                        VAQRIN_STDLIB_DIR=str(module.parents[1]),
                        VAQRIN_RUNTIME_DIR=str(REPOSITORY / "compiler/runtime")):
            def run(expected):
                self.process(VAQC, program, "-o", "table-program")
                self.assertEqual(self.process("./table-program", good=False).returncode, expected)

            run(19)
            entries = sorted(cache.glob("*.table"))
            self.assertEqual(len(entries), 2)
            signatures = [file.stat().st_mtime_ns for file in entries]
            run(19)
            self.assertEqual([file.stat().st_mtime_ns for file in entries], signatures)

            entries[0].write_bytes(b"broken compiled constant")
            run(19)
            self.assertGreater(len(entries[0].read_bytes()), 64)

            module.write_text(base.replace("19u32", "31u32"), encoding="utf-8")
            run(31)
            self.assertEqual(len(list(cache.glob("*.table"))), 4)

    def test_optimized_module_cache_reuses_verified_bitcode(self):
        helper = self.source("math_data/values.vqr", "package math_data\npub func square() i32 { 42 }")
        source = self.source("main.vqr", "import (math_data)\nfunc main() i32 { math_data.square() }")
        cache = self.root / "artifacts"
        with patch.dict(os.environ, VAQRIN_MODULE_CACHE=str(cache)):
            def build(expected, hit=False, level="-O2"):
                compiled = self.process(VAQC, "build", "--timings", level, source, "-o", "cached")
                keyword = "hit" if hit else "miss"
                self.assertIn(f"optimization cache {keyword}".encode(), compiled.stderr)
                self.assertEqual(self.process("./cached", good=False).returncode, expected)

            build(42)
            entry, = cache.glob("*.bc")
            signature = entry.stat().st_mtime_ns
            build(42, hit=True)
            self.assertEqual(entry.stat().st_mtime_ns, signature)

            entry.write_bytes(b"invalid cache")
            build(42)
            invalid = b"not LLVM bitcode"
            entry.write_bytes(hashlib.sha256(invalid).hexdigest().encode() + invalid)
            build(42)
            build(42, hit=True)
            build(42, level="-O1")

            helper.write_text("package math_data\npub func square() i32 { 19 }", encoding="utf-8")
            build(19)
            self.assertEqual(len(list(cache.glob("*.bc"))), 3)

            args = ("build", "--timings", "-O2", "--emit=llvm-ir", source)
            self.process(VAQC, *args, "-o", "fresh.ll")
            warm = self.process(VAQC, *args, "-o", "cached.ll")
            self.assertIn(b"optimization cache hit", warm.stderr)
            self.assertEqual((self.root / "fresh.ll").read_bytes(), (self.root / "cached.ll").read_bytes())

    def test_large_zeroed_byte_array_native_codegen(self):
        program = self.source("zeros.vqr", """
            import (mem)
            const count: usize = 131072;
            const example = mem.zeroedBytes<4>();
            func main() i32 {
                var bytes = mem.zeroedBytes<count>();
                for byte in bytes[:] { if byte != 0 { return 1; } }
                bytes[count - 1] = 7;
                if bytes[count - 1] != 7 || example[0] != 0 { return 2; }
                0
            }
        """)
        self.process(VAQC, "build", "-O0", program, "-o", "zeros")
        self.process("./zeros")
        self.process(VAQC, "build", "-O0", "--emit=obj", program, "-o", "zeros.o")
        self.process(VAQC, "build", "-O0", "--profile=freestanding",
                     "--emit=obj", program, "-o", "freestanding.o")
        symbols = self.process("llvm-nm", "--undefined-only", "freestanding.o")
        self.assertNotIn(b"memset", symbols.stdout)

    def test_large_local_array_copy_at_o0(self):
        source = self.source("local.vqr", """
            import (mem)
            func main() i32 {
                var original = mem.zeroedBytes<131072>();
                original[131071] = 18;
                var copy = original;
                original[131071] = 29;
                if copy[131071] != 18 { return 1; }
                0
            }
        """)
        self.process(VAQC, "build", "-O0", "-g", source, "-o", "copy")
        self.process("./copy")
        self.process(VAQC, "build", "-O0", "--profile=freestanding", "--emit=obj", source, "-o", "local.o")
        symbols = self.process("llvm-nm", "--undefined-only", "local.o").stdout
        self.assertNotIn(b"memcpy", symbols)

    def test_large_constant_arrays_copy_in_bulk(self):
        pattern = ",".join("17u32" if index % 2 == 0 else "254u32" for index in range(32768))
        literals = ",".join("7u8" for _ in range(4096))
        source = self.source("large_arrays.vqr", f"""
            const base = [_]u32{{{pattern}}};
            func main() i32 {{
                var copied = base;
                var inline = [_]u8{{{literals}}};
                copied[0] = 10;
                inline[0] = 1;
                if base[0] != 17u32 || copied[0] != 10u32 || copied[32767] != 254u32 {{ return 1; }}
                if inline[0] != 1u8 || inline[4095] != 7u8 {{ return 2; }}
                0
            }}
        """)
        self.process(VAQC, "build", "-O0", source, "-o", "copied")
        self.process("./copied")
        self.process(VAQC, "build", "-O0", "--profile=freestanding", "--emit=obj", source, "-o", "copied.o")
        symbols = self.process("llvm-nm", "--undefined-only", "copied.o").stdout
        self.assertNotIn(b"memcpy", symbols)
        self.assertNotIn(b"memset", symbols)

    def test_windows_release_entry_uses_native_system_entropy(self):
        release = runpy.run_path(str(REPOSITORY / "compiler/tools/release.py"))
        source = self.source("release.vqr", release["WINDOWS_SMOKE_SOURCE"])
        for arch in ("x86_64", "aarch64"):
            with self.subTest(arch=arch):
                filename = f"{arch}.obj"
                target = f"{arch}-pc-windows-msvc"
                self.process(VAQC, "build", "--target", target, "--emit=obj", source, "-o", filename)
                symbols = self.process("llvm-nm", "--extern-only", filename).stdout
                self.assertIn(b"T main", symbols)
                self.assertIn(b"U BCryptGenRandom", symbols)
                self.assertNotIn(b"__vqr_io_random", symbols)

    def test_large_constant_compilation(self):
        def timings(path):
            run = self.process(VAQC, "check", "--timings", path)
            result = dict(re.findall(r"vaqc: (compile[.a-z]*) (\d+(?:\.\d+)?(?:[eE][+-]?\d+)?) ms",
                                     run.stderr.decode()))
            self.assertTrue({"compile", "compile.types", "compile.instantiation",
                             "compile.ctfe", "compile.lowering"}.issubset(result))
            return float(result["compile"])

        samples = []
        for size in (4096, 8192):
            values = ",".join(str(index) for index in range(size))
            path = self.source(f"const{size}.vqr", f"const values: [{size}]u32 = [{size}]u32{{{values}}};")
            samples.append(timings(path))
        self.assertLess(samples[1], samples[0] * 3 + 200)
        self.assertLess(timings(REPOSITORY / "examples/stdlib/regex.vqr"), 6000)

    def test_git_and_registry_dependencies_build_offline(self):
        if not shutil.which("git"):
            self.skipTest("git is required to prepare dependency fixtures")

        def repository(directory, package, number):
            folder = self.root / directory
            self.source(f"{directory}/src/{package}.vqr",
                        f"package {package}\npub func number() i32 {{ {number} }}")
            self.process("git", "-C", folder, "init", "--quiet")
            self.process("git", "-C", folder, "add", "--", "src")
            self.process("git", "-C", folder, "-c", "user.name=Acceptance",
                         "-c", "user.email=acceptance@example.invalid", "commit", "--quiet", "-m", "Fixture")
            return self.process("git", "-C", folder, "rev-parse", "HEAD").stdout.decode().strip()

        revision = repository("git-source", "model", 40)
        indexed = repository("index-source", "offset", 2)
        project = self.root / "offline"
        entry = self.source("offline/main.vqr",
                            "package offline\nimport (model, offset)\nfunc main() i32 { model.number() + offset.number() - 42 }")
        self.source("offline/package.toml", f"""
            [build]
            entry = "offline::main"
            [registry]
            index = "index.json"
            [dependencies]
            model = {{ git = "../git-source", rev = "{revision}", path = "src" }}
            offset = {{ version = "1.2.3" }}
        """)
        index = self.source("offline/index.json", json.dumps({
            "offset": {"1.2.3": {"git": "../index-source", "rev": indexed, "path": "src"}}
        }))
        cache = self.root / "cache"
        environment = {**os.environ, "VAQRIN_PACKAGE_CACHE": str(cache)}

        def compiler(*args, good=True):
            result = subprocess.run([str(VAQC), *(str(x) for x in args)], env=environment,
                                    cwd=self.root, capture_output=True, timeout=25)
            self.assertEqual(result.returncode == 0, good, result.stderr.decode(errors="replace"))
            return result

        self.assertIn(b"offline cache", compiler("check", entry, good=False).stderr)
        compiler("lock", project)
        lock = (project / "package.lock").read_text()
        self.assertIn(revision, lock)
        self.assertIn('registry "." "offset" "1.2.3"', lock)
        index.unlink()
        shutil.move(self.root / "git-source", self.root / "unavailable-git")
        shutil.move(self.root / "index-source", self.root / "unavailable-index")
        relocated = self.root / "relocated-cache"
        shutil.move(cache, relocated)
        environment["VAQRIN_PACKAGE_CACHE"] = str(relocated)
        compiler("build", project, "-o", "offline-build")
        self.process("./offline-build")
        generated = next(relocated.glob("*/src/model.vqr"))
        generated.write_text("package model\npub func number() i32 { 99 }", encoding="utf-8")
        self.assertIn(b"missing or stale", compiler("check", entry, good=False).stderr)

    def test_local_path_dependencies_and_lock(self):
        project = self.root / "app"
        self.source("app/package.toml", '[build]\nentry = "app::main"\n'
                    '[dependencies]\nmodels = { path = "../model-files" }\n')
        main = self.source("app/main.vqr", "package app\nimport (models)\nfunc main() i32 { models.answer() - 42 }")
        self.source("model-files/package.toml", "[dependencies]\nbase = { path = '../base-files' }\n")
        self.source("model-files/models.vqr",
                    "package models\nimport (base)\npub func answer() i32 { base.answer() }")
        base = self.source("base-files/base.vqr", "package base\npub func answer() i32 { 42 }")
        self.assertIn(b"missing or stale", self.process(VAQC, "check", main, good=False).stderr)
        self.process(VAQC, "lock", project)
        lock = project / "package.lock"
        first_lock = lock.read_bytes()
        self.assertIn(b"base.vqr", first_lock)
        self.process(VAQC, "lock", project)
        self.assertEqual(lock.read_bytes(), first_lock)
        self.process(VAQC, "build", project, "-o", "locked")
        self.process("./locked")
        base.write_text("package base\npub func answer() i32 { 43 }", encoding="utf-8")
        self.assertIn(b"missing or stale", self.process(VAQC, "check", main, good=False).stderr)
        self.process(VAQC, "lock", project)
        self.process(VAQC, "check", main)
        self.assertNotEqual(lock.read_bytes(), first_lock)

    def test_tuple_projections_and_optional_tag_without_copy(self):
        self.hosted('''
            class Owned {
                #copy: disabled
                pub var value: i32;
                func deinit() void { io.println(value) catch {}; }
            }
            func main() !void {
                var tuple = ((1, 2), 3);
                tuple.0.1 = 9;
                var pointer: *const ((i32, i32), i32) = raw tuple;
                io.println(pointer.0.1)!;
                var optional = some(tuple.0);
                io.println(optional?.1.unwrap())!;
                var moveOnly = some(Owned{value: 40});
                io.println(null != moveOnly)!;
                io.println(move(moveOnly) == null)!;
            }
        ''', '9\n9\ntrue\n40\nfalse\n')
        for code, diagnostic in (
            ("func f(t: (i32,i32)) i32 { t.2 }", "tuple field index is out of range"),
            ("func f(t: *const (i32,i32)) void { t.0 = 9; }", "writable"),
        ):
            file = self.source("invalid_tuple.vqr", code)
            result = self.process(VAQC, "check", file, good=False)
            self.assertIn(diagnostic.encode(), result.stderr)

    def test_syntax_sema(self):
        self.source("geometry/math.vqr", '''
            package geometry
            pub func twice(x: i32) i32 { x * 2 }
        ''')
        source = self.source("root/main.vqr", '''
            package main
            import (geometry as g, io)
            type NestedOptional = ??i32;
            type Nested = !NestedOptional;
            func id<T: Copyable>(x: T) T { x }
            func main() !void {
                café := [_]i32{g.twice(3), id<i32>(4),};
                var value: i32;
                if true { value = café[0]; } else { value = 1; }
                for i, n in enumerate(café[:]) {
                    io.println(usize(n)! + i)!;
                }
                for i := 0; i < 2; i++ { io.println(i)!; }
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

    def test_v020_raw_address_and_ampersand_operators(self):
        self.hosted('''
            func read(pointer: *const i32) i32 { unsafe { *pointer } }
            func overwrite(pointer: *i32, value: i32) void { unsafe { *pointer = value; } }
            func borrowed(value: in i32) i32 { read(value.toRaw()) }
            func raw(value: i32) i32 { value }
            func main() !void {
                var numbers = [_]i32{2, 3};
                const immutable = 6;
                var writable: *i32 = raw (numbers)[1];
                var readOnly: *const i32 = raw immutable;
                overwrite(writable, 7);
                io.println(read(raw numbers[1]))!;
                io.println(read(readOnly))!;
                io.println(borrowed(11))!;
                var bits = 6 & 3;
                bits &= 1;
                if bits == 0 && raw(5) == 5 { io.println(bits)!; }
            }
        ''', "7\n6\n11\n0\n")
        for code, diagnostic in (
            ("func bad() void { var x = 0; var p = &x; }", "use 'raw place'"),
            ("func bad(x: in i32) *const i32 { raw x }", "use toRaw()"),
            ("func bad() *const i32 { raw (1) }", "existing storage location"),
        ):
            with self.subTest(code=code):
                source = self.source("bad_raw.vqr", code)
                result = self.process(VAQC, "check", source, good=False)
                self.assertIn(diagnostic.encode(), result.stderr)

    def test_v020_direct_wrapper_limit_and_alias_composition(self):
        self.hosted('''
            type NestedOptional = ??i32;
            type DeepResult = !NestedOptional;
            type NestedResult = !!i32;
            type WrapperOnEitherSideOfPointer = !?*??i32;
            type GroupedPair = !(?i32);

            func mixed() !?i32 { some(42) }
            func bump() !?i32 { mixed()!? + 1 }
            func main() !void {
                var nested: DeepResult = ok(some(some(40)));
                var outside: ?!i32 = some(ok(41));
                var errors: NestedResult = ok(ok(42));
                io.println(nested!.unwrap().unwrap())!;
                io.println(outside.unwrap()!)!;
                io.println(errors!!)!;
                io.println(bump()! ?? 0)!;
            }
        ''', "40\n41\n42\n43\n")

        for code in [
            *(f"type Bad = {outer}{middle}{inner}i32;"
              for outer in "?!" for middle in "?!" for inner in "?!"),
            "type Bad = ? ! ? i32;",
            "type Bad = ?(!(?i32));",
            "func bad(value: !??i32) void {}",
            "func bad() void { var value: ?!!i32; }",
            "type Bad = (?i32, !??i32);",
            "type Bad = Box<!!?i32>;",
        ]:
            with self.subTest(code=code):
                source = self.source("bad_wrapper.vqr", code)
                result = self.process(VAQC, "check", source, good=False)
                self.assertIn(b"error[E110]", result.stderr)
                self.assertIn(b"at most two consecutive type wrappers", result.stderr)

    def test_v020_integer_width_and_overflow(self):
        for level in ("-O0", "-O2"):
            with self.subTest(level=level):
                self.hosted('''
                    import (convert)
                    func add(a: u8, b: u8) u8 { a + b }
                    func run(a: i8, b: u8) !void {
                        var total: u8 = add(b, 4);
                        var inverted: u8 = ~b;
                        var signed: i8 = -a;
                        var wrap: u8 = b +% 250;
                        var shifted: u8 = b << 1;
                        var count: u8 = 8;
                        var inferred: i32 = (1 << count) + 1;
                        io.println(0 < 2 < total)!;
                        io.println(total)!;
                        io.println(inverted)!;
                        io.println(signed)!;
                        io.println(wrap)!;
                        io.println(shifted)!;
                        io.println(inferred)!;
                        io.println(convert.truncBits<i8, u16>(u16(511)))!;
                        io.println(convert.narrow<u8, u16>(u16(256)) catch u8(7))!;
                    }
                    func main() !void { run(-2, 8)!; }
                ''', "true\n12\n247\n2\n2\n16\n257\n-1\n7\n", level)
                for body in ("n += 1; n", "n + 1", "n << 1", "u8(0) - n"):
                    with self.subTest(body=body):
                        source = self.source("overflow.vqr", f"""
                            func operation(n: u8) u8 {{ {body} }}
                            func main() i32 {{ i32(operation(255)) }}
                        """)
                        self.process(VAQC, level, source, "-o", "overflow")
                        result = self.process("./overflow", good=False)
                        self.assertLess(result.returncode, 0)
        mixed = self.source("mixed.vqr", "func add(a: u8, b: i16) i16 { a + b }")
        self.assertIn(b"incompatible operands", self.process(VAQC, "check", mixed, good=False).stderr)
        for operator in ("+|", "-|", "*|", "<<|", "+|=", "-|=", "*|=", "<<|="):
            with self.subTest(operator=operator):
                removed = self.source("removed.vqr", f"func f(n: u8) void {{ n {operator} 1; }}")
                self.assertIn(b"saturating operators were removed",
                              self.process(VAQC, "check", "--syntax-only", removed, good=False).stderr)

    def test_numerics_ctfe(self):
        self.hosted('''
            import (num)
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
                io.println(length(raw a))!;
                io.println(length<u8, (1 + 2)>(raw a))!;
                io.println(scale(2 + 3, 8))!;
                io.println(add(5))!;
                io.println(u8(250) + u8(5))!;
                u := u8(250); u +%= 10;
                io.println(u)!;
                io.println(num.saturatingAdd(u8(250), u8(10)))!;
                io.println(narrow(127)!)!;
                io.println(narrow(128) catch i8(7))!;
                io.println(typedRem(-2147483648, -1))!;
                io.println(typedRem(-7, 3))!;
                io.println(0 < 2 < 3)!;
                count := 0;
                for n in u8(254)..=u8(255) { count++; }
                io.println(count)!;
            }
        ''', "10\n3\n3\n40\n15\n255\n4\n255\n127\n7\n0\n-1\ntrue\n2\n", "-O2")
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
                pub func init(n: i32) : number(n) {}
                pub func deinit() void { io.println(number) catch {}; }
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
                pub func init(n: i32) : number(n) {}
                pub func init() : self(10) {}
                pub const func get() i32 { number }
                pub func deinit() void { io.println(number) catch {}; }
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
            type NestedItem = ??Item;
            func lookup(i: i32) !NestedItem {
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
            func even(x: *const i32) bool { *x % 2 == 0 }
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
                io.println(core.isSome(raw optional))!;
                io.println(core.isOk(raw result))!;
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
                io.println(sequenceLen(raw v))!;
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
                io.println(core.isOk(raw invalid))!;
            }
        ''', "静态\n2\ntrue\n中🙂\ntrue\n7\nlatest\ntrue\nfalse\n", "-O2")

    def test_vec_ownership_and_allocation_failure(self):
        self.hosted('''
            import (coll)
            class Probe {
                var id: i32;
                pub func init(id: i32) : id(id) {}
                pub const func get() i32 { id }
                pub func deinit() void { io.println(id + 100) catch {}; }
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
                pub func init(id: i32) : id(id) {}
                pub func deinit() void { unsafe { dropped(id); } }
            }
            func check() !i32 {
                var vec = coll.Vec<Probe>();
                for id in 1..=4 { vec.push(Probe(id))!; }
                unsafe { fail_next(); }
                var push = vec.push(Probe(99));
                if core.isOk(raw push) || vec.len() != 4 { return 1; }

                var builder = text.StringBuilder();
                builder.append("持久")!;
                unsafe { fail_next(); }
                var failed = builder.build();
                if core.isOk(raw failed) { return 2; }
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
        allocation = self.source("allocation.vqr", """
            package denied
            import (coll, mem)
            export "C" func allocate_default() c_int {
                var v = coll.Vec<u8>();
                v.push(1) catch (reason) {
                    if reason == mem.AllocationError.AllocatorUnavailable { return 37; }
                    return 4;
                };
                5
            }
            export "C" func allocate_explicit() c_int {
                var backing = mem.zeroedBytes<64>();
                var allocator = mem.FixedBufferAllocator(backing[:]);
                var v = coll.Vec<u8>.withAllocator(raw allocator);
                v.push(19) catch { return 3; };
                v.push(10) catch { return 4; };
                i32(v[0]) + i32(v[1])
            }
        """)
        self.process(VAQC, allocation, *options, "-O2", "--emit=obj", "-o", "bare-storage.o")
        self.assertEqual(self.process("llvm-nm", "-u", "bare-storage.o").stdout, b"")
        self.process(VAQC, allocation, "--profile", "freestanding", "-O2", "--emit=obj", "-o", "native-storage.o")
        consumer = self.source("storage.c", """
            extern int allocate_default(void), allocate_explicit(void);
            int main(void) { return allocate_default() != 37 || allocate_explicit() != 29; }
        """)
        self.process("cc", consumer, "native-storage.o", "-o", "storage")
        self.process("./storage")

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

    def test_c_aggregate_abi_and_function_pointers(self):
        source = self.source("aggregate.vqr", r"""
            class Mixed {
                #repr: C
                pub var gain: f64;
                pub var count: i32;
            }
            class Pair {
                #repr: C
                pub var x: f32;
                pub var y: f32;
            }
            class Homogeneous {
                #repr: C
                pub var pairs: [2]Pair;
            }
            class Large {
                #repr: C
                pub var a: u64;
                pub var b: u64;
                pub var c: u64;
            }
            class Bytes {
                #repr: C
                pub var a: u8;
                pub var b: u8;
                pub var c: u8;
            }
            extern "C" func transform(value: Mixed) Mixed;
            extern "C" func pressure(a,b,c,d,e,f: i64, value: Mixed, tail: i8) Mixed;
            extern "C" func callbackHost(value: Homogeneous,
                action: extern "C" func(Homogeneous) Homogeneous) Homogeneous;
            extern "C" func makeLarge(value: Large) Large;
            extern "C" func bytes(value: Bytes) Bytes;
            export "C" func modifyFours(value: Homogeneous) Homogeneous {
                value.pairs[1].y += 7.0;
                value
            }
            export "C" func echoLarge(value: Large) Large {
                unsafe { makeLarge(value) }
            }
            export "C" func exercise() i32 {
                var input = Mixed{gain: 2.5, count: 17};
                var changed = unsafe { transform(input) };
                if changed.gain != 5.0 || changed.count != 20 { return 1; }
                var callback = transform;
                var indirect = unsafe { callback(input) };
                if indirect.gain != 5.0 || indirect.count != 20 { return 2; }
                var forced = unsafe { pressure(1,2,3,4,5,6,input,-3) };
                if forced.count != 35 || forced.gain != 2.5 { return 3; }
                var pairs = Homogeneous{pairs: [2]Pair{Pair{x: 1.0, y: 2.0}, Pair{x: 3.0, y: 4.0}}};
                var floats = unsafe { callbackHost(pairs, modifyFours) };
                if floats.pairs[0].x != 1.0 || floats.pairs[1].y != 11.0 { return 4; }
                var packed = unsafe { bytes(Bytes{a: 1, b: 2, c: 3}) };
                if packed.a != 3 || packed.b != 2 || packed.c != 1 { return 5; }
                0
            }
        """)
        c = self.source("caller.c", r"""
            #include <stdint.h>
            typedef struct { double gain; int32_t count; } Mixed;
            typedef struct { float x, y; } Pair;
            typedef struct { Pair pairs[2]; } Homogeneous;
            typedef struct { uint64_t a, b, c; } Large;
            typedef struct { uint8_t a,b,c; } Bytes;
            Mixed transform(Mixed v) { return (Mixed){v.gain * 2, v.count + 3}; }
            Mixed pressure(int64_t a, int64_t b, int64_t c, int64_t d, int64_t e, int64_t f,
                           Mixed v, int8_t tail) {
                v.count += a + b + c + d + e + f + tail;
                return v;
            }
            Homogeneous callbackHost(Homogeneous v, Homogeneous (*f)(Homogeneous)) { return f(v); }
            Large makeLarge(Large v) { return (Large){v.c, v.a, v.b}; }
            Bytes bytes(Bytes v) { return (Bytes){v.c, v.b, v.a}; }
            extern Large echoLarge(Large);
            extern int32_t exercise(void);
            int main(void) {
                Large v = echoLarge((Large){1,2,3});
                if (v.a != 3 || v.b != 1 || v.c != 2) return 6;
                return exercise();
            }
        """)
        for flags in (("-g",), ("-O2",)):
            self.process(VAQC, source, "--emit=obj", *flags, "-o", "aggregate.o")
            self.process("cc", c, "aggregate.o", "-o", "caller")
            self.process("./caller")
        for triple in ("aarch64-unknown-linux-gnu", "arm64-apple-darwin",
                       "x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc"):
            self.process(VAQC, source, "--emit=obj", f"--target={triple}", "-o", "foreign.o")

        invalid = self.source("invalid.vqr", """
            class Native { pub var payload: i32; }
            extern "C" func bad(value: Native) Native;
        """)
        result = self.process(VAQC, "check", invalid, good=False)
        self.assertIn(b"#repr: C", result.stderr)

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

    def test_v020_explicit_files_do_not_discover_anonymous_siblings(self):
        program = self.source("program.vqr", "func main() i32 { 13 }")
        helper = self.source("helpers.vqr", "pub func twice(n: i32) i32 { n * 2 }")
        other = self.source("other.vqr", "func main() i32 { 22 }")
        self.source("build/ignored.vqr", "func main() void {}")
        self.assertEqual(self.process(VAQC, "run", program, good=False).returncode, 13)
        result = self.process(VAQC, "build", self.root, good=False)
        for filename in (b"program.vqr", b"other.vqr"):
            self.assertIn(filename, result.stderr)
        self.assertNotIn(b"ignored.vqr", result.stderr)
        self.assertIn(b"directory build and package::main",
                      self.process(VAQC, "build", program, other, "--entry", other, good=False).stderr)
        for expr in ("twice(5)", "helpers.twice(5)"):
            with self.subTest(expr=expr):
                program.write_text(f"func main() i32 {{ {expr} }}", encoding="utf-8")
                self.process(VAQC, "check", program, good=False)
                self.process(VAQC, "check", program, helper, good=False)

        program.write_text("package demo; func main() i32 { twice(5) }", encoding="utf-8")
        helper.write_text("package demo; func twice(n: i32) i32 { n * 2 }", encoding="utf-8")
        self.assertEqual(self.process(VAQC, "run", program, good=False).returncode, 10)
        program.write_text("package demo; func main() i32 { helpers.twice(5) }", encoding="utf-8")
        self.process(VAQC, "check", program, good=False)

    def test_v020_named_package_entry(self):
        self.source("a/main.vqr", "package alpha; func main() i32 { answer() }")
        self.source("a/shared.vqr", "package alpha; func answer() i32 { 13 }")
        self.source("b/main.vqr", "package beta; func main() i32 { 27 }")
        self.process(VAQC, "build", self.root, "--entry", "beta::main", "-o", "app")
        self.assertEqual(self.process("./app", good=False).returncode, 27)
        self.source("package.toml", "[package]\nname = 'project'\n[build]\nentry = 'alpha::main'\n")
        self.process(VAQC, "build", self.root, "-o", "app")
        self.assertEqual(self.process("./app", good=False).returncode, 13)
        self.assertIn(b"package::main", self.process(
            VAQC, "build", self.root, "--entry", "a/main.vqr", good=False).stderr)
        missing = self.process(VAQC, "build", self.root, "--entry", "missing::main", good=False)
        self.assertIn(b"no main in package missing", missing.stderr)
        self.source("a/second.vqr", "package alpha; func main() void {}")
        duplicate = self.process(VAQC, "build", self.root, "--entry", "alpha::main", good=False)
        self.assertIn(b"multiple main files in package alpha", duplicate.stderr)
        self.source("package.toml", "[build]\nentry = 'a/main.vqr'\n")
        self.assertIn(b"package::main", self.process(VAQC, "build", self.root, good=False).stderr)

    def test_v020_catch_binding_tail_and_function_return(self):
        self.hosted("""
            error Failure { Missing, Broken }
            func read(fail: bool) !i32 {
                if fail { return Failure.Missing; }
                5
            }
            func fallback(fail: bool) i32 {
                var ran = false;
                value := read(fail) catch (reason) {
                    ran = true;
                    if reason == Failure.Missing { 40 } else { 90 }
                };
                if ran { value + 2 } else { value + 1 }
            }
            func early(fail: bool) i32 {
                value := read(fail) catch (reason) {
                    if reason == Failure.Missing { return 41; }
                    13
                };
                value + 1
            }
            func shadow() i32 {
                reason := 7;
                value := read(true) catch (reason) {
                    if reason == Failure.Missing { 9 } else { 0 }
                };
                reason + value
            }
            func forward() !i32 {
                var value = read(true) catch (reason) { return reason; };
                value + 100
            }
            func main() !void {
                for result in [_]i32{fallback(false), fallback(true), early(false), early(true), shadow()} {
                    io.println(result)!;
                }
                io.println(forward() catch (reason) {
                    if reason == Failure.Missing { 11 } else { 0 }
                })!;
            }
        """, "6\n42\n6\n41\n16\n11\n")

        source = self.source("catch-leak.vqr", """
            error Failure { Missing }
            func read() !i32 { Failure.Missing }
            func main() i32 {
                value := read() catch (onlyInHandler) { 0 };
                if onlyInHandler == Failure.Missing { 1 } else { value }
            }
        """)
        result = self.process(VAQC, "check", source, good=False)
        self.assertIn(b"unknown value 'onlyInHandler'", result.stderr)

    def test_v020_if_branches_are_blocks(self):
        for body, diagnostic in (
            ("if (true) 7 else 0", "if branch must be a block"),
            ("if (true) { 7 } else 0", "else branch must be a block"),
            ("if (true) 7 else { 0 }", "if branch must be a block"),
        ):
            with self.subTest(body=body):
                entry = self.source("removed-if.vqr", f"func main() i32 {{ {body} }}")
                result = self.process(VAQC, "check", "--syntax-only", entry, good=False)
                self.assertIn(diagnostic.encode(), result.stderr)

        self.source("helper/values.vqr", "package helper; pub func value() i32 { 4 }")
        for package_suffix, import_suffix in (("", ";"), (";", ""), ("", "")):
            with self.subTest(package_suffix=package_suffix, import_suffix=import_suffix):
                entry = self.source("main.vqr", f"""
                    package app{package_suffix}
                    import (helper){import_suffix}
                    func choose(n: i32) i32 {{
                        if (n < 0) {{
                            magnitude := 0 - n;
                            magnitude + 1
                        }} else if (n == 0) {{
                            2
                        }} else {{
                            helper.value()
                        }}
                    }}
                    func main() i32 {{ choose(-3) + choose(0) + choose(7) }}
                """)
                self.assertEqual(self.process(VAQC, "run", entry, good=False).returncode, 10)

    def test_v020_loop_headers_require_canonical_spelling(self):
        for header, diagnostic in (
            ("for i < 3", "use while for a condition-only loop"),
            ("for (i < 3)", "omit parentheses around the for header"),
            ("for (j in 0..3)", "omit parentheses around the for header"),
            ("for (i = 0; i < 3; i++)", "omit parentheses around the for header"),
            ("while (i < 3)", "omit parentheses around the while header"),
        ):
            with self.subTest(header=header):
                source = self.source("removed-loop.vqr", f"func main() void {{ i := 0; {header} {{ break; }} }}")
                result = self.process(VAQC, "check", "--syntax-only", source, good=False)
                self.assertIn(diagnostic.encode(), result.stderr)

        self.hosted("""
            func main() !void {
                total := 0;
                for i in (0..3) { total += i; }
                for i := (1 + 1); i > 0; i-- { total += i; }
                outer: while (total + 1) < 10 {
                    for {
                        total++;
                        if total == 8 { continue outer; }
                        if total == 9 { break outer; }
                        break;
                    }
                }
                if let some(number) = some(total) { io.println(number)!; }
            }
        """, "9\n")

    def test_v020_local_reference_bindings_are_explicit(self):
        for binding in (
            "var &loan = raw number;",
            "var &loan: i32 = raw number;",
            "&loan := raw number;",
            "key, &loan := (7, raw number);",
            "&loan, key := (raw number, 7);",
        ):
            with self.subTest(binding=binding):
                source = self.source("legacy-binding.vqr", f"func f() void {{ number := 4; {binding} }}")
                result = self.process(VAQC, "check", "--syntax-only", source, good=False)
                self.assertIn(b"legacy reference binding", result.stderr)

        for body, diagnostic in (
            ("ref mut loan = *new i32(4)!; loan", "borrow from a temporary cannot escape"),
            ("owner := new i32(4)!; ref loan = *owner; next := move(owner); loan",
             "access conflicts with active borrow"),
        ):
            source = self.source("bad-borrow.vqr", f"func f() !i32 {{ {body} }}")
            result = self.process(VAQC, "check", source, good=False)
            self.assertIn(diagnostic.encode(), result.stderr)

        self.hosted("""
            func main() !void {
                key, owner := (7, new i32(34)!);
                {
                    ref mut value = *owner;
                    value++;
                }
                ref value = *owner;
                io.println(key + value)!;
            }
        """, "42\n")

    def test_v020_safe_reference_types_use_ref(self):
        for spelling in ("&i32", "&const i32", "&mut i32", "?&i32", "&&i32"):
            with self.subTest(spelling=spelling):
                source = self.source("legacy-type.vqr", f"type Legacy = {spelling};")
                result = self.process(VAQC, "check", "--syntax-only", source, good=False)
                self.assertIn(b"legacy reference type; use 'ref T' or 'ref mut T'", result.stderr)

        source = self.source("ref-types.vqr", """
            func read(value: ref i32) i32 { value }
            func bump(value: ref mut i32) void { value++; }
            func main() i32 {
                number := 4;
                {
                    ref mut cursor = number;
                    bump(cursor);
                }
                var loan: ref i32 = ref number;
                read(loan) + (7 & 3)
            }
        """)
        self.assertEqual(self.process(VAQC, "run", source, good=False).returncode, 8)

    def test_v020_removed_access_and_borrow_syntax(self):
        for code, diagnostic in (
            ("class C { public: func next() i32 { 1 } }", b"use 'pub'"),
            ("class C { private: var n: i32; }", b"private by default"),
            ("#return_borrow: x\nfunc f(x: in i32) ref i32 { x }", b"'from name'"),
            ("type F = func(ref i32) borrow(1) ref i32;", b"'from name'"),
            ("func f(x: in &const i32) void {}", b"write the data type"),
            ("func f<T: Copyable & Movable>(x: T) void {}", b"use '+'"),
        ):
            with self.subTest(code=code):
                source = self.source("removed.vqr", code)
                self.assertIn(diagnostic, self.process(VAQC, "check", "--syntax-only", source, good=False).stderr)

    def test_v020_logical_imports_and_same_package_recursion(self):
        entry = self.source("src/root.vqr",
                            "package src; import (src/lib/helpers as h, src/lib/helpers as j) "
                            "func main() i32 { h.answer() + j.answer() }")
        dep = self.source("src/lib/helpers/helper-tools.vqr",
                          "package helpers; pub func answer() i32 { secret() } func secret() i32 { 3 }")
        self.process(VAQC, "build", self.root, "-o", "app")
        self.assertEqual(self.process("./app", good=False).returncode, 6)
        entry.write_text("package src; import (src/lib/helpers as h) func main() i32 { h.secret() }")
        self.assertIn(b"private", self.process(VAQC, "check", entry, good=False).stderr)
        entry.write_text('import ("lib/helpers/helper-tools.vqr" as h) func main() i32 { h.answer() }')
        for flags in (("check",), ("check", "--syntax-only"), ("fmt", "--check")):
            result = self.process(VAQC, *flags, entry, good=False)
            self.assertIn(b"use a logical package import", result.stderr)
        entry.write_text("package src; import (src/lib/helpers as h) func main() i32 { h.secret() }")
        dep.write_text("package helpers; import (src) pub func secret() i32 { 3 }")
        self.assertIn(b"cyclic", self.process(VAQC, "check", entry, good=False).stderr)
        entry.write_text("package src; func main() i32 { even(4) }")
        self.source("src/e.vqr", "package src; func even(n: i32) i32 { if n == 0 { 1 } else { odd(n-1) } }")
        self.source("src/o.vqr", "package src; func odd(n: i32) i32 { if n == 0 { 0 } else { even(n-1) } }")
        self.assertEqual(self.process(VAQC, "run", entry, good=False).returncode, 1)

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
            ("func main() void {} func main(x: i32) void {}", "file-private"),
            ("pub func main() void {}", "file-private"),
            ("func main(a: i32) void {}", "no parameters"),
            ("func main() bool { true }", "main must return"),
            ("func f() (i32,i32) { return 1, 2; }", "tuple"),
            ("func f(x: i32) void { x move= x; }", "expected"),
            ("func f() i32 { if (true) 7 }", "if branch must be a block"),
            ("func f() never {}", "never"),
            ("func f() void { var n = 0; var x: ref i32 = raw n; var x: ref i32 = raw n; }", "duplicate local"),
            ("func f() void { var n = 0; var x: ref i32 = raw n; x = 2; }", "writable"),
            ("func f() void { var n = 0; ref mut x = n; y := move(x); }", "cannot be moved"),
            ("func f(&x: *i32) void {} func f(p: *i32) void {}", "identifier"),
            ("func f(p: *const ?i32) void { switch *p { some(ref x) => { x++; }, null => {} } }", "writable"),
            ("func f(v: ?i32) void { switch v { some(ref x) => { a := move(v); }, null => {} } }", "active borrow"),
            ("enum E { A(i32), B(i32) } func f(v: E) i32 { switch v { E.A(x), E.B(ref x) => x } }", "alternative"),
        ):
            with self.subTest(source=code):
                f = self.source("test.vqr", code)
                self.assertIn(diagnostic.encode(), self.process(VAQC, "check", f, good=False).stderr)

    def test_borrowing_and_control_flow(self):
        self.hosted("""
            enum Event { Link(*i32), Num(i32) }
            func inc(x: ref mut i32) void { x++; }
            func pair(x: i32, p: *i32) (i32, *i32) { return (x, p); }
            class C { pub var n: i32; pub const func value() !i32 { n } }
            func abort() never { panic("stop") }
            func liveValue(okay: bool) i32 { if okay { 7 } else { abort() } }
            func main() !void {
                a := 1; b := 8; p := raw a;
                {
                    ref mut x = a;
                    f := (target: ref mut i32) void => { target++; };
                    p = raw b; f(x); inc(x);
                }
                { var slot: ref mut *i32 = raw p; slot = raw a; }
                n, address := pair(4, p);
                *address += n;
                event := Event.Link(raw a);
                switch event {
                    Event.Link(&v) => { v++; },
                    Event.Num(ref mut v) => { v++; },
                }
                for &v in [_]*i32{raw a, raw b} { v++; }
                { var readOnly: ref i32 = raw a; io.println(readOnly)!; }
                io.println(b)!;
                c := C{n: 9}; maybe := some(c);
                var nested: ?!i32 = maybe?.value();
                io.println(nested.unwrap()!)!;
                i := 0;
                while i < 3 { i++; }
                io.println(if i == 3 { liveValue(true) } else { 0 })!;
            }
        """, "9\n9\n9\n7\n")

    def test_optional_propagation(self):
        program = """
            import (opt, coll)
            error Lookup { Missing, Broken }
            class Item { pub var id: i32; }
            class Probe {
                #copy: disabled
                pub var id: i32;
                pub func init(value: i32) : id(value) {}
                func deinit() void { io.println(id) catch {}; }
            }
            func lookup(mode: i32) !?i32 {
                if mode < 0 { return Lookup.Broken; }
                if mode == 0 { return null; }
                mode
            }
            func increment(mode: i32) !?i32 { lookup(mode)!? + 1 }
            func nested(value: ??i32) ??i32 { some(value?) }
            func peel(value: ??i32) ?i32 { (value?)? }
            func outside(value: ?!i32) ?!i32 { some(value?) }
            func field(value: ?Item) ?i32 { (value?).id }
            func owned(value: ?^Probe) ?i32 {
                var outer = Probe(90);
                var inner = move(value)?;
                inner.id
            }
            func record(counter: *i32) ?i32 { (*counter)++; null }
            func once(counter: *i32) ?i32 { record(counter)? }
            func borrowed(value: ?ref i32) !?ref i32 { value? }
            comptime func atCompileTime(value: ?i32) ?i32 { value? + 1 }
            comptime const answer = atCompileTime(some(40));
            func main() !void {
                io.println(increment(4)! ?? 0)!;
                io.println(increment(0)! ?? 0)!;
                var failed = increment(-1) catch (reason) {
                    io.println(reason == Lookup.Broken)!;
                    null
                };
                io.println(failed ?? 0)!;
                var value: ??i32 = some(null);
                io.println(nested(value) != null)!;
                io.println(peel(value) == null)!;
                var outer: ?!i32 = null;
                io.println(outside(outer) == null)!;
                outer = some(ok(42));
                io.println(outside(outer).unwrap()!)!;
                var item: ?Item = some(Item{id: 8});
                io.println(field(item) ?? 0)!;
                io.println(item?.id ?? 0)!;
                var callback: func(?i32) ?i32 = (x: ?i32) => x? + 1;
                io.println(callback(some(7)) ?? 0)!;
                io.println(callback(null) ?? 0)!;
                var counter = 0;
                io.println(once(raw counter) ?? counter)!;
                io.println(owned(some(new Probe(7)!)) ?? 0)!;
                io.println(owned(null) ?? 0)!;
                var failure = [&counter]() ErrorId => { counter++; Lookup.Missing };
                var present: ?i32 = some(21);
                io.println(opt.okOrElse(present, failure)!)!;
                var missing: ?i32 = null;
                io.println(opt.okOrElse(missing, failure) catch (reason) {
                    if reason != Lookup.Missing { return reason; }
                    counter
                })!;
                var map = coll.i32Map<i32>();
                map.insert(5, 30)!;
                {
                    var r = opt.okOr(map.get(5), Lookup.Missing)!;
                    io.println(r)!;
                }
                switch borrowed(map.get(6))! { null => {}, some(_) => panic("unexpected reference") }
                var pointer: ?*i32 = some(raw counter);
                var unwrapped = (x: ?*i32) ?*i32 => x?;
                io.println(*unwrapped(pointer).unwrap())!;
                io.println(answer.unwrap())!;
            }
        """
        output = "5\n0\ntrue\n0\ntrue\ntrue\ntrue\n42\n8\n8\n8\n0\n1\n7\n90\n7\n90\n0\n21\n2\n30\n2\n41\n"
        for level in ("-O0", "-O2"):
            with self.subTest(level=level):
                self.hosted(program, output, level)
        for code, message in [
            ("func f() ?i32 { 2? }", "requires an Optional operand"),
            ("func f(v: ?i32) i32 { v? }", "requires an Optional return channel"),
            ("func f(v: ?i32) !i32 { v? }", "LookupError.NotFound"),
            ("type Maybe = ?i32; func f(v: ?i32) !!Maybe { v? }", "requires '?U' or '!?U'"),
            ("func f(v: ?^i32) ?^i32 { v? }", "move(value)?"),
            ("comptime const n = some(2)?;", "error[E220]"),
            ("func read(x: in i32) ?ref i32 { x } "
             "func leak() ?i32 { var view = read(5)?; view }", "temporary"),
            ("import (opt) func f(v: ?i32, wrong: func() i32) !i32 { opt.okOrElse(v, wrong) }", "expected 'ErrorId'"),
        ]:
            with self.subTest(code=code):
                source = self.source("invalid.vqr", code)
                self.assertIn(message, self.process(VAQC, "check", source, good=False).stderr.decode())

    def test_unwrap_trap_diagnostics(self):
        source = self.source("unwrap.vqr", "func main() i32 { var absent: ?i32 = null; absent.unwrap() }\n")
        for flags in [("-O2",), ("-g",)]:
            self.process(VAQC, *flags, source, "-o", "trap")
            result = self.process("./trap", good=False)
            self.assertLess(result.returncode, 0)
            if "-g" in flags:
                self.assertIn(b"unwrap.vqr:1: unwrap() called on null in main", result.stderr)
            else:
                self.assertEqual(result.stderr, b"")
        target = self.source("freestanding.vqr", """
            func unwrap(value: ?i32) i32 { value.unwrap() }
            func forward(value: ?i32) ?i32 { value? + 1 }
        """)
        self.process(VAQC, "build", "--emit=llvm-ir", "--target", "thumbv7m-none-eabi", "--cpu", "cortex-m3",
                     "--profile", "freestanding", "-g", target, "-o", "bare.ll")
        ir = (self.root / "bare.ll").read_text()
        self.assertIn("@llvm.trap", ir)
        for symbol in ("@fprintf", "@fflush", "@stderr"):
            self.assertNotIn(symbol, ir)

    def test_readonly_reference_joins_pin_all_sources(self):
        self.hosted(r"""
            func choose(a, b: i32, first: bool) !void {
                var left: ref i32 = raw a;
                var right: ref i32 = raw b;
                var selected: ref i32 = if first { left } else { right };
                var switched: ref i32 = switch first { true => right, false => selected };
                io.println(selected)!;
                io.println(switched)!;
            }
            func main() !void {
                choose(7, 42, true)!;
                choose(7, 42, false)!;
            }
        """, "7\n42\n42\n42\n")
        for target in ("a", "b"):
            source = self.source("borrow.vqr", f"""
                func invalid(flag: bool) i32 {{
                    var a = 1;
                    var b = 2;
                    var view: ref i32 = if flag {{
                        var left: ref i32 = raw a;
                        left
                    }} else {{
                        var right: ref i32 = raw b;
                        right
                    }};
                    {target} = 3;
                    view
                }}
            """)
            error = self.process(VAQC, "check", source, good=False)
            self.assertIn(b"access conflicts with active borrow", error.stderr)

    def test_scoped_borrow_fields(self):
        self.hosted(r"""
            class View<T> {
                #borrowed: scoped
                pub var first: ref T;
                pub var second: ref T;
                pub var tag: i32;
            }
            func pair(value: ref i32) View<i32> {
                View<i32>{first: value, second: value, tag: 1}
            }
            func first(value: View<i32>) ref i32 { value.first }
            func main() !void {
                var number = 11;
                var alternate = 30;
                {
                    var both = View<i32>{first: raw number, second: raw alternate, tag: 99};
                    io.println(*both.first + *both.second + both.tag)!;
                    var included: ?View<i32> = some(both);
                    switch included?.first { some(&item) => { io.println(item)!; }, null => {} }
                }
                number++;
                var borrow: ref i32 = raw number;
                var held = pair(borrow);
                io.println(*first(held))!;
            }
        """, "140\n11\n12\n")

        view = """
            import (coll)
            class Owner {
                #copy: disabled
                pub var number: i32;
                func deinit() void {}
            }
            class View {
                #borrowed: scoped
                pub var owner: ref Owner;
                pub var tag: i32;
            }
            func consume(value: Owner) i32 { value.number }
            func inspect(view: View, tag: i32) i32 { view.tag + tag }
        """
        cases = {
            "move": """func bad() i32 {
                var x = Owner{number: 1};
                var saved = View{owner: raw x, tag: 3};
                consume(move(x))
            }""",
            "temporary": """func bad() i32 {
                var x = Owner{number: 1};
                inspect(View{owner: raw x, tag: 3}, consume(move(x)))
            }""",
            "initializer": """func bad() void {
                var x = Owner{number: 1};
                var view = View{owner: raw x, tag: consume(move(x))};
            }""",
            "address": """func bad() void {
                var x = Owner{number: 1};
                var view = View{owner: raw x, tag: 3};
                var pointer = raw view;
            }""",
            "escape": """func bad(source: ref Owner) View {
                var x = Owner{number: 1};
                View{owner: raw x, tag: 3}
            }""",
            "container": "func bad() void { var values = coll.Vec<View>(); }",
            "pointer_type": "type Invalid = *View;",
            "rebind": """func bad() void {
                var owner = Owner{number: 1};
                var other = Owner{number: 2};
                var view = View{owner: raw owner, tag: 3};
                var next: ref Owner = raw other;
                view.owner = next;
            }""",
            "mutable": "class Invalid { #borrowed: scoped; pub var owner: ref mut Owner; }",
        }
        for name, body in cases.items():
            with self.subTest(name=name):
                source = self.source(f"{name}/main.vqr", view + body)
                result = self.process(VAQC, "check", source, good=False)
                diagnostic = b"assignment requires a writable location" if name == "rebind" else b"E230"
                self.assertIn(diagnostic, result.stderr, result.stderr.decode())

    def test_scoped_closure_captures_pin_referents(self):
        self.hosted(r"""
            class View {
                #borrowed: scoped
                pub var item: ref i32;
            }
            func forward<T>(value: T) T { value }
            func main() !void {
                var left = 10;
                var right = 20;
                {
                    var view = View{item: raw left};
                    var other: ref i32 = raw right;
                    var bias = 3;
                    var reader = [view, &other, bias]() i32 => *view.item + other + bias;
                    var get = forward(reader);
                    var copy = get;
                    io.println(get() + copy())!;
                }
                left++;
                right++;
                io.println(left + right)!;
            }
        """, "66\n32\n")
        cases = {
            "owner_change": """func bad() i32 {
                var number = 10;
                var view = View{item: raw number};
                var callback = [view]() i32 => *view.item;
                number++;
                callback()
            }""",
            "escaping_owner": """func bad() void {
                var callback = {
                    var number = 10;
                    var loan: ref i32 = raw number;
                    [&loan]() i32 => loan
                };
            }""",
            "captured_return": """func bad() void {
                var number = 10;
                var loan: ref i32 = raw number;
                var callback = [&loan](argument: ref i32) ref i32 => loan;
            }""",
            "mutable_capture": """func bad() void {
                var number = 10;
                var loan: ref mut i32 = raw number;
                var callback = [&loan]() i32 => loan;
            }""",
            "mixed_capture": """func bad() void {
                var number = 10;
                var regular = 7;
                var loan: ref i32 = raw number;
                var callback = [&loan, &regular]() i32 => loan + regular;
            }""",
        }
        view = "class View { #borrowed: scoped; pub var item: ref i32; }\n"
        for name, body in cases.items():
            with self.subTest(name=name):
                source = self.source(f"{name}/main.vqr", view + body)
                result = self.process(VAQC, "check", source, good=False)
                self.assertIn(b"E230", result.stderr, result.stderr.decode())

    def test_readonly_reference_tuples(self):
        self.hosted(r"""
            type Same = (ref i32, ref i32);
            func twice(value: ref i32) Same { (value, value) }
            func main() !void {
                var first = 17;
                var second = 25;
                {
                    var pair: (ref i32, ref i32, i32) = (raw first, raw second, 1);
                    io.println(*pair.0 + *pair.1 + pair.2)!;
                }
                first++;
                var input: ref i32 = raw first;
                a, b := twice(input);
                io.println(a + b)!;
            }
        """, "43\n36\n")
        cases = [
            """func bad() i32 {
                var left = 10;
                var right = 20;
                var pair: (ref i32, ref i32) = (raw left, raw right);
                right++;
                *pair.1
            }""",
            """func bad() void {
                var first = 1;
                var second = 2;
                var pair: (ref mut i32, ref mut i32) = (raw first, raw second);
            }""",
            """func bad() void {
                var outer = 1;
                var pair = {
                    var inner = 2;
                    var value: (ref i32, ref i32) = (raw outer, raw inner);
                    value
                };
            }""",
            """func bad() void {
                var first = 1;
                var pair: (ref i32, string) = (raw first, "owned");
            }""",
        ]
        for index, source in enumerate(cases):
            with self.subTest(index=index):
                path = self.source(f"tuple-{index}/main.vqr", source)
                result = self.process(VAQC, "check", path, good=False)
                self.assertIn(b"E230", result.stderr, result.stderr.decode())

    def test_readonly_reference_arrays_index_and_iterate(self):
        self.hosted(r"""
            func twice(number: ref i32) [2]ref i32 {
                [_]ref i32{number, number}
            }
            func main() !void {
                var left = 20;
                var right = 22;
                {
                    var items = [_]ref i32{raw left, raw right};
                    var total = 0;
                    for item in items { total += item; }
                    io.println(total)!;
                    io.println(*items[1])!;
                }
                left++;
                var input: ref i32 = raw left;
                io.println(*twice(input)[0] + *twice(input)[1])!;
            }
        """, "42\n22\n42\n")
        cases = {
            "mutation": """func bad() void {
                var number = 42;
                var values = [_]ref i32{raw number};
                number++;
            }""",
            "write_in_loop": """func bad() void {
                var number = 42;
                for value in [_]ref i32{raw number} { number += value; }
            }""",
            "move_during_loop": """func bad() void {
                var number = 42;
                var references = [_]ref i32{raw number};
                for _ in references { move(references); }
            }""",
            "move_during_index": """func take(items: [1]ref i32) usize { 0 }
                func bad() i32 {
                    var number = 42;
                    var references = [_]ref i32{raw number};
                    *references[take(move(references))]
                }""",
            "mutable_reference": """func bad() void {
                var number = 42;
                var values = [_]ref mut i32{raw number};
            }""",
            "raw_slice": """func bad() void {
                var number = 42;
                var values = [_]ref i32{raw number};
                var slice = values[:];
            }""",
            "index_side_effect": """class Owner { #copy: disabled; pub var number: i32; }
                func take(value: Owner) usize { 0 }
                func bad() void {
                    var owner = Owner{number: 42};
                    ([_]ref Owner{raw owner})[take(move(owner))];
                }""",
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                source = self.source(f"array-{name}/main.vqr", text)
                result = self.process(VAQC, "check", source, good=False)
                self.assertIn(b"E230", result.stderr, result.stderr.decode())

    def test_scoped_enums_and_empty_borrow_branches(self):
        self.hosted(r"""
            enum Hit {
                #borrowed: scoped
                Empty = 7,
                Match(ref i32, i32),
            }
            func find(value: ref i32, found: bool) Hit {
                var empty = Hit.Empty;
                if found { Hit.Match(value, 3) } else { empty }
            }
            func fallback(hit: ?Hit) Hit {
                var empty = Hit.Empty;
                hit ?? empty
            }
            func increment(hit: Hit) Hit {
                switch hit {
                    Hit.Empty => Hit.Empty,
                    Hit.Match(&number, count) => Hit.Match(number, count + 1),
                }
            }
            func print(hit: Hit) !void {
                switch hit {
                    Hit.Empty => { io.println("empty")!; },
                    Hit.Match(&number, count) => { io.println(number + count)!; },
                }
            }
            func main() !void {
                var number = 40;
                var view: ref i32 = raw number;
                print(increment(find(view, true)))!;
                print(increment(find(view, false)))!;
                print(fallback(some(Hit.Match(view, 3))))!;
            }
        """, "44\nempty\n43\n")
        cases = [
            """enum Hit { #borrowed: scoped; Empty, Match(ref i32), }
               func bad() i32 {
                   var number = 42;
                   var selected = Hit.Match(raw number);
                   number++;
                   switch selected { Hit.Empty => 0, Hit.Match(&result) => result, }
               }""",
            """enum Hit { #borrowed: scoped; Empty, Match(ref i32), }
               func bad(source: ref i32) Hit {
                   var number = 42;
                   Hit.Match(raw number)
               }""",
            """enum Hit { #borrowed: scoped; Match(ref mut i32), }""",
        ]
        for index, text in enumerate(cases):
            with self.subTest(index=index):
                source = self.source(f"enum-{index}/main.vqr", text)
                result = self.process(VAQC, "check", source, good=False)
                self.assertIn(b"E230", result.stderr, result.stderr.decode())

    def test_indirect_reference_return_contract(self):
        self.hosted(r"""
            type Pick = func(left: in i32, right: in i32) ref i32 from right;
            func pick(left, right: in i32) ref i32 from right { right }
            func forward(fn: Pick, left, right: in i32) ref i32 from right { fn(left, right) }
            func main() !void {
                var number = 42;
                var first = 7;
                var call: Pick = pick;
                var left: ref i32 = raw first;
                var right: ref i32 = raw number;
                var checked: ref i32 = forward(call, left, right);
                io.println(checked)!;
                var identity = [](value: ref i32) ref i32 => value;
                io.println(*identity(checked))!;
                var bias = 1;
                var closure = [bias](value: ref i32) ref i32 => {
                    if bias == 1 { return value; }
                    value
                };
                io.println(*closure(checked))!;
            }
        """, "42\n" * 3)
        mismatched = self.source("mismatch.vqr", r"""
            type Pick = func(left: in i32, right: in i32) ref i32 from left;
            func pick(left, right: in i32) ref i32 from right { right }
            func test() void { var invalid: Pick = pick; }
        """)
        self.process(VAQC, "check", mismatched, good=False)
        escapes = self.source("escapes.vqr", r"""
            func identity(value: ref i32) ref i32 { value }
            func invalid(other: ref i32) ref i32 {
                var call: func(ref i32) ref i32 = identity;
                var value = 42;
                var loan: ref i32 = raw value;
                call(loan)
            }
        """)
        error = self.process(VAQC, "check", escapes, good=False)
        self.assertIn(b"E230", error.stderr)

    def test_reference_contracts(self):
        source = REPOSITORY / "examples/language/references.vqr"
        self.process(VAQC, "build", "-g", "-O2", source, "-o", "references")
        self.assertEqual(self.process("./references").stdout.decode(), "11\n24\n*i32\ni32\n42\n10\n21\n11\n12\n31\n")
        self.hosted("""
            class Large {
                #copy: disabled
                pub var values: [4]i32;
                pub func init() : values([4]i32{0, 1, 2, 3}) {}
            }
            func read(value: in Large) i32 { value.values[3] }
            interface Reader {
                const func get() ref i32 from self;
                const func read(value: in Large) i32;
            }
            class Impl {
                #implements: Reader
                pub const func get() ref i32 from self { var result: ref i32 = id; result }
                pub var id: i32;
                pub const func read(value: in Large) i32 { value.values[2] }
            }
            func second(first, second: in i32) ref i32 from second { second }
            func main() !void {
                var owned = Large();
                io.println(read(owned) + read(Large()))!;
                var impl = Impl{id: 7};
                var reader: *const Reader = raw impl;
                io.println(reader.read(owned))!;
                var checked: ref Reader = raw impl;
                io.println(checked.read(owned))!;
                var value = checked.get();
                io.println(*second(value, value))!;
            }
        """, "6\n2\n2\n7\n")
        self.hosted("""
            import (opt)
            class Items {
                var sent: bool;
                pub func init() : sent(false) {}
                pub func next() ?^i32 {
                    if sent { return null; }
                    sent = true;
                    return some(new i32(41) catch panic("allocation failed"));
                }
            }
            func main() !void {
                for &value in Items() { value++; io.println(value)!; }
                key, owner := (7, new i32(34)!);
                {
                    ref mut value = *owner;
                    value++;
                    io.println(key + value)!;
                }
                var item: ?i32 = some(41);
                {
                    var slot: ref mut ?i32 = raw item;
                    switch opt.asMutRef(slot) { some(&n) => { n++; }, null => {} }
                }
                switch opt.asRef(item) { some(&n) => { io.println(n)!; }, null => {} }
            }
        """, "42\n42\n42\n")
        self.hosted("""
            error Failure { Stop }
            class Probe {
                #copy: disabled
                pub var id: i32;
                func deinit() void { io.println(id) catch {}; }
            }
            func use(item: in Probe, value: i32) !void { io.println(item.id + value)!; }
            func stop() !i32 { return Failure.Stop; }
            func attempt() !void { use(Probe{id: 2}, stop()!)!; }
            func main() !void {
                use(Probe{id: 1}, 100)!;
                attempt() catch (reason) {
                    if reason != Failure.Stop { return reason; }
                    io.println(3)!;
                };
            }
        """, "101\n1\n2\n3\n")
        for code, diagnostic in [
            ("func f(r: ref mut i32) void {} func g() void { var n = 0; f(n); }", "no accessible overload"),
            ("func f(r: ref i32) void {} func g() void { var n = 0; f(raw n); }", "no accessible overload"),
            ("func f(r: ref i32) void { var p: ref mut i32 = r; }", "read-only"),
            ("func f() void { var n = 0; var p: ref mut i32 = raw n; var q: ref mut i32 = raw n; }", "active borrow"),
            ("func f(x: ref i32) ref i32 { var n = 0; var r: ref i32 = raw n; return r; }",
             "declared source"),
            ("func get(x: in i32) ref i32 { x } func f() void { var r = get(7); }", "temporary"),
            ("func f() !void { var b = new i32(7)!; var r: ref i32 = b; var next = move(b); }",
             "active borrow"),
            ("func f() !void { var v = coll.Vec<i32>(); v.push(7)!; var r = v.get(0); v.push(8)!; }",
             "active borrow"),
            ("func f() !void { var b = new i32(7)!; var b2 = b; }", "cannot copy"),
            ("func f() void { var r: in i32; }", "only permitted in parameter"),
            ("func f() in i32 { return 0; }", "only permitted in parameter"),
            ("func f(r: in &i32) void {}", "write the data type"),
            ("func f(r: ref mut ref mut i32) void {}", "indirect storage"),
            ("func f(r: i32) void {} func f(r: in i32) void {}", "duplicate overload"),
            ("func f(a, b: ref mut i32) void {} func g() void { var n = 0; var r: ref mut i32 = raw n; f(r, r); }",
             "active borrow"),
            ("func f(obj: ?i32) void { switch obj { some(&x) => {}, null => {}, } }",
             "requires a pointer, reference or Box"),
            ("func f(r: in i32) void {} func g() void { var n = 0; var p = raw n; var pp = raw p; f(pp); }",
             "no accessible overload"),
            ("func f() !void { var v = coll.Vec<i32>(); v.push(7)!; var opt = v.getMut(0);"
             " switch opt { some(value) => { value++; }, null => {}, } }", "cannot copy"),
            ("func f() void { var n = 0; var b = 1; var p = raw n; var pp = raw p;"
             " *pp = raw b; var r: ref mut i32 = p; }", "source is unproven"),
            ("func set(p: *i32) void { *p = 18; } func f() void { var n = 0; var p = raw n;"
             " var r: ref i32 = p; set(p); }", "active borrow"),
            ("func f(use: bool) void { var n = 0; var b = 1; var p: ?*i32 = some(raw n);"
             " var address = p ?? raw b; var r: ref mut i32 = address; }", "source is unproven"),
            ("func f() !void { var box = new i32(1)!; var p = box.asPtr();"
             " box = new i32(2)!; var r: ref i32 = p; }", "source is unproven"),
            ("func f() void { var n = 0; var b = 1; var p = raw n;"
             " for i in 0..2 { var r: ref i32 = p; p = raw b; } }", "source is unproven"),
        ]:
            with self.subTest(code=code):
                bad = self.source("bad-reference.vqr", "import (coll)\n" + code)
                self.assertIn(diagnostic, self.process(VAQC, "check", bad, good=False).stderr.decode())

    def test_optional_core_and_iterator_contracts(self):
        self.hosted("""
            import (core, opt, iter, coll)
            error Broken { Stop }
            func bumped(x: *const i32) bool { *x > 3 }
            func sum(x, y: i32) i32 { x + y }
            func seven() i32 { 7 }
            func noReturn(message: str) never { panic(message) }
            func wouldTrap() i32 { unsafe { core.unreachableUnchecked() } }
            func main() !void {
                a := 18; b := 2;
                core.swap(raw a, raw b); core.swap(raw a, raw a);
                io.println(core.replace(raw b, core.identity(a)))!;
                core.drop(b);
                var o: ?i32 = null;
                io.println(opt.isNone(raw o))!;
                var value = opt.getOrInsert(raw o, 3);
                *value += 2;
                opt.getOrInsertWith(raw o, seven);
                io.println(*opt.asPtr(raw o).unwrap())!;
                io.println(opt.replace(raw o, 8).unwrap())!;
                io.println(opt.take(raw o).unwrap())!;
                io.println(opt.isNone(raw o))!;
                var newValue = opt.insert(raw o, 9);
                (*newValue)--;
                io.println(opt.contains(raw o, newValue))!;
                seed := 3;
                transform := [seed](n: i32) ?i32 => { if n > seed { some(n) } else { null } };
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
                for index, &item in enumerate(items.iterMut()) {
                    item += i32(index)!;
                }
                var cursor = items.iterRef();
                io.println(*cursor.nextBack().unwrap())!;
                io.println(cursor.remaining())!;
                io.println(*cursor.next().unwrap())!;
                var once = opt.intoIter<i32>(opt.take(raw o));
                io.println(once.next().unwrap())!;
                var exhausted = once.next(); io.println(opt.isNone(raw exhausted))!;
                var listed = iter.iter(items.asConstSlice());
                io.println(listed.sizeHint().lower)!;
                for index, &count in iter.enumerate<iter.SliceIterMut<i32>, *i32>(items.iterMut()) {
                    count += i32(index)!;
                }
                io.println(*items.last().unwrap())!;
                io.println(core.isNone(raw o))!;
            }
        """, "18\ntrue\n5\n5\n8\ntrue\ntrue\n8\n8\n12\n8\n2\n8\n23\n11\n12\n2\n8\n8\ntrue\n3\n14\ntrue\n")

    def test_result_contracts(self):
        self.hosted("""
            import (result, core, iter)
            error Problem { Missing }
            func plus(x: i32) i32 { x + 1 }
            func twice(x: i32) !i32 { x * 2 }
            func recover(e: ErrorId) !i32 { if e == Problem.Missing { 10 } else { 0 } }
            func recoverValue(e: ErrorId) i32 { if e == Problem.Missing { 11 } else { 0 } }
            func main() !void {
                var original: !i32 = 2;
                var failed: !i32 = Problem.Missing;
                io.println(result.isOk(raw original))!;
                io.println(result.isErr(raw failed))!;
                io.println(result.errorOf(raw failed).unwrap() == Problem.Missing)!;
                io.println(*result.okValue(raw original).unwrap())!;
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
                io.println(result.isErr(raw checked))!;
                io.println(seen)!;
                var missing: !?i32 = null;
                var absent = result.transpose(missing);
                io.println(core.isNone(raw absent))!;
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
            func shorter(a: *const i32, b: *const i32) bool { *a < *b }
            func main() !void {
                io.println(width)!; io.println(stride)!; io.println(typeLabel)!;
                io.println(meta.alignOf<u32>())!;
                io.println(meta.isTriviallyDrop<i32>())!;
                io.println(meta.isCopyable<coll.Vec<i32>>())!;
                io.println(meta.isMovable<coll.Vec<i32>>())!;
                io.println(meta.isZeroSized<[0]u8>())!;
                a := -0.0f64; b := 0.0f64;
                io.println(cmp.totalCmp(raw a, raw b) == cmp.Ordering.Less)!;
                c := bitCast<f64>(0x7ff8000000000001u64);
                var unordered = cmp.partialCmp(raw b, raw c);
                io.println(core.isNone(raw unordered))!;
                io.println(cmp.totalCmp(raw b, raw c) == cmp.Ordering.Less)!;
                io.println(cmp.minBy(3, 2, shorter))!;
                io.println(cmp.clamp(8, 2, 5))!;
                var input = [_]i32{1, 2, 3};
                io.println(meta.arrayLen(raw input))!;
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
                io.println(core.isNone(raw empty))!;
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
                io.println(core.isErr(raw failed))!;
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
                io.println(core.isNone(raw eof))!;
                var little = bits.BitReader(data[:], bits.BitOrder.LsbFirst);
                io.println(little.readBits(3)!.unwrap())!;
            }
        """, "11\n15\n64\n3\n7\n6\n5649426\n13330\n64\n7\n7\ntrue\n0\n128\ntrue\n16777215\n2\n0\n498\n4\n8\n22\n32\n16\ntrue\n2\n191\n5\n0\n5\n1\ntrue\n7\n")

    def test_slice_search_and_heap(self):
        self.hosted("""
            import (algo, core)
            func less(a, b: *const i32) bool { *a < *b }
            func equal(a, b: *const i32) bool { *a == *b }
            func positive(value: *const i32) bool { *value > 0 }
            func main() !void {
                var values = [_]i32{7, 3, 2, 3, 4};
                var key = 3;
                io.println(algo.allOf(values[:], positive))!;
                io.println(algo.anyOf(values[:], positive))!;
                io.println(algo.noneOf(values[:], positive))!;
                io.println(algo.countIf(values[:], positive))!;
                io.println(algo.count(values[:], raw key))!;
                io.println(algo.find(values[:], raw key).unwrap())!;
                io.println(algo.findLast(values[:], raw key).unwrap())!;
                io.println(algo.findIf(values[:], positive).unwrap())!;
                var missing = algo.findIfNot(values[:], positive);
                io.println(core.isNone(raw missing))!;
                algo.heapify(values[:], less);
                io.println(algo.isHeap(values[:], less))!;
                algo.popHeap(values[:], less);
                io.println(values[4])!;
                algo.pushHeap(values[:], less);
                io.println(values[0])!;
                algo.sortHeap(values[:], less);
                io.println(algo.isSorted(values[:], less))!;
                lo, hi := algo.equalRange(values[:], raw key, less);
                io.println(lo)!; io.println(hi)!;
                io.println(algo.lowerBound(values[:], raw key, less))!;
                io.println(algo.upperBound(values[:], raw key, less))!;
                io.println(algo.binarySearch(values[:], raw key, less).unwrap())!;
                io.println(algo.adjacentFind(values[:], equal).unwrap())!;
                algo.rotate(values[:], 2);
                io.println(values[4])!;
                algo.reverse(values[:]);
                io.println(values[0])!;
                offset := -1;
                descending := [offset](a, b: *const i32) bool => *a * offset < *b * offset;
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
                var empty = slot.swap(raw n);
                io.println(core.isNone(raw empty))!;
                io.println(*slot.load().unwrap())!;
                var removed = slot.compareExchange(raw n, null);
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
                io.println(core.isErr(raw rejected))!;
                io.println(reader.position())!;
                var payloadRead = reader.readLengthPrefixed(3)!;
                io.println(payloadRead[2])!;
                var end = reader.readU8();
                io.println(core.isErr(raw end))!;
                reader.seek(0)!;
                io.println(reader.readI8()!)!;
                var small = [_]u8{8};
                var failedWrite = binary.writeVarUint(300, small[:]);
                io.println(core.isErr(raw failedWrite))!;
                io.println(small[0])!;
                var invalid = [_]u8{128, 0};
                var bad = binary.readVarUint(invalid[:]);
                io.println(core.isErr(raw bad))!;
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
                pub var data: Buffer<T, N>;
                pub func init(data: Buffer<T, N>) : data(move(data)) {}
                pub const func len() usize { N }
                pub const func tail() [1]T { [1]T{data[N - 1]} }
                comptime if N == 3 {
                    pub const func marker() i32 { 3 }
                } else {
                    pub const func marker() i32 { 1 }
                    pub var different: MissingType = invalid;
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
                io.println(length(raw one))!;
                io.println(one.len())!;
                io.println(one.marker())!;
                io.println(one.tail()[0])!;
                io.println(Selection<true>.selected())!;
                io.println(Selection<false>.selected())!;
                var code = Code<4>.fromInt(5);
                var missing = Code<4>.fromInt(6);
                io.println(core.isSome(raw code))!;
                io.println(core.isNone(raw missing))!;
            }
        """, "3\n3\n3\n8\n10\n20\ntrue\ntrue\n")

    def test_raw_storage_and_fixed_vector(self):
        self.hosted("""
            import (coll, core, mem, meta)
            class Probe {
                var number: i32;
                pub func init(number: i32) : number(number) {}
                pub const func id() i32 { number }
                pub func deinit() void { io.println(number) catch {}; }
            }
            func even(n: *const i32) bool { *n % 2 == 0 }
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
                var alias = numbers.getMut(2).unwrap();
                *alias = 8;
                var cursor = coll.FixedVec<i32, 4>.intoIter(move(numbers));
                io.println(cursor.nextBack().unwrap())!;
                io.println(cursor.next().unwrap())!;
                io.println(cursor.next().unwrap())!;
                var end = cursor.next();
                io.println(core.isNone(raw end))!;
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
            func inspect<T: Sendable + Shareable>(value: T) bool { true }
            func main() !void {
                var byte = mem.Layout.fromSizeAlign(1, 1)!;
                var wordLayout = mem.layout<u32>();
                var layout = byte.extend(wordLayout)!;
                io.println(layout.size())!;
                io.println(layout.align())!;
                var empty = mem.Layout.array<u64>(0)!;
                io.println(empty.size())!;
                var wrong = mem.Layout.fromSizeAlign(6, 3);
                io.println(core.isErr(raw wrong))!;
                var overflow = mem.Layout.array<u64>(~usize(0));
                io.println(core.isErr(raw overflow))!;
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
                var lhs = mmio.MmioRegion.new(mmio.addressOf(raw word), 4);
                unsafe {
                    var reg = lhs.registerAt<u32>(0)!;
                    reg.setBits(4);
                    reg.clearBits(2);
                    io.println(reg.read())!;
                    var invalid = lhs.registerAt<u32>(1);
                    io.println(core.isErr(raw invalid))!;
                    var flag = mmio.Volatile<u32>.new(raw word);
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

    def test_nested_explicit_generic_results_drive_outer_inference(self):
        self.source("types/helpers.vqr", """
            package types
            pub func identity<T>(value: T) T { value }
            pub class Passthrough {
                pub const func call<T>(value: T) T { value }
            }
        """)
        self.hosted(r"""
            import (types as source)
            func wrap<T>(value: T) T { value }
            func check<T>(value: T) T {
                var instance = source.Passthrough{};
                wrap(instance.call<T>(value) - source.identity<T>(T(1)))
            }
            func main() !void {
                io.println(check<f32>(8.0) == 7.0f32)!;
                io.println(check<f64>(8.0) == 7.0f64)!;
            }
        """, "true\ntrue\n")

    def test_generic_interface_methods(self):
        self.hosted(r"""
            interface Transformer<C> {
                const func apply<T: Movable + Copyable>(value: T, fallback: C) T;
                const func block<comptime N: usize>(value: [N]u8) [N]u8;
                const func number() i32;
            }
            class Identity {
                #implements: Transformer<i32>
                pub const func apply<U: Copyable + Movable>(value: U, unused: i32) U { value }
                pub const func block<comptime Count: usize>(value: [Count]u8) [Count]u8 { value }
                pub const func number() i32 { 1 }
            }
            class Forward {
                #implements: Transformer<i32>
                pub embed inner: Identity;
                pub const func number() i32 { 2 }
            }
            func use(value: *const Transformer<i32>) !void {
                io.println(value.apply<i16>(13, 0))!;
                io.println(value.apply<f64>(2.5, 0) == 2.5)!;
                var bytes = value.block<3>([3]u8{1, 5, 9});
                io.println(bytes[1])!;
                io.println(value.number())!;
            }
            func main() !void {
                var direct = Identity{};
                var embedded = Forward{inner: Identity{}};
                use(raw direct)!;
                use(raw embedded)!;
            }
        """, "13\ntrue\n5\n1\n13\ntrue\n5\n2\n")
        invalid = self.source("bad-generic-interface.vqr", """
            interface Transform { const func apply<T: Movable>(x: T) T; }
            class Bad {
                #implements: Transform
                pub const func apply<U: Copyable + Movable>(x: U) U { x }
            }
        """)
        failure = self.process(VAQC, "check", invalid, good=False)
        self.assertIn(b"one public implementation", failure.stderr)

    def test_generic_interface_preserves_nested_function_move_modes(self):
        source = """
            interface Runner {
                const func run<T: Copyable>(callback: func(move ^i32, T) i32, value: T) i32;
            }
            class Implementation {
                #implements: Runner
                pub const func run<U: Copyable>(callback: func(MODE ^i32, U) i32, value: U) i32 { 0 }
            }
            func main() void { var implementation = Implementation{}; }
        """
        self.process(VAQC, "check", self.source("matching.vqr", source.replace("MODE", "move")))
        failure = self.process(
            VAQC, "check", self.source("mismatched.vqr", source.replace("MODE", "")), good=False,
        )
        self.assertIn(b"one public implementation", failure.stderr)

    def test_interfaces_and_embedded_receivers(self):
        self.hosted("""
            import (meta)
            interface Readable<T> { const func read() T; }
            interface Writable { func write(value: i32) void; }
            interface Comparable { const func same(other: *const Self) bool; }
            class Cell {
                #implements: Readable<i32>, Writable, Comparable
                var number: i32;
                pub func init(number: i32) : number(number) {}
                pub const func read() i32 { number }
                pub func write(value: i32) void { number = value; }
                pub const func same(other: *const Self) bool { number == other.number }
            }
            class Wrapper {
                #implements: Readable<i32>, Writable
                pub embed cell: Cell;
                pub func init(n: i32) : cell(Cell(n)) {}
                pub const func read() i32 { cell.read() + 1 }
                pub func write(flag: bool) void { cell.write(if flag { 1 } else { 0 }); }
            }
            class Outer {
                #implements: Readable<i32>
                pub embed Wrapper;
                pub func init(n: i32) : Wrapper(Wrapper(n)) {}
            }
            class Private {
                embed cell: Cell;
                pub func init(n: i32) : cell(Cell(n)) {}
                pub const func inside() i32 { read() }
            }
            func statically<T: Readable<i32> + Copyable>(target: *const T) i32 { target.read() }
            func dynamically(target: *const Readable<i32>) i32 {
                var restored = target;
                var call = [target]() i32 => target.read();
                if target == restored { call() } else { 0 }
            }
            func mutate(target: *Writable) void { target.write(88); }
            func main() !void {
                var wrapper = Wrapper(11);
                io.println(statically(raw wrapper))!;
                io.println(dynamically(raw wrapper))!;
                wrapper.write(44);
                io.println(wrapper.cell.read())!;
                wrapper.write(false);
                io.println(wrapper.read())!;
                mutate(raw wrapper);
                io.println(wrapper.read())!;
                var outer = Outer(31);
                io.println(outer.read())!;
                io.println(dynamically(raw outer))!;
                var secret = Private(9);
                io.println(secret.inside())!;
                var a = Cell(5);
                var b = Cell(5);
                io.println(a.same(raw b))!;
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
                pub var releases: i32;
                pub var refuse: bool;
                pub func init() : defaultAllocator(mem.GlobalAllocator()), releases(0), refuse(false) {}
                pub func alloc(layout: mem.Layout) !?*u8 {
                    if refuse { return Trouble.Refused; }
                    defaultAllocator.alloc(layout)
                }
                pub func free(ptr: *u8, layout: mem.Layout) void {
                    releases++;
                    defaultAllocator.free(ptr, layout);
                }
            }
            class Guarded {
                var number: i32;
                pub func init(value: i32) !void : number(value) {
                    if value < 0 { return Trouble.Constructor; }
                }
                pub const func get() i32 { number }
                pub func deinit() void { io.println(number) catch {}; }
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
                var failed = new(raw heap) Guarded(-1);
                io.println(core.isErr(raw failed))!;
                io.println(heap.releases)!;
                io.println(check(raw heap)!)!;
                io.println(heap.releases)!;
                {
                    var boxed = new(heap) Guarded(41)!;
                    var taken = boxed.intoInner();
                    io.println(heap.releases)!;
                    io.println(taken.get())!;
                }
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
                    "19\n0\n19\n1\ntrue\n2\n33\n3\n4\n41\n41\n4\ntrue\n")
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
                io.println(core.isErr(raw rejected))!;
                io.println(input[1])!;
                simd.store(input[:], gathered)!;
                io.println(input[0])!;
                io.println(simd.horizontalSum(input[:]))!;
                var bytes = [_]u8{0, 0, 0, 0, 0};
                unsafe {
                    var address = ptrCast<*u16>(raw bytes[1]);
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
                pub func init() : remaining(2) {}
                pub func read(dst: []u8) !usize {
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
                pub func init() : remaining(3) {}
                pub func write(src: []const u8) !usize {
                    if src.len() == 0 { return 0; }
                    if remaining == 0 { return io.IoError.Interrupted; }
                    remaining--;
                    1
                }
                pub func flush() !void {}
            }
            func main() !void {
                var source = [_]u8{2, 4, 6, 9};
                var scratch = [_]u8{0, 0, 0, 0};
                var reader = io.MemoryReader(source[:]);
                io.println(reader.seek(-2, io.SeekOrigin.End)!)!;
                var progress = io.readExactProgress(raw reader, scratch[:])!;
                io.println(progress.processed)!;
                io.println(progress.status == io.ReadStatus.Eof)!;
                io.println(scratch[1])!;
                reader.seek(0, io.SeekOrigin.Start)!;
                var limited = io.limit(raw reader, 3);
                var counted = io.CountingReader(raw limited);
                io.println(io.readAtLeast(raw counted, scratch[:], 2)!)!;
                io.println(counted.count())!;
                io.println(limited.remaining())!;
                io.println(io.read(raw limited, scratch[:])!)!;
                var fixed = io.FixedWriter(scratch[0..2]);
                io.println(io.write(raw fixed, source[:])!)!;
                var full = io.write(raw fixed, source[:]);
                io.println(core.isErr(raw full))!;
                io.println(fixed.written()[1])!;
                var short = ShortReader();
                var partial = io.readExactProgress(raw short, scratch[:])!;
                io.println(partial.processed)!;
                io.println(partial.status == io.ReadStatus.WouldBlock)!;
                var narrow = ShortWriter();
                var interrupted = io.writeAllProgress(raw narrow, source[:])!;
                io.println(interrupted.processed)!;
                io.println(interrupted.status == io.ReadStatus.Interrupted)!;
                var first = io.MemoryReader(source[0..2]);
                var second = io.MemoryReader(source[2..4]);
                var readers = [2]*io.Reader{raw first, raw second};
                var multi = io.MultiReader(readers[:]);
                var output = io.MemoryWriter();
                var writer = io.CountingWriter(raw output);
                io.println(io.copyAll(raw multi, raw writer)!)!;
                io.println(writer.count())!;
                io.writeAll(raw output, output.asSlice())!;
                var list = io.MemoryWriter.intoBytes(move(output));
                io.println(list.len())!;
                io.println(list[7])!;
                var tiny = io.MemoryReader(source[:]);
                var oversize = io.readToEnd(raw tiny, 3);
                io.println(core.isErr(raw oversize))!;
                var chars = "中文".asBytes();
                var text = io.MemoryReader(chars);
                var decoded = io.readToString(raw text, 6)!;
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
                io.println(core.isErr(raw rejected))!;
                var max = 255u32;
                io.println(convert.narrow<u8>(max)!)!;
                io.println(convert.widen<u32>(17u8))!;
                var missing = convert.checkedCast<u8>(max + 1)!;
                io.println(core.isNone(raw missing))!;
                io.println(convert.toFloat<f64>(max) == 255.0)!;
                io.println(convert.fromChar('中'))!;
                io.println(convert.toChar(20013)! == '中')!;
                var surrogate = convert.toChar(0xd800);
                io.println(core.isErr(raw surrogate))!;
                io.println(convert.toBool(1)!)!;
                var invalidBool = convert.toBool(2);
                io.println(core.isErr(raw invalidBool))!;
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
                io.println(core.isErr(raw odd))!;
                var malformed = encoding.hexDecode("0g");
                io.println(core.isErr(raw malformed))!;
                io.println(encoding.percentEncode("a+/ 😀".asBytes(), "/".asBytes())!)!;
                var unescaped = encoding.percentDecode("%E4%B8%AD+%2F")!;
                io.println(text.fromBytes(unescaped.asConstSlice())!)!;
                var high = [_]u8{0xe9};
                io.println(encoding.latin1Decode(high[:])!)!;
                var wrongAscii = encoding.asciiDecode(high[:]);
                io.println(core.isErr(raw wrongAscii))!;
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
                io.println(core.isErr(raw rejected))!;
                var overlong = [_]u8{0xc0, 0x80};
                var invalid = encoding.utf8Validate(overlong[:]);
                io.println(core.isErr(raw invalid))!;
                var opts = encoding.Base64Options{alphabet: encoding.Base64Alphabet.Standard,
                    padding: true, lineWidth: 0, ignoreWhitespace: false};
                io.println(encoding.base64EncodedLen(4)!)!;
                io.println(encoding.base64Encode("f".asBytes(), opts)!)!;
                io.println(encoding.base64Encode("fo".asBytes(), opts)!)!;
                io.println(encoding.base64Encode("foo".asBytes(), opts)!)!;
                var base = encoding.base64Decode("Zm9v", opts)!;
                io.println(text.fromBytes(base.asConstSlice())!)!;
                var noncanonical = encoding.base64Decode("Zh==", opts);
                io.println(core.isErr(raw noncanonical))!;
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
                io.println(core.isErr(raw unfinished))!;
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
            func view(ptr: *const Pair) *const i32 { raw ptr.number }
            func edit(ptr: *Pair) *i32 { raw ptr.number }
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
                    io.println(core.isErr(raw conflict))!;
                }
                {
                    var borrowed = storage.borrowMut()!;
                    var leaf = cell.BorrowMut<Pair>.map<i32>(move(borrowed), edit);
                    var x = leaf.get();
                    *x = 20;
                    var conflict = storage.tryBorrow();
                    io.println(core.isNone(raw conflict))!;
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
                io.println(core.isErr(raw failed))!;
                io.println(*once.getOrTryInit(callback)!)!;
                var lazy = makeLazy(move(callback));
                io.println(*lazy.force()!)!;
                io.println(*lazy.force()!)!;
                io.println(tried)!;
                var locked = cell.OnceLock<i32>.new();
                var callbackFail = () !i32 => Failure.Once;
                var lockFailure = locked.getOrTryInit(callbackFail);
                io.println(core.isErr(raw lockFailure))!;
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
                    while !thread.stopRequested() { thread.yieldNow(); }
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
            func choose(pair: *Pair) *i32 { raw pair.other }
            func main() !void {
                var lock = sync.Mutex<Pair>.new(Pair{value: 13, other: 2});
                {
                    var first = lock.lock()!;
                    var unavailable = lock.tryLock();
                    io.println(core.isNone(raw unavailable))!;
                    var entry = sync.MutexGuard<Pair>.map<i32>(move(first), choose);
                    var number = entry.get();
                    *number *= 4;
                    entry.unlock();
                }
                var observe = (pair: *Pair) !void => io.println(pair.other);
                sync.withLock(raw lock, observe)!;
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
                pub func init() : counter(sync.Mutex<Counter>.new(Counter{value: 0, waiting: false})),
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
                var final = sync.waitUntil(raw state.condition, move(guard), threshold)
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
        if sys.platform == "darwin":
            # The archive includes the native address-wait provider.
            library = "libsync.a"
            self.process(VAQC, source, "--emit=staticlib", "-O2", "-o", library)
        else:
            library = "sync.o"
            self.process(VAQC, source, "--emit=obj", "-O2", "-o", library)
        self.process("cc", client, library, "-pthread", "-o", "sync-client")
        self.process("./sync-client")

    def test_condition_wait_uses_absolute_deadline(self):
        self.hosted('''
            import (cmp, sync, time)
            func main() !void {
                var mutex = sync.Mutex<i32>.new(23);
                var condition = sync.CondVar.new();
                var delay = time.Duration.fromMillis(5);
                var start = time.Instant.now()!;
                var instant = start.checkedAdd(delay)!;
                var deadline = time.Deadline.at(instant);
                var guard = mutex.lock()!;
                for {
                    acquired, elapsed := condition.waitUntilDeadline<i32>(move(guard), deadline)!;
                    guard = move(acquired);
                    if elapsed { break; }
                }
                io.println(start.elapsed()!.compare(delay) != cmp.Ordering.Less)!;
                io.println(*guard.get())!;
            }
        ''', "true\n23\n")

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
                io.println(core.isNone<*Probe>(raw aliased))!;
                {
                    var duplicate = original;
                    var weakCopy = observer;
                    io.println(observer.ptrEq(raw weakCopy))!;
                    io.println(duplicate.ptrEq(raw original))!;
                    io.println(duplicate.strongCount())!;
                    io.println(duplicate.weakCount())!;
                    var upgraded = observer.upgrade();
                    io.println(original.strongCount())!;
                    var failure = mem.Shared<Probe>.tryUnwrap(move(duplicate));
                    io.println(core.isErr<Probe>(raw failure))!;
                }
                {
                    var final = mem.Shared<Probe>.tryUnwrap(move(original))!;
                    io.println(final.number)!;
                    var dead = observer.upgrade();
                    io.println(core.isNone<mem.Shared<Probe>>(raw dead))!;
                    io.println(observer.weakCount())!;
                }
                var common = mem.Shared<sync.Mutex<u32>>.new(sync.Mutex<u32>.new(5))!;
                var weak = common.downgrade();
                var job = [move(weak)]() !u32 => {
                    var clone = weak.upgrade();
                    var owned = move(clone).unwrap();
                    var guard = owned.asPtr().lock()!;
                    var value = guard.get();
                    *value += 9;
                    *value
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
                io.println(core.isErr<time.Duration>(raw overflow))!;
                var underflow = time.Duration.fromSeconds(-9223372036854775807i64 - 1).abs();
                io.println(core.isErr<time.Duration>(raw underflow))!;
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
                io.println(core.isErr<time.Date>(raw skippedLeap))!;
                var early = time.Date.new(-400, 2, 29)!;
                io.println(time.Date.fromOrdinal(early.toOrdinal())!.year())!;
                var extreme = time.Date.new(2147483647, 12, 31)!;
                var impossible = extreme.addDays(1);
                io.println(core.isErr<time.Date>(raw impossible))!;
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
                io.println(core.isErr<void>(raw cancelled))!;
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
                io.println(core.isNone<Probe>(raw empty))!;

                inner, outer := channel.Channel<?i32>.bounded(1)!;
                inner.send(null)!;
                inner.close();
                var nested = outer.recv()!;
                var payload = nested.unwrap();
                io.println(core.isSome<?i32>(raw nested))!;
                io.println(core.isNone<i32>(raw payload))!;
                var terminal = outer.recv()!;
                io.println(core.isNone<?i32>(raw terminal))!;

                fast, slow := channel.unbounded<i32>()!;
                for n in 0..4 { fast.send(n)!; }
                for n in 0..2 { if slow.recv()!.unwrap() != n { panic("initial order"); } }
                for n in 4..100 { fast.send(n)!; }
                core.drop<channel.Sender<i32>>(move(fast));
                for n in 2..100 { if slow.recv()!.unwrap() != n { panic("growing queue order"); } }
                var end = slow.recv()!;
                io.println(core.isNone<i32>(raw end))!;
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
                io.println(receiveReady(raw rx))!;
                io.println(worker.join()!)!;
                var finished = rx.recv()!;
                io.println(core.isNone<i32>(raw finished))!;

                out, incoming := channel.bounded<i32>(0)!;
                var consumer = [move(incoming)]() !i32 => {
                    var reader = move(incoming);
                    reader.recv()!.unwrap()
                };
                var partner = thread.Thread<i32>.spawn(move(consumer))!;
                var sent = false;
                while !sent {
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

    def test_select_buffered_timeout_and_moves(self):
        self.hosted(r"""
            import (channel, time)
            class Owned {
                #copy: disabled
                pub var value: i32;
                func deinit() void { io.println(value) catch {}; }
            }
            func main() !void {
                sendA, recvA := channel.bounded<i32>(1)!;
                sendB, recvB := channel.bounded<i32>(1)!;
                var count: i32 = 0;
                for round in 0..8 {
                    sendA.send(1)!;
                    sendB.send(2)!;
                    var selected = (select {
                        item := recvA.recv() => item.unwrap(),
                        item := recvB.recv() => item.unwrap(),
                    })!;
                    count += selected;
                    if selected == 1 { var unused = recvB.recv()!; }
                    else { var unused = recvA.recv()!; }
                }
                io.println(count == 12)!;
                var immediate = (select {
                    item := recvB.recv() => 1,
                    else => 71,
                })!;
                io.println(immediate)!;
                var expired = (select {
                    item := recvA.recv() => 1,
                    after(time.Duration.fromMillis(2)) => 72,
                })!;
                io.println(expired)!;
                sendA.close();
                io.println((select { item := recvA.recv() => item == null })!)!;

                tx, rx := channel.bounded<Owned>(1)!;
                tx.send(Owned{value: 101})!;
                {
                    var pending = Owned{value: 102};
                    var lost = (select {
                        tx.send(move(pending)) => 0,
                        else => pending.value,
                    })!;
                    io.println(lost == 102)!;
                }
                {
                    var first = rx.recv()!.unwrap();
                    io.println(first.value == 101)!;
                }
                {
                    var outgoing = Owned{value: 103};
                    (select { tx.send(move(outgoing)) => {} })!;
                    var received = rx.recv()!.unwrap();
                    io.println(received.value == 103)!;
                }
            }
        """, "true\n71\n72\ntrue\ntrue\n102\ntrue\n101\ntrue\n103\n")

    def test_select_rendezvous_and_cancellation(self):
        self.hosted(r"""
            import (channel, result, task, time)
            func main() !void {
                var group = task.TaskGroup.withWorkers(1)!;
                tx, rx := channel.bounded<i32>(0)!;
                sideTx, sideRx := channel.bounded<i32>(0)!;
                var sending = [move(tx), move(sideTx)]() !i32 => {
                    var message = 41;
                    (select {
                        tx.send(move(message)) => 7,
                        sideTx.send(move(message)) => 8,
                    })!
                };
                var listening = [move(rx)]() !i32 => {
                    var reader = move(rx);
                    (select { item := reader.recv() => item.unwrap() })!
                };
                var a = group.spawn<i32>(move(sending))!;
                var b = group.spawn<i32>(move(listening))!;
                io.println(a.join()! + b.join()!)!;

                ordinaryTx, selectedRx := channel.bounded<i32>(0)!;
                var received = [move(selectedRx)]() !i32 => {
                    var reader = move(selectedRx);
                    (select { item := reader.recv() => item.unwrap() })!
                };
                var observe = group.spawn<i32>(move(received))!;
                ordinaryTx.send(52)!;
                io.println(observe.join()!)!;

                selectedTx, ordinaryRx := channel.bounded<i32>(0)!;
                var output = [move(selectedTx)]() !void => {
                    (select { selectedTx.send(63) => {} })!;
                };
                var sendingJob = group.spawn<void>(move(output))!;
                io.println(ordinaryRx.recv()!.unwrap())!;
                sendingJob.join()!;

                waitingTx, waitingRx := channel.bounded<i32>(0)!;
                var both = [move(waitingTx), move(waitingRx)]() !i32 => {
                    var reader = move(waitingRx);
                    var value = 1;
                    (select {
                        waitingTx.send(move(value)) => 2,
                        got := reader.recv() => 3,
                    })!
                };
                var waiting = group.spawn<i32>(move(both))!;
                var checkpoint = () !void => {};
                var fence = group.spawn<void>(move(checkpoint))!;
                fence.join()!;
                waiting.cancel();
                var outcome = waiting.join();
                io.println(result.errorOf(raw outcome).unwrap() == channel.ChannelError.Cancelled)!;
            }
        """, "48\n52\n63\ntrue\n")

    def test_task_channel_single_worker(self):
        self.hosted(r"""
            import (channel, task, time)
            func exchange(capacity: usize, receiveFirst: bool) !void {
                var group = task.TaskGroup.withWorkers(1)!;
                tx, rx := channel.bounded<i32>(capacity)!;
                var put = [move(tx)]() !void => {
                    tx.send(41)!;
                    tx.send(42)!;
                    tx.close();
                };
                var get = [move(rx)]() !i32 => {
                    var reader = move(rx);
                    var first = reader.recv()!.unwrap();
                    var next = reader.recv()!.unwrap();
                    if reader.recv()! != null { panic("receiver did not close"); }
                    first + next
                };
                if receiveFirst {
                    var reader = group.spawn<i32>(move(get))!;
                    var writer = group.spawn<void>(move(put))!;
                    io.println(reader.join()!)!;
                    writer.join()!;
                } else {
                    var writer = group.spawn<void>(move(put))!;
                    var reader = group.spawn<i32>(move(get))!;
                    io.println(reader.join()!)!;
                    writer.join()!;
                }
            }
            func main() !void {
                exchange(1, true)!;
                exchange(1, false)!;
                exchange(0, true)!;
                exchange(0, false)!;

                var group = task.TaskGroup.withWorkers(1)!;
                tx, rx := channel.bounded<i32>(0)!;
                var waitReceiver = [move(rx)]() !bool => {
                    var reader = move(rx);
                    reader.recv() catch (reason) { return reason == channel.ChannelError.Cancelled; };
                    false
                };
                var receiver = group.spawn<bool>(move(waitReceiver))!;
                var marker = () bool => true;
                var parked = group.spawn<bool>(marker)!;
                if !parked.join()! { panic("worker did not make progress"); }
                receiver.cancel();
                io.println(receiver.join()!)!;

                delayed, sink := channel.bounded<i32>(0)!;
                var waitSender = [move(delayed)]() !i32 => {
                    switch delayed.sendPreserving(73) {
                        channel.SendResult.Cancelled(value) => value,
                        _ => -1,
                    }
                };
                var sender = group.spawn<i32>(move(waitSender))!;
                var checkpoint = group.spawn<bool>(marker)!;
                if !checkpoint.join()! { panic("worker did not make progress"); }
                sender.cancel();
                io.println(sender.join()!)!;
                switch sink.tryRecv() {
                    channel.TryRecv.Closed => {},
                    _ => panic("cancelled offer is still present"),
                }

                timeoutTx, timeoutRx := channel.bounded<i32>(0)!;
                var timeout = [move(timeoutRx)]() !bool => {
                    var reader = move(timeoutRx);
                    reader.recvTimeout(time.Duration.fromMillis(5)) catch (reason) {
                        return reason == channel.ChannelError.Timeout;
                    };
                    false
                };
                var timer = group.spawn<bool>(move(timeout))!;
                io.println(timer.join()!)!;

                timedTx, timedRx := channel.bounded<i32>(1)!;
                timedTx.send(7)!;
                var sendWithDeadline = [move(timedTx)]() !i32 => {
                    switch timedTx.sendTimeout(11, time.Duration.fromMillis(5)) {
                        channel.SendResult.Cancelled(value) => value,
                        _ => -1,
                    }
                };
                var expired = group.spawn<i32>(move(sendWithDeadline))!;
                io.println(expired.join()!)!;
                io.println(timedRx.recv()!.unwrap())!;
            }
        """, "83\n" * 4 + "true\n73\ntrue\n11\n7\n", "-O2")

    def test_spec_standalone_and_project_examples(self):
        spec = (REPOSITORY / "docs/language-spec-v0.1.5.md").read_text(encoding="utf-8")

        def blocks(section):
            return re.findall(r"```cpp\n(.*?)```", section, flags=re.S)

        section = spec.split("### 4.1", 1)[1].split("### 4.2", 1)[0]
        examples = [block for block in blocks(section) if "func main()" in block]
        self.assertEqual(len(examples), 2)
        for index, (example, expected) in enumerate(zip(examples, ("Hello", "single file"))):
            source = self.source(f"standalone{index}.vqr", example)
            self.process(VAQC, "build", source, "-o", "standalone")
            self.assertEqual(self.process("./standalone").stdout.decode().strip(), expected)

        # The v0.1.5 appendix illustrates legacy anonymous/file-path project syntax.
        # Exercise the migrated shipped package layout instead.
        project = REPOSITORY / "examples/projects/local_import"
        self.process(VAQC, "build", project, "-o", "example")
        self.assertEqual(self.process("./example").stdout, b"42\n")

    def test_spec_task_example(self):
        spec = (REPOSITORY / "docs/language-spec-v0.1.5.md").read_text(encoding="utf-8")
        section = spec.split("## 19.", 1)[1].split("## 20.", 1)[0]
        example = next(block.removeprefix("cpp\n") for block in section.split("```")
                       if "package main\nimport (io, task, channel)" in block)
        source = self.source("spec.vqr", example)
        self.process(VAQC, "build", source, "-o", "spec")
        self.assertEqual(self.process("./spec").stdout.decode(), "".join(f"{i*i}\n" for i in range(10)))

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
                    if core.isNone<task.TaskId>(raw id) { panic("missing task ID"); }
                    while other.asPtr().load(atomic.Order.Acquire) < 1 { task.yieldNow()!; }
                    44
                };
                var first = group.spawn<i32>(move(waitForOther))!;
                var source = [move(definite)]() !void => {
                    var moved = move(definite);
                    var flag = moved.asPtr();
                    // This fixture has a unique writer and the cell operations are atomic.
                    unsafe { constCast<*atomic.Atomic<u8>>(flag).store(1, atomic.Order.Release); }
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
                io.println(core.isNone<i32>(raw available))!;
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
