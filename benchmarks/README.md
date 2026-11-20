# CLI performance comparison

## Core profiling

To locate expensive functions before changing algorithms, run:

```sh
poetry run python benchmarks/profile_code.py --output benchmarks/baselines/profile.json
```

The profiler covers root counts, nested discovery projection, cache reads/writes,
exact/prefix lookup, broad/fuzzy/missing searches, history, config and filesystem
scanning. It uses temporary fixtures, with 1,000 synthetic projects, eight nested
roots and up to 250 real repositories by default. `--size`, `--roots` and `--repeats`
adjust the workload; `--source <checkout>` selects another revision's source tree.
Use `--operations roots nested_discoveries` to focus on individual operations.

Each operation has a warm-up, uninstrumented timing samples, a separate cProfile
run (top cumulative functions), and a separate tracemalloc peak. JSON records the
source hash, interpreter and platform. These are component timings, excluding
process startup and fixture creation. Run comparisons without other tests or
benchmarks running concurrently. Use the CLI comparison below for startup and
end-to-end behavior; the profiler does not measure interactive terminal latency.

## End-to-end comparison

Run from the checkout with project dependencies installed:

```sh
poetry run python benchmarks/compare_cli.py --baseline master --output benchmarks/baselines/local.json
```

The baseline is resolved to a commit and exported to a temporary directory. The
candidate source is copied beside it to avoid comparing source-import costs on
different filesystems. Each CLI invocation runs in a fresh Python process with
isolated configuration, cache, history and home directories. No user projects
or configuration are used.
Both revisions use the same interpreter and installed dependencies.

Default matrix:

- empty cache, 1,000, 10,000 and 100,000 cached projects;
- empty history and up to 2,048 history entries, restored before each invocation;
- startup (`--help`), first/last exact name, broad/selective fuzzy query, and Bash
  completion protocol;
- not-found navigation with an empty cache;
- usage-history load and record at each cache/history size;
- Bash wrapper navigation on POSIX systems with Bash installed (including shell startup
  and a real directory change); the JSON marks it unavailable otherwise;
- wide and grouped scan roots with 250 and 2,000 repositories: refresh, repeated
  exact misses that rescan the roots, and manual add (including conflict scan);
- one warm-up followed by seven timing samples, alternating revision order;
- separate memory runs with Python allocation peak and process peak RSS;
- cache publication time and cache-file size during fixture setup (single observations).

Timing includes process startup, imports, cache/history I/O and process exit.
Tree fixtures include `.git` directories in either one wide root or groups of 25.
For state-changing operations, config, cache and history are restored outside the
timed section before *every* invocation, including warm-ups and memory runs.
Exact misses are repeated against the same root snapshot, not just an empty cache.
History I/O uses the real history implementation in a fresh process (not a CLI
subcommand); the Bash wrapper uses a shim to run the selected revision through
the same worker. Filesystem setup and snapshot restoration are not timed.
Direct exact and selective lookups time destination resolution. In the working
version, the shell records a visit after changing directory, so only a wrapper
measurement includes that step. The broad fuzzy case loads
the real picker and computes its initial result, but replaces the interactive
terminal loop with cancellation so it cannot wait for a human. It does not
measure terminal rendering or typing latency. Completion invokes Click's protocol,
not a real shell wrapper. Filesystem caches are warm; this is not a cold-disk test.

Memory is measured in separate processes: Python allocation peaks with
`-X tracemalloc`, and peak process RSS without tracing overhead. Linux reads the worker process's
VmHWM from /proc/self/status to exclude peak memory inherited from the fixture builder.

JSON contains raw timing samples, medians, p90, memory observations, interpreter,
platform, baseline commit, and a hash of the candidate Python source. Memory is
sampled separately, not as a timing distribution; Bash wrapper memory describes
the CLI child, not the shell. Save measurements are single fixture-setup observations
rather than distributions.
Do not infer a platform-wide guarantee from one local run.

For local runs, use `--output benchmarks/baselines/<name>.json` to keep
machine-specific results outside version control. Re-run with `--sizes` and
`--tree-sizes` for targeted measurements; the default matrix is larger.
Use ``--operations startup exact-first exact-last completion`` to rerun only
those operations; default CI still runs the full matrix. An empty ``--tree-sizes``
argument skips scan fixtures during lookup-only comparisons. The selected
operations are recorded in JSON. Review each platform's CI artifact for shell
wrapper coverage and platform-specific results.

The `CLI baseline comparison` workflow runs the same matrix on Linux, macOS
and Windows for relevant pull requests, and accepts a baseline revision for
manual runs. Download its three artifacts before marking cross-platform
performance validation complete.

## Storage format comparison

```sh
poetry run python benchmarks/storage_candidates.py --output benchmarks/baselines/storage.json
```

Compare the legacy JSONL resolver and the production SQLite store at 1k/10k/100k
projects. The harness checks equivalent results before timing, including Unicode
prefix edges. SQLite reads metadata for ranking and decodes the selected window.
JSONL retains its old exact-first, full-decode fallback. Its output is capped
after sorting for comparison.

