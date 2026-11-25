"""Compile and exercise the added container implementations."""
import unittest

from acceptance import CompilerCase, VAQC


class ContainerAcceptance(CompilerCase):
    def run_container(self, source):
        entry = self.source("containers.vqr", "import (coll, core, hash, io, mem)\n" + source)
        self.process(VAQC, "build", entry, "-O2", "-o", "containers")
        return self.process("./containers").stdout.decode().splitlines()

    def test_vec_checked_slice_keeps_owner_live(self):
        prelude = """
            import (coll)
            func fromVec(owner: in coll.Vec<i32>) []const i32 from owner {
                owner.borrowSlice()
            }
            func sum(view: in []const i32) i32 {
                var total = 0;
                for ref item in view { total += item; }
                total
            }
        """
        program = prelude + """
            func main() !void {
                var owner = coll.Vec<i32>();
                io.println(owner.borrowSlice().len())!;
                owner.push(3)!;
                owner.push(5)!;
                {
                    var all = fromVec(owner);
                    var tail = all[1..2];
                    var element = ref all[0];
                    io.println(sum(tail) + element)!;
                }
                owner.reserve(128)!;
                io.println(owner.remove(0).unwrap())!;
                io.println(sum(owner.borrowSlice()))!;
                io.println(coll.Vec<i32>().borrowSlice().len())!;
            }
        """
        for level in ("-O0", "-O2"):
            with self.subTest(level=level):
                self.hosted(program, "0\n8\n3\n5\n0\n", level)

        cases = {
            "growth": ("var view = owner.borrowSlice(); owner.reserve(128)!;", "active borrow"),
            "remove": ("var view = owner.borrowSlice(); owner.remove(0);", "active borrow"),
            "move": ("var view = owner.borrowSlice(); var moved = move(owner);", "active borrow"),
            "mutable": ("var view = owner.borrowSlice(); var item = ref mut view[0];",
                        "cannot borrow a read-only object mutably"),
            "temporary": ("var view = coll.Vec<i32>().borrowSlice();", "bind the owner first"),
            "wrong_return_source": ("return owner.borrowSlice();", "declared source"),
            "untracked_callee": ("var view = owner.borrowSlice(); unchecked(view);", "untracked"),
            "competing_call": ("conflict(owner.borrowSlice(), owner);", "active borrow"),
            "mutating_index": ("var view = owner.borrowSlice(); owner[0]++;", "active borrow"),
            "outlives_owner": ("var outer: []const i32; { var inner = coll.Vec<i32>(); "
                               "outer = inner.borrowSlice(); }", "outlive"),
            "non_scalar": ("var composite = coll.Vec<(i32, i32)>(); var view = composite.borrowSlice();",
                           "checked readonly view"),
        }
        for name, (code, diagnostic) in cases.items():
            with self.subTest(name=name):
                if name == "wrong_return_source":
                    body = "func rejected(other: in coll.Vec<i32>) []const i32 from other {"
                else:
                    body = "func rejected() !void {"
                source = self.source(name + "/main.vqr", prelude + """
                    func unchecked(view: []const i32) void {}
                    func conflict(view: in []const i32, owner: mut coll.Vec<i32>) void {}
                """ + body + "var owner = coll.Vec<i32>(); " + code + "}")
                result = self.process(VAQC, "check", source, good=False)
                self.assertIn(diagnostic.encode(), result.stderr, result.stderr.decode())

    def test_optional_storage_checked_ownership_and_raw_admission(self):
        definitions = """
            import (opt)
            class Owned {
                #copy: disabled
                pub var id: i32;
                pub func init(id: i32) : id(id) {}
                func deinit() void { io.println(id) catch {}; }
            }
        """
        result = self.run_container(definitions + """
            func main() !void {
                var integers: ?i32 = some(3);
                var copy = 4;
                if opt.Storage<i32>.replace(integers, copy).unwrap() != 3 || copy != 4 ||
                   opt.take(integers).unwrap() != 4 || integers != null { panic("wrong copy/take"); }

                var slot: ?Owned = some(Owned(11));
                var value = Owned(12);
                var old = opt.Storage<Owned>.replace(slot, move(value)).unwrap();
                var owned = opt.take(slot).unwrap();
                if old.id != 11 || owned.id != 12 || slot != null { panic("wrong ownership"); }
                slot = some(Owned(13));
                var rawOwned = unsafe { opt.takeRaw(raw slot) }.unwrap();
                if rawOwned.id != 13 || slot != null { panic("raw take failed to leave a valid slot"); }
            }
        """)
        self.assertEqual(sorted(map(int, result)), [11, 12, 13])
        for operation, diagnostic in (
            ("opt.take(raw slot);", "no accessible overload"),
            ("opt.takeRaw(raw slot);", "requires an unsafe block"),
            ("opt.Storage<Owned>.replace(slot, value);", "move parameter requires move(object)"),
            ("var other = ref slot; opt.take(slot);", "active borrow"),
        ):
            with self.subTest(operation=operation):
                entry = self.source("opt-storage.vqr", "import (io)\n" + definitions + """
                    func main() void {
                        var slot: ?Owned = null;
                        var value = Owned(21);
                """ + operation + "\n}")
                self.assertIn(diagnostic.encode(), self.process(VAQC, "check", entry, good=False).stderr)

    def test_checked_replacement_copies_or_consumes_and_returns_previous_owner(self):
        definitions = """
            class Counted {
                #copy: enable
                pub var id: i32;
                pub func init(id: i32) : id(id) {}
                pub const func copy() Self {
                    io.println(100 + id) catch {};
                    Counted(id)
                }
                func deinit() void { io.println(id) catch {}; }
            }
            class Owned {
                #copy: disabled
                pub var id: i32;
                pub func init(id: i32) : id(id) {}
                func deinit() void { io.println(id) catch {}; }
            }
            error Refusal { NoReplacement }
            func rejected() !Owned { Refusal.NoReplacement }
            func tryReplace(target: mut Owned) !void {
                core.Exchange<Owned>.replace(target, rejected()!);
            }
        """
        result = self.run_container(definitions + """
            func main() !void {
                var target = Counted(1);
                var replacement = Counted(2);
                var previous = core.Exchange<Counted>.replace(target, replacement);
                if previous.id != 1 || target.id != 2 || replacement.id != 2 {
                    panic("wrong Copyable replacement");
                }
                var owner = Owned(3);
                var incoming = Owned(4);
                var extracted = core.Exchange<Owned>.replace(owner, move(incoming));
                var again = core.Exchange<Owned>.replace(owner, Owned(5));
                if extracted.id != 3 || again.id != 4 || owner.id != 5 {
                    panic("wrong owning replacement");
                }
                tryReplace(owner) catch (reason) {
                    if reason != Refusal.NoReplacement || owner.id != 5 {
                        panic("failed replacement expression changed the old owner");
                    }
                    return;
                };
                panic("replacement unexpectedly succeeded");
            }
        """)
        self.assertEqual(sorted(map(int, result)), [1, 2, 2, 3, 4, 5, 102])
        for body, diagnostic in (
            ("core.Exchange<Owned>.replace(owner, incoming);", "move parameter requires move(object)"),
            ("core.Exchange<Owned>.replace(raw owner, move(incoming));", "no accessible overload"),
            ("var loan = ref owner; core.Exchange<Owned>.replace(owner, move(incoming));", "active borrow"),
            ("core.Exchange<Owned>.replace(owner, { owner = Owned(6); Owned(7) });", "active borrow"),
        ):
            with self.subTest(body=body):
                entry = self.source("invalid-exchange.vqr", "import (core, io)\n" + definitions + """
                    func main() void {
                        var owner = Owned(3);
                        var incoming = Owned(4);
                """ + body + "\n}")
                self.assertIn(diagnostic.encode(), self.process(VAQC, "check", entry, good=False).stderr)

    def test_manual_storage_ownership_modes(self):
        definitions = """
            class Counted {
                #copy: enable
                pub var id: i32;
                pub func init(id: i32) : id(id) {}
                pub const func copy() Counted {
                    io.println(100 + id) catch {};
                    Counted(id)
                }
                func deinit() void { io.println(id) catch {}; }
            }
            class Owned {
                #copy: disabled
                pub var id: i32;
                pub func init(id: i32) : id(id) {}
                func deinit() void { io.println(id) catch {}; }
            }
        """
        output = self.run_container(definitions + """
            func main() !void {
                var input = Counted(1);
                var slot = mem.MaybeUninit<Counted>.uninit();
                unsafe {
                    var stored = slot.write(input);
                    if stored.id != 1 { panic("wrong copied payload"); }
                    slot.assumeInitDrop();
                }
                var copy = mem.ManuallyDrop<Counted>.new(input);
                if input.id != 1 { panic("copy failed to preserve the source"); }
                var extracted = mem.ManuallyDrop<Counted>.intoInner(move(copy));

                var owner = Owned(2);
                var vacant = mem.MaybeUninit<Owned>.uninit();
                unsafe {
                    vacant.write(move(owner));
                    vacant.assumeInitDrop();
                }
                var manual = mem.ManuallyDrop<Owned>.new(Owned(3));
                var transferred = mem.ManuallyDrop<Owned>.intoInner(move(manual));
                if extracted.id != 1 || transferred.id != 3 { panic("wrong transfer"); }
            }
        """)
        self.assertEqual(sorted(map(int, output)), [1, 1, 1, 2, 3, 101, 101])
        for operation in (
            "unsafe { slot.write(owner); }",
            "var manual = mem.ManuallyDrop<Owned>.new(owner);",
            "var value = mem.ManuallyDrop<Counted>.intoInner(manual);",
        ):
            with self.subTest(operation=operation):
                entry = self.source("move.vqr", "import (io, mem)\n" + definitions + """
                    func main() void {
                        var owner = Owned(4);
                        var slot = mem.MaybeUninit<Owned>.uninit();
                        var manual = mem.ManuallyDrop<Counted>.new(Counted(5));
                """ + operation + "\n}")
                diagnostic = self.process(VAQC, "check", entry, good=False).stderr
                self.assertIn(b"move parameter requires move(object)", diagnostic)

    def test_conditional_vec_methods_do_not_force_recursive_layout(self):
        self.assertEqual(self.run_container(r"""
            import (meta)
            class Tree { pub var children: coll.Vec<Tree>; }
            enum Value { Leaf(i32), List(coll.Vec<Value>) }
            func main() !void {
                var children = coll.Vec<Tree>();
                var child = Tree{children: coll.Vec<Tree>()};
                children.push(move(child))!;
                if children.len() != 1 { panic("recursive class element was not stored"); }

                var values = coll.Vec<Value>();
                var leaf = Value.Leaf(7);
                values.push(move(leaf))!;
                values.push(Value.List(coll.Vec<Value>()))!;
                if values.len() != 2 { panic("recursive enum elements were not stored"); }
                if meta.isCopyable<Tree>() || meta.isCopyable<Value>() {
                    panic("owning Vec elements must not be Copyable");
                }
            }
        """), [])
        direct = self.source("inline-cycle.vqr", "class Loop { var child: Loop; }")
        diagnostic = self.process(VAQC, "check", direct, good=False).stderr
        self.assertIn(b"recursive by-value layout", diagnostic)
        implicit = self.source("implicit-move.vqr", """
            import (coll)
            class Tree { pub var children: coll.Vec<Tree>; }
            func main() !void {
                var children = coll.Vec<Tree>();
                var child = Tree{children: coll.Vec<Tree>()};
                children.push(child)!;
            }
        """)
        diagnostic = self.process(VAQC, "check", implicit, good=False).stderr
        self.assertIn(b"move parameter requires move(object)", diagnostic)

    def test_conditional_copy_and_deinit_are_resolved_when_requested(self):
        self.assertEqual(self.run_container(r"""
            import (meta)
            class Marker { #copy: disabled; }
            class Value<T> {
                #copy: enable
                pub var number: i32;
                pub func init(n: i32) : number(n) {}
                comptime if meta.isCopyable<T>() {
                    pub const func copy() Self {
                        io.println(100 + number) catch {};
                        Value<T>(number)
                    }
                    func deinit() void { io.println(number) catch {}; }
                } else {
                    pub const func copy() Self { Value<T>(number) }
                }
            }
            func main() void {
                var first = Value<i32>(7);
                var duplicate = first;
                duplicate.number += 1;
                if first.number != 7 { panic("copy aliased its source"); }
                var silent = Value<Marker>(3);
                var alsoSilent = silent;
            }
        """), ["107", "8", "7"])

    def test_map_entry_lazy_insert_and_remove(self):
        output = self.run_container(r"""
            func main() !void {
                var map = coll.HashMap<i32, i32>.withHasher(hash.hashI32, hash.eqI32);
                {
                    var entry = unsafe { map.entry(8) };
                    io.println(entry.isVacant() && *entry.key() == 8)!;
                    var build = () i32 => 42;
                    io.println(*entry.orInsertWith(build)! == 42)!;
                }
                {
                    var entry = unsafe { map.entry(8) };
                    var adjust = (address: *i32) void => { *address += 3; };
                    var changed = entry.andModify(adjust);
                    io.println(changed.isOccupied() && *entry.get().unwrap() == 45)!;
                    io.println(entry.remove().unwrap() == 45)!;
                }
                io.println(map.isEmpty())!;
            }
        """)
        self.assertEqual(output, ["true"] * 5)

    def test_map_insert_selects_independent_key_and_value_modes(self):
        declarations = r"""
            class Owned {
                #copy: disabled
                pub var id: i32;
                pub var lifetime: i32;
                pub func init(key: i32, lifetime: i32) : id(key), lifetime(lifetime) {}
                func deinit() void { io.println(lifetime) catch {}; }
            }
            func hashOwned(key: *const Owned, seed: u32) usize {
                hash.hashI32(raw key.id, seed)
            }
            func equalOwned(a: *const Owned, b: *const Owned) bool { a.id == b.id }
        """
        output = self.run_container(declarations + r"""
            func main() !void {
                var first = coll.i32Map<i32>();
                var key = 1;
                var number = 17;
                first.insert(key, number)!;
                if first.insert(key, number + 1)!.unwrap() != 17 || key != 1 || number != 17 {
                    panic("Copyable key/value were not copied");
                }

                var second = coll.i32Map<Owned>();
                var incoming = Owned(2, 21);
                second.insert(key, move(incoming))!;
                var previous = second.insert(key, Owned(2, 22))!.unwrap();
                if previous.lifetime != 21 || second.get(key).unwrap().lifetime != 22 { panic("wrong value"); }

                var third = coll.HashMap<Owned, i32>.withHasher(hashOwned, equalOwned);
                var namedKey = Owned(3, 31);
                third.insert(move(namedKey), number)!;
                switch third.insertPreserving(Owned(3, 32), number + 1) {
                    coll.InsertResult.Replaced(old) => { if old != 17 { panic("wrong copied value"); } },
                    _ => panic("move-key overwrite failed"),
                }

                var fourth = coll.HashMap<Owned, Owned>.withHasher(hashOwned, equalOwned);
                var ownedKey = Owned(4, 41);
                var ownedValue = Owned(5, 51);
                switch fourth.insertPreserving(move(ownedKey), move(ownedValue)) {
                    coll.InsertResult.Inserted => {},
                    _ => panic("move-key and move-value insert failed"),
                }
                var current = fourth.insert(Owned(4, 42), Owned(5, 52))!.unwrap();
                if current.lifetime != 51 || fourth.len() != 1 { panic("wrong previous value"); }
            }
        """)
        self.assertEqual(sorted(output), ["21", "22", "31", "32", "41", "42", "51", "52"])
        for expression in (
            "values.insert(1, value)!;",
            "values.insertPreserving(1, value);",
            "keys.insert(key, 1)!;",
            "keys.insertPreserving(key, 1);",
            "both.insert(move(key), value)!;",
            "both.insertPreserving(key, move(value));",
        ):
            with self.subTest(expression=expression):
                source = self.source("ownership.vqr", "import (coll, hash, io)\n" + declarations + """
                    func main() !void {
                        var values = coll.i32Map<Owned>();
                        var keys = coll.HashMap<Owned, i32>.withHasher(hashOwned, equalOwned);
                        var both = coll.HashMap<Owned, Owned>.withHasher(hashOwned, equalOwned);
                        var key = Owned(3, 31);
                        var value = Owned(4, 41);
                """ + expression + "\n}")
                diagnostic = self.process(VAQC, "check", source, good=False).stderr
                self.assertIn(b"move parameter requires move(object)", diagnostic)

    def test_map_copyable_arguments_are_copied_only_at_the_public_boundary(self):
        result = self.run_container(r"""
            class Counted {
                #copy: enable
                pub var id: i32;
                pub func init(id: i32) : id(id) {}
                pub const func copy() Counted {
                    io.println(100 + id) catch {};
                    Counted(id)
                }
                func deinit() void { io.println(id) catch {}; }
            }
            func keyHash(key: *const Counted, seed: u32) usize {
                hash.hashI32(raw key.id, seed)
            }
            func keyEquals(a: *const Counted, b: *const Counted) bool { a.id == b.id }
            func main() !void {
                var key = Counted(1);
                var value = Counted(2);
                var table = coll.HashMap<Counted, Counted>.withHasher(keyHash, keyEquals);
                switch table.insertPreserving(key, value) {
                    coll.InsertResult.Inserted => {},
                    _ => panic("fresh copied entry was not inserted"),
                }
                if key.id != 1 || value.id != 2 { panic("insertion consumed Copyable sources"); }
            }
        """)
        self.assertEqual(sorted(map(int, result)), [1, 1, 2, 2, 101, 102])

    def test_map_owned_get_or_insert_and_entry_modes(self):
        definitions = """
            class Owned {
                #copy: disabled
                pub var id: i32;
                pub var label: i32;
                pub func init(id: i32, label: i32) : id(id), label(label) {}
                func deinit() void { io.println(label) catch {}; }
            }
            func keyHash(key: *const Owned, seed: u32) usize { hash.hashI32(raw key.id, seed) }
            func keyEquals(a, b: *const Owned) bool { a.id == b.id }
            func create() Owned { io.println(90) catch {}; Owned(3, 32) }
            class RefusingAllocator {
                #implements: mem.Allocator
                pub func init() {}
                pub func alloc(layout: mem.Layout) core.ResultOptional<*u8> {
                    mem.AllocationError.OutOfMemory
                }
                pub unsafe func free(ptr: *u8, layout: mem.Layout) void { panic("unallocated storage"); }
            }
        """
        output = self.run_container(definitions + """
            func main() !void {
                var map = coll.HashMap<Owned, Owned>.withHasher(keyHash, keyEquals);
                var key = Owned(1, 11);
                var value = Owned(1, 12);
                {
                    var inserted = map.getOrInsert(move(key), move(value))!;
                    inserted.id++;
                    if inserted.label != 12 { panic("wrong inserted value"); }
                }
                if map.getOrInsertPtr(Owned(1, 13), Owned(1, 14))!.id != 2 {
                    panic("hit overwrote stored value");
                }
                {
                    var query = Owned(2, 21);
                    var bound = unsafe { map.entry(move(query)) };
                    var initial = Owned(2, 22);
                    if bound.orInsert(move(initial))!.label != 22 { panic("entry lost value"); }
                }
                {
                    var lookup = Owned(2, 23);
                    var existing = map.getOrInsertWith(move(lookup), create)!;
                    if existing.label != 22 { panic("lazy hit called factory"); }
                }
                {
                    var existing = map.getOrInsertWith(Owned(3, 31), create)!;
                    if existing.label != 32 { panic("lazy miss did not call factory"); }
                }
                var set = coll.HashSet<Owned>.withHasher(keyHash, keyEquals);
                var member = Owned(4, 41);
                if !set.insert(move(member))! { panic("move-only set insert failed"); }
                var allocator = RefusingAllocator();
                var refused = coll.HashMap<Owned, Owned>.withAllocator(keyHash, keyEquals, unsafe { mem.AllocatorRef.fromRaw(raw allocator) });
                refused.getOrInsert(Owned(5, 51), Owned(5, 52)) catch (reason) {
                    if reason != mem.AllocationError.OutOfMemory || !refused.isEmpty() {
                        panic("failed allocation changed the map");
                    }
                    return;
                };
                panic("allocation unexpectedly succeeded");
            }
        """)
        self.assertEqual(sorted(map(int, output)), [11, 12, 13, 14, 21, 22, 23, 31, 32, 41, 51, 52, 90])

        for statement in (
            "map.getOrInsert(key, Owned(2, 22))!;",
            "map.getOrInsertPtr(Owned(1, 21), value)!;",
            "unsafe { var result = map.entry(key); }",
            "map.getOrInsertWith(key, create)!;",
            "var result = unsafe { map.entry(Owned(2, 21)) }; result.orInsert(value)!;",
        ):
            with self.subTest(statement=statement):
                entry = self.source("failed.vqr", "import (coll, core, hash, io, mem)\n" + definitions + """
                    func main() !void {
                        var map = coll.HashMap<Owned, Owned>.withHasher(keyHash, keyEquals);
                        var key = Owned(1, 11);
                        var value = Owned(2, 12);
                """ + statement + "\n}")
                diagnostic = self.process(VAQC, "check", entry, good=False).stderr
                self.assertIn(b"move parameter requires move(object)", diagnostic)

    def test_consuming_vec_drops_each_slot_once(self):
        output = self.run_container(r"""
            class Owned {
                #copy: disabled
                pub var value: i32;
                func deinit() void { io.println(value) catch {}; }
            }
            func main() !void {
                var vector = coll.Vec<Owned>();
                for i in 1..6 { vector.push(Owned{value: i})!; }
                var iterator = coll.Vec<Owned>.intoIter(move(vector));
                {
                    var first = iterator.next().unwrap();
                    var last = iterator.nextBack().unwrap();
                    if first.value != 1 || last.value != 5 || iterator.remaining() != 3 {
                        panic("invalid Vec iterator cursor");
                    }
                }
            }
        """)
        self.assertEqual(sorted(output), ["1", "2", "3", "4", "5"])

    def test_vec_clone_copies_each_element_once(self):
        self.assertEqual(self.run_container("""
            class Counted {
                #copy: enable
                pub var id: i32;
                pub func init(id: i32) : id(id) {}
                pub const func copy() Self {
                    io.println(id) catch {};
                    Counted(id)
                }
            }
            func main() !void {
                var input = coll.Vec<Counted>();
                input.push(Counted(1))!;
                input.push(Counted(2))!;
                var output = input.clone()!;
                if input.len() != 2 || output.len() != 2 {
                    panic("cloning changed the number of elements");
                }
            }
        """), ["1", "2"])

    def test_vec_push_selects_copy_or_move(self):
        self.assertEqual(self.run_container(r"""
            class Item { #copy: disabled; pub var value: i32; }
            func main() !void {
                var numbers = coll.Vec<i32>();
                var number = 12;
                numbers.push(number)!;
                switch numbers.pushPreserving(number + 1) {
                    coll.PushResult.Stored => {},
                    _ => panic("the copied item was not stored"),
                }
                if number != 12 || numbers.pop().unwrap() != 13 || numbers.pop().unwrap() != 12 {
                    panic("copying into Vec changed the source value");
                }
                var items = coll.Vec<Item>();
                var first = Item{value: 19};
                items.push(move(first))!;
                switch items.pushPreserving(Item{value: 20}) {
                    coll.PushResult.Stored => {},
                    _ => panic("the transferred item was not stored"),
                }
                if items.pop().unwrap().value != 20 || items.pop().unwrap().value != 19 {
                    panic("moving into Vec lost an item");
                }
            }
        """), [])
        for method, suffix in (("push", "!"), ("pushPreserving", "")):
            entry = self.source("unmoved.vqr", f"""
                import (coll)
                class Item {{ #copy: disabled; pub var value: i32; }}
                func main() !void {{
                    var items = coll.Vec<Item>();
                    var item = Item{{value: 19}};
                    items.{method}(item){suffix};
                }}
            """)
            diagnostic = self.process(VAQC, "check", entry, good=False).stderr
            self.assertIn(b"move parameter requires move(object)", diagnostic)

    def test_vec_insert_replace_modes_preserve_ownership_and_error_payloads(self):
        self.assertEqual(self.run_container(r"""
            class Item {
                #copy: disabled
                pub var id: i32;
                pub func init(value: i32) : id(value) {}
                func deinit() void { io.println(id) catch {}; }
            }
            func main() !void {
                var numbers = coll.Vec<i32>();
                var number = 12;
                numbers.insert(0, number)!;
                switch numbers.insertPreserving(4, number) {
                    coll.PushResult.Failed(reason, recovered) => {
                        if reason != coll.CapacityError.IndexOutOfBounds || recovered != 12 { panic("bad insertion"); }
                    },
                    _ => panic("insert accepted invalid index"),
                }
                if numbers.replace(0, number + 1).unwrap() != 12 || number != 12 { panic("bad copy"); }
                switch numbers.replacePreserving(0, number + 2) {
                    coll.ReplaceResult.Replaced(previous) => { if previous != 13 { panic("bad replacement"); } },
                    _ => panic("replace rejected valid index"),
                }
                var copy = numbers.clone()!;
                if numbers.pop().unwrap() != 14 || copy.pop().unwrap() != 14 { panic("bad clone"); }

                var items = coll.Vec<Item>();
                var original = Item(21);
                items.insert(0, move(original))!;
                switch items.insertPreserving(4, Item(22)) {
                    coll.PushResult.Failed(reason, item) => {
                        if reason != coll.CapacityError.IndexOutOfBounds || item.id != 22 { panic("bad rejected payload"); }
                    },
                    _ => panic("insert accepted invalid index"),
                }
                var replacement = Item(23);
                switch items.replacePreserving(0, move(replacement)) {
                    coll.ReplaceResult.Replaced(previous) => { if previous.id != 21 { panic("wrong previous item"); } },
                    _ => panic("replace rejected valid index"),
                }
                switch items.replacePreserving(8, Item(24)) {
                    coll.ReplaceResult.InvalidIndex(item) => { if item.id != 24 { panic("wrong returned item"); } },
                    _ => panic("replace accepted invalid index"),
                }
                var old = items.replace(0, Item(25)).unwrap();
                if old.id != 23 || items.get(0).unwrap().id != 25 || items.len() != 1 { panic("bad stored item"); }
            }
        """), ["22", "21", "24", "23", "25"])
        for operation in (
            "items.insert(0, item)!;", "items.insertPreserving(0, item);",
            "items.replace(0, item);", "items.replacePreserving(0, item);",
            "items.spliceOwned(0, 0, replacement)!;",
            "var iterator = coll.Vec<Item>.intoIter(replacement);",
        ):
            with self.subTest(operation=operation):
                source = self.source("unmoved.vqr", """
                    import (coll)
                    class Item { #copy: disabled; pub var id: i32; }
                    func main() !void {
                        var items = coll.Vec<Item>();
                        var item = Item{id: 22};
                        var replacement = coll.Vec<Item>();
                """ + operation + "\n}")
                diagnostic = self.process(VAQC, "check", source, good=False).stderr
                self.assertIn(b"move parameter requires move(object)", diagnostic)

    def test_stable_sort_ranges_and_map_projections(self):
        output = self.run_container(r"""
            import (algo)
            class Item {
                #copy: disabled
                pub var key: i32;
                pub var ordinal: i32;
            }
            func less(a: *const Item, b: *const Item) bool { a.key < b.key }
            func intLess(a: *const i32, b: *const i32) bool { *a < *b }
            func main() !void {
                var data = coll.Vec<Item>();
                for i in 0..48 { data.push(Item{key: (47 - i) % 5, ordinal: i})!; }
                algo.stableSortBy<Item>(data.asSlice(), less);
                for i in usize(1)..data.len() {
                    var a = data.getPtr(i - 1).unwrap();
                    var b = data.getPtr(i).unwrap();
                    if a.key > b.key || (a.key == b.key && a.ordinal >= b.ordinal) { panic("unstable sort"); }
                }
                var tree = coll.BTreeMap<i32, i32>.withComparator(intLess);
                for i in 0..48 { var previous = tree.insert(i * 2, i)!; }
                var start = 11;
                var end = 27;
                var range = tree.range(some(raw start), some(raw end));
                var counter = 6;
                for key, value in move(range) {
                    if *key != counter * 2 || *value != counter { panic("range cursor mismatch"); }
                    counter++;
                }
                io.println(counter)!;
                var mixed = tree.iterMut()!;
                var low = mixed.next().unwrap();
                var high = mixed.nextBack().unwrap();
                *low.1 = 100;
                *high.1 = 200;
                io.println(*tree.first().unwrap().1 == 100 && *tree.last().unwrap().1 == 200)!;
                var table = coll.HashMap<i32, i32>.withHasher(hash.hashI32, hash.eqI32);
                for i in 0..30 { var previous = table.insert(i, i * 2)!; }
                var removed = table.remove(start);
                var keys = table.keys();
                var values = table.values();
                var total = 0;
                for key in move(keys) { total += *key; }
                var valueTotal = 0;
                for value in move(values) { valueTotal += *value; }
                io.println(total == 424 && valueTotal == 848)!;
                var factory = () i32 => 700;
                {
                    var newEntry = table.getOrInsertWith(31, factory)!;
                    io.println(newEntry == 700)!;
                }
                var existing = table.getOrInsertWith(31, factory)!;
                io.println(existing == 700)!;
                var pair = (5, 10);
                var pointers = coll.Vec<(*const i32, *const i32)>();
                pointers.push((raw pair.0, raw pair.1))!;
                var old = coll.MapIterRef<i32, i32>(move(pointers), 1);
                var valuesFromOld = old.nextBack().unwrap();
                var treePointers = coll.Vec<*const (i32, i32)>();
                treePointers.push(raw pair)!;
                var oldTree = coll.BTreeIterRef<i32, i32>(move(treePointers), 1);
                io.println(*valuesFromOld.1 == 10 && *oldTree.next().unwrap().0 == 5)!;
            }
        """)
        self.assertEqual(output, ["14", "true", "true", "true", "true", "true"])

    def test_map_projection_owns_cursor_and_preserves_its_remaining_state(self):
        self.assertEqual(self.run_container("""
            func less(a: *const i32, b: *const i32) bool { unsafe { *a < *b } }
            func main() !void {
                var table = coll.i32Map<i32>();
                table.insert(1, 10)!;
                table.insert(2, 20)!;
                table.insert(3, 30)!;
                var live = table.iterRef()!;
                var first = live.next().unwrap();
                var projected = coll.Keys<i32, i32>(move(live));
                var tail = projected.nextBack().unwrap();
                var middle = projected.next().unwrap();
                if projected.remaining() != 0 || projected.next() != null ||
                   first.0 == tail || first.0 == middle || tail == middle {
                    panic("moving a live cursor reset or duplicated its iteration state");
                }
                if unsafe { *first.0 + *tail + *middle } != 6 {
                    panic("projected keys no longer refer to the live map");
                }

                var pairs = [3](i32, i32){(1, 10), (2, 20), (3, 30)};
                var pointers = coll.Vec<(*const i32, *const i32)>();
                for i in usize(0)..3 { pointers.push((raw pairs[i].0, raw pairs[i].1))!; }
                var snapshot = coll.MapIterRef<i32, i32>(move(pointers), 3);
                var firstSnapshot = snapshot.next().unwrap();
                var values = coll.Values<i32, i32>(move(snapshot));
                var firstPointer: *const i32 = raw pairs[0].1;
                var middlePointer: *const i32 = raw pairs[1].1;
                var lastPointer: *const i32 = raw pairs[2].1;
                if values.remaining() != 2 || firstSnapshot.1 != firstPointer ||
                   values.nextBack().unwrap() != lastPointer ||
                   values.next().unwrap() != middlePointer || values.nextBack() != null {
                    panic("moving a snapshot cursor did not preserve its pointers and consumed prefix");
                }

                var btreePointers = coll.Vec<*(i32, i32)>();
                btreePointers.push(raw pairs[0])!;
                btreePointers.push(raw pairs[1])!;
                var writable = coll.BTreeIterMut<i32, i32>(move(btreePointers), 2);
                var existing = writable.nextBack().unwrap();
                var keyPointer: *const i32 = raw pairs[1].0;
                var valuePointer: *i32 = raw pairs[1].1;
                if existing.0 != keyPointer || existing.1 != valuePointer {
                    panic("snapshot changed the address of a mutable BTree entry");
                }
                unsafe { *existing.1 = 99; }
                if pairs[1].1 != 99 || writable.remaining() != 1 {
                    panic("snapshot lost access to the original value");
                }

                var hashSet = coll.HashSet<i32>.withHasher(hash.hashI32, hash.eqI32);
                hashSet.insert(4)!;
                var hashKeys = hashSet.iterRef()!;
                if unsafe { *hashKeys.next().unwrap() } != 4 || hashKeys.remaining() != 0 {
                    panic("hash-set projection lost its fresh cursor");
                }
                var treeSet = coll.BTreeSet<i32>.withComparator(less);
                treeSet.insert(5)!;
                treeSet.insert(6)!;
                var treeKeys = treeSet.iterRef()!;
                if unsafe { *treeKeys.nextBack().unwrap() } != 6 ||
                   unsafe { *treeKeys.next().unwrap() } != 5 || treeKeys.remaining() != 0 {
                    panic("BTree-set projection lost its fresh cursor");
                }
            }
        """), [])
        for projection in ("Keys<i32, i32>", "Values<i32, i32>", "SetIterRef<i32>"):
            with self.subTest(projection=projection):
                entry = self.source(projection.split("<")[0] + "/main.vqr", """
                    import (coll)
                    func main() !void {
                        var table = coll.i32Map<TYPE>();
                        var cursor = table.iterRef()!;
                        var projection = coll.PROJECTION(cursor);
                    }
                """.replace("TYPE", "u8" if projection.startswith("Set") else "i32")
                    .replace("PROJECTION", projection))
                diagnostic = self.process(VAQC, "check", entry, good=False).stderr
                self.assertIn(b"move parameter requires move(object) or an owning temporary", diagnostic)
        after_move = self.source("moved/main.vqr", """
            import (coll)
            func main() !void {
                var table = coll.i32Map<i32>();
                var cursor = table.iterRef()!;
                var keys = coll.Keys<i32, i32>(move(cursor));
                cursor.next();
            }
        """)
        diagnostic = self.process(VAQC, "check", after_move, good=False).stderr
        self.assertIn(b"not initialized", diagnostic)

    def test_vec_splice_and_drain_owned_elements(self):
        output = self.run_container(r"""
            class Tag {
                #copy: disabled
                pub var id: i32;
                func deinit() void { io.println(id) catch {}; }
            }
            func main() !void {
                var source = coll.Vec<Tag>();
                for i in 0..6 { source.push(Tag{id: i})!; }
                var replacement = coll.Vec<Tag>();
                for i in 6..10 { replacement.push(Tag{id: i})!; }
                var removed = source.spliceOwned(1, 3, move(replacement))!;
                var tail = removed.nextBack().unwrap();
                if tail.id != 2 || source.len() != 8 ||
                    source.getPtr(1).unwrap().id != 6 || source.getPtr(5).unwrap().id != 3 {
                    panic("invalid splice");
                }
                var middle = source.drain(2, 6)!;
                var first = middle.next().unwrap();
                if first.id != 7 || source.len() != 4 || source.getPtr(2).unwrap().id != 4 {
                    panic("invalid drain");
                }
            }
        """)
        self.assertEqual(sorted(output), list("0123456789"))

    def test_vecdeque_push_modes_preserve_values_and_drop_once_on_failure(self):
        setup = """
            error Injected { Capacity }
            class Allocator {
                #implements: mem.Allocator
                var backing: mem.GlobalAllocator;
                pub var fail: bool;
                pub var live: usize;
                pub func init() : backing(mem.GlobalAllocator()), fail(false), live(0) {}
                pub func alloc(layout: mem.Layout) core.ResultOptional<*u8> {
                    if fail { return Injected.Capacity; }
                    var out = backing.alloc(layout)!;
                    if out != null { live += layout.size(); }
                    out
                }
                pub unsafe func free(ptr: *u8, layout: mem.Layout) void {
                    live -= layout.size();
                    unsafe { backing.free(ptr, layout); }
                }
            }
            class CopyProbe {
                #copy: enable
                var copies: *i32;
                var drops: *i32;
                pub var id: i32;
                pub func init(copies: *i32, drops: *i32, id: i32) : copies(copies), drops(drops), id(id) {}
                pub const func copy() Self {
                    unsafe { (*copies)++; }
                    CopyProbe(copies, drops, id)
                }
                func deinit() void { unsafe { (*drops)++; } }
            }
            class Owned {
                #copy: disabled
                var drops: *i32;
                pub var id: i32;
                pub func init(drops: *i32, id: i32) : drops(drops), id(id) {}
                func deinit() void { unsafe { (*drops)++; } }
            }
        """
        self.assertEqual(self.run_container(setup + """
            func main() !void {
                var allocator = Allocator();
                var copies = 0;
                var copyDrops = 0;
                var ownerDrops = 0;
                {
                    var back = CopyProbe(raw copies, raw copyDrops, 1);
                    var front = CopyProbe(raw copies, raw copyDrops, 2);
                    var deque = coll.VecDeque<CopyProbe>.withCapacityAndAllocator(2, unsafe {
                        mem.AllocatorRef.fromRaw(raw allocator)
                    })!;
                    deque.pushBack(back)!;
                    deque.pushFront(front)!;
                    if copies != 2 || copyDrops != 0 { panic("copy did not stay at the public boundary"); }
                    allocator.fail = true;
                    switch deque.pushFrontPreserving(front) {
                        coll.PushResult.Failed(reason, item) => {
                            if reason != Injected.Capacity || item.id != 2 || copies != 3 || copyDrops != 0 {
                                panic("front failure did not return the argument copy");
                            }
                        },
                        _ => panic("front growth should fail"),
                    }
                    switch deque.pushBackPreserving(back) {
                        coll.PushResult.Failed(reason, item) => {
                            if reason != Injected.Capacity || item.id != 1 || copies != 4 || copyDrops != 1 {
                                panic("back failure did not return the argument copy");
                            }
                        },
                        _ => panic("back growth should fail"),
                    }
                    deque.pushFront(front) catch (reason) {
                        if reason != Injected.Capacity { return reason; }
                    };
                    deque.pushBack(back) catch (reason) {
                        if reason != Injected.Capacity { return reason; }
                    };
                    if copies != 6 || copyDrops != 4 || deque.len() != 2 ||
                       deque.get(0).unwrap().id != 2 || deque.get(1).unwrap().id != 1 ||
                       front.id != 2 || back.id != 1 { panic("failed pushes changed the ring or consumed a copy source"); }
                    allocator.fail = false;
                    switch deque.pushFrontPreserving(front) {
                        coll.PushResult.Stored => {},
                        _ => panic("front growth should recover"),
                    }
                    switch deque.pushBackPreserving(back) {
                        coll.PushResult.Stored => {},
                        _ => panic("back storage should recover"),
                    }
                    if copies != 8 || copyDrops != 4 || deque.len() != 4 ||
                       deque.get(0).unwrap().id != 2 || deque.get(1).unwrap().id != 2 ||
                       deque.get(2).unwrap().id != 1 || deque.get(3).unwrap().id != 1 {
                        panic("copy path duplicated ownership or lost order during growth");
                    }
                }
                if copyDrops != 10 || allocator.live != 0 { panic("copy path leaked or double-dropped"); }

                {
                    var deque = coll.VecDeque<Owned>.withCapacityAndAllocator(2, unsafe {
                        mem.AllocatorRef.fromRaw(raw allocator)
                    })!;
                    var back = Owned(raw ownerDrops, 10);
                    deque.pushBack(move(back))!;
                    deque.pushFront(Owned(raw ownerDrops, 20))!;
                    allocator.fail = true;
                    var frontRejected = Owned(raw ownerDrops, 30);
                    switch deque.pushFrontPreserving(move(frontRejected)) {
                        coll.PushResult.Failed(reason, item) => {
                            if reason != Injected.Capacity || item.id != 30 || ownerDrops != 0 {
                                panic("front failure did not return precisely the moved owner");
                            }
                        },
                        _ => panic("front growth should fail"),
                    }
                    switch deque.pushBackPreserving(Owned(raw ownerDrops, 40)) {
                        coll.PushResult.Failed(reason, item) => {
                            if reason != Injected.Capacity || item.id != 40 || ownerDrops != 1 {
                                panic("back failure did not return precisely the temporary owner");
                            }
                        },
                        _ => panic("back growth should fail"),
                    }
                    deque.pushBack(Owned(raw ownerDrops, 50)) catch (reason) {
                        if reason != Injected.Capacity { return reason; }
                    };
                    var rejected = Owned(raw ownerDrops, 60);
                    deque.pushFront(move(rejected)) catch (reason) {
                        if reason != Injected.Capacity { return reason; }
                    };
                    if ownerDrops != 4 || deque.len() != 2 ||
                       deque.get(0).unwrap().id != 20 || deque.get(1).unwrap().id != 10 {
                        panic("failed growth altered the ring or mishandled an owner");
                    }
                    allocator.fail = false;
                    switch deque.pushFrontPreserving(Owned(raw ownerDrops, 70)) {
                        coll.PushResult.Stored => {},
                        _ => panic("owning front growth should recover"),
                    }
                    var last = Owned(raw ownerDrops, 80);
                    switch deque.pushBackPreserving(move(last)) {
                        coll.PushResult.Stored => {},
                        _ => panic("owning back storage should recover"),
                    }
                    if deque.len() != 4 || deque.get(0).unwrap().id != 70 ||
                       deque.get(1).unwrap().id != 20 || deque.get(2).unwrap().id != 10 ||
                       deque.get(3).unwrap().id != 80 { panic("recovered ring order is wrong"); }
                }
                if ownerDrops != 8 || allocator.live != 0 { panic("move path leaked or double-dropped"); }
            }
        """), [])
        for operation, post in (("pushBack", "!"), ("pushFront", "!"),
                                ("pushBackPreserving", ""), ("pushFrontPreserving", "")):
            with self.subTest(operation=operation):
                source = self.source(operation + "/main.vqr", "import (coll, core, mem)\n" + setup + """
                    func main() !void {
                        var count = 0;
                        var item = Owned(raw count, 7);
                        var ring = coll.VecDeque<Owned>();
                """ + f"ring.{operation}(item){post};" + "\n}")
                diagnostic = self.process(VAQC, "check", source, good=False).stderr
                self.assertIn(b"move parameter requires move(object) or an owning temporary", diagnostic)

    def test_fixed_insertions_keep_copies_and_failed_owners_distinct(self):
        setup = """
            class Counted {
                #copy: enable
                var copies: *i32;
                var drops: *i32;
                pub var id: i32;
                pub func init(c: *i32, d: *i32, id: i32) : copies(c), drops(d), id(id) {}
                pub const func copy() Self {
                    unsafe { (*copies)++; }
                    Counted(copies, drops, id)
                }
                func deinit() void { unsafe { (*drops)++; } }
            }
            class Owned {
                #copy: disabled
                var drops: *i32;
                pub var id: i32;
                pub func init(d: *i32, id: i32) : drops(d), id(id) {}
                func deinit() void { unsafe { (*drops)++; } }
            }
        """
        self.assertEqual(self.run_container(setup + """
            func main() !void {
                var copies = 0;
                var drops = 0;
                {
                    var one = Counted(raw copies, raw drops, 1);
                    var two = Counted(raw copies, raw drops, 2);
                    var values = coll.FixedVec<Counted, 2>();
                    switch values.push(one) { coll.PushResult.Stored => {}, _ => panic("push failed") }
                    switch values.tryPush(two) { coll.PushResult.Stored => {}, _ => panic("tryPush failed") }
                    if copies != 2 || drops != 0 { panic("extra copies on insertion"); }
                    switch values.tryPush(one) {
                        coll.PushResult.Full(returned) => {
                            if returned.id != 1 || copies != 3 || drops != 0 { panic("wrong rejected copy"); }
                        },
                        _ => panic("tryPush should report full"),
                    }
                    switch values.insert(3, two) {
                        coll.PushResult.Failed(reason, returned) => {
                            if reason != coll.CapacityError.IndexOutOfBounds || returned.id != 2 {
                                panic("invalid index must precede full");
                            }
                        },
                        _ => panic("invalid insert should fail"),
                    }
                    if values.replace(3, one) != null || drops != 3 { panic("invalid replace did not drop its copy"); }
                    switch values.replacePreserving(2, two) {
                        coll.ReplaceResult.InvalidIndex(returned) => {
                            if returned.id != 2 || copies != 6 || drops != 3 { panic("wrong exposed copy"); }
                        },
                        _ => panic("replacePreserving should return its copy"),
                    }
                    switch values.replacePreserving(1, one) {
                        coll.ReplaceResult.Replaced(previous) => {
                            if previous.id != 2 || copies != 7 || drops != 4 { panic("wrong replaced owner"); }
                        },
                        _ => panic("replacement failed"),
                    }
                    {
                        var previous = values.replace(0, two).unwrap();
                        if previous.id != 1 || copies != 8 || drops != 5 { panic("replace made another copy"); }
                    }
                    if values.get(0).unwrap().id != 2 || values.get(1).unwrap().id != 1 ||
                       one.id != 1 || two.id != 2 { panic("wrong final slots or consumed copy source"); }
                }
                if copies != 8 || drops != 10 { panic("fixed vec dropped the wrong number of copies"); }
                {
                    var one = Counted(raw copies, raw drops, 11);
                    var two = Counted(raw copies, raw drops, 12);
                    var ring = coll.FixedDeque<Counted, 3>();
                    switch ring.pushBack(one) { coll.PushResult.Stored => {}, _ => panic("back failed") }
                    switch ring.pushFront(two) { coll.PushResult.Stored => {}, _ => panic("front failed") }
                    switch ring.insert(1, one) { coll.PushResult.Stored => {}, _ => panic("insert failed") }
                    switch ring.pushFront(two) {
                        coll.PushResult.Full(returned) => {
                            if returned.id != 12 || copies != 12 || drops != 10 { panic("wrong full owner"); }
                        },
                        _ => panic("ring should be full"),
                    }
                    {
                        var previous = ring.replace(1, two).unwrap();
                        if previous.id != 11 || copies != 13 || drops != 11 { panic("wrong replacement"); }
                    }
                    if ring.get(0).unwrap().id != 12 || ring.get(1).unwrap().id != 12 ||
                       ring.get(2).unwrap().id != 11 { panic("incorrect ring order"); }
                }
                if copies != 13 || drops != 17 { panic("fixed ring dropped the wrong number of copies"); }

                var owners = 0;
                {
                    var values = coll.FixedVec<Owned, 1>();
                    var first = Owned(raw owners, 21);
                    switch values.push(move(first)) { coll.PushResult.Stored => {}, _ => panic("push failed") }
                    var replacement = Owned(raw owners, 22);
                    switch values.replacePreserving(1, move(replacement)) {
                        coll.ReplaceResult.InvalidIndex(returned) => {
                            if returned.id != 22 || owners != 0 { panic("lost original owner"); }
                        },
                        _ => panic("invalid replacement was accepted"),
                    }
                    var ownedIter = coll.FixedVec<Owned, 1>.intoIter(move(values));
                    var entry = ownedIter.next().unwrap();
                    if entry.id != 21 || owners != 1 || ownedIter.next() != null { panic("wrong owned traversal"); }
                }
                {
                    var ring = coll.FixedDeque<Owned, 0>();
                    var first = Owned(raw owners, 23);
                    switch ring.pushFront(move(first)) {
                        coll.PushResult.Full(returned) => {
                            if returned.id != 23 || owners != 2 || ring.len() != 0 { panic("zero capacity"); }
                        },
                        _ => panic("zero capacity should be full"),
                    }
                }
                {
                    var ring = coll.FixedDeque<Owned, 1>();
                    switch ring.pushBack(Owned(raw owners, 24)) {
                        coll.PushResult.Stored => {},
                        _ => panic("ring push failed"),
                    }
                    var ownedIter = coll.FixedDeque<Owned, 1>.intoIter(move(ring));
                    var entry = ownedIter.nextBack().unwrap();
                    if entry.id != 24 || owners != 3 || ownedIter.nextBack() != null {
                        panic("wrong owned ring traversal");
                    }
                }
                if owners != 4 { panic("lost an owning value or dropped it twice"); }
            }
        """), [])
        for kind, operation in (
            ("FixedVec", "push(item)"), ("FixedVec", "tryPush(item)"),
            ("FixedVec", "insert(0, item)"), ("FixedVec", "replace(0, item)"),
            ("FixedVec", "replacePreserving(0, item)"),
            ("FixedDeque", "pushBack(item)"), ("FixedDeque", "pushFront(item)"),
            ("FixedDeque", "insert(0, item)"), ("FixedDeque", "replace(0, item)"),
        ):
            with self.subTest(container=kind, operation=operation):
                name = kind + "_" + operation.split("(")[0] + "/main.vqr"
                source = self.source(name, "import (coll)\n" + setup + f"""
                    func main() void {{
                        var drops = 0;
                        var item = Owned(raw drops, 31);
                        var value = coll.{kind}<Owned, 1>();
                        value.{operation};
                    }}
                """)
                diagnostic = self.process(VAQC, "check", source, good=False).stderr
                self.assertIn(b"move parameter requires move(object) or an owning temporary", diagnostic)

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
                ring.append(raw suffix)!;
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

    def test_stack_and_queue_preserve_copies_and_moved_values(self):
        self.assertEqual(self.run_container("""
            error Injected { Capacity }
            class Allocator {
                #implements: mem.Allocator
                var backing: mem.GlobalAllocator;
                pub var fail: bool;
                pub var live: usize;
                pub func init() : backing(mem.GlobalAllocator()), fail(false), live(0) {}
                pub func alloc(layout: mem.Layout) core.ResultOptional<*u8> {
                    if fail { return Injected.Capacity; }
                    var address = backing.alloc(layout)!;
                    if address != null { live += layout.size(); }
                    address
                }
                pub unsafe func free(address: *u8, layout: mem.Layout) void {
                    live -= layout.size();
                    unsafe { backing.free(address, layout); }
                }
            }
            class Counted {
                #copy: enable
                var copies: *i32;
                var drops: *i32;
                pub var id: i32;
                pub func init(c: *i32, d: *i32, id: i32) : copies(c), drops(d), id(id) {}
                pub const func copy() Self {
                    unsafe { (*copies)++; }
                    Counted(copies, drops, id)
                }
                func deinit() void { unsafe { (*drops)++; } }
            }
            class Owner {
                #copy: disabled
                var drops: *i32;
                pub var id: i32;
                pub func init(d: *i32, id: i32) : drops(d), id(id) {}
                func deinit() void { unsafe { (*drops)++; } }
            }
            func main() !void {
                var allocator = Allocator();
                var source = unsafe { mem.AllocatorRef.fromRaw(raw allocator) };
                var copies = 0;
                var drops = 0;
                {
                    var token = Counted(raw copies, raw drops, 1);
                    var stack = coll.Stack<Counted>.withCapacityAndAllocator(1, source)!;
                    var queue = coll.Queue<Counted>.withCapacityAndAllocator(1, source)!;
                    stack.push(token)!;
                    queue.enqueue(token)!;
                    if copies != 2 || drops != 0 { panic("adapter recopied the acquired payload"); }
                    allocator.fail = true;
                    switch stack.pushPreserving(token) {
                        coll.PushResult.Failed(reason, received) => {
                            if reason != Injected.Capacity || received.id != 1 ||
                               copies != 3 || drops != 0 { panic("stack did not return its acquired copy"); }
                        },
                        _ => panic("stack growth unexpectedly succeeded"),
                    }
                    switch queue.enqueuePreserving(token) {
                        coll.PushResult.Failed(reason, received) => {
                            if reason != Injected.Capacity || received.id != 1 ||
                               copies != 4 || drops != 1 { panic("queue did not return its acquired copy"); }
                        },
                        _ => panic("queue growth unexpectedly succeeded"),
                    }
                    var fromStack = stack.pop().unwrap();
                    var fromQueue = queue.dequeue().unwrap();
                    if copies != 4 || drops != 2 || fromStack.id != 1 || fromQueue.id != 1 ||
                       token.id != 1 { panic("Copyable payload was lost or recopied"); }
                    allocator.fail = false;
                }
                if drops != 5 || allocator.live != 0 { panic("copy cleanup leaked or dropped twice"); }

                var owners = 0;
                {
                    var stack = coll.Stack<Owner>.withCapacityAndAllocator(1, source)!;
                    var queue = coll.Queue<Owner>.withCapacityAndAllocator(1, source)!;
                    var first = Owner(raw owners, 11);
                    stack.push(move(first))!;
                    queue.enqueue(Owner(raw owners, 12))!;
                    allocator.fail = true;
                    var refused = Owner(raw owners, 13);
                    switch stack.pushPreserving(move(refused)) {
                        coll.PushResult.Failed(reason, received) => {
                            if reason != Injected.Capacity || received.id != 13 ||
                               owners != 0 { panic("stack did not return its original owner"); }
                        },
                        _ => panic("stack growth unexpectedly succeeded"),
                    }
                    switch queue.enqueuePreserving(Owner(raw owners, 14)) {
                        coll.PushResult.Failed(reason, received) => {
                            if reason != Injected.Capacity || received.id != 14 ||
                               owners != 1 { panic("queue did not return its original owner"); }
                        },
                        _ => panic("queue growth unexpectedly succeeded"),
                    }
                    stack.push(Owner(raw owners, 17)) catch (reason) {
                        if reason != Injected.Capacity { return reason; }
                    };
                    var rejected = Owner(raw owners, 18);
                    queue.enqueue(move(rejected)) catch (reason) {
                        if reason != Injected.Capacity { return reason; }
                    };
                    if owners != 4 { panic("nonpreserving failure did not drop its input exactly once"); }
                    allocator.fail = false;
                    stack.push(Owner(raw owners, 15))!;
                    var next = Owner(raw owners, 16);
                    queue.enqueue(move(next))!;
                    var top = stack.pop().unwrap();
                    var base = stack.pop().unwrap();
                    var head = queue.dequeue().unwrap();
                    var tail = queue.dequeue().unwrap();
                    if top.id != 15 || base.id != 11 || head.id != 12 || tail.id != 16 ||
                       owners != 4 { panic("failed growth damaged order or ownership"); }
                }
                if owners != 8 || allocator.live != 0 { panic("move cleanup leaked or dropped twice"); }
            }
        """), [])
        for kind, operation in (
            ("Stack", "push"), ("Stack", "pushPreserving"),
            ("Queue", "enqueue"), ("Queue", "enqueuePreserving"),
        ):
            with self.subTest(container=kind, operation=operation):
                source = self.source(operation + "/main.vqr", f"""
                    import (coll)
                    class Owned {{ #copy: disabled; pub var id: i32; }}
                    func f() void {{
                        var container = coll.{kind}<Owned>();
                        var owner = Owned{{id: 3}};
                        container.{operation}(owner);
                    }}
                """)
                diagnostic = self.process(VAQC, "check", source, good=False).stderr
                self.assertIn(b"move parameter requires move(object) or an owning temporary", diagnostic)

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
                list.append(raw tail)!;
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

    def test_heap_iterator_copy_and_ownership_boundary(self):
        self.assertEqual(self.run_container("""
            class Cursor {
                #copy: enable
                var copies: *i32;
                var current: i32;
                pub func init(copies: *i32, at: i32) : copies(copies), current(at) {}
                pub const func copy() Self {
                    (*copies)++;
                    Cursor(copies, current)
                }
                pub func next() ?i32 {
                    if current == 0 { return null; }
                    var currentValue = current;
                    current--;
                    some(currentValue)
                }
            }
            class Owner {
                #copy: disabled
                pub var id: i32;
                var drops: *i32;
                pub func init(id: i32, drops: *i32) : id(id), drops(drops) {}
                func deinit() void { (*drops)++; }
            }
            func lessInt(a, b: *const i32) bool { *a < *b }
            func lessOwner(a, b: *const Owner) bool { a.id < b.id }
            func main() !void {
                var copies = 0;
                var cursor = Cursor(raw copies, 4);
                if cursor.next().unwrap() != 4 { panic("source cursor initialization"); }
                var heap = coll.PriorityQueue<i32>.fromIter(cursor, lessInt, coll.HeapOrder.Min)!;
                if cursor.next().unwrap() != 3 || copies != 1 ||
                   heap.pop().unwrap() != 1 || heap.pop().unwrap() != 2 ||
                   heap.pop().unwrap() != 3 || !heap.isEmpty() {
                    panic("fromIter lost cursor position, moved caller, or recopied");
                }

                var drops = 0;
                {
                    var values = coll.Vec<Owner>();
                    for rank in 1..5 { values.push(Owner(rank, raw drops))!; }
                    var iterator = coll.Vec<Owner>.intoIter(move(values));
                    {
                        var alreadyTaken = iterator.next().unwrap();
                        if alreadyTaken.id != 1 { panic("wrong prior cursor position"); }
                    }
                    var owned = coll.BinaryHeap<Owner>.fromOwnedIter(move(iterator), lessOwner, coll.HeapOrder.Max)!;
                    if drops != 1 || owned.len() != 3 { panic("iterator prematurely discarded payload"); }
                    for rank in 2..5 {
                        var element = owned.pop().unwrap();
                        if element.id != 6 - rank { panic("wrong transferred priority"); }
                    }
                }
                if drops != 4 { panic("iterator-finished cleanup was not exact"); }
                {
                    var values = coll.Vec<Owner>();
                    values.push(Owner(99, raw drops))!;
                    var owned = coll.BinaryHeap<Owner>.fromOwnedIter<coll.IntoVecIter<Owner>>(
                        coll.Vec<Owner>.intoIter(move(values)), lessOwner, coll.HeapOrder.Min)!;
                    if owned.peek().unwrap().id != 99 { panic("owning iterator temporary lost"); }
                }
                if drops != 5 { panic("owning iterator temporary cleanup was not exact"); }
            }
        """), [])
        for expression, diagnostic in (
            ("coll.BinaryHeap<i32>.fromIter(it, less, coll.HeapOrder.Min)", b"no accessible overload"),
            ("coll.BinaryHeap<i32>.fromOwnedIter(it, less, coll.HeapOrder.Min)",
             b"move parameter requires move(object) or an owning temporary"),
            ("coll.BinaryHeap<i32>.fromOwnedIter(move(copyable), less, coll.HeapOrder.Min)",
             b"move parameter requires an owning, movable, non-Copyable type"),
        ):
            with self.subTest(expression=expression):
                source = self.source("heap-iter/main.vqr", """
                    import (coll, iter)
                    func less(a, b: *const i32) bool { *a < *b }
                    func main() void {
                        var values = coll.Vec<i32>();
                        var it = coll.Vec<i32>.intoIter(move(values));
                        var copyable = iter.OnceIter<i32>(3);
                """ + expression + """
                    }
                """)
                self.assertIn(diagnostic, self.process(VAQC, "check", source, good=False).stderr)

    def test_heap_value_acquisition_and_failed_growth(self):
        self.assertEqual(self.run_container("""
            error Injected { Capacity }
            class Allocator {
                #implements: mem.Allocator
                var backing: mem.GlobalAllocator;
                pub var fail: bool;
                pub var live: usize;
                pub func init() : backing(mem.GlobalAllocator()), fail(false), live(0) {}
                pub func alloc(layout: mem.Layout) core.ResultOptional<*u8> {
                    if fail { return Injected.Capacity; }
                    var address = backing.alloc(layout)!;
                    if address != null { live += layout.size(); }
                    address
                }
                pub unsafe func free(address: *u8, layout: mem.Layout) void {
                    live -= layout.size();
                    unsafe { backing.free(address, layout); }
                }
            }
            class Counted {
                #copy: enable
                var copies: *i32;
                var drops: *i32;
                pub var id: i32;
                pub func init(c: *i32, d: *i32, id: i32) : copies(c), drops(d), id(id) {}
                pub const func copy() Self {
                    (*copies)++;
                    Counted(copies, drops, id)
                }
                func deinit() void { (*drops)++; }
            }
            class Owner {
                #copy: disabled
                var drops: *i32;
                pub var id: i32;
                pub func init(d: *i32, id: i32) : drops(d), id(id) {}
                func deinit() void { (*drops)++; }
            }
            func lessCounted(a, b: *const Counted) bool { a.id < b.id }
            func lessOwner(a, b: *const Owner) bool { a.id < b.id }
            func main() !void {
                var allocator = Allocator();
                var backing = unsafe { mem.AllocatorRef.fromRaw(raw allocator) };
                var copies = 0;
                var drops = 0;
                {
                    var first = Counted(raw copies, raw drops, 5);
                    var later = Counted(raw copies, raw drops, 20);
                    var heap = coll.BinaryHeap<Counted>.withCapacityAndAllocator(
                        1, lessCounted, coll.HeapOrder.Min, backing)!;
                    heap.push(first)!;
                    if copies != 1 || drops != 0 { panic("push did not acquire one copy"); }
                    allocator.fail = true;
                    switch heap.pushPreserving(later) {
                        coll.PushResult.Failed(reason, returned) => {
                            if reason != Injected.Capacity || returned.id != 20 || copies != 2 || drops != 0 {
                                panic("preserving failure lost the acquired copy");
                            }
                        },
                        _ => panic("heap growth unexpectedly succeeded"),
                    }
                    var firstReturn = heap.pushPop(later);
                    var secondReturn = heap.replaceTop(first)!.unwrap();
                    if firstReturn.id != 5 || secondReturn.id != 20 || heap.peek().unwrap().id != 5 ||
                       later.id != 20 || first.id != 5 || copies != 4 || drops != 1 {
                        panic("combined heap operations did not transfer their acquired copy exactly once");
                    }
                }
                if drops != 6 || allocator.live != 0 { panic("Copyable heap cleanup is not exact"); }

                var owners = 0;
                allocator.fail = false;
                {
                    var heap = coll.PriorityQueue<Owner>.withCapacityAndAllocator(
                        1, lessOwner, coll.HeapOrder.Max, backing)!;
                    var original = Owner(raw owners, 10);
                    heap.push(move(original))!;
                    allocator.fail = true;
                    var rejected = Owner(raw owners, 30);
                    switch heap.pushPreserving(move(rejected)) {
                        coll.PushResult.Failed(reason, returned) => {
                            if reason != Injected.Capacity || returned.id != 30 || owners != 0 {
                                panic("preserving failure lost the original owner");
                            }
                        },
                        _ => panic("heap growth unexpectedly succeeded"),
                    }
                    heap.push(Owner(raw owners, 19)) catch (reason) {
                        if reason != Injected.Capacity { return reason; }
                    };
                    if owners != 2 || heap.peek().unwrap().id != 10 { panic("nonpreserving cleanup damaged heap"); }

                    var higher = Owner(raw owners, 40);
                    var untouched = heap.pushPop(move(higher));
                    var previous = heap.pushPop(Owner(raw owners, 1));
                    var lowest = Owner(raw owners, -5);
                    var replaced = heap.replaceTop(move(lowest))!.unwrap();
                    if untouched.id != 40 || previous.id != 10 || replaced.id != 1 ||
                       heap.peek().unwrap().id != -5 || owners != 2 {
                        panic("max heap did not preserve exact owners");
                    }
                    allocator.fail = false;
                    var next = Owner(raw owners, 50);
                    switch heap.pushPreserving(move(next)) {
                        coll.PushResult.Stored => {},
                        _ => panic("growth did not recover"),
                    }
                    var restoredTop = heap.pop().unwrap();
                    if restoredTop.id != 50 || heap.peek().unwrap().id != -5 || owners != 2 {
                        panic("preserving success did not sift the new owner");
                    }
                }
                if owners != 7 || allocator.live != 0 { panic("owned heap cleanup is not exact"); }
            }
        """), [])
        for operation in ("push", "pushPreserving", "replaceTop", "pushPop"):
            with self.subTest(operation=operation):
                source = self.source(operation + "/main.vqr", f"""
                    import (coll)
                    class Owner {{ #copy: disabled; pub var id: i32; }}
                    func less(a, b: *const Owner) bool {{ a.id < b.id }}
                    func main() void {{
                        var heap = coll.BinaryHeap<Owner>.withMinComparator(less);
                        var value = Owner{{id: 1}};
                        heap.{operation}(value);
                    }}
                """)
                self.assertIn(b"move parameter requires move(object) or an owning temporary",
                              self.process(VAQC, "check", source, good=False).stderr)

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
                    var taken = map.remove(id);
                    var value = move(taken).unwrap();
                    if value.id != id { panic("wrong removal"); }
                }
                for id in 41..81 {
                    if map.get(id).unwrap().id != id { panic("probe stopped at tombstone"); }
                }
                for id in 81..121 { var old = map.insert(id, Probe{id: id})!; }
                var key = 55;
                var replacement = map.insert(key, Probe{id: 121})!;
                var old = move(replacement).unwrap();
                if old.id != 55 || map.get(key).unwrap().id != 121 { panic("replace failed"); }
                var iterator = map.iterRef()!;
                var count = 0;
                for entry in move(iterator) { count++; }
                if count != 80 { panic("map iterator count"); }
                var set = coll.HashSet<i32>.withHasher(collision, hash.eqI32);
                if !set.insert(key)! || set.insert(key)! || !set.remove(key) || set.contains(key) {
                    panic("set uniqueness");
                }
                if hash.hashBytes("hello".asBytes()) != usize(1335831723) { panic("FNV-1a"); }
                var signed: i16 = -1;
                var unsigned = 65535u16;
                if hash.hashI16(raw signed, 17) != hash.hashU16(raw unsigned, 17) {
                    panic("16-bit byte order or signed representation");
                }
                var first = Key{tag: 61};
                var other = Key{tag: 61};
                var bytes = [1]u8{61};
                if !hash.equalKey<Key>(raw first, raw other) ||
                   hash.hashValue<Key>(raw first) != hash.hashBytes(bytes[:]) {
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
                if *tree.lowerBound(raw k).unwrap() != 65 || *tree.upperBound(raw k).unwrap() != 66 {
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
                    var picked = tree.remove(id);
                    var item = move(picked).unwrap();
                    if item.id != id || tree.containsKey(id) { panic("incorrect removal"); }
                }
                if !tree.isEmpty() { panic("root did not contract"); }
                var set = coll.BTreeSet<i32>.withComparator(less);
                if !set.insert(k)! || set.insert(k)! || !set.remove(k) || set.contains(k) {
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
                    var borrowed = tree.lowerBound(raw query).unwrap();
                    if borrowed.id != id { panic("borrowed key lookup"); }
                    var entry = tree.removeEntry(query).unwrap();
                    if entry.0.id != id || entry.1 != id || tree.containsKey(query) {
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

    def test_btree_insert_selects_independent_key_and_value_modes(self):
        setup = """
            class Owned {
                #copy: disabled
                pub var id: i32;
                pub var label: i32;
                pub func init(id: i32, label: i32) : id(id), label(label) {}
                func deinit() void { io.println(label) catch {}; }
            }
            func lessInt(a, b: *const i32) bool { *a < *b }
            func lessOwned(a, b: *const Owned) bool { a.id < b.id }
        """
        output = self.run_container(setup + """
            func main() !void {
                var key = 1;
                var value = 7;
                var integers = coll.BTreeMap<i32, i32>.withComparator(lessInt);
                integers.insert(key, value)!;
                if integers.insert(key, value + 1)!.unwrap() != value || key != 1 {
                    panic("wrong copied entry");
                }

                var values = coll.BTreeMap<i32, Owned>.withComparator(lessInt);
                var ownedValue = Owned(1, 21);
                values.insert(key, move(ownedValue))!;
                var previous = values.insert(key, Owned(1, 22))!.unwrap();
                if previous.label != 21 || values.get(key).unwrap().label != 22 { panic("wrong owned value"); }

                var keys = coll.BTreeMap<Owned, i32>.withComparator(lessOwned);
                var ownedKey = Owned(1, 31);
                keys.insert(move(ownedKey), value)!;
                if keys.insert(Owned(1, 32), value + 1)!.unwrap() != value { panic("wrong owned key"); }

                var both = coll.BTreeMap<Owned, Owned>.withComparator(lessOwned);
                var pairKey = Owned(1, 41);
                var pairValue = Owned(1, 51);
                both.insert(move(pairKey), move(pairValue))!;
                var old = both.insert(Owned(1, 42), Owned(1, 52))!.unwrap();
                if old.label != 51 || both.len() != 1 { panic("wrong previous owner"); }
            }
        """)
        self.assertEqual(sorted(output), ["21", "22", "31", "32", "41", "42", "51", "52"])
        for statement in (
            "values.insert(1, value)!;",
            "keys.insert(key, 7)!;",
            "both.insert(move(key), value)!;",
            "both.insert(key, move(value))!;",
            "set.insert(key)!;",
            "fixed.insert(0, value);",
        ):
            with self.subTest(statement=statement):
                source = self.source("missing-move.vqr", "import (coll, io)\n" + setup + """
                    func main() !void {
                        var values = coll.BTreeMap<i32, Owned>.withComparator(lessInt);
                        var keys = coll.BTreeMap<Owned, i32>.withComparator(lessOwned);
                        var both = coll.BTreeMap<Owned, Owned>.withComparator(lessOwned);
                        var set = coll.BTreeSet<Owned>.withComparator(lessOwned);
                        var fixed = coll.FixedVec<Owned, 2>();
                        var key = Owned(1, 11);
                        var value = Owned(1, 12);
                """ + statement + "\n}")
                diagnostic = self.process(VAQC, "check", source, good=False).stderr
                self.assertIn(b"move parameter requires move(object)", diagnostic)

    def test_allocator_failure_preserves_value_and_layout(self):
        self.assertEqual(self.run_container('''
            error Injected { Exhausted }
            class Allocator {
                #implements: mem.Allocator
                var owner: mem.GlobalAllocator;
                pub var fail: bool;
                pub var live: usize;
                pub func init() : owner(mem.GlobalAllocator()), fail(false), live(0) {}
                pub func alloc(layout: mem.Layout) core.ResultOptional<*u8> {
                    if fail { return Injected.Exhausted; }
                    var allocation = owner.alloc(layout)!;
                    if allocation != null { live += layout.size(); }
                    allocation
                }
                pub unsafe func free(ptr: *u8, layout: mem.Layout) void {
                    live -= layout.size();
                    unsafe { owner.free(ptr, layout); }
                }
            }
            class Probe { #copy: disabled; pub var id: i32; }
            func hashProbe(key: *const Probe, seed: u32) usize { hash.hashI32(raw key.id, seed) }
            func equalProbe(a: *const Probe, b: *const Probe) bool { a.id == b.id }
            func main() !void {
                var allocator = Allocator();
                {
                    var values = coll.Vec<Probe>.withCapacityAndAllocator(1, unsafe { mem.AllocatorRef.fromRaw(raw allocator) })!;
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
                    switch values.insertPreserving(0, Probe{id: 18}) {
                        coll.PushResult.Failed(reason, item) => {
                            if reason != Injected.Exhausted || item.id != 18 || values.len() != 1 {
                                panic("failed insertion did not preserve the initialized prefix");
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
                    var deque = coll.VecDeque<Probe>.withAllocator(unsafe { mem.AllocatorRef.fromRaw(raw allocator) });
                    deque.reserve(1)!;
                    deque.pushBack(Probe{id: 31})!;
                    allocator.fail = true;
                    switch deque.pushBackPreserving(Probe{id: 32}) {
                        coll.PushResult.Failed(reason, item) => {
                            if reason != Injected.Exhausted || item.id != 32 ||
                               deque.len() != 1 || deque.get(0).unwrap().id != 31 {
                                panic("failed deque growth lost an initialized slot or its input");
                            }
                        },
                        _ => panic("failed deque growth unexpectedly stored the input"),
                    }
                    allocator.fail = false;
                    deque.pushBack(Probe{id: 33})!;
                    if deque.popFront().unwrap().id != 31 || deque.popFront().unwrap().id != 33 {
                        panic("recovering deque growth changed slot order");
                    }
                }
                if allocator.live != 0 { panic("deque growth leaked storage"); }
                {
                    var map = coll.HashMap<i32, Probe>.withAllocator(hash.hashI32, hash.eqI32, unsafe { mem.AllocatorRef.fromRaw(raw allocator) });
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
                    if !rejected || map.len() != 1 || map.get(key).unwrap().id != 3 {
                        panic("failed rehash lost entry");
                    }
                    {
                        var hit = map.getOrInsert(1, Probe{id: 4})!;
                        if hit.id != 3 { panic("getOrInsert replaced a hit"); }
                    }
                    for id in 2..8 {
                        allocator.fail = false;
                        var item = map.getOrInsert(id, Probe{id: id + 10})!;
                        if item.id != id + 10 { panic("getOrInsert returned the wrong insertion"); }
                    }
                    allocator.fail = true;
                    var insertionFailed = false;
                    for i in 0..1 {
                        map.getOrInsert(8, Probe{id: 18}) catch (reason) {
                            if reason != Injected.Exhausted { return reason; }
                            insertionFailed = true;
                            break;
                        };
                    }
                    if !insertionFailed || map.len() != 7 || map.get(key).unwrap().id != 3 {
                        panic("getOrInsert failed growth changed the map");
                    }
                    allocator.fail = false;
                }
                if allocator.live != 0 { panic("map allocation/free layouts differed"); }
                {
                    var map = coll.HashMap<Probe, Probe>.withAllocator(hashProbe, equalProbe, unsafe { mem.AllocatorRef.fromRaw(raw allocator) });
                    allocator.fail = true;
                    var key = Probe{id: 71};
                    var value = Probe{id: 81};
                    switch map.insertPreserving(move(key), move(value)) {
                        coll.InsertResult.Failed(reason, recoveredKey, recoveredValue) => {
                            if reason != Injected.Exhausted || recoveredKey.id != 71 ||
                               recoveredValue.id != 81 || map.len() != 0 {
                                panic("map did not return both moved owners");
                            }
                        },
                        _ => panic("map accepted owners without storage"),
                    }
                    allocator.fail = false;
                }
                if allocator.live != 0 { panic("failed move-key insertion leaked storage"); }
            }
        '''), [])

    def test_btree_root_split_allocation_failure_keeps_old_tree(self):
        source = self.source("oom.vqr", '''
            import (coll, mem)
            extern "C" func fail_second_allocation() void;
            extern "C" func record_owned_drop(id: c_int) void;
            extern "C" func owned_drops(id: c_int) c_int;
            func less(a, b: *const i32) bool { *a < *b }
            class Owned {
                #copy: disabled
                pub var id: i32;
                pub func init(id: i32) : id(id) {}
                func deinit() void { unsafe { record_owned_drop(id); } }
            }
            func lessOwned(a, b: *const Owned) bool { a.id < b.id }
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
                    if *tree.get(key).unwrap() != key * 3 { return 2; }
                }
                var after = tree.insert(7, 21)!;
                if after != null || tree.len() != 8 { return 3; }
                0
            }
            export "C" func exercise() c_int { try_insert() catch 4 }
            func try_owned() !i32 {
                var tree = coll.BTreeMap<Owned, Owned>.withComparator(lessOwned);
                for key in 0..7 { tree.insert(Owned(key), Owned(key + 10))!; }
                var key = Owned(7);
                var value = Owned(17);
                unsafe { fail_second_allocation(); }
                var rejected = false;
                tree.insert(move(key), move(value)) catch (reason) {
                    if reason != mem.AllocationError.OutOfMemory { return reason; }
                    rejected = true;
                    null
                };
                if !rejected || tree.len() != 7 || unsafe { owned_drops(7) } != 1 ||
                   unsafe { owned_drops(17) } != 1 || unsafe { owned_drops(0) } != 0 { return 5; }
                0
            }
            export "C" func exercise_owned() c_int { try_owned() catch 6 }
        ''')
        client = self.source("oom.c", '''
            #include <stddef.h>
            extern void *__real_malloc(size_t);
            extern void *__real_aligned_alloc(size_t, size_t);
            extern int exercise(void);
            extern int exercise_owned(void);
            static int countdown;
            static int drops[18];
            void record_owned_drop(int id) { ++drops[id]; }
            int owned_drops(int id) { return drops[id]; }
            void fail_second_allocation(void) { countdown = 2; }
            void *__wrap_malloc(size_t bytes) {
                if (countdown && --countdown == 0) return NULL;
                return __real_malloc(bytes);
            }
            void *__wrap_aligned_alloc(size_t alignment, size_t bytes) {
                if (countdown && --countdown == 0) return NULL;
                return __real_aligned_alloc(alignment, bytes);
            }
            int main(void) {
                int result = exercise();
                if (result) return result;
                result = exercise_owned();
                if (result) return result;
                for (int i = 0; i < 8; ++i)
                    if (drops[i] != 1 || drops[i + 10] != 1) return 7;
                return 0;
            }
        ''')
        self.process(VAQC, source, "-O2", "--emit=obj", "-o", "tree.o")
        self.process("cc", client, "tree.o", "-Wl,--wrap=malloc,--wrap=aligned_alloc", "-o", "tree")
        self.process("./tree")


if __name__ == "__main__":
    unittest.main()
