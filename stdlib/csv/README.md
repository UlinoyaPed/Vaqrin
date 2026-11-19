# csv

## API

- CsvOptions: delimiter, quote, lineEnding, hasHeader, maxFieldBytes,
  maxRecordBytes, maxInputBytes, maxRecords.
- CsvReader: init, readRecord, readFields, headers, position, close.
- CsvWriter: init, writeRecord, writeAll, flush.
- parseRecord, parse, stringify, escapeField.

CsvReader is a byte-oriented state machine over the existing io.Reader. It
preserves empty and trailing fields, doubled quotes, quoted CR/LF sequences,
and CRLF/LF record endings. When hasHeader is enabled, the first record is
captured by headers() and omitted from readRecord results. Limits are enforced
while reading and before writing a record.

Quote placement is strict: a quote may only begin an empty field; after a
closing quote, only a delimiter, record terminator, or EOF is valid. The
implementation has not been compiled or run locally. Test short reads, EOF,
multiline fields, and maxInputBytes boundaries before integration.
