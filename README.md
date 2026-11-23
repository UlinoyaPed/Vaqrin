# Vaqrin

Vaqrin 是一门原生编译的静态类型系统语言，源文件使用 `.vqr` 后缀。编译器命令为 `vaqc`。当前实现覆盖 v0.1.5 的核心语言子集。

## 快速开始

在仓库根目录使用 CMake、Ninja、Clang 和 LLVM 开发包构建。

```sh
cmake -S compiler -B build/host -G Ninja \
  -DCMAKE_CXX_COMPILER=clang++ -DLLVM_DIR="$(llvm-config --cmakedir)"
cmake --build build/host
ctest --test-dir build/host --output-on-failure
build/host/vaqc build examples/hello.vqr -o build/hello
./build/hello
```

## 仓库结构

| 目录 | 内容 |
| --- | --- |
| [compiler/](compiler/) | C++20 编译器、CMake 构建、测试和 Unicode 表生成工具 |
| [stdlib/](stdlib/) | 用 Vaqrin 编写的标准包 |
| [examples/](examples/README.md) | 从语法入门到标准库和多文件项目的示例路线 |
| [boards/](boards/) | Cortex-M3 板级启动和链接文件 |
| [docs/](docs/) | 语言规范、实现设计和使用文档索引 |

当前功能范围、安装和交叉编译说明见[编译器使用说明](compiler/README.md)。


### 项目依赖

在项目目录的 `package.toml` 中声明依赖。依赖目录须含声明了对应包名的 `.vqr` 源文件，也可在自己的 `package.toml` 中声明传递依赖。

```toml
[build]
entry = "main.vqr"

[dependencies]
models = { path = "../shared/models" }
```

运行 `vaqc lock .` 后，将 `package.lock` 与清单一同提交。构建和语义检查会核对锁文件中的相对依赖路径和 SHA-256 源文件摘要。依赖路径或内容更新后，重新运行 `vaqc lock .`。构建可离线执行。Git 和注册表依赖同样使用锁文件和离线缓存，参见[项目依赖](compiler/README.md#项目依赖)。

编译器按源码内容与编译器版本缓存大型标准库模块的语法树和定型整数字面量表。
Unicode 数据表可跨项目复用编译后的常量字节。模块类型和依赖仍按当前项目检查。
可用绝对路径形式的 `VAQRIN_AST_CACHE` 指定缓存目录，默认使用
`$XDG_CACHE_HOME/vaqrin/syntax` 或 `~/.cache/vaqrin/syntax`。将该变量设为 `0` 可禁用缓存。

在重复构建中，可以用绝对路径 `VAQRIN_MODULE_CACHE` 启用优化后的 LLVM bitcode 缓存。
该缓存用于 `-O1` 至 `-O3`，按当前解析并实例化后的 IR、编译器及目标参数查找。
`--timings` 会显示缓存命中或未命中。原生代码生成和链接使用当前工具链。
