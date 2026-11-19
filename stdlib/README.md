# Vaqrin 标准包

使用 `import (core, coll, text, io);` 导入。可空组合子通过 `import (opt);` 导入。包名也接受 `std/` 前缀，例如 `import (std/io);`。CMake 在构建目录生成 `share/vaqrin/stdlib`，安装时将同一目录放到安装前缀下。消费者编译时从源码实例化用到的声明。

| 包 | 接口 |
| --- | --- |
| `core` | `Box<T>` 与 `Vec<T>` 别名，`isSome`、`isOk`、`unwrapOr` |
| `coll` | `Vec<T>`、`iter`、`enumerate`、`zip`、`map`、`filter`、`filterMap`、`take`、`skip` |
| `opt` | `map` 保留一层、`andThen` 接续可空回调、`flatten` 显式合并一层 |
| `text` | UTF-8 字节相等比较 `equal`、`byteLen`、`scalarCount`、`chars`、`fromBytes`、`StringBuilder` |
| `io` | 宿主 `println`，输出失败通过 `!void` 返回 |

```cpp
package main;
import (coll, io);

func main() !void {
    var values = coll.Vec<i32>();
    values.push(42)!;
    values.push(7)!;
    for index, value in coll.enumerate(values.asSlice()) {
        io.println(index)!;
        io.println(value)!;
    }
}
```

Vec 按值接收可移动元素。其 `reserve` 先分配，再搬迁已有元素，分配失败时保留原缓冲区和元素。Vec 还提供下标、`len`、`capacity`、`isEmpty`、`pop`、`replace`、`clear` 和可复制元素的 `clone`。`asSlice` 返回借用视图，扩容后原视图失效。迭代器适配器当前从切片读取元素，`map` 和 `filter` 接收普通函数指针。文本按字节比较，`chars` 按 Unicode 标量遍历。`fromBytes` 验证 UTF-8 后复制成不可变字符串，`StringBuilder.build()` 生成可独立持有的快照。拥有型 `string` 的副本共享不可变存储，`str` 与字节视图遵守来源的生命周期。

默认堆分配器目前面向 Linux 宿主。裸机可以构造和销毁空 Vec，增长操作会报告缺少分配器。自定义分配器、字素边界、任务和通道包仍待实现。
