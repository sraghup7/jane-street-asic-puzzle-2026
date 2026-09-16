#!/usr/bin/env python3
"""fault_inject.py -- the meta-gate: does each gate actually bite?

Deliberately **not** named `check_*.py`, so `run_all.py` does not pick it up. It mutates
derived artifacts, so it is a diagnostic you run deliberately, not part of the routine
suite:

    .venv/Scripts/python tools/checks/fault_inject.py [--only REGEX] [--in-place]

It mutates the tree it runs in, so by default it refuses to run anywhere but a scratch clone
(`git clone -b <branch> . <dir>`) -- `--in-place` overrides that and accepts the risk. `--only`
restricts the sweep to the mutations whose label matches REGEX, which is how a *new* step's
coverage gets closed without paying for the whole grid. The cost matters: measured, one mutation
against the full (non-`stepF4`) gate list takes about 7.5 minutes, so the full grid of roughly 130
mutations is about **16 hours**. Use `--only` while a phase is in progress and the full grid at a
phase boundary. The baseline check is unaffected -- all gates must still pass before any mutation
is injected.

**Run it alone.** It holds artifacts in a mutated state between two restores, so anything
reading them concurrently can see a fault on purpose. Running `run_all.py` alongside it
will report spurious failures (measured: `stepA1` failing with 2 checks while a
fault-injection run was in flight).

For every (artifact, mutation) pair it restores a pristine snapshot, injects one
well-formed but wrong value, runs ONE gate, and records whether that gate noticed. Two
properties are measured:

**Per-gate sensitivity in isolation.** Running the whole suite in order hides a downstream
gate's sensitivity: gates run in dependency order, and a gate that regenerates its artifact
repairs it before the next gate reads it. Measured that way `stepA4` appeared blind to
corruption of `pin_names.json`; isolated, it fires. This is why the suite's order is not
evidence of what each gate can detect.

**Hermeticity.** Running a gate must not change any artifact's byte content. A gate that
rewrites the artifact it is judging destroys the evidence it just found -- and because of
the ordering above, silently repairs it for the next gate.

Two gates are expected never to fire here, by design: `target` validates the acceptance
contract (code, not artifacts) and `hygiene` scans source. Everything else must fire on at
least one mutation of the artifact it owns. "Caught" means at least one gate fired on a mutation;
the firing gate(s) are recorded, not just a pass/fail bit, so a mutation caught by the wrong gate
is still visible as a finding rather than looking clean.

**What this measures, and what it does not.** Every mutation here is artifact tampering: a
committed JSON or Verilog file is rewritten to a wrong-but-well-formed value, as if some earlier
stage had silently produced bad output. It says nothing about whether the *code* computes the
right thing when a cell model is wrong -- that is a different question, measured by
`tools/checks/model_faults.py`, which mutates the evaluator's own model rather than an artifact.

Exit code 0 iff no mutation escaped every gate and no gate rewrote the tree.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(ROOT))
from tools import utf8_env               # noqa: E402
from tools.checks.run_all import gate_paths  # noqa: E402

# stepF4 is excluded: its subject is the frozen, clean repository, and a mutation dirties the tree by
# design, so it would fire on every mutation and drown the signal. (It also has no artifact of its own
# to mutate; it is run by the suite, which is where the freeze is declared.) Every other gate the suite
# discovers is included automatically, so a new gate file is covered without touching this list.
GATES = [(name, str(path.relative_to(ROOT)).replace('\\', '/'))
         for name, path in gate_paths() if name != 'stepF4']

LAYERS = 'recon/derived/layers.json'
VIA = 'recon/derived/via_pairs.json'
NAMES = 'recon/derived/pin_names.json'
GEOM = 'recon/derived/pinmodel.json'
COV = 'recon/derived/pin_coverage.json'
INST = 'recon/derived/instances.json'
WNET = 'recon/derived/warmup_netlist.json'
NETS = 'recon/derived/nets.json'
PINNET = 'recon/derived/pin_net.json'
CHECK = 'recon/derived/netlist_check.json'
INV = 'recon/inventory.json'
# B6's outputs are the first artifacts that are not JSON, so the harness mutates them as text.
PUZZLEV = 'build/puzzle.v'
CELLSV = 'build/cells.v'
WARMV = 'build/warmup.v'
WARMREP = 'recon/derived/warmup_b7.json'
# C1 added a generated harness (text) and two reports (JSON): the replay itself, and the
# measurement of what the replay can see.
REPLAYV = 'build/replay_tb.v'
REPLAY = 'recon/derived/vcd_replay.json'
POWER = 'recon/derived/c1_power.json'
# C2's: the warm-up harness, the equivalence report, and its own power measurement.
EQTB = 'build/warmup_equiv_tb.v'
EQREP = 'recon/derived/warmup_equiv.json'
WUPOW = 'recon/derived/c2_power.json'
# C3's: the two decomposition harnesses (reference and winning stimulus) and the block assignment.
DECREF = 'build/decompose_ref_tb.v'
DECWIN = 'build/decompose_win_tb.v'
BLOCKS = 'recon/derived/blocks.json'
# C4/C5/E1's (added 2026-09-13): the candidate partition with its controls, the rejected boards, and
# the winning-vector tables. No generated Verilog of their own -- verdict.py evaluates the netlist
# directly, so there is no harness to mutate.
C4ART = 'recon/derived/c4_partition.json'
C5ART = 'recon/derived/c5_rejections.json'
E1ART = 'recon/derived/e1_messages.json'
# D's: the derived board, both bit orders, the two enumerators' counts and the validation.
SOLART = 'recon/derived/solutions.json'
# E2's: the boards built from C4's partition and the chip's own readings of them.
E2ART = 'recon/derived/e2_messages.json'
# F6's: the chip's one undriven net -- recovered from the geometry, tested against three hypotheses,
# and now a stage of its own (it was a document with its scripts in the ignored scratch tree, which is
# the one place in this repository where a claim had no gate behind it).
NET806ART = 'recon/derived/net806.json'
# E3's: the acceptance matrix over every criterion.
ACCEPTART = 'recon/derived/acceptance.json'
# E4's: the plan, the per-stage exit codes and the cold byte comparison.
REPROART = 'recon/derived/reproduction.json'
# S0.1's second artifact: the Step-1 decoded cycle table. Committed, read by the C1 stage as a
# cross-check oracle, and -- until F5 -- checked by no gate (registered here so its mutation has an
# owner: check_stepC1).
VCDREF = 'recon/vcd_cycles.csv'
# F's subjects: source files and documents rather than artifacts. They are mutated as *text* (see the
# harness's text branch) and their mutators keep them valid Python -- a syntax error would fire every
# gate that imports the module, which is not the fault under test.
SRC_CELLS = 'tools/puzzle/cells.py'
SRC_C5GATE = 'tools/checks/check_stepC5.py'
DOC_README = 'README.md'
DOC_DEPS = 'docs/deps.md'
SRC_POWER = 'tools/puzzle/power.py'
SRC_SOLVE = 'tools/puzzle/solve.py'
ARTIFACTS = [LAYERS, VIA, NAMES, GEOM, COV, INST, WNET, NETS, PINNET, CHECK, INV,
             PUZZLEV, CELLSV, WARMV, WARMREP, REPLAYV, REPLAY, POWER, EQTB, EQREP, WUPOW,
             DECREF, DECWIN, BLOCKS, C4ART, C5ART, E1ART, SOLART, E2ART, NET806ART, ACCEPTART, REPROART,
             # F's steps guard the source tree and the documents rather than a derived artifact, so
             # their subjects are registered here too: the harness snapshots and restores them, and
             # the drift check then proves no gate rewrote one while it was mutated.
             SRC_CELLS, SRC_C5GATE, DOC_README, DOC_DEPS, SRC_POWER, SRC_SOLVE,
             VCDREF]

# Supply/body pins, as connect.py defines them. Duplicated here so this diagnostic tool needs
# no pipeline import -- it must stay runnable even when the pipeline is mid-edit.
SUPPLY = {'VPWR', 'VGND', 'VPB', 'VNB'}

NAND2 = 'sky130_fd_sc_hd__nand2_2'


# --- mutations: each changes a real value and leaves the JSON well formed ------------
def m_layers_role_table(d):
    d['roles']['non_electrical'].append([67, 20])
    d['roles']['local_wire'].remove([67, 20])


def m_layers_pair_role(d):
    for r in d['pairs']:
        if (r['layer'], r['datatype']) == (67, 20):
            r['role'] = 'non_electrical'


def m_layers_pitch(d):
    d['derived_site_pitch_um'] = 0.55


def m_layers_pin_dt(d):
    d['derived_pin_datatype']['datatype'] = 20


def m_layers_histogram(d):
    d['summary']['by_role']['local_wire'] = 2


def m_layers_review(d):
    d['summary']['needs_review'] = []


def m_via_rule(d):
    d['pairs'][0]['connects'] = ['67/20', '69/20']


def m_via_total(d):
    d['totals']['via_instances'] += 1


def m_names_drop_pin(d):
    d['masters'][NAND2]['pins'].pop('A')


def m_names_total(d):
    d['totals']['pin_labels'] += 1


def m_names_per_layer(d):
    d['label_counts_by_layer']['67/5'] += 1


def m_geom_drop_rect(d):
    d['masters'][NAND2]['pins']['A']['shapes'].pop()


def m_geom_bbox(d):
    d['masters'][NAND2]['bbox_um'][0] = -0.29


def m_geom_rule_total(d):
    d['local_rule_table'][0]['instances'] += 1


def m_geom_pin_drop(d):
    d['masters'][NAND2]['pins'].pop('B')


def m_cov_routed(d):
    d['pin_coverage']['with_routing_geometry'] = 490


def m_cov_pct(d):
    d['instance_reach']['coverage_pct'] = 90.0


def m_inv_instances(d):
    d['gds']['standard_cell_instances'] += 1


def m_inv_structures(d):
    d['gds']['structure_count'] = 82


def m_inv_defsite(d):
    d['warmup']['def_site_width_um'] = 0.47


def m_inv_sha(d):
    d['gds']['sha256'] = '0' * 64


def m_inst_convention(d):
    d['convention']['chosen_by_kind']['rot180_mirror'] = [-1, -1]


def m_inst_origin(d):
    d['instances'][0]['origin_dbu'][0] += 460


def m_inst_bbox(d):
    d['instances'][5]['bbox_dbu'][2] += 1000


def m_inst_delete(d):
    d['instances'].pop()
    d['totals']['instances'] -= 1


def m_inst_footprint(d):
    d['instances'][3]['footprint_dbu'][1] -= 2720


def m_inst_def_oracle(d):
    d['def_oracle']['matched'] = 229


def m_net_equivalent(d):
    d['comparison']['equivalent'] = False


def m_net_probed(d):
    d['totals']['pins_probed'] += 1


def m_net_unconnected(d):
    d['totals']['pins_unconnected_by_engine'] = 1


def m_net_move_terminal(d):
    """Move one terminal into a different net's terminal set."""
    first = min(d['our_partition'], key=lambda k: len(d['our_partition'][k]))
    t = d['our_partition'][first].pop()
    if not d['our_partition'][first]:
        del d['our_partition'][first]
    other = next(k for k in d['our_partition'] if k != first)
    d['our_partition'][other].append(t)


