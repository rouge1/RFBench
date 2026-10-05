#!/usr/bin/env python3
"""Hold the hopping transmitter to a shot it built itself, with no radio.

    python scripts/test_bt_tx_hop.py

``scripts/bt_tx_hop.py`` builds one hopping train and, only with ``--go``,
plays it once. The radio is a fake module put in ``sys.modules``. What is
checked:

* a noiseless amplitude is constant inside every burst and zero outside,
  and the defaults still add noise;
* the dry-run file is the sidecar's ``shot_samples``, on the channel each
  burst names, at unit magnitude, and those channels are ``bt_hop``'s;
* ``--go`` sends that buffer once (never ``repeat_waveform``), at the centre,
  rate and level it was given, and stops even when the send raises or the
  terminal is gone; nothing is imported without ``--go``;
* the shot is built at ``--center-mhz`` and ``--clk0``, not at the defaults,
  at amplitude 1.0, and its sidecar says what the level and centre were;
* the sidecar is on disk before the radio is opened ("not started") and
  before the send ("pending"), and ends as sent, not sent or error, so a
  failed run never leaves an old dry run's file standing for it;
* a level that is not a finite number in (-200, 0], and a shot longer than
  15 s, are refused first, and ``timeout -k`` advice is printed;
* a map change uses both maps, and the same arguments give the same samples.
"""
import errno
import io
import json
import math
import os
import re
import sys
import tempfile
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_hop  # noqa: E402
from scripts import bt_synth, bt_synth_hop as hop  # noqa: E402
from scripts import bt_tx_hop as tx  # noqa: E402

failures = []


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    if not ok:
        failures.append(what)


def run(argv):
    """``main`` with ``argv``, returning ``(code, stdout, stderr)``. A
    ``SystemExit`` is a code, not a crash of this test."""
    out, err = io.StringIO(), io.StringIO()
    old, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    code = 0
    try:
        try:
            tx.main(argv)
        except SystemExit as e:
            code = e.code if e.code is not None else 0
    finally:
        sys.stdout, sys.stderr = old, old_err
    return code, out.getvalue(), err.getvalue()


class ImportGuard:
    """A meta-path finder that records an import of ``apps.vsg_sink`` and
    refuses to load the real module."""

    def __init__(self):
        self.imported = False

    def find_spec(self, fullname, path, target=None):
        if fullname == 'apps.vsg_sink':
            self.imported = True
            raise ImportError('test refused to import the real vsg_sink')
        return None


def drop_vsg():
    sys.modules.pop('apps.vsg_sink', None)


def burst_support(entry, fs):
    """The samples ``gfsk`` wrote for one burst, and the bit samples inside
    them, which are past the raised-cosine ramps."""
    bits = [int(c) for c in entry['air_bits']]
    wave, lead = br.gfsk(bits, fs)
    lo = entry['start_sample'] - lead
    sps = int(round(fs / br.SYMBOL_RATE))
    return lo, lo + len(wave), entry['start_sample'], entry['start_sample'] + len(bits) * sps


def check_amplitude():
    print('\nNo noise at amplitude A, and the defaults still add noise')
    # The ramps are 2 us at each end of the burst, so |iq| is A on the bits
    # themselves and exactly 0 off the burst. The ramps are the edge.
    A = 0.7
    iq, side = hop.synthesise_afh([(0, hop.MAP_A)], 'DH5', bursts=4, seed=4,
                                  snr_db=None, amplitude=A)
    check(side['snr_db'] is None, 'snr_db None is null in the sidecar')
    fs = side['sample_rate']
    cover = np.zeros(len(iq), dtype=bool)
    interior = np.zeros(len(iq), dtype=bool)
    for entry in side['bursts']:
        lo, hi, a, b = burst_support(entry, fs)
        cover[lo:hi] = True
        interior[a:b] = True
    mag = np.abs(iq[interior])
    check(np.max(np.abs(mag - A)) < 1e-4,
          'inside every burst |iq| is %.4f, worst deviation %.2e' % (A, np.max(np.abs(mag - A))))
    check(not np.any(iq[~cover]), 'outside every burst the samples are exactly 0')
    noisy, noisy_side = hop.synthesise_afh([(0, hop.MAP_A)], 'DH1', bursts=3, seed=4)
    check(noisy_side['snr_db'] == 30.0 and np.any(noisy[:2000]),
          'the defaults still add noise from sample 0, snr_db %.1f' % noisy_side['snr_db'])


