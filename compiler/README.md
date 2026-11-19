# Vaqrin 编译器

`vaqc` 将 `.vqr` 源文件编译为原生产物。当前实现以 [语言规范 v0.1.2](../docs/language-spec-v0.1.2.md) 和 [实现设计 v0.1.2](../docs/language-implementation-v0.1.2.md) 为依据，覆盖核心语言子集。

## 构建和运行

在仓库根目录执行。使用 CMake 3.24+、Ninja、C++20 编译器和 LLVM 开发包。本机验证使用 LLVM 22.1.8。选择文档的 LLVM 20.1.8 基线时，将 `LLVM_DIR` 指向对应 SDK，并可添加 `-DVAQRIN_REQUIRE_BASELINE=ON` 强制精确版本。

```bash
cmake -S compiler -B build/host -G Ninja \
  -DCMAKE_CXX_COMPILER=clang++ -DLLVM_DIR="$(llvm-config --cmakedir)"
cmake --build build/host
ctest --test-dir build/host --output-on-failure
build/host/vaqc --version
build/host/vaqc build examples/hello.vqr -o build/hello
./build/hello
```

示例依次输出 `Hello, Vaqrin!`、`1`、`4` 和 `9`。宿主链接默认调用 `clang`，归档默认调用 `llvm-ar`。可用 `cmake --install build/host --prefix <安装目录>` 安装 `bin/vaqc` 和标准包源码。

```bash
build/host/vaqc check examples/hello.vqr
build/host/vaqc build --emit=llvm-ir examples/hello.vqr -o build/hello.ll
build/host/vaqc build -O2 -g examples/hello.vqr -o build/hello
```

默认产物是宿主可执行文件，入口是 `func main() void` 或 `func main() !void`。未处理错误输出到标准错误流，进程退出码为 1。也可生成 `llvm-ir`、`bc`、`asm`、`obj`、`staticlib` 和 `sharedlib`。生成库或对象时允许省略入口，供 C 消费者使用的符号通过 `export "C"` 声明。`--emit=ast` 将语法树写到标准输出。`check` 解析、类型检查并验证生成的 IR，无需链接。

传入目录时，编译器读取其中的同包 `.vqr` 文件。传入文件时，可以在命令行列出同包的多个源文件。本地导入按目录查找，额外搜索根通过 `--package-path DIR` 提供。命令选项见 `vaqc --help`。标准包可用 `import (core, collections, text, io);` 或带 `std/` 前缀的路径导入。参见[标准包说明](../stdlib/README.md)。

在 Zed 中打开仓库根目录，`task: spawn` 可启动 `build vaqc`。已有绑定指向 `run code` 时，当前文件是 `.vqr` 就在构建编译器后编译并运行它，其他文件只触发编译器构建。任务首次配置时生成 `build/host/compile_commands.json`，供 clangd 使用。

## 当前覆盖

| 领域 | 已实现 |
| --- | --- |
| 前端 | UTF-8 校验、Unicode 17.0 XID 和 NFC、字面量、包和文件内导入、别名与可见性 |
| 数值和控制流 | 1–128 位整数的前端类型、f32/f64、目标宽度整数、检查/回绕/饱和运算、转换、短路和比较链、分支、范围与数组循环、`iter()` / `next()` 协议、switch |
| 值和资源 | 元组、固定数组、切片、普通指针、可空值与错误结果、类和构造器委托、带载荷枚举、复制和显式移动、析构、默认分配器的 Box 与 Vec |
| 函数和编译期 | 函数指针、显式捕获 Lambda、受支持签名的重载、函数和类的类型泛型、部分泛型实参推导、函数编译期值参数、可解释的常量和函数 |
| 目标接口 | Linux 宿主 I/O、标量 C ABI、词法 unsafe、普通指针转换、volatile 访问、DWARF 函数与基础局部信息、Cortex-M3 固件链接 |

目前缺少完整任务与通道运行时、`select`、接口派发、嵌入方法提升、Unicode 字素分割、通用迭代器组合、独立的目标运行时和 C 聚合 ABI 桥。Box、Vec 与拥有型动态文本的默认分配器在 Linux 宿主可用，自定义分配器接口和不可移动值的通用按值就地构造仍待实现。包管理使用本地目录，暂未实现 `package.toml`、锁文件和依赖获取。泛型主要在实例化时检查。循环初始化检查使用保守的回边合并。规范 §19 的并发示例依赖尚未实现的运行时。

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
