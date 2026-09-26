#!/usr/bin/env python3
"""Run one app module on its own, owning the Qt event loop.

An app's ``main()`` ends with::

    if app.instance():
        return tb
    else:
        return app.exec_()

which is the launcher contract in CLAUDE.md - the launcher already owns a
QApplication and its loop, so ``main()`` hands back the top block rather
than blocking. The catch is that ``app.instance()`` is truthy the moment a
QApplication exists, so a module run directly takes that same branch: it
builds the flowgraph, shows the window, returns, and the window never gets
an event loop at all.

This is the piece the launcher supplies, for running one app without
it - on a bench machine over ssh, say, or from a test. It owns the
QApplication, calls ``main()`` exactly as ``launch_application`` does, and
runs the loop itself::

    python apps/_run.py amSineGenerator
    python apps/_run.py amSineGenerator --config /tmp/values.json

Without ``--config`` the flowgraph puts up the app's own ``ConfigDialog``,
so this is the same experience as clicking the tile in the grid. With
one, the dialog is skipped and the values are used as they stand. With
no screen - over ssh - set ``QT_QPA_PLATFORM=offscreen``: the window is
drawn into nothing, and the flowgraph and the radio run as normal.

SIGTERM or SIGINT - ``timeout``, ``kill``, Ctrl+C - stops the flowgraph
and ends the process, and if that stop is stuck, the process ends anyway
:data:`STOP_GRACE_S` later. Nothing is saved, as it would be had the
window been closed. See devnotes/ui.md for why this needs doing here.

Run it with the ``gnu`` environment's Python - it imports GNU Radio
through the app module.
"""

import argparse
import ctypes
import importlib.util
import json
import os
import signal
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: How often Python gets the main thread while the app runs, so a signal's
#: handler runs within this long of the signal. In milliseconds.
TICK_MS = 200

#: How long a signalled stop may take before the process ends regardless,
#: in seconds - for a radio whose block never returns from ``work()``.
STOP_GRACE_S = 5


def _init_x11_threads():
    """What each app's own ``__main__`` block does before Qt starts.

    Those blocks are guarded by ``__name__ == '__main__'``, so importing
    the module - which is what the launcher and this script do - skips
    them. It has to happen before anything opens a display connection.
    """
    if not sys.platform.startswith('linux'):
        return
    try:
        ctypes.cdll.LoadLibrary('libX11.so').XInitThreads()
    except Exception:
        print("Warning: failed to XInitThreads()", file=sys.stderr)


def load_app(module_name):
    """Import apps/<module_name>.py the way the launcher imports it."""
    path = os.path.join(ROOT, 'apps', f"{module_name}.py")
    if not os.path.isfile(path):
        raise SystemExit(f"_run: no such app module: apps/{module_name}.py")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec so a module that imports itself by name - and
    # so that any gr.sync_block it defines can be found later - resolves.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, 'main'):
        raise SystemExit(f"_run: apps/{module_name}.py has no main()")
    return module


def _hard_stop_after(seconds):
    """End this process ``seconds`` from now, whatever it is stuck in.

    ``alarm`` hands the job to the kernel: SIGALRM's default action ends
    the process, and needs no Python to run, so it works even with the
    main thread blocked inside a radio's library. Windows has no alarm, and
    a thread does it there.
    """
    if hasattr(signal, 'alarm'):
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(seconds)
    else:
        timer = threading.Timer(seconds, os._exit, args=(1,))
        timer.daemon = True
        timer.start()


def _stop_on_signals(qapp, tb):
    """Make SIGTERM and SIGINT stop the app, and return the timer that
    lets them - keep a reference to it.

    Python runs a signal's handler only when the main thread next runs
    Python, and under ``qapp.exec_()`` the main thread is in Qt's C++ event
    loop. Each app's ``main()`` starts a timer for exactly this, but holds
    it in a local, so it is collected as ``main()`` returns - and an app
    run here then ignored SIGTERM and SIGINT for good, still on the air
    minutes later. Two of the receivers install no handler at all. So the
    handler and the timer are this module's, and the same for every app.
    """
    from PyQt5 import Qt

    def stop(signum, frame):
        _hard_stop_after(STOP_GRACE_S)
        tb.stop()
        tb.wait()
        qapp.quit()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    ticker = Qt.QTimer()
    ticker.timeout.connect(lambda: None)
    ticker.start(TICK_MS)
    return ticker


def read_config(path):
    if not path:
        return None
    with open(path) as fh:
        values = json.load(fh)
    if not isinstance(values, dict):
        raise SystemExit("_run: --config must hold a JSON object")
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run one GNU Radio app module with its own Qt event loop.")
    parser.add_argument('module', help="module name under apps/, without .py")
    parser.add_argument('--config', metavar='FILE',
                        help="JSON config values; omit to show the app's own "
                             "configuration dialog")
    args = parser.parse_args(argv)

    # The apps find config/, apps/icons/ and apps/fonts/ from their own
    # files, not the working directory; the chdir only makes a relative
    # --config path resolve where the launcher's would.
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    os.chdir(ROOT)

    _init_x11_threads()

    from PyQt5 import Qt  # after XInitThreads, before any widget exists
    from apps.utils import (flowgraph_settings, restore_window_geometry,
                            save_flowgraph_settings, save_window_geometry)

    # argv[:1] so the app module never re-parses our arguments as Qt's.
    qapp = Qt.QApplication(sys.argv[:1])

    values = read_config(args.config)
    module = load_app(args.module)

    # Cancelling the dialog is sys.exit(0) inside the flowgraph, so this
    # returns only when there is something to run.
    tb = module.main(app=qapp, config_values=values)
    if tb is None:
        return 0

    # The same window geometry the launcher keeps, so an app run this way
    # comes back where it was left too. After main(), which is what shows
    # the window - see apps/utils.py: restore_window_geometry.
    if hasattr(tb, 'move'):
        restore_window_geometry(tb, args.module, qapp)
    if hasattr(tb, 'closeEvent'):
        original_close_event = tb.closeEvent
        opened_with = flowgraph_settings(tb)

        def closed(event):
            save_window_geometry(tb, args.module)
            save_flowgraph_settings(tb, args.module, since=opened_with)
            original_close_event(event)

        tb.closeEvent = closed

    ticker = _stop_on_signals(qapp, tb)  # noqa: F841 - held for exec_()
    return qapp.exec_()


if __name__ == '__main__':
    sys.exit(main())
