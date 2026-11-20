0.3.0 storage and resolution design
==================================

Scope and rollout
-----------------

Replace the disposable project cache independently of aliases. ``config.toml``
remains authoritative for roots and manual registrations. Persistent navigation
history is migrated independently of the cache (see `storage and upgrades
<configuration.rst#storage-and-upgrades>`_).

The development build uses SQLite through Python's standard-library
``sqlite3`` module. Both SQLite and a custom indexed file accelerated exact
lookup in the prototype comparison. SQLite was selected to keep transaction
handling, reader isolation and on-disk indexing in a maintained database
engine. Aliases and groups remain separate roadmap items.

SQLite project cache
--------------------

``projects.sqlite3`` stores a versioned catalog and the refresh generation.
SQLite indexes Python-casefolded names and canonical paths. Exact lookup seeks
to a name and prefix completion reads an ordered range. Fuzzy search streams
the metadata and decodes full projects only for the selected window. Project
payloads include the display path, source and all discovery routes. A reader
uses a single database snapshot throughout each lookup.

An existing database is refreshed in one transaction. If the process exits
before commit, SQLite can roll back its journal on the next ordinary read.
Completion opens the database read-only and returns no suggestions while a
rollback is pending. Initial creation and recovery from an unreadable format
build a fresh database beside the target and publish it by atomic replacement.
The refresh start time is checked under the cache writer lock so a late writer
cannot overwrite a newer valid catalog.

Snapshots also record the configuration file identity and timestamps. Every
cache read, including the exact-name dispatch shortcut, rejects a snapshot
whose configuration stamp changed. Configuration mutations remain committed if
a later scan fails, but the old snapshot is then unavailable to navigation,
listing, the picker and completion. Normal lookups with configured roots or
manual projects rebuild it before considering a learned match. With no roots
or registrations, a learned hit can be returned directly. Cache-only
picker queries and completion never scan. Refresh checks the settings again
under the configuration lock before publishing and retries if another config
writer changed them during the scan. This prevents a concurrent old scan from
making a removed registration or exclusion reappear. The previous SQLite file
is retained until a complete replacement is ready. Mutation errors report that
the configuration was saved and that the next ordinary lookup retries; run
``pcd refresh`` once the underlying scan or I/O problem is resolved. An error
after atomic TOML replacement also warns that the configuration may be saved.

When a legacy JSONL snapshot exists, a configuration write persists the cache
initialization barrier before replacing TOML. The source JSONL is preserved,
but neither normal reads nor completion can reimport that pre-change snapshot
after a failed refresh or a crash. Older SQLite snapshots without a configuration
stamp are rebuilt once before normal use. Legacy imports also check that the
TOML modification time is no newer than JSONL, so a manual edit skips an older
legacy cache. Publication stores the verified pre-write configuration stamp
and checks it afterwards, covering editors which do not acquire pcd's lock.

Compatibility and recovery
--------------------------

On the first normal read without a SQLite cache, a valid schema-3 JSONL snapshot
is imported with its original refresh timestamp and discovery provenance.
``projects.jsonl`` is preserved. A small ``projects.sqlite3.initialized`` marker is
persisted before the first database publication. Keeping it prevents stale legacy
records from reappearing if the database is subsequently removed. After a failed
first publication the resolver rebuilds from configuration. Completion never
migrates, creates configuration, loads history or scans roots; before migration
it can read the legacy cache.

An absent, unsupported or corrupt cache is rebuilt through the resolver from
configuration and a complete scan. Failed scans do not replace the previous
cache. If configuration is unchanged it remains usable. Invalid or unreadable
configuration is reported rather than reset.
An existing SQLite database takes precedence over legacy data. If you tested
the unreleased ``projects.idx`` prototype, run ``pcd refresh`` once to rebuild
from current configuration. The prototype file is no longer read.

Returning to the published v0.2.8 tag uses its separate JSONL file. Run ``pcd refresh`` after a
downgrade to update that cache from current configuration. Do not run alternating
versions expecting their disposable caches to stay synchronized. Registered
roots and manual projects remain available to both versions. After downgrade,
the released version reads its old JSON history. Visits recorded in the newer
SQLite database are available again when you return to the development build.

Lookup and picker contract
--------------------------

