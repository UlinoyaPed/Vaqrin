# Vaqrin 编译器

`vaqc` 将 `.vqr` 源文件编译为原生产物。当前实现覆盖 [v0.1.5 语言规范](../docs/language-spec-v0.1.5.md) 的核心语言子集。

`value?` 传播空值，`result!` 传播错误，`lookup()!?` 可依次检查两个通道。
宿主程序使用 `-g` 构建时，断言式 `.unwrap()` 会在 trap 前输出文件、行号和函数。
可在 [Optional 示例](../examples/language/optional.vqr) 中试用传播、后备值和显式错误转换。

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
build/host/vaqc run examples/hello.vqr
```

示例输出 `Hello, Vaqrin!`。更多程序和预期输出见[示例学习路线](../examples/README.md)。宿主链接默认调用 `clang`，归档默认调用 `llvm-ar`。可用 `cmake --install build/host --prefix <安装目录>` 安装 `bin/vaqc` 和标准包源码。

```bash
build/host/vaqc check examples/hello.vqr
build/host/vaqc check --syntax-only --diagnostics=json examples/hello.vqr examples/basics/collections.vqr
build/host/vaqc fmt --check examples/hello.vqr
build/host/vaqc fmt --tab-size=4 --write examples/hello.vqr
build/host/vaqc build --emit=llvm-ir examples/hello.vqr -o build/hello.ll
build/host/vaqc build -O2 -g examples/hello.vqr -o build/hello
```

默认产物是宿主可执行文件，入口支持 `func main() void`、`func main() !void`、`func main() i32` 和 `func main() !i32`。成功的整数结果用作进程退出码，未处理错误输出到标准错误流并以 1 退出。也可生成 `llvm-ir`、`bc`、`asm`、`obj`、`staticlib` 和 `sharedlib`。生成库或对象时允许省略入口，供 C 消费者使用的符号通过 `export "C"` 声明。`--emit=ast` 将语法树写到标准输出。`check` 解析、类型检查并验证生成的 IR，无需链接。

`vaqc run file.vqr` 在临时目录构建宿主可执行文件。程序继承终端输入和输出，退出时返回程序的退出码并清理临时产物。用 `--` 分隔传给程序的参数。

`check --syntax-only` 逐个检查源文件的语法，单个文件出错后继续检查其他文件。加上
`--diagnostics=json` 后，每条诊断占标准错误流的一行，位置采用从零开始的 UTF-16 范围。
`fmt` 支持打印、检查、原位写入和从标准输入读取，默认使用四个空格缩进，可用
`--tab-size=N` 和 `--use-tabs` 调整。

对未保存的缓冲区，可以用 `vaqc check --syntax-only --diagnostics=json -` 从标准输入检查语法。
诊断中的路径显示为 `<stdin>`。

`--dependencies` 使用单独的 `.json` 路径，并在清单写完后原子替换文件。POSIX 上，编译器
仅从当前用户持有且防止其他用户写入的缓存加载语法和编译产物。不可验证的缓存会重新生成对应产物。

文件头可以省略 `package`。`package demo` 和 `import (io)` 的分号可选。目录构建优先采用 `--entry`，其次采用 `package.toml` 的 `[build].entry`，最后递归查找唯一入口。两处设置都支持 `demo::main`，按源文件的 `package demo` 声明选择唯一的 `main`。同名包存在多个入口时会列出候选源文件。文件路径选择仍可用于现有项目和显式源码列表。

同目录匿名文件可以直接引用唯一的辅助声明，也可用 `helpers.name` 限定。跨目录使用 `import ("lib/helpers.vqr" as h)` 导入目标的公开声明。有名包通过逻辑包名导入，额外搜索根由 `--package-path DIR` 指定。标准包也支持 `std/` 前缀。参见[标准包说明](../stdlib/README.md)。

在 Zed 中打开仓库根目录，`task: spawn` 可启动 `build vaqc`。`run code` 使用已有的编译器运行当前 `.vqr`
文件，`build vaqc and run` 会先更新编译器。任务首次配置时生成 `build/host/compile_commands.json`，供 clangd 使用。

## 调试

`vaqdbg` 先用同一构建或安装中的 `vaqc` 以 `-O0 -g` 生成程序，再启动系统的 LLDB。
传 `-o` 可保留可执行文件，省略时使用调试会话的临时目录。也可传 `--build-only -o PATH`
只构建调试目标。若需指定工具路径，可用 `VAQC` 和 `VAQRIN_LLDB`。

```sh
cmake --build build/host --target vaqdbg
build/host/vaqdbg examples/language/ownership.vqr
```

| 内容 | 位置 |
| --- | --- |
| 编译器实现和调试信息生成 | [compiler/src/](src/) |
| 调试命令源码 | [compiler/tools/vaqdbg.cpp](tools/vaqdbg.cpp) |
| 命令行与 DAP 验收 | [compiler/tests/debug_acceptance.py](tests/debug_acceptance.py) |
| CMake 构建产物 | `build/host/vaqc`、`build/host/vaqdbg`。安装后位于安装前缀的 `bin/` |
| Zed 适配器与开发扩展 | [editors/zed/](../editors/zed/)、`build/zed-extension/` |
| 仓库的 Zed 调试配置与待调试程序 | [.zed/debug.json](../.zed/debug.json)、`build/debug/` |

## 项目依赖

项目根目录的 `package.toml` 可声明本地目录、固定提交的 Git 包和注册表版本。

```toml
[build]
entry = "main.vqr"

