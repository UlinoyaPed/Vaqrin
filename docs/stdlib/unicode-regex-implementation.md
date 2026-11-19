# Unicode 与正则表达式标准包实现说明

开发分支：`feature/unicode-regex`

## Unicode 17.0.0 数据

数据源固定为 Unicode Consortium 官方 Unicode Character Database 17.0.0：

- https://www.unicode.org/Public/17.0.0/ucd/
- UnicodeData.txt
- DerivedCoreProperties.txt
- DerivedNormalizationProps.txt
- PropList.txt
- Scripts.txt
- PropertyValueAliases.txt
- CaseFolding.txt
- auxiliary/GraphemeBreakProperty.txt
- emoji/emoji-data.txt

生成器位于 `stdlib/unicode/tools/generate.mjs`，要求 Node.js 18+，使用内置 fetch，不依赖第三方包。生成数据位于 `stdlib/unicode/unicode_data.vqr` 与 `stdlib/unicode/script.vqr`，二者与包内源码处于同一显式包。数据归属与许可说明位于 `stdlib/unicode/data/UNICODE-LICENSE.txt`。

当前生成表统计：

- 5,623 个压缩字符属性区间。
- 2,989 个简单大小写映射。
- 5,914 个分解条目，映射到 8,740 个标量。
- 961 个规范组合映射。
- 1,585 个完整大小写折叠条目，映射到 1,705 个标量。
- 176 个 Script 值。

压缩属性表包含 General_Category、Script、Canonical_Combining_Class、Alphabetic、Uppercase、Lowercase、XID_Start、XID_Continue、White_Space、Emoji、Extended_Pictographic、Grapheme_Cluster_Break 与 Indic_Conjunct_Break。

## Unicode API 与实现

`unicode` 包实现：

- `GeneralCategory`、完整 Unicode 17.0 `Script`、`CharInfo`、`NormalForm`、`GraphemeBreak`。
- Alphabetic、Nd、White_Space、Cc、Uppercase、Lowercase、Lt、XID_Start、XID_Continue、Emoji、Extended_Pictographic、Mark 属性查询。
- 简单 lower/upper/title 映射、逐标量完整 case folding，以及字符串 `caseFold`。
- 严格 UTF-8 编码和解码，拒绝过长编码、代理项编码、非法 continuation、截断序列及超出 U+10FFFF 的值。
- NFC、NFD、NFKC、NFKD：递归规范/兼容分解、Hangul 算法分解与组合、canonical combining class 稳定排序、composition exclusion 与规范组合。
- UAX #29 扩展字素簇状态机：CR/LF、控制字符、Hangul、Extend、ZWJ、SpacingMark、Prepend、Regional Indicator 奇偶配对、Extended_Pictographic ZWJ 序列、Indic conjunct GB9c。
- `Graphemes` 字素簇遍历、`graphemeCount` 与 UTF-8 字节偏移边界检测 `isGraphemeBoundary`。

`Normalizer` 的 `push` 接受标量，当前实现将输入标量暂存在内部 Vec，`finish` 生成规范化字符串；`flushTo` 将当前完整结果复制到调用者缓冲区。该版本尚未实现可限制内存的安全前缀流式 flush，长文本的暂存内存随输入增长。

大小写折叠遵循默认 Unicode full case folding（非 Turkic 映射）。简单大小写接口保持一对一标量映射；未提供依赖语言环境的 SpecialCasing 字符串大小写转换。

## Regex 语法

正则引擎使用 Thompson NFA 指令和有序 epsilon closure。匹配阶段采用显式状态集合与工作预算，不执行递归回溯。解析器使用最大嵌套深度限制，NFA 状态数和输入字节数均有上限。

已实现语法：

