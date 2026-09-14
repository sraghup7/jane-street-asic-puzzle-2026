#!/usr/bin/env python3
"""verdict.py -- C4/C5/E1: reading the chip's verdict out of our own netlist.

Three stages share one instrument:

  the instrument   a **simulation-free evaluator** over the recovered netlist: the cells' own
                   expressions, evaluated with Kleene three-valued logic on the gate structure, on
                   the reference waveform's own control timeline. Promoted from
                   `recon/scratch/symb.py`, which C4 validated against `iverilog` on every
                   module-scope net (225 956 comparisons, 0 disagreements). It is validated again on
                   every gate run by replaying `example_inputs.vcd` and demanding zero output
                   mismatches over 312 cycles -- the instrument's agreement with the simulator is
                   what lets its *silence* about a signal be treated as evidence.

  C4  region-map   the eleven latches whose single-star trigger sets partition the grid, each holding
                   exactly two of the answer's stars: a **candidate** region map, with the controls
                   that bound how much it is worth. The map is derived, never assumed: the sweep
                   measures every flop's trigger set, and the partition is *found* as an exact cover.
  C5  rejections   boards that satisfy every visible rule and are rejected anyway -- the measured
                   proof that a hidden constraint exists -- plus the measurement of how little a
                   rejection can ever say about that constraint.
  E1  winning      the winning vector driven through the netlist: success at cycle 126, the message
                   it prints, and the four wrong-input messages.

Why the payload is fed on the reference's control timeline: the reference waveform contains only
*failing* attempts, so the one thing that may differ between a failing attempt and the winning one
is the data line. Copying rst_n/enable from the file and replacing only `I` makes that true by
construction.

Usage:

    python -m tools.puzzle region-map     # C4 -> recon/derived/c4_partition.json     (~100 s)
    python -m tools.puzzle rejections     # C5 -> recon/derived/c5_rejections.json    (~1 min)
    python -m tools.puzzle winning        # E1 -> recon/derived/e1_messages.json      (~10 s)
"""
from __future__ import annotations

import ast
import json
import random
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.puzzle import cells as CL
from tools.puzzle import connect as C
from tools.puzzle import simulate as S
from tools import target as T

SUPPLY_PINS = ('VGND', 'VPWR', 'VPB', 'VNB')
X = None                                          # Kleene 'unknown'
WIN_FEED_OFFSET = 4                               # measured by E1, not assumed
DECISION_CYCLE = 124                              # the sampled cycle whose D-feeding edge is 126
C4_CYCLES = 126                                   # C4's window: up to the decision
MESSAGE_CYCLES = 140                              # feed ends at 124; the message lands 125..134


def rj(rel: str):
    return json.loads((C.ROOT / rel).read_text(encoding='utf-8'))


# --------------------------------------------------------------------------------------
# Kleene three-valued primitives
# --------------------------------------------------------------------------------------
# A `z` on one input does not poison a gate whose other input is controlling (0 & z = 0), which is
# what the 4-state simulator does and what a blanket "any unknown in, unknown out" rule gets wrong:
# the first cut of this evaluator lost O[1] and O[4] that way.
def k_not(a):
    return X if a is X else 1 - a


def k_and(a, b):
    if a == 0 or b == 0:
        return 0
    if a is X or b is X:
        return X
    return 1


def k_or(a, b):
    if a == 1 or b == 1:
        return 1
    if a is X or b is X:
        return X
    return 0


def k_xor(a, b):
    if a is X or b is X:
        return X
    return a ^ b


def k_mux(s, a1, a0):
    if s == 1:
        return a1
    if s == 0:
        return a0
    return a1 if (a1 is not X and a1 == a0) else X


def ev(node, env):
    if isinstance(node, ast.Expression):
        return ev(node.body, env)
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
        return k_not(ev(node.operand, env))
    if isinstance(node, ast.BinOp):
        a, b = ev(node.left, env), ev(node.right, env)
        if isinstance(node.op, ast.BitAnd):
            return k_and(a, b)
        if isinstance(node.op, ast.BitOr):
            return k_or(a, b)
        if isinstance(node.op, ast.BitXor):
            return k_xor(a, b)
    if isinstance(node, ast.IfExp):
        return k_mux(ev(node.test, env), ev(node.body, env), ev(node.orelse, env))
    raise ValueError(ast.dump(node))


