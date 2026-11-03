#!/usr/bin/env python3
"""Validate saved episodes and analyze paired, independent cohort blocks.

The unit of resampling is a cohort seed. Replays at multiple load levels and
channel patterns remain together; individual time intervals are never samples.
"""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
POLICIES = ('B0', 'B1', 'B2', 'B3', 'B4', 'P', 'R')
PATTERNS = ('T1', 'T2', 'T3', 'T4')
ABLATIONS = ('P_no_pending', 'P_no_fallback', 'P_acceptance', 'P_aggregate')
SENSITIVITY_LABELS = ('confirmation15', 'confirmation90', 'confirmation180',
    'affected1', 'affected2', 'affected4', 'actuation0', 'actuation5',
    'actuation15', 'ieee13_t1', 'ieee13_t2', 'ieee13_t3')
BOOTSTRAP_DRAWS = 10000
BOOTSTRAP_SEED = 697213
IDENTITY = ('phase', 'label', 'seed', 'pattern', 'loading', 'feeder', 'policy')
CONDITION = ('pattern', 'loading', 'feeder')


def expected_keys(phase):
    """Independent explicit record of the prespecified experiment layout."""
    if phase == 'main':
        return {(phase, 'main', seed, pattern, loading, 'ieee123', policy)
                for seed in range(1000, 1030) for pattern in ('clean',) + PATTERNS
                for loading in (.9, 1.) for policy in POLICIES}
    if phase == 'sensitivity':
        return {(phase, label, seed,
                 {'ieee13_t1': 'T1', 'ieee13_t2': 'T2', 'ieee13_t3': 'T3'}.get(label, 'T3'),
                 1., 'ieee13' if label.startswith('ieee13') else 'ieee123', policy)
                for label in SENSITIVITY_LABELS for seed in range(2000, 2010)
                for policy in POLICIES}
    if phase == 'ablations':
        return {(phase, 'mechanism', seed, pattern, 1., 'ieee123', policy)
                for seed in range(1000, 1005) for pattern in PATTERNS
                for policy in ABLATIONS}
    raise ValueError(f'Unknown experiment phase: {phase}')


def _finite(value, path='row'):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f'Non-finite number at {path}')
    if isinstance(value, dict):
        for key, item in value.items():
            _finite(item, f'{path}.{key}')
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _finite(item, f'{path}[{i}]')


def validate_rows(rows, expected):
    """Reject incomplete, duplicated, nonconverged, or unequally paired data."""
    seen = set()
    cohorts = {}
    for row in rows:
        _finite(row)
        key = tuple(row[name] for name in IDENTITY)
        if key in seen:
            raise ValueError(f'Duplicate episode: {key}')
        seen.add(key)
        if row.get('failed') or row.get('convergence_failures', 0):
            raise ValueError(f'Failed or nonconverged episode: {key}')
        if not math.isclose(row['simulated_seconds'], row['duration_s'], abs_tol=1e-8):
            raise ValueError(f'Incomplete physical replay: {key}')
        cohort_key = key[:-1]
        signature = (row['requested_kwh'], frozenset(row['session_delivered_kwh']))
        if cohort_key in cohorts:
            reference = cohorts[cohort_key]
            if not math.isclose(signature[0], reference[0], rel_tol=0, abs_tol=1e-8) or signature[1] != reference[1]:
                raise ValueError(f'Unequal cohort across paired policies: {cohort_key}')
        cohorts[cohort_key] = signature
    missing, extra = set(expected) - seen, seen - set(expected)
    if missing or extra:
        raise ValueError(f'Experiment layout mismatch: missing={len(missing)}, extra={len(extra)}; '
                         f'first missing={next(iter(sorted(missing)), None)}, '
                         f'first extra={next(iter(sorted(extra)), None)}')


