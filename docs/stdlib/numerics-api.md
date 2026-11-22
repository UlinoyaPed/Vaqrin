# Vaqrin 数值标准包公开 API

本清单对应 `feature/numerics` 当前源码。所有可失败操作使用 Vaqrin 的 `!T` 结果通道。

## num

**错误类型：** `NumError`

成员：`Overflow`、`Underflow`、`DivisionByZero`、`InvalidRadix`、`InvalidDigit`、`InvalidSyntax`、`NegativeValue`、`ZeroDenominator`、`UnsupportedType`。

**类型信息**

- `isSigned<T>() -> bool`（comptime）
- `minValue<T>() -> T`、`maxValue<T>() -> T`（comptime）
- `IntLimits<T>`: `bits`、`signed`、`min`、`max`
- `FloatInfo<T>`: `bits`、`precision`、`exponentBits`、`minExponent`、`maxExponent`、`minNormal`、`minSubnormal`、`maxSubnormal`、`maxFinite`、`epsilon`、`radix`、`hasInfinity`、`hasNaN`
- `floatInfo<T>() -> FloatInfo<T>`

**整数运算**

- `checkedAdd<T>(a,b) -> !T`
- `checkedSub<T>(a,b) -> !T`
- `checkedMul<T>(a,b) -> !T`
- `checkedDiv<T>(a,b) -> !T`
- `checkedNeg<T>(value) -> !T`
- `wrappingAdd<T>(a,b) -> T`
- `wrappingSub<T>(a,b) -> T`
- `wrappingMul<T>(a,b) -> T`
- `saturatingAdd<T>(a,b) -> T`
- `saturatingSub<T>(a,b) -> T`
- `saturatingMul<T>(a,b) -> T`
- `abs<T>(value) -> !T`
- `absDiff<T>(a,b) -> u128`
- `signum<T>(value) -> T`
- `divMod<T>(a,b) -> !(T,T)`
- `divEuclid<T>(a,b) -> !T`
- `remEuclid<T>(a,b) -> !T`
- `gcd<T>(a,b) -> !T`
- `lcm<T>(a,b) -> !T`
- `powInt<T>(base,exponent: usize) -> !T`
- `midpoint<T>(a,b) -> T`
- `sum<T>(values: []const T) -> !T`
- `product<T>(values: []const T) -> !T`
- `average<T>(values: []const T) -> !?T`

这些运算函数面向整数 T。语言当前没有 `Unsigned<T>` 类型级映射，因此 `absDiff` 使用 u128 返回；对最小有符号值求绝对值、gcd 结果无法装入 T 等情况会返回错误。

**BigUint**

`BigUint` 是不可隐式复制的动态精度无符号整数。

- 构造：`zero()`、`fromU64(u64)`、`fromU128(u128)`、`fromUnsigned<T>(T)`、`fromBytes([]const u8, Endian)`、`parse(str, radix)`、`parseBytes([]const u8, radix)`
- 查询：`isZero()`、`limbCount()`、`bitLength()`、`compare(*const Self)`
- 算术：`add`、`sub`、`mul`、`divMod`、`div`、`rem`、`shl`、`shr`
- 输出：`toBytes(Endian)`、`toText(radix)`、`toU64()`、`toU128()`
- `clone() -> !Self`

`sub` 在结果为负时返回 Underflow。零值使用空 limb 向量规范表示；`toBytes` 将零编码为单字节 0。

**BigInt**

`BigInt` 是符号-幅值表示的动态精度有符号整数。

- 构造：`zero()`、`fromI64(i64)`、`fromInteger<T>(T)`、`fromTwosComplementBytes([]const u8, Endian)`、`parse(str, radix)`
- 查询：`isZero()`、`isNegative()`、`compare(*const Self)`
- 算术：`add`、`sub`、`mul`、`divMod`、`div`、`rem`、`shl`、`shr`、`abs`、`gcd`
- 符号：`negate() -> !Self`、`negated() -> !Self`、`negateInPlace() -> void`
- 转换：`toInteger<T>()`、`toI64()`、`toU64()`、`toText(radix)`、`toBytes(Endian)`、`toTwosComplementBytes(Endian)`
- `clone() -> !Self`

除法向零截断，余数与被除数同号。算术右移对负数采用向负无穷方向扩展的语义。字节接口使用最短二进制补码编码。

**Rational<T>**

