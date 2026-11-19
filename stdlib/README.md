# Vaqrin 标准包

可用 `import (core, coll, iter, opt, result, cmp, convert, meta, mem, hash, mmio, bits, algo, atomic, binary, bytes, cell, encoding, simd, text, parse, io, path, fs, bufio, fmt, thread, sync, time, channel, task, num, math, random, json, csv, toml, serde, unicode, regex, url, log, errors)` 导入标准包，也可使用 `std/` 前缀。CMake 在构建目录生成 `share/vaqrin/stdlib`，安装时将标准包源码复制到安装前缀下。编译器按使用情况实例化泛型声明。完整的目标接口见[标准包 API 清单](../docs/stdlib/Vaqrin-stdlib-API-complete-draft.md)。

| 包 | 当前接口 |
| --- | --- |
| `core` | `Box<T>`、可空及错误结果状态检查、`unwrapOr`、`identity`、`drop`、`swap`、`replace`、`unreachable`、`unreachableUnchecked` |
| `coll` | `Vec<T>`、`FixedVec<T,N>`、`VecDeque<T>`、`FixedDeque<T,N>`、`LinkedList<T>`、`Stack<T>`、`Queue<T>`、`BinaryHeap<T>` / `PriorityQueue<T>`、`HashMap<K,V>` / `HashSet<T>`、`BTreeMap<K,V>` / `BTreeSet<T>` |
| `iter` | 按值和按指针遍历切片、单项和空游标、映射、过滤、枚举、组合、反向及截取适配器 |
| `opt` | 可空值的组合、访问、原地替换、容器转换和外层交换 |
| `result` | 错误结果的组合、恢复、观察、只读载荷访问与收集 |
| `convert` | 检查转换、整数与浮点舍入、位转换及 Unicode 标量验证 |
| `cmp` | 标量比较、部分序、浮点总序和上下界助手 |
| `hash` | Hasher / HashKey 协议、带种子的 FNV-1a 32 位哈希及常用整数哈希/相等回调 |
| `num` | 定宽整数边界与检查/回绕/饱和运算、BigUint、BigInt、有理数 |
| `math` | IEEE 754 分类、f32/f64 数学函数、复数与基础区间运算 |
| `random` | PCG64 XSL-RR 128/64、xoshiro256**、均匀分布、洗牌与抽样 |
| `json` | JSON DOM、指针、严格数字词法和 Codec |
| `csv` | 引号、换行、带表头的记录读写 |
| `toml` | 基本类型、数组、嵌套表和 Codec |
| `serde` | 显式序列化协议和 Codec |
| `unicode` | Unicode 17 属性、规范化和字素簇 |
| `regex` | Thompson NFA、分组与有界执行 |
| `url` | URI 解析、解析相对引用、百分号编码及重复查询键 |
| `log` | 级别、结构化属性和同步 Handler |
| `errors` | 错误报告、上下文和多原因聚合 |
| `meta` | 目标布局、类型名称、数组长度、复制、移动、析构、接口实现及线程间移交和共享特性 |
| `mem` | 布局、字节操作、原始槽、手动析构、作用域守卫、强弱共享所有权和默认或固定缓冲分配器 |
| `mmio` | 带边界和对齐检查的寄存器、宽度受限的 volatile 访问、编译器屏障及 ARM 指令屏障 |
| `bits` | 定宽整数位操作、对齐及可指定顺序的位流读写 |
| `algo` | 切片查找、二分、堆操作、排序、翻转和旋转 |
| `atomic` | 原生标量、可空指针、原子标志、比较交换、读改写和内存栅栏 |
| `binary` | 定宽端序转换、带位置的读写游标、VarUint 和 ZigZag |
| `bytes` | 切片查找、预处理字节模式、分割及可增长缓冲区 |
| `cell` | 内部可变值、运行时借用守卫、惰性及单次初始化、跨线程单次发布 |
| `encoding` | Hex、Base64、百分号编码、ASCII、Latin-1、UTF-8 和 UTF-16，含跨块 UTF 校验与解码 |
| `simd` | 定宽标量通道、掩码、载入与存储、重排和归约 |
| `text` | UTF-8 字节相等比较 `equal`、`byteLen`、`scalarCount`、`chars`、`fromBytes`、`StringBuilder` |
| `parse` | 整串及前缀整数解析、布尔值、Unicode 标量、UTF-8 字节偏移游标和空白分词 |
| `io` | Reader/Writer/Seeker/Closer、内存流、组合适配器、标准流、宿主文件描述符及原生错误码 |
| `path` | UTF-8 词法路径、父目录/文件名/扩展名、组件迭代与规范化 |
| `fs` | File、OpenOptions、Metadata、Permissions、目录读取及文件系统操作 |
| `bufio` | BufferedReader/Writer、Scanner、Token、peek/unread、按行和分隔符读取 |
| `fmt` | Writer 格式化、整数/浮点/布尔/字符/字符串、宽度/精度/进制/填充 |
| `thread` | GNU/Linux 原生线程、一次性领取结果、分离、协作停止、当前线程 ID 及 CPU 数 |
| `sync` | 互斥锁、可投影的 RAII 守卫、条件变量、等待组及单次初始化 |
| `time` | 纳秒时长、单调时钟和定时器、公历日期、日内时间及 UTC 墙钟 |
| `channel` | 多发送端、单接收端的有界、无界及零容量通道，含关闭、定时等候和保留未交付值 |
| `task` | 轻量任务组、让出执行线程、定时等待、协作取消和一次性领取结果 |

