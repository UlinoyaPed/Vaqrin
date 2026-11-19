# Numerics examples

Run the static check with the repository's current compiler:

```sh
vaqc check examples/numerics/main.vqr
```

On hosted Linux, `vaqc` links the target math library when the program uses it.

```sh
vaqc build examples/numerics/main.vqr -o numerics-example
./numerics-example
```

The expected output is in `expected.txt`. It contains only deterministic integer, boolean, and rational results; the random sample itself is checked as a range predicate rather than printed.