The resolver makes one storage-independent lookup call per attempt and owns
refresh/retry behavior. An unavailable project cache is rebuilt before accepting
a learned-directory match, so a weaker learned name cannot hide an exact
registered project. A unique exact project name resolves through the index.
A fuzzy query also considers learned directories. The first result window reads
project usage history for ranking only when at least two entries match. Exact
duplicates remain separate choices in the picker.
The directory-history database indexes the casefolded basename of each
learned path. For an imported directory name, pcd retrieves exact candidates
through this index and checks for weaker matches so they remain selectable.
For a fuzzy query, SQLite filters names that can contain the query characters
in sequence. For several words, it applies that filter to the normalized path.
The existing scorer still decides which candidates match and in what order.
Broad queries can require checking many directories for existence.
An empty picker query resolves the full catalog by recency.

Windows search after importing 100,000 directories
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The table compares the previous linear learned-directory lookup with the
indexed version, using equivalent fixtures of 100,000 existing directories
on local disk. Each value is the median of nine runs after two warm-ups.
Exact, fuzzy and multiword queries include starting and exiting pcd.exe.
The broad query resolves the initial window without rendering the picker.

==============================  ===========  ==========
Search                          Before (ms)  After (ms)
==============================  ===========  ==========
Exact imported name                5,119.3       331.7
Selective fuzzy                    4,495.4       324.9
Two path components                8,635.5       786.5
Broad, 99,997 matches             10,005.4     5,390.1
==============================  ===========  ==========

For each selective query, SQLite returned one candidate to Python. For the
broad query it returned 99,997 candidates, which still require existence
checks and ranking to produce the total and the first 100 results.
See the `Windows benchmark instructions <../benchmarks/README.md#windows-navigation-after-a-large-import>`_.

The subsequent broad-query optimization streams learned rows in path order
and carries each row's visit data and text score into ranking. When a batch
contains many siblings, a bounded directory listing can verify them together.
Sparse candidates, unavailable listings and ambiguous path spellings use
individual filesystem checks. Each page recomputes the live total.

On a new Windows fixture of 100,000 existing directories, the same broad query
returned 99,997 matches. Across nine fresh Python processes per version after
two warm-ups, the first 100 results took 8.34 s before and 2.09 s after this
change (p90: 10.82 s and 2.43 s). Both versions queried the same paths and
scores. A separate comparison verified the ordered first and second pages,
return to the first page, counts and rank cursors. Timings exclude rendering.

The current implementation groups consecutive candidates by parent before
batching them. A group of 1,000 siblings can now be checked with one directory
listing. Ranking consumes SQLite rows directly and constructs project objects
for the selected window. The catalog and history also share the local SQLite
URI encoder, avoiding the network-library import on ordinary Windows paths.

In a repeat comparison on one Windows fixture, the previous implementation
took 2,625 ms and the current one took 838 ms (medians of nine fresh processes
after two warm-ups). The slowest current run took 874 ms. Resolution inside
the process took 2,123 ms and 528 ms respectively. A secondary five-run check
with two manual registrations in the configuration took 949 ms median and
976 ms maximum. The query still returned 99,997 matches; page paths, offsets
and rank cursors agreed with the previous implementation.

Picker windows
~~~~~~~~~~~~~~

Ranked lookup returns at most 100 projects, a total count and rank cursors. Cursor-based
next/previous windows keep memory bounded independently of how far the user
scrolls. Arrow navigation can reach every duplicate even when there are more
than 100 identical names. Editing the query starts a new lookup against the
whole catalog instead of filtering only the original matches. Edits and scrolling
never refresh the filesystem; a missing match stays empty until an explicit
refresh or a normal command-line lookup. ``--project`` and ``remove`` retain
exact-name matching for nonempty edited queries.

Validation gate
---------------

Required checks cover legacy import without scanning/config writes, corrupt and
future cache versions, interrupted publication, stale concurrent refreshes,
Unicode exact/prefix queries, duplicates across windows, stable history ranking,
full-catalog picker edits, cache-only completion, bounded allocations and handle
cleanup. The normal quality, coverage, shell and package checks still apply.

Algorithmic bounds
------------------

For N projects, M matches and a window of K=100, exact/prefix metadata lookup is
O(log N + M). Unique exact lookup decodes one project and does not read history.
Completion returns all prefix matches in catalog order without ranking them, so
its output memory grows with M.

Fuzzy matching visits metadata once, with ranking O(M log K) instead of sorting
all matches in O(M log M). String scoring still depends on query/name lengths.
At most K payloads are decoded and retained; rank cursors keep this bound even on
later pages. Full listing and refresh necessarily materialize the catalog.
The database and its index occupy O(N) space.

Release checks
--------------

Run the storage and CLI comparisons described in
`the benchmark guide <../benchmarks/README.md>`_. The storage harness checks
equivalent results before timing. The CLI harness exercises the command,
including imports, history I/O and shell completion. Review the Linux, macOS
and Windows reports before release.

