# Vaqrin host I/O runtime

The first host port implements synchronous GNU/Linux file and directory operations.
The C ABI keeps OS handles opaque to Vaqrin and returns the native errno value through
an out parameter. The high bit of ErrorId marks a native system error; the low 31
bits preserve errno. This is an internal representation and must not be persisted.

