#!/usr/bin/env python3
"""simulate.py -- C1: replay the reference waveform into our emitted netlist.

Up to here every check compared our artifacts with *evidence about the artifacts*. B7 broke
that pattern once, on the warm-up, and it was the only time an independent answer existed.
The chip has one too, and this stage uses it: `asic-puzzle-2026/example_inputs.vcd` is a real
simulation of the real design, so its *outputs* are ground truth for our recovered netlist and
our 69 behavioural models. The README says the waveform does not contain the winning input --
that is fine, and in fact better: it exercises the wrong-input path, and `success` staying low
for all 312 cycles is itself a prediction our netlist has to reproduce.

Why "byte-exact replay" means semantic equality and not a diff of two VCD files
--------------------------------------------------------------------------------
Two simulators never agree on a VCD byte-for-byte: identifier characters, dump granularity,
scope nesting (this reference emits one `$scope` block *per signal*, which no simulator we know
of does), and x-propagation detail all differ. So the deliverable is stricter where it matters
and honest about where it cannot be: every value of every signal is compared at every one of
the 312 sampled instants, x included. 312 cycles x 9 output bits = 2808 comparisons, plus 936
on the stimulus.

The timing convention, measured rather than assumed
--------------------------------------------------
* `clk` has 625 edges, every one exactly 5000 ps apart, the first rise at 5000 ps.
* every change to `rst_n` / `enable` / `I` is on a falling edge (a multiple of 10000 ps).
* therefore "cycle k" is t = 5000 + 10000k ps: the register state *after* the rising edge at
  that instant (which is what a VCD dumps) together with the stimulus held across the cycle.
We sample one time step *after* the rising edge, so a rising edge and an assignment in the
harness can never race. `_derive_clock` asserts the uniformity above against the file, and the
resulting table is cross-checked against `recon/vcd_cycles.csv`, the Step-1 dossier's own
per-cycle reading -- a second, older derivation of the same facts.

The stimulus is transcribed, not authored
-----------------------------------------
`emit_testbench` writes the transition list *parsed from the file* as `#delay` assignments, so
the harness cannot drift from the waveform. The only thing not transcribed is the clock, and
that is the one property asserted (`always #<half_period>`).

The undriven net, resolved instead of deferred
----------------------------------------------
B5 enumerated exactly one net with no driver: cluster 806, terminals `a311o_2.A1` and
`a31oi_2.A1`, both inputs. B7 predicted C1 would "show it as X" -- measured, it reads **z**, which
is what 4-state simulation gives an undriven `wire` (an unwritten `reg` would read x), and the
distinction is worth recording rather than glossing: the prediction was right that the value is
undefined and wrong about which flavour. The real question is whether it reaches an output, and
two runs answer it instead of an argument: the same replay with the wire first forced to 0 and
then forced to 1. Both leave every interface output identical to the reference over all 312
cycles, so the net cannot move an output *under this stimulus*. That is deliberately narrower
than "the pin is dead": the masking depends on the other inputs of its two consumers, which are
state-dependent, so the winning vector is re-probed in E1 where it exists.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

REF_VCD = ROOT / 'asic-puzzle-2026' / 'example_inputs.vcd'
STEP1_CSV = ROOT / 'recon' / 'vcd_cycles.csv'
NETLIST_CHECK = ROOT / 'recon' / 'derived' / 'netlist_check.json'
EMITTED = ROOT / 'build' / 'puzzle.v'
CELLS = ROOT / 'build' / 'cells.v'

OUT_TB = ROOT / 'build' / 'replay_tb.v'
OUT_REPORT = ROOT / 'recon' / 'derived' / 'vcd_replay.json'
VVP = ROOT / 'recon' / 'scratch' / 'sim' / 'replay.vvp'

#: A *literal*, never `str(OUT_TB.relative_to(ROOT))`. The gates redirect OUT_TB into scratch
#: to regenerate it hermetically, and an artifact that embeds its own output path then differs
#: from the committed one by that path alone (the non-determinism B4 hit with a stored runtime,
#: and B7 avoided the same way). The artifact must not depend on where it was written.
EMITTED_REL = 'build/replay_tb.v'

#: One time step between the rising edge and the sample. Any value in (0, half_period) gives
#: the same readings; 1 ps is the timescale's own step, so it is not an arbitrary choice.
SAMPLE_DELAY_PS = 1

TS_RE = re.compile(r'\$timescale\s+(\d+)\s*([fpnum]?s)\s*\$end')
SCOPE_RE = re.compile(r'\$scope\s+(\w+)\s+([^$]+?)\s*\$end')
VAR_RE = re.compile(r'\$var\s+(\w+)\s+(\d+)\s+(\S+)\s+([^$]+?)\s*\$end')
TIME_RE = re.compile(r'^#(\d+)$')

#: The order the interface is declared in, and the width the reference gives each signal.
EXPECTED_SIGNALS = {'clk': 1, 'rst_n': 1, 'enable': 1, 'I': 1, 'O': 8, 'success': 1}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------------
# the reference waveform
# ---------------------------------------------------------------------------------
def parse_vcd(path: Path) -> dict:
    """The reference as data: declared signals, and every value change, by name.

    Deliberately a plain reader with no interpretation: the timing convention is derived
    separately, in :func:`derive_clock`, so a reader bug cannot hide inside a timing bug.
    """
    text = path.read_text(encoding='utf-8', errors='replace')
    ts = TS_RE.search(text)
    if not ts:
        raise ValueError('no $timescale')
    unit = {'s': 10 ** 12, 'ms': 10 ** 9, 'us': 10 ** 6, 'ns': 10 ** 3,
            'ps': 1, 'fs': 10 ** -3}[ts.group(2)]

    signals, scopes = [], []
    for m in SCOPE_RE.finditer(text):
        scopes.append(m.group(1))
    by_id = {}
    for m in VAR_RE.finditer(text):
        kind, width, ident, name = m.group(1), int(m.group(2)), m.group(3), m.group(4).strip()
        base = name.split()[0]                     # `O [7:0]` -> `O`
        by_id[ident] = base
        signals.append({'id': ident, 'kind': kind, 'width': width, 'name': name, 'base': base})

    body = text.split('$enddefinitions $end', 1)[1]
    changes: dict[str, list[tuple[int, str]]] = {s['base']: [] for s in signals}
    t = 0
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith('$'):
            continue
        m = TIME_RE.match(line)
        if m:
            t = int(m.group(1))
            continue
        if line[0] == 'b':
            val, ident = line[1:].split()
        else:
            val, ident = line[0], line[1:]
        name = by_id.get(ident)
        if name is None:
            raise ValueError(f'value change for undeclared id {ident!r}')
        changes[name].append((t, val))

    # A VCD is a stream: a second change at the same instant is a glitch within that instant,
    # and the *last* one is what the signal settles to. Keeping both would break the
    # monotone walk in `values_at`, so collapse equal timestamps to the settled value.
    for name, seq in changes.items():
        merged: list[tuple[int, str]] = []
        for tt, vv in seq:
            if merged and merged[-1][0] == tt:
                merged[-1] = (tt, vv)
            else:
                merged.append((tt, vv))
        changes[name] = merged

    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha256(path),
            'timescale_ps': unit, 'scopes': scopes, 'signals': signals, 'changes': changes}


def derive_clock(changes: dict[str, list[tuple[int, str]]]) -> dict:
    """The clock geometry, asserted rather than assumed: uniform edges, first rise, half period."""
    edges = changes['clk']
    times = [t for t, _ in edges]
    if len(times) < 4:
        raise ValueError('the reference has no usable clock')
    deltas = {times[i + 1] - times[i] for i in range(len(times) - 1)}
    uniform = len(deltas) == 1
    half = min(deltas)
    rises = [t for t, v in edges if v == '1']
    return {'edges': len(edges), 'half_period_ps': half, 'first_rise_ps': rises[0],
            'uniform': uniform, 'distinct_deltas': sorted(deltas),
            'posedges': len(rises)}


def values_at(changes: dict[str, list[tuple[int, str]]], name: str, width: int,
              times: list[int]) -> list[str]:
    """The settled value of `name` at each time, as a fixed-width 0/1/x/z string."""
    seq = changes[name]
    out: list[str] = []
    i, cur = 0, None
    for t in times:
        while i < len(seq) and seq[i][0] <= t:
            cur = seq[i][1]
            i += 1
        out.append(_normalise(cur, width))
    return out


def _normalise(raw: str | None, width: int) -> str:
    if raw is None:
        return 'x' * width
    v = raw[1:] if raw.startswith('b') else raw
    if 'x' in v:
        return 'x' * width
    if 'z' in v:
        return 'z' * width
    return v.rjust(width, '0')


# ---------------------------------------------------------------------------------
# what C1 asserts about the reference, before any simulation runs
# ---------------------------------------------------------------------------------
def read_reference() -> dict:
    """The reference table plus the derived facts the stage and its gate both need."""
    ref = parse_vcd(REF_VCD)
    width = {s['base']: s['width'] for s in ref['signals']}
    if set(EXPECTED_SIGNALS) - set(width):
        raise ValueError(f'reference is missing signals: '
                         f'{sorted(set(EXPECTED_SIGNALS) - set(width))}')
    clock = derive_clock(ref['changes'])
    times = [clock['first_rise_ps'] + 2 * clock['half_period_ps'] * k + SAMPLE_DELAY_PS
             for k in range(clock['posedges'])]
    table = {name: values_at(ref['changes'], name, width[name], times) for name in width}
    cycles = [{'cycle': k, 't': times[k] - SAMPLE_DELAY_PS,
               **{n: table[n][k] for n in ('rst_n', 'enable', 'I', 'O', 'success')}}
              for k in range(len(times))]
    return {'ref': ref, 'width': width, 'clock': clock, 'times': times,
            'table': table, 'cycles': cycles}


def enable_runs(enable: list[str]) -> list[list[int]]:
    """The maximal runs of cycles with `enable` high: the two 121-bit feeds."""
    runs, start = [], None
    for k, v in enumerate(enable):
        if v == '1' and start is None:
            start = k
        elif v != '1' and start is not None:
            runs.append([start, k - 1])
            start = None
    if start is not None:
        runs.append([start, len(enable) - 1])
    return runs


def nonzero_runs(bits: list[str]) -> list[list[int]]:
    """The maximal runs of cycles whose 8-bit value is not zero: the streamed characters."""
    return enable_runs(['0' if all(c == '0' for c in b) else '1' for b in bits])


def decode_streams(O: list[str]) -> list[dict]:
    """Read the output stream as ASCII, from the transitions themselves."""
    out = []
    for a, b in nonzero_runs(O):
        chars = [int(O[k], 2) for k in range(a, b + 1)]
        out.append({'cycles': [a, b], 'bytes': chars,
                    'hex': ''.join(f'{c:02x}' for c in chars),
                    'text': ''.join(chr(c) if 32 <= c < 127 else '.' for c in chars)})
    return out


# ---------------------------------------------------------------------------------
# the harness
# ---------------------------------------------------------------------------------
def probe_wires() -> list[dict]:
    """The undriven net(s) B5 enumerated, and the wire name the emitter gave each.

    Taken from the artifact that documents them rather than hard-coded, so the harness and the
    netlist cannot disagree about the name.
    """
    nc = json.loads(NETLIST_CHECK.read_text(encoding='utf-8'))
    text = EMITTED.read_text(encoding='utf-8')
    out = []
    for rec in nc['direction']['undriven_nets']:
        wire = f"n{rec['cluster']}"
        if not re.search(rf'^\s*wire\s+{wire}\s*;', text, re.M):
            raise ValueError(f'undriven cluster {rec["cluster"]} is not a wire named {wire!r} '
                             f'in {EMITTED.name} -- the emitter renamed something')
        out.append({'cluster': rec['cluster'], 'wire': wire,
                    'terminals': rec['terminals'],
                    'pin_names': sorted(rec['pin_names'])})
    return out


def emit_testbench(ref_data: dict, probes: list[dict], out_path: Path) -> str:
    """Write the harness: the reference's stimulus, transcribed; its clock, asserted."""
    ref, clock, width = ref_data['ref'], ref_data['clock'], ref_data['width']
    cycles = clock['posedges']
    half = clock['half_period_ps']

    # ---- the stimulus, merged across signals and grouped by instant ----------------
    step: list[tuple[int, str, str]] = []
    initial: dict[str, str] = {}
    for name in ('rst_n', 'enable', 'I'):
        seq = ref['changes'][name]
        if not seq:
            raise ValueError(f'no changes for {name}')
        initial[name] = _normalise(seq[0][1], 1)
        step += [(t, name, _normalise(v, 1)) for t, v in seq if t > 0]
    step.sort(key=lambda r: (r[0], r[1]))

    stim: list[str] = []
    prev = 0
    i = 0
    while i < len(step):
        t = step[i][0]
        same = [r for r in step if r[0] == t]
        assigns = ' '.join(f'{n} = 1\'b{v};' for _, n, v in same)
        stim.append(f'    #{t - prev} {assigns}')
        prev = t
        i += len(same)

    probe_fmt = ' %b' * len(probes)
    # The probe is read hierarchically rather than aliased: one less net to keep in step with
    # the emitter's naming, and iverilog resolves `dut.<wire>` for reading and for `force` alike.
    display_args = ', '.join(f'dut.{a["wire"]}' for a in probes)
    force_block = ''
    for a in probes:
        force_block += (f'    if ($value$plusargs("force=%d", force_arg)) begin\n'
                        f'      force_bit = force_arg[0];\n'
                        f'      force dut.{a["wire"]} = force_bit;\n'
                        f'      $display("F %s forced = %b", "{a["wire"]}", force_bit);\n'
                        f'    end\n')

    head = [
        '// =============================================================================',
        f'// {EMITTED_REL} -- C1: replay the reference stimulus into {EMITTED.name}.',
        '//',
        '// Generated by tools/puzzle/simulate.py (plan step C1). Do not edit by hand.',
        '// Regenerate with:  python -m tools.puzzle vcd-replay',
        f'// Compile with:     iverilog -g2012 -o replay.vvp {EMITTED_REL} '
        f'{EMITTED.name} build/{CELLS.name}',
        '//',
        f'// The stimulus below is not authored. It is the change list parsed out of',
        f'// asic-puzzle-2026/example_inputs.vcd',
        f'//   sha256 {ref["sha256"]}',
        f'// replayed at the same instants ({len(step)} changes on rst_n/enable/I, all of them',
        f'// on falling edges). The one thing not transcribed is the clock, because it is the one',
        f'// thing that can be asserted: {clock["edges"]} edges, every one {half} ps apart, first',
        f'// rise at {clock["first_rise_ps"]} ps -- so `always #{half}` reproduces it exactly, and',
        '// tools/checks/check_stepC1.py re-checks that uniformity against the file itself.',
        '//',
        f'// Sampling: {SAMPLE_DELAY_PS} ps after each rising edge, the instant the reference',
        '// dumps -- the register state after the edge, with the stimulus held across the cycle.',
        f'// {cycles} cycles are printed, one line each, as',
        '//   C <cycle> <rst_n> <enable> <I> <O[7:0]> <success> [<probe>...]',
        '//',
        '// Usage:  vvp replay.vvp [+force=0|1]',
        '//   +force=<v>  forces each undriven net to <v> for the whole run, to test whether it',
        '//               can move an output at all. Without it nothing is forced.',
        '// =============================================================================',
        '',
        '`timescale 1ps/1ps',
        '',
        'module replay_tb;',
        f'  localparam integer CYCLES = {cycles};',
        f'  localparam integer SAMPLE_DELAY_PS = {SAMPLE_DELAY_PS};',
        '',
        '  reg clk = 1\'b0;',
    ]
    for name in ('rst_n', 'enable', 'I'):
        head.append(f'  reg {name} = 1\'b{initial[name]};')
    head += [
        '  wire [7:0] O;',
        '  wire success;',
        '  integer cyc = 0;',
        '  integer force_arg;',
        '  reg force_bit = 1\'b0;',
        '',
    ]
    head += [
        '  // VGND/VPWR are declared inout on the netlist, so they have to be driven through a',
        '  // net that supports continuous assignment rather than tied to a literal expression.',
        '  wire VGND;',
        '  wire VPWR;',
        '  assign VGND = 1\'b0;',
        '  assign VPWR = 1\'b1;',
        '',
        '  puzzle dut (.clk(clk), .rst_n(rst_n), .enable(enable), .I(I),',
        '              .O(O), .success(success), .VGND(VGND), .VPWR(VPWR));',
        '',
        f'  // The reference clock: {clock["edges"]} edges, {half} ps half period, first rise at',
        f'  // {clock["first_rise_ps"]} ps.',
        f'  always #{half} clk = ~clk;',
        '',
        '  // The reference stimulus, one line per instant that something changed.',
        '  initial begin',
    ]
    head += [force_block.rstrip('\n')] if force_block else []
    head += stim
    call_args = ', '.join(['cyc, rst_n, enable, I, O, success'] +
                          ([display_args] if display_args else []))
    head += [
        '  end',
        '',
        '  // Sample one time step after every rising edge, for as many cycles as the reference has.',
        '  always @(posedge clk) begin',
        f'    #{SAMPLE_DELAY_PS};',
        f'    $display("C %0d %b %b %b %b %b{probe_fmt}", {call_args});',
        '    cyc = cyc + 1;',
        '    if (cyc >= CYCLES) $finish;',
        '  end',
        'endmodule',
    ]
    text = '\n'.join(head) + '\n'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding='utf-8', newline='\n')
    return text