def m_net_drop_net(d):
    biggest = max(d['our_partition'], key=lambda k: len(d['our_partition'][k]))
    del d['our_partition'][biggest]


def m_net_engine(d):
    d['engine']['conductors'] = d['engine']['conductors'][:-1]


def m_nets_orphan(d):
    d['totals']['orphan_shapes'] = 1


def m_nets_unexplained(d):
    d['totals']['supply_unassigned_unexplained'] = 5


def m_nets_layer(d):
    d['per_layer_shapes']['67/20']['assigned'] -= 1


def m_nets_shapes(d):
    d['totals']['conductor_shapes'] -= 1


def m_nets_supply_claim(d):
    d['supply_identification']['largest_two_are_supply'] = False


def m_nets_ids_collide(d):
    # The property that forced the flattening: net identity must be unique. After the
    # cluster_id collision was found, a gate that does not notice this is not guarding it.
    d['totals']['distinct_cluster_ids'] = d['totals']['nets'] - 1


def m_nets_unique_claim(d):
    d['totals']['net_identity_unique'] = False


def _first_assigned(d, functional=None):
    """(instance, pin, value) of the first assigned pin, deterministically."""
    for iid, rec in sorted(d['instances'].items()):
        for pin, val in sorted(rec['pins'].items()):
            if val is None:
                continue
            if functional is None or functional == (pin not in SUPPLY):
                return iid, pin, val
    return None, None, None


def m_pn_unassign_functional(d):
    d['totals']['unassigned_functional'] = 1


def m_pn_move_terminal(d):
    _, _, val = _first_assigned(d, functional=True)
    if val is not None:
        val[0] = 3 if val[0] != 3 else 27


def m_pn_falsify_evidence(d):
    # Leave the claimed net alone but move the recorded probe point: only the independent
    # re-probe can catch this, which is exactly the check it exists for.
    _, _, val = _first_assigned(d)
    if val is not None:
        val[2] += 1000


def m_pn_drop_instance(d):
    d['instances'].pop(sorted(d['instances'])[0])


def m_pn_gap_unexplained(d):
    d['gap_classes']['unexplained'] = 1
    d['totals']['unassigned_supply_unexplained'] = 1


def m_pn_uniqueness_lie(d):
    d['totals']['engine_distinct_cluster_ids'] = d['totals']['engine_nets'] - 1


def m_chk_port_net(d):
    # Reassign a port to another net: the gate re-derives the port->net binding from the GDS
    # and recomputes each port net's driver count, so this must not survive.
    d['ports'][0]['net'] = 3


def m_chk_port_dir(d):
    d['ports'][0]['dir'] = 'out' if d['ports'][0]['dir'] == 'in' else 'in'


