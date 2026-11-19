# Vaqrin 标准库本地集成记录

集成分支为 `integration/stdlib`。基准是 `master` 的 `aff143372eed57b35042044bc3770ef46aeb9293`。保留开发分支的原始提交和两次合并历史。

本文记录容器和 I/O 阶段。后续四个标准库开发分支见[第二阶段集成记录](follow-up-integration.md)。

| 开发分支 | 原始 HEAD | 与 master 的共同祖先 | 合并提交 | 冲突文件 |
| --- | --- | --- | --- | --- |
| `feature/coll-containers` | `06d7111fd5f3423e1b50bff3b0ff6c561d1c36da` | `aff1433` | `606db8c` | 0 |
| `feature/io-stack` | `1881ba01a0e4a6a36d0c1a78e0434a591b73c99f` | `aff1433` | `afd765d` | 1，`stdlib/README.md` |

## 构建和链接

I/O 分支使用的 `llvm_map_components_to_libnames(... all)` 在本机安装版 SDK 中返回空列表。最小复现的链接命令完全没有 LLVM 库，并报 `llvm::sys::getDefaultTargetTriple()` 和其他未定义符号。现在通过 `find_package(LLVM REQUIRED CONFIG)` 使用 SDK 导出的完整 `LLVM` 共享目标，或在只有组件库时映射显式组件和目标后端。路径由 SDK 提供。

CMake 4.3.0、Ninja、Clang 22.1.8 和 Release 模式下，LLVM 22.1.8 共享库及切换 `VAQRIN_USE_LLVM_DYLIB=OFF` 后的组件库均链接成功。官方 Ubuntu LLVM 20.1.8 SDK 在独立目录解包，使用 `VAQRIN_REQUIRE_BASELINE=ON` 构建。Ubuntu SDK 的依赖只用于本地验收环境。

宿主 x86-64 验收包括 I/O 示例、C 程序链接含 I/O 运行时的静态归档，以及同时链接任务运行时和 I/O 运行时的可执行文件。AArch64 已生成引用 POSIX 运行时的 ELF64 对象。AArch64 的原生链接和运行未覆盖。Cortex-M3 的 freestanding 用例验证了显式分配器路径和宿主 I/O 的能力诊断。

## 验收用例

通过 `ctest --test-dir <build-dir> -j 1 -V` 对两个 SDK 分别串行运行。LLVM 20.1.8 和 22.1.8 均通过五组测试。每套 SDK 的 72 个 Python 测试方法与两项独立静态检查均通过，失败和跳过均为零。

| 测试脚本 | 验收项 | 验收内容 |
| --- | ---: | --- |
| `compiler/tests/acceptance.py` | 55 | 编译器、语言语义、ABI、包加载和仓库已有标准库回归 |
| `compiler/tests/coll_acceptance.py` | 7 | 所有权、析构、环形回绕、链表、堆、哈希与 B 树 |
| `compiler/tests/io_acceptance.py` | 10 | 标准流、文件与目录、短进度、路径、格式化、运行时链接 |
| `compiler/tests/io_static_validation.py` | 1 | 新 I/O 包静态检查 |
| `compiler/tests/coll_static_audit.py` | 1 | 容器八个源码文件的静态审计 |

LLVM 22.1.8 组件库配置额外通过真实 I/O 示例。对 CMake 安装产物复验了全部 31 个标准包的一次性导入与三项 I/O 链接和运行用例。安装的 `tasks.c`、`io_posix.cpp` 和相应源码逐字节一致。

此前同时并行运行两套 SDK 时，各有一项 I/O 编译触发固定的 25 秒超时。两个用例单独复跑通过，串行整套验证继续沿用既有超时。

## 主要修复

| 根因 | 处理 |
| --- | --- |
| 容器源码依赖位置字段、可空标签比较及通过类型别名调用关联方法。前端缺少相应路径 | 编译器支持 `tuple.0`、`tuple?.0`、与 `null` 的标签比较和 `PriorityQueue<T>` 关联调用。修复嵌套字段数字的词法切分，并保留只读指针权限。 |
| B 树返回具名可空结果会复制不可复制载荷。链表接管裸槽后遇到所有权表分配失败时缺少载荷析构 | B 树返回前移动整个可空结果。链表节点记录活跃槽状态，迁出后只释放节点，未迁出时在析构中释放元素。 |
| 以树内借出的键查询删除时，B 树的预先重平衡可能移动待比较的键 | 删除定位完成后再自底向上借用或合并，避免继续读取已失效的指针。新增不可复制键回归，覆盖借出键删除及交替移除首尾元素。 |
| 自定义分配器容器仍会实例化裸机不可用的默认分配。旧能力推导忽略 Vec 内可能借用的分配器 | 按目标选择默认分配实现。裸机保留显式分配路径，缺少默认分配器时返回 `AllocatorUnavailable`。通用 Vec 保守地拒绝跨线程移交。 |
| 合并后部分 I/O 适配器重复定义，直接在通用重试循环里吞掉 `Interrupted` 会让自定义流挂起 | 保留单一定义和 TeeReader。只在 POSIX 系统调用边界重试被信号中断的操作。通用进度接口报告中断及短读短写。 |
| 出于 errno 桥接而开放通用 C ABI ErrorId 会暴露内部表示。宿主能力原先仅在最终链接检查 | 把 errno 标记编码限制在标准包内建转换，保留原始数值。宿主 I/O 在类型检查阶段拒绝不可用目标。 |
| 路径和格式参数借用了已析构的临时字符串，部分外部缓冲区尚未完整初始化 | Path 持有 UTF-8 字符串并提供可失败的视图复制入口。FormatArg 持有从 `string` 传入的内容。写入前为外部字节缓冲区建立已初始化存储。 |
| 有符号 16 位哈希的移位会受符号扩展影响。格式化宽度填充会把零放到负号前，浮点十进制分隔符受进程区域设置影响 | FNV 按固定宽度的原始字节更新。符号在零填充前写出，浮点转换临时使用 C 数值区域设置。 |
| 原 I/O 测试在退出临时目录作用域后执行已删除的程序。静态容器审计也把所有集成修改算到了容器分支 | 测试编译与运行共用每例工作目录。静态审计接受显式的原始分支范围，同时继续检查当前集成源码。CTest 分别运行基础、容器、I/O 与静态验证。 |

## 标准包范围

当前集成有 31 个包含源码的包。本轮增加的容器、哈希、文件、路径、缓冲和格式化接口均已运行代表用例。各包的当前状态、缺口和直接导入列在[实现矩阵](Vaqrin-stdlib-package-matrix.md)。[容器接口](coll-container-implementation.md)及[I/O 接口](io-stack.md)描述当前可用签名，[API 全量清单](Vaqrin-stdlib-API-complete-draft.md)保留设计目标。

未参与这次指定顺序合并的远端分支已有提交，交付前核对到 `feature/numerics` 的 `85adcb7`、`feature/data-formats` 的 `8f1a262`、`feature/unicode-regex` 的 `4ac2b04` 和 `feature/utilities` 的 `9643d67`。数值大整数与有理数、随机分布、JSON/CSV/TOML、Unicode 文本规范化和正则、URL、日志和错误报告仍需在相应集成范围内验证。
