"""Forward DAP traffic to lldb-dap and publish paused locals to Vaqrin hovers."""

import itertools
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

if "debug_socket_path" not in globals():
    from debug_hover import debug_socket_path, private_debug_directory


def packets(stream):
    while header := stream.readline():
        length = None
        while header.strip():
            key, value = header.split(b":", 1)
            if key.lower() == b"content-length":
                length = int(value)
            header = stream.readline()
        if length is None or not header:
            return
        data = stream.read(length)
        if len(data) != length:
            return
        yield json.loads(data)


def write_packet(stream, message):
    data = json.dumps(message).encode()
    stream.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
    stream.flush()


class DebugSession:
    def __init__(self, dap):
        self.dap = dap
        self.lock = threading.Lock()
        self.serial = itertools.count(1)
        self.pending = {}
        self.stop = None
        self.snapshot = None
        self.lines_start_at_one = True
        self.columns_start_at_one = True

    def send(self, message, reply):
        sequence = next(self.serial)
        if reply is not None:
            self.pending[sequence] = reply
        write_packet(self.dap.stdin, {**message, "seq": sequence})

    def inspect(self, stop, command, callback, **arguments):
        def reply(message):
            if self.stop is stop and message.get("success"):
                callback(stop, message.get("body", {}))

        self.send({"type": "request", "command": command, "arguments": arguments}, reply)

    def collect_frame(self, stop, body):
        frames = body.get("stackFrames", [])
        if not frames:
            return
        frame = frames[0]
        source = frame.get("source", {}).get("path")
        if not source or not Path(source).is_absolute():
            return
        stop.update(path=source, line=frame["line"] - self.lines_start_at_one,
                    column=max(0, frame.get("column", 0) - self.columns_start_at_one), values={})
        self.inspect(stop, "scopes", self.collect_scopes, frameId=frame["id"])

    def collect_scopes(self, stop, body):
        scopes = [scope for scope in body.get("scopes", [])
                  if scope.get("presentationHint") in ("locals", "arguments")
                  or scope.get("name") in ("Locals", "Arguments")]
        stop["remaining"] = len(scopes)
        for scope in scopes:
            self.inspect(stop, "variables", self.collect_values,
                         variablesReference=scope["variablesReference"])

    def collect_values(self, stop, body):
        values = stop["values"]
        for variable in body.get("variables", []):
            name = variable.get("name", "")
            value = variable.get("value", "")
            if len(values) < 128 and name.isidentifier() and value and not value.startswith("<"):
                if name in values:
                    values[name] = None
                else:
                    values[name] = value[:256]
        stop["remaining"] -= 1
        if stop["remaining"] == 0:
            self.snapshot = {key: stop[key] for key in ("path", "line", "column", "values")}

    def from_client(self):
        try:
            with os.fdopen(os.dup(sys.stdin.fileno()), "rb") as client:
                for message in packets(client):
                    with self.lock:
                        command = message.get("command")
                        if command in ("continue", "next", "stepIn", "stepOut", "stepBack",
                                       "reverseContinue", "goto", "restart", "terminate", "disconnect"):
                            self.stop = self.snapshot = None
                        if command == "initialize":
                            arguments = message.get("arguments", {})
                            self.lines_start_at_one = arguments.get("linesStartAt1", True)
                            self.columns_start_at_one = arguments.get("columnsStartAt1", True)
                        reply = message["seq"] if message["type"] == "request" else None
                        self.send(message, reply)
        except BrokenPipeError:
            pass
        finally:
            with self.lock:
                self.dap.stdin.close()

    def from_adapter(self):
        for message in packets(self.dap.stdout):
            with self.lock:
                if message["type"] == "response":
                    reply = self.pending.pop(message["request_seq"], None)
                    if callable(reply):
                        reply(message)
                        continue
                    if reply is None:
                        continue
                    message["request_seq"] = reply
                event = message.get("event")
                if event in ("continued", "exited", "terminated"):
                    self.stop = self.snapshot = None
                write_packet(sys.stdout.buffer, message)
                if event == "stopped":
                    self.snapshot = None
                    self.stop = {}
                    thread = message.get("body", {}).get("threadId")
                    if thread is not None:
                        self.inspect(self.stop, "stackTrace", self.collect_frame,
                                     threadId=thread, startFrame=0, levels=1)

    def serve(self, listener):
        try:
            while True:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(0.08)
                    with self.lock:
                        data = json.dumps(self.snapshot, ensure_ascii=False).encode()
                    try:
                        connection.sendall(data + b"\n")
                    except OSError:
                        continue
        except OSError:
            pass


def main():
    worktree, command, *arguments = sys.argv[1:]
    directory, prefix = debug_socket_path(worktree)
    if directory is None:
        return subprocess.call([command, *arguments])
    with subprocess.Popen([command, *arguments], stdin=subprocess.PIPE, stdout=subprocess.PIPE) as dap:
        session = DebugSession(dap)
        endpoint = directory / f"{prefix}-{os.getpid()}.sock"
        with socket.socket(socket.AF_UNIX) as listener:
            bound = False
            try:
                directory.mkdir(mode=0o700, exist_ok=True)
                if private_debug_directory(directory):
                    listener.bind(str(endpoint))
                    bound = True
                    listener.listen()
                    threading.Thread(target=session.serve, args=(listener,), daemon=True).start()
            except OSError:
                pass
            threading.Thread(target=session.from_client, daemon=True).start()
            try:
                session.from_adapter()
            except BrokenPipeError:
                pass
            finally:
                if bound:
                    endpoint.unlink(missing_ok=True)
                if dap.poll() is None:
                    dap.terminate()
    return dap.returncode


if __name__ == "__main__":
    sys.exit(main())