def carrier_khz(iq, side, entry):
    """The burst's carrier minus the channel it names, in kHz, with the bits'
    own mean frequency taken off, measured on the raw samples."""
    fs = side['sample_rate']
    sps = int(round(fs / br.SYMBOL_RATE))
    bits = np.array([int(c) for c in entry['air_bits']])
    ref, lead = br.gfsk(bits, fs)

    def mean_hz(x, first, count):
        step = np.angle(x[first + 1:first + count + 1] * np.conj(x[first:first + count]))
        return float(step.mean() * fs / (2 * np.pi))

    mine = mean_hz(iq, entry['start_sample'], 72 * sps)
    clean = mean_hz(ref, lead, 72 * sps)
    return (side['center_mhz'] + (mine - clean) / 1e6 - entry['channel_mhz']) * 1e3


def channels_are_kernel(side):
    address = side['address_for_hop']
    got, want = [], []
    for entry in side['bursts']:
        mask = sum(1 << c for c in side['afh_maps'][entry['afh_map_index']]['channels'])
        got.append(entry['channel'])
        want.append(bt_hop.hop_channel(entry['clk'], address, mask))
    return got, want


def check_dry_run(tmp, guard):
    print('\nA dry run: the file, the carriers, the kernel')
    out = os.path.join(tmp, 'dry')
    code, text, err = run(['--dry-run', '--out', out, '--name', 'hop', '--bursts', '8',
                           '--ptype', 'DH5', '--level', '-20', '--seed', '3'])
    check(code == 0 and not guard.imported, 'dry run writes the shot and does not import vsg_sink')
    iq_path = os.path.join(out, 'synth_hop_txshot.cf32')
    side_path = os.path.join(out, 'synth_hop_tx.json')
    side = json.load(open(side_path))
    size = os.path.getsize(iq_path)
    check(size == side['shot_samples'] * 8,
          'cf32 is %d bytes, shot_samples %d' % (size, side['shot_samples']))
    iq = np.fromfile(iq_path, dtype='<c8')
    check(len(iq) == side['shot_samples'], 'the file has shot_samples complex samples')
    fs = side['sample_rate']
    worst = 0.0
    for entry in side['bursts']:
        _, _, a, b = burst_support(entry, fs)
        worst = max(worst, float(np.max(np.abs(np.abs(iq[a:b]) - 1.0))))
    check(worst < 1e-4, 'every burst has unit magnitude, worst deviation %.2e' % worst)
    errs = [abs(carrier_khz(iq, side, side['bursts'][k])) for k in (0, 2, 4, 7)]
    check(max(errs) < 2.0, 'four bursts sit on their channel, worst %.3f kHz' % max(errs))
    got, want = channels_are_kernel(side)
    check(got == want, 'all %d channels are bt_hop for the map' % len(got))
    check(side['tx'].get('dry_run') is True and side['center_mhz'] == 2441.0
          and side['tx']['vsg_center_mhz'] == 2441.0 and side['afh_map'] == list(range(31, 51))
          and side['sample_rate'] == tx.FS and side['snr_db'] is None
          and 'exact' in side['clk_convention'] and not err.strip(),
          'sidecar: dry_run, 2441.0 MHz, map 31-50, 40 MS/s, no noise, exact clocks')
    check('vsg_sink' not in text and 'nothing sent' not in text, 'the dry run does not pretend to transmit')
    return iq


class FakeSink:
    instances = []
    raise_on_send = False
    raise_on_open = False
    #: The sidecar the run writes, read from disk at the moments that matter.
    side_path = None
    #: What the file on disk said when the sink was opened, and when the
    #: send began.
    at_open = None
    at_send = None
    repeat_called = False
    #: Replaces ``sys.stdout`` once the sink is open: a session that died.
    dead_stdout = None

    def __init__(self, center_freq=None, sample_rate=None, level_dbm=None):
        FakeSink.at_open = FakeSink.read_sidecar()
        if FakeSink.raise_on_open:
            raise RuntimeError('no VSG60A')
        self.center_freq = center_freq
        self.sample_rate = sample_rate
        self.level_dbm = level_dbm
        self.calls = []
        self.sent = None
        FakeSink.instances.append(self)
        if FakeSink.dead_stdout is not None:
            sys.stdout = FakeSink.dead_stdout

    @staticmethod
    def read_sidecar():
        try:
            return json.load(open(FakeSink.side_path))
        except (OSError, ValueError, TypeError):
            return None

    def get_serial(self):
        self.calls.append('serial')
        return 4242

    def send_waveform(self, samples):
        self.calls.append('send')
        FakeSink.at_send = FakeSink.read_sidecar()
        self.sent = np.array(samples, dtype=np.complex64, copy=True)
        if FakeSink.raise_on_send:
            raise RuntimeError('send failed')

    def repeat_waveform(self, samples):
        # The shot is one pass. A loop would restart the channels.
        FakeSink.repeat_called = True
        raise AssertionError('repeat_waveform must never be called')

    def stop_waveform(self):
        self.calls.append('stop_waveform')

    def stop(self):
        self.calls.append('stop')


