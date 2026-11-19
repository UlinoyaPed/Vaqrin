# toml

This is an explicitly partial TOML reader. The initial subset supports basic
key/value assignments, one-level table headers, basic quoted strings, booleans,
decimal signed integers, and date-like values retained as source strings.
Duplicate keys and duplicate table declarations are rejected.

The implementation does not claim TOML 1.0 conformance. Multiline strings,
literal strings, dotted/quoted keys, inline tables, arrays of tables, full
numeric grammar, floats, local date/time variants, comments within values,
Unicode escape forms and complete table redefinition rules remain unsupported.
The current array parser is intentionally limited and should be treated as
pending local validation.
