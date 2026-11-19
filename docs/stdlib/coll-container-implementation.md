# coll 容器实现状态与设计裁定

本文件记录 `feature/coll-containers` 分支相对 v0.1.3 规范和标准库 API 草案的实际实现状态。草案中的名称不代表源码已实现；本文件只把已写入分支的源码列为“已实现源码”，编译和运行验收另行标记。

## 容器矩阵

| 类型 | 目标语义 | 当前源码状态 | 验收状态 |
|---|---|---|---|
| `Vec<T>` | 连续动态数组，拥有元素 | 已扩展源码 | 未运行编译器验收 |
| `FixedVec<T,N>` | 内联固定容量顺序容器 | 已扩展源码 | 未运行编译器验收 |
| `VecDeque<T>` | 循环缓冲双端队列 | 新增源码 | 未运行编译器验收 |
| `FixedDeque<T,N>` | 固定容量循环双端队列 | 新增源码 | 未运行编译器验收 |
| `Stack<T>` | Vec 尾部 LIFO 适配器 | 新增源码 | 未运行编译器验收 |
| `Queue<T>` | VecDeque FIFO 适配器 | 新增源码 | 未运行编译器验收 |
| `BinaryHeap<T>` | 基于 Vec 的最大二叉堆 | 新增源码 | 未运行编译器验收 |
| `PriorityQueue<T>` | BinaryHeap 别名，默认最大优先级 | 新增源码 | 未运行编译器验收 |
| `LinkedList<T>` | 双向链表，节点地址稳定 | 未实现 | 未验收 |
| `HashMap<K,V>` / `HashSet<T>` | 开放寻址哈希容器 | 未实现 | 协议设计待完成 |
| `BTreeMap<K,V>` / `BTreeSet<T>` | B 树有序容器 | 未实现 | 比较器与删除算法待完成 |

## 已落地源码

- `stdlib/coll/vec.vqr`
- `stdlib/coll/fixed.vqr`
- `stdlib/coll/deque.vqr`
- `stdlib/coll/adapters.vqr`

## API 和语义裁定

### Vec

- `reserve`、`reserveAdditional`、`shrinkTo`、`shrinkToFit` 负责容量；分配在移动任何元素之前完成。
- `push` / `insert` 是简洁消费 API，错误通过 `!void` 传播；`pushPreserving` / `insertPreserving` 在失败时返回原值。
- `pop`、`remove`、`swapRemove` 将元素所有权交还调用者；越界返回 null。
- `replace` 返回旧值；`replacePreserving` 在索引无效时交还新值。
- `append` 先预留目标容量，再转移源元素；分配失败时两个容器均未改变。
- `splitOff` 先分配结果缓冲区，再转移后缀。
- `retain` 通过只读元素指针调用谓词，删除项恰好析构一次。
- `clone` 是显式复制 API，只有元素可复制时才可实例化。
- 缓冲区重分配会使所有旧切片和元素指针失效；插入/删除会使受影响位置失效。
- `intoIter` 接管整个 Vec；游标逐项移出，未取元素由迭代器持有的 Vec 在析构时清理。

### FixedVec

- 不分配堆内存，容量固定为 N。
- `push` / `tryPush`、`insert` 在容量不足时返回 `PushResult.Full(value)`，原值归还调用者。
- 无效索引的 insert 返回 `PushResult.Failed(error,value)`；remove / replace 的无效索引返回 null。
- `MaybeUninit` 槽仅在 length 范围内包含活对象；缩短、清空、析构按逆序销毁活槽。

### VecDeque / FixedDeque

- 逻辑索引 0 对应 head；物理位置通过环形映射计算。
- 动态版本扩容时先分配新的槽数组，再按逻辑顺序迁移元素，完成后 head 归零。
- 固定版本 N=0 合法；插入返回 Full(value)，不进行模零运算。
- `iterRef` / `iterMut` 保存环形元数据，按逻辑顺序正反向遍历；结构修改会使迭代器失效。
- 动态 deque 当前以 `Vec<MaybeUninit<T>>` 持有槽存储，元素只有在成功 write 后才算初始化。

### 适配器与堆

- Stack 以 Vec 尾部实现 LIFO。
- Queue 以 VecDeque 两端实现 FIFO。
- BinaryHeap 为最大堆，父节点不小于子节点；push 上滤、pop 下滤，建堆自底向上。
- PriorityQueue 是 BinaryHeap 的公开类型别名，默认最大堆。自定义比较器和最小堆接口尚未落地。

## 语言能力边界

1. 当前泛型约束和接口实现能力足以实现由具体类型实现接口的泛型算法；编译器尚不支持泛型接口方法。
2. 内建整数、浮点和字符没有自动实现用户定义 Hash / Eq / Compare 接口的机制。因此不能宣称 `HashMap<i32,V>` 等自动成立。hash 包应定义 Hash、Hasher、BuildHasher，并为标准标量提供显式基础实现或由编译器提供受限内建桥接；本任务禁止自行增加内建函数，故需先确定纯标准库方案。
3. 泛型方法调用要求实例化期完成类型推导；复杂的键借用比较、闭包比较器和异构查询 API 需要单独验收。
4. `mem.MaybeUninit<T>` 目前提供 raw slot 与显式 write/take/drop。容器不得对未初始化槽执行普通读取或析构。
5. 现有分配 intrinsic `__vqr_alloc<T>` 使用默认堆分配器；Vec 尚未接入 `mem.AllocatorRef`。分配器生命周期需要随容器保留，不能仅保存裸的临时 allocator 指针。

## 未实现目标

- LinkedList、ListCursor、节点 splice。
- HashMap、HashSet、hash 包及随机种子实现。
- BTreeMap、BTreeSet。
- 双端 deque 的自定义分配器。
- 自定义比较器的 BinaryHeap / MinHeap。
- 完整 Drain、Splice、Entry API、map/set 投影迭代器。
- 完整迭代器失效诊断；目前属于调用方合同，不进行静态生命周期检查。

## 验收限制

本次会话的 GitHub 连接可读取和写入仓库，但当前没有可用的本地 Git clone / shell 构建环境；直接克隆失败于运行环境无法解析 github.com。因而本分支的提交只证明文件已写入 Git 对象，不证明 Vaqrin 编译器能接受这些新增源码。不得将“未运行”描述为“通过”。下一步必须在有 LLVM SDK 和 Python 的仓库工作区执行独立容器验收，发现语法/类型问题后修正，再补充 move-only、析构计数、环形回绕、分配失败和迭代器正反向测试。