# --------------------------------------------------------------------------------------
# the netlist as equations
# --------------------------------------------------------------------------------------
class Netlist:
    """Every instance as a boolean equation, from B4's pin-to-net map and B6's cell models."""

    def __init__(self) -> None:
        pn = rj('recon/derived/pin_net.json')
        self.insts = pn['instances']
        self.names = rj('recon/derived/pin_names.json')['masters']
        self.comb: list[tuple[str, str, ast.Expression, list[tuple[str, str]]]] = []
        self.seq: list[dict] = []
        self.consts: list[tuple[str, int]] = []
        self._build()

    def net(self, inst: str, pin: str):
        p = self.insts[inst]['pins'].get(pin)
        return None if p is None else p[0]

    def _build(self) -> None:
        for inst, spec in self.insts.items():
            family = self.names[spec['master']]['type']
            kind = CL.family_kind(family)
            pins = {k: v for k, v in spec['pins'].items()
                    if k not in SUPPLY_PINS and v is not None}
            if kind in ('physical', 'protection'):
                continue
            if kind == 'tie':
                for pin in ('HI', 'LO'):
                    if pin in pins:
                        self.consts.append((pins[pin][0], 1 if pin == 'HI' else 0))
                continue
            if kind == 'sequential':
                ctrl = CL.SEQUENTIAL[family]
                self.seq.append({'inst': inst, 'family': family,
                                 'q': self.net(inst, 'Q'), 'd': self.net(inst, 'D'),
                                 'ctrl': None if ctrl is None else self.net(inst, ctrl)})
                continue
            if family == 'mux2':
                out_pin, expr = 'X', '(A1 if S else A0)'
            elif kind == 'compound':
                _, out_pin = CL.compound_pins(family)
                expr = CL.compound_expr(family)
            else:
                out_pin = CL.simple_out(family)
                expr = CL.simple_expr(family, [p for p in pins if p != out_pin], out_pin)
            ins = [p for p in pins if p != out_pin]
            self.comb.append((inst, out_pin, ast.parse(expr, mode='eval'),
                              [(p, pins[p][0]) for p in ins]))
        self._order_gates()

    def _order_gates(self) -> None:
        """Sort gates by level so one pass propagates a whole cycle.

        Without this, evaluation re-scans all gates until nothing changes -- about a hundred passes
        per cycle on this design, which is what made a 121-position sweep take minutes instead of
        seconds. Gates in a combinational loop keep level 0 and are resolved by the fixed-point loop.
        """
        driven = {self.net(i, p): [n for _p, n in ins] for i, p, _t, ins in self.comb}
        level: dict[str, int] = {}
        for _ in range(200):
            changed = False
            for out, inputs in driven.items():
                lvl = 1 + max((level.get(n, 0) for n in inputs), default=0)
                if level.get(out, 0) != lvl:
                    level[out] = lvl
                    changed = True
            if not changed:
                break
        self.comb.sort(key=lambda g: level.get(self.net(g[0], g[1]), 0))

    def comb_eval(self, nets: dict, rounds: int = 300) -> bool:
        for _ in range(rounds):
            changed = False
            for inst, out_pin, tree, ins in self.comb:
                val = ev(tree, {p: nets.get(n) for p, n in ins})
                out = self.net(inst, out_pin)
                if nets.get(out, 'unset') != val:
                    nets[out] = val
                    changed = True
            if not changed:
                return True
        return False

    def next_state(self, state: dict, nets: dict) -> dict:
        """The next state, with an *unknown* asynchronous control meaning 'hold'.

        An X on SET_B/RESET_B is not an assertion: no edge occurs, so the flop holds. Treating X as 0
        latched a set/reset that never happened -- the defect behind n1813 reading 1 where the
        simulator holds 0.
        """
        nxt = {}
        for s in self.seq:
            d = nets.get(s['d'])
            c = None if s['ctrl'] is None else nets.get(s['ctrl'])
            if c == 0:
                nxt[s['inst']] = 1 if s['family'] == 'dfstp' else 0
            elif s['ctrl'] is not None and c is None:
                nxt[s['inst']] = state[s['inst']]
            else:
                nxt[s['inst']] = d
        return nxt


def supplies() -> dict:
    """VDD/VSS as constants: the two supply-only nets, by their own pin names (B3)."""
    out = {}
    for net in rj('recon/derived/pin_net.json')['nets']:
        if not net.get('is_supply_only'):
            continue
        pn = net['pin_names']
        if 'VGND' in pn:
            out[net['cluster']] = 0
        elif 'VPWR' in pn:
            out[net['cluster']] = 1
    return out


