"""Measure an exact jump through the next interactive Bash prompt, including history hooks."""

import argparse
import json
import math
import os
import select
import shlex
import sqlite3
import statistics
import subprocess
import tempfile
import time
from contextlib import closing
from pathlib import Path


def prompt(proc: subprocess.Popen[bytes]) -> bytes:
    assert proc.stdout is not None
    data = bytearray()
    deadline = time.monotonic() + 20
    while not data.endswith(b"@END@"):
        if not select.select([proc.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
            raise TimeoutError(f"Bash prompt timed out: {data[-300:]!r}")
        part = os.read(proc.stdout.fileno(), 4096)
        if not part:
            raise RuntimeError(f"Bash exited: {data[-300:]!r}")
        data.extend(part)
    return bytes(data)


def measure(root: Path, mode: str) -> list[float]:
    root.mkdir()
    home = root / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PCD_", "_PCD_", "_ZO_"))}
    env.update(
        HOME=str(home),
        BENCH_ROOT=str(root),
        _ZO_DATA_DIR=str(home / "zo"),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_CACHE_HOME=str(home / "cache"),
        XDG_STATE_HOME=str(home / "state"),
        TERM="dumb",
        HISTFILE="/dev/null",
    )
    names = ("jump-alpha", "jump-beta")
    for name in names:
        target = root / name
        target.mkdir()
        tool = "zoxide" if mode == "zoxide" else "pcd"
        subprocess.run(
            [tool, "add", str(target)], env=env, cwd=root, check=True, capture_output=True
        )
    init = (
        'eval "$(zoxide init bash)"'
        if mode == "zoxide"
        else 'eval "$(command pcd shell init bash --track-cd)"'
    )
    env["PCD_BASH_WORKER"] = "1" if mode == "worker" else "0"
    rc = home / "bench.rc"
    if mode == "worker":
        init += "\nPROMPT_COMMAND+='; if [[ $PWD != $BENCH_ROOT && -z ${_pcd_worker_active:-} ]]; then exit 91; fi'"
    rc.write_text(init + "\nPS1='@READY@${PWD}@END@'\n", encoding="utf-8")
    values = []
    with subprocess.Popen(
        ["bash", "--noprofile", "--rcfile", str(rc), "--noediting", "-i"],
        env=env,
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ) as proc:
        assert proc.stdin is not None
        try:
            prompt(proc)
            for i in range(48):
                name = names[i % 2]
                command = "z" if mode == "zoxide" else "pcd"
                line = f"{command} {shlex.quote(name)}\n".encode()
                start = time.perf_counter_ns()
                proc.stdin.write(line)
                proc.stdin.flush()
                output = prompt(proc)
                elapsed = (time.perf_counter_ns() - start) / 1e6
                if not output.endswith(f"@READY@{root / name}@END@".encode()):
                    raise RuntimeError(f"Wrong destination: {output!r}")
                if i >= 6:
                    values.append(elapsed)
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    if mode == "zoxide":
        listing = subprocess.check_output(
            ["zoxide", "query", "--list", "--score"], env=env, text=True
        )
        scores = {
            path: float(score)
            for line in listing.splitlines()
            for score, path in [line.split(maxsplit=1)]
        }
        # One seeded visit + 24 prompt-hook visits; fresh zoxide scores have weight four.
        assert all(scores[str(root / name)] == 100 for name in names), scores
    else:
        (history,) = home.rglob("directories.sqlite3")
        with closing(sqlite3.connect(history)) as db:
            counts = dict(db.execute("SELECT path, count FROM visits WHERE learned=1"))
        assert all(counts[str(root / name)] == 24 for name in names), counts
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        data = {
            mode: measure(Path(temp).resolve() / mode, mode) for mode in ("pcd", "worker", "zoxide")
        }
    args.output.write_text(json.dumps(data, indent=2), encoding="utf-8")
    for mode, values in data.items():
        print(mode, statistics.median(values), sorted(values)[math.ceil(0.95 * len(values)) - 1])


if __name__ == "__main__":
    main()
