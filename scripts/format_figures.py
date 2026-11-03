#!/usr/bin/env python3
"""Render the frozen evidence plots with uniform, eight-point serif typography.

The historical plot and analysis scripts are retained byte-for-byte because the
retry manifest includes their hashes. This presentation-only wrapper reuses
their plot functions and data without changing any numerical calculation.
"""
import argparse
import hashlib
import json
from pathlib import Path

from matplotlib import font_manager
from matplotlib.text import Text

import make_figures as figures


ROOT = Path(__file__).resolve().parents[1]


def select_font():
    for family in ('Times New Roman', 'Nimbus Roman'):
        try:
            path = font_manager.findfont(family, fallback_to_default=False)
            # The inherited Type-42 PDF backend requires TrueType outlines.
            # A CFF-flavored Nimbus OTF creates font-type mismatch warnings.
            if Path(path).suffix.lower() in ('.ttf', '.ttc'):
                return family, path
        except ValueError:
            continue
    raise RuntimeError('Install a TrueType Times New Roman or Nimbus Roman font; CFF/OTF fonts are not supported here.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, default=ROOT/'results/analysis/summary.json')
    parser.add_argument('--retry-summary', type=Path, default=ROOT/'results/retry_analysis/summary.json')
    parser.add_argument('--trace', type=Path, default=ROOT/'results/representative_trace.json')
    parser.add_argument('--out', type=Path, default=ROOT/'manuscript/figures')
    args = parser.parse_args()
    family, font_path = select_font()
    figures.plt.rcParams.update({'font.family': family, 'font.size': 8,
        'axes.labelsize': 8, 'axes.titlesize': 8, 'xtick.labelsize': 8,
        'ytick.labelsize': 8, 'legend.fontsize': 8})
    original_save = figures.save

    def save_with_typography(figure, name, destination):
        for artist in figure.findobj(match=Text):
            artist.set_fontfamily(family)
            artist.set_fontsize(8)
        original_save(figure, name, destination)
        svg = destination/f'{name}.svg'
        svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')

    figures.save = save_with_typography
    summary = json.loads(args.summary.read_text())
    retry = json.loads(args.retry_summary.read_text())
    trace = json.loads(args.trace.read_text())
    figures.outcome_figure(summary, args.out)
    figures.paired_figure(summary, args.out)
    figures.sensitivity_figure(summary, args.out)
    figures.trace_figure(trace, args.out)
    values = retry['comparisons']['P_retry_minus_B3_retry']
    patterns = ('T1', 'T2', 'T3', 'T4')
    effects = [values['by_pattern'][p] for p in patterns] + [values['overall']]
    labels = [figures.PATTERN_LABELS[p] for p in patterns] + ['All impaired cases']
    figure, axis = figures.plt.subplots(figsize=(3.5, 1.95))
    figures.forest(axis, effects, labels, [figures.COLORS['P']]*4 + ['#292929'])
    axis.set_xlabel('P retry − B3 retry energy (kWh)')
    axis.axhline(3.5, color='#b9c0c7', linewidth=.5)
    figure.subplots_adjust(left=.40, right=.97, bottom=.26, top=.95)
    figures.save(figure, 'retry_effects', args.out)
    paths = {'primary_summary': args.summary, 'retry_summary': args.retry_summary,
             'trace': args.trace, 'frozen_plot_source': Path(figures.__file__),
             'presentation_source': Path(__file__)}
    provenance = {'purpose': 'Presentation-only revision after primary and retry analyses',
        'font_family': family, 'font_file_basename': Path(font_path).name,
        'font_size_pt': 8, 'numerical_inputs_sha256': {
            key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items()},
        'svg_text': 'editable text; no embedded raster images',
        'figure_formats': ['svg', 'pdf'], 'trace_selection': trace['selection']}
    (args.out/'figure_provenance.json').write_text(json.dumps(provenance, indent=2)+'\n')
    print(json.dumps({'output': str(args.out), 'font_family': family, 'font_size_pt': 8,
        'figures': sorted(p.name for p in args.out.glob('*.svg'))}))


if __name__ == '__main__':
    main()
