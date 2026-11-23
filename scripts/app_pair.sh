#!/bin/bash
# One transmitter app on a radio, judged by a receiver: a receiver app on
# the BB60D or the N210, or a BB60D spectrum capture. The over-the-air test
# of every app on the Ettus N210 - knowledge/ettus.md.
#
#   USRP_ADDR=<the N210> scripts/app_pair.sh \
#       usrp fmRdsTransmitter frequency_mhz 915 power_percent 10 \
#       bb60 rdsReceiver frequency_mhz 915 40 40
#
#   TX_HOST=worklaptop1-fast USRP_ADDR=<the N210> scripts/app_pair.sh \
#       vsg ismXmitter cf 433.92 pwr -30dBm \
#       usrp ismReceiver frequency_mhz 433.92 50 35 -- interval_s=5
#
#   ... usrp atscXmitter cf 915 pwr 10   spectrum - - 915 20e6 0.5
#
# Arguments: TXRADIO (usrp|vsg) TXAPP FKEY FREQ_MHZ PKEY POWER - POWER is a
# percent, or for the VSG60 a level written like -30dBm - then RXRADIO
# (bb60|usrp|spectrum) RXAPP RXFKEY RXFREQ_MHZ and, for a receiver app,
# GAIN_PCT SECS, or for spectrum, RATE SECS (RXAPP and RXFKEY are '-').
# Anything after a '--' is passed to the transmitter as KEY=VALUE.
#
# Where the radios are: the BB60D on this machine; the N210 on N210_HOST
# ('local', the default, or an ssh alias - it was on worklaptop1 until
# 2026-11-21); the VSG60 on TX_HOST (an ssh alias). Each end runs on its
# radio's machine. A transmitter goes through scripts/app_tx_check.py, which
# refuses to start without both keys and stops within a second if the radio
# is not where it was asked; a receiver through scripts/app_rx_check.py; a
# spectrum through scripts/bb60_tone.py and spectrum_report.py at 20 dB of
# BB60D attenuation (an N210 a metre away overloads it at 0).
#
# Optional: TX_NO_CALLBACKS=1 (do not move the transmitter's power and
# frequency mid-run - a disruption the NTSC pair does not ride out),
# TX_REPO (this repo on a remote host, default /data/python/SDR), TX_TOOLS
# (where a remote host finds the check scripts, default $TX_REPO/scripts),
# OUT (a folder for the logs; default a new temporary one).
#
# The N210 cannot be both ends: one process holds it (for RF1 into RF2 on
# the one radio, see knowledge/ettus.md). The VSG60 and the BB60D will not
# stream on one host. Tell anyone else using the radios first.
set -u
usage() { sed -n '2,40p' "$0" >&2; exit 2; }
[ $# -ge 10 ] || usage
TXR=$1; TXM=$2; TFK=$3; TF=$4; TPK=$5; TP=$6; RXR=$7; RXM=$8; RFK=$9; RF=${10}
shift 10
A1=${1:-40}; A2=${2:-30}; shift 2 2>/dev/null || shift $#
[ "${1:-}" = "--" ] && shift
TXSET=""; for kv in "$@"; do TXSET="$TXSET --set $kv"; done
[ -n "${TX_NO_CALLBACKS:-}" ] && TXSET="$TXSET --no-callbacks"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
N210_HOST=${N210_HOST:-local}
TX_REPO=${TX_REPO:-/data/python/SDR}
TX_TOOLS=${TX_TOOLS:-$TX_REPO/scripts}
OUT=${OUT:-$(mktemp -d)}
mkdir -p "$OUT"
ENV='source ~/miniconda3/etc/profile.d/conda.sh && conda activate gnu'
source ~/miniconda3/etc/profile.d/conda.sh
conda activate gnu

busy() {
    # Only a python running one of these, or tx_waveforms itself - a shell
    # whose command line merely mentions them (this one's caller, say) is not.
    local pat='^[^ ]*python[0-9.]* [^ ]*(app_tx_check|app_rx_check|_run|vsg_stream_file)\.py|^[^ ]*tx_waveforms'
    if [ "$1" = local ]; then pgrep -f "$pat" >/dev/null
    else ssh -n "$1" "pgrep -f '$pat'" >/dev/null; fi
}

case $TXR in
    usrp) : "${USRP_ADDR:?set USRP_ADDR for the N210}"; TXHOST=$N210_HOST
          TXARGS="--radio usrp --addr $USRP_ADDR --power $TP" ;;
    vsg)  : "${TX_HOST:?set TX_HOST to the ssh alias of the VSG60s machine}"; TXHOST=$TX_HOST
          if [[ $TP == *dBm ]]; then TXARGS="--radio vsg --level-dbm ${TP%dBm}"
          else TXARGS="--radio vsg --power $TP"; fi ;;
    *) usage ;;