class DeadStdout:
    """A stdout whose far end has gone: every write raises ``exc``."""

    def __init__(self, exc):
        self.exc = exc

    def write(self, _text):
        raise self.exc

    def flush(self):
        raise self.exc


def install_fake():
    drop_vsg()
    mod = types.ModuleType('apps.vsg_sink')
    mod.vsg_sink = FakeSink
    sys.modules['apps.vsg_sink'] = mod
    FakeSink.instances = []
    FakeSink.raise_on_send = False
    FakeSink.raise_on_open = False
    FakeSink.side_path = None
    FakeSink.at_open = None
    FakeSink.at_send = None
    FakeSink.repeat_called = False
    FakeSink.dead_stdout = None


def go_args(out, name='hop', **extra):
    """The argv of one small --go run, with the sidecar's path told to the fake."""
    FakeSink.side_path = os.path.join(out, 'synth_%s_tx.json' % name)
    argv = ['--go', '--out', out, '--name', name, '--bursts', '8', '--ptype', 'DH5',
            '--level', '-20', '--seed', '3']
    for key, value in extra.items():
        argv += ['--' + key.replace('_', '-'), str(value)]
    return argv


def check_go(tmp, iq_dry):
    print('\n--go sends the shot once, and stops when the send raises')
    install_fake()
    out = os.path.join(tmp, 'go')
    code, text, _ = run(go_args(out))
    check(code == 0 and len(FakeSink.instances) == 1, '--go opens the sink once')
    sink = FakeSink.instances[0]
    check(sink.calls.count('send') == 1 and sink.sent is not None
          and len(sink.sent) == len(iq_dry) and np.array_equal(sink.sent, iq_dry),
          'send_waveform once, with the dry run\'s samples')
    check(not FakeSink.repeat_called and 'repeat_waveform' not in sink.calls,
          'repeat_waveform is never called')
    check(sink.center_freq == tx.CENTER_MHZ * 1e6 and sink.sample_rate == tx.FS
          and sink.level_dbm == -20.0, 'centre %.3f MHz, %.0f MS/s, %.0f dBm' % (
              sink.center_freq / 1e6, sink.sample_rate / 1e6, sink.level_dbm))
    check(sink.calls[-2:] == ['stop_waveform', 'stop'], 'stop_waveform and stop after the send')
    side = json.load(open(os.path.join(out, 'synth_hop_tx.json')))
    check(side['tx']['vsg_serial'] == 4242 and side['tx']['mode'] == 'send_waveform: one shot'
          and side['tx']['tx_stop_unix'] >= side['tx']['tx_start_unix']
          and 'dry_run' not in side['tx'] and side['shot_samples'] == len(iq_dry),
          'the sidecar records the radio, the one-shot mode and the length')
    check(isinstance(side['tx']['tx_start_unix'], float) and side['tx']['tx_start_unix'] > 1.5e9,
          'tx_start_unix is a real time (%r)' % side['tx']['tx_start_unix'])
    check(side['tx']['sent'] is True and side['tx']['state'] == 'sent',
          'tx.sent is true and the state is "sent"')
    check(side['tx']['vsg_level_dbm'] == -20.0 and side['tx']['vsg_center_mhz'] == 2441.0,
          'tx.vsg_level_dbm %r and tx.vsg_center_mhz %r are what the sink was given' % (
              side['tx']['vsg_level_dbm'], side['tx']['vsg_center_mhz']))
    check(not os.path.exists(os.path.join(out, 'synth_hop_txshot.cf32')),
          '--go does not write the cf32')
    check('started' in text, 'it prints when the shot started')

    print('\nThe sidecar is on disk before the radio is opened, and before the send')
    at_open, at_send = FakeSink.at_open, FakeSink.at_send
    check(at_open is not None and at_open['tx']['sent'] is False
          and at_open['tx']['state'] == 'not started' and 'tx_start_unix' not in at_open['tx'],
          'when the sink is opened the file says sent false, "not started" (%r)' % (
              None if at_open is None else at_open['tx'].get('state')))
    check(at_send is not None and at_send['tx']['sent'] == 'pending'
          and at_send['tx']['state'] == 'pending' and at_send['tx']['tx_start_unix'] > 0
          and at_send['tx']['vsg_serial'] == 4242,
          'when the send begins it says "pending", with the start time and the serial (%r)' % (
              None if at_send is None else at_send['tx'].get('sent')))

    print('\nA stale dry run at the same path is replaced even when the open fails')
    install_fake()
    out = os.path.join(tmp, 'stale')
    code, _, _ = run(['--dry-run', '--out', out, '--name', 'hop', '--bursts', '4'])
    stale = json.load(open(os.path.join(out, 'synth_hop_tx.json')))
    check(stale['tx']['dry_run'] is True and stale['tx']['sent'] is False,
          'the dry run says dry_run true, sent false (%r)' % stale['tx'].get('state'))
    FakeSink.raise_on_open = True
    raised = False
    try:
        run(go_args(out) + [])
    except RuntimeError:
        raised = True
    after = json.load(open(os.path.join(out, 'synth_hop_tx.json')))
    check(raised and 'dry_run' not in after['tx'] and after['tx']['sent'] is False
          and after['tx']['state'].startswith('not started')
          and 'no VSG60A' in after['tx'].get('error', '')
          and FakeSink.at_open['tx']['state'] == 'not started',
          'the file at the open said "not started", and after the failed open it names the error')

    print('\nstop runs even when send_waveform raises, and the sidecar says what happened')
    install_fake()
    FakeSink.raise_on_send = True
    out = os.path.join(tmp, 'boom')
    raised = False
    try:
        run(go_args(out, level=-15, center_mhz=2440, bursts=4))
    except RuntimeError:
        raised = True
    sink = FakeSink.instances[0]
    check(raised and sink.calls[-2:] == ['stop_waveform', 'stop']
          and sink.center_freq == 2440e6,
          'the send raised, stop still ran, and the tuning was 2440.0 MHz')
    side = json.load(open(os.path.join(out, 'synth_hop_tx.json')))
    check(side['tx']['sent'] == 'error' and 'send failed' in side['tx']['error']
          and 'error' in side['tx']['state'] and side['tx']['tx_stop_unix'] > 0,
          'after the failed send the sidecar says sent "error": %r' % side['tx'].get('error'))
    check(side['tx']['vsg_level_dbm'] == -15.0 and side['tx']['vsg_center_mhz'] == 2440.0
          and side['center_mhz'] == 2440.0,
          'it keeps level -15 and centre 2440 (tx.vsg_center_mhz %r)' % side['tx']['vsg_center_mhz'])

    print('\nA signal before the send: nothing sent, and the sidecar says so')
    install_fake()
    out = os.path.join(tmp, 'sig')
    argv = go_args(out)
    import signal as _signal
    real_signal = _signal.signal
    handlers = {}
    _signal.signal = lambda sig, h: handlers.setdefault(sig, h)
    old_init = FakeSink.__init__

    def init_then_signal(self, *a, **k):
        old_init(self, *a, **k)
        handlers[_signal.SIGTERM](_signal.SIGTERM, None)          # arrives during the open
    FakeSink.__init__ = init_then_signal
    try:
        code, text, _ = run(argv)
    finally:
        FakeSink.__init__ = old_init
        _signal.signal = real_signal
    sink = FakeSink.instances[0]
    side = json.load(open(os.path.join(out, 'synth_hop_tx.json')))
    check(code == 0 and 'send' not in sink.calls and sink.calls[-2:] == ['stop_waveform', 'stop']
          and side['tx']['sent'] is False and 'SIGTERM' in side['tx']['state']
          and side['interrupted_by'] == 'SIGTERM',
          'SIGTERM during the open: no send, stopped, sent false (%r)' % side['tx']['state'])

    print('\nA dead terminal after the open does not stop the stop')
    for exc in (BrokenPipeError(errno.EPIPE, 'Broken pipe'), OSError(errno.EIO, 'I/O error')):
        install_fake()
        out = os.path.join(tmp, 'dead')
        FakeSink.dead_stdout = DeadStdout(exc)
        argv = go_args(out)
        try:
            run(argv)
            raised = None
        except Exception as e:                                # noqa: BLE001
            raised = e
        sink = FakeSink.instances[0]
        side = json.load(open(os.path.join(out, 'synth_hop_tx.json')))
        check(raised is None and 'send' in sink.calls and sink.calls[-2:] == ['stop_waveform', 'stop']
              and side['tx']['sent'] is True and side['tx']['tx_stop_unix'] > 0,
              '%s on every print: the shot was sent, the sink stopped, the sidecar says sent (%r)'
              % (type(exc).__name__, raised))
        FakeSink.dead_stdout = None


