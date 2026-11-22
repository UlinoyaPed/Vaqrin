"""Exercise the installed editor server's JSON-RPC entry point."""
import json
import os
import subprocess
import sys
import unittest

from acceptance import CompilerCase, REPOSITORY, VAQC


class EditorAcceptance(CompilerCase):
    def test_formatter_and_syntax_only(self):
        source = self.source("tooling.vqr", "import (unavailable)\nfunc main() i32 {\n\treturn 0;  \n}\n")
        form = "import (unavailable)\nfunc main() i32 {\n    return 0;\n}\n"
        self.assertEqual(self.process(VAQC, "fmt", source).stdout.decode(), form)
        self.process(VAQC, "fmt", "--check", source, good=False)
        self.process(VAQC, "fmt", "--write", source)
        self.assertEqual(source.read_text(encoding="utf-8"), form)
        self.process(VAQC, "fmt", "--check", source)
        self.process(VAQC, "check", "--syntax-only", source)
        self.process(VAQC, "check", source, good=False)
        source.write_text('func f() void { var text = "📚"; @ }', encoding="utf-8")
        failure = self.process(VAQC, "check", "--syntax-only", "--diagnostics=json",
                               source, good=False)
        diagnostic = json.loads(failure.stderr)
        self.assertTrue(diagnostic["code"].startswith("E1"))
        self.assertEqual(diagnostic["path"], str(source))
        self.assertEqual(diagnostic["range"]["start"]["character"],
                         len('func f() void { var text = "📚"; '.encode("utf-16-le")) // 2)

    def test_formatter_preserves_expression_roles(self):
        source = self.source("style.vqr", """func main() i32 {
var n=1+2*3;
var b=n<12&&n>0;
n+=4;
var value = target("first thirty-character argument here", "the second argument makes this call longer still", negative(-n));
var selected=generic<i32>(1,2);
n
}
""")
        expected = """func main() i32 {
    var n = 1 + 2 * 3;
    var b = n < 12 && n > 0;
    n += 4;
    var value = target(
        "first thirty-character argument here",
        "the second argument makes this call longer still",
        negative(-n)
    );
    var selected = generic<i32>(1,2);
    n
}
"""
        self.assertEqual(self.process(VAQC, "fmt", source).stdout.decode(), expected)
        source.write_text(expected, encoding="utf-8")
        self.process(VAQC, "fmt", "--check", source)
        self.process(VAQC, "check", "--syntax-only", source)

    def test_multi_file_overlays_and_compiler_local_symbols(self):
        entry = self.source("app/main.vqr", "func main() void {}")
        helper = self.root / "app/helper.vqr"
        content = ("func main() i32 {\n"
                   '    /* 📚 */ var n = helper.read();\n'
                   "    var call = [n]() i32 => n;\n"
                   "    call()\n"
                   "}\n")
        edited = self.source("buffers/main.vqr", content)
        new_helper = self.source("buffers/helper.vqr", "pub func read() i32 { 42 }")
        overlays = self.source("overlays.json", json.dumps({str(entry): str(edited), str(helper): str(new_helper)}))
        result = self.process(VAQC, "check", "--editor-symbols", "--overlay-map", overlays, entry)
        symbols = json.loads(result.stdout)
        data = content.encode()
        binding = data.index(b"n =")
        points = [item for item in symbols if item["declaration"] == binding]
        self.assertEqual(len(points), 3)
        self.assertEqual(sum(item["definition"] for item in points), 1)
        self.assertTrue(all(data[item["begin"]:item["end"]] == b"n" for item in points))
        self.assertFalse(helper.exists())

    def test_editor_features_on_unsaved_source(self):
        original = self.source("main.vqr", "func f() void {}\n")
        source = "import (parse as reader)\nfunc f() void {\n    var value = 10;\n    reader.\n}\n"
        uri = original.as_uri()
        with subprocess.Popen(
            [sys.executable, REPOSITORY / "editors/zed/src/server.py", REPOSITORY],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, "VAQC": str(VAQC)},
        ) as server:
            self.addCleanup(lambda: server.poll() is None and server.kill())

            def send(method, params=None, identifier=None):
                message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
                if identifier is not None:
                    message["id"] = identifier
                body = json.dumps(message).encode()
                server.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
                server.stdin.flush()

            def receive():
                header = server.stdout.readline()
                self.assertTrue(header)
                size = int(header.split(b":", 1)[1])
                self.assertEqual(server.stdout.readline(), b"\r\n")
                return json.loads(server.stdout.read(size))

            send("initialize", {"capabilities": {"textDocument": {"completion": {
                "completionItem": {"snippetSupport": True}}}}}, identifier=1)
            capabilities = receive()["result"]["capabilities"]
            self.assertEqual(capabilities["positionEncoding"], "utf-16")
            for name in ("hover", "definition", "declaration", "implementation", "typeDefinition", "inlayHint",
                         "signatureHelp", "documentFormatting"):
                self.assertTrue(capabilities[name + "Provider"])
            doc = {"uri": uri, "version": 1, "text": source}
            send("textDocument/didOpen", {"textDocument": doc})
            send("textDocument/completion", {"textDocument": {"uri": uri},
                  "position": {"line": 3, "character": 11}}, 2)
            messages = [receive(), receive()]
            items = next(message["result"] for message in messages if message.get("id") == 2)
            completion = next(item for item in items if item["label"] == "parseInt")
            self.assertEqual(completion["insertText"], "parseInt($0)")
            self.assertEqual(completion["insertTextFormat"], 2)
            self.assertIn("func parseInt", completion["detail"])
            diagnostics = next(message["params"] for message in messages if "params" in message)
            self.assertEqual(diagnostics["version"], 1)
            self.assertTrue(diagnostics["diagnostics"][0]["code"].startswith("E1"))
            source = """import (parse as reader)
interface Greeter {
    const func greet() i32;
}
class GreeterImpl {
    #implements: Greeter
    pub const func greet() i32 { 42 }
}
func sample(value: i32) !i32 {
    var number = 3;
    var instance = GreeterImpl{};
    var fromCall = instance.greet();
    var parsed = reader.parseInt<i32>("7")!;
    number + value + fromCall + parsed
}
"""
            send("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2},
                                           "contentChanges": [{"text": source}]})
            clean = receive()["params"]
            self.assertEqual(clean["version"], 2)
            self.assertEqual(clean["diagnostics"], [])

            def position(line, word):
                return {"line": line, "character": source.splitlines()[line].index(word)}

            def request(method, **fields):
                send("textDocument/" + method, {"textDocument": {"uri": uri}, **fields}, 4)
                reply = receive()
                self.assertEqual(reply.get("id"), 4)
                self.assertNotIn("error", reply)
                return reply["result"]

            def go(method, row, word, destination):
                found = request(method, position=position(row, word))
                self.assertEqual([(item["uri"], item["range"]["start"]["line"]) for item in found],
                                 [(uri, destination)])

            go("definition", 13, "number", 9)
            go("declaration", 13, "number", 9)
            go("typeDefinition", 11, "instance", 4)
            go("implementation", 5, "Greeter", 4)
            go("implementation", 2, "greet", 6)
            hover = request("hover", position=position(11, "greet"))
            self.assertIn("func greet() i32", hover["contents"]["value"])
            hover = request("hover", position=position(13, "parsed"))
            self.assertIn("parsed: i32", hover["contents"]["value"])
            imported = request("definition", position=position(12, "parseInt"))
            self.assertTrue(imported and all(item["uri"].endswith("/stdlib/parse/integer.vqr") for item in imported))
            signatures = request("signatureHelp", position=position(12, '"7"'))
            self.assertEqual(signatures["activeParameter"], 0)
            self.assertTrue(any({"label": "radix: u8"} in item["parameters"] for item in signatures["signatures"]))
            existing_call = request("completion", position=position(12, "<i32>"))
            self.assertNotIn("insertText", next(item for item in existing_call if item["label"] == "parseInt"))
            hints = request("inlayHint", range={"start": {"line": 9, "character": 0},
                                               "end": {"line": 13, "character": 0}})
            self.assertEqual({hint["position"]["line"]: hint["label"] for hint in hints},
                             {9: ": i32", 10: ": GreeterImpl", 11: ": i32", 12: ": i32"})

            cursor = position(9, "number")
            actions = request("codeAction", range={"start": cursor, "end": cursor},
                              context={"only": ["refactor.rewrite"], "diagnostics": []})
            action, = actions
            change, = action["edit"]["documentChanges"]
            self.assertEqual(action["kind"], "refactor.rewrite")
            self.assertEqual(change["textDocument"]["version"], 2)
            self.assertEqual(change["edits"][0]["newText"], ": i32")

            found = request("references", position=position(13, "number"),
                            context={"includeDeclaration": True})
            self.assertEqual([item["range"]["start"]["line"] for item in found], [9, 13])
            prepared = request("prepareRename", position=position(13, "number"))
            self.assertEqual(prepared["start"], position(13, "number"))
            renamed = request("rename", position=position(13, "number"), newName="totalCount")
            change, = renamed["documentChanges"]
            self.assertEqual(change["textDocument"], {"uri": uri, "version": 2})
            self.assertEqual([item["newText"] for item in change["edits"]], ["totalCount", "totalCount"])
            send("textDocument/rename", {"textDocument": {"uri": uri},
                                         "position": position(13, "number"), "newName": "value"}, 4)
            self.assertIn("error", receive())

            invalid_type = source.replace('reader.parseInt<i32>("7")!', 'reader.parseInt<i32>(true)!')
            send("textDocument/didChange", {"textDocument": {"uri": uri, "version": 3},
                                           "contentChanges": [{"text": invalid_type}]})
            failure = receive()["params"]
            self.assertEqual(failure["version"], 3)
            self.assertTrue(failure["diagnostics"][0]["code"].startswith("E2"))

            source = """func bindings() void {
    a, b := (i16(1), i64(2));
    /* 🚀 */ var total = i16(2);
    &alias := &total;
    for i in i64(0)..b { alias += a; }
    for j := i16(0); j < a; j++ { alias += j; }
    var opt: ?*i16 = some(&alias);
    switch opt { some(ptr) => { (*ptr)++; }, null => {}, }
    switch opt { some(ref borrowed) => { (*borrowed)++; }, null => {}, }
}
"""
            send("textDocument/didChange", {"textDocument": {"uri": uri, "version": 4},
                                           "contentChanges": [{"text": source}]})
            # An editor can ask for hints before the debounced check has finished.
            send("textDocument/inlayHint", {"textDocument": {"uri": uri}, "range": {
                "start": {"line": 0, "character": 0}, "end": {"line": 10, "character": 0}}}, 5)
            checked, result = sorted((receive(), receive()), key=lambda msg: msg.get("id", 0))
            self.assertEqual(checked["params"]["diagnostics"], [])
            self.assertEqual(result["id"], 5)
            expected = {}
            short = {1, 3, 5}
            for row, name, hint in ((1, "a", "i16"), (1, "b", "i64"), (2, "total", "i16"),
                                    (3, "alias", "&i16"), (4, "i", "i64"), (5, "j", "i16"),
                                    (7, "ptr", "*i16"), (8, "borrowed", "&*i16")):
                line = source.splitlines()[row]
                end = line.index(name, 8 if row == 4 else 0) + len(name)
                label = hint if row in short else (": " if row == 2 else ":") + hint
                expected[row, len(line[:end].encode("utf-16-le")) // 2] = (label, row in short)
            self.assertEqual({(hint["position"]["line"], hint["position"]["character"]):
                              (hint["label"], hint.get("paddingLeft", False))
                              for hint in result["result"]}, expected)

            source = """import (bufio, coll)
func query(scanner: *bufio.Scanner) !void {
    num := scanner.readInt<i32>()!;
    vec := coll.Vec<i32>();
    rest := vec.splitOff(0)!;
    for i in 0..num {}
    empty := coll.Vec<i32>.withCapacity(0)!;
    unknownName;
}
"""
            send("textDocument/didChange", {"textDocument": {"uri": uri, "version": 5},
                                           "contentChanges": [{"text": source}]})
            failure = receive()["params"]
            self.assertIn("unknownName", failure["diagnostics"][0]["message"])
            hover = request("hover", position=position(4, "splitOff"))
            self.assertIn("!coll.Vec<i32>", hover["contents"]["value"])
            self.assertNotIn("Self", hover["contents"]["value"])
            hover = request("hover", position=position(2, "readInt"))
            self.assertIn("!i32", hover["contents"]["value"])
            hover = request("hover", position=position(4, "rest"))
            self.assertIn("rest: coll.Vec<i32>", hover["contents"]["value"])
            hover = request("hover", position=position(6, "withCapacity"))
            self.assertIn("!coll.Vec<i32>", hover["contents"]["value"])
            self.assertNotIn("Self", hover["contents"]["value"])
            hints = request("inlayHint", range={"start": {"line": 2, "character": 0},
                                               "end": {"line": 6, "character": 0}})
            self.assertIn((5, ":i32"), [(item["position"]["line"], item["label"]) for item in hints])

            source = """func lookup(value: ?i32) !?i32 { value }
func forward(value: ?i32) !?i32 {
    var a = value?;
    var b = lookup(value)!?;
    a + b
}
"""
            send("textDocument/didChange", {"textDocument": {"uri": uri, "version": 6},
                                           "contentChanges": [{"text": source}]})
            self.assertEqual(receive()["params"]["diagnostics"], [])
            hints = request("inlayHint", range={"start": {"line": 2, "character": 0},
                                               "end": {"line": 4, "character": 0}})
            self.assertEqual([(item["position"]["line"], item["label"]) for item in hints],
                             [(2, ": i32"), (3, ": i32")])
            source = source.replace("    var a", "    unknownName;\n    var a")
            send("textDocument/didChange", {"textDocument": {"uri": uri, "version": 7},
                                           "contentChanges": [{"text": source}]})
            self.assertIn("unknownName", receive()["params"]["diagnostics"][0]["message"])
            hover = request("hover", position=position(5, "b"))
            self.assertIn("b: i32", hover["contents"]["value"])
            helper = self.source("helper.vqr", "pub func count() i32 { 4 }\n")
            source = "func f() i32 { helper.count() }\n"
            send("textDocument/didChange", {"textDocument": {"uri": uri, "version": 8},
                                           "contentChanges": [{"text": source}]})
            self.assertEqual(receive()["params"]["diagnostics"], [])
            found = request("references", position=position(0, "count"),
                            context={"includeDeclaration": True})
            self.assertEqual({item["uri"] for item in found}, {uri, helper.as_uri()})
            rename = request("rename", position=position(0, "count"), newName="countItems")
            self.assertEqual({item["textDocument"]["uri"] for item in rename["documentChanges"]},
                             {uri, helper.as_uri()})
            self.assertTrue(all(item["edits"][0]["newText"] == "countItems"
                                for item in rename["documentChanges"]))

            helper.write_text("pub class Element { pub var value: i32; }\n", encoding="utf-8")
            source = "func f() i32 { var x: helper.Element = helper.Element{value: 4}; x.value }\n"
            send("textDocument/didChange", {"textDocument": {"uri": uri, "version": 9},
                                           "contentChanges": [{"text": source}]})
            self.assertEqual(receive()["params"]["diagnostics"], [])
            changed = request("rename", position=position(0, "Element"), newName="Value")
            self.assertEqual({edit["textDocument"]["uri"]: len(edit["edits"])
                              for edit in changed["documentChanges"]}, {uri: 2, helper.as_uri(): 1})

            field = request("rename", position=position(0, "value"), newName="quantity")
            self.assertEqual({edit["textDocument"]["uri"]: len(edit["edits"])
                              for edit in field["documentChanges"]}, {uri: 2, helper.as_uri(): 1})

            helper.write_text("""pub class Boxed<T> {
    pub var size: T;
    pub func init(input: T) : size(input) {}
    pub const func measure() T { size }
}
pub class Other { pub var size: i32; }
""", encoding="utf-8")
            source = """func f() i32 {
    var item = helper.Boxed<i32>(4);
    var maybe = some(item);
    var other = helper.Other{size: 5};
    item.measure() + item.size + maybe?.size.unwrap() + other.size
}
"""
            send("textDocument/didChange", {"textDocument": {"uri": uri, "version": 10},
                                           "contentChanges": [{"text": source}]})
            self.assertEqual(receive()["params"]["diagnostics"], [])
            cursor = position(4, "size")
            found = request("references", position=cursor, context={"includeDeclaration": True})
            self.assertEqual({path: [item["range"]["start"]["line"] for item in found
                                     if item["uri"] == path] for path in (uri, helper.as_uri())},
                             {uri: [4, 4], helper.as_uri(): [1, 2, 3]})
            field = request("rename", position=cursor, newName="capacity")
            self.assertEqual({edit["textDocument"]["uri"]: len(edit["edits"])
                              for edit in field["documentChanges"]}, {uri: 2, helper.as_uri(): 3})

            send("shutdown", identifier=3)
            self.assertEqual(receive()["id"], 3)
            send("exit")
            self.assertEqual(server.wait(timeout=5), 0)
        self.assertEqual(original.read_text(), "func f() void {}\n")


if __name__ == "__main__":
    unittest.main()
