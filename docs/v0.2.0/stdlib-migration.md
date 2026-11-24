# v0.2.0 标准库迁移摘录（开发分支）

这是已实现改动的摘录，不是完整 API 手册或发版声明。以对应提交的公开源码与验收测试为准。[v0.1.3 的 59 包设计清单](../stdlib/Vaqrin-stdlib-API-complete-draft.md)照原样归档。

| 模块 | 已实现的变化 | 实现来源 |
| --- | --- | --- |
| `opt` | `asRef<T>(value: in ?T) ?ref T`；`asMutRef<T>(value: mut ?T) ?ref mut T`。使用可检查的借用，而非旧式指针形参。 | [opt.vqr](../../stdlib/opt/opt.vqr) |
| `bufio` | `Token.text() str from self`。文本视图受 Token 来源限制；这不等于 `ByteToken.bytes()` 或一般切片都已有相同契约。 | [bufio.vqr](../../stdlib/bufio/bufio.vqr) |
| `num`、`linalg` | 大整数和有理数的迁移运算，以及已迁移的矩阵乘法、LU、QR 和求解，使用 `in` 借用只读操作数，不再让调用方为了这些操作创建裸指针。矩阵和向量的 `get/getMut` 返回随本体存活的 `?ref T` / `?ref mut T`；现有 Slice 接口尚未同步迁移。 | [num](../../stdlib/num)、[linalg](../../stdlib/linalg) |
| `coll` | Vec 的追加、插入和替换，以及 HashMap、BTreeMap 的已迁移插入接口，按类型的 `Copyable` 能力选择复制或显式转移。单个实例不会仅靠参数模式区分多个同名入口。 | [coll](../../stdlib/coll)、[容器验收](../../compiler/tests/coll_acceptance.py) |

| `cmp` | `partialCmp`、`compare`、`totalCmp` 和 `between` 的操作数使用 `in T`。只读借用支持变量、临时值和安全引用，调用方不再传 `raw x`。此切片不含 `eq`、`ne`、接口或算法回调。 | [cmp.vqr](../../stdlib/cmp/cmp.vqr) |

| `mem` 和自定义容器分配器 | `AllocatorRef` 不再是 `*Allocator` 的别名，而是持有私有地址的非拥有句柄；调用者通过 `unsafe { mem.AllocatorRef.fromRaw(raw allocator) }` 明确承担地址稳定、调用序列化和分配器/后备存储的生命周期责任。已有容器及 Arena/Pool 的分配器入口接收该句柄。原始释放必须使用 `unsafe` 并提供同一分配器、原布局、未释放的分配。默认分配不变。 | [allocator.vqr](../../stdlib/mem/allocator.vqr) |
| `json` 解析 | Unicode 转义写入由解析器局部拥有的缓冲区，通过 `mut` 传递；保留最后一个重复键时，就地替换并释放旧值。编码器、借用 Reader 的入口及通用 JSON 字段指针接口不在此切片。 | [parser.vqr](../../stdlib/json/parser.vqr) |
| `json` 编码值 | `Encoder.encodeValue`、`write`、`stringify`、`stringifyPretty` 通过 `in Value` 读取输入，不再接收 `raw value`。递归访问使用绑定到数组或对象的 `getRef`、`valueRefAt`。旧索引指针接口仍供现有 Reader/Serde/迭代器使用；Writer 存储和排序回调未迁移。 | [encoder.vqr](../../stdlib/json/encoder.vqr) |
| `io` 进度计数 | `readExact/readAtLeast/writeAll` 的可恢复进度计数使用 `mut usize`，调用方传进度变量本身；错误前成功传输的字节数仍保留。Writer/Reader 的原始生命周期约定不变。 | [streams.vqr](../../stdlib/io/streams.vqr) |
| `json` 所有权转移 | `JsonArray.push/insert/replace`、`JsonObject.insert`、`pointerSet` 与 DOM 反序列化构造显式消费 `Value`。具名值需写 `move(value)`；构造所得临时值可直接交付。编码和查询仍借用输入。 | [json.vqr](../../stdlib/json/json.vqr)、[serde.vqr](../../stdlib/json/serde.vqr) |

自定义分配器必须比句柄的所有副本、从中派生的容器和尚未释放的块活得更久。该契约是调用者的显式保证，不是编译器验证的引用生命周期。Box 的定制分配形式 `new(allocator) T(...)` 也要求显式 `unsafe`，若来源是原始指针，必须先显式解引用。编译器仍不追踪 Box 中的定制分配器地址，调用者必须保证分配器及后备存储一直有效，直到最后一个使用该分配器的 Box 或其拆分后的释放上下文完成释放。默认 `new T(...)` 不需 `unsafe`。

开发分支仍以 `0.1.5` 构建并报告版本。全局 Copyable 检查、所有裸指针解引用的 `unsafe` 检查和一般容器借用还未达到正式交付标准。

正式发版时，从最终源码更新 API 记录；同步配置中的项目版本和编译器自报版本，并在安装后的二进制上断言 `vaqc --version` 的值。仅运行版本命令并检查退出码不能证明版本正确。应以最终接口重跑配对 I/O 基准；若在宿主环境验证 DAP，须确认真实 `lldb-dap` 用例执行而非跳过。