def check_level_and_shot(tmp, guard):
    print('\nThe level guard, the default level, --center-mhz, --clk0 and the amplitude')
    install_fake()
    # 0 dBm is the limit and is allowed; 0.01, nan and inf are not; -200 is
    # not "from -200 up". -inf is given as --level=-inf, which argparse takes.
    ok = os.path.join(tmp, 'lvl0')
    FakeSink.side_path = os.path.join(ok, 'synth_hop_tx.json')
    code, _, _ = run(['--go', '--level', '0', '--bursts', '2', '--out', ok, '--name', 'hop'])
    check(code == 0 and FakeSink.instances and FakeSink.instances[0].level_dbm == 0.0,
          '--level 0 is accepted and the sink gets 0 dBm')
    side = json.load(open(os.path.join(ok, 'synth_hop_tx.json')))
    check(side['tx']['vsg_level_dbm'] == 0.0, 'the sidecar says 0.0 dBm')
    for text in ('0.01', 'nan', 'inf', '-200', '1e9'):
        install_fake()
        out = os.path.join(tmp, 'lvl_' + text)
        try:
            code, _, _ = run(['--go', '--level=' + text, '--bursts', '2', '--out', out, '--name', 'hop'])
        except Exception:                                     # noqa: BLE001 - it got through the guard
            code = None
        check(code not in (0, None) and not FakeSink.instances and not guard.imported
              and not os.path.exists(out), '--level %s is refused before the radio is opened' % text)
        guard.imported = False
    for text in ('-inf', '-Infinity'):
        install_fake()
        try:
            code, _, _ = run(['--dry-run', '--level=' + text, '--bursts', '2', '--out',
                              os.path.join(tmp, 'lvl_ninf')])
        except Exception:                                     # noqa: BLE001 - it got through the guard
            code = None
        check(code not in (0, None) and not os.path.exists(os.path.join(tmp, 'lvl_ninf')),
              '--level=%s is refused' % text)
    check(tx.level_problem(-199.0) is None and tx.level_problem(-40.0) is None
          and tx.level_problem(0.0) is None
          and all(tx.level_problem(v) for v in (0.5, 0.0001, float('nan'), float('inf'),
                                                 float('-inf'), -200.0, -1e308)),
          'level_problem: -199, -40 and 0 pass; 0.5, 0.0001, nan, +-inf and -200 do not')

    # The default level is -40, in the sink and in the sidecar. A dry run has
    # no sink, so use --go with the fake.
    install_fake()
    out = os.path.join(tmp, 'deflvl')
    FakeSink.side_path = os.path.join(out, 'synth_hop_tx.json')
    run(['--go', '--bursts', '2', '--out', out, '--name', 'hop'])
    side = json.load(open(os.path.join(out, 'synth_hop_tx.json')))
    check(FakeSink.instances[0].level_dbm == -40.0 and side['tx']['vsg_level_dbm'] == -40.0
          and json.dumps(side['tx']['vsg_level_dbm']) == '-40.0',
          'the default level is -40.0 dBm in the sink and in the sidecar')

    # A dry run at 2440 is built at 2440: measured on the samples with the
    # same carrier measurement as the 2441 run, not read from the sidecar.
    out = os.path.join(tmp, 'c2440')
    code, _, err = run(['--dry-run', '--center-mhz', '2440', '--bursts', '8', '--out', out,
                        '--name', 'hop', '--seed', '3'])
    side = json.load(open(os.path.join(out, 'synth_hop_tx.json')))
    iq = np.fromfile(os.path.join(out, 'synth_hop_txshot.cf32'), dtype='<c8')
    errs = [abs(carrier_khz(iq, side, side['bursts'][k])) for k in range(8)]
    check(code == 0 and side['center_mhz'] == 2440.0 and side['tx']['vsg_center_mhz'] == 2440.0
          and max(errs) < 2.0,
          'the dry run at 2440 MHz has every carrier on its channel, worst %.3f kHz; '
          'tx.vsg_center_mhz %r' % (max(errs), side['tx']['vsg_center_mhz']))
    # The same bursts measured as if the shot were at 2441 are 1 MHz off, so
    # the check above can tell the two centres apart.
    wrong = dict(side, center_mhz=2441.0)
    off = abs(carrier_khz(iq, wrong, side['bursts'][0]))
    check(abs(off - 1000.0) < 3.0, 'the measurement can tell the centres apart (%.1f kHz)' % off)

    # --clk0 reaches the shot, in the clocks and in the channels.
    base = tx.make_shot(bursts=6, ptype='DH3', seed=2)[1]
    out = os.path.join(tmp, 'clk0')
    code, _, _ = run(['--dry-run', '--clk0', '0x7654320', '--bursts', '6', '--ptype', 'DH3',
                      '--seed', '2', '--out', out, '--name', 'hop'])
    side = json.load(open(os.path.join(out, 'synth_hop_tx.json')))
    check(code == 0 and side['bursts'][0]['clk'] == 0x7654320 and side['clk'] == 0x7654320
          and side['bursts'][1]['clk'] == 0x7654320 + 2 * 4
          and base['bursts'][0]['clk'] == 0x0123400,
          '--clk0 0x7654320 is burst 0\'s clock (%#x; the default is %#x)' % (
              side['bursts'][0]['clk'], base['bursts'][0]['clk']))
    got, want = channels_are_kernel(side)
    check(got == want and [b['channel'] for b in side['bursts']] != [b['channel'] for b in base['bursts']],
          'the channels follow the new clock')

    # Amplitude 1.0 and no noise: the peak is 1, to float32 rounding, and
    # the sidecar's snr is null. The level is the VSG60A's setting, not a gain.
    iq, side = tx.make_shot(bursts=3, ptype='DH5', seed=5)
    peak = float(np.max(np.abs(iq)))
    check(abs(peak - 1.0) < 1e-6 and side['snr_db'] is None,
          'the noiseless shot peaks at %.7f (amplitude 1.0)' % peak)
    conv = side['clk_convention']
    check('exact CLK' in conv and 'preamble' in conv and 'ramp' in conv and 'start_sample' in conv,
          'clk_convention says the clock belongs to the preamble\'s first sample, not the ramp')
    check(not side['clk_convention'].startswith('exact CLK[27:0] at the first sample of every burst'),
          'it is not the old "first sample of every burst" wording')


