#!/usr/bin/env python3
"""Check what apps/atsc_source.py names its programme in the transport stream.

    python scripts/test_atsc_source.py

Encodes a second of the built-in colour bars, and of a clip when one is
given, reads the SDT back with apps/dvbt2/dvb_si.py, and checks the service
is named from the file and the provider is SERVICE_PROVIDER - not ffmpeg's
own "Service01" from "FFmpeg", which is what went out until 2026-11-23.
Needs ffmpeg; no radio.

    python scripts/test_atsc_source.py /path/to/clip.mp4
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps.atsc_source import (COLOUR_BARS, SERVICE_PROVIDER,  # noqa: E402
                              TransportStream, have_ffmpeg, service_name)
from apps.dvbt2 import dvb_si  # noqa: E402

failures = []


def check(name, ok, detail=''):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail else ''))
    if not ok:
        failures.append(name)


def first_bytes(path, nbytes=4 << 20, timeout=60):
    """The first ``nbytes`` of the stream ffmpeg makes for ``path``."""
    stream = TransportStream(path, loop=False)
    out, t0 = b'', time.time()
    try:
        fd = stream.fileno()
        while len(out) < nbytes and time.time() - t0 < timeout:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            out += chunk
    finally:
        stream.close()
    return out


def services(ts):
    a = dvb_si.DvbTsAnalyzer()
    a.feed(ts)
    return a.psip.services


def main():
    if not have_ffmpeg():
        print("SKIP: ffmpeg is not installed")
        return 0
    print("names from file names")
    check("colour bars", service_name(COLOUR_BARS) == 'Colour Bars')
    check("dashes and underscores become spaces",
          service_name('/x/FD7-Dish_Alignment.mp4') == 'FD7 Dish Alignment',
          repr(service_name('/x/FD7-Dish_Alignment.mp4')))

    paths = [COLOUR_BARS] + sys.argv[1:]
    for path in paths:
        print(f"\nthe SDT of {service_name(path)!r}, encoded as it plays")
        got = services(first_bytes(path))
        check("one service in the SDT", len(got) == 1, repr(got))
        entry = next(iter(got.values()), {})
        check("named from the file", entry.get('name') == service_name(path),
              repr(entry.get('name')))
        check(f"provider {SERVICE_PROVIDER!r}, not ffmpeg's own",
              entry.get('provider') == SERVICE_PROVIDER, repr(entry.get('provider')))

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