def m_chk_undriven_hidden(d):
    """Hide the one net with no driver.

    Note the history: this mutation used to be `..._hide_undetermined`, setting
    `classes_undetermined = 0` and `undetermined = []`. That was a lie when the structural
    solver ran the show; now it is simply the truth, so the mutation would never fire. These
    three replace it, and they attack the claims the artifact actually makes today.
    """
    d['direction']['undriven_nets'] = []
    d['totals']['undriven_nets'] = 0


def m_chk_direction_flipped(d):
    """Move one input class into the output table -- the gate re-derives from A4, so it must."""
    victim = 'sky130_fd_sc_hd__nand2_2::A'
    assert victim in d['direction']['inputs'], victim
    d['direction']['inputs'].remove(victim)
    d['direction']['outputs'] = sorted([*d['direction']['outputs'], victim])
    d['totals']['output_classes'] += 1
    d['totals']['input_classes'] -= 1


def m_chk_crosscheck_hidden(d):
    """Hide the auditor's one contradiction with the labels, i.e. the fabricated driver."""
    d['direction']['cross_check']['structure_contradicts_the_labels'] = []


def m_v_repoint(text):
    """Re-point one instance pin at a neighbouring net: the round trip must notice."""
    assert '.A1(n806)' in text, 'the target connection moved'
    return text.replace('.A1(n806)', '.A1(n807)', 1)


def m_v_drop_instance(text):
    """Delete one instantiation line: the inventory and round trip must notice."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith('  sky130_fd_sc_hd__a31oi_2 i0523 ('):
            del lines[i]
            return ''.join(lines)
    raise AssertionError('the target instance moved')


def m_v_cells_flip_direction(text):
    """Declare one model's output as an input: the interface check must notice."""
    marker = 'module sky130_fd_sc_hd__dfrtp_2 ('
    head, tail = text.split(marker, 1)
    assert '  output Q;\n' in tail, 'the flop model moved'
    return head + marker + tail.replace('  output Q;\n', '  input Q;\n', 1)


def m_v_warmup_repoint(text):
    """Re-point a connection in the warm-up netlist: regeneration must notice.

    Swaps the first two wire names wherever they appear as *connections*, leaving the `wire`
    declarations alone -- so the text stays valid Verilog and the only thing that changed is the
    connectivity. Written against our own `n<cluster>` naming, not the reference's net names,
    because the mutation has to apply to what we emit.
    """
    wires = sorted(set(re.findall(r'  wire (n\d+);', text)), key=lambda w: int(w[1:]))
    assert len(wires) >= 2, 'not enough wires to swap'
    a, b = wires[0], wires[1]
    return (text.replace(f'({a})', '(@@SWAP@@)')
                .replace(f'({b})', f'({a})')
                .replace('(@@SWAP@@)', f'({b})'))


def m_b7_claim_equivalent(d):
    """Claim equivalence while dropping most of the coverage: the floor check must notice."""
    d['comparison']['equivalent'] = True
    d['comparison']['coverage'] = 0.5


def m_b7_claim_vpb(d):
    """Claim we assigned the well ties, which A5 says have no routeable geometry."""
    d['supply']['ours']['VPB'] = d['supply']['reference'].get('VPB', 0)


def m_b7_drop_port(d):
    """Drop a located port: the interface check must notice."""
    d['interface']['located'].pop(sorted(d['interface']['located'])[0], None)


def m_chk_extra_single(d):
    d['single_terminal_nets'].append({'cluster': 999999,
                                      'master': 'invented_1', 'pin': 'X',
                                      'direction': 'output',
                                      'classification': 'unused_output'})


def m_chk_infeasible(d):
    d['totals']['infeasible_nets'] = 7


# --- C1: the generated harness, and the two C1 reports --------------------------------
def m_v_replay_shift(text):
    """Move the stimulus one clock period: the outputs must land somewhere else."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*)#(\d+)(.*enable = 1'b0;.*)$", ln)
        if m:
            lines[i] = f'{m.group(1)}#{int(m.group(2)) + 10000}{m.group(3)}'
            return '\n'.join(lines) + '\n'
    raise AssertionError('the harness has no enable falling edge to move')


def m_v_replay_short(text):
    """Run one cycle short: a missing reading must not pass as a match."""
    return text.replace('localparam integer CYCLES = 312;', 'localparam integer CYCLES = 311;')


def m_v_replay_no_delay(text):
    """Sample in the edge's own time step, before the flops update: the convention is pinned."""
    return text.replace('localparam integer SAMPLE_DELAY_PS = 1;',
                        'localparam integer SAMPLE_DELAY_PS = 0;')


def m_rep_reference_value(d):
    """Change one byte of the reference table: it must be checked against the file, not trusted."""
    d['reference_values']['O'][125] = '00000000'


def m_rep_stream_text(d):
    """Change the decoded message: the ASCII reading is asserted, not assumed."""
    d['decode']['streams'][0]['text'] = 'TRY AGAINX'


def m_rep_probe_z(d):
    """Claim the undriven net read 0: the probe reading is evidence, so it is checked."""
    d['undriven_probe']['n806']['values_seen'] = ['0']


def m_rep_forced_run(d):
    """Claim a forced run moved outputs, when the same artifact says it moved none."""
    d['forced_runs']['0']['moved_count'] = 3


def m_pw_totals(d):
    """Fake a total: the per-model records must add up to it."""
    d['totals']['caught'] = 40


def m_pw_f1_silent(d):
    """Mark the F1 break invisible: the gate re-measures it and must disagree."""
    rec = next(r for r in d['per_model'] if r['model'] == 'sky130_fd_sc_hd__or2_2')
    rec['caught'], rec['cycles_wrong'] = False, 0


def m_pw_consumer_visible(d):
    """Call a consumer of the undriven net visible: it corroborates the forced-run result."""
    rec = next(r for r in d['per_model'] if r['model'] == 'sky130_fd_sc_hd__a311o_2')
    rec['caught'], rec['cycles_wrong'] = True, 18


# --- C2: the warm-up harness and the two reports --------------------------------------
def m_eq_tb_short(text):
    """Sweep less than the whole space: an incomplete sweep must not pass as exhaustive."""
    return text.replace('localparam integer LIMIT = 256;', 'localparam integer LIMIT = 255;')


def m_eq_tb_off_by_one(text):
    """Shift one bit fewer per operand: every pair then carries the wrong value."""
    return text.replace('for (bit_i = WIDTH - 1;', 'for (bit_i = WIDTH - 2;')


def m_dec_tb_delay(text):
    """Sample at the edge instead of one time step after it: the reading becomes a race."""
    return text.replace('    #1;\n', '', 1)


def m_dec_tb_drop_flop(text):
    """Print one flop fewer than the design has: a classification over a subset must not pass."""
    return text.replace('    $display("F %0d i1609 %b", cyc, dut.i1609.Q);\n', '', 1)


