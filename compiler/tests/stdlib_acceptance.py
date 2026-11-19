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
            self.example("examples/numerics/main.vqr", "--link-input", self.math_library()),
            (REPOSITORY / "examples/numerics/expected.txt").read_text(encoding="utf-8"),
        )

    def math_library(self):
        return self.process("cc", "-print-file-name=libm.so").stdout.decode().strip()

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
        self.assertEqual(self.run_program(program, "--link-input", self.math_library()), "true\ntrue\n")

    def test_json(self):
        lines = self.example("stdlib/json/examples/json_smoke.vqr").splitlines()
        self.assertEqual(lines[0], "true")
        expected = {"ok": True, "items": [1, 2, {"name": "Vaqrin"}], "rocket": "🚀"}
        self.assertEqual(json.loads(lines[1]), expected)
        self.assertEqual(json.loads("\n".join(lines[2:-5])), expected)
        self.assertEqual(lines[-5:], ["true", "true", "1", "true", "true"])

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
