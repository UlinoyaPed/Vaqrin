# URL、日志与错误报告标准包

本次开发新增 `url`、`log`、`errors` 三个独立标准包。源码只依赖当前 master 已有的 `coll`、`encoding`、`text`、`io`、`time`、`mem` 和内建 `ErrorId`。不依赖其他并行分支，也不执行 DNS 查询、网络请求或系统日志调用。

## url

公开类型：`Url`、`UrlBuilder`、`QueryParam`、`QueryParams`、`QueryValueIter`、`UrlError`。

公开入口和方法：

- `Url.parse(value)`、`parse(value)`：解析绝对 URI 或相对引用。
- `Url.resolve(reference)`、`resolve(base, reference)`：按 RFC 3986 合并相对引用；base 必须有 scheme。
- `Url.normalize()`、`normalize(value)`：scheme/host 小写化、百分号十六进制大写、解码百分号编码的 unreserved 字符、移除点段。
- `scheme()`、`host()`、`port()`、`userInfo()`、`path()`、`query()`、`fragment()`、`hasAuthorityComponent()`、`isAbsolute()`、`toString()`。
- `UrlBuilder.init`、`scheme`、`userInfo`、`host`、`port`、`path`、`query`、`fragment`、`build`。
- `QueryParams.init/parse/append/set/get/getAll/remove/items/len/isEmpty/encode`。
- `queryEscape`、`queryUnescape`、`pathEscape`、`pathUnescape`、`parseQuery`、`encodeQuery`、`isAbsolute`。

`UrlBuilder` 采用不可变值式构建：除 `port` 外，setter 返回一个新的 `!Self`，必须接收返回值继续构建，例如 `builder = builder.host("example.com")!`。构建结果统一交给 `Url.parse` 验证。IPv6 host getter 返回不带方括号的文本，序列化会恢复方括号。非空端口接受 0..65535；空端口分隔符会保留在序列化结果中，此时 `port()` 返回 null。

URI 语法按 ASCII 字节解析。非 ASCII 文本应先按 UTF-8 字节百分号编码；`queryEscape` 和 `pathEscape` 自动按 UTF-8 编码。解码成 `string` 时要求结果是有效 UTF-8，否则返回 `UrlError.InvalidUtf8`。非法或不完整的百分号转义返回 `UrlError.InvalidPercentEncoding`。加号始终是字面加号，绝不自动转换为空格；没有实现 application/x-www-form-urlencoded 语义。query escape 只保留 unreserved 字符；path escape 用于单个路径段，会转义斜杠。

`QueryParams` 以有序键值序列存储，保留重复键和插入顺序。解析时按第一个等号分割字段，空字段忽略；`set` 替换第一个匹配项并删除后续重复项，保留该键首次出现的位置。查询迭代器借用参数集合的存储，在集合销毁、修改或可能扩容后不得继续使用。

当前实现覆盖通用 URI 引用、authority、userinfo、reg-name、IPv4/IPv6 literal、IPvFuture 基础语法、端口、路径、查询和片段；IPv4 与 IPv6 literal 使用独立错误标识。IPvFuture 和 IPv6 校验仍需 RFC 测试向量进一步验证；不支持 IDNA、IRI 转换、IPv6 zone identifier，也不自动移除 HTTP/HTTPS 默认端口。解析和序列化完全不依赖网络运行时。

## log

公开类型：`Level`、`AttrKind`、`Attr`、`Record`、`Handler`、`Logger`、`TextHandler`、`BufferHandler`、`MultiHandler`、`LogError`。

公开入口和方法：

- `Level.Trace/Debug/Info/Warn/Error/Fatal`、`parseLevel`、`levelText`。
- `Attr.string/int/bool/error/group`、`key`、`kind`、`asString/asInt/asBool/asError/asGroup`、`clone`、`formatAttr`。
- `Record.level/timestamp/message/attrs/clone/fromOwned`。
- `Handler.enabled/handle/flush`。
- `Logger.new/enabled/setLevel/setClock/log/with/withGroup/flush`。
- `TextHandler.handle/flush`、`BufferHandler.handle/records/len/clear/flush`、`MultiHandler.enabled/handle/flush`。
- `trace`、`debug`、`info`、`warn`、`error`、`fatal`、`flush(logger)`。