```cpp
package main
import (coll, iter, io)

func main() !void {
    var values = coll.Vec<i32>();
    values.push(42)!;
    values.push(7)!;
    for index, value in iter.enumerate<iter.SliceIter<i32>, i32>(values.iter()) {
        io.println(index)!;
        io.println(value)!;
    }
}
```

Vec 按值接收可移动元素。其 `reserve` 先分配，再搬迁已有元素，分配失败时保留原缓冲区和元素；`withAllocator` 可绑定自定义分配器，分配器及其上下文必须比容器活得更久。Vec 还提供 `insert`、`remove`、`swapRemove`、`replace`、`retain`、`append`、`splitOff`、容量收缩和显式 `clone`。`pushPreserving` 与 `insertPreserving` 在失败时归还输入值。`asSlice` 返回借用视图，重分配或移动相关元素后原视图失效。

`VecDeque` 使用循环缓冲槽数组，支持两端插入、移除和随机访问；`FixedDeque<T,N>` 使用内联固定槽，容量不足时以 `PushResult.Full(value)` 归还元素。两者均提供正反向借用迭代。Stack 以 Vec 尾部实现 LIFO，Queue 以 VecDeque 实现 FIFO。BinaryHeap 通过显式比较回调和 `HeapOrder.Min/Max` 构造最小堆或最大堆，也提供 `min()` / `max()` 使用 `<` 运算符；PriorityQueue 是同一实现的别名。

LinkedList 使用双向节点和独立 Box 所有权表，节点地址在插入、删除其他节点和链表拼接时保持稳定；按索引访问仍为 O(n)。HashMap/HashSet 使用开放寻址和 tombstone 删除标记，构造时必须提供哈希与相等回调；`HashMap.iterRef()` 构建指针索引，返回 `!MapIterRef`，迭代器只借用键和值，容器结构修改后失效；`hash` 包提供 FNV-1a 32 位实现和 i8/u8/i16/u16/i32/u32/i64/u64/bool 的回调。种子由调用方提供，FNV 不适合抵御主动哈希洪泛。BTreeMap/BTreeSet 使用最小度数为 4 的 B 树，构造时必须提供严格弱序比较回调；迭代器创建时会构建指针索引，因此可能分配失败。关联容器的键只能通过只读借用访问，不能原地修改。

按引用遍历时使用 `iterRef()` 或 `iterMut()`，后者可配合 `for &item in ...` 修改原位置。

文本按字节比较，`chars` 按 Unicode 标量遍历。`fromBytes` 验证 UTF-8 后复制成不可变字符串，`StringBuilder.build()` 生成可独立持有的快照。拥有型 `string` 的副本共享不可变存储，`str` 与字节视图遵守来源的生命周期。

默认堆分配器目前面向 Linux 宿主。Vec、VecDeque、LinkedList 和 HashMap 可绑定 `mem.AllocatorRef`；FixedVec / FixedDeque 不需要堆分配。BTree 节点目前使用默认 Box 分配器。裸机上的 Vec、VecDeque、LinkedList 和 HashMap 可以用显式分配器增长。缺少默认堆且未指定分配器时，支持两种分配方式的容器返回 `AllocatorUnavailable`。Vec 可能持有借用的分配器，因而类型能力检查禁止将其跨线程移交。

位流的 `LsbFirst` 从字节低位开始，也先读写结果或参数的低位。`MsbFirst` 从字节高位开始，从读写宽度所覆盖的最高位开始。读不到足够位时，`BitReader` 返回成功的空值并保持位置。超出写入容量时，`BitWriter` 返回错误并保持缓冲区和位置。

`atomic` 在 x86-64 和 AArch64 上支持 8、16、32、64 位原子操作，在 Thumbv7-M 和 Thumbv7E-M 上支持 8、16、32 位原子操作。默认内存顺序为 `SeqCst`，非法的顺序组合触发 trap。用 `Atomic<T>.intoInner(move(value))` 交付原子对象并取出其值。

`RefCell` 用借用守卫管理共享和独占访问，守卫或其映射结果释放时归还借用。`OnceCell` 检测重入，`OnceLock` 使用 Acquire/Release 发布已初始化的值，争抢初始化的线程采用自旋等待。可失败的初始化失败后允许重试。守卫和借出的指针必须在对应容器的有效期内使用。