- UTF-8 普通字符、点号、转义字符。
- `\\n`、`\\r`、`\\t`、`\\f`、`\\v`、`\\0`、`\\xHH`、`\\u{...}`。
- `\\d` / `\\D`、`\\s` / `\\S`、`\\w` / `\\W`。
- 字符类、否定字符类、闭区间范围。
- 捕获组 `()`、非捕获组 `(?:...)`、分支 `|`。
- 贪婪和非贪婪 `*`、`+`、`?`、`{m}`、`{m,n}`、`{m,}`。
- `^`、`$`，以及多行模式。
- 大小写不敏感、dotAll、Unicode/ASCII 预定义类模式。
- 按编号捕获组、find/findAt/findAll、captures/capturesAll、split、replace/replaceAll。
- 替换串支持 `$0`、`$1` 等组编号、`$&` 完整匹配和 `$$` 字面美元符号。

默认资源限制：最多 4,096 个 NFA 状态、输入最多 16 MiB、每次搜索最多 64 Mi 次 VM 工作步骤、最多 32 个捕获组、4,194,304 个捕获历史节点。工作步骤预算按单次搜索计算；RegexBuilder 可覆盖状态数、输入大小和工作预算。

`unicode(false)` 保持 UTF-8 标量边界推进，但将预定义字符类和不区分大小写行为限制为 ASCII；它不是任意字节模式。

## 明确不支持的正则构造

以下构造会返回 `RegexError.UnsupportedConstruct`，不会静默忽略：

- 反向引用，例如 `\\1`、`\\2`。
- 正向/负向前瞻、后顾等环视。
- 命名捕获组。
- Unicode 属性转义 `\\p{...}` / `\\P{...}`。
- 单词边界 `\\b` / `\\B`。
- 内联模式标志。

大小写不敏感匹配使用简单一对一大小写映射，不执行多标量扩展折叠；例如不会将单个 `ß` 自动展开成两个 `s`。替换回调形式尚未实现。FindIter、CaptureIter、SplitIter 与 Match/Captures 保存对 Regex 或输入 str 的非拥有引用；调用者必须保证被引用对象在迭代/视图使用期间仍然存活。

## 静态审查结论

- 正则 VM 不使用回溯栈；epsilon closure 用显式 Vec 工作栈处理。
- unanchored 搜索在每个 UTF-8 标量边界注入新起点，并按状态去重，保持最早起点优先。
- Split 分支顺序控制贪婪/非贪婪与 alternation 优先级。
- 捕获位置通过不可变历史节点记录；Save 槽位、NFA 状态索引和捕获组数量均有边界检查。
- 空匹配迭代推进一个 UTF-8 标量，避免无限循环。
- findAt 拒绝 UTF-8 continuation byte 偏移。
- 编译期限制状态数、嵌套深度、重复次数及捕获组数；运行期限制输入字节数和 VM 工作预算。
- Unicode 正规化和字素分段算法已实现，但尚未在本环境运行官方完整一致性测试。
- Vaqrin 编译器与运行时验收尚未执行；当前工作环境只能通过 GitHub 源码接口读写仓库，不能将静态审查结论表述为编译通过。

## 本地验收步骤

1. 在网络可用的 Node.js 18+ 环境运行 `node stdlib/unicode/tools/generate.mjs`，检查生成数据与仓库内文件一致。
2. 使用本地 `vaqc check` 检查 `stdlib/unicode/unicode.vqr` 与 `stdlib/regex/regex.vqr` 入口及其包内依赖。
3. 编译运行 `examples/unicode-regex.vqr`。
4. 使用 Unicode 17.0.0 `NormalizationTest.txt` 验证 NFC/NFD/NFKC/NFKD；重点覆盖 Hangul、兼容字符、阻断组合和乱序 combining marks。
5. 使用 Unicode 17.0.0 `auxiliary/GraphemeBreakTest.txt` 验证完整 GB3–GB13、GB9c、GB11 规则。
6. 验证 UTF-8 截断、孤立 continuation、过长编码、代理项和超范围码点。
7. 验证 regex：ASCII、多语言标量、字符类、范围、捕获、分支、贪婪/非贪婪、有界重复、锚点、多行、空匹配、非法语法、资源上限和长输入。
8. 使用 `(a+)+$`、`(a|aa)*b` 等对抗输入检查工作预算是否能及时返回 `StepLimitExceeded`，而非进入指数回溯。
