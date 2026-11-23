# Numerics implementation notes

Target branch: `feature/numerics`. Base compiler and standard library: Vaqrin 0.1.3 master. No compiler, backend, linker, I/O, filesystem, collection, or hash source is modified by this package work.

## Package layout

- `stdlib/num/limits.vqr`
- `stdlib/num/checked.vqr`
- `stdlib/num/bigint.vqr`
- `stdlib/num/rational.vqr`
- `stdlib/math/math.vqr`
- `stdlib/math_ffi/ffi.vqr` (internal C ABI declarations used by math)
- `stdlib/random/random.vqr`

## num

The compiler exposes integer parsing/type-width intrinsics for fixed-width integer types up to 128 bits. It does not provide a type-level `Unsigned<T>` mapping, so `absDiff<T>` returns u128. `IntLimits<T>` and `FloatInfo<T>` expose the target's existing fixed-width integer and IEEE binary32/binary64 format properties.

Checked, wrapping and saturating operations are implemented for fixed-width integers. Division checks zero and the signed minimum divided by -1 case. Euclidean division adjusts the truncating quotient/remainder to ensure a nonnegative remainder. Signed minimum absolute value is reported as overflow. `gcd<T>` reports overflow if the nonnegative gcd cannot fit in signed T (for example gcd(min, 0)); `lcm` similarly reports unrepresentable magnitudes.

`sum` and `product` use checked fixed-width operations. `average` accumulates and divides with BigInt so the intermediate sum does not overflow; its final integer result is truncated toward zero.

BigUint stores normalized little-endian base-2^32 limbs in `coll.Vec<u32>`. Zero is an empty limb vector. Addition/subtraction use carry/borrow; multiplication is schoolbook O(n*m); division uses bitwise long division; parsing/formatting use repeated small-radix multiply/divide. Capacity errors propagate from Vec. Byte conversion uses unsigned magnitude bytes, minimal length, and explicit endian.

BigInt stores sign {-1,0,+1} and a BigUint magnitude. It accepts all current fixed-width integer types through `fromInteger<T>`, and parses signed radix text. Division truncates toward zero and remainder follows the dividend sign. Arithmetic right shift rounds negative values toward negative infinity. `toBytes`/ `toTwosComplementBytes` use minimal two's-complement bytes; `fromTwosComplementBytes` reverses that representation. The BigInt implementation is not a constant-time cryptographic integer.

Rational<T> is a fixed-width integer rational. It converts operands to BigInt for arithmetic and comparison, reduces with a BigInt Euclidean gcd, normalizes the denominator to positive, and converts the final numerator/denominator back to T. Thus intermediate cross-products do not overflow T; the final reduced result can still report overflow if it cannot fit T. Denominator zero is rejected.

## math

The compiler currently provides no standard-library-level direct math intrinsic set. `math_ffi` therefore declares the target C math ABI, and `math` wraps those symbols. f32 calls use the C float functions and f64 calls use the C double functions. Classification, NaN, infinity, signed zero and signbit are determined from IEEE bit fields, avoiding comparisons that can quiet or misclassify NaNs.

Rounding follows the C/IEEE conventions exposed by the platform: `round` rounds halfway cases away from zero; `roundEven` implements ties-to-even explicitly; `remainder` follows the nearest-integer quotient rule; `fmod` follows truncating quotient semantics. Elementary and special-function domain/pole behavior follows the target C math library, including its NaN/infinity results. The wrappers do not translate errno or floating-point exception flags into Vaqrin errors.

The driver does not automatically link arbitrary extern C references. For hosted Linux executables and shared libraries that reference `math_ffi`, the driver passes `-lm` to the target linker. Static library consumers supply their own final link dependencies. Other targets can use `--link-input` if the target toolchain requires a separate math library. Apple platforms commonly resolve these symbols through libSystem. Verify the corresponding CRT/math provider on Windows. The POSIX Bessel symbols `j0`, `j1`, `y0`, and `y1` are not ISO C functions and may be unavailable on non-POSIX targets.

Complex division scales the denominator before forming its squared magnitude to reduce avoidable overflow. Interval supports basic closed-interval queries and arithmetic, but uses ordinary round-to-nearest operations. It does not round bounds outward and must not be used as a rigorous enclosure or proof system.

