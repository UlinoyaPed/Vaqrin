# CBOR

Use `marshal` and `unmarshal` with a `serde.Codec<T>`, or use `Encoder` and
`Decoder` directly. The adapter supports null, booleans, the two CBOR integer
major types, IEEE floats, UTF-8 text, arrays, and text-keyed maps. It can read
and write indefinite-length arrays and maps. The encoder emits binary64
floats, and the decoder accepts binary16, binary32, and binary64.

The serde adapter rejects byte strings, tags, and indefinite-length text.
Maps follow each codec's field policy. Pass `serde.DecodeLimits` to the
constructors to bound bytes, nesting depth, and values.