`binary` 支持显式小端、大端和目标本机端序。VarUint 使用最短编码，读到非最短或超出 64 位范围的值时返回错误。读取长度前缀失败时保留游标位置，写入容量不足时保留缓冲区内容。

`parseInt` 和 `parseUint` 支持 2 到 36 进制，按目标整数的取值范围检查溢出。`parseIntPrefix` 返回解析结果和消耗的字节数。`parseHex`、`parseBinary` 和 `parseOctal` 接受对应的可选进制前缀。`Cursor` 和 `splitFields` 返回借用输入的文本视图，游标位置总落在 UTF-8 标量边界。

`thread.Thread<T>.spawn` 要求闭包及成功载荷可跨线程移交。回调返回 `T` 或 `!T`，`join` 一次性领取 `!T`。线程句柄析构时分离仍持有的线程，分离后由工作线程与句柄共同负责回收结果和运行状态。`requestStop` 设置协作标志，线程回调可由 `stopRequested` 查询。原生线程当前支持 GNU/Linux x86-64 和 AArch64。

`Mutex`、`CondVar` 和 `WaitGroup` 通过 GNU/Linux futex 阻塞调用线程。`CondVar.wait` 允许虚假唤醒，`waitUntil` 会重新检查谓词。等待开始前须为 `WaitGroup` 登记本轮工作，计数上限为 `u32` 最大值。在所有守卫释放且等待结束前，应保持锁和条件变量的位置有效。守卫释放后，其 `get()` 返回的借用视图也到期。

`Duration` 将亚秒部分归一化到与整数秒一致的方向，整数换算向零截断。可失败的算术操作报告溢出，`fromMinutes`、`fromHours` 和 `fromDays` 在超出表示范围时饱和。`Date.toOrdinal` 以 1970 年 1 月 1 日为第 0 天，公历规则向前后延伸到整个 `i32` 年份范围。`Instant`、`Deadline`、`Timer` 和 `Ticker` 以 Linux 单调时钟为准，睡眠阻塞调用线程，周期定时按前一次计划的到期点推进。

`channel` 的接收端排空关闭的缓冲区后返回成功的外层 `null`。零容量通道只在发送与接收配对时交付。`sendPreserving` 在关闭或内存不足时交还值，`sendTimeout` 以 `Cancelled(value)` 交还超时值，`recvTimeout` 以 `ChannelError.Timeout` 报告超时。当前等待使用原生线程同步。`drain` 只迭代本次可立即取得的消息。

`new(allocator) T(...)` 借用一个实现 `mem.Allocator` 的可变位置。分配器及其缓冲区必须比所有借用它的 Box 活得更久。拆出自定义分配的 Box 时使用 `Box.intoRawParts`，恢复时传回原释放上下文。由于同一个 `^T` 类型可保存借用的分配器，当前线程能力检查保守地拒绝跨线程移交或共享 Box。

`Shared<T>` 的复制增加强引用计数，`Weak<T>` 仅延长计数块的寿命。最后一个强引用释放载荷，最后一个弱引用释放计数块。`getMut` 要求唯一强引用且没有显式弱引用，`tryUnwrap` 消费强引用，并在其唯一时移出载荷。只有可安全共享的 T 才允许将这两类拥有者跨线程移交。

当前实现范围、实际直接导入与未覆盖接口见[标准包实现矩阵](../docs/stdlib/Vaqrin-stdlib-package-matrix.md)。容器及 I/O 的接口细节见[容器实现](../docs/stdlib/coll-container-implementation.md)与[I/O 实现](../docs/stdlib/io-stack.md)。

## 数值、数学与随机数

`num` 的固定宽度整数运算面向编译器现有的最多 128 位整数类型。BigUint 使用 base-2^32 小端 limb，BigInt 使用符号-幅值表示；Rational 使用 BigInt 中间值完成约分与算术，再检查最终结果是否能装入 T。当前 `absDiff<T>` 返回 u128，因为语言尚无 `Unsigned<T>` 类型级映射。

`math` 通过内部 `math_ffi` 包调用目标 C 数学 ABI。它为 f32/f64 分别使用对应的 C 数学入口。宿主链接需要目标数学库；编译器不会为任意 extern C 符号自动添加 `-lm`。区间运算采用普通 IEEE 舍入，不提供向外舍入保证，因此 Interval 目前不构成严格的浮点包络证明。

`random` 的 Pcg64 是 PCG XSL-RR 128/64，Xoshiro256 使用 xoshiro256**。两者都可复现且都不是密码学安全随机源。当前没有 OsRng 或 CryptoRng 实现；`newDefault()` 使用固定种子，不从系统时间伪造熵。随机字符串按 Unicode 标量抽样，不按字素簇抽样。
