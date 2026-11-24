# v0.2.0 标准库迁移摘录（开发分支）

这是已实现改动的摘录，不是完整 API 手册或发版声明。以对应提交的公开源码与验收测试为准。[v0.1.3 的 59 包设计清单](../stdlib/Vaqrin-stdlib-API-complete-draft.md)照原样归档。

| 模块 | 已实现的变化 | 实现来源 |
| --- | --- | --- |
| `opt` | `asRef<T>(value: in ?T) ?ref T`；`asMutRef<T>(value: mut ?T) ?ref mut T`。使用可检查的借用，而非旧式指针形参。 | [opt.vqr](../../stdlib/opt/opt.vqr) |
| `bufio` | `Token.text() str from self`。文本视图受 Token 来源限制；这不等于 `ByteToken.bytes()` 或一般切片都已有相同契约。 | [bufio.vqr](../../stdlib/bufio/bufio.vqr) |
| `num`、`linalg` | 大整数和有理数的迁移运算，以及已迁移的矩阵乘法、LU、QR 和求解，使用 `in` 借用只读操作数，不再让调用方为了这些操作创建裸指针。 | [num](../../stdlib/num)、[linalg](../../stdlib/linalg) |
| `coll` | Vec 的追加、插入和替换，以及 HashMap、BTreeMap 的已迁移插入接口，按类型的 `Copyable` 能力选择复制或显式转移。单个实例不会仅靠参数模式区分多个同名入口。 | [coll](../../stdlib/coll)、[容器验收](../../compiler/tests/coll_acceptance.py) |

开发分支仍以 `0.1.5` 构建并报告版本。全局 Copyable 检查、所有裸指针解引用的 `unsafe` 检查和一般容器借用还未达到正式交付标准。

正式发版时，从最终源码更新 API 记录；同步配置中的项目版本和编译器自报版本，并在安装后的二进制上断言 `vaqc --version` 的值。仅运行版本命令并检查退出码不能证明版本正确。应以最终接口重跑配对 I/O 基准；若在宿主环境验证 DAP，须确认真实 `lldb-dap` 用例执行而非跳过。
