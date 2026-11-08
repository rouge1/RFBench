#!/usr/bin/env python3
"""RFCOMM sink for bt_capture_roleswitch.sh, run on daPie (a Raspberry Pi, BlueZ).

Connects to the ESP32's SPP server, reads what it streams and logs bytes per second on
this machine's own clock.  The ESP32 stream is what puts multi-slot packets on the air
from the ESP32 once it is master.

    bt_spp_sink.py <bd_addr> <logfile>

Signals from the script's marker pauses:
    SIGUSR1  send b'P' -> the sketch stops writing
    SIGUSR2  send b'G' -> the sketch resumes
SIGTERM / SIGINT: log the totals and exit.

The channel is not assumed: 1..30 are tried until one connects.  The connect pairs the
link (Just Works) and encrypts it - see the comment at the socket.  The sketch ignores
everything but P and G.
"""
import signal
import socket
import sys
import time

addr, logfile = sys.argv[1], sys.argv[2]
log = open(logfile, "a", buffering=1)


def say(msg):
    log.write("%.6f %s\n" % (time.time(), msg))


want_pause = want_go = want_stop = False


def on_usr1(*_):
    global want_pause
    want_pause = True


def on_usr2(*_):
    global want_go
    want_go = True


def on_term(*_):
    global want_stop
    want_stop = True


signal.signal(signal.SIGUSR1, on_usr1)
signal.signal(signal.SIGUSR2, on_usr2)
signal.signal(signal.SIGTERM, on_term)
signal.signal(signal.SIGINT, on_term)

sock = None
for ch in range(1, 31):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    # Security is left at the kernel's default. Tried and failed 2026-11-08: BT_SECURITY level 0 (SDP) makes the
    # ESP32 refuse the RFCOMM channel and hang up with "Authentication Failure" (EACCES here). At the default
    # level the Pi authenticates by Simple Pairing (Just Works) and encrypts the link - so an SPP run is NOT
    # unpaired or unencrypted, and its btsnoop holds a link key (the Pi does not store it; never print it).
    s.settimeout(4.0)
    try:
        s.connect((addr, ch))
        sock, chan = s, ch
        break
    except OSError as e:
        say("channel %d: %s" % (ch, e))
        s.close()
        if want_stop:
            sys.exit(1)
if sock is None:
    say("no RFCOMM channel connected")
    sys.exit(2)
say("connected channel=%d" % chan)
sock.settimeout(0.2)

total = 0
sec_bytes = 0
sec_t = int(time.time())
while not want_stop:
    if want_pause:
        want_pause = False
        sock.send(b"P")
        say("sent P (pause)")
    if want_go:
        want_go = False
        sock.send(b"G")
        say("sent G (go)")
    try:
        d = sock.recv(65536)
        if not d:
            say("closed by peer")
            break
        total += len(d)
        sec_bytes += len(d)
    except socket.timeout:
        pass
    except OSError as e:
        say("recv error: %s" % e)
        break
    now = int(time.time())
    if now != sec_t:
        say("bytes_in_second=%d t=%d" % (sec_bytes, sec_t))
        sec_t, sec_bytes = now, 0
say("total_bytes=%d" % total)
sock.close()
