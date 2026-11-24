# v0.2.0 审计与实施记录

基线为 `main` 的 `2726222`，开发在 `feature/v0.2.0`。现行已发布规范仍为 v0.1.5。

## 基线审计

| 层次 | 现有实现与需要迁移的边界 |
| --- | --- |
| 解析和语法树 | `source.cpp` 处理 Unicode 标识符与最长匹配，`parser.cpp` 使用递归下降和 Pratt。参数的旧兼容借用编码为类型前缀。AST 和编译期表支持持久化缓存 |
| 名称、Trait 和泛型 | `inputs.cpp` 保留匿名邻居查找和文件级入口。函数实例、接口匹配和回调类型分别参与解析，参数模式必须贯穿三者 |
| 所有权 | `body.cpp` 保存每个局部的初始化状态和析构登记，`expressions.cpp` 在调用点执行适配和所有权交付。原有普通参数允许临时非 Copy 值传入 |
| 生命周期 | 当前来源以局部根、父借用和只读来源集合表示。能拦截部分逃逸和冲突，缺少字段投影、动态索引别名、通用聚合区域和完整的控制流不动点分析 |
| 返回引用 | 直接调用、间接调用和接口动态派发已有来源传播。方法依赖旧属性，函数类型有旧的索引契约。临时对象由表达式清理作用域保活 |
| 控制流 | `!`、`?`、`??`、`?.` 和 catch 已接入 LLVM 清理路径。循环有回边初始化检查。defer 需要把动作按注册顺序加入清理 |
| 算术与后端 | `numeric.cpp` 生成有符号溢出检查和多种整数运算。表达式降低还有窄类型提升，复合赋值规则不同。LLVM 同时承担 IR 验证、CTFE 所解释的控制流和目标链接 |
| 标准库 | 基线 146 个 Vaqrin 源文件。容器、字符串视图、I/O 读写、Serde 与生成的 RPC 适配器复用指针、旧借用参数、引用字段或回调。改签名前须验证所有权和失效条件 |
| 工具与测试 | CMake/Ninja 构建 C++20 编译器。验收同时检查正反程序、运行时、包、编辑器和 LLDB。格式化器使用 AST，Zed 包含词法树以及独立的签名和符号索引。修改语法须一并更新 |

## 顺序

1. 统一参数模式的调用匹配、ABI 与来源追踪。验证直接调用、函数值、Lambda 和接口。
2. 将借用冲突检查扩展到可投影位置与聚合区域，完成安全借用语法和返回契约。
3. 在核心检查可用后迁移标准库、测试、代码生成器和示例。删除旧引用、权限、整数提升和控制流语法入口。
4. 完成模块入口、延迟清理和工具支持。分别运行优化与调试构建、全量验收、示例、性能基准和安装验证。

## 开发分支状态

| 已落地范围 | 本分支中的行为 |
| --- | --- |
| 参数模式 | `in` 和 `mut` 不复制实参，不隐式解引用裸指针或 Box。`move` 参数检查可移动、非 Copyable 和所有权。模式参与函数值、Lambda、接口声明和实现匹配 |
| 参考来源 | 支持 `ref`、`ref mut`、`from`，函数和值传递复用完整表达式临时对象。接收者或实参的借用来源继续约束返回值 |
| 别名 | 对类和元组的已知字段保留投影。不同字段可独立借用，父对象访问及未知索引按可能重叠处理。只读模式借用和显式可变模式借用具有独立权限 |
| Box 与控制流 | Box 载荷的字段、方法、索引、调用和借用均须显式写 `*box`；`intoInner()` 和 `asPtr()` 是 Box 自身的操作。支持 `while`、`if let` 和循环标签。常规对象清理沿用现有返回和跳转路径 |
| 延迟清理 | `defer call(...);` 在登记时求值并保存函数、接收者和参数；`defer { ... }` 捕获登记点可见、实际引用的绑定，分只读与独占借用。与析构共用反向登记次序，在正常离开、返回、传播、`break`、`continue` 时运行。清理块中的错误显式处理，不允许传播或跳向外层控制流 |
| 复合包装 | 签名和字段不允许直接连续书写 `?` 或 `!`，须引用命名类型别名；其他类型位置最多允许直接连续写两个包装，更深的组合也须经别名。后缀解包语法不变 |
| 模块入口 | 显式单文件入口保留；目录构建使用 `package::main` 选择入口，跨包导入使用逻辑包名。删除匿名同目录查找、文件名隐式限定符和源文件路径导入 |
| 整数 | 普通运算保持位宽，检查有符号和无符号溢出。回绕运算保留显式拼写，饱和运算使用 `num` 函数。`convert.truncBits` 显式丢弃整数高位 |
| 标准库和工具 | Vec 查询签名使用 `from self`。bufio、opt、JSON、TOML 和 gRPC 显式区分可写模式借用。`vaqc run` 使用临时宿主产物。格式化接受新签名，Zed 内联提示显示 `ref`，签名保留 `from` |