Cross-platform CLI and storage comparison
-----------------------------------------

The `release comparison <https://github.com/jestenough/pcd/actions/runs/44695085828>`_
ran v0.2.8 and master at ``9a1679c`` on each runner with Python 3.14.
The CLI timings below are medians of seven fresh processes, on warm fixtures
without prior visits. Values are baseline / candidate. Broad search measures
the initial selection on registered projects, excluding interactive rendering.

.. csv-table::
   :header: "Operation (ms)", "Linux", "macOS", "Windows"

   "Startup (--help), 1k projects", 122 / 105, 94 / 82, 168 / 141
   "Exact last name, 1k projects", 131 / 124, 93 / 93, 184 / 164
   "Exact last name, 100k projects", 448 / 126, 342 / 119, 619 / 161
   "Broad search, 100k projects", 2252 / 1079, 2097 / 1180, 2604 / 1398
   "Completion returning all 100k names", 4419 / 4939, 3701 / 4037, 4854 / 5360
   "Refresh of 2k repositories in one root", 346 / 349, 237 / 238, 633 / 687

At 100k projects, peak RSS for the broad CLI search fell from 208 to 43 MiB
on Linux, 254 to 48 MiB on macOS and 265 to 45 MiB on Windows. Returning all
completion entries took 9–12% longer. The SQLite completion path reads and
decodes project payloads before rendering names and display paths. Profile
that path before choosing an optimization. Windows refresh took 8.6% longer
on the wide fixture. With 2,048 visits, full history reads also took longer
on all three runners (Linux 102 / 115 ms, macOS 83 / 108 ms, Windows 137 / 153 ms).

The separate storage fixture measured 100k discovered projects. Values are
legacy JSONL / SQLite. Path lengths differ across runners; compare formats
within a column.

.. csv-table::
   :header: "Storage operation", "Linux", "macOS", "Windows"

   "Build (ms)", 2551 / 1114, 1543 / 805, 3204 / 1636
   "Disk footprint (MiB)", 24.2 / 39.6, 41.0 / 67.7, 39.5 / 66.4
   "Exact lookup (ms)", 482 / 104, 323 / 81, 689 / 136
   "Broad fuzzy lookup (ms)", 2571 / 411, 1534 / 271, 2982 / 470

Cross-platform shell latency
----------------------------

The `shell jump comparison <https://github.com/jestenough/pcd/actions/runs/44696943676>`_
at ``979c282`` used two registered directories and the default shell integrations.
Each median includes 42 verified exact-name jumps in a persistent shell after
six warm-up jumps per tool. The timer includes the local pipe round trip and
stops when the shell reports its destination. Shell startup and prompt hooks
are outside the measured interval.

================  ============  =================  ==========
Runner / shell    PCD (ms)      zoxide 0.9.8 (ms)  Time ratio
================  ============  =================  ==========
Linux / Bash      168.79        1.95               86.5x
macOS / Bash      381.91        13.87              27.5x
Windows / pwsh    376.17        28.15              13.4x
================  ============  =================  ==========

The default PCD wrapper invokes the CLI to resolve the destination, then
invokes it again to record the successful directory change. Profile both
invocations separately before attributing the timing gap to storage.

Local Windows follow-up (Python 3.15.2, PowerShell 7)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Compared with ``0ac4142``, the candidate defers the TOML editing library until
needed, records visits without opening the project catalog, and reuses common
paths when decoding and formatting completion entries. A refresh reuses one
JSON encoder when writing its SQLite payloads.

.. csv-table::
   :header: "Operation", "Before (ms)", "After (ms)"

   "Real shell jump (42 samples)", 613.1, 555.0
   "Resolver process (9 samples)", 272.7, 250.6
   "Post-change recorder process (9 samples)", 384.3, 327.6
   "Completion: 100k names, empty history", 2937.6, 2383.9
   "History load: 2,048 visits", 212.7, 188.7
   "Refresh: 2,000 repositories in one root", 897.3, 829.1

The process timings are separate medians from alternating baseline and
candidate runs. They are independent of the real shell-jump samples. The
last three rows use three fresh processes per version after warm-up.
A second candidate run with ``--phases`` measured 580.8 ms per shell jump,
228.4 ms for resolution and 335.8 ms for recording (21 samples per phase).
Compare within a row; the hosted runners above used different environments.