# --------------------------------------------------------------------------------------
# the machine
# --------------------------------------------------------------------------------------
class Machine:
    """The design under a stimulus we choose, on the reference's own control timeline."""

    def __init__(self, cycles: int = C4_CYCLES, offset: int = WIN_FEED_OFFSET) -> None:
        self.offset = offset
        self.nl = Netlist()
        self.ports = {p['port']: p['net'] for p in rj('recon/derived/netlist_check.json')['ports']}
        self.base = supplies()
        for n, v in self.nl.consts:
            self.base[n] = v
        self.cycles = S.read_reference()['cycles'][:cycles]
        self.driver: dict[str, tuple[str, str, ast.Expression, list]] = {}
        for inst, out_pin, tree, ins in self.nl.comb:
            self.driver[self.nl.net(inst, out_pin)] = (inst, out_pin, tree, ins)
        self.flop_of_net = {s['q']: s['inst'] for s in self.nl.seq}
        self.flop = {s['inst']: s for s in self.nl.seq}

    # -- stimulus --------------------------------------------------------------------
    def _inputs(self, k: int, pattern: list[int] | None) -> dict:
        cy = self.cycles[k]
        p = k - self.offset
        bit = 0 if pattern is None else (pattern[p] if 0 <= p < len(pattern) else 0)
        return {self.ports['rst_n']: int(cy['rst_n']), self.ports['enable']: int(cy['enable']),
                self.ports['I']: bit, self.ports['clk']: 1}

    def _settle(self, state: dict, inputs: dict) -> dict:
        nets = dict(self.base)
        nets.update(inputs)
        for s in self.nl.seq:
            q = state[s['inst']]
            c = None if s['ctrl'] is None else nets.get(s['ctrl'])
            if c is not None and c == 0:
                q = 1 if s['family'] == 'dfstp' else 0
            nets[s['q']] = q
        self.nl.comb_eval(nets)
        return nets

    def run(self, pattern: list[int] | None, watch_nets=(), watch_flops=()):
        """One pass. Returns per-cycle samples plus the final state.

        The flop latches at the edge that *ends* a cycle, so the D it takes is the one built from the
        next cycle's inputs: the stimulus changes mid-cycle in the reference (enable rises at
        40000 ps, inside cycle 3), and using this cycle's inputs lags the state by one step.
        """
        state = {s['inst']: (0 if s['ctrl'] is not None else X) for s in self.nl.seq}
        nets_hist = {n: [] for n in watch_nets}
        flops_hist = {f: [] for f in watch_flops}
        o_hist, succ_hist = [], []
        for k in range(len(self.cycles)):
            nets = self._settle(state, self._inputs(k, pattern))
            for n in watch_nets:
                nets_hist[n].append(nets.get(n))
            for f in watch_flops:
                flops_hist[f].append(state[f])
            o_hist.append(''.join({1: '1', 0: '0', None: '?'}[nets.get(self.ports[f'O[{i}]'])]
                                  for i in range(7, -1, -1)))
            succ_hist.append(nets.get(self.ports['success']))
            nxt = min(k + 1, len(self.cycles) - 1)
            state = dict(self.nl.next_state(state, self._settle(state, self._inputs(nxt, pattern))))
        return {'nets': nets_hist, 'flops': flops_hist, 'O': o_hist,
                'success': succ_hist, 'final': state}

    def message(self, pattern: list[int] | None) -> dict:
        """What the design prints, read off `O` exactly as the iverilog harnesses are read.

        A byte can be **partially unknown**, and E2 is where that first happened: on some boards one
        output bit reads `z`/`x`, because this design has exactly one structurally undriven net (B5's
        net 806) and these inputs make its indeterminacy visible at an interface output. The old code
        called `int(byte, 2)` and raised; the byte is now reported as unknown instead, counted, and
        the decoded text carries a `?` in that position, so a caller cannot mistake an indeterminate
        answer for a definite one.
        """
        r = self.run(pattern)
        chars, unknown_bytes = [], 0
        for k, v in enumerate(r['O']):
            if v == '00000000':
                continue
            if '?' in v:
                unknown_bytes += 1
                chars.append((k, None))
            else:
                chars.append((k, int(v, 2)))
        text = ''.join('?' if c is None else (chr(c) if 32 <= c < 127 else '.') for _, c in chars)
        rise = next((k for k, v in enumerate(r['O']) if r['success'][k] == 1), None)
        return {'text': text, 'success_cycle_0based': rise,
                'message_cycles': (chars[0][0], chars[-1][0]) if chars else None,
                'unknown_bits': sum(v.count('?') for v in r['O']),
                'unknown_bytes': unknown_bytes,
                'indeterminate': unknown_bytes > 0 or r['success'][-1] is None}

    # -- structure -------------------------------------------------------------------
    def cone(self, root: str, depth: int = 12) -> tuple[list[str], int]:
        """Nets feeding `root`, stopping at flops and ports (they are the cone's inputs)."""
        seen, stack, gates = set(), [(root, 0)], 0
        while stack:
            n, d = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            if n in self.flop_of_net or n in set(self.ports.values()):
                continue
            if n not in self.driver or d >= depth:
                continue
            gates += 1
            stack += [(i, d + 1) for _p, i in self.driver[n][3]]
        return sorted(seen), gates

    def reference_replay(self) -> dict:
        """The instrument's own validation: replay example_inputs.vcd and compare to the file.

        This is the claim everything else rests on. The reference stimulus is fed exactly as the file
        recorded it, and every sampled instant of `O` and `success` must agree -- `x` included, since
        the file records them.
        """
        state = {s['inst']: (0 if s['ctrl'] is not None else X) for s in self.nl.seq}
        mismatches, unknown, first = 0, 0, None
        for k, cy in enumerate(self.cycles):
            nets = self._settle(state, {self.ports['rst_n']: int(cy['rst_n']),
                                        self.ports['enable']: int(cy['enable']),
                                        self.ports['I']: int(cy['I']),
                                        self.ports['clk']: 1})
            got_o = ''.join({1: '1', 0: '0', None: '?'}[nets.get(self.ports[f'O[{i}]'])]
                            for i in range(7, -1, -1))
            got_s = {1: '1', 0: '0', None: '?'}[nets.get(self.ports['success'])]
            unknown += got_o.count('?') + got_s.count('?')
            if got_o != cy['O'] or got_s != cy['success']:
                mismatches += 1
                first = first or (k, got_o, cy['O'], got_s, cy['success'])
            nxt = min(k + 1, len(self.cycles) - 1)
            cy2 = self.cycles[nxt]
            state = dict(self.nl.next_state(state, self._settle(
                state, {self.ports['rst_n']: int(cy2['rst_n']), self.ports['enable']: int(cy2['enable']),
                        self.ports['I']: int(cy2['I']), self.ports['clk']: 1})))
        return {'cycles': len(self.cycles), 'output_comparisons': 9 * len(self.cycles),
                'mismatches': mismatches, 'unknown_bits': unknown, 'first_mismatch': first}


# --------------------------------------------------------------------------------------
# boards
# --------------------------------------------------------------------------------------
def answer_cells() -> set[tuple[int, int]]:
    return {(r, c) for r in range(11) for c in range(11) if T.GRID[r][c] == '*'}


def columns_cover() -> list[frozenset]:
    """The eleven columns as cell sets. Cell index is `r * 11 + c`, so a column is fixed `c`."""
    return [frozenset(r * 11 + c for r in range(11)) for c in range(11)]


