#!/usr/bin/env python3
"""check_hygiene.py -- standing invariants that keep the plan's approach honest.

The declarations in `docs/03_our_plan.md` (differentiators D1-D6) and `docs/deps.md` are
only worth anything if something fails when they stop being true. This gate is that
something. It is scoped to the *implementation surface* -- every `.py` under `tools/`
plus `requirements.txt` -- because that is the only place a dependency can actually take
hold. See `docs/deps.md` sec.5 for why prose is deliberately out of scope.

Invariants:

  1. no forbidden third-party import anywhere under tools/
  2. the pipeline (tools/puzzle/**) imports only stdlib, an allowed third-party allowlist,
     or our own `tools` package
  3. no forbidden token in tools/puzzle/** or requirements.txt (negation-aware)
  4. requirements.txt lists every third-party module that shipped code imports
  5. every pinned version in requirements.txt matches the version actually installed
  6. no tracked script opens a write handle on a path inside the read-only upstream clone

    .venv/Scripts/python tools/checks/check_hygiene.py
    .venv/Scripts/python tools/checks/check_hygiene.py --print-imports
"""
from __future__ import annotations

import ast
import importlib.metadata
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

UPSTREAM = 'asic-puzzle-2026'
REQUIREMENTS = ROOT / 'requirements.txt'
PIPELINE_DIR = ROOT / 'tools' / 'puzzle'

# Directories that are not part of the shipped source.
EXCLUDE_DIRS = {'.git', '.venv', 'venv', '__pycache__', UPSTREAM, 'recon', 'build',
                'node_modules', '.idea', '.vscode'}

# -- invariant 1 -------------------------------------------------------------------
# A dependency on any of these would contradict a declared differentiator.
FORBIDDEN_IMPORTS = {
    'shapely':      'polygon-merge geometry is the published extraction method (P1, D3)',
    'yosys':        'formal cover flow is the published solve (P4, D4)',
    'sby':          'formal cover flow is the published solve (P4, D4)',
    'symbiyosys':   'formal cover flow is the published solve (P4, D4)',
    'bitwuzla':     'formal cover flow is the published solve (P4, D4)',
    'z3':           'third-party solver would weaken D4 ("two of our own implementations")',
    'pysat':        'third-party solver would weaken D4',
}

# -- invariant 2 -------------------------------------------------------------------
# Third-party packages the pipeline is allowed to import. Adding one here is a
# deliberate decision that must be recorded in docs/deps.md.
ALLOWED_PIPELINE_IMPORTS = {'gdstk', 'klayout', 'numpy'}

# -- invariant 3 -------------------------------------------------------------------
# Tokens that must not appear in pipeline source or in requirements.txt. A line is
# exempt if it also carries a negation marker, so a comment such as
# "no shapely here" is fine while `import shapely` is not.
FORBIDDEN_TOKENS = {
    'shapely':     r'shapely',
    'unary_union': r'unary_union',
    'skywater':    r'skywater',
    'libs.ref':    r'libs\.ref',
    'tlef':        r'\.tlef',
    'primitives.v': r'primitives\.v',
    'yosys':       r'yosys',
    'sby':         r'\bsby\b',
    'symbiyosys':  r'symbiyosys',
    'bitwuzla':    r'bitwuzla',
    'z3':          r'\bz3\b',
}
NEGATION = re.compile(
    r"\b(?:no|not|never|nothing|without|prohibit\w*|off-limits|avoid\w*|exclude\w*|"
    r"unused|remov\w*|remov\w*|drop\w*|skip\w*|abandon\w*|instead|rather\s+than|"
    r"cannot|can't|won't|deliberately|exempt\w*|forbid\w*)\b", re.I)

# -- invariant 5 -------------------------------------------------------------------
# Import name -> distribution name, where they differ.
MODULE_TO_DIST = {'klayout': 'klayout', 'gdstk': 'gdstk', 'matplotlib': 'matplotlib',
                  'numpy': 'numpy'}

WRITE_PAT = re.compile(
    r'open\s*\(|write_text|write_bytes|shutil\.|os\.remove|os\.unlink|\.unlink\s*\(|'
    r'\.mkdir\s*\(|Path\([^)]*\)\s*\.\s*write')

results: list[tuple[str, str, object, object]] = []


def check(label, actual, expected):
    results.append(('PASS' if actual == expected else 'FAIL', label, actual, expected))


def skip(label, why):
    results.append(('SKIP', label, why, ''))


def walk_py() -> list[Path]:
    out = []
    for p in sorted(ROOT.rglob('*.py')):
        if any(part in EXCLUDE_DIRS for part in p.relative_to(ROOT).parts):
            continue
        out.append(p)
    return out


def imports_of(path: Path) -> set[str]:
    """Top-level module names imported by a file. AST-based, so probe strings in
    tools/inventory.py are never mistaken for imports."""
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                mods.add(node.module.split('.')[0])
    return mods