def check_sidecar_is_json(tmp):
    print('\nThe sidecar is always valid JSON')
    path = os.path.join(tmp, 'nan.json')
    try:
        tx._write_sidecar(path, {'tx': {'vsg_level_dbm': float('nan')}})
        check(False, 'a NaN is refused by _write_sidecar')
    except ValueError:
        check(True, 'a NaN is refused by _write_sidecar, not written as a bare NaN')
    tx._write_sidecar(path, {'tx': {'vsg_level_dbm': -40.0}})
    check(json.load(open(path))['tx']['vsg_level_dbm'] == -40.0, 'a finite sidecar round-trips')


def check_default_burst_count(tmp):
    print('\nThe default is 200 bursts, and every channel gets some')
    out = os.path.join(tmp, 'default')
    code, _, err = run(['--dry-run', '--out', out, '--name', 'dflt'])
    side = json.load(open(os.path.join(out, 'synth_dflt_tx.json')))
    counts = {}
    for b in side['bursts']:
        counts[b['channel']] = counts.get(b['channel'], 0) + 1
    check(tx.DEFAULT_BURSTS == 200 and code == 0 and len(side['bursts']) == 200
          and abs(side['shot_samples'] / tx.FS - 0.7525) < 0.001,
          'a default shot has %d bursts, %.3f s' % (len(side['bursts']), side['shot_samples'] / tx.FS))
    check(set(counts) == set(range(31, 51)) and min(counts.values()) >= 2,
          'all 20 channels are used, the fewest %d bursts, channel 39 %d, channel 50 %d' % (
              min(counts.values()), counts.get(39, 0), counts.get(50, 0)))
    os.remove(os.path.join(out, 'synth_dflt_txshot.cf32'))