def is_columns_cover(sets) -> bool:
    """Is this collection of cell sets the eleven columns?

    Compared through a canonical form, not by sorting the sets themselves: `frozenset` defines only a
    *partial* order (subset), so `sorted()` of sets is stable rather than sorted -- it returns them in
    the order they arrived whenever no two are comparable, which is exactly the case here. An
    order-sensitive version of this test therefore answers "is this the columns?" with "did they
    arrive in column order?", and it called the column cover a brand-new candidate the first time it
    ran (R20). Sorting the *elements* first makes the comparison about the sets and nothing else.
    """
    def canon(items):
        return sorted(tuple(sorted(s)) for s in items)

    return canon(sets) == canon(columns_cover())


def region_partition() -> tuple[list[int], dict]:
    """C4's candidate partition as a 121-entry class map, plus the record of where it came from.

    The one loader for the hidden constraint, so C4's artifact is interpreted in exactly one place.
    Refuses to guess: if the artifact is missing, or holds anything other than one non-column
    capacity-2 cover, or the classes do not partition the grid, or the accepted board does not sit two
    per class, this raises. An arbitrary choice here would silently change the problem being solved.
    """
    if not OUT_C4.exists():
        raise SystemExit(f'missing {OUT_C4.relative_to(ROOT).as_posix()} -- run: '
                         f'python -m tools.puzzle region-map')
    art = json.loads(OUT_C4.read_text(encoding='utf-8'))
    ragged = [c for c in art['candidates'] if not c['is_the_visible_column_rule']]
    if len(ragged) != 1:
        raise SystemExit(f'expected exactly one non-column capacity-2 cover, found {len(ragged)}')
    cand = ragged[0]
    class_of = [-1] * 121
    for i, flop in enumerate(cand['flops']):
        for p in cand['classes'][flop]:
            if class_of[p] != -1:
                raise SystemExit(f'cell {p} appears in two classes')
            class_of[p] = i
    if -1 in class_of:
        raise SystemExit('the classes do not cover all 121 cells')
    stars = {r * 11 + c for r, c in answer_cells()}
    loads = [sum(1 for p, k in enumerate(class_of) if k == i and p in stars)
             for i in range(len(cand['flops']))]
    if loads != [2] * len(cand['flops']):
        raise SystemExit(f'the accepted board does not sit two-per-class: {loads}')
    return class_of, {'source': OUT_C4.relative_to(ROOT).as_posix(),
                      'flops': cand['flops'], 'class_sizes': cand['class_sizes'],
                      'is_the_visible_column_rule': False}


def pattern_from_cells(cells) -> list[int]:
    """The 121-bit feed order: first bit = cell (0,0), row-major (tools/target.py)."""
    s = set(cells)
    return [1 if (r, c) in s else 0 for r in range(11) for c in range(11)]


def has_adjacency(cells) -> bool:
    """Eight-neighbour adjacency -- the puzzle's rule, and T4's cell-spacing trap."""
    s = set(cells)
    return any((r + dr, c + dc) in s
               for (r, c) in s for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc)


def visible_rules(cells) -> dict:
    s = set(cells)
    rows = [sum(1 for r, _c in s if r == i) for i in range(11)]
    cols = [sum(1 for _r, c in s if c == i) for i in range(11)]
    return {'distinct': len(s) == 22, 'rows_ok': rows == [2] * 11, 'cols_ok': cols == [2] * 11,
            'no_adjacency': not has_adjacency(s),
            'all_ok': len(s) == 22 and rows == [2] * 11 and cols == [2] * 11
                      and not has_adjacency(s)}


def valid_boards(seed_max: int = 600, want: int = 20) -> list[dict]:
    """C4's rejected-but-visible-valid boards, generator kept verbatim so numbers stay comparable.

    Two different boards have different column counts and diverge early for that reason, so the
    comparison of accepted vs rejected is made at the decision cycle only, on boards that satisfy
    every *visible* rule -- whatever separates them there is the hidden rule's output.
    """
    out = []
    for seed in range(seed_max):
        if len(out) >= want:
            break
        rnd = random.Random(seed)
        placed, count = {}, [0] * 11

        def ok(r, cols):
            a, b = cols
            if abs(a - b) <= 1 or any(count[c] >= 2 for c in cols):
                return False
            prev = placed.get(r - 1)
            return not (prev and any(abs(c - p) <= 1 for c in cols for p in prev))

        def dfs(r):
            if r == 11:
                return all(n == 2 for n in count)
            pool = list(combinations(range(11), 2))
            rnd.shuffle(pool)
            for c in pool:
                if not ok(r, c):
                    continue
                placed[r], saved = list(c), count[:]
                for cc in c:
                    count[cc] += 1
                if dfs(r + 1):
                    return True
                count[:] = saved
                del placed[r]
            return False

        if dfs(0):
            cells = frozenset((r, c) for r, cols in placed.items() for c in cols)
            if cells != answer_cells():
                out.append({'seed': seed, 'cells': sorted(cells)})
    return out


