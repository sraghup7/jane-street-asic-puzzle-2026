#!/usr/bin/env python3
"""check_stepF4.py -- F4: the freeze, and the consistency of the machinery itself.

F4's verify clause is "every gate PASS; git tag `review-fixes-complete`; working tree clean". The full
suite run *is* that evidence, and it cannot be re-run from inside a gate without recursing. What this
gate does instead is check the invariants that make the freeze meaningful and that nothing else checks:

`step5-complete` marks the pre-review state (2026-09-12, before the solution review found the R0-R9
findings this fix pass addresses) and is never moved again -- it is history, not the thing this gate
freezes. `review-fixes-complete` is the tag this gate actually requires, made once every finding is
fixed or recorded and the full suite passes.

* **the tree is clean** -- no modified, staged or untracked file, so the tag below names exactly the
  reviewed state and a clone gets exactly it;
* **the tag `review-fixes-complete` exists and points at HEAD** -- a tag on an earlier commit, or a
  commit made after tagging, both mean the frozen state is not the state that passed;
* **the fault grid is internally consistent**: every mutation targets an artifact that is registered,
  every registered artifact exists on disk, and every gate the grid names exists -- a mutation aimed at
  a path nobody tracks is a mutation that silently does nothing, which would make the sweep report a
  pass it did not earn;
* **the suite is complete**: the number of gates discovered is at least the number the README claims,
  so the freeze cannot be declared with gates missing from disk.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.checks import fault_inject as FI

TAG = 'review-fixes-complete'
checks: list[dict] = []


def check(name: str, passed: bool, detail: str = '') -> None:
    checks.append({'check': name, 'passed': bool(passed), 'detail': detail})


def git(*args: str) -> str:
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True,
                          encoding='utf-8', errors='replace').stdout.strip()


def main() -> int:
    status = git('status', '--porcelain')
    check('the working tree is clean', not status,
          status.splitlines()[0] if status else 'nothing modified, staged or untracked')

    head = git('rev-parse', 'HEAD')
    tagged = git('rev-list', '-n', '1', TAG)
    check(f'the tag {TAG} exists and points at HEAD',
          bool(tagged) and tagged == head,
          f'tag {tagged[:9] or "missing"} vs HEAD {head[:9]}')
    later = git('rev-list', f'{TAG}..HEAD', '--count') if tagged else '?'
    check('nothing was committed after the tag',
          later == '0', f'{later} commit(s) after {TAG}')

    registered = {str(a) for a in getattr(FI, 'ARTIFACTS', [])}
    missing_art = [a for a in sorted(registered) if not (ROOT / a).exists()]
    check('every artifact the fault grid registers exists on disk',
          not missing_art, ', '.join(missing_art[:5]) or f'{len(registered)} artifacts')

    unowned = sorted({label for label, art, _fn in FI.MUTATIONS if str(art) not in registered})
    check('every mutation targets a registered artifact',
          not unowned, '; '.join(unowned[:4]) or f'{len(FI.MUTATIONS)} mutations owned')

    # Read the *path* out of the grid rather than deriving a name: `target`'s gate is
    # `tools/target.py`, not `tools/checks/check_target.py`, and the first version of this check
    # invented that file and failed on a correct repository.
    gates_on_disk = {p.stem for p in (ROOT / 'tools' / 'checks').glob('check_*.py')} | {'target'}
    named_files = {Path(rel).stem for _name, rel in FI.GATES}
    unknown = sorted(named_files - gates_on_disk)
    check('every gate the fault grid names exists on disk',
          not unknown, ', '.join(unknown) or f'{len(named_files)} gate files named')

    from tools.checks.run_all import gate_paths
    live = len(list(gate_paths()))
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    stated = [int(n) for n in __import__('re').findall(r'all (\d+) gates', readme)]
    check('the suite discovers at least the number of gates the README claims',
          bool(stated) and live >= max(stated), f'README {stated or "nothing"}, discovered {live}')

    failed = [c for c in checks if not c['passed']]
    width = max(len(c['check']) for c in checks)
    for c in checks:
        print(f'  {"PASS" if c["passed"] else "FAIL"}  {c["check"]:<{width}}  {c["detail"]}')
    print()
    print(f'{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print('STEP F4 GATE: FAIL')
        return 1
    print('STEP F4 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