def check_advice(tmp):
    print('\nThe timeout -k advice')
    install_fake()
    out = os.path.join(tmp, 'advice')
    code, text, _ = run(go_args(out, bursts=100))
    seconds = tx.shot_len('DH5', 100) / tx.FS
    m = re.search(r'timeout -k (\d+)', text)
    check(m is not None and int(m.group(1)) == math.ceil(seconds + 15)
          and ('%.3f s' % seconds) in text,
          'a %.3f s shot prints timeout -k %s (the shot plus 15 s)' % (
              seconds, None if m is None else m.group(1)))
    check(0 <= text.find('timeout -k') < text.find('transmitting'),
          'the advice comes before the transmission starts')
    # A 19 s shot needs 35, which is more than the 30 the docstring example uses.
    n = tx.shot_len('DH5', 5000) / tx.FS
    check(tx.kill_after(n) == math.ceil(n) + 15 and tx.kill_after(n) > 30 and tx.kill_after(0.75) == 16,
          'kill_after(%.2f s) = %d, kill_after(0.75 s) = %d' % (n, tx.kill_after(n), tx.kill_after(0.75)))
    check('timeout -k 30' in tx.__doc__ and 'timeout -k 5 ' not in tx.__doc__
          and 'longer than the shot' in tx.__doc__,
          'the docstring\'s example uses -k 30 and says the -k time must be longer than the shot')
    check(tx.MAX_SECONDS == 15.0, 'a shot is at most 15 s, what -k 30 covers')


