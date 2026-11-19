# Vaqrin v0.1.2 编译器与运行时实现设计

**输入契约**为《语言规范 v0.1.2》20 节。本文用于直接分模块编写编译器、运行时和工具链。编译器命令为 `vaqc`，源文件后缀为 `.vqr`。本文中的目录布局和符号前缀 `lg_` 用于描述设计。当前实现范围见[编译器说明](../compiler/README.md#当前覆盖)。各章按部件组织，没有发布批次或开发路线。看某个语法怎么做，可从末尾的规范对照表进入相应章节。

## A．十五项实现规定的裁定

| 规定 | 采用的工程决策 | 直接后果 |
|---|---|---|
| 实现语言 | 编译器自有 C++ 与运行时可移植核心使用 ISO C++20；禁止自有 C++ 使用 VLA、GNU statement expression、内联汇编、编译器私有大整数作语义基础 | 各 OS/ABI 的接口、独立汇编和板级启动件隔离在 port/BSP；引用平台 API 是显式的平台依赖。第三方 LLVM 头与构建参数单独管理 |
| LLVM 后端 | 锁 LLVM **20.1.8** 为本设计可复现基线；以官方 C++ API 生成 IR、优化、发对象 | 每个发行物记录精确补丁、LLVM 配置与构建 ID。选择该版是为了指定可复现接口；更换基线须重新验证 |
| 自主前端 | 自写 UTF-8 lexer、递归下降声明/类型/模式解析器、Pratt 表达式解析器、名字和类型检查器 | 不链接 clangAST、clangParse 或 LibTooling 处理本语言。宿主可用 clang++ 或 g++ 编译编译器；目标 C 驱动可选做 ABI 桥与链接 |
| AST/HIR/MIR | AST 存源结构，HIR 存解析出的定义、类型与取值动作，MIR 存可执行控制流、存储位置、清理和提交点 | 三层各有独立打印器、验证器；不另设冗余的通用字节码 |
| 编译期计算 | 解释已类型化、具备明确清理行为的 MIR，使用目标布局与虚拟对象存储 | 不 JIT 执行用户代码，不将目标地址转换为编译器进程地址 |
| 按需运行时 | 以目标编译独立归档和对象，定义小型版本化 C ABI；纯算术、布局、范围检查与大部分 RAII 直接发 IR | 普通裸机对象不引用线程、堆、I/O；机器运算辅助函数仍可能由目标工具链提供 |
| 标准库 | 以本语言编写数据结构、迭代器、错误/可空 API、任务和通道泛型外壳 | C++ 只实现分配/系统服务、共享控制块、调度与通信等底层原语。bootstrap 由宿主构建的 vaqc 直接编译库源 |
| 构建 | 宿主 CMake + Ninja；目标运行时使用独立 toolchain/preset 与独立构建目录 | 不用宿主的 sizeof、系统库或 LLVMConfig 推断目标 ABI |
| Clang/GCC | Clang 为宿主首选，GCC 为同源兼容验证。两者须使用与已安装 LLVM 兼容的 C++ 标准库 ABI、RTTI、编译选项 | 宿主 GCC 可生成面向 ARM、Windows 等目标的 vaqc；这与目标 SDK 是否齐备分别报告 |
| 依赖 | LLVM 与版本化 Unicode 数据是固定输入；链接器、归档器、目标 C 驱动按目标发现 | 不把 ICU、Clang 前端、通用协程框架或完整 C++ 运行库强行带进用户产物 |
| 平台 | 主验收 Linux x86-64 ELF；明确另设 Windows x64 MSVC ABI、macOS arm64/x86-64、Linux AArch64、Thumb 裸机配置 | 解析/类型检查按目标能力执行；有 LLVM 架构后端仍须独立提供运行时端口、SDK 与 ABI 参数 |
| Cortex-M | 自由站立配置；用 STM32F103C8 的 Cortex-M3/Thumb/soft-float 作具体板例 | 不需 OS、全局构造器或任务系统。板级启动、链接脚本、栈、向量表和必要算术辅助符号由 BSP/工具链供给 |
| 产物 | `--emit=llvm-ir,obj,staticlib,sharedlib,exe`；可附 `bc,asm` 用于定位 | IR/对象不触发完整宿主链接；MCU 可执行文件为 ELF 固件，按需转 bin/hex。无装载器的目标拒绝 sharedlib |
| 调试 | ELF/Mach-O 用 DWARF；Windows MSVC 路径用 CodeView + PDB；GNU Windows 如另配则走 DWARF | GDB/LLDB 保障落到实际格式和目标；Windows PDB 另用 LLDB/Windows 调试器，不声称 GDB 能读全部 PDB |
| 测试 | 一套表驱动前端、MIR/CTFE 判例、少量运行/互操作场景和一份板级链接检查 | 不为每个 AST 节点、平台和优化等级做笛卡尔积；检查资源计数、退出结果、符号与目标格式 |

LLVM 20.1.8 是已有的正式补丁版本；CMake 的 CXX_EXTENSIONS 应显式关闭。目标链接所需的 C 运行库与内建运算辅助件取决于工具链。[LLVM 发布公告](https://discourse.llvm.org/t/llvm-20-1-8-released/87259) · [CMake 属性](https://cmake.org/cmake/help/latest/prop_tgt/CXX_EXTENSIONS.html) · [工具链组成](https://clang.llvm.org/docs/Toolchain.html)

## B．仓库与编译请求

| 路径建议 | 负责的数据或入口 |
|---|---|
| `compiler/src/driver.cpp` | 构造 BuildRequest，发现包、生成依赖 DAG、选择 emit、调用归档与链接工具、发布产物 |
| `compiler/source`、`syntax` | SourceManager、UnicodeTables、TokenCursor、AstArena、Parser、Diagnostic |
| `compiler/sema` | DefTable、ScopeTree、TypeStore、TraitQuery、OverloadSet、PatternCoverage、TypedBody |
| `compiler/mir`、`ctfe` | Body、BasicBlock、Place、Operand、Terminator、初始化数据流、DropElaboration、Interpreter |
| `compiler/target`、`backend/llvm`、`compiler/ffi` | TargetSpec、TypeLayout、FnAbi、ModuleEmitter、DebugEmitter、CBridgeEmitter |
| `runtime/include`、`runtime/{alloc,text,task,channel,port}` | 内部 C ABI、分配器、文本引用计数、等待与提交、目标适配及汇编 |
| `stdlib/{core,collections,text,task,channel,io}` | 本语言接口与泛型实现。编译器通过受信任定义 ID 识别少数内建入口 |
| `targets/`、`boards/stm32f103c8/`、`tests/` | 目标描述、SDK 发现、BSP 与小规模验收夹具 |

BuildRequest 保存根包、语言版本、目标 triple、CPU/features、ABI/浮点 ABI、SDK/sysroot、运行时能力、优化/调试、产物和输出路径。Session 保存只读包图、源码、intern 表、诊断与查询缓存；每个并行翻译单元使用独立 LLVMContext/Module。driver 的工作顺序为读清单及锁定版本 → 收集包和声明头 → 解析函数体 → 类型检查与按需常量查询 → 泛型实例化、MIR 验证和析构展开 → 解释常量或生成 LLVM → 输出/链接。前端出现错误即停止该包发码，仍可诊断无依赖的其他源。临时目录写完后原子替换最终输出，失败不覆盖旧的可用产物。

同包文件按包内归一化相对路径排序。导入为文件私有别名，pub 决定包外可见性。逻辑路径只经清单映射解析，不能当作任意网络地址。Git tag 在解析依赖时解析为提交，锁文件记录提交与内容标识；常规构建从锁定的源目录读，缺依赖才通过显式获取命令处理。v0 清单解析器仅接受已公布的 TOML 子集（依赖表、带引号的键、字符串和内联表），遇其他结构报具体键和行号。通过 package DAG 与常量求值 DAG 分别报包循环和初值循环。

查询示例：`resolve(DefId)`、`typeOf(DefId,Subst)`、`constEval(Instance,args,target)`、`layout(T,target)`。条目标记 computing/done/failed，重复进入 computing 时报告完整依赖链；函数的合法递归先发布签名，函数体不属于必须无环的布局/初值查询。跨包泛型通过包含签名、已解析模板体、常量依赖和私有定义身份的接口件实例化；私有名字不进入外部符号查找。实例键为包身份、DefId、规范化类型及常量实参；变体 ABI、目标、编译器 build ID、Unicode 表版本、依赖接口摘要加入缓存键。普通函数用包限定修饰名，泛型实例由一个代码生成单元负责或按目标使用可合并的同一实例定义。内部符号不宣称稳定 ABI；C 导出保留所声明的外部名字，重名链接前报错。

## C．源码到 AST：语法入口和歧义处理

### C1．词法与源位置

SourceFile 保存原始 UTF-8 字节、换行索引、原始拼写区间。Span 用文件 ID 与半开字节偏移；显示行列时转为人可读列，机器接口可另给 UTF-16 列。校验 UTF-8，拒绝过长编码、代理区和超过 U+10FFFF 的值。由 Unicode 17.0 的 DerivedCoreProperties、UnicodeData、正规化属性/组合排除数据生成 XID 范围、NFC 表，实现 Hangul 算法；绑定使用 NFC 后标识符，诊断展示原字形。关键字走固定 ASCII 表；文本常量保存实际字节，不跟随名字正规化。按已固定的 Unicode 数据构建与分发生成表，不依赖用户 locale。[Unicode 17 数据](https://www.unicode.org/Public/17.0.0/ucd/)

Token 保存 kind、Span、可选符号/数值文本、前置换行标志。空白、//、不可嵌套 /* */ 为 trivia；保留换行供属性终止。识别十/二/八/十六进制、合法数字间的下划线及 iN/uN/f32/f64 后缀；小数点后须有数字，先排除两点范围。十进制浮点接受带符号的十进制指数字段；非法数位或后缀整段报告，负号交解析器。char 解转义后必须恰为一个标量，string 解码后为 UTF-8；处理换行、制表、反斜杠、两种引号、Unicode 标量转义，未知转义报原位置。true/false/null 为单独 token。单独下划线、#、保留的 @ 均有 token，@ 出现在可执行源码即诊断。

运算符按最长匹配，包括从 `<<%=` 到单字符的所有复合、`..=`、`=>`、`:=`、`?.`。完整标识符恰为 move 且紧邻等号时识别 `move=`，`remove` 保持标识符。类型解析器有局部虚拟 token 游标：在类型前缀处把 `??` 拆为两个问号，连续右尖括号只消费当前实参表所需的一层，可将 `>>=` 的剩余字符交回外层；源码偏移仍精确。表达式中的位移和后备照常解析。

### C2．解析器分派表

下表的“动作”可直接作为 AST 节点或字段名。列表公用带闭合符的解析函数，允许尾逗号；语句错误恢复同步至分号、右花括号或下个声明头，不吞掉外层右括号。block 存 statements 和可选 tail，两者不可重复保存同一节点。

| 入口／源码形式 | 解析动作及关键判别 |
|---|---|
| 文件、包、导入 | 先 `package Ident;`，再零或多个 `import (path [as alias], ...);`，随后顶层声明；逻辑路径段间用 /，不能当成除法解析 |
| 顶层声明 | 读 pub、comptime、unsafe、extern/export "C" 等适用修饰，再按 func/class/interface/enum/error/type/var/const 分派；保存属性与修饰 Span，已有语法但非法组合在声明检查报错 |
| 绑定与别名 | `var a[:T][=e]; const a[:T]=e; comptime const a=e; a,b := e;`。可变局部无初值必须显式写类型。解构每项为标识符或 _；type A = T 在类型上下文读右侧 |
| 类型 | 递归前缀 ?, !, *, *const, ^；`[N]T`、`[]T`、`[]const T`；命名/包限定/泛型类型；在 Shared<const T> 等允许只读目标的类型参数位置保存 const 资格，普通值绑定的只读另存 binding flags；元组至少用逗号区别分组；本语言 `func(A,B) R` 或 `extern "C" func(A,B) R`。解析出 [ _ ] 后限构造处使用 |
| 函数 | `func name[<P...>](paramGroups) R {body}`，接口或 extern 的声明以分号结束。缓存一组未定型参数名，读到冒号后为整组绑定类型，覆盖 `a,b:i32` 与 `a:i32,b:f64`。comptime 形参带标志 |
| 泛型参数 | `T`、`T: I & Copyable`、`comptime N: usize`。实例实参保留类型或常量的 token 区间，依据声明参数种类复核。以命名空间/类型为接收者的 `pkg.A<T>.f` 与实例方法调用共享后缀解析 |
| 类与接口体 | 读属性、public:/private:、var/const 字段、普通/const/static 方法、operator、init、deinit、embed，以及类体 comptime if 选择的成员表。接口方法保存签名；接口声明只接纳有定义的成员种类 |
| 构造与委托 | `func init(...) [!void] [: field(e), ...] {body}` 或单独 `: self(args)`；禁止混用委托和字段清单。类体外的 T(args) 暂存调用，T{f:e,...} 存命名字段构造 |
| 枚举、错误 | enum 项存名字、可选元组载荷类型、可选常量标签；error 集只允许具名项。#repr: C/u8 等属性由被修饰声明分类校验 |
| 前缀与原子 | 标识符、字面量、括号分组/元组、带尾值的块；! ~ - * & 为前缀。move(e)、new [(allocator)] T(args)、unsafe 块、if、switch、select、Lambda、数组构造有各自入口 |
| 方括号起始 | 用有界试读区分 `[move(x), &y](...) =>` 与 `[_]T{...}`、`[N]T{...}`。在可判定为函数签名且箭头存在时建 Lambda；余者按类型加初始化解析。空捕获、零长度数组均有独立判别 |
| Lambda | `x => expr`、`(x:T) [R] => expr/block`、可选捕获前缀。完整括号签名后有 => 才提交；缺上下文的省略类型留给 sema。捕获项区分复制、引用、移动 |
| 后缀 | `f(args)`、`obj.f`、`p?.f`、`e!`、下标 `[i]`、切片 `[:]` 或 `[lo..hi]`。保留整段可空方法调用，以便跳过实参。先构造完整 new 再允许后缀 ! |
| if、switch、模式 | 无括号条件启用“到分支左花括号为止”模式，条件内字段构造可加括号；括号条件允单表达式。switch 臂保存模式列表、=> 与表达式；else 为兜底。模式递归读常量、null、some(p)、Type.Member(p,...)、名字、&名字、_、元组 |
| for | `for {}`、带/不带括号的三段式、布尔条件、`x in it` 或 `i,x in it`。三段式以顶层两个分号判别，末段允许 ++/-- 而不要求再写分号。循环体必须是块 |
| 转移与更新 | `return;`、`return e;`、`return a,b;`，break、continue；允许作 switch 臂的无后继表达式。赋值/移动/复合赋值为语句，后置 ++/-- 也是语句或 for 更新项 |
| select | 臂头存 Recv(binding,endpoint)、Send(endpoint,source)、After(duration) 或 Else；普通点调用解析后在语义层按内建定义身份验证。禁止把臂头先当作普通 send 调用执行 |
| catch | `expr catch fallback` 或 `expr catch (err) {body}`，后者只建立绑定作用域。绑定后须跟块，其他括号表达式保留作 fallback |

泛型名判别采用两遍办法：先用配对分隔符扫描各文件的声明头、导入和包级泛型签名，再解析函数体。名称被局部遮蔽、或类体条件实例化后才知道成员身份时，在歧义处保存尖括号 token 区间与两种局部解析候选，由解析后的作用域裁决；禁止为一个文件永久改写 token。实例化后紧跟调用、成员或构造后缀时优先实例化；否则按比较解析。类型实参里的大于/移位常量表达式用括号明确。解析复杂度由候选片段缓存和最大嵌套深度控制。

Pratt 结合顺序从紧到松：后缀 → 前缀 → 乘除余（含回绕/饱和）→ 加减（含变种）→ 移位 → 范围 → 顺序比较 → 相等 → 位与 → 位异或 → 位或 → 逻辑与 → 逻辑或 → ?? → catch。最后两级右结合。连续顺序比较生成一个 CompareChain 而非二叉树；相等链与范围链诊断第二个运算符。赋值不入 Pratt 表。属性清单含 copy: enable/disabled、move: disabled、implements: 接口、repr: 标签整数或 C；未知属性或非法位置报错。

词法器、Pratt 表和语句更新器由同一份运算符目录生成。左侧为一级表达式运算符族；下列清单的每个拼写都可单独制作一行表驱动判例。

```text
postfix        ()  []  .  ?.  !
prefix         !  ~  -  *  &
multiplicative *  /  %  *%  *|
additive       +  -  +%  -%  +|  -|
shift          <<  >>  <<%  <<|
range          ..  ..=
order          <  <=  >  >=
equality       ==  !=
bitwise        &    ^    |          （三个独立优先级）
logical        &&   ||              （两个独立优先级）
fallback       ??   catch           （两个独立优先级）
assignment     =  move=  +=  -=  *=  /=  %=  &=  ^=  |=  <<=  >>=
updates        +%=  -%=  *%=  <<%=  +|=  -|=  *|=  <<|=  ++  --
```

复合更新沿用底层操作的检查，强制运算宽度为左值类型；移位右值仍按自身数学值查数位。识别用户重载时只开放普通二元算术与比较；更新语句写回前检查其结果能否合法构造左值类型。分号不能被当成任意换行替代，属性行终止和块尾值是有意保留的两个边界。

## D．HIR、类型和符号

每种声明获跨本包稳定的 DefId，每个绑定为 LocalId。TypeId 由 TypeKey 驻留：整数含位数与符号；指针/切片含目标与权限；函数含签名/调用约定；类、接口、枚举含声明与实参；另有 Void、Never、MetaType、Optional、Result、Tuple、Array、Box、Closure、动态接口视图。type 别名在相等判断前展开，保留书写名供诊断，识别别名循环。带值泛型参数用目标定型的常量做键。顺序递归按字段、数组、元组及载荷检查类型大小；普通指针、Box、视图中断按值递归。函数签名递归与对象内嵌递归分别处理。

HIR 表达式保存 `{kind,span,type,category,access,def/instance}`。category 为 Place、OwnedTemp、Value 或 Diverges；access 表示是否可写与来源。建节点时显式插入 ReadCopy、TakeOwned、Borrow、WeakenPtr、MakeSome、MakeOk、MakeErr、CallResolved、IfaceDispatch、ConstructInto、CheckedCast 等。初始化、返回、实参、运算符、模式按同一个 `coerce(expected,expr,use)` 处理；禁止每种语法自行决定“自动 move”。不可复制位置的取值直接指向该位置与所需动作，给出合法使用 move 的位置时才给修复建议。

名字查找顺序为内层局部、外层局部、self 成员、文件导入、包级、预定义。类成员默认 private，包外只认 pub。const 令绑定与内嵌字段只读，不递归剥夺独立指针/切片的目标权限；`&constBinding` 为只读指针。方法先按点左值借 self，普通参数不自动借址。重载先筛元数、权限、约束，再逐参数比较匹配等级；一个候选所有位置不差且至少一个更好才胜出。字面量在每个候选的预期类型下独立试配，平局须显式定型，返回类型不单独定胜负。

上下文包装先试同型，再看最外层：Optional 接受 null 作空、合法内值作 some；Result 接受错误作失败或成功载荷作 ok。some/ok 强制一层；不自动剥离。无期望的 null 报错。Never 路径不参加类型合并；void 是零大小完成值，在 LLVM 中作实参/字段时按内部 ABI 省略或用零大小表示，不能创建 void 类型的 LLVM alloca。bool 与整数不隐式转换。无具体期望的纯整数字面量表达式最终收敛 i32，浮点收敛 f64。按源码有无后缀/显式构造区分“数学整数计算”与“已定型运算”；常量折叠不能变换返回类型。

类签名表先包含直接成员，再按 embed 路径枚举可访问的普通实例方法，以 (名、参数、接收者权限、泛型形状) 分组。记录转发路径而非复制字段表；自身同签名同结果遮蔽，结果冲突声明时报错；多嵌入歧义留到使用处。访问控制取整个路径的交集，不得提升构造、copy/clone/deinit 或字段。#implements 对每项建立 Witness(slot,fn,adjustPath)；需要调整 self 时生成 thunk。动态视图布局为 {dataPtr,witnessPtr}，只读视图禁止可变槽；含依赖具体 Self 的普通参数或结果标成静态专用。静态约束可直接调用单态实例，动态调用读 witness 槽。static func 没有隐式实参。operator 仅接受 v0 白名单内的普通二元算术和比较、只读 self；可失败返回保留 !，逻辑/赋值/借址等不走重载。

泛型签名引入抽象类型参数与约束环境。普通体用该环境验证，如返回具名 T 必须有 Copyable；对具体实参再实例化布局、条件成员和重载。comptime if 的两臂均解析，在实参已确定时仅选中臂加入声明表、检查依赖名字与权限；不可让未选中臂导出真实符号。函数及类型实例缓存先放占位再解析递归；不断扩张的类型实参有实例预算和调用链诊断。Copyable/Movable 按所有字段、枚举所有可能载荷及显式属性求值；有 deinit 的类启用复制须提供签名正确的 copy。Sendable/Shareable 另检查捕获、分配器与线程亲和性，不能由 Copyable 或 const 自动推出。标准库 TrustedImpl 表仅列经审核的内建类型/包装的判定；普通包不能靠同名 # 属性越权。

### D1．容易被“交给类型检查”漏掉的语义入口

| 入口 | 检查与生成的具体动作 |
|---|---|
| 包级 var/const、静态 string | 先解释初值再递归检查是否需要运行期 drop。静态字符串字面量例外使用永生描述符，引用计数标为静态；对普通包级可析构对象给定义处诊断。允许的 const 写只读段，var 写数据段或零初始化段。不得生成隐藏的 C++ 全局构造链 |
| `[_]T{...}`、`[N]T{...}`、`[_]T` | 只数最外层元素，空数组得 [0]T；显式 N 必须与实参个数完全一致。元素在 T 上下文中从左到右初始化；只有构造表达式可有推断标记。在类型参数、字段、返回、别名中出现裸 [_]T 均指出其位置 |
| `Status.fromInt(raw)`、enum 到整数 | 仅无载荷枚举合成 fromInt 的关联函数，先在不截断 raw 的表示域比较有效标签，密集标签用范围/位图，稀疏标签用 switch，成功构造 ?Self，否则 null；不会凭一个整数构造含 Vec 的成员。无载荷枚举用显式目标整数转换读取标签，目标不能覆盖标签值域时沿用 checked-cast；#repr 指定标签存储，不复制整个对象位表示 |
| `?*T`、`*const T`、`?^T`、接口值 | OrdinaryPtr 保留 pointee/权限，Optional 不继承 pointee 的 mutable 权。Box、指针与可空均分别有 TypeId，禁止把 pointer-to-const 与 const 绑定混淆。允许隐式权限收窄；资格放宽只在受限内建 constCast 中处理 |
| `ptrFromInt<P>`、`ptrCast<P>`、`constCast<P>`、`ptr.offset(n)` | 都先检查 unsafe 令牌。整数造地址只在目标允许的可整数表示地址空间中按目标地址宽度检查/转换；已知非法非空值诊断。ptrCast 记录目标对齐义务；同地址空间通常无 IR 操作。offset 按元素跨度计算，使用非 inbounds 的 GEP，保留调用者的有效访问义务。移除 const 不使真实只读对象可写 |
| `MaybeUninit<T>`、`write`、`move(slot).assumeInit()` | 布局取 T 的大小与对齐，槽自身 drop 为空。write 直接在槽内初始化，不能通过普通字段读 T；assumeInit 检查 unsafe 并消费槽，把已构造值交给目标，保证后续不会以原槽再析构。虚拟解释器检查 init mask；原生路径把“确实已经完整写入”作为 unsafe 前提，不给每个普通槽强加状态字。若 T 禁止搬迁，仅在能证明同一最终存储就地取得时提供该按值提取，否则通过指针访问已就位对象 |
| `volatileLoad(ptr)`、`volatileStore(ptr,value)` | 按目标 MMIO 所支持的宽度与对齐发单条 volatile load/store；未对齐或过宽、无法保证硬件所需访问粒度时拒绝该 MMIO 形态。普通字段更新不得当作 volatile 的可替代写法，也不把读改写擅自合并成原子操作 |
| `copy()`、`clone()!`、`deinit()` | 特殊 copy 与 deinit 依签名和所属声明注册 glue；clone 是普通显式方法，只受普通调用/传播规则约束。Vec.clone 为每项调用可用的明确复制操作，失败释放已建前缀并保留源 Vec。析构函数须 void，里面可以 catch 可失败调用，不能将错误传出 |
| 参数/返回类型推导、丢弃错误 | Lambda 先取上下文函数形状补参数，再求所有可达 return/尾值的统一类型；存在 postfix ! 传播时要求已知或能据显式返回/上下文确定外层 Result，不能任意给普通函数添隐藏错误通道。语句位置的 Result<T> 诊断未处理，!void 同样检查；catch 可在失败分支记录、转交或结束外围函数 |
| `for ... in` 的绑定、字符/字素迭代 | 按 next 的实际 ?Item 拆 optional；Item 为 ?U 时保留内层标签。char 迭代取得标量，字素迭代借用一段 str。两名绑定要求 Item 可按二元元组拆分，不把 tuple 误当成两次 next |
| 按值传输与禁移动类型 | 对 by-value 的内建性质采用明确的推导：Sendable(T) 要求 T 能合法移动且字段/析构/分配器均许可跨线程。可以复制成另一份的类型若禁移动，也不隐式成为可搬运的消息。固定地址对象用可移动的 Box 或合适的共享句柄传输，另检查其指向对象的线程权限。由此 Channel<T> 和跨任务结果无需偷偷复制载荷或迁移固定地址对象 |
| 组生命周期的自依赖 | TaskGroup 作为唯一回收责任不具普通跨任务 Sendable；可以移出组的是 Task 句柄。子任务不能捕获本组的回收责任。组析构在任务上下文内用不可取消的 **park 当前任务** 等待；从外部线程调用才可阻塞该线程。不要在单 worker 上让组析构用系统条件变量堵住待回收子任务 |

## E．MIR：位置、控制流、所有权和清理

### E1．可直接编码的对象模型

下列为接口骨架；Id 均为有类型的小整数，数组和字符串由所属 arena 持有。一个 MIR Body 只属于一个已知签名或一个参数化模板，不携带 LLVM 指针。MIR 使用局部槽加有向基本块，不必首先构造 SSA；标量提升交给后端。

```cpp
struct Place {
    LocalId base;
    ProjectionList projections;  // Field, Deref, Index(checked local), ActivePayload
};
enum class UseKind { Copy, Move, BorrowConst, BorrowMut, ObserveTag };
struct Operand { UseKind use; Place place; };
struct Edge { BlockId target; CleanupDepth keep; };
struct BasicBlock { StatementList statements; Terminator end; };
struct Body {
    SignatureId signature;
    LocalTable locals;
    ScopeTree scopes;
    BlockTable blocks;
    BlockId entry;
};
```

常量是 Operand 的另一变体（C++ 用 std::variant）；BasicBlock 恰有一个终结指令。语句集：StorageLive、Init/ConstructInto、AssignScalar、MoveInto、CopyInto、SetDiscriminant、SetDropFlag、DropIfLive、Borrow、Project、原子/volatile 原语。终结指令：Goto、Branch、SwitchTag、Call（正常控制返回）、Check（成功或 trap）、Return、Trap、Unreachable、SelectWait。可失败调用**返回普通带标签结果**，随后通过 SwitchTag 分支；它不触发 LLVM 异常展开。MIR verifier 检查类型、支配、分支目标、活动载荷投影、每条返回的结果、危险操作授权、goto 清理深度及 moved 位置。unreachable 只在已证明路径或不返回终结之后使用。

### E2．初始化与资源算法

1. 为每个完整局部、拥有临时、返回槽和逐字段构造槽建立生命周期。静态分析格为 Dead（未初始化或已移走）、Live、Maybe，另有不可达块。入口参数 Live、其余 Dead；使用、借址、移动要求 Live。move 后源 Dead、目的 Live；覆盖在该路径合法构造后设 Live。用工作队列求 CFG 不动点，包括回边、break、continue、错误边、select 各后继。
2. drop elaboration 依据每条离开作用域的边产生共享清理块。确定 Live 直接 drop，Dead 跳过，Maybe 用运行期 drop flag；全确定的局部无须 flag。作用域嵌套按逆序退出，临时在完整表达式末释放。对同一作用域内先声明空槽、后任意次重建的情况，以**本次活值的实际构造顺序**析构：静态可排序的用定序表；相对顺序依赖分支的少数局部用可移除的作用域内索引链，重新构造时重登记。不能把所有情况近似为逆声明序。
3. 返回普通/错误/尾表达式先在调用者返回槽完成构造，取消本函数对该返回对象的清理责任，再退出路径。传播仅清理到当前函数/Lambda。break 跳循环退出的清理目标，continue 清理迭代体后跳三段循环的 update 或迭代器 next。trap 不沿正常清理链。
4. 赋值只求一次左地址，所有有可能传播错误的右侧工作先完成；自复制/自移动完整绑定先判同一槽。已有可复制值先调用 copy 或逐字段复制到中间拥有槽，成功后 drop 旧目标，再将替代值落位。复制可观察，不能省掉自定义 copy 或析构。赋给未初始化位置可直接目标构造。禁止通过 memcpy 重定位禁移动对象。对于**活目标、禁移动且有可观察自定义构造**，如既不能暂存后合法交付、又不能在不破坏旧值的条件下原位替换，诊断“该替换要求移动”，允许在新绑定中原位构造；这是本实现对未完全定义的覆盖边界的裁决。
5. 生成每类型的 `drop(T*)`、`copy_init(dst,src)`（仅可复制）、`move_init(dst,src)`（仅可移动）和所需的构造入口。自动移动转移活跃表示，源不再析构，无用户移动钩子；平凡字节搬运也必须更新语义活性。仅对布局、生命周期与重叠前提已证实的位置用 memcpy/memmove；禁用对拥有对象的任意 bitCast。数组、元组和枚举按活动分量递归处理。class 的 drop 先调用用户 deinit，再逆字段序；deinit 不能把错误传出，析构执行期间不得再次处置整体。
6. 构造器取隐藏目标地址和必要的“已构造前缀”。按字段声明顺序求值/初始化，禁止在此前读后续字段、暴露未完成 self。数组记录成功前缀，枚举在载荷成功后写有效标签。错误路径只逆序销毁已构造字段/元素，不调整体析构；new 另释放原存储。委托调用与外层共享构造状态，委托成功但外层失败时仍按字段回滚。无构造器的字段字面量检查访问、重复、缺项；调用可失败构造 T(args) 产 !T，new 合并分配/构造错误为 !^T。
7. 消费整个枚举/元组时把源责任交给匹配临时；选中臂将按值项交给分支局部，其余活跃项各 drop 一次。非消费的名字绑定复制，&绑定取指针并继承权限。部分字段不对用户暴露为一般 move 源。消费型闭包的拥有捕获视为这次调用的独立移动源，以字段活性掩码支持提前返回；引用捕获保留借用属性。

### E3．表达式和语句如何建 CFG

| 语法族 | MIR 算法 |
|---|---|
| 普通调用/运算、块、return 元组 | 从左到右将有副作用的子式落临时。块尾无分号把结果写外槽，有分号产生 void；多个 return 分量按元组构造。接收者先定位，再求普通实参 |
| if、switch | 写统一结果槽；标量可在 IR 用 phi。按源序建模式决策树，先验父标签再投影子载荷；分支体只执行一次。模式覆盖用构造子矩阵对 bool、Optional、枚举及其嵌套逐层展开，对整数常量集合去重，对开放域要求 else 或通配。备选绑定签名须相同，值式检查穷尽，语句无匹配可直接继续 |
| &&、逻辑或、比较链 | 右侧各建延迟基本块，短路边直接形成结果；比较链缓存中间值，后一段仅在前一段为真时计算。链中可失败重载先拒绝。普通位运算没有短路 |
| 范围/四种 for、for-in | 固定 init/head/body/update/exit。范围两端在进入时求一次，半开用 <，闭区间在访问端点后先判相等再增量；continue 也走这一步。for-in 只构造一次 iter，next 的 null 退出，some 交本轮绑定；对元组迭代项做解构。惰性 map/filter/take/skip/zip/enumerate 是库适配器 |
| 下标、切片、指针 | 先检有符号非负并以足够宽度与 len 比较，防止索引窄化截断；范围检查 lo≤hi≤len，再换算地址。`[:]` 用 0,len。普通指针解引用按用户前提，不额外生成完整生命周期验证；点访问链自动解引用一次合适的指针 |
| ?、!、??、?.、unwrap、some/ok | 所有标签操作共用构造/观察/提取原语。?? 在 null 边求 fallback；?. 在有值边执行整段指定成员/实参，然后再包一层；unwrap 空边 trap。postfix ! 失败边复制错误身份入外围返回槽并清理，catch 在失败边执行后备块；其 return 直返外围。消费/复制判据与普通读相同 |
| Lambda | 捕获列表按源码序构造环境；中途失败清理已形成项。隐式外部局部引用报错。生成 invoke(env,args)、drop_env、可选 copy_env 和 consume_invoke；无捕获可降为薄函数指针。捕获值随闭包而非句柄清理。不逃逸且按值使用的闭包可放栈，除显式拥有本身需要的存储，不规定每次造堆对象 |
| select | 端点/时长先求值，仅记录候选 move 的 Place 与来源编号。SelectWait 返回分支号、状态及可能的接收槽；通过“成功交付 i”这一条边清源 drop flag。保留分支在臂内可读原变量；汇合仍按 E2 求解 |
| unsafe | 检查 lexicalSafetyDepth，普通函数内从零开始，unsafe func 只改变调用侧前提；Lambda 体重新从零开始。MIR 原语带授权来源供 verifier 检查；不会关闭其他运行期检查 |

**例：解包不可复制的具名结果。** 下例假定库提供 drain 并按值消费 Vec。可以用它联调 HIR、MIR 与析构打印器。

```cpp
func relay(r: !Vec<u8>) !void {
    v := move(r)!;
    drain(move(v))!;
}
```

```text
entry:  take r -> tmp; r.live=false; switch_tag tmp -> err1 / good
err1:   init_err ret from tmp.error; dispose tag shell; goto return
good:   take tmp.payload -> v; invalidate tmp; prepare owned argument from v
        v.live=false; call drain(argument) -> s; switch_tag s -> err2 / done
err2:   init_err ret from s.error; goto return
done:   init_ok_void ret; goto return
return: drop only remaining live locals; return
```

取得形参的责任在调用交接处转给 callee。drain 失败不会恢复 v。将 `move(r)!` 改成 `r!` 应在 HIR 就以不可复制载荷拒绝。若失败分支只读错误，不得预读非活动 Vec 的指针。

## F．目标布局、数值与 LLVM IR

TargetSpec 提供 triple/CPU/features、端序、可寻址单位、地址空间、整数存储/对齐、C 标量、原子能力、对象格式、浮点 ABI。LLVM TargetMachine 提供 DataLayout；所有 size/align/offsetof 均按目标查询，前端不调用宿主 sizeof 模拟目标。内部布局单独带 lang-abi 版本；同一程序和其库须一致。字段声明顺序保留，允许自然填充。枚举载荷区按所有变体的最大对齐、最大大小布置，显式对齐分配，不能只用一个对齐为 1 的 byte-array 代替联合体。

| 源类型 | 寄存器、内存与 ABI 选择 |
|---|---|
| bool、char、整数、float | bool 寄存器 i1、存储 i8(0/1)；char 为经验证的 i32；整数寄存器 iN，内存按目标布局与填充，明确加载截断/存储扩展；isize/usize 随地址宽度。浮点 float/double；bitCast 只放行无填充、等位宽、合法标量 |
| [N]T、元组、class、embed | 固定数组及结构按布局表投影；[0]T 不产生可解引用的元素。数组下标与切片先检查后计算；零大小元素按逻辑长度计数且逐个执行有副作用的析构，指针相等不能充当对象身份 |
| 普通指针、C 函数指针、接口视图 | LLVM 使用 opaque ptr；MIR 自有 pointee 与地址空间决定 GEP/load 的显式类型。接口视图为 data+witness，函数指针保留调用约定 |
| Optional、Result、带数据 enum | 默认显式标签加对齐的活动载荷，Result 的错误载荷为 ErrorDesc 引用。各层有独立标签，!??T 不压平；?*T 在 C ABI 适配器显式映射 C NULL。优化内部空指针表示须能逐层证明无歧义 |
| slice、str、string、Box、Vec | 前两者为 {data,len}，不拥有；string 为可静态驻留或原子引用计数的只读描述符；Box 含对象指针、释放上下文，Vec 含 data/len/cap/allocator。具体内部 ABI 不与 C struct 自动互换 |
| void、never、type | void 作为值零大小、作为函数结果可用 LLVM void；never 无正常边；type 仅存在 CTFE。!void 保留错误/成功的判定 |
| 类/枚举非平凡返回、禁移动类型 | 内部调用约定显式隐藏 out 指针，由调用者分配最终槽；已有值实参按 HIR 构造/转交。小标量按值；非平凡按值参数通过已转移的地址传入。间接参数不能误作可观察的源语言借用 |

每个源 error 成员生成只读描述符，记录规范化包身份、集合、成员和稳定内容哈希；相等先比可选快路径，再比完整身份，跨 DSO 不单凭地址或短哈希。静态描述符寿命覆盖使用期。v0 动态库采用装载期固定依赖，不提供携带活对象跨卸载的语言合同。接口描述符、drop glue 与闭包 invoke 同理。

### F1．数值生成表

| 情况 | 生成操作 |
|---|---|
| 未定型整数、类型适配、小整数提升 | 未定型整数用任意精度有符号语义；可用 llvm::APInt 按操作先扩位并规范化，不能以固定 64/128 位冒充无限精度。普通算术先扩到 i32（按源符号 sext/zext），再查另一操作数。复合赋值、++/-- 在原左值宽度算 |
| 有符号加减乘/一元负 | 调 `llvm.sadd/ssub/smul.with.overflow`（取负是 0−x），分支 overflow→trap。无符号普通和显式回绕用无 nsw/nuw 的 add/sub/mul |
| 饱和 +、−、× | 优先使用适用的 signed/unsigned saturating intrinsic；饱和乘用溢出标志及操作数符号选择端点，或用足够宽的乘积并夹限再截断。不可先在宿主 C++ 中做会溢出的有符号运算 |
| /、% | 先判除数为零；有符号 MIN/-1 的除法走 trap、余数直接给 0，安全边才发 sdiv/srem。无符号用 udiv/urem。浮点除法单独处理 |
| 移位 | 先以原右值的数学值检查 0≤count<W，再转为 LLVM 要求的同宽 count。普通 signed 左移用足够宽的有符号运算或可逆性比较检查结果；wrap 左移截断；sat 左移在越界时按原符号夹限。signed 右移 ashr，unsigned 用 lshr。非法 count 一律 trap |
| 按位、相等、顺序 | and/or/xor，取反与全 1 xor；整数比较按提升后的 signed/unsigned 选择 icmp。bool 直接比较；文本相等比 UTF-8 长度和字节。浮点 == 用有序相等、!= 用无序或不等，其余用有序比较以符合 NaN 语义 |
| 受检数值转换 | 整数显式拓宽按完整源值域判断（含符号变化）；越界可能性决定静态 !T，常量确定越界诊断。检查上下界时在足够宽的共同域比较，然后 trunc/sext/zext。转浮点 sitofp/uitofp，浮点宽窄按 IEEE 舍入。浮点转整数先按函数约定舍入，再检有限和上下界，最后才发 fptosi/fptoui；使用精确的二次幂开区间上界，避免把最大整数先舍入成更大的浮点数 |
| 已知越界和折叠 | 类型化的运算只在要求编译期求值处把 trap 转诊断；显式数值转换的已知越界按规范单独静态诊断。折叠不能使运行期可能失败的表达式改变静态类型 |
| 位模式与指针重解释 | 同宽合法标量用 bitcast/同型恒等。ptrCast 保留地址空间约束；constCast 通常无 IR 指令。无单独证明不加 noalias、inbounds、nonnull、dereferenceable 或 nsw/nuw。合法 C NULL 进口先构造可空，不能直接产生源非空指针 |

显式 wrap/saturate 的操作数宽度保持不变；未定型常量在这一上下文中适配。普通浮点默认就近舍入、无 fast-math；禁止擅自合并成 FMA 改变逐操作结果。目标 soft-float 同样须保持语言结果，可以由明确选择的辅助库实现。对未细化的转换函数名作一项库级定案：truncToInt 朝零，roundToInt 取最近整数、平局取偶；两者舍入后再检查范围，NaN/无穷返回转换错误。调整该约定只需改变明确版本的库 API 与判例。所有能力受 TargetSpec 支持的 iN 范围和后端合法化约束；至少验证 8/16/32/64。逻辑位宽大于机器寄存器时用 LLVM 合法化或受控 helper，若目标未公布支持则静态拒绝。[LLVM 指令、固有函数与 poison 语义](https://releases.llvm.org/20.1.0/docs/LangRef.html) · [Opaque pointers](https://releases.llvm.org/20.1.0/docs/OpaquePointers.html)

**IR 判例：最小有符号数对 −1 取余。** 不论优化等级都不能先发可能产生 poison 的 srem 再用结果判断。

```llvm
declare void @llvm.trap()

define i32 @safe_rem(i32 %a, i32 %b) {
entry:
  %zero = icmp eq i32 %b, 0
  br i1 %zero, label %bad, label %nonzero
bad:
  call void @llvm.trap()
  unreachable
nonzero:
  %min = icmp eq i32 %a, -2147483648
  %neg1 = icmp eq i32 %b, -1
  %special = and i1 %min, %neg1
  br i1 %special, label %exactzero, label %ordinary
exactzero:
  ret i32 0
ordinary:
  %r = srem i32 %a, %b
  ret i32 %r
}
```

### F2．LLVM C++ 后端接线

用 TargetRegistry 查 target，显式初始化所构建架构的 TargetInfo/Target/TargetMC/AsmPrinter，避免仅初始化宿主架构。TargetMachine 取 triple、cpu、features、relocation/code model、opt；每个 Module 在创建布局相关类型前设置 triple 和 `TM->createDataLayout()`。Module 内先声明函数、全局、类型描述和内部 ABI，再生成函数体，允许前向调用。IRBuilder 在 entry 建固定 alloca 后按 MIR 块插入，局部只在需要时取地址，禁止令循环中的固定槽随迭代无界长大。聚合优先直接写 out 槽；只读常量与字面量放常量段。

终结调试元数据后调 verifyFunction/verifyModule，失败视为编译器内部错误并保存对应 MIR/IR 与位置。使用 PassBuilder 建 Loop/Function/CGSCC/Module analysis managers，注册并交叉代理，O0 和普通优化各选匹配的默认 pipeline；之后再次 verifyModule。文本 IR 用 Module::print；bitcode 用 WriteBitcodeToFile；对象/汇编在此固定 LLVM 系列按 TargetMachine::addPassesToEmitFile 驱动目标 emission。将此处涉及 LLVM 版本签名的代码集中在 BackendSession，避免扩散到前端。[官方对象文件示例](https://releases.llvm.org/20.1.0/docs/tutorial/MyFirstLanguageFrontend/LangImpl08.html) · [New Pass Manager](https://releases.llvm.org/20.1.0/docs/NewPassManager.html)

## G．CTFE：用目标语义解释 MIR

执行入口 `eval(instance,args,TargetSpec,limits) -> ConstValue | ErrorValue | Diagnostic`。区别三种出口：用户的 !T 失败仍可由外层 MIR catch；必须常量的位置若最终值类型合法，可以保存带标签错误值；内部遇到非法编译期副作用、未初始化读取、目标 trap 或预算耗尽，报带调用栈的编译错误。普通 const 局部没有 CTFE 要求。包级初值、comptime const、comptime func 的调用、comptime 参数、数组长度、标签、条件成员和类型值都调用同一解释器。泛型模板内尚依赖参数的常量形成延迟表达式，实参齐备再求值。

状态含栈帧 (Instance,BB,PC,locals,returnDst)、虚拟分配表 (AllocId,layout,initMask,activeTag,alive,origin)、预算和目标上下文。指针是 (AllocId,offset,permission) 或允许的静态符号重定位；不使用宿主地址。每条 MIR 指令与原生 lowering 共用 NumericSemantics、TypeLayout、取值与析构规则。移动要清源，drop 执行同一用户 deinit 的 MIR；析构若触及 I/O 等禁用操作同样诊断。可解释临时局部与可释放的编译期存储；允许持久化可序列化的纯值、字面量与合法静态符号引用，拒绝逃逸的局部地址、动态分配句柄、Mutex、任务等目标资源。编译期分配器仅提供虚拟分配/释放，无法调用目标 malloc。

整数按是否定型区分 BigInt 与 APInt，浮点用目标 APFloat 的格式与指定舍入；从 APFloat 读取转换状态做 NaN/范围诊断。循环和递归正常执行，同时按指令数、栈深与活跃字节限制资源；诊断给最内调用与触发常量的源位置，可通过编译请求调整限制。CTFE 不访问目标时钟、网络、OS、MMIO、外部函数、任务、原子设备和运行期可变全局。type 值用 TypeId 表示；编译期程序只能组合原有类型和已声明泛型。结果序列化为目标常量时逐活动字段写入，重定位表另存符号，禁止把宿主字节序整块拷贝进目标镜像。

例：`comptime const N = square(4); type Buffer = [N]u8;` 经签名解析将 4 适配为目标 usize，解释已定型乘法得到 16，再经 layout query 建立固定数组。若 N 的求值反向依赖 Buffer 的尺寸，报告具体依赖环。CTFE 与运行时差分使用同一组小函数的类型化输入输出，避免两个计算器长期悄悄分叉。

## H．标准库、运行时和 C 边界

### H1．运行时 ABI 与分包

跨边界只传目标 C ABI 的定宽标量、size_t、opaque 指针、指向调用者槽的指针和 C 兼容回调。公开内部 ABI 头（例 `lg_rt_v1.h`）、能力清单、版本符号；不跨边界抛 C++ 异常、传 std::string/容器或 C++ class。C++20 核心不把 std::expected 当标准设施；用有标签结果或状态枚举。可失败接口不用在禁异常构建下仍可能抛出的 new/vector 承担 OOM 契约。使用显式 fallible 分配、预留节点/缓冲，或在允许异常的宿主适配层捕获并转换；任何异常不得越过 C 边界。

| 部件 | 符号/数据合同（名称为设计名） | 链接触发 |
|---|---|---|
| core/intrinsics | 整数、数组、视图、标签、trap、RAII glue 内联；默认 trap 发 llvm.trap，配置可改为 noreturn 的 `lg_trap(code,location)` | 零依赖路径无独立语言运行时符号 |
| alloc | `AllocatorRef{ctx,try_alloc,dealloc,retain,release}`；按 size、align 申请，失败不写出对象；有状态分配器上下文须活到最后释放 | Box、Vec、StringBuilder 或显式分配 |
| text | 常量描述符与不可变字节、共享块 retain/release、按需 Unicode 分割数据 | 字面量字节或 str 无需堆；动态拥有内容及字素遍历各自触发 |
| sync/task | `lg_group_spawn`、`lg_task_join`、`lg_cancel`、park/unpark、clock、worker、Mutex 等 | 使用相应宿主/可选 RTOS 能力时；嵌入式缺能力时诊断所用符号 |
| channel | `lg_chan_new`、发送/接收尝试、选择注册/提交、关闭、端点引用计数 | 可靠调度服务及可选分配器；通过单独归档依赖 task/sync |
| io/port/builtins | write/exit 等宿主适配；编译器算术辅助、memcpy/memset 单独来自目标 provider | driver 按未决符号和目标能力选择，不默许宿主 libc++、pthread 或主机 CRT |

type-erased 通道/任务载荷传 `TypeOps{size,align,relocate,drop}`。relocate 表示已合法建立的移动、无用户钩子、不可失败；用户自定义 copy/clone 和析构可有副作用，应在调用方或脱锁后的清理点执行。Box 的 releaseCtx 保存原分配器、布局与释放责任；intoRawParts 交出唯一票据，不 drop 对象；fromRawParts 在 unsafe 下合并原地址和原票据，重复领取或错配违反前提。仅地址 intoRaw 在**脱离前**验证默认分配器，非默认 trap。Box 移动只传管理字段，堆对象地址不变。Vec 扩容先查 size 乘法与容量增长溢出，再申请，失败时旧 Vec 保持有效；按合同处置已传入的元素，成功逐项合法 move、释放旧缓冲。push 的简洁消费形态在未插入时清理已取得的参数。扩容依赖 Movable，删除/移动后旧视图依规范失效。

标准库 `core` 提供 Optional/Result 的内建扩展、Box/Vec 和分配器接口、迭代协议。string 复制共享不变块，静态字面量不 retain；动态块跨线程 retain/release，以最后拥有者销毁。str 与 asBytes 不修改引用计数。验证可变字节后须复制或接管独占存储才产不可变文本。byteLen 取描述符长度，scalarCount/chars 解 UTF-8，graphemeCount/graphemes 采用固定 Unicode 17 的扩展字素边界并以字节范围返回视图；字素数据和 builder 仅在使用时链接。text equality 长度相等再比较字节。range/zip/enumerate 等适配器保存底层迭代器与状态，next 返回 ?Item，filter 可连续拉取，take 达上限即不再拉取，zip 任一端耗尽结束；不复制不可复制 Item 来实现遍历。

### H1a．跨编译器/运行时的调用面示例

下列是内部头文件中与 select 相关的可编译片段。结构经目标 C 编译器布局，编译器侧按目标 ABI 与布局断言绑定；fixed-width code 常量在同一份 ABI 描述生成。这里只展示一个边界，其余 runtime 函数也只以同样的标量/槽传递。

```c
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct lg_endpoint lg_endpoint;
typedef struct lg_cancel_token lg_cancel_token;

typedef struct {
    size_t size;
    size_t alignment;
    void (*move_init)(void *dst, void *src);
    void (*drop)(void *live);
} lg_type_ops;

typedef struct {
    uint32_t kind;            /* recv, send, after, else */
    lg_endpoint *endpoint;    /* NULL for after / else */
    void *slot;               /* send source or uninitialized recv payload */
    uint64_t deadline_ticks;  /* absolute monotonic deadline for after */
} lg_select_arm;

typedef struct {
    size_t arm_index;
    uint32_t status;
    uint32_t source_taken;
    uint32_t received_value;
} lg_select_out;

void lg_select_v1(const lg_select_arm *arms, size_t count,
                  lg_cancel_token *cancel, lg_select_out *out);
#ifdef __cplusplus
}
#endif
```

endpoint 在创建时绑定唯一载荷 TypeOps；前端按每个臂的静态类型分配 slot 并将其地址存入只读描述符。recv 的 slot 仅在 received_value=1 时初始化，允许 T 自身为空值。source_taken 只对成功发送且原值被消费的臂置位；按值标量发送的 source 是复制后的临时，不影响用户绑定。status 区分成功、关闭发送、取消、分配失败；关闭排空的 recv 是成功且 received_value=0，无对应臂的取消使用 SIZE_MAX。after/else 返回成功但两项标志为零。若已可提交的另一个臂和取消同时被观察到，仲裁锁决定本次唯一结果；不得先移动后宣称取消。所有返回途径写完整 out，不能让临时 slot 指向已释放栈，runtime 返回前须注销全部等待引用。内部 waiter 队列保留代际作迟到通知过滤，不能继续引用此次 arms 指针。用户析构不得在 move_init 里执行；由 commit 后的唯一 owner 决定 drop。

### H2．FFI 使用目标 C 驱动生成 ABI 桥

LLVM DataLayout 解决对象布局，不能单独决定各平台 C 聚合的分拆、寄存器类、返回槽或扩展属性。工程选择一条统一通路：普通本语言函数使用 F 节内部 ABI；复杂 extern/export 与 C 函数指针调用生成小型 **C 桥翻译单元**，由所选目标 C 编译器使用真实目标 ABI 编译，正常本语言解析不依赖 Clang。固定签名的运行时 ABI 与经过双向验证的纯标量 C 子集可由 LLVM 直接发射。clang 或 ABI 匹配的交叉 GCC 只读桥的 C 代码；需要桥且无匹配驱动时在生成对应产物前给出所需工具和 triple，不猜聚合 ABI。[Clang 工具链](https://clang.llvm.org/docs/Toolchain.html)

例如 `extern "C" func transform(p: CPoint) CPoint;`：桥以目标 C 声明真实 transform；另定义内部 `void bridge_transform(void *out, const void *in)`，将 in 字节 memcpy 到对齐的 CPoint 局部，以真正 C 调用执行 `r=transform(p)`，再 memcpy 到 out。导出反向操作，并仅导出目标 C 符号。桥内生成 sizeof/alignof/offsetof 静态断言，与编译器 #repr:C 布局对照。标量位宽/有符号性由目标表确定，c_char 尤其不得硬编码为 i8。禁止传递带析构字段、string/str、Result、接口视图、语言闭包或未声明 C 表示的带载荷枚举。可空 C 指针按明确约定编解码；字符串显式转 NUL 终止缓冲并规定有效期。非空进口的额外前提写入接口合同。C++/其他语言异常不得逃入本语言帧。

C 函数指针使用 `extern "C" func(A,B) R` 的签名专用 C thunk；保持**函数指针类型**，不偷换为可无损往返的 void*。捕获回调由 C 签名的静态入口和独立 data context 两件组成，外部保存期间由显式 owner 持有，注销/完成后释放。含聚合的间接调用同样走桥。仅请求本语言的 .ll 可输出主 LLVM module 并记录任何桥的未定义符号；若需可独立消费的完整 FFI IR，选能发 bitcode 的目标 C 驱动，将匹配 LLVM 系列的桥 bitcode 合并；纯 GCC 桥改以配套目标对象交付。请求单一、含桥的 .o 时用目标可重定位合并能力；目标无该能力则用匹配 bitcode 合并后发一份对象，若均无则明确诊断，可直接选择静态库携带多个对象。不能伪装单个不含桥的文件为自足产物。

## I．任务、Channel 与 select 的具体实现

### I1．任务栈、等待和所有权

宿主实现 M:N 的**有独立栈、协作挂起**的轻量任务。TaskRecord 与入口闭包分别拥有：TaskRecord 持完成状态、结果槽、错误观察状态、取消源、等待列表和独立引用计数；入口闭包由执行任务独占。GroupControl 持每个成员到回收完成；Handle 持独立控制记录或墓碑到句柄释放。组有关闭标记/登记表，消除记录相互强引用环。调用者拿到 Handle 不会因此延长入口闭包的生命。

portable/scheduler 用标准 C++20 管理可运行队列、worker、单调定时器与等待令牌；port 提供 `stack_alloc, context_init, switch_context, stack_free, monotonic_now, wake_worker`。Linux x86-64 用受保护的固定地址映射（保留空间、守卫页，栈不搬迁）与独立 SysV 汇编 switch，保存栈指针、继续地址、callee-saved 寄存器以及 ABI 要求的浮点控制状态，入口栈对齐/红区符合 ABI；AArch64 用相应 AAPCS64 保存集；Windows 端可用系统 fiber，创建/切换时保持所需浮点状态，栈回收在调度栈进行。切换点是普通 ABI 调用边界，禁止在仍运行的栈上释放该栈。仅新任务参加跨 worker 窃取，已启动任务固定到原 worker，简化线程亲和 Guard、TLS 和回调；其他 worker 唤醒时入 owner 的远端队列。线程阻塞的外部 API 交给专用 blocking executor，工作线程上的用户无限循环仍可妨碍其他任务，取消不做异步强制析构。Windows fiber 接口为显式平台端口。[Windows fiber](https://learn.microsoft.com/en-us/windows/win32/procthread/using-fibers)

通用 park 不把等待通信的轻量任务变成阻塞 worker 的系统线程。在锁下登记 waiter，并将状态从 Running 改 Parking；释放锁、切回调度栈后由调度器完成 Parking→Parked。唤醒若命中 Parking 则置 notified，调度器看到后直接重新入队；命中 Parked 原子转 Ready 并入队，重复唤醒无效。定时/取消注册使用带代际的令牌；注销等待后迟到事件不能触碰已释放帧。只有当 worker 无可运行任务才在系统事件上睡眠。同步库从非任务线程进入时可选择线程等待；任务内必须走 park。

spawn 先取得闭包，分配记录、结果槽及栈，再在组锁下检查未关闭并登记，最后发布 ready。任何失败回滚已分配的对象并恰好释放闭包一次；新任务在登记前不可运行。执行 trampoline 的顺序为调用入口 → 完成返回构造/局部清理 → **销毁入口闭包的剩余捕获** → 在锁或 release 操作下发布 Completed → 唤醒 joiner。失败完成也如此。panic 按进程策略终止，不进入普通错误槽。协作取消只是置位并通知可取消等待；checkCancelled 和等待入口在提交之前检查。

join 在同一受保护状态内裁决：组已关闭→GroupClosed；直接自身→SelfJoin；已领取→AlreadyJoined；已完成未领取→消费一次（含任务错误）；尚未完成且调用者取消→JoinInterrupted 并保留可重试；余者登记等待。区分“子任务返回了名为 JoinInterrupted 的错误”与“等待者中断”的内部来源，前者已观察，不重复发放。组析构关闭入口、向仍活跃任务请求取消，在锁外逐个不可取消地等完成；按同一归属协议与 join 竞争，仅一方领取。未领取成功值 drop、未观察错误各报告一次，回收后保留句柄墓碑。析构和错误报告器不得向库边界抛出可恢复错误。不得持组锁等待任务完成或调用用户析构/报告器。

### I2．通道事务（可实现的同步策略）

采用**一个宿主调度域内的通信仲裁锁**，保护本域 channel 队列、等待注册、select 的选中标记与交付。引入这一把锁是为了把双向会合和跨通道选择做成一个可审计的原子提交；临界区只含固定数据操作和不抛错的内部移动，实际阻塞、分配、用户代码与析构均在锁外。跨 OS 线程的端点也经同一域，任务来源不影响通道归属；同一次 select 涉及的端点统一参加进程级通信域。按此规则实现不会出现两个 channel 各自声称同一 select 中选的问题。可测量的代价是无关通道的短临界区会串行。

ChannelState 包含 capacity、FIFO/环、senderCount、sendClosed、receiverAlive、发送和接收等待队列。bounded(N) 在创建时检查大小溢出并预留 N 个对齐槽；零容量没有队列，只有待配对者。unbounded 使用可失败的分段缓冲；需要增长时在锁外备好空段，重新加锁并重新判关闭、对端、容量，最后附加或回收多余段。分配失败在重新检查是否其实已能提交后再报告。资源清理、释放分段、调度 wake 均在脱锁后做；收集需要唤醒或析构的节点时不得再次依赖可能失败的临时堆分配，可接走既有链表。

Sender 复制必须先安全 retain 再发布副本，最后一个释放在锁内结束发送方向；显式 Sender.close 关闭所有副本。Receiver 独占，close 保留排空能力，destroy 接走未读载荷列表、拒绝新发送并使等待者就绪，锁外逐个 drop。普通 send 的参数槽归调用操作；若失败，简洁 send 将槽 drop，preserving/trySend 从同一槽构造 Closed(T)/Cancelled(T)/Full(T)/OutOfMemory(T) 中适用的一项。tryRecv 用三态 Value/Empty/Closed，try 操作从不留下持久等待注册。recv 的消息分支对 T 再构造一个 some，保证 T 自身为 null 仍可交付。发送提交顺序确定 FIFO，提交成功与业务处理结束无关。

运行时外层以标准库的五个签名解释结果码，错误分支不得写成隐式 void：`send(value) !void`、`sendPreserving(value) SendResult<T>`、`trySend(value) TrySendResult<T>`、`recv() !?T`、`tryRecv() TryRecv<T>`。SendResult 的标签为 Sent、Closed(T)、Cancelled(T)、OutOfMemory(T)；TrySendResult 用 Full(T) 代替 Cancelled(T)；TryRecv 为 Value(T)、Empty、Closed。将发送和接收端的 copy/移动/drop 胶水连接 senderCount 与 receiverAlive，不能让用作挂起保护的内部引用使“最后一个用户发送端”一直存活。特别地，可移动的 Receiver 表示唯一接收权，不允许复制它来实现 wait 注册。

SelectWait 带 groupToken、分支列表、可借用的源槽地址、接收目的槽、pending/committed/failed 状态。执行流程如下。

1. 调用方从左到右求值所有端点/时长，仅记录 move 候选与拥有局部的索引。运行时持有端点内部引用防 UAF，端点关闭语义另以用户句柄计数判定。普通发送与选择发送使用不同入口。所有待交付缓冲、结果槽、waiter 在可能提交前就绪；复杂发送表达式在 select 外绑定。
2. 加锁轮转起始臂扫描就绪集合，优先次序跨调用变化。能直接接收缓冲、发往已等待对端、观察已关闭、已到期均算就绪。无就绪且有 else 立即返回，否则把全部臂注册到各自队列，再用 I1 的 park 协议等待。取消/定时器通过同一仲裁锁重新判定。
3. 配对必须属于两个不同选择令牌，双方都 pending。锁内同时确认双方胜者，内部 relocate 从已构造的源槽到接收槽或已预留通道槽，记录 sender 被消费和接收有效；随后才允许发布完成并唤醒。单生产者普通 send 也遵守相同一次提交边界。选中关闭发送是失败，没有成功交付，不清选择源；闭合排空接收交付外层 null。
4. 清除其他臂注册或作废代际，解锁后唤醒。超时/else/提交前取消均不消费选择源；已提交状态不能被取消改写。调用者按返回记录进入所选 MIR 边并清相应 drop flag，已交付且尚未恢复的发送任务不会因异步取消而执行旧槽析构。臂主体内的 return 或错误传播发生在提交之后。重复监听已关闭且排空的接收端会持续就绪，由程序停用该臂或退出。

Mutex 通过等待队列与 Guard 释放唤醒，withLock 在调用完成及错误路径皆释放。单线程执行器使用任务级 mutex，不在 worker 上持互斥量阻塞等待其他任务。Atomic<T> 对外可发布的位宽和对齐由同一目标描述生成；共享一个原子对象时，代码生成与运行时必须使用同一对象表示和操作约定，不能混用互不兼容的 std::atomic<T> 布局。Shared<const T> 检查 T 的共享读，Shared<Mutex<T>> 检查 T 可移交。不可同步的原始切片、str、接口视图和引用捕获拒绝作为普通跨线程值，泛型算法也走同一性质查询。parallelFor/parallelMap 由库组建 TaskGroup、分配不重叠的输入/结果槽并 join；部分失败时取消和回收已启动任务，按输入序交 result，未完成槽不析构。

原子操作的内存序做成编译期枚举，省略选 seq_cst。load/store/RMW 校验允许的顺序；CAS 失败只读，并按 v0.1.2 表逐项匹配（release/acq_rel 不能作失败顺序）。relaxed 映射 LLVM monotonic，其余对应 LLVM 的 acquire/release/acq_rel/seq_cst。同步边由实际锁/原子支撑：spawn 发布与 worker 取得、完成与 join、关闭与观察 EOF、成功交接与读取、零容量接收准备与发送返回、unlock 与后续 lock。volatile 用独立标记，不充当原子或屏障。目标表对每个宽度指明 native、libcall 或不可用；裸机只有提供具备所需并发语义的 helper 才启用相应宽度，不宣称所有原子都 lock-free。[LLVM 原子语义](https://releases.llvm.org/20.1.0/docs/Atomics.html)

## J．构建、链接、平台和裸机

### J1．CMake 与可复现输入

```cmake
cmake_minimum_required(VERSION 3.24)
project(lang_host LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED YES)
set(CMAKE_CXX_EXTENSIONS NO)
find_package(LLVM 20.1.8 EXACT REQUIRED CONFIG)
add_library(lang_frontend STATIC ${frontend_sources})
add_library(lang_backend_llvm STATIC ${backend_sources})
target_include_directories(lang_backend_llvm SYSTEM PRIVATE ${LLVM_INCLUDE_DIRS})
add_executable(vaqc compiler/src/driver.cpp)
target_link_libraries(vaqc PRIVATE lang_frontend lang_backend_llvm)
```

这是 host CMake 接线片段，source lists、LLVM 库列表和目标适配见下述规则。将 LLVMConfig 导出的 include、预处理定义与实际提供的链接目标局部加到 backend 及使用 APInt/APFloat 的数值适配库。发行预设选择安装中存在的单体 LLVM 目标，或以 llvm_map_components_to_libnames 选 core、support、analysis、passes、target、MC、codegen、bitwriter 和启用的 X86/ARM/AArch64 后端等所需组件；配置阶段检查 LLVM_TARGETS_TO_BUILD。不得同时链接单体和另一套组件。构建 LLVM 自身时 `LLVM_TARGETS_TO_BUILD=X86;ARM;AArch64` 足以覆盖本文指定架构，LLD 是可选外部工具。编译器自有 C++ TU 用 GCC/Clang 的 -pedantic-errors 并隔离第三方头；自有核心不用非标汇编关键字，独立 .S/系统头仅在 port 编译。按 LLVMConfig 检查 LLVM_ENABLE_RTTI、LLVM_ENABLE_EH、C++ 标准库和 Windows CRT；宿主核心处理可预期错误用显式结果，构建选项不泄漏到生成的语言语义。[LLVM 的 CMake 集成](https://releases.llvm.org/20.1.0/docs/CMake.html)

```sh
cmake -S compiler -B build/host -G Ninja -DCMAKE_CXX_COMPILER=clang++ -DLLVM_DIR=/sdk/llvm-20.1.8/lib/cmake/llvm
cmake --build build/host --target vaqc
cmake -S runtime -B build/rt-m3 -G Ninja -DCMAKE_TOOLCHAIN_FILE=cmake/thumbv7m.cmake
cmake --build build/rt-m3
```

交叉 CMake toolchain 设 CMAKE_SYSTEM_NAME=Generic、CPU/ABI、目标编译器、sysroot、归档器、查找根和 CMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY；宿主工具通过绝对路径供应，不 try_run 目标程序。裸机 runtime 工程不 find_package(LLVM)，不把 LLVM 编到目标。标准库构建由已构建的 vaqc、目标清单和包输入驱动，生成目标相关接口件/对象与归档，泛型实例在使用处或统一实例单元生成。相同输入、路径映射与工具版本应产生可复现的符号、表顺序和归档；不将绝对工作目录、随机任务次序或当前时间混进定义身份。第三方 tag、源码摘要、Unicode 数据和目标 SDK 身份写入工具链锁；切换 GCC 需要 ABI 兼容的 LLVM 构建，前端语义无需分支。

### J2．driver 的产物与配置

| 请求 | driver 实际执行 | 验收对象 |
|---|---|---|
| llvm-ir / bc | 构造并校验模块，按指定优化，再写文本或 bitcode；外部声明与必需桥件有清单 | 可被同版 llvm-as/opt 校验，含正确 triple、DataLayout、可见性与调试 metadata |
| obj / asm | 调目标 emitter，FFI 的单对象处理见 H2；不查找进程入口、不链接全套运行时 | ELF/COFF/Mach-O 目标类型、架构、重定位、符号、段 |
| staticlib | 将目标对象、必要 C 桥与泛型/ABI 元数据配套分发，归档使用 llvm-ar/llvm-ranlib 或 llvm-lib；保留未解析的外部库依赖清单 | .a/.lib 可被独立程序消费，抽取一个成员不牵入任务系统 |
| sharedlib | ELF 用 PIC+共享链接，Mach-O 用 dylib/install name，Windows 用 DLL 并生成 import lib，导出按显式清单 | 外部消费者能装载，符号修饰、版本、依赖与平台相符 |
| exe | 编排语言入口包装、目标对象、桥、按需 rt、builtins、宿主 CRT 和系统库，使用目标 C 驱动或清单中的完整链接配方 | main 成功退出，!void 未处理错误由宿主报告并返回失败；裸机见 J3 |

hosted 的默认链接用预设目标 C 驱动承接 sysroot、启动对象与 SDK 搜索，允许显式选择 LLD。切勿拿宿主 `ld` 裸链接所有平台。Windows MSVC 对应 lld-link/link、UCRT/SDK、/DLL 与必要导入库；Linux 对应系统 CRT、libc、动态加载器、libgcc 或 compiler-rt builtins；macOS 对应合规提供的目标 SDK、部署版本、libSystem、系统 ld 或兼容 ld64.lld。-fPIC/relocation model 在生成 obj 时确定。对象、静态库、动态库分别编排，档案索引不替代最终可执行链接。目标分配/文本/并发分别归档，按符号抽取配合 function/data sections 与 --gc-sections、-dead_strip 或 /OPT:REF。通过 map/nm 检查真正依赖，避免 whole-archive 整个运行时；必要描述符/中断表按名称或 KEEP 保留。[交叉编译与 sysroot](https://clang.llvm.org/docs/CrossCompilation.html) · [LLD](https://lld.llvm.org/) · [Windows DLL/PDB 链接](https://lld.llvm.org/windows_support.html)

工具链清单还记录 target-data 的端序与整数对齐探测、C 编译器版本/ABI 参数、所选归档器、链接器、SDK/CRT 路径、可选 debugger，查找结果用参数数组执行，避免通过 shell 拼接用户路径。驱动输出可读的 link manifest，使“为何链接了分配器或 libatomic”能直接追溯到对象与未决符号。

TargetSpec 以 `{triple,cpu,features,abi,floatAbi,endian,objectFormat,pointerWidth,layoutSource,unwind,debug,os,capabilities,providers,linkRecipe}` 构造，禁止把“ARM”当唯一配置。Linux x86-64 SysV 为主，Linux AArch64 用 ELF/AAPCS64；Windows x64 MSVC 为 COFF/Win64；macOS arm64 与 x86-64 各造 slice（如需 universal 再组合）；额外平台端口需独立交付调度切换、原子策略、FFI 桥、SDK/链接及调试验证。即便不具备目标 SDK，若只有 emit=obj 且无须编译 C 桥，可生成纯本语言对象；需要的 SDK 或 provider 不足时列明缺什么、由哪项请求引入。

### J3．STM32F103C8 的完整产物链

选 `thumbv7m-none-eabi`、CPU=cortex-m3、Thumb、soft 浮点 ABI，链接器使用板级内存描述。本板例按 STM32F103C8 标称 64 KiB Flash、20 KiB SRAM 配置，其他 F103 型号从对应 BSP 读取。依赖能力包括 trap、普通指针、volatile、静态存储；heap、IO、任务、线程、动态库默认关闭。core 的 by-value 函数、静态数组、无堆 RAII、枚举、泛型、CTFE、可恢复错误照常工作。选用无 provider 的设施时指出引用链，例如 main → Vec.push → allocator。将需要分配的语义与“构造空 Vec 本身”区分，不因类型名字出现就拒绝程序。

```cpp
package boardapp;
export "C" func app_write(reg: *u32, bits: u32) void {
    unsafe { volatileStore(reg, bits); }
}
```

该入口只作宽度明确的单次 volatile 写，寄存器地址、时钟与引脚配置归板级代码。BSP 的 Reset_Handler 准备栈与必要系统状态、从 Flash 复制 .data、清零 .bss、按板约定调用本语言的 C 导出入口；可使用已有 CMSIS 启动件，避免重复初始化。链接脚本分别映射 FLASH/RAM，向量表置于启动地址，以 KEEP 保留，表首为初始 MSP，复位及中断入口按 Thumb ABI；预留栈、检查 .data+.bss+预留区不超 RAM。不要把宿主 main/exit、全局 C++ 构造器表、任务栈或堆带入板例。Soft-float、宽除法、内存拷贝等若形成 `__aeabi_*`/memcpy 等未决符号，显式提供匹配架构的 builtins/极小支持件。对非原生原子宽度要么给出适用于主循环/ISR 的实现，要么诊断；简单屏蔽中断只在明确单核和中断语义的 BSP 内讨论，不作为通用同步策略。[ST 芯片规格](https://www.st.com/en/microcontrollers-microprocessors/stm32f103c8.html) · [CMSIS 启动件](https://arm-software.github.io/CMSIS_6/latest/Core/startup_c_pg.html) · [Arm ABI](https://github.com/ARM-software/abi-aa/releases)

```sh
build/host/vaqc build boardapp --target thumbv7m-none-eabi --cpu cortex-m3 --profile freestanding --emit=obj -o app.o
arm-none-eabi-gcc -mcpu=cortex-m3 -mthumb -mfloat-abi=soft -nostdlib -T board.ld startup.o board.o app.o -Wl,--gc-sections,-Map=firmware.map -lgcc -o firmware.elf
llvm-objcopy -O binary firmware.elf firmware.bin
llvm-readobj --file-headers --sections --symbols firmware.elf
```

命令示例假定 board.o 定义复位路径实际调用的板级入口，startup.o 提供向量表/启动，脚本与存储大小匹配。可仅产 ELF 调试，也可导出 bin/hex。核验机器架构、Thumb 属性、向量地址、内存图、未决依赖和无关宿主符号；生成 ARM 对象不等于已完成板上运行。

## K．诊断与调试信息

诊断结构 `{code,severity,primarySpan,labels,notes,fixits}`；重载错误列最接近候选及失败参数，move 错误同时标明首次移动与汇合路径，构造错误标明字段声明顺序，CTFE 错误显示目标与常量调用栈，缺运行时能力打印从根符号到 provider 的引用链。只有明确正确才建议 move；从字段挖值、const 源、借用原值的选择臂不提示非法改写。实现内部错误和用户诊断分开，不对非法 HIR 继续发机器码。

用 DIBuilder 创建 DIFile、DICompileUnit、DISubprogram、DILexicalBlock、参数和局部，Span 贯穿 HIR/MIR，以语句/表达式位置设置 DILocation；隐式清理/通道注册使用人工位置或 artificial 标志，避免虚假源码步进。源整数保留实际位宽，类/元组/枚举提供真实字节偏移、载荷与 tag；闭包、结果和接口提供可检查的描述结构，动态美化器后加也不影响基础显示。本语言尚无专用标准 DW_LANG；DWARF 后端选择一个记录在目标清单的用户扩展语言码，并通过调试器类型描述及可选格式化器提供显示；不能假定调试器已懂本语言表达式。O0 优先给槽位置，优化后使用 value/location ranges，在 move 后终止旧值位置，不能把已移动对象显示为有效。DIBuilder.finalize 在 verifier 前执行。LLVM 20 的调试记录表示须统一通过该版 API 生成。[LLVM 源码级调试](https://releases.llvm.org/20.1.0/docs/SourceLevelDebugging.html)

ELF/Mach-O 生成 DWARF，ELF 可分离调试文件，macOS 可经 dsymutil 形成 dSYM；Windows MSVC 设置 CodeView 和 Debug Info Version module flag、生成 .debug$S/T，链接时 lld-link /DEBUG 写 PDB。验证用 llvm-dwarfdump --verify、llvm-readobj --codeview、llvm-pdbutil，配 GDB/LLDB 实际断点、局部变量、栈回溯。Windows PDB 不承诺用 GDB；若需要 Windows+GDB，应交付并验收 GNU ABI/DWARF 预设。任务上下文切换汇编提供相应 CFI/平台展开记录，运行时保留挂起任务的栈边界和保存的 PC/SP；普通调试器的线程栈与所有挂起轻量任务是两件事，后者由只读任务枚举/调试器辅助脚本展示。Cortex-M 以 ELF+DWARF 对接 GDB/OpenOCD 等板级调试端口。[DWARF 验证](https://llvm.org/docs/CommandGuide/llvm-dwarfdump.html) · [COFF/PDB](https://lld.llvm.org/windows_support.html)

## L．按规范示例贯通一次

使用规范 §19 的 producer/consumer。绑定 `channel.Channel<Work>.bounded(8)!` 首先得到 !元组；传播检查只去外层 Result，按位置把两个非复制/可复制端点从临时元组交给本地槽。两个 `[move(...)]` 各构造一个拥有环境，使调用方 tx/rx 失效。任务函数返回 !void，spawn 的失败表示创建失败，join 的单层 !void 表示任务结果或管理错误。生产者逐次构造 Work.Number，调用 send 的消费形参；接收者通过 `rx.recv()!` 得到 ?Work，模式编译先检查 optional tag，只有 some 才检查 Work tag，Number 臂读取 i32 再调用 io.println 的 !void 并传播。

正常路径上容量 8 可能使生产者在十条消息完成前 park；消费者释放槽并唤醒。生产者完成时 trampoline drop 环境，使最后 Sender 关闭，消费者排空再退出，join 返回。输出依次为 0、1、4、9、16、25、36、49、64、81。第二次 spawn 若失败，清理其已消费的闭包释放唯一 Receiver，等待发送操作失败或观察取消，父函数经错误边进入组的不可取消回收。若消费者输出失败，则其完成清理同样释放 Receiver；未由父任务显式领取的错误归组报告。上述结论依赖“发布完成前释放捕获”和“阻塞通信只 park 当前任务”两条可以在运行时观察的合同。

### L1．少量验收夹具

只维护以下七个命名夹具。边界值用数据行，平台差异用预设，避免同一断言抄成很多文件。日常默认运行宿主表与核心端到端；平台专属检查在具备对应 SDK 的环境执行。每次改语言规则更新相应数据行；缺陷回归优先落到已有夹具。

| 夹具 | 输入与检查 | 专门捕捉 |
|---|---|---|
| syntax-sema（编译） | 表驱动示例包含 NFC 等价名字、1..10、嵌套泛型尖括号、类型 !??T、所有优先级、类/接口/闭包及正确和错误的 move；只断言节点关键结构或诊断码与位置 | 语法歧义、权限和类型误判 |
| numerics-ctfe（单元+编译） | 同一算术小函数在 CTFE 和本机执行，比对类型/值或相应 trap/诊断。边界行含 u8 普通与饱和、非法位数、MIN/-1、零除与 NaN 转换 | LLVM poison、宿主位宽泄漏、过度折叠 |
| ownership（端到端） | 带跟踪字段的对象覆盖/自移动、条件移动后重建、字段三构造失败、消费匹配、分配器恢复；预期构造/析构事件序列与一次释放 | 漏析构、错序、重复释放、错池 |
| channels（运行时+端到端） | 单 worker 跑 §19 验输出；可控调度钩子驱动零容量会合、同令牌不可自配对、select 发送/超时/关闭、发送 null 后 EOF、spawn 失败、join 打断重试。每个值计唯一归属，无固定 sleep，死锁用超时仅作失败上界 | 假关闭、丢唤醒、并发双提交 |
| ffi-debug（端到端） | 小型 C 驱动与本语言双向传标量、CPoint、回调；检查入口、C 布局断言；一个调试点检查位置/局部与返回 | 平台 ABI、导出、DWARF/PDB 接线错误 |
| freestanding（链接） | 同一 M3 板例生成 ELF + map，检查向量、对齐、段与 undefined symbols；纯算术版本无任务/堆/C++ 启动依赖 | 错用宿主 CRT、编译器偷偷引入 helper |
| build-artifacts（工具链） | 同一个小包按 emit 生成 ll、obj、归档、动态库和 exe，静态/动态库由外部消费者链接；用 Clang/GCC 各构建一次宿主核心 | 打包、依赖传播、宿主编译器绑定 |

平台级的“支持”以对应产物与调试合同验证为准；检查符号和静态互操作可在交叉环境进行，实际启动、断点、真实 MMIO 与系统线程行为仍需目标系统、模拟器或实板。工具没有对应 SDK 时报告缺项，不把 skip 计为通过。

## M．规范覆盖索引

| 规范节 | 实现位置 | 必须可观察的结果 |
|---|---|---|
| §1 版本 | A、B、J | 语言模式、接口件、缓存和产物明确记录版本；v0 与 v1 语法接受策略可分别选择 |
| §2 模型 | D、E、G、I | error 可传播，trap 终止；UB 前提不伪装成普通错误 |
| §3 词法 | C1、C2、H1 | 字符/文本、数值字面量、正规化、注释、分号/换行及属性边界 |
| §4 包 | B、C2、D、J | 文件导入别名、包 pub、成员权限、前向声明、无环和包初值 |
| §5 类型及数组 | C2、D、E、F | iN/usize、void/type、指针/视图、数组长度推断、切片、元组、别名 |
| §6 绑定与块 | D、E | var/const/comptime/解构、路径初始化、尾值与从左到右求值 |
| §7 运算 | C、E3、F1 | 普通、回绕、饱和、更新、比较链、转换、bitCast 与 trap |
| §8 控制流 | C2、E3、H1 | 两种 if、switch 穷尽、各类 for、闭区间、迭代器与循环转移 |
| §9 函数与 Lambda | C2、D、E、F | 值参数、元组返回、捕获、一次调用、运算符重载与错误 |
| §10 类与接口 | C2、D、E2、F | init/委托/static/const、字段构造、两种 embed、静态约束与动态派发 |
| §11 所有权 | D、E2、H1 | copy/clone/deinit、禁移动类型、就地返回、Box 裸部件、Vec 失效 |
| §12 可空与文本 | D、E3、H1 | some(null) 可表达，?. 延迟实参，拥有/借用文本和字素分割 |
| §13 错误 | D、E、F、G | 开放错误身份、ok/错误/嵌套、catch 的外围 return、表达式结果不可静默丢弃 |
| §14 枚举 | C2、D、E2/E3、F | 标签与表示、嵌套模式、借用/消费、无载荷 fromInt |
| §15 泛型与编译期 | B、D、G | 接口交集、内建性质、单态化、comptime 参数/函数/成员与 type |
| §16 unsafe/FFI | E3、F1、H2、J3 | 危险操作词法授权、MaybeUninit、MMIO、C 调用与布局 |
| §17 并发 | E2/E3、H、I、L | 任务完成、取消与观察、MPSC、单次提交、原子/互斥/共享状态 |
| §18 优先级 | C1/C2、E3 | 长运算符、嵌套类型、泛型与比较/移位消歧、结合方向 |
| §19 综合程序 | I、L | 十项有序输出、两个任务结束；第二次启动失败能回收 |
| §20 边界 | A、J、K | 目标能力诊断、宿主与裸机入口、平台附件；不引入隐式 GC/async/借用标注 |

**设计阶段的验证记录。** 使用环境提供的 LLVM 19.1 对 F1 所列 LLVM IR 做了解析及 module verifier，生成 x86-64 ELF 和 Cortex-M3 的 ARM EABI 目标文件。在 x86-64 链接并执行，得到 MIN % −1 = 0、−7 % 3 = −1、7 % −3 = 1，另在独立进程确认除数为零导致 trap。此外，H1a 的内部 ABI 头片段分别通过 GCC 的 C11 和 G++ 的 ISO C++20（-pedantic-errors）语法检查。这些是指令级和头文件级核查。指定 LLVM 20.1.8 的 C++ 工程、完整语言编译器、并发运行时、Windows/macOS 调试和实板启动均属于本文定义的实现与验收工作，未作为已有成品宣称。
