# process

`Command.new(path)` creates a reusable command. Set literal arguments with
`arg` or `args` and optionally set the child working directory with `cwd`.
`status()` waits for the command with inherited standard streams.
`output(limit)` supplies an empty standard input and captures standard output
and error as two owned byte sequences. The limit applies to the combined
number of captured bytes. Exceeding it terminates and reaps the direct child
and returns `ProcessError.OutputLimit`.

`run` and `output` are convenience functions for a single call. A bare
executable name is resolved from the inherited process environment. Every
argument is passed as one argument. `ExitStatus` provides an exit code or a
terminating signal, and a nonzero exit code is returned normally. Failed
spawns and system I/O errors carry their native error code.

The current implementation targets hosted GNU/Linux x86-64 and AArch64 with
glibc 2.29 or later. Child environment overrides, asynchronous handles and
configurable standard streams remain pending.
