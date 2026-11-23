PCD 0.3.0 can remember directories visited with ordinary `cd`, import an existing zoxide history, and return you to the subdirectory where you last worked.

## Everyday navigation

- Enable directory tracking with `PCD_TRACK_CD=1` before the managed shell startup block. Successful directory changes are remembered across sessions, including visits outside Git repositories. [Shell setup](https://github.com/jestenough/pcd/blob/v0.3.0/docs/shells.rst)
- Use ordered words to narrow a search. For example, `pcd acme api` finds an API checkout under `acme`. A unique exact project name opens directly. Multiple matches open the picker, where frequency and recency help rank equally good text matches.
- Preview a zoxide import with `pcd import zoxide --dry-run`, then apply it with `pcd import zoxide`. Reimporting the same data preserves preferences without doubling them.
- Use `pcd PROJECT --resume` to return to its last tracked subdirectory. If it has disappeared, PCD explains why and opens the project root.

## Project management and diagnostics

- Inspect a project's path, source and discovery routes with `pcd info PROJECT`.
- Exclude a discovered project from future scans with `pcd remove PROJECT`. Its directory stays on disk. Restore it with `pcd add PATH`.
- Run `pcd doctor` to check configuration, catalog integrity, history and shell integration.

## Storage and upgrades

PCD migrates the legacy JSONL project catalog and JSON visit history to separate SQLite databases. Deleting the project cache and running `pcd refresh` preserves visit counts, imported preferences and resume paths. If you downgrade to 0.2.8, run `pcd refresh` to rebuild its cache. That version reads its own JSON history. [Migration and recovery](https://github.com/jestenough/pcd/blob/v0.3.0/docs/storage-030.rst)

## Shell integration and performance

Interactive Bash on Linux and macOS reuses a worker for unique exact, substring and subsequence matches. Ambiguous queries still open the picker. PowerShell 7 on Windows also reuses a worker for exact jumps and visit recording. On Windows, the setup and troubleshooting guidance covers both blocked PowerShell profiles and blocked executables. [Windows guide](https://github.com/jestenough/pcd/blob/v0.3.0/docs/windows.rst)

Median time to the next Bash prompt with history enabled, measured between two projects over 42 timed jumps per scenario. The first-jump timer starts after the initial prompt of a fresh shell. [Benchmark run](https://github.com/jestenough/pcd/actions/runs/45126893952)

| Bash jump | Linux | macOS |
| --- | ---: | ---: |
| First exact jump | 58.52 ms | 78.62 ms |
| Warm exact jump | 2.69 ms | 4.08 ms |
| Warm substring match | 3.46 ms | 4.49 ms |
| Warm subsequence match | 3.41 ms | 4.90 ms |

## Known limitation

On the Windows runner, refreshing 2,000 Git repositories in groups of 25 took 615 ms versus 584 ms in 0.2.8 (+5.2% by median). A flat root took 544 ms versus 549 ms. [Refresh comparison](https://github.com/jestenough/pcd/actions/runs/45119795590)

[Full changelog from v0.2.8 to v0.3.0](https://github.com/jestenough/pcd/compare/v0.2.8...v0.3.0)