属性支持字符串、i64、布尔值、ErrorId 和递归 group。字符串消息、属性键和值会进行行安全转义；group 在 TextHandler 中以点分隔的键路径输出。Attr 工厂返回 `!Self`，因为键和值必须复制为拥有型 string。Logger 在同步调用期间组合固定属性和调用属性，Record 接管拥有型快照；Handler 不得保留传入 Record 的借用指针。BufferHandler 对记录进行深复制。

Logger 持有借用的 `*Handler`；TextHandler 持有借用的 `*io.Writer`；MultiHandler 持有借用的 Handler 指针切片。调用方必须保证这些对象及切片在使用期间有效。日志写入和 flush 的错误通过 `!void` 传播。Logger 的最低级别过滤在构造 Record 之前执行。Record 的时间戳是可选的单调时钟 Instant。Logger 默认不读取系统时钟；调用方可通过 `setClock(*time.Clock)` 注入借用时钟，未注入或读取失败时保留 null，避免标准日志包强制链接宿主时钟。fatal 只写入 Fatal 级别，不主动终止进程。

本阶段实现同步 Handler。JsonHandler、AsyncHandler、syslog 和异步队列未实现；JSON 不会通过未合并的 json 包形成依赖。

## errors

公开类型：`SourceLocation`、`Frame`、`Report`、`ReportCauseIter`、`ErrorSink`、`ReportDrain`、`ErrorSeverity`、`ReportError`。

公开入口和方法：

- `Report.fromError/new`、`message/severity/setSeverity/rootError/frameCount/causeCount`。
- `addContext/addContextAt/withContext/addCause/join/clone`。
- `causes/matches/format/formatString`。
- `SourceLocation.file/startLine/startColumn/endLine/endColumn/function`。
- `Frame.message/location/attrs/clone`。
- `ErrorSink.init/push/len/isEmpty/drain/toReport/clear`。
- `fromError/addContext/withContext/addCause/join/rootError/causes/matches/is/wrap/format/reportTo/assertNoError`。

Report 拥有 ErrorId、消息、上下文帧和递归原因树。原因通过值所有权保存在 Vec 中，没有 Report 间的非拥有指针边，因此不能形成引用环；addCause 接收并消费原因对象。clone 对帧、属性和原因递归深拷贝。rootError 沿第一条原因链向下寻找最深层可用 ErrorId；causes 返回直接原因迭代器。ReportCauseIter 借用 Report 的原因切片，Report 发生修改或销毁后迭代器失效。

ErrorSink.drain 把内部 Vec 的所有权转移给 ReportDrain，按插入顺序产出 Report；toReport 会消费 Sink 内容并构造多原因 Report。Report.format 和 reportTo 直接使用 io.Writer，Writer 错误原样传播，不依赖 log 包的 Handler。Frame 中的 log.Attr 由 errors 自己通过普通 Writer 格式化，不需要创建 Logger 或 Handler。formatString 使用 MemoryWriter 生成拥有型 string。

内建 ErrorId 是编译器管理的错误身份句柄。本包保留原始 ErrorId 并提供匹配能力，但当前编译器没有公开错误集合名/成员名查询接口，因此文本格式只输出通用 `[ErrorId]` 标记；fromError 的默认消息为 `error`。调用方应使用上下文消息补充可读信息。错误传播仍由内建 `!T` 负责，本包不修改编译器错误类型。

## 本地集成

`examples/utilities.vqr` 是待本地执行的综合验收入口，覆盖 URI 解析、相对引用解析、Builder、IPv6、空端口、非法百分号、重复查询参数、加号语义、日志级别过滤、结构化 group、Writer/Buffer Handler、错误链、SourceLocation 和 ErrorSink。

在线开发环境未运行 Vaqrin 编译器，编译与运行结果必须标记为待本地执行。建议合并前优先检查构造器 `!void`、非拥有迭代器生命周期、递归 Vec 类型和接口指针适配；随后运行项目现有 acceptance.py，并补充 RFC 3986 Section 5.4 的 reference resolution vectors。
