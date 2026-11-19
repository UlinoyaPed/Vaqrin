# Vaqrin 标准包

可用 `import (core, coll, iter, opt, result, cmp, meta, bits, algo, atomic, text, io)` 导入标准包，也可使用 `std/` 前缀。CMake 在构建目录生成 `share/vaqrin/stdlib`，安装时将标准包源码复制到安装前缀下。编译器按使用情况实例化泛型声明。完整的目标接口见[标准包 API 清单](../docs/stdlib/Vaqrin-stdlib-API-complete-draft.md)。

| 包 | 当前接口 |
| --- | --- |
| `core` | `Box<T>`、可空及错误结果状态检查、`unwrapOr`、`identity`、`drop`、`swap`、`replace`、`unreachable`、`unreachableUnchecked` |
| `coll` | `Vec<T>`、容量调整、位置插入和移除、借用访问和可变迭代 |
| `iter` | 按值和按指针遍历切片、单项和空游标、映射、过滤、枚举、组合、反向及截取适配器 |
| `opt` | 可空值的组合、访问、原地替换、容器转换和外层交换 |
| `result` | 错误结果的组合、恢复、观察、只读载荷访问与收集 |
| `cmp` | 标量比较、部分序、浮点总序和上下界助手 |
| `meta` | 目标布局、类型名称、数组长度、复制、移动和析构特性 |
| `bits` | 定宽整数位操作、对齐及可指定顺序的位流读写 |
| `algo` | 切片查找、二分、堆操作、排序、翻转和旋转 |
| `atomic` | 原生标量、可空指针、原子标志、比较交换、读改写和内存栅栏 |
| `text` | UTF-8 字节相等比较 `equal`、`byteLen`、`scalarCount`、`chars`、`fromBytes`、`StringBuilder` |
| `io` | 宿主 `println`，输出失败通过 `!void` 返回 |

```cpp
package main
import (coll, iter, io)

func main() !void {
    var values = coll.Vec<i32>();
    values.push(42)!;
    values.push(7)!;
    for index, value in iter.enumerate<iter.SliceIter<i32>, i32>(values.iter()) {
        io.println(index)!;
        io.println(value)!;
    }
}
```

Vec 按值接收可移动元素。其 `reserve` 先分配，再搬迁已有元素，分配失败时保留原缓冲区和元素。Vec 还提供下标、`len`、`capacity`、`isEmpty`、`pop`、`replace`、`clear` 和可复制元素的 `clone`。`asSlice` 返回借用视图，扩容后原视图失效。迭代适配器可拥有上游游标及回调。按引用遍历时使用 `iterRef()` 或 `iterMut()`，后者可配合 `for &item in ...` 修改原位置。文本按字节比较，`chars` 按 Unicode 标量遍历。`fromBytes` 验证 UTF-8 后复制成不可变字符串，`StringBuilder.build()` 生成可独立持有的快照。拥有型 `string` 的副本共享不可变存储，`str` 与字节视图遵守来源的生命周期。

默认堆分配器目前面向 Linux 宿主。裸机可以构造和销毁空 Vec，增长操作会报告缺少分配器。自定义分配器、字素边界、任务和通道包仍待实现。

位流的 `LsbFirst` 从字节低位开始，也先读写结果或参数的低位。`MsbFirst` 从字节高位开始，从读写宽度所覆盖的最高位开始。读不到足够位时，`BitReader` 返回成功的空值并保持位置。超出写入容量时，`BitWriter` 返回错误并保持缓冲区和位置。

`atomic` 在 x86-64 和 AArch64 上支持 8、16、32、64 位原子操作，在 Thumbv7-M 和 Thumbv7E-M 上支持 8、16、32 位原子操作。默认内存顺序为 `SeqCst`，非法的顺序组合触发 trap。用 `Atomic<T>.intoInner(move(value))` 交付原子对象并取出其值。