## random

Pcg64 implements PCG XSL-RR 128/64: a 128-bit LCG state, multiplier `0x2360ed051fc65da44385df649fccf645`, odd increment `(stream << 1) | 1`, and XSL-RR output rotation from the old state. `seed(u64)` uses the supplied value as initial state with a fixed stream constant. `fromSeed(Seed)` combines the two 64-bit Seed words into a 128-bit initial state and derives the stream from both words. `jump(distance)` advances the LCG by the requested number of steps using exponentiation of its affine transition. `fill` emits each u64 least-significant byte first.

Xoshiro256 implements xoshiro256**. Its 32-byte seed is interpreted as four little-endian u64 state words; the all-zero state is rejected. `jump` and `longJump` use the published 2^128 and 2^192 jump polynomials. `fill` emits little-endian bytes.

`Seed.fromU64` uses SplitMix64 mixing. `seedFromBytes` uses deterministic FNV-1a accumulators followed by SplitMix64 expansion. These are seed-expansion utilities, not cryptographic derivation functions.

Both PRNGs are deterministic and non-cryptographic. `newDefault()` returns a generator initialized with a fixed constant seed; it does not query the clock or OS entropy. `CryptoRng` is only an interface contract; no OsRng, secureBytes, or operating-system entropy adapter is implemented. Do not use these generators for keys, tokens, nonces, passwords, or other secrets.

Integer ranges are half-open. u64 and i64 samplers use rejection sampling to remove modulo bias; the signed sampler maps signed order into unsigned order before sampling. The current interface cannot express an exclusive upper bound of 2^64, so it cannot represent the full u64 domain in one call. Floating ranges are half-open for f32/f64; f32 uses 24 random mantissa bits and f64 uses 53. If final floating rounding reaches the excluded high endpoint, the sampler steps one representable value toward low.

UniformInt currently supports integer T up to 64 bits; the generic unsigned counterpart mapping needed for arbitrary 128-bit ranges is not available. UniformFloat supports f32/f64. Normal uses Box-Muller with a cached second variate; Exponential uses the inverse CDF; WeightedIndex uses cumulative weights and binary search. Slice sample uses partial Fisher-Yates; reservoirSample supports an iterable input and the standard reservoir replacement rule. randomString selects Unicode scalar values, not grapheme clusters.

## Static review status

This branch has received source-level algorithm, ownership, and boundary review only. The online GitHub editing environment cannot invoke `vaqc`, so no claim is made that the new files parse, type-check, pass LLVM verification, link, or run. The review also cannot validate target-specific libm symbol availability.

Before local integration, pay particular attention to compiler diagnostics around generic instantiation, generic numeric casts, interface conformance, static class calls, and the target's C ABI. Run the acceptance examples, compare BigUint/BigInt against Python arbitrary-precision integers, compare PCG and xoshiro vectors against independent reference implementations, and test f32/f64 math on the actual target.

## Local acceptance

1. Run `vaqc check examples/stdlib/numerics/main.vqr` using the current compiler.
2. Build the example with `vaqc build examples/stdlib/numerics/main.vqr -o numerics-example` on hosted Linux.
3. Compare BigUint add/sub/mul/divMod/shifts/parse/bytes against Python integers, including zero, carries, borrow chains, and division by one-limb and multi-limb divisors.
4. Test BigInt conversion at i8/i64/i128 minima and maxima, negative zero parsing, truncating division, arithmetic right shift, and signed byte round-trips.
5. Test Rational negative denominators, zero numerator, denominator minimum, exact reduction, cross-product overflow avoidance, and final-result overflow.
6. Test checked arithmetic at min/max, zero, min/-1, signed/unsigned multiplication quadrants, and saturating endpoints.
7. Compare PCG64 and xoshiro256** output and jump vectors with independent reference implementations; verify repeatability after identical seeds.
8. Test range sampling at width 1, powers of two, awkward widths, near signed/unsigned limits, and f32/f64 adjacent endpoints.
9. Test math classification for positive/negative zero, subnormals, finite maxima, infinities, quiet NaNs, and signed NaNs; test rounding ties and C libm domain/pole cases.
10. Run sanitizer builds and allocation-failure tests once the local compiler/runtime toolchain is available.
