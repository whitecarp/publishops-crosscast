# Local audit export contract

`vpub audit export --out public-audit.json` writes `crosscast-audit/1` without
contacting a platform. Same source bytes produce the same output bytes. Its own
export is not journalled. The ledger is opened in SQLite read-only mode. Only
the audit directory and linked publication, deletion and edit receipts are read;
account files, browser jobs/debug captures and media are excluded. Linked source
files are refused. Export destinations must be outside VPUB_HOME.

| Section | Allowed values |
| --- | --- |
| schema, site, times | Literal schema; salted 8-hex site or null; relative/exact |
| window | Requested ISO boundaries, UTC days; origin only with exact times |
| builds | Version/Python strings starting with a digit, closed OS/install words, source/commit hex digests |
| publications | 16-hex salted ref/media_ref; closed platform/route/visibility/state/phase/error words; booleans, nonnegative counts, rounded MiB, readback flags, named differing fields, edit summaries, relative or explicit exact timestamps |
| deletions | Publication pseudonym, state, attempted boolean, proof booleans, fixed error/fault words, code stamps, times |
| runs | 32-hex command id, closed command/action/flags, build index, optional declared session and purpose, exit and duration, stages, slot spans, concurrency, ended flag and loss fields |
| events | Closed command_start/end, transition, call, stage, span, fault, observation projections; seq, previous digest, run, time/day, dropped_before |
| losses | Deduplicated 32-hex loss id, command id or null, relative/exact original time or null, optional reported_at and time_unplaced, closed reason and persistent boolean; no exception text or path |
| counts | Integer counts by closed state/code/point or exit code; unknown network read/write totals are null |
| integrity | Sequence bounds, raw-chain head, chain_ok, first_bad_seq, outside-command count, dropped count and lower-bound flag, unplaced_dropped_events, unreadable_loss_records and loss_records_complete |

Strings copied from text, names, addresses, work ids, raw ledger keys, media/plan
hashes and paths never pass through. Unknown state/code vocabulary becomes
`other`; absent values remain null. Source-code hashes are explicitly retained
to identify the running build. Session labels must be nonprivate study labels.
Default times are seconds from the first selected event plus UTC day; no absolute
origin is exported. `--times exact` opts into UTC instants. File sizes use 0.1 MiB
unless `--exact-sizes` is selected. Missing historical size evidence stays null.

The source chain hashes bytes including LF. Broken source evidence is reported,
not repaired; consumers must check integrity before relying on counts. Exports
cover selected events plus current linked receipt projections, not reconstructed
historical snapshots. A bounded window may omit starts/ends. `history_complete`
requires a visible claim, an unbounded interval and no detected loss; silent
process-death losses still cannot be excluded. Every run includes dropped_events,
sequence_gap, chain_ok and loss_complete (false: the dropped counter is a lower
bound: hard process death and a wholly unwritable destination cannot be excluded). A gap without an identifiable
command is reported globally rather than assigned to an invented command.

Journal lock acquisition initially waits at most 10 seconds (with the lock
helper's 200 ms polling granularity). After a lock give-up, later appends in the
same process and data folder make one immediate acquisition attempt until a
durable append succeeds; success restores the initial wait. Forty events behind
a continuously stuck lock therefore spend one long acquisition wait, not forty.
This bound is per process and home, not per CLI command: each new browser worker
can pay its own initial wait, and a successful append re-arms that process.
Local filesystem/write/fsync time is additional. The offline
contract tests three writers at once and the study shape of one parent plus
two workers with events 250 ms apart: all 24 attempted events must be written,
without relaxing the assertion. Each unsuccessful append reports a closed
diagnostic reason and attempts to fsync its own unique `audit/losses` record,
independently of the contended journal lock. A worker's final failed append is
therefore exported even after that worker exits without another successful
append. Later `dropped_before` reports are deduplicated against these records;
legacy counters remain supported. A durable line followed by a failed head
replacement is recoverable and is not counted as a missing event.

If the entire destination is unwritable, a process-local fallback remains and
the diagnostic says `loss_persisted: false`; it is exported after a later
successful append in that process. A crash before fallback persistence remains
unobservable. An individually unreadable or malformed loss file increments
`integrity.unreadable_loss_records`, sets `loss_records_complete` false, and does
not stop export of the usable evidence. That count is not silently converted
into a dropped-event count: the record's contents and time could not be read.
`loss_records_complete` describes the files found, not complete loss capture.
An inaccessible or non-directory loss folder sets `loss_folder_unreadable: true`
and `loss_records_complete: false`. Individual files are bounded to 16 KiB;
oversized, deeply nested or otherwise unreadable records stay counted as
unreadable while the usable evidence is exported.
If an event reports the id of an unreadable file, `losses` counts that event once
with `file_unreadable: true`, `reason: file_unreadable`, and unknown original time.
The same file remains in `unreadable_loss_records`: this is a file-quality count,
not an extra dropped event. Never add it to `dropped_events` or treat a matched
report as evidence that the damaged file's unseen contents were complete.
These records account for caught give-ups, not attempts interrupted by power
loss, and do not make `loss_complete` true. Bounded exports count dated durable
loss records in the requested interval. An unpersisted id reported by an event
inside the window also counts once, with `at: null`, `time_unplaced: true` and
the reporter's `reported_at`; it increments `unplaced_dropped_events`. Its actual
loss may precede the window. A readable durable record with a known time outside
the window is excluded even when a later event repeats its id. Unreadable records
prevent `history_complete` from being true. No source evidence is repaired.

Press counters are durable attempts written before a click, not proof the click
reached the platform. Calls count adapter invocations, not HTTP requests. Person
observations do not change receipts or turn them into platform verification.
Span times use the parent command's monotonic clock; idle measures a slot waiting
while another platform is active, not launch overhead. Missing spans do not imply
idle time. Real-account field validation is separate from these offline tools.

For an approved private test only, config.json `fault_injection: true` together
with VPUB_FAULT selects before_press, after_press, delete_readback_lost or external.
Only explicitly private/unlisted YouTube/Douyin browser plans pass the gate;
scheduled, public, account-default and Bilibili plans are refused. `external`
labels the command but injects nothing. The other hooks raise injected_fault at
most once per command; uncertainty and press markers persist. Disabling the
switch takes precedence over an old armed upload plan. No fault retries, receipt
resets or cleanup actions are performed. This switch is instrumentation, not
permission to act on an account.
