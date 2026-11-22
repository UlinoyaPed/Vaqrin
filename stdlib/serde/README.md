# serde

The serde package provides format-independent Serializer, Deserializer,
`Encode<T>`, `Decode<T>`, and `Codec<T>` interfaces. Use an explicit codec or
derive one for a record with a fixed field list and no custom initializer.

Import `serde`, add `#derive: serde` to the class, and use
`serde.DerivedCodec<MyType>`. The generated methods encode by field name and
decode in any field order. Required fields must appear once. A missing
optional field becomes null. Unknown fields produce an error. A field's
`#serde_name: "wire-name"` overrides its name in the encoded data and in
`serdeFieldName(index)`. `serdeFieldCount()` reports the number of fields.
Derivation supports primitive values, nested derived records, generic record
specializations and direct optional fields. Explicit codecs remain available
for custom layouts and construction rules.

Primitive codecs cover bool, signed and unsigned integer types, float types,
string and char. OptionalCodec<T>, SequenceCodec<T>, and StringMapCodec<T>
provide reusable composition. Sequence and map codecs use Vec-backed values.

DecodeLimits and DecodeBudget provide shared depth, element and byte counters.
DecodeBudget is an explicit helper; the current JSON and TOML DOM adapters do
not automatically apply it.

JSON and TOML provide DOM-backed Serializer and Deserializer adapters. The
`cbor` package provides binary Encoder and Decoder adapters. JSON
preserves raw numeric lexemes and supports f64 serialization and conversion.
TOML supports f64 conversion and has no null value or date-time codec.
ByteSerializer and ByteDeserializer are extension interfaces.
The standard library acceptance suite exercises the JSON and TOML adapters.