标准库的 146 个源文件已迁移 `public:`、`private:`、`in &const T`、`#return_borrow` 和 Trait 交集的旧 `&` 用法。解析器也拒绝旧函数类型的 `borrow(index)`。Serde 和 Protobuf 生成器采用相同语法。目录模式的 `--entry` 和清单 `[build].entry` 显式选入口时使用 `package::main`。清单中的路径依赖仍然可用。

版本号保留 0.1.5。分支尚未达到发布验收标准。继续实施时优先处理下列依赖。

- 完成普通 Value 参数的全局 Copyable 验证与非 Copy 参数迁移。动态调用、接口和方法的权限约束也需要进一步统一验收。
- 建立引用聚合、闭包捕获、迭代器和守卫的跨过程区域关系。当前仍拒绝多种间接引用存储，结构性失效和控制流分析需要继续扩展。
- 移除 `&` 剩余的旧引用入口，完成循环与捕获的 `ref` 语法。为所有裸指针解引用要求 `unsafe`，并迁移受影响的标准库与代码生成器。
- 延迟调用当前要求返回 `void` 的运行期函数、方法或 Vaqrin 函数值，支持已验证捕获值的闭包。借用值必须有可追踪且存活的来源；不能通过延迟调用延长临时借用的生命周期，先绑定所有者。有错误的清理可使用带具体 `catch` 处理的延迟块。继续淘汰其余旧语法。
- 补齐其余标准库签名、API 清单、正式规范和完整 EBNF。保持设计文档中的目标与已验证行为可区分。

设计中使用 `!` 的示例函数必须有错误返回通道。`processFile(file)?` 需要 Optional 成功通道，返回 `!void` 的示例应使用 `!` 传播错误。共享借用与独占借用示例应使用不同词法作用域，两个冲突借用不可同时存活。

### 普通 Value 参数的标准库迁移

- `Vec<T>` 的 `push`、`insert`、`replace` 及相应的保留值变体用类成员的 `comptime if meta.isCopyable<T>()` 选择签名：Copyable 元素用 `value: T`，否则用 `value: move T`。每个实例只有一个成员，不是仅模式不同的重载。两条分支都可交付独立临时值；非 Copyable 具名实参须显式 `move`。保留原有分配失败和越界时的所有权语义。
- `Vec<T>.spliceOwned` 和 `intoIter` 明确消费整个 Vec；`fromSlice`、`splice` 和 `clone` 则只向 Copyable 元素开放。独立的迭代器或回调类型参数（如 `fromIter<I>`、`extend<I>`、`retain<F>`）不会从元素的条件分支取得 Copyable 保证，仍需要后续迁移。
- 对只有一个元素类型的容器，可沿用此模式迁移插入、替换和保留值的操作。对 HashMap 和 BTree 等键、值独立的接口，须分别判定 K 和 V，而非假设两者具有相同模式。只读取的参数优先用 `in`；纯复制泛型函数加 `T: Copyable`。不能声明条件成员的独立函数，需要拆分 API 或重构为符合实际所有权的借用操作。
- `HashMap<K, V>` 的 `insert`、`insertPreserving` 和 `getOrInsert` 系列按各自键和值是否 Copyable 选择形参模式。探测或扩容时只借用键；`insertPreserving` 失败时归还两项输入，覆盖时返回旧值并销毁旧键。现有键的 `getOrInsert` 不覆盖旧值。私有转移和扩容不再次复制；`HashSet.insert`、`entry` 的键与 `Entry.orInsert` 的值也采用相应模式。Entry 的现有 unsafe 生命周期约定没有改变。方法自身的泛型工厂、回调仍待迁移。
- `BTreeMap<K, V>.insert` 也分别判定 K 与 V。查找、节点分裂和插槽选择期间只借用键，确定位置后再搬入整对输入；失败时销毁收到的所有者，保留已存在的键和值。`BTreeSet.insert` 和底层 `FixedVec.insert` 按各自元素的 Copyable 能力选择单一签名。
- `MaybeUninit<T>.write` 与 `ManuallyDrop<T>.new` 按 T 选择 Copy/Move 参数。手动存储容器始终不可复制，`ManuallyDrop.intoInner` 消费整个容器；初始化、提取与手动销毁的既有契约不变。deque、链表、共享存储和 OnceLock 的存储调用已使用显式移动或临时值。
- 原有的标准库替换调用已迁到 `core.Exchange<T>.replace`：目标必须通过受检查的 `mut` 借用进入，替换值按类型选择 Copy/Move，结果返回原所有者。此迁移包括 JSON 的 Vec 可写元素借用；没有将裸地址当作所有权证明。旧 `core.replace` 的三个内部调用仍来自接受指针的 `mem.replace`、`opt.take`、`opt.replace` 公开接口。它们的调用契约尚未迁移，类成员的条件签名也不能直接套用到这些独立函数。
- 启用全局检查前，声明检查必须证明普通泛型参数 Copyable，包括包装类型的结构推导和条件成员的受控分支。一个具体实例恰好 Copyable 不是无约束声明的证明；若采用条件分支作为证明，必须确认分支受真实 Copyable 判定支配，不能仅根据函数名推断。
- 一次未合入的实验在共用参数降低入口拒绝非 Copyable Value，不作标准库豁免。容器 11 项里有 10 项失败，标准库 43 项里有 39 项失败。首批诊断涉及容器、JSON、HTTP、正则、线代、文件与网络等。这仅是调用点实验，不是完整签名验证。签名验证还需要覆盖声明、函数类型、Lambda 和接口，以及示例和代码生成器。

