# Local Unicode / regex acceptance checklist

- [ ] ASCII properties and case mappings
- [ ] Greek, Cyrillic, Arabic, Devanagari, Han, and supplementary-plane scripts
- [ ] NFC/NFD canonical equivalence (for example U+0065 U+0301 and U+00E9)
- [ ] NFKC/NFKD compatibility mappings (fullwidth forms, ligatures, circled forms)
- [ ] Greek sigma, sharp s, dotted/dotless I case behavior
- [ ] Emoji modifier, variation selector, ZWJ family, flag RI pairs
- [ ] UTF-8 malformed/truncated/overlong/surrogate/out-of-range inputs
- [ ] GraphemeBreakTest.txt and NormalizationTest.txt Unicode 17.0 conformance
- [ ] Regex literals, escaping, classes, ranges, groups, alternation, quantifiers
- [ ] Greedy/lazy and bounded repetition
- [ ] Anchors and multiline behavior
- [ ] Capture boundaries and named captures
- [ ] Empty-match iteration and split/replace behavior
- [ ] Invalid patterns and explicit unsupported-construct errors
- [ ] Maximum state count, maximum input bytes, long adversarial inputs
