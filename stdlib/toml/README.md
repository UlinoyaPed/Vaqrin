# toml

## API

- Value: String, Integer, Float, Bool, DateTime, Array, Table.
- Document: len, get, getMut, set, insertUnique, remove, keyAt, valueAt,
  getPath, setPath, stringify.
- TomlOptions: maxDepth, maxBytes, maxEntries.
- parse, parseWithOptions, parseReader, stringify, get, asF64.
- TomlDomSerializer, TomlDomDeserializer, encode<T>, decode<T>.

The parser accepts basic and literal strings in single-line and multiline forms,
booleans, decimal and base-prefixed integers, floats, date and time values,
multiline arrays, inline tables, dotted keys and arrays of tables. Table headers
resolve through the latest element of a table array. The parser checks duplicate
keys, table redefinition, and input, depth and entry limits.

Float values and date/time values retain their original text. asF64 converts
integer and decimal values, including underscored decimals and TOML's signed
infinity and NaN forms. The serde adapters read and write f64 values. Finite
values use a round-trippable scientific decimal representation and can be
stored inside a root table. Optional and date-time codecs remain pending.

CI checks acceptance and rejection against the TOML 1.0.0 fixture
manifest from `toml-lang/toml-test`. The standard-library acceptance
suite checks representative decoded values and round trips through the
DOM adapters.
