#!/usr/bin/env python3
"""Analyze the separately frozen, post-review retry robustness experiment.

This follow-up uses new cohort seeds and never rewrites the initial primary
analysis. Same-generation retransmission retains the command's cap and absolute
lease; command totals include these additional transport attempts.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
POLICIES=('B3','P','B3_retry','P_retry')
PATTERNS=('T1','T2','T3','T4')
COMPARISONS=(('P_retry','B3_retry'),('B3_retry','B3'),('P_retry','P'),('P','B3'))


def _load(name,filename):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/filename)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE=_load('ev_primary_analysis','analyze_study.py')


def expected_keys():
    return {('retry','retry60',seed,pattern,loading,'ieee123',policy)
            for seed in range(3000,3030) for pattern in PATTERNS
            for loading in (.9,1.) for policy in POLICIES}


def build_summary(rows):
    BASE.validate_rows(rows,expected_keys())
    policies={}
    for policy in POLICIES:
        selected=[row for row in rows if row['policy']==policy]
        policies[policy]=BASE.summarize(selected)
        if all('retries' in row for row in selected):
            policies[policy]['retries_mean']=sum(row['retries'] for row in selected)/len(selected)
            policies[policy]['retries_total']=sum(row['retries'] for row in selected)
    comparisons={}
    for policy,reference in COMPARISONS:
        comparisons[f'{policy}_minus_{reference}']=dict(
            overall=BASE.paired_effect(rows,policy,reference),
            by_pattern={pattern:BASE.paired_effect(
                [row for row in rows if row['pattern']==pattern],policy,reference)
                for pattern in PATTERNS})
    return dict(schema_version=1,metadata=dict(
        study_stage='post_review_robustness',
        design_status='separately frozen before access to new-cohort outcomes',
        episodes=len(rows),seed_min=3000,seed_max=3029,independent_cohort_seeds=30,
        conditions_per_cohort=8,episodes_per_policy=240,
        primary_comparison='P_retry_minus_B3_retry',
        independent_unit='synthetic cohort seed',
        bootstrap_method='paired percentile bootstrap of cohort means',
        bootstrap_draws=BASE.BOOTSTRAP_DRAWS,bootstrap_seed=BASE.BOOTSTRAP_SEED,
        retry_period_s=60,
        retry_semantics='same generation, cap, start, absolute end, and epoch; no lease extension',
        command_count_definition='all command transport attempts including retransmissions',
        safety_episode_definition='violation_seconds > 0, unchanged simulator limits',
        confidence_interval_scope='marginal 95 percent intervals; no simultaneous coverage claim',
        initial_primary_analysis='unchanged'),policies=policies,comparisons=comparisons)


def write_outputs(rows,summary,destination):
    destination.mkdir(parents=True,exist_ok=True)
    ordered=sorted(rows,key=lambda row:tuple(row[key] for key in BASE.IDENTITY))
    BASE.write_csv(ordered,destination/'episodes.csv')
    (destination/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True,allow_nan=False)+'\n')
    effects=[]
    for comparison,value in summary['comparisons'].items():
        for pattern,effect in [('all_impaired',value['overall'])]+list(value['by_pattern'].items()):
            effects.append(dict(comparison=comparison,pattern=pattern,
                **{key:value for key,value in effect.items() if key not in ('ci95','block_means')},
                ci95_low=effect['ci95'][0],ci95_high=effect['ci95'][1]))
    BASE.write_csv(effects,destination/'paired_effects.csv')
    labels={'B3':'B3','P':'P','B3_retry':r'B3$_{\mathrm{r}}$','P_retry':r'P$_{\mathrm{r}}$'}
    table=[r'% Post-review robustness experiment on new cohorts; initial primary analysis unchanged.',
           r'\begin{tabular}{lrrrr}',r'\hline',
           r'Policy & Energy & Unsafe & Commands & Queries \\',
           r' & (kWh) & (/240) & (mean) & (mean) \\',r'\hline']
    for policy in POLICIES:
        values=summary['policies'][policy]
        table.append(f'{labels[policy]} & {values["delivered_kwh_mean"]:.1f} & '
                     f'{values["unsafe_episodes"]} & {values["commands_mean"]:.1f} & '
                     f'{values["queries_mean"]:.1f} \\\\')
    table += [r'\hline',r'\end{tabular}']
    (destination/'retry_table.tex').write_text('\n'.join(table)+'\n')


def make_figure(summary,destination):
    figures=_load('ev_primary_figures','make_figures.py')
    values=summary['comparisons']['P_retry_minus_B3_retry']
    effects=[values['by_pattern'][pattern] for pattern in PATTERNS]+[values['overall']]
    labels=[figures.PATTERN_LABELS[pattern] for pattern in PATTERNS]+['All impaired cases']
    figure,axis=figures.plt.subplots(figsize=(3.5,1.95))
    figures.forest(axis,effects,labels,[figures.COLORS['P']]*4+['#292929'])
    axis.set_xlabel('P retry − B3 retry energy (kWh)')
    axis.axhline(3.5,color='#b9c0c7',linewidth=.5)
    figure.subplots_adjust(left=.40,right=.97,bottom=.26,top=.95)
    figures.save(figure,'retry_effects',destination)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=ROOT/'results/retry.jsonl')
    parser.add_argument('--config',type=Path,default=ROOT/'results/retry.config.json')
    parser.add_argument('--out',type=Path,default=ROOT/'results/retry_analysis')
    parser.add_argument('--figures',action='store_true',help='Write companion SVG/PDF retry forest only')
    args=parser.parse_args()
    rows=[json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    # A saved resolved configuration must accompany these post-review results.
    json.loads(args.config.read_text())
    summary=build_summary(rows)
    summary['metadata']['input_sha256']=hashlib.sha256(args.input.read_bytes()).hexdigest()
    summary['metadata']['config_sha256']=hashlib.sha256(args.config.read_bytes()).hexdigest()
    summary['metadata']['analysis_source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    summary['metadata']['primary_analysis_source_sha256']=hashlib.sha256((ROOT/'scripts/analyze_study.py').read_bytes()).hexdigest()
    write_outputs(rows,summary,args.out)
    if args.figures:
        make_figure(summary,ROOT/'manuscript/figures')
        provenance=dict(study_stage='post_review_robustness',
            summary_sha256=hashlib.sha256((args.out/'summary.json').read_bytes()).hexdigest(),
            retry_analysis_source_sha256=summary['metadata']['analysis_source_sha256'],
            shared_figure_source_sha256=hashlib.sha256((ROOT/'scripts/make_figures.py').read_bytes()).hexdigest(),
            figure_files=['retry_effects.svg','retry_effects.pdf'])
        (args.out/'figure_provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    print(json.dumps(dict(output=str(args.out),episodes=len(rows),
        primary_robustness_effect=summary['comparisons']['P_retry_minus_B3_retry']['overall']),indent=2))


if __name__=='__main__':
    main()
