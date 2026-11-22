#!/usr/bin/env python3
"""build_dvbt2_native: compile the DVB-T2 LDPC decoder where the apps run.

    python scripts/build_dvbt2_native.py            # build what is missing or older than its sources
    python scripts/build_dvbt2_native.py --force    # build again
    python scripts/build_dvbt2_native.py --check    # say whether it is built from the sources as they are; exit 1 if not

Today that is one library: ``apps/dvbt2/native/libt2ldpc`` (``.so`` on Linux, ``.dylib``
on macOS), DVB-T2's LDPC decoder from ``t2ldpc.cc`` and the vendored xdsopl/LDPC headers
(0BSD). It is built for this machine's processor (``-march=native`` on x86-64: AVX2 where
there is AVX2; NEON on Apple Silicon) and never committed. The compiler is ``$CXX``, else the
first of c++, g++ and clang++ on PATH (on a Mac, Xcode's command line tools).

Without it everything still runs: ``iqt2_data`` falls back to its NumPy decoder, about a
hundred times slower and a little weaker.
"""

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apps.dvbt2 import iqt2_native  # noqa: E402


def compiler():
    cxx = os.environ.get('CXX')
    if cxx:
        return cxx
    for name in ('c++', 'g++', 'clang++'):
        if shutil.which(name):
            return name
    return None


def flags():
    f = ['-std=c++17', '-O2', '-ffast-math', '-fno-exceptions', '-fno-rtti', '-fPIC', '-shared',
         '-W', '-Wall', '-Wno-vla', '-Wno-unused-parameter']
    if sys.platform == 'darwin':
        f += ['-dynamiclib']
    import platform
    if platform.machine().lower() in ('x86_64', 'amd64'):
        f += ['-march=native']
    return f


def build(src, out, force=False):
    if not force and iqt2_native.up_to_date(out):
        print(f"{out}: up to date")
        return 0
    cxx = compiler()
    if not cxx:
        print("no C++ compiler: set CXX, or install one (Linux: g++; macOS: xcode-select --install)",
              file=sys.stderr)
        return 1
    tmp = out + '.tmp'
    cmd = [cxx] + flags() + [src, '-o', tmp]
    print(' '.join(cmd))
    r = subprocess.run(cmd)
    if r.returncode:
        return r.returncode
    os.replace(tmp, out)        # a running process keeps the old file it loaded
    with open(iqt2_native.hash_path(out), 'w') as f:
        f.write(iqt2_native.sources_hash() + '\n')
    print(f"{out}: built")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args(argv)
    out = iqt2_native.library_path()
    if a.check:
        ok = iqt2_native.up_to_date(out)
        print(f"{out}: {'up to date' if ok else 'missing or older than its sources'}")
        return 0 if ok else 1
    return build(iqt2_native.SOURCE, out, a.force)


if __name__ == '__main__':
    sys.exit(main())
