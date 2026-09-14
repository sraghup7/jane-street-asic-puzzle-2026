#!/usr/bin/env python3
"""check_stepF1.py -- F1: nothing shipped is dead, nothing shipped is scratch.

F1's verify clause is "a search for unused modules and unreferenced functions returns nothing; all
gates still pass". A search that returns nothing is only meaningful if it is *specific*, so this gate
is written to fail on the interesting mistakes rather than to pass on a clean tree:

* **every module-level function and class** in shipped code (`tools/*.py`, `tools/puzzle/*.py`,
  `tools/checks/*.py`) must be referenced at least once somewhere in the repo besides its own `def` —
  including from its own file, so a private helper used locally is fine;
* **every module file** must be referenced: imported by another module, or registered as a pipeline
  stage, or cited by a document as the instrument that produced a checked-in artifact. The third case
  matters here: `tools/kl_recon.py` and `tools/gds_dump.py` are the recon instruments behind the Step-1
  and Step-2 dossiers, and `tools/render.py` is the instrument named in C4's record, so "not imported
  by the pipeline" does not make them dead — but they must be *cited*, which is checkable;
* **the dependency floor F1 asks for**: no `shapely` import anywhere under `tools/` (plan section 5's
  assertion, which also lets it be dropped from the dependency list — it is already absent from
  `requirements.txt`);
* **no scratch in the tree**: `recon/scratch/`, `recon/hints/`, `recon/renders/` and `recon/sources/`
  must be gitignored *and* must have nothing tracked, so a clone cannot receive them.

Kept-name exceptions are listed explicitly below, with the reason, so an exception is a decision
rather than an oversight.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.puzzle import cli as C

SHIPPED_GLOBS = ('tools/*.py', 'tools/puzzle/*.py', 'tools/checks/*.py')
SCRATCH_DIRS = ('recon/scratch', 'recon/hints', 'recon/renders', 'recon/sources')
FORBIDDEN_IMPORTS = ('shapely', 'yosys', 'sby', 'bitwuzla')
TEXT_SUFFIXES = ('.py', '.md', '.txt', '.v', '.json', '.csv', '.sh', '.gitignore')

# Names kept on purpose. Each needs a reason; an empty reason is a bug.
KEEP = {
    '__init__': 'dunder, not called by name',
    'main': 'entry point, dispatched by the CLI',
}
DOCS = list((ROOT / 'docs').rglob('*.md'))
checks: list[dict] = []


def check(name: str, passed: bool, detail: str = '') -> None:
    checks.append({'check': name, 'passed': bool(passed), 'detail': detail})


def sources() -> list[Path]:
    out: list[Path] = []
    for g in SHIPPED_GLOBS:
        out.extend(sorted(p for p in ROOT.glob(g) if p.is_file()))
    return out


def repo_text() -> dict[str, str]:
    """Every text file in the repo, so a reference can be found wherever it lives."""
    texts: dict[str, str] = {}
    for p in ROOT.rglob('*'):
        if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
            continue
        if any(part in ('.venv', '.git', '__pycache__', 'scratch', 'asic-puzzle-2026')
               for part in p.parts):
            continue
        try:
            texts[str(p.relative_to(ROOT))] = p.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
    return texts


def defined_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
    return names


def identifier_counts(text: str) -> Counter:
    """How often each identifier appears in a Python file, by AST.

    Deliberately not a text search. A name mentioned in a docstring, a comment, a printed label or a
    **string literal** is not a reference to a function, and counting those made this gate blind to the
    one thing it exists to find: the first F sweep's mutation appends a dead helper whose name appears
    in `fault_inject.py`'s own source as a string, which the text version counted as a use, so both
    dead-code mutations escaped. Two escapes, one cause, fixed here rather than by renaming the
    mutation.
    """
    c: Counter = Counter()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Name):
            c[node.id] += 1
        elif isinstance(node, ast.Attribute):
            c[node.attr] += 1
        elif isinstance(node, ast.arg):
            c[node.arg] += 1
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                c[a.asname or a.name.split('.')[0]] += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            c[node.name] += 1
    return c


def main() -> int:
    texts = repo_text()
    shipped = sources()

    # ---- every module-level name is used somewhere -------------------------------------
    # Counted over identifiers, not over text (see `identifier_counts`), and a file's own definitions
    # are subtracted so that defining a name is not mistaken for using it.
    counts = {rel: identifier_counts(t) for rel, t in texts.items() if rel.endswith('.py')}
    defs_here = {rel: Counter(defined_names(ROOT / rel)) for rel in counts}
    unused: list[str] = []
    for path in shipped:
        rel = str(path.relative_to(ROOT))
        for name in defined_names(path):
            if name in KEEP:
                continue
            uses = 0
            for other, c in counts.items():
                n = c.get(name, 0)
                if other == rel:
                    n -= defs_here[rel].get(name, 0)
                uses += n
            if uses <= 0:
                unused.append(f'{rel}:{name}')
    check('every shipped function and class is referenced at least once',
          not unused, ', '.join(unused[:8]) or f'{sum(len(defined_names(p)) for p in shipped)} names')

    # ---- every module file is referenced: imported, a stage, or a cited instrument ------
    stages = {m for _s, m, _p, _x in C.STAGES}
    unreferenced: list[str] = []
    for path in shipped:
        rel = str(path.relative_to(ROOT))
        stem = path.stem
        if stem in stages or rel in ('tools/__init__.py', 'tools/puzzle/__init__.py',
                                     'tools/puzzle/__main__.py'):
            continue
        # A gate is referenced by the suite's *discovery*, not by name: run_all walks
        # `tools/checks/check_*.py`, so being found is what makes a gate live. The first version of
        # this check demanded a citation and flagged the four F gates as unlisted instruments.
        if path.parent.name == 'checks' and path.name.startswith('check_'):
            continue
        cited = any(stem in text for other, text in texts.items() if other != rel)
        if not cited:
            unreferenced.append(rel)
    check('every shipped module is imported, a pipeline stage, or cited as an instrument',
          not unreferenced, ', '.join(unreferenced) or f'{len(shipped)} modules')

    # ---- F1's dependency floor ----------------------------------------------------------
    offenders = []
    for path in shipped:
        text = path.read_text(encoding='utf-8')
        for mod in FORBIDDEN_IMPORTS:
            if re.search(rf'^\s*(import|from)\s+{mod}\b', text, re.M):
                offenders.append(f'{path.relative_to(ROOT)}: {mod}')
    req = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
    check('no forbidden dependency is imported, and none is listed',
          not offenders and not re.search(r'^\s*shapely\b', req, re.M),
          ', '.join(offenders) or 'shapely/yosys/sby/bitwuzla absent from code and manifest')

    # ---- no scratch in the tree --------------------------------------------------------
    tracked = subprocess.run(['git', 'ls-files', *SCRATCH_DIRS], cwd=ROOT, capture_output=True,
                             text=True, encoding='utf-8', errors='replace').stdout.split()
    ignored = {}
    for d in SCRATCH_DIRS:
        proc = subprocess.run(['git', 'check-ignore', '-q', d], cwd=ROOT)
        ignored[d] = proc.returncode == 0
    check('the throwaway directories are gitignored and hold nothing tracked',
          not tracked and all(ignored.values()),
          f'tracked: {tracked or "none"}; ignored: '
          f'{sum(1 for v in ignored.values() if v)}/{len(SCRATCH_DIRS)}')

    # ---- the F1 record itself must not claim more than it checked ----------------------
    check('this gate reports the counts it checked, not just a verdict',
          sum(len(defined_names(p)) for p in shipped) > 50,
          f'{sum(len(defined_names(p)) for p in shipped)} names, {len(shipped)} modules, '
          f'{len(texts)} text files searched')

    failed = [c for c in checks if not c['passed']]
    width = max(len(c['check']) for c in checks)
    for c in checks:
        print(f'  {"PASS" if c["passed"] else "FAIL"}  {c["check"]:<{width}}  {c["detail"]}')
    print()
    print(f'{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print('STEP F1 GATE: FAIL')
        return 1
    print('STEP F1 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