def m_blocks_delay(d):
    """One shift stage claims the wrong depth: the re-derivation must disagree with the claim."""
    delays = d['blocks']['input_shift_register']['delays']
    victim = min(delays, key=lambda k: delays[k])
    delays[victim] = delays[victim] + 1


def m_blocks_drop_flop(d):
    """Drop a flop from a block's list, leaving the two records of the block inconsistent."""
    d['blocks']['input_shift_register']['flops'].pop()


def m_blocks_peak(d):
    """Inflate the ones counter's reported peak: the artifact must not outrun the simulation."""
    d['blocks']['ones_counter']['value_peak_winning'] = 23


def m_blocks_ones_bit(d):
    """Drop a bit from the ones counter chain: the chain stops being a counter."""
    d['blocks']['ones_counter']['flops'].pop()


def m_eq_ref_width(d):
    """Misstate the protocol width: the gate re-derives it from the source."""
    d['reference']['width'] = 7


def m_eq_sweep_claim(d):
    """Claim one pair fewer was visited."""
    d['sweep']['checked'] = 65535


def m_eq_mismatch(d):
    """Claim mismatches the sweep does not have (and vice versa)."""
    d['comparison']['ours_vs_reference_mismatches'] = 3


def m_eq_rename(d):
    """Name the wrong top module: the rename is re-derived from the source."""
    d['reference']['top'] = 'shift_register'


def m_eq_pw_totals(d):
    """Fake the caught count: the per-model records must add up to it."""
    d['totals']['caught'] = 12


def m_eq_pw_sample(d):
    """Mark a caught model silent: the gate re-measures that exact model."""
    rec = next(r for r in d['per_model'] if r['model'] == 'sky130_fd_sc_hd__and2_2')
    rec['caught'], rec['pairs_wrong'] = False, 0


# --- C4/C5/E1: the candidate partition with its controls, the rejected boards, the winning tables ---
def _ragged(d):
    """The candidate that is not the visible column rule -- what C4's gate asserts on."""
    return next(c for c in d['candidates'] if not c['is_the_visible_column_rule'])


def m_c4_drop_cell(d):
    """Drop one cell from a class: the cover stops covering the grid."""
    cand = _ragged(d)
    cand['classes'][cand['flops'][0]].pop()
    cand['class_sizes'] = sorted(len(cand['classes'][f]) for f in cand['flops'])


def m_c4_solution_count(d):
    """Claim the partition admits more than one solution: the gate recounts it exhaustively."""
    _ragged(d)['unique_solution']['solutions'] = 2


def m_c4_trigger_total(d):
    """Drop a trigger set but leave the total claiming it: the consistency check must notice."""
    del d['trigger_sets'][sorted(d['trigger_sets'])[0]]


def m_c5_accept_one(d):
    """Claim one of the visible-valid boards was accepted."""
    d['boards'][0]['message'] = '(* TWO STARS *)'
    d['boards'][0]['success_any'] = True
    d['accepted'] = 1
    d['rejected'] = len(d['boards']) - 1


def m_c5_drop_board(d):
    """Drop a board from the set the design rejected."""
    d['boards'].pop()
    d['rejected'] = len(d['boards'])


def m_c5_replay_lie(d):
    """Fault the instrument's own validation, recorded inside this artifact."""
    d['reference_replay']['mismatches'] = 3


def m_c5_power(d):
    """Call the rejection test powerful, when the control says it is nearly blind.

    This is the mutation that matters most in this group: the whole point of R20 is that a passing
    rejection test is not evidence, so a claim to the contrary must not survive. (Found stale during
    the Task 11 fix-pass verification: this mutation used to target `rejection_test` on the C4
    artifact's ragged candidate, but review R1/R5/R8 moved control A -- and this field -- to C5's
    artifact, `c5_rejections.json`, and the mutator was never updated to follow. It crashed with
    `KeyError: 'rejection_test'` the first time it ran against the current schema.)
    """
    rt = d['rejection_test']
    rt['lookalikes_that_also_reject_every_board'] = 40
    rt['discriminating_power'] = 0.8


def m_e1_cycle(d):
    """Move the rising cycle: the acceptance cycle is the step's whole point."""
    d['offsets'][0]['success_cycle_1based'] = 125
    d['offsets'][0]['success_cycle_0based'] = 124


def m_e1_message(d):
    """Change a decoded message."""
    d['messages'][0]['got'] = 'EMPTY SKYX'


def m_e1_open_hidden(d):
    """Delete the open item: the honest statement of what is NOT reproduced must not be removable."""
    d['open'] = 'every message class reproduced'


# --- D: the derived board, both bit orders, the enumerators, the validation -------------
def m_d_uniqueness_faked(d):
    """Claim a second solution: the gate re-runs both searches and counts."""
    d['unique'] = False
    d['solutions_found'] = 2


def m_d_vector_flipped(d):
    """Flip one bit of the derived 121-bit vector: AC1 is exactly this string."""
    bits = d['solution']['feed_order']
    d['solution']['feed_order'] = ('0' if bits[0] == '1' else '1') + bits[1:]


def m_d_grid_moved(d):
    """Move a star in the recorded board, leaving the JSON well formed."""
    row = d['solution']['grid'][0]
    moved = ('*' if row[0] == '.' else '.') + row[1:]
    d['solution']['grid'][0] = moved
    d['validation']['mechanical']['row_counts'] = [row.count('*') for row in d['solution']['grid']]


def m_d_enumerator_disagree(d):
    """Make the two enumerators disagree, which is the one thing D2 exists to detect."""
    d['enumerators']['bitmask_stack']['count'] = 2
    d['enumerators']['agree_on_count'] = False


def m_d_region_load(d):
    """Overload a class on the recorded board: five constraints, not four."""
    d['validation']['region_loads'][0] = 3
    d['validation']['region_capacity_ok'] = False


# --- E2: the boards built from the partition, and the chip's readings --------------------
def m_e2_claim_all_spell(d):
    """Claim only a few boards spell the message: this is the finding of the step."""
    d['boards_spelling_the_message'] = 3


def m_e2_control_silenced(d):
    """Say the control group also spelled TWO NOT TOUCH, which would void the contrast."""
    d['control_group']['boards_not_spelling_it'] = 0


def m_e2_undriven_smoothed(d):
    """Delete the awkward part: that neither tie of the undriven net gives the published string."""
    f = d['undriven_net_in_the_message']
    f['positions'] = []
    f['tie_0'] = 'TWO NOT TOUCH'


def m_e2_board_moved(d):
    """Move a star in the first tested board: the gate re-derives that board from the family."""
    g = d['results'][0]['grid']
    g[0] = ('*' if g[0][0] == '.' else '.') + g[0][1:]


def m_e2_class_wrong(d):
    """Corrupt one of the other message classes."""
    d['message_classes']['all_zeros']['text'] = 'EMPTY SKYX'


def m_e2_lookalike_power_hidden(d):
    """Claim the null model gets much closer than it does: hides how strong the test really is."""
    d['lookalike_power']['best_match'] = d['lookalike_power']['boards']
    d['lookalike_power']['reproduce_all_boards'] = d['lookalike_power']['tested']


