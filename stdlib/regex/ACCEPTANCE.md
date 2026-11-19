# Regex 本地验收清单

所有偏移均以 UTF-8 字节计。FindIter/CaptureIter/SplitIter 的 next 返回可传播错误的可空结果。

## 语法与匹配

- [ ] 普通 ASCII 字符与中文、希腊文、补充平面字符
- [ ] `\\n`、`\\r`、`\\t`、`\\f`、`\\v`、`\\0`
- [ ] `\\xHH`、`\\u{...}`、转义元字符
- [ ] `\\d` / `\\D`、`\\s` / `\\S`、`\\w` / `\\W`
- [ ] 正/负字符类、ASCII 和 Unicode 范围
- [ ] 捕获组、非捕获组、嵌套组和未参与匹配的可选组
- [ ] 分支顺序：`a|aa` 对 `aa` 优先匹配 `a`
- [ ] 贪婪 `a*`、非贪婪 `a*?`、`+`、`?`
- [ ] `{m}`、`{m,n}`、`{m,}`，上下界反转与超限诊断
- [ ] `^`、`$`、多行模式、dotAll
- [ ] Unicode 简单大小写不敏感与 ASCII 模式
- [ ] find/findAt/findAll、captures/capturesAll、split、replace/replaceAll
- [ ] 替换串 `$0`、`$1`、`$&`、`$$`
- [ ] 空模式、空匹配、空匹配位于输入末尾
- [ ] findAt 对 continuation byte 偏移返回 InvalidOffset

## 必须拒绝

- [ ] `(a)\\1` 反向引用
- [ ] `(?=a)`、`(?!a)`、`(?<=a)`、`(?<!a)` 环视
- [ ] `(?<name>a)` 命名组
- [ ] `\\p{L}`、`\\P{L}` Unicode 属性转义
- [ ] `\\b`、`\\B` 单词边界
- [ ] 内联模式标志
- [ ] 未闭合分组/字符类、空范围、反向范围、重复量词

## 资源上限

- [ ] 超过 maxStates 的模式返回 TooManyStates
- [ ] 超过 32 个捕获组返回 CaptureLimit
- [ ] 超过 4,194,304 个捕获历史节点返回 StepLimitExceeded
- [ ] 超过 16 MiB 默认输入上限返回 InputTooLarge
- [ ] 超过工作预算返回 StepLimitExceeded
- [ ] 长输入与 `(a+)+$`、`(a|aa)*b` 对抗模式在预算内退出

## 固定回归样例

- `Regex.compile("a*").find("baaa")` 匹配空串（左侧最早位置）。
- `Regex.compile("a*").find("aaa")` 贪婪匹配 `aaa`。
- `Regex.compile("a*?").find("aaa")` 非贪婪匹配空串。
- `Regex.compile("(ab)+").captures("zabab")` 的 group 0 为 `abab`，group 1 为 `ab`。
- `Regex.compile("^[a-z]{2,4}$")` 匹配 `abcd`，不匹配 `abcde`。
- `Regex.compile("\\d+")` 在 Unicode 模式匹配阿拉伯-印度数字。
- `Regex.compile("a|aa")` 对 `aa` 遵循左分支优先，结果为 `a`。
- 空模式在长度为 N 的 N 标量文本上产生 N+1 个 FindIter 结果。
- `replaceAll("a", "banana", "$0$0")` 的结果为 `banaana`。
