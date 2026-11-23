"""Bounded Content-Length framing shared by the editor protocols."""

import json

MAX_HEADER_BYTES = 8 * 1024
MAX_MESSAGE_BYTES = 32 * 1024 * 1024


def read_packet(stream):
    remaining = MAX_HEADER_BYTES
    length = None
    while True:
        line = stream.readline(remaining + 1)
        if not line and remaining == MAX_HEADER_BYTES:
            return None
        if len(line) > remaining or not line.endswith(b"\n"):
            raise ValueError("invalid or oversized protocol header")
        remaining -= len(line)
        if line in (b"\r\n", b"\n"):
            break
        name, separator, value = line.partition(b":")
        if not separator or not name:
            raise ValueError("invalid protocol header")
        if name.lower() == b"content-length":
            value = value.strip()
            if length is not None or not value.isdigit() or len(value) > 8:
                raise ValueError("invalid protocol Content-Length")
            length = int(value)
    if length is None or not 0 < length <= MAX_MESSAGE_BYTES:
        raise ValueError("missing or oversized protocol Content-Length")
    data = stream.read(length)
    if len(data) != length:
        raise ValueError("incomplete protocol message")
    try:
        message = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        raise ValueError("invalid protocol JSON") from None
    if not isinstance(message, dict):
        raise ValueError("protocol message must be a JSON object")
    return message
