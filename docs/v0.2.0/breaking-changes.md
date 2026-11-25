# v0.2.0 迁移对照

下表记录目标变更，分支的实际验收状态见 [实施记录](implementation-plan.md)。

| v0.1.x | v0.2.0 | 迁移要点 |
| --- | --- | --- |
| `in &const T` | `in T` | 显式解引用裸指针和 Box。安全引用可直接重借用 |
| 用指针参数修改调用者对象 | `mut T` 或 `*T` | 安全借用用前者，ABI 和底层存储使用后者 |
| `opt.take(raw slot)`、`opt.replace(raw slot, value)` | `opt.take(slot)`、`opt.Storage<T>.replace(slot, value)` | 传入可独占借用的可选槽；不可复制的具名替换值须显式 `move`。仅持有裸指针时，`opt.takeRaw` 须在 `unsafe` 中满足独占、有效且已初始化的存储约定 |
| `opt.filter`、`opt.inspect`、`result.inspect` 接收裸指针载荷回调 | 回调参数写作 `item: in T` | 载荷通过共享借用传入，可直接读取 `item`。需要借用具名观察回调环境时使用 `inspectMut` |
| `core.replace<T>(raw place, value)`、`mem.replace<T>(raw place, value)` | `core.Exchange<T>.replace(place, value)` | 删除旧的普通指针接口。替换目标须可独占借用；不可复制的具名替换值须显式 `move` |
| `iter.once(value)`、`iter.onceOptional(value)` | `iter.OnceIter<T>(value)`、`iter.OnceOptional<T>(value)` | 可复制元素按值传入，不可复制的具名元素或可选对象须显式 `move` |
| `opt.toVec(value)`、`opt.intoIter(value)` | `opt.Collection<T>.toVec(value)`、`opt.Collection<T>.intoIter(value)` | 在类型上选择唯一的 Copy 或 Move 签名；可选载荷的嵌套层数保持不变 |
| 非 Copy 形参写 `T` | `move T` | 移动具名对象须写 `move(expr)` |
| `&T`、`&const T` | `ref mut T`、`ref T` | 对于安全位置用 `ref mut place` 或 `ref place` 建立借用 |
| `&place` | `raw place` | 仅获取原始地址。任何裸指针解引用都须位于 `unsafe` |
| `var &x = p`、`[&x]` | `ref x = place`、`[ref x]` 或 `[ref mut x]` | 共享与独占借用需显式区分。 |
| `some(&x)` | 引用载荷用 `some(x)`；Box 用 `some(ref box)`，再显式访问 `*box`；裸指针用 `some(pointer)`，保持裸指针的访问规则 | `some(ref x)` 借用载荷槽，不会自动借用指针指向的对象。独占槽位用 `some(ref mut x)`；非 Copyable 载荷的按值绑定需要消费判别式。 |
| `for &x in items`、`for i, &x in enumerate(items)` | 受检来源用 `for ref x` / `for ref mut x`；需要索引时可按索引调用 `getMut`。裸指针迭代按值绑定指针，再显式使用 `*pointer`。 | 循环不再自动解引用迭代元素。 |
| `#return_borrow`、`borrow(index)` | `from name` | 唯一借用来源可省略 |
| `T: A & B` | `T: A + B` | Trait 交集与位与分开 |
| `public:`、`private:` | 声明级 `pub` | 成员默认私有 |
| 单表达式 `if`、`for condition` | 块式 `if`、`while condition` | 只保留一组控制流形式 |
| 多种类构造 | `T(args)`、`new T(args)!` | 字段初始化位于类内 |
| 签名或字段直接写连续的 `!` / `?` 类型包装 | 具名别名，如 `core.ResultOptional<T>` 或 `type Found<T> = !?T;` | 包装层级保留，后缀传播顺序不变 |
| 窄整数隐式提升、饱和算符 | 原位宽与显式饱和方法 | 跨位宽计算显式转换 |
| 匿名目录查找、文件路径入口选择 | 显式导入、包入口 | 单文件实验使用 `vaqc run` |
