#!/usr/bin/env python3
"""The live DVB-T2 transmitter's chain into a file, then our receiver on it: no radio.

    python scripts/test_dvbt2_tx.py                 # 16QAM 3/5, three programmes, 4 s
    python scripts/test_dvbt2_tx.py --mod QPSK --seconds 6 --keep /tmp/t2

What it pins, on the chain ``dvbt2Xmitter`` builds (``apps/dvbt2_tx.py``):

- ffmpeg muxes three programmes into one transport stream at exactly the rate the frames
  carry: the built-in colour bars and tone, a made-up clip with sound and one without (a
  silent file maps no sound, so the stream numbers after it move up). Made in a throwaway
  folder: no media and no captured data.
- gr-dtv's modulator and the 35/32 resampler run it to 10 MS/s; the level is BASEBAND_RMS
  and its peak stays under 1.0.
- Brought back to the elementary rate, ``apps.dvbt2`` learns the signal from it (L1 says what
  was sent), decodes every codeword of every frame, and the transport stream out is the one
  that went in.
- The programmes come out as three services named from the files (the SDT, read by
  ``dvb_si``), each with its streams, and the sound only where there was sound.
- The modulator keeps ahead of the air (timed; reported, and a failure under 1.5 times).

Needs GNU Radio with gr-dtv, ffmpeg, scipy, and ``scripts/build_dvbt2_native.py`` run once.
About a minute.
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apps import dvbt2_tx  # noqa: E402
from apps.dvbt2 import dvb_si, iqt2_live, iqt2_native, t2_tx  # noqa: E402

RESULTS = []


def report(name, ok, detail=''):
    RESULTS.append((name, ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ('' if ok else f"  ({detail})"), flush=True)


def expect(name, test):
    try:
        ok, detail = test()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    report(name, ok, detail)


def make_clips(folder):
    """Two short clips: 'Test Card One' with a 440 Hz tone, 'Silent Two' with no sound."""
    one = os.path.join(folder, 'Test-Card_One.mp4')
    two = os.path.join(folder, 'Silent-Two.mp4')
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                    '-f', 'lavfi', '-i', 'testsrc=size=640x480:rate=25:duration=3',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100:duration=3',
                    '-c:v', 'libx264', '-preset', 'veryfast', '-c:a', 'aac', '-shortest', one], check=True)
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                    '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30:duration=2',
                    '-c:v', 'libx264', '-preset', 'veryfast', two], check=True)
    return [dvbt2_tx.COLOUR_BARS, one, two]


def transmit(paths, plan, seconds, iq_path, ts_path):
    """The transmitter's chain, the radio replaced by a file; the TS that went in kept too."""
    from gnuradio import blocks, gr
    mux = dvbt2_tx.Multiplex(paths, plan)
    tb = gr.top_block('t2 tx test', catch_exceptions=True)
    src = blocks.file_descriptor_source(gr.sizeof_char, mux.descriptor(), False)
    chain = dvbt2_tx.modulator(plan)
    rs = dvbt2_tx.resampler(plan)
    head = blocks.head(gr.sizeof_gr_complex, int(seconds * dvbt2_tx.OUT_RATE))
    sink = blocks.file_sink(gr.sizeof_gr_complex, iq_path, False)
    ts_sink = blocks.file_sink(gr.sizeof_char, ts_path, False)
    tb.connect(src, *chain, rs, head, sink)
    tb.connect(src, ts_sink)
    keep = (src, chain, rs, head, sink, ts_sink)       # every block outlives the run
    t = time.monotonic()
    tb.run()
    took = time.monotonic() - t
    err = mux.error()
    mux.close()
    del keep
    return took, err


def receive(iq_path, plan):
    """10 MS/s back to the elementary rate, then the receiver: (card, summary, TS)."""
    from scipy.signal import resample_poly
    x = np.fromfile(iq_path, np.complex64)
    y = resample_poly(x, 32, 35).astype(np.complex64)
    fs = plan['elementary_rate']
    card = iqt2_live.learn(y[:int(iqt2_live.ACQUIRE_S * fs)], fs)
    if not card:
        return None, {}, b'', x
    out = bytearray()
    l1 = card['l1']
    with iqt2_live.T2Stream(fs, card['fft'], card['guard'], l1['extended'], l1['frame_s'],
                            workers=iqt2_live.default_workers(), on_ts=out.extend) as st:
        for k in range(0, len(y), 1 << 20):
            st.feed(y[k:k + (1 << 20)])
        st.flush()
        s = st.summary()
    return card, s, bytes(out), x