def main(argv: list[str]) -> int:
    print_imports = '--print-imports' in argv
    stdlib = set(sys.stdlib_module_names) | {'tools'}

    py_files = walk_py()
    rel = {p: p.relative_to(ROOT).as_posix() for p in py_files}

    # Raw imports per file, then classify as local or third-party. A module is "local"
    # if it is stdlib, our own `tools` package, or the stem of a shipped file -- so a
    # sibling import can never be mistaken for a missing third-party dependency.
    raw_imports: dict[str, set[str]] = {}
    for p in py_files:
        try:
            raw_imports[rel[p]] = imports_of(p)
        except SyntaxError as e:
            check(f'{rel[p]} parses', f'SyntaxError: {e}', 'parses')
            raw_imports[rel[p]] = set()
    check('all shipped python files parse', True, True)

    local_stems = {p.stem for p in py_files}
    local_mods = local_stems | {'tools'}

    third_party: dict[str, set[str]] = {
        name: {m for m in mods if m not in stdlib and m not in local_mods}
        for name, mods in raw_imports.items()}

    all_third = sorted({m for mods in third_party.values() for m in mods})

    if print_imports:
        print('third-party imports by shipped file')
        for name in sorted(third_party):
            if third_party[name]:
                print(f'  {name:<34} {", ".join(sorted(third_party[name]))}')
        print(f'  distinct: {", ".join(all_third)}')
        print()

    # ---- 1. forbidden imports -------------------------------------------------
    forbidden_found = sorted({m for m in all_third if m in FORBIDDEN_IMPORTS})
    detail = [f'{m} ({FORBIDDEN_IMPORTS[m]})' for m in forbidden_found]
    check('no forbidden third-party import under tools/', detail, [])

    # ---- 1b. no sibling-style imports -----------------------------------------
    # `from vcd_probe import ...` only resolves via a sys.path hack, so it silently
    # breaks when the caller's cwd or sys.path changes. Every local import must go
    # through the `tools` package. The first run of this gate found exactly one.
    bare = sorted(f'{name} -> {m}'
                  for name, mods in raw_imports.items()
                  for m in mods
                  if m in local_stems and m not in stdlib)
    check('no bare sibling-module imports (use `tools.<module>`)', bare, [])

    # ---- 2. pipeline allowlist ------------------------------------------------
    pipeline_files = [rel[p] for p in py_files if PIPELINE_DIR in p.parents or p.parent == PIPELINE_DIR]
    outside = sorted({m for f in pipeline_files for m in third_party.get(f, set())
                      if m not in ALLOWED_PIPELINE_IMPORTS})
    check(f'pipeline imports only {sorted(ALLOWED_PIPELINE_IMPORTS)}',
          outside, [])
    check('pipeline files were discovered', len(pipeline_files) > 0, True)

    # ---- 3. forbidden tokens --------------------------------------------------
    scan_targets = list(PIPELINE_DIR.rglob('*.py')) + ([REQUIREMENTS] if REQUIREMENTS.exists() else [])
    violations = []
    for p in scan_targets:
        for n, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
            for name, pat in FORBIDDEN_TOKENS.items():
                if re.search(pat, line, re.I) and not NEGATION.search(line):
                    violations.append(f'{p.relative_to(ROOT).as_posix()}:{n} [{name}] '
                                      f'{line.strip()[:60]}')
    check('no forbidden token in pipeline source or requirements.txt', violations, [])

    # ---- 4. requirements.txt completeness -------------------------------------
    if not REQUIREMENTS.exists():
        skip('requirements.txt exists', 'not created yet')
    else:
        text = REQUIREMENTS.read_text(encoding='utf-8')
        listed = set()
        for line in text.splitlines():
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            m = re.match(r'^([A-Za-z0-9_.\-]+)\s*==', line)
            if m:
                listed.add(m.group(1).lower().replace('_', '-'))
        needed = {MODULE_TO_DIST.get(m, m).lower().replace('_', '-') for m in all_third}
        missing = sorted(needed - listed)
        check('requirements.txt lists every third-party import by shipped code',
              missing, [])
        extra = sorted(listed - needed)
        check('requirements.txt has no unused entries', extra, [])

        # ---- 5. pinned versions match the environment --------------------------
        mismatched = []
        for line in text.splitlines():
            line = line.split('#', 1)[0].strip()
            m = re.match(r'^([A-Za-z0-9_.\-]+)\s*==\s*(\S+)$', line)
            if not m:
                continue
            name, pinned = m.group(1), m.group(2)
            try:
                installed = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                mismatched.append(f'{name}: pinned {pinned}, not installed')
                continue
            if installed != pinned:
                mismatched.append(f'{name}: pinned {pinned}, installed {installed}')
        check('requirements.txt pins match the installed versions', mismatched, [])

        # ---- 7. no forbidden package pinned -----------------------------------
        bad = [t for t in FORBIDDEN_TOKENS if re.search(FORBIDDEN_TOKENS[t], text, re.I)]
        check('requirements.txt pins no forbidden package', bad, [])

    # ---- 6. upstream clone is read-only --------------------------------------
    writers = []
    for p in py_files:
        for n, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
            if line.lstrip().startswith('#'):
                continue
            if UPSTREAM in line and WRITE_PAT.search(line):
                writers.append(f'{rel[p]}:{n}')
    check('no shipped script writes into the upstream clone', writers, [])

    # ---- report --------------------------------------------------------------
    width = max(len(r[1]) for r in results)
    n_fail = n_skip = 0
    for status, label, actual, expected in results:
        if status == 'FAIL':
            n_fail += 1
            print(f'  FAIL  {label:<{width}}')
            if isinstance(actual, (list, tuple)) and actual:
                for item in actual:
                    print(f'          - {item}')
            else:
                print(f'          got {actual!r}  want {expected!r}')
        elif status == 'SKIP':
            n_skip += 1
            print(f'  SKIP  {label:<{width}}  {actual}')
        else:
            print(f'  PASS  {label}')
    passed = sum(1 for r in results if r[0] == 'PASS')
    print()
    print(f'{passed} passed, {n_fail} failed, {n_skip} skipped   '
          f'({len(py_files)} python files scanned)')
    if n_fail:
        print(f'HYGIENE GATE: FAIL ({n_fail} failing)')
        return 1
    print('HYGIENE GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
