"""Local, read-only connection from language hovers to paused debug frames."""

import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import time


MAX_FRAME_BYTES = 128 * 1024


def debug_socket_path(worktree):
    if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
        return None, ""
    directory = Path(tempfile.gettempdir()) / f"vaqrin-debug-{os.getuid()}"
    prefix = hashlib.sha256(os.fsencode(Path(worktree).resolve())).hexdigest()[:24]
    return directory, prefix


def private_debug_directory(directory):
    mode = directory.lstat()
    return stat.S_ISDIR(mode.st_mode) and mode.st_uid == os.getuid() and not mode.st_mode & 0o077


def read_frame(connection, deadline):
    data = bytearray()
    while len(data) < MAX_FRAME_BYTES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        connection.settimeout(remaining)
        chunk = connection.recv(min(8192, MAX_FRAME_BYTES - len(data)))
        if not chunk:
            return None
        data.extend(chunk)
        if data.endswith(b"\n"):
            frame = json.loads(data.decode("utf-8"))
            if not isinstance(frame, dict):
                return None
            path, line, column, values = (frame.get(key) for key in ("path", "line", "column", "values"))
            if (isinstance(path, str) and "\0" not in path and Path(path).is_absolute()
                    and type(line) is int and line >= 0 and type(column) is int and column >= 0
                    and isinstance(values, dict)
                    and all(value is None or isinstance(value, str) for value in values.values())):
                return frame
            return None
    return None


def debug_hover_frames(worktree):
    directory, prefix = debug_socket_path(worktree)
    try:
        if directory is None or not private_debug_directory(directory):
            return
    except OSError:
        return
    deadline = time.monotonic() + 0.08
    for path in directory.glob(f"{prefix}-*.sock"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            if not stat.S_ISSOCK(path.lstat().st_mode):
                continue
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(remaining)
                connection.connect(str(path))
                frame = read_frame(connection, deadline)
                if frame:
                    yield frame
        except ConnectionRefusedError:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        except (OSError, ValueError, RecursionError):
            continue
