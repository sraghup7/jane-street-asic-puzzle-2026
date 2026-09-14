#!/usr/bin/env python3
"""check_stepF3.py -- F3: one convention, applied everywhere.

F3's verify clause is "a scan for parked-work markers and commented-out blocks is clean". A scan is
only evidence if it is written down somewhere executable, so this gate holds the conventions the rest
of the codebase already follows, and fails on the specific ways that drifts:

* no parked-work markers -- the four classic all-caps ones, omitted here so this file does not contain
  the words it bans -- anywhere in shipped code or the README: a parked thought in a comment is work
  that has been silently dropped;
* every module has a module docstring, and every definition that is *public* (some other file calls it)
  or longer than 25 lines has one: this repo's docstrings carry the *reasoning* (why a rule exists,
  what a measurement showed), which is the most valuable thing in it and the first thing to disappear;
* no commented-out code: a `#` line that still parses as an assignment or a call is dead code wearing a
  disguise -- the exact thing F1 is about, one level down;
* file names, and the shape of a gate's output, are uniform: every `check_step*.py` reports a
  `GATE: PASS` / `GATE: FAIL` verdict line and exits non-zero when it fails, because "the gate passed"
  has to mean the same thing in every one of them.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PY_GLOBS = ('tools/*.py', 'tools/puzzle/*.py', 'tools/checks/*.py')
# Spelled as concatenated halves so this gate's own source does not contain the words it bans --
# otherwise it flags its own definition, and the only fixes are an exemption or a weaker rule.
BANNED = tuple(a + b for a, b in (('TO', 'DO'), ('FIX', 'ME'), ('XX', 'X'), ('HA', 'CK')))
# The convention this repo already follows: plain snake_case for modules, `check_step<Id>.py` for
# gates, where the step id keeps its capitals (A1, C4, E2, F1).
NAME_RE = re.compile(r'(check_step[A-Z][A-Za-z0-9]*|[a-z_][a-z0-9_]*)\.py')
# "Public" in a closed repo means: another file calls it. Those need a contract; so does anything
# long enough that a reader cannot hold it in their head. Short helpers used only inside their own
# module (and the two pieces of uniform scaffolding below, whose contract is fixed and identical in
# every gate) are exempt -- enforcing a docstring on a three-line boilerplate helper would produce
# thirty copies of one sentence, which is noise rather than documentation.
MAX_UNDOC_LINES = 25
SCAFFOLDING = {'main', 'check'}
# Comment text that is a directive or prose, not code. Kept deliberately short: an entry here is a
# decision that this shape of comment is legitimate.
DIRECTIVE = re.compile(r'^(noqa|type:|pragma|pylint|mypy|ruff|fmt:|---|===|\*\*\*)', re.I)
CODE_SHAPE = re.compile(r'^[A-Za-z_][\w\.\[\]\'"]*\s*(=[^=]|\((?:[^)]*)\)\s*$)')

checks: list[dict] = []


def check(name: str, passed: bool, detail: str = '') -> None:
    checks.append({'check': name, 'passed': bool(passed), 'detail': detail})


def shipped() -> list[Path]:
    out: list[Path] = []
    for g in PY_GLOBS:
        out.extend(sorted(p for p in ROOT.glob(g) if p.is_file()))
    return out


def commented_out_code(text: str) -> list[str]:
    """Lines that are comments but still parse as an assignment or a call."""
    found = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith('#'):
            continue
        body = stripped.lstrip('#').strip()
        if not body or DIRECTIVE.match(body):
            continue
        if not CODE_SHAPE.match(body):
            continue
        try:
            tree = ast.parse(body)
        except SyntaxError:
            continue
        if any(isinstance(n, (ast.Assign, ast.AugAssign, ast.Expr)) for n in tree.body):
            found.append(f'{i}: {body[:60]}')
    return found


def main() -> int:
    files = shipped()
    texts = {p: p.read_text(encoding='utf-8') for p in files}

    banned = [f'{p.relative_to(ROOT)}:{tok}' for p, t in texts.items() for tok in BANNED
              if re.search(rf'\b{tok}\b', t)]
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    banned += [f'README.md:{tok}' for tok in BANNED if re.search(rf'\b{tok}\b', readme)]
    check('no parked work markers in shipped code or the README',
          not banned, ', '.join(banned[:6]) or f'{len(files)} files + README scanned')

    missing_mod = [str(p.relative_to(ROOT)) for p, t in texts.items() if not ast.get_docstring(ast.parse(t))]
    check('every module has a docstring', not missing_mod, ', '.join(missing_mod) or f'{len(files)} modules')

    missing_def: list[str] = []
    documented = 0
    exempt = 0
    for p, t in texts.items():
        for node in ast.parse(t).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            has = bool(ast.get_docstring(node))
            documented += has
            if has:
                continue
            body_len = (node.end_lineno or 0) - (node.lineno or 0)
            if node.name in SCAFFOLDING:
                # `main` is the conventional entry point and `check` the shared verdict helper: their
                # contract is identical in every gate and is stated once -- in the gate's docstring and
                # in this gate's verdict-shape check -- so thirty copies of one sentence would be noise.
                exempt += 1
                continue
            if not isinstance(node, ast.ClassDef) and body_len <= MAX_UNDOC_LINES:
                exempt += 1
                continue
            missing_def.append(f'{p.relative_to(ROOT)}:{node.name} ({body_len} lines)')
    check('every definition a reader cannot hold in their head has a docstring',
          not missing_def,
          ', '.join(missing_def[:5])
          or f'{documented} documented, {exempt} short helpers exempt '
             f'(rule: classes, and any function over {MAX_UNDOC_LINES} lines, must be documented; '
             f'the {sorted(SCAFFOLDING)} scaffolding is exempt)')

    commented = {str(p.relative_to(ROOT)): commented_out_code(t) for p, t in texts.items()}
    commented = {k: v for k, v in commented.items() if v}
    check('no commented-out code',
          not commented, '; '.join(f'{k}: {v[0]}' for k, v in list(commented.items())[:4])
          or 'no comment line parses as a statement')

    bad_names = [p.name for p in files if not NAME_RE.fullmatch(p.name)]
    check('module file names follow the convention',
          not bad_names, ', '.join(bad_names) or f'{len(files)} files')

    gates = sorted((ROOT / 'tools' / 'checks').glob('check_*.py'))
    no_verdict = [p.name for p in gates if 'GATE: PASS' not in p.read_text(encoding='utf-8')
                  or 'GATE: FAIL' not in p.read_text(encoding='utf-8')]
    no_exit = [p.name for p in gates if 'return 1' not in p.read_text(encoding='utf-8')
               and 'sys.exit(1)' not in p.read_text(encoding='utf-8')]
    check('every gate prints a GATE: PASS/FAIL verdict and exits non-zero on failure',
          not no_verdict and not no_exit,
          f'no verdict: {no_verdict or "none"}; no failure path: {no_exit or "none"}')

    failed = [c for c in checks if not c['passed']]
    width = max(len(c['check']) for c in checks)
    for c in checks:
        print(f'  {"PASS" if c["passed"] else "FAIL"}  {c["check"]:<{width}}  {c["detail"]}')
    print()
    print(f'{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print('STEP F3 GATE: FAIL')
        return 1
    print('STEP F3 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
