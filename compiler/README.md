# Vaqrin 编译器

`vaqc` 将 `.vqr` 源文件编译为原生产物。当前实现覆盖 [v0.1.5 语言规范](../docs/language-spec-v0.1.5.md) 的核心语言子集。

`value?` 传播空值，`result!` 传播错误，`lookup()!?` 可依次检查两个通道。
宿主程序使用 `-g` 构建时，断言式 `.unwrap()` 会在 trap 前输出文件、行号和函数。
可在 [Optional 示例](../examples/optional.vqr) 中试用传播、后备值和显式错误转换。

## 构建和运行

在仓库根目录执行。使用 CMake 3.24+、Ninja、C++20 编译器和 LLVM 开发包。已验证 LLVM 20.1.8 和 22.1.8。选择 LLVM 20.1.8 基线时，将 `LLVM_DIR` 指向对应 SDK，并添加 `-DVAQRIN_REQUIRE_BASELINE=ON` 强制精确版本。CMake 优先链接 SDK 导出的完整 LLVM 共享库，也可通过 `-DVAQRIN_USE_LLVM_DYLIB=OFF` 使用显式组件列表。

```bash
cmake -S compiler -B build/host -G Ninja \
  -DCMAKE_CXX_COMPILER=clang++ -DLLVM_DIR="$(llvm-config --cmakedir)"
cmake --build build/host
ctest --test-dir build/host --output-on-failure
build/host/vaqc --version
build/host/vaqc build examples/hello.vqr -o build/hello
./build/hello
```

示例先输出 `Hello, Vaqrin!`，再依次输出 1 到 3000 的平方。宿主链接默认调用 `clang`，归档默认调用 `llvm-ar`。可用 `cmake --install build/host --prefix <安装目录>` 安装 `bin/vaqc` 和标准包源码。

```bash
build/host/vaqc check examples/hello.vqr
build/host/vaqc build --emit=llvm-ir examples/hello.vqr -o build/hello.ll
build/host/vaqc build -O2 -g examples/hello.vqr -o build/hello
```

默认产物是宿主可执行文件，入口支持 `func main() void`、`func main() !void`、`func main() i32` 和 `func main() !i32`。成功的整数结果用作进程退出码，未处理错误输出到标准错误流并以 1 退出。也可生成 `llvm-ir`、`bc`、`asm`、`obj`、`staticlib` 和 `sharedlib`。生成库或对象时允许省略入口，供 C 消费者使用的符号通过 `export "C"` 声明。`--emit=ast` 将语法树写到标准输出。`check` 解析、类型检查并验证生成的 IR，无需链接。

文件头可以省略 `package`。写出 `package demo` 或 `import (io)` 时，行尾省略分号。可执行文件从指定的源文件选取 `main`。目录模式优先采用 `--entry`，其次采用 `package.toml` 的 `[build].entry`，最后递归查找唯一的候选入口。多个显式输入各自保留文件级 `main`，可以用 `--entry` 选择其中之一。

同目录匿名文件可以直接引用唯一的辅助声明，也可用 `helpers.name` 限定。跨目录使用 `import ("lib/helpers.vqr" as h)` 导入目标的公开声明。有名包通过逻辑包名导入，额外搜索根由 `--package-path DIR` 指定。标准包也支持 `std/` 前缀。参见[标准包说明](../stdlib/README.md)。

在 Zed 中打开仓库根目录，`task: spawn` 可启动 `build vaqc`。已有绑定指向 `run code` 时，当前文件是 `.vqr` 就在构建编译器后编译并运行它，其他文件只触发编译器构建。任务首次配置时生成 `build/host/compile_commands.json`，供 clangd 使用。

## 当前覆盖

| 领域 | 已实现 |
| --- | --- |
| 前端 | UTF-8 校验、Unicode 17.0 XID 和 NFC、字面量、可选包头、按文件选择入口、本地包和匿名文件导入、别名与可见性 |
| 数值和控制流 | 1–128 位整数的前端类型、f32/f64、目标宽度整数、检查/回绕/饱和运算、转换、短路和比较链、分支、范围与数组循环、`iter()` / `next()` 协议、switch |
| 值和资源 | 元组、固定数组、切片、普通指针、可空值与错误结果、类、构造器委托、嵌入方法提升、显式接口与动态接口视图、带载荷枚举、解引用绑定与模式借用、复制和显式移动、析构、支持自定义分配器的 Box 与默认分配器的 Vec |
| 函数和编译期 | 函数指针、显式捕获 Lambda、受支持签名的重载、函数、类、枚举及类型别名的类型和编译期值泛型、类内 `comptime if`、部分泛型实参推导、可解释的常量和函数 |
| 目标接口 | Linux 宿主 I/O、标量 C ABI、词法 unsafe、普通指针转换、volatile 访问、按目标宽度提供原子操作、DWARF 函数与基础局部信息、Cortex-M3 固件链接 |

目前缺少完整任务与通道运行时、`select`、泛型接口方法、Unicode 字素分割、通用迭代器组合、独立的目标运行时和 C 聚合 ABI 桥。Box、Vec 与拥有型动态文本的默认分配器在 Linux 宿主可用。Box 另支持 `new(allocator)` 和带释放上下文的原始部件转换。不可移动值的通用按值就地构造仍待实现。工程清单支持本地 `[build].entry`，依赖版本、锁文件和依赖获取仍待实现。泛型主要在实例化时检查。循环初始化检查使用保守的回边合并。规范 §19 的并发示例依赖尚未实现的运行时。

实现流程是自写 AST 前端、静态类型与所有权检查、LLVM C++ API 生成和验证目标 IR。编译期解释器执行未优化的类型化 LLVM 控制流，使用目标布局和虚拟存储。当前没有设计文档中独立的 HIR、MIR 和通用初始化数据流求解器。Unicode 表随源码分发，生成脚本使用 Unicode 17.0 的 Node.js，构建编译器无需 Node.js 或 ICU。

## Cortex-M3 裸机链接

BSP 示例为 STM32F103C8 提供初始栈顶、向量表和 `.data`、`.bss` 初始化，随后调用用户程序的 `export "C" func board_main() void`。对没有额外算术辅助符号的程序，可按下面的命令链接。

```bash
arm-none-eabi-gcc -mcpu=cortex-m3 -mthumb -c \
  boards/stm32f103c8/startup.S -o build/startup.o
build/host/vaqc build app.vqr --target thumbv7m-none-eabi --cpu cortex-m3 \
  --profile freestanding --cc arm-none-eabi-gcc \
  --linker-script boards/stm32f103c8/board.ld \
  --link-input build/startup.o -o build/firmware.elf
arm-none-eabi-objcopy -O binary build/firmware.elf build/firmware.bin
```

验收对固件检查向量表地址和未定义符号。板上启动、MMIO 实际行为以及 Windows/macOS 宿主链接和调试仍需相应平台环境验证。
