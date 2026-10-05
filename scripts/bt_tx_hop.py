#!/usr/bin/env python3
"""Stage 3: send one hopping Bluetooth train from the VSG60A.

    python scripts/bt_tx_hop.py --dry-run --out /tmp/x          # cf32, no radio
    timeout -k 30 60 python scripts/bt_tx_hop.py --go --level -20

``scripts/bt_tx.py`` repeats one channel from the VSG60A's memory. A hop
sequence cannot be looped that way: the channel depends on the clock up to
CLK27, so a loop that restarted would restart the channels while a
receiver's clock kept running. This plays the whole train once
(``sink.send_waveform``) and returns when that buffer has been played. The
sidecar's ``clk`` is the exact CLK[27:0] of every burst. Nothing wraps.

The ``-k`` of ``timeout`` has to be longer than the shot and the cleanup.
``send_waveform`` blocks inside the VSG60A's C library and cannot be
interrupted, so a signal only sets a flag that is looked at before the send
and after it. ``timeout`` sends SIGTERM and then, ``-k`` seconds later,
SIGKILL, which ends the process inside the send with no ``vsgAbort``, no RF
off and no sidecar. So the ``-k`` time is the shot's length plus 15 s; this
prints what the shot in hand needs when ``--go`` starts, and 30 covers any
shot up to 15 s. (A thread that aborted the send would be a second caller
into a library that is not thread safe, so there is none.) The 60 is the
time the whole run may take, the 4.7 s open included.

The shot is ``bt_synth_hop.synthesise_afh`` with no noise and unit
amplitude, the same idea as ``bt_tx.py``'s loop, so ``--level`` is the
burst's power at the VSG60A output. The sample rate is 40 MS/s. The centre
defaults to 2441.0 MHz and has to be a whole number of MHz, which is what
bluey-ox-walker's channelizer needs.

The BB60D's usable band at 40 MS/s is only ±13.5 MHz about its centre. A
scan of this room put the noise 20-28 dB over the floor from 2452 MHz
upward (a Wi-Fi access point) and showed 2431-2432 MHz hot as well;
2433-2451 MHz was clean. So the map is channels 31-50 (2433-2452 MHz,
20 channels, the AFH minimum) and the tuning is 2441.0 MHz. A channel
whose own band, the channel ±1 MHz, falls outside that ±13.5 MHz is
refused, dry run included, because the BB60D could not see it.

The VSG60A is tuned to the capture centre in force. Its carrier
feedthrough (about −40 dBc), the BB60D's own DC and the Bluetooth channel
on that frequency then sit on the same bin. At 2441.0 MHz that channel is
39, inside the map, and a receiver should expect it to be the weak one.

``--reference`` also writes ``synth_<name>_ref``, the same train as a
synthetic capture: noise at 25 dB in 1 MHz and the default amplitude, for
comparison with the recording.

The default is 200 bursts, 0.75 s of DH5. A hundred leaves some of the 20
channels with no burst at all (35 had none) and two or three on the ones the
notes care about (39, the feedthrough bin, and 50, the noisy one), too few
for a per-channel figure; 200 puts a few on every channel.

Nothing is transmitted without ``--go``. A level that is not a finite number
from -200 to 0 dBm is refused (``nan`` compares false against every limit, so
a bare "above 0" check lets it through to the radio), and so is a shot longer
than 15 s. The buffer is the one complex64 array ``synthesise_hop`` already
builds; with no noise it is not copied into two float64 arrays beside it.

The sidecar is written before the radio is opened, with ``tx.sent`` false and
``tx.state`` "not started", so a run that fails or is stopped never leaves an
earlier dry run's file standing for it. It is written again as "pending" just
before the send and a last time with what happened: ``tx.sent`` true, false
(a signal came before the send) or "error" (``tx.error`` says what). A
receiver should accept only ``tx.sent`` true. Every print after the sink is
open goes through ``say``, which swallows ``OSError``: a dropped ssh session
with no terminal sends no SIGHUP, and the next print raises BrokenPipeError
(Python ignores SIGPIPE), which must not stop the sink being stopped.
"""
import argparse
import json
import math
import os
import signal
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth, bt_synth_hop  # noqa: E402

