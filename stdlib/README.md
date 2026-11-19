# Vaqrin 标准包

可用 `import (core, coll, iter, opt, result, cmp, convert, meta, mem, mmio, bits, algo, atomic, binary, bytes, cell, encoding, simd, text, parse, io, thread)` 导入标准包，也可使用 `std/` 前缀。CMake 在构建目录生成 `share/vaqrin/stdlib`，安装时将标准包源码复制到安装前缀下。编译器按使用情况实例化泛型声明。完整的目标接口见[标准包 API 清单](../docs/stdlib/Vaqrin-stdlib-API-complete-draft.md)。

| 包 | 当前接口 |
| --- | --- |
| `core` | `Box<T>`、可空及错误结果状态检查、`unwrapOr`、`identity`、`drop`、`swap`、`replace`、`unreachable`、`unreachableUnchecked` |
| `coll` | `Vec<T>`、零堆分配的 `FixedVec<T, N>`、容量调整、位置插入和移除、借用访问和可变迭代 |
| `iter` | 按值和按指针遍历切片、单项和空游标、映射、过滤、枚举、组合、反向及截取适配器 |
| `opt` | 可空值的组合、访问、原地替换、容器转换和外层交换 |
| `result` | 错误结果的组合、恢复、观察、只读载荷访问与收集 |
| `convert` | 检查转换、整数与浮点舍入、位转换及 Unicode 标量验证 |
| `cmp` | 标量比较、部分序、浮点总序和上下界助手 |
| `meta` | 目标布局、类型名称、数组长度、复制、移动、析构、接口实现及线程间移交和共享特性 |
| `mem` | 布局与对齐、字节操作、原始槽、手动析构、作用域守卫和默认或固定缓冲分配器 |
| `mmio` | 带边界和对齐检查的寄存器、宽度受限的 volatile 访问、编译器屏障及 ARM 指令屏障 |
| `bits` | 定宽整数位操作、对齐及可指定顺序的位流读写 |
| `algo` | 切片查找、二分、堆操作、排序、翻转和旋转 |
| `atomic` | 原生标量、可空指针、原子标志、比较交换、读改写和内存栅栏 |
| `binary` | 定宽端序转换、带位置的读写游标、VarUint 和 ZigZag |
| `bytes` | 切片查找、预处理字节模式、分割及可增长缓冲区 |
| `cell` | 内部可变值、运行时借用守卫、惰性及单次初始化、跨线程单次发布 |
| `encoding` | Hex、Base64、百分号编码、ASCII、Latin-1、UTF-8 和 UTF-16，含跨块 UTF 校验与解码 |
| `simd` | 定宽标量通道、掩码、载入与存储、重排和归约 |
| `text` | UTF-8 字节相等比较 `equal`、`byteLen`、`scalarCount`、`chars`、`fromBytes`、`StringBuilder` |
| `parse` | 整串及前缀整数解析、布尔值、Unicode 标量、UTF-8 字节偏移游标和空白分词 |
| `io` | 读写与定位接口、内存流、限制与计数适配器、短读短写进度及宿主 `println` |
| `thread` | GNU/Linux 原生线程、一次性领取结果、分离、协作停止、当前线程 ID 及 CPU 数 |

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

`RefCell` 用借用守卫管理共享和独占访问，守卫或其映射结果释放时归还借用。`OnceCell` 检测重入，`OnceLock` 使用 Acquire/Release 发布已初始化的值，争抢初始化的线程采用自旋等待。可失败的初始化失败后允许重试。守卫和借出的指针必须在对应容器的有效期内使用。

`binary` 支持显式小端、大端和目标本机端序。VarUint 使用最短编码，读到非最短或超出 64 位范围的值时返回错误。读取长度前缀失败时保留游标位置，写入容量不足时保留缓冲区内容。

`parseInt` 和 `parseUint` 支持 2 到 36 进制，按目标整数的取值范围检查溢出。`parseIntPrefix` 返回解析结果和消耗的字节数。`parseHex`、`parseBinary` 和 `parseOctal` 接受对应的可选进制前缀。`Cursor` 和 `splitFields` 返回借用输入的文本视图，游标位置总落在 UTF-8 标量边界。

`thread.Thread<T>.spawn` 要求闭包及成功载荷可跨线程移交。回调返回 `T` 或 `!T`，`join` 一次性领取 `!T`。线程句柄析构时分离仍持有的线程，分离后由工作线程与句柄共同负责回收结果和运行状态。`requestStop` 设置协作标志，线程回调可由 `stopRequested` 查询。原生线程当前支持 GNU/Linux x86-64 和 AArch64。

`new(allocator) T(...)` 借用一个实现 `mem.Allocator` 的可变位置。分配器及其缓冲区必须比所有借用它的 Box 活得更久。拆出自定义分配的 Box 时使用 `Box.intoRawParts`，恢复时传回原释放上下文。由于同一个 `^T` 类型可保存借用的分配器，当前线程能力检查保守地拒绝跨线程移交或共享 Box。