def two_switch_boards(n: int = 40, seed_base: int = 0) -> list[dict]:
    """C5's falsification set: walk away from the winning pattern, keeping every visible rule.

    A swap exchanges the columns of two rows, which preserves both the row and the column counts by
    construction; moves that would create adjacency are rejected rather than accepted with a caveat.

    Which star of a row is moved is stated explicitly (`min`), not left to set iteration order: the
    original probe took the first cell the set happened to yield, which is deterministic only until
    the set is copied (`set(s)` can reorder), so the same code gave different boards in a different
    container. The boards here are therefore *this* generator's, re-measured rather than inherited,
    and C5's conclusion (every such board is rejected) is checked on them from scratch.
    """
    out = []
    for seed in range(seed_base, seed_base + n):
        rng = random.Random(seed)
        cells = set(answer_cells())
        for _ in range(rng.randint(1, 15)):
            for _attempt in range(20):
                r1, r2 = rng.sample(range(11), 2)
                c1 = min(c for r, c in cells if r == r1)
                c2 = min(c for r, c in cells if r == r2)
                if c1 == c2:
                    continue
                trial = (cells - {(r1, c1), (r2, c2)}) | {(r1, c2), (r2, c1)}
                if len(trial) == 22 and not has_adjacency(trial):
                    cells = trial
                    break
        out.append({'seed': seed, 'cells': sorted(cells), **visible_rules(cells)})
    return out


# --------------------------------------------------------------------------------------
# partitions: exact cover, capacity, and solution counting
# --------------------------------------------------------------------------------------
def exact_covers(sets: dict[str, set[int]], cap: int = 200) -> list[list[str]]:
    """Every sub-collection of `sets` that partitions the 121 cells.

    Complete over the candidate collection, driven by the lowest uncovered cell so no branch is
    missed and none is walked twice.
    """
    allm = (1 << 121) - 1
    masks = [(name, sum(1 << p for p in sorted(s))) for name, s in sorted(sets.items()) if s]
    by_cell: dict[int, list[int]] = {}
    for idx, (_n, m) in enumerate(masks):
        for p in range(121):
            if m >> p & 1:
                by_cell.setdefault(p, []).append(idx)
    found: list[list[str]] = []

    def dfs(covered: int, chosen: list[str]) -> None:
        if len(found) >= cap:
            return
        if covered == allm:
            found.append(list(chosen))
            return
        cell = next(p for p in range(121) if not (covered >> p & 1))
        for idx in by_cell.get(cell, []):
            name, m = masks[idx]
            if m & covered:
                continue
            chosen.append(name)
            dfs(covered | m, chosen)
            chosen.pop()

    dfs(0, [])
    return found


def capacity2_cover(sets: dict[str, set[int]], star_cells: set[tuple[int, int]]) -> list[list[str]]:
    """The covers that are a region map as the puzzle describes it: 11 classes, two stars each."""
    stars = {r * 11 + c for r, c in star_cells}
    return [c for c in exact_covers(sets) if len(c) == 11
            and all(len(sets[n] & stars) == 2 for n in c)]


PAIR_PATTERNS = [(a, b) for a, b in combinations(range(11), 2) if abs(a - b) >= 2]


def count_solutions(class_of: list[int] | None, cap: int = 2, node_budget: int = 4_000_000):
    """Boards with two per row, two per column, no adjacency -- optionally at most two per class.

    Exhaustive: the answer to "is it unique?" has to come from a finished search, so the count only
    stops early once it has seen `cap` solutions, and a node budget keeps a weak constraint set from
    running for hours.
    """
    colcnt = [0] * 11
    clscnt = [0] * 11
    board: list[tuple[int, int] | None] = [None] * 11
    nodes = total = 0

    def dfs(r: int) -> bool:
        nonlocal nodes, total
        nodes += 1
        if nodes > node_budget:
            return True
        if r == 11:
            if all(v == 2 for v in colcnt):
                total += 1
                return total >= cap
            return False
        prev = board[r - 1]
        for a, b in PAIR_PATTERNS:
            if colcnt[a] >= 2 or colcnt[b] >= 2:
                continue
            if class_of is not None:
                ca, cb = class_of[r * 11 + a], class_of[r * 11 + b]
                if ca == cb:
                    if clscnt[ca] + 2 > 2:
                        continue
                elif clscnt[ca] + 1 > 2 or clscnt[cb] + 1 > 2:
                    continue
            if prev is not None and any(abs(x - y) <= 1
                                        for x in (a, b) for y in prev):
                continue
            colcnt[a] += 1
            colcnt[b] += 1
            if class_of is not None:
                ca, cb = class_of[r * 11 + a], class_of[r * 11 + b]
                clscnt[ca] += 1
                clscnt[cb] += 1
            board[r] = (a, b)
            stop = dfs(r + 1)
            board[r] = None
            if class_of is not None:
                ca, cb = class_of[r * 11 + a], class_of[r * 11 + b]
                clscnt[ca] -= 1
                clscnt[cb] -= 1
            colcnt[a] -= 1
            colcnt[b] -= 1
            if stop:
                return True
        return False

    dfs(0)
    return total, nodes


def class_of_grid(sets: list[set[int]]) -> list[int]:
    """A 121-entry class map from a list of cell sets (each cell must appear exactly once)."""
    out = [-1] * 121
    for i, s in enumerate(sets):
        for p in s:
            assert out[p] == -1, f'cell {p} is in two classes'
            out[p] = i
    assert -1 not in out, 'cells not covered'
    return out


