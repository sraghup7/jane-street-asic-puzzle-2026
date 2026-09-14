#!/usr/bin/env python3
"""check_stepE4.py -- the E4 gate: is the reproduction path real?

E4's claim is the protocol's Step 5 claim: *fresh clone -> commands -> same result*. What that means
here is stronger than "the command exits 0", and this gate holds each part of it:

* the stage list in the committed report is **the same list the command table defines** -- the pipeline
  cannot quietly omit a stage, because the plan is re-derived from `cli.STAGES` and compared;
* every stage in it resolves to a module that actually has a `main`, so the plan is runnable rather
  than a list of names;
* the run recorded was **clean**: every stage exit code 0, and the acceptance stage last;
* the run recorded was **cold**: tracked derived artifacts were deleted and rebuilt, and the rebuilt
  bytes matched what was committed (`regenerated_identically == tracked_before`, no differences). A
  report from a warm run is not this step's evidence, and the gate says how to produce the right one;
* the artifact contains **no timings**: B3's rule is that a derived artifact must regenerate
  byte-identically, and a clock in a file guarantees it will not. Timings belong on stdout.

The prerequisites are checked live too, so the gate fails if this machine can no longer run the
pipeline (missing iverilog, missing upstream layout) rather than passing on a stale report.
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.puzzle import cli as C

ART = ROOT / 'recon' / 'derived' / 'reproduction.json'
CLOCK_KEYS = ('seconds', 'duration', 'elapsed', 'timestamp', 'started', 'finished', 'time',
              'runtime', 'wall')
MIN_STAGES = 25
MIN_REBUILT = 15

checks: list[dict] = []


def check(name: str, passed: bool, detail: str = '') -> None:
    checks.append({'check': name, 'passed': bool(passed), 'detail': detail})


def find_clock_keys(node, path: str = '') -> list[str]:
    """Any key that smells like a clock, anywhere in the artifact (recursively)."""
    found: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in CLOCK_KEYS:
                found.append(f'{path}.{k}')
            found.extend(find_clock_keys(v, f'{path}.{k}'))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found.extend(find_clock_keys(v, f'{path}[{i}]'))
    return found


def main() -> int:
    if not ART.exists():
        print(f'missing {ART.relative_to(ROOT).as_posix()}')
        print('run: python -m tools.puzzle reproduce --cold')
        return 1
    art = json.loads(ART.read_text(encoding='utf-8'))

    # ---- the plan, re-derived from the command table ---------------------------------
    plan = [s for s in C.STAGES if s[0] != 'reproduce']
    recorded = [(r['stage'], r['module'], r['step']) for r in art['stages']]
    check('the recorded plan is the command table s plan, in order',
          recorded == [(s, m, p) for s, m, p, _ in plan],
          f"{len(recorded)} recorded vs {len(plan)} in cli.STAGES"
          + ('' if recorded == [(s, m, p) for s, m, p, _ in plan]
             else f"; first mismatch "
                  f"{next((i for i, (a, b) in enumerate(zip(recorded, [(s, m, p) for s, m, p, _ in plan])) if a != b), 'length')}"))
    check(f'the plan has at least {MIN_STAGES} stages',
          len(plan) >= MIN_STAGES, f'{len(plan)} stages')
    check('the acceptance stage is the last one',
          plan[-1][0] == 'acceptance' and art['stages'][-1]['stage'] == 'acceptance',
          f"last stage {plan[-1][0]}")

    # ---- every stage is runnable -----------------------------------------------------
    unrunnable = []
    for stage, module, step, _purpose in plan:
        try:
            mod = importlib.import_module(f'tools.puzzle.{module}')
            if not callable(getattr(mod, 'main', None)):
                unrunnable.append(f'{stage}: {module}.main missing')
        except Exception as exc:                      # noqa: BLE001 -- reporting, not handling
            unrunnable.append(f'{stage}: {module} import failed ({exc})')
    check('every stage in the plan resolves to a module with a main',
          not unrunnable, '; '.join(unrunnable) or f'{len(plan)} stages resolve')

    # ---- the recorded run was clean --------------------------------------------------
    check('every recorded stage exited 0',
          art['all_stages_exit_zero'] is True
          and all(r['exit_code'] == 0 for r in art['stages']),
          f"{sum(1 for r in art['stages'] if r['exit_code'] != 0)} non-zero of "
          f"{len(art['stages'])}")
    check('the recorded run ended in an acceptance PASS',
          art['acceptance'] == 'PASS', art['acceptance'])

    # ---- the recorded run was cold, and it agreed ------------------------------------
    cold = art.get('cold_check')
    check('the report is from a COLD run (delete tracked artefacts, rebuild, compare)',
          isinstance(cold, dict) and cold.get('tracked_before', 0) >= MIN_REBUILT,
          'if this fails, regenerate with: python -m tools.puzzle reproduce --cold'
          if not isinstance(cold, dict) else f"{cold.get('tracked_before')} files rebuilt")
    if isinstance(cold, dict):
        check('the rebuilt artifacts are byte-identical to the committed ones',
              cold['differences'] == []
              and cold['regenerated_identically'] == cold['tracked_before'],
              f"{cold['regenerated_identically']}/{cold['tracked_before']} identical, "
              f"{len(cold['differences'])} differing")

    # ---- no clocks in the artifact ---------------------------------------------------
    clocks = find_clock_keys(art)
    check('the artifact carries no timings (B3: byte-identical regeneration)',
          not clocks, ', '.join(clocks[:6]) or 'none')

    # ---- the prerequisites are true now, not just then -------------------------------
    proc = subprocess.run([sys.executable, '-m', 'tools.puzzle', 'reproduce', '--check'],
                          cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                          errors='replace')
    check('the prerequisites still hold on this machine',
          proc.returncode == 0,
          '; '.join((proc.stdout or '').strip().splitlines()[-3:]) if proc.returncode
          else 'upstream layout, iverilog and git all present')
    plan_proc = subprocess.run([sys.executable, '-m', 'tools.puzzle', 'reproduce', '--plan'],
                               cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                               errors='replace')
    missing_from_plan = [s for s, _m, _p, _x in plan if s not in (plan_proc.stdout or '')]
    check('--plan prints every stage',
          plan_proc.returncode == 0 and not missing_from_plan,
          f"{len(plan) - len(missing_from_plan)} of {len(plan)} printed")

    failed = [c for c in checks if not c['passed']]
    width = max(len(c['check']) for c in checks)
    for c in checks:
        print(f'  {"PASS" if c["passed"] else "FAIL"}  {c["check"]:<{width}}  {c["detail"]}')
    print()
    print(f'{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print('STEP E4 GATE: FAIL')
        return 1
    print('STEP E4 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