def check_refused(tmp, guard):
    print('\nNothing without --go, and a level or a length that is refused')
    drop_vsg()
    guard.imported = False
    out = os.path.join(tmp, 'none')
    code, text, _ = run(['--out', out, '--bursts', '4', '--level', '-10'])
    check(code == 0 and text.strip() == 'nothing sent: give --go to transmit, or --dry-run',
          'without --go or --dry-run: %r' % text.strip())
    check(not os.path.exists(out) and not guard.imported, 'it writes nothing and imports nothing')

    drop_vsg()
    guard.imported = False
    for extra in (['--dry-run'], ['--go'], []):
        code, _, _ = run(extra + ['--level', '1', '--bursts', '2', '--out', os.path.join(tmp, 'hot')])
        check(code not in (0, None) and not guard.imported,
              '+1 dBm with %s is refused before vsg_sink is imported' % (extra or 'no mode'))
        guard.imported = False
    check(not os.path.exists(os.path.join(tmp, 'hot')), 'the refused level writes nothing')

    # 4200 DH5 periods is 15.75 s. Refused from the length, before the buffer.
    drop_vsg()
    guard.imported = False
    code, text, _ = run(['--dry-run', '--bursts', '4200', '--out', os.path.join(tmp, 'long')])
    seconds = tx.shot_len('DH5', 4200) / tx.FS
    check(seconds > 15 and code not in (0, None) and not guard.imported
          and not os.path.exists(os.path.join(tmp, 'long')),
          'a %.2f s shot is refused before it is built' % seconds)

    # Channel 52 is 2454 MHz; its band is 2453-2455, 14 MHz from 2441.
    drop_vsg()
    guard.imported = False
    outside = os.path.join(tmp, 'outside')
    code, _, err = run(['--dry-run', '--reference', '--map', '31-52', '--bursts', '2',
                        '--out', outside])
    check(code not in (0, None) and 'channel 52' in err and not guard.imported
          and not os.path.exists(outside),
          'channel 52 is refused before the shot is built (%s)' % err.strip().splitlines()[-1:])
    guard.imported = False
    code, _, err = run(['--go', '--map', '52', '--bursts', '2', '--out', outside])
    check(code not in (0, None) and 'channel 52' in err and not guard.imported,
          'channel 52 with --go is refused before vsg_sink is imported')

    drop_vsg()
    guard.imported = False
    code, _, err = run(['--dry-run', '--center-mhz', '2441.5', '--bursts', '2',
                        '--out', os.path.join(tmp, 'frac')])
    check(code not in (0, None) and '2441.5' in err and 'whole' in err and not guard.imported
          and not os.path.exists(os.path.join(tmp, 'frac')),
          'a centre of 2441.5 MHz is refused')
    guard.imported = False
    code, _, err = run(['--go', '--center-mhz', '2441.5', '--bursts', '2'])
    check(code not in (0, None) and '2441.5' in err and not guard.imported,
          '--center-mhz 2441.5 with --go is refused before vsg_sink is imported')


