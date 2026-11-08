#!/bin/bash
# Mid-link role switch capture: daPie (Pi, BlueZ) <-> ESP32 (Classic), BB60D at 40 MS/s.
#
#   t = 0            IQ recording starts (iq_start_wall is taken from record_iq.py's own
#                    "Recording to ..." line, not from the moment this script starts)
#   PRE_S            steady l2ping traffic (daPie is central/master)
#   marker pauses    PAUSE_S of silence at known wall-clock times -> visible in the IQ and
#                    in btmon, so IQ time and host time can be aligned to ~tens of ms
#   switch           hcitool sr <esp32> slave  (daPie becomes peripheral, ESP32 master)
#   POST_S           steady l2ping traffic again
#
# Run on the host that has the BB60D attached (st-health did not show one on 2026-11-08).
# Needs: ssh alias daPie (passwordless sudo there), conda env `blue`, bluey-ox-walker's
# bin/record_iq.py.  Outputs go under $DATA_ROOT/{iq,btsnoop,sidecar} with the names
# bluey-bb's freeze_truth.py expects:
#   iq/bb60d_<label>_<ts>.cf32
#   btsnoop/btmon_dapie_<label>_<ts>.btsnoop
#   sidecar/<label>_<ts>.meta and .timeline   (timeline carries iq_start_wall, iq_rate_hz)
#
# This script TRANSMITS only ordinary Bluetooth traffic from daPie to the ESP32.
# The BB60D only receives.  No VSG60/HackRF involved.
#
# Usage: run_dapie_esp32_roleswitch.sh [label] [freq_mhz] [pre_s] [post_s]
#        DRY=1 ...   skip the BB60D (btmon + l2ping + switch only; use to rehearse)
set -uo pipefail

LABEL="${1:-dapie_esp32_roleswitch}"
FREQ_MHZ="${2:-2441}"
PRE_S="${3:-30}"
POST_S="${4:-30}"
PAUSE_S="${PAUSE_S:-2}"
DRY="${DRY:-0}"
RATE_HZ="${RATE_HZ:-40000000}"
# RF gain 0: on 2026-11-08 the BB60D logged "GetIQ: ADC overflow" on ~46 of every 375 reads at RF gain 10 and 20
# (and each such read seemed to cost one 8 ms block), but none at RF gain 0. record_iq.py uses min(GAIN,20) as RF gain.
GAIN="${GAIN:-0}"
# Narrow AFH map (same as the earlier runs), sent on daPie before the link exists. AFH_MAP=none to skip.
AFH_MAP="${AFH_MAP:-00 00 00 ff df ff 07 00 00 00}"

DAPIE_SSH="${DAPIE_SSH:-daPie}"
DAPIE_BD="${DAPIE_BD:-D8:3A:DD:65:49:35}"
ESP_BD="${ESP_BD:-04:B2:47:06:0A:C2}"

BLUEY="${BLUEY:-/data/python/bluey/bluey-ox-walker}"
DATA_ROOT="${DATA_ROOT:-$BLUEY/data}"
CONDA_ENV="${CONDA_ENV:-blue}"
CONDA="${CONDA:-$HOME/miniconda3/bin/conda}"
# l2ping traffic. Flood with a 200-byte payload keeps the air busy so the marker pauses stand out;
# set PING_ARGS="-s 44" for the plain 1 Hz ping the dry run used.
PING_ARGS="${PING_ARGS:--s 200 -f}"

# Time layout (seconds after the recording starts): pause 1, switch, pause 2.
WARM_S=6                                   # link up and pinging before the first marker
P1_AT=$((WARM_S + PRE_S / 3))              # first marker pause
P2_AT=$((WARM_S + 2 * PRE_S / 3))          # second marker pause
SW_AT=$((WARM_S + PRE_S))                  # the switch
P3_AT=$((SW_AT + 2 + POST_S / 3))          # pause after the switch
TOTAL_S=$((SW_AT + 2 + POST_S + 5))        # recording length incl. tail

