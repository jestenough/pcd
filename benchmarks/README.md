# CLI performance comparison

Run from the checkout with project dependencies installed:

```sh
poetry run python benchmarks/compare_cli.py --baseline master --output cli-comparison.json
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
- one warm-up followed by seven timing samples, alternating revision order;
- separate memory runs with Python allocation peak and process peak RSS;
- cache publication time and disk usage.

Timing includes process startup, imports, cache/history I/O and process exit.
Exact/selective navigation writes history normally. The broad fuzzy case loads
the real picker and computes its initial result, but replaces the interactive
terminal loop with cancellation so it cannot wait for a human. It does not
measure terminal rendering or typing latency. Completion invokes Click's protocol,
not a real shell wrapper. Filesystem caches are warm; this is not a cold-disk test.

Memory is measured in separate processes: Python allocation peaks with
`-X tracemalloc`, and peak process RSS without tracing overhead.

JSON contains raw timing samples, medians, memory observations, interpreter,
platform and baseline commit. Save measurements are single observations rather
than distributions. Do not infer a platform-wide guarantee from one local run.

The `CLI baseline comparison` workflow runs the same matrix on Linux, macOS
and Windows for relevant pull requests, and accepts a baseline revision for
manual runs. Download its three artifacts before marking cross-platform
performance validation complete.
