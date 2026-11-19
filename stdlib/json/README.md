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
The current asF64 deliberately accepts only integer lexemes within the exact
binary64 integer range; decimal and exponent conversion is not implemented.
JSON serde writeF64 is currently unsupported. escapeNonAscii and sortKeys are
reserved options and return UnsupportedOption rather than being ignored.

parseReader currently reads a bounded document before parsing; it is not a
token-streaming parser. The parser and encoder have not been compiled or run
locally; use the acceptance examples after building Vaqrin.
