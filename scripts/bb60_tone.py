#!/usr/bin/env python3
"""Record a short BB60D capture and list the strongest tones in it.

    python scripts/bb60_tone.py 915e6                      # 2 s at 2.5 MS/s, ATT 0, RF 0
    python scripts/bb60_tone.py 915e6 --wait 4             # start 4 s late (a transmitter warming up)
    python scripts/bb60_tone.py 100.85e6 --secs 20 --out cap.cfile   # keep the IQ (complex64)

Prints one line - centre, rate, total power in dBFS, the median floor per FFT
bin, the bin width, and the driver's ADC-overflow and sample-overrun counts -
then up to six peaks at least 15 dB over that floor, each as an offset from
centre, an absolute frequency and a power in dBFS (summed over seven bins, so a
Blackman-windowed tone reads its whole power). Nothing printed under the first
line means nothing stood 15 dB over the floor.

The levels are dBFS, not dBm: nothing here converts them. They compare one
capture with another at the same centre, gain and rate - an antenna against an
antenna, a transmitter setting against a setting - not against a power meter.

Runs on the conda ``gnu`` Python. Opens the BB60D through the raw SoapySDR
binding the way ``apps/bb60_source.py`` does (setupStream before any setting,
opened by driver name alone) - see knowledge/radios.md. Gain is the two
elements as such: ``--att`` −30…0 dB and ``--rf`` 0…20 dB; the default, both 0,
is what the receivers' 60 % gives. Nothing here writes config/.
Used for the N210 antenna comparison in knowledge/ettus.md.
"""
import argparse
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'apps'))
import bb60_source as bb  # noqa: E402

#: A peak must stand this far over the median bin to be listed.
PEAK_OVER_FLOOR_DB = 15
#: Seconds read and thrown away after the stream starts, while it settles.
SETTLE_S = 0.3


def record(freq, rate, secs, att, rf, wait):
    bb.install_log_handler()
    if not bb.find_devices():
        sys.exit("No BB60D found (or its SoapySDR module did not load).")
    import SoapySDR  # noqa: E402 - after find_devices has set the plugin path
    from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32
    dev = SoapySDR.Device(f"driver={bb.DRIVER}")
    st = dev.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
    dev.setSampleRate(SOAPY_SDR_RX, 0, rate)
    dev.setFrequency(SOAPY_SDR_RX, 0, freq)
    dev.setGain(SOAPY_SDR_RX, 0, "ATT", att)
    dev.setGain(SOAPY_SDR_RX, 0, "RF", rf)
    dev.activateStream(st)
    n = int(rate * secs)
    buf = np.empty(n, np.complex64)
    chunk = np.empty(65536, np.complex64)
    try:
        t_end = time.time() + SETTLE_S + wait
        while time.time() < t_end:
            dev.readStream(st, [chunk], len(chunk), timeoutUs=500000)
        got = 0
        while got < n:
            r = dev.readStream(st, [chunk], min(len(chunk), n - got),
                               timeoutUs=500000)
            if r.ret > 0:
                buf[got:got + r.ret] = chunk[:r.ret]
                got += r.ret
    finally:
        dev.deactivateStream(st)
        dev.closeStream(st)
        del dev
    return buf


def peaks(buf, freq, rate, nfft=1 << 18, count=6):
    """The summary line and up to ``count`` peak lines, as strings."""
    segs = buf[: (len(buf) // nfft) * nfft].reshape(-1, nfft)
    if not len(segs):
        nfft = 1 << int(np.log2(len(buf)))
        segs = buf[:nfft].reshape(1, nfft)
    w = np.blackman(nfft)
    psd = np.mean(np.abs(np.fft.fftshift(np.fft.fft(segs * w, axis=1),
                                         axes=1)) ** 2, axis=0)
    psd /= np.sum(w) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / rate))
    db = 10 * np.log10(psd + 1e-30)
    floor = np.median(db)
    lines = [f"centre {freq/1e6:.4f} MHz rate {rate/1e6} MS/s  total "
             f"{10*np.log10(np.mean(np.abs(buf)**2)):.1f} dBFS  floor/bin "
             f"{floor:.1f} dB  bin {rate/nfft:.1f} Hz  overflows "
             f"{bb.overflow_count()} overruns {bb.overrun_count()}"]
    d = db.copy()
    for _ in range(count):
        i = int(np.argmax(d))
        if d[i] < floor + PEAK_OVER_FLOOR_DB:
            break
        pw = 10 * np.log10(np.sum(psd[max(i - 3, 0):i + 4]))
        lines.append(f"  peak {f[i]/1e3:+10.3f} kHz  ({(freq+f[i])/1e6:.6f} MHz)"
                     f"  {pw:6.1f} dBFS  {d[i]-floor:5.1f} dB over floor")
        d[max(i - 40, 0):i + 41] = -999
    return lines


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument("freq", type=float, help="centre, Hz")
    p.add_argument("--rate", type=float, default=2.5e6,
                   help="sample rate; the BB60D's are 40 MS/s halved down")
    p.add_argument("--secs", type=float, default=2.0)
    p.add_argument("--att", type=float, default=0, help="-30..0 dB")
    p.add_argument("--rf", type=float, default=0, help="0..20 dB")
    p.add_argument("--wait", type=float, default=0,
                   help="seconds to wait after setup before recording")
    p.add_argument("--out", help="write the IQ here, raw complex64")
    a = p.parse_args()
    buf = record(a.freq, a.rate, a.secs, a.att, a.rf, a.wait)
    if a.out:
        buf.tofile(a.out)
    print("\n".join(peaks(buf, a.freq, a.rate)), flush=True)


if __name__ == "__main__":
    main()
