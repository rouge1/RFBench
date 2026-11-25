# Two radios on one host: which pairs stream together

One of the notes in `knowledge/`. What applies everywhere, and which file
covers what, is in [CLAUDE.md](../CLAUDE.md). Each radio's own limits are
in [radios](radios.md) (VSG60, BB60D) and [ettus](ettus.md) (N210).

The bench has four radios and two Linux machines. Which pairs can stream
at the same time on one host decides where a transmitter and a receiver
go. This file collects the answers, with the details left in the notes
they come from.

| Pair, one host | Streams together? | Where it was measured |
|---|---|---|
| VSG60 + BB60D | **No.** BB60D: `Device packet framing issues` after three buffers; VSG: a USB transfer failure | [radios](radios.md#vsg60-notes) |
| VSG60 + HackRF | Yes, in two processes. In one process the VSG60 transmits nothing if the HackRF was opened first | [radios](radios.md#vsg60-notes) |
| **VSG60 + N210** | **Yes, as radios.** The VSG60 streaming at 20 and 40 MS/s while the N210 received it at 25 MS/s: no gap on either, below. The ATSC pair still overruns there: the receiver alone is short of the 8 cores | here, 2026-11-25 |
| **BB60D + N210** | **Yes.** No overruns or drops on either, up to 40 MS/s on the BB60D and 25 MS/s on the N210, receiving or transmitting, below. But the ATSC transmitter app on the N210 starves beside the ATSC receiver app on the BB60D | here, 2026-11-23 and 2026-11-25 |

So the only pair that cannot share a host is the VSG60 with the BB60D. A
VSG60 transmitter and an N210 receiver can share worklaptop1, as far as
the radios go; what the apps cost in CPU on 8 cores is a separate question
(below) - the ATSC receiver, for one, does not keep up there even with
the transmitter's encoder taken off the machine. The receiving machine can
hold the BB60D and the N210 both, and use them at once.

## BB60D and N210 together (2026-11-23)

Both are on the BB60D's machine: the BB60D on USB 3, and the N210 on
gigabit Ethernet through a USB Ethernet adapter. It is a 20-thread host,
with a load average of about 1.5 while these ran.

- **The BB60D's input** was a known signal. The VSG60 on worklaptop1
  played CTF night three's round 22 file at 3.2 MS/s and −40 dBm, over the
  cable into the BB60D. That file is a 200 kBd 16QAM, APRS every 10.000 s,
  and four more emitters.
- **The checks** were the ones that caught the night's timing question. A
  dropped buffer makes everything after it early, and the BB60D reports
  drops only on stderr:
  - the 16QAM's cyclic line, which should sit at 200,000 Hz;
  - the spacing of the APRS bursts;
  - multimon-ng decoding AFSK1200 at the nominal rate;
  - the capture's sample count;
  - `bb60_tone.py`'s overflow and overrun count.
- **The N210** received off air on RX2 through UHD's `benchmark_rate`
  (no disk), which counts received samples, drops, overruns and sequence
  errors. Off air is passive, so nothing about the N210 needs a go. The
  control, alone at 25 MS/s for 30 s: 749,894,893 samples, 0 dropped,
  0 overruns.

| Run | BB60D | N210 |
|---|---|---|
| Control, N210 idle (test D, the same morning) | 10 MS/s, 40 s: 16QAM line 200,000.1 Hz, APRS 10.00 s apart, 4 of 4 decoded, 0 overruns | idle |
| Both, N210 started first | 10 MS/s, 40 s: 200,000.1 Hz, 10.00 s, 4 of 4, 0 overruns, exactly 400 M samples | 25 MS/s, 60 s: 0 dropped, 0 overruns |
| BB60D at its maximum, alone | 40 MS/s, 12 s: 200,000.1 Hz, APRS decoded, 0 overruns | idle |
| BB60D at its maximum, N210 starting 4 s in | 40 MS/s, 12 s: 200,000.1 Hz, APRS decoded, 0 overruns | 25 MS/s, 10 s: 0 dropped, 0 overruns |
| The live ATSC receiver on the BB60D, N210 from 10 s to 50 s | `atscReceiver` at 10 MS/s, 55 s: "Locked - clean stream", 0 bad packets in every 5 s report, MER 22.0-22.9 dB | 25 MS/s, 40 s: 0 dropped, 0 overruns |

So opening the N210 mid-capture, streaming it at 25 MS/s, and decoding
8VSB live on the same host disturbed nothing on either radio. The
night-three captures taken on the N210 while skinny-fm held the BB60D
(rounds 10 and 18) were fine to take. The VSG60 + N210 pair is in the next
section.

## VSG60 and N210 together (2026-11-25)

The N210 was moved to worklaptop1, beside the VSG60: the VSG60 on USB, the
N210 on gigabit Ethernet through a USB Ethernet adapter. It is the 8-core
machine, on the performance governor, with UHD's socket buffers at 50 MB.
Every radio was on an antenna, so this ran over the air with the user's
go: a CW tone at 916.000 MHz (the VSG60 at 915 MHz, the tone 1,000,037 Hz
off centre in a one-second file), at −30 dBm.

- **The VSG60 streamed** the file from disk through `vsg_stream_file.py`
  (by `remote_tx.py stream`), at 20 MS/s and at 40 MS/s, the rate that
  matters most to the USB: 320 MB/s of IQ. It reports no underruns of its
  own - an underrun would go out as a gap in the RF - so the tone was
  checked at the far end.
- **The N210 received that tone off its own antenna** on RX2, at 25 MS/s,
  through UHD's `rx_samples_to_file` into `/dev/shm`, gain 10 (peaks at
  1,400-2,800 of 32,767).
- **The BB60D**, on the other machine, recorded the same tone at 5 MS/s as
  a check on the VSG60 that does not share its host.
- **The checks** on each recording, in 1,000-sample blocks after mixing the
  tone to DC: any block under half the median amplitude (a gap or an
  underrun), and any step in phase between blocks over 0.5 rad (lost
  samples). Neither recording ever came near: the worst phase step in any
  of them was 0.12 rad. The tone's frequency is chosen so that losing any
  short run of samples moves its phase - at 1 MHz exactly, losing 500
  samples at 20 MS/s is 25 whole cycles and invisible, which a self-test
  with planted faults caught. On top of that, UHD's own `O`/`D` and the
  BB60D's overrun count.

| Run | VSG60 | N210, same host | BB60D, other host |
|---|---|---|---|
| N210 alone (`benchmark_rate`) | off | 25 MS/s, 30 s: 749,907,350 samples, 0 dropped, 0 overruns | - |
| VSG60 alone | 20 MS/s | - | 30 s: no gap, no phase step, 0 overruns |
| **Both** | **20 MS/s** | **30 s: no `O`/`D`, no gap, no phase step** | 30 s: no gap, no phase step, 0 overruns |
| **Both** | **40 MS/s** | **40 s: 999,970,000 samples, no `O`/`D`, no gap, no phase step** | 40 s: tone on 100 % of the time, no gap, no phase step, 0 overruns |

worklaptop1 sat 90-92 % idle through both, the streamer on about 62 % of
one core at 40 MS/s. So the radios themselves share the host at full rate.
The ATSC transmitter and receiver that overran together there
([ettus](ettus.md#every-app-on-the-n210-2026-11-21)) had the machine 83 %
busy: that was the apps' CPU, or one receiver stage too slow at 12.5 MS/s,
and not the pair.

Against the VSG60 at 916 MHz the N210 read the tone 566 to 616 Hz high
(+0.62 to +0.67 ppm, across two runs) and the BB60D 373 to 380 Hz
(+0.41 ppm).

**A capture timed by hand missed two runs.** Two BB60D captures at
40 MS/s started 60-70 s after the run's `ON AIR` line, caught its end, and
looked like the VSG60 falling silent mid-stream. The silence began on the
second of `remote_tx`'s scheduled stop. Start a check capture from the
`ON AIR` line in the same shell, and before calling anything a transmitter
fault, put the capture's start (its file time less its length) beside the
run's on and stop times.

### Then the apps: ATSC both ends on worklaptop1 (same day)

The pair that overran before, retested over the air (user's go): the
VSG60 at 915 MHz (tuned to 919.5, `LO_OFFSET`), −9.5 dBm, and
`atscReceiver` on the N210 at 915 MHz, 12.5 MS/s, gain 30 %, both on
worklaptop1, through `remote_tx.py app` and `app_rx_check.py` from a staged
copy of the tree, 60 s of receiver each.

| Transmitter | worklaptop1 | Receiver |
|---|---|---|
| `atscXmitter`, live: ffmpeg encoding Big Buck Bunny, the 8VSB chain | 87 % busy; each app about 3.3 cores | 2-3 `usrp_source` overflows a second all through; pilot found (+0.6 ppm), MER 14.9-16.9 dB, **never decoded** - as on 2026-11-21 |
| `vsg_stream_file.py`, a 20 s file of the same chain rendered offline (colour bars) | 67 % busy; streamer 0.9 core, receiver 4.2 | **MER 23.1-23.6 dB, eye wide open, "Locked - clean stream" at 5, 10 and 60 s** - but overflows from about 12 s, about one a second (44 reports, 125 `O`/`D`), and 27-100 % of packets lost in the 5 s windows between |

So the receiver alone is short of this machine, not only the two apps
together. With the transmitter's encoder off the machine the signal is as
good as the BB60D ever read it, the first 10 s are clean, and the receiver
still overruns at a third idle - once it is locked and decoding, some
stage of it cannot keep up at 12.5 MS/s on these 8 cores. UHD also said
`Unable to set the thread priority` (no realtime limit for the user
there), which a loaded machine feels most. The same receiver on the
20-thread machine had 0 `O`/`D` and decoded clean
([ettus](ettus.md#every-app-on-the-n210-2026-11-21)). Which stage, by
per-thread CPU, is not measured.

How to run it this way: a heavy transmitter app can be replaced by its
own chain rendered to a file and streamed, which leaves its CPU off the
host - the render is the block chain of `scripts/test_atsc_loopback.py` up
to the scale, into a `file_sink`; 20 s at 20 MS/s took about a minute here.
It loops with a break in the transport stream every pass.

Not tested: the N210 *transmitting* beside the VSG60; per-thread CPU of
the ATSC receiver; worklaptop1 with a realtime limit for UHD.

## The N210 transmitting beside the BB60D (2026-11-25)

The N210 back on the BB60D's machine (USB Ethernet adapter), over the air
with the user's go. That machine is an i7-12700H - 6 performance cores
(12 threads) and 8 efficiency cores - on the `powersave` governor, and the
N210's adapter, the BB60D and the 4TB drive hang off one 5 Gb/s USB hub.

| Run | N210 transmitting | BB60D, same machine | Machine |
|---|---|---|---|
| A: a tone | UHD's `tx_waveforms`, 25 MS/s, sine at 916.0 MHz, gain 0, 0.3: **0 `U`** in 60 s | `bb60_tone.py` 5 MS/s, ATT −20, 30 s: no gap, no phase step, 0 overruns; tone 67.6 dB over the floor, the N210's LO −37.5 dBc | idle |
| B: the ATSC pair | `atscXmitter`, 25 MS/s, 10 %, Big Buck Bunny encoded live: **21,033 `U`**, in bursts all through | `atscReceiver`, gain 20 %: 0 overruns, pilot found, programme list read, "Locked - clean stream" in one 5 s window only, 100 % of packets lost in the rest, MER 15.8-18.6 dB | 66-68 % idle |
| C: the transmitter alone | the same, no receiver: **13 `U`** in 60 s | off | 89 % idle |

So the radios share the machine, as in A, and the ATSC transmitter at
25 MS/s on the N210 is fine alone (C - its first time on the air at that
rate, which [atsc](atsc.md#the-transmitters-own-lo-leak-2026-11-23) had as
arithmetic only). Together with the receiver app it starves, though the
machine is two thirds idle: the receiver takes about 3.5 threads, the
transmitter 2.2. The likely reading, not proven: an average over 20
threads hides the 6 fast cores filling, and the transmitter's 25 MS/s chain
then runs on efficiency cores or a busy core's second thread and falls
behind - on `powersave`, too. Not tried: the `performance` governor,
pinning each app to its own P-cores (`taskset`), and the receiver app
beside the tone of A.

UHD on both machines says `Unable to set the thread priority`
(`pthread_setschedparam` fails): the user has no realtime limit, so no
UHD thread gets priority on a loaded machine.

Two things around it, neither the pairing:

- **`bb60_tone.py` at 40 MS/s for 30 s** wrote all 9.6 GB (exactly 30.0 s
  of samples) and then exited with an error, once, with the N210 running;
  12 s at the same rate was fine with it and without. Its `peaks()` takes
  one FFT across the whole capture at once in double precision, which for
  1.2 billion samples needs several times the file's size in memory - the
  likely cause, read from the code rather than the error, which was
  filtered out. Keep long 40 MS/s captures out of it, or analyse a slice.
- **One `atscReceiver` run reported 100 % of packets lost for all 55 s**,
  with MER 23 dB. The failure was already there at 5 s, before the N210
  started at 10 s. The receiver alone, straight after, was clean, and so was
  the repeat with the N210. Whether the branch's receiver or the old one
  does this is in [atsc](atsc.md#the-transmitters-own-lo-leak-2026-11-23). See [atsc](atsc.md#the-transmitters-own-lo-leak-2026-11-23).

Not tested then: the N210 *transmitting* beside the BB60D - done since,
above; the N210 writing to the same disk as a BB60D capture
at full rate; and the HackRF with either.

The scripts were throwaways in the session scratchpad. `benchmark_rate`
lives at `$CONDA_PREFIX/lib/uhd/examples/benchmark_rate`, run as
`benchmark_rate --args addr=<the N210's address> --rx_rate 25e6 --duration 60`,
with the address from `uhd_find_devices`.
