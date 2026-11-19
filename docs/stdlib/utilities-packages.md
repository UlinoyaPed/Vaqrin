# URL、日志与错误报告标准包

本次增加 `url`、`log`、`errors` 三个包。包源码仅依赖 master 已有的 `coll`、`encoding`、`text`、`io`、`time` 和内建 `ErrorId`。不执行 DNS 查询、网络请求或系统日志调用。

## url

`Url.parse` 解析 URI 引用；`Url.resolve` 按 RFC 3986 的引用合并模型解析相对引用；`normalize` 处理 scheme/host 大小写、百分号十六进制大写、未保留字符解码和点段。查询参数采用有序键值序列，重复键不会丢失。query/path escape 以 UTF-8 字节为输入，非 ASCII 字节百分号编码；解码输出必须是有效 UTF-8。加号始终是字面加号，不使用 application/x-www-form-urlencoded 的空格规则。

`Url.host()` 对 IPv6 返回不含方括号的地址文本，序列化时恢复方括号。端口仅接受 0..65535。URL 对象拥有其文本组件；查询迭代器借用 QueryParams 的底层条目，不能超过 QueryParams 的生命周期或在其发生修改后继续使用。

当前实现针对通用 URI/URL 的 ASCII 语法与 UTF-8 百分号编码。IPvFuture 仅作有限语法检查；IPv6 校验不提供地址转换或 DNS。IDNA、IRI 到 URI 转换、默认端口消除、完整 RFC 3986 正则语法矩阵仍待补充。

## log

同步 Handler 接口通过 `io.Writer` 输出。Logger 在调用 Handler 前深复制属性和消息，Record/BufferHandler 继续拥有记录内容，因而不保留临时字符串或调用方属性切片。Logger 的 Handler 指针为借用，必须比 Logger 活得更久；TextHandler 的 Writer 指针同理。BufferHandler 拥有记录，clear/deinit 负责释放。Handler 错误通过 `!void` 返回。JSON Lines、异步 Handler、分组属性和宿主 syslog 不在本阶段实现。

## errors

Report 以值所有权保存 ErrorId、上下文帧和原因树，不持有日志 Handler，也不依赖全局错误注册表。原因树由拥有型 Vec 递归嵌套，不保存 Report 指针，因此 API 不提供可形成引用环的边。SourceLocation 和 Frame 均复制拥有型文本与属性。Report.format/formatString 使用 Writer 并传播 I/O 错误。

内建 ErrorId 在本语言中是稳定错误身份句柄，本包不重新定义错误传播，也不把 ErrorId 映射成猜测的数字或宿主字符串；fromError 当前使用通用消息 `error`，格式化时输出该错误标记。需要具体错误名称时由调用方添加上下文。
