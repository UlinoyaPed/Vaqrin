# Vaqrin 示例

从 [hello.vqr](hello.vqr) 开始。先构建[宿主编译器](../README.md#快速开始)，再从仓库根目录运行。

```sh
mkdir -p build/examples
build/host/vaqc build examples/hello.vqr -o build/examples/hello
./build/examples/hello
```

单文件示例将 `.vqr` 文件传给编译器。带 `package.toml` 的项目也可传入目录。例如
`build/host/vaqc build examples/projects/local_import -o build/examples/local_import`。
Zed 的 `run code` 任务可直接启动单文件和各项目的入口文件。

## 基础与语言

| 示例 | 练习内容 | 标准输出 |
| --- | --- | --- |
| [hello.vqr](hello.vqr) | 导入、`main`、传播输出错误 | `Hello, Vaqrin!` |
| [basics/control_flow.vqr](basics/control_flow.vqr) | 常量、数组、`if`、`for`、函数末尾表达式 | `excellent`、`retry`、`pass`、`2` |
| [basics/collections.vqr](basics/collections.vqr) | `Vec` 的借用迭代和 `HashMap` 频次统计 | `2`、`1`、`0` |
| [language/optional.vqr](language/optional.vqr) | `?` 传播空值，`!` 传播错误，`??` 提供后备值 | `42`、`0`、`-1` |
| [language/generics.vqr](language/generics.vqr) | 类型参数和函数类型 | `42`、`true` |
| [language/interfaces.vqr](language/interfaces.vqr) | 接口、带载荷的枚举和模式匹配 | `21`、`16`、`10` |
| [language/ownership.vqr](language/ownership.vqr) | Box、借用、`move`、作用域退出时的析构 | `42`、`Counter released` |
| [language/references.vqr](language/references.vqr) | 严格和兼容引用、裸指针、Box 的借用、返回引用 | 多组类型和值 |
| [language/parameter_modes.vqr](language/parameter_modes.vqr) | v0.2.0 开发分支的参数模式、重借用和返回借用 | `20`、`11`、`21`、`11` |

函数尾部不带分号的表达式可以作为返回值。示例多用 `main() !void`，从而能把 I/O 错误传播给运行时。

## 标准库

| 示例 | 练习内容与运行提示 |
| --- | --- |
| [stdlib/stdin.vqr](stdlib/stdin.vqr) | Scanner 从标准输入读取整数，遇到 EOF 后输出总和 |
| [stdlib/files.vqr](stdlib/files.vqr) | 在当前可写目录创建临时文件，写入、定位、逐行读取。文件随对象释放 |
| [stdlib/file_operations.vqr](stdlib/file_operations.vqr) | 在独立临时目录练习随机访问、文件元数据、目录枚举和格式化输出 |
| [stdlib/data_formats.vqr](stdlib/data_formats.vqr) | 读取 JSON、TOML 字段及带表头的 CSV。输出 `Ada`、`8080`、`2` |
| [stdlib/protobuf.vqr](stdlib/protobuf.vqr) | 手工写入、读取 Protobuf 字段。输出 `Vaqrin`、`42` |
| [stdlib/unicode.vqr](stdlib/unicode.vqr) | NFC 规范化和 Unicode 版本查询 |
| [stdlib/regex.vqr](stdlib/regex.vqr) | 字素簇、正则捕获和空匹配迭代 |
| [stdlib/numerics](stdlib/numerics/) | 大整数、分数、数学函数和随机数范围。目录提供预期输出 |
| [stdlib/utilities.vqr](stdlib/utilities.vqr) | URL 解析、结构化日志、错误上下文和错误聚合 |
| [stdlib/tasks.vqr](stdlib/tasks.vqr) | 在支持任务运行时的宿主上启动任务并 `join`。输出 `42` |

标准输入示例的运行方式同样适用于下面的题解。

```sh
build/host/vaqc build examples/stdlib/stdin.vqr -o build/examples/stdin
printf '1 -2 43\n' | ./build/examples/stdin
```

## 多文件项目

- [本地导入](projects/local_import/) 用 `package.toml` 指定入口，再通过文件别名调用另一个文件的公开函数。输出 `42`。
- [gRPC Greeter](projects/grpc/README.md) 包含服务端、客户端、Protobuf 定义及生成的 Vaqrin 绑定。按项目说明生成 TLS 证书，再启动服务端和客户端。客户端输出 `Hello, Vaqrin`。

## 算法题解

| 题目 | 方法 | 样例输入中的换行以 `\n` 表示 | 预期输出 |
| --- | --- | --- | --- |
| [P1102 A-B 数对](contests/p1102.vqr) | 用 HashMap 保存频次，按不同的键累加 64 位频次乘积 | `4 1\n1 1 2 2\n` | `4` |
| [P1918 保龄球](contests/p1918.vqr) | 将号码映射到从 1 开始的位置 | `4\n5 7 9 12\n3\n9 3 5\n` | `3`、`0`、`1` |
| [B3612 前缀和](contests/b3612.vqr) | 使用 64 位前缀和回答区间和查询 | `5\n1 -2 3 4 -5\n3\n1 3\n2 5\n4 4\n` | `2`、`0`、`4` |

学习路线的输出由 `vaqc-stdlib-acceptance` 核对。P1102 的大输入与错误路径由
`vaqc-io-acceptance` 覆盖，gRPC 示例的客户端和服务端由 `vaqc-protocol-acceptance` 联动运行。

完整语法和所有权规则见[语言规范](../docs/language-spec-v0.1.5.md)。
