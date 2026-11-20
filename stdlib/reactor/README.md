# Linux readiness reactor

`Reactor.open()` creates an epoll instance. Call `register(fd, interest)`
to watch a descriptor for reads, writes, or both. Update or remove a
registration by its `WatchId`. `wait` returns a readiness, hangup or
error notification with its registration ID. Pass `null` for an
unbounded wait or a nonnegative duration to bound the wait.

Registrations are level-triggered. Readiness can change before use.
Perform the subsequent I/O on a nonblocking descriptor and be prepared
to retry after would-block. For a socket error or hangup, use the socket
operation to retrieve the error or read through to EOF.

The reactor duplicates each registered descriptor and closes its copy on
removal or destruction. Registration IDs are never reused by the reactor.
Closing and reusing the original descriptor number does not redirect the
old registration. Remove the watch to release its duplicate, including when
the watched socket should be closed. Serialize operations on each reactor
and keep the original descriptor live while registering it.

[Linux epoll](https://man7.org/linux/man-pages/man7/epoll.7.html)
