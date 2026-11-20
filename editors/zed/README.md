# Vaqrin for Zed

从仓库根目录执行 `./editors/zed/prepare-dev-extension.sh`。在 Zed 命令面板运行
`zed: install dev extension`，选择命令输出的 `build/zed-extension` 目录。
打开 `examples/hello.vqr` 或其他 `.vqr` 文件，右下角语言应显示为 Vaqrin。

本扩展使用 Tree-sitter 为关键字、声明、内建类型、属性、注释、字面量和运算符着色。
语法树主要服务于着色。修改 `grammar/grammar.js` 后，在 `grammar/` 中运行
`tree-sitter generate --abi 14`，重新准备扩展，再在 Zed 中重载开发扩展。
生成的本地语法仓库位于忽略的 `build/` 下，主仓库无需发布即可安装试用。

## 语言服务器

扩展需要 Python 3.10 或更新版本，以及支持 `vaqc check --overlay` 的编译器。仓库中的 `build/host/vaqc`
可由 Zed 的 `build vaqc` 任务生成，也可从环境的 `PATH` 查找 `vaqc`，或通过 `VAQC`
指定可执行文件。重新生成并重载现有开发扩展后，编辑中的内容会通过独立临时文件交给
编译器检查，真实源文件及其包搜索路径保持原样。

补全根据当前位置列出关键字、内建类型、作用域中的名字、包的公开声明和可解析的成员。
语法和类型错误来自 `vaqc`，编辑未保存时也会显示错误码与位置。悬停优先显示已检查的绑定类型，
方法签名会按接收者展开 `Self`。输入调用参数时会显示候选签名与当前参数位置。
Zed 可跳转到定义、声明、类型定义和接口实现。接口实现查找使用 `#implements`，覆盖当前包、
导入文件以及此仓库中的标准库和示例。只有一处声明的符号，其定义与声明会跳到同一位置。

仓库的 `.zed/settings.json` 为 Vaqrin 打开行内类型提示。在其他项目里可从命令面板运行
`editor: toggle inlay hints`，或在语言设置中启用 `inlay_hints.enabled`。使用本仓库编译器时，
`:=`、`for` 和 `some(...)` 等绑定会显示编译器推断的类型，例如 `num i32 := ...`、
`for i:i32 in ...` 和 `some(v:*i32)`。遇到错误时，保留编译器已检查到的绑定类型，并尽量显示源码索引
可推断的提示。仓库将编辑后的行内提示等待时间设为 100 毫秒。函数补全会放入调用括号并将光标留在其中。仓库已打开
`show_signature_help_after_edits`，让 Zed 在接受函数补全后显示调用签名。

本机验证中，Zed 对 Git 忽略的 `examples/uses/` 下的文件仍能请求悬停和诊断，
但没有发出行内提示请求。同一份 `.vqr` 放到未被忽略的 `examples/` 下时可收到行内提示。

## 构建与运行任务

- `run code` 使用现有编译器构建当前文件。若编译器和记录的依赖未变，直接运行缓存程序。
- `build vaqc and run` 先用 Ninja 更新编译器，再按常规规则运行当前文件。
- `rerun last successful executable` 直接运行该文件最近一次成功生成的程序，源码可能已经更改。

阶段信息写入 stderr，看到 `---running …---` 后即可向程序输入。
程序缓存位于 `build/host/run/`，运行时对象使用 `XDG_CACHE_HOME/vaqrin/runtime/`
或 `~/.cache/vaqrin/runtime/`。常规运行从仓库源码读取标准库和运行时，模块搜索路径的
变动会使相关缓存失效。