def m_e2_boundary_resolution_hidden(d):
    """Claim every boundary move is detected: the resolution limit (R6 medium 2) must stay visible."""
    d['boundary_resolution']['undetected'] = 0


# --- E3: the acceptance matrix, and the overclaims it must refuse ------------------------
def _row(d, cid):
    return next(r for r in d['criteria'] if r['id'] == cid)


def m_e3_ac6_downgraded(d):
    """Downgrade AC6 from PASS to PARTIAL while its evidence still says PASS: the matrix's status must
    track what `ac6_ok()` computes, not a worded-down opinion of it."""
    r = _row(d, 'AC6')
    r['status'] = 'PARTIAL'
    d['partial'] = ['AC6']
    d['counts'] = {'PASS': 5, 'PARTIAL': 1, 'FAIL': 0}


def m_e3_ac1_friendly(d):
    """Record AC1's bit-order comparison as false while the matrix still says PASS."""
    _row(d, 'AC1')['evidence']['as_printed_equals_target'] = False


def m_e3_ac5_vacuous(d):
    """Zero the replay's compared bits, so a vacuous comparison would pass a floor."""
    _row(d, 'AC5')['evidence']['output_bits_compared'] = 0


def m_e3_counts_faked(d):
    """Claim a different PASS/PARTIAL split than the rows actually show, whatever that split is."""
    c = d['counts']
    d['counts'] = {'PASS': c['PASS'] - 1, 'PARTIAL': c['PARTIAL'] + 1, 'FAIL': c['FAIL']}


def m_e3_unmet_removed(d):
    """Delete AC6's method-disclosure note and the report's list of what is not claimed."""
    r = _row(d, 'AC6')
    r['notes'] = [n for n in r['notes'] if 'single-star' not in n]
    d['claims_not_made'] = []


# --- E4: the reproduction path, and the claims it must not make --------------------------
def m_e4_drop_stage(d):
    """Drop a stage from the recorded plan: the pipeline would no longer match the command table."""
    d['stages'] = [r for r in d['stages'] if r['stage'] != 'warmup-regression']
    d['stages_run'] = len(d['stages'])


def m_e4_fake_exit(d):
    """One stage recorded as failing while the report still says everything is clean."""
    d['stages'][0]['exit_code'] = 1


def m_e4_clock(d):
    """Put a timing in the artifact -- B3's rule forbids it, because it breaks byte-identity."""
    d['stages'][0]['seconds'] = 5.3


def m_e4_reorder(d):
    """Move the acceptance stage off the end."""
    rows = list(d['stages'])
    acc = next(r for r in rows if r['stage'] == 'acceptance')
    rows.remove(acc)
    rows.insert(0, acc)
    d['stages'] = rows


def m_e4_cold_smoothed(d):
    """Report a cold run that agreed, when a file differed."""
    c = d['cold_check']
    c['differences'] = [{'path': 'recon/derived/c4_partition.json', 'why': 'differs'}]
    c['regenerated_identically'] = c['tracked_before'] - 1


def m_e4_warm_run(d):
    """Present a warm run as the evidence, when E4's claim needs the cold one."""
    d['cold_check'] = None


# --- F1: dead code -----------------------------------------------------------------------
def m_f1_dead_function(text):
    """Append a helper nothing calls, in the pipeline package: F1's exact subject."""
    return text + ('\n\ndef f1_unreferenced_helper():\n'
                   '    """A helper nothing calls, added by fault injection."""\n'
                   '    return 1\n')


def m_f1_dead_function_in_checks(text):
    """The same, in a gate: dead code is not a privilege of the pipeline."""
    return text + ('\n\ndef f1_unreferenced_gate_helper():\n'
                   '    """A helper nothing calls, added by fault injection."""\n'
                   '    return 1\n')


# --- F2: documentation truth -------------------------------------------------------------
def m_f2_gate_count(text):
    """State one gate fewer than the suite discovers."""
    import re
    m = re.search(r'all (\d+) gates', text)
    return text[:m.start(1)] + str(int(m.group(1)) - 1) + text[m.end(1):]


def m_f2_unknown_stage(text):
    """Name a pipeline command that does not exist."""
    return text.replace('-m tools.puzzle reproduce --cold', '-m tools.puzzle reproducee --cold', 1)


def m_f2_deps_row(text):
    """Delete a package row from the dependency document."""
    import re
    return re.sub(r'^\| `matplotlib`.*\n', '', text, count=1, flags=re.M)


# --- F3: the conventions -----------------------------------------------------------------
def m_f3_parked_marker(text):
    """Park a thought in a comment, which is the thing F3 forbids."""
    return text + '\n# ' + 'TO' + 'DO' + ': come back to this\n'


def m_f3_commented_code(text):
    """Comment a line of code out instead of deleting it."""
    return text + '\n# total = 42\n'


def m_f3_docstring_removed(text):
    """Strip a long function's docstring, leaving valid Python behind."""
    import re
    return re.sub(r'(def stage_solve\(\) -> int:\n)    """.*?"""\n', r'\1    pass\n',
                  text, count=1, flags=re.S)


# --- F5: the three defects the post-review audit found -------------------------------
# Two are the audit's own subjects: a provenance stamp that named files it did not hash, and two
# artifacts that stated the answer without naming their inputs. The third is the Step-1 cycle table,
# which no gate checked at all.
def m_f5_cells_stamp(text):
    """Name an artifact in cells.v's provenance stamp that does not exist."""
    return text.replace('recon/derived/pin_names.json', 'recon/derived/pin_namess.json', 1)


def m_f5_acceptance_source(d):
    """Keep the matrix's verdict, falsify one of the input hashes it stands on."""
    d['source']['recon/derived/solutions.json'] = '0' * 64


def m_f5_csv_extra_row(text):
    """Append a duplicated row to the decoded cycle table: parseable, and not the reference's."""
    lines = text.splitlines()
    return '\n'.join(lines + [lines[-1]]) + '\n'


# --- F6: the two claims the undriven-net artifact exists for ---------------------------
# If either is false the note and the plan's E2 outcome block are false with it, so both are mutated:
# the tie that is one character off the published string, and the refutation that says no cut over the
# wire could have hidden the driver.
def m_f6_tie_matches(d):
    """Make one tie print the published string after all -- the smoothed-over version."""
    d['message_tie']['tie_1'] = d['message_tie']['published_string']


def m_f6_nearby_removed(d):
    """Empty the nearby-signal reproducers: the constant-tie refutation alone does not close the
    question of what the lost wire carried, and the gate must not pass on that refutation alone."""
    d['message_tie']['nearby_signals']['reproduce_on_all_boards'] = []


def m_f6_merges_claimed(d):
    """Claim a cut over the wire *could* merge, i.e. that a missed merge may hide a driver."""
    d['cut_overlap']['merges_that_could_hide_a_driver'] = d['cut_overlap']['overlapping'][:1]


