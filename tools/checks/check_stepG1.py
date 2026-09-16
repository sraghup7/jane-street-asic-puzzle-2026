#!/usr/bin/env python3
"""check_stepG1.py -- G1: the answer is derived without the published answer.

The review (R5, R8) found the pipeline read tools/target.py to choose the region map and to build
its test boards, while the README called the published answer "a yardstick only". This gate makes
the claim mechanical: every stage that derives something is run with the answer made unreadable,
and must still produce the committed artifact byte-for-byte. Only the stages whose *job* is to
compare with the contract (D4's comparison fields, E3) are exempt.

Re-running `region-map` and `confirm` costs the same as the real stages do (roughly 5 minutes
together), so this is excluded from `fault_inject.py`'s default grid.

    .venv/Scripts/python tools/checks/check_stepG1.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.checks import _regen as R          # noqa: E402

# (module, output attributes, stage) -- the stages that consume the region map or a board
STAGES = [('tools.puzzle.verdict', ('OUT_C4',), 'region-map'),
          ('tools.puzzle.verdict', ('OUT_C5',), 'rejections'),
          ('tools.puzzle.verdict', ('OUT_E1',), 'winning'),
          ('tools.puzzle.confirm', ('OUT',), 'confirm')]
checks: list[dict] = []


def check(name, passed, detail=''):
    checks.append({'check': name, 'passed': bool(passed), 'detail': detail})


def main() -> int:
    import tools.target as T
    saved = {k: getattr(T, k) for k in ('GRID', 'FEED_ORDER', 'WITNESS_AS_PRINTED')}
    try:
        for k in saved:                     # the answer is not available to anything below
            setattr(T, k, None)
        for module, attrs, stage in STAGES:
            art = ROOT / 'recon' / 'derived' / {'region-map': 'c4_partition.json',
                                                 'rejections': 'c5_rejections.json',
                                                 'winning': 'e1_messages.json',
                                                 'confirm': 'e2_messages.json'}[stage]
            try:
                rc, produced, _out = R.regenerate(module, attrs, stage)
                same = produced == art.read_bytes()
                check(f'{stage} runs without the published answer and reproduces its artifact',
                      rc == 0 and same, f'exit {rc}, identical={same}')
            except Exception as exc:            # a read of T.GRID etc. lands here
                check(f'{stage} runs without the published answer and reproduces its artifact',
                      False, f'{type(exc).__name__}: {exc}')
    finally:
        for k, v in saved.items():
            setattr(T, k, v)
    failed = [c for c in checks if not c['passed']]
    for c in checks:
        print(f'  {"PASS" if c["passed"] else "FAIL"}  {c["check"]}  {c["detail"]}')
    print(f'\n{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print('STEP G1 GATE: FAIL')
        return 1
    print('STEP G1 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
