"""Local, read-only connection from language hovers to paused debug frames."""

import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import tempfile


def debug_socket_path(worktree):
    if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
        return None, ""
    directory = Path(tempfile.gettempdir()) / f"vaqrin-debug-{os.getuid()}"
    prefix = hashlib.sha256(os.fsencode(Path(worktree).resolve())).hexdigest()[:24]
    return directory, prefix


def private_debug_directory(directory):
    mode = directory.lstat()
    return stat.S_ISDIR(mode.st_mode) and mode.st_uid == os.getuid() and not mode.st_mode & 0o077


def debug_hover_frames(worktree):
    directory, prefix = debug_socket_path(worktree)
    try:
        if directory is None or not private_debug_directory(directory):
            return
        endpoints = list(directory.glob(f"{prefix}-*.sock"))
    except OSError:
        return
    for path in endpoints:
        try:
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(0.08)
                connection.connect(str(path))
                with connection.makefile("rb") as response:
                    frame = json.loads(response.readline(128 * 1024))
                if frame:
                    yield frame
        except ConnectionRefusedError:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        except (OSError, ValueError):
            continue
