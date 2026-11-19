# coll / hash 容器实现状态与公开 API

本文件记录 `feature/coll-containers` 分支的源码现状。状态“已写入”表示源码已进入分支；本分支没有在本次环境中运行 vaqc/LLVM 编译，因此不表示编译或运行验收通过。

## 1. 类型矩阵

| 类型 | 数据结构 | 源码状态 | 编译验收 |
|---|---|---|---|
| `Vec<T>` | 连续动态数组 | 已写入并扩展 | 未运行 |
| `FixedVec<T,N>` | 内联固定容量数组 | 已写入并扩展 | 未运行 |
| `VecDeque<T>` | 循环缓冲动态双端队列 | 已写入 | 未运行 |
| `FixedDeque<T,N>` | 内联固定容量循环双端队列 | 已写入 | 未运行 |
| `LinkedList<T>` | 双向链表 | 已写入 | 未运行 |
| `Stack<T>` | Vec 尾部 LIFO 适配器 | 已写入 | 未运行 |
| `Queue<T>` | VecDeque FIFO 适配器 | 已写入 | 未运行 |
| `BinaryHeap<T>` | 二叉堆，比较器回调 | 已写入 | 未运行 |
| `PriorityQueue<T>` | BinaryHeap 类型别名 | 已写入 | 未运行 |
| `HashMap<K,V>` | 开放寻址哈希表 | 已写入 | 未运行 |
| `HashSet<T>` | 基于 HashMap 的集合 | 已写入 | 未运行 |
| `BTreeMap<K,V>` | 最小度数 4 的 B 树 | 已写入 | 未运行 |
| `BTreeSet<T>` | 基于 BTreeMap 的集合 | 已写入 | 未运行 |

本分支不再把 API 草案中的 `SmallVec`、`RingBuffer`、`ForwardList`、`StableVec`、`OrderedHashMap`、`FlatMap`、`MultiMap`、`SlotMap` 等符号视为已经实现。

## 2. 源码文件

- `stdlib/coll/vec.vqr`
- `stdlib/coll/fixed.vqr`
- `stdlib/coll/deque.vqr`
- `stdlib/coll/linked_list.vqr`
- `stdlib/coll/adapters.vqr`
- `stdlib/coll/hash_map.vqr`
- `stdlib/coll/btree.vqr`
- `stdlib/hash/hash.vqr`

## 3. 顺序容器 API

### Vec<T>

构造与容量：
- `init() -> Self`
- `withCapacity(n: usize) -> !Self`
- `withAllocator(allocator: mem.AllocatorRef) -> Self`
- `withCapacityAndAllocator(n: usize, allocator: mem.AllocatorRef) -> !Self`
- `fromSlice(values: []const T) -> !Self`（要求 T 可复制）
- `fromIter<I: Movable>(values: I) -> !Self`
- `len() -> usize`、`capacity() -> usize`、`isEmpty() -> bool`
- `reserve(minCapacity: usize) -> !void`
- `reserveAdditional(extra: usize) -> !void`
- `shrinkTo(minCapacity: usize) -> !void`、`shrinkToFit() -> !void`

元素操作：
- `push(value: T) -> !void`
- `pushPreserving(value: T) -> PushResult<T>`
- `pop() -> ?T`
- `insert(index: usize, value: T) -> !void`
- `insertPreserving(index: usize, value: T) -> PushResult<T>`
- `remove(index: usize) -> ?T`、`swapRemove(index: usize) -> ?T`
- `replace(index: usize, value: T) -> ?T`
- `replacePreserving(index: usize, value: T) -> ReplaceResult<T>`
- `retain<F: Movable>(predicate: F) -> void`
- `extend<I: Movable>(values: I) -> !void`
- `append(other: *Self) -> !void`
- `splitOff(index: usize) -> !Self`
- `clone() -> !Self`（要求 T 可复制）