TS_TAG="$(date +%Y%m%d_%H%M%S)"
TAG="${LABEL}_${TS_TAG}"
IQ="$DATA_ROOT/iq/bb60d_${TAG}.cf32"
BT_LOCAL="$DATA_ROOT/btsnoop/btmon_dapie_${TAG}.btsnoop"
META="$DATA_ROOT/sidecar/${TAG}.meta"
TL="$DATA_ROOT/sidecar/${TAG}.timeline"
LOGDIR="$DATA_ROOT/sidecar"
REC_LOG="$LOGDIR/${TAG}.recorder.log"
BT_REMOTE="/tmp/btmon_dapie_${TAG}.btsnoop"
PING_REMOTE="/tmp/l2ping_${TAG}.log"

die() { echo "FATAL: $*" >&2; exit 1; }
ev()  { printf '%s %s\n' "$(date +%s.%N)" "$1" >> "$TL"; echo "  [$(date +%H:%M:%S.%N | cut -c1-12)] $1"; }
rs()  { ssh -o BatchMode=yes -o ConnectTimeout=8 "$DAPIE_SSH" "$@"; }

echo "=== $TAG : daPie <-> ESP32 role switch, ${FREQ_MHZ} MHz, ${RATE_HZ} S/s, DRY=$DRY ==="
echo "    markers at +${P1_AT}s +${P2_AT}s, switch at +${SW_AT}s, pause at +${P3_AT}s, total ${TOTAL_S}s"

# ---- preflight (nothing transmitted yet) ----
mkdir -p "$DATA_ROOT/iq" "$DATA_ROOT/btsnoop" "$DATA_ROOT/sidecar"
if [ "$DRY" != 1 ]; then
  lsusb | grep -q -i '2817:0007' || die "no BB60D (2817:0007) on this host: run this on the machine it is plugged into"
  [ -f "$BLUEY/bin/record_iq.py" ] || die "no $BLUEY/bin/record_iq.py"
  AVAIL_KB=$(df --output=avail -k "$DATA_ROOT/iq" | tail -1)
  NEED_KB=$(( TOTAL_S * RATE_HZ * 8 / 1024 + 5*1024*1024 ))
  [ "$AVAIL_KB" -gt "$NEED_KB" ] || die "not enough free space for ~$((NEED_KB/1024/1024)) GB under $DATA_ROOT/iq"
fi
rs 'true' || die "cannot ssh to $DAPIE_SSH"
echo "  clock skew vs daPie (host minus daPie, seconds; includes ssh latency ~ms):"
H0=$(date +%s.%N); R=$(rs 'date +%s.%N'); H1=$(date +%s.%N)
SKEW=$(python3 - <<EOF
h0,h1,r=$H0,$H1,$R
print(f"{(h0+h1)/2 - r:+.4f}  (round trip {h1-h0:.4f})")
EOF
)
echo "    $SKEW"
rs "sudo -n true" || die "daPie has no passwordless sudo"
RF=$(rs 'sudo rfkill list bluetooth 2>/dev/null | grep -c "Soft blocked: yes"')
if [ "${RF:-0}" != 0 ]; then
  echo "  daPie Bluetooth is soft-blocked; unblocking (will re-block at the end)"
  REBLOCK=1; rs 'sudo rfkill unblock bluetooth; sleep 1'
else REBLOCK=0; fi
rs 'sudo hciconfig hci0 up; sleep 1; hciconfig hci0 | sed -n 2,3p'
# clear anything left from an earlier run
rs 'sudo pkill -x btmon 2>/dev/null; sudo pkill -x l2ping 2>/dev/null; sudo rm -f /tmp/btmon_dapie_* /tmp/l2ping_*; sleep 0.5; true'

