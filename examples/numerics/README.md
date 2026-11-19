# Numerics examples

Run the static check with the repository's current compiler:

```sh
vaqc check examples/numerics/main.vqr
```

For hosted ELF execution, link the target math library explicitly. For example, after locating the correct library for the active architecture/sysroot:

```sh
vaqc build examples/numerics/main.vqr --link-input /absolute/path/to/libm.so -o numerics-example
./numerics-example
```

The expected output is in `expected.txt`. It contains only deterministic integer, boolean, and rational results; the random sample itself is checked as a range predicate rather than printed.