借用、遍历和维护：
- `get(index: usize) -> ?*const T`、`getMut(index: usize) -> ?*T`
- `at(index: usize) -> *const T`、`atMut(index: usize) -> *T`
- `first() -> ?*const T`、`firstMut() -> ?*T`
- `last() -> ?*const T`、`lastMut() -> ?*T`
- `asPtr() -> ?*T`、`asConstPtr() -> ?*const T`
- `asSlice() -> []T`、`asConstSlice() -> []const T`
- `iter() -> iter.SliceIter<T>`（要求 T 可复制）
- `iterRef() -> iter.SliceIterRef<T>`、`iterMut() -> iter.SliceIterMut<T>`
- `reverseIter() -> iter.RevIter<...>`
- `intoIter(value: Self) -> IntoVecIter<T>`
- `swap(a: usize,b: usize) -> void`、`reverse() -> void`
- `truncate(n: usize) -> void`、`clear() -> void`、`deinit() -> void`

错误与所有权合同：容量计算溢出返回 `CapacityError.Overflow`；索引插入越界返回 `CapacityError.IndexOutOfBounds`。常规 `push/insert` 消费输入值，传播分配错误；保留式 API 在失败时通过 `PushResult.Failed(error,value)` 归还输入值。remove/pop 返回所有权；get/first/last 只返回借用指针。reserve 先申请新缓冲区，再移动旧元素；申请失败时原 Vec 和元素保持不变。append 先预留目标容量，再转移源元素。splitOff 先申请结果缓冲区，再移动后缀。扩容使旧切片和指针失效；插入/删除使受影响位置失效。

### FixedVec<T,N>

- `init() -> Self`、`len() -> usize`、`capacity() -> usize`、`isEmpty() -> bool`
- `push(value: T) -> PushResult<T>`、`tryPush(value: T) -> PushResult<T>`
- `pop() -> ?T`
- `insert(index: usize,value: T) -> PushResult<T>`
- `remove(index: usize) -> ?T`、`swapRemove(index: usize) -> ?T`
- `replace(index: usize,value: T) -> ?T`
- `replacePreserving(index: usize,value: T) -> ReplaceResult<T>`
- `get(index: usize) -> ?*const T`、`getMut(index: usize) -> ?*T`
- `first() -> ?*const T`、`firstMut() -> ?*T`、`last() -> ?*const T`、`lastMut() -> ?*T`
- `asSlice() -> []T`、`asConstSlice() -> []const T`
- `retain<F: Movable>(predicate: F) -> void`、`extend<I: Movable>(values: I) -> !void`
- `append(other: *Self) -> !void`、`splitOff(index: usize) -> !Self`
- `swap(a: usize,b: usize) -> void`、`reverse() -> void`
- `clone() -> Self`（要求 T 可复制）
- `iter() -> iter.SliceIter<T>`（要求 T 可复制）
- `iterRef() -> iter.SliceIterRef<T>`、`iterMut() -> iter.SliceIterMut<T>`
- `reverseIter() -> iter.RevIter<...>`
- `intoIter(value: Self) -> IntoFixedVecIter<T,N>`
- `truncate(n: usize) -> void`、`clear() -> void`、`deinit() -> void`

所有槽位由 MaybeUninit 管理；只有 [0,len) 是活对象。满容量 push/insert 返回 `PushResult.Full(value)`，无效插入索引返回 `PushResult.Failed(error,value)`。N=0 合法。

### VecDeque<T> 与 FixedDeque<T,N>

VecDeque 构造：
- `init() -> Self`
- `withCapacity(n: usize) -> !Self`
- `withAllocator(allocator: mem.AllocatorRef) -> Self`
- `withCapacityAndAllocator(n: usize, allocator: mem.AllocatorRef) -> !Self`

