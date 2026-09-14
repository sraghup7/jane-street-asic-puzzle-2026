#!/usr/bin/env python3
"""repro.py -- E4: the whole pipeline, from nothing, in one command.

The protocol's Step 5 asks for a documented reproduction path: *fresh clone -> commands -> same
result*. This is that path, and it does not just re-run the stages in the happy case:

* the stage list is **derived from `cli.STAGES`**, never duplicated here, so the pipeline cannot drift
  away from the command table;
* prerequisites are checked first (the upstream puzzle layout, `iverilog` for the waveform stages,
  `git` for the cold comparison) and a missing one stops the run with an instruction rather than a
  half-built pipeline;
* `--cold` deletes every **tracked** derived artifact and rebuilds it, then compares the rebuilt bytes
  against `git show HEAD:...` -- which is the strongest form of the claim available here: *delete what
  we derived, rebuild from the upstream layout and the puzzle alone, and get the same bytes back.*
  Only tracked files are deleted, so `git checkout` is always the undo;
* the artifact it writes (`recon/derived/reproduction.json`) records the plan, the per-stage exit
  codes and the byte comparison -- and **no timings**, because B3's rule is that a derived artifact
  must regenerate byte-identically, and a clock in a file guarantees it will not.

    python -m tools.puzzle reproduce --plan     # the plan, and nothing else
    python -m tools.puzzle reproduce --check    # prerequisites only
    python -m tools.puzzle reproduce --cold     # the full claim
    python -m tools.puzzle reproduce            # full run, artefacts kept as they are
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.puzzle import cli as C

OUT = ROOT / 'recon' / 'derived' / 'reproduction.json'
UPSTREAM = ROOT / 'asic-puzzle-2026'

# What the pipeline is expected to regenerate. Deletion in --cold mode is limited to files git
# tracks, so every deletion is undone by `git checkout -- <path>`.
DERIVED_GLOBS = ('build/*.v', 'recon/derived/*.json', 'recon/derived/*.csv',
                 'recon/inventory.json', 'recon/vcd_cycles.csv')


def pipeline() -> list[tuple[str, str, str, str]]:
    """Every stage except this one, in the table's order -- the pipeline order."""
    return [s for s in C.STAGES if s[0] != 'reproduce']


def prerequisites() -> dict:
    """What must be true before the pipeline can run, checked rather than assumed."""
    return {
        'upstream_layout': UPSTREAM.is_dir(),
        'upstream_gds_present': bool(list((UPSTREAM / 'asic-puzzle').glob('*.gds')))
        if (UPSTREAM / 'asic-puzzle').is_dir() else bool(list(UPSTREAM.glob('*.gds'))),
        'iverilog': shutil.which('iverilog') is not None,
        'git': shutil.which('git') is not None,
        'python': sys.executable,
    }


