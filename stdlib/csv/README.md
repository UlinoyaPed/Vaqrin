# csv

The CSV reader is a byte-oriented state machine over the existing io.Reader
interface. It preserves empty and trailing fields, doubled quotes, quoted
CR/LF sequences, CRLF/LF record endings, and supports optional header-row
capture. It enforces field, record, and record-count limits while reading.
The writer escapes fields and emits the configured line ending.

The parser uses strict quote placement: a quote may only begin an empty field,
and after a closing quote only a delimiter, record terminator, or EOF is valid.
The implementation is intended to cover the common RFC 4180 dialect plus
configurable delimiter and line endings. It has not been runtime-tested in
Vaqrin; local acceptance is required for short reads, EOF and multiline cases.