## 验证记录

整数语义迁移后，LLVM 22.1.8 Release 的十套 CTest 均通过，包含 214 项 Python 测试和两个静态脚本。Debug 重新构建后通过八项 v0.2.0 定向测试。有类型的窄整数加减乘、移位和复合赋值按原宽度运算。用 `-O0` 和 `-O2` 验证溢出终止、类型保持、回绕和检查转换。ARM Cortex-M3 freestanding 抽测中，两种优化等级生成的检查加法、回绕乘法和截断对象均无未定义符号。固定种子的 PCG64、Xoshiro256 和 300 字节 SipHash 向量与迁移前输出一致。

可见性、借用参数、Trait 约束和命名入口迁移后，Release 的 10 套 CTest 均通过，包含 212 项 Python 测试和两个静态脚本。Debug 重新构建后通过七项 v0.2.0 定向测试。测试覆盖旧拼写拒绝、配置文件入口、命令行入口、缺失包入口、同包多入口、Serde 生成和 Protobuf 互通。

上一个里程碑在 x86-64 Linux 使用 Clang 和 LLVM 22.1.8 构建 Release 与 Debug。Release 的 CTest 共 10 套，全部通过。Debug 在同一编译器产物上完成九套 CTest，随后用 Python 3.14 单独重跑协议套件，26 项均通过。首次 Debug 协议测试因 CMake 选中的 Python 3.11 与本机 gRPC 扩展的 Python 3.14 ABI 不符而失败。Release 的 Python 测试共 210 项，另有两个静态检查脚本。测试覆盖动态接口、泛型、Lambda、调用期间借用冲突、移动后的重新初始化、临时模式借用逃逸、Box 自定义分配器的释放和析构、包与协议互通。参数模式示例在仓库和临时安装目录中均依次输出 20、11、21、11。

```sh
cmake -S compiler -B build/host -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/clang -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
  -DLLVM_DIR="$(llvm-config --cmakedir)" -DPython3_EXECUTABLE=/usr/bin/python3.14
cmake --build build/host --parallel 3
ctest --test-dir build/host --output-on-failure -j 3
cmake -S compiler -B build/v020-debug -G Ninja -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_COMPILER=/usr/bin/clang -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
  -DLLVM_DIR="$(llvm-config --cmakedir)" -DPython3_EXECUTABLE=/usr/bin/python3.14
cmake --build build/v020-debug --parallel 2
ctest --test-dir build/v020-debug --output-on-failure -j 3
build/host/vaqc run examples/language/parameter_modes.vqr
build/host/vaqc fmt --check examples/language/parameter_modes.vqr
```

本机测试使用 Python 3.14，gRPC 原生扩展须与之匹配。协议套件还需要 nghttp2 开发包、OpenSSL 和 zlib。Zed 高亮查询和 LSP 通过命令行测试。

## 性能对照

使用同一 LLVM、Clang 和 Release 配置分别构建 `main` 基线和开发分支。两侧使用独立的暖缓存，交替运行，每个组合去掉首次结果后取七次中位数。用户程序使用 `-O0`。计时为当前机器的进程墙钟时间，含工具启动和构建所需的子进程。

| 输入和命令 | 基线中位数 | 分支中位数 |
| --- | ---: | ---: |
| Hello，`check` | 158.5 ms | 153.6 ms |
| Hello，`build` | 271.9 ms | 268.0 ms |
| P1102，`check` | 177.1 ms | 186.9 ms |
| P1102，`build` | 707.1 ms | 705.1 ms |

基线和分支的各组数据范围相交。当前结果可以记录这两个入口的运行量级，无法据此判定整套标准库的性能变化。
