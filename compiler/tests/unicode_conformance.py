"""Run the Unicode 17.0 normalization and grapheme conformance vectors."""

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile


def rows(path, sha256):
    contents = path.read_bytes()
    if hashlib.sha256(contents).hexdigest() != sha256:
        raise ValueError(f"unexpected checksum for {path}")
    for number, raw in enumerate(contents.decode("utf-8").splitlines(), start=1):
        row = raw.split("#", 1)[0].strip()
        if row and not row.startswith("@"):
            yield number, row


def utf8_hex(codepoints):
    return "".join(chr(int(cp, 16)) for cp in codepoints).encode("utf-8").hex()


def vectors(directory):
    norm = "5019ffd530751a741900c849c0e010332f142a3612234639bd200b82138a87db"
    for number, row in rows(directory / "NormalizationTest.txt", norm):
        columns = [utf8_hex(column.split()) for column in row.split(";")[:5]]
        yield f"n {number} " + " ".join(columns)
    grapheme = "e2d134d2c52919bace503ebb6a551c1855fe1a1faec18478c78fff254a1793ec"
    for number, row in rows(directory / "GraphemeBreakTest.txt", grapheme):
        clusters = [utf8_hex(group.replace("×", " ").split()) for group in row.split("÷") if group.strip()]
        yield f"g {number} {len(clusters)} {''.join(clusters)} " + " ".join(clusters)


CHECKER = r"""
import (bufio, coll, encoding, io, text, unicode)

func nextValue(input: *bufio.Scanner) !string {
    var hex = input.nextString()!.unwrap();
    var raw = encoding.hexDecode(hex)!;
    text.fromBytes(raw.asConstSlice())
}

func drain(normalizer: *unicode.StreamingNormalizer, output: *coll.Vec<u8>) !void {
    var scratch = [3]u8{0,0,0};
    while normalizer.available() != 0 {
        var count = normalizer.readOutput(scratch[:]);
        for byte in scratch[0..count] { output.push(byte)!; }
    }
}

func streaming(source: str, form: unicode.NormalForm) !string {
    var normalizer = unicode.StreamingNormalizer(form, 128);
    var output = coll.Vec<u8>();
    for scalar in text.chars(source) {
        normalizer.push(scalar)!;
        drain(raw normalizer, raw output)!;
    }
    normalizer.finish()!;
    drain(raw normalizer, raw output)!;
    text.fromBytes(output.asConstSlice())
}

func normalization(input: *bufio.Scanner) !bool {
    var raw = nextValue(input)!;
    var canonical = nextValue(input)!;
    var decomposed = nextValue(input)!;
    var compatibility = nextValue(input)!;
    var compatibilityDecomposed = nextValue(input)!;
    var values = [5]str{raw, canonical, decomposed, compatibility, compatibilityDecomposed};
    for i in usize(0)..5 {
        var canonicalExpected = if i < 3 { values[1] } else { values[3] };
        var decomposedExpected = if i < 3 { values[2] } else { values[4] };
        var forms = [4]unicode.NormalForm{unicode.NormalForm.NFC, unicode.NormalForm.NFD,
                                           unicode.NormalForm.NFKC, unicode.NormalForm.NFKD};
        var expected = [4]str{canonicalExpected, decomposedExpected, values[3], values[4]};
        for form in usize(0)..forms.len() {
            if unicode.normalize(values[i], forms[form])! != expected[form] ||
               streaming(values[i], forms[form])! != expected[form] {
                return false;
            }
        }
    }
    true
}

func grapheme(input: *bufio.Scanner) !bool {
    var count = input.readInt<usize>()!;
    var value = nextValue(input)!;
    var clusters = unicode.graphemes(value);
    var offset: usize = 0;
    for i in usize(0)..count {
        var expected = nextValue(input)!;
        var actual = clusters.next() ?? { return false; };
        if !text.equal(actual, expected) { return false; }
        if !unicode.isGraphemeBoundary(value, offset) { return false; }
        for skip in usize(1)..actual.byteLen() {
            if unicode.isGraphemeBoundary(value, offset + skip) { return false; }
        }
        offset += actual.byteLen();
    }
    clusters.next() == null && offset == value.byteLen() && unicode.isGraphemeBoundary(value, offset)
}

func main() !i32 {
    var source = io.stdin();
    var input = bufio.Scanner(raw source)!;
    for {
        var kind = input.nextString()! ?? { return 0; };
        var line = input.readInt<usize>()!;
        var good = if kind == "n" { normalization(raw input)! } else { grapheme(raw input)! };
        if !good {
            io.println(kind)!;
            io.println(line)!;
            return 1;
        }
    }
}
"""


def main(compiler, data):
    cases = list(vectors(data))
    with tempfile.TemporaryDirectory(prefix="vaqrin-unicode-") as directory:
        source = Path(directory) / "conformance.vqr"
        source.write_text(CHECKER, encoding="utf-8")
        executable = Path(directory) / "conformance"
        subprocess.run([compiler, "build", "-O2", source, "-o", executable], check=True, timeout=60)
        result = subprocess.run([executable], input="\n".join(cases), text=True,
                                capture_output=True, timeout=60)
        if result.returncode:
            raise SystemExit(f"Unicode conformance failed ({result.returncode}): {result.stdout}{result.stderr}")
    print(f"Passed {len(cases)} Unicode 17.0 conformance vectors")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]))
