# Calendar time and time zones

`now`, `Instant.now` and the blocking deadline and sleep APIs support hosted
GNU/Linux and macOS on x86-64 and AArch64. Deadlines use the platform's monotonic
clock. Signal interruptions preserve the original sleep deadline.

`TimeZone.load` reads the host's IANA TZif files under `/usr/share/zoneinfo`.
`tzdbVersion` reads the version from `tzdata.zi` when the host provides it.
`TimeZone.utc` and `fixedOffset` work without a host database.
`TimeZone.fromTzif` accepts caller-provided data and supports the 32-bit and
64-bit layouts and future POSIX transition rules. The calendar represents Unix
seconds and rejects TZif files with leap-second records.

`toUtc` takes a local time and a resolution policy. `Earlier` and `Later` choose
the respective instant during an overlap. `RejectGap` rejects nonexistent local
times and chooses the earlier instant during an overlap. `ShiftForward` moves a
nonexistent local time forward by the gap duration. It chooses the earlier
instant during an overlap.

Calendar formatting uses `%Y` for year, `%m` for month, `%d` for day, `%j` for
day of year, `%H` for hour, `%M` for minute, `%S` for second, `%f` for the
nine-digit nanosecond fraction and `%z` for the numeric UTC offset. `%%` yields
a literal percent sign. A directive that needs a date, time or offset fails
when that component is unavailable.