def check_reference(tmp):
    print('\n--reference: the same train, with noise')
    out = os.path.join(tmp, 'ref')
    code, _, err = run(['--dry-run', '--reference', '--out', out, '--name', 'hop',
                        '--bursts', '8', '--ptype', 'DH5', '--seed', '3', '--level', '-20'])
    shot = json.load(open(os.path.join(out, 'synth_hop_tx.json')))
    ref = json.load(open(os.path.join(out, 'synth_hop_ref.json')))
    ref_iq = np.fromfile(os.path.join(out, 'synth_hop_ref.cf32'), dtype='<c8')
    mismatch = [k for k, (a, b) in enumerate(zip(shot['bursts'], ref['bursts']))
                if (a['channel'], a['clk'], a['air_bits']) != (b['channel'], b['clk'], b['air_bits'])]
    check(code == 0 and not err.strip() and not mismatch
          and len(ref['bursts']) == len(shot['bursts']),
          'with --dry-run the reference matches the shot burst by burst (%s)' % mismatch[:4])
    check(len(ref_iq) == shot['shot_samples'],
          'the reference is %d samples, the shot is %d' % (len(ref_iq), shot['shot_samples']))
    before = shot['bursts'][0]['start_sample'] - 1
    check(before > 0 and ref_iq[before] != 0 and ref['snr_db'] == 25.0
          and ref.get('note') == tx.REF_NOTE and ref['center_mhz'] == shot['center_mhz'],
          'noise before the first burst, 25 dB, the comparison note, the same centre')

    # As bt_tx.py does: --reference alone writes the file and does not transmit.
    alone = os.path.join(tmp, 'refonly')
    code, text, _ = run(['--reference', '--out', alone, '--name', 'only', '--bursts', '4', '--seed', '2'])
    check(code == 0 and text.strip().splitlines()[-1] == 'nothing sent: give --go to transmit, or --dry-run'
          and os.path.isfile(os.path.join(alone, 'synth_only_ref.cf32'))
          and os.path.isfile(os.path.join(alone, 'synth_only_ref.json'))
          and not os.path.exists(os.path.join(alone, 'synth_only_txshot.cf32')),
          '--reference alone writes the synthetic file and does not transmit')


def check_map_and_repeat(tmp):
    print('\nA map change, and the same arguments twice')
    # 33-52 does not fit at 2441: channel 52's band reaches 2455 MHz, 14 MHz
    # from the centre. 32-51 is the nearest 20-channel map that does fit.
    out = os.path.join(tmp, 'map')
    code, _, err = run(['--dry-run', '--out', out, '--name', 'chg', '--bursts', '16',
                        '--map', '31-50', '--map-b', '32-51', '--change-at-burst', '8', '--seed', '9'])
    check(code == 0 and not err.strip(), 'map change dry-run (%s)' % err.strip())
    if code != 0:
        return
    side = json.load(open(os.path.join(out, 'synth_chg_tx.json')))
    got, want = channels_are_kernel(side)
    check(got == want, 'both maps: every channel is the kernel\'s')
    map_a, map_b = list(range(31, 51)), list(range(32, 52))
    only_a, only_b = set(map_a) - set(map_b), set(map_b) - set(map_a)
    before = set(got[:8])
    after = set(got[8:])
    check(before <= set(map_a) and after <= set(map_b)
          and before & only_a and not after & only_a
          and after & only_b and not before & only_b,
          'bursts 0-7 stay in 31-50 and 8-15 move to 32-51, exclusive channels included')

    a, sa = tx.make_shot(bursts=5, ptype='DH3', seed=2, start_offset=3, clk0=0x100)
    b, sb = tx.make_shot(bursts=5, ptype='DH3', seed=2, start_offset=3, clk0=0x100)
    c, _ = tx.make_shot(bursts=5, ptype='DH3', seed=3, start_offset=3, clk0=0x100)
    check(a.tobytes() == b.tobytes() and sa['bursts'] == sb['bursts'],
          'the same arguments give the same samples and the same bursts')
    check(a.tobytes() != c.tobytes(), 'a different seed gives different samples')


def main():
    guard = ImportGuard()
    sys.meta_path.insert(0, guard)
    try:
        check_amplitude()
        with tempfile.TemporaryDirectory(prefix='bt_tx_hop_') as tmp:
            iq = check_dry_run(tmp, guard)
            check_refused(tmp, guard)
            check_go(tmp, iq)
            check_level_and_shot(tmp, guard)
            check_sidecar_is_json(tmp)
            check_default_burst_count(tmp)
            check_advice(tmp)
            check_reference(tmp)
            check_map_and_repeat(tmp)
    finally:
        sys.meta_path.remove(guard)
        drop_vsg()
    print('\nRESULT: %s' % ('FAIL (%d)' % len(failures) if failures else 'PASS'))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
