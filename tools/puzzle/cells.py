#!/usr/bin/env python3
"""cells.py -- B6: our behavioural Verilog models for the chip's own cell masters.

Delta2 (docs/03_our_plan.md): the models are grounded in the *labels the chip carries*,
not in a PDK. Every pin name comes from A3, read out of the cell masters' own pin-label
geometry. The function of each master is then recovered from that master's own
function-family name (also an in-chip label -- the 73 cell-name labels A3 read) applied to
that pin set. Direction comes from B5, which inferred it structurally from the
connectivity graph. Nothing in this file was read from sky130 documentation, a Liberty
file, or any PDK; the models are hypotheses and Phase C tests them (C2 against the
warm-up's known RTL, C1 against the recorded waveform).

Two rules carry most of the weight, and both are read off the artifact:

* **A pin label ending in ``_N`` is the complemented form of the signal.** That is why this
  file never needs to know sky130's convention for *whereabouts* the inverted inputs sit
  in a gate: ``and4bb`` has ``A_N,B_N,C,D`` and ``or4bb`` has ``A,B,C_N,D_N``, but both are
  just "AND/OR of each pin, negated where the label says ``_N``". The two families are
  therefore handled by one rule instead of two exceptions.
* **The ``a<digits>o`` / ``o<digits>a`` family names parse into their own group
  structure.** A digit run gives the arity of one input group; a ``b`` run immediately
  after it says how many of *that* group's inputs are complemented (``o2bb2a`` is
  ``2bb`` then ``2``: group A is both-inverted, group B is not); the trailing letter says
  which level is the wide one; a trailing ``i`` inverts the output.

Because a mis-parse would produce a *plausible* wrong model rather than an error -- the
project's signature failure mode, now five times over -- :func:`build_models` asserts for
every master that the pin set the parse predicts equals the pin set A3 actually found.
A convention mismatch fails loudly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.puzzle.connect import read_json                      # noqa: E402

A3 = ROOT / 'recon' / 'derived' / 'pin_names.json'
B5 = ROOT / 'recon' / 'derived' / 'netlist_check.json'
OUT = ROOT / 'build' / 'cells.v'

#: Supply pin names, as A3 read them out of the masters. These are declared on every model
#: but their *connections* come from B4/B5, not from here.
SUPPLY_PINS = ('VGND', 'VPWR', 'VPB', 'VNB')

#: Sequential families and their asynchronous control pin, if any.
SEQUENTIAL = {'dfxtp': None, 'dfrtp': 'RESET_B', 'dfstp': 'SET_B'}


def simple_out(family: str) -> str:
    """The output pin name a single-level gate's own label carries.

    The family strings keep their arity (``and2``, ``nand2b``, ``or4bb``), so this matches on
    prefixes -- and matches the *inverting* families first, though ``nand``/``nor``/``xnor``
    cannot be confused with their non-inverting forms anyway.
    """
    if family.startswith(('nand', 'nor', 'xnor', 'inv')):
        return 'Y'
    if family.startswith(('and', 'or', 'xor', 'buf', 'clkbuf', 'mux2')):
        return 'X'
    raise ValueError(f'no simple-family rule for {family!r}')


# --------------------------------------------------------------------------------------
# family-name parsing
# --------------------------------------------------------------------------------------

def parse_compound(family: str) -> tuple[str, list[tuple[int, int]], bool]:
    """``(level, groups, inverted)`` for an ``a...o`` / ``o...a`` family name.

    ``level`` is ``'a'`` when the *wide* level is the OR (so each group is an AND) and
    ``'o'`` when it is the AND. ``groups`` is one ``(arity, n_inverted)`` per input group,
    in letter order. ``inverted`` is the trailing ``i``.
    """
    assert family[0] in 'ao', family
    level = family[0]
    body = family[1:]
    inverted = body.endswith('i')
    core = body[:-1] if inverted else body
    terminator = core[-1]
    assert terminator in 'ao' and terminator != level, family
    spec = core[:-1]
    groups: list[tuple[int, int]] = []
    i = 0
    while i < len(spec):
        # One digit *per group*, not one number per digit run: ``a2111oi`` is four groups of
        # 2, 1, 1, 1 -- not a group of 2111. Reading the run as a single integer is the
        # mistake this parser originally made, and the pin-set assertion below caught it.
        assert spec[i].isdigit(), f'{family}: expected a digit at {spec[i:]!r}'
        digit = int(spec[i])
        assert 1 <= digit <= 4, f'{family}: implausible group arity {digit}'
        i += 1
        n_inv = 0
        while i < len(spec) and spec[i] == 'b':
            n_inv += 1
            i += 1
        assert n_inv <= digit, f'{family}: {n_inv} inverted inputs in a group of {digit}'
        groups.append((digit, n_inv))
    assert groups and len(groups) <= 4, family
    return level, groups, inverted


def compound_pins(family: str) -> tuple[list[str], str]:
    """``(input_pin_names, output_pin_name)`` as predicted by the family name alone."""
    _, groups, inverted = parse_compound(family)
    ins: list[str] = []
    for gi, (digit, n_inv) in enumerate(groups):
        letter = 'ABCD'[gi]
        for j in range(1, digit + 1):
            ins.append(f'{letter}{j}_N' if j <= n_inv else f'{letter}{j}')
    return ins, ('Y' if inverted else 'X')


def compound_expr(family: str) -> str:
    """The boolean expression, written in terms of the pin names the label predicts."""
    level, groups, inverted = parse_compound(family)
    inner, outer = (' & ', ' | ') if level == 'a' else (' | ', ' & ')
    parts: list[str] = []
    for gi, (digit, n_inv) in enumerate(groups):
        letter = 'ABCD'[gi]
        terms = []
        for j in range(1, digit + 1):
            if j <= n_inv:
                terms.append(f'~{letter}{j}_N')
            else:
                terms.append(f'{letter}{j}')
        parts.append('(' + inner.join(terms) + ')' if digit > 1 else terms[0])
    expr = outer.join(parts)
    return f'~({expr})' if inverted else expr


def family_kind(family: str) -> str:
    """One of: simple, compound, sequential, tie, protection, physical."""
    if family in ('decap', 'tapvpwrvgnd'):
        return 'physical'
    if family == 'diode':
        return 'protection'
    if family == 'conb':
        return 'tie'
    if family in SEQUENTIAL:
        return 'sequential'
    if family.startswith(('and', 'nand', 'or', 'nor', 'xor', 'xnor', 'buf', 'clkbuf',
                          'inv', 'mux2')):
        return 'simple'
    if family[0] in 'ao':
        return 'compound'
    raise ValueError(f'no rule for family {family!r}')


def simple_expr(family: str, ins: list[str], out: str) -> str:
    """The expression for an AND/OR/XOR/BUF/INV/MUX family, negating ``_N`` inputs."""
    terms = [f'~{p}' if p.endswith('_N') else p for p in ins]
    if family.startswith('and'):
        return ' & '.join(terms)
    if family.startswith('nand'):
        return '~(' + ' & '.join(terms) + ')'
    if family.startswith('or'):
        return ' | '.join(terms)
    if family.startswith('nor'):
        return '~(' + ' | '.join(terms) + ')'
    if family.startswith('xor') or family.startswith('xnor'):
        expr = ' ^ '.join(terms)
        return f'~({expr})' if family.startswith('xnor') else expr
    if family == 'inv':
        return '~' + terms[0]
    if family in ('buf', 'clkbuf'):
        return terms[0]
    if family == 'mux2':
        by_name = dict(zip(ins, terms))
        return f"{by_name['S']} ? {by_name['A1']} : {by_name['A0']}"
    raise ValueError(family)


# --------------------------------------------------------------------------------------
# model construction
# --------------------------------------------------------------------------------------

def build_models() -> tuple[list[dict], list[str]]:
    """Every cell model, plus the list of ``master::pin`` classes assumed from their name.

    Raises if a family-name parse disagrees with A3's pin set, or if B5's structural
    direction contradicts a family name's output pin.
    """
    a3 = read_json(A3)
    b5 = read_json(B5)

    declared_out: dict[str, list[str]] = {}
    for key in b5['direction']['outputs']:
        master, pin = key.split('::')
        declared_out.setdefault(master, []).append(pin)

    models: list[dict] = []
    assumed: list[str] = []

    for master in sorted(a3['masters']):
        spec = a3['masters'][master]
        family = spec['type']
        pins = list(spec['pins'])
        kind = family_kind(family)
        supplies = [p for p in pins if p in SUPPLY_PINS]
        signal = [p for p in pins if p not in SUPPLY_PINS]
        found_out = sorted(declared_out.get(master, []))

        if kind == 'compound':
            predicted_in, predicted_out = compound_pins(family)
        elif kind == 'simple':
            predicted_out = simple_out(family)
            predicted_in = [p for p in signal if p != predicted_out]
            if family == 'mux2':
                predicted_in = ['A0', 'A1', 'S']
        elif kind == 'sequential':
            # The output pin here is the flip-flop's own `Q` label, not a family-name game: the
            # sequential families in this library have exactly one output and it is called Q.
            # Leaving this None originally made the renderer declare `input Q;` and emit a
            # stray `reg None;` -- which iverilog accepted, and the B6 gate caught.
            predicted_out = 'Q'
            predicted_in = [p for p in signal if p != predicted_out]
        else:
            predicted_in, predicted_out = [], None

        if kind in ('compound', 'simple'):
            # The parse must reproduce A3's pin set exactly -- no invented, no leftover.
            # Compared against the signal pins *including* the output, so this also checks
            # that the family name predicts the right output pin name (X vs Y).
            expected = sorted([*predicted_in, predicted_out])
            missing = sorted(set(expected) - set(signal))
            extra = sorted(set(signal) - set(expected))
            assert not missing and not extra, (
                f'{master}: family {family!r} predicts {len(expected)} signal pins, '
                f'A3 found {len(signal)}; parse-only {missing[:6]}, '
                f'A3-only {extra[:6]}')
            assert predicted_out in pins, f'{master}: no {predicted_out} pin'
            # B5's structural direction must agree wherever it reached a verdict.
            if found_out:
                assert found_out == [predicted_out], (
                    f'{master}: family name says output {predicted_out}, '
                    f'B5 derived {found_out}')
            else:
                assumed.append(f'{master}::{predicted_out}')

        models.append({'master': master, 'family': family, 'kind': kind,
                       'pins': pins, 'supplies': supplies, 'signal': signal,
                       'out_pin': predicted_out, 'text': None})

    # Second pass: render, now that every model's interface is settled.
    for m in models:
        m['text'] = _render(m)
    return models, assumed


def _render(m: dict) -> str:
    """Render one master's behavioural model as a Verilog module.

    Built from the pin roles and the function the master's own layout implies, so the model is a
    consequence of the chip rather than a table typed in by hand.
    """
    master, family, kind = m['master'], m['family'], m['kind']
    pins, supplies, signal, out = m['pins'], m['supplies'], m['signal'], m['out_pin']

    lines = [f'// {master} -- {family} ({kind})',
             f'module {master} ({", ".join(pins)});']
    inputs = [p for p in pins if p in signal and p != out]
    if kind == 'tie':
        inputs = []
    for p in pins:
        if p in supplies:
            lines.append(f'  inout {p};')
        elif p == out or (kind == 'tie' and p in ('HI', 'LO')):
            lines.append(f'  output {p};')
        else:
            lines.append(f'  input {p};')

    if kind == 'simple':
        lines.append(f'  assign {out} = {simple_expr(family, inputs, out)};')
    elif kind == 'compound':
        lines.append(f'  assign {out} = {compound_expr(family)};')
    elif kind == 'tie':
        # The tie cell's own pin labels say which output is which constant.
        for p in ('HI', 'LO'):
            if p in pins:
                value = "1'b1" if p == 'HI' else "1'b0"
                lines.append(f'  assign {p} = {value};')
    elif kind == 'sequential':
        ctrl = SEQUENTIAL[family]
        lines.append(f'  reg {out};')
        if ctrl is None:
            lines.append('  always @(posedge CLK)')
            lines.append(f'    {out} <= D;')
        else:
            # The control pin's own label carries the polarity: ``RESET_B``/``SET_B`` are
            # the complemented forms, so they assert when driven *low*. That reading is
            # corroborated by the artifact -- the top-level port labelled ``rst_n`` resolves
            # to the very net that carries the design's 84 ``RESET_B`` terminals (B5).
            level = "1'b0" if family == 'dfrtp' else "1'b1"
            lines.append(f'  always @(posedge CLK or negedge {ctrl})')
            lines.append(f'    if (!{ctrl}) {out} <= {level};')
            lines.append(f'    else         {out} <= D;')
    # physical / protection cells have no logic at all: they are layout-only parts, and
    # their body is what the GDS shows. Nothing to model.

    lines.append('endmodule')
    return '\n'.join(lines)


HEADER = """\
// =====================================================================================
// build/cells.v -- our behavioural models for the {n} cell masters the chip instantiates.
//
// Generated by tools/puzzle/cells.py (plan step B6). Do not edit by hand.
//
// Provenance: pin names from A3 (recon/derived/pin_names.json, sha256 {a3sha}), direction
// from B5 (recon/derived/netlist_check.json, sha256 {b5sha}).
//
// These are *our* semantics, recovered from the chip's own labels -- see the module
// docstring of tools/puzzle/cells.py for exactly which rule produces which gate. They are
// validated against two independent oracles in Phase C (C2: the warm-up design's known
// RTL; C1: the recorded example_inputs.vcd), not trusted because they look right.
//
// Supply pins are declared on every model because the masters really have them, but a
// model's *connections* are the netlist's business. VPB/VNB in particular have no
// routeable geometry anywhere in this layout (A5), so they are never connected.
// =====================================================================================
"""


def stage_cells() -> int:
    """B6: write `build/cells.v`, our behavioural models for every master.

    Stamps the header with the hashes of the artifacts the models were derived from, so a stale
    `cells.v` is visible in the file itself rather than only in a diff.
    """
    models, assumed = build_models()
    a3sha = json.loads((ROOT / 'recon' / 'derived' / 'pin_names.json').read_text('utf-8'))['source']['sha256']
    with open(ROOT / 'recon' / 'derived' / 'netlist_check.json', encoding='utf-8') as fh:
        b5sha = json.load(fh)['source']['sha256']

    out = [HEADER.format(n=len(models), a3sha=a3sha[:16], b5sha=b5sha[:16])]
    out.append(f'// {len(models)} modules; {sum(1 for m in models if m["kind"] == "physical")} '
               f'layout-only, '
               f'{sum(1 for m in models if m["kind"] == "sequential")} sequential, '
               f'{sum(1 for m in models if m["kind"] in ("simple", "compound"))} combinational.')
    if assumed:
        out.append(f'// Direction assumed from the pin name (B5 could not decide it from '
                   f'connectivity alone): {", ".join(assumed)}.')
    out.append('')
    for m in models:
        out.append(m['text'])
        out.append('')

    text = '\n'.join(out)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding='utf-8', newline='\n')
    print(f'cells: {len(models)} modules -> {OUT.relative_to(ROOT)}')
    print(f'  layout-only: {sum(1 for m in models if m["kind"] == "physical")}, '
          f'tie: {sum(1 for m in models if m["kind"] == "tie")}, '
          f'sequential: {sum(1 for m in models if m["kind"] == "sequential")}, '
          f'combinational: {sum(1 for m in models if m["kind"] in ("simple", "compound"))}, '
          f'protection: {sum(1 for m in models if m["kind"] == "protection")}')
    if assumed:
        print(f'  assumed from pin name: {", ".join(assumed)}')
    return 0


def main(argv: list[str] | None = None) -> int:
    stage = (argv or ['cells'])[0]
    if stage != 'cells':
        print(f'unknown stage for cells.py: {stage!r}', file=sys.stderr)
        return 2
    return stage_cells()


if __name__ == '__main__':
    raise SystemExit(main())