FS = 40e6
CENTER_MHZ = 2441.0
#: Channels 31-50, 2433-2452 MHz: the 20 clean megahertz in this room.
MAP = list(range(31, 51))
#: What the BB60D can actually use either side of its centre at 40 MS/s.
BB60D_REACH_MHZ = 13.5
#: Over the air this project goes no higher than 0 dBm.
MAX_LEVEL_DBM = 0.0
#: One shot, not a recording: longer than this is refused before it is built.
MAX_SECONDS = 15.0
#: A level is a finite number of dBm in (-200, 0]; the limit is 0.
MIN_LEVEL_DBM = -200.0
#: ``timeout -k`` has to exceed the shot by this much (the cleanup and the
#: 4.7 s open are inside it): the shot's seconds plus this.
KILL_MARGIN_S = 15
#: Bursts in a default shot. See the docstring.
DEFAULT_BURSTS = 200
#: The synthetic copy of the shot, the same number the bench recordings use.
REF_SNR_DB = 25.0
REF_NOTE = ('the over-the-air shot as a synthetic file, for comparison '
            'with the capture')
LAP, UAP = 0x9E8B33, 0x47


def outside_bb60d(channel, center_mhz):
    """Whether ``channel`` ±1 MHz lies outside ±``BB60D_REACH_MHZ`` of the centre."""
    return (abs(bt_synth_hop.channel_mhz(channel) - float(center_mhz))
            + bt_synth_hop.HALF_BAND_MHZ > BB60D_REACH_MHZ)


def bb60d_problem(channels, center_mhz):
    """The refusal text for every named channel the BB60D cannot see, or None."""
    bad = []
    for channel in channels:
        if outside_bb60d(channel, center_mhz):
            mhz = bt_synth_hop.channel_mhz(channel)
            bad.append('channel %d (%g-%g MHz)' % (
                channel, mhz - bt_synth_hop.HALF_BAND_MHZ, mhz + bt_synth_hop.HALF_BAND_MHZ))
    if not bad:
        return None
    return '%s is outside +-%.1f MHz of %.1f MHz' % (
        ', '.join(bad), BB60D_REACH_MHZ, center_mhz)


def shot_len(ptype='DH5', bursts=DEFAULT_BURSTS, start_offset=0, fs=FS):
    """How many samples the shot will be, which is ``synthesise_afh``'s length
    and nothing is allocated to find it."""
    slot = fs * br.SLOT_US * 1e-6
    if abs(slot - round(slot)) > 1e-6:
        raise ValueError('%g S/s does not divide a 625 us slot' % fs)
    slot = int(round(slot))
    period = br.PACKET_TYPES[ptype][1] + 1
    first = int(bt_synth.LEAD_SLOTS * slot) + int(start_offset)
    return first + bursts * period * slot + slot


def _maps(channels, map_b, change_at_burst):
    """``(first_burst, channels)`` for one map, or two when ``map_b`` is set."""
    if channels is None:
        channels = MAP
    maps = [(0, list(channels))]
    if map_b is not None:
        maps.append((change_at_burst, list(map_b)))
    return maps


def _require_bb60d(maps, center_mhz):
    """Refuse a map that names a channel the BB60D cannot see."""
    named = []
    for _, channels in maps:
        named.extend(channels)
    problem = bb60d_problem(named, center_mhz)
    if problem:
        raise ValueError(problem)