def _fail_missing(prereq: dict) -> list[str]:
    missing = []
    if not prereq['upstream_layout'] or not prereq['upstream_gds_present']:
        missing.append('the upstream puzzle layout: expected asic-puzzle-2026/ with the GDS in the '
                       'working tree (it is read-only upstream input and is gitignored, so a fresh '
                       'clone must obtain it separately -- see README)')
    if not prereq['iverilog']:
        missing.append('iverilog on PATH (the C1/C2/B7 waveform stages need it)')
    if not prereq['git']:
        missing.append('git on PATH (needed for --cold comparisons)')
    return missing


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tracked(path: Path) -> bool:
    return subprocess.run(['git', 'ls-files', '--error-unmatch', str(path.relative_to(ROOT))],
                          cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def derived_files() -> list[Path]:
    out: list[Path] = []
    for g in DERIVED_GLOBS:
        out.extend(sorted(ROOT.glob(g)))
    return out


def run_pipeline(cold: bool, out_path: Path) -> tuple[list[dict], dict, int]:
    """Run every stage in order; stop at the first failure. Returns per-stage rows and the cold check."""
    rows: list[dict] = []
    before: dict[str, str] = {}
    if cold:
        for p in derived_files():
            if _tracked(p):
                before[str(p.relative_to(ROOT))] = _sha(p)
                p.unlink()
        print(f'cold run: removed {len(before)} tracked derived files; git can restore them all\n')

    t_all = time.time()
    rc = 0
    for stage, module, step, purpose in pipeline():
        t0 = time.time()
        proc = subprocess.run([sys.executable, '-m', 'tools.puzzle', stage],
                              cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                              errors='replace')
        dt = time.time() - t0
        ok = proc.returncode == 0
        rows.append({'stage': stage, 'module': module, 'step': step, 'exit_code': proc.returncode,
                     'seconds': round(dt, 1)})
        status = 'ok' if ok else f'FAILED ({proc.returncode})'
        print(f'  {step:<6} {stage:<20} {dt:7.1f}s  {status}   {purpose}')
        if not ok:
            print('\n--- stage output (tail) ---')
            print('\n'.join((proc.stdout or '').splitlines()[-25:]))
            print('\n--- stage stderr (tail) ---')
            print('\n'.join((proc.stderr or '').splitlines()[-25:]))
            rc = 1
            break
    total = time.time() - t_all

    check: dict = {}
    if cold and rc == 0:
        print('\ncold comparison: rebuilt bytes against the committed ones')
        same, diff, untracked_new = 0, [], []
        for rel, old in before.items():
            p = ROOT / rel
            # git wants forward slashes; str(Path) on Windows gives backslashes, and `git show` then
            # fails with an empty stdout -- which would have been reported as a content difference.
            rel_git = rel.replace('\\', '/')
            if not p.exists():
                diff.append({'path': rel_git, 'why': 'not regenerated'})
                continue
            new = _sha(p)
            if new == old:
                same += 1
            else:
                head = subprocess.run(['git', 'show', f'HEAD:{rel_git}'], cwd=ROOT,
                                      capture_output=True).stdout
                why = 'differs from HEAD too' if hashlib.sha256(head).hexdigest() != old \
                    else 'differs from what was committed'
                diff.append({'path': rel_git, 'why': why})
        for p in derived_files():
            rel = str(p.relative_to(ROOT)).replace('\\', '/')
            if rel not in before and not _tracked(p):
                untracked_new.append(rel)
        check = {'tracked_before': len(before), 'regenerated_identically': same,
                 'differences': diff, 'files_not_restored': len(diff),
                 'untracked_new_files': untracked_new}
        print(f'  {same} of {len(before)} regenerated byte-identically')
        for d in diff:
            print(f'  DIFFERS: {d["path"]} ({d["why"]})')
        if untracked_new:
            print(f'  note: {len(untracked_new)} new untracked files appeared: {untracked_new[:5]}')
    return rows, check, rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(add_help=True, description='E4: full pipeline from clean')
    ap.add_argument('--plan', action='store_true', help='print the plan and exit')
    ap.add_argument('--check', action='store_true', help='check prerequisites and exit')
    ap.add_argument('--cold', action='store_true',
                    help='delete tracked derived artefacts first, then compare the rebuilt bytes')
    # The CLI invokes module mains as main([stage, *argv]), so drop the stage name if present.
    rest = list(argv if argv is not None else sys.argv[1:])
    if rest and not rest[0].startswith('-'):
        rest = rest[1:]
    args = ap.parse_args(rest)

    plan = pipeline()
    prereq = prerequisites()

    if args.plan:
        print(f'{len(plan)} stages, in order (derived from tools/puzzle/cli.py::STAGES):')
        for stage, module, step, purpose in plan:
            print(f'  {step:<6} {stage:<20} {module:<10} {purpose}')
        return 0

    missing = _fail_missing(prereq)
    print('prerequisites:')
    for k, v in prereq.items():
        print(f'  {k:<24} {v}')
    if missing:
        print('\ncannot run the pipeline yet:')
        for m in missing:
            print(f'  - missing {m}')
        return 1
    print('all prerequisites present\n')
    if args.check:
        return 0

    rows, check, rc = run_pipeline(args.cold, OUT)

    agree = 'not run'
    if rc == 0:
        t0 = time.time()
        proc = subprocess.run([sys.executable, '-m', 'tools.puzzle', 'acceptance'],
                              cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                              errors='replace')
        print(f'\nacceptance ({time.time() - t0:.1f}s):')
        print('\n'.join((proc.stdout or '').splitlines()[-14:]))
        agree = 'PASS' if proc.returncode == 0 else 'FAIL'
        rc = proc.returncode

    total = sum(r['seconds'] for r in rows)
    print(f'\n{len(rows)} stages, {total:.1f}s total, acceptance {agree}')

    # No timings in the artifact: B3's rule (a clock in a file breaks byte-identical regeneration).
    import json
    report = {
        'generated_by': 'tools/puzzle/repro.py',
        'mode': {'cold': bool(args.cold), 'stage_source': 'tools/puzzle/cli.py::STAGES'},
        'prerequisites': {k: v for k, v in prereq.items() if k != 'python'},
        'stages': [{k: v for k, v in r.items() if k != 'seconds'} for r in rows],
        'stages_run': len(rows),
        'all_stages_exit_zero': all(r['exit_code'] == 0 for r in rows),
        'cold_check': check or None,
        'acceptance': agree,
        'note': 'timings are printed to stdout and deliberately not recorded here',
    }
    OUT.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(f'report: {OUT.relative_to(ROOT).as_posix()}')
    print(f'REPRODUCTION: {"PASS" if rc == 0 else "FAIL"}')
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
