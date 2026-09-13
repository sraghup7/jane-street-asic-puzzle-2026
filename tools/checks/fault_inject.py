#!/usr/bin/env python3
"""fault_inject.py -- the meta-gate: does each gate actually bite?

Deliberately **not** named `check_*.py`, so `run_all.py` does not pick it up. It mutates
derived artifacts, so it is a diagnostic you run deliberately, not part of the routine
suite:

    .venv/Scripts/python tools/checks/fault_inject.py

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
least one mutation of the artifact it owns.

Exit code 0 iff no mutation escaped every gate and no gate rewrote the tree.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GATES = [
    ('target', 'tools/target.py'),
    ('hygiene', 'tools/checks/check_hygiene.py'),
    ('step1', 'tools/checks/check_step1.py'),
    ('step2', 'tools/checks/check_step2.py'),
    ('step3', 'tools/checks/check_step3.py'),
    ('stepA1', 'tools/checks/check_stepA1.py'),
    ('stepA2', 'tools/checks/check_stepA2.py'),
    ('stepA3', 'tools/checks/check_stepA3.py'),
    ('stepA4', 'tools/checks/check_stepA4.py'),
    ('stepA5', 'tools/checks/check_stepA5.py'),
    ('recompute', 'tools/checks/check_recompute.py'),
    ('stepB1', 'tools/checks/check_stepB1.py'),
    ('stepB2', 'tools/checks/check_stepB2.py'),
    ('stepB3', 'tools/checks/check_stepB3.py'),
    ('stepB4', 'tools/checks/check_stepB4.py'),
]

LAYERS = 'recon/derived/layers.json'
VIA = 'recon/derived/via_pairs.json'
NAMES = 'recon/derived/pin_names.json'
GEOM = 'recon/derived/pinmodel.json'
COV = 'recon/derived/pin_coverage.json'
INST = 'recon/derived/instances.json'
WNET = 'recon/derived/warmup_netlist.json'
NETS = 'recon/derived/nets.json'
PINNET = 'recon/derived/pin_net.json'
INV = 'recon/inventory.json'
ARTIFACTS = [LAYERS, VIA, NAMES, GEOM, COV, INST, WNET, NETS, PINNET, INV]

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
]


def read(rel: str) -> bytes:
    return (ROOT / rel).read_bytes()


def snapshot() -> dict[str, bytes]:
    return {a: read(a) for a in ARTIFACTS}


def restore(snap: dict[str, bytes], attempts: int = 10) -> None:
    """Rewrite the pristine snapshot, and *prove* it landed.

    Crash-safety matters here more than style, because a failed restore is corrosive: it
    leaves the working tree holding a deliberately corrupted artifact, so the next
    `run_all.py` fails for a reason that has nothing to do with the code, and the artifact
    stays silently wrong on disk until somebody notices.

    Measured once, and it is why this function looks like this: the suite died here with
    `OSError: [Errno 22] Invalid argument` while rewriting the 745 KB `pinmodel.json`, after
    14 gate subprocesses had just read it -- and left a mutated `pinmodel.json` behind. The
    identical write succeeds in isolation, so it is an OS-level transient (a filter driver
    holding a file that was repeatedly rewritten under load), which is exactly the case a
    bounded retry is for. Two properties are added on top of the retry:

    * the target is never opened for truncation while something else may hold it -- the bytes
      go to a sibling temp file and are moved in with `os.replace`, which is atomic;
    * success is not assumed from a clean return: the bytes on disk are read back and
      compared, and a failure raises loudly instead of passing quietly.
    """
    for a, b in snap.items():
        p = ROOT / a
        tmp = p.with_name(p.name + '.restore.tmp')
        last: object = None
        for k in range(attempts):
            try:
                tmp.write_bytes(b)
                os.replace(tmp, p)
                if p.read_bytes() == b:
                    break
                last = f'{a}: bytes differ after replace'
            except OSError as exc:
                last = f'{a}: {exc}'
                try:
                    tmp.unlink()
                except OSError:
                    pass
            time.sleep(0.2 * (k + 1))
        else:
            raise OSError(f'restore failed after {attempts} attempts -- {last}')


def stamp_path() -> Path:
    return ROOT / 'recon' / 'scratch' / 'verify' / 'fault_inject.stamp'


def drift(pristine: dict[str, bytes]) -> list[str]:
    return sorted(a for a in ARTIFACTS if read(a) != pristine[a])


def run_gate(rel: str) -> int:
    return subprocess.run([sys.executable, rel], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=600).returncode


def main() -> int:
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
    print(header)
    print('-' * len(header))

    misses, nonhermetic, fired_any = [], [], set()
    try:
        for label, art, mut in MUTATIONS:
            cells, fired = [], []
            for gname, grel in GATES:
                restore(pristine)
                d = json.loads((ROOT / art).read_text(encoding='utf-8'))
                mut(d)
                (ROOT / art).write_text(json.dumps(d, indent=2, sort_keys=False) + '\n',
                                        encoding='utf-8', newline='\n')
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
