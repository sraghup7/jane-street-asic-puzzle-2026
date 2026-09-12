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

import json
import subprocess
import sys
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
]

LAYERS = 'recon/derived/layers.json'
VIA = 'recon/derived/via_pairs.json'
NAMES = 'recon/derived/pin_names.json'
GEOM = 'recon/derived/pinmodel.json'
COV = 'recon/derived/pin_coverage.json'
INST = 'recon/derived/instances.json'
INV = 'recon/inventory.json'
ARTIFACTS = [LAYERS, VIA, NAMES, GEOM, COV, INST, INV]

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
]


def read(rel: str) -> bytes:
    return (ROOT / rel).read_bytes()


def snapshot() -> dict[str, bytes]:
    return {a: read(a) for a in ARTIFACTS}


def restore(snap: dict[str, bytes]) -> None:
    for a, b in snap.items():
        (ROOT / a).write_bytes(b)


def run_gate(rel: str) -> int:
    return subprocess.run([sys.executable, rel], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=600).returncode


def main() -> int:
    pristine = snapshot()
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

    silent = [n for n, _ in GATES if n not in fired_any]
    print()
    print(f'mutations with no gate firing : {len(misses)}')
    for m in misses:
        print('  -', m)
    print(f'hermeticity violations        : {len(nonhermetic)}')
    for v in sorted(set(nonhermetic)):
        print('  -', v)
    print(f'gates that never fired        : {silent or "none"}')
    print(f'artifacts restored            : {snapshot() == pristine}')
    ok = not misses and not nonhermetic and snapshot() == pristine
    print()
    print('FAULT INJECTION: ' + ('PASS -- every injected fault was caught, nothing rewrote it'
                                if ok else 'FAIL -- see above'))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