VecDeque 状态和操作：
- `len() -> usize`、`capacity() -> usize`、`isEmpty() -> bool`
- `reserve(n: usize) -> !void`、`reserveAdditional(n: usize) -> !void`、`shrinkToFit() -> !void`
- `pushFront(value: T) -> !void`、`pushBack(value: T) -> !void`
- `pushFrontPreserving(value: T) -> PushResult<T>`、`pushBackPreserving(value: T) -> PushResult<T>`
- `popFront() -> ?T`、`popBack() -> ?T`
- `front() -> ?*const T`、`frontMut() -> ?*T`、`back() -> ?*const T`、`backMut() -> ?*T`
- `get(index: usize) -> ?*const T`、`getMut(index: usize) -> ?*T`
- `insert(index: usize,value: T) -> !void`、`remove(index: usize) -> ?T`
- `swapRemove(index: usize) -> ?T`、`replace(index: usize,value: T) -> ?T`
- `retain<F: Movable>(predicate: F) -> void`、`extend<I: Movable>(values: I) -> !void`、`truncate(n: usize) -> void`
- `append(other: *Self) -> !void`、`splitOff(index: usize) -> !Self`
- `clone() -> !Self`（要求 T 可复制）
- `iter() -> DequeIter<T>`（要求 T 可复制）
- `iterRef() -> DequeIterRef<T>`、`iterMut() -> DequeIterMut<T>`
- `reverseIter() -> iter.RevIter<...>`、`intoIter(value: Self) -> IntoDequeIter<T>`
- `swap(a: usize,b: usize) -> void`、`reverse() -> void`
- `clear() -> void`、`deinit() -> void`

FixedDeque<T,N> 提供 `init/len/capacity/isEmpty`、`pushFront/pushBack`、`popFront/popBack`、`front/frontMut/back/backMut`、`get/getMut`、`insert/remove/replace`、`retain/extend/append/splitOff/truncate`、`swap/reverse`、`iter/iterRef/iterMut/reverseIter/intoIter`、`clone/clear/deinit`。固定容量 push/insert 返回 `PushResult<T>`，不分配堆内存。动态版本使用 Vec<MaybeUninit<T>> 作为槽存储；扩容先申请新槽，再按逻辑顺序搬迁，完成后 head 归零。迭代器以逻辑索引映射物理环形槽；结构修改使借用迭代器失效。

### LinkedList<T>

- `init() -> Self`、`withAllocator(allocator: mem.AllocatorRef) -> Self`
- `len() -> usize`、`isEmpty() -> bool`
- `pushFront(value: T) -> !void`、`pushBack(value: T) -> !void`
- `insert(index: usize,value: T) -> !void`
- `popFront() -> ?T`、`popBack() -> ?T`、`remove(index: usize) -> ?T`
- `get(index: usize) -> ?*const T`、`getMut(index: usize) -> ?*T`
- `front() -> ?*const T`、`frontMut() -> ?*T`、`back() -> ?*const T`、`backMut() -> ?*T`
- `append(other: *Self) -> !void`、`splitOff(index: usize) -> !Self`、`reverse() -> void`
- `iterRef() -> ListIterRef<T>`、`iterMut() -> ListIterMut<T>`
- `intoIter(value: Self) -> IntoListIter<T>`
- `clear() -> void`、`deinit() -> void`

节点由 Box 单独分配，链表用 Vec<^ListNode<T>> 保存 Box 所有权及 allocator release context，节点间仅保存非拥有链接。节点地址在其他节点插入/删除时稳定。头尾 push/pop 为 O(1)；按索引操作为 O(n)。append 先合并 Box 所有权表，再改链指针；splitOff 先为结果所有权表预留容量，再移动后缀节点。当前没有 ListCursor 和 splice API。

## 4. 适配器与堆

### Stack<T>

`init`、`withCapacity`、`withAllocator`、`withCapacityAndAllocator`、`len`、`isEmpty`、`push`、`pushPreserving`、`pop`、`peek`、`peekMut`、`clear`。底层 Vec 尾部实现 LIFO。

### Queue<T>

`init`、`withCapacity`、`withAllocator`、`withCapacityAndAllocator`、`len`、`isEmpty`、`enqueue`、`enqueuePreserving`、`dequeue`、`front`、`back`、`clear`。底层 VecDeque 实现 FIFO。

### BinaryHeap<T> / PriorityQueue<T>

BinaryHeap 构造时需要比较回调 `func(*const T,*const T) bool`，也提供 `max()` / `min()` 使用元素的 `<` 运算符：
- `init(less, order)`、`withComparator(less,order)`
- `withMaxComparator(less)`、`withMinComparator(less)`、`max()`、`min()`
- `withCapacity(n,less,order) -> !Self`
- `fromSlice(items,less,order) -> !Self`（要求 T 可复制）
- `fromIter(items,less,order) -> !Self`（支持 move-only 元素）

