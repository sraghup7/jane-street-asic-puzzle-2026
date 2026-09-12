#!/usr/bin/env python3
"""_regen.py -- hermetic stage regeneration, shared by the gates.

A gate that proves "the committed artifact is exactly what the code produces" has to run
the stage. The obvious way is:

    before = ART.read_text()
    STAGE_MAIN(['stage'])                 # rewrites ART
    check(ART.read_text() == before, True)

That does detect a stale artifact -- but it destroys it in the process, and because
`run_all.py` runs the gates in dependency order, an *upstream* gate silently repairs an
artifact before a *downstream* gate can notice it was ever wrong. Measured on the A-phase
gates: corrupting `pin_names.json` and running the whole suite showed only `stepA3`
failing, while running `stepA4` alone against the same corruption failed as well (38
passed, 1 failed) -- the A3 gate had already rewritten the file by the time A4 looked.

This helper points the stage's output at a scratch file instead, so the gate can compare
the produced bytes without touching the working tree. PASS leaves the tree byte-clean
and FAIL leaves the committed artifact on disk to be inspected. The gates additionally
assert that the tree was untouched, so this property is itself regression-protected.

The scratch file must live *inside* the repository: the stages report their output path
with `Path.relative_to(ROOT)`, which raises for a path outside the tree.
"""
from __future__ import annotations

import contextlib
import importlib
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = ROOT / 'recon' / 'scratch' / 'regen'


def regenerate(module: str, out_attrs: tuple[str, ...], stage: str) -> tuple[int, bytes | None, str]:
    """Run ``<module>.main([stage])`` with ``out_attrs`` redirected to scratch.

    ``out_attrs`` names the module-global output Path(s) the stage writes. Every stage
    reads its *inputs* from the real artifacts, which is what makes this a test of the
    committed upstream chain rather than of a self-contained re-run.

    Returns ``(returncode, bytes_written_or_None, captured_stdout)``.
    """
    m = importlib.import_module(module)
    saved = {a: getattr(m, a) for a in out_attrs}
    tmp = SCRATCH / f'{stage}.json'
    buf = io.StringIO()
    try:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        if tmp.exists():
            tmp.unlink()
        for a in out_attrs:
            setattr(m, a, tmp)
        with contextlib.redirect_stdout(buf):
            rc = m.main([stage])
        produced = tmp.read_bytes() if tmp.exists() else None
    finally:
        for a, orig in saved.items():
            setattr(m, a, orig)
        if tmp.exists():
            tmp.unlink()
        try:
            SCRATCH.rmdir()
        except OSError:
            pass          # non-empty (a parallel gate) -- leave it
    return rc, produced, buf.getvalue()
