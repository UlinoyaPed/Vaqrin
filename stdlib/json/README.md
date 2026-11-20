# json

## API

- Value: Null, Bool, Number, String, Array, Object.
- JsonNumber: parse, text, isInteger, asI64, asU64, asF64.
- JsonArray: len, isEmpty, reserve, get, getMut, push, pop, insert, remove, replace, asSlice, iterRef.
- JsonObject: len, isEmpty, containsKey, get, getMut, insert, remove, keyAt, valueAt, valueAtMut.
- ParseOptions: maxDepth, maxBytes, maxElements, duplicateKeys.
- EncodeOptions: indent, maxDepth, escapeNonAscii, sortKeys.
- Parser, parse, parseBytes, parseReader, validate.
- Encoder, write, stringify, stringifyPretty, escapeString, unescapeString.
- pointerGet, pointerSet.
- JsonDomSerializer, JsonDomDeserializer, marshal<T>, unmarshal<T>.

JSON parsing validates RFC 8259 number grammar, UTF-8, control characters, escapes
and UTF-16 surrogate pairs. Duplicate object keys default to Reject; KeepFirst
and KeepLast are explicit options. Input bytes, nesting depth and aggregate
container elements are bounded.

JsonNumber preserves the exact source lexeme. Integer conversion is checked.
asF64 parses decimal and exponent lexemes as binary64 and retains the existing
integer-lexeme guard for values outside ±2^53. JSON serde writeF64 uses a
round-trippable scientific decimal representation and rejects NaN and
infinities.

Both write and Encoder.encodeValue apply the encoding options to the entire
value. By default, object keys follow insertion order and non-ASCII text is
written as UTF-8. sortKeys puts each object's keys in lexicographic order by
their original UTF-8 bytes. escapeNonAscii emits lowercase \uXXXX escapes in
keys and string values, using UTF-16 surrogate pairs for supplementary scalars.
The two options can be combined with indentation.

Each encodeValue call starts at depth zero. If encoding fails, the writer may
already contain a partial value. The caller is responsible for handling those
bytes before writing another document to the same destination.

parseReader reads a bounded document into memory before parsing it.
