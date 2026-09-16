#!/usr/bin/env python3
"""check_step3.py -- executable gate for Step 3 (our own plan).

A plan is only useful if it is mechanically checkable. This gate verifies the plan in
`docs/03_our_plan.md` is internally sound and mutually consistent with the rest of the
project:

  1. every step has a unique ID and a *Verify* clause;
  2. the prohibited-method list agrees with `docs/02_known_solution.md`;
  3. every declared differentiator names at least one step that actually exists;
  4. the traceability matrix covers all six acceptance criteria;
  5. the plan's acceptance constants agree with `tools/target.py`;
  6. nothing in the plan writes into the read-only upstream clone.

    .venv/Scripts/python tools/checks/check_step3.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import target                          # noqa: E402

PLAN = ROOT / 'docs' / '03_our_plan.md'
KNOWN = ROOT / 'docs' / '02_known_solution.md'
UPSTREAM_DIRNAME = 'asic-puzzle-2026'

# NB: re.MULTILINE is essential here -- without it `^` only matches at position 0 and
# the "unique IDs" check degenerates into a single match that trivially passes.
STEP_RE = re.compile(r'^\*\*((?:S\d+\.\d+)|(?:[A-G]\d+(?:\.\d+)?)) — ', re.M)
DELTA_RE = re.compile(r'^\| \*\*Δ(\d)\*\* \|(.*)$', re.M)

# The five prohibited method families, as canonical keywords. Both Step 2 and Step 3
# must name every one of them.
PROHIBITED_KEYWORDS = {
    'shapely polygon merge': ('shapely', 'unary_union'),
    'pins from PDK LEF': ('LEF',),
    'models from PDK primitives': ('primitives.v',),
    'formal cover via SymbiYosys': ('SymbiYosys',),
    'region map by stimulus probing': ('probing',),
}

results: list[tuple[str, str, object, object]] = []


def check(label, actual, expected):
    results.append(('PASS' if actual == expected else 'FAIL', label, actual, expected))


def skip(label, why):
    results.append(('SKIP', label, why, ''))


def split_steps(text: str) -> dict[str, str]:
    """Map step ID -> its body text (up to the next step heading)."""
    lines = text.splitlines()
    idx = [i for i, ln in enumerate(lines) if STEP_RE.match(ln)]
    steps = {}
    for n, i in enumerate(idx):
        sid = STEP_RE.match(lines[i]).group(1)
        end = idx[n + 1] if n + 1 < len(idx) else len(lines)
        steps[sid] = '\n'.join(lines[i:end])
    return steps


def main() -> int:
    plan = PLAN.read_text(encoding='utf-8')
    known = KNOWN.read_text(encoding='utf-8')

    # ---- 1. step IDs unique, each with a Verify clause -------------------------
    steps = split_steps(plan)
    raw_ids = STEP_RE.findall(plan)
    check('heading count matches discovered steps', len(raw_ids), len(steps))
    check('step IDs are unique', len(raw_ids), len(set(raw_ids)))
    check('at least 20 steps planned', len(steps) >= 20, True)
    without_verify = sorted(sid for sid, body in steps.items()
                            if 'Verify' not in body)
    check('every step has a Verify clause', without_verify, [])

    # ---- 2. prohibited list agrees with Step 2 --------------------------------
    for label, kws in PROHIBITED_KEYWORDS.items():
        check(f'plan names prohibited: {label}',
              all(k in plan for k in kws), True)
        check(f'step 2 names prohibited: {label}',
              all(k in known for k in kws), True)

    # ---- 3. each differentiator references real steps -------------------------
    deltas = DELTA_RE.findall(plan)
    check('six differentiators declared', len(deltas), 6)
    for num, rest in deltas:
        cols = [c.strip() for c in rest.split('|')]
        # columns: Where | Why
        where = cols[1] if len(cols) > 1 else ''
        refs = re.findall(r'\b(?:S\d+\.\d+|[A-F]\d+(?:\.\d+)?)\b', where)
        missing = [r for r in refs if r not in steps]
        check(f'Δ{num} references only existing steps', missing, [])
        check(f'Δ{num} references at least one step', len(refs) > 0, True)

    # ---- 4. traceability covers all acceptance criteria -----------------------
    trace = plan.split('## 10. Traceability', 1)
    if len(trace) < 2:
        check('traceability section present', False, True)
    else:
        body = trace[1].split('## 11.', 1)[0]
        for n in range(1, 7):
            check(f'traceability covers AC{n}', f'AC{n}' in body, True)

    # ---- 5. plan constants agree with tools/target.py -------------------------
    check('plan states answer string', target.ANSWER_STRING in plan, True)
    check('plan states success cycle', str(target.SUCCESS_CYCLE) in plan, True)
    check('plan states output message',
          '(* TWO STARS *)' in plan, True)
    for msg in ('EMPTY SKY', 'BIG BANG', 'TWO NOT TOUCH', 'TRY AGAIN'):
        check(f'plan states message {msg!r}', msg in plan, True)

    # ---- 6. nothing writes into the read-only upstream clone -----------------
    upstream_write = []
    pat = re.compile(rf'.*{UPSTREAM_DIRNAME}.*(?:["\']w|["\']a|write|mkdir|unlink)', re.I)
    for py in sorted((ROOT / 'tools').rglob('*.py')):
        for n, line in enumerate(py.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            if UPSTREAM_DIRNAME in line and not line.lstrip().startswith('#'):
                if re.search(r'open\(|write_text|write_bytes|shutil|os\.remove|unlink|mkdir', line):
                    upstream_write.append(f'{py.relative_to(ROOT)}:{n}')
    check('no tool writes into the upstream clone', upstream_write, [])
    check('plan states the upstream is read-only',
          'read-only' in plan, True)

    # ---- 7. the plan discloses P5's use, not just its prohibition -------------
    check('plan discloses that the region map uses single-star stimulus probing '
          '(prohibited method 5)',
          bool(re.search(r'single-star.{0,120}(prohibited|method 5|P5)', plan, re.S)), True)

    # ---- report ---------------------------------------------------------------
    width = max(len(r[1]) for r in results)
    n_fail = n_skip = 0
    for status, label, actual, expected in results:
        if status == 'FAIL':
            n_fail += 1
            print(f'  FAIL  {label:<{width}}  got {actual!r}  want {expected!r}')
        elif status == 'SKIP':
            n_skip += 1
            print(f'  SKIP  {label:<{width}}  {actual}')
        else:
            print(f'  PASS  {label}')
    passed = sum(1 for r in results if r[0] == 'PASS')
    print()
    print(f'{passed} passed, {n_fail} failed, {n_skip} skipped   '
          f'({len(steps)} steps discovered)')
    if n_fail:
        print(f'STEP 3 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP 3 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
