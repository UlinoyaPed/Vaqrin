# Vaqrin

Vaqrin 是一门原生编译的静态类型系统语言，源文件使用 `.vqr` 后缀。编译器命令为 `vaqc`。当前实现覆盖 v0.1.2 的核心语言子集。

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
| [examples/](examples/) | 可直接运行的 Vaqrin 示例 |
| [boards/](boards/) | Cortex-M3 板级启动和链接文件 |
| [docs/](docs/) | 语言规范、实现设计和使用文档索引 |

当前功能范围、安装和交叉编译说明见[编译器使用说明](compiler/README.md)。