操作：
- `len`、`capacity`、`isEmpty`、`peek`
- `push(value) -> !void`、`pushPreserving(value) -> PushResult<T>`
- `pop() -> ?T`、`replaceTop(value) -> !?T`、`pushPop(value) -> T`
- `intoSortedVec() -> !Vec<T>`、`clear() -> void`

`HeapOrder.Max` 时 less 定义严格小于，堆顶为最大值；`HeapOrder.Min` 时堆顶为最小值。PriorityQueue 是 BinaryHeap 的类型别名。父子比较完全通过指针回调，不复制元素，因而支持 move-only 类型。

## 5. Hash 与关联容器

### hash 包

- `Hasher` 接口：`write(bytes: []const u8) -> void`、`finish() -> usize`
- `HashKey` 接口：`hash(state: *Hasher) -> void`、`equals(other: *const Self) -> bool`
- `FnvHasher`：`init`、`initSeeded`、`writeByte`、`write`、`finish`、`reset`、`resetSeeded`
- 函数：`hashValue`、`hashValueSeeded`、`hashBytes`、`hashBytesSeeded`、`hashCombine`、`hashKey`、`equalKey`
- 标量回调：i8/u8/i16/u16/i32/u32/i64/u64/bool 对应的 `hashX` 与 `eqX`

FnvHasher 是 FNV-1a 32 位，种子为调用方提供的 u32。它不是密码学哈希，也不抵御主动构造碰撞；本包没有系统熵源，因此没有伪造 `randomSeed()`。需要处理不可信键时，应在调用方提供可靠随机种子并换用抗洪泛哈希回调。

### HashMap<K,V> / HashSet<T>

HashMap 构造必须提供两个回调：
- `hashFunction: func(*const K,u32) usize`
- `equalsFunction: func(*const K,*const K) bool`

HashMap 构造：
- `init(hashFunction,equalsFunction)`
- `withHasher(hashFunction,equalsFunction)`
- `withSeed(seed,hashFunction,equalsFunction)`
- `withCapacity(n,hashFunction,equalsFunction) -> !Self`
- `withCapacityAndSeed(n,seed,hashFunction,equalsFunction) -> !Self`
- `withAllocator(hashFunction,equalsFunction,allocator)`
- `withSeedAndAllocator(seed,hashFunction,equalsFunction,allocator)`
- `withCapacityAndAllocator(n,hashFunction,equalsFunction,allocator) -> !Self`

HashMap 操作：
- `len`、`capacity`、`isEmpty`、`reserve`、`reserveAdditional`
- `insert(key,value) -> !?V`
- `insertPreserving(key,value) -> InsertResult<K,V>`
- `get(key) -> ?*const V`、`getMut(key) -> ?*V`
- `getKeyValue(key) -> ?(*const K,*const V)`、`containsKey(key) -> bool`
- `remove(key) -> ?V`、`removeEntry(key) -> ?(K,V)`
- `retain(predicate)`、`extend(entries) -> !void`
- `iterRef() -> !MapIterRef<K,V>`、`clear`、`shrinkToFit -> !void`、`deinit`

HashSet 构造使用相同的哈希与相等回调，提供 `init`、`withHasher`、`withCapacity`、`withSeed`、`withAllocator`、`withCapacityAndAllocator`、`withSeedAndAllocator`。操作包括 `len/capacity/isEmpty/insert/remove/take/contains/reserve/clear/iterRef/deinit`；`iterRef()` 返回 `!SetIterRef<T>`。

哈希表采用开放寻址和线性探测。删除留下 tombstone，查找越过 tombstone，插入优先复用第一个 tombstone；扩容在新表完整分配后重新插入活键值，失败时原表不变。桶类型为包内私有，公开迭代器不暴露桶布局。`iterRef()` 为构造指针索引分配一段小型 Vec，因此返回 `!MapIterRef<K,V>`；索引只保存键和值的借用指针，容器结构修改会使迭代器失效。键通过相等回调比较，哈希与相等一致性由调用方负责。键不提供可变借用接口。insert 是消费式接口；insertPreserving 在增长失败时返回原 key/value。

### BTreeMap<K,V> / BTreeSet<T>

