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

## 固定回归样例

- `unicode.version()` 返回 `17.0.0`。
- `generalCategory('A')` 为 `Lu`，`generalCategory('中')` 为 `Lo`。
- `script('A')` 为 Latin，`script('中')` 为 Han，`script('Ж')` 为 Cyrillic。
- `isDigit('٣')` 为 true；`isWhitespace('\u{3000}')` 为 true。
- `caseFoldFull('ß')` 为 `ss`；`caseFold("Straße")` 为 `strasse`。
- `normalize("e\u{301}", NFC)` 为 U+00E9；`normalize(U+00E9, NFD)` 为 U+0065 U+0301。
- NFKC 将全角 A 映射为 ASCII A，并将兼容连字分解。
- Hangul syllable NFC/NFD 往返一致。
- 家庭 ZWJ emoji、肤色修饰符 emoji、国旗 RI 对均按预期计为字素簇。