[registry]
index = "index.json"

[dependencies]
models = { path = "../shared/models" }
schema = { git = "https://example.org/team/schema.git", rev = "0123456789abcdef0123456789abcdef01234567", path = "vaqrin" }
offset = { version = "1.2.3" }
```

依赖名是源码的逻辑导入名。本地路径相对于声明该依赖的清单。Git 的 `rev`
使用完整提交哈希。可用 `path` 选择仓库中的包目录。

注册表以 JSON 对象组织包名和版本。版本采用精确字符串匹配。`index` 可以是项目
内的文件路径或 HTTPS URL。每个版本对应固定的 Git 提交，并可指定包目录。

```json
{
  "offset": {
    "1.2.3": {
      "git": "https://example.org/team/offset.git",
      "rev": "abcdef0123456789abcdef0123456789abcdef01",
      "path": "src"
    }
  }
}
```

在项目目录运行 `vaqc lock .` 解析注册表、获取 Git 源码并生成 `package.lock`。
将清单和锁文件一同提交。后续 `vaqc build .` 和语义检查使用锁定的版本、提交和
SHA-256 源文件摘要，可从本地缓存离线执行。依赖修改后重新锁定。CI 可通过绝对路径
`VAQRIN_PACKAGE_CACHE` 保存或迁移离线缓存。首次锁定 Git 依赖需要 `git`，读取
HTTPS 注册表还需要 `curl`。

## 当前覆盖

| 领域 | 已实现 |
| --- | --- |
| 前端 | UTF-8 校验、Unicode 17.0 XID 和 NFC、字面量、可选包头、按文件选择入口、本地包和匿名文件导入、别名与可见性 |
| 数值和控制流 | 1–128 位整数的前端类型、f32/f64、目标宽度整数、检查/回绕/饱和运算、转换、短路和比较链、分支、范围与数组循环、`iter()` / `next()` 协议、switch |
| 值和资源 | 元组、固定数组、切片、普通指针、可空值与错误结果、类、构造器委托、嵌入方法提升、显式接口与动态接口视图、带载荷枚举、解引用绑定与模式借用、复制和显式移动、析构、支持自定义分配器的 Box 与默认分配器的 Vec |
| 函数和编译期 | 函数指针、显式捕获 Lambda、受支持签名的重载、类型与编译期值泛型、泛型接口方法、类内 `comptime if`、可解释的常量和函数 |
| 并发和标准库 | GNU/Linux 任务组、通道、`select`、异步 I/O 和协议栈，Unicode 17 规范化与字素分割 |
| 目标接口 | Linux 和 macOS 的 POSIX 文件 I/O、Windows CNG 系统熵源、标量和普通 C 聚合 ABI、目标宽度原子操作、DWARF 和 CodeView 调试信息、Cortex-M3 固件链接 |

Box、Vec 与拥有型动态文本的默认分配器支持 GNU/Linux 和 macOS。Box 支持显式
分配器和带释放上下文的原始部件转换。不可移动值的通用按值就地构造仍待实现。
泛型主要在实例化时检查。循环初始化检查使用保守的回边合并。规范 §19 的任务示例、
泛型接口和 C 聚合集成都有编译运行验收。平台范围见 [宿主平台与发布](../docs/platforms.md)。

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