def services(ts):
    a = dvb_si.DvbTsAnalyzer()
    a.feed(ts)
    return a


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--mod', default='16QAM', choices=sorted(t2_tx.MODS))
    ap.add_argument('--rate', default='3/5', choices=sorted(t2_tx.RATES))
    ap.add_argument('--seconds', type=float, default=4.0)
    ap.add_argument('--keep', help='keep the IQ and the TS in this folder')
    a = ap.parse_args(argv)
    if not iqt2_native.available():
        print("the native LDPC is not built: python scripts/build_dvbt2_native.py")
        return 1
    plan = t2_tx.frame_plan(16384, '19/128', a.mod, a.rate, 2, True)
    with tempfile.TemporaryDirectory() as tmp:
        folder = a.keep or tmp
        os.makedirs(folder, exist_ok=True)
        paths = make_clips(tmp)
        iq, ts_in = os.path.join(folder, 'tx.cf32'), os.path.join(folder, 'in.ts')
        took, err = transmit(paths, plan, a.seconds, iq, ts_in)
        report(f"{a.seconds:g} s of air made in {took:.1f} s ({a.seconds / took:.1f} times the air)",
               a.seconds / took >= 1.5, err)
        card, s, ts_out, x = receive(iq, plan)
        rms, peak = float(np.sqrt(np.mean(np.abs(x) ** 2))), float(np.abs(x).max())
        expect(f"level: rms {rms:.3f} (BASEBAND_RMS {dvbt2_tx.BASEBAND_RMS}), peak {peak:.2f} under 1.0",
               lambda: (abs(rms - dvbt2_tx.BASEBAND_RMS) < 0.1 * dvbt2_tx.BASEBAND_RMS and peak < 1.0,
                        (rms, peak)))
        plp = ((card or {}).get('l1', {}).get('plps') or [{}])[0]
        expect(f"the receiver learns it, and L1 says {plan['mod']} {plan['rate']}, {plan['blocks']} blocks, PP2",
               lambda: (card is not None and plp.get('modulation') == plan['mod']
                        and plp.get('code_rate') == plan['rate'] and plp.get('blocks') == plan['blocks']
                        and card['l1'].get('pilot_pattern') == 'PP2', (card, plp)))
        # Fed as fast as it goes, more than a few seconds outruns the workers and frames are
        # dropped (counted apart): every frame is decoded or dropped, none fails.
        expect(f"every codeword of every frame decodes ({s.get('codewords_ok')} of {s.get('codewords')}, "
               f"{s.get('decoded')} frames, {s.get('dropped')} dropped as the workers fell behind)",
               lambda: (s.get('decoded', 0) + s.get('dropped', 0) >= int(a.seconds / plan['frame_s']) - 2
                        and s.get('decoded', 0) >= 8 and s.get('failed') == 0
                        and s.get('codewords') == s.get('codewords_ok') > 0, s))
        sent = open(ts_in, 'rb').read()
        expect("the transport stream out is the one that went in",
               lambda: (len(ts_out) > 188 * 1000 and ts_out[188 * 100:188 * 1100] in sent,
                        (len(ts_out), len(sent))))
        an = services(ts_out)
        names = {n: an.psip.services.get(n, {}).get('name') for n in sorted(an.programs)}
        want = {1: 'Colour Bars', 2: 'Test Card One', 3: 'Silent Two'}
        expect(f"three programmes, named from the files in the SDT: {names}",
               lambda: (names == want, names))
        provs = {n: an.psip.services.get(n, {}).get('provider') for n in sorted(an.programs)}
        expect(f"every programme's provider is the one asked for, not ffmpeg's own: {provs}",
               lambda: (set(provs.values()) == {'RF Bench'}, provs))
        from apps.dvbt2.atsc_core import STREAM_TYPES
        kinds = {n: sorted(STREAM_TYPES.get(t, hex(t)) for t in an.programs[n]['streams'].values())
                 for n in sorted(an.programs)}
        expect(f"each programme's streams, sound only where there was sound: {kinds}",
               lambda: (len(kinds) == 3 and all(any('video' in k.lower() for k in v) for v in kinds.values())
                        and len(kinds.get(1, [])) == 2 and len(kinds.get(2, [])) == 2
                        and len(kinds.get(3, [])) == 1, kinds))
    bad = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)} ok, {len(bad)} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