BTreeMap 构造必须提供严格弱序回调 `less: func(*const K,*const K) bool`：
- `init(less)`、`withComparator(less)`
- `len`、`isEmpty`
- `insert(key,value) -> !?V`
- `get(key) -> ?*const V`、`getMut(key) -> ?*V`、`containsKey(key) -> bool`
- `remove(key) -> ?V`、`removeEntry(key) -> ?(K,V)`
- `first/last -> ?(*const K,*const V)`
- `popFirst/popLast -> ?(K,V)`
- `lowerBound/upperBound -> ?*const K`
- `iterRef() -> !BTreeIterRef<K,V>`、`iterMut() -> !BTreeIterMut<K,V>`
- `intoIter`、`clear`、`deinit`

BTreeSet 提供 `init(less)`、`withComparator(less)`、`len/isEmpty/contains/insert/remove/take/first/last/popFirst/popLast/lowerBound/upperBound/iterRef/clear/deinit`。

树节点最多 7 个键、8 个子节点，最小度数为 4。插入采用 top-down split；删除实现借兄弟、合并子节点和根收缩。比较等价定义为 `!less(a,b) && !less(b,a)`。迭代器创建时分配一个有序指针索引，失败会返回分配错误；迭代期间不得修改树。当前 BTree 节点使用默认 Box 分配器，尚无自定义分配器入口。remove 的 key 参数应来自树外部稳定存储；不要把本树迭代器借出的键指针作为同一次删除的查询参数。

## 6. 内存、所有权和迭代器合同

- 所有拥有型容器均不可复制；移动容器只转移其缓冲区/节点所有权。
- 非 Copy 元素通过 `__vqr_slot_write/take/drop` 或 `MaybeUninit` 显式管理，不对未初始化槽读取或析构。
- Vec/VecDeque/HashMap/LinkedList 可保存借用的 AllocatorRef；分配器和上下文必须活到容器及其所有派生节点释放之后。
- LinkedList 节点 Box 自带 allocator release context；链表额外持有 Box 所有权表。
- FixedVec/FixedDeque 不做堆分配。
- Vec/VecDeque/HashMap 的扩容先分配新存储，再移动元素；分配失败保留原状态。
- HashMap rehash 后旧桶被清理；删除/retain 后 tombstone 参与探测。
- BTree split 先申请右节点再改动树；删除不分配。
- 借用迭代器不拥有元素。容器结构修改、扩容、删除或重排会使相应迭代器/指针失效；编译器当前不做容器借用生命周期静态检查。
- intoIter 接管容器；尚未消费的元素由迭代器持有的源容器析构。

## 7. 依赖关系

| 源文件 | 依赖 |
|---|---|
| `coll/vec.vqr` | core、iter、mem |
| `coll/fixed.vqr` | core、iter、mem |
| `coll/deque.vqr` | core、iter、mem |
| `coll/linked_list.vqr` | mem、Vec |
| `coll/adapters.vqr` | Vec、VecDeque、PushResult |
| `coll/hash_map.vqr` | core、mem、Vec |
| `coll/btree.vqr` | FixedVec、Vec、Box |
| `hash/hash.vqr` | 语言内建数值、接口和 FNV 运算 |

没有修改编译器前端、LLVM 后端、运行时、共享验收脚本、CMake 配置、io/fs/path/bufio/fmt/bytes。

## 8. 尚未实现或尚未验证

尚未实现：ListCursor、链表 splice、Vec Drain/Splice、Map Entry API、keys/values/valuesMut 投影、IntoMapIter、HashSet union/intersection/difference、BTree range/cursor、BTreeMap/Set 自定义分配器、SipHash/系统随机种子、OrderedHashMap、FlatMap、MultiMap、SmallVec、RingBuffer、ForwardList、StableVec、SlotMap。

当前仅完成源码写入和静态审查；没有在本次环境运行 vaqc、LLVM、ASan/UBSan 或独立运行验收。下一步应在仓库工作区运行独立 coll 验收，重点覆盖 move-only、析构计数、OOM、deque 回绕、hash 碰撞/扩容/tombstone、B-tree 插入删除分裂合并及迭代器正反向遍历。
