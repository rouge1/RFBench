#!/bin/bash
# One transmitter app on a radio on TX_HOST, judged by a receiver: either a
# receiver app (on the BB60D here, or the N210 on TX_HOST) or a BB60D
# spectrum capture. The over-the-air test of every app on the Ettus N210 -
# knowledge/ettus.md.
#
#   TX_HOST=worklaptop1-fast USRP_ADDR=<the N210> scripts/app_pair.sh \
#       usrp fmRdsTransmitter frequency_mhz 915 power_percent 10 \
#       bb60 rdsReceiver frequency_mhz 915 40 40
#
#   TX_HOST=worklaptop1-fast scripts/app_pair.sh \
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
# The transmitter runs through scripts/app_tx_check.py on TX_HOST (an ssh
# alias), which refuses to start without both keys and stops within a second
# if the radio is not where it was asked; the receiver through
# scripts/app_rx_check.py, or scripts/bb60_tone.py and spectrum_report.py at
# 20 dB of BB60D attenuation (the N210 a metre away overloads it at 0).
#
# Optional: TX_NO_CALLBACKS=1 (do not move the transmitter's power and
# frequency mid-run), TX_REPO (this repo on TX_HOST, default /data/python/SDR), TX_TOOLS
# (where TX_HOST finds the two check scripts, default $TX_REPO/scripts), OUT
# (a folder for the logs; default a new temporary one).
#
# The N210 cannot be both ends: one process holds it. The VSG60 and the BB60D
# will not stream on one host, which is why the transmitters are on TX_HOST.
# Tell anyone else using the radios first.
set -u
usage() { sed -n '2,36p' "$0" >&2; exit 2; }
[ $# -ge 10 ] || usage
TXR=$1; TXM=$2; TFK=$3; TF=$4; TPK=$5; TP=$6; RXR=$7; RXM=$8; RFK=$9; RF=${10}
shift 10
A1=${1:-40}; A2=${2:-30}; shift 2 2>/dev/null || shift $#
[ "${1:-}" = "--" ] && shift
TXSET=""; for kv in "$@"; do TXSET="$TXSET --set $kv"; done
# TX_NO_CALLBACKS=1 leaves the window's controls alone - the mid-run move
# of power and frequency is itself a disruption a receiver may not ride out.
[ -n "${TX_NO_CALLBACKS:-}" ] && TXSET="$TXSET --no-callbacks"
: "${TX_HOST:?set TX_HOST to the ssh alias of the machine the transmitter is on}"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TX_REPO=${TX_REPO:-/data/python/SDR}
TX_TOOLS=${TX_TOOLS:-$TX_REPO/scripts}
OUT=${OUT:-$(mktemp -d)}
mkdir -p "$OUT"
ENV='source ~/miniconda3/etc/profile.d/conda.sh && conda activate gnu'
source ~/miniconda3/etc/profile.d/conda.sh
conda activate gnu

case $TXR in
    usrp) : "${USRP_ADDR:?set USRP_ADDR for the N210}"; TXPOW="--power $TP"; TXADDR="--addr $USRP_ADDR" ;;
    vsg)  TXADDR=""
          if [[ $TP == *dBm ]]; then TXPOW="--level-dbm ${TP%dBm}"; else TXPOW="--power $TP"; fi ;;
    *) usage ;;
esac
if [ "$TXR" = usrp ] && [ "$RXR" = usrp ]; then
    echo "The N210 cannot transmit and receive in two processes at once." >&2; exit 2
fi
if [ "$RXR" = spectrum ]; then RXSECS=${A2%.*}; else RXSECS=${A2%.*}; fi
TXSECS=$((RXSECS + 25))

busy() { ssh -n "$TX_HOST" 'pgrep -f "app_tx_check|app_rx_check|_run.py|tx_waveforms"' >/dev/null; }
if busy; then echo "Something is already running on $TX_HOST; not starting." >&2; exit 1; fi

ssh -n "$TX_HOST" "cd $TX_REPO && $ENV && timeout -k 5 $((TXSECS + 40)) python $TX_TOOLS/app_tx_check.py \
    $TXM --radio $TXR $TXADDR --fkey $TFK --freq $TF --pkey $TPK $TXPOW --secs $TXSECS $TXSET" \
    > "$OUT/tx_$TXM.log" 2>&1 &
TXPID=$!
for _ in $(seq 60); do grep -q "CHECK radio\|ABORT\|CHECK RESULT" "$OUT/tx_$TXM.log" && break; sleep 1; done
if ! grep -q "CHECK radio" "$OUT/tx_$TXM.log"; then
    echo "=== $TXM did not start on the $TXR"; grep CHECK "$OUT/tx_$TXM.log"; wait $TXPID; exit 1
fi
echo "=== $TXR $TXM ($TF MHz) -> $RXR ${RXM/-/spectrum} ($RF MHz)"
grep -o "CHECK radio.*" "$OUT/tx_$TXM.log" | cut -c1-200 | sed 's/^/  /'
sleep 3

case $RXR in
    spectrum)
        python "$ROOT/scripts/bb60_tone.py" "${RF}e6" --rate "$A1" --secs "$A2" --att -20 \
            --out "$OUT/cap.cfile" 2>&1 | grep -v '^\[INFO\]' | head -1 | sed 's/^/  bb60: /'
        python "$ROOT/scripts/spectrum_report.py" "$OUT/cap.cfile" "$A1" "${RF}e6" | sed 's/^/  /'
        rm -f "$OUT/cap.cfile" ;;
    bb60)
        (cd "$ROOT" && timeout -k 5 $((RXSECS + 40)) python scripts/app_rx_check.py "$RXM" \
            --radio bb60 --fkey "$RFK" --freq "$RF" --gain "$A1" --secs "$A2") > "$OUT/rx_$RXM.log" 2>&1
        grep "^RX t=" "$OUT/rx_$RXM.log" | tail -2 | cut -c1-600 | sed 's/^/  /' ;;
    usrp)
        : "${USRP_ADDR:?set USRP_ADDR for the N210}"
        ssh -n "$TX_HOST" "cd $TX_REPO && $ENV && timeout -k 5 $((RXSECS + 40)) python $TX_TOOLS/app_rx_check.py \
            $RXM --radio usrp --addr $USRP_ADDR --fkey $RFK --freq $RF --gain $A1 --secs $A2" \
            > "$OUT/rx_$RXM.log" 2>&1
        grep "^RX radio" "$OUT/rx_$RXM.log" | sed 's/^/  /'
        grep "^RX t=" "$OUT/rx_$RXM.log" | tail -2 | cut -c1-600 | sed 's/^/  /' ;;
    *) usage ;;
esac

wait $TXPID
until ! busy; do sleep 2; done
grep -o "CHECK \(NOTE\|PROBLEM\|ABORT\|RESULT\).*" "$OUT/tx_$TXM.log" | sed 's/^/  TX /'
U=$(sed -e 's/CHECK.*//' -e 's/UHD\|USRP2\|USRP\|UDP//g' "$OUT/tx_$TXM.log" \
    | grep -v '^\[\|Traceback\|File \|Error\|^ ' | grep -o U | wc -l)
echo "  TX underruns: $U   logs: $OUT"
