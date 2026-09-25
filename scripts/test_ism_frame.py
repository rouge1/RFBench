#!/usr/bin/env python3
"""Build every ISM frame, render it, and let rtl_433 grade the result.

    python scripts/test_ism_frame.py
    python scripts/test_ism_frame.py nexus_th          # one profile
    python scripts/test_ism_frame.py --keep /tmp/ism   # keep the captures

No radio is involved and nothing is transmitted: `rtl_433` decodes files, so
the whole encoder can be checked with nothing plugged in. That is the point of
building this half first, and it is why `rtl_433` is worth having on the bench
even though nothing in the launcher ever calls it - see
[devnotes/ism.md](../devnotes/ism.md#rtl_433-as-the-referee).

It grades at three depths, cheapest first, because a failure at one of them
means something quite different from a failure at the next:

* **Bits.** ``rtl_433 -y`` takes a bitbuffer straight to the decoders with no
  DSP anywhere in the way, so a failure here is field packing or a check byte
  and nothing else.
* **Samples.** The same frame rendered to ``.cu8`` and read back through the
  real pulse detector and slicer. A failure here with the bits passing is
  timing: a gap on the wrong side of a ``gap_limit``, a pulse too short to
  see, rows that did not land in one package.
* **The codings on their own**, against rtl_433's flex decoders, for the two
  that no profile here exercises.

It also sweeps for the Rubicson collision. Roughly one Nexus frame in 250
satisfies Rubicson's CRC and is claimed by that decoder instead, with a
perfectly plausible reading and no error anywhere, so ``rubicson_collides()``
has to agree with rtl_433 exactly rather than approximately.

``rtl_433`` lives in /usr/bin, outside the conda environment - it is a bench
tool like ffmpeg, not a dependency - so this skips rather than fails when it
is not installed.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import ism_frame as ism  # noqa: E402

RTL_433 = shutil.which('rtl_433')

#: What each profile should come back as, at its defaults.
EXPECTED = {
    'nexus_th': {'model': 'Nexus-TH', 'id': 181, 'channel': 1,
                 'temperature_C': 19.0, 'humidity': 71},
    'acurite_609txc': {'model': 'Acurite-609TXC', 'id': 202,
                       'temperature_C': 26.2, 'humidity': 76},
    'lacrosse_tx141th_bv2': {'model': 'LaCrosse-TX141THBv2', 'id': 231,
                             'temperature_C': -20.0, 'humidity': 10},
    'ev1527': {'model': 'Generic-Remote', 'id': 4660, 'cmd': 8},
}

#: Rows are cut down from the real devices' counts to keep the files small.
#: Nexus and LaCrosse both need three identical rows in one package, so
#: nothing here goes below three.
#:
#: LaCrosse is 6 for a reason worth knowing: at **exactly four** rows the
#: frame comes back as TFA-303221 and LaCrosse-TX141THBv2 never appears at
#: all. Three works, five and up work, twelve - what the real sensor sends -
#: works. Only four fails, and it fails with a perfectly good reading under
#: the wrong model name. Trimming repeats to make a test file smaller is what
#: found it; nothing about the frame was wrong.
ROWS = {'nexus_th': 4, 'acurite_609txc': 3, 'lacrosse_tx141th_bv2': 6,
        'ev1527': 3}


def decode(args):
    """Run rtl_433 and return the JSON lines it printed, oldest first."""
    out = subprocess.run([RTL_433, '-F', 'json'] + args,
                         capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def matches(row, expect):
    """Every key in ``expect`` present in ``row`` with that value."""
    for key, want in expect.items():
        got = row.get(key)
        if isinstance(want, float):
            if got is None or abs(float(got) - want) > 0.05:
                return False
        elif str(got) != str(want):
            return False
    return True


def build(name):
    kwargs = {}
    if name == 'ev1527':
        kwargs['frames'] = ROWS[name]
    else:
        kwargs['rows'] = ROWS[name]
    return ism.PROFILES[name](**kwargs)


def check_bits(names):
    """Fields and check bytes, with no DSP in the way."""
    print("Bits, through rtl_433 -y:")
    ok = True
    for name in names:
        frame = build(name)
        rows = decode(['-y', frame.code])
        hit = any(matches(r, EXPECTED[name]) for r in rows)
        models = ', '.join(sorted({r.get('model', '?') for r in rows})) or 'nothing'
        print("  %-22s %-4s %d bits, %2d rows -> %s"
              % (name, "ok" if hit else "FAIL", len(frame.bits), frame.rows,
                 models))
        ok = ok and hit
    return ok


def check_render(names, out_dir):
    """The same frames through the real pulse detector and slicer."""
    print("\nSamples, rendered to .cu8 at 250 kS/s and read back:")
    ok = True
    for name in names:
        frame = build(name)
        iq = ism.render(frame, fs=250e3)
        path = os.path.join(out_dir, ism.capture_name('synth_' + name))
        ism.write_capture(path, iq)
        rows = decode(['-r', path])
        hit = any(matches(r, EXPECTED[name]) for r in rows)
        print("  %-22s %-4s %6.1f ms, %6d samples, %d decode(s)"
              % (name, "ok" if hit else "FAIL", frame.duration_us / 1000.0,
                 len(iq), len(rows)))
        ok = ok and hit
    return ok


def check_offset(out_dir):
    """The LO-offset path, at the rate and offset a transmitter would use.

    All three radios leak their local oscillator at I=Q=0, so the transmitter
    tunes low and puts the signal back as a baseband tone. This confirms the
    decode survives it - 400 kHz is well outside rtl_433's 250 kHz capture,
    which is the whole idea.
    """
    print("\nThe LO offset the transmitter will use:")
    ok = True
    frame = build('nexus_th')
    for fs, offset in ((1e6, 100e3), (8e6, 400e3)):
        iq = ism.render(frame, fs=fs, offset_hz=offset)
        path = os.path.join(out_dir,
                            ism.capture_name('offset%g' % (offset / 1e3), fs=fs))
        ism.write_capture(path, iq)
        rows = decode(['-r', path])
        hit = any(matches(r, EXPECTED['nexus_th']) for r in rows)
        print("  %-22s %-4s %.6g S/s, %+g kHz, %.1f MB"
              % ('nexus_th', "ok" if hit else "FAIL", fs, offset / 1e3,
                 os.path.getsize(path) / 1e6))
        ok = ok and hit
    return ok


def check_codings(out_dir):
    """PCM and Manchester, which no profile here uses, against flex decoders."""
    print("\nThe codings no profile exercises, against rtl_433 -X:")
    ok = True

    data = ism.pack([(0xAAAA, 16), (0x1234, 16)])
    train = ism.pcm(data, 500, 500)
    train[-1] = (train[-1][0], 25000)
    frame = ism.Frame('pcm', data, train, 1, 'flex')
    path = os.path.join(out_dir, ism.capture_name('pcm'))
    ism.write_capture(path, ism.render(frame, fs=250e3))
    rows = decode(['-r', path, '-R', '0',
                   '-X', 'n=pcm,m=OOK_PCM,s=500,l=500,r=5000'])
    got = rows[0]['codes'][0] if rows and rows[0].get('codes') else ''
    # NRZ cannot tell a trailing zero from silence, so the slicer runs on into
    # the frame gap and the row comes back long by about reset_limit/long_width
    # bits of zero. The leading bits are what matter.
    hit = got.split('}')[-1].startswith('aaaa1234')
    print("  %-22s %-4s %s" % ('pcm (NRZ)', "ok" if hit else "FAIL", got))
    ok = ok and hit

    data = ism.pack([(0x1234, 16)])
    train = ism.manchester(data, 500)
    train[-1] = (train[-1][0], 25000)
    frame = ism.Frame('mc', data, train, 1, 'flex')
    path = os.path.join(out_dir, ism.capture_name('mc'))
    ism.write_capture(path, ism.render(frame, fs=250e3))
    rows = decode(['-r', path, '-R', '0',
                   '-X', 'n=mc,m=OOK_MC_ZEROBIT,s=500,l=500,r=5000'])
    got = rows[0]['codes'][0] if rows and rows[0].get('codes') else ''
    # The decoder hardcodes a zero bit onto the front, so 16 bits in is 17 out.
    want = ism.code([0] + data)
    hit = got == want
    print("  %-22s %-4s %s (want %s)"
          % ('manchester', "ok" if hit else "FAIL", got, want))
    return ok and hit


def check_rubicson():
    """``rubicson_collides()`` has to agree with rtl_433 exactly."""
    print("\nThe Rubicson collision, swept:")
    flagged, claimed, total = set(), set(), 0
    for sensor_id in range(0, 256, 7):
        for tenths in range(-200, 500, 37):
            frame = ism.nexus_th(sensor_id=sensor_id, temp_c=tenths / 10.0,
                                 humidity=sensor_id % 100, rows=3)
            total += 1
            key = (sensor_id, tenths)
            if ism.rubicson_collides(frame.bits):
                flagged.add(key)
            if any('Rubicson' in r.get('model', '')
                   for r in decode(['-y', frame.code])):
                claimed.add(key)
    ok = flagged == claimed
    print("  %d frames swept, %d claimed by Rubicson (1 in %d), "
          "rubicson_collides() agrees: %s"
          % (total, len(claimed), total // max(1, len(claimed)),
             "yes" if ok else "NO"))
    if not ok:
        print("    flagged but not claimed: %s" % sorted(flagged - claimed))
        print("    claimed but not flagged: %s" % sorted(claimed - flagged))
    return ok


def check_guards():
    """The three things render() and pack() must refuse rather than fudge."""
    print("\nGuards:")
    cases = [
        ("a field that does not fit",
         lambda: ism.pack([(256, 8)])),
        ("a pulse under ten samples",
         lambda: ism.render(ism.lacrosse_tx141th_bv2(rows=3), fs=40e3)),
        ("a lead-in under 1024 samples",
         lambda: ism.render(ism.nexus_th(rows=3), fs=250e3, lead_in_us=100)),
        ("a temperature outside 12 bits",
         lambda: ism.lacrosse_tx141th_bv2(temp_c=400.0)),
    ]
    ok = True
    for label, call in cases:
        try:
            call()
        except ValueError:
            print("  %-34s refused" % label)
        else:
            print("  %-34s NOT REFUSED" % label)
            ok = False
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('profiles', nargs='*', choices=sorted(ism.PROFILES) + [],
                    help="profiles to check (default: all)")
    ap.add_argument('--keep', metavar='DIR',
                    help="write the captures here and leave them behind")
    args = ap.parse_args()

    if not RTL_433:
        print("rtl_433 is not on the path - install it with "
              "`sudo apt install rtl-433`. Skipping.")
        return 0

    names = args.profiles or sorted(ism.PROFILES)
    out_dir = args.keep or tempfile.mkdtemp(prefix='ism_')
    os.makedirs(out_dir, exist_ok=True)
    print(subprocess.run([RTL_433, '-V'], capture_output=True,
                         text=True).stderr.strip().splitlines()[0])
    print()

    results = [check_bits(names), check_render(names, out_dir)]
    if names == sorted(ism.PROFILES):
        results += [check_offset(out_dir), check_codings(out_dir),
                    check_rubicson(), check_guards()]

    if args.keep:
        print("\nCaptures left in %s" % out_dir)
    else:
        shutil.rmtree(out_dir, ignore_errors=True)

    ok = all(results)
    print()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