In a separate run against the v0.2.8 tag on the same machine, full completion
with empty history took 2,351 ms versus 2,652 ms in v0.2.8. Loading 2,048
visits took 188 ms versus 193 ms, while reading empty history took 183 ms
versus 177 ms. An 11-sample refresh comparison found a remaining regression
against v0.2.8: 820 ms versus 779 ms for a wide root (+5.2%), and 882 ms
versus 803 ms for repositories grouped under subdirectories (+9.8%).

A local launcher comparison used one Windows machine, Python 3.15.2,
the same checkout and the same warm SQLite database. Across 21 measured
calls per operation, a fresh Python process running ``pass`` took 58 ms.
A fresh process importing ``pcd_cli.cli`` took 309 ms including startup and
exit. The default resolver process took 346 ms. In a separate in-process
probe, warm exact lookup took about 1 ms and a committed project visit took
83–89 ms. These independent medians cover different boundaries.

The packaged launcher now routes the two ordinary ``_visit`` notifications
directly to the existing history writer. Other argument forms, including
help and completion, use Click. Alternating the old and new launchers reduced
the median recorder call from 410 to 347 ms and the real PowerShell jump from
770 to 733 ms. Separate runs of the end-to-end harness gave 774 and 769 ms;
the smaller difference illustrates the sensitivity to run order and host load.

PowerShell jumps on small catalogs
----------------------------------

Local Windows measurements compared commit ``d2787d3`` with the indexed
shortcut for unique exact names. Each value is the median of 21 runs after
three warmups, using the same Python interpreter and a populated SQLite cache.

===========================================  ========  ========
Operation                                    Baseline  Shortcut
===========================================  ========  ========
Full PowerShell jump, 1 project              688 ms    597 ms
Full PowerShell jump, 25 projects            652 ms    568 ms
Resolve exact name, 25 projects              309 ms    229 ms
Resolve partial name, 25 projects            309 ms    310 ms
===========================================  ========  ========

The full jump includes changing directory and recording the visit.
The resolution measurements end when the command returns the destination.

Manual projects and concurrent terminals
---------------------------------------

A separate Windows PowerShell 7 comparison used the working tree with the
exact-name shortcut as its baseline, then applied the configuration, history
and recovery fixes. Both versions used the same Python 3.15 interpreter.
The 1- and 25-project fixtures registered each project in TOML. Two terminals
shared a history database per version. Every destination and final visit count
was verified. Values below show the elapsed time per jump in milliseconds.

.. csv-table::
   :header: "Projects", "Terminals", "Median before", "Median after", "p95 before", "p95 after"

   1, 1, 454.4, 438.9, 583.7, 626.3
   1, 2, 505.2, 514.0, 674.5, 645.8
   25, 1, 441.1, 442.0, 593.7, 512.9
   25, 2, 510.2, 481.2, 730.5, 579.0

Each row alternated versions over 21 measured rounds after three warmups.
Two terminals produced 42 samples per version. With 25 manual projects the
recorder now resolves zero registered project paths to read an empty exclusion
list, down from 25. Single-terminal median latency at that size was within
1 ms of baseline. The largest improvement was in the two-terminal p95.
The single-project, single-terminal p95 increased in this run. Reproduce
with ``benchmarks/small_shell_jumps.py`` as described in the benchmark guide.

Releasing history reads in batches trades some throughput for shorter writer
waits. In a separate local probe of 100,000 history rows, consuming broad-query
metadata took 173.5 ms versus 134.8 ms before, excluding filesystem checks.
With a candidate iterator paused after its first row, a second connection
previously timed out after 5.4 seconds and lost the visit. On the updated
code all 24 test writes committed while the iterator remained paused. After
three warmups the median write took 5.22 ms and p95 was 10.13 ms. This probe
measures the history writer in-process, excluding shell and Python startup.

Local validation of the SQLite implementation
---------------------------------------------

Linux container (Python 3.14.7): 514 tests passed, 18 Windows-specific cases
skipped, branch-aware coverage 95.27%. The installed 0.3.0 wheel passed the
isolated CLI smoke test. Windows (Python 3.15.2): 503 tests passed, 17 skipped,
and 12 failed while creating symlinks (WinError 1314, missing privilege).
Windows branch-aware coverage reached 95.70%. The cases exercising JSONL
import, rollback after process termination, read-only completion during
recovery and stale writes passed on both systems. All six performance limit
checks passed on each platform. At 100k projects, SQLite fuzzy lookup retained
at most 100 results, with a traced peak of 115 KiB on Windows and 125 KiB
on Linux in the in-process test. The later master test matrix passed on macOS.

Storage measurements
--------------------

