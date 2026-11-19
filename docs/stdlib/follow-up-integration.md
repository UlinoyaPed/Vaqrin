# 数值、数据格式、Unicode 与工具包本地集成

这次集成接续容器与 I/O 的 `14df2cc`。四个开发分支与 `master` 的共同祖先均为 `aff1433`。

| 分支 | 原始 HEAD | 合并提交 | 冲突文件 |
| --- | --- | --- | --- |
| `feature/numerics` | `9913e30bb4b4667547f7077c81c1fb6a45dfc936` | `9f65e22` | `stdlib/README.md`，保留容器、I/O 和数值说明 |
| `feature/data-formats` | `93aaf82ed488a5203d84118616b94d02ea9e956b` | `2d95e1d` | 0 |
| `feature/unicode-regex` | `79b3d4c5a877bc0ce865062beebf7d0599717fc1` | `64a1204` | 0 |
| `feature/utilities` | `5aa62f39698849abf89527e03430b578ce5cc91d` | `5f312a3` | 0 |

## 根因与修复

布局构造阶段提前验证 `Vec<T>` 的约束，会把 `Node → Vec<Node> → 指针` 误判为直接按值递归。编译器现在在布局完成后验证实例约束，并允许在属性名位置使用保留字 `move`。直接按值递归和 `Vec<Immovable>` 仍报错。`switch` 在读取借用枚举前确保枚举布局已完成。导入扫描排除返回类型中的 `*const`、`[]const`，避免把后面的包限定符误报为重复声明。

各包采用编译器实际支持的类、循环、切片和模式语法。移动整个本地值，或通过 `core.replace` 显式接管字段，避免复制不可复制的 DOM、捕获组、容器和报告。需要将 `string` 借成 `str` 时先绑定拥有者。Codec 工厂接受 `*Codec<T>`，编码和解码可以调用可变接口方法。日志入口使用 `Attr.errorId` 和 `log.logError`，避免把关键字 `error` 用作标识符。CSV 验证构造参数时传播原始错误，测试脚本使用独立临时目录容纳四个可执行文件。

正则 NFA 的贪婪重复连接到了错误的退出边。修正后，贪婪和惰性分别以规定顺序探索，保留 `(ab)+` 的完整匹配。URI 去除 `/..` 段时保留了被移除的首段对应的斜杠，导致 `/a/../b` 变成 `//b`。现在一并移除该斜杠，并验证合法的连续斜杠继续保留。

## 验收范围

`compiler/tests/stdlib_acceptance.py` 由 CTest 运行。用例验证数值示例、整数溢出及除零、Euclidean 除法、gcd/lcm、固定种子的范围抽样、浮点定义域、JSON 与 CSV、TOML 和 Codec 往返、Unicode 四种规范化与非法 UTF-8、正则匹配与资源限制、URI 解析及相对引用、日志过滤和多 Handler、错误上下文与聚合。

构建使用 x86-64 GNU/Linux、CMake、Ninja 和 Release。LLVM 20.1.8 构建启用 `VAQRIN_REQUIRE_BASELINE=ON`，LLVM 22.1.8 使用当前兼容路径。两套 SDK 均通过六组 CTest。每套为基础 55、容器 7、I/O 10、本轮标准包 13，以及 I/O 和容器各一项静态检查，失败和跳过均为零。独立数据格式脚本完成四个示例的构建与运行，安装版 `vaqc` 可以一起导入 44 个源码命名空间。

逐包状态、特定缺口及直接导入见[实现矩阵](Vaqrin-stdlib-package-matrix.md)。目前的覆盖包括 43 个公开源码包和一个内部 `math_ffi` 包。数学函数继续使用目标 C ABI，宿主数值验收会显式链接工具链提供的 `libm`。完整 Unicode 官方数据回归、所有随机分布统计、全部 RFC/TOML 向量和其他平台运行仍待专项验收。