- `new(numerator, denominator) -> !Self`
- `numerator() -> T`、`denominator() -> T`
- `add`、`sub`、`mul`、`div`、`compare`

分母始终为正且非零。运算使用 BigInt 中间值，约分后检查最终分子和分母是否可由 T 表示。

## math

**类型：** `FpClass`（Nan、Infinite、Zero、Subnormal、Normal）、`MathError`（Domain、Pole、InvalidInterval、Overflow、DivisionByZero）。

**常量：** `pi<T>()`、`tau<T>()`、`e<T>()`、`sqrt2<T>()`、`ln2<T>()`、`ln10<T>()`、`inf<T>()`、`nan<T>()`。

**分类和符号：** `isNan`、`isInfinite`、`isFinite`、`isNormal`、`signbit`、`fpClassify`、`abs`、`copysign`。

**舍入和余数：** `floor`、`ceil`、`trunc`、`round`、`roundEven`、`fract`、`modf`、`fmod`、`remainder`、`remQuo`。

**基础函数：** `sqrt`、`cbrt`、`hypot`、`pow`、`powi`、`exp`、`exp2`、`expm1`、`log`、`log2`、`log10`、`log1p`、`sin`、`cos`、`tan`、`asin`、`acos`、`atan`、`atan2`、`sinh`、`cosh`、`tanh`、`asinh`、`acosh`、`atanh`、`fma`、`nextAfter`。

**特殊函数：** `erf`、`erfc`、`gamma`、`logGamma`、`beta`、`j0`、`j1`、`y0`、`y1`。Bessel 函数当前仅提供 f64 接口，依赖目标平台提供的相应 C 数学符号。

**辅助函数：** `sinCos`、`ulp`、`maxNum`、`minNum`、`totalMin`、`totalMax`、`clamp`、`lerp`、`midpoint`、`degrees`、`radians`、`distance2D`、`reduceAngle`。

**Complex<T>**

字段为 `re` 和 `im`。方法包括 `real`、`imag`、`conjugate`、`norm`、`abs`、`arg`、`add`、`sub`、`mul`、`div`、`sqrt`、`exp`、`log` 和 `pow`。`log` 缩放输入分量后计算对数模。`exp` 在公共指数溢出时分别计算实部和虚部，以保留可表示的分量及带符号零。

**Interval<T>**

- `new(low, high) -> !Self`
- `contains`、`containsZero`、`intersects`、`intersection`、`hull`、`width`、`midpoint`
- `add`、`sub`、`mul`、`div`

当前 Interval 是基础区间运算容器，使用宿主 IEEE 舍入，不执行向外舍入，不保证每次运算结果都包含精确实数结果。

## random

**错误：** `RandomError`（EmptyRange、InvalidProbability、InvalidWeights、EmptyAlphabet、InvalidAlphabet、InvalidSeed、SampleTooLarge、UnsupportedWidth、SystemEntropyUnavailable）。

**接口：** `Rng`（`nextU64`、`fill`）；`CryptoRng`（`fillSecure`）。`OsRng` 实现 `CryptoRng`，通过系统熵源提供随机字节。

**种子**包括 `Seed`、`seedFromBytes` 和系统种子 `systemSeed`。

**生成器：**

- `Pcg64.seed(u64)`、`Pcg64.fromSeed(Seed)`、`nextU32`、`nextU64`、`fill`、`jump(distance)`、`split(stream)`
- `Xoshiro256.fromSeed([32]u8)`、`nextU64`、`fill`、`jump`、`longJump`
- `newDefault() -> Pcg64`

**分布：**

- `UniformInt<T>.new(low, highExclusive)`、`sample<R>(rng)`
- `UniformFloat<T>.new(low, high)`、`sample<R>(rng)`
- `Bernoulli.new(probability)`、`sample<R>(rng)`
- `Normal.new(mean, standardDeviation)`、`sample<R>(rng)`
- `Exponential.new(rate)`、`sample<R>(rng)`
- `WeightedIndex.new(weights)`、`update(weights)`、`sample<R>(rng)`

**函数：** `randomBool`、`randomU32`、`randomU64`、`rangeInt`（u64/i64 半开区间重载）、`rangeFloat`（f32/f64 半开区间重载）、`shuffle`、`choose`、`chooseMut`、`sample`、`reservoirSample`、`randomString`、`fill`。

`randomString` 按 Unicode 标量抽样；重复出现在 alphabet 中的标量会增加其抽中权重。当前没有 OsRng、secureBytes 或安全熵源实现。