def compile_and_run(tb: Path, vvp: Path, sources: list[Path] | None = None,
                    plusargs: tuple[str, ...] = ()) -> tuple[str, int, int]:
    """Compile a harness against a file set, run it, and hand back its raw stdout.

    One path for both stages: C1's harness and C2's drive different designs and print different
    lines, but the compile, the plusargs and the failure modes are the same, and a second
    implementation of "compile and run iverilog" would be a second thing to get wrong.
    """
    for tool in ('iverilog', 'vvp'):
        if shutil.which(tool) is None:
            raise RuntimeError(f'{tool} is not on PATH; the simulation stages need Icarus '
                               f'Verilog 12')
    files = [tb, *(sources if sources is not None else [EMITTED, CELLS])]
    vvp.parent.mkdir(parents=True, exist_ok=True)
    cmd = ['iverilog', '-g2012', '-o', vvp.as_posix(), *[f.as_posix() for f in files]]
    comp = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if comp.returncode != 0:
        raise RuntimeError(f'iverilog failed ({comp.returncode}):\n{comp.stdout}{comp.stderr}')
    run = subprocess.run(['vvp', vvp.as_posix(), *plusargs],
                         capture_output=True, text=True, cwd=ROOT)
    return run.stdout, comp.returncode, run.returncode


