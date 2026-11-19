# serde

The serde package defines a format-independent data model protocol. Format
packages implement Serializer and Deserializer; user types implement Codec<T>
explicitly.

The protocol supports null, booleans, signed and unsigned integers,
floating-point values, strings, sequences, and string-keyed maps.
OptionalCodec<T>, SequenceCodec<T>, and StringMapCodec<T> provide reusable
implementations. SignedIntegerCodec<T>, UnsignedIntegerCodec<T>, and
FloatCodec<T> cover numeric primitive types through checked conversions.

There is no automatic reflection or derive support. A user-defined class must
supply its own codec. DecodeBudget provides shared depth, element, and byte
accounting for concrete decoders.

ByteSerializer and ByteDeserializer are optional byte-backed adapters. They
keep byte collection separate from the core streaming protocol.
