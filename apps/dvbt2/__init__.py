"""DVB-T2 for the toolkit: a receiver written from the standard, and the transmitter's
frame planning, copied from skinny-fm-receiver at commit 71c2827 (2026-11-22), where they
were built and tested over the air (its ``knowledge/dvbt2.md``; ours is
``knowledge/dvbt2.md`` too).

- ``iqt2``, ``iqt2_tables``: P1, the OFDM front end and L1 (EN 302 755), numpy only.
- ``iqt2_data``: cells to BBFRAMEs to the transport stream; ``iqt2_native`` is its LDPC in
  native code, built per machine by ``scripts/build_dvbt2_native.py`` (``native/``,
  xdsopl/LDPC, 0BSD; the built library is never committed). Without it the numpy decoder
  runs, far slower than the air.
- ``iqt2_live``: the live receiver, frames decoded in spawned worker processes.
- ``dvb_si`` on ``atsc_core``: service names and now/next (EN 300 468).
- ``t2_tx``: the frame plan (capacity and rates) and gr-dtv's T2 transmitter.

Changes from the copy: ``iqt2._p1_metric`` is brought in from skinny-fm's ``iqmeasure``,
the build script's paths, and ``iqt2_live._watch_parent`` (each worker ends when its parent
does: orphaned, they held the window test's pipe open for ever). Fixes belong in both repos: tell skinny-fm.
"""