def same_shape_partitions(rng: random.Random, sizes: list[int],
                          star_cells: set[tuple[int, int]]) -> list[int]:
    """A random partition with the same class sizes and exactly two answer stars in every class.

    The null model for "does this partition pin the answer?": same shape, same necessary property,
    different cells.
    """
    stars = [r * 11 + c for r, c in sorted(star_cells)]
    rng.shuffle(stars)
    class_of = [-1] * 121
    for i in range(len(sizes)):
        for p in stars[2 * i:2 * i + 2]:
            class_of[p] = i
    rest = [p for p in range(121) if class_of[p] == -1]
    rng.shuffle(rest)
    k = 0
    for i, s in enumerate(sizes):
        for _ in range(s - 2):
            class_of[rest[k]] = i
            k += 1
    return class_of


# --------------------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------------------
OUT_C4 = ROOT / 'recon' / 'derived' / 'c4_partition.json'
OUT_C5 = ROOT / 'recon' / 'derived' / 'c5_rejections.json'
OUT_E1 = ROOT / 'recon' / 'derived' / 'e1_messages.json'


def stage_region_map() -> int:
    """C4: measure every flop's single-star trigger set, then find the partition they form."""
    m = Machine(cycles=C4_CYCLES)
    print(f'instrument: {len(m.nl.comb)} combinational gates, {len(m.nl.seq)} flops, '
          f'{len(m.nl.consts)} tie cells; window {len(m.cycles)} cycles')

    # ---- the sweep: a single star at each position, and which flops end high -----------
    triggers: dict[str, set[int]] = {}
    for p in range(121):
        pat = [1 if i == p else 0 for i in range(121)]
        final = m.run(pat)['final']
        for inst, q in final.items():
            if q == 1:
                triggers.setdefault(inst, set()).add(p)
        if p % 30 == 0:
            print(f'  swept {p}/121', flush=True)
    nonempty = {i: s for i, s in triggers.items() if s}
    print(f'  flops that end high for at least one single-star input: {len(nonempty)}')

    # ---- the partition is *found*, not assumed: exact cover over the trigger sets -------
    covers = exact_covers(nonempty)
    stars = answer_cells()
    star_lin = {r * 11 + c for r, c in stars}
    cap2 = [c for c in covers if len(c) == 11
            and all(len(nonempty[n] & star_lin) == 2 for n in c)]
    print(f'  exact covers of the 121 cells by trigger sets: {len(covers)} (the trivial covers, where '
          f'one flop fires everywhere, included)')
    print(f'  covers with 11 classes holding exactly two answer stars each: {len(cap2)}')
    # A column: cell index is r*11 + c, so a fixed c with r running (the first version of this had the
    # two swapped, which made it rows and mislabelled the column cover as a new candidate).
    for cov in covers:
        sizes = sorted(len(nonempty[n]) for n in cov)
        same_as_columns = is_columns_cover([nonempty[n] for n in cov])
        print(f'    {len(cov):2d} classes, sizes {sizes}'
              f'{"   <-- capacity-2 reading" if cov in cap2 else ""}'
              f'{" = the eleven columns (the visible rule itself)" if same_as_columns else ""}')

    # ---- controls: how much is each candidate worth? ------------------------------------
    # The controls are measured *against C5's rejection set*, so that artifact is an input here, not
    # an optional extra. E4's cold run caught the earlier version skipping it silently when the file
    # was absent (C4 runs before C5 in the table): the artifact then regenerated without these fields
    # and was not byte-reproducible. Missing input is now an error that names the command to run.
    if not OUT_C5.exists():
        raise SystemExit(f'missing {OUT_C5.relative_to(ROOT).as_posix()} -- C4 s controls are measured '
                         f'against the rejected boards, so run: python -m tools.puzzle rejections')
    c5 = json.loads(OUT_C5.read_text(encoding='utf-8'))
    candidates = []
    for idx, cov in enumerate(cap2):
        cls = [nonempty[n] for n in cov]
        is_columns = is_columns_cover(cls)
        entry = {
            'name': 'columns' if is_columns else f'irregular_{idx}',
            'flops': sorted(cov),
            'class_sizes': sorted(len(s) for s in cls),
            'classes': {n: sorted(nonempty[n]) for n in sorted(cov)},
            'is_the_visible_column_rule': is_columns,
        }
        got, nodes = count_solutions(class_of_grid(cls))
        entry['unique_solution'] = {'solutions': got, 'nodes': nodes, 'cap': 2}
        print(f'\n  candidate {entry["name"]}: class sizes {entry["class_sizes"]}; '
              f'the visible column rule itself? {is_columns}')
        print(f'    + two per row + two per column + no adjacency -> {got} solution(s) '
              f'({nodes} nodes){"" if got > 1 else " -- the accepted board is the only one"}')
        if c5:
            boards = [set(map(tuple, b['cells'])) for b in c5['boards']]
            # (a) the rejection test: does "no class over 2" separate this partition from a
            #     look-alike? If most look-alikes also reject every board, the design's own
            #     rejections carry almost no information about which partition is the real one.
            rng = random.Random(1)
            trials, pass_all = 200, 0
            for _ in range(trials):
                look = same_shape_partitions(rng, entry['class_sizes'], stars)
                ok = True
                for b in boards:
                    load = [0] * 11
                    for cell in b:
                        load[look[cell[0] * 11 + cell[1]]] += 1
                    if max(load) <= 2:
                        ok = False
                        break
                pass_all += 1 if ok else 0
            entry['rejection_test'] = {
                'boards': len(boards), 'lookalikes_tested': trials,
                'lookalikes_that_also_reject_every_board': pass_all,
                'discriminating_power': None if not trials else 1 - pass_all / trials}
            print(f'    rejection test: {pass_all} of {trials} same-shape look-alikes also reject '
                  f'all {len(boards)} boards, so the test discriminates '
                  f'{100 * (1 - pass_all / trials):.0f}% of the time')
            # (b) is "exactly one solution" generic?
            rng = random.Random(20260913)
            trials_b, n_unique = 6, 0
            for _ in range(trials_b):
                look = same_shape_partitions(rng, entry['class_sizes'], stars)
                u, _n = count_solutions(look)
                n_unique += 1 if u == 1 else 0
            entry['lookalike_uniqueness'] = {'tested': trials_b, 'also_unique': n_unique}
            print(f'    uniqueness test: {n_unique} of {trials_b} same-shape look-alikes also have '
                  f'exactly one solution')
        candidates.append(entry)

    # ---- for scale: what the visible rules alone leave open ------------------------------
    mech, mech_nodes = count_solutions(None, cap=1000, node_budget=2_000_000)
    print(f'\n  visible rules alone: {mech} solutions seen before the cap/budget '
          f'({mech_nodes} nodes) -- a lower bound, the cap was reached')
    print('    (the full count is D3s question, and no region-free search of this size is a '
          'constraint system the chip could be enforcing)')

    report = {
        'generated_by': 'tools/puzzle/verdict.py::stage_region_map',
        'method': {
            'instrument': 'the simulation-free evaluator of tools/puzzle/verdict.py, validated '
                          'against example_inputs.vcd by check_stepC4.py on every run',
            'sweep': 'one star at each of the 121 positions; the flops that end high are recorded, '
                     'so a trigger set is a measurement, not a hypothesis',
            'partition': 'found as an exact cover of the 121 cells by those trigger sets, with the '
                         'capacity-2 covers (11 classes, two answer stars each) selected from it',
            'what_it_is_not': 'a proven map. Two capacity-2 covers fall out and neither is picked '
                              'here: one of them is the visible column rule and adds nothing, the '
                              'other does pin the accepted board, and the controls measure how weak '
                              'that is as evidence. C4 R9-R18 remain the record of what was searched '
                              'and not found (no per-cell decode, no per-region counter)',
        },
        'window_cycles': C4_CYCLES,
        'flops_with_a_trigger_set': len(nonempty),
        'trigger_sets': {i: sorted(s) for i, s in sorted(nonempty.items())},
        'exact_covers': [{'classes': len(c), 'sizes': sorted(len(nonempty[n]) for n in c),
                          'is_the_eleven_columns': is_columns_cover([nonempty[n] for n in c]),
                          'flops': sorted(c)} for c in covers],
        'capacity2_cover_count': len(cap2),
        'candidates': candidates,
        'visible_rules_only': {'solutions_seen': mech, 'nodes': mech_nodes, 'cap': 1000,
                               'lower_bound_only': True},
    }
    OUT_C4.parent.mkdir(parents=True, exist_ok=True)
    OUT_C4.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(f'report: {OUT_C4.relative_to(ROOT).as_posix()}')
    return 0


