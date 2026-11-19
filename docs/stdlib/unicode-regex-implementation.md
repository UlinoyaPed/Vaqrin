# Unicode and regex implementation status

Branch: `feature/unicode-regex`

## Unicode data

The generated tables target Unicode 17.0.0 and are derived from the official
Unicode Character Database release at https://www.unicode.org/Public/17.0.0/ucd/.
The generator is `stdlib/unicode/tools/generate.mjs`. It downloads versioned UCD
files and emits property ranges, simple case mappings, canonical/compatibility
decompositions, canonical composition pairs, full case-fold mappings, script
IDs, grapheme-break classes, and Indic_Conjunct_Break values. The data license
and attribution are in `stdlib/unicode/data/UNICODE-LICENSE.txt`.

Generated data currently contains 5,623 packed property ranges, 2,989 simple
case mapping entries, 5,914 decomposition entries (8,740 scalars), 961
composition pairs, 1,585 full case-fold mappings (1,705 scalars), and 176
Script values.

## Implemented foundations

- Unicode property lookup and General_Category / Script mapping.
- Alphabetic, decimal digit, White_Space, control, case, XID, Emoji,
  Extended_Pictographic and combining-mark properties.
- Simple lower/upper/title mappings and full per-scalar case folding.
- Strict UTF-8 scalar decoding and encoding with overlong, surrogate,
  continuation, and out-of-range checks.
- Recursive canonical/compatibility decomposition, canonical combining-class
  ordering, Hangul decomposition/composition, and canonical composition data.
- Grapheme-break property lookup and a basic UAX #29 segmenter surface.
- Regex instruction representation, bounded epsilon-closure VM structure,
  UTF-8 scalar stepping, byte-offset match representation, and empty-match
  iterator advancement.

## Known incomplete work (do not treat as release-ready)

The Unicode normalizer and grapheme implementation still need conformance
validation against the complete Unicode 17.0 NormalizationTest.txt and
GraphemeBreakTest.txt suites. In particular, the current grapheme segmenter
does not yet implement all contextual UAX #29 rules (including complete
GB11 and GB9c state tracking), and the backwards grapheme iterator is absent.
String-level case folding currently exposes per-scalar mapping only; contextual
SpecialCasing rules are not implemented.

The regex parser/compiler is not complete. Character-class descriptors,
quantifier compilation, alternation/group compilation, capture Save slots,
named captures, non-greedy ordering, multiline anchor semantics, and robust
diagnostics still require implementation. The VM's class predicate is a
placeholder and capture execution currently returns only the whole match.
Consequently the regex package must be considered an implementation scaffold,
not a usable complete regex engine. Unsupported constructs must not be silently
accepted.

## Static review notes

- The NFA VM is iterative and uses explicit state sets rather than recursive
  backtracking.
- UTF-8 input is stepped by decoded Unicode scalar widths; findAt rejects
  continuation-byte offsets.
- Empty matches advance by one UTF-8 scalar in FindIter.
- Input and state limits are represented in the public builder/API shape.
- Runtime compilation and acceptance execution have not been run in this
  environment. The repository compiler cannot be invoked through the GitHub
  source connector. Run the local acceptance instructions below after merging.

## Local validation plan

1. Run `node stdlib/unicode/tools/generate.mjs` with network access and compare
   generated output to the checked-in generated files.
2. Build/check all `.vqr` sources in `stdlib/unicode` and `stdlib/regex`.
3. Run Unicode 17.0 `NormalizationTest.txt` conformance cases for NFC/NFD/
   NFKC/NFKD, including Hangul, compatibility characters, and blocked
   composition.
4. Run Unicode 17.0 `GraphemeBreakTest.txt`, including CRLF, combining marks,
   emoji ZWJ sequences, RI parity, Prepend, SpacingMark and Indic conjuncts.
5. Add UTF-8 rejection cases for truncated sequences, stray continuations,
   overlong encodings, surrogate encodings and values above U+10FFFF.
6. Once the regex parser is completed, test literals, escapes, classes/ranges,
   groups, alternation, greedy/lazy and bounded repeats, anchors, flags,
   captures, empty matches, invalid syntax, large inputs and state limits.
