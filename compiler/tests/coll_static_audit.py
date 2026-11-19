#!/usr/bin/env python3
"""Static source audit for the Vaqrin coll/hash container branch.

This script checks repository layout and source invariants only. It does not
parse or type-check Vaqrin, invoke vaqc/LLVM, or replace runtime acceptance.
Run from any directory with: python3 compiler/tests/coll_static_audit.py
"""

from __future__ import annotations

import re
import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    "stdlib/coll/vec.vqr",
    "stdlib/coll/fixed.vqr",
    "stdlib/coll/deque.vqr",
    "stdlib/coll/linked_list.vqr",
    "stdlib/coll/adapters.vqr",
    "stdlib/coll/hash_map.vqr",
    "stdlib/coll/btree.vqr",
    "stdlib/hash/hash.vqr",
)
ALLOWED_CHANGED = (
    "stdlib/coll/",
    "stdlib/hash/",
    "stdlib/README.md",
    "docs/stdlib/",
    "compiler/tests/coll_static_audit.py",
)
FORBIDDEN_CHANGED = (
    "stdlib/io/",
    "stdlib/fs/",
    "stdlib/path/",
    "stdlib/bufio/",
    "stdlib/fmt/",
    "stdlib/bytes/",
    "compiler/src/",
    "compiler/tests/acceptance.py",
    "CMakeLists.txt",
)