def run_simulation(tb: Path, vvp: Path, force: int | None = None,
                   sources: list[Path] | None = None) -> tuple[list[dict], int, int]:
    """C1's harness: returns (per-cycle readings, compile rc, sim rc)."""
    stdout, comp_rc, sim_rc = compile_and_run(
        tb, vvp, sources, (f'+force={force}',) if force is not None else ())
    rows = []
    for line in stdout.splitlines():
        f = line.split()
        if f and f[0] == 'C':
            rows.append({'cycle': int(f[1]), 'rst_n': f[2], 'enable': f[3], 'I': f[4],
                         'O': f[5], 'success': f[6], 'probes': f[7:]})
    return rows, comp_rc, sim_rc


# ---------------------------------------------------------------------------------
# the stage
# ---------------------------------------------------------------------------------
def stage_vcd_replay() -> int:
    ref_data = read_reference()
    ref, clock, width = ref_data['ref'], ref_data['clock'], ref_data['width']
    table, cycles = ref_data['table'], ref_data['cycles']
    n = len(cycles)
    print(f'reference                         : {ref["path"]}')
    print(f'  sha256                          : {ref["sha256"][:16]}...')
    print(f'  timescale                       : {ref["timescale_ps"]} ps, '
          f'scopes {ref["scopes"]}')
    print(f'  clock                           : {clock["edges"]} edges, '
          f'{clock["half_period_ps"]} ps half period, first rise {clock["first_rise_ps"]} ps, '
          f'uniform {clock["uniform"]}')
    print(f'  cycles                          : {n} (sample {SAMPLE_DELAY_PS} ps after each rise)')

    runs = enable_runs(table['enable'])
    vectors = [''.join(table['I'][k] for k in range(a, b + 1)) for a, b in runs]
    streams = decode_streams(table['O'])
    print(f'  enable runs                     : {runs} '
          f'(lengths {[b - a + 1 for a, b in runs]})')
    print(f'  output streams                  : '
          + ', '.join(f'{s["cycles"][0]}..{s["cycles"][1]} = {s["text"]!r}' for s in streams))
    print(f'  success high in reference       : '
          f'{sum(1 for v in table["success"] if v == "1")} cycle(s)')

    probes = probe_wires()
    print(f'undriven nets to probe            : '
          + ', '.join(f'{p["wire"]} (cluster {p["cluster"]}, {p["pin_names"]})' for p in probes)
          if probes else 'undriven nets to probe            : none')

    text = emit_testbench(ref_data, probes, OUT_TB)
    print(f'emitted                           : {EMITTED_REL} ({len(text)} bytes, '
          f'{sum(1 for l in text.splitlines() if l.strip().startswith("#"))} timed lines)')

    rows, comp_rc, sim_rc = run_simulation(OUT_TB, VVP)
    print(f'simulation                        : {len(rows)} cycles, iverilog rc={comp_rc}, '
          f'vvp rc={sim_rc}')

    # ---- compare, bit by bit -------------------------------------------------------
    compare_signals = ('rst_n', 'enable', 'I', 'O', 'success')
    out_bits = sum(width[s] for s in ('O', 'success'))
    stim_bits = sum(width[s] for s in ('rst_n', 'enable', 'I'))
    mismatches: list[dict] = []
    out_cmp = out_mis = stim_cmp = stim_mis = 0
    for k, want in enumerate(cycles):
        got = rows[k] if k < len(rows) else None
        for name in compare_signals:
            w = width[name]
            g = (got[name] if got else 'x' * w)
            same = g == want[name]
            if name in ('O', 'success'):
                out_cmp += w
                out_mis += 0 if same else w
            else:
                stim_cmp += w
                stim_mis += 0 if same else w
            if not same and len(mismatches) < 40:
                mismatches.append({'cycle': k, 't': want['t'], 'signal': name,
                                   'ours': g, 'reference': want[name]})
    our_x = sum(1 for r in rows for s in ('O', 'success')
                if 'x' in r[s] or 'z' in r[s])

    # ---- does the undriven net reach an output? ------------------------------------
    probe_report: dict = {}
    for i, p in enumerate(probes):
        vals = sorted({r['probes'][i] for r in rows}) if rows and rows[0]['probes'] else []
        probe_report[p['wire']] = {
            'cluster': p['cluster'], 'pin_names': p['pin_names'],
            'values_seen': vals,
            'undefined_on_all_cycles': vals in (['x'], ['z'])}
        print(f'undriven probe read               : {p["wire"]} = {vals} on all '
              f'{len(rows)} cycles' if vals in (['x'], ['z']) else
              f'undriven probe read               : {p["wire"]} took values {vals}')

    forced: dict[str, dict] = {}
    for fv in (0, 1):
        frows, _, _ = run_simulation(OUT_TB, VVP, force=fv)
        same = (len(frows) == n and all(
            frows[k]['O'] == cycles[k]['O'] and frows[k]['success'] == cycles[k]['success']
            for k in range(n)))
        moved = sorted({k for k in range(min(n, len(frows)))
                        if frows[k]['O'] != rows[k]['O'] or frows[k]['success'] != rows[k]['success']})
        forced[str(fv)] = {'matches_reference': same, 'cycles_moved_vs_unforced': moved[:20],
                           'moved_count': len(moved)}
        print(f'  forced {p["wire"] if probes else "n/a"}={fv}              : '
              f'{len(frows)} cycles, matches reference {same}, '
              f'{len(moved)} cycle(s) differ from the unforced run')

    # ---- cross-check the Step-1 dossier --------------------------------------------
    cross = {'compared': 0, 'mismatches': 0, 'example': None, 'rows': 0}
    if STEP1_CSV.exists():
        import csv as _csv
        for row in _csv.DictReader(STEP1_CSV.open(encoding='utf-8')):
            k = int(row['cycle'])
            cross['rows'] += 1
            if k >= n:
                cross['mismatches'] += 1
                continue
            fields = [('t', int(row['t']), cycles[k]['t'])]
            for name in ('rst_n', 'enable', 'I', 'O', 'success'):
                cell = table[name][k]
                fields.append((name, int(row[name], 2),
                               int(cell, 2) if 'x' not in cell else None))
            for name, want, got in fields:
                cross['compared'] += 1
                if got is None or got != want:
                    cross['mismatches'] += 1
                    if cross['example'] is None:
                        cross['example'] = {'cycle': k, 'field': name,
                                            'dossier': want, 'vcd': got}
    print(f'cross-check vs Step-1 dossier     : {cross["compared"]} fields, '
          f'{cross["mismatches"]} mismatch(es)')

    # ---- what the stage asserts ----------------------------------------------------
    checks = [
        {'check': 'the reference declares clk/rst_n/enable/I/O[7:0]/success',
         'passed': all(s['base'] in width for s in ref['signals'])
                   and {s['base']: s['width'] for s in ref['signals']} == EXPECTED_SIGNALS},
        {'check': 'the clock is uniform (one edge spacing, first rise recorded)',
         'passed': bool(clock['uniform']) and clock['first_rise_ps'] == clock['half_period_ps']},
        {'check': 'every stimulus change is on a falling edge',
         'passed': all(t % (2 * clock['half_period_ps']) == 0
                       for name in ('rst_n', 'enable', 'I') for t, _ in ref['changes'][name][1:])},
        {'check': 'the waveform yields exactly two 121-cycle enable runs',
         'passed': [b - a + 1 for a, b in runs] == [121, 121]},
        {'check': 'the two feeds are different vectors',
         'passed': len(vectors) == 2 and vectors[0] != vectors[1]},
        {'check': 'the reference streams TRY AGAIN twice, at 125..133 and 281..289',
         'passed': [s['cycles'] for s in streams] == [[125, 133], [281, 289]]
                   and [s['text'] for s in streams] == ['TRY AGAIN', 'TRY AGAIN']},
        {'check': 'the reference holds success low on every cycle',
         'passed': all(v == '0' for v in table['success'])},
        {'check': 'the Step-1 dossier agrees with a fresh parse of the raw VCD',
         'passed': cross['mismatches'] == 0 and cross['compared'] == n * 6
                   and cross['rows'] == n},
        {'check': 'the simulation produced one reading per reference cycle',
         'passed': len(rows) == n},
        {'check': 'our replay matches the reference on every output bit',
         'passed': out_mis == 0 and out_cmp == n * out_bits},
        {'check': 'the harness replayed the reference stimulus, bit for bit',
         'passed': stim_mis == 0 and stim_cmp == n * stim_bits},
        {'check': 'our outputs contain no x or z',
         'passed': our_x == 0},
        {'check': 'the undriven net reads an undefined value on every cycle',
         'passed': all(v['undefined_on_all_cycles'] for v in probe_report.values())},
        {'check': 'forcing the undriven net to 0 and to 1 leaves every output unchanged',
         'passed': all(f['matches_reference'] and f['moved_count'] == 0 for f in forced.values())},
    ]

    report = {
        'generated_by': 'tools/puzzle/simulate.py::stage_vcd_replay',
        'reference': {'path': ref['path'], 'sha256': ref['sha256'],
                      'timescale_ps': ref['timescale_ps'], 'scopes': ref['scopes'],
                      'signals': ref['signals'], 'cycles': n,
                      'clock': {'edges': clock['edges'],
                                'half_period_ps': clock['half_period_ps'],
                                'first_rise_ps': clock['first_rise_ps'],
                                'uniform': clock['uniform'],
                                'distinct_deltas': clock['distinct_deltas']}},
        'method': {
            'sampling': f'{SAMPLE_DELAY_PS} ps after each rising edge: the register state the '
                        f'reference dumps there, with the stimulus held across the cycle',
            'sample_delay_ps': SAMPLE_DELAY_PS,
            'harness': 'iverilog -g2012 + vvp; the testbench is generated from the reference '
                       'transition list, so it cannot drift from the waveform',
            'testbench': EMITTED_REL,
            'byte_exact_means': 'every value of every signal at every sampled instant, x '
                                'included -- not a byte diff of two VCD files, which no two '
                                'simulators produce (see the module docstring)',
            'force_runs': ['none', '0', '1'],
        },
        'stimulus': {'transitions': [[t, nm, _normalise(v, 1)[0]]
                                     for nm in ('rst_n', 'enable', 'I')
                                     for t, v in ref['changes'][nm][1:]],
                     'enable_runs': runs,
                     'feed_vectors': {f'attempt_{i + 1}_cycles_{a}_{b}': vectors[i]
                                      for i, (a, b) in enumerate(runs)},
                     'vectors_differ': len(vectors) == 2 and vectors[0] != vectors[1]},
        'decode': {'streams': streams, 'idle_byte': '0x00',
                   'note': 'the README says this waveform does not contain the winning input, '
                           'so two failed attempts are the expected content: it is the '
                           'wrong-input path, and TRY AGAIN is what the published messages '
                           'predict for it'},
        'reference_values': {name: table[name] for name in compare_signals},
        'simulated_values': {'O': [r['O'] for r in rows], 'success': [r['success'] for r in rows],
                             'probes': {p['wire']: [r['probes'][i] for r in rows]
                                        for i, p in enumerate(probes)}},
        'comparison': {'cycles': n, 'output_bits_compared': out_cmp,
                       'output_bits_mismatched': out_mis,
                       'stimulus_bits_compared': stim_cmp,
                       'stimulus_bits_mismatched': stim_mis,
                       'mismatches': mismatches, 'exact': out_mis == 0 and stim_mis == 0,
                       'our_outputs_with_x_or_z': our_x},
        'undriven_probe': {**probe_report,
                           'note': 'B5 enumerated this net by structure (an input pin with no '
                                   'driver anywhere in the layout). B7 predicted C1 would show '
                                   'it as X; measured it reads z, which is the 4-state value of '
                                   'an undriven wire (x is what an unwritten reg reads). The '
                                   'forced runs show it cannot move an interface output under '
                                   'this stimulus, so nothing in the recovered netlist rests '
                                   'on it -- but "inconsequential pin" is NOT the claim: the '
                                   'masking depends on the state of its consumers other '
                                   'inputs, so E1 re-probes it on the winning vector, where '
                                   'the design finally reads its inputs for real.'},
        'forced_runs': forced,
        'cross_check': {**cross, 'source': STEP1_CSV.relative_to(ROOT).as_posix()},
        'compiler': {'iverilog_rc': comp_rc, 'vvp_rc': sim_rc},
        'checks': checks,
    }
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUT_REPORT.write_text(json.dumps(report, indent=2, sort_keys=False) + '\n',
                          encoding='utf-8', newline='\n')
    print(f'report                            : {OUT_REPORT.relative_to(ROOT).as_posix()}')

    failed = [c['check'] for c in checks if not c['passed']]
    ok = not failed
    print()
    print('output bit comparisons            : '
          f'{out_cmp} ({out_mis} mismatched), stimulus {stim_cmp} ({stim_mis} mismatched)')
    if mismatches:
        print('first mismatches:')
        for m in mismatches[:8]:
            print(f'  cycle {m["cycle"]:>3} (t={m["t"]}) {m["signal"]}: '
                  f'ours {m["ours"]} vs reference {m["reference"]}')
    print()
    print(f'C1 VCD REPLAY: {"PASS" if ok else "FAIL " + str(failed)}')
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    stage = (argv or ['vcd-replay'])[0]
    if stage != 'vcd-replay':
        print(f'simulate.py has no stage {stage!r}', file=sys.stderr)
        return 2
    return stage_vcd_replay()


if __name__ == '__main__':
    raise SystemExit(main())
