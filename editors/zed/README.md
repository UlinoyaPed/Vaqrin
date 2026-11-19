# Vaqrin for Zed

从仓库根目录执行 `./editors/zed/prepare-dev-extension.sh`。在 Zed 命令面板运行
`zed: install dev extension`，选择命令输出的 `build/zed-extension` 目录。
打开 `examples/hello.vqr` 或其他 `.vqr` 文件，右下角语言应显示为 Vaqrin。

本扩展使用 Tree-sitter 为关键字、声明、内建类型、属性、注释、字面量和运算符着色。
语法树主要服务于着色。修改 `grammar/grammar.js` 后，在 `grammar/` 中运行
`tree-sitter generate --abi 14`，重新准备扩展，再在 Zed 中重载开发扩展。
生成的本地语法仓库位于忽略的 `build/` 下，主仓库无需发布即可安装试用。
