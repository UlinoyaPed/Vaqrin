# v0.2.0 迁移对照

下表记录目标变更，分支的实际验收状态见 [实施记录](implementation-plan.md)。

| v0.1.x | v0.2.0 | 迁移要点 |
| --- | --- | --- |
| `in &const T` | `in T` | 显式解引用裸指针和 Box。安全引用可直接重借用 |
| 用指针参数修改调用者对象 | `mut T` 或 `*T` | 安全借用用前者，ABI 和底层存储使用后者 |
| 非 Copy 形参写 `T` | `move T` | 移动具名对象须写 `move(expr)` |
| `&T`、`&const T` | `ref mut T`、`ref T` | 对于安全位置用 `ref mut place` 或 `ref place` 建立借用 |
| `&place` | `raw place` | 仅获取原始地址。任何裸指针解引用都须位于 `unsafe` |
| `var &x = p`、`some(&x)`、`[&x]` | `ref x = *p`、`some(ref x)`、`[ref x]` | 将间接对象与载荷槽分开处理。对载荷槽的旧式可写 `some(ref x)` 改成 `some(ref mut x)` |
| `#return_borrow`、`borrow(index)` | `from name` | 唯一借用来源可省略 |
| `T: A & B` | `T: A + B` | Trait 交集与位与分开 |
| `public:`、`private:` | 声明级 `pub` | 成员默认私有 |
| 单表达式 `if`、`for condition` | 块式 `if`、`while condition` | 只保留一组控制流形式 |
| 多种类构造 | `T(args)`、`new T(args)!` | 字段初始化位于类内 |
| 签名或字段直接写连续的 `!` / `?` 类型包装 | 具名别名，如 `core.ResultOptional<T>` 或 `type Found<T> = !?T;` | 包装层级保留，后缀传播顺序不变 |
| 窄整数隐式提升、饱和算符 | 原位宽与显式饱和方法 | 跨位宽计算显式转换 |
| 匿名目录查找、文件路径入口选择 | 显式导入、包入口 | 单文件实验使用 `vaqc run` |
