# Numerics implementation notes

Target: Vaqrin 0.1.3. This branch does not modify the compiler or linker.

## Source groups
- num: integer limits, checked/wrapping/saturating arithmetic, BigUint, signed-magnitude BigInt, fixed-width Rational.
- math: floating classification/constants, C math ABI wrappers, Complex and Interval.
- random: PCG-XSH-RR (32-bit output composed as u64), xoshiro256**, range sampling, shuffle and slice sampling.

## Semantics and limitations
The existing compiler's integer parsing intrinsics support integer types up to 128 bits. There is no generic type-level signed/unsigned counterpart mapping, so absDiff returns u128. BigUint stores little-endian base-2^32 limbs and represents zero as an empty vector. BigInt uses sign {-1,0,+1} plus magnitude; division truncates toward zero and remainder follows the dividend sign. Rational<T> is fixed-width: checked intermediate products can overflow before mathematical reduction.

Math wrappers call the target C math ABI. Link consumers with the platform math library (typically -lm on hosted ELF), using vaqc --link-input. The current driver does not infer arbitrary extern C libm references. f32 calls are promoted to f64, evaluated by the C double routine, then narrowed; this is not a native binary32 libm path. Domain/pole behavior follows target libm. No fast-math assumptions are made. Extended special functions, fully directed-rounding interval arithmetic and total-order helpers remain unimplemented.

Pcg64 is named for API continuity but currently implements PCG-XSH-RR with a 64-bit LCG state and 32-bit outputs; nextU64 concatenates two outputs. It is not PCG XSL-RR 128/64. Xoshiro256 uses xoshiro256** and published jump polynomials. Both are deterministic, non-cryptographic generators. OsRng/CryptoRng are intentionally absent: no verified cross-platform OS entropy source exists in current master. Never use these PRNGs for secrets.

Integer ranges are half-open and use rejection sampling. Signed i64 ranges map signed ordering into unsigned ordering. Floating ranges target [low, high), with nextAfter guarding a rounded upper endpoint. Reservoir sampling currently accepts slices, not arbitrary streaming iterators.

## Static review status
Source-level inspection only. The online editing environment cannot invoke vaqc, so parsing, type checking, LLVM verification, linking and execution have not been verified. Pay particular attention in local validation to generic inference, module imports, pointer receivers, casts near 128-bit limits, and syntax diagnostics.

## Local acceptance
1. Run vaqc check on examples.
2. Build/run on hosted Linux with libm linked.
3. Compare BigUint operations with Python integers.
4. Test BigInt signs, zero, multi-limb carries, division and two's-complement bytes.
5. Test Rational normalization, zero denominator and intermediate overflow.
6. Compare PRNG outputs with independent reference vectors.
7. Test integer sampling at width 1, powers of two, awkward widths and near u64 limits.
8. Run sanitizers for ownership and allocation after compiler support is available.
