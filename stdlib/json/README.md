# json

JSON targets RFC 8259. The DOM owns strings, arrays, objects and raw number
lexemes. Duplicate keys default to Reject; KeepFirst and KeepLast are explicit
alternatives. Parser limits cover input bytes, nesting depth and aggregate
container elements.

Number lexemes are never silently converted to f64. asI64 and asU64 accept
integral lexemes only. The current asF64 is intentionally conservative and
accepts only integer lexemes within the exactly representable binary64 integer
range. Decimal and exponent conversion remains pending a correctly rounded
decimal-to-binary implementation.

Compile and run the examples locally before relying on runtime behavior.
