#!/usr/bin/env python3
"""Generate editable SVG and vector PDF figures from validated saved results."""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
POLICIES = ('B0', 'B1', 'B2', 'B3', 'B4', 'P', 'R')
COLORS = dict(B0='#6c6c6c', B1='#b28b55', B2='#9cacc2', B3='#d46726',
              B4='#89649c', P='#146e9b', R='#367c66')
PATTERN_LABELS = dict(T1='T1: link loss', T2='T2: delayed reduction',
                     T3='T3: delayed increase', T4='T4: expiry / fallback')
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 8,
    'axes.labelsize': 8, 'axes.titlesize': 8, 'xtick.labelsize': 7.5,
    'ytick.labelsize': 7.5, 'legend.fontsize': 7, 'axes.linewidth': .6,
    'lines.linewidth': 1.15, 'grid.linewidth': .4, 'grid.color': '#d7dde3',
    'svg.fonttype': 'none', 'svg.hashsalt': 'ev-recovery-pesgm2027',
    'pdf.fonttype': 42, 'ps.fonttype': 42, 'savefig.facecolor': 'white'})


def save(figure, name, destination):
    destination.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination/f'{name}.svg', metadata={'Date': None, 'Creator': 'EV recovery study'})
    figure.savefig(destination/f'{name}.pdf', metadata={'CreationDate': None, 'ModDate': None,
                                                       'Creator': 'EV recovery study'})
    plt.close(figure)


def clean_axis(axis, grid='x'):
    axis.spines[['top', 'right']].set_visible(False)
    axis.set_axisbelow(True)
    axis.grid(axis=grid)


def outcome_figure(summary, destination):
    values=summary['main']['impaired']
    figure, axes=plt.subplots(1, 2, figsize=(3.5, 2.25), sharey=True,
                             gridspec_kw={'width_ratios': [1.25, 1.]})
    y=np.arange(len(POLICIES))
    energy=[values[p]['delivered_kwh_mean'] for p in POLICIES]
    unsafe=[values[p]['unsafe_episodes'] for p in POLICIES]
    axes[0].barh(y, energy, color=[COLORS[p] for p in POLICIES], height=.67)
    axes[1].barh(y, unsafe, color=[COLORS[p] for p in POLICIES], height=.67)
    axes[0].set_yticks(y, POLICIES)
    axes[0].invert_yaxis()
    axes[0].set_xlim(0, max(energy)*1.23)
    axes[1].set_xlim(0, max(1, max(unsafe))*1.25)
    for yi, value in enumerate(energy):
        axes[0].text(value+max(energy)*.025, yi, f'{value:.0f}', va='center', fontsize=7)
    for yi, value in enumerate(unsafe):
        axes[1].text(value+max(1,max(unsafe))*.03, yi, str(value), va='center', fontsize=7)
    axes[0].set_xlabel('Delivered energy (kWh)')
    axes[1].set_xlabel('Violating episodes / 240')
    axes[0].set_title('(a) Mean charging service', loc='left', pad=5)
    axes[1].set_title('(b) AC feasibility', loc='left', pad=5)
    for axis in axes:
        clean_axis(axis)
    figure.subplots_adjust(left=.105, right=.985, bottom=.20, top=.87, wspace=.17)
    save(figure, 'outcomes', destination)


def forest(axis, effects, labels, colors=None):
    y=np.arange(len(effects))
    for index, effect in enumerate(effects):
        value=effect['mean']
        lo,hi=effect['ci95']
        color=colors[index] if colors else COLORS['P']
        axis.errorbar(value, index, xerr=[[value-lo],[hi-value]], fmt='o',
                      color=color, markersize=3.8, capsize=2.5, elinewidth=1.)
    axis.axvline(0, color='#353535', linewidth=.8, linestyle='--', zorder=0)
    axis.set_yticks(y, labels)
    axis.invert_yaxis()
    axis.set_xlabel('P − B3 delivered energy (kWh)')
    clean_axis(axis)


def paired_figure(summary, destination):
    values=summary['main']['paired_P_minus_B3']
    effects=[values['by_pattern'][pattern] for pattern in ('T1','T2','T3','T4')]+[values['overall']]
    labels=[PATTERN_LABELS[pattern] for pattern in ('T1','T2','T3','T4')]+['All impaired cases']
    figure, axis=plt.subplots(figsize=(3.5, 1.95))
    forest(axis, effects, labels, [COLORS['P']]*4+['#292929'])
    axis.axhline(3.5, color='#b9c0c7', linewidth=.5)
    figure.subplots_adjust(left=.40, right=.97, bottom=.26, top=.95)
    save(figure, 'paired_effects', destination)


