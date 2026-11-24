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
| VSG60 + N210 | They streamed, but the ATSC transmitter and receiver together overran on worklaptop1. Two heavy apps on 8 cores, or one slow stage, not separated | [ettus](ettus.md#every-app-on-the-n210-2026-11-21) |
| **BB60D + N210** | **Yes.** No overruns or drops on either, up to 40 MS/s on the BB60D and 25 MS/s on the N210, below | here, 2026-11-23 |

So a transmitter and a receiver still need two machines whenever the
transmitter is the VSG60. The receiving machine can hold the BB60D and the
N210 both, and use them at once.

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
worklaptop1 overruns in [ettus](ettus.md#every-app-on-the-n210-2026-11-21)
were that machine's load, not the pairing. The night-three captures taken
on the N210 while skinny-fm held the BB60D (rounds 10 and 18) were fine to
take.

Two things around it, neither the pairing:

- **`bb60_tone.py` at 40 MS/s for 30 s** wrote all 9.6 GB (exactly 30.0 s
  of samples, 0 overruns) and then exited with an error. It did the same
  with or without the N210. It analyses the whole capture in memory, so
  keep captures that long at that rate short, or skip its analysis.
- **One `atscReceiver` run reported 100 % of packets lost for all 55 s**,
  with MER 23 dB. The failure was already there at 5 s, before the N210
  started at 10 s. The receiver alone, straight after, was clean, and so was
  the repeat with the N210. See [atsc](atsc.md#the-transmitters-own-lo-leak-2026-11-23).

Not tested: the N210 *transmitting* beside the BB60D, which needs a cable
or a go over the air; the N210 writing to the same disk as a BB60D capture
at full rate; and the HackRF with either.

The scripts were throwaways in the session scratchpad. `benchmark_rate`
lives at `$CONDA_PREFIX/lib/uhd/examples/benchmark_rate`, run as
`benchmark_rate --args addr=<the N210's address> --rx_rate 25e6 --duration 60`,
with the address from `uhd_find_devices`.
