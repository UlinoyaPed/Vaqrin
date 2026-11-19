@core|最小预定义类型的辅助函数；不复制内建类型体系
#alias Box<T>|^T 的兼容别名；拥有型指针
#alias Vec<T>|coll.Vec<T> 的迁移期门面，最终是否保留以包策略为准
= isSome<T>(value: *const ?T)->bool; isNone<T>(value: *const ?T)->bool; isOk<T>(value: *const !T)->bool; isErr<T>(value: *const !T)->bool; unwrapOr<T>(value: ?T, fallback: func() T)->T; swap<T>(a: *T,b: *T)->void; replace<T>(dst: *T,value: T)->T; identity<T>(value: T)->T; drop<T>(value: T)->void; unreachable()->never; unreachableUnchecked()->never
@opt|可空值组合子；不把嵌套 ?T 自动展平
= isSome<T>(value: *const ?T)->bool; isNone<T>(value: *const ?T)->bool; asPtr<T>(value: *const ?T)->?*const T; asMutPtr<T>(value: *?T)->?*T; map<T,U,F>(value: ?T,f: F)->?U; andThen<T,U,F>(value: ?T,f: F)->?U; flatten<T>(value: ??T)->?T; filter<T,F>(value: ?T,pred: F)->?T; inspect<T,F>(value: ?T,f: F)->?T; zip<A,B>(a: ?A,b: ?B)->?(A,B); zipWith<A,B,U,F>(a: ?A,b: ?B,f: F)->?U; unzip<A,B>(value: ?(A,B))->(?A,?B); xor<T>(a: ?T,b: ?T)->?T; unwrapOr<T>(value: ?T,default: T)->T; unwrapOrElse<T,F>(value: ?T,f: F)->T; expect<T>(value: ?T,msg: str)->T; contains<T>(value: *const ?T,needle: *const T)->bool; take<T>(value: *?T)->?T; replace<T>(slot: *?T,value: T)->?T; insert<T>(slot: *?T,value: T)->*T; getOrInsert<T>(slot: *?T,value: T)->*T; getOrInsertWith<T,F>(slot: *?T,f: F)->*T; toVec<T>(value: ?T)->!coll.Vec<T>; intoIter<T>(value: ?T)->iter.OnceOptional<T>; transpose<T>(value: ?!T)->!?T
@result|错误结果 !T 的组合与错误身份，保留错误/成功的层次
#enum ResultState|Ok / Err 状态标签，完整 !T 仍为内建类型
= isOk<T>(value: *const !T)->bool; isErr<T>(value: *const !T)->bool; map<T,U,F>(value: !T,f: F)->!U; andThen<T,U,F>(value: !T,f: F)->!U; orElse<T,F>(value: !T,f: F)->!T; inspect<T,F>(value: !T,f: F)->!T; inspectErr<T,F>(value: !T,f: F)->!T; unwrapOr<T>(value: !T,default: T)->T; unwrapOrElse<T,F>(value: !T,f: F)->T; expect<T>(value: !T,msg: str)->T; okValue<T>(value: *const !T)->?*const T; errorOf<T>(value: *const !T)->?ErrorId; transpose<T>(value: !?T)->?!T; collect<T,I>(values: I)->!coll.Vec<T>; tryJoin<T,I>(values: I)->!T
@cmp|值比较和全序约束；不以指针或对象填充作为默认键比较
#enum Ordering|Less, Equal, Greater
#interface Equal<T>|equals(other: *const T)->bool
#interface Compare<T>|compare(other: *const T)->Ordering
#interface TotalOrder<T>|compareTotal(other: *const T)->Ordering
#record KeyCompare<K>|可调用比较策略包装
= eq<T>(a: *const T,b: *const T)->bool; ne<T>(a: *const T,b: *const T)->bool; compare<T>(a: *const T,b: *const T)->Ordering; partialCmp<T>(a: *const T,b: *const T)->?Ordering; totalCmp<T>(a: *const T,b: *const T)->Ordering; reverse(order: Ordering)->Ordering; min<T>(a: T,b: T)->T; max<T>(a: T,b: T)->T; minBy<T,F>(a: T,b: T,less: F)->T; maxBy<T,F>(a: T,b: T,less: F)->T; clamp<T>(value: T,low: T,high: T)->T; between<T>(value: *const T,low: *const T,high: *const T)->bool
@convert|无隐式窄化；数值转换显式标识失败及舍入
#enum Rounding|TowardZero, Floor, Ceil, NearestEven, AwayFromZero
#enum OverflowMode|Checked, Wrap, Saturate
= narrow<T,U>(value: U)->!T; widen<T,U>(value: U)->T; truncToInt<T,F>(value: F)->!T; floorToInt<T,F>(value: F)->!T; ceilToInt<T,F>(value: F)->!T; roundToInt<T,F>(value: F)->!T; toFloat<T,U>(value: U)->T; bitCast<T,U>(value: U)->T; checkedCast<T,U>(value: U)->!?T; toChar(value: u32)->!char; fromChar(value: char)->u32; toBool(value: u8)->!bool
@meta|类型性质与目标布局只在编译期求值，禁止假定宿主布局
#record TypeInfo|类型名称、类别、性质
#record TargetInfo|目标三元组、字宽、端序、ABI
#enum Endian|Little, Big
= typeName<T>()->str; typeId<T>()->TypeId; sizeOf<T>()->usize; alignOf<T>()->usize; strideOf<T>()->usize; bitWidth<T>()->usize; isCopyable<T>()->bool; isMovable<T>()->bool; isSendable<T>()->bool; isShareable<T>()->bool; isZeroSized<T>()->bool; isTriviallyDrop<T>()->bool; isAssignable<T,U>()->bool; hasInterface<T,I>()->bool; targetInfo()->TargetInfo; targetHas(capability: str)->bool; arrayLen<T,comptime N: usize>(value: *const [N]T)->usize; offsetOf<T>(fieldName: str)->usize
@mem|分配、独占/共享所有权、未初始化槽与 RAII
#interface Allocator|alloc(layout: Layout)->!?*u8; free(ptr: *u8,layout: Layout)->void
#record Layout|目标大小、对齐、步长
+ fromSizeAlign(size: usize,align: usize)->!Self; array<T>(count: usize)->!Self; size()->usize; align()->usize; extend(other: Layout)->!Layout
#record AllocatorRef|ctx + 函数表 + 必要保活状态
+ alloc(layout: Layout)->!?*u8; free(ptr: *u8,layout: Layout)->void; resize(ptr: *u8,old: Layout,new: Layout)->!?*u8; owns(ptr: *const u8)->bool
#class GlobalAllocator|宿主默认分配器适配
+ init()->Self; alloc(layout: Layout)->!?*u8; free(ptr: *u8,layout: Layout)->void
#class Arena|块链线性分配/整批释放
+ init(backing: AllocatorRef)->Self; alloc(layout: Layout)->!?*u8; mark()->ArenaMark; rewind(mark: ArenaMark)->void; reset()->void; used()->usize; capacity()->usize; deinit()->void
#record ArenaMark|回退标记
#class Pool<T>|固定槽回收池
+ init(backing: AllocatorRef)->Self; acquire()->!?*T; release(ptr: *T)->void; allocated()->usize; clear()->void; deinit()->void
#class FixedBufferAllocator|调用者给缓冲、无堆
+ init(bytes: []u8)->Self; alloc(layout: Layout)->!?*u8; mark()->usize; reset()->void; remaining()->usize
#class CountingAllocator|统计分配器包装
+ init(backing: AllocatorRef)->Self; liveBytes()->usize; allocations()->usize; peakBytes()->usize; asAllocator()->AllocatorRef
#class FailingAllocator|按序号模拟分配失败
+ init(backing: AllocatorRef,failAt: usize)->Self; asAllocator()->AllocatorRef; reset(failAt: usize)->void
#class MaybeUninit<T>|与 T 同布局的原始槽
+ uninit()->Self; write(value: T)->*T; asPtr()->*T; asConstPtr()->*const T; assumeInit()->T; assumeInitRef()->*T; assumeInitDrop()->void
#class ManuallyDrop<T>|显式承担销毁责任
+ new(value: T)->Self; asPtr()->*T; intoInner()->T; dropInPlace()->void
#class ScopeGuard<F>|离开作用域时单次清理
+ init(callback: F)->Self; dismiss()->void; runNow()->void; isArmed()->bool
#class Shared<T>|原子引用计数拥有者
+ new(value: T)->!Self; clone()->Self; downgrade()->Weak<T>; strongCount()->usize; weakCount()->usize; asPtr()->*const T; ptrEq(other: *const Self)->bool; getMut()->?*T; tryUnwrap()->!T
#class Weak<T>|不延长 T 生命周期的弱所有权
+ clone()->Self; upgrade()->?Shared<T>; strongCount()->usize; weakCount()->usize; ptrEq(other: *const Self)->bool
#class Rc<T>|仅单线程的引用计数所有者
+ new(value: T)->!Self; clone()->Self; downgrade()->RcWeak<T>; getMut()->?*T; strongCount()->usize; tryUnwrap()->!T
#class RcWeak<T>|单线程弱所有权
+ upgrade()->?Rc<T>; clone()->Self
#enum Cow<T>|Borrowed(*const T) / Owned(T)
+ asRef()->*const T; toMut()->!*T; intoOwned()->!T; isBorrowed()->bool; isOwned()->bool
#class BoxedSlice<T>|单次分配、固定长度、拥有元素的切片，与借用 []T 类型不同
+ fromSlice(values: []const T)->!Self; len()->usize; isEmpty()->bool; get(index: usize)->?*const T; getMut(index: usize)->?*T; asSlice()->[]T; asConstSlice()->[]const T; intoRawParts()->( *T,usize,AllocatorRef ); clone()->!Self
#class Function<Args,R>|拥有型闭包类型擦除
+ from<F>(callback: F)->!Self; call(args: Args)->R; intoCall(args: Args)->R; isEmpty()->bool; clear()->void
= layout<T>()->Layout; alloc(layout: Layout,allocator: AllocatorRef)->!?*u8; free(ptr: *u8,layout: Layout,allocator: AllocatorRef)->void; copyBytes(src: []const u8,dst: []u8)->!void; moveBytes(src: []const u8,dst: []u8)->!void; zeroBytes(dst: []u8)->void; compareBytes(a: []const u8,b: []const u8)->cmp.Ordering; swap<T>(a: *T,b: *T)->void; replace<T>(dst: *T,value: T)->T; dropInPlace<T>(ptr: *T)->void; take<T>(ptr: *T)->T; write<T>(ptr: *T,value: T)->void; alignUp(value: usize,alignment: usize)->!usize; isAligned(ptr: *const u8,alignment: usize)->bool; newIn<T>(allocator: AllocatorRef,value: T)->!^T
@cell|单线程内部可变性与单次初始化；运行期借用守卫
#class Cell<T>|适用于可复制小值
+ new(value: T)->Self; get()->T; set(value: T)->void; replace(value: T)->T; update<F>(f: F)->void; intoInner()->T
#class RefCell<T>|运行期检查的读写借用
+ new(value: T)->Self; borrow()->!BorrowRef<T>; borrowMut()->!BorrowMut<T>; tryBorrow()->?BorrowRef<T>; tryBorrowMut()->?BorrowMut<T>; replace(value: T)->!T; intoInner()->T
#class BorrowRef<T>|只读守卫
+ get()->*const T; map<U,F>(f: F)->BorrowRef<U>
#class BorrowMut<T>|独占可变守卫
+ get()->*T; map<U,F>(f: F)->BorrowMut<U>
#class OnceCell<T>|单线程只初始化一次
+ new()->Self; get()->?*const T; getMut()->?*T; set(value: T)->?T; getOrInit<F>(f: F)->*T; getOrTryInit<F>(f: F)->!*T; take()->?T
#class OnceLock<T>|跨线程单次初始化
+ new()->Self; get()->?*const T; set(value: T)->?T; getOrInit<F>(f: F)->*const T; getOrTryInit<F>(f: F)->!*const T
#class Lazy<T,F>|首次使用才构造
+ init(factory: F)->Self; force()->!*const T; isInitialized()->bool
#class UnsafeCell<T>|显式 unsafe 内部可变性
+ new(value: T)->Self; get()->*T; intoInner()->T
@bits|无分配整数按位工具和位流
#enum BitOrder|LsbFirst, MsbFirst
#class BitReader|位级读取游标
+ init(source: []const u8,order: BitOrder)->Self; readBits(count: usize)->!?u64; peekBits(count: usize)->!?u64; alignByte()->void; position()->usize; remaining()->usize
#class BitWriter|写入位缓冲
+ init(dest: []u8,order: BitOrder)->Self; writeBits(value: u64,count: usize)->!void; alignByte(fillBit: bool)->!void; position()->usize; writtenBytes()->usize
= countOnes<T>(value: T)->usize; countZeros<T>(value: T)->usize; leadingZeros<T>(value: T)->usize; trailingZeros<T>(value: T)->usize; reverseBits<T>(value: T)->T; reverseBytes<T>(value: T)->T; byteSwap<T>(value: T)->T; rotateLeft<T>(value: T,n: usize)->T; rotateRight<T>(value: T,n: usize)->T; firstSet<T>(value: T)->?usize; lastSet<T>(value: T)->?usize; testBit<T>(value: T,index: usize)->bool; setBit<T>(value: T,index: usize)->T; clearBit<T>(value: T,index: usize)->T; toggleBit<T>(value: T,index: usize)->T; maskLow<T>(count: usize)->T; extractBits<T>(value: T,start: usize,count: usize)->T; insertBits<T>(value: T,field: T,start: usize,count: usize)->T; isPowerOfTwo<T>(value: T)->bool; nextPowerOfTwo<T>(value: T)->!T; alignUp(value: usize,alignment: usize)->!usize; alignDown(value: usize,alignment: usize)->usize
@hash|Hash/Eq 一致性、进程内防碰撞种子与版本化稳定摘要
#interface Hash<T>|hash(state: *Hasher)->void
#interface Hasher|write(bytes: []const u8)->void; finish()->u64
#interface BuildHasher|build()->Hasher
#class SipHasher|宿主哈希防碰撞引擎
+ init(key: [16]u8)->Self; write(bytes: []const u8)->void; finish()->u64; reset()->void
#class FnvHasher|非密码学固定算法，性能用途
+ init()->Self; write(bytes: []const u8)->void; finish()->u64; reset()->void
#class StableHasher|持久跨进程、算法版本固定
+ init(version: u32)->Self; write(bytes: []const u8)->void; finishBytes()->[32]u8
#record HashSeed|独立随机种子
= hash<T>(value: *const T)->u64; hashWith<T,H>(value: *const T,hasher: *H)->u64; hashBytes(bytes: []const u8)->u64; hashText(value: str)->u64; hashCombine(seed: u64,value: u64)->u64; randomSeed()->!HashSeed; stableDigest(bytes: []const u8)->[32]u8; compareDigest(a: []const u8,b: []const u8)->bool
@num|整数约束、检查运算、宽整数与数学基础
#alias Unsigned<T>|与 T 同位宽的无符号整数类型级映射，需要编译期类型映射支持
#record IntLimits<T>|整数最小最大、位宽
#record FloatInfo<T>|浮点精度、指数和 NaN 特性
#class BigUint|动态精度无符号整数
+ zero()->Self; fromU64(value: u64)->Self; fromBytes(bytes: []const u8,endian: meta.Endian)->!Self; add(other: *const Self)->!Self; sub(other: *const Self)->!Self; mul(other: *const Self)->!Self; divMod(other: *const Self)->!(Self,Self); shl(bits: usize)->!Self; shr(bits: usize)->Self; compare(other: *const Self)->cmp.Ordering; toBytes(endian: meta.Endian)->!coll.Vec<u8>; toText(radix: u8)->!string
#class BigInt|动态精度有符号整数
+ zero()->Self; fromI64(value: i64)->Self; parse(value: str,radix: u8)->!Self; add(other: *const Self)->!Self; sub(other: *const Self)->!Self; mul(other: *const Self)->!Self; divMod(other: *const Self)->!(Self,Self); abs()->!Self; negate()->Self; compare(other: *const Self)->cmp.Ordering; toText(radix: u8)->!string
#class Rational<T>|有理数，分母非零且标准化
+ new(num: T,den: T)->!Self; numerator()->T; denominator()->T; add(other: *const Self)->!Self; sub(other: *const Self)->!Self; mul(other: *const Self)->!Self; div(other: *const Self)->!Self; compare(other: *const Self)->cmp.Ordering
= minValue<T>()->T; maxValue<T>()->T; checkedAdd<T>(a: T,b: T)->!T; checkedSub<T>(a: T,b: T)->!T; checkedMul<T>(a: T,b: T)->!T; checkedDiv<T>(a: T,b: T)->!T; checkedNeg<T>(value: T)->!T; wrappingAdd<T>(a: T,b: T)->T; wrappingSub<T>(a: T,b: T)->T; wrappingMul<T>(a: T,b: T)->T; saturatingAdd<T>(a: T,b: T)->T; saturatingSub<T>(a: T,b: T)->T; saturatingMul<T>(a: T,b: T)->T; abs<T>(value: T)->!T; absDiff<T>(a: T,b: T)->Unsigned<T>; signum<T>(value: T)->T; divEuclid<T>(a: T,b: T)->T; remEuclid<T>(a: T,b: T)->T; divMod<T>(a: T,b: T)->(T,T); gcd<T>(a: T,b: T)->T; lcm<T>(a: T,b: T)->!T; powInt<T>(base: T,exp: usize)->!T; midpoint<T>(a: T,b: T)->T; average<T>(values: []const T)->!?T; sum<T>(values: []const T)->!T; product<T>(values: []const T)->!T
@coll|拥有型容器和收集；所有可失败增长返回 !T，集合键不可原地修改
#class Vec<T>|连续动态数组，尾插摊还 O(1)
+ init()->Self; withCapacity(n: usize)->!Self; withAllocator(allocator: mem.AllocatorRef)->Self; fromSlice(values: []const T)->!Self; fromIter<I>(items: I)->!Self; len()->usize; capacity()->usize; isEmpty()->bool; asSlice()->[]T; asConstSlice()->[]const T; asPtr()->?*T; asConstPtr()->?*const T; get(index: usize)->?*const T; getMut(index: usize)->?*T; at(index: usize)->*const T; atMut(index: usize)->*T; first()->?*const T; firstMut()->?*T; last()->?*const T; lastMut()->?*T; push(value: T)->!void; pushPreserving(value: T)->PushResult<T>; pop()->?T; insert(index: usize,value: T)->!void; insertSlice(index: usize,values: []const T)->!void; remove(index: usize)->?T; swapRemove(index: usize)->?T; replace(index: usize,value: T)->?T; reserve(minCapacity: usize)->!void; reserveAdditional(extra: usize)->!void; shrinkToFit()->!void; shrinkTo(minCapacity: usize)->!void; resize(newLen: usize,value: T)->!void; resizeWith<F>(newLen: usize,f: F)->!void; extend<I>(values: I)->!void; append(other: *Vec<T>)->!void; clear()->void; truncate(n: usize)->void; retain<F>(predicate: F)->void; dedupAdjacent<F>(eq: F)->void; reverse()->void; rotateLeft(n: usize)->void; rotateRight(n: usize)->void; swap(a: usize,b: usize)->void; splitOff(index: usize)->!Self; drain(start: usize,end: usize)->Drain<T>; splice(start: usize,end: usize,replaceWith: []const T)->!void; sort()->void; sortBy<F>(less: F)->void; stableSortBy<F>(less: F)->!void; binarySearch(value: *const T)->?usize; clone()->!Self; iter()->iter.SliceIter<T>; iterRef()->iter.SliceIterRef<T>; iterMut()->iter.SliceIterMut<T>; intoIter()->coll.IntoVecIter<T>; intoBoxedSlice()->!mem.BoxedSlice<T>
#class FixedVec<T,comptime N: usize>|内联 N 槽，零堆分配
+ init()->Self; len()->usize; capacity()->usize; isEmpty()->bool; push(value: T)->PushResult<T>; tryPush(value: T)->PushResult<T>; pop()->?T; insert(index: usize,value: T)->PushResult<T>; remove(index: usize)->?T; clear()->void; asSlice()->[]T; asConstSlice()->[]const T; get(index: usize)->?*const T; getMut(index: usize)->?*T; truncate(n: usize)->void; retain<F>(predicate: F)->void; intoIter()->coll.IntoFixedVecIter<T,N>
#class SmallVec<T,comptime N: usize>|前 N 项内联，溢出转堆
+ init()->Self; withAllocator(allocator: mem.AllocatorRef)->Self; len()->usize; capacity()->usize; isInline()->bool; push(value: T)->!void; pop()->?T; insert(index: usize,value: T)->!void; remove(index: usize)->?T; reserve(n: usize)->!void; shrinkToInline()->!void; asSlice()->[]T; asConstSlice()->[]const T; clear()->void; intoVec()->!Vec<T>
#class Deque<T>|环形动态双端队列
+ init()->Self; withCapacity(n: usize)->!Self; len()->usize; capacity()->usize; isEmpty()->bool; pushFront(value: T)->!void; pushBack(value: T)->!void; popFront()->?T; popBack()->?T; front()->?*const T; frontMut()->?*T; back()->?*const T; backMut()->?*T; get(index: usize)->?*const T; getMut(index: usize)->?*T; insert(index: usize,value: T)->!void; remove(index: usize)->?T; rotateLeft(n: usize)->void; rotateRight(n: usize)->void; reserve(n: usize)->!void; makeContiguous()->[]T; asSlices()->([]const T,[]const T); clear()->void; iter()->coll.DequeIter<T>; iterMut()->coll.DequeIterMut<T>; intoIter()->coll.IntoDequeIter<T>
#class RingBuffer<T,comptime N: usize>|固定容量循环缓冲
+ init()->Self; len()->usize; capacity()->usize; isFull()->bool; isEmpty()->bool; tryPush(value: T)->PushResult<T>; pushOverwrite(value: T)->?T; pop()->?T; front()->?*const T; back()->?*const T; asSlices()->([]const T,[]const T); clear()->void
#class LinkedList<T>|双向链表、稳定节点地址
+ init()->Self; len()->usize; isEmpty()->bool; pushFront(value: T)->!void; pushBack(value: T)->!void; popFront()->?T; popBack()->?T; front()->?*const T; frontMut()->?*T; back()->?*const T; backMut()->?*T; cursorFront()->ListCursor<T>; cursorBack()->ListCursor<T>; append(other: *Self)->!void; splitOff(index: usize)->!Self; clear()->void; iterRef()->coll.ListIter<T>; iterMut()->coll.ListIterMut<T>
#class ListCursor<T>|LinkedList 节点游标
+ current()->?*T; moveNext()->void; movePrev()->void; insertBefore(value: T)->!void; insertAfter(value: T)->!void; removeCurrent()->?T; spliceBefore(other: *LinkedList<T>)->!void; spliceAfter(other: *LinkedList<T>)->!void
#class ForwardList<T>|单向链表
+ init()->Self; isEmpty()->bool; pushFront(value: T)->!void; popFront()->?T; front()->?*const T; cursorBeforeBegin()->ForwardCursor<T>; clear()->void; reverse()->void; iterRef()->coll.ForwardListIter<T>
#class ForwardCursor<T>|单链表前驱游标
+ next()->?*T; insertAfter(value: T)->!void; eraseAfter()->?T; spliceAfter(other: *ForwardList<T>)->!void
#class StableVec<T>|分块槽地址稳定，序号可变
+ init()->Self; len()->usize; push(value: T)->!usize; get(index: usize)->?*const T; getMut(index: usize)->?*T; remove(index: usize)->?T; swapRemove(index: usize)->?T; clear()->void; iterRef()->coll.StableVecIter<T>
#class HashMap<K,V>|开放寻址表，均摊 O(1)，迭代顺序不保证
+ init()->Self; withCapacity(n: usize)->!Self; withHasher(hasher: hash.BuildHasher)->Self; len()->usize; capacity()->usize; isEmpty()->bool; reserve(n: usize)->!void; get(key: *const K)->?*const V; getMut(key: *const K)->?*V; getKeyValue(key: *const K)->?( *const K,*const V ); containsKey(key: *const K)->bool; insert(key: K,value: V)->!?V; tryInsert(key: K,value: V)->InsertResult<K,V>; remove(key: *const K)->?V; removeEntry(key: *const K)->?(K,V); entry(key: K)->Entry<K,V>; getOrInsertWith<F>(key: K,f: F)->!*V; retain<F>(predicate: F)->void; clear()->void; shrinkToFit()->!void; extend<I>(entries: I)->!void; keys()->coll.Keys<K,V>; values()->coll.Values<K,V>; valuesMut()->coll.ValuesMut<K,V>; items()->coll.Items<K,V>; iterRef()->coll.MapIterRef<K,V>; intoIter()->coll.IntoMapIter<K,V>; clone()->!Self
#enum Entry<K,V>|Occupied / Vacant 借用柄，独占一次 entry 生命周期
+ andModify<F>(f: F)->Self; orInsert(value: V)->!*V; orInsertWith<F>(f: F)->!*V; orDefault()->!*V; key()->*const K; isOccupied()->bool; isVacant()->bool
#class OccupiedEntry<K,V>|已存在键的入口
+ key()->*const K; get()->*const V; getMut()->*V; replace(value: V)->V; remove()->V; removeEntry()->(K,V)
#class VacantEntry<K,V>|缺失键，持有待插键
+ key()->*const K; intoKey()->K; insert(value: V)->!*V
#class HashSet<T>|按 Hash/Eq 判等的无序集合
+ init()->Self; len()->usize; isEmpty()->bool; insert(value: T)->!bool; remove(value: *const T)->bool; take(value: *const T)->?T; contains(value: *const T)->bool; reserve(n: usize)->!void; clear()->void; retain<F>(pred: F)->void; union(other: *const Self)->!Self; intersection(other: *const Self)->!Self; difference(other: *const Self)->!Self; symmetricDifference(other: *const Self)->!Self; iterRef()->coll.SetIterRef<T>
#class OrderedHashMap<K,V>|哈希索引与稳定顺序双链表
+ init()->Self; len()->usize; insert(key: K,value: V)->!?V; get(key: *const K)->?*const V; getMut(key: *const K)->?*V; remove(key: *const K)->?V; moveToFront(key: *const K)->bool; moveToBack(key: *const K)->bool; first()->?(*const K,*const V); last()->?(*const K,*const V); keys()->coll.Keys<K,V>; items()->coll.Items<K,V>; clear()->void
#class OrderedHashSet<T>|保留首次插入顺序的集合
+ init()->Self; insert(value: T)->!bool; contains(value: *const T)->bool; remove(value: *const T)->bool; len()->usize; clear()->void; iterRef()->coll.SetIterRef<T>
#class TreeMap<K,V>|B 树有序字典
+ init()->Self; withComparator<C>(cmp: C)->Self; len()->usize; isEmpty()->bool; get(key: *const K)->?*const V; getMut(key: *const K)->?*V; containsKey(key: *const K)->bool; insert(key: K,value: V)->!?V; remove(key: *const K)->?V; removeEntry(key: *const K)->?(K,V); first()->?(*const K,*const V); last()->?(*const K,*const V); popFirst()->?(K,V); popLast()->?(K,V); lowerBound(key: *const K)->TreeCursor<K,V>; upperBound(key: *const K)->TreeCursor<K,V>; range(low: *const K,high: *const K)->coll.TreeRange<K,V>; keys()->coll.Keys<K,V>; values()->coll.Values<K,V>; items()->coll.Items<K,V>; clear()->void
#class TreeCursor<K,V>|有序树位置游标
+ key()->?*const K; value()->?*const V; valueMut()->?*V; next()->void; prev()->void
#class TreeSet<T>|B 树有序集合
+ init()->Self; len()->usize; contains(value: *const T)->bool; insert(value: T)->!bool; remove(value: *const T)->bool; take(value: *const T)->?T; first()->?*const T; last()->?*const T; popFirst()->?T; popLast()->?T; lowerBound(value: *const T)->?*const T; upperBound(value: *const T)->?*const T; range(low: *const T,high: *const T)->coll.TreeRange<T>; clear()->void
#class FlatMap<K,V>|排序的连续数组，查 O(log n) 插 O(n)
+ init()->Self; fromSorted(items: []const (K,V))->!Self; fromItems<I>(items: I)->!Self; len()->usize; capacity()->usize; get(key: *const K)->?*const V; getMut(key: *const K)->?*V; insert(key: K,value: V)->!?V; remove(key: *const K)->?V; lowerBound(key: *const K)->usize; upperBound(key: *const K)->usize; range(low: *const K,high: *const K)->[]const (K,V); asSlice()->[]const (K,V); reserve(n: usize)->!void; clear()->void
#class FlatSet<T>|排序的连续集合
+ init()->Self; fromSorted(items: []const T)->!Self; fromItems<I>(items: I)->!Self; len()->usize; contains(value: *const T)->bool; insert(value: T)->!bool; remove(value: *const T)->bool; lowerBound(value: *const T)->usize; upperBound(value: *const T)->usize; asSlice()->[]const T; clear()->void
#class MultiMap<K,V>|允许重复键、按等价组维护值
+ init()->Self; len()->usize; keyCount()->usize; insertOne(key: K,value: V)->!void; getAll(key: *const K)->?[]const V; count(key: *const K)->usize; removeOne(key: *const K)->?V; removeAll(key: *const K)->!Vec<V>; equalRange(key: *const K)->coll.Items<K,V>; clear()->void
#class MultiSet<T>|允许重复元素的计数集合
+ init()->Self; len()->usize; distinctLen()->usize; insertOne(value: T)->!void; count(value: *const T)->usize; removeOne(value: *const T)->bool; removeAll(value: *const T)->usize; contains(value: *const T)->bool; clear()->void
#class Stack<T>|Vec 尾部实现的后进先出
+ init()->Self; len()->usize; isEmpty()->bool; push(value: T)->!void; pop()->?T; peek()->?*const T; peekMut()->?*T; clear()->void
#class Queue<T>|Deque 实现的先进先出
+ init()->Self; len()->usize; isEmpty()->bool; enqueue(value: T)->!void; dequeue()->?T; front()->?*const T; back()->?*const T; clear()->void
#class PriorityQueue<T>|二叉堆，默认最大堆
+ init()->Self; withComparator<C>(less: C)->Self; fromItems(items: []const T)->!Self; len()->usize; isEmpty()->bool; push(value: T)->!void; peek()->?*const T; pop()->?T; replaceTop(value: T)->?T; pushPop(value: T)->T; clear()->void; intoSortedVec()->Vec<T>
#class BitSet<comptime N: usize>|固定 N 位，无堆
+ init()->Self; len()->usize; test(index: usize)->bool; set(index: usize)->void; reset(index: usize)->void; flip(index: usize)->void; setAll()->void; clear()->void; countOnes()->usize; countZeros()->usize; firstSet()->?usize; lastSet()->?usize; iterOnes()->coll.BitIndices; union(other: *const Self)->Self; intersection(other: *const Self)->Self; difference(other: *const Self)->Self; xor(other: *const Self)->Self; shiftLeft(n: usize)->void; shiftRight(n: usize)->void
#class BitVec|动态位集合，末字无效位归零
+ init()->Self; withLen(n: usize,value: bool)->!Self; len()->usize; capacity()->usize; resize(n: usize,value: bool)->!void; push(value: bool)->!void; pop()->?bool; get(index: usize)->?bool; set(index: usize,value: bool)->void; flip(index: usize)->void; countOnes()->usize; countZeros()->usize; firstSet()->?usize; iterOnes()->coll.BitIndices; union(other: *const Self)->!Self; intersection(other: *const Self)->!Self; clear()->void
#record SlotKey|index + generation + domain 组成持久键
#class SlotMap<T>|代际键和稳定占位索引
+ init()->Self; len()->usize; insert(value: T)->!SlotKey; get(key: SlotKey)->?*const T; getMut(key: SlotKey)->?*T; contains(key: SlotKey)->bool; remove(key: SlotKey)->?T; retain<F>(pred: F)->void; clear()->void; keys()->coll.SlotKeys; items()->coll.SlotItems<T>
#class SparseSet|稠密元素 ID 列表和稀疏反查
+ init()->Self; len()->usize; isEmpty()->bool; contains(id: usize)->bool; insert(id: usize)->!bool; remove(id: usize)->bool; clear()->void; iter()->iter.SliceIter<usize>
#class SparseMap<V>|ID 键直接稀疏索引
+ init()->Self; len()->usize; contains(id: usize)->bool; get(id: usize)->?*const V; getMut(id: usize)->?*V; insert(id: usize,value: V)->!?V; remove(id: usize)->?V; clear()->void
#class DisjointSet|路径压缩、按大小合并
+ init(count: usize)->!Self; len()->usize; add()->!usize; find(index: usize)->usize; unite(a: usize,b: usize)->bool; same(a: usize,b: usize)->bool; componentSize(index: usize)->usize; componentCount()->usize
#class LruCache<K,V>|哈希索引与最近使用链表
+ init(capacity: usize)->!Self; len()->usize; capacity()->usize; isEmpty()->bool; get(key: *const K)->?*V; peek(key: *const K)->?*const V; contains(key: *const K)->bool; put(key: K,value: V)->!(?V,?(K,V)); remove(key: *const K)->?V; popLeastRecent()->?(K,V); popMostRecent()->?(K,V); clear()->void; resize(capacity: usize)->!void
#class SlotKeys|代际 SlotMap 所有活跃键的迭代器
+ next()->?SlotKey
#class ListIter<T>|借用链表元素的只读迭代器，节点删除后失效
+ next()->?*const T; nextBack()->?*const T
#class DequeIterMut<T>|Deque 逐槽可变指针迭代器，源结构修改前有效
+ next()->?*T; nextBack()->?*T; remaining()->usize
#class SetIterRef<T>|集合元素只读迭代器
+ next()->?*const T
#class IntoVecIter<T>|接管 Vec 并逐项取走
+ init(values: Vec<T>)->Self; next()->?T; nextBack()->?T; remaining()->usize
#class ListIterMut<T>|借用链表元素的可变迭代器
+ next()->?*T; nextBack()->?*T
#class SlotItems<T>|代际 SlotMap 键值借用迭代器
+ next()->?(SlotKey,*const T)
#class MapIterRef<K,V>|借用哈希映射键值，禁止修改键和结构
+ next()->?(*const K,*const V); sizeHint()->iter.SizeHint
#class TreeRange<K,V>|B 树的有序区间只读迭代器
+ next()->?(*const K,*const V)
#class StableVecIter<T>|分块稳定地址容器的只读迭代器
+ next()->?*const T
#class ValuesMut<K,V>|映射可变值投影，不授予修改键权限
+ next()->?*V
#class IntoDequeIter<T>|消费 Deque 逐个移交元素
+ next()->?T; nextBack()->?T; remaining()->usize
#class ForwardListIter<T>|借用单链表元素的只读迭代器
+ next()->?*const T
#class IntoMapIter<K,V>|消费哈希映射并移交键值
+ next()->?(K,V); sizeHint()->iter.SizeHint
#class IntoFixedVecIter<T,comptime N: usize>|接管固定数组容器
+ init(values: FixedVec<T,N>)->Self; next()->?T; nextBack()->?T
#class Items<K,V>|映射只读键值投影
+ next()->?(*const K,*const V)
#class DequeIter<T>|双段环形 Deque 的借只读值迭代器；复制要求 T: Copyable
+ next()->?T; nextBack()->?T; remaining()->usize
#class Grouping<T,K,I,F>|按键聚合计划，不存全部组值
+ init(source: I,key: F)->Self; eachCount()->!HashMap<K,usize>; eachSum<U,F2>(value: F2)->!HashMap<K,U>; eachMax<U,F2>(value: F2)->!HashMap<K,U>; eachMin<U,F2>(value: F2)->!HashMap<K,U>; eachFold<A,F2>(initial: A,f: F2)->!HashMap<K,A>; eachAggregate<A,F2>(f: F2)->!HashMap<K,A>
#class BitIndices|按从小到大顺序枚举集合中为 1 的位索引
+ next()->?usize; remaining()->usize
#class Keys<K,V>|映射只读键投影
+ next()->?*const K
#class Values<K,V>|映射只读值投影
+ next()->?*const V
#class Drain<T>|借用 Vec 区间的消费游标，提前销毁时清理剩余元素并修复缺口；源容器不能同时修改
+ next()->?T; nextBack()->?T; remaining()->usize; isEmpty()->bool
#enum PushResult<T>|Stored / Full(T) / Failed(error,T)
#enum InsertResult<K,V>|Inserted / Replaced(V) / Failed(error,K,V)
= fromBoxedSlice<T>(source: mem.BoxedSlice<T>)->!Vec<T>; sorted<T>(values: []const T)->!Vec<T>; sortedBy<T,F>(values: []const T,less: F)->!Vec<T>; sortedByCachedKey<T,K,F>(values: []const T,key: F)->!Vec<T>; topK<T,F>(values: []const T,k: usize,less: F)->!Vec<T>; sample<T,R>(values: []const T,k: usize,rng: *R)->!Vec<T>; reservoirSample<I,T,R>(items: I,k: usize,rng: *R)->!Vec<T>; equalRangeIndices<T,F>(a: []const T,b: []const T,less: F)->!Vec<(usize,usize)>
= toVec<T,I>(items: I)->!Vec<T>; toSet<T,I>(items: I)->!HashSet<T>; toMap<K,V,I>(items: I)->!HashMap<K,V>; collectInto<T,I>(items: I,dst: *Vec<T>)->!usize; buildVec<T,F>(f: F)->!Vec<T>; buildSet<T,F>(f: F)->!HashSet<T>; buildMap<K,V,F>(f: F)->!HashMap<K,V>; groupBy<T,K,I,F>(items: I,key: F)->!OrderedHashMap<K,Vec<T>>; groupingBy<T,K,I,F>(items: I,key: F)->Grouping<T,K,I,F>; associateBy<T,K,I,F>(items: I,key: F)->!HashMap<K,T>; associateWith<T,V,I,F>(items: I,value: F)->!HashMap<T,V>; countEach<T,K,I,F>(items: I,key: F)->!HashMap<K,usize>; distinct<T,I>(items: I)->!Vec<T>; distinctBy<T,K,I,F>(items: I,key: F)->!Vec<T>; partition<T,I,F>(items: I,predicate: F)->!(Vec<T>,Vec<T>); unzip<A,B,I>(items: I)->!(Vec<A>,Vec<B>); zipToVec<A,B,IA,IB>(a: IA,b: IB)->!Vec<(A,B)>; concat<T,I>(items: I)->!Vec<T>
#class SliceIter<T>|原 collections 切片迭代类，迁移到 iter 后保留短期兼容
+ init(values: []const T)->Self; next()->?T
#class Enumerate<T>|原切片枚举类，迁移到 iter.EnumerateIter
+ init(values: []const T)->Self; next()->?(usize,T)
#class Zip<A,B>|原双切片 zip 类
+ init(a: []const A,b: []const B)->Self; next()->?(A,B)
#class Map<T,U>|原只接 func(T) U 的切片映射类
+ init(values: []const T,transform: func(T) U)->Self; next()->?U
#class Filter<T>|原只接函数指针的切片过滤类
+ init(values: []const T,pred: func(T) bool)->Self; next()->?T
#class FilterMap<T,U>|补丁增加的切片可空映射类
+ init(values: []const T,transform: func(T) ?U)->Self; next()->?U
= iter<T>(values: []const T)->SliceIter<T>; enumerate<T>(values: []const T)->Enumerate<T>; zip<A,B>(a: []const A,b: []const B)->Zip<A,B>; map<T,U>(values: []const T,f: func(T) U)->Map<T,U>; filter<T>(values: []const T,pred: func(T) bool)->Filter<T>; filterMap<T,U>(values: []const T,f: func(T) ?U)->FilterMap<T,U>; take<T>(values: []const T,n: usize)->SliceIter<T>; skip<T>(values: []const T,n: usize)->SliceIter<T>
@iter|统一惰性单次游标；终止只取 next() 外层 ?/!?，回调可持有捕获
#interface Iterator<T>|可变 next() ?T；迭代器本身拥有/借用上游
+ next()->?T; sizeHint()->SizeHint
#interface FallibleIterator<T>|可变 next() !?T，读错不是结束
+ next()->!?T; sizeHint()->SizeHint
#interface DoubleEndedIterator<T>|还可从尾部获取
+ nextBack()->?T
#interface ExactSizeIterator<T>|有可准确查询的剩余长度
+ remaining()->usize
#interface FusedIterator<T>|遇结束后永远结束
#record SizeHint|下界 usize 与可能的上界 ?usize
#class Cursor<I,T>|拥有上游且允许链式转出剩余状态的游标
+ init(source: I)->Self; next()->?T; intoSource()->I; map<U,F>(f: F)->Cursor<MapIter<I,T,U,F>,U>; filter<F>(pred: F)->Cursor<FilterIter<I,T,F>,T>; filterMap<U,F>(f: F)->Cursor<FilterMapIter<I,T,U,F>,U>; take(n: usize)->Cursor<TakeIter<I,T>,T>; skip(n: usize)->Cursor<SkipIter<I,T>,T>; enumerate()->Cursor<EnumerateIter<I,T>,(usize,T)>; fold<A,F>(initial: A,f: F)->A
#class SliceIter<T>|从只读切片复制 T
+ init(values: []const T)->Self; next()->?T; nextBack()->?T; remaining()->usize; sizeHint()->SizeHint
#class SliceIterRef<T>|按元素借用 *const T
+ init(values: []const T)->Self; next()->?*const T; nextBack()->?*const T; remaining()->usize
#class SliceIterMut<T>|按元素借用 *T
+ init(values: []T)->Self; next()->?*T; nextBack()->?*T; remaining()->usize
#class RangeIter<T>|由起止与步长产生整数序列
+ init(start: T,end: T,step: T)->!Self; next()->?T; nextBack()->?T; remaining()->?usize
#class MapIter<I,T,U,F>|映射、保留回调返回的可空层次
+ init(source: I,transform: F)->Self; next()->?U; sizeHint()->SizeHint
#class FilterIter<I,T,F>|按谓词跳过项目
+ init(source: I,pred: F)->Self; next()->?T; sizeHint()->SizeHint
#class FilterMapIter<I,T,U,F>|回调返回 ?U 时丢弃无值项
+ init(source: I,transform: F)->Self; next()->?U; sizeHint()->SizeHint
#class TryMapIter<I,T,U,F>|回调返回 !U，next 返回 !?U
+ init(source: I,transform: F)->Self; next()->!?U
#class TryFilterIter<I,T,F>|可失败谓词
+ init(source: I,pred: F)->Self; next()->!?T
#class MapWhileIter<I,T,U,F>|首次返回 null 即终止
+ init(source: I,transform: F)->Self; next()->?U
#class InspectIter<I,T,F>|只观察、保持原元素所有权
+ init(source: I,observer: F)->Self; next()->?T
#class TakeIter<I,T>|至多产 n 项
+ init(source: I,count: usize)->Self; next()->?T; remainingLimit()->usize
#class SkipIter<I,T>|跳过前 n 项
+ init(source: I,count: usize)->Self; next()->?T
#class TakeWhileIter<I,T,F>|包含界限前项目，条件失败耗掉边界项
+ init(source: I,pred: F)->Self; next()->?T
#class SkipWhileIter<I,T,F>|保留首个不满足项
+ init(source: I,pred: F)->Self; next()->?T
#class StepByIter<I,T>|步长必须大于 0
+ init(source: I,step: usize)->!Self; next()->?T
#class EnumerateIter<I,T>|0 起的索引、usize 溢出检查
+ init(source: I)->Self; next()->?(usize,T)
#class ZipIter<IA,IB,A,B>|以较短源结束
+ init(a: IA,b: IB)->Self; next()->?(A,B)
#class ChainIter<IA,IB,T>|先消耗第一源，再消耗第二源
+ init(a: IA,b: IB)->Self; next()->?T
#class PeekableIter<I,T>|最多缓存一项
+ init(source: I)->Self; peek()->?*const T; next()->?T
#class FuseIter<I,T>|保证结束后不再拉取上游
+ init(source: I)->Self; next()->?T
#class FlattenIter<I,J,T>|展开一层可迭代元素
+ init(source: I)->Self; next()->?T
#class FlatMapIter<I,J,T,U,F>|把每项投影为内部游标并串接
+ init(source: I,f: F)->Self; next()->?U
#class RevIter<I,T>|双端迭代器的反向外壳
+ init(source: I)->Self; next()->?T
#class ChunksIter<T>|从切片输出末块可不足大小
+ init(values: []const T,chunkSize: usize)->!Self; next()->?[]const T
#class WindowsIter<T>|从切片输出固定宽度的重叠只读窗
+ init(values: []const T,width: usize)->!Self; next()->?[]const T
#class ScanIter<I,T,S,U,F>|带状态的逐项转换
+ init(source: I,state: S,f: F)->Self; next()->?U; intoState()->S
#class RunningFoldIter<I,T,S,F>|初值也交付，输出 n+1 项
+ init(source: I,initial: S,f: F)->Self; next()->?S
#class OnceIter<T>|仅有一个元素
+ init(value: T)->Self; next()->?T
#class OnceOptional<T>|零或一个元素
+ init(value: ?T)->Self; next()->?T
#class RepeatIter<T>|无限重复可复制值
+ init(value: T)->Self; next()->?T
#class FromFnIter<T,F>|按回调提供下一项
+ init(callback: F)->Self; next()->?T
#class EmptyIter<T>|空游标
+ init()->Self; next()->?T
#class CycleIter<I,T>|需要可重启迭代源/显式缓存
+ init(source: I)->!Self; next()->?T
= from<I,T>(source: I)->Cursor<I,T>; iter<T>(values: []const T)->SliceIter<T>; iterRef<T>(values: []const T)->SliceIterRef<T>; iterMut<T>(values: []T)->SliceIterMut<T>; range<T>(start: T,end: T)->RangeIter<T>; rangeStep<T>(start: T,end: T,step: T)->!RangeIter<T>; empty<T>()->EmptyIter<T>; once<T>(value: T)->OnceIter<T>; onceOptional<T>(value: ?T)->OnceOptional<T>; repeat<T>(value: T)->RepeatIter<T>; fromFn<T,F>(callback: F)->FromFnIter<T,F>; map<I,T,U,F>(source: I,f: F)->MapIter<I,T,U,F>; filter<I,T,F>(source: I,pred: F)->FilterIter<I,T,F>; filterMap<I,T,U,F>(source: I,f: F)->FilterMapIter<I,T,U,F>; tryMap<I,T,U,F>(source: I,f: F)->TryMapIter<I,T,U,F>; tryFilter<I,T,F>(source: I,pred: F)->TryFilterIter<I,T,F>; mapWhile<I,T,U,F>(source: I,f: F)->MapWhileIter<I,T,U,F>; inspect<I,T,F>(source: I,observer: F)->InspectIter<I,T,F>; enumerate<I,T>(source: I)->EnumerateIter<I,T>; zip<IA,IB,A,B>(a: IA,b: IB)->ZipIter<IA,IB,A,B>; chain<IA,IB,T>(a: IA,b: IB)->ChainIter<IA,IB,T>; take<I,T>(source: I,n: usize)->TakeIter<I,T>; skip<I,T>(source: I,n: usize)->SkipIter<I,T>; stepBy<I,T>(source: I,step: usize)->!StepByIter<I,T>; takeWhile<I,T,F>(source: I,pred: F)->TakeWhileIter<I,T,F>; skipWhile<I,T,F>(source: I,pred: F)->SkipWhileIter<I,T,F>; flatMap<I,T,U,F>(source: I,f: F)->FlatMapIter<I,U,T,F>; flatten<I,T>(source: I)->FlattenIter<I,T>; flattenOptional<I,T>(source: I)->FilterMapIter<I,?T,T>; peekable<I,T>(source: I)->PeekableIter<I,T>; fuse<I,T>(source: I)->FuseIter<I,T>; rev<I,T>(source: I)->RevIter<I,T>; scan<I,T,S,U,F>(source: I,state: S,f: F)->ScanIter<I,T,S,U,F>; runningFold<I,T,S,F>(source: I,initial: S,f: F)->RunningFoldIter<I,T,S,F>; chunks<T>(data: []const T,size: usize)->!ChunksIter<T>; windows<T>(data: []const T,size: usize)->!WindowsIter<T>; adjacent<T>(data: []const T)->AdjacentIter<T>; product<IA,IB,A,B>(a: IA,b: IB)->!ProductIter<IA,IB,A,B>; fold<I,T,A,F>(source: I,initial: A,f: F)->A; reduce<I,T,F>(source: I,f: F)->?T; tryFold<I,T,A,F>(source: I,initial: A,f: F)->!A; forEach<I,T,F>(source: I,f: F)->void; tryForEach<I,T,F>(source: I,f: F)->!void; forEachWhile<I,T,F>(source: I,f: F)->void; any<I,T,F>(source: I,pred: F)->bool; all<I,T,F>(source: I,pred: F)->bool; none<I,T,F>(source: I,pred: F)->bool; find<I,T,F>(source: I,pred: F)->?T; findLast<I,T,F>(source: I,pred: F)->?T; position<I,T,F>(source: I,pred: F)->?usize; count<I,T>(source: I)->usize; countIf<I,T,F>(source: I,pred: F)->usize; nth<I,T>(source: I,index: usize)->?T; last<I,T>(source: I)->?T; min<I,T>(source: I)->?T; max<I,T>(source: I)->?T; minBy<I,T,F>(source: I,less: F)->?T; maxBy<I,T,F>(source: I,less: F)->?T; sum<I,T>(source: I)->!T; productValues<I,T>(source: I)->!T; sumBy<I,T,U,F>(source: I,f: F)->!U; joinToString<I,T>(source: I,separator: str)->!string; sizeHint<I,T>(source: *const I)->SizeHint
#class AdjacentIter<T>|只读切片相邻元素成对迭代器
+ init(values: []const T)->Self; next()->?(T,T)
#class ProductIter<IA,IB,A,B>|两个可重启或缓存的输入笛卡尔积迭代器
+ init(a: IA,b: IB)->!Self; next()->?(A,B)
@algo|借用切片与迭代范围上的搜索、排序、二分、堆、数值和集合算法
= allOf<T,F>(values: []const T,pred: F)->bool; anyOf<T,F>(values: []const T,pred: F)->bool; noneOf<T,F>(values: []const T,pred: F)->bool; count<T>(values: []const T,needle: *const T)->usize; countIf<T,F>(values: []const T,pred: F)->usize; find<T>(values: []const T,needle: *const T)->?usize; findIf<T,F>(values: []const T,pred: F)->?usize; findIfNot<T,F>(values: []const T,pred: F)->?usize; findLast<T>(values: []const T,needle: *const T)->?usize; adjacentFind<T,F>(values: []const T,eq: F)->?usize; mismatch<T,U,F>(a: []const T,b: []const U,eq: F)->?(usize,usize); equal<T,U,F>(a: []const T,b: []const U,eq: F)->bool; lexCompare<T,U,F>(a: []const T,b: []const U,less: F)->cmp.Ordering; startsWith<T,F>(a: []const T,prefix: []const T,eq: F)->bool; endsWith<T,F>(a: []const T,suffix: []const T,eq: F)->bool; contains<T,F>(a: []const T,needle: *const T,eq: F)->bool; containsSubrange<T,F>(a: []const T,pattern: []const T,eq: F)->bool; search<T,F>(a: []const T,pattern: []const T,eq: F)->?usize; searchLast<T,F>(a: []const T,pattern: []const T,eq: F)->?usize; searchN<T,F>(a: []const T,count: usize,value: *const T,eq: F)->?usize
= copyTo<T>(src: []const T,dst: []T)->!usize; copyBackward<T>(src: []const T,dst: []T)->!usize; moveInto<T>(src: []T,dst: []T)->!usize; moveBackward<T>(src: []T,dst: []T)->!usize; fill<T>(dst: []T,value: T)->void; fillN<T>(dst: []T,n: usize,value: T)->!void; generate<T,F>(dst: []T,f: F)->void; generateN<T,F>(dst: []T,n: usize,f: F)->!void; transformTo<T,U,F>(src: []const T,dst: []U,f: F)->!usize; transformZipTo<A,B,U,F>(a: []const A,b: []const B,dst: []U,f: F)->!usize; forEach<T,F>(values: []const T,f: F)->void; forEachMut<T,F>(values: []T,f: F)->void; replace<T>(values: []T,old: *const T,new: T)->void; replaceIf<T,F>(values: []T,pred: F,new: T)->void; replaceCopyTo<T>(src: []const T,dst: []T,old: *const T,new: T)->!usize; removeIf<T,F>(values: []T,pred: F)->usize; removeValue<T>(values: []T,value: *const T)->usize; uniqueAdjacent<T,F>(values: []T,eq: F)->usize; uniqueCopyTo<T,F>(src: []const T,dst: []T,eq: F)->!usize; swapRanges<T>(a: []T,b: []T)->!usize
= reverse<T>(values: []T)->void; reverseCopyTo<T>(src: []const T,dst: []T)->!usize; rotate<T>(values: []T,middle: usize)->void; rotateCopyTo<T>(src: []const T,middle: usize,dst: []T)->!usize; shiftLeft<T>(values: []T,n: usize)->usize; shiftRight<T>(values: []T,n: usize)->usize; partition<T,F>(values: []T,pred: F)->usize; stablePartition<T,F>(values: []T,pred: F)->!usize; partitionCopyTo<T,F>(src: []const T,yes: []T,no: []T,pred: F)->!(usize,usize); isPartitioned<T,F>(values: []const T,pred: F)->bool; partitionPoint<T,F>(values: []const T,pred: F)->usize
= sort<T>(values: []T)->void; sortBy<T,F>(values: []T,less: F)->void; stableSort<T>(values: []T)->!void; stableSortBy<T,F>(values: []T,less: F)->!void; partialSort<T,F>(values: []T,k: usize,less: F)->void; partialSortTo<T,F>(src: []const T,dst: []T,less: F)->!usize; selectNth<T,F>(values: []T,n: usize,less: F)->!*T; isSorted<T,F>(values: []const T,less: F)->bool; isSortedUntil<T,F>(values: []const T,less: F)->usize
= min<T,F>(values: []const T,less: F)->?*const T; max<T,F>(values: []const T,less: F)->?*const T; minMax<T,F>(values: []const T,less: F)->?(*const T,*const T); clamp<T,F>(value: *const T,low: *const T,high: *const T,less: F)->*const T; lowerBound<T,K,F>(values: []const T,key: *const K,less: F)->usize; upperBound<T,K,F>(values: []const T,key: *const K,less: F)->usize; equalRange<T,K,F>(values: []const T,key: *const K,less: F)->(usize,usize); binarySearch<T,K,F>(values: []const T,key: *const K,less: F)->?usize
= heapify<T,F>(values: []T,less: F)->void; pushHeap<T,F>(values: []T,less: F)->void; popHeap<T,F>(values: []T,less: F)->void; sortHeap<T,F>(values: []T,less: F)->void; isHeap<T,F>(values: []const T,less: F)->bool; isHeapUntil<T,F>(values: []const T,less: F)->usize
= mergeTo<T,F>(a: []const T,b: []const T,dst: []T,less: F)->!usize; inplaceMerge<T,F>(values: []T,middle: usize,less: F)->!void; includes<T,F>(a: []const T,b: []const T,less: F)->bool; setUnionTo<T,F>(a: []const T,b: []const T,dst: []T,less: F)->!usize; setIntersectionTo<T,F>(a: []const T,b: []const T,dst: []T,less: F)->!usize; setDifferenceTo<T,F>(a: []const T,b: []const T,dst: []T,less: F)->!usize; setSymmetricDifferenceTo<T,F>(a: []const T,b: []const T,dst: []T,less: F)->!usize
= fold<T,A,F>(values: []const T,initial: A,f: F)->A; accumulate<T,A,F>(values: []const T,initial: A,f: F)->A; iota<T>(values: []T,start: T)->void; inclusiveScan<T,F>(values: []const T,dst: []T,f: F)->!usize; exclusiveScan<T,F>(values: []const T,dst: []T,initial: T,f: F)->!usize; transformInclusiveScan<T,U,F,G>(src: []const T,dst: []U,transform: F,combine: G)->!usize; transformExclusiveScan<T,U,F,G>(src: []const T,dst: []U,initial: U,transform: F,combine: G)->!usize; adjacentDifference<T,F>(src: []const T,dst: []T,f: F)->!usize; innerProduct<T,U,A,F,G>(a: []const T,b: []const U,initial: A,combine: F,multiply: G)->!A; gcd<T>(a: T,b: T)->T; lcm<T>(a: T,b: T)->!T
= shuffle<T,R>(values: []T,rng: *R)->void; nextPermutation<T,F>(values: []T,less: F)->bool; prevPermutation<T,F>(values: []T,less: F)->bool; isPermutation<T,F>(a: []const T,b: []const T,eq: F)->!bool
@bytes|任意二进制字节序列、零拷贝视图和可变缓冲
#class ByteSplitIter|借用字节切片的分隔游标，空分隔符拒绝
+ init(data: []const u8,separator: []const u8)->!Self; next()->?[]const u8
#class Buffer|Vec<u8> 封装、维护读写位置
+ init()->Self; withCapacity(n: usize)->!Self; len()->usize; capacity()->usize; isEmpty()->bool; asSlice()->[]const u8; asMutSlice()->[]u8; unread()->[]const u8; reserve(n: usize)->!void; write(data: []const u8)->!usize; writeByte(value: u8)->!void; read(dst: []u8)->!usize; readByte()->?u8; peek(n: usize)->[]const u8; advance(n: usize)->!void; discard(n: usize)->usize; compact()->void; clear()->void; intoVec()->coll.Vec<u8>
#class ByteReader|切片顺序读取游标
+ init(data: []const u8)->Self; read(dst: []u8)->usize; readByte()->?u8; peek(n: usize)->[]const u8; skip(n: usize)->!void; offset()->usize; remaining()->usize
#class ByteWriter|切片固定缓冲写入游标
+ init(data: []u8)->Self; write(src: []const u8)->usize; writeByte(byte: u8)->bool; written()->[]const u8; remaining()->usize; reset()->void
#record BytePattern|预处理后的字节查找模式
+ compile(pattern: []const u8)->!Self; find(haystack: []const u8)->?usize; findAll(haystack: []const u8)->iter.Iterator<usize>
= equal(a: []const u8,b: []const u8)->bool; compare(a: []const u8,b: []const u8)->cmp.Ordering; compareConstantTime(a: []const u8,b: []const u8)->bool; find(data: []const u8,needle: u8)->?usize; findLast(data: []const u8,needle: u8)->?usize; findSubslice(data: []const u8,pattern: []const u8)->?usize; contains(data: []const u8,pattern: []const u8)->bool; startsWith(data: []const u8,prefix: []const u8)->bool; endsWith(data: []const u8,suffix: []const u8)->bool; count(data: []const u8,needle: u8)->usize; split(data: []const u8,delimiter: []const u8)->!ByteSplitIter; splitOnce(data: []const u8,delimiter: []const u8)->?([]const u8,[]const u8); trim(data: []const u8,cutset: []const u8)->[]const u8; trimStart(data: []const u8,cutset: []const u8)->[]const u8; trimEnd(data: []const u8,cutset: []const u8)->[]const u8; join(parts: []const []const u8,separator: []const u8)->!coll.Vec<u8>; repeat(data: []const u8,n: usize)->!coll.Vec<u8>; replace(data: []const u8,old: []const u8,new: []const u8)->!coll.Vec<u8>; clone(data: []const u8)->!coll.Vec<u8>; reverse(data: []u8)->void; isAscii(data: []const u8)->bool
@text|不可变 string 与借视图 str；字节位置与 Unicode 位置严格区分
#class StringBuilder|可变 UTF-8 拼接与快照
+ init()->Self; withCapacity(n: usize)->!Self; byteLen()->usize; capacity()->usize; isEmpty()->bool; reserve(n: usize)->!void; append(value: str)->!void; appendChar(ch: char)->!void; appendLine(value: str)->!void; appendBytesValidated(bytes: []const u8)->!void; appendFormat<F>(format: str,args: F)->!void; insertByteBoundary(index: usize,value: str)->!void; removeByteRange(start: usize,end: usize)->!void; truncateByteBoundary(n: usize)->!void; clear()->void; asStr()->str; build()->!string; finish()->!string; intoBytes()->coll.Vec<u8>
#class Scalars|按 Unicode 标量迭代
+ init(value: str)->Self; next()->?char; remainingBytes()->usize
#class Graphemes|UAX #29 扩展字素簇视图
+ init(value: str)->Self; next()->?str; nextBack()->?str
#class Lines|兼容 LF 与 CRLF 的行视图
+ init(value: str)->Self; next()->?str
#class Split|模式分割视图
+ init(value: str,separator: str)->Self; next()->?str
#class MatchIndices|子串位置序列，位置是 UTF-8 字节偏移
+ init(value: str,pattern: str)->Self; next()->?(usize,usize)
#enum TextBoundary|Byte, Scalar, Grapheme
= equal(a: str,b: str)->bool; compare(a: str,b: str)->cmp.Ordering; byteLen(value: str)->usize; scalarCount(value: str)->usize; graphemeCount(value: str)->usize; isEmpty(value: str)->bool; chars(value: str)->Scalars; graphemes(value: str)->Graphemes; lines(value: str)->Lines; split(value: str,delimiter: str)->Split; splitOnce(value: str,delimiter: str)->?(str,str); rsplit(value: str,delimiter: str)->Split; words(value: str)->iter.Iterator<str>; find(value: str,pattern: str)->?usize; findLast(value: str,pattern: str)->?usize; matchIndices(value: str,pattern: str)->MatchIndices; contains(value: str,pattern: str)->bool; startsWith(value: str,prefix: str)->bool; endsWith(value: str,suffix: str)->bool; stripPrefix(value: str,prefix: str)->?str; stripSuffix(value: str,suffix: str)->?str; trim(value: str)->str; trimStart(value: str)->str; trimEnd(value: str)->str; trimMatches(value: str,cutset: str)->str; substringBytes(value: str,start: usize,end: usize)->!str; scalarAt(value: str,index: usize)->?char; graphemeAt(value: str,index: usize)->?str; byteOffsetOfScalar(value: str,index: usize)->?usize; fromBytes(value: []const u8)->!string; fromBytesLossy(value: []const u8)->!string; fromChars<I>(chars: I)->!string; toBytes(value: str)->!coll.Vec<u8>; concat(a: str,b: str)->!string; join<I>(parts: I,separator: str)->!string; repeat(value: str,n: usize)->!string; replace(value: str,old: str,new: str)->!string; replaceFirst(value: str,old: str,new: str)->!string; toLower(value: str)->!string; toUpper(value: str)->!string; caseFold(value: str)->!string; normalizeNFC(value: str)->!string; normalizeNFD(value: str)->!string; normalizeNFKC(value: str)->!string; normalizeNFKD(value: str)->!string; isNormalizedNFC(value: str)->bool; trimIndent(value: str)->!string; escape(value: str)->!string; unescape(value: str)->!string; compareIgnoreCase(a: str,b: str)->!cmp.Ordering
@unicode|Unicode 17.0.0 字符属性、大小写、UTF-8、正规化与 UAX #29 扩展字素簇
#enum GeneralCategory|Unicode 17.0 General_Category 完整值域
#enum Script|Unicode 17.0 Script 完整值域
#enum GraphemeBreak|Other, CR, LF, Control, Extend, ZWJ, RegionalIndicator, Prepend, SpacingMark, L, V, T, LV, LVT
#enum NormalForm|NFC, NFD, NFKC, NFKD
#record CharInfo|GeneralCategory、Script、canonical combining class 与二进制属性标志
#class Normalizer|按 Unicode 17.0 递归分解、规范组合类排序和规范组合；输入暂存至 finish
+ init(form: NormalForm)->Self; push(ch: char)->!void; flushTo(dst: []u8)->!usize; finish()->!string; reset()->void
#class GraphemeSegmenter|UAX #29 上下文状态机，支持 CRLF、Hangul、Extend、SpacingMark、Prepend、RI、Emoji ZWJ 和 Indic conjunct
+ init()->Self; feed(ch: char)->bool; hasPrevious()->bool; reset()->void
#class Graphemes|扩展字素簇视图，next 返回 UTF-8 字节借用视图
+ init(value: str)->Self; next()->?str
= version()->str; charInfo(ch: char)->CharInfo; generalCategory(ch: char)->GeneralCategory; script(ch: char)->Script; combiningClass(ch: char)->u8; graphemeBreak(ch: char)->GraphemeBreak; isAlphabetic(ch: char)->bool; isDigit(ch: char)->bool; isWhitespace(ch: char)->bool; isControl(ch: char)->bool; isUpper(ch: char)->bool; isLower(ch: char)->bool; isTitle(ch: char)->bool; isXidStart(ch: char)->bool; isXidContinue(ch: char)->bool; isEmoji(ch: char)->bool; isExtendedPictographic(ch: char)->bool; isCombiningMark(ch: char)->bool; isAscii(ch: char)->bool; isHexDigit(ch: char)->bool; toLowerSimple(ch: char)->char; toUpperSimple(ch: char)->char; toTitleSimple(ch: char)->char; caseFoldFull(ch: char)->!string; caseFold(value: str)->!string; normalize(value: str,form: NormalForm)->!string; isNormalized(value: str,form: NormalForm)->bool; graphemes(value: str)->Graphemes; graphemeCount(value: str)->usize; isGraphemeBoundary(value: str,byteOffset: usize)->bool; utf8Len(ch: char)->usize; encodeUtf8(ch: char,dst: []u8)->!usize; decodeUtf8(src: []const u8)->!(char,usize)
@encoding|文本/二进制编码转换，明确错误偏移与容量限制
#enum Base64Alphabet|Standard, UrlSafe
#record Base64Options|字母表、padding、换行和宽容策略
#record DecodeProgress|已消费和已写入字节数
#class HexEncoder|增量十六进制编码
+ init(writer: *io.Writer,upper: bool)->Self; write(data: []const u8)->!void; finish()->!void
#class HexDecoder|增量十六进制解码
+ init(reader: *io.Reader)->Self; read(dst: []u8)->!usize; finish()->!void
#class Base64Encoder|按三字节分组的流式编码
+ init(writer: *io.Writer,opts: Base64Options)->Self; write(data: []const u8)->!void; finish()->!void
#class Base64Decoder|跨块保留余量与 padding 状态
+ init(reader: *io.Reader,opts: Base64Options)->Self; read(dst: []u8)->!usize; finish()->!void
#class Utf8Decoder|跨块校验 UTF-8
+ init()->Self; push(bytes: []const u8)->!void; finish()->!void; offset()->usize; reset()->void
#class Utf16Decoder|配对代理项并按端序读取
+ init(endian: meta.Endian)->Self; feed(bytes: []const u8)->!void; finish()->!string
#class Utf16Encoder|生成 UTF-16 的字节流
+ init(writer: *io.Writer,endian: meta.Endian)->Self; write(value: str)->!void; finish()->!void
= hexEncode(data: []const u8)->!string; hexDecode(value: str)->!coll.Vec<u8>; hexEncodedLen(n: usize)->!usize; hexDecodedLen(n: usize)->!usize; base64Encode(data: []const u8,opts: Base64Options)->!string; base64Decode(value: str,opts: Base64Options)->!coll.Vec<u8>; base64EncodedLen(n: usize)->!usize; percentEncode(data: []const u8,safe: []const u8)->!string; percentDecode(value: str)->!coll.Vec<u8>; utf8Validate(data: []const u8)->!void; utf8Decode(data: []const u8)->!string; utf8Encode(value: str)->!coll.Vec<u8>; utf16Decode(data: []const u8,endian: meta.Endian)->!string; utf16Encode(value: str,endian: meta.Endian)->!coll.Vec<u8>; asciiDecode(data: []const u8)->!string; asciiEncode(value: str)->!coll.Vec<u8>; latin1Decode(data: []const u8)->!string; latin1Encode(value: str)->!coll.Vec<u8>
@binary|固定端序和可变长整数编码，不直接强转外部结构体
#enum Endian|Little, Big, Native(仅显式指定)
#class Reader|带偏移的二进制读取
+ init(data: []const u8,endian: Endian)->Self; position()->usize; remaining()->usize; seek(position: usize)->!void; readU8()->!u8; readU16()->!u16; readU32()->!u32; readU64()->!u64; readI8()->!i8; readI16()->!i16; readI32()->!i32; readI64()->!i64; readF32()->!f32; readF64()->!f64; readVarUint()->!u64; readVarInt()->!i64; readBytes(n: usize)->![]const u8; readLengthPrefixed(max: usize)->![]const u8
#class Writer|带偏移的二进制写入
+ init(data: []u8,endian: Endian)->Self; position()->usize; remaining()->usize; writeU8(value: u8)->!void; writeU16(value: u16)->!void; writeU32(value: u32)->!void; writeU64(value: u64)->!void; writeI16(value: i16)->!void; writeI32(value: i32)->!void; writeI64(value: i64)->!void; writeF32(value: f32)->!void; writeF64(value: f64)->!void; writeVarUint(value: u64)->!void; writeVarInt(value: i64)->!void; writeBytes(value: []const u8)->!void; written()->[]const u8
= readU16(data: []const u8,endian: Endian)->!u16; readU32(data: []const u8,endian: Endian)->!u32; readU64(data: []const u8,endian: Endian)->!u64; readI16(data: []const u8,endian: Endian)->!i16; readI32(data: []const u8,endian: Endian)->!i32; readI64(data: []const u8,endian: Endian)->!i64; readF32(data: []const u8,endian: Endian)->!f32; readF64(data: []const u8,endian: Endian)->!f64; writeU16(data: []u8,value: u16,endian: Endian)->!void; writeU32(data: []u8,value: u32,endian: Endian)->!void; writeU64(data: []u8,value: u64,endian: Endian)->!void; writeF32(data: []u8,value: f32,endian: Endian)->!void; writeF64(data: []u8,value: f64,endian: Endian)->!void; readVarUint(data: []const u8)->!(u64,usize); readVarInt(data: []const u8)->!(i64,usize); writeVarUint(value: u64,dst: []u8)->!usize; writeVarInt(value: i64,dst: []u8)->!usize; zigZagEncode(value: i64)->u64; zigZagDecode(value: u64)->i64; u16ToBytes(value: u16,endian: Endian)->[2]u8; u32ToBytes(value: u32,endian: Endian)->[4]u8; u64ToBytes(value: u64,endian: Endian)->[8]u8
@parse|从 str 构造值；整串消费与前缀消费分开
#record ParseOptions|进制、空白、前缀、分隔符和大小写规则
#record ParseErrorInfo|错误种类、字节偏移及输入片段
#record Parsed<T>|值与已消耗字节数
#class Cursor|无分配文本扫描游标
+ init(value: str)->Self; remaining()->str; offset()->usize; isEnd()->bool; peek()->?char; next()->?char; skipWhitespace()->void; consumeChar(ch: char)->bool; consumeString(value: str)->bool; takeWhile<F>(pred: F)->str; mark()->usize; reset(mark: usize)->void
#class Tokenizer|规则驱动的分词器
+ init(value: str)->Self; next()->?Token; peek()->?Token; position()->usize
#record Token|文本片段、类别与字节位置
= parseInt<T>(value: str,radix: u8)->!T; parseUint<T>(value: str,radix: u8)->!T; parseIntPrefix<T>(value: str,radix: u8)->!Parsed<T>; parseFloat<T>(value: str)->!T; parseFloatPrefix<T>(value: str)->!Parsed<T>; parseBool(value: str)->!bool; parseChar(value: str)->!char; parseHex<T>(value: str)->!T; parseBinary<T>(value: str)->!T; parseOctal<T>(value: str)->!T; parseWith<T,F>(value: str,parser: F)->!T; parseDuration(value: str)->!time.Duration; parseDate(value: str)->!time.Date; parseTime(value: str)->!time.TimeOfDay; parseNumberWithInfo<T>(value: str)->(?!T,?ParseErrorInfo); isInt(value: str,radix: u8)->bool; isFloat(value: str)->bool; splitFields(value: str)->iter.Iterator<str>
@fmt|可检查格式串、动态计划与 Writer 流式格式化
#enum Alignment|Left, Right, Center
#enum SignMode|Default, Always, Space
#enum FormatStyle|Display, Debug, Binary, Octal, Hex, Scientific, Fixed, General
#record FormatOptions|宽度、精度、填充、对齐和符号
#record FormatArg|借用参数与受信任类型调度信息
#class FormatPlan|解析后的文本片段与占位符
+ parse(value: str)->!Self; argCount()->usize; writeTo(writer: *io.Writer,args: []const FormatArg)->!void; format(args: []const FormatArg)->!string
#interface Display<T>|writeDisplay(value: *const T,writer: *io.Writer)->!void
#interface DebugFormat<T>|writeDebug(value: *const T,writer: *io.Writer)->!void
#class Formatter|写入流和当前格式状态
+ init(writer: *io.Writer,options: FormatOptions)->Self; write(value: str)->!void; writeChar(value: char)->!void; writeInt<T>(value: T)->!void; writeFloat<T>(value: T)->!void; writeHex(data: []const u8)->!void; pad(value: str)->!void; finish()->!void
= format<F>(pattern: str,args: F)->!string; formatWithPlan<F>(plan: *const FormatPlan,args: F)->!string; writeTo<W,F>(writer: *W,pattern: str,args: F)->!void; writeValue<T,W>(writer: *W,value: *const T)->!void; writeDebug<T,W>(writer: *W,value: *const T)->!void; formatInt<T>(value: T,radix: u8)->!string; formatFloat<T>(value: T,style: FormatStyle,precision: ?usize)->!string; formatBytes(data: []const u8,style: FormatStyle)->!string; formatDuration(value: time.Duration)->!string; formatDate(value: time.Date,pattern: str)->!string; padLeft(value: str,width: usize,fill: char)->!string; padRight(value: str,width: usize,fill: char)->!string; center(value: str,width: usize,fill: char)->!string; escapeDebug(value: str)->!string; parsePlan(pattern: str)->!FormatPlan
@regex|Thompson NFA 正则引擎；按 UTF-8 标量推进，使用有序 epsilon-closure 与显式工作预算，不执行递归回溯
#enum Op|Character, Any, Class, Split, Jump, Save, AssertStart, AssertEnd, Match
#record State|NFA 指令、主边与备选边
#record CharRange|闭区间 Unicode scalar 范围
#record CharacterClass|范围片段、预定义属性掩码和取反标记
#class Regex|不可变已编译 NFA、字符类、捕获组与资源上限
+ compile(pattern: str)->!Self; isMatch(value: str)->!bool; find(value: str)->!?Match; findAt(value: str,byteOffset: usize)->!?Match; findAll(value: str)->FindIter; captures(value: str)->!?Captures; capturesAt(value: str,byteOffset: usize)->!?Captures; capturesAll(value: str)->CaptureIter; split(value: str)->SplitIter; replace(value: str,replacement: str)->!string; replaceAll(value: str,replacement: str)->!string; captureCount()->usize; pattern()->str
#class RegexBuilder|配置 Unicode/ASCII 预定义类、大小写、点号换行、行锚点与资源上限
+ init(pattern: str)->Self; ignoreCase(value: bool)->Self; multiLine(value: bool)->Self; dotAll(value: bool)->Self; unicode(value: bool)->Self; maxStates(n: usize)->Self; maxInputBytes(n: usize)->Self; maxSteps(n: usize)->Self; compile()->!Regex
#record Match|startByte / endByte 与原文本借用视图
+ start()->usize; end()->usize; asStr()->str; lenBytes()->usize
#class Captures|按编号访问的捕获组视图，未参与匹配的组返回 null
+ len()->usize; get(index: usize)->?Match; full()->Match
#class FindIter|逐个非重叠匹配；空匹配推进一个 UTF-8 scalar
+ next()->!?Match
#class CaptureIter|逐个捕获组集合
+ next()->!?Captures
#class SplitIter|非匹配文本借用视图，支持空匹配推进
+ next()->!?str
= compile(pattern: str)->!Regex; isMatch(pattern: str,value: str)->!bool; find(pattern: str,value: str)->!?Match; findAt(pattern: str,value: str,byteOffset: usize)->!?Match; findAll(pattern: str,value: str)->!coll.Vec<Match>; captures(pattern: str,value: str)->!?Captures; escape(value: str)->!string; replace(pattern: str,value: str,replacement: str)->!string; replaceAll(pattern: str,value: str,replacement: str)->!string; split(pattern: str,value: str)->!coll.Vec<str>
@math|浮点数学、复数及特殊函数；禁止无声明 fast-math
#record Complex<T>|实部/虚部，具可检查缩放的除法
+ new(real: T,imag: T)->Self; real()->T; imag()->T; conjugate()->Self; norm()->T; abs()->T; arg()->T; add(other: Self)->Self; sub(other: Self)->Self; mul(other: Self)->Self; div(other: Self)->Self; sqrt()->Self; exp()->Self; log()->Self; pow(other: Self)->Self
#record Interval<T>|上下界、闭区间的数值区间
+ new(low: T,high: T)->!Self; contains(value: T)->bool; intersects(other: *const Self)->bool; intersection(other: *const Self)->?Self; hull(other: *const Self)->Self; width()->T; midpoint()->T
#enum FpClass|Nan, Infinite, Zero, Subnormal, Normal
#enum MathMode|Accurate, Fast（显式选择且记录目标误差）
= pi<T>()->T; tau<T>()->T; e<T>()->T; sqrt2<T>()->T; ln2<T>()->T; ln10<T>()->T; inf<T>()->T; nan<T>()->T; abs<T>(x: T)->T; signbit<T>(x: T)->bool; copysign<T>(x: T,y: T)->T; isNan<T>(x: T)->bool; isInfinite<T>(x: T)->bool; isFinite<T>(x: T)->bool; isNormal<T>(x: T)->bool; fpClassify<T>(x: T)->FpClass; nextAfter<T>(x: T,toward: T)->T; ulp<T>(x: T)->T; floor<T>(x: T)->T; ceil<T>(x: T)->T; trunc<T>(x: T)->T; round<T>(x: T)->T; roundEven<T>(x: T)->T; fract<T>(x: T)->T; modf<T>(x: T)->(T,T); fmod<T>(x: T,y: T)->T; remainder<T>(x: T,y: T)->T; remQuo<T>(x: T,y: T)->(T,i32); sqrt<T>(x: T)->T; cbrt<T>(x: T)->T; hypot<T>(x: T,y: T)->T; pow<T>(x: T,y: T)->T; powi<T>(x: T,n: i32)->T; exp<T>(x: T)->T; exp2<T>(x: T)->T; expm1<T>(x: T)->T; log<T>(x: T)->T; log2<T>(x: T)->T; log10<T>(x: T)->T; log1p<T>(x: T)->T; sin<T>(x: T)->T; cos<T>(x: T)->T; tan<T>(x: T)->T; asin<T>(x: T)->T; acos<T>(x: T)->T; atan<T>(x: T)->T; atan2<T>(y: T,x: T)->T; sinCos<T>(x: T)->(T,T); sinh<T>(x: T)->T; cosh<T>(x: T)->T; tanh<T>(x: T)->T; asinh<T>(x: T)->T; acosh<T>(x: T)->T; atanh<T>(x: T)->T; erf<T>(x: T)->T; erfc<T>(x: T)->T; gamma<T>(x: T)->T; logGamma<T>(x: T)->T; beta<T>(x: T,y: T)->T; j0<T>(x: T)->T; j1<T>(x: T)->T; y0<T>(x: T)->T; y1<T>(x: T)->T; fma<T>(a: T,b: T,c: T)->T; maxNum<T>(a: T,b: T)->T; minNum<T>(a: T,b: T)->T; totalMin<T>(a: T,b: T)->T; totalMax<T>(a: T,b: T)->T; clamp<T>(x: T,low: T,high: T)->T; lerp<T>(a: T,b: T,t: T)->T; midpoint<T>(a: T,b: T)->T; degrees<T>(radians: T)->T; radians<T>(degrees: T)->T; distance2D<T>(x1: T,y1: T,x2: T,y2: T)->T; reduceAngle<T>(x: T)->T
@random|可复现伪随机与密码学熵源分开，不自动降级
#interface Rng|nextU64()->u64; fill(dst: []u8)->void
#interface CryptoRng|fillSecure(dst: []u8)->!void
#class Pcg64|固定算法版本的可复现 PRNG
+ seed(value: u64)->Self; nextU32()->u32; nextU64()->u64; fill(dst: []u8)->void; jump(distance: u64)->void; split(stream: u64)->Self
#class Xoshiro256|256 位状态 PRNG
+ fromSeed(seed: [32]u8)->Self; nextU64()->u64; jump()->void; longJump()->void; fill(dst: []u8)->void
#class OsRng|由系统提供安全随机源
+ init()->!Self; fillSecure(dst: []u8)->!void; randomU64()->!u64
#record Seed|固定长度、公开算法版本的种子
#class UniformInt<T>|拒绝采样的无偏有界整数
+ init(low: T,highExclusive: T)->!Self; sample<R>(rng: *R)->T
#class UniformFloat<T>|构造 [0,1) 有效随机尾数
+ init(low: T,high: T)->!Self; sample<R>(rng: *R)->T
#class Bernoulli|给定成功概率
+ init(probability: f64)->!Self; sample<R>(rng: *R)->bool
#class Normal|均值和方差参数化正态分布
+ init(mean: f64,stddev: f64)->!Self; sample<R>(rng: *R)->f64
#class Exponential|指数分布
+ init(rate: f64)->!Self; sample<R>(rng: *R)->f64
#class WeightedIndex|非负权重的离散抽样
+ init(weights: []const f64)->!Self; sample<R>(rng: *R)->usize; update(weights: []const f64)->!void
= seedFromBytes(data: []const u8)->Seed; newDefault()->Pcg64; secureBytes(dst: []u8)->!void; secureToken(nBytes: usize)->!string; randomBool<R>(rng: *R)->bool; randomU32<R>(rng: *R)->u32; randomU64<R>(rng: *R)->u64; rangeInt<T,R>(rng: *R,low: T,highExclusive: T)->!T; rangeFloat<T,R>(rng: *R,low: T,high: T)->!T; shuffle<T,R>(items: []T,rng: *R)->void; sample<T,R>(items: []const T,k: usize,rng: *R)->!coll.Vec<T>; choose<T,R>(items: []const T,rng: *R)->?*const T; chooseMut<T,R>(items: []T,rng: *R)->?*T; reservoirSample<T,I,R>(items: I,k: usize,rng: *R)->!coll.Vec<T>; randomString<R>(alphabet: str,len: usize,rng: *R)->!string; fill<R>(rng: *R,dst: []u8)->void
@simd|可按目标 ISA 特化、等价标量回退；显式 lane 和对齐
#record Simd<T,comptime N: usize>|定长多通道值
+ splat(value: T)->Self; fromArray(data: [N]T)->Self; toArray()->[N]T; extract(index: usize)->T; replace(index: usize,value: T)->Self; add(other: Self)->Self; sub(other: Self)->Self; mul(other: Self)->Self; div(other: Self)->Self; min(other: Self)->Self; max(other: Self)->Self; compareEq(other: Self)->Mask<N>; compareLt(other: Self)->Mask<N>; reduceAdd()->T; reduceMin()->T; reduceMax()->T
#record Mask<comptime N: usize>|每通道谓词
+ splat(value: bool)->Self; any()->bool; all()->bool; none()->bool; countTrue()->usize; and(other: Self)->Self; or(other: Self)->Self; xor(other: Self)->Self; not()->Self
= load<T,comptime N: usize>(src: []const T)->!Simd<T,N>; loadUnaligned<T,comptime N: usize>(ptr: *const T)->Simd<T,N>; store<T,comptime N: usize>(dst: []T,value: Simd<T,N>)->!void; storeUnaligned<T,comptime N: usize>(ptr: *T,value: Simd<T,N>)->void; select<T,comptime N: usize>(mask: Mask<N>,yes: Simd<T,N>,no: Simd<T,N>)->Simd<T,N>; shuffle<T,comptime N: usize>(value: Simd<T,N>,indices: [N]usize)->Simd<T,N>; permute<T,comptime N: usize>(a: Simd<T,N>,b: Simd<T,N>,indices: [N]usize)->Simd<T,N>; gather<T,comptime N: usize>(src: []const T,indices: [N]usize)->!Simd<T,N>; scatter<T,comptime N: usize>(dst: []T,indices: [N]usize,value: Simd<T,N>)->!void; reduceAdd<T,comptime N: usize>(value: Simd<T,N>)->T; reduceMul<T,comptime N: usize>(value: Simd<T,N>)->T; reduceMin<T,comptime N: usize>(value: Simd<T,N>)->T; reduceMax<T,comptime N: usize>(value: Simd<T,N>)->T; horizontalSum<T>(src: []const T)->T
@linalg|连续或分块存储矩阵、借用多维视图和可选数值分解
#class Vector<T>|一维动态向量
+ zeros(n: usize)->!Self; fromSlice(values: []const T)->!Self; len()->usize; get(index: usize)->?*const T; getMut(index: usize)->?*T; asSlice()->[]const T; asMutSlice()->[]T; resize(n: usize,value: T)->!void; dot(other: *const Self)->!T; norm()->T; normalize()->!void; add(other: *const Self)->!Self; scale(factor: T)->void
#class Matrix<T>|行主序动态二维矩阵
+ zeros(rows: usize,cols: usize)->!Self; fromRows(rows: []const []const T)->!Self; rows()->usize; cols()->usize; shape()->(usize,usize); get(row: usize,col: usize)->?*const T; getMut(row: usize,col: usize)->?*T; row(index: usize)->?[]const T; rowMut(index: usize)->?[]T; transposeView()->MatrixView<T>; transposeCopy()->!Self; matmul(other: *const Self)->!Self; add(other: *const Self)->!Self; sub(other: *const Self)->!Self; scale(value: T)->void; clone()->!Self; asSlice()->[]const T; asMutSlice()->[]T
#record MatrixView<T>|二维借用视图，带 stride 和边界
+ new(data: []const T,rows: usize,cols: usize,rowStride: usize,colStride: usize)->!Self; rows()->usize; cols()->usize; get(row: usize,col: usize)->?*const T; transpose()->Self; submatrix(r0: usize,r1: usize,c0: usize,c1: usize)->!Self
#record TensorView<T,comptime R: usize>|R 维借用张量视图
+ new(data: []const T,shape: [R]usize,strides: [R]usize)->!Self; rank()->usize; shape()->[R]usize; strides()->[R]usize; get(index: [R]usize)->?*const T; slice(axis: usize,start: usize,end: usize)->!Self; transpose(axes: [R]usize)->!Self; reshape(shape: [R]usize)->!Self
#class Tensor<T,comptime R: usize>|拥有型多维数据
+ zeros(shape: [R]usize)->!Self; fromSlice(shape: [R]usize,values: []const T)->!Self; shape()->[R]usize; len()->usize; get(index: [R]usize)->?*const T; getMut(index: [R]usize)->?*T; view()->TensorView<T,R>; reshape(newShape: [R]usize)->!void; clone()->!Self
#record LU<T>|带主元的 LU 分解
+ solve(b: *const Vector<T>)->!Vector<T>; determinant()->!T; inverse()->!Matrix<T>
#record QR<T>|QR 分解结果
+ solveLeastSquares(b: *const Vector<T>)->!Vector<T>; q()->Matrix<T>; r()->Matrix<T>
#record SVD<T>|奇异值分解结果
+ singularValues()->Vector<T>; u()->Matrix<T>; vt()->Matrix<T>; rank(tolerance: T)->usize
= dot<T>(a: []const T,b: []const T)->!T; norm2<T>(a: []const T)->T; normInf<T>(a: []const T)->T; distance<T>(a: []const T,b: []const T)->!T; normalize<T>(a: []T)->!void; axpy<T>(a: T,x: []const T,y: []T)->!void; matmul<T>(a: *const Matrix<T>,b: *const Matrix<T>)->!Matrix<T>; transpose<T>(a: *const Matrix<T>)->!Matrix<T>; trace<T>(a: *const Matrix<T>)->!T; determinant<T>(a: *const Matrix<T>)->!T; inverse<T>(a: *const Matrix<T>)->!Matrix<T>; solve<T>(a: *const Matrix<T>,b: *const Vector<T>)->!Vector<T>; lu<T>(a: *const Matrix<T>)->!LU<T>; qr<T>(a: *const Matrix<T>)->!QR<T>; svd<T>(a: *const Matrix<T>)->!SVD<T>; eigenSymmetric<T>(a: *const Matrix<T>)->!(Vector<T>,Matrix<T>); outer<T>(a: []const T,b: []const T)->!Matrix<T>; kron<T>(a: *const Matrix<T>,b: *const Matrix<T>)->!Matrix<T>
@units|静态量纲和有理单位比例，禁止不同维度隐式相加
#record Ratio<comptime Num: i64,comptime Den: i64>|编译期约分的比例
+ numerator()->i64; denominator()->i64; invert()->Ratio<Den,Num>
#record Quantity<T,Unit>|数值和量纲/单位标签
+ new(value: T)->Self; value()->T; convert<U>(rounding: convert.Rounding)->!Quantity<T,U>; add(other: Self)->Self; sub(other: Self)->Self; scale(factor: T)->Self; compare(other: Self)->cmp.Ordering
#record Dimension|七个 SI 基本量指数
#record Unit|维度、前缀与基准比例
#enum Prefix|Nano, Micro, Milli, Centi, Deci, Kilo, Mega, Giga
= convert<T,From,To>(value: Quantity<T,From>)->!Quantity<T,To>; convertRounded<T,From,To>(value: Quantity<T,From>,rounding: convert.Rounding)->!Quantity<T,To>; ratioMul<A,B>()->Ratio; ratioDiv<A,B>()->Ratio; dimensionOf<U>()->Dimension; compatible<A,B>()->bool; formatQuantity<T,U>(value: Quantity<T,U>)->!string
@io|最小读写协议 read/write，短读短写、EOF、WouldBlock 与关闭错误区分
#interface Reader|对非空 dst 返回 0 表永久 EOF
+ read(dst: []u8)->!usize
#interface Writer|非空 src 返回 0 属 NoProgress
+ write(src: []const u8)->!usize; flush()->!void
#interface Seeker|当前、开头、末尾相对定位
+ seek(offset: i64,origin: SeekOrigin)->!u64
#interface Closer|显式关闭可报告失败，析构仅回收资源
+ close()->!void
#interface ReadWriter|组合 Reader 与 Writer
#interface ReadSeeker|组合 Reader 与 Seeker
#interface WriteSeeker|组合 Writer 与 Seeker
#enum SeekOrigin|Start, Current, End
#enum ReadStatus|Bytes, Eof, WouldBlock, Interrupted
#record IoProgress|已处理字节数和可恢复状态
#class MemoryReader|借用只读字节作为 Reader
+ init(data: []const u8)->Self; read(dst: []u8)->!usize; seek(offset: i64,origin: SeekOrigin)->!u64; remaining()->usize
#class MemoryWriter|写入可增长 Buffer
+ init()->Self; write(data: []const u8)->!usize; flush()->!void; intoBytes()->coll.Vec<u8>; asSlice()->[]const u8
#class FixedWriter|写入调用者给的 []u8
+ init(data: []u8)->Self; write(src: []const u8)->!usize; written()->[]const u8; remaining()->usize
#class LimitedReader|限制最多读取 n 字节
+ init(reader: *Reader,limit: u64)->Self; read(dst: []u8)->!usize; remaining()->u64
#class TeeReader|读取同时写出，短写处理
+ init(reader: *Reader,writer: *Writer)->Self; read(dst: []u8)->!usize
#class MultiReader|顺序拼接多个 Reader
+ init(sources: []const *Reader)->Self; read(dst: []u8)->!usize
#class MultiWriter|向全部 Writer 发送相同字节
+ init(targets: []const *Writer)->Self; write(data: []const u8)->!usize; flush()->!void
#class CountingReader|统计实际读取字节
+ init(reader: *Reader)->Self; read(dst: []u8)->!usize; count()->u64
#class CountingWriter|统计实际写入字节
+ init(writer: *Writer)->Self; write(src: []const u8)->!usize; count()->u64
#class PipeReader|内存管道读取端
+ read(dst: []u8)->!usize; close()->!void
#class PipeWriter|内存管道写入端
+ write(data: []const u8)->!usize; close()->!void
= read(reader: *Reader,dst: []u8)->!usize; readExact(reader: *Reader,dst: []u8)->!void; readExactProgress(reader: *Reader,dst: []u8)->!IoProgress; readAtLeast(reader: *Reader,dst: []u8,min: usize)->!usize; readToEnd(reader: *Reader,limit: usize)->!coll.Vec<u8>; readToString(reader: *Reader,limit: usize)->!string; write(writer: *Writer,src: []const u8)->!usize; writeAll(writer: *Writer,src: []const u8)->!void; writeAllProgress(writer: *Writer,src: []const u8)->!IoProgress; writeString(writer: *Writer,value: str)->!void; copy(reader: *Reader,writer: *Writer,scratch: []u8)->!u64; copyN(reader: *Reader,writer: *Writer,n: u64,scratch: []u8)->!u64; copyAll(reader: *Reader,writer: *Writer)->!u64; discard(reader: *Reader,n: u64,scratch: []u8)->!u64; pipe(capacity: usize)->!(PipeReader,PipeWriter); limit(reader: *Reader,n: u64)->LimitedReader; tee(reader: *Reader,writer: *Writer)->TeeReader; stdin()->!Reader; stdout()->!Writer; stderr()->!Writer; print<T>(value: T)->!void; println<T>(value: T)->!void; eprint<T>(value: T)->!void; eprintln<T>(value: T)->!void; printf<F>(pattern: str,args: F)->!void; flushStdout()->!void
@bufio|缓冲 Reader/Writer、按行和 Token 扫描，短写后保留未提交后缀
#class BufferedReader|持久字节缓冲、peek 视图会被下一次修改失效
+ init(reader: *io.Reader,capacity: usize)->!Self; read(dst: []u8)->!usize; readByte()->!?u8; unreadByte(value: u8)->!void; peek(n: usize)->![]const u8; discard(n: usize)->!usize; readUntil(delim: u8,max: usize)->!coll.Vec<u8>; readLine(max: usize)->!?string; readString(delim: u8,max: usize)->!string; bufferLen()->usize; reset(reader: *io.Reader)->void
#class BufferedWriter|可明确 flush/finish 错误
+ init(writer: *io.Writer,capacity: usize)->!Self; write(data: []const u8)->!usize; writeByte(byte: u8)->!void; writeString(value: str)->!void; available()->usize; buffered()->usize; flush()->!void; finish()->!void; reset(writer: *io.Writer)->!void
#class ReadWriter|BufferedReader 与 BufferedWriter 组合
+ init(reader: *io.Reader,writer: *io.Writer,readCapacity: usize,writeCapacity: usize)->!Self; read(dst: []u8)->!usize; write(data: []const u8)->!usize; flush()->!void
#class Scanner|可配置 token 边界和最大 token
+ init(reader: *io.Reader)->!Self; setSplit(split: SplitFn)->void; setMaxToken(n: usize)->void; next()->!?[]const u8; nextString()->!?string; position()->u64; reset(reader: *io.Reader)->void
#interface SplitFn|token(data: []const u8,atEof: bool)->!SplitResult
#record SplitResult|advance、token 和 NeedMore
#class LineReader|保留换行规范的行扫描器
+ init(reader: *io.Reader,maxBytes: usize)->!Self; next()->!?str; nextOwned()->!?string
= newReader(reader: *io.Reader,capacity: usize)->!BufferedReader; newWriter(writer: *io.Writer,capacity: usize)->!BufferedWriter; newScanner(reader: *io.Reader,maxToken: usize)->!Scanner; scanLines(data: []const u8,atEof: bool)->!SplitResult; scanWords(data: []const u8,atEof: bool)->!SplitResult; scanBytes(data: []const u8,atEof: bool)->!SplitResult; readLine(reader: *io.Reader,maxBytes: usize)->!?string
@path|区分原生路径字节/宽字符与展示 UTF-8，词法运算不访盘
#record Path|只读原生路径视图
+ isAbsolute()->bool; isRelative()->bool; hasRoot()->bool; fileName()->?str; fileStem()->?str; extension()->?str; parent()->?Path; components()->PathComponents; asNativeBytes()->[]const u8; toDisplayString()->!string; startsWith(other: Path)->bool; endsWith(other: Path)->bool; join(other: Path)->!PathBuf; normalizeLexical()->!PathBuf
#class PathBuf|拥有路径，在 Windows 可保存 UTF-16 原值
+ fromString(value: str)->!Self; fromNativeBytes(value: []const u8)->!Self; asPath()->Path; push(part: Path)->!void; pop()->bool; setFileName(value: str)->!void; setExtension(value: str)->!void; clear()->void; clone()->!Self; intoNativeBytes()->!coll.Vec<u8>
#class PathComponents|路径组件游标
+ next()->?Component; nextBack()->?Component
#enum Component|Prefix, Root, Current, Parent, Normal(str)
#enum PrefixKind|Drive, UNC, Device, Verbatim
= join(a: Path,b: Path)->!PathBuf; resolve(base: Path,child: Path)->!PathBuf; normalizeLexical(path: Path)->!PathBuf; clean(path: Path)->!PathBuf; isAbsolute(path: Path)->bool; isRelative(path: Path)->bool; base(path: Path)->?str; dir(path: Path)->?Path; extension(path: Path)->?str; stem(path: Path)->?str; split(path: Path)->(?Path,?str); splitExtension(path: Path)->(Path,?str); relative(base: Path,target: Path)->!PathBuf; commonPrefix(a: Path,b: Path)->!PathBuf; fromString(value: str)->!PathBuf; toString(path: Path)->!string; separator()->char; listSeparator()->char
@fs|宿主文件系统接口及 MemFS；路径下真实操作可失败
#interface FileSystem|open(path: path.Path,options: OpenOptions)->!File
#interface ReadDirFs|readDir(path: path.Path)->!ReadDir
#interface StatFs|stat(path: path.Path)->!Metadata
#interface WriteFs|mkdir(path: path.Path)->!void; remove(path: path.Path)->!void; rename(from: path.Path,to: path.Path)->!void
#class File|拥有 OS/板级文件句柄，兼容 Reader/Writer/Seeker
+ read(dst: []u8)->!usize; write(src: []const u8)->!usize; seek(offset: i64,origin: io.SeekOrigin)->!u64; flush()->!void; syncAll()->!void; syncData()->!void; truncate(size: u64)->!void; metadata()->!Metadata; setPermissions(value: Permissions)->!void; close()->!void
#record OpenOptions|读写、创建、覆盖、追加、权限的构建选项
+ new()->Self; read(value: bool)->Self; write(value: bool)->Self; append(value: bool)->Self; create(value: bool)->Self; createNew(value: bool)->Self; truncate(value: bool)->Self; mode(value: u32)->Self; open(path: path.Path)->!File
#record Metadata|大小、类型、权限、时间戳
+ len()->u64; isFile()->bool; isDir()->bool; isSymlink()->bool; permissions()->Permissions; modified()->?time.UtcDateTime; created()->?time.UtcDateTime
#record Permissions|可读、可写与平台扩展
+ readonly()->bool; setReadonly(value: bool)->void
#enum FileType|File, Directory, Symlink, Device, Socket, Other
#record DirEntry|名称、原生路径、可延迟读取 metadata
+ name()->str; path()->path.PathBuf; fileType()->!FileType; metadata()->!Metadata
#class ReadDir|目录项流
+ next()->!?DirEntry; close()->void
#class WalkDir|递归遍历、显式符号链接策略与深度上限
+ init(root: path.Path,options: WalkOptions)->!Self; next()->!?DirEntry; close()->void
#record WalkOptions|深度、跟随链接与错误策略
#class MemFS|内存文件系统，适配测试与嵌入式
+ init()->Self; open(path: path.Path,options: OpenOptions)->!File; mkdir(path: path.Path)->!void; remove(path: path.Path)->!void; rename(a: path.Path,b: path.Path)->!void; readDir(path: path.Path)->!ReadDir; stat(path: path.Path)->!Metadata
#class EmbeddedFS|只读嵌入资源的文件系统
+ fromManifest(manifest: []const Resource)->Self; open(path: path.Path)->!File; readDir(path: path.Path)->!ReadDir
#record Resource|编译期资源路径与字节数据
= open(path: path.Path,options: OpenOptions)->!File; create(path: path.Path)->!File; openRead(path: path.Path)->!File; openWrite(path: path.Path)->!File; readFile(path: path.Path,limit: usize)->!coll.Vec<u8>; readText(path: path.Path,limit: usize)->!string; writeFile(path: path.Path,bytes: []const u8)->!void; writeText(path: path.Path,value: str)->!void; appendFile(path: path.Path,bytes: []const u8)->!void; copy(src: path.Path,dst: path.Path)->!u64; rename(src: path.Path,dst: path.Path)->!void; remove(path: path.Path)->!void; removeAll(path: path.Path)->!void; mkdir(path: path.Path)->!void; mkdirAll(path: path.Path)->!void; readDir(path: path.Path)->!ReadDir; walkDir(path: path.Path,options: WalkOptions)->!WalkDir; stat(path: path.Path)->!Metadata; lstat(path: path.Path)->!Metadata; exists(path: path.Path)->bool; isFile(path: path.Path)->!bool; isDir(path: path.Path)->!bool; canonicalize(path: path.Path)->!path.PathBuf; readLink(path: path.Path)->!path.PathBuf; symlink(target: path.Path,link: path.Path)->!void; hardLink(target: path.Path,link: path.Path)->!void; currentDir()->!path.PathBuf; setCurrentDir(path: path.Path)->!void; tempDir()->!path.PathBuf; tempFile(prefix: str)->!File; tempDirectory(prefix: str)->!path.PathBuf
@time|Duration 与单调 Instant、墙钟 Date/Time/Zone 分开；时间等待依赖目标能力
#record Duration|带符号秒与纳秒归一化
+ zero()->Self; fromNanos(value: i64)->Self; fromMicros(value: i64)->Self; fromMillis(value: i64)->Self; fromSeconds(value: i64)->Self; fromMinutes(value: i64)->Self; fromHours(value: i64)->Self; fromDays(value: i64)->Self; seconds()->i64; nanosPart()->i32; toMillis()->!i64; toNanos()->!i64; abs()->!Self; add(other: Self)->!Self; sub(other: Self)->!Self; mul(scale: i64)->!Self; div(scale: i64)->!Self; compare(other: Self)->cmp.Ordering; isNegative()->bool; isZero()->bool
#record Instant|同一单调时钟域内的计时点
+ now()->!Self; elapsed()->!Duration; durationSince(earlier: Instant)->!Duration; checkedAdd(value: Duration)->!Self; checkedSub(value: Duration)->!Self; compare(other: Self)->cmp.Ordering
#record Date|公历年月日
+ new(year: i32,month: u8,day: u8)->!Self; year()->i32; month()->u8; day()->u8; dayOfWeek()->Weekday; dayOfYear()->u16; isLeapYear()->bool; addDays(days: i64)->!Self; addMonths(months: i32,policy: MonthPolicy)->!Self; toOrdinal()->i64; fromOrdinal(ordinal: i64)->!Self
#record TimeOfDay|日内时、分、秒、纳秒
+ new(hour: u8,minute: u8,second: u8,nano: u32)->!Self; hour()->u8; minute()->u8; second()->u8; nanosecond()->u32; secondsSinceMidnight()->u32
#record LocalDateTime|不附时区的本地日历时间
+ new(date: Date,time: TimeOfDay)->Self; date()->Date; time()->TimeOfDay; add(value: Duration)->!Self
#record UtcDateTime|UTC 绝对墙钟时间
+ fromUnixSeconds(seconds: i64,nanos: u32)->!Self; unixSeconds()->i64; nanosecond()->u32; date()->Date; time()->TimeOfDay; add(value: Duration)->!Self
#record TimeZone|固定偏移或版本化时区标识
+ utc()->Self; fixedOffset(seconds: i32)->!Self; load(name: str)->!Self; name()->str; offsetAt(utc: UtcDateTime)->!Duration
#record ZonedDateTime|时区与绝对时间共同组成
+ fromUtc(utc: UtcDateTime,zone: TimeZone)->Self; fromLocal(local: LocalDateTime,zone: TimeZone,policy: LocalTimePolicy)->!Self; utc()->UtcDateTime; local()->LocalDateTime; zone()->TimeZone; withZone(zone: TimeZone)->Self; add(value: Duration)->!Self
#enum Weekday|Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday
#enum Month|January..December
#enum MonthPolicy|Clamp, Reject, Carry
#enum LocalTimePolicy|Earlier, Later, RejectGap, ShiftForward
#enum LocalTimeResolution|Unique, Ambiguous, Gap
#interface Clock|monotonicNow()->!Instant; utcNow()->!UtcDateTime
#class Timer|到期时触发的一次性定时器
+ after(delay: Duration)->!Self; at(deadline: Instant)->!Self; wait()->!void; cancel()->void; reset(delay: Duration)->!void
#class Ticker|固定周期产生时钟事件
+ every(period: Duration)->!Self; next()->!Instant; stop()->void
#record Deadline|单调时钟截止时间及剩余间隔
+ after(delay: Duration)->!Self; at(instant: Instant)->Self; expired()->!bool; remaining()->!Duration
= now()->!UtcDateTime; monotonicNow()->!Instant; sleep(duration: Duration)->!void; sleepUntil(instant: Instant)->!void; parseDate(value: str,pattern: str)->!Date; parseTime(value: str,pattern: str)->!TimeOfDay; parseDateTime(value: str,pattern: str)->!UtcDateTime; formatDate(value: Date,pattern: str)->!string; formatTime(value: TimeOfDay,pattern: str)->!string; formatDateTime(value: ZonedDateTime,pattern: str)->!string; isLeapYear(year: i32)->bool; daysInMonth(year: i32,month: u8)->!u8; daysBetween(a: Date,b: Date)->i64; tzdbVersion()->str; loadTimeZone(name: str)->!TimeZone; toUtc(local: LocalDateTime,zone: TimeZone,policy: LocalTimePolicy)->!UtcDateTime; toLocal(utc: UtcDateTime,zone: TimeZone)->LocalDateTime
@env|环境变量、进程参数、系统路径的显式访问
#record VarValue|Missing / Present(string)，区分未设置与空字符串
#record Arg|原始平台参数与可显示文本
#class Environment|环境变量快照，不直接暴露悬垂宿主指针
+ snapshot()->!Self; get(key: str)->!?string; set(key: str,value: str)->!void; remove(key: str)->!void; contains(key: str)->bool; keys()->iter.Iterator<str>; items()->iter.Iterator<(str,str)>; clone()->!Self
= args()->!coll.Vec<string>; argsNative()->!coll.Vec<Arg>; currentExe()->!path.PathBuf; currentDir()->!path.PathBuf; tempDir()->!path.PathBuf; homeDir()->!?path.PathBuf; get(key: str)->!?string; getOr(key: str,default: str)->!string; set(key: str,value: str)->!void; remove(key: str)->!void; contains(key: str)->!bool; vars()->!coll.Vec<(string,string)>; snapshot()->!Environment; expand(value: str)->!string; os()->str; arch()->str; hostname()->!string
@process|明确子进程参数/env/cwd/stdio；句柄回收和 kill 分离
#class Command|子进程创建配置
+ new(program: path.Path)->Self; arg(value: str)->!Self; args<I>(values: I)->!Self; env(key: str,value: str)->!Self; envRemove(key: str)->Self; envClear()->Self; cwd(path: path.Path)->Self; stdin(config: Stdio)->Self; stdout(config: Stdio)->Self; stderr(config: Stdio)->Self; spawn()->!Child; status()->!ExitStatus; output(limit: usize)->!Output
#class Child|持有进程句柄，不默认强制杀进程
+ id()->u64; stdin()->?*io.Writer; stdout()->?*io.Reader; stderr()->?*io.Reader; tryWait()->!?ExitStatus; wait()->!ExitStatus; waitWithOutput(limit: usize)->!Output; kill()->!void; terminate()->!void; closeHandles()->void
#enum Stdio|Inherit, Null, Piped, File(fs.File)
#record ExitStatus|退出代码与信号信息
+ success()->bool; code()->?i32; signal()->?i32
#record Output|退出状态与 stdout/stderr 字节
+ status()->ExitStatus; stdout()->[]const u8; stderr()->[]const u8; stdoutText()->!string; stderrText()->!string
#enum Signal|Term, Kill, Int, Hup 等可用目标子集
= run(program: path.Path,args: []const str)->!ExitStatus; output(program: path.Path,args: []const str,limit: usize)->!Output; spawn(command: Command)->!Child; exit(code: i32)->never; abort()->never; pid()->u64; parentPid()->!?u64; executable()->!path.PathBuf; signal(pid: u64,signal: Signal)->!void; currentCommandLine()->!string
@net|Socket 地址值与字节序，TCP/UDP 和 DNS；后台阻塞不得卡死轻量调度器
#enum IpAddr|V4(Ipv4Addr), V6(Ipv6Addr)
#record Ipv4Addr|4 字节网络地址
+ new(a: u8,b: u8,c: u8,d: u8)->Self; octets()->[4]u8; isLoopback()->bool; isPrivate()->bool; isMulticast()->bool; toString()->!string
#record Ipv6Addr|16 字节地址与作用域
+ fromOctets(bytes: [16]u8)->Self; octets()->[16]u8; isLoopback()->bool; isMulticast()->bool; toString()->!string
#record SocketAddr|IP + 16 位端口
+ new(ip: IpAddr,port: u16)->Self; ip()->IpAddr; port()->u16; toString()->!string
#enum SocketDomain|Inet4, Inet6, Unix
#enum SocketType|Stream, Datagram
#class TcpStream|连接的字节流、可以关闭半连接
+ connect(address: SocketAddr)->!Self; connectTimeout(address: SocketAddr,timeout: time.Duration)->!Self; read(dst: []u8)->!usize; write(src: []const u8)->!usize; flush()->!void; shutdownRead()->!void; shutdownWrite()->!void; close()->!void; localAddr()->!SocketAddr; peerAddr()->!SocketAddr; setNoDelay(value: bool)->!void; setReadTimeout(value: ?time.Duration)->!void; setWriteTimeout(value: ?time.Duration)->!void; tryClone()->!Self
#class TcpListener|TCP 被动监听
+ bind(address: SocketAddr)->!Self; accept()->!(TcpStream,SocketAddr); localAddr()->!SocketAddr; setNonblocking(value: bool)->!void; close()->!void
#class UdpSocket|独立消息边界，截断状态显式返回
+ bind(address: SocketAddr)->!Self; connect(address: SocketAddr)->!void; sendTo(data: []const u8,address: SocketAddr)->!usize; recvFrom(dst: []u8)->!Datagram; send(data: []const u8)->!usize; recv(dst: []u8)->!Datagram; localAddr()->!SocketAddr; joinMulticast(ip: IpAddr)->!void; leaveMulticast(ip: IpAddr)->!void; setBroadcast(value: bool)->!void; close()->!void
#record Datagram|已收到字节、来源地址、是否截断
#class DnsResolver|可缓存的名称解析器
+ system()->!Self; lookupHost(name: str)->!coll.Vec<IpAddr>; lookupAddr(name: str,port: u16)->!coll.Vec<SocketAddr>; lookupReverse(ip: IpAddr)->!coll.Vec<string>; clearCache()->void
#class UnixStream|POSIX 域字节流
+ connect(path: path.Path)->!Self; read(dst: []u8)->!usize; write(src: []const u8)->!usize; shutdown()->!void; close()->!void
#class UnixListener|POSIX 域监听
+ bind(path: path.Path)->!Self; accept()->!UnixStream; close()->!void
#record NetTimeout|连接、读写和截止时间
= parseIp(value: str)->!IpAddr; parseIpv4(value: str)->!Ipv4Addr; parseIpv6(value: str)->!Ipv6Addr; parseSocketAddr(value: str)->!SocketAddr; resolveHost(name: str)->!coll.Vec<IpAddr>; resolveAddr(name: str,port: u16)->!coll.Vec<SocketAddr>; dialTcp(address: SocketAddr)->!TcpStream; listenTcp(address: SocketAddr)->!TcpListener; bindUdp(address: SocketAddr)->!UdpSocket; isValidPort(port: i32)->bool; htons(value: u16)->u16; ntohs(value: u16)->u16; htonl(value: u32)->u32; ntohl(value: u32)->u32
@http|HTTP/1.1 请求响应和流式 body；TLS 必须由验证过的端口提供
#enum Method|GET POST PUT DELETE PATCH HEAD OPTIONS TRACE CONNECT
#record StatusCode|三位状态码，带有效性检查
+ new(value: u16)->!Self; value()->u16; isSuccess()->bool; isRedirection()->bool; isClientError()->bool; isServerError()->bool; reasonPhrase()->?str
#class HeaderMap|大小写无关名称，多值 headers
+ init()->Self; len()->usize; append(name: str,value: str)->!void; set(name: str,value: str)->!void; get(name: str)->?str; getAll(name: str)->iter.Iterator<str>; remove(name: str)->bool; contains(name: str)->bool; items()->iter.Iterator<(str,str)>; clear()->void
#alias Url|url.Url 的公开别名；类型身份完全相同，方法仅在 url.Url 定义
#alias UrlBuilder|url.UrlBuilder 的公开别名；同一实现，不复制方法集合
#record Request|method/url/version/headers/body
+ new(method: Method,url: Url)->Self; method()->Method; url()->Url; headers()->*HeaderMap; setBody(body: Body)->void; body()->*Body; cloneHeaders()->!Self
#record Response|status/version/headers/body
+ new(status: StatusCode)->Self; status()->StatusCode; headers()->*HeaderMap; body()->*Body; setBody(body: Body)->void; close()->!void
#class Body|读取/写入消息体并管理连接复用
+ fromBytes(data: []const u8)->!Self; fromReader(reader: *io.Reader,length: ?u64)->Self; read(dst: []u8)->!usize; writeTo(writer: *io.Writer)->!u64; discard(limit: usize)->!u64; close()->!void; isComplete()->bool
#class Client|连接池、重定向、超时、TLS 与验证策略
+ init()->!Self; withTransport(transport: Transport)->Self; send(request: Request)->!Response; get(url: str)->!Response; head(url: str)->!Response; post(url: str,body: Body)->!Response; setTimeout(value: time.Duration)->void; closeIdle()->void; close()->void
#class Server|监听器和 Handler、请求配额
+ init(address: net.SocketAddr,handler: Handler)->Self; serve()->!void; shutdown(deadline: time.Deadline)->!void; localAddr()->!net.SocketAddr
#interface Handler|handle(request: Request)->!Response
#interface Transport|roundTrip(request: Request)->!Response
#class Router|按方法与路径选择 Handler
+ init()->Self; add(method: Method,pattern: str,handler: Handler)->!void; route(request: *const Request)->!?Handler
#record Cookie|name/value/attributes 的结构化表示
= parseUrl(value: str)->!Url; encodeQuery(items: []const (str,str))->!string; decodeQuery(query: str)->!coll.Vec<(string,string)>; parseRequest(reader: *io.Reader,limits: HttpLimits)->!Request; parseResponse(reader: *io.Reader,limits: HttpLimits)->!Response; writeRequest(writer: *io.Writer,request: *Request)->!void; writeResponse(writer: *io.Writer,response: *Response)->!void; statusText(code: u16)->?str; parseMethod(value: str)->!Method; get(url: str)->!Response; post(url: str,body: Body)->!Response; serve(address: net.SocketAddr,handler: Handler)->!void
#record HttpLimits|头部大小、字段数、body 大小、空闲/连接超时
@json|严格 RFC 8259 的 DOM/流式解析与显式 Codec<T>
#enum Value|Null, Bool(bool), Number(JsonNumber), String(string), Array(JsonArray), Object(JsonObject)
+ isNull()->bool; asBool()->?bool; asNumber()->?*const JsonNumber; asString()->?str; asArray()->?*const JsonArray; asObject()->?*const JsonObject; clone()->!Self
#class JsonArray|拥有值序列
+ init()->Self; len()->usize; get(index: usize)->?*const Value; getMut(index: usize)->?*Value; push(value: Value)->!void; pop()->?Value; insert(index: usize,value: Value)->!void; remove(index: usize)->?Value; iterRef()->iter.Iterator<*const Value>
#class JsonObject|拥有键值表，保留或显式选择顺序
+ init()->Self; len()->usize; get(key: str)->?*const Value; getMut(key: str)->?*Value; insert(key: string,value: Value)->!?Value; remove(key: str)->?Value; keys()->iter.Iterator<str>; items()->iter.Iterator<(str,*const Value)>; containsKey(key: str)->bool
#record JsonNumber|保留原始词形或精确整数/十进制信息
+ asI64()->!i64; asU64()->!u64; asF64()->!f64; asDecimal()->!num.Rational<i64>; text()->str
#record ParseOptions|最大深度、字节数、重复键策略
#enum DuplicateKeyPolicy|Reject, KeepFirst, KeepLast
#record EncodeOptions|缩进、转义、排序键、浮点策略
#class TokenReader|跨块逐项读取 Token
+ init(reader: *io.Reader,options: ParseOptions)->Self; next()->!?Token; skipValue()->!void; depth()->usize; position()->(usize,usize,usize)
#record Token|BeginArray, EndArray, BeginObject, EndObject, Key, Scalar，含位置
#class Decoder|token 流转 DOM 或调用 Codec
+ init(reader: *io.Reader,options: ParseOptions)->Self; decodeValue()->!Value; decode<T>(codec: *Codec<T>)->!T; finish()->!void
#class Encoder|通过 Writer 逐项输出
+ init(writer: *io.Writer,options: EncodeOptions)->Self; encodeValue(value: *const Value)->!void; encode<T>(value: *const T,codec: *Codec<T>)->!void; flush()->!void
#interface Codec<T>|encode(value: *const T,out: *Encoder)->!void; decode(reader: *Decoder)->!T
= parse(value: str)->!Value; parseBytes(value: []const u8)->!Value; parseReader(reader: *io.Reader,options: ParseOptions)->!Value; stringify(value: *const Value)->!string; stringifyPretty(value: *const Value,indent: usize)->!string; write(writer: *io.Writer,value: *const Value,opts: EncodeOptions)->!void; marshal<T>(value: *const T,codec: *Codec<T>)->!string; unmarshal<T>(value: str,codec: *Codec<T>)->!T; validate(value: str)->!void; pointerGet(value: *const Value,pointer: str)->?*const Value; pointerSet(value: *Value,pointer: str,newValue: Value)->!void; escapeString(value: str)->!string; unescapeString(value: str)->!string
@log|级别、结构化 Attr、同步/异步 Handler 与最小裸机实现
#enum Level|Trace, Debug, Info, Warn, Error, Fatal
#record Attr|key/value 的结构化属性
+ string(key: str,value: str)->Self; int(key: str,value: i64)->Self; bool(key: str,value: bool)->Self; error(key: str,value: ErrorId)->Self; group(name: str,values: []const Attr)->Self
#record Record|级别、时间、消息、元数据、属性
#interface Handler|enabled(level: Level)->bool; handle(record: *const Record)->!void; flush()->!void
#class Logger|持 Handler、minLevel 和固定字段
+ new(handler: *Handler)->Self; enabled(level: Level)->bool; log(level: Level,message: str,attrs: []const Attr)->!void; with(attrs: []const Attr)->!Self; withGroup(name: str)->!Self; setLevel(level: Level)->void; flush()->!void
#class TextHandler|人类可读行式格式
+ new(writer: *io.Writer)->Self; handle(record: *const Record)->!void; flush()->!void
#class JsonHandler|JSON Lines 结构化格式
+ new(writer: *io.Writer)->Self; handle(record: *const Record)->!void; flush()->!void
#class MultiHandler|按顺序分发至多个 Handler
+ new(handlers: []const *Handler)->Self; handle(record: *const Record)->!void; flush()->!void
#class BufferHandler|内存收集供测试检查
+ init()->Self; handle(record: *const Record)->!void; records()->[]const Record; clear()->void
#class AsyncHandler|异步拥有队列，属性需要复制并满足 Sendable
+ new(inner: *Handler,capacity: usize)->!Self; handle(record: *const Record)->!void; flush()->!void; close()->!void; dropped()->u64
= newLogger(handler: *Handler)->Logger; trace(logger: *Logger,message: str,attrs: []const Attr)->!void; debug(logger: *Logger,message: str,attrs: []const Attr)->!void; info(logger: *Logger,message: str,attrs: []const Attr)->!void; warn(logger: *Logger,message: str,attrs: []const Attr)->!void; error(logger: *Logger,message: str,attrs: []const Attr)->!void; fatal(logger: *Logger,message: str,attrs: []const Attr)->!void; parseLevel(value: str)->!Level; levelText(value: Level)->str; discardHandler()->Handler
@atomic|目标可用宽度上的无锁/原子操作及内存顺序，默认 seq_cst
#enum Order|Relaxed, Acquire, Release, AcqRel, SeqCst
#record CompareExchange<T>|Old(T), Exchanged(bool) 的结构化结果
#class Atomic<T>|单个原子位置，T 限目标允许的标量/指针
+ new(value: T)->Self; load(order: Order)->T; store(value: T,order: Order)->void; swap(value: T,order: Order)->T; compareExchange(expected: T,desired: T,success: Order,failure: Order)->CompareExchange<T>; compareExchangeWeak(expected: T,desired: T,success: Order,failure: Order)->CompareExchange<T>; fetchAdd(value: T,order: Order)->T; fetchSub(value: T,order: Order)->T; fetchAnd(value: T,order: Order)->T; fetchOr(value: T,order: Order)->T; fetchXor(value: T,order: Order)->T; fetchMax(value: T,order: Order)->T; fetchMin(value: T,order: Order)->T; intoInner()->T
#class AtomicFlag|一次置位和清除
+ init(value: bool)->Self; test(order: Order)->bool; testAndSet(order: Order)->bool; clear(order: Order)->void
#class AtomicPtr<T>|非拥有指针的原子槽
+ new(value: ?*T)->Self; load(order: Order)->?*T; store(value: ?*T,order: Order)->void; swap(value: ?*T,order: Order)->?*T; compareExchange(expected: ?*T,desired: ?*T,success: Order,failure: Order)->CompareExchange<?*T>
= fence(order: Order)->void; compilerFence(order: Order)->void; isLockFree<T>()->bool; isAlwaysLockFree<T>()->bool; supportedWidths()->[]const usize; atomicLoad<T>(ptr: *const Atomic<T>,order: Order)->T; atomicStore<T>(ptr: *Atomic<T>,value: T,order: Order)->void
@sync|共享内存同步、RAII Guard 和条件等待；普通借用不可逃离守卫
#class Mutex<T>|不允许递归加锁，Guard 释放解锁
+ new(value: T)->Self; lock()->!MutexGuard<T>; tryLock()->?MutexGuard<T>; intoInner()->T; isPoisoned()->bool
#class MutexGuard<T>|持独占写权限，线程亲和要求由目标决定
+ get()->*T; unlock()->void; map<U,F>(f: F)->MappedMutexGuard<U>
#class MappedMutexGuard<T>|从守卫投影的独占子视图，拥有原锁的解锁责任
+ get()->*T; unlock()->void
#class RwLock<T>|共享读或独占写
+ new(value: T)->Self; read()->!RwReadGuard<T>; write()->!RwWriteGuard<T>; tryRead()->?RwReadGuard<T>; tryWrite()->?RwWriteGuard<T>; intoInner()->T
#class RwReadGuard<T>|只读 RAII 守卫
+ get()->*const T; unlock()->void
#class RwWriteGuard<T>|可写 RAII 守卫
+ get()->*T; downgrade()->RwReadGuard<T>; unlock()->void
#class CondVar|与 Mutex 配套，原子释放锁并等待
+ new()->Self; wait<T>(guard: MutexGuard<T>)->!MutexGuard<T>; waitTimeout<T>(guard: MutexGuard<T>,timeout: time.Duration)->!(MutexGuard<T>,bool); notifyOne()->void; notifyAll()->void
#class Once|无载荷仅一次执行
+ new()->Self; callOnce<F>(f: F)->!void; isDone()->bool
#class OnceLock<T>|携带值的跨线程一次初始化
+ new()->Self; get()->?*const T; set(value: T)->?T; getOrInit<F>(f: F)->*const T; getOrTryInit<F>(f: F)->!*const T
#class Semaphore|可计数许可、支持协作等待
+ new(permits: usize)->Self; acquire()->!Permit; tryAcquire()->?Permit; acquireMany(n: usize)->!Permit; available()->usize; close()->void
#class Permit|RAII 自动归还许可
+ release()->void; forget()->void
#class Barrier|固定参与者协同步骤
+ new(participants: usize)->!Self; wait()->!BarrierWaitResult; reset()->!void
#record BarrierWaitResult|是否为一轮 leader
#class WaitGroup|显式计数任务完成，不管理任务寿命
+ new()->Self; add(n: usize)->!void; done()->void; wait()->!void; count()->usize
#class Event|手动/自动重置事件
+ new(initial: bool)->Self; wait()->!void; waitTimeout(timeout: time.Duration)->!bool; set()->void; reset()->void
#class Latch|一次性计数归零事件
+ new(count: usize)->Self; countDown()->void; wait()->!void
#class Pool<T>|非唯一资源持有的复用缓存
+ new(factory: func() !T)->Self; get()->!T; put(value: T)->void; clear()->void
#class ConcurrentMap<K,V>|分片锁版本的并发哈希映射
+ init(shards: usize)->!Self; getClone(key: *const K)->!?V; insert(key: K,value: V)->!?V; remove(key: *const K)->!?V; contains(key: *const K)->!bool; lenApprox()->usize; snapshot()->!coll.HashMap<K,V>; clear()->!void
#class ConcurrentQueue<T>|多生产者多消费者队列
+ init(capacity: usize)->!Self; push(value: T)->!void; tryPush(value: T)->coll.PushResult<T>; pop()->!?T; tryPop()->?T; close()->void
= withLock<T,F>(mutex: *Mutex<T>,f: F)->!void; withRead<T,F>(lock: *RwLock<T>,f: F)->!void; withWrite<T,F>(lock: *RwLock<T>,f: F)->!void; once<F>(call: F)->!void; waitUntil<T,F>(cond: *CondVar,guard: MutexGuard<T>,pred: F)->!MutexGuard<T>; yieldNow()->void
@thread|原生 OS 线程、TLS 和 CPU 亲和性；与轻量 task 区分
#record ThreadId|当前进程内线程标识
#class Thread<T>|拥有 OS 线程句柄，join 一次领取结果
+ id()->ThreadId; join()->!T; isFinished()->bool; detach()->void; requestStop()->void
#class ThreadBuilder|线程名称、栈大小、亲和性
+ new()->Self; name(value: str)->Self; stackSize(bytes: usize)->Self; affinity(cpus: []const usize)->Self; spawn<T,F>(f: F)->!Thread<T>
#class ThreadLocal<T>|逐线程独立值和析构注册
+ new<F>(factory: F)->Self; get()->!*T; tryGet()->?*T; set(value: T)->!void; clear()->void
#class ScopedThreadGroup|作用域内线程回收，借用必须保持寿命
+ init()->Self; spawn<T,F>(f: F)->!Thread<T>; joinAll()->!void; cancelAll()->void
#record CpuSet|逻辑核心集合
+ init()->Self; insert(cpu: usize)->!void; remove(cpu: usize)->bool; contains(cpu: usize)->bool; iter()->iter.Iterator<usize>
= spawn<T,F>(f: F)->!Thread<T>; currentId()->ThreadId; currentName()->?str; setCurrentName(value: str)->!void; cpuCount()->usize; availableParallelism()->usize; yieldNow()->void; sleep(duration: time.Duration)->!void; park()->!void; unpark(id: ThreadId)->!void; setAffinity(cpus: CpuSet)->!void; getAffinity()->!CpuSet; isMainThread()->bool
@task|结构化并行 + CSP，无强制 async/await；协作取消和可挂起调用
#class TaskGroup|作用域化管理子任务，析构取消并回收
+ init()->Self; spawn<T,F>(f: F)->!Task<T>; spawnWithContext<T,F>(context: TaskContext,f: F)->!Task<T>; cancel()->void; isCancelled()->bool; joinAll()->!void; collectErrors()->!errors.Report; activeCount()->usize; close()->!void
#class Task<T>|不可复制、一次性 join，组管理底层寿命
+ join()->!T; tryJoin()->!?T; cancel()->void; isFinished()->bool; id()->TaskId
#record TaskId|任务标识
#class TaskContext|取消令牌、Deadline、任务局部元数据
+ root()->Self; child()->Self; withDeadline(deadline: time.Deadline)->Self; withTimeout(timeout: time.Duration)->!Self; cancelToken()->CancelToken; deadline()->?time.Deadline; checkCancelled()->!void; isCancelled()->bool
#class CancelToken|支持父子取消传播的共享令牌
+ new()->Self; child()->Self; cancel()->void; isCancelled()->bool; cancelled()->!void; reason()->?ErrorId
#alias Deadline|time.Deadline 的公开别名，生命周期和时钟语义统一
#interface Executor|spawn(f: F)->!TaskId; shutdown()->!void
#class Scheduler|可配置 M:N 工作窃取执行器
+ new(workers: usize)->!Self; spawn<T,F>(f: F)->!Task<T>; activeCount()->usize; shutdown()->!void; join()->!void
#class JoinSet<T>|动态数量的任务和完成队列
+ init(group: *TaskGroup)->Self; spawn<F>(f: F)->!void; joinNext()->!?T; cancelAll()->void; len()->usize
#class TaskLocal<T>|任务作用域内的可访问上下文值
+ new<F>(factory: F)->Self; get()->!*T; set(value: T)->!void; clear()->void
#class BlockingExecutor|为无法挂起的同步调用隔离系统线程
+ init(maxThreads: usize)->!Self; spawnBlocking<T,F>(f: F)->!Task<T>; shutdown()->!void
= spawn<T,F>(group: *TaskGroup,f: F)->!Task<T>; scope<F>(f: F)->!void; currentId()->?TaskId; currentContext()->?TaskContext; checkCancelled()->!void; yieldNow()->!void; sleep(duration: time.Duration)->!void; sleepUntil(instant: time.Instant)->!void; withTimeout<T,F>(timeout: time.Duration,f: F)->!T; withDeadline<T,F>(deadline: time.Deadline,f: F)->!T; runBlocking<T,F>(f: F)->!T; spawnBlocking<T,F>(executor: *BlockingExecutor,f: F)->!Task<T>; race<T>(tasks: coll.Vec<Task<T>>)->!T; joinAll<T>(tasks: []Task<T>)->!coll.Vec<T>; selectAll<T>(tasks: []Task<T>)->!(usize,T)
@channel|MPSC 有界缺省，复制 Sender、独占 Receiver，接收关闭为成功 null
#record Channel<T>|Sender + Receiver 的一次性构造入口
+ bounded(capacity: usize)->!(Sender<T>,Receiver<T>); unbounded()->!(Sender<T>,Receiver<T>)
#class Sender<T>|可复制/可移动的发送端；close 令通道拒绝新消息
+ clone()->Self; send(value: T)->!void; sendPreserving(value: T)->SendResult<T>; trySend(value: T)->TrySendResult<T>; sendTimeout(value: T,timeout: time.Duration)->SendResult<T>; close()->void; isClosed()->bool; capacity()->?usize; lenApprox()->usize
#class Receiver<T>|不可复制的单消费者端
+ recv()->!?T; tryRecv()->TryRecv<T>; recvTimeout(timeout: time.Duration)->!?T; close()->void; isClosed()->bool; lenApprox()->usize; capacity()->?usize; drain()->iter.Iterator<T>
#enum SendResult<T>|Sent, Closed(T), Cancelled(T), OutOfMemory(T)
#enum TrySendResult<T>|Sent, Full(T), Closed(T), OutOfMemory(T)
#enum TryRecv<T>|Value(T), Empty, Closed
#class Broadcast<T>|可选一发多收；不同于默认 MPSC
+ bounded(capacity: usize)->!(BroadcastSender<T>,BroadcastReceiver<T>); subscribe()->!BroadcastReceiver<T>; close()->void
#class BroadcastSender<T>|广播生产端
+ send(value: T)->!usize; trySend(value: T)->!usize; close()->void
#class BroadcastReceiver<T>|广播消费端，落后可以返回 Lagged
+ recv()->!?T; tryRecv()->!TryRecv<T>; close()->void
#class Watch<T>|持最新值的状态广播
+ new(initial: T)->!Self; sender()->WatchSender<T>; receiver()->WatchReceiver<T>
#class WatchSender<T>|更新最新值
+ send(value: T)->!void; update<F>(f: F)->!void; close()->void
#class WatchReceiver<T>|按版本等待更新
+ borrow()->!*const T; changed()->!void; hasChanged()->bool; close()->void
= bounded<T>(capacity: usize)->!(Sender<T>,Receiver<T>); unbounded<T>()->!(Sender<T>,Receiver<T>); send<T>(tx: *Sender<T>,value: T)->!void; recv<T>(rx: *Receiver<T>)->!?T; trySend<T>(tx: *Sender<T>,value: T)->TrySendResult<T>; tryRecv<T>(rx: *Receiver<T>)->TryRecv<T>; after(delay: time.Duration)->!Receiver<time.Instant>; tick(interval: time.Duration)->!Receiver<time.Instant>; selectTimeout(timeout: time.Duration)->!void
@parallel|任务组和通道之上的数据并行，输入保活且切片互不重叠
#enum Schedule|Static, Dynamic, Guided
#record ParallelOptions|最小块大小、最大任务数、调度策略和确定性归并
#class ParallelIterator<I,T>|拥有上游/分块策略的并行流水
+ init(source: I,options: ParallelOptions)->Self; map<U,F>(f: F)->ParallelIterator<I,U>; filter<F>(pred: F)->ParallelIterator<I,T>; collectVec()->!coll.Vec<T>; forEach<F>(f: F)->!void; reduce<F>(identity: T,f: F)->!T
= parallelFor<F>(start: usize,end: usize,options: ParallelOptions,f: F)->!void; parallelForEach<T,F>(input: []const T,options: ParallelOptions,f: F)->!void; parallelForEachMut<T,F>(input: []T,options: ParallelOptions,f: F)->!void; parallelMap<T,U,F>(input: []const T,options: ParallelOptions,f: F)->!coll.Vec<U>; parallelFilter<T,F>(input: []const T,options: ParallelOptions,pred: F)->!coll.Vec<T>; parallelReduce<T,F>(input: []const T,identity: T,options: ParallelOptions,f: F)->!T; parallelFold<T,A,F,G>(input: []const T,identity: A,options: ParallelOptions,fold: F,combine: G)->!A; parallelScan<T,F>(input: []const T,options: ParallelOptions,f: F)->!coll.Vec<T>; parallelSort<T,F>(input: []T,options: ParallelOptions,less: F)->!void; parallelStableSort<T,F>(input: []T,options: ParallelOptions,less: F)->!void; parallelFind<T,F>(input: []const T,options: ParallelOptions,pred: F)->!?usize; parallelAny<T,F>(input: []const T,options: ParallelOptions,pred: F)->!bool; parallelAll<T,F>(input: []const T,options: ParallelOptions,pred: F)->!bool; chunkCount(length: usize,options: ParallelOptions)->usize
@errors|错误身份、上下文、多错误报告，与内建 !T 独立
#record ErrorId|包名 + error 集合 + 成员组成稳定身份
+ package()->str; name()->str; fullName()->str; equals(other: Self)->bool
#record Frame|上下文消息、位置、可选关键值
+ message()->str; location()->?SourceLocation; attrs()->[]const log.Attr
#class Report|可扩展上下文链和子错误
+ fromError(err: ErrorId)->Self; new(message: str)->!Self; addContext(message: str)->!void; withContext(message: str)->!Self; addCause(report: Self)->!void; join(other: Self)->!Self; rootError()->?ErrorId; causes()->iter.Iterator<*const Report>; matches(err: ErrorId)->bool; message()->str; format(writer: *io.Writer)->!void; clone()->!Self
#class ErrorSink|从任务、算法或 I/O 收集额外错误报告
+ init()->Self; push(report: Report)->!void; len()->usize; isEmpty()->bool; drain()->iter.Iterator<Report>; toReport()->!Report; clear()->void
#record SourceLocation|文件名、起止行列、函数标识
#enum ErrorSeverity|Info, Warning, Error, Fatal
= matches(report: *const Report,error: ErrorId)->bool; is(report: *const Report,error: ErrorId)->bool; join(reports: []const Report)->!Report; withContext(err: ErrorId,message: str)->!Report; wrap(err: ErrorId,message: str)->!Report; root(report: *const Report)->?ErrorId; causes(report: *const Report)->iter.Iterator<*const Report>; format(report: *const Report)->!string; reportTo(writer: *io.Writer,report: *const Report)->!void; assertNoError<T>(value: !T)->T
@debug|无分配 trap/panic 通道、断言、可选栈回溯和诊断钩子
#record SourceLocation|文件、行、列、函数
#record StackFrame|函数地址、模块、源码位置
#class Backtrace|可选栈回溯信息
+ capture()->!Self; frames()->[]const StackFrame; format(writer: *io.Writer)->!void
#interface PanicHook|onPanic(message: str,location: SourceLocation)->void
= assert(condition: bool)->void; assertMessage(condition: bool,message: str)->void; debugAssert(condition: bool)->void; debugAssertMessage(condition: bool,message: str)->void; panic(message: str)->never; panicWithLocation(message: str,location: SourceLocation)->never; trap()->never; unreachable()->never; unreachableUnchecked()->never; breakpoint()->void; blackBox<T>(value: T)->T; sourceLocation()->SourceLocation; typeName<T>()->str; dump<T>(value: *const T)->!void; printBacktrace()->!void; setPanicHook(hook: *PanicHook)->void; getPanicHook()->?*PanicHook; isDebugBuild()->bool
@testing|显式清单测试/基准/模糊测试，不把执行器链接到普通程序
#class T|单次测试上下文和清理栈
+ name()->str; fail(message: str)->void; failNow(message: str)->never; log(message: str)->void; skip(reason: str)->never; cleanup<F>(f: F)->void; run(name: str,test: func(*T) void)->bool; tempDir()->!path.PathBuf; deadline()->?time.Instant; failed()->bool
#class B|基准测试上下文
+ name()->str; N()->usize; resetTimer()->void; startTimer()->void; stopTimer()->void; reportMetric(value: f64,name: str)->void; reportAllocs()->void; setBytes(n: usize)->void; run(name: str,benchmark: func(*B) void)->void
#class F|模糊测试种子、输入与复现
+ addBytes(value: []const u8)->!void; addInt(value: i64)->void; fuzz<F>(f: F)->void; seed()->u64; failCase(input: []const u8)->void
#record TestCase|名称、函数、标签与超时
#record BenchmarkResult|耗时、分配数和指标
#record TestResult|成功、失败、跳过、输出及日志
#class Runner|执行测试、过滤和汇总
+ init()->Self; add(test: TestCase)->!void; filter(pattern: str)->void; run()->!coll.Vec<TestResult>; runBenchmarks()->!coll.Vec<BenchmarkResult>; setParallelism(n: usize)->void
#class Property<T>|生成器+收缩器的性质测试入口
+ new(generator: Generator<T>,shrinker: Shrinker<T>)->Self; check<F>(iterations: usize,pred: F)->!void; seed(value: u64)->void
#interface Generator<T>|next(rng: *random.Rng)->!T
#interface Shrinker<T>|shrink(value: *const T)->iter.Iterator<T>
#class Snapshot|基线文本/字节快照
+ new(name: str)->Self; assertText(value: str)->!void; assertBytes(value: []const u8)->!void
= test(name: str,callback: func(*T) void)->TestCase; benchmark(name: str,callback: func(*B) void)->TestCase; assert(condition: bool)->void; assertEq<T>(actual: *const T,expected: *const T)->void; assertNe<T>(actual: *const T,unexpected: *const T)->void; assertApprox<T>(actual: T,expected: T,tolerance: T)->void; assertError<U>(value: !U,error: errors.ErrorId)->void; assertNull<T>(value: ?T)->void; assertSome<T>(value: ?T)->T; assertPanics<F>(f: F)->void; fail(message: str)->never; skip(message: str)->never; runAll()->!i32; seed(value: u64)->void; captureOutput<F>(f: F)->!string; tempDir()->!path.PathBuf; tempFile()->!fs.File
@serde|数据格式无关的显式编码/解码接口；无反射时仍可手写 Codec
#interface Encode<T>|encode(value: *const T,writer: *Serializer)->!void
#interface Decode<T>|decode(reader: *Deserializer)->!T
#interface Serializer|beginSeq(len: ?usize)->!void; element<T>(value: *const T)->!void; endSeq()->!void; beginMap(len: ?usize)->!void; key<T>(value: *const T)->!void; value<T>(value: *const T)->!void; endMap()->!void
#interface Deserializer|beginSeq()->!?usize; nextElement<T>()->!?T; endSeq()->!void; beginMap()->!?usize; nextKey<T>()->!?T; nextValue<T>()->!T; endMap()->!void
#record Codec<T>|保存指定格式的 encode/decode 函数策略
#record DecodeLimits|深度、元素数、字节数
= encode<T,S>(value: *const T,serializer: *S)->!void; decode<T,D>(deserializer: *D)->!T; encodeToBytes<T,S>(value: *const T,serializer: S)->!coll.Vec<u8>; decodeFromBytes<T,D>(bytes: []const u8,deserializer: D)->!T
@csv|CSV 行式读写、分隔符和引号策略
#record CsvOptions|分隔符、quote、换行、表头与字段上限
#class CsvReader|流式记录读取
+ init(reader: *io.Reader,options: CsvOptions)->Self; readRecord()->!?coll.Vec<string>; readFields(dst: *coll.Vec<string>)->!bool; headers()->!?coll.Vec<string>; position()->(usize,usize); close()->void
#class CsvWriter|保留列边界、按需转义
+ init(writer: *io.Writer,options: CsvOptions)->Self; writeRecord(fields: []const str)->!void; writeAll(rows: []const []const str)->!void; flush()->!void
= parseRecord(value: str,options: CsvOptions)->!coll.Vec<string>; parse(value: str,options: CsvOptions)->!coll.Vec<coll.Vec<string>>; stringify(records: []const []const str,options: CsvOptions)->!string; escapeField(value: str,options: CsvOptions)->!string
@toml|配置文件读写，保持 TOML 类型语义和重复键检查
#enum Value|String, Integer, Float, Bool, DateTime, Array, Table
#class Document|TOML 文档 AST 与词法位置信息
+ parse(value: str)->!Self; get(path: str)->?*const Value; set(path: str,value: Value)->!void; remove(path: str)->bool; stringify()->!string
#record TomlOptions|最大深度、表大小、日期解析约束
= parse(value: str)->!Document; parseReader(reader: *io.Reader,limit: usize)->!Document; stringify(document: *const Document)->!string; decode<T>(value: str,codec: *serde.Codec<T>)->!T; encode<T>(value: *const T,codec: *serde.Codec<T>)->!string
@url|URI/URL、查询参数与百分号编码；HTTP 可复用
#record Url|URI/URL 的唯一拥有者类型；http.Url 仅为别名
+ parse(value: str)->!Self; scheme()->str; host()->?str; port()->?u16; userInfo()->?str; path()->str; query()->?str; fragment()->?str; resolve(reference: str)->!Self; normalize()->!Self; toString()->!string
#class UrlBuilder|URL 可验证构造器，唯一权威实现（http.UrlBuilder 同类型别名）
+ init()->Self; scheme(value: str)->Self; host(value: str)->Self; port(value: u16)->Self; path(value: str)->Self; query(value: str)->Self; fragment(value: str)->Self; build()->!Url
#class QueryParams|保持键重复与插入顺序
+ init()->Self; parse(value: str)->!Self; append(key: str,value: str)->!void; set(key: str,value: str)->!void; get(key: str)->?str; getAll(key: str)->iter.Iterator<str>; remove(key: str)->usize; encode()->!string
#record UrlSearch|Scheme, Authority, Host, Path 等组件
= parse(value: str)->!Url; resolve(base: Url,relative: str)->!Url; queryEscape(value: str)->!string; queryUnescape(value: str)->!string; pathEscape(value: str)->!string; pathUnescape(value: str)->!string; encodeQuery(items: []const (str,str))->!string; parseQuery(value: str)->!QueryParams; isAbsolute(value: str)->bool
@compress|流式压缩端口；目标可选择纯实现或系统后端
#enum Format|Gzip, Zlib, Deflate, RawDeflate
#enum Level|NoCompression, Fast, Default, Best
#class Compressor|流式编码器
+ init(writer: *io.Writer,format: Format,level: Level)->!Self; write(data: []const u8)->!usize; flush()->!void; finish()->!void; reset(writer: *io.Writer)->!void
#class Decompressor|流式解码器，输出/比率上限
+ init(reader: *io.Reader,format: Format,limit: usize)->!Self; read(dst: []u8)->!usize; close()->!void
= compress(data: []const u8,format: Format,level: Level)->!coll.Vec<u8>; decompress(data: []const u8,format: Format,limit: usize)->!coll.Vec<u8>; gzip(data: []const u8)->!coll.Vec<u8>; gunzip(data: []const u8,limit: usize)->!coll.Vec<u8>; checksumAdler32(data: []const u8)->u32; checksumCrc32(data: []const u8)->u32
@crypto|密码学原语必须采用独立验证的实现；随机来自 OS 熵源
#class Sha256|SHA-256 增量摘要
+ init()->Self; update(data: []const u8)->void; finish()->[32]u8; reset()->void
#class Sha512|SHA-512 增量摘要
+ init()->Self; update(data: []const u8)->void; finish()->[64]u8; reset()->void
#class HmacSha256|带密钥消息认证码
+ init(key: []const u8)->Self; update(data: []const u8)->void; finish()->[32]u8; reset()->void
#class AesGcm|经验证的认证加密接口
+ new(key: []const u8)->!Self; seal(nonce: []const u8,plaintext: []const u8,aad: []const u8)->!coll.Vec<u8>; open(nonce: []const u8,ciphertext: []const u8,aad: []const u8)->!coll.Vec<u8>
#record SecretBytes|尽可能清理的敏感拥有字节，禁隐式复制
+ new(value: []const u8)->!Self; asSlice()->[]const u8; clear()->void; intoBytes()->coll.Vec<u8>
= sha256(data: []const u8)->[32]u8; sha512(data: []const u8)->[64]u8; hmacSha256(key: []const u8,data: []const u8)->[32]u8; hkdfSha256(ikm: []const u8,salt: []const u8,info: []const u8,out: []u8)->!void; pbkdf2Sha256(password: []const u8,salt: []const u8,rounds: u32,out: []u8)->!void; secureRandom(dst: []u8)->!void; constantTimeEq(a: []const u8,b: []const u8)->bool; verifyTag(expected: []const u8,actual: []const u8)->bool
@tls|证书校验的安全流；不允许默默关闭验证
#record TlsConfig|最低协议版本、信任根、SNI 与客户端身份
#record Certificate|证书编码及经验证的元数据
#class TlsStream|建立安全连接后的 Reader/Writer
+ connect(stream: net.TcpStream,serverName: str,config: TlsConfig)->!Self; read(dst: []u8)->!usize; write(src: []const u8)->!usize; flush()->!void; peerCertificates()->[]const Certificate; negotiatedProtocol()->str; close()->!void
#class TlsAcceptor|服务器端握手
+ new(config: TlsConfig)->!Self; accept(stream: net.TcpStream)->!TlsStream
= connect(address: net.SocketAddr,serverName: str,config: TlsConfig)->!TlsStream; defaultClientConfig()->!TlsConfig; systemRoots()->!coll.Vec<Certificate>; verifyCertificate(chain: []const Certificate,hostname: str,when: time.UtcDateTime)->!void
@context|跨调用的取消、deadline、键值上下文；与 task.TaskContext 统一适配
#class Context|父级上下文、取消令牌、deadline、请求元数据
+ background()->Self; child()->Self; withCancel()->(Self,CancelHandle); withTimeout(duration: time.Duration)->!(Self,CancelHandle); withDeadline(deadline: time.Deadline)->(Self,CancelHandle); cancelled()->!void; checkCancelled()->!void; deadline()->?time.Deadline; get<T>(key: ContextKey<T>)->?*const T; withValue<T>(key: ContextKey<T>,value: T)->!Self
#class CancelHandle|显式取消能力
+ cancel()->void; isCancelled()->bool
#record ContextKey<T>|类型化键，不依赖字符串拼接
= background()->Context; withCancel(ctx: Context)->(Context,CancelHandle); withTimeout(ctx: Context,duration: time.Duration)->!(Context,CancelHandle); withDeadline(ctx: Context,deadline: time.Deadline)->(Context,CancelHandle); checkCancelled(ctx: *const Context)->!void
@asyncstream|建立在可挂起任务与通道上的流，错误/结束必须分层
#interface Stream<T>|next()->!?T 或 next()->?T，按是否可失败明确选择
#class StreamMap<I,T,U,F>|可挂起的逐项映射
+ init(source: I,f: F)->Self; next()->!?U
#class StreamFilter<I,T,F>|可挂起的过滤流
+ init(source: I,pred: F)->Self; next()->!?T
#class StreamMerge<A,B,T>|两个数据流公平合并
+ init(a: A,b: B)->Self; next()->!?T
#class StreamBuffer<I,T>|固定前取缓冲，尊重背压
+ init(source: I,capacity: usize)->!Self; next()->!?T; close()->!void
= fromChannel<T>(receiver: channel.Receiver<T>)->Stream<T>; toChannel<T,S>(source: S,sender: channel.Sender<T>)->!void; map<T,U,S,F>(source: S,f: F)->StreamMap<S,T,U,F>; filter<T,S,F>(source: S,f: F)->StreamFilter<S,T,F>; merge<T,A,B>(a: A,b: B)->StreamMerge<A,B,T>; take<T,S>(source: S,n: usize)->Stream<T>; timeout<T,S>(source: S,duration: time.Duration)->Stream<T>; collect<T,S>(source: S)->!coll.Vec<T>; forEach<T,S,F>(source: S,f: F)->!void
@persistent|可选不可变持久数据结构，结构共享与引用计数正确
#class PersistentVec<T>|树状持久向量
+ empty()->Self; len()->usize; get(index: usize)->?*const T; set(index: usize,value: T)->!Self; push(value: T)->!Self; pop()->!?Self; iterRef()->iter.Iterator<*const T>; transient()->!TransientVec<T>
#class PersistentMap<K,V>|结构共享的不可变哈希映射
+ empty()->Self; len()->usize; get(key: *const K)->?*const V; insert(key: K,value: V)->!Self; remove(key: *const K)->!Self; containsKey(key: *const K)->bool; items()->iter.Iterator<(*const K,*const V)>; transient()->!TransientMap<K,V>
#class PersistentSet<T>|结构共享不可变集合
+ empty()->Self; len()->usize; contains(value: *const T)->bool; insert(value: T)->!Self; remove(value: *const T)->!Self; iterRef()->iter.Iterator<*const T>
#class TransientVec<T>|短暂独占可变批量构造器
+ push(value: T)->!void; set(index: usize,value: T)->!void; finish()->PersistentVec<T>
#class TransientMap<K,V>|短暂独占可变字典构造器
+ insert(key: K,value: V)->!void; remove(key: *const K)->bool; finish()->PersistentMap<K,V>
@ffi|目标 C ABI 互操作、原生文本缓冲与显式动态库接口
#alias c_char|目标 C char 类型，在 target C ABI 环境确定，不能假定总是 i8
#alias c_int|目标 C int 类型，在 target C ABI 环境确定，不能假定总是 i32
#alias c_uint|目标 C unsigned int 类型
#alias c_long|目标 C long 类型，其宽度与目标 ABI 相关
#alias c_ulong|目标 C unsigned long 类型
#alias c_void|目标 C void，用于无类型数据指针
#alias c_size_t|目标 C size_t；与 Vaqrin usize 按目标确认 ABI 一致性
#record CStr|以 NUL 结尾的借用 C 字符串
+ fromPtr(ptr: *const c_char,maxLen: usize)->!Self; asPtr()->*const c_char; toStr()->!str; toString()->!string; toBytes()->[]const u8
#class CString|拥有 NUL 终止 UTF-8 字节串，不允许内部 NUL
+ fromStr(value: str)->!Self; asPtr()->*const c_char; asBytes()->[]const u8; intoRaw()->*c_char; fromRaw(ptr: *c_char)->Self
#record OpaquePtr|未知类型的不透明指针容器
#class DynamicLibrary|按平台加载共享库（仅 hosted）
+ open(path: path.Path)->!Self; symbol<T>(name: str)->!?T; close()->!void
#record CLayout|C 对齐、大小和字段偏移检查
= cString(value: str)->!CString; cStr(ptr: *const c_char,maxLen: usize)->!CStr; ptrCast<T,U>(ptr: *U)->*T; constCast<T>(ptr: *const T)->*T; fromPtrLen<T>(ptr: *T,n: usize)->[]T; toOpaque<T>(ptr: *T)->OpaquePtr; fromOpaque<T>(ptr: OpaquePtr)->*T; cSizeOf<T>()->usize; cAlignOf<T>()->usize; errno()->i32; setErrno(value: i32)->void
@mmio|低级嵌入式寄存器、volatile 与屏障，危险动作限 unsafe
#record Volatile<T>|MMIO 地址和值宽度封装
+ new(ptr: *T)->Self; load()->T; store(value: T)->void; update<F>(f: F)->void
#record Register<T>|带读/写权限标记的寄存器
+ read()->T; write(value: T)->void; modify<F>(f: F)->void; setBits(mask: T)->void; clearBits(mask: T)->void
#record MmioRegion|基地址、长度、映射权限
+ new(base: usize,length: usize)->Self; registerAt<T>(offset: usize)->!Register<T>; contains(address: usize)->bool
= volatileLoad<T>(ptr: *const T)->T; volatileStore<T>(ptr: *T,value: T)->void; volatileUpdate<T,F>(ptr: *T,f: F)->void; compilerBarrier()->void; memoryBarrier()->void; instructionBarrier()->void; ptrFromInt<T>(address: usize)->*T; addressOf<T>(ptr: *T)->usize
