# Vaqrin 标准包

数值分支新增 `num`、`math`、`random`。详见 [数值实现说明](../docs/stdlib/numerics-implementation.md)。

| 包 | 当前接口 |
| --- | --- |
| `num` | 数值限制、检查/回绕/饱和整数运算、BigUint、BigInt、有理数 |
| `math` | 浮点分类、常量、C libm 包装、Complex、Interval |
| `random` | PCG-XSH-RR、xoshiro256**、均匀范围、洗牌和抽样 |

数学函数需要按目标平台链接 C 数学库；随机包仅提供可复现的非密码学 PRNG，不提供 OS 熵源。