# --- F6 also found a defect in E4's own evidence --------------------------------------
# `reproduce --cold` deleted its own report, could not regenerate it (it is written after the
# comparison), and therefore reported one permanent difference -- so `check_stepE4`'s
# `differences == []` was satisfiable only by a stale report. The fix is in `repro.derived_files`;
# this mutation covers the gate check that now holds it.
def m_e4_report_counted(d):
    """Drop the record that the run's own report is excluded from the cold set."""
    d['cold_check'].pop('report_excluded', None)


MUTATIONS = [
    ('layers: role table moved li1 -> non_elec', LAYERS, m_layers_role_table),
    ('layers: a pair\'s own role field flipped', LAYERS, m_layers_pair_role),
    ('layers: site pitch 0.46 -> 0.55', LAYERS, m_layers_pitch),
    ('layers: pin datatype 16 -> 20', LAYERS, m_layers_pin_dt),
    ('layers: summary role histogram wrong', LAYERS, m_layers_histogram),
    ('layers: summary review list emptied', LAYERS, m_layers_review),
    ('via_pairs: rule bridges li1 -> met2', VIA, m_via_rule),
    ('via_pairs: via instance total +1', VIA, m_via_total),
    ('pin_names: nand2 loses pin A', NAMES, m_names_drop_pin),
    ('pin_names: pin label total +1', NAMES, m_names_total),
    ('pin_names: per-layer label count +1', NAMES, m_names_per_layer),
    ('pinmodel: nand2 pin A loses a rect', GEOM, m_geom_drop_rect),
    ('pinmodel: nand2 bbox widened', GEOM, m_geom_bbox),
    ('pinmodel: nand2 loses pin B', GEOM, m_geom_pin_drop),
    ('pinmodel: local rule instance count +1', GEOM, m_geom_rule_total),
    ('pin_coverage: routed 489 -> 490', COV, m_cov_routed),
    ('pin_coverage: instance pct 87.69 -> 90', COV, m_cov_pct),
    ('inventory: std cell instance count +1', INV, m_inv_instances),
    ('inventory: structure count 81 -> 82', INV, m_inv_structures),
    ('inventory: warmup DEF site width 0.46 -> 0.47', INV, m_inv_defsite),
    ('inventory: GDS source hash blanked', INV, m_inv_sha),
    ('instances: a kind\'s matrix flipped', INST, m_inst_convention),
    ('instances: one anchor moved 1 site', INST, m_inst_origin),
    ('instances: one bbox widened', INST, m_inst_bbox),
    ('instances: a placement deleted', INST, m_inst_delete),
    ('instances: a footprint shifted a row', INST, m_inst_footprint),
    ('instances: DEF oracle match count faked', INST, m_inst_def_oracle),
    ('netlist: equivalence flag inverted', WNET, m_net_equivalent),
    ('netlist: probed pin count +1', WNET, m_net_probed),
    ('netlist: an unconnected pin reported', WNET, m_net_unconnected),
    ('netlist: a terminal moved to another net', WNET, m_net_move_terminal),
    ('netlist: a whole net dropped', WNET, m_net_drop_net),
    ('netlist: a conductor layer removed', WNET, m_net_engine),
    ('nets: an orphan shape reported', NETS, m_nets_orphan),
    ('nets: unexplained unassigned supplies', NETS, m_nets_unexplained),
    ('nets: one layer short by a shape', NETS, m_nets_layer),
    ('nets: total shape count -1', NETS, m_nets_shapes),
    ('nets: supply claim weakened', NETS, m_nets_supply_claim),
    ('nets: cluster ids made non-unique', NETS, m_nets_ids_collide),
    ('nets: uniqueness claim inverted', NETS, m_nets_unique_claim),
    ('pin_net: a functional pin left unassigned', PINNET, m_pn_unassign_functional),
    ('pin_net: a terminal moved to another net', PINNET, m_pn_move_terminal),
    ('pin_net: evidence point falsified', PINNET, m_pn_falsify_evidence),
    ('pin_net: an instance dropped from the map', PINNET, m_pn_drop_instance),
    ('pin_net: a supply gap called unexplained', PINNET, m_pn_gap_unexplained),
    ('pin_net: uniqueness lied about', PINNET, m_pn_uniqueness_lie),
    ('netlist_check: a port reassigned to another net', CHECK, m_chk_port_net),
    ('netlist_check: a port direction flipped', CHECK, m_chk_port_dir),
    ('netlist_check: the undriven net hidden', CHECK, m_chk_undriven_hidden),
    ('netlist_check: an input class moved to the output table', CHECK, m_chk_direction_flipped),
    ('netlist_check: the auditor contradiction hidden', CHECK, m_chk_crosscheck_hidden),
    ('netlist_check: an extra single-terminal net', CHECK, m_chk_extra_single),
    ('netlist_check: infeasible net count faked', CHECK, m_chk_infeasible),
    ('puzzle.v: one pin re-pointed at a neighbouring net', PUZZLEV, m_v_repoint),
    ('puzzle.v: one instantiation deleted', PUZZLEV, m_v_drop_instance),
    ('cells.v: a model output declared as an input', CELLSV, m_v_cells_flip_direction),
    ('warmup.v: one connection re-pointed', WARMV, m_v_warmup_repoint),
    ('warmup_b7: equivalence claimed on half the coverage', WARMREP, m_b7_claim_equivalent),
    ('warmup_b7: the well ties claimed as assigned', WARMREP, m_b7_claim_vpb),
    ('warmup_b7: a located port dropped', WARMREP, m_b7_drop_port),
    ('replay_tb: stimulus moved one clock period', REPLAYV, m_v_replay_shift),
    ('replay_tb: one cycle short', REPLAYV, m_v_replay_short),
    ('replay_tb: samples in the edge s time step', REPLAYV, m_v_replay_no_delay),
    ('vcd_replay: a reference byte changed', REPLAY, m_rep_reference_value),
    ('vcd_replay: the decoded message changed', REPLAY, m_rep_stream_text),
    ('vcd_replay: the undriven probe reading changed', REPLAY, m_rep_probe_z),
    ('vcd_replay: a forced run claimed to move outputs', REPLAY, m_rep_forced_run),
    ('c1_power: the caught count faked', POWER, m_pw_totals),
    ('c1_power: the F1 break called silent', POWER, m_pw_f1_silent),
    ('c1_power: an undriven-net consumer called visible', POWER, m_pw_consumer_visible),
    ('warmup_equiv_tb: sweeps less than the whole space', EQTB, m_eq_tb_short),
    ('warmup_equiv_tb: shifts one bit too few', EQTB, m_eq_tb_off_by_one),
    ('warmup_equiv: the protocol width misstated', EQREP, m_eq_ref_width),
    ('warmup_equiv: a shorter sweep claimed', EQREP, m_eq_sweep_claim),
    ('warmup_equiv: mismatches claimed', EQREP, m_eq_mismatch),
    ('warmup_equiv: the wrong top module named', EQREP, m_eq_rename),
    ('decompose_ref_tb: samples at the edge instead of after it', DECREF, m_dec_tb_delay),
    ('decompose_win_tb: prints one flop fewer than the design has', DECWIN, m_dec_tb_drop_flop),
    ('blocks: a shift stage claims the wrong depth', BLOCKS, m_blocks_delay),
    ('blocks: a flop dropped from a block list', BLOCKS, m_blocks_drop_flop),
    ('blocks: the ones counter peak inflated', BLOCKS, m_blocks_peak),
    ('blocks: a bit dropped from the ones counter chain', BLOCKS, m_blocks_ones_bit),
    ('c2_power: the caught count faked', WUPOW, m_eq_pw_totals),
    ('c2_power: a caught model called silent', WUPOW, m_eq_pw_sample),
    ('c4_partition: a class loses a cell', C4ART, m_c4_drop_cell),
    ('c4_partition: the solution count faked', C4ART, m_c4_solution_count),
    ('c4_partition: a trigger set dropped from the total', C4ART, m_c4_trigger_total),
    ('c5_rejections: a board claimed accepted', C5ART, m_c5_accept_one),
    ('c5_rejections: a board dropped', C5ART, m_c5_drop_board),
    ('c5_rejections: the recorded replay faulted', C5ART, m_c5_replay_lie),
    ('c5_rejections: the rejection test called powerful', C5ART, m_c5_power),
    ('e1_messages: the rising cycle moved', E1ART, m_e1_cycle),
    ('e1_messages: a decoded message changed', E1ART, m_e1_message),
    ('e1_messages: the open item deleted', E1ART, m_e1_open_hidden),
    ('solutions: a second solution claimed', SOLART, m_d_uniqueness_faked),
    ('solutions: one bit of the vector flipped', SOLART, m_d_vector_flipped),
    ('solutions: a star moved in the board', SOLART, m_d_grid_moved),
    ('solutions: the enumerators made to disagree', SOLART, m_d_enumerator_disagree),
    ('solutions: a class loaded beyond capacity', SOLART, m_d_region_load),
    ('e2_messages: only a few boards claimed to spell it', E2ART, m_e2_claim_all_spell),
    ('e2_messages: the control group silenced', E2ART, m_e2_control_silenced),
    ('e2_messages: the undriven-net finding smoothed', E2ART, m_e2_undriven_smoothed),
    ('e2_messages: a tested board moved', E2ART, m_e2_board_moved),
    ('e2_messages: another message class corrupted', E2ART, m_e2_class_wrong),
    ('e2_messages: the look-alike power hidden', E2ART, m_e2_lookalike_power_hidden),
    ('e2_messages: the boundary resolution limit hidden', E2ART, m_e2_boundary_resolution_hidden),
    ('acceptance: AC6 downgraded from PASS to PARTIAL', ACCEPTART, m_e3_ac6_downgraded),
    ('acceptance: AC1 s bit-order result made unfavourable', ACCEPTART, m_e3_ac1_friendly),
    ('acceptance: the replay comparison made vacuous', ACCEPTART, m_e3_ac5_vacuous),
    ('acceptance: the counts faked without the statuses', ACCEPTART, m_e3_counts_faked),
    ('acceptance: what is not met deleted', ACCEPTART, m_e3_unmet_removed),
    ('reproduction: a stage dropped from the plan', REPROART, m_e4_drop_stage),
    ('reproduction: a stage recorded as failing', REPROART, m_e4_fake_exit),
    ('reproduction: a timing put in the artifact', REPROART, m_e4_clock),
    ('reproduction: acceptance moved off the end', REPROART, m_e4_reorder),
    ('reproduction: a cold difference smoothed over', REPROART, m_e4_cold_smoothed),
    ('reproduction: a warm run presented as the evidence', REPROART, m_e4_warm_run),
    ('dead code: an unreferenced helper in the pipeline', SRC_CELLS, m_f1_dead_function),
    ('dead code: an unreferenced helper in a gate', SRC_C5GATE, m_f1_dead_function_in_checks),
    ('readme: a gate count one short of the truth', DOC_README, m_f2_gate_count),
    ('readme: a pipeline command that does not exist', DOC_README, m_f2_unknown_stage),
    ('deps: a package row deleted from the document', DOC_DEPS, m_f2_deps_row),
    ('style: a parked-work marker in a comment', SRC_POWER, m_f3_parked_marker),
    ('style: a line of code commented out', SRC_POWER, m_f3_commented_code),
    ('style: a long function without a docstring', SRC_SOLVE, m_f3_docstring_removed),
    # This one fires stepB6 as well as stepF2, and legitimately: B6 owns the claim "cells.v is what
    # the writer emits", so any edit to that file fails it, while F2 owns "the stamp tells the truth".
    # Two gates, two distinct claims -- recorded here rather than engineered away.
    ('stamp: cells.v names an artifact that does not exist', CELLSV, m_f5_cells_stamp),
    ('stamp: the matrix falsifies the hash of one of its inputs', ACCEPTART, m_f5_acceptance_source),
    ('s0.1: the decoded cycle table gains a row', VCDREF, m_f5_csv_extra_row),
    ('net806: a tie made to print the published string', NET806ART, m_f6_tie_matches),
    ('net806: the nearby-signal reproducers emptied', NET806ART, m_f6_nearby_removed),
    ('net806: a cut over the wire claimed able to merge', NET806ART, m_f6_merges_claimed),
    ('reproduction: the report s own exclusion dropped', REPROART, m_e4_report_counted),
]


