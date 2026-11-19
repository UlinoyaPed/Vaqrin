# csv

The package provides delimiter/quote options, record parsing, whole-document
parsing, field escaping, stringification and Reader/Writer wrappers. Parsing
preserves empty and trailing fields and supports doubled quotes and embedded
newlines in the whole-document parser.

The current CsvReader is record-buffered and requires a physical line ending
to delimit a record; quoted multiline records require the whole-document
parser. Header metadata is reserved but header-row promotion is not yet wired.
This package is not yet a complete RFC 4180 implementation.