def make_shot(ptype='DH5', bursts=DEFAULT_BURSTS, channels=None, map_b=None, change_at_burst=None,
              clk0=0x0123400, start_offset=0, seed=1, level_dbm=-40.0,
              center_mhz=CENTER_MHZ, lap=LAP, uap=UAP):
    """The hopping shot and its sidecar. No radio.

    ``channels`` is the first map (31-50 when None). ``map_b`` with
    ``change_at_burst`` is the second map, from that burst on. The samples
    are unit-amplitude GFSK with no noise, so a burst's |iq| is 1 and
    ``level_dbm`` is only a record of what the VSG60A would be set to.
    ``center_mhz`` is the capture centre and the VSG60A tuning.
    """
    maps = _maps(channels, map_b, change_at_burst)
    _require_bb60d(maps, center_mhz)
    iq, side = bt_synth_hop.synthesise_afh(
        maps, ptype=ptype, bursts=bursts, lap=lap, uap=uap, clk0=clk0, fs=FS,
        center_mhz=center_mhz, snr_db=None, amplitude=1.0, seed=seed,
        start_offset=start_offset)
    if len(iq) != shot_len(ptype, bursts, start_offset, FS):
        raise RuntimeError('shot length %d is not the planned length' % len(iq))
    # synthesise_afh's clock note is about a file, and says the clock is
    # "at the first sample of the burst". It belongs to start_sample, the
    # first sample of the access code's preamble, and not to the burst's own
    # first sample, the ramp, which is 81 samples (2.025 us) earlier.
    # bt_synth_hop.py is not this script's to change, so the key is replaced
    # here. This is a single transmission: every clk is the real CLK[27:0],
    # and start_sample is counted from the first sample of this buffer.
    side['clk_convention'] = ("exact CLK[27:0] at start_sample, the first sample of the "
                              "access code's preamble (not the burst's own first, ramp, "
                              'sample, 81 samples earlier); a one-shot, so nothing wraps '
                              'and the higher bits are the truth')
    side['start_sample_meaning'] = ('samples from the first sample of the shot to the '
                                    "first sample of the access code's preamble")
    side['shot_samples'] = int(len(iq))
    side['tx'] = {
        'vsg_center_mhz': float(center_mhz),
        'vsg_sample_rate': FS,
        'vsg_level_dbm': float(level_dbm),
        'mode': 'send_waveform: one shot',
        'vsg_reference': 'internal (free-running)',
        # A shot is not sent until main has sent it. A receiver takes only
        # sent true.
        'sent': False,
        'state': 'not started',
    }
    return iq, side


def make_reference(ptype='DH5', bursts=DEFAULT_BURSTS, channels=None, map_b=None, change_at_burst=None,
                   clk0=0x0123400, start_offset=0, seed=1, center_mhz=CENTER_MHZ,
                   lap=LAP, uap=UAP):
    """The same train as a synthetic capture: default amplitude, 25 dB in 1 MHz.

    Same maps, packet type, bursts, clock and start offset as the shot, so
    each burst keeps its channel, its clock and its air bits. The seed is
    the one ``--seed`` gave the shot.
    """
    maps = _maps(channels, map_b, change_at_burst)
    _require_bb60d(maps, center_mhz)
    iq, side = bt_synth_hop.synthesise_afh(
        maps, ptype=ptype, bursts=bursts, lap=lap, uap=uap, clk0=clk0, fs=FS,
        center_mhz=center_mhz, snr_db=REF_SNR_DB, seed=seed, start_offset=start_offset)
    side['note'] = REF_NOTE
    return iq, side


def _dirs(out):
    if out:
        return out, out
    return (os.path.join(bt_synth.BLUEY, 'data', 'iq'),
            os.path.join(bt_synth.BLUEY, 'data', 'sidecar'))


def _write_sidecar(path, side):
    # allow_nan=False: a bare NaN is not JSON, and nothing here is one.
    text = json.dumps(side, indent=1, allow_nan=False)
    with open(path, 'w') as f:
        f.write(text)


def say(*args):
    """``print`` that never raises: a closed terminal or a broken pipe is not
    a reason to leave the radio on or the sidecar unwritten."""
    try:
        print(*args, flush=True)
    except OSError:
        pass


def level_problem(level):
    """The refusal text for a level that is not a finite number in (-200, 0],
    or None."""
    if not (math.isfinite(level) and MIN_LEVEL_DBM < level <= MAX_LEVEL_DBM):
        return ('level %s dBm is not a finite number from %.0f to %.0f dBm; over the '
                'air this project goes no higher than 0' % (level, MIN_LEVEL_DBM, MAX_LEVEL_DBM))
    return None


