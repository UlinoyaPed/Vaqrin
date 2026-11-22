# Vaqrin 标准包实现与依赖矩阵

本表以当前集成源码为依据。签名计数来自 59 包的设计清单，包含历史记录。直接导入从 `.vqr` 源码核对。源码存在、编译通过和运行通过是独立证据。「部分实现，已运行代表用例」表示有源码及运行证据，设计合同仍有缺口或待核验签名。「未实现」表示当前集成没有包级源码。单独通过编译或静态审计的路径在各实现文档中注明平台和范围。

| 包 | 设计类型 | 设计成员 | 设计函数 | 历史移除 | 当前状态 | 已覆盖范围及主要缺口 | 源码直接导入 |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| `core` | 2 | 0 | 11 | 1 | 部分实现，已运行代表用例 | Box 的持有、原始部件和自定义分配，通用可空及结果助手 | — |
| `opt` | 0 | 0 | 29 | 0 | 部分实现，已运行代表用例 | 可空组合、原地取换、迭代与容器桥接。`asRef` 和 `asMutRef` 的运行用例已覆盖载荷借用。okOr 和 okOrElse 已验缺失转换与惰性错误回调，完整重载矩阵待验 | `coll`, `core`, `iter` |
| `result` | 1 | 0 | 15 | 0 | 部分实现，已运行代表用例 | 错误组合及桥接，通用 tryJoin 待实现 | `coll`, `core` |
| `cmp` | 5 | 3 | 12 | 0 | 部分实现，已运行代表用例 | 标量与浮点排序辅助，通用比较协议待补 | — |
| `convert` | 2 | 0 | 12 | 0 | 部分实现，已运行代表用例 | 检查转换、舍入及 Unicode 标量，更多精度边界待验 | — |
| `meta` | 3 | 0 | 18 | 0 | 部分实现，已运行代表用例 | 布局、能力与接口反射，完整 TargetInfo 和字段布局查询待补 | — |
| `mem` | 20 | 97 | 15 | 0 | 部分实现，已运行代表用例 | 布局、原始槽、RAII、默认与固定缓冲分配器、Shared/Weak、Arena、Pool 和 Cow。Rc、BoxedSlice 待补 | `atomic`, `bits`, `cmp`, `core`, `meta` |
| `cell` | 8 | 35 | 0 | 0 | 部分实现，已运行代表用例 | 动态借用、惰性和单次初始化，所有映射及错误恢复组合待逐项核验 | `atomic`, `core`, `mem`, `opt` |
| `bits` | 3 | 11 | 22 | 0 | 部分实现，已运行代表用例 | 位运算及定宽位流，完整位宽与目标矩阵待验 | `meta` |
| `hash` | 7 | 15 | 8 | 0 | 部分实现，已运行代表用例 | FNV-1a、SipHash-2-4、进程随机种子和显式回调。稳定摘要套件待补 | `bits` |
| `num` | 6 | 31 | 26 | 0 | 部分实现，已运行代表用例 | 定宽整数检查与 Euclidean 除法、BigUint、BigInt、Rational；完整除法和跨位宽属性测试待补 | `bits`, `cmp`, `coll`, `convert`, `meta`, `text` |
| `coll` | 65 | 510 | 34 | 26 | 部分实现，已运行代表用例 | 顺序容器、堆、哈希表和 B 树。含线性消费 Vec 游标、惰性 Map 视图、Entry、Drain/Splice 与 B 树范围游标 | `core`, `hash`, `iter`, `mem` |
| `iter` | 43 | 103 | 63 | 0 | 部分实现，已运行代表用例 | 切片与常用惰性适配器，完整适配器和端点协议待补 | — |
| `algo` | 0 | 0 | 96 | 0 | 部分实现，已运行代表用例 | 查找、排序、堆及切片操作，含稳定排序、稳定分区和分界二分。其余序列算法待补 | `cmp`, `core` |
| `bytes` | 5 | 37 | 21 | 0 | 部分实现，已运行代表用例 | 字节查找、切分、模式和缓冲，完整变换与池化待补 | `algo`, `cmp`, `coll`, `core`, `mem` |
| `text` | 7 | 31 | 50 | 0 | 部分实现，已运行代表用例 | UTF-8 所有权、标量与构建器，字素、全文规范化和完整文本操作待补 | `coll` |
| `unicode` | 8 | 8 | 28 | 0 | 部分实现，已运行代表用例 | Unicode 17 属性、四种规范化和字素簇。通过官方规范化和字素边界向量验收，含分段流式规范化 | `coll`, `convert`, `core`, `text` |
| `encoding` | 10 | 23 | 18 | 0 | 部分实现，已运行代表用例 | Hex、Base64、百分号、ASCII、Latin-1、UTF-8、UTF-16，完整流式编码套件待补 | `bytes`, `coll`, `convert`, `core`, `meta`, `text` |
| `binary` | 3 | 34 | 22 | 0 | 部分实现，已运行代表用例 | 端序游标与 VarUint/ZigZag，其余协议和类型待补 | — |
| `parse` | 6 | 16 | 18 | 0 | 部分实现，已运行代表用例 | 整数、f32/f64 整串和前缀解析、布尔、标量和扫描。结构化解析错误和日期解析待补 | `bytes`, `convert`, `core`, `text` |
| `fmt` | 9 | 14 | 15 | 0 | 部分实现，已运行代表用例 | Writer、字串、整数、浮点、宽度精度及位置参数。通用 Display/Debug 与编译期格式计划待补 | `coll`, `convert`, `io`, `mem`, `text` |
| `regex` | 7 | 32 | 6 | 0 | 部分实现，已运行代表用例 | Thompson NFA、编号与命名捕获、分支、重复及资源上限。环视待补 | `coll`, `convert`, `core`, `text`, `unicode` |
| `math` | 4 | 22 | 75 | 0 | 部分实现，已运行代表用例 | IEEE 754 分类、舍入及 C 数学入口；区间向外舍入与其余数学合同待补 | `cmp`, `convert`, `math_ffi`, `meta` |
| `random` | 12 | 30 | 16 | 0 | 部分实现，已运行代表用例 | 可复现 PCG64、xoshiro256**、基础分布及 GNU/Linux 系统熵源。各分布统计验收待补 | `coll`, `convert`, `io`, `math`, `mem`, `meta`, `text` |
| `simd` | 2 | 25 | 14 | 0 | 部分实现，已运行代表用例 | 定宽标量回退，硬件向量加速和多目标验收待补 | `mem` |
| `linalg` | 8 | 64 | 18 | 0 | 部分实现 | 稠密向量、矩阵、范数、LU、Householder QR、线性求解和最小二乘。张量、步幅视图、SVD 与特征值待补 | `coll`, `math`, `num` |
| `units` | 5 | 10 | 7 | 0 | 未实现 | 暂无标准包源码 | — |
| `io` | 21 | 38 | 26 | 0 | 部分实现，已运行代表用例 | 流协议、内存流、组合流、POSIX 标准流、定位 I/O 与可恢复部分进度。非阻塞轮询和异步流待补 | `bytes`, `coll`, `mem`, `text` |
| `bufio` | 7 | 35 | 7 | 0 | 部分实现，已运行代表用例 | 复用缓冲、按块扫描行、短写恢复、ASCII 与自定义字节分隔、UTF-8 与二进制 Token | `coll`, `io`, `mem`, `parse`, `text` |
| `path` | 5 | 26 | 18 | 0 | 部分实现，已运行代表用例 | 拥有型 POSIX UTF-8 词法路径，原生非 UTF-8、Windows 路径和更多相对路径运算待补 | `coll`, `text` |
| `fs` | 16 | 53 | 31 | 0 | 部分实现，已运行代表用例 | POSIX 文件身份、原子替换、目录遍历与清理、符号链接和临时资源。MemFS 与其他系统端口待补 | `coll`, `io`, `path`, `text` |
| `time` | 17 | 79 | 17 | 0 | 部分实现，已运行代表用例 | 时长、单调时钟、定时、公历格式化及 GNU/Linux 宿主 TZif 数据库 | `binary`, `cmp`, `coll`, `fmt`, `fs`, `text` |
| `env` | 3 | 8 | 17 | 0 | 部分实现，已运行代表用例 | 进程环境读写、独立快照和路径查询。参数、主机信息、原生字节与变量展开待补 | `coll`, `io`, `path`, `text` |
| `process` | 6 | 31 | 10 | 0 | 部分实现，已运行代表用例 | POSIX 命令执行和子进程句柄、环境覆盖、工作目录、标准流与文件重定向、退出状态、有上限的双流捕获及异步管道读写 | `coll`, `env`, `fs`, `io`, `path`, `text` |
| `net` | 14 | 58 | 14 | 0 | 部分实现，已运行代表用例 | IPv4/IPv6、TCP 与 UDP、正向系统解析、网络字节序、连接及 I/O 超时。含可挂起的 TCP、UDP 和系统 DNS。多播与其他平台待补 | `binary`, `coll`, `fmt`, `io`, `mem`, `parse`, `reactor`, `task`, `text`, `time` |
| `reactor` | — | — | — | 0 | 部分实现，已运行代表用例 | Linux epoll 注册、修改、注销、就绪等待与 fd 复用防护 | `coll`, `io`, `time` |
| `http` | 15 | 55 | 12 | 0 | 部分实现 | HTTP/1.x 报文读写、同步和异步 HTTP/HTTPS、串行连接复用、流式响应读取和分块请求上传及有界空闲连接池 | `bufio`, `coll`, `fmt`, `io`, `net`, `parse`, `text`, `time`, `tls`, `url` |
| `json` | 12 | 45 | 13 | 0 | 部分实现，已运行代表用例 | 嵌套 DOM、精确数字词法、代理对、指针及 Codec。编码支持递归键排序、非 ASCII 转义和 f64 转换；完整 RFC 向量待补 | `algo`, `bytes`, `cmp`, `coll`, `convert`, `core`, `fmt`, `io`, `iter`, `math`, `opt`, `parse`, `serde`, `text` |
| `log` | 10 | 33 | 10 | 0 | 部分实现，已运行代表用例 | 级别、结构化属性、同步多 Handler、JSON Lines 及有界异步 Handler | `channel`, `coll`, `io`, `iter`, `json`, `task`, `text`, `time` |
| `atomic` | 5 | 23 | 7 | 0 | 部分实现，已运行代表用例 | 目标受支持位宽上的原子操作，完整硬件目标矩阵待验 | — |
| `sync` | 19 | 76 | 6 | 0 | 部分实现，已运行代表用例 | 互斥锁、条件变量、等待组、单次初始化，读写锁与信号量等待补 | `atomic`, `cell`, `core`, `opt`, `time` |
| `thread` | 6 | 24 | 13 | 0 | 部分实现，已运行代表用例 | 原生线程、停止、join 与睡眠，TLS、亲和性和构建器待补 | `atomic`, `opt`, `time` |
| `task` | 11 | 47 | 15 | 0 | 部分实现，已运行代表用例 | 任务组、结果领取、计时与取消，任务上下文、阻塞适配和完整调度 API 待补 | `opt`, `result`, `time` |
| `channel` | 12 | 38 | 9 | 0 | 部分实现，已运行代表用例 | MPSC 缓冲、rendezvous、轻量任务挂起、取消、超时和 select。Broadcast 与 Watch 待补 | `atomic`, `coll`, `core`, `mem`, `opt`, `sync`, `time` |
| `parallel` | 3 | 6 | 14 | 0 | 未实现 | 暂无标准包源码 | — |
| `errors` | 6 | 26 | 10 | 0 | 部分实现，已运行代表用例 | 错误身份、上下文、原因链与多错误聚合；错误名称反射与序列化待补 | `coll`, `io`, `iter`, `log`, `mem`, `text` |
| `debug` | 4 | 4 | 18 | 0 | 未实现 | 暂无标准包源码 | — |
| `testing` | 11 | 38 | 17 | 0 | 未实现 | 暂无标准包源码 | — |
| `serde` | 6 | 16 | 4 | 0 | 部分实现，已运行代表用例 | 显式序列化协议、Codec、JSON/TOML 适配及记录类派生。其余编码后端待补 | `coll`, `convert`, `opt`, `parse`, `text` |
| `cbor` | — | — | — | 0 | 部分实现，已运行代表用例 | 二进制 serde Codec，含有界解码、定长或不定长容器和文本键 | `binary`, `coll`, `convert`, `core`, `fmt`, `parse`, `serde`, `text` |
| `csv` | 3 | 10 | 4 | 0 | 部分实现，已运行代表用例 | 带引号换行、双引号、可选表头及 Reader/Writer；更大方言组合待验 | `coll`, `io`, `text` |
| `toml` | 3 | 5 | 5 | 0 | 部分实现，已运行代表用例 | 基本值、多行字符串和数组、表数组、表定义规则、日期时间和 f64 Codec | `coll`, `convert`, `core`, `fmt`, `io`, `math`, `opt`, `parse`, `serde`, `text` |
| `url` | 4 | 27 | 9 | 0 | 部分实现，已运行代表用例 | URI 组件、百分号、IPv6 和重复查询键；IDNA、IRI、zone identifier 待补 | `coll`, `encoding`, `text` |
| `compress` | 4 | 8 | 6 | 0 | 未实现 | 暂无标准包源码 | — |
| `crypto` | 5 | 19 | 8 | 0 | 未实现 | 暂无标准包源码 | — |
| `tls` | 4 | 9 | 4 | 0 | 部分实现，已运行代表用例 | OpenSSL 客户端及服务端、ALPN、CA、SNI、超时与加密流，支持异步握手及读写。双向认证与会话复用待补 | `coll`, `io`, `mem`, `net`, `text`, `time` |
| `protobuf` | — | — | — | 0 | 部分实现，已运行代表用例 | Wire Format、标量、子消息、Packed 字段、保留未知字段及 Codec。proto3 schema 代码生成 | `binary`, `coll`, `core`, `mem`, `text` |
| `http2` | — | — | — | 0 | 部分实现，已运行代表用例 | nghttp2 支持的连接、帧、HPACK、流、窗口和退出事件。TLS ALPN 与显式 h2c | `binary`, `bytes`, `coll`, `io`, `mem`, `text`, `tls` |
| `grpc` | — | — | — | 0 | 部分实现，已运行代表用例 | 四种 RPC、元数据、状态、消息帧、Deadline 和取消。生成客户端与服务端适配器，支持 gzip 和客户端交错调用。一元和流式服务端 handler 可并发调度，流式发送支持 HTTP/2 背压。生成的服务端适配器使用同步接口，优雅关闭待补 | `binary`, `bytes`, `channel`, `cmp`, `coll`, `core`, `encoding`, `fmt`, `http2`, `io`, `net`, `parse`, `task`, `text`, `time`, `tls`, `url` |
| `context` | 3 | 12 | 5 | 0 | 未实现 | 暂无标准包源码 | — |
| `asyncstream` | 5 | 10 | 9 | 0 | 未实现 | 暂无标准包源码 | — |
| `persistent` | 5 | 28 | 0 | 0 | 未实现 | 暂无标准包源码 | — |
| `ffi` | 12 | 13 | 11 | 0 | 未实现 | 暂无标准包源码 | — |
| `mmio` | 3 | 12 | 8 | 0 | 部分实现，已运行代表用例 | 地址、对齐、volatile 读写和 ARM 屏障，其他架构与通用权限封装待补 | `meta` |

v0.1.4 的安全容器查询覆盖 Vec、HashMap 和 BTreeMap。引用来源与裸迭代器的边界见 [容器实现](coll-container-implementation.md)。

## 当前源码的依赖顺序

标准库包含 48 个有公开源码的包，以及供 `math` 使用的内部 `math_ffi` 包。内部包声明目标 C ABI 的数学符号。以下依赖图无环。

```text
atomic, binary, cmp, convert, core, hash, iter, math_ffi, meta
algo, bits, math, mmio, time
mem
coll, simd
bytes, opt, result, text
cell, encoding, io, num, parse, path, random, task, thread, unicode
bufio, csv, fmt, fs, log, regex, serde, sync, url
channel, env, errors, json, linalg, net, toml
http, process
```

容器和 I/O 的具体入口与验证边界见[容器实现](coll-container-implementation.md)和[I/O 实现](io-stack.md)。[全量 API 设计清单](Vaqrin-stdlib-API-complete-draft.md)及其 CSV/JSON 保留目标合同。