esac
case $RXR in
    usrp) : "${USRP_ADDR:?set USRP_ADDR for the N210}"; RXHOST=$N210_HOST ;;
    bb60|spectrum) RXHOST=local ;;
    *) usage ;;
esac
if [ "$TXR" = usrp ] && [ "$RXR" = usrp ]; then
    echo "The N210 cannot transmit and receive in two processes at once." >&2; exit 2
fi
if [ "$TXR" = vsg ] && [ "$TXHOST" = local ] && [ "$RXHOST" = local ] && [ "$RXR" != usrp ]; then
    echo "The VSG60 and the BB60D will not stream on one host." >&2; exit 2
fi
RXSECS=${A2%.*}
TXSECS=$((RXSECS + 25))
for h in $(printf '%s\n' "$TXHOST" "$RXHOST" | sort -u); do
    if busy "$h"; then echo "Something is already running on $h; not starting." >&2; exit 1; fi
done

if [ "$TXHOST" = local ]; then
    (cd "$ROOT" && timeout -k 5 $((TXSECS + 40)) python scripts/app_tx_check.py $TXM $TXARGS \
        --fkey $TFK --freq $TF --pkey $TPK --secs $TXSECS $TXSET) > "$OUT/tx_$TXM.log" 2>&1 &
else
    ssh -n "$TXHOST" "cd $TX_REPO && $ENV && timeout -k 5 $((TXSECS + 40)) python $TX_TOOLS/app_tx_check.py \
        $TXM $TXARGS --fkey $TFK --freq $TF --pkey $TPK --secs $TXSECS $TXSET" > "$OUT/tx_$TXM.log" 2>&1 &
fi
TXPID=$!
for _ in $(seq 60); do grep -q "CHECK radio\|ABORT\|CHECK RESULT" "$OUT/tx_$TXM.log" && break; sleep 1; done
if ! grep -q "CHECK radio" "$OUT/tx_$TXM.log"; then
    echo "=== $TXM did not start on the $TXR"; grep CHECK "$OUT/tx_$TXM.log"; wait $TXPID; exit 1
fi
echo "=== $TXR $TXM ($TF MHz, on $TXHOST) -> $RXR ${RXM/-/spectrum} ($RF MHz, on $RXHOST)"
grep -o "CHECK radio.*" "$OUT/tx_$TXM.log" | cut -c1-200 | sed 's/^/  /'
sleep 3

case $RXR in
    spectrum)
        python "$ROOT/scripts/bb60_tone.py" "${RF}e6" --rate "$A1" --secs "$A2" --att -20 \
            --out "$OUT/cap.cfile" 2>&1 | grep -v '^\[INFO\]' | head -1 | sed 's/^/  bb60: /'
        python "$ROOT/scripts/spectrum_report.py" "$OUT/cap.cfile" "$A1" "${RF}e6" | sed 's/^/  /'
        rm -f "$OUT/cap.cfile" ;;
    bb60|usrp)
        RXARGS="--radio $RXR --fkey $RFK --freq $RF --gain $A1 --secs $A2"
        [ "$RXR" = usrp ] && RXARGS="$RXARGS --addr $USRP_ADDR"
        if [ "$RXHOST" = local ]; then
            (cd "$ROOT" && timeout -k 5 $((RXSECS + 40)) python scripts/app_rx_check.py $RXM $RXARGS) \
                > "$OUT/rx_$RXM.log" 2>&1
        else
            ssh -n "$RXHOST" "cd $TX_REPO && $ENV && timeout -k 5 $((RXSECS + 40)) python \
                $TX_TOOLS/app_rx_check.py $RXM $RXARGS" > "$OUT/rx_$RXM.log" 2>&1
        fi
        grep "^RX radio" "$OUT/rx_$RXM.log" | sed 's/^/  /'
        grep "^RX t=" "$OUT/rx_$RXM.log" | tail -2 | cut -c1-600 | sed 's/^/  /'
        OD=$(sed -e 's/^RX.*//' -e 's/UHD\|USRP2\|USRP\|UDP\|DEBUG//g' "$OUT/rx_$RXM.log" \
            | grep -v '^\[\|Traceback\|File \|Error\|^ ' | grep -o '[OD]' | wc -l)
        [ "$RXR" = usrp ] && echo "  RX overruns (O/D): $OD" ;;
esac

wait $TXPID
until ! busy "$TXHOST"; do sleep 2; done
grep -o "CHECK \(NOTE\|PROBLEM\|ABORT\|RESULT\).*" "$OUT/tx_$TXM.log" | sed 's/^/  TX /'
U=$(sed -e 's/CHECK.*//' -e 's/UHD\|USRP2\|USRP\|UDP//g' "$OUT/tx_$TXM.log" \
    | grep -v '^\[\|Traceback\|File \|Error\|^ ' | grep -o U | wc -l)
echo "  TX underruns: $U   logs: $OUT"
