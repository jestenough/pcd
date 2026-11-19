Performance and safety audit
============================

Measured on Windows with Python 3.15.2 on 2026-11-17. Baseline: ``ba1bd94``
(0.2.8). Audit candidate: the source snapshot later committed as ``925793c``,
whose core-profiler SHA-256 starts with ``38b95aff9e87``. These are local, warm-filesystem measurements,
not cross-platform guarantees.

Measurements
------------

The component profiler uses 1,000 projects and eight nested roots unless noted.
Timings exclude fixture setup, process startup and instrumentation. Baseline
medians use three samples; candidate medians use five. The 128-root comparison
uses three samples for both revisions.

.. list-table:: Component medians
   :header-rows: 1

   * - Operation
     - Before
     - After
   * - Root-count table, nine roots including the parent
     - 1,989 ms
     - 2.9 ms
   * - Nested discovery projection, eight nested roots
     - 42.1 ms
     - 24.3 ms
   * - Nested discovery projection, 128 nested roots
     - 521.9 ms
     - 27.7 ms
   * - Fuzzy search
     - 0.86 ms
     - 0.44 ms
   * - Search with no match
     - 0.57 ms
     - 0.16 ms

Root counting previously traversed projects separately for each registered root
and repeatedly resolved the same paths. It now visits discovery routes once,
deduplicates roots per project and resolves each distinct discovery root once.
For P projects, R roots and D routes per project, counting changes from
O(R * P * D) to O(P * D + R), excluding filesystem path-resolution costs.

Nested discovery projection now looks up the ancestors of each project route
in a root index. It no longer compares every route against unrelated nested
roots; its work depends on path depth and the number of matching routes. Alias
provenance, discovery order and duplicate suppression are preserved.

Fuzzy matching retains its greedy scores and ordering, but uses string searches
instead of a Python loop over every name character. Its worst-case scan remains
linear in the input text. Cache writes encode one complete JSON line at a time;
they still stream projects instead of building a second full catalog in memory.

A CLI comparison covered empty, 1,000- and 10,000-project caches, empty/bounded
history and 250-repository trees. A final seven-sample check at 10,000 projects
put completion at 482.4 -> 484.2 ms with empty history and 481.1 -> 482.2 ms with
2,048 history entries. Startup stayed around 250 ms. Component improvements do
not imply the same speedup for an entire CLI invocation. Exact/prefix cache
lookups in that audit revision remained linear JSONL reads. The separate 0.3.0
storage candidate is described in `storage-030.rst <storage-030.rst>`_.

Safety and cleanup
------------------

* Close the temporary-file descriptor if opening its text stream fails, before
  removing the temporary file. A regression reproduces the previous leak.
* Preserve configuration on access errors instead of treating failed metadata
  access as absence and writing defaults.
* Escape terminal controls in displayed names/paths, and omit unsafe names from
  the line-based completion protocol. JSON and stored values retain their data.
* Recover from JSON nesting/integer decoder limits and non-finite or overflowing
  history timestamps. Validate cache sources even in unmatched lookup rows.
* Preserve literal exclamation marks and carets in CMD destinations with delayed
  expansion enabled. Refresh an installed managed wrapper with
  ``pcd shell install cmd``.

Reproduction
------------

See the `profiling commands <../benchmarks/README.md>`_. Local raw results are in
the ignored ``benchmarks/baselines/`` directory: ``audit-before.json``,
``audit-after.json``, ``audit-nested-before.json``, ``audit-nested-after.json``,
``audit-cli.json`` and ``audit-cli-final.json``. The last CLI file rechecks startup,
broad fuzzy selection and completion after optimizing printable-text handling;
each file records its measured source hash.

The five performance-limit tests passed: 100,000-project cache load used
59.7 MiB of traced allocations, and retained allocation growth after 500 searches
was 0.86 KiB. Exhaustive comparison preserved fuzzy scores for 54,600 query/name
pairs. Wheel and source distribution builds passed.

Ruff formatting/lint and strict mypy checks passed. The final Windows pytest run
passed 425 tests with 96.70% total coverage including branch measurement; 17
POSIX tests were skipped and 12 symlink tests were explicitly deselected after
the full run confirmed WinError 1314 (missing symlink privileges).

Windows symlink tests require privileges unavailable on this machine. POSIX
workflow tests also require Linux/macOS verification; the local Docker engine
was unavailable. The installed-launcher wheel smoke test was not run. These
limitations are not evidence of passing those checks.
