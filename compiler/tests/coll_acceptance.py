"""Compile and exercise the added container implementations."""
import unittest

from acceptance import CompilerCase, VAQC


class ContainerAcceptance(CompilerCase):
    def run_container(self, source):
        entry = self.source("containers.vqr", "import (coll, core, hash, io, mem)\n" + source)
        self.process(VAQC, "build", entry, "-O2", "-o", "containers")
        return self.process("./containers").stdout.decode().splitlines()

    def test_deques_wrap_and_preserve_noncopy_values(self):
        dropped = self.run_container('''
            class Probe {
                #copy: disabled
                pub var id: i32;
                func deinit() void { io.println(id) catch {}; }
            }
            func main() !void {
                var ring = coll.VecDeque<Probe>.withCapacity(4)!;
                for id in 1..5 { ring.pushBack(Probe{id: id})!; }
                var a = ring.popFront();
                var b = ring.popFront();
                ring.pushBack(Probe{id: 5})!;
                ring.pushBack(Probe{id: 6})!;
                var it = ring.iterRef();
                if it.next().unwrap().id != 3 || it.nextBack().unwrap().id != 6 {
                    panic("ring traversal order");
                }
                var suffix = ring.splitOff(2)!;
                ring.append(&suffix)!;
                var out = ring.remove(1);
                var removed = move(out).unwrap();
                if removed.id != 4 { panic("wrapped remove"); }
                var fixed = coll.FixedDeque<Probe, 2>();
                switch fixed.pushBack(Probe{id: 7}) { coll.PushResult.Stored => {}, _ => panic("fixed push"), }
                switch fixed.pushFront(Probe{id: 8}) { coll.PushResult.Stored => {}, _ => panic("fixed push"), }
                switch fixed.pushBack(Probe{id: 9}) {
                    coll.PushResult.Full(item) => { if item.id != 9 { panic("lost full value"); } },
                    _ => panic("unexpected fixed capacity"),
                }
                var zero = coll.FixedDeque<Probe, 0>();
                switch zero.pushFront(Probe{id: 10}) {
                    coll.PushResult.Full(item) => { if item.id != 10 { panic("lost zero capacity value"); } },
                    _ => panic("zero capacity accepted a value"),
                }
            }
        ''')
        self.assertEqual(sorted(map(int, dropped)), list(range(1, 11)))

    def test_list_stack_queue_heap_and_priority_queue(self):
        self.assertEqual(self.run_container('''
            func less(a, b: *const i32) bool { *a < *b }
            func main() !void {
                var list = coll.LinkedList<i32>();
                for id in 1..6 { list.pushBack(id)!; }
                var stable = list.getMut(3).unwrap();
                var tail = list.splitOff(2)!;
                if tail.getMut(1).unwrap() != stable { panic("node moved"); }
                tail.reverse();
                list.append(&tail)!;
                if list.remove(2).unwrap() != 5 || list.getMut(2).unwrap() != stable {
                    panic("list splice order");
                }
                list.clear();
                if !list.isEmpty() { panic("clear failed"); }
                var stack = coll.Stack<i32>();
                var queue = coll.Queue<i32>();
                var minimum = coll.BinaryHeap<i32>.min();
                var maximum = coll.PriorityQueue<i32>.withMaxComparator(less);
                var order = [4]i32{3, 1, 4, 2};
                for id in order {
                    stack.push(id)!; queue.enqueue(id)!; minimum.push(id)!; maximum.push(id)!;
                }
                if stack.pop().unwrap() != 2 || queue.dequeue().unwrap() != 3 {
                    panic("adapter order");
                }
                if minimum.pushPop(5) != 1 { panic("min pushPop"); }
                for id in 2..6 {
                    if minimum.pop().unwrap() != id || maximum.pop().unwrap() != 6 - id {
                        panic("heap order");
                    }
                }
            }
        '''), [])

    def test_hash_collisions_tombstones_and_move_only_rehash(self):
        dropped = self.run_container('''
            class Probe {
                #copy: disabled
                pub var id: i32;
                func deinit() void { io.println(id) catch {}; }
            }
            func collision(value: *const i32, seed: u32) usize { 7 }
            class Key {
                #implements: hash.HashKey
                pub var tag: u8;
                pub const func hash(state: *hash.Hasher) void {
                    var value = [1]u8{tag};
                    state.write(value[:]);
                }
                pub const func equals(other: *const Self) bool { tag == other.tag }
            }
            func main() !void {
                var map = coll.HashMap<i32, Probe>.withHasher(collision, hash.eqI32);
                for id in 1..81 { var old = map.insert(id, Probe{id: id})!; }
                for id in 1..41 {
                    var taken = map.remove(&id);
                    var value = move(taken).unwrap();
                    if value.id != id { panic("wrong removal"); }
                }
                for id in 41..81 {
                    if map.get(&id).unwrap().id != id { panic("probe stopped at tombstone"); }
                }
                for id in 81..121 { var old = map.insert(id, Probe{id: id})!; }
                var key = 55;
                var replacement = map.insert(key, Probe{id: 121})!;
                var old = move(replacement).unwrap();
                if old.id != 55 || map.get(&key).unwrap().id != 121 { panic("replace failed"); }
                var iterator = map.iterRef()!;
                var count = 0;
                for entry in move(iterator) { count++; }
                if count != 80 { panic("map iterator count"); }
                var set = coll.HashSet<i32>.withHasher(collision, hash.eqI32);
                if !set.insert(key)! || set.insert(key)! || !set.remove(&key) || set.contains(&key) {
                    panic("set uniqueness");
                }
                if hash.hashBytes("hello".asBytes()) != usize(1335831723) { panic("FNV-1a"); }
                var signed: i16 = -1;
                var unsigned = 65535u16;
                if hash.hashI16(&signed, 17) != hash.hashU16(&unsigned, 17) {
                    panic("16-bit byte order or signed representation");
                }
                var first = Key{tag: 61};
                var other = Key{tag: 61};
                var bytes = [1]u8{61};
                if !hash.equalKey<Key>(&first, &other) ||
                   hash.hashValue<Key>(&first) != hash.hashBytes(bytes[:]) {
                    panic("HashKey conformance");
                }
            }
        ''')
        self.assertEqual(sorted(map(int, dropped)), list(range(1, 122)))

    def test_btree_split_merge_borrow_and_move_only_entries(self):
        dropped = self.run_container('''
            class Probe {
                #copy: disabled
                pub var id: i32;
                func deinit() void { io.println(id) catch {}; }
            }
            func less(a, b: *const i32) bool { *a < *b }
            func main() !void {
                var tree = coll.BTreeMap<i32, Probe>.withComparator(less);
                for step in 0..129 {
                    var id = (step * 73) % 129;
                    var previous = tree.insert(id, Probe{id: id})!;
                }
                var k = 65;
                if *tree.lowerBound(&k).unwrap() != 65 || *tree.upperBound(&k).unwrap() != 66 {
                    panic("bound lookup failed");
                }
                {
                    var scan = tree.iterRef()!;
                    for expected in 0..129 {
                        var entry = scan.next().unwrap();
                        if *entry.0 != expected || entry.1.id != expected { panic("iterator order"); }
                    }
                }
                for step in 0..129 {
                    var id = (step * 107) % 129;
                    var picked = tree.remove(&id);
                    var item = move(picked).unwrap();
                    if item.id != id || tree.containsKey(&id) { panic("incorrect removal"); }
                }
                if !tree.isEmpty() { panic("root did not contract"); }
                var set = coll.BTreeSet<i32>.withComparator(less);
                if !set.insert(k)! || set.insert(k)! || !set.remove(&k) || set.contains(&k) {
                    panic("B-tree set uniqueness");
                }
            }
        ''')
        self.assertEqual(sorted(map(int, dropped)), list(range(129)))

    def test_btree_remove_by_borrowed_move_only_key(self):
        self.assertEqual(self.run_container('''
            class Key {
                #copy: disabled
                pub var id: i32;
            }
            func less(a, b: *const Key) bool { a.id < b.id }
            func main() !void {
                var tree = coll.BTreeMap<Key, i32>.withComparator(less);
                for id in 0..129 { var inserted = tree.insert(Key{id: id}, id)!; }
                for step in 0..129 {
                    var id = (step * 107) % 129;
                    var query = Key{id: id};
                    var borrowed = tree.lowerBound(&query).unwrap();
                    if borrowed.id != id { panic("borrowed key lookup"); }
                    var entry = tree.removeEntry(borrowed).unwrap();
                    if entry.0.id != id || entry.1 != id || tree.containsKey(&query) {
                        panic("removal invalidated its own query");
                    }
                }
                if !tree.isEmpty() { panic("root did not contract"); }
                for id in 0..129 { var inserted = tree.insert(Key{id: id}, id)!; }
                for expected in 0..64 {
                    var first = tree.popFirst().unwrap();
                    var last = tree.popLast().unwrap();
                    if first.0.id != expected || last.0.id != 128 - expected {
                        panic("wrong extreme while repairing a child");
                    }
                }
                if tree.popLast().unwrap().0.id != 64 || !tree.isEmpty() {
                    panic("wrong final key");
                }
            }
        '''), [])

    def test_allocator_failure_preserves_value_and_layout(self):
        self.assertEqual(self.run_container('''
            error Injected { Exhausted }
            class Allocator {
                #implements: mem.Allocator
                var owner: mem.GlobalAllocator;
                pub var fail: bool;
                pub var live: usize;
            public:
                func init() : owner(mem.GlobalAllocator()), fail(false), live(0) {}
                func alloc(layout: mem.Layout) !?*u8 {
                    if fail { return Injected.Exhausted; }
                    var allocation = owner.alloc(layout)!;
                    if allocation != null { live += layout.size(); }
                    allocation
                }
                func free(ptr: *u8, layout: mem.Layout) void {
                    live -= layout.size();
                    owner.free(ptr, layout);
                }
            }
            class Probe { #copy: disabled; pub var id: i32; }
            func main() !void {
                var allocator = Allocator();
                {
                    var values = coll.Vec<Probe>.withCapacityAndAllocator(1, &allocator)!;
                    values.push(Probe{id: 7})!;
                    allocator.fail = true;
                    switch values.pushPreserving(Probe{id: 8}) {
                        coll.PushResult.Failed(reason, item) => {
                            if reason != Injected.Exhausted || item.id != 8 || values.len() != 1 {
                                panic("failed growth did not preserve state");
                            }
                        },
                        _ => panic("allocation was expected to fail"),
                    }
                    allocator.fail = false;
                    if values.get(0).unwrap().id != 7 { panic("original slot was invalidated"); }
                    values.push(Probe{id: 9})!;
                    values.shrinkToFit()!;
                }
                if allocator.live != 0 { panic("allocation/free layouts differed"); }
                {
                    var map = coll.HashMap<i32, Probe>.withAllocator(hash.hashI32, hash.eqI32, &allocator);
                    allocator.fail = true;
                    switch map.insertPreserving(1, Probe{id: 2}) {
                        coll.InsertResult.Failed(reason, key, item) => {
                            if reason != Injected.Exhausted || key != 1 || item.id != 2 || !map.isEmpty() {
                                panic("map allocation failure lost inputs");
                            }
                        },
                        _ => panic("map accepted a value without storage"),
                    }
                    allocator.fail = false;
                    var old = map.insert(1, Probe{id: 3})!;
                    allocator.fail = true;
                    var rejected = false;
                    map.reserve(500) catch (reason) {
                        if reason != Injected.Exhausted { return reason; }
                        rejected = true;
                    };
                    var key = 1;
                    if !rejected || map.len() != 1 || map.get(&key).unwrap().id != 3 {
                        panic("failed rehash lost entry");
                    }
                    allocator.fail = false;
                }
                if allocator.live != 0 { panic("map allocation/free layouts differed"); }
            }
        '''), [])

    def test_btree_root_split_allocation_failure_keeps_old_tree(self):
        source = self.source("oom.vqr", '''
            import (coll, mem)
            extern "C" func fail_second_allocation() void;
            func less(a, b: *const i32) bool { *a < *b }
            func try_insert() !i32 {
                var tree = coll.BTreeMap<i32, i32>.withComparator(less);
                for key in 0..7 { var old = tree.insert(key, key * 3)!; }
                unsafe { fail_second_allocation(); }
                var rejected = false;
                var outcome = tree.insert(7, 21) catch (reason) {
                    if reason != mem.AllocationError.OutOfMemory { return reason; }
                    rejected = true;
                    null
                };
                if !rejected || tree.len() != 7 { return 1; }
                for key in 0..7 {
                    if *tree.get(&key).unwrap() != key * 3 { return 2; }
                }
                var after = tree.insert(7, 21)!;
                if after != null || tree.len() != 8 { return 3; }
                0
            }
            export "C" func exercise() c_int { try_insert() catch 4 }
        ''')
        client = self.source("oom.c", '''
            #include <stddef.h>
            extern void *__real_malloc(size_t);
            extern void *__real_aligned_alloc(size_t, size_t);
            extern int exercise(void);
            static int countdown;
            void fail_second_allocation(void) { countdown = 2; }
            void *__wrap_malloc(size_t bytes) {
                if (countdown && --countdown == 0) return NULL;
                return __real_malloc(bytes);
            }
            void *__wrap_aligned_alloc(size_t alignment, size_t bytes) {
                if (countdown && --countdown == 0) return NULL;
                return __real_aligned_alloc(alignment, bytes);
            }
            int main(void) { return exercise(); }
        ''')
        self.process(VAQC, source, "-O2", "--emit=obj", "-o", "tree.o")
        self.process("cc", client, "tree.o", "-Wl,--wrap=malloc,--wrap=aligned_alloc", "-o", "tree")
        self.process("./tree")


if __name__ == "__main__":
    unittest.main()