def kill_after(seconds):
    """The ``timeout -k`` that a shot of ``seconds`` needs, in whole seconds."""
    return int(math.ceil(seconds + KILL_MARGIN_S))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--name', default='ota_hop_dh5')
    ap.add_argument('--ptype', default='DH5', choices=['DH1', 'DM1', 'DH3', 'DM3', 'DH5', 'DM5'])
    ap.add_argument('--bursts', type=int, default=DEFAULT_BURSTS,
                    help='default %d, 0.75 s of DH5: 100 leaves channels with no burst' % DEFAULT_BURSTS)
    ap.add_argument('--map', type=bt_synth_hop.channel_list,
                    help='channels in use, 31-50 or a comma list; default 31-50')
    ap.add_argument('--map-b', type=bt_synth_hop.channel_list, help='the map from --change-at-burst on')
    ap.add_argument('--center-mhz', type=float, default=CENTER_MHZ,
                    help='capture centre and VSG60A tuning, a whole number of MHz; '
                         'default %.1f' % CENTER_MHZ)
    ap.add_argument('--change-at-burst', type=int, help='the burst --map-b starts on')
    ap.add_argument('--clk0', type=lambda text: int(text, 0), default=0x0123400,
                    help="the master's clock at burst 0, CLK1-0 = 00; default 0x0123400")
    ap.add_argument('--start-offset', type=int, default=0,
                    help='samples to move every burst by; default 0')
    ap.add_argument('--seed', type=int, default=1, help='default 1')
    ap.add_argument('--level', type=float, default=-40.0, help='dBm at the VSG60A output')
    ap.add_argument('--out', help='folder for the files, instead of bluey-ox-walker\'s data/')
    ap.add_argument('--dry-run', action='store_true', help='write the shot as cf32; never open the radio')
    ap.add_argument('--go', action='store_true', help='play the shot once on the VSG60A')
    ap.add_argument('--reference', action='store_true',
                    help='also write synth_<name>_ref, the same train with noise')
    args = ap.parse_args(argv)

    if args.go and args.dry_run:
        ap.error('give --go or --dry-run, not both')
    if (args.map_b is None) != (args.change_at_burst is None):
        ap.error('--map-b and --change-at-burst go together')
    # A level that is not a finite number in (-200, 0] is refused before the
    # shot is built and before vsg_sink is imported, whatever else was asked.
    # (nan > 0 is false, so a plain "above 0" test does not stop it.)
    problem = level_problem(args.level)
    if problem:
        ap.error(problem)
    try:
        n = shot_len(args.ptype, args.bursts, args.start_offset, FS)
    except (KeyError, ValueError) as e:
        ap.error(str(e))
    if n / FS > MAX_SECONDS:
        ap.error('shot is %.2f s (%d samples), longer than %.0f s' % (n / FS, n, MAX_SECONDS))
    # A fractional megahertz puts every bluey channelizer bin half a channel off.
    if args.center_mhz != round(args.center_mhz):
        ap.error('centre %.4f MHz is not a whole number of MHz' % args.center_mhz)
    # Every channel the map names, not only the ones this shot lands on: a
    # channel the BB60D cannot see is refused before the buffer is built.
    named = list(MAP if args.map is None else args.map)
    if args.map_b is not None:
        named = named + list(args.map_b)
    problem = bb60d_problem(named, args.center_mhz)
    if problem:
        ap.error(problem)
    if not args.go and not args.dry_run and not args.reference:
        print('nothing sent: give --go to transmit, or --dry-run')
        return

    iq_dir, side_dir = _dirs(args.out)
    if args.reference:
        try:
            ref_iq, ref_side = make_reference(
                ptype=args.ptype, bursts=args.bursts, channels=args.map, map_b=args.map_b,
                change_at_burst=args.change_at_burst, clk0=args.clk0,
                start_offset=args.start_offset, seed=args.seed, center_mhz=args.center_mhz)
        except ValueError as e:
            ap.error(str(e))
        os.makedirs(iq_dir, exist_ok=True)
        os.makedirs(side_dir, exist_ok=True)
        ref_iq_path = os.path.join(iq_dir, 'synth_%s_ref.cf32' % args.name)
        ref_side_path = os.path.join(side_dir, 'synth_%s_ref.json' % args.name)
        ref_iq.astype('<c8', copy=False).tofile(ref_iq_path)
        _write_sidecar(ref_side_path, ref_side)
        print(ref_iq_path)
        print(ref_side_path)
        del ref_iq
    if not args.go and not args.dry_run:
        print('nothing sent: give --go to transmit, or --dry-run')
        return

    try:
        iq, side = make_shot(ptype=args.ptype, bursts=args.bursts, channels=args.map,
                             map_b=args.map_b, change_at_burst=args.change_at_burst,
                             clk0=args.clk0, start_offset=args.start_offset, seed=args.seed,
                             level_dbm=args.level, center_mhz=args.center_mhz)
    except ValueError as e:
        ap.error(str(e))
    print('shot: %d samples (%.3f s), %d %s bursts, %d channels, peak |iq| %.3f' % (
        len(iq), len(iq) / FS, len(side['bursts']), args.ptype, len(side['hop_channels']),
        float(np.max(np.abs(iq)))))

    iq_dir, side_dir = _dirs(args.out)
    os.makedirs(side_dir, exist_ok=True)
    side_path = os.path.join(side_dir, 'synth_%s_tx.json' % args.name)

    if args.dry_run:
        os.makedirs(iq_dir, exist_ok=True)
        iq_path = os.path.join(iq_dir, 'synth_%s_txshot.cf32' % args.name)
        iq.astype('<c8', copy=False).tofile(iq_path)
        side['tx']['dry_run'] = True
        side['tx'].update(sent=False, state='dry run: never sent')
        _write_sidecar(side_path, side)
        print(iq_path)
        print(side_path)
        return

    seconds = len(iq) / FS
    say('this shot is %.3f s, so run it under timeout -k %d (the shot plus %d s): '
        'a SIGKILL inside the send leaves no RF off and no sidecar'
        % (seconds, kill_after(seconds), KILL_MARGIN_S))
    # The sidecar goes down before the radio is opened, so a run that fails
    # in the open, or is stopped, does not leave an earlier dry run's file
    # at this path for a receiver to take as this transmission.
    side['tx'].update(sent=False, state='not started')
    _write_sidecar(side_path, side)

    # SIGTERM (timeout), SIGHUP (a dropped session) and SIGINT (Ctrl-C) only
    # raise a flag. send_waveform blocks until the buffer has been played and
    # cannot be interrupted; a signal is noticed in the wait before that
    # call, or once the call has returned.
    stop_now = []

    def on_signal(signum, _frame):
        stop_now.append(signal.Signals(signum).name)

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, on_signal)
    sink = None
    try:
        from apps import vsg_sink
        sink = vsg_sink.vsg_sink(center_freq=args.center_mhz * 1e6, sample_rate=FS,
                                 level_dbm=args.level)
    except Exception as e:                                    # noqa: BLE001
        side['tx'].update(sent=False, error='%s: %s' % (type(e).__name__, e),
                          state='not started: the VSG60A could not be opened')
        _write_sidecar(side_path, side)
        raise
    # From here the sink is open, and nothing - a print into a closed
    # terminal included - may leave without stopping it.
    failure = None
    send_started = False
    try:
        side['tx']['vsg_serial'] = sink.get_serial()
        side['tx']['tx_start_unix'] = time.time()
        side['tx'].update(sent='pending', state='pending')
        _write_sidecar(side_path, side)
        say('transmitting %d samples (%.3f s) at %.1f dBm, centre %.1f MHz, started %.3f' % (
            len(iq), seconds, args.level, args.center_mhz, side['tx']['tx_start_unix']))
        if stop_now:
            say('%s: stopping before the shot' % stop_now[0])
            side['tx'].update(sent=False, state='not sent: %s came before the shot' % stop_now[0])
        else:
            send_started = True
            sink.send_waveform(iq)
            side['tx'].update(sent=True, state='sent')
            if stop_now:
                say('%s: the shot had already started and could not be cut' % stop_now[0])
        if stop_now:
            side['interrupted_by'] = stop_now[0]
    except Exception as e:                                    # noqa: BLE001
        failure = e
        message = '%s: %s' % (type(e).__name__, e)
        if send_started:
            side['tx'].update(sent='error', error=message,
                              state='error in the send: part of the shot may have gone out')
        else:
            side['tx'].update(sent=False, error=message, state='error before the send')
    finally:
        try:
            sink.stop_waveform()
        finally:
            sink.stop()
            side['tx']['tx_stop_unix'] = time.time()
            _write_sidecar(side_path, side)
    say(side_path)
    if failure is not None:
        raise failure


if __name__ == '__main__':
    main()