def paired_effect(rows, policy, reference, metric='delivered_kwh',
                  draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED):
    """Policy minus reference, averaging conditions before cohort bootstrap."""
    by_policy = {policy: {}, reference: {}}
    for row in rows:
        if row['policy'] not in by_policy:
            continue
        key = (row['seed'],) + tuple(row[name] for name in CONDITION)
        values = by_policy[row['policy']]
        if key in values:
            raise ValueError(f'Duplicate paired episode: {row["policy"]}, {key}')
        values[key] = float(row[metric])
    left, right = by_policy[policy], by_policy[reference]
    if not left or left.keys() != right.keys():
        raise ValueError(f'Unmatched policy pairs for {policy} versus {reference}')
    grouped = defaultdict(list)
    conditions = defaultdict(set)
    for key in sorted(left):
        grouped[key[0]].append(left[key] - right[key])
        conditions[key[0]].add(key[1:])
    first_conditions = next(iter(conditions.values()))
    if any(value != first_conditions for value in conditions.values()):
        raise ValueError('Cohort blocks must have balanced condition sets')
    blocks = {str(key): float(np.mean(grouped[key])) for key in sorted(grouped)}
    values = np.array(list(blocks.values()))
    rng = np.random.default_rng(seed)
    sampled = values[rng.integers(0, len(values), size=(int(draws), len(values)))].mean(axis=1)
    low, high = np.quantile(sampled, [.025, .975])
    ref_mean = float(np.mean(list(right.values())))
    mean = float(values.mean())
    return dict(policy=policy, reference=reference, metric=metric,
                n_pairs=len(left), n_blocks=len(blocks), conditions_per_block=len(first_conditions),
                mean=mean, ci95=[float(low), float(high)], block_means=blocks,
                policy_mean=float(np.mean(list(left.values()))), reference_mean=ref_mean,
                relative_to_reference_percent=100*mean/ref_mean if ref_mean else None,
                bootstrap_draws=int(draws), bootstrap_seed=int(seed))


def summarize(rows):
    if not rows:
        raise ValueError('Cannot summarize an empty experiment group')
    means = ('delivered_kwh', 'deadline_shortfall_kwh', 'requested_kwh',
             'reserved_unused_kwh', 'commands', 'queries', 'filter_seconds')
    totals = ('violation_seconds', 'voltage_exposure_pu_s', 'loading_exposure_s',
              'envelope_containment_failures', 'envelope_containment_seconds',
              'commands', 'queries', 'convergence_failures')
    result = dict(episodes=len(rows),
        unsafe_episodes=sum(row['violation_seconds'] > 0 for row in rows),
        initial_violation_episodes=sum(bool(row['initial_electrical_violation']) for row in rows),
        containment_failure_episodes=sum(row['envelope_containment_failures'] > 0 for row in rows),
        failed_episodes=sum(bool(row['failed']) for row in rows),
        v_min=float(min(row['v_min'] for row in rows)),
        v_max=float(max(row['v_max'] for row in rows)),
        loading_max=float(max(row['loading_max'] for row in rows)),
        max_affine_excess=float(max(row['max_affine_excess'] for row in rows)),
        max_envelope_excess_kw=float(max(row['max_envelope_excess_kw'] for row in rows)),
        filter_episode_median_ms_median=float(np.median([row['filter_median_ms'] for row in rows])),
        filter_max_ms=float(max(row['filter_max_ms'] for row in rows)))
    result.update({f'{name}_mean': float(np.mean([row[name] for row in rows])) for name in means})
    result.update({f'{name}_total': float(sum(row[name] for row in rows)) for name in totals})
    result['delivered_fraction_of_requested'] = result['delivered_kwh_mean']/result['requested_kwh_mean']
    return result


def policy_summaries(rows, policies=POLICIES):
    return {policy: summarize([row for row in rows if row['policy'] == policy]) for policy in policies}


