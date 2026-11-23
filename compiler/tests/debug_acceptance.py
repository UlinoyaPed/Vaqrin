"""Compile Vaqrin debug targets and exercise LLDB over DAP."""

import json
import os
import queue
import runpy
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest

from acceptance import CompilerCase, REPOSITORY, VAQC


class DebugAcceptance(CompilerCase):
    @unittest.skipUnless(os.name == "posix", "local Unix debug bridge")
    def test_debug_bridge_bounds_incoming_messages(self):
        result = self.process(sys.executable, "-I", "-c", editor_script("dap.py"), self.root,
                              sys.executable, "-c", "import sys; sys.stdin.buffer.read()",
                              input=b"Content-Length: 33554433\r\n\r\n", good=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(b"invalid client message", result.stderr)

        read_frame = runpy.run_path(str(REPOSITORY / "editors/zed/src/debug_hover.py"))["read_frame"]
        reader, writer = socket.socketpair()
        with reader, writer:
            writer.sendall(b'{"path":"/main.vqr","line":0,"column":0,"values":[]}\n')
            self.assertIsNone(read_frame(reader, time.monotonic() + 1))

    def build_target(self):
        source = self.source("counter.vqr", """import (bufio, io)
class Counter {
    pub var value: i32;
    pub func init(start: i32) : value(start) {}
}
func main() !void {
    var owner = new Counter(35)!;
    var total: i32 = owner.value + 7;
    io.println(total)!;
    io.println(total + 1)!;
    io.eprintln("enter a number")!;
    var scanner = bufio.newScanner(io.stdin())!;
    var number = scanner.readInt<i32>()!;
    io.println(number + 1)!;
}
func other() i32 {
    var total: i32 = 3;
    total
}
""")
        name = "counter.exe" if os.name == "nt" else "counter"
        executable = self.root / "debug output" / name
        launcher = VAQC.with_name("vaqdbg.exe" if os.name == "nt" else "vaqdbg")
        self.process(launcher, "--build-only", "-o", executable, source)
        return source, executable

    def test_debug_driver_builds_program(self):
        _, executable = self.build_target()
        result = self.process(executable, input=b"123\n")
        self.assertEqual(result.stdout, b"42\n43\n124\n")
        self.assertEqual(result.stderr, b"enter a number\n")

    def hover(self, source, line, name):
        text = source.read_text(encoding="utf-8")
        uri = source.as_uri()
        requests = [
            {"method": "initialize", "id": 1},
            {"method": "textDocument/didOpen", "params": {"textDocument": {
                "uri": uri, "text": text, "version": 1}}},
            {"method": "textDocument/hover", "id": 2, "params": {"textDocument": {"uri": uri},
                "position": {"line": line, "character": text.splitlines()[line].index(name)}}},
            {"method": "shutdown", "id": 3},
            {"method": "exit"},
        ]
        payload = b""
        for request in requests:
            body = json.dumps({"jsonrpc": "2.0", **request}).encode()
            payload += f"Content-Length: {len(body)}\r\n\r\n".encode() + body
        output = self.process(sys.executable, "-I", "-c", editor_script("server.py"), self.root, input=payload).stdout
        while output:
            header, output = output.split(b"\r\n\r\n", 1)
            length = int(header.split(b":", 1)[1])
            message = json.loads(output[:length])
            output = output[length:]
            if message.get("id") == 2:
                self.assertNotIn("error", message)
                return message["result"]["contents"]["value"]
        self.fail("hover response missing")

    @unittest.skipUnless(shutil.which("lldb-dap"), "lldb-dap is not installed")
    def test_native_dap_breakpoint_variables_and_step(self):
        source, executable = self.build_target()
        with subprocess.Popen([sys.executable, "-I", "-c", editor_script("dap.py"), self.root,
                               shutil.which("lldb-dap"), "--no-lldbinit"], cwd=self.root,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE) as dap:
            self.addCleanup(lambda: dap.poll() is None and dap.kill())
            messages = queue.Queue()

            def read_messages():
                while line := dap.stdout.readline():
                    length = int(line.split(b":", 1)[1])
                    if dap.stdout.readline() != b"\r\n":
                        break
                    messages.put(json.loads(dap.stdout.read(length)))
                messages.put(None)

            threading.Thread(target=read_messages, daemon=True).start()
            serial = 0
            backlog = []

            def send(message):
                nonlocal serial
                serial += 1
                payload = json.dumps({**message, "seq": serial}).encode()
                dap.stdin.write(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
                dap.stdin.flush()
                return serial

            def start(command, **arguments):
                return send({"type": "request", "command": command, "arguments": arguments})

            def receive(match):
                for index, message in enumerate(backlog):
                    if match(message):
                        return backlog.pop(index)
                until = time.monotonic() + 15
                while True:
                    try:
                        message = messages.get(timeout=max(0, until - time.monotonic()))
                    except queue.Empty:
                        self.fail(f"timed out waiting for LLDB DAP; received: {backlog}")
                    self.assertIsNotNone(message, f"LLDB DAP exited; received: {backlog}")
                    if match(message):
                        return message
                    backlog.append(message)

            def response(seq):
                reply = receive(lambda msg: msg.get("request_seq") == seq)
                self.assertTrue(reply["success"], reply)
                return reply.get("body", {})

            def request(command, **arguments):
                return response(start(command, **arguments))

            def event(name):
                return receive(lambda msg: msg.get("event") == name).get("body", {})

            terminal = None
            try:
                config, = json.loads((REPOSITORY / ".zed/debug.json").read_text(encoding="utf-8"))
                capabilities = request("initialize", adapterID="Vaqrin", linesStartAt1=True, columnsStartAt1=True,
                                       pathFormat="path", supportsRunInTerminalRequest=True)
                self.assertTrue(capabilities.get("supportsEvaluateForHovers"))
                launched = start("launch", program=str(executable), cwd=str(self.root),
                                 disableASLR=config["disableASLR"], runInTerminal=config["runInTerminal"])
                reverse = receive(lambda msg: msg.get("command") == "runInTerminal")
                self.assertEqual(reverse["type"], "request")
                args = reverse["arguments"]
                self.assertEqual(args["kind"], "integrated")
                env = dict(os.environ)
                for name, value in args.get("env", {}).items():
                    if value is None:
                        env.pop(name, None)
                    else:
                        env[name] = value
                terminal = subprocess.Popen(args["args"], cwd=args.get("cwd") or self.root, env=env,
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                send({"type": "response", "command": "runInTerminal", "request_seq": reverse["seq"],
                      "success": True, "body": {"processId": terminal.pid}})
                event("initialized")
                breaks = request("setBreakpoints", source={"path": str(source)}, breakpoints=[{"line": 9}])
                self.assertTrue(breaks["breakpoints"][0]["verified"], breaks)
                request("configurationDone")
                response(launched)
                stopped = event("stopped")
                self.assertEqual(stopped["reason"], "breakpoint")
                thread = stopped["threadId"]
                frame = request("stackTrace", threadId=thread)["stackFrames"][0]
                self.assertEqual((frame["source"]["path"], frame["line"]), (str(source), 9))
                scopes = request("scopes", frameId=frame["id"])["scopes"]
                reference = next(scope["variablesReference"] for scope in scopes if scope["name"] == "Locals")
                variables = {value["name"]: value for value in request("variables",
                             variablesReference=reference)["variables"]}
                self.assertEqual(variables["total"]["value"], "42")
                hover = request("evaluate", expression="total", frameId=frame["id"], context="hover")
                self.assertEqual(hover["result"], "42")
                if os.name == "posix":
                    self.assertIn("var total: i32 = 42", self.hover(source, 8, "total"))
                    self.assertNotIn("= 42", self.hover(source, 17, "total"))
                self.assertIn("Counter", variables["owner"]["type"])
                box = request("variables", variablesReference=variables["owner"]["variablesReference"])
                pointee = next(value for value in box["variables"] if value["name"] == "pointee")
                fields = request("variables", variablesReference=pointee["variablesReference"])
                self.assertEqual(fields["variables"][0]["name"], "value")
                self.assertEqual(fields["variables"][0]["value"], "35")
                request("next", threadId=thread)
                event("stopped")
                frame = request("stackTrace", threadId=thread)["stackFrames"][0]
                self.assertEqual(frame["line"], 10)
                request("continue", threadId=thread)
                if os.name == "posix":
                    self.assertNotIn("= 42", self.hover(source, 8, "total"))
                stdout, stderr = terminal.communicate(b"123\n", timeout=15)
                self.assertEqual(terminal.returncode, 0, stderr)
                self.assertEqual(stdout, b"42\n43\n124\n")
                self.assertEqual(stderr, b"enter a number\n")
                self.assertEqual(event("exited")["exitCode"], 0)
                request("disconnect")
                self.assertFalse(any(msg.get("body", {}).get("category") in ("stdout", "stderr")
                                     for msg in backlog if msg.get("event") == "output"), backlog)
                dap.stdin.close()
                self.assertEqual(dap.wait(timeout=5), 0)
            finally:
                if dap.poll() is None:
                    dap.terminate()
                if terminal is not None:
                    if terminal.poll() is None:
                        terminal.kill()
                    terminal.communicate()


def editor_script(name):
    directory = REPOSITORY / "editors/zed/src"
    return "\n".join((directory / filename).read_text(encoding="utf-8")
                     for filename in ("debug_hover.py", "protocol.py", name))


if __name__ == "__main__":
    unittest.main()
