"""Run the examples for the numeric, data, Unicode and utility packages."""
import json
import subprocess
import unittest

from acceptance import CompilerCase, REPOSITORY, VAQC


class StandardLibraryAcceptance(CompilerCase):
    def example(self, source, *flags):
        return self.run_program(REPOSITORY / source, *flags)

    def run_program(self, source, *flags):
        compiled = subprocess.run(
            [VAQC, "build", source, *flags, "-o", "example"],
            cwd=self.root,
            capture_output=True,
            timeout=120,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stderr.decode(errors="replace"))
        return self.process("./example").stdout.decode()

    def test_numerics(self):
        self.assertEqual(
            self.example("examples/numerics/main.vqr"),
            (REPOSITORY / "examples/numerics/expected.txt").read_text(encoding="utf-8"),
        )

    def test_integer_arithmetic_edges(self):
        program = self.source("integers.vqr", '''
            import (core, io, num)
            func main() !void {
                var overflow = num.checkedAdd<i64>(num.maxValue<i64>(), 1);
                var zero = num.checkedDiv<i64>(1, 0);
                var minimum = num.checkedDiv<i64>(num.minValue<i64>(), -1);
                io.println(core.isErr(&overflow) && core.isErr(&zero) && core.isErr(&minimum))!;
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
                    var offset = random.rangeInt(&rng, -3i64, 4i64)! + 3i64;
                    bins[usize(offset)!]++;
                }
                for count in bins { if count < 800 || count > 1200 { panic("uniform smoke failed"); } }
                var bad = random.rangeInt(&rng, 2u64, 2u64);
                io.println(core.isErr(&bad))!;
                io.println(math.isNan<f64>(math.sqrt<f64>(-1.0)))!;
            }
        ''')
        self.assertEqual(self.run_program(program), "true\ntrue\n")

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
                var scanner = bufio.newScanner(&input)!;
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
                var wire = json.stringify(&jsonValue)!;
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
                io.println(toml.asF64(&infinity)! == -math.inf<f64>())!;
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
                snapshot.remove(key)!;
                io.println(!snapshot.contains(key))!;
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
        """, "true\n" * 10)

    def test_json(self):
        lines = self.example("stdlib/json/examples/json_smoke.vqr").splitlines()
        self.assertEqual(lines[0], "true")
        expected = {"ok": True, "items": [1, 2, {"name": "Vaqrin"}], "rocket": "🚀"}
        self.assertEqual(json.loads(lines[1]), expected)
        self.assertEqual(json.loads("\n".join(lines[2:-5])), expected)
        self.assertEqual(lines[-5:], ["true", "true", "1", "true", "true"])

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
                json.write(&compact, &value, options)!;
                io.println(text.fromBytes(compact.asSlice())!)!;

                options.sortKeys = true;
                options.indent = 2;
                var pretty = io.MemoryWriter();
                var encoder = json.Encoder(&pretty, options);
                encoder.encodeValue(&value)!;
                encoder.flush()!;
                io.println(text.fromBytes(pretty.asSlice())!)!;
                io.println(json.stringify(&value)!)!;
            }
        ''', expected)

    def test_json_encoder_reuse_after_depth_error(self):
        self.hosted('''
            import (json, text)

            func main() !void {
                var options = json.EncodeOptions();
                options.maxDepth = 1;
                var output = io.MemoryWriter();
                var encoder = json.Encoder(&output, options);
                var nested = json.parse("[[]]")!;
                encoder.encodeValue(&nested) catch (reason) {
                    if reason != json.JsonError.DepthLimit { return reason; }
                    io.println("depth limit")!;
                };
                var shallow = json.parse("[]")!;
                encoder.encodeValue(&shallow)!;
                io.println(text.fromBytes(output.asSlice())!)!;
            }
        ''', "depth limit\n[[]\n")

    def test_csv(self):
        lines = self.example("stdlib/csv/examples/csv_smoke.vqr").splitlines()
        row = 'alpha,"contains,comma","line one\nline two"\r\n'
        self.assertEqual(lines, ["2", "3", '"value,with,commas"', str(len(row))])

    def test_toml(self):
        lines = self.example("stdlib/toml/examples/toml_smoke.vqr").splitlines()
        self.assertEqual(lines[:2], ["7", "8080"])
        self.assertIn("ratio = 1.25", lines)
        self.assertIn("when = 1979-05-27T07:32:00Z", lines)
        self.assertEqual(lines[-2:], ["true", "true"])

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
        self.assertEqual(self.example("examples/unicode-basic.vqr"), "é\n17.0.0\n")

    def test_regex(self):
        self.assertEqual(self.example("examples/unicode-regex.vqr"), "é\ntrue\n1\nabab\nab\n3\n")

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
                io.println(core.isErr(&invalidUtf8) && core.isErr(&invalidPattern) &&
                           core.isErr(&tooManyStates) && core.isErr(&tooLong))!;
            }
        ''')
        self.assertEqual(self.run_program(program), "e\u0301\nAfi\nAe\u0301\n3\ntrue\n")

    def test_utilities(self):
        output = self.example("examples/utilities.vqr")
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
