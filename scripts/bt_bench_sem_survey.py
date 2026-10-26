#!/usr/bin/env python3
"""Every bench recording's level spread on record, against the F + rate amendment's ``SNR_MAX_SEM_DB`` (2026-10-26).

    python -B scripts/bt_bench_sem_survey.py /home/user/bench-raw /media/user/4TB/sdr-synth-tmp/bench

Walks the folders for ``NAME.snr.json`` (``bt_bench.snr``'s result, whose ``snr_db_sem`` is the spread of the median
level over the shot's ``SNR_PARTS`` parts), keeps one of each set of identical files (a recording's copy on the drive
is the same bytes), and prints one JSON row per recording, sorted by path: the file and its sha256, the snr's own
problem, ``snr_db_sem``, whether ``bt_bench_session.spread_refusal`` refuses it, the aligner's verdict beside it when
there is one (``problem``, bursts found of bursts), and the "Sample Overrun" warnings in its ``NAME.rec.log`` when
there is one. Then the largest sem among the rows not refused, and the rows refused. It reads only those three files of
each recording: no carrier value, no truth.

A row's ``snr_db_sem`` is recomputed from its recording with ``python scripts/bt_bench.py snr CAPTURE SHOT --out DIR``
(``bt_bench.snr``, unchanged since ad81b63).
"""
import glob
import hashlib
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import bt_bench_session as bs  # noqa: E402


def _sem(v):
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


def survey(roots):
    seen = {}
    for root in roots:
        for p in glob.glob(os.path.join(root, '**', '*.snr.json'), recursive=True):
            seen.setdefault(hashlib.sha256(open(p, 'rb').read()).hexdigest(), []).append(p)
    rows = []
    for h, paths in seen.items():
        paths = sorted(paths)
        snr = json.load(open(paths[0]))
        row = dict(snr_json=paths[0], copies=len(paths), sha256=h, problem=snr.get('problem'),
                   snr_db_sem=_sem(snr.get('snr_db_sem')), sem_is_number=_sem(snr.get('snr_db_sem')) is not None,
                   refused=bool(bs.spread_refusal(snr).get('problem')), align=None, overruns=None)
        for p in paths:
            base = p[:-len('.snr.json')]
            if row['align'] is None and os.path.exists(base + '.align.json'):
                al = json.load(open(base + '.align.json'))
                row['align'] = dict(problem=al.get('problem'), found=al.get('n_found'), bursts=al.get('n_bursts'))
            if row['overruns'] is None and os.path.exists(base + '.rec.log'):
                row['overruns'] = open(base + '.rec.log', 'rb').read().count(b'Sample Overrun')
        rows.append(row)
    return sorted(rows, key=lambda r: r['snr_json'])


def main(argv=None):
    roots = (argv if argv is not None else sys.argv[1:]) or ['/home/user/bench-raw',
                                                             '/media/user/4TB/sdr-synth-tmp/bench']
    rows = survey(roots)
    for r in rows:
        print(json.dumps(r))
    kept = [r for r in rows if not r['refused']]
    refused = [r for r in rows if r['refused']]
    print('%d recordings; threshold %.2f dB; not refused %d, largest sem %.4f (%s); refused %d: %s' % (
        len(rows), bs.SNR_MAX_SEM_DB, len(kept), max(r['snr_db_sem'] for r in kept),
        max(kept, key=lambda r: r['snr_db_sem'])['snr_json'], len(refused),
        ', '.join('%s (%s)' % (os.path.basename(r['snr_json']), 'none' if r['snr_db_sem'] is None
                                else '%.3f' % r['snr_db_sem']) for r in refused)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
