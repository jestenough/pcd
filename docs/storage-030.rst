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
cache. Invalid or unreadable configuration is reported rather than reset.
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
refresh/retry behavior. A unique exact project name resolves through the index.
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
on Linux in the in-process test. macOS validation is pending.

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
source with SHA-256 prefix ``5a5bd395ae62``. macOS remains unmeasured.

To repeat the comparison against a single baseline with the repository harness::

    poetry run python benchmarks/compare_cli.py --baseline v0.2.8 --sizes 1000 --tree-sizes --operations exact-last --repeats 18 --output benchmarks/baselines/exact-1k.json
