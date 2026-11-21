#!/bin/bash
# Over-the-air check of an Ettus N210 into the BB60D on this machine: a CW tone
# at four bands, then a minute of the FM + RDS Transmitter decoded by the RDS
# receiver. Built for comparing N210 antennas (knowledge/ettus.md) and as a
# quick "is the Ettus link alive" check.
#
#   TX_HOST=worklaptop1-fast USRP_ADDR=<the N210's IP> scripts/n210_ota_test.sh ant2
#
# The N210 is driven from TX_HOST (an ssh alias for the machine on its Ethernet
# link); the BB60D is on the machine this runs on. Nothing writes config/ on
# either side: the transmitter gets --config through apps/_run.py, the
# receiver runs through scripts/watch_rds_receiver.py.
#
# What it sends, all at low power:
#   - for F in 101.1, 433.92, 915, 2440 MHz: tx_waveforms, gain 10 dB, a tone at
#     F + 100 kHz (amplitude 0.3, TX/RX port), 14 s; scripts/bb60_tone.py at F
#     records 2 s of it, 4 s in, at ATT 0 / RF 0 and prints the tone in dBFS
#   - fmRdsTransmitter at RDS_MHZ (default 101.1 - pick an empty channel), 25 %
#     power, 95 s; the RDS receiver on the BB60D at 60 % gain for 60 s
# Each transmitter runs under timeout -k, and the script waits until none is
# left on TX_HOST before it returns.
#
# Optional: OUT (folder for the logs, default a new temporary one), TX_REPO
# (this repo's path on TX_HOST, default /data/python/SDR), RDS_MHZ, AUDIO (what
# the transmitter plays: 'tone', the default, or a WAV/MP3 path on TX_HOST -
# a song with tags also puts Now Playing into the RadioText).
#
# Close any launcher on either machine first (CLAUDE.md, Rules that bite), and
# tell anyone else using the N210 or the BB60D.
set -u
LABEL=${1:?usage: TX_HOST=... USRP_ADDR=... $0 LABEL}
: "${TX_HOST:?set TX_HOST to the ssh alias of the machine the N210 is cabled to}"
: "${USRP_ADDR:?set USRP_ADDR to the N210 address}"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TX_REPO=${TX_REPO:-/data/python/SDR}
RDS_MHZ=${RDS_MHZ:-101.1}
AUDIO=${AUDIO:-tone}
OUT=${OUT:-$(mktemp -d)}
mkdir -p "$OUT"
REMOTE_ENV='source ~/miniconda3/etc/profile.d/conda.sh && conda activate gnu'
TXW='$CONDA_PREFIX/lib/uhd/examples/tx_waveforms'

source ~/miniconda3/etc/profile.d/conda.sh
conda activate gnu

tx_idle() { ! ssh "$TX_HOST" 'pgrep -f "_run.py fmRdsTransmitter|tx_waveforms"' >/dev/null; }
if ! tx_idle; then
    echo "A transmitter is already running on $TX_HOST; not starting another." >&2
    exit 1
fi

for F in 101.1e6 433.92e6 915e6 2440e6; do
    ssh "$TX_HOST" "$REMOTE_ENV && timeout -k 5 14 $TXW --args addr=$USRP_ADDR \
        --freq $F --rate 1e6 --gain 10 --wave-type SINE --wave-freq 100e3 \
        --ampl 0.3 --ant TX/RX" > "$OUT/${LABEL}_tone_$F.log" 2>&1 &
    timeout -k 5 30 python "$ROOT/scripts/bb60_tone.py" "$F" --wait 4 2>&1 \
        | grep -v '^\[INFO\]' | sed -n 1,2p | sed "s/^/[$LABEL $F] /"
    wait
done

CFG=$(ssh "$TX_HOST" mktemp --suffix=.json)
ssh "$TX_HOST" "cat > $CFG" <<EOF
{"radio_type": "usrp", "ipXmitAddr": "$USRP_ADDR", "frequency_mhz": $RDS_MHZ,
 "power_percent": 25, "call": "KTST", "ps": "N210TEST",
 "radiotext": "N210 into BB60D test", "pty": 5, "track_in_rt": true,
 "audio": "$AUDIO", "audio_list": [$( [ "$AUDIO" = tone ] || echo "\"$AUDIO\"" )]}
EOF
ssh "$TX_HOST" "cd $TX_REPO && $REMOTE_ENV && QT_QPA_PLATFORM=offscreen \
    timeout -k 5 95 python apps/_run.py fmRdsTransmitter --config $CFG" \
    > "$OUT/${LABEL}_rds_tx.log" 2>&1 &
TXPID=$!
# The window is up (offscreen) once Qt complains about size hints.
for _ in $(seq 60); do
    grep -q propagateSizeHints "$OUT/${LABEL}_rds_tx.log" && break
    sleep 1
done
(cd "$ROOT" && QT_QPA_PLATFORM=offscreen timeout -k 5 85 \
    python scripts/watch_rds_receiver.py --minutes 1 --every 30 \
    --freq "$RDS_MHZ" --gain 60 --audio off --rt 1) > "$OUT/${LABEL}_rds_rx.log" 2>&1
grep '^ \+[0-9]\|final' "$OUT/${LABEL}_rds_rx.log" | sed "s/^/[$LABEL RDS] /"
wait $TXPID
until tx_idle; do sleep 2; done
ssh "$TX_HOST" "rm -f $CFG"
echo "[$LABEL] transmitter off; logs in $OUT"