# ---- .meta (freeze_truth reads master=/slave= lines with BD=, and freq_mhz) ----
# The sidecar "master" is the device that PAGED. daPie pages the ESP32 and is central at setup.
{
  echo "label=$LABEL"
  echo "ts_tag=$TS_TAG"
  echo "freq_mhz=$FREQ_MHZ"
  echo "duration_s=$TOTAL_S"
  echo "rate_hz=$RATE_HZ"
  echo "bb60_gain=$GAIN"
  echo "master=daPie (Raspberry Pi, BlueZ) BD=$DAPIE_BD"
  echo "slave=ESP32-D0WD-V3 esp32-rstest (Classic, SSP off) BD=$ESP_BD"
  echo "encryption=NONE (unpaired; l2ping only; no authentication requested)"
  echo "scenario=role switch mid-link: daPie central -> peripheral via hcitool sr $ESP_BD slave"
  echo "marker_pause_s=$PAUSE_S"
  if [ "$AFH_MAP" = none ]; then echo "afh=NOT set"; else echo "afh_host_classification=$AFH_MAP (HCI 0x03|0x003f, daPie host input to its controller; map in force unread)"; fi
  echo "notes=after the switch the ESP32 is master: hop sequence = ESP32 LAP/UAP; whether it applies an AFH map is unknown"
} > "$META"

# ---- start btmon on daPie, then the IQ recorder ----
rs "sudo setsid btmon -T -w $BT_REMOTE </dev/null >/dev/null 2>&1 & sleep 0.5; pgrep -x btmon >/dev/null && echo btmon running" || die "btmon did not start"
if [ "$AFH_MAP" != none ]; then
  AFHOUT=$(rs "sudo hciconfig hci0 up; sleep 1; sudo hcitool -i hci0 cmd 0x03 0x003f $AFH_MAP" 2>&1) || die "AFH command failed"
  echo "  AFH: $(echo "$AFHOUT" | tr '\n' ' ' | tail -c 120)"
  echo "$AFHOUT" | grep -qE 'HCI Event: 0x0e.*' && echo "$AFHOUT" | tail -1 | grep -qE ' 00 *$' || die "AFH not accepted: $AFHOUT"
fi
: > "$TL"
echo "scenario=roleswitch freq=${FREQ_MHZ}MHz dur=${TOTAL_S}s pre=${PRE_S}s post=${POST_S}s" >> "$TL"
echo "T0=$(date +%s.%N)" >> "$TL"
echo "iq_rate_hz=$RATE_HZ" >> "$TL"
echo "host_minus_dapie_s=$SKEW" >> "$TL"

REC_PID=""
if [ "$DRY" != 1 ]; then
  echo "[host] starting BB60D recorder"
  (
    "$CONDA" run --no-capture-output -n "$CONDA_ENV" python -u "$BLUEY/bin/record_iq.py" \
      --driver SignalHoundBB60 --freq "$(python3 -c "print(int($FREQ_MHZ*1e6))")" \
      --rate "$RATE_HZ" --gain "$GAIN" --duration "$TOTAL_S" --output "$IQ" 2>&1 \
    | while IFS= read -r line; do
        printf '%s %s\n' "$(date +%s.%N)" "$line" >> "$REC_LOG"
        case "$line" in "Recording to"*) echo "iq_start_wall=$(date +%s.%N)" >> "$TL" ;; esac
      done
  ) &
  REC_PID=$!
  # wait for the recorder to report it is recording (BB60D open can take several seconds)
  for i in $(seq 1 60); do grep -q '^iq_start_wall=' "$TL" && break; sleep 0.5; done
  grep -q '^iq_start_wall=' "$TL" || die "recorder never started (see $REC_LOG)"
  echo "  IQ running; iq_start_wall=$(grep '^iq_start_wall=' "$TL" | cut -d= -f2)"
else
  echo "iq_start_wall=$(date +%s.%N)" >> "$TL"      # DRY: no IQ, use now as the time origin
fi
T_IQ=$(grep '^iq_start_wall=' "$TL" | cut -d= -f2)

wait_until() {   # wait until $1 seconds after iq_start_wall
  python3 - <<EOF
import time
t=$T_IQ+$1
while time.time()<t: time.sleep(0.005)
EOF
}