Measurements below precede the exact-navigation startup fix. They are medians
of five fresh-process runs after warm-up on 100,000 synthetic discovered
projects. Times include interpreter startup and imports. The prefix returns
1,000 projects. Broad fuzzy ranking returns a window of 100. Disk figures are
decimal MB and depend on path lengths. Build time is one publication, excluding
the directory scan. Linux ran in a local Docker container under WSL2.
The two formats used identical fixtures within each platform.

============================  =============  ==============  ===========  ============
Operation                     Windows JSONL  Windows SQLite  Linux JSONL  Linux SQLite
============================  =============  ==============  ===========  ============
Exact lookup (ms)             521            178             428          109
Prefix completion (ms)        548            206             399          120
Broad fuzzy lookup (ms)       1,975          407             1,850        279
Broad fuzzy Python peak (MB)  240.4          5.5             197.9        3.7
Broad fuzzy process RSS (MB)  291.1          34.1            238.2        31.9
Cache publication (ms)        1,911          961             2,014        917
Persistent cache (MB)         40.2           62.0            25.4         41.5
============================  =============  ==============  ===========  ============

At 1,000 projects, fresh-process exact lookup took 152 ms with JSONL and
191 ms with SQLite on Windows, and 94 ms and 110 ms on Linux. The large
catalog speedup therefore does not establish the small-catalog release
criterion. In a separate Windows CLI comparison with release v0.2.8 at 1k
projects, exact navigation took 221/231 ms (JSONL/SQLite, empty history)
and 237/251 ms (1k history entries), medians of five runs.
Machine-specific reports are in ``benchmarks/baselines/``.

Windows CLI comparison with the indexed-file prototype
-------------------------------------------------------

This prototype comparison also predates the startup fix. Three fresh-process
timing samples per version were taken after warm-up, alternating
execution order. Both versions used the same interpreter, empty history and
fixture paths of equal length. Unlike the storage microbenchmark, the exact
navigation includes history recording, and completion emits Click's output
for all matching names. The refresh measurements include directory scanning.

=================================  ============  ========
Operation                          projects.idx  SQLite
=================================  ============  ========
Exact navigation, 1k projects      297 ms        292 ms
Exact navigation, 100k projects    220 ms        234 ms
Complete all 100k names            3,164 ms      3,449 ms
Refresh 250 repositories, wide     265 ms        281 ms
Refresh 250 repositories, grouped  256 ms        279 ms
=================================  ============  ========

With 2,048 history entries the 100k exact navigation measured 248/255 ms
(indexed file/SQLite), and full completion measured 2,962/2,971 ms.
Completion ignores history; the spread between those timings illustrates
run-to-run variability. The comparison used the pre-SQLite Git tree
``7e47403d510f9cdb4c8a0c727ca77241cb91ff94`` and the uncommitted SQLite
source digest ``1c3632cd5bfe9374776ac97990576c19b48097cd5ae9bbd37b3af56e3f3299ce``.

Exact navigation at 1,000 projects
----------------------------------

Windows profiling identified two startup costs. On Python 3.15 the first call
to ``Path.as_uri()`` imported ``urllib.request`` to encode the SQLite filename
(21-23 ms in the diagnostic run). The CLI also looked up the installed package
version when registering commands, including in 0.2.8. The SQLite store now
encodes local file paths directly, preserving percent escaping and read-only
open mode. Windows UNC and device paths retain the pathlib conversion. Package
metadata is now read only when the user requests ``--version``.

Median end-to-end time (ms) for ``pcd project-000999`` on a warm filesystem:

===============  ===============  =======  =============  =============
Platform         History entries  v0.2.8   SQLite before  SQLite after
===============  ===============  =======  =============  =============
Windows 3.15.2   0                230.7    263.8          203.5
Windows 3.15.2   1,000            247.0    263.8          205.7
Linux 3.14.7     0                117.5    139.2          113.3
Linux 3.14.7     1,000            122.8    142.4          117.0
===============  ===============  =======  =============  =============

Versions ran in balanced order (all six permutations, three times) after one
warm-up per version and history size. Each sample started a new Python process,
restored the same initial history before timing, and checked the destination
path and shell protocol exit code 10. Times include imports, lookup, recording
usage and process exit. Linux ran in a local container. The measurements
compare release v0.2.8, pre-fix SQLite tree ``be77c6b1``, and the updated
source with SHA-256 prefix ``5a5bd395ae62``. This historical three-way run
covered Windows and Linux.

To repeat the comparison against a single baseline with the repository harness::

    poetry run python benchmarks/compare_cli.py --baseline v0.2.8 --sizes 1000 --tree-sizes --operations exact-last --repeats 18 --output benchmarks/baselines/exact-1k.json
