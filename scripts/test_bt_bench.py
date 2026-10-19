#!/usr/bin/env python3
"""Hold the bench tier to planted captures, with no radio.

    python scripts/test_bt_bench.py

* **The shot** is the fresh-set file without its noise: shot / scale + the file's noise blocks equals the file's own
  render over the same samples; the sha256 is the file's; every burst marked in_shot is whole inside it; a shot
  started at ``first`` has every index moved by it.
* **The aligner**, on short shots (0.4 s from 3.5 s into the file, IDs and connection packets) made into captures by
  ``make_capture``:
  - against itself: every burst, the delay exact, 0 ppm, 0 Hz;
  - with a 0.37 sample shift, -0.37 ppm, +57.1 kHz on a VSG offset of 58 kHz and noise at 20 dB in 1 MHz: the clock,
    the reference error and every truth start against the planted ones;
  - refused: a 40-sample drop, a 1-sample drop, lost samples in the recorder, a capture shorter than the shot, noise
    alone, a carrier 58 kHz from where --tx puts it;
  - the command line: a --tx not sent or of another shot refused; a dry run writes the cropped capture and its truth,
    whose starts are the planted ones in the cropped file's samples.
* **The player**, with a fake ``apps.vsg_sink``: --check and every refusal touch no radio (a level over 0 or nan, a pad
  missing, negative or nan, more than -20 dBm into the BB60D (level minus pad), an offset over 200 kHz, a shot whose sha256 is not its sidecar's); --go sends the exact
  buffer once (never repeat_waveform) at 2439.0 MHz plus the offset and the level given; the sidecar is "not started"
  at the open, "pending" at the send and sent true after, "error" when the send raises, and the sink is always stopped.
"""
import contextlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import time
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import bt_bench as bb  # noqa: E402
from scripts import bt_bench_play as play  # noqa: E402
from scripts import bt_synth_slotb as sb, bt_synth_slotb2 as g  # noqa: E402
from scripts.bt_synth_interf import amplitude_for, noise_block  # noqa: E402

failures = []
FIRST = 140_000_000
N_SHORT = 16_000_000
DELIVERED = '/media/user/4TB/sdr-synth-tmp/freshset/synth_slotb2_F_885614_d23_n3004.cf32'


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what), flush=True)
    if not ok:
        failures.append(what)


