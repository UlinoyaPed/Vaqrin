# env

On hosted GNU/Linux, `get`, `getOr`, `contains`, `set`, `remove` and `vars`
access the process environment. `get` returns `null` for an unset name and an
owned empty string for a set but empty value. Keys must be nonempty and may
contain neither NUL nor `=`. Values may contain `=` and must exclude NUL.
Inherited entries containing invalid UTF-8 produce `text.Utf8Error.InvalidUtf8`
when read. The package serializes its environment reads and writes. Callers
must coordinate environment mutation by foreign code separately.

`snapshot()` returns an owned `Environment`. Its `set` and `remove` methods
edit only that snapshot. `keys`, `items` and `clone` return owned copies.
`currentDir` returns the process working directory, `currentExe` reads
`/proc/self/exe`, `tempDir` uses `TMPDIR` with a `/tmp` fallback, and
`homeDir` uses a nonempty `HOME` or returns `null`.
