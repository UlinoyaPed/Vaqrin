# process

`Command.new(path)` creates a reusable command. Set literal arguments with
`arg` or `args`, and set the child working directory with `cwd`. Use `env` and
`envRemove` to override individual child variables. `envClear` starts a new
empty child environment. A bare executable name is resolved through the
parent's search path by POSIX spawn.

`stdin`, `stdout` and `stderr` accept `Stdio.Inherit`, `Stdio.Null`,
`Stdio.Piped` or an owned `Stdio.File`. By default, `spawn` and `status`
inherit the standard streams. `output(limit)` defaults to empty input and
captured standard output and error. Explicit stream settings apply to all
three operations. `status` drains any configured output pipes and discards
their data.

`spawn()` returns a move-only `Child`. `tryWait()` polls for exit. `wait()`
closes piped stdin, then waits and preserves any unread output pipes. Drain
large piped output using `waitWithOutput(limit)`, which consumes both output
pipes concurrently and returns their remaining bytes. The limit counts both
streams. Exceeding it terminates and reaps the direct child with
`ProcessError.OutputLimit`. Repeated waits return the cached exit status.

Pipe accessors borrow the child. Close child stdin to signal EOF, or let
`wait` and `waitWithOutput` close it. `terminate` sends SIGTERM and `kill`
sends SIGKILL. `closeHandles` closes pipes while retaining the child for a
later wait. Dropping a running child closes its pipes and arranges reaping
without sending a signal. When a reaping thread cannot be started, dropping
the handle waits for the child directly.

`run` and `output` provide one-call execution. `ExitStatus` exposes the exit
code or terminating signal, with nonzero exit codes returned as ordinary
results. System failures carry their native error code. This implementation
targets hosted GNU/Linux x86-64 and AArch64 with glibc 2.29 or later.
