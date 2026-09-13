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


def regenerate_many(module: str, out_attrs: tuple[str, ...], stage: str,
                    names: dict[str, str] | None = None,
                    suffix: str = '.json') -> tuple[int, dict[str, bytes | None], str]:
    """Run ``<module>.main([stage])`` with each output redirected to scratch.

    Returns ``(returncode, {attr: bytes_or_None}, captured_stdout)``.

    ``names`` gives an attr its own scratch filename, for stages that write more than one
    artifact (B7 writes `build/warmup.v` *and* a JSON report). An attr absent from ``names``
    shares ``<stage><suffix>`` with the others, which is what :func:`regenerate` has always
    done -- A1 relies on that shared path, so it is preserved exactly rather than tidied.
    """
    m = importlib.import_module(module)
    saved = {a: getattr(m, a) for a in out_attrs}
    tmp = {a: SCRATCH / ((names or {}).get(a, f'{stage}{suffix}')) for a in out_attrs}
    buf = io.StringIO()
    try:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        for p in set(tmp.values()):
            if p.exists():
                p.unlink()
        for a in out_attrs:
            setattr(m, a, tmp[a])
        with contextlib.redirect_stdout(buf):
            rc = m.main([stage])
        produced = {a: (tmp[a].read_bytes() if tmp[a].exists() else None) for a in out_attrs}
    finally:
        for a, orig in saved.items():
            setattr(m, a, orig)
        for p in set(tmp.values()):
            if p.exists():
                p.unlink()
        try:
            SCRATCH.rmdir()
        except OSError:
            pass          # non-empty (a parallel gate) -- leave it
    return rc, produced, buf.getvalue()


def regenerate(module: str, out_attrs: tuple[str, ...], stage: str,
               suffix: str = '.json') -> tuple[int, bytes | None, str]:
    """The single-artifact case of :func:`regenerate_many`, unchanged in behaviour."""
    rc, produced, out = regenerate_many(module, out_attrs, stage, suffix=suffix)
    return rc, produced[out_attrs[0]], out