def check(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def balanced_delimiters(source: str) -> bool:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    state = "code"
    quote = ""
    escaped = False
    index = 0

    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""

        if state == "line":
            if char == "\n":
                state = "code"
            index += 1
            continue
        if state == "block":
            if char == "*" and following == "/":
                state = "code"
                index += 2
            else:
                index += 1
            continue
        if state == "string":
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                state = "code"
            index += 1
            continue

        if char == "/" and following == "/":
            state = "line"
            index += 2
            continue
        if char == "/" and following == "*":
            state = "block"
            index += 2
            continue
        if char in ("'", '"'):
            state = "string"
            quote = char
            index += 1
            continue
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack or stack.pop() != pairs[char]:
                return False
        index += 1

    return not stack and state not in ("block", "string")


def source_imports(source: str) -> list[str]:
    imported: list[str] = []
    for match in re.finditer(r"^\s*import\s*\(([^)]*)\)", source, re.MULTILINE):
        for item in match.group(1).split(","):
            item = item.strip()
            if not item:
                continue
            imported.append(item.split(" as ", 1)[0].strip())
    return imported


def changed_paths(scope: tuple[str, str], errors: list[str]) -> None:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{scope[0]}...{scope[1]}", "--"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        errors.append(f"cannot inspect container branch scope: {result.stderr.strip()}")
        return

    for path in filter(None, result.stdout.splitlines()):
        if path.startswith(FORBIDDEN_CHANGED):
            errors.append(f"forbidden path changed: {path}")
        elif not path.startswith(ALLOWED_CHANGED):
            errors.append(f"path outside container task scope: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", nargs=2, metavar=("BASE", "TIP"),
                        help="also audit the original container branch's Git range")
    args = parser.parse_args()
    errors: list[str] = []
    loaded: dict[str, str] = {}

    for relative in SOURCES:
        path = ROOT / relative
        check(path.is_file(), f"missing source: {relative}", errors)
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        loaded[relative] = source
        check(balanced_delimiters(source), f"unbalanced delimiters: {relative}", errors)

        package = re.search(r"^package\s+(\w+)\s*$", source, re.MULTILINE)
        expected = Path(relative).parts[1]
        check(package is not None and package.group(1) == expected,
              f"wrong package declaration: {relative}", errors)

        for imported in source_imports(source):
            check((ROOT / "stdlib" / imported).is_dir(),
                  f"{relative} imports missing package {imported}", errors)

    vec = loaded.get("stdlib/coll/vec.vqr", "")
    fixed = loaded.get("stdlib/coll/fixed.vqr", "")
    deque = loaded.get("stdlib/coll/deque.vqr", "")
    linked = loaded.get("stdlib/coll/linked_list.vqr", "")
    adapters = loaded.get("stdlib/coll/adapters.vqr", "")
    hash_map = loaded.get("stdlib/coll/hash_map.vqr", "")
    btree = loaded.get("stdlib/coll/btree.vqr", "")
    hash_source = loaded.get("stdlib/hash/hash.vqr", "")

    check("func allocationLayout(capacity: usize) !mem.Layout" in vec,
          "Vec lacks checked allocation layout helper", errors)
    check("owner.free(bytes, layout)" in vec and "freeBuffer(previous, reserved)" in vec,
          "Vec allocation/free paths do not share the same allocator layout", errors)
    check("func reserveAdditional(extra: usize) !void" in vec,
          "Vec lacks checked additional-capacity reservation", errors)
    check("PushResult<T>.Full(move(value))" in fixed,
          "FixedVec does not preserve values on capacity exhaustion", errors)
    check("func splitOff(index: usize) !Self" in fixed,
          "FixedVec splitOff is missing", errors)

    check("if cap == 0 { return 0; }" in deque,
          "deque physical-index mapping lacks zero-capacity guard", errors)
    check("core.swap<T>(getMut(a).unwrap(), getMut(b).unwrap())" in deque,
          "deque swap must address initialized logical elements", errors)
    check("if N == 0 { return 0; }" in deque,
          "FixedDeque physical-index mapping lacks N=0 guard", errors)
    check("func append(other: *Self) !void" in deque and
          "func splitOff(index: usize) !Self" in deque,
          "FixedDeque transfer operations are missing", errors)

    check("ownerIndex: usize" in linked and "nodes: Vec<^ListNode<T>>" in linked,
          "LinkedList lacks explicit Box ownership tracking", errors)
    check("(*nodes.getMut(ownerIndex).unwrap()).asPtr().ownerIndex = ownerIndex" in linked,
          "LinkedList swap-removal does not repair the moved node index", errors)
    check("nodes.append(&other.nodes)!" in linked,
          "LinkedList append does not transfer Box ownership", errors)

    check("var less: func(*const T, *const T) bool" in adapters,
          "BinaryHeap lacks a stored comparator", errors)
    check("HeapOrder.Min" in adapters and "HeapOrder.Max" in adapters,
          "BinaryHeap lacks min/max ordering", errors)
    check("func pushPop(value: T) T" in adapters,
          "BinaryHeap pushPop is missing", errors)

    check(re.search(r"^class HashBucket<", hash_map, re.MULTILINE) is not None and
          "pub class HashBucket" not in hash_map,
          "HashMap bucket storage must remain package-private", errors)
    check("const func iterRef() !MapIterRef<K, V>" in hash_map,
          "HashMap iterator must report pointer-index allocation failure", errors)
    check("state = BucketState.Empty;" in hash_map,
          "HashMap clear path does not restore Empty bucket state", errors)
    check("core.swap<Vec<HashBucket<K, V>>>(&buckets, &next);" in hash_map,
          "HashMap rehash does not swap to the fully allocated table", errors)
    check("for i in usize(0)..next.len()" in hash_map,
          "HashMap rehash does not walk the old table after swap", errors)
    check("HashKey" not in hash_map,
          "HashMap must not require implicit primitive HashKey conformance", errors)

    check(re.search(r"^class BTreeNode<", btree, re.MULTILINE) is not None and
          "pub class BTreeNode" not in btree,
          "B-tree node storage must remain package-private", errors)
    check("btreeMinimumDegree: usize = 4" in btree and
          "btreeMaximumEntries: usize = 7" in btree,
          "B-tree degree/capacity invariants are missing", errors)
    check("func splitChild(" in btree and "func mergeChildren(" in btree and
          "func borrowFromPrevious(" in btree and "func borrowFromNext(" in btree,
          "B-tree split/merge/borrow operations are incomplete", errors)
    check("contractRoot();" in btree and "func lowerBound(" in btree and
          "func upperBound(" in btree,
          "B-tree root contraction or bound lookup is missing", errors)
    check("compareTotal" not in btree and "cmp." not in btree,
          "B-tree must use its explicit comparator callback", errors)

    check("pub interface Hasher" in hash_source and "pub class FnvHasher" in hash_source,
          "hash package lacks Hasher/FnvHasher", errors)
    check("hashI32" in hash_source and "eqI32" in hash_source,
          "hash package lacks primitive integer callbacks", errors)

    if args.scope:
        changed_paths(tuple(args.scope), errors)

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1

    print(f"Static source audit passed for {len(loaded)} Vaqrin source files.")
    print("This is not a Vaqrin compile or runtime acceptance result.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
