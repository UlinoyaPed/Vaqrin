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
`import` 中可补全项目包名、`package.toml` 中的本地路径依赖和带引号的相对 `.vqr` 路径。
导入后可补全包名或 `as` 指定的别名，
输入 `report.` 可查看该文件的公开成员。
语法和类型错误来自 `vaqc`，编辑未保存时也会显示错误码与位置。悬停优先显示已检查的绑定类型，
方法签名会按接收者展开 `Self`。输入调用参数时会高亮当前形参，并按实参数量优先选中匹配的重载。
显式类型实参会代入形参和返回类型，例如 `parseInt<i32>(value: str) !i32`。元组和嵌套泛型
里的逗号保留在原实参里，函数值的间接调用也能显示签名。
`value?` 和 `lookup()!?` 的行内提示显示逐层解包后的类型。
Zed 可跳转到定义、声明、类型定义和接口实现。接口实现查找使用 `#implements`，覆盖当前包、
导入文件以及此仓库中的标准库和示例。只有一处声明的符号，其定义与声明会跳到同一位置。

仓库的 `.zed/settings.json` 为 Vaqrin 打开行内类型提示。在其他项目里可从命令面板运行
`editor: toggle inlay hints`，或在语言设置中启用 `inlay_hints.enabled`。使用本仓库编译器时，
`:=`、`for` 和 `some(...)` 等绑定会显示编译器推断的类型，例如 `num i32 := ...`、
`for i:i32 in ...` 和 `some(v:*i32)`。`var &item = ptr`、`some(ref item)` 和 `some(&item)`
按实际权限显示 `&T` 或 `&const T`。函数签名保留兼容参数的 `in &const T`。遇到错误时，保留编译器已检查到的绑定类型，并尽量显示源码索引
可推断的提示。仓库将编辑后的行内提示等待时间设为 100 毫秒。函数补全会放入调用括号并将光标留在其中。仓库已打开
`show_signature_help_after_edits`，让 Zed 在接受函数补全后显示调用签名。

Zed 1.24.4 的同内容对照中，Zed 会向语言服务器发送 Git 忽略文件的打开通知并接收诊断，
但只对未被忽略的文件发出 `textDocument/inlayHint`。`file_scan_inclusions` 可纳入文件扫描，
在该版本的实测中仍未触发行内提示请求。插件按请求返回推断类型，没有检查 Git 状态。
要在该版本查看行内提示，可以把练习文件放在 Git 忽略规则之外，文件可以继续保持未跟踪。
本地被忽略的练习文件仍可通过悬停查看类型。

## 格式化

使用 Zed 的格式化命令，扩展会将当前缓冲区交给 `vaqc fmt -`，按 Zed 的缩进宽度和制表符设置生成编辑。
语法无效时，手动格式化会返回解析错误。编辑中的语法先独立于导入检查，语法通过后再做类型检查。
编译器检查提供 UTF-16 错误范围。Windows 支持从 `python3`、`py -3` 或 `python`
启动语言服务器，包括带盘符的本地文件 URI。

## 构建与运行任务

- `run code` 使用现有编译器构建当前文件。若编译器和记录的依赖未变，直接运行缓存程序。
- `build vaqc and run` 先用 Ninja 更新编译器，再按常规规则运行当前文件。
- `rerun last successful executable` 直接运行该文件最近一次成功生成的程序，源码可能已经更改。

阶段信息写入 stderr，看到 `---running …---` 后即可向程序输入。
程序缓存位于 `build/host/run/`，运行时对象使用 `XDG_CACHE_HOME/vaqrin/runtime/`
或 `~/.cache/vaqrin/runtime/`。常规运行从仓库源码读取标准库和运行时，模块搜索路径的
变动会使相关缓存失效。

## 调试

开发扩展提供 `Vaqrin` 适配器，通过 PATH 上的 `lldb-dap` 处理断点、单步和变量查看。
适配器代码与配置结构分别位于 [src/lib.rs](src/lib.rs) 和
[debug_adapter_schemas/vaqrin.json](debug_adapter_schemas/vaqrin.json)。
项目的 [.zed/debug.json](../../.zed/debug.json) 使用已构建的 `build/host/vaqdbg` 编译当前
保存的 `.vqr` 文件，随后启动调试会话。各文件的调试可执行文件按相对路径存放在 `build/debug/`。
调试程序默认在集成终端里读取标准输入并显示标准输出和标准错误。Debug Console 用于调试器命令和表达式求值。
在 Linux 等支持 Unix 域套接字的系统，断点暂停后将鼠标移到当前线程顶层栈帧的局部变量上，悬停会显示
`var total: i32 = 42` 这样的值。继续运行时会清除暂停值。旧版 Zed 通过适配器和语言服务器共享这组值，
支持原生调试悬停的版本还可以通过编辑器直接查看和展开调试值。
编译器命令和安装布局见[调试说明](../../compiler/README.md#调试)。

## 查找引用与重构

当前编译器提供本地绑定、顶层函数、全局值及命名类型的语义索引。语言服务器检查同目录源文件及
已解析的导入，包括未保存的缓冲区。查找引用和重命名使用编译器选中的声明。重命名
会再次检查编辑结果，并在名称解析改变时拒绝生成编辑。代码操作可为局部绑定加上
已推断的类型，也可格式化当前文档。格式化统一二元运算符与赋值的空格，并可在无注释
的长调用实参之间折行。