def read(rel: str) -> bytes:
    return (ROOT / rel).read_bytes()


def snapshot() -> dict[str, bytes]:
    return {a: read(a) for a in ARTIFACTS}


def write_artifact(rel: str, data: bytes, attempts: int = 10) -> None:
    """Write one artifact through a temp file + ``os.replace``, and *prove* it landed.

    Crash-safety matters here more than style, because a failed write is corrosive: it
    leaves the working tree holding a deliberately corrupted artifact, so the next
    `run_all.py` fails for a reason that has nothing to do with the code, and the artifact
    stays silently wrong on disk until somebody notices.

    Measured twice. First the suite died in `restore()` with `OSError: [Errno 22] Invalid
    argument` while rewriting the 745 KB `pinmodel.json`, after 14 gate subprocesses had just
    read it, and left a mutated `pinmodel.json` behind. Then it died the same way at the
    *mutation* write on `instances.json` -- so hardening `restore()` alone was not enough;
    that simply moved the failure to the other writer. Hence one write path, used by both.

    The identical write succeeds in isolation, so it is an OS-level transient (a filter
    driver holding a file that was repeatedly rewritten under load), which is exactly the
    case a bounded retry is for. Two properties are added on top of the retry:

    * the target is never opened for truncation while something else may hold it -- the bytes
      go to a sibling temp file and are moved in with `os.replace`, which is atomic;
    * success is not assumed from a clean return: the bytes on disk are read back and
      compared, and a failure raises loudly instead of passing quietly.
    """
    p = ROOT / rel
    tmp = p.with_name(p.name + '.write.tmp')
    last: object = None
    for k in range(attempts):
        try:
            tmp.write_bytes(data)
            os.replace(tmp, p)
            if p.read_bytes() == data:
                return
            last = f'{rel}: bytes differ after replace'
        except OSError as exc:
            last = f'{rel}: {exc}'
            try:
                tmp.unlink()
            except OSError:
                pass
        time.sleep(0.2 * (k + 1))
    raise OSError(f'write failed after {attempts} attempts -- {last}')


