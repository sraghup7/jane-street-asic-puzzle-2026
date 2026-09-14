#!/usr/bin/env python3
"""inventory.py -- S0.1, in the pipeline: regenerate `recon/inventory.json`.

The B-phase consumes `recon/inventory.json` (the workspace inventory: upstream commit, file hashes,
GDS/vcd/warmup summaries, environment). It was produced by the Step-1 tool `tools/inventory.py` and
committed -- but **no stage regenerated it**, which E4's cold run found the hard way: delete the tracked
derived files, rebuild, and B1 dies on `missing input recon/inventory.json`. A pipeline that cannot
regenerate its own input is not a reproduction path, so this stage closes that hole.

It deliberately calls the Step-1 tool's own `main()` rather than re-implementing its serialisation: if
the tool's format ever changes, this stage follows it instead of quietly drifting, and the artifact
stays byte-identical (indent=2, sort_keys=True, LF, trailing newline -- as the tool documents).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import inventory as step1

OUT = ROOT / 'recon' / 'inventory.json'


def main(argv: list[str] | None = None) -> int:
    if not (ROOT / 'asic-puzzle-2026').is_dir():
        print('missing asic-puzzle-2026/: this stage inventories the upstream puzzle workspace, which '
              'is read-only input and gitignored, so a fresh clone must obtain it separately')
        return 1
    saved, sys.argv = sys.argv, ['inventory', '--out', str(OUT)]
    try:
        step1.main()
    finally:
        sys.argv = saved
    return 0 if OUT.exists() else 1


if __name__ == '__main__':
    raise SystemExit(main())