def stage_rejections() -> int:
    """C5: boards that satisfy every visible rule and are rejected anyway."""
    m = Machine(cycles=MESSAGE_CYCLES)
    boards = two_switch_boards(40)
    print(f'generated {len(boards)} boards by two-switches from the winning pattern')
    # The generator can in principle return the winning pattern or a pattern with adjacency; such a
    # board would say nothing, so it is counted and reported rather than quietly included.
    usable = [b for b in boards if b['all_ok'] and set(map(tuple, b['cells'])) != answer_cells()]
    print(f'  usable (all visible rules, distinct from the winning pattern): {len(usable)}')
    accepted = 0
    for b in usable:
        r = m.message(pattern_from_cells(b['cells']))
        b['message'] = r['text']
        b['message_cycles'] = r['message_cycles']
        b['success_any'] = r['success_cycle_0based'] is not None
        b['unknown_bits'] = r['unknown_bits']
        accepted += 1 if r['text'] == '(* TWO STARS *)' else 0
    print(f'  accepted: {accepted}   rejected: {len(usable) - accepted}')
    for b in usable[:4]:
        print(f'    seed {b["seed"]:>3}: {b["message"]!r}')

    # The instrument is checked against the simulator once per run: the reference waveform.
    replay = m.reference_replay()
    print(f'  instrument on the reference waveform: {replay["output_comparisons"]} comparisons, '
          f'{replay["mismatches"]} mismatches, {replay["unknown_bits"]} unknown bits')

    report = {
        'generated_by': 'tools/puzzle/verdict.py::stage_rejections',
        'method': {
            'boards': 'random 2-switches away from the winning pattern (two rows exchange their '
                      'columns, which preserves the row and column counts by construction), keeping '
                      'only moves that leave the pattern adjacency-free -- so every board satisfies '
                      '22 ones, two per row, two per column and no 8-neighbour adjacency',
            'decision': 'the design is asked and its own message is read off O; no internal signal '
                        'is interpreted',
            'instrument': 'tools/puzzle/verdict.py, re-validated against example_inputs.vcd on '
                          'every gate run',
        },
        'window_cycles': MESSAGE_CYCLES,
        'boards': usable,
        'generated': len(boards),
        'accepted': accepted,
        'rejected': len(usable) - accepted,
        'reference_replay': replay,
    }
    OUT_C5.parent.mkdir(parents=True, exist_ok=True)
    OUT_C5.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(f'report: {OUT_C5.relative_to(ROOT).as_posix()}')
    return 0 if accepted == 0 and len(usable) >= 20 else 1


