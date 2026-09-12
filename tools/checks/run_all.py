#!/usr/bin/env python3
"""run_all.py -- run every gate, in order, and report a single table.

The plan requires that after each executed step *all* earlier steps are re-verified
(AGENTS.md step 4). This is the command that does it, so the claim is mechanical rather
than remembered:

    .venv/Scripts/python tools/checks/run_all.py

Exit code 0 iff every gate passed.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKS = Path(__file__).resolve().parent


def summary_line(out: str) -> str:
    """Last meaningful line, which every gate prints as its verdict."""
    for line in reversed([ln.strip() for ln in out.splitlines() if ln.strip()]):
        if 'GATE' in line.upper() or 'passed' in line or 'PASS' in line.upper():
            return line
    return ''


def main() -> int:
    gates: list[tuple[str, Path]] = [('target', ROOT / 'tools' / 'target.py')]
    gates += [(p.stem.replace('check_', ''), p) for p in sorted(CHECKS.glob('check_*.py'))]

    rows = []
    failed = 0
    for name, path in gates:
        if not path.exists():
            rows.append((name, '-', 'MISSING', ''))
            failed += 1
            continue
        t0 = time.time()
        proc = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
        dt = time.time() - t0
        out = (proc.stdout or '') + (proc.stderr or '')
        if proc.returncode != 0:
            failed += 1
        rows.append((name, f'{dt:5.1f}s', f'exit={proc.returncode}', summary_line(out)))

    w = max(len(r[0]) for r in rows)
    sw = max(len(r[1]) for r in rows)
    for name, dur, code, summary in rows:
        print(f'  {name:<{w}}  {dur:>{sw}}  {code:<6}  {summary}')
    print()
    if failed:
        print(f'ALL GATES: FAIL ({failed} of {len(rows)} failing)')
        return 1
    print(f'ALL GATES: PASS ({len(rows)} gates)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
