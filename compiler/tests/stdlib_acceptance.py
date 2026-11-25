"""Run the examples for the numeric, data, Unicode and utility packages."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
import errno
import json
import os
import socket
import ssl
import subprocess
import unittest

from acceptance import CompilerCase, REPOSITORY, VAQC


class StandardLibraryAcceptance(CompilerCase):
    def example(self, source, *flags):
        return self.run_program(REPOSITORY / source, *flags)

    def run_program(self, source, *flags, input=None):
        compiled = subprocess.run(
            [VAQC, "build", source, *flags, "-o", "example"],
            cwd=self.root,
            capture_output=True,
            timeout=120,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stderr.decode(errors="replace"))
        return self.process("./example", input=input).stdout.decode()

    def test_learning_examples(self):
        examples = REPOSITORY / "examples"
        for path, data, expected in (
            ("hello.vqr", None, "Hello, Vaqrin!\n"),
            ("basics/control_flow.vqr", None, "excellent\nretry\npass\n2\n"),
            ("basics/collections.vqr", None, "2\n1\n0\n"),
            ("language/generics.vqr", None, "42\ntrue\n"),
            ("language/interfaces.vqr", None, "21\n16\n10\n"),
            ("language/ownership.vqr", None, "42\nCounter released\n"),
            ("language/optional.vqr", None, "42\n0\n-1\n"),
            ("language/parameter_modes.vqr", None, "20\n11\n21\n11\n"),
            ("stdlib/stdin.vqr", b"1 -2 43\n", "42\n"),
            ("stdlib/files.vqr", None, "first line\nsecond line\n"),
            ("stdlib/data_formats.vqr", None, "Ada\n8080\n2\n"),
            ("stdlib/protobuf.vqr", None, "Vaqrin\n42\n"),
            ("stdlib/tasks.vqr", None, "42\n"),
            ("projects/local_import", None, "42\n"),
            ("contests/b3612.vqr", b"5\n1 -2 3 4 -5\n3\n1 3\n2 5\n4 4\n", "2\n0\n4\n"),
            ("contests/p1918.vqr", b"4\n5 7 9 12\n3\n9 3 5\n", "3\n0\n1\n"),
        ):
            with self.subTest(example=path):
                self.assertEqual(self.run_program(examples / path, input=data), expected)

    def test_native_clocks_and_absolute_deadline(self):
        self.hosted(r"""
            import (cmp, time)
            func main() !void {
                var utc = time.now()!;
                io.println(utc.unixSeconds() > 1700000000)!;
                var start = time.monotonicNow()!;
                var delay = time.Duration.fromMillis(5);
                var end = start.checkedAdd(delay)!;
                var deadline = time.Deadline.at(end);
                time.sleepUntil(end)!;
                io.println(deadline.expired()! && deadline.remaining()!.isZero())!;
                io.println(start.elapsed()!.compare(delay) != cmp.Ordering.Less)!;
                time.sleepUntil(start)!;
            }
        """, "true\n" * 3)

    def test_cbor_adapter_and_decode_limits(self):
        self.hosted(r"""
            import (bytes, cbor, result, serde)
            class Point {
                #derive: serde
                pub var x: i16;
                pub var y: f64;
            }

            func main() !void {
                var data = Point{x: -42, y: 1.25};
                var codec = serde.DerivedCodec<Point>();
                var encoded = cbor.marshal<Point>(raw data, raw codec)!;
                var wire = [16]u8{162, 97, 120, 56, 41, 97, 121, 251, 63, 244, 0, 0, 0, 0, 0, 0};
                io.println(bytes.equal(encoded.asConstSlice(), wire[:]))!;

                // Indefinite map with reversed keys, binary16 value, and a break.
                var reference = [11]u8{191, 97, 121, 249, 61, 0, 97, 120, 56, 41, 255};
                var decoded = cbor.unmarshal<Point>(reference[:], raw codec)!;
                io.println(decoded.x == -42 && decoded.y == 1.25)!;
                var truncated = cbor.unmarshal<Point>(reference[0..10], raw codec);
                io.println(result.errorOf(raw truncated).unwrap() == serde.SerdeError.UnexpectedEnd)!;

                var input = cbor.Decoder(serde.DecodeLimits.withLimits(8, 8, 5));
                var tooManyBytes = serde.decodeFromBytes<Point>(wire[:], raw input, raw codec);
                io.println(result.errorOf(raw tooManyBytes).unwrap() == serde.SerdeError.ByteLimit)!;
                var sequence = cbor.Encoder();
                sequence.beginSeq(null)!;
                sequence.writeBool(true)!;
                sequence.writeNull()!;
                sequence.endSeq()!;
                var items = sequence.takeBytes()!;
                var expected = [4]u8{159, 245, 246, 255};
                io.println(bytes.equal(items.asConstSlice(), expected[:]))!;
            }
        """, "true\n" * 5)

    def test_derived_serde_records_and_field_metadata(self):
        self.hosted(r"""
            import (json, result, serde as codec)
            class Entry {
                #derive: serde
                pub var id: i128;
                #serde_name: "display-name"
                pub var name: string;
            }
            class Record<T> {
                #derive: serde
                pub var entry: Entry;
                pub var data: T;
                pub var maybe: ?i8;
            }

            func main() !void {
                var adapter = codec.DerivedCodec<Record<u16>>();
                var value = Record<u16>{entry: Entry{id: -9223372036854775810i128, name: "hello"},
                                        data: 32, maybe: null};
                var serialized = json.marshal<Record<u16>>(raw value, raw adapter)!;
                io.println(serialized)!;
                var decoded = json.unmarshal<Record<u16>>(serialized, raw adapter)!;
                io.println(decoded.entry.name == "hello" && decoded.data == 32 &&
                           decoded.entry.id == -9223372036854775810i128 && decoded.maybe == null)!;
                io.println(Entry.serdeFieldCount() == 2 &&
                           Entry.serdeFieldName(1).unwrap() == "display-name")!;
                var withoutOptional = json.unmarshal<Record<u16>>(
                    "{\"data\":3,\"entry\":{\"id\":1,\"display-name\":\"name\"}}", raw adapter)!;
                io.println(withoutOptional.maybe == null && withoutOptional.data == 3)!;
                var missing = json.unmarshal<Record<u16>>("{}", raw adapter);
                io.println(result.errorOf(raw missing).unwrap() == codec.SerdeError.MissingField)!;
                var extra = json.unmarshal<Record<u16>>("{\"extra\":1}", raw adapter);
                io.println(result.errorOf(raw extra).unwrap() == codec.SerdeError.UnknownField)!;
            }
        """, '{"entry":{"id":-9223372036854775810,"display-name":"hello"},"data":32,"maybe":null}\n'
             + "true\n" * 5)

    def test_json_and_async_logging(self):
        self.hosted(r"""
            import (channel, log)
            class Counting {
                #implements: log.Handler
                var sender: channel.Sender<i32>;
                pub func init(value: channel.Sender<i32>) : sender(move(value)) {}
                pub const func enabled(level: log.Level) bool { true }
                pub func handle(record: *const log.Record) !void { sender.send(42) }
                pub func flush() !void {}
            }

            func main() !void {
                var out = io.stdout();
                var json = log.JsonHandler.new(raw out);
                var first = log.Logger.new(raw json)!;
                var attrs = [1]log.Attr{log.Attr.int("value", 7)!};
                first.log(log.Level.Info, "first", attrs[:])!;
                json.flush()!;
                tx, rx := channel.bounded<i32>(1)!;
                var handler = log.AsyncHandler.new(Counting(move(tx)), 4)!;
                var second = log.Logger.new(raw handler)!;
                second.log(log.Level.Info, "second", attrs[:])!;
                handler.flush()!;
                io.println(rx.recv()!.unwrap() == 42 && handler.dropped() == 0)!;
                handler.close()!;
            }
        """, '{"level":"INFO","message":"first","attrs":{"value":7}}\ntrue\n')

    @unittest.skipUnless(os.path.isfile("/usr/share/zoneinfo/America/New_York"), "requires host IANA time zones")
    def test_native_monotonic_clock_and_sleep(self):
        self.hosted("""
            import (cmp, time)
            func main() !void {
                var start = time.monotonicNow()!;
                var delay = time.Duration.fromMillis(1);
                time.sleep(delay)!;
                var elapsed = start.elapsed()!;
                io.println(elapsed.compare(delay) != cmp.Ordering.Less)!;
            }
        """, "true\n")

    def test_time_zone_calendar_and_posix_future_rules(self):
        self.hosted(r"""
            import (core, time)
            func main() !void {
                var zone = time.TimeZone.load("America/New_York")!;
                var summer = time.UtcDateTime.fromUnixSeconds(2540918400, 123)!;
                io.println(zone.offsetAt(summer).seconds())!;
                var local = time.LocalDateTime.new(time.Date.new(2024, 11, 3)!,
                                                  time.TimeOfDay.new(1, 30, 0, 123)!);
                var first = time.toUtc(local, raw zone, time.LocalTimePolicy.Earlier)!;
                var second = time.toUtc(local, raw zone, time.LocalTimePolicy.Later)!;
                io.println(second.unixSeconds() - first.unixSeconds())!;
                var gap = time.LocalDateTime.new(time.Date.new(2024, 3, 10)!,
                                                time.TimeOfDay.new(2, 30, 0, 123)!);
                var rejected = time.toUtc(gap, raw zone, time.LocalTimePolicy.RejectGap);
                var invalid = time.TimeZone.load("../zoneinfo/America/New_York");
                io.println(core.isErr(raw rejected) && core.isErr(raw invalid))!;
                var moved = time.ZonedDateTime.fromLocal(gap, move(zone), time.LocalTimePolicy.ShiftForward)!;
                var formatted = time.formatDateTime(raw moved, "%Y-%m-%d %H:%M:%S.%f %z %%")!;
                io.println(formatted)!;
            }
        """, "-14400\n3600\ntrue\n2024-03-10 03:30:00.000000123 -0400 %\n")

    def test_arena_pool_and_copy_on_write(self):
        self.hosted(r"""
            import (mem)
            func main() !void {
                var allocator = mem.GlobalAllocator();
                var arena = mem.Arena(unsafe { mem.AllocatorRef.fromRaw(raw allocator) });
                var tight = mem.Layout.fromSizeAlign(32, 32)!;
                var memory = arena.alloc(tight)!.unwrap();
                io.println(mem.isAligned(memory, 32) && arena.used() == 32)!;
                var mark = arena.mark();
                var huge = mem.Layout.fromSizeAlign(5000, 64)!;
                var other = arena.alloc(huge)!.unwrap();
                io.println(mem.isAligned(other, 64) && arena.used() >= 5032)!;
                unsafe { arena.rewind(mark); }
                io.println(arena.used() == 32 && arena.capacity() == 4096)!;
                unsafe { arena.reset(); }
                io.println(arena.used() == 0 && arena.capacity() == 0)!;

                var pool = mem.Pool<i64>(unsafe { mem.AllocatorRef.fromRaw(raw allocator) });
                var first = pool.acquire()!.unwrap();
                unsafe { mem.write<i64>(first, 7); }
                io.println(*first == 7 && pool.allocated() == 1 && pool.live() == 1)!;
                unsafe { pool.release(first); }
                var second = pool.acquire()!.unwrap();
                io.println(first == second && pool.allocated() == 1)!;
                unsafe { pool.release(second); }
                pool.clear();

                var original = 41;
                var value = unsafe { mem.Cow<i32>.borrow(raw original) };
                io.println(value.isBorrowed() && *value.asRef() == 41)!;
                var writable = value.toMut();
                *writable += 1;
                io.println(value.isOwned() && original == 41 && mem.Cow<i32>.intoOwned(move(value)) == 42)!;
            }
        """, "true\n" * 8)

    def test_placement_write_copies_or_transfers_exactly_once(self):
        types = """
            import (mem)
            class Owner {
                #copy: disabled
                var dropped: *i32;
                pub var id: i32;
                pub func init(dropped: *i32, id: i32) : dropped(dropped), id(id) {}
                func deinit() void { unsafe { (*dropped)++; } }
            }
        """
        self.hosted(types + """
            class Counted {
                #copy: enable
                var copies: *i32;
                var dropped: *i32;
                pub var id: i32;
                pub func init(copies: *i32, dropped: *i32, id: i32)
                    : copies(copies), dropped(dropped), id(id) {}
                pub const func copy() Self {
                    unsafe { (*copies)++; }
                    Counted(copies, dropped, id)
                }
                func deinit() void { unsafe { (*dropped)++; } }
            }
            func main() !void {
                var allocator = mem.GlobalAllocator();
                var backing = unsafe { mem.AllocatorRef.fromRaw(raw allocator) };
                var copies = 0;
                var dropped = 0;
                {
                    var pool = mem.Pool<Counted>(backing);
                    var slot = pool.acquire()!.unwrap();
                    var original = Counted(raw copies, raw dropped, 11);
                    unsafe { mem.write(slot, original); }
                    if copies != 1 || dropped != 0 || original.id != 11 ||
                       unsafe { (*slot).id } != 11 { panic("placement write did not preserve exactly one copy"); }
                    unsafe {
                        mem.dropInPlace(slot);
                        pool.release(slot);
                    }
                    if dropped != 1 || pool.live() != 0 { panic("slot copy leaked or dropped twice"); }
                }
                if dropped != 2 { panic("caller copy was not retained"); }
                {
                    var pool = mem.Pool<Owner>(backing);
                    var slot = pool.acquire()!.unwrap();
                    var owner = Owner(raw dropped, 21);
                    unsafe { mem.writeOwned(slot, move(owner)); }
                    var readBack = unsafe { mem.take(slot) };
                    if readBack.id != 21 || dropped != 2 { panic("placed owner moved or dropped incorrectly"); }

                    // take left the acquired slot empty, so it may be initialized again.
                    unsafe {
                        mem.writeOwned(slot, Owner(raw dropped, 31));
                        if (*slot).id != 31 { panic("temporary was not placed"); }
                        mem.dropInPlace(slot);
                        pool.release(slot);
                    }
                    if dropped != 3 || pool.live() != 0 { panic("owner temporary was not dropped exactly once"); }
                }
                if dropped != 4 { panic("moved or taken owner leaked or dropped twice"); }
            }
        """, "", "-O2")
        for name, call, diagnostic in (
            ("copy_owner", "unsafe { mem.write(slot, value); }", b"no accessible overload"),
            ("missing_move", "unsafe { mem.writeOwned(slot, value); }", b"move parameter requires move(object)"),
            ("move_copy", "unsafe { mem.writeOwned(scalar, move(number)); }",
             b"move parameter requires an owning, movable, non-Copyable type"),
            ("safe_copy", "mem.write(scalar, number);", b"requires an unsafe block"),
            ("safe_move", "mem.writeOwned(slot, move(value));", b"requires an unsafe block"),
            ("second_use", "unsafe { mem.writeOwned(slot, move(value)); } var second = value.id;",
             b"not initialized"),
        ):
            with self.subTest(name=name):
                source = self.source(name + "/main.vqr", types + """
                    func place(slot: *Owner, scalar: *i32) void {
                        var dropped = 0;
                        var number = 9;
                        var value = Owner(raw dropped, 41);
                """ + call + "\n}")
                self.assertIn(diagnostic, self.process(VAQC, "check", source, good=False).stderr)

    def test_os_random_and_siphash(self):
        self.hosted(r"""
            import (hash, mem, random, bytes, coll)
            func feed(value: *const u16, state: *hash.Hasher) void {
                var encoded = [2]u8{u8(*value & 255) catch { panic("byte conversion"); }, u8(*value >> 8) catch { panic("byte conversion"); }};
                state.write(encoded[:]);
            }
            func main() !void {
                var rng = random.OsRng();
                var one = mem.zeroedBytes<512>();
                var two = mem.zeroedBytes<512>();
                rng.fillSecure(one[:])!;
                random.fillSystem(two[:])!;
                io.println(!bytes.equal(one[:], two[:]))!;

                var seed = hash.HashSeed(0x0706050403020100, 0x0f0e0d0c0b0a0908);
                var sip = hash.SipHasher(seed);
                io.println(sip.finish64() == 0x726fdb47dd0e0e31u64)!;
                var input = [15]u8{0,1,2,3,4,5,6,7,8,9,10,11,12,13,14};
                sip.write(input[0..5]);
                sip.write(input[5..15]);
                io.println(sip.finish64() == 0xa129ca6149be45e5u64)!;
                var first = hash.processSeed()!;
                var second = hash.processSeed()!;
                io.println(first.low == second.low && first.high == second.high)!;
                var table = coll.HashMap<u16, i32>.withSecureHasher(feed, hash.eqU16)!;
                var old = table.insert(200, 13)!;
                var key: u16 = 200;
                io.println(*table.get(key).unwrap() == 13)!;
            }
        """, "true\n" * 5)

    def test_regex_named_captures(self):
        self.hosted(r"""
            import (regex, result)
            func main() !void {
                var pattern = regex.Regex.compile("(?<kind>[a-z]+)(?P<suffix>[0-9]+)?")!;
                var hit = pattern.captures("box42")!.unwrap();
                io.println(pattern.captureNamed(raw hit, "kind").unwrap().asStr() == "box")!;
                io.println(pattern.captureNamed(raw hit, "suffix").unwrap().asStr() == "42")!;
                io.println(pattern.captureIndex("unknown") == null)!;
                var missing = pattern.captures("box")!.unwrap();
                io.println(pattern.captureNamed(raw missing, "suffix") == null)!;
                var duplicate = regex.Regex.compile("(?<x>a)(?<x>b)");
                io.println(result.errorOf(raw duplicate).unwrap() == regex.RegexError.DuplicateCaptureName)!;
                var lookbehind = regex.Regex.compile("(?<=x)y");
                io.println(result.errorOf(raw lookbehind).unwrap() == regex.RegexError.UnsupportedConstruct)!;
            }
        """, "true\n" * 6)

    def test_toml_multiline_values_table_arrays_and_redefinitions(self):
        self.hosted(r"""
            import (toml, result, text)
            func childName(value: *const toml.Value) !string {
                switch *value {
                    toml.Value.Table(ref table) => {
                        switch *table.get("name").unwrap() {
                            toml.Value.String(ref name) => return text.fromBytes(name.asBytes()),
                            _ => return toml.TomlError.TypeMismatch,
                        }
                    },
                    _ => return toml.TomlError.TypeMismatch,
                }
            }
            func main() !void {
                var source = "list = [\n 1, # one\n 2,\n]\nbasic = \"\"\"\r\nalpha\\\r\n  beta\r\ngamma\"\"\"\nliteral = \u{27}\u{27}\u{27}\n# kept\ntext\u{27}\u{27}\u{27}\n[[product]]\nname = 'one'\n[product.origin]\nx = 1\n[[product]]\nname = 'two'\n[product.origin]\nx = 2\n";
                var table = toml.parse(source)!;
                switch *table.get("list").unwrap() {
                    toml.Value.Array(ref list) => io.println(list.len() == 2)!,
                    _ => panic("array missing"),
                }
                switch *table.get("basic").unwrap() {
                    toml.Value.String(ref value) => io.println(text.equal(value, "alphabeta\ngamma"))!,
                    _ => panic("basic string missing"),
                }
                switch *table.get("literal").unwrap() {
                    toml.Value.String(ref value) => io.println(text.equal(value, "# kept\ntext"))!,
                    _ => panic("literal string missing"),
                }
                switch *table.get("product").unwrap() {
                    toml.Value.Array(ref entries) => {
                        io.println(entries.len() == 2)!;
                        io.println(childName(entries.getPtr(0).unwrap())! == "one")!;
                        io.println(childName(entries.getPtr(1).unwrap())! == "two")!;
                    },
                    _ => panic("array of tables missing"),
                }
                var earlier = toml.parse("[a.b]\nx = 1\n[a]\ny = 2\n");
                io.println(result.errorOf(raw earlier) == null)!;
                var duplicate = toml.parse("a.b = 1\n[a]\nc = 2\n");
                io.println(result.errorOf(raw duplicate).unwrap() == toml.TomlError.DuplicateTable)!;
                var frozen = toml.parse("a = { b = 1 }\n[a.c]\nz = 3\n");
                io.println(result.errorOf(raw frozen).unwrap() == toml.TomlError.DuplicateTable)!;
                var conflict = toml.parse("a = []\n[[a]]\n");
                io.println(result.errorOf(raw conflict).unwrap() == toml.TomlError.TypeConflict)!;
            }
        """, "true\n" * 10)

    def test_public_generic_integer_widths(self):
        signed = ("i8", "i16", "i32", "i64", "i128", "isize")
        unsigned = ("u8", "u16", "u32", "u64", "u128", "usize")
        calls = "\n".join(f"exercise<{s}, {u}>()!;" for s, u in zip(signed, unsigned))
        self.hosted(r"""
            import (coll, json, parse, serde)
            class Pair<S, U> {
                #derive: serde
                pub var signed: S;
                pub var unsigned: U;
            }

            func exercise<S, U>() !void {
                var first = parse.parseInt<S>("-42")!;
                var second = parse.parseUint<U>("42")!;
                var pair = Pair<S, U>{signed: first, unsigned: second};
                var codec = serde.DerivedCodec<Pair<S, U>>();
                var wire = json.marshal<Pair<S, U>>(raw pair, raw codec)!;
                var decoded = json.unmarshal<Pair<S, U>>(wire, raw codec)!;
                var values = coll.Vec<U>();
                values.push(decoded.unsigned)!;
                io.println(decoded.signed == first && values.pop().unwrap() == second)!;
            }

            func main() !void { CALLS }
        """.replace("CALLS", calls), "true\n" * len(signed))

    def test_public_float_generic_boundaries(self):
        self.hosted(r"""
            import (math, result)
            func exercise<T>(huge: T, tiny: T) !void {
                var shared = math.Complex<T>.new(huge, huge);
                var unit = shared.div(shared)!;
                io.println(unit.real() == T(1) && unit.imag() == T(0))!;
                var small = math.Complex<T>.new(tiny, tiny);
                var ratio = small.div(small)!;
                io.println(ratio.real() == T(1) && ratio.imag() == T(0))!;
                var mixed = math.Complex<T>.new(huge, tiny);
                var normalized = mixed.div(math.Complex<T>.new(T(1), T(0)))!;
                io.println(normalized.real() == huge && normalized.imag() == tiny)!;
                var zero = math.Complex<T>.new(T(0), -T(0));
                var rejected = small.div(zero);
                io.println(result.errorOf(raw rejected).unwrap() == math.MathError.DivisionByZero)!;
                io.println(math.signbit(zero.sqrt().imag()))!;
                var root = shared.sqrt();
                io.println(math.isFinite(root.real()) && root.real() > math.sqrt(huge))!;
                var tinyRoot = small.sqrt();
                io.println(tinyRoot.real() > T(0) && tinyRoot.imag() > T(0))!;
                var nanRoot = math.Complex<T>.new(math.nan<T>(), T(0)).sqrt();
                io.println(math.isNan(nanRoot.real()) && math.isNan(nanRoot.imag()))!;
                var infiniteRoot = math.Complex<T>.new(-math.inf<T>(), -T(0)).sqrt();
                io.println(infiniteRoot.real() == T(0) && infiniteRoot.imag() == -math.inf<T>())!;
                io.println(math.beta<T>(T(1), T(1)) == T(1))!;
            }
            func main() !void {
                exercise<f32>(3.3e38, 1.0e-43)!;
                exercise<f64>(1.7e308, 1.0e-320)!;
                var b = math.beta<f64>(100.0, 100.0);
                io.println(b > 2.20876e-61 && b < 2.20877e-61)!;
                var a = math.beta<f64>(1.0, 1.0e308);
                io.println(a > 0.99999e-308 && a < 1.00001e-308)!;
                var root = math.Complex<f64>.new(1.0e308, 0.0).sqrt();
                io.println(root.real() > 0.99999e154 && root.real() < 1.00001e154 && root.imag() == 0.0)!;
            }
        """, "true\n" * 23)

    def test_float_rounding_copyable_value_forwarders(self):
        self.hosted("""
            import (math)
            type Single = f32;
            type Double = f64;
            func exercise<T: Copyable>(x: T) !void {
                io.println(math.floor(x) == T(-3))!;
                io.println(math.ceil<T>(x) == T(-2))!;
                io.println(math.trunc(x) == T(-2))!;
                io.println(math.round(x) == T(-3))!;
                io.println(math.roundEven(x) == T(-2))!;
                io.println(math.fract(x) == T(-0.5) && x == T(-2.5))!;
            }
            func main() !void {
                exercise<Single>(-2.5f32)!;
                exercise<Double>(-2.5f64)!;
            }
        """, "true\n" * 12)

        # Copyability is necessary but doesn't admit a non-floating operand.
        invalid = self.source("round_integer.vqr", """
            package main
            import (math)
            func main() void { math.floor<i32>(-7); }
        """)
        diagnostic = self.process(VAQC, "check", invalid, good=False).stderr
        self.assertIn(b"invalid explicit conversion", diagnostic)

    def test_complex_log_and_exp_preserve_representable_components(self):
        self.hosted(r"""
            import (math)
            func exercise<T>(largest: T, tiny: T, exponent: T, phase: T) !void {
                var log = math.Complex<T>.new(largest, largest).log();
                var expected = math.log<T>(largest) + math.log<T>(T(2)) / T(2);
                io.println(math.isFinite(log.real()) && math.abs<T>(log.real() - expected) < T(0.001))!;
                io.println(math.abs(log.imag() - math.pi<T>() / T(4)) < T(0.001))!;
                var small = math.Complex<T>.new(tiny, tiny).log();
                io.println(math.isFinite(small.real()) && small.real() > math.log<T>(tiny))!;
                var onAxis = math.Complex<T>.new(exponent, -T(0)).exp();
                io.println(math.isInfinite(onAxis.real()) && onAxis.imag() == T(0) && math.signbit(onAxis.imag()))!;
                var exponential = math.Complex<T>.new(exponent, phase).exp();
                io.println(math.isInfinite(exponential.real()) &&
                           math.isFinite(exponential.imag()) && exponential.imag() > T(0))!;
            }
            func main() !void {
                exercise<f32>(3.3e38, 1.0e-43, 100.0, 1.0e-40)!;
                exercise<f64>(1.7e308, 1.0e-320, 1000.0, 1.0e-320)!;
                var computed = math.Complex<f64>.new(1000.0, 1.0e-320).exp().imag();
                io.println(computed > 1.96e114 && computed < 1.98e114)!;
            }
        """, "true\n" * 11)

    def test_numerics(self):
        self.assertEqual(
            self.example("examples/stdlib/numerics/main.vqr"),
            (REPOSITORY / "examples/stdlib/numerics/expected.txt").read_text(encoding="utf-8"),
        )

    def test_numeric_operands_are_shared_borrows(self):
        self.hosted("""
            import (cmp, num, result)
            func main() !void {
                const value = num.BigUint.fromU64(12)!;
                io.println(value.add(value)!.toU64()!)!;
                quotient, remainder := value.divMod(value)!;
                io.println(quotient.toU64()!)!;
                io.println(remainder.toU64()!)!;
                var zero = num.BigUint.fromU64(0)!;
                var bad = value.div(zero);
                io.println(result.errorOf(raw bad).unwrap() == num.NumError.DivisionByZero)!;
                const negative = num.BigInt.fromI64(-6)!;
                var divisor = num.BigInt.fromI64(4)!;
                io.println(negative.mul(divisor)!.toI64()!)!;
                io.println(num.BigInt.gcd(negative, divisor)!.toI64()!)!;
                io.println(negative.compare(negative) == cmp.Ordering.Equal)!;
                const fraction = num.Rational<i64>.new(-2, 3)!;
                var sum = fraction.add(fraction)!;
                var identity = fraction.div(fraction)!;
                io.println(sum.numerator())!;
                io.println(sum.denominator())!;
                io.println(identity.numerator())!;
                io.println(identity.denominator())!;
                io.println(fraction.compare(fraction)! == cmp.Ordering.Equal)!;
            }
        """, "24\n1\n0\ntrue\n-24\n2\ntrue\n-4\n3\n1\n1\ntrue\n")

        source = self.source("numeric_raw_operand.vqr", """
            import (num)
            func f() !void {
                var value = num.BigUint.fromU64(2)!;
                var outcome = value.mul(raw value)!;
            }
        """)
        result = self.process(VAQC, "check", source, good=False)
        self.assertIn(b"no accessible overload", result.stderr)

    def test_bigint_twos_complement_byte_carry(self):
        program = self.source("bigint_twos_complement.vqr", """
            import (io, meta, num)
            func main() !void {
                little := [_]u8{0, 0, 255};
                var negative = num.BigInt.fromTwosComplementBytes(little[:], meta.Endian.Little)!;
                io.println(negative.toInteger<i64>()!)!;
                var roundtrip = negative.toTwosComplementBytes(meta.Endian.Little)!;
                var reversed = negative.toTwosComplementBytes(meta.Endian.Big)!;
                for byte in roundtrip.asConstSlice() { io.println(byte)!; }
                for byte in reversed.asConstSlice() { io.println(byte)!; }

                big := [_]u8{255, 0, 0};
                var decoded = num.BigInt.fromTwosComplementBytes(big[:], meta.Endian.Big)!;
                io.println(decoded.toInteger<i64>()!)!;
                var positive = num.BigInt.fromInteger<i64>(128)!;
                var positiveBytes = positive.toTwosComplementBytes(meta.Endian.Little)!;
                for byte in positiveBytes.asConstSlice() { io.println(byte)!; }
            }
        """)
        self.assertEqual(self.run_program(program), "-65536\n0\n0\n255\n255\n0\n0\n-65536\n128\n0\n")

    def test_integer_arithmetic_edges(self):
        program = self.source("integers.vqr", '''
            import (core, io, num)
            func main() !void {
                var overflow = num.checkedAdd<i64>(num.maxValue<i64>(), 1);
                var zero = num.checkedDiv<i64>(1, 0);
                var minimum = num.checkedDiv<i64>(num.minValue<i64>(), -1);
                io.println(core.isErr(raw overflow) && core.isErr(raw zero) && core.isErr(raw minimum))!;
                io.println(num.divEuclid<i64>(-7, -3)!)!;
                io.println(num.remEuclid<i64>(-7, -3)!)!;
                io.println(num.gcd<i64>(-18, 24)!)!;
                io.println(num.lcm<i64>(-18, 24)!)!;
            }
        ''')
        self.assertEqual(self.run_program(program), "true\n3\n2\n6\n72\n")

    def test_slice_partition(self):
        self.hosted('''
            import (algo)

            class Entry {
                #copy: disabled
                pub var id: i32;
            }

            func main() !void {
                var values = [5]Entry{
                    Entry{id: 1}, Entry{id: 2}, Entry{id: 3}, Entry{id: 4}, Entry{id: 5}
                };
                var even = (entry: *const Entry) bool => entry.id % 2 == 0;
                io.println(algo.isPartitioned(values[:], even))!;
                io.println(algo.stablePartition(values[:], even)!)!;
                io.println(algo.isPartitioned(values[:], even))!;
                io.println(algo.partitionPoint(values[:], even))!;
                for index in usize(0)..values.len() { io.println(values[index].id)!; }

                var odd = (entry: *const Entry) bool => entry.id % 2 != 0;
                io.println(algo.partition(values[:], odd))!;
                io.println(algo.isPartitioned(values[:], odd))!;
                io.println(algo.partitionPoint(values[0..0], odd))!;
                io.println(algo.partitionPoint(values[0..3], odd))!;
                io.println(algo.partitionPoint(values[3..5], odd))!;
                io.println(algo.stablePartition(values[0..0], odd)!)!;
                io.println(algo.partition(values[0..0], odd))!;
            }
        ''', "false\n2\ntrue\n2\n2\n4\n1\n3\n5\n3\ntrue\n0\n3\n0\n0\n0\n")

    def test_recursive_layout_rejects_invalid_types(self):
        direct = self.source("direct.vqr", "class Node { var child: Node; }\nfunc f(x: Node) void {}")
        result = self.process(VAQC, "check", direct, good=False)
        self.assertIn(b"recursive by-value layout", result.stderr)
        immovable = self.source("immovable.vqr", '''
            import (coll)
            class Node {
                #move: disabled
                var children: coll.Vec<Node>;
            }
            func f(x: Node) void {}
        ''')
        result = self.process(VAQC, "check", immovable, good=False)
        self.assertIn(b"does not satisfy Movable", result.stderr)

    def test_random_ranges_and_float_domain(self):
        program = self.source("random_ranges.vqr", '''
            import (core, io, math, random)
            func main() !void {
                var rng = random.Pcg64.seed(42u64);
                var bins = [7]usize{0, 0, 0, 0, 0, 0, 0};
                for sample in usize(0)..7000 {
                    var offset = random.rangeInt(raw rng, -3i64, 4i64)! + 3i64;
                    bins[usize(offset)!]++;
                }
                for count in bins { if count < 800 || count > 1200 { panic("uniform smoke failed"); } }
                var bad = random.rangeInt(raw rng, 2u64, 2u64);
                io.println(core.isErr(raw bad))!;
                io.println(math.isNan<f64>(math.sqrt<f64>(-1.0)))!;
            }
        ''')
        self.assertEqual(self.run_program(program), "true\ntrue\n")

    def test_uniform_float_samples_both_precisions(self):
        self.hosted("""
            import (convert, math, random, result)
            class FixedRng {
                #implements: random.Rng
                pub var word: u64;
                pub var calls: usize;
                pub func init(value: u64) : word(value), calls(0) {}
                pub func nextU64() u64 { calls++; word }
                pub func fill(dst: []u8) void { panic("unexpected byte sampling"); }
            }
            func main() !void {
                var half = FixedRng(0x8000000000000000u64);
                var n32 = random.UniformFloat<f32>.new(-2.0f32, 6.0f32)!;
                var n64 = random.UniformFloat<f64>.new(-2.0, 6.0)!;
                io.println(n32.sample(raw half)! == 2.0f32 && n64.sample(raw half)! == 2.0)!;
                io.println(random.rangeFloat(raw half, 10.0f32, 14.0f32)! == 12.0f32 &&
                           random.rangeFloat(raw half, 10.0f64, 14.0f64)! == 12.0)!;
                io.println(half.calls == 4)!;

                var highest = FixedRng(0xffffffffffffffffu64);
                var unit32 = random.UniformFloat<f32>.new(0.0f32, 1.0f32)!;
                var unit64 = random.UniformFloat<f64>.new(0.0, 1.0)!;
                io.println(unit32.sample(raw highest)! == convert.bitCast<f32>(0x3f7fffffu32))!;
                io.println(unit64.sample(raw highest)! == convert.bitCast<f64>(0x3fefffffffffffffu64))!;
                var adjacent32 = random.UniformFloat<f32>.new(1.0f32, convert.bitCast<f32>(0x3f800001u32))!;
                var adjacent64 = random.UniformFloat<f64>.new(1.0, convert.bitCast<f64>(0x3ff0000000000001u64))!;
                io.println(adjacent32.sample(raw highest)! == 1.0f32 && adjacent64.sample(raw highest)! == 1.0)!;

                var lowest = FixedRng(0);
                io.println(n32.sample(raw lowest)! == -2.0f32 && n64.sample(raw lowest)! == -2.0)!;
                var point32 = random.UniformFloat<f32>.new(3.5f32, 3.5f32)!;
                var point64 = random.UniformFloat<f64>.new(3.5, 3.5)!;
                io.println(point32.sample(raw lowest)! == 3.5f32 && point64.sample(raw lowest)! == 3.5)!;
                var reversed32 = random.UniformFloat<f32>.new(1.0f32, 0.0f32);
                var reversed64 = random.UniformFloat<f64>.new(1.0, 0.0);
                var infinite32 = random.UniformFloat<f32>.new(0.0f32, math.inf<f32>());
                var infinite64 = random.UniformFloat<f64>.new(0.0, math.inf<f64>());
                var nan32 = random.rangeFloat(raw lowest, math.nan<f32>(), 1.0f32);
                var nan64 = random.rangeFloat(raw lowest, math.nan<f64>(), 1.0f64);
                io.println(result.errorOf(raw reversed32).unwrap() == random.RandomError.EmptyRange &&
                           result.errorOf(raw reversed64).unwrap() == random.RandomError.EmptyRange)!;
                io.println(result.errorOf(raw infinite32).unwrap() == random.RandomError.EmptyRange &&
                           result.errorOf(raw infinite64).unwrap() == random.RandomError.EmptyRange)!;
                io.println(result.errorOf(raw nan32).unwrap() == random.RandomError.EmptyRange &&
                           result.errorOf(raw nan64).unwrap() == random.RandomError.EmptyRange && lowest.calls == 2)!;
            }
        """, "true\n" * 11)

    def test_rng_and_siphash_preserve_fixed_width_bit_patterns(self):
        seed = ", ".join(map(str, range(32)))
        data = "x" * 300
        self.hosted('''
            import (hash, random)
            func main() !void {
                var pcg = random.Pcg64.seed(42u64);
                var xoshiro = random.Xoshiro256.fromSeed([32]u8{SEED})!;
                for i in 0..3 {
                    io.println(pcg.nextU64())!;
                    io.println(xoshiro.nextU64())!;
                }
                var hasher = hash.SipHasher(hash.HashSeed(1, 2));
                hasher.write("DATA".asBytes());
                io.println(hasher.finish64())!;
            }
        '''.replace("SEED", seed).replace("DATA", data),
                    "6261763436413913661\n13557399450712487245\n5707447046872229490\n"
                    "2706373525000986293\n7522330712029359324\n88962455226424365\n"
                    "12910671513412005119\n")

    def test_float_parsing_and_scanning(self):
        self.hosted(r"""
            import (bufio, convert, parse)
            func main() !void {
                var prefix = parse.parseFloatPrefix<f64>("+1.25e2制")!;
                io.println(prefix.value == 125.0 && prefix.consumed == 7)!;
                var incomplete = parse.parseFloatPrefix<f64>("1e+")!;
                io.println(incomplete.value == 1.0 && incomplete.consumed == 1)!;
                var directlyRounded = parse.parseFloat<f32>("1.0000000596046448")!;
                io.println(convert.bitCast<u32>(directlyRounded) == 1065353217)!;
                var negativeZero = parse.parseFloat<f64>("-0")!;
                io.println(convert.bitCast<u64>(negativeZero) == 0x8000000000000000u64)!;
                var subnormal = parse.parseFloat<f64>("5e-324")!;
                io.println(convert.bitCast<u64>(subnormal) == 1)!;
                var invalidInputs = [4]str{"nan", " .5", ".", "+-1"};
                for bad in invalidInputs {
                    var invalid = parse.parseFloat<f64>(bad) catch (reason) {
                        io.println(reason == parse.ParseError.InvalidSyntax)!;
                        continue;
                    };
                    io.println(false)!;
                }
                parse.parseFloat<f64>("1.5px") catch (reason) {
                    io.println(reason == parse.ParseError.TrailingInput)!;
                    0.0
                };
                parse.parseFloat<f32>("1e39") catch (reason) {
                    io.println(reason == parse.ParseError.OutOfRange)!;
                    0.0f32
                };
                var input = io.MemoryReader(" .5 -2.5e1 ".asBytes());
                var scanner = bufio.newScanner(raw input)!;
                io.println(scanner.readFloat<f32>()! == 0.5f32)!;
                io.println(scanner.readFloat<f64>()! == -25.0)!;
                io.println(scanner.nextFloat<f64>()! == null)!;
            }
        """, "true\n" * 14)

    def test_json_and_toml_float_round_trip(self):
        self.hosted(r"""
            import (convert, json, math, toml)
            func main() !void {
                var decimal = json.JsonNumber.parse("1.25e2")!;
                io.println(decimal.asF64()! == 125.0)!;
                var integer = json.JsonNumber.parse("9007199254740993")!;
                integer.asF64() catch (reason) {
                    io.println(reason == json.JsonError.InexactNumber)!;
                    0.0
                };
                var jsonWriter = json.JsonDomSerializer();
                jsonWriter.writeF64(-0.0)!;
                var jsonValue = jsonWriter.takeValue()!;
                var wire = json.stringify(jsonValue)!;
                var jsonReader = json.JsonDomDeserializer(json.parse(wire)!);
                var signedZero = jsonReader.readF64()!;
                io.println(convert.bitCast<u64>(signedZero) == 0x8000000000000000u64)!;
                jsonWriter.writeF64(math.inf<f64>()) catch (reason) {
                    io.println(reason == json.JsonSerdeError.UnsupportedNumber)!;
                };

                var table = toml.parse("amount = 1_000.25\n")!;
                io.println(toml.asF64(table.get("amount").unwrap())! == 1000.25)!;
                var tomlWriter = toml.TomlDomSerializer();
                tomlWriter.writeF64(5e-324)!;
                var tomlReader = toml.TomlDomDeserializer(tomlWriter.takeValue()!);
                var subnormal = tomlReader.readF64()!;
                io.println(convert.bitCast<u64>(subnormal) == 1)!;
                tomlWriter.writeF64(-math.inf<f64>())!;
                var infinity = tomlWriter.takeValue()!;
                io.println(toml.asF64(raw infinity)! == -math.inf<f64>())!;
            }
        """, "true\n" * 7)

    def test_environment_snapshot(self):
        self.hosted(r"""
            import (env, text)
            func main() !void {
                var key: str = "VAQRIN_TEST_SNAPSHOT";
                env.remove(key)!;
                io.println(!env.contains(key)!)!;
                var fallback = env.getOr(key, "missing")!;
                io.println(text.equal(fallback, "missing"))!;
                env.set(key, "")!;
                io.println(env.contains(key)!)!;
                var empty = env.get(key)!.unwrap();
                io.println(empty.byteLen() == 0)!;
                env.set(key, "有=值")!;
                var snapshot = env.snapshot()!;
                var previous = snapshot.get(key)!.unwrap();
                io.println(text.equal(previous, "有=值"))!;
                snapshot.set(key, "owned")!;
                var cloned = snapshot.clone()!;
                var keys = snapshot.keys()!;
                snapshot.remove(key)!;
                io.println(!snapshot.contains(key))!;
                var wasListed = false;
                for index in usize(0)..keys.len() {
                    if text.equal(keys[index], key) { wasListed = true; }
                }
                io.println(wasListed)!;
                var clonedValue = cloned.get(key)!.unwrap();
                var liveValue = env.getOr(key, "missing")!;
                io.println(text.equal(clonedValue, "owned"))!;
                io.println(text.equal(liveValue, "有=值"))!;
                env.remove(key)!;
                env.set("bad=key", "value") catch (reason) {
                    io.println(reason == env.EnvError.InvalidKey)!;
                };
                var executable = env.currentExe()!;
                io.println(executable.isAbsolute())!;
            }
        """, "true\n" * 11)

    def test_process_status_and_output(self):
        self.hosted(r"""
            import (env, fs, path, process)
            func main() !void {
                fs.mkdir("child-workdir")!;
                env.set("VAQRIN_CHILD_MARKER", "works")!;
                var command = process.Command.new(path.fromString("/bin/sh")!);
                command.cwd(path.fromString("child-workdir")!)!;
                var args = [2]str{"-c",
                    "test \"${PWD##*/}\" = child-workdir && printf %s \"$VAQRIN_CHILD_MARKER\"; printf stderr >&2; exit 37"};
                command.args(args[:])!;
                var captured = command.output(11)!;
                io.println(captured.stdoutText()!)!;
                io.println(captured.stderrText()!)!;
                io.println(captured.status().code().unwrap())!;
                io.println(captured.status().signal() == null)!;
                command.output(10) catch (reason) {
                    io.println(reason == process.ProcessError.OutputLimit)!;
                    var empty = process.Command.new(path.fromString("/bin/sh")!);
                    var zero = empty.output(0)!;
                    io.println(zero.status().success() && zero.stdout().len() == 0)!;
                    var script = [2]str{"-c", "head -c 70000 /dev/zero; head -c 80000 /dev/zero >&2"};
                    var both = process.output(path.fromString("/bin/sh")!, script[:], 150000)!;
                    io.println(both.stdout().len() == 70000 && both.stderr().len() == 80000)!;
                    var terminate = [2]str{"-c", "kill -TERM $$"};
                    var signaled = process.run(path.fromString("/bin/sh")!, terminate[:])!;
                    io.println(signaled.code() == null && signaled.signal().unwrap() == 15)!;
                    var missing = process.Command.new(path.fromString("./missing-executable")!);
                    missing.status() catch (reason) {
                        io.println(io.isSystemError(reason))!;
                        return;
                    };
                    return;
                };
            }
        """, "works\nstderr\n37\n" + "true\n" * 6)

    def test_spawn_child_pipes_and_file_redirection(self):
        self.hosted(r"""
            import (env, fs, path, process)

            func main() !void {
                env.set("VAQRIN_CHILD_REMOVE_TEST", "present")!;
                var command = process.Command.new(path.fromString("/bin/sh")!);
                var args = [2]str{"-c",
                    "IFS= read -r word; printf '%s:%s' \"$word\" \"$VAQRIN_CHILD_TEST\"; test -z \"$VAQRIN_CHILD_REMOVE_TEST\""};
                command.args(args[:])!;
                command.env("VAQRIN_CHILD_TEST", "before")!;
                command.env("VAQRIN_CHILD_TEST", "child")!;
                command.envRemove("VAQRIN_CHILD_REMOVE_TEST")!;
                command.stdin(process.Stdio.Piped);
                command.stdout(process.Stdio.Piped);
                command.stderr(process.Stdio.Null);
                var child = process.spawn(command)!;
                io.println(child.tryWait()! == null)!;
                {
                    var input = child.stdin().unwrap();
                    io.writeAll(input.toRaw(), "hello\n".asBytes())!;
                    input.close()!;
                }
                var captured = child.waitWithOutput(20)!;
                io.println(captured.stdoutText()!)!;
                io.println(captured.status().success() && child.wait()!.success())!;
                io.println(env.contains("VAQRIN_CHILD_REMOVE_TEST")!)!;

                var inherited = [2]str{"-c", "printf 'inherited\\n'"};
                io.println(process.run(path.fromString("/bin/sh")!, inherited[:])!.success())!;

                var producer = process.Command.new(path.fromString("/bin/sh")!);
                var redirectedArgs = [2]str{"-c", "test -z \"$VAQRIN_CHILD_REMOVE_TEST\" && printf file"};
                producer.args(redirectedArgs[:])!;
                producer.envClear();
                var destination = fs.create("redirected.txt")!;
                var redirect = process.Stdio.File(move(destination));
                producer.stdout(move(redirect));
                io.println(producer.status()!.success())!;
                var file = fs.openRead(path.fromString("redirected.txt")!)!;
                io.println(io.readToString(raw file)!)!;

                var blocked = process.Command.new(path.fromString("/bin/cat")!);
                blocked.stdin(process.Stdio.Piped);
                var running = blocked.spawn()!;
                running.kill()!;
                io.println(running.wait()!.signal().unwrap() == 9)!;

                var closed = process.Command.new(path.fromString("/bin/sh")!);
                var closedArgs = [2]str{"-c", "exec 0<&-; printf x"};
                closed.args(closedArgs[:])!;
                closed.stdin(process.Stdio.Piped);
                closed.stdout(process.Stdio.Piped);
                var noInput = closed.spawn()!;
                {
                    var out = noInput.stdout().unwrap();
                    var byte = [1]u8{0};
                    io.readExact(out.toRaw(), byte[:])!;
                }
                {
                    var input = noInput.stdin().unwrap();
                    input.write("x".asBytes()) catch (reason) {
                        io.println(io.systemErrorCode(reason).unwrap() == BROKEN_PIPE_ERRNO)!;
                        usize(0)
                    };
                }
                io.println(noInput.wait()!.success())!;
            }
        """.replace("BROKEN_PIPE_ERRNO", str(errno.EPIPE)),
                    "true\nhello:child\ntrue\ntrue\ninherited\ntrue\ntrue\nfile\ntrue\ntrue\ntrue\n")

        source = self.source("borrowed_child.vqr", """
            import (path, process)
            func f() !void {
                var cmd = process.Command.new(path.fromString("/bin/cat")!);
                var child = cmd.spawn()!;
                var input = child.stdin();
                var moved = move(child);
            }
        """)
        result = self.process(VAQC, "check", source, good=False)
        self.assertIn(b"active borrow", result.stderr)

    def test_network_addresses_and_loopback(self):
        self.hosted(r"""
            import (net, result, time)

            func main() !void {
                var ip = net.parseIpv4("127.0.0.2")!;
                io.println(ip.isLoopback() && !ip.isPrivate())!;
                var address6 = net.parseSocketAddr("[fe80::1%7]:80")!;
                io.println(address6.toString()!)!;
                var invalid = net.parseSocketAddr("::1:80");
                io.println(result.errorOf(raw invalid).unwrap() == net.NetError.InvalidAddress)!;
                var resolved = net.resolveAddr("127.0.0.1", 81)!;
                io.println(resolved[0].toString()!)!;
                io.println(net.ntohl(net.htonl(0x12345678)) == 0x12345678)!;

                var local = net.parseSocketAddr("127.0.0.1:0")!;
                var listener = net.listenTcp(local)!;
                var endpoint = listener.localAddr()!;
                var client = net.TcpStream.connectTimeout(endpoint, time.Duration.fromSeconds(2))!;
                var accepted = listener.accept()!;
                io.println(client.peerAddr()!.port() == endpoint.port())!;
                io.println(accepted.1.port() == client.localAddr()!.port())!;
                client.setNoDelay(true)!;
                client.setWriteTimeout(some(time.Duration.fromSeconds(1)))!;
                io.writeString(raw client, "ping")!;
                client.shutdownWrite()!;
                var clone = accepted.0.tryClone()!;
                accepted.0.close()!;
                io.println(io.readToString(raw clone, 8)!)!;
                var broken = client.write("again".asBytes());
                io.println(io.systemErrorCode(result.errorOf(raw broken).unwrap()).unwrap() == BROKEN_PIPE_ERRNO)!;

                listener.setNonblocking(true)!;
                var pending = listener.accept();
                io.println(io.isWouldBlock(result.errorOf(raw pending).unwrap()))!;

                var receiver = net.bindUdp(local)!;
                var sender = net.bindUdp(local)!;
                var destination = receiver.localAddr()!;
                io.println(sender.sendTo("datagram".asBytes(), destination)!)!;
                var buffer = [3]u8{0, 0, 0};
                var datagram = receiver.recvFrom(buffer[:])!;
                io.println(datagram.truncated && datagram.len == 3)!;
                io.println(datagram.source.port() == sender.localAddr()!.port())!;
                sender.connect(destination)!;
                io.println(sender.send("".asBytes())!)!;
                var empty = receiver.recv(buffer[0..0])!;
                io.println(empty.len == 0 && !empty.truncated)!;
                sender.send("one".asBytes())!;
                var discarded = receiver.recv(buffer[0..0])!;
                io.println(discarded.len == 0 && discarded.truncated)!;
                sender.send("two".asBytes())!;
                var exact = receiver.recv(buffer[:])!;
                io.println(exact.len == 3 && !exact.truncated)!;
            }
        """.replace("BROKEN_PIPE_ERRNO", str(errno.EPIPE)),
                    "true\n[fe80::1%7]:80\ntrue\n127.0.0.1:81\ntrue\ntrue\ntrue\nping\ntrue\ntrue\n8\ntrue\ntrue\n0\ntrue\ntrue\ntrue\n")

    def test_dense_linear_algebra(self):
        self.hosted(r"""
            import (linalg, math, result)
            func main() !void {
                var entries = [4]f64{0.0, 2.0, 1.0, 3.0};
                var rhs = [2]f64{4.0, 7.0};
                var matrix = linalg.Matrix<f64>.fromFlat(2, 2, entries[:])!;
                var b = linalg.Vector<f64>.fromSlice(rhs[:])!;
                var solution = linalg.solve<f64>(matrix, b)!;
                io.println(*solution.get(0).unwrap() == 1.0 && *solution.get(1).unwrap() == 2.0)!;
                io.println(linalg.determinant<f64>(matrix)! == -2.0)!;
                var inverse = linalg.inverse<f64>(matrix)!;
                var identity = matrix.matmul(inverse)!;
                io.println(math.abs<f64>(identity.at(0, 0) - 1.0) < 0.000001)!;
                var singular = linalg.Matrix<f64>.zeros(2, 2)!;
                var rejected = linalg.inverse<f64>(singular);
                io.println(result.errorOf(raw rejected).unwrap() == linalg.LinalgError.Singular)!;
            }
        """, "true\n" * 4)

    def test_linalg_operands_are_checked_borrows(self):
        self.hosted("""
            import (linalg)
            func main() !void {
                input := [_]f64{1.0, 2.0, 3.0, 4.0};
                const matrix = linalg.Matrix<f64>.fromFlat(2, 2, input[:])!;
                var squared = linalg.matmul<f64>(matrix, matrix)!;
                var doubled = matrix.add(matrix)!;
                var zero = matrix.sub(matrix)!;
                var transposed = linalg.transpose<f64>(matrix)!;
                var product = linalg.kron<f64>(matrix, matrix)!;
                io.println(squared.at(1, 1) == 22.0 && doubled.at(1, 1) == 8.0)!;
                io.println(zero.at(0, 1) == 0.0 && transposed.at(0, 1) == 3.0)!;
                io.println(product.at(3, 3) == 16.0 && linalg.trace<f64>(matrix)! == 5.0)!;

                const v = linalg.Vector<f64>.fromSlice(input[:])!;
                var sum = v.add(v)!;
                io.println(v.dot(v)! == 30.0 && sum.dot(v)! == 60.0)!;
            }
        """, "true\n" * 4)

        source = self.source("raw_matrix_operand.vqr", """
            import (linalg)
            func f() !void {
                var matrix = linalg.Matrix<f64>.zeros(2, 2)!;
                var outcome = matrix.matmul(raw matrix)!;
            }
        """)
        result = self.process(VAQC, "check", source, good=False)
        self.assertIn(b"no accessible overload", result.stderr)

    def test_linalg_element_references_are_tied_to_owner(self):
        self.hosted("""
            import (linalg)
            func main() !void {
                var vector = linalg.Vector<f64>.zeros(2)!;
                {
                    var slot: ref mut f64 = vector.getMut(1).unwrap();
                    slot = 7.0;
                }
                {
                    var slot: ref f64 = vector.get(1).unwrap();
                    io.println(slot == 7.0)!;
                }
                vector.resize(3, 2.0)!;
                io.println(*vector.get(2).unwrap() == 2.0)!;

                var matrix = linalg.Matrix<f64>.zeros(2, 2)!;
                {
                    var slot: ref mut f64 = matrix.getMut(1, 1).unwrap();
                    slot = 3.0;
                }
                {
                    var slot: ref f64 = matrix.get(1, 1).unwrap();
                    io.println(slot == 3.0)!;
                }
                matrix.set(0, 0, 5.0);
                io.println(matrix.at(0, 0) == 5.0)!;
            }
        """, "true\n" * 4)
        for case, program in (
            ("resize", """
                var vector = linalg.Vector<f64>.zeros(2)!;
                var shared: ref f64 = vector.get(0).unwrap();
                vector.resize(4, 1.0)!;
                io.println(shared)!;
            """),
            ("matrix_write", """
                var matrix = linalg.Matrix<f64>.zeros(2, 2)!;
                var shared: ref f64 = matrix.get(1, 1).unwrap();
                matrix.set(1, 1, 2.0);
                io.println(shared)!;
            """),
        ):
            with self.subTest(case=case):
                source = self.source(case + "/main.vqr", """
                    package main
                    import (io, linalg)
                    func main() !void {
                """ + program + "}")
                result = self.process(VAQC, "check", source, good=False)
                self.assertIn(b"error[E230]: access conflicts with active borrow", result.stderr)

    def test_linalg_readonly_slices_track_the_matrix_and_vector(self):
        prelude = """
            import (linalg)
            func elements(owner: in linalg.Matrix<f64>) []const f64 from owner {
                owner.asSlice()
            }
            func untracked(slice: []const f64) void {}
        """
        program = prelude + """
            func main() !void {
                var matrix = linalg.Matrix<f64>.zeros(1, 2)!;
                matrix.set(0, 0, 3.0);
                matrix.set(0, 1, 4.0);
                var vector = linalg.Vector<f64>.zeros(2)!;
                *vector.getMut(1).unwrap() = 2.0;
                {
                    var flat = elements(matrix);
                    var data = vector.asSlice();
                    io.println(linalg.dot<f64>(flat, data)! == 8.0)!;
                    io.println(linalg.norm2<f64>(flat) == 5.0)!;
                    io.println(linalg.outer<f64>(flat, data)!.at(1, 1) == 8.0)!;
                }
                matrix.set(0, 1, 2.0);
                vector.resize(3, 1.0)!;
                io.println(matrix.at(0, 1) == 2.0 && vector.len() == 3)!;
            }
        """
        for level in ("-O0", "-O2"):
            with self.subTest(level=level):
                self.hosted(program, "true\n" * 4, level)
        for name, code, diagnostic in (
            ("write", "var view = elements(matrix); matrix.set(0, 0, 1.0);", "active borrow"),
            ("resize", "var view = vector.asSlice(); vector.resize(4, 1.0)!;", "active borrow"),
            ("move", "var view = matrix.asSlice(); var next = move(matrix);", "active borrow"),
            ("untracked", "untracked(matrix.asSlice());", "untracked"),
        ):
            with self.subTest(name=name):
                source = self.source(name + "/main.vqr", prelude + """
                    func main() !void {
                        var matrix = linalg.Matrix<f64>.zeros(2, 2)!;
                        var vector = linalg.Vector<f64>.zeros(2)!;
                """ + code + "}")
                result = self.process(VAQC, "check", source, good=False)
                self.assertIn(diagnostic.encode(), result.stderr, result.stderr.decode())

    def test_qr_least_squares(self):
        self.hosted(r"""
            import (linalg, math, result)
            func main() !void {
                var design = [6]f64{1.0, 0.0, 1.0, 1.0, 1.0, 2.0};
                var measurements = [3]f64{1.0, 2.0, 2.0};
                var matrix = linalg.Matrix<f64>.fromFlat(3, 2, design[:])!;
                var rhs = linalg.Vector<f64>.fromSlice(measurements[:])!;
                var factor = linalg.qr<f64>(matrix)!;
                var coeff = factor.solveLeastSquares(rhs)!;
                io.println(math.abs<f64>(*coeff.get(0).unwrap() - 7.0 / 6.0) < 0.000001)!;
                io.println(math.abs<f64>(*coeff.get(1).unwrap() - 0.5) < 0.000001)!;
                var q = factor.q()!;
                var r = factor.r()!;
                var reconstructed = q.matmul(r)!;
                var transpose = q.transposeCopy()!;
                var gram = transpose.matmul(q)!;
                io.println(linalg.distance<f64>(reconstructed.asSlice(), matrix.asSlice())! < 0.000001)!;
                io.println(math.abs<f64>(gram.at(0, 0) - 1.0) < 0.000001 &&
                           math.abs<f64>(gram.at(1, 1) - 1.0) < 0.000001 &&
                           math.abs<f64>(gram.at(0, 1)) < 0.000001)!;
                var rankZero = linalg.Matrix<f64>.zeros(3, 2)!;
                var deficient = linalg.qr<f64>(rankZero)!;
                var rejected = deficient.solveLeastSquares(rhs);
                io.println(result.errorOf(raw rejected).unwrap() == linalg.LinalgError.Singular)!;
            }
        """, "true\n" * 5)

    def test_http_wire_and_message_boundaries(self):
        self.hosted(r"""
            import (http, result)
            func main() !void {
                var input = io.MemoryReader("POST /submit HTTP/1.1\r\nhOsT: localhost\r\nTransfer-Encoding: chunked\r\n\r\n4\r\ndata\r\n0\r\n\r\nGET /next HTTP/1.1\r\nHost: localhost\r\n\r\n".asBytes());
                var limits = http.HttpLimits();
                var request = http.parseRequest(raw input, limits)!;
                io.println(request.target())!;
                io.println(request.bodyBytes().len())!;
                var next = http.parseRequest(raw input, limits)!;
                io.println(next.target())!;
                var response = http.Response.new(200)!;
                response.setBody(http.Body.fromBytes("hello".asBytes())!);
                var writer = io.MemoryWriter();
                http.writeResponse(raw writer, raw response)!;
                var back = io.MemoryReader(writer.asSlice());
                var decoded = http.parseResponse(raw back, limits)!;
                io.println(decoded.status().value())!;
                io.println(decoded.bodyBytes().len())!;
                var other = io.MemoryReader("HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\n".asBytes());
                var head = http.parseResponseFor(raw other, http.Method.HEAD, limits)!;
                io.println(head.bodyBytes().len())!;
                var duplicate = io.MemoryReader("POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\nContent-Length: 1\r\n\r\n".asBytes());
                var rejected = http.parseRequest(raw duplicate, limits);
                io.println(result.errorOf(raw rejected).unwrap() == http.HttpError.InvalidFraming)!;
            }
        """, "/submit\n4\n/next\n200\n5\n0\ntrue\n")

    def test_http_tcp_client(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                data = self.path.encode("ascii")
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            source = self.source("http_client.vqr", f"""
                import (http, io, text)
                func main() !void {{
                    var response = http.get("http://127.0.0.1:{server.server_port}/value?x=1")!;
                    io.println(response.status().value())!;
                    io.println(text.fromBytes(response.bodyBytes())!)!;
                }}
            """)
            self.process(VAQC, source, "-o", "client")
            server.timeout = 8
            with ThreadPoolExecutor(max_workers=1) as thread:
                handled = thread.submit(server.handle_request)
                self.assertEqual(self.process("./client").stdout, b"200\n/value?x=1\n")
                handled.result(timeout=10)

    def test_https_client(self):
        def create_certificate(name, alternative_names):
            cert = self.root / f"{name}.pem"
            key = self.root / f"{name}.key"
            self.process(
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-subj", f"/CN=Vaqrin {name} test", "-addext", f"subjectAltName={alternative_names}",
                "-keyout", key, "-out", cert,
            )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            return context, cert

        dns, dns_cert = create_certificate("dns", "DNS:localhost")
        ip, ip_cert = create_certificate("ip", "IP:127.0.0.1")
        ca_bundle = self.root / "trust.pem"
        ca_bundle.write_bytes(dns_cert.read_bytes() + ip_cert.read_bytes())
        empty_certs = self.root / "empty-certs"
        empty_certs.mkdir()
        names = []
        dns.set_servername_callback(lambda _, name, __: names.append(name))
        ip.set_servername_callback(lambda _, name, __: names.append(name))

        with socket.create_server(("127.0.0.1", 0)) as server:
            server.settimeout(8)
            port = server.getsockname()[1]

            def serve(context, response, expected_request=None, clean_close=True):
                connection, _ = server.accept()
                with connection:
                    connection.settimeout(5)
                    if response is None:
                        with self.assertRaises(ssl.SSLError):
                            with context.wrap_socket(connection, server_side=True):
                                pass
                        return
                    with context.wrap_socket(connection, server_side=True) as tls:
                        with tls.makefile("rb") as request:
                            self.assertEqual(request.readline(), expected_request)
                            headers = {}
                            for line in iter(request.readline, b"\r\n"):
                                name, value = line.rstrip(b"\r\n").split(b":", 1)
                                headers[name.lower()] = value.strip()
                            host = b"127.0.0.1" if context is ip else b"localhost"
                            self.assertEqual(headers[b"host"], host + f":{port}".encode())
                            self.assertEqual(headers[b"connection"], b"close")
                            body = request.read(int(headers.get(b"content-length", b"0")))
                            if expected_request.startswith(b"PATCH "):
                                self.assertEqual(body, b"update")
                            elif expected_request.startswith(b"POST "):
                                self.assertEqual(headers[b"x-probe"], b"custom")
                                self.assertEqual(body, b"example")
                        tls.sendall(response)
                        if clean_close:
                            tls.unwrap().close()

            program = self.source("https.vqr", f"""
                import (env, http, io, result, text, time)
                func main() !void {{
                    var client = http.Client();
                    if env.contains("VAQRIN_TEST_UNTRUSTED")! {{
                        var rejected = client.get("https://localhost:{port}/");
                        io.println(result.errorOf(raw rejected).unwrap() == http.TlsError.Certificate)!;
                        return;
                    }}
                    var fetched = http.get("https://localhost:{port}/probe?name=vqr#fragment")!;
                    io.println(text.fromBytes(fetched.bodyBytes())!)!;
                    var body = http.Body.fromBytes("update".asBytes())!;
                    var patched = client.request("https://localhost:{port}/record", http.Method.PATCH, move(body))!;
                    io.println(text.fromBytes(patched.bodyBytes())!)!;
                    var head = http.head("https://127.0.0.1:{port}/")!;
                    io.println(head.bodyBytes().len())!;
                    var message = http.Request.new(http.Method.POST, "/custom")!;
                    message.headers().set("X-Probe", "custom")!;
                    message.setBody(http.Body.fromBytes("example".asBytes())!);
                    var sent = client.send("https://localhost:{port}/", raw message)!;
                    io.println(sent.status().value())!;
                    var truncated = client.get("https://localhost:{port}/");
                    io.println(result.errorOf(raw truncated).unwrap() == http.TlsError.Protocol)!;
                    var mismatch = client.get("https://127.0.0.1:{port}/");
                    io.println(result.errorOf(raw mismatch).unwrap() == http.TlsError.Certificate)!;
                    client.setTimeout(time.Duration.fromMillis(250));
                    var stalled = client.get("https://localhost:{port}/");
                    io.println(result.errorOf(raw stalled).unwrap() == io.systemError({errno.ETIMEDOUT}))!;
                }}
            """)
            executable = self.root / "https-client"
            self.process(VAQC, program, "-o", executable)

            def serve_trusted_requests():
                serve(dns, b"HTTP/1.0 200 OK\r\n\r\nclose-delimited",
                      b"GET /probe?name=vqr HTTP/1.1\r\n")
                serve(dns, b"HTTP/1.1 103 Early Hints\r\n\r\n"
                           b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n7\r\npatched\r\n0\r\n\r\n",
                      b"PATCH /record HTTP/1.1\r\n")
                serve(ip, b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n", b"HEAD / HTTP/1.1\r\n")
                serve(dns, b"HTTP/1.1 201 Created\r\nContent-Length: 0\r\n\r\n", b"POST /custom HTTP/1.1\r\n")
                serve(dns, b"HTTP/1.0 200 OK\r\n\r\npartial", b"GET / HTTP/1.1\r\n", clean_close=False)
                serve(dns, None)
                connection, _ = server.accept()
                with connection:
                    connection.settimeout(5)
                    # Read the ClientHello and let the client time out waiting for a reply.
                    while connection.recv(4096):
                        pass

            environment = {**os.environ, "SSL_CERT_DIR": str(empty_certs), "SSL_CERT_FILE": str(ca_bundle)}
            environment.pop("VAQRIN_TEST_UNTRUSTED", None)
            with ThreadPoolExecutor(max_workers=1) as thread:
                handled = thread.submit(serve_trusted_requests)
                client = subprocess.run([executable], env=environment, capture_output=True, timeout=15)
                self.assertEqual(client.returncode, 0, client.stderr.decode(errors="replace"))
                self.assertEqual(client.stdout, b"close-delimited\npatched\n0\n201\ntrue\ntrue\ntrue\n")
                handled.result(timeout=10)
                handled = thread.submit(serve, dns, None)
                environment.update(SSL_CERT_FILE=str(ip_cert), VAQRIN_TEST_UNTRUSTED="1")
                client = subprocess.run([executable], env=environment, capture_output=True, timeout=15)
                self.assertEqual(client.returncode, 0, client.stderr.decode(errors="replace"))
                self.assertEqual(client.stdout, b"true\n")
                handled.result(timeout=10)

        self.assertEqual(names, ["localhost", "localhost", None, "localhost", "localhost", None, "localhost"])

    def test_http_tcp_server(self):
        program = self.source("server.vqr", r"""
            import (fmt, http, io, net)

            class Echo {
                #implements: http.Handler
                pub func handle(request: move http.Request) !http.Response {
                    var response = http.Response.new(201)!;
                    response.setBody(http.Body.fromBytes(request.bodyBytes())!);
                    move(response)
                }
            }

            func main() !void {
                var endpoint = net.parseSocketAddr("127.0.0.1:0")!;
                var server = http.Server.bind(endpoint)!;
                var address = server.localAddr()!;
                var port: u16 = address.port();
                var digits = fmt.formatUint(port, 10)!;
                io.print(digits)!;
                io.print("\n")!;
                var handler = Echo();
                server.serveOne(raw handler)
            }
        """)
        executable = self.root / "http-server"
        self.process(VAQC, program, "-o", executable)
        with subprocess.Popen([executable], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as server:
            with ThreadPoolExecutor(max_workers=1) as thread:
                port_result = thread.submit(server.stdout.readline)
                try:
                    port = int(port_result.result(timeout=5))
                    connection = HTTPConnection("127.0.0.1", port, timeout=5)
                    try:
                        connection.request("POST", "/echo", body=b"Vaqrin")
                        response = connection.getresponse()
                        self.assertEqual(response.status, 201)
                        self.assertEqual(response.read(), b"Vaqrin")
                    finally:
                        connection.close()
                    self.assertEqual(server.wait(timeout=5), 0)
                finally:
                    if server.poll() is None:
                        server.kill()

    def test_json(self):
        lines = self.example("stdlib/json/examples/json_smoke.vqr").splitlines()
        self.assertEqual(lines[0], "true")
        expected = {"ok": True, "items": [1, 2, {"name": "Vaqrin"}], "rocket": "🚀"}
        self.assertEqual(json.loads(lines[1]), expected)
        self.assertEqual(json.loads("\n".join(lines[2:-5])), expected)
        self.assertEqual(lines[-5:], ["true", "true", "1", "true", "true"])

    def test_json_parser_uses_owned_output_and_duplicate_slots(self):
        self.hosted(r"""
            import (json)

            func printString(value: move json.Value) !void {
                switch move(value) {
                    json.Value.String(text) => { io.println(text)!; },
                    _ => panic("expected a JSON string"),
                }
            }

            func parseDuplicate(policy: json.DuplicateKeyPolicy) !void {
                var options = json.ParseOptions();
                options.duplicateKeys = policy;
                var parsed = json.parseWithOptions("{\"a\":\"old\",\"\\u0061\":\"new\"}", options)!;
                switch move(parsed) {
                    json.Value.Object(object) => {
                        if object.len() != 1 { panic("duplicate produced an extra entry"); }
                        printString(object.remove("a").unwrap())!;
                    },
                    _ => panic("expected a JSON object"),
                }
            }

            func main() !void {
                printString(json.parse("\"\\u0041\\u00e9\\u6c34\\ud83d\\ude80\"")!)!;
                parseDuplicate(json.DuplicateKeyPolicy.KeepFirst)!;
                parseDuplicate(json.DuplicateKeyPolicy.KeepLast)!;
                var rejected = json.parse("{\"a\":\"old\",\"\\u0061\":\"new\"}") catch (reason) {
                    io.println(reason == json.JsonError.DuplicateKey)!;
                    return;
                };
                panic("duplicate keys were not rejected");
            }
        """, "Aé水🚀\nold\nnew\ntrue\n")

    def test_json_object_replaces_and_returns_owned_payload(self):
        self.hosted("""
            import (json)
            func main() !void {
                var object = json.JsonObject();
                var initial: string = "old";
                if object.insert("key", json.Value.String(move(initial)))! != null {
                    panic("initial insertion replaced an entry");
                }
                var previous = object.insert("key", json.Value.Bool(true))!.unwrap();
                switch move(previous) {
                    json.Value.String(value) => { io.println(value)!; },
                    _ => panic("replacement lost the old owned string"),
                }
                var removed = object.remove("key").unwrap();
                switch move(removed) {
                    json.Value.Bool(value) => { io.println(value)!; },
                    _ => panic("removal did not return the new value"),
                }
                io.println(object.len() == 0 && object.remove("key") == null)!;
            }
        """, "old\ntrue\ntrue\n")

    def test_json_encoder_borrows_values_from_their_owners(self):
        self.hosted("""
            import (json)
            func main() !void {
                var array = json.JsonArray();
                array.push(json.Value.Bool(true))!;
                {
                    var child: ref json.Value = array.getRef(0).unwrap();
                    io.println(json.stringify(child)!)!;
                }
                array.push(json.Value.Null)!;
                io.println(json.stringify(json.Value.Array(move(array)))!)!;

                var object = json.JsonObject();
                var previous = object.insert("key", json.Value.Bool(false))!;
                {
                    var child: ref json.Value = object.valueRefAt(0).unwrap();
                    io.println(json.stringify(child)!)!;
                }
                var removed = object.remove("key").unwrap();
                io.println(json.stringify(removed)!)!;
            }
        """, "true\n[true,null]\nfalse\nfalse\n")

        for case, program, diagnostic in (
            ("array_growth", """
                var array = json.JsonArray();
                array.push(json.Value.Null)!;
                var child: ref json.Value = array.getRef(0).unwrap();
                array.reserve(16)!;
                io.println(json.stringify(child)!)!;
            """, b"access conflicts with active borrow"),
            ("object_removal", """
                var object = json.JsonObject();
                var previous = object.insert("key", json.Value.Null)!;
                var child: ref json.Value = object.valueRefAt(0).unwrap();
                var removed = object.remove("key");
                io.println(json.stringify(child)!)!;
            """, b"access conflicts with active borrow"),
            ("raw_value", """
                var value = json.Value.Null;
                var encoded = json.stringify(raw value)!;
            """, b"no accessible overload"),
        ):
            with self.subTest(case=case):
                source = self.source(case + "/main.vqr", """
                    package main
                    import (io, json)
                    func main() !void {
                """ + program + "}")
                result = self.process(VAQC, "check", source, good=False)
                self.assertIn(diagnostic, result.stderr)

    def test_json_value_insert_and_serde_transfer_ownership(self):
        self.hosted("""
            import (json)
            func main() !void {
                var input = json.Value.Bool(true);
                var array = json.JsonArray();
                array.push(move(input))!;
                {
                    var element: ref json.Value = array.getRef(0).unwrap();
                    switch element {
                        json.Value.Bool(value) => { io.println(value)!; },
                        _ => panic("expected original owned boolean"),
                    }
                }
                input = json.Value.Null;
                array.insert(0, move(input))!;
                var old = array.replace(0, json.Value.Bool(false)).unwrap();
                switch old {
                    json.Value.Null => { io.println(true)!; },
                    _ => panic("replacement did not transfer the previous value"),
                }

                var root = json.Value.Array(move(array));
                var updated = json.Value.Bool(true);
                json.pointerSet(raw root, "/0", move(updated))!;
                var reader = json.JsonDomDeserializer(move(root));
                io.println(reader.beginSeq()!.unwrap() == 2)!;
                if !reader.nextElement()! { panic("first element missing"); }
                io.println(reader.readBool()!)!;
                if !reader.nextElement()! { panic("second element missing"); }
                io.println(reader.readBool()!)!;
                reader.endSeq()!;
                reader.finish()!;

                var writer = json.JsonDomSerializer();
                writer.beginMap(null)!;
                writer.writeKey("seq")!;
                writer.beginSeq(null)!;
                writer.writeBool(true)!;
                writer.endSeq()!;
                writer.endMap()!;
                var document = writer.takeValue()!;
                var decoded = json.pointerGet(raw document, "/seq/0").unwrap();
                io.println(json.asBool(decoded).unwrap())!;
            }
        """, "true\n" * 6)

        for case, action, diagnostic in (
            ("push", "array.push(input)!;", b"move parameter requires move(object)"),
            ("insert", "array.insert(0, input)!;", b"move parameter requires move(object)"),
            ("replace", "var previous = array.replace(0, input);", b"move parameter requires move(object)"),
            ("object", 'var old = object.insert("name", input)!;', b"move parameter requires move(object)"),
            ("reader", "var reader = json.JsonDomDeserializer(input);", b"move parameter requires move(object)"),
            ("pointer", 'json.pointerSet(raw target, "", input)!;', b"move parameter requires move(object)"),
            ("use_after_move", "array.push(move(input))!; var reused = move(input);", b"not initialized"),
        ):
            with self.subTest(case=case):
                source = self.source(case + "/main.vqr", """
                    package main
                    import (json)
                    func main() !void {
                        var array = json.JsonArray();
                        var object = json.JsonObject();
                        var target = json.Value.Null;
                        var input = json.Value.Bool(true);
                """ + action + "}")
                result = self.process(VAQC, "check", source, good=False)
                self.assertIn(diagnostic, result.stderr)

    def test_json_encoder_options(self):
        expected = "\n".join([
            r'{"z":[{"\ud83d\ude80":"\u00e9\ud83d\ude00\n\u0001","b":2}],"\u00e9":0,"aa":1,"a":2}',
            "{",
            '  "a": 2,',
            '  "aa": 1,',
            '  "z": [',
            "    {",
            '      "b": 2,',
            r'      "\ud83d\ude80": "\u00e9\ud83d\ude00\n\u0001"',
            "    }",
            "  ],",
            r'  "\u00e9": 0',
            "}",
            r'{"z":[{"🚀":"é😀\n\u0001","b":2}],"é":0,"aa":1,"a":2}',
            "",
        ])
        self.hosted(r'''
            import (json, text)

            func main() !void {
                var value = json.parse(
                    "{\"z\":[{\"🚀\":\"é😀\\n\\u0001\",\"b\":2}],\"é\":0,\"aa\":1,\"a\":2}"
                )!;
                var options = json.EncodeOptions();
                options.escapeNonAscii = true;
                var compact = io.MemoryWriter();
                json.write(raw compact, value, options)!;
                io.println(text.fromBytes(compact.asSlice())!)!;

                options.sortKeys = true;
                options.indent = 2;
                var pretty = io.MemoryWriter();
                var encoder = json.Encoder(raw pretty, options);
                encoder.encodeValue(value)!;
                encoder.flush()!;
                io.println(text.fromBytes(pretty.asSlice())!)!;
                io.println(json.stringify(value)!)!;
            }
        ''', expected)

    def test_json_encoder_reuse_after_depth_error(self):
        self.hosted('''
            import (json, text)

            func main() !void {
                var options = json.EncodeOptions();
                options.maxDepth = 1;
                var output = io.MemoryWriter();
                var encoder = json.Encoder(raw output, options);
                var nested = json.parse("[[]]")!;
                encoder.encodeValue(nested) catch (reason) {
                    if reason != json.JsonError.DepthLimit { return reason; }
                    io.println("depth limit")!;
                };
                var shallow = json.parse("[]")!;
                encoder.encodeValue(shallow)!;
                io.println(text.fromBytes(output.asSlice())!)!;
            }
        ''', "depth limit\n[[]\n")

    def test_csv(self):
        lines = self.example("stdlib/csv/examples/csv_smoke.vqr").splitlines()
        row = 'alpha,"contains,comma","line one\nline two"\r\n'
        self.assertEqual(lines, ["2", "3", '"value,with,commas"', str(len(row))])

    def test_csv_counts_crlf_and_escaped_quotes_at_record_limit(self):
        self.hosted(r"""
            import (csv, text)
            func main() !void {
                var options = csv.CsvOptions();
                options.maxRecordBytes = 8;
                var decoded = csv.parseRecord("\"a\"\"b\"\r\n", options)!;
                io.println(decoded.len() == 1 && text.equal(decoded[0], "a\"b"))!;
                options.maxRecordBytes = 7;
                csv.parseRecord("\"a\"\"b\"\r\n", options) catch (reason) {
                    io.println(reason == csv.CsvError.RecordLimit)!;
                    return;
                };
                panic("expected the record limit");
            }
        """, "true\ntrue\n")

    def test_toml(self):
        lines = self.example("stdlib/toml/examples/toml_smoke.vqr").splitlines()
        self.assertEqual(lines[:2], ["7", "8080"])
        self.assertIn("ratio = 1.25", lines)
        self.assertIn("when = 1979-05-27T07:32:00Z", lines)
        self.assertEqual(lines[-2:], ["true", "true"])

    def test_toml_scanner_preserves_quoted_separators_and_following_statements(self):
        self.hosted(r"""
            import (toml)
            func main() !void {
                var data = "# comment\r\ntext = \"a#b=c\\u0023\" # trailer\r\nitems = [\"a,b\", \"x=y\", 'hash#']\r\nanswer = 42\r\n";
                var table = toml.parse(data)!;
                var text: ref toml.Value = table.getRef("text").unwrap();
                switch text {
                    toml.Value.String(ref value) => { io.println(value)!; },
                    _ => panic("quoted text was not a string"),
                }
                var list: ref toml.Value = table.getRef("items").unwrap();
                switch list {
                    toml.Value.Array(ref values) => { io.println(values.len())!; },
                    _ => panic("quoted commas split the array incorrectly"),
                }
                var answer: ref toml.Value = table.getRef("answer").unwrap();
                switch answer {
                    toml.Value.Integer(value) => { io.println(value)!; },
                    _ => panic("scanner did not advance to the next statement"),
                }
                var invalid = toml.parse("text = \"\\uD800\"") catch (reason) {
                    io.println(reason == toml.TomlError.InvalidString)!;
                    return;
                };
                panic("surrogate was accepted");
            }
        """, "a#b=c#\n3\n42\ntrue\n")

    def test_toml_post_parse_limits_walk_checked_tables_in_source_order(self):
        self.hosted(r"""
            import (toml, result)

            func limited(input: str, depth: usize, entries: usize) !toml.Document {
                var options = toml.TomlOptions();
                options.maxDepth = depth;
                options.maxEntries = entries;
                toml.parseWithOptions(input, options)
            }

            func tableId(value: in toml.Value) i64 {
                switch value {
                    toml.Value.Table(ref table) => {
                        var id: ref toml.Value = table.getRef("id").unwrap();
                        switch id {
                            toml.Value.Integer(number) => number,
                            _ => panic("expected an integer id"),
                        }
                    },
                    _ => panic("expected a table"),
                }
            }

            func main() !void {
                var input = "root = { child = { leaf = 1 } }\n[[items]]\nid = 2\n[[items]]\nid = 3\n";
                var document = limited(input, 2, 6)!;
                if document.keyAt(0).unwrap() != "root" || document.keyAt(1).unwrap() != "items" {
                    panic("top-level order changed");
                }
                var items: ref toml.Value = document.getRef("items").unwrap();
                switch items {
                    toml.Value.Array(ref array) => {
                        if array.len() != 2 || tableId(array.get(0).unwrap()) != 2 ||
                            tableId(array.get(1).unwrap()) != 3 {
                            panic("array of tables order changed");
                        }
                    },
                    _ => panic("expected an array of tables"),
                }
                var belowEntryLimit = limited(input, 2, 5);
                if result.errorOf(raw belowEntryLimit).unwrap() != toml.TomlError.SizeLimit {
                    panic("expected the post-parse entry limit");
                }

                // The dotted inline key constructs nested tables without
                // invoking parseValue at each intermediate table depth.
                var dotted = "outer = { a.b.c = 1 }\n";
                limited(dotted, 3, 4)!;
                var belowDepthLimit = limited(dotted, 2, 4);
                if result.errorOf(raw belowDepthLimit).unwrap() != toml.TomlError.DepthLimit {
                    panic("expected the post-parse depth limit");
                }
                var both = limited(dotted, 2, 0);
                if result.errorOf(raw both).unwrap() != toml.TomlError.DepthLimit {
                    panic("depth check must still precede the aggregate entry check");
                }
            }
        """, "")

    def test_toml_serializer_reads_checked_document_elements(self):
        self.hosted(r"""
            import (coll, toml)
            func main() !void {
                var document = toml.Document();
                var title: string = "alpha\nquote: \"";
                document.insertUnique("title", toml.Value.String(move(title)))!;

                var inline = toml.Document();
                inline.insertUnique("escaped key", toml.Value.Bool(true))!;
                var array = coll.Vec<toml.Value>();
                array.push(toml.Value.Table(move(inline)))!;
                array.push(toml.Value.Integer(-2))!;
                document.insertUnique("items", toml.Value.Array(move(array)))!;

                var child = toml.Document();
                child.insertUnique("port", toml.Value.Integer(7))!;
                document.insertUnique("server group", toml.Value.Table(move(child)))!;

                {
                    var first: ref toml.Value = document.getRef("title").unwrap();
                    switch first {
                        toml.Value.String(ref name) => { if name.byteLen() != 14 { panic("wrong string length"); } },
                        _ => panic("wrong table entry"),
                    }
                    var indexed: ref toml.Value = document.valueRefAt(2).unwrap();
                    switch indexed {
                        toml.Value.Table(ref childView) => {
                            var port: ref toml.Value = childView.getRef("port").unwrap();
                            switch port {
                                toml.Value.Integer(value) => { if value != 7 { panic("wrong port"); } },
                                _ => panic("wrong port kind"),
                            }
                        },
                        _ => panic("wrong child table kind"),
                    }
                }
                var rendered = toml.stringify(document)!;
                if rendered != "title = \"alpha\\nquote: \\\"\"\nitems = [{\"escaped key\" = true}, -2]\n\n[\"server group\"]\nport = 7\n" {
                    panic("serializer did not preserve exact TOML bytes");
                }
            }
        """, "")

        for case, statement, expected in (
            ("raw", "var rendered = toml.stringify(raw table)!;", b"no accessible overload"),
            ("owner", """
                var selected: ref toml.Value = table.valueRefAt(0).unwrap();
                table.insertUnique("other", toml.Value.Bool(false))!;
                switch selected { _ => {} }
            """, b"access conflicts with active borrow"),
        ):
            with self.subTest(case=case):
                source = self.source(case + "/main.vqr", """
                    package main
                    import (toml)
                    func main() !void {
                        var table = toml.Document();
                        table.insertUnique("item", toml.Value.Integer(1))!;
                """ + statement + "}")
                result = self.process(VAQC, "check", source, good=False)
                self.assertIn(expected, result.stderr)

    def test_toml_dom_mutation_takes_value_ownership(self):
        self.hosted("""
            import (serde, toml)
            func main() !void {
                var table = toml.Document();
                var initial = toml.Value.Integer(11);
                table.set("number", move(initial))!;
                table.set("number", toml.Value.Integer(27))!;
                var inner = toml.Value.Bool(true);
                table.setPath("nested.active", move(inner))!;
                var root = toml.Value.Table(move(table));
                var reader = toml.TomlDomDeserializer(move(root));
                reader.beginMap()!;
                io.println(reader.nextKey()!.unwrap())!;
                io.println(reader.readI64()!)!;
                io.println(reader.nextKey()!.unwrap())!;
                reader.beginMap()!;
                io.println(reader.nextKey()!.unwrap())!;
                io.println(reader.readBool()!)!;
                reader.endMap()!;
                reader.endMap()!;
            }
        """, "number\n27\nnested\nactive\ntrue\n")

        for operation in ('table.set("key", value)!;', 'table.insertUnique("key", value)!;',
                          'table.setPath("outer.key", value)!;', 'toml.TomlDomDeserializer(value);'):
            with self.subTest(operation=operation):
                source = self.source("named_toml_value.vqr", """
                    import (toml)
                    func main() !void {
                        var table = toml.Document();
                        var value = toml.Value.Integer(7);
                        """ + operation + """
                    }
                """)
                error = self.process(VAQC, "check", source, good=False).stderr
                self.assertIn(b"move parameter requires move(object) or an owning temporary", error)

    def test_codec_round_trip(self):
        lines = self.example("stdlib/serde/examples/manual_codec.vqr").splitlines()
        self.assertEqual(json.loads(lines[0]), {"name": "Ada", "age": 37})
        self.assertEqual(lines[1:3], ["Ada", "37"])
        self.assertIn('name = "Ada"', lines[3:-2])
        self.assertIn("age = 37", lines[3:-2])
        self.assertEqual(lines[-2:], ["Ada", "37"])

    def test_deserializers_report_missing_current_value(self):
        self.hosted("""
            import (coll, json, serde, toml)
            func main() !void {
                var jsonReader = json.JsonDomDeserializer(json.parse("[]")!);
                jsonReader.beginSeq()!;
                io.println(jsonReader.nextElement()!)!;
                jsonReader.readBool() catch (reason) {
                    io.println(reason == serde.SerdeError.InvalidState)!;
                    false
                };
                jsonReader.endSeq()!;
                var tomlReader = toml.TomlDomDeserializer(toml.Value.Array(coll.Vec<toml.Value>()));
                tomlReader.beginSeq()!;
                io.println(tomlReader.nextElement()!)!;
                tomlReader.readString() catch (reason) {
                    io.println(reason == serde.SerdeError.InvalidState)!;
                    return;
                };
                panic("expected invalid state");
            }
        """, "false\ntrue\nfalse\ntrue\n")

    def test_unicode(self):
        self.assertEqual(self.example("examples/stdlib/unicode.vqr"), "é\n17.0.0\n")

    def test_regex(self):
        self.assertEqual(self.example("examples/stdlib/regex.vqr"), "é\ntrue\n1\nabab\nab\n3\n")

    def test_unicode_and_regex_edges(self):
        program = self.source("unicode_edges.vqr", r'''
            import (core, io, regex, unicode)
            func main() !void {
                io.println(unicode.normalize("é", unicode.NormalForm.NFD)!)!;
                io.println(unicode.normalize("Ａﬁ", unicode.NormalForm.NFKC)!)!;
                io.println(unicode.normalize("Ａé", unicode.NormalForm.NFKD)!)!;
                io.println(unicode.graphemeCount("🇨🇳👍🏽e\u{301}"))!;
                var bytes = [2]u8{192, 128};
                var invalidUtf8 = unicode.decodeUtf8(bytes[:]);
                var invalidPattern = regex.compile("[a-");
                var builder = regex.RegexBuilder("a{4}");
                var shortBuilder = builder.maxStates(2);
                var tooManyStates = shortBuilder.compile();
                var anotherBuilder = regex.RegexBuilder("a");
                var inputLimit = anotherBuilder.maxInputBytes(1);
                var limited = inputLimit.compile()!;
                var tooLong = limited.find("aa");
                io.println(core.isErr(raw invalidUtf8) && core.isErr(raw invalidPattern) &&
                           core.isErr(raw tooManyStates) && core.isErr(raw tooLong))!;
            }
        ''')
        self.assertEqual(self.run_program(program), "e\u0301\nAfi\nAe\u0301\n3\ntrue\n")

    def test_record_and_reports_transfer_owned_values(self):
        self.hosted("""
            import (coll, errors, log, text)

            func main() !void {
                var attrs = coll.Vec<log.Attr>();
                var nested = [1]log.Attr{log.Attr.bool("ready", true)!};
                attrs.push(log.Attr.group("stage", nested[:])!)!;
                var record = log.Record.fromOwned(log.Level.Warn, "checkpoint", move(attrs))!;
                var output = io.MemoryWriter();
                var handler = log.JsonHandler.new(raw output);
                handler.handle(raw record)!;
                handler.flush()!;
                var line = text.fromBytes(output.asSlice())!;
                io.print(line)!;

                const first = errors.Report.new("left")!;
                var cause = errors.Report.new("right")!;
                var combined = first.join(move(cause))!;
                var sink = errors.ErrorSink();
                sink.push(move(combined))!;
                var backlog = sink.drain();
                var report = backlog.next().unwrap();
                var formatted = report.formatString()!;
                io.print(formatted)!;
                io.println(first.message())!;
            }
        """, '{"level":"WARN","message":"checkpoint","attrs":{"stage":{"ready":true}}}\n'
             'multiple errors\n  left\n  right\nleft\n')

        for case, call in (
            ("record", 'var record = log.Record.fromOwned(log.Level.Info, "", attrs)!;'),
            ("cause", "first.addCause(second)!;"),
            ("sink", "sink.push(first)!;"),
            ("join", "var combined = errors.join(first, move(second))!;"),
        ):
            with self.subTest(case=case):
                source = self.source(case + "/main.vqr", """
                    package main
                    import (coll, errors, log)
                    func main() !void {
                        var attrs = coll.Vec<log.Attr>();
                        var first = errors.Report.new("first")!;
                        var second = errors.Report.new("second")!;
                        var sink = errors.ErrorSink();
                """ + call + "}")
                result = self.process(VAQC, "check", source, good=False)
                self.assertIn(b"move parameter requires move(object)", result.stderr)

    def test_log_and_error_nested_attributes_keep_order_and_ownership(self):
        self.hosted("""
            import (errors, log, text)
            error Failure { First, Second }

            func group() !log.Attr {
                var items = [2]log.Attr{
                    log.Attr.int("attempt", 2)!,
                    log.Attr.bool("retry", false)!,
                };
                log.Attr.group("request", items[:])
            }

            func aggregate(attrs: []const log.Attr) !errors.Report {
                var first = errors.fromError(Failure.First)!;
                var location = errors.SourceLocation("client.vqr", 4, 2, 4, 6, "call")!;
                first.addContextAt("request failed", move(location), attrs)!;
                var next = errors.fromError(Failure.Second)!;
                next.addContext("transport lost")!;
                first.addCause(move(next))!;
                var entries = [1]errors.Report{move(first)};
                errors.join(entries[:])
            }

            func main() !void {
                var writer = io.MemoryWriter();
                var handler = log.TextHandler.new(raw writer);
                var root = log.Logger.new(raw handler)!;
                var extra = [1]log.Attr{log.Attr.string("worker", "a")!};
                var logger = root.with(extra[:])!;
                var attrs = [1]log.Attr{group()!};
                logger.log(log.Level.Info, "ready", attrs[:])!;
                var rendered = text.fromBytes(writer.asSlice())!;
                io.print(rendered)!;

                var report = aggregate(attrs[:])!;
                io.println(report.rootError().unwrap() == Failure.Second)!;
                io.println(report.matches(Failure.First) && report.matches(Failure.Second))!;
                var diagnostic = report.formatString()!;
                io.print(diagnostic)!;
            }
        """, 'INFO "ready" "worker"="a" "request.attempt"=2 "request.retry"=false\n'
             'true\ntrue\n'
             'multiple errors\n'
             '  error [ErrorId]\n'
             '    context: request failed (client.vqr:4:2) "request.attempt"=2 "request.retry"=false\n'
             '    error [ErrorId]\n'
             '      context: transport lost\n')

    def test_utilities(self):
        output = self.example("examples/stdlib/utilities.vqr")
        lines = output.splitlines()
        self.assertEqual(lines[:12], [
            "https://example.com:443/b?tag=one&tag=two#top", "http://a/b/g", "2001:db8::1",
            "http://example.com:/", "https://example.com/api?q=a+b", "3", "one", "two", "a+b",
            "tag=three&plus=a%2Bb", "true", "true",
        ])
        self.assertEqual(lines[12:14], ["true", "1"])
        self.assertNotIn("filtered", output)
        self.assertIn("network.component", output)
        self.assertIn("client.vqr:12:5", output)
        self.assertIn("socket wait expired", output)
        self.assertIn("multiple errors", output)
        self.assertEqual(lines[-1], "true")

    def test_uri_resolution_vectors(self):
        vectors = {
            "g": "http://a/b/c/g", "./g": "http://a/b/c/g", "g/": "http://a/b/c/g/",
            "/g": "http://a/g", "//g": "http://g", "?y": "http://a/b/c/d;p?y",
            "../..": "http://a/", "../../../g": "http://a/g", "g/../h": "http://a/b/c/h",
            "/./g": "http://a/g", "/../g": "http://a/g", "g?y/../x": "http://a/b/c/g?y/../x",
            "/a//b": "http://a/a//b", "/a//../b": "http://a/a/b",
        }
        checks = "\n".join(f"io.println(base.resolve({json.dumps(ref)})!.toString()!)!;" for ref in vectors)
        program = self.source("references.vqr", f'''
            import (io, url)
            func main() !void {{
                var base = url.parse("http://a/b/c/d;p?q")!;
                {checks}
            }}
        ''')
        self.assertEqual(self.run_program(program).splitlines(), list(vectors.values()))


if __name__ == "__main__":
    unittest.main()