# ---- traffic: one l2ping stream on daPie, 1 Hz, paused for markers by stop/start ----
# ONE l2ping process for the whole run. A marker pause is SIGSTOP/SIGCONT on it: the L2CAP channel
# stays open, so the ACL link is never closed and re-made (a reconnect would put daPie back as
# master and undo the switch). The baseband still polls during a pause; the marker is the drop in
# data traffic, not total silence.
start_ping() { rs "sudo setsid sh -c 'stdbuf -oL l2ping -i hci0 $PING_ARGS -c 100000 -t 2 $ESP_BD >> $PING_REMOTE 2>&1' </dev/null >/dev/null 2>&1 &"; }
# A marker is ONE remote command that stamps daPie's own clock right after the STOP and right
# before the CONT, so the logged times carry no ssh latency (the host-clock ev lines around it
# are only good to ~0.3 s). Those two daPie-clock times are the same clock btmon stamps with.
marker() {   # $1 = name
  local out a b
  out=$(rs "sudo pkill -STOP -x l2ping; a=\$(date +%s.%N); sleep $PAUSE_S; b=\$(date +%s.%N); sudo pkill -CONT -x l2ping; echo \$a \$b")
  read -r a b <<<"$out"
  printf '%s %s_pause_start (daPie clock)\n%s %s_pause_end (daPie clock)\n' "$a" "$1" "$b" "$1" >> "$TL"
  echo "    $1: daPie clock $a .. $b"
}
stop_ping()   { rs "sudo pkill -CONT -x l2ping; sudo pkill -INT -x l2ping; true" >/dev/null; }

ev "l2ping_start"; start_ping
wait_until "$P1_AT"; marker marker1
wait_until "$P2_AT"; marker marker2
wait_until "$SW_AT"
rs "sudo hcitool con" | sed 's/^/    daPie: /'
SWOUT=$(rs "a=\$(date +%s.%N); sudo timeout 10 hcitool sr $ESP_BD slave; rc=\$?; b=\$(date +%s.%N); echo \$a \$b \$rc")
read -r SW_A SW_B SW_RC <<<"$SWOUT"
printf '%s switch_req (daPie clock)\n%s switch_cmd_returned (daPie clock) exit=%s\n' "$SW_A" "$SW_B" "$SW_RC" >> "$TL"
echo "    switch: daPie clock $SW_A .. $SW_B exit=$SW_RC"
rs "sudo hcitool con" | sed 's/^/    daPie: /'
wait_until "$P3_AT"; marker marker3

# ---- finish ----
if [ -n "$REC_PID" ]; then wait "$REC_PID"; fi
stop_ping
echo "T1=$(date +%s.%N)" >> "$TL"
rs "sudo pkill -INT -x btmon; sleep 1; true"
scp -o ConnectTimeout=8 "$DAPIE_SSH:$BT_REMOTE" "$BT_LOCAL" || echo "btmon copy failed (file stays on daPie at $BT_REMOTE)"
scp -o ConnectTimeout=8 "$DAPIE_SSH:$PING_REMOTE" "$LOGDIR/${TAG}.l2ping.log" 2>/dev/null || true
if [ "$REBLOCK" = 1 ]; then rs 'sudo hciconfig hci0 down; sudo rfkill block bluetooth; true'; echo "  daPie Bluetooth re-blocked"; fi

echo
echo "=== done: $TAG ==="
[ "$DRY" != 1 ] && { echo "  IQ: $IQ ($(du -h "$IQ" 2>/dev/null | cut -f1))"; \
  echo "  Sample Overrun warnings: $(grep -c 'Sample Overrun' "$REC_LOG" 2>/dev/null || echo '?')   ADC overflow lines: $(grep -c 'ADC overflow' "$REC_LOG" 2>/dev/null || echo '?')  (both must be 0; see $REC_LOG)"; }
echo "  btmon: $BT_LOCAL"
echo "  sidecar: $META  $TL"
echo "  --- host events in btmon (Role Change / Connect / Disconnect) ---"
btmon -T -r "$BT_LOCAL" 2>/dev/null | grep -a -E '^(<|>) HCI (Command|Event): (Switch|Role Change|Connect Com|Create Conn|Disconn)' -A3 \
  | grep -a -E 'HCI|Status|Role|Reason' | head -20
echo "  --- l2ping summary ---"; tail -2 "$LOGDIR/${TAG}.l2ping.log" 2>/dev/null
