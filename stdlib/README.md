# Vaqrin 标准包

可用 `import (core, coll, iter, opt, text, io)` 导入标准包，也可使用 `std/` 前缀。CMake 在构建目录生成 `share/vaqrin/stdlib`，安装时将标准包源码复制到安装前缀下。编译器按使用情况实例化泛型声明。完整的目标接口见[标准包 API 清单](../docs/stdlib/Vaqrin-stdlib-API-complete-draft.md)。

| 包 | 当前接口 |
| --- | --- |
| `core` | `Box<T>`、可空及错误结果状态检查、`unwrapOr`、`identity`、`drop`、`swap`、`replace`、`unreachable`、`unreachableUnchecked` |
| `coll` | `Vec<T>`、借用访问和可变迭代 |
| `iter` | 按值和按指针遍历切片、单项和空游标、映射、过滤、枚举、组合、反向及截取适配器 |
| `opt` | 可空值的组合、访问、原地替换、容器转换和外层交换 |
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