Each lookup has a warm-up and five fresh-process timing samples, rotating format
order. Separate processes measure Python allocation peaks and peak RSS. Startup,
open, exact, completion prefix, selective fuzzy and broad fuzzy are included.
Build time is a single observation with file fsync; disk includes persistent side
files. This benchmark does not include directory scanning; the CLI comparison
measures complete refresh and manual-add scenarios. Results record source hashes,
Python/SQLite versions, platform and raw samples. This is warm-cache measurement,
not cold-disk latency. Do not run tests or other benchmarks concurrently.

The performance workflow uploads this comparison on Linux, macOS and Windows.
Review all three reports before releasing the storage replacement. Local numbers
and migration details belong in `docs/storage-030.rst`; raw machine-specific JSON
stays under the ignored `benchmarks/baselines` directory.

## Performance limits and atomic-write cost

These checks are separate from the CLI baseline comparison and its JSON artifacts:

```sh
poetry run pytest benchmarks -q -s
```

`make bench` runs the same command. `benchmarks/test_bench.py` checks cache lookup
and load at 100,000 projects, fuzzy search at 10,000 projects, retained allocations
after repeated searches, and scanning 250 repositories. The SQLite check
uses 100,000 projects, requires a 100-result window and a Python allocation peak
below 2 MiB, and verifies retained allocations after 500 exact lookups. The limits are encoded in
the tests and run in the Linux `Performance limits` CI job. Ordinary `pytest`
collects `tests/` only, so it does not run these benchmarks.

The atomic-write check times batches of 50 replacements with and without
parent-directory synchronization, retaining the best of three batches for each.
It prints the two timings and their difference. File-content fsync remains enabled
in both cases. On Windows the directory-sync step is a no-op, so the difference
is measurement noise rather than a directory-sync durability cost.

## Windows navigation after a large import

Run the following from the checkout in PowerShell. Preparation creates 100,000
real directories under a temporary fixture root. The second command imports
synthetic visit scores through the installed zoxide executable, checks each
destination, and times searches and alternating directory changes in PowerShell.

```powershell
poetry run python benchmarks/windows_navigation.py --prepare
poetry run python benchmarks/windows_navigation.py --zoxide (Get-Command zoxide.exe).Source
```

The JSON report in `benchmarks/baselines/windows-navigation.json` includes
individual samples, medians, p90 and source hashes. Process startup is included
in CLI searches. Shell startup is excluded from the directory-change timings.
To measure searches alone, add `--search-only` to the second command. This
writes `benchmarks/baselines/windows-search.json`. Use `--output` to retain
separate reports for the two versions being compared.

For profiling broad imported-directory results without downloading zoxide again,
`windows_broad_window.py` seeds the same 100,000 existing-directory fixture with
the synthetic import scores directly. Point `--source` at a directory containing
the `pcd_cli` package and keep before/after source snapshots, the Python runtime,
dependencies, and fixture on the same local filesystem:

```powershell
poetry run python benchmarks/windows_broad_window.py --source src --root "$env:TEMP/pcd-broad-fixture" --prepare
poetry run python benchmarks/windows_broad_window.py --source src --root "$env:TEMP/pcd-broad-fixture" --repeats 9 --output benchmarks/baselines/broad.json
poetry run python benchmarks/windows_broad_window.py --source src --root "$env:TEMP/pcd-broad-fixture" --validate --output benchmarks/baselines/broad-windows.json
```

Run both snapshots against the unchanged fixture. The repeated measurement starts
a fresh Python process for every first-100 query, includes imports and exit, and
reports two warmups, individual samples, median and nearest-rank p90. It does not
time zoxide import, a `pcd.exe` launcher, or picker rendering. `--validate` records
the complete ordered first, next and previous windows and their totals/offsets;
compare those artifacts across snapshots, not just their result counts. Add
`--reference <before-windows.json>` to the candidate validation run to assert
equality of every ordered path, rank, total and offset. Add `--profile` in a
separate run for cProfile self/cumulative time and call counts.
Profiler overhead affects wall time, so use the unprofiled runs for comparison.
Do not run timed measurements alongside tests or other benchmarks. Filesystem
caches are warm and antivirus state is uncontrolled.

## End-to-end shell jumps

With the installed `pcd` and `zoxide` executables on PATH, run
`python benchmarks/shell_jumps.py --output shell-jumps.json`. The manual
Shell jump comparison workflow uses zoxide 0.9.8, Bash on Linux and macOS,
and PowerShell 7 on Windows.

The driver seeds two real destinations in isolated stores and times verified
exact-name jumps in one persistent shell. Timing includes the local pipe round
trip and ends when the shell reports its working directory. Each tool makes
six warm-up jumps and 42 measured jumps. Tool order alternates. Shell startup,
prompt rendering, and interactive prompt hooks are outside the measured interval.
