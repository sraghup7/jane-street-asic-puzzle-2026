#!/usr/bin/env python3
"""vcd_table.py -- S0.1, in the pipeline: regenerate `recon/vcd_cycles.csv`.

`recon/vcd_cycles.csv` is the per-cycle decoding of the reference waveform, produced in Step 1 by
`tools/vcd_probe.py` and committed. C1 reads it as its independent cross-check source ("the Step-1
dossier's own decoding"), and -- like `recon/inventory.json` -- no pipeline stage regenerated it, which
E4's cold run surfaced. This stage closes that hole the same way.

It calls the Step-1 tool's own `main()`, so the CSV's exact serialisation stays the tool's business:
`csv.DictWriter` with `lineterminator='\\n'` (the tool explains why: the default CRLF would make the
artifact platform-dependent).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import vcd_probe as step1

VCD = ROOT / 'asic-puzzle-2026' / 'example_inputs.vcd'
OUT = ROOT / 'recon' / 'vcd_cycles.csv'


def main(argv: list[str] | None = None) -> int:
    if not VCD.exists():
        print(f'missing {VCD.relative_to(ROOT).as_posix()}: the reference waveform is read-only '
              f'upstream input and gitignored, so a fresh clone must obtain it separately')
        return 1
    saved, sys.argv = sys.argv, ['vcd_probe', str(VCD), '--csv', str(OUT)]
    try:
        step1.main()
    finally:
        sys.argv = saved
    return 0 if OUT.exists() else 1


if __name__ == '__main__':
    raise SystemExit(main())
