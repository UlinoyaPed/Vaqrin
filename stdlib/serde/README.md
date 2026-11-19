# serde

The serde package provides format-independent Serializer, Deserializer,
Encode<T>, Decode<T>, and Codec<T> interfaces. Implement Codec<T> explicitly
for application-defined types; there is no reflection or derive support.

Primitive codecs cover bool, signed and unsigned integer types, float types,
string and char. OptionalCodec<T>, SequenceCodec<T>, and StringMapCodec<T>
provide reusable composition. Sequence and map codecs use Vec-backed values.

DecodeLimits and DecodeBudget provide shared depth, element and byte counters.
DecodeBudget is an explicit helper; the current JSON and TOML DOM adapters do
not automatically apply it.

JSON and TOML provide DOM-backed Serializer and Deserializer adapters. JSON
preserves raw numeric lexemes and currently rejects f64 serialization and
non-integral f64 conversion. TOML does not support null, floating-point
conversion, or date-time codecs. ByteSerializer and ByteDeserializer are
extension interfaces; concrete byte adapters are not yet supplied.

All runtime behavior remains pending local compilation and acceptance.