def restore(snap: dict[str, bytes], attempts: int = 10) -> None:
    """Rewrite the pristine snapshot, and *prove* it landed. See :func:`write_artifact`."""
    for a, b in snap.items():
        write_artifact(a, b, attempts)


def stamp_path() -> Path:
    return ROOT / 'recon' / 'scratch' / 'verify' / 'fault_inject.stamp'


def drift(pristine: dict[str, bytes]) -> list[str]:
    return sorted(a for a in ARTIFACTS if read(a) != pristine[a])


def run_gate(rel: str) -> int:
    # 1200s, not 600s: `stepG1` (region-map re-derivation under a nulled target.py, added Task 6 of
    # the fix pass) measured 552-689s standalone and hit the old 600s cap under load once GATES
    # started including it (Task 11) -- a real timeout, not a hang, needs headroom above the slowest
    # gate's worst-case time, not just its typical one.
    return subprocess.run([sys.executable, rel], cwd=str(ROOT), capture_output=True, text=True,
                          encoding='utf-8', errors='replace', env=utf8_env(), timeout=1200).returncode


def is_scratch_clone() -> bool:
    """A clone made with `git clone <this repo> <dir>` has an `origin` pointing at a local directory.

    The primary working repository has no remote at all (checked 2026-09-15: `git remote -v` is
    empty), so "origin is a local directory" separates the two without guessing from paths.
    """
    url = subprocess.run(['git', 'config', '--get', 'remote.origin.url'], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip()
    return bool(url) and Path(url).is_dir()


def main() -> int:
    argv = sys.argv[1:]
    in_place = '--in-place' in argv
    argv = [a for a in argv if a != '--in-place']
    if not in_place and not is_scratch_clone():
        print('fault_inject mutates artifacts in place; run it in a scratch clone '
              '(git clone -b <branch> . <dir>) or pass --in-place to accept that risk', file=sys.stderr)
        return 2
    only = None
    if argv:
        if len(argv) != 2 or argv[0] != '--only':
            print('usage: fault_inject.py [--only REGEX] [--in-place]', file=sys.stderr)
            return 2
        only = re.compile(argv[1])
    selected = [m for m in MUTATIONS if only is None or only.search(m[0])]
    if not selected:
        print(f'--only {argv[1]!r} matches no mutation label', file=sys.stderr)
        return 2
    # A run that died between a mutation and its restore left the tree holding corrupted
    # artifacts. Detect that rather than starting on top of it -- otherwise the baseline
    # check below aborts with a misleading "baseline already failing", pointing the finger
    # at the gates instead of at the leftovers.
    if stamp_path().exists():
        want = json.loads(stamp_path().read_text(encoding='utf-8'))
        bad = [a for a, h in want.items() if hashlib.sha256(read(a)).hexdigest() != h]
        if bad:
            print('!! a previous run died with artifacts left mutated:')
            for a in bad:
                print(f'   - {a}')
            print('!! fix with:  git checkout -- ' + ' '.join(bad))
            return 1
        print('note: stale stamp, but no artifact drifted -- removing it')
        stamp_path().unlink()

    pristine = snapshot()
    stamp_path().parent.mkdir(parents=True, exist_ok=True)
    stamp_path().write_text(
        json.dumps({a: hashlib.sha256(b).hexdigest() for a, b in pristine.items()},
                   indent=2) + '\n', encoding='utf-8', newline='\n')
    base = {n: run_gate(rel) for n, rel in GATES}
    restore(pristine)
    print('baseline (unmutated): ' + ' '.join(f'{n}={rc}' for n, rc in base.items()))
    if any(rc != 0 for rc in base.values()):
        print('!! baseline already failing -- aborting')
        return 1

    names = [n for n, _ in GATES]
    header = 'injected fault'.ljust(46) + '| ' + ' '.join(n.rjust(9) for n in names)
    print()
    if only is not None:
        print(f'--only {argv[1]!r}: {len(selected)} of {len(MUTATIONS)} mutations')
        # Print what the filter actually selected, not just how many: a pattern that looks right
        # and silently matches the wrong set is worse than no filter, and the count alone does not
        # make the set visible. (Measured: `--only 'warmup_equiv:|c2_power:'` cannot match
        # `warmup_equiv_tb: ...`, so a run intended to cover eight mutations covered six.)
        for lbl, art_rel, _ in selected:
            print(f'   - {art_rel}: {lbl}')
    print(header)
    print('-' * len(header))

    misses, nonhermetic, fired_any = [], [], set()
    try:
        for label, art, mut in selected:
            cells, fired = [], []
            for gname, grel in GATES:
                restore(pristine)
                if art.endswith('.json'):
                    d = json.loads((ROOT / art).read_text(encoding='utf-8'))
                    mut(d)
                    write_artifact(art,
                                   json.dumps(d, indent=2, sort_keys=False).encode() + b'\n')
                else:
                    # Verilog (or any other text) artifact: the mutator takes and returns text.
                    write_artifact(art, mut((ROOT / art).read_text(encoding='utf-8'))
                                   .encode('utf-8'))
                mutated = read(art)
                rc = run_gate(grel)
                cells.append('FAIL' if rc else '.')
                if rc:
                    fired.append(gname)
                    fired_any.add(gname)
                expected = {**pristine, art: mutated}
                for a in ARTIFACTS:
                    if read(a) != expected[a]:
                        nonhermetic.append(f'{gname} rewrote {a} on "{label}"')
            print(label.ljust(46) + '| ' + ' '.join(c.rjust(9) for c in cells)
                  + '  -> ' + (','.join(fired) if fired else '*** NO GATE FIRED ***'))
            if not fired:
                misses.append(label)
    finally:
        restore(pristine)
    # Reached only on a clean pass: an exception above propagates past this line and leaves
    # the stamp in place for the next run to find.
    stamp_path().unlink(missing_ok=True)

    silent = [n for n, _ in GATES if n not in fired_any]
    left = drift(pristine)
    print()
    print(f'mutations in this sweep       : {len(selected)} of {len(MUTATIONS)}')
    print(f'mutations with no gate firing : {len(misses)}')
    for m in misses:
        print('  -', m)
    print(f'hermeticity violations        : {len(nonhermetic)}')
    for v in sorted(set(nonhermetic)):
        print('  -', v)
    print(f'gates that never fired        : {silent or "none"}')
    print(f'artifacts restored            : {not left}')
    if left:
        print(f'  STILL MUTATED: {left}  ->  git checkout -- ' + ' '.join(left))
    ok = not misses and not nonhermetic and not left
    print()
    print('FAULT INJECTION: ' + ('PASS -- every injected fault was caught, nothing rewrote it'
                                if ok else 'FAIL -- see above'))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