def run(fn, argv):
    """``fn(argv)``, returning ``(code, stdout, stderr)``; a SystemExit is a code."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = fn(argv) or 0
        except SystemExit as e:
            code = e.code if e.code is not None else 0
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------------------------------- the shot

def test_shot(tmp):
    print('\nThe shot: the fresh-set file without its noise')
    n = 4_000_000
    iq_path, side_path = bb.make_shot('885614', os.path.join(tmp, 's0'), n_samples=n)
    side = json.load(open(side_path))
    info = side['bench_shot']
    shot = np.fromfile(iq_path, dtype='<c8')
    plan = g.plan_file(bb.SHOTS['885614'])
    # A block's noise is drawn whole (every real part, then every imaginary part), so blocks are made at their full
    # length and cut, never made short.
    noise = np.concatenate([noise_block(plan['noise_seed'], k, min(sb.NOISE_BLOCK, plan['total'] - k * sb.NOISE_BLOCK),
                                        bb.FS) for k in range((n - 1) // sb.NOISE_BLOCK + 1)])[:n].astype(np.complex128)
    back = shot.astype(np.complex128) / info['scale'] + noise
    if os.path.exists(DELIVERED):
        file_iq, where = np.fromfile(DELIVERED, dtype='<c8', count=n).astype(np.complex128), 'the delivered file'
    else:
        file_iq, where = sb.render_range(plan, 0, n, noise=True).astype(np.complex128), 'a render (no drive)'
    err = float(np.max(np.abs(back - file_iq))) / amplitude_for(20.0)
    check(len(shot) == n and info['n_samples'] == n and err < 1e-5,
          'shot / scale + the noise blocks of its seed = %s over %d samples (worst %.1e of a burst)' % (where, n, err))
    check(info['scale'] == 1.0 / amplitude_for(side['snr_db']) and abs(info['peak_abs_iq'] - 1.0) < 1e-5,
          'scale 1/amplitude_for(20), peak |iq| %.6f' % info['peak_abs_iq'])
    check(info['sha256'] == play.sha256_of(iq_path), 'bench_shot.sha256 is the file\'s')
    sps = side['samples_per_symbol']
    ins = [e for e in side['bursts'] if e['in_shot']]
    whole = all(e['rendered'] and e['start_sample'] >= bb.PAD and
                e['start_sample'] + e['air_bits_length'] * sps + bb.PAD <= n for e in ins)
    late = [e for e in side['bursts'] if e['rendered'] and e['start_sample'] + e['air_bits_length'] * sps + bb.PAD <= n
            and e['start_sample'] >= bb.PAD and not e['in_shot']]
    check(len(ins) > 0 and whole and not late, '%d bursts in_shot, each whole inside it, none whole left out' % len(ins))
    check(info['source_stem'] == 'F_885614_d23_n3004' and info['first_source_sample'] == 0
          and side['drift_ppm_signed'] == 23.0, 'source F_885614_d23_n3004 from sample 0, +23 ppm drift')
    p2, s2 = bb.make_shot('885614', os.path.join(tmp, 's1'), n_samples=n, first=FIRST)
    side2 = json.load(open(s2))
    a = np.fromfile(p2, dtype='<c8')
    want = (sb.render_range(plan, FIRST, FIRST + n, noise=False) * info['scale']).astype(np.complex64)
    by0 = {e['index']: e for e in side['bursts']}
    moved = all(e['start_sample'] == by0[e['index']]['start_sample'] - FIRST for e in side2['bursts'])
    check(np.max(np.abs(a - want)) < 1e-6 and moved and side2['bench_shot']['first_source_sample'] == FIRST,
          'a shot from sample %d is the file\'s samples there, every start moved by it' % FIRST)


# ---------------------------------------------------------------------------------------------------- the aligner

def short_shot(tmp):
    iq_path, side_path = bb.make_shot('885614', os.path.join(tmp, 'short'), n_samples=N_SHORT, first=FIRST)
    return iq_path, json.load(open(side_path))


def capture(tmp, shot_path, name, **kw):
    path = os.path.join(tmp, name + '.cf32')
    kw.setdefault('pre', 0.1)
    kw.setdefault('post', 0.1)
    planted = bb.make_capture(shot_path, path, **kw)
    return path, planted


def tx_for(planted):
    return {'vsg_center_mhz': bb.CENTER_MHZ + planted['vsg_offset_hz'] / 1e6, 'dry_run': True}


def truth_errors(side, result, planted):
    """Every in-shot burst's truth start minus the planted one, in capture samples."""
    by = {e['index']: e for e in side['bursts']}
    errs = []
    for k, row in result['rows'].items():
        x = by[k]['start_sample'] + by[k]['timing_frac']
        truth = x + result['delay_at_0'] + result['ppm'] * 1e-6 * x
        errs.append(truth - (planted['delay_at_0'] + x * (1 + planted['ppm'] * 1e-6)))
    return np.array(errs)