def build_summary(main, sensitivity, ablations):
    for phase, rows in [('main', main), ('sensitivity', sensitivity), ('ablations', ablations)]:
        validate_rows(rows, expected_keys(phase))
    impaired = [row for row in main if row['pattern'] in PATTERNS]
    clean = [row for row in main if row['pattern'] == 'clean']
    result = dict(schema_version=1, metadata=dict(
        independent_unit='synthetic cohort seed', bootstrap_method='paired percentile bootstrap of cohort means',
        bootstrap_draws=BOOTSTRAP_DRAWS, bootstrap_seed=BOOTSTRAP_SEED,
        primary_population='T1-T4; two loading levels; 30 paired cohort seeds',
        safety_episode_definition='violation_seconds > 0, using simulator thresholds 0.95 <= V <= 1.05 and loading <= 1',
        filter_runtime_definition='median of per-episode decision-time medians; maximum over all decisions',
        main_episodes=len(main), sensitivity_episodes=len(sensitivity), ablation_episodes=len(ablations)),
        main=dict(impaired=policy_summaries(impaired), clean=policy_summaries(clean),
            by_pattern={pattern: policy_summaries([r for r in main if r['pattern'] == pattern]) for pattern in ('clean',)+PATTERNS},
            paired_P_minus_B3=dict(overall=paired_effect(impaired, 'P', 'B3'),
                by_pattern={pattern: paired_effect([r for r in impaired if r['pattern'] == pattern], 'P', 'B3') for pattern in PATTERNS}),
            other_paired_effects={f'P_minus_{reference}': paired_effect(impaired, 'P', reference) for reference in ('B0','B1','B2','B4','R')}),
        sensitivity=dict(by_label={}), ablations=dict(by_policy={}))
    for label in SENSITIVITY_LABELS:
        rows = [r for r in sensitivity if r['label'] == label]
        result['sensitivity']['by_label'][label] = dict(policies=policy_summaries(rows),
            paired_P_minus_B3=paired_effect(rows, 'P', 'B3'))
    selected = [r for r in main if r['policy']=='P' and 1000<=r['seed']<1005
                and r['pattern'] in PATTERNS and r['loading']==1.]
    result['ablations']['full_P_matched'] = summarize(selected)
    for policy in ABLATIONS:
        rows = [r for r in ablations if r['policy']==policy]
        result['ablations']['by_policy'][policy] = dict(summary=summarize(rows),
            paired_minus_full_P=paired_effect(rows+selected, policy, 'P'))
    return result


def write_csv(rows, destination):
    fields = sorted(set().union(*(row.keys() for row in rows)))
    with destination.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, sort_keys=True, separators=(',',':'))
                             if isinstance(value, (dict, list)) else value for key, value in row.items()})


def write_outputs(main, sensitivity, ablations, summary, destination):
    destination.mkdir(parents=True, exist_ok=True)
    all_rows = sorted(main+sensitivity+ablations, key=lambda row: tuple(row[k] for k in IDENTITY))
    write_csv(all_rows, destination/'episodes.csv')
    (destination/'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)+'\n')
    table = [r'\begin{tabular}{lrrrr}', r'\hline',
             r'Policy & Energy & Shortfall & Unsafe & Peak load \\',
             r' & (kWh) & (kWh) & (/240) & (p.u.) \\', r'\hline']
    for policy in POLICIES:
        values = summary['main']['impaired'][policy]
        table.append(f'{policy} & {values["delivered_kwh_mean"]:.1f} & '
                     f'{values["deadline_shortfall_kwh_mean"]:.1f} & {values["unsafe_episodes"]} & '
                     f'{values["loading_max"]:.3f} \\\\')
    table += [r'\hline', r'\end{tabular}']
    (destination/'main_table.tex').write_text('\n'.join(table)+'\n')
    effects = []
    main_effects = summary['main']['paired_P_minus_B3']
    for label, effect in [('all_impaired', main_effects['overall'])]+list(main_effects['by_pattern'].items()):
        effects.append(dict(group='main', label=label, **{k:v for k,v in effect.items() if k not in ('ci95','block_means')},
                            ci95_low=effect['ci95'][0], ci95_high=effect['ci95'][1]))
    for label, values in summary['sensitivity']['by_label'].items():
        effect=values['paired_P_minus_B3']
        effects.append(dict(group='sensitivity', label=label, **{k:v for k,v in effect.items() if k not in ('ci95','block_means')},
                            ci95_low=effect['ci95'][0], ci95_high=effect['ci95'][1]))
    for label, values in summary['ablations']['by_policy'].items():
        effect=values['paired_minus_full_P']
        effects.append(dict(group='ablation', label=label, **{k:v for k,v in effect.items() if k not in ('ci95','block_means')},
                            ci95_low=effect['ci95'][0], ci95_high=effect['ci95'][1]))
    write_csv(effects, destination/'paired_effects.csv')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=ROOT/'results')
    parser.add_argument('--out', type=Path, default=ROOT/'results/analysis')
    args=parser.parse_args()
    phases={}
    provenance={}
    for phase in ('main', 'sensitivity', 'ablations'):
        path=args.results/f'{phase}.jsonl'
        phases[phase]=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        provenance[phase] = dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    summary=build_summary(**phases)
    summary['metadata']['inputs']=provenance
    summary['metadata']['analysis_source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    write_outputs(**phases, summary=summary, destination=args.out)
    print(json.dumps(dict(output=str(args.out), episodes=sum(len(rows) for rows in phases.values()),
        primary_effect=summary['main']['paired_P_minus_B3']['overall']), indent=2))


if __name__=='__main__':
    main()