def sensitivity_figure(summary, destination):
    labels=[('confirmation15','Evidence delay: 15 s'), ('confirmation90','Evidence delay: 90 s'),
            ('confirmation180','Evidence delay: 180 s'), ('affected1','Affected groups: 1'),
            ('affected2','Affected groups: 2'), ('affected4','Affected groups: 4'),
            ('actuation0','Actuation: 0 s'), ('actuation5','Actuation: 5 s'),
            ('actuation15','Actuation: 15 s'), ('ieee13_t1','IEEE 13: T1'),
            ('ieee13_t2','IEEE 13: T2'), ('ieee13_t3','IEEE 13: T3')]
    effects=[summary['sensitivity']['by_label'][key]['paired_P_minus_B3'] for key,_ in labels]
    figure,axis=plt.subplots(figsize=(3.5,3.35))
    forest(axis,effects,[label for _,label in labels])
    for y in (2.5,5.5,8.5):
        axis.axhline(y,color='#b9c0c7',linewidth=.5)
    figure.subplots_adjust(left=.40,right=.97,bottom=.15,top=.97)
    save(figure,'sensitivity',destination)


def trace_series(trace):
    if not trace:
        raise ValueError('An empty trace cannot be plotted')
    times=np.array([row['t'] for row in trace],dtype=float)
    intervals=np.array([row['dt'] for row in trace],dtype=float)
    if np.any(intervals<=0) or not np.allclose(times[1:],(times+intervals)[:-1],rtol=0,atol=1e-7):
        raise ValueError('Trace intervals must be positive and contiguous')
    energy=np.array([row['delivered_kwh'] for row in trace],dtype=float)
    final=energy[-1]+trace[-1]['total_actual_kw']*intervals[-1]/3600.
    return dict(minutes=np.append(times,times[-1]+intervals[-1])/60.,
        energy_kwh=np.append(energy,final),
        loading=np.append([row['loading_max'] for row in trace],trace[-1]['loading_max']))


def trace_figure(data,destination):
    selection=data['selection']
    figure,axes=plt.subplots(2,1,figsize=(3.5,2.4),sharex=True)
    for policy in ('B0','B3','P','R'):
        if policy not in data['episodes']:
            continue
        episode=data['episodes'][policy]
        series=trace_series(episode['trace'])
        if not np.isclose(series['energy_kwh'][-1],episode['metrics']['delivered_kwh'],rtol=0,atol=1e-7):
            raise ValueError(f'Energy endpoint disagrees with saved episode metrics for {policy}')
        linestyle='--' if policy=='R' else '-'
        axes[0].plot(series['minutes'],series['energy_kwh'],color=COLORS[policy],label=policy,linestyle=linestyle)
        axes[1].step(series['minutes'],series['loading'],where='post',color=COLORS[policy],linestyle=linestyle)
    axes[0].set_ylabel('Delivered energy\n(kWh)')
    axes[1].set_ylabel('Peak terminal\ncurrent (p.u.)')
    axes[1].set_xlabel('Time (min)')
    axes[1].axhline(1.,color='#333333',linestyle=':',linewidth=.8)
    axes[0].legend(ncol=4,frameon=False,loc='upper left',handlelength=1.5,columnspacing=1.)
    axes[0].set_ylim(bottom=0)
    for axis in axes:
        clean_axis(axis,grid='both')
        axis.set_xlim(0,30)
    if selection.get('pattern')=='T1':
        # Union of the three staggered loss windows in the default scenario.
        # The exact interval is described as a union, not a common outage.
        start=selection.get('attack_start',270.)/60.
        end=(selection.get('attack_end',690.)+13*(selection.get('affected_stations',3)-1))/60.
        for axis in axes:
            axis.axvspan(start,end,color='#c4cbd1',alpha=.20,zorder=-1)
    figure.subplots_adjust(left=.195,right=.98,bottom=.145,top=.97,hspace=.12)
    save(figure,'representative_trace',destination)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary',type=Path,default=ROOT/'results/analysis/summary.json')
    parser.add_argument('--trace',type=Path,default=ROOT/'results/representative_trace.json')
    parser.add_argument('--out',type=Path,default=ROOT/'manuscript/figures')
    args=parser.parse_args()
    summary=json.loads(args.summary.read_text())
    outcome_figure(summary,args.out)
    paired_figure(summary,args.out)
    sensitivity_figure(summary,args.out)
    provenance=dict(summary_file=str(args.summary.relative_to(ROOT)) if args.summary.is_relative_to(ROOT) else str(args.summary),
        summary_sha256=hashlib.sha256(args.summary.read_bytes()).hexdigest(),
        figure_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        svg_text='editable text; no embedded raster images', figure_formats=['svg','pdf'])
    if args.trace.exists():
        data=json.loads(args.trace.read_text())
        trace_figure(data,args.out)
        provenance['trace_sha256']=hashlib.sha256(args.trace.read_bytes()).hexdigest()
        provenance['trace_selection']=data['selection']
    (args.out/'figure_provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    print(json.dumps(dict(output=str(args.out),figures=sorted(path.name for path in args.out.glob('*.svg')))))


if __name__=='__main__':
    main()