def test_align(tmp):
    print('\nThe aligner on planted captures')
    shot_path, side = short_shot(tmp)
    roles = {e['role'] for e in side['bursts'] if e['in_shot']}
    n_in = sum(e['in_shot'] for e in side['bursts'])
    check({'id_page', 'master', 'slave'} <= roles, 'the short shot holds %d bursts: IDs, masters and slaves' % n_in)

    cap, pl = capture(tmp, shot_path, 'self')
    r = bb.align(cap, shot_path, side, tx=tx_for(pl))
    check(r['problem'] is None and r['n_found'] == n_in and abs(r['delay_at_0'] - pl['delay_at_0']) < 1e-3
          and abs(r['ppm']) < 1e-3 and abs(r['cfo_instrument_hz']) < 1.0,
          'against itself: %s' % bb.summary(r)[:110])

    for frac in (0.25, 0.5):
        cap, pl = capture(tmp, shot_path, 'frac', frac=frac)
        r = bb.align(cap, shot_path, side, tx=tx_for(pl))
        err = [row['delay'] - pl['delay_at_0'] for row in r.get('rows', {}).values()]
        check(r['problem'] is None and max(abs(x) for x in err) < 0.005,
              'no noise, shifted %.2f sample: every burst\'s own reading within %.4f (the estimator\'s bias; the '
              'capture maker\'s sinc is under 0.002)' % (frac, max(abs(x) for x in err) if err else -1))
    # With noise the line through 222 bursts is good to about 0.05 sample; a full shot's 5000 to about 0.01.
    kw = dict(frac=0.37, ppm=-0.37, cfo_hz=57100.0, vsg_offset_hz=58000.0, snr_db=20.0)
    cap, pl = capture(tmp, shot_path, 'imp', **kw)
    r = bb.align(cap, shot_path, side, tx=tx_for(pl))
    ok = r['problem'] is None
    check(ok, 'shift 0.37, -0.37 ppm, +57.1 kHz on a 58 kHz VSG offset, 20 dB: %s' % bb.summary(r)[:150])
    if ok:
        check(abs(r['ppm'] + 0.37) < 0.02 and abs(r['delay_at_0'] - pl['delay_at_0']) < 0.05,
              'the clock: %+.4f ppm (planted -0.37), delay at 0 off by %+.3f sample'
              % (r['ppm'], r['delay_at_0'] - pl['delay_at_0']))
        check(abs(r['reference_error_hz'] + 900.0) < 100 and abs(r['cfo_instrument_hz'] - 57100.0) < 100,
              'the carrier: instruments %+.0f Hz (planted 57100), reference error %+.0f Hz (planted -900)'
              % (r['cfo_instrument_hz'], r['reference_error_hz']))
        errs = truth_errors(side, r, pl)
        check(np.max(np.abs(errs)) < 0.1 and r['n_found'] == n_in,
              'every truth start within %.3f sample of the planted (%d of %d found)'
              % (np.max(np.abs(errs)), r['n_found'], n_in))

    for drop, words in ((40, ('lost or gained',)), (1, ('step',))):
        cap, pl = capture(tmp, shot_path, 'drop%d' % drop, drop_at=int(0.1 * bb.FS) + N_SHORT // 2, drop=drop, **kw)
        r = bb.align(cap, shot_path, side, tx=tx_for(pl))
        check(r['problem'] is not None and any(w in r['problem'] for w in words),
              '%d sample(s) dropped mid-shot is refused: %s' % (drop, r['problem']))

    ins = [e for e in side['bursts'] if e['in_shot']]
    pre = int(0.1 * bb.FS)

    def at(e, back=0):
        return pre + e['start_sample'] - back

    for what, drop_at, drop in (('10 samples before the last burst', at(ins[-1], 200), 10),
                                ('400 samples before the last 8 bursts', at(ins[-8], 200), 400),
                                ('10 samples after the first burst', at(ins[1], 200), 10),
                                ('3 samples before the 30th burst from the end', at(ins[-30], 200), 3)):
        cap, pl = capture(tmp, shot_path, 'edge', drop_at=drop_at, drop=drop, **kw)
        r = bb.align(cap, shot_path, side, tx=tx_for(pl))
        check(r['problem'] is not None and ('off the clock line' in r['problem'] or 'step' in r['problem']),
              'a loss of %s is refused: %s' % (what, r['problem']))
    cap, pl = capture(tmp, shot_path, 'bend', bend=1.0, **kw)
    r = bb.align(cap, shot_path, side, tx=tx_for(pl))
    check(r['problem'] is not None and ('bends' in r['problem'] or 'step' in r['problem']),
          'a bend of 1 sample is refused (on a short shot the step test may see it first): %s' % r['problem'])
    cap, pl = capture(tmp, shot_path, 'drop40', drop_at=int(0.1 * bb.FS) + N_SHORT // 2, drop=40, **kw)
    r = bb.align(cap, shot_path, side, tx=tx_for(pl), rec={'lost_sample_overflows': 2})
    check(r['problem'] is not None and 'lost samples' in r['problem'], 'lost samples in the recorder: refused')
    short = os.path.join(tmp, 'short.cf32')
    with open(short, 'wb') as f:
        bb.Cf32(shot_path).read(0, N_SHORT // 2).tofile(f)
    r = bb.align(short, shot_path, side, tx=tx_for(pl))
    check(r['problem'] is not None and 'shorter' in r['problem'], 'a capture shorter than the shot: refused')
    noise = os.path.join(tmp, 'noise.cf32')
    rng = np.random.default_rng(3)
    nn = N_SHORT + int(0.2 * bb.FS)
    (0.3 * (rng.standard_normal(nn) + 1j * rng.standard_normal(nn))).astype('<c8').tofile(noise)
    r = bb.align(noise, shot_path, side, tx=tx_for(pl))
    check(r['problem'] is not None and 'not found' in r['problem'], 'noise alone: %s' % r['problem'])
    cap, pl = capture(tmp, shot_path, 'wrongf', cfo_hz=58000.0, vsg_offset_hz=0.0, snr_db=20.0)
    r = bb.align(cap, shot_path, side, tx=tx_for(pl))
    check(r['problem'] is not None, 'a carrier 58 kHz from where --tx puts it: refused (%s)' % r['problem'])
    return shot_path, side


def test_align_cli(tmp, shot_path, side):
    print('\nThe aligner\'s command line')
    kw = dict(frac=0.37, ppm=-0.37, cfo_hz=57100.0, vsg_offset_hz=58000.0, snr_db=20.0)
    cap = os.path.join(tmp, 'cli.cf32')
    code, text, err = run(bb.main, ['dryrun', shot_path, '--out', cap, '--frac', '0.37', '--ppm', '-0.37',
                                    '--cfo-hz', '57100', '--vsg-offset-hz', '58000', '--snr-db', '20'])
    planted = json.load(open(cap + '.planted.json'))
    check(code == 0 and planted['vsg_offset_hz'] == 58000.0, 'dryrun writes the capture and its planted truth')
    out = os.path.join(tmp, 'aligned')
    tx = dict(shot_sha256=side['bench_shot']['sha256'], sent=False, state='not started', vsg_center_mhz=2439.058)
    txp = os.path.join(tmp, 'tx.json')
    json.dump(tx, open(txp, 'w'))
    code, _, err = run(bb.main, ['align', cap, shot_path, '--tx', txp, '--out', out])
    check(code == 2 and 'does not say the shot was sent' in err, 'a --tx not sent is refused')
    tx.update(sent=True, state='sent', shot_sha256='0' * 64)
    json.dump(tx, open(txp, 'w'))
    code, _, err = run(bb.main, ['align', cap, shot_path, '--tx', txp, '--out', out])
    check(code == 2 and 'another shot' in err, 'a --tx of another shot is refused')
    code, _, err = run(bb.main, ['align', cap, shot_path, '--out', out])
    check(code == 2 and '--dry' in err, 'neither --tx nor --dry is refused')
    tx.update(shot_sha256=side['bench_shot']['sha256'])
    json.dump(tx, open(txp, 'w'))
    code, _, err = run(bb.main, ['align', cap, shot_path, '--tx', txp, '--out', out])
    check(code == 2 and 'needs --rec' in err, 'a --tx without --rec is refused')
    recp = os.path.join(tmp, 'rec.json')
    json.dump({'file': 'other.cf32', 'lost_sample_overflows': 0}, open(recp, 'w'))
    code, _, err = run(bb.main, ['align', cap, shot_path, '--tx', txp, '--rec', recp, '--out', out])
    check(code == 2 and 'not of this capture' in err, 'a --rec of another file is refused')
    code, text, err = run(bb.main, ['align', cap, shot_path, '--dry', cap + '.planted.json', '--out', out,
                                    '--name', 'dry1'])
    check(code == 0 and os.path.exists(os.path.join(out, 'dry1.cf32')), 'a dry run aligns: %s' % text.split('\n')[0][:120])
    if code != 0:
        return
    code, _, err = run(bb.main, ['align', cap, shot_path, '--dry', cap + '.planted.json', '--out', out, '--name', 'dry1'])
    check(code == 2 and 'already exists' in err, 'a second align to the same name is refused, the first truth kept')
    truth = json.load(open(os.path.join(out, 'dry1.json')))
    check(all(truth['bursts'][0].get(k) is None for k in bb.STALE_BURST_KEYS) and 'noise_seed' not in truth
          and abs(truth['bursts'][0]['start_us'] * 40 - truth['bursts'][0]['start_exact']) < 1e-6,
          'the source file\'s grid and noise keys are gone; start_us is the capture\'s')
    crop0 = truth['capture_crop_first_sample']
    cropped = bb.Cf32(os.path.join(out, 'dry1.cf32'))
    whole = bb.Cf32(cap)
    same = np.array_equal(cropped.read(0, 1000), whole.read(crop0, crop0 + 1000))
    by = {e['index']: e for e in side['bursts']}
    errs = []
    for e in truth['bursts']:
        x = by[e['index']]['start_sample'] + by[e['index']]['timing_frac']
        errs.append(e['start_sample'] + e['timing_frac'] - (planted['delay_at_0'] + x * (1 + planted['ppm'] * 1e-6) - crop0))
    errs = np.array(errs)
    check(same and len(cropped) == truth['n_samples'] and truth['dry_run'] is True,
          'the cropped capture is the recording from sample %d, n_samples %d, dry_run true' % (crop0, truth['n_samples']))
    check(np.max(np.abs(errs)) < 0.1 and all(0 <= e['timing_frac'] < 1 for e in truth['bursts'])
          and all(e['symbol_phase'] == e['start_sample'] % 40 for e in truth['bursts']),
          'every truth start (cropped samples) within %.3f sample of the planted; timing_frac in [0, 1), symbol_phase '
          '= start_sample mod 40' % np.max(np.abs(errs)))
    check(abs(truth['clock']['ppm'] + 0.37) < 0.05 and abs(truth['cfo']['reference_error_hz'] + 900) < 100
          and all(e['cfo_planted_hz'] is not None and e['carrier_phase_rad'] is None for e in truth['bursts']),
          'the truth\'s clock %+.4f ppm and reference error %+.0f Hz; planted carriers kept, phases null'
          % (truth['clock']['ppm'], truth['cfo']['reference_error_hz']))


# ---------------------------------------------------------------------------------------------------- the player

class FakeSink:
    log = []
    raise_on_send = False
    side_path = None

    def __init__(self, center_freq=None, sample_rate=None, level_dbm=None):
        FakeSink.log.append(('open', center_freq, sample_rate, level_dbm, FakeSink.read()))
        self.stopped = False

    @staticmethod
    def read():
        try:
            return json.load(open(FakeSink.side_path))
        except (OSError, ValueError):
            return None

    def get_serial(self):
        return 12345

    def send_waveform(self, iq):
        FakeSink.log.append(('send', iq.copy(), FakeSink.read()))
        if FakeSink.raise_on_send:
            raise RuntimeError('USB gone')

    def repeat_waveform(self, iq):
        FakeSink.log.append(('repeat',))

    def stop_waveform(self):
        FakeSink.log.append(('stop_waveform',))

    def stop(self):
        FakeSink.log.append(('stop',))


class Guard:
    """Refuses, and records, an import of the real ``apps.vsg_sink``."""

    def __init__(self):
        self.imported = False

    def find_spec(self, fullname, path, target=None):
        if fullname == 'apps.vsg_sink':
            self.imported = True
            raise ImportError('test refused the real vsg_sink')
        return None


def test_player(tmp, shot_path):
    print('\nThe player, with no radio')
    guard = Guard()
    sys.modules.pop('apps.vsg_sink', None)
    sys.meta_path.insert(0, guard)
    out = os.path.join(tmp, 'tx')
    try:
        code, text, _ = run(play.main, [shot_path, '--check', '--out', out, '--vsg-offset-khz', '58'])
        side = json.load(open(os.path.join(out, 'bench_885614_d23_check.json')))
        check(code == 0 and 'OK' in text and side['sent'] is False and 'check' in side['state']
              and side['vsg_center_mhz'] == 2439.058 and not guard.imported, '--check: the shot is OK, no radio')
        for argv, what in (([], 'neither --go nor --check sends nothing'),
                           (['--go'], '--go without --pad-db'),
                           (['--go', '--pad-db', '0', '--level', '-19'], '-19 dBm with no pad (-19 at the BB60D)'),
                           (['--go', '--pad-db', '10', '--level', '-5'], '-5 dBm through 10 dB (-15 at the BB60D)'),
                           (['--go', '--pad-db', '-3', '--level', '-60'], 'a negative pad'),
                           (['--go', '--pad-db', '41', '--level', '-60'], 'a 41 dB pad (over 40)'),
                           (['--go', '--pad-db', '0', '--level', '-19.9'], '-19.9 dBm with no pad'),
                           (['--check', '--pad-db', '0', '--level', '-10'], '--check of -10 dBm with no pad'),
                           (['--go', '--pad-db', 'nan'], 'a nan pad'),
                           (['--go', '--pad-db', '30', '--level', '1'], 'a level of +1 dBm'),
                           (['--go', '--pad-db', '30', '--level', 'nan'], 'a nan level'),
                           (['--go', '--pad-db', '30', '--vsg-offset-khz', '500'], 'a 500 kHz offset')):
            code, text, err = run(play.main, [shot_path, '--out', out] + argv)
            want = 0 if not argv else 2
            check(code == want and not guard.imported, '%s: refused before any radio (%s)' % (
                what, (err.strip().split('\n')[-1] or text.strip())[:90]))
        bad = os.path.join(tmp, 'bad')
        os.makedirs(bad, exist_ok=True)
        bad_shot = os.path.join(bad, os.path.basename(shot_path))
        shutil.copy(shot_path, bad_shot)
        shutil.copy(os.path.splitext(shot_path)[0] + '.json', bad)
        with open(bad_shot, 'r+b') as f:
            f.seek(8 * 1000)
            f.write(np.array([0.5 + 0.5j], dtype='<c8').tobytes())
        code, _, err = run(play.main, [bad_shot, '--go', '--pad-db', '30', '--out', out])
        check(code == 2 and 'sha256' in err and not guard.imported, 'a shot whose sha256 is not its sidecar\'s: refused')
    finally:
        sys.meta_path.remove(guard)

    fake = types.ModuleType('apps.vsg_sink')
    fake.vsg_sink = FakeSink
    sys.modules['apps.vsg_sink'] = fake
    import apps
    old = getattr(apps, 'vsg_sink', None)
    apps.vsg_sink = fake
    try:
        FakeSink.log = []
        FakeSink.side_path = os.path.join(out, 'go1_tx.json')
        code, text, err = run(play.main, [shot_path, '--go', '--pad-db', '30', '--level', '-40',
                                          '--vsg-offset-khz', '-58', '--out', out, '--name', 'go1'])
        side = json.load(open(FakeSink.side_path))
        shot = np.fromfile(shot_path, dtype='<c8')
        opens = [e for e in FakeSink.log if e[0] == 'open']
        sends = [e for e in FakeSink.log if e[0] == 'send']
        check(code == 0 and len(opens) == 1 and abs(opens[0][1] - 2438.942e6) < 1 and opens[0][2] == 40e6
              and opens[0][3] == -40.0, '--go opens the VSG60A at 2438.942 MHz, 40 MS/s, -40 dBm')
        check(len(sends) == 1 and np.array_equal(sends[0][1], shot) and ('repeat',) not in FakeSink.log,
              'the exact shot is sent once, never repeated')
        check(opens[0][4]['state'] == 'not started' and sends[0][2]['sent'] == 'pending' and side['sent'] is True
              and side['state'] == 'sent' and side['pad_db'] == 30 and side['level_at_receiver_dbm'] == -70
              and side['shot_sha256'] == json.load(open(os.path.splitext(shot_path)[0] + '.json'))['bench_shot']['sha256'],
              'the sidecar: not started at the open, pending at the send, sent after; pad 30, -70 dBm at the BB60D')
        k = play.kill_after(len(shot) / play.FS)
        check(FakeSink.log[-2:] == [('stop_waveform',), ('stop',)] and 'timeout -k %d' % k in text
              and play.kill_after(10.0) == 25, 'the sink is stopped, and timeout -k %d is advised for this shot '
              '(25 for a 10 s shot)' % k)
        FakeSink.log = []
        FakeSink.side_path = os.path.join(out, 'go0_tx.json')
        code, _, err = run(play.main, [shot_path, '--go', '--pad-db', '0', '--level', '-20', '--out', out, '--name', 'go0'])
        side0 = json.load(open(FakeSink.side_path))
        check(code == 0 and side0['sent'] is True and side0['pad_db'] == 0 and side0['level_at_receiver_dbm'] == -20,
              'no pad at -20 dBm (the limit at the BB60D) plays, and says pad 0, -20 dBm at the BB60D')
        code, _, err = run(play.main, [shot_path, '--go', '--pad-db', '30', '--out', out, '--name', 'go1'])
        check(code == 2 and 'already exists' in err and not [e for e in FakeSink.log if e[0] == 'open'][1:],
              'a second --go under the same name is refused before the radio; the first sidecar stands')
        FakeSink.log = []
        FakeSink.raise_on_send = True
        FakeSink.side_path = os.path.join(out, 'go2_tx.json')
        try:
            code, text, err = run(play.main, [shot_path, '--go', '--pad-db', '30', '--out', out, '--name', 'go2'])
        except RuntimeError:
            code = 'raised'
        side = json.load(open(FakeSink.side_path))
        check(code == 'raised' and side['sent'] == 'error' and 'USB gone' in side['error']
              and FakeSink.log[-2:] == [('stop_waveform',), ('stop',)],
              'a send that raises: sent "error", the error kept, the sink stopped, the error raised')
    finally:
        FakeSink.raise_on_send = False
        sys.modules.pop('apps.vsg_sink', None)
        if old is None:
            delattr(apps, 'vsg_sink')
        else:
            apps.vsg_sink = old


def main():
    t0 = time.time()
    tmp = tempfile.mkdtemp(prefix='test_bt_bench_')
    try:
        test_shot(tmp)
        shot_path, side = test_align(tmp)
        test_align_cli(tmp, shot_path, side)
        test_player(tmp, shot_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n%.0f s' % (time.time() - t0))
    for w in failures:
        print('FAILED:', w)
    print('RESULT: PASS' if not failures else 'RESULT: FAIL (%d)' % len(failures))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