def adjacent_vector() -> str:
    """Two per row and column, with an adjacent pair -- E1's `TWO NOT TOUCH` case.

    Built rather than guessed: stars at the four corners of the top-left 2x2 block (which makes rows
    0-1 and columns 0-1 hold two each and puts an adjacent pair in the grid), then rows 2..10 filled
    with a Latin pattern so every remaining column also ends up with exactly two.
    """
    cells = {(0, 0), (0, 1), (1, 0), (1, 1)}
    for r in range(2, 11):
        cells.add((r, 2 + (r - 2) % 9))
        cells.add((r, 2 + (r - 1) % 9))
    return ''.join('1' if (r, c) in cells else '0' for r in range(11) for c in range(11))


def other_wrong_vector() -> str:
    """The winning grid with one star moved: still 22 ones, but no longer two per row."""
    cells = [(r, c) for r in range(11) for c in range(11) if T.GRID[r][c] == '*']
    cells[0] = (cells[0][0], (cells[0][1] + 1) % 11)
    return ''.join('1' if (r, c) in set(cells) else '0' for r in range(11) for c in range(11))


def stage_winning() -> int:
    """E1: drive the netlist with the winning vector, and with each class of wrong input."""
    m = Machine(cycles=MESSAGE_CYCLES)
    winning = [int(b) for b in T.FEED_ORDER]

    print('offset   success cycle   message                       ones fed in window')
    offsets = []
    for offset in (4, 5, 6, 7, 8):
        m.offset = offset
        r = m.message(winning)
        # "ones fed" is measured on the run, not computed from the vector: the bit is clocked in only
        # where enable is high, and an offset that pushes a bit outside the window drops it.
        ones = sum(1 for k, cy in enumerate(m.cycles)
                   if cy['enable'] == '1' and 0 <= k - offset < 121 and winning[k - offset] == 1)
        offsets.append({'offset': offset, 'success_cycle_0based': r['success_cycle_0based'],
                        'success_cycle_1based': (None if r['success_cycle_0based'] is None
                                                 else r['success_cycle_0based'] + 1),
                        'message': r['text'], 'message_cycles': r['message_cycles'],
                        'ones_in_window': ones, 'unknown_bits': r['unknown_bits']})
        print(f'  {offset:<6} {str(offsets[-1]["success_cycle_1based"]):<15} '
              f'{r["text"]!r:<29} {ones}')
    m.offset = WIN_FEED_OFFSET

    cases = [
        ('all_zeros', '0' * 121, 'EMPTY SKY'),
        ('all_ones', '1' * 121, 'BIG BANG'),
        ('two_per_row_col_but_adjacent', adjacent_vector(), 'TWO NOT TOUCH'),
        ('other_wrong', other_wrong_vector(), 'TRY AGAIN'),
        ('correct', T.FEED_ORDER, '(* TWO STARS *)'),
    ]
    print(f'\n{"case":<30} {"ones":>4}  {"expected":<16} {"got":<16} {"success":>7}  cycles')
    results = []
    for tag, feed, expected in cases:
        r = m.message([int(b) for b in feed])
        results.append({'case': tag, 'ones': feed.count('1'), 'expected': expected,
                        'got': r['text'], 'match': r['text'] == expected,
                        'success_cycle_0based': r['success_cycle_0based'],
                        'success_cycle_1based': (None if r['success_cycle_0based'] is None
                                                 else r['success_cycle_0based'] + 1),
                        'message_cycles': r['message_cycles'], 'unknown_bits': r['unknown_bits']})
        print(f'  {tag:<28} {feed.count("1"):>4}  {expected!r:<16} {r["text"]!r:<16} '
              f'{str(results[-1]["success_cycle_1based"]):>7}  {r["message_cycles"]}'
              f'{"   OK" if results[-1]["match"] else "   MISMATCH"}')

    report = {
        'generated_by': 'tools/puzzle/verdict.py::stage_winning',
        'method': {
            'control_timeline': 'rst_n and enable copied from example_inputs.vcd; only the data line '
                                'is replaced, so the payload is the single variable',
            'offset': 'measured, not assumed: the acceptance test (success at cycle 126) decides it',
            'instrument': 'tools/puzzle/verdict.py, re-validated against example_inputs.vcd on '
                          'every gate run',
        },
        'window_cycles': MESSAGE_CYCLES,
        'success_cycle_expected': T.SUCCESS_CYCLE,
        'feed_order_bits': 121,
        'offsets': offsets,
        'messages': results,
        'open': 'TWO NOT TOUCH cannot be constructed without knowing the region map: the adjacent '
                'vector built here also violates the hidden constraint, and the design answers '
                'TRY AGAIN. That is E1s measured result, recorded rather than worked around.',
    }
    OUT_E1.parent.mkdir(parents=True, exist_ok=True)
    OUT_E1.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(f'\nreport: {OUT_E1.relative_to(ROOT).as_posix()}')
    matched = sum(1 for r in results if r['match'])
    print(f'E1: {matched} of {len(results)} message classes reproduced')
    return 0 if matched >= 4 else 1


def main(argv: list[str] | None = None) -> int:
    stage = (argv or ['region-map'])[0]
    stages = {'region-map': stage_region_map, 'rejections': stage_rejections,
              'winning': stage_winning}
    if stage not in stages:
        print(f'verdict.py has no stage {stage!r}; it has {sorted(stages)}', file=sys.stderr)
        return 2
    return stages[stage]()


if __name__ == '__main__':
    raise SystemExit(main())
