# toml

## API

- Value: String, Integer, Float, Bool, DateTime, Array, Table.
- Document: len, get, getMut, set, insertUnique, remove, keyAt, valueAt,
  getPath, setPath, stringify.
- TomlOptions: maxDepth, maxBytes, maxEntries.
- parse, parseWithOptions, parseReader, stringify, get, asF64.
- TomlDomSerializer, TomlDomDeserializer, encode<T>, decode<T>.

The implemented subset supports basic and literal single-line strings, Unicode
escapes in basic strings, booleans, signed decimal/hex/octal/binary integers,
decimal floats retained as their exact source lexeme, local dates, local times,
offset date-times, arrays, inline tables, dotted keys, nested tables, comments,
duplicate-key checks, and bounded input/depth/entry counts.

Float values and date/time values retain their original text. asF64 converts
integer and decimal values, including underscored decimals and TOML's signed
infinity and NaN forms. The serde adapters read and write f64 values. Finite
values use a round-trippable scientific decimal representation and can be
stored inside a root table. Optional and date-time codecs remain pending.

This is not a complete TOML 1.0 implementation. Multiline strings, multiline
arrays, arrays of tables, full table redefinition semantics, and all edge cases
of the specification remain unsupported. Do not advertise full TOML 1.0
conformance.
