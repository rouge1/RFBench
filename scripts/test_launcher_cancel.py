#!/usr/bin/env python3
"""Drive a launcher tile, click Cancel, and check its save path.

The launcher runs from an isolated temporary checkout so this test never
touches the user's ``config/``. It uses real Qt mouse clicks rather than
calling ``accept`` or ``reject`` directly:

    QT_QPA_PLATFORM=offscreen python scripts/test_launcher_cancel.py
"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import QtCore, QtWidgets  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402

from RFbenchToolkit import RFbenchToolkit  # noqa: E402


def prepare_workspace(folder):
    for name in ('apps', 'fonts', 'icons'):
        shutil.copytree(
            os.path.join(ROOT, name),
            os.path.join(folder, name),
            ignore=shutil.ignore_patterns('__pycache__'),
        )
    shutil.copy2(os.path.join(ROOT, 'RFbenchToolkit.py'),
                 os.path.join(folder, 'RFbenchToolkit.py'))
    os.makedirs(os.path.join(folder, 'config'))
    with open(os.path.join(folder, 'config', 'window_settings.json'), 'w',
              encoding='utf-8') as fh:
        json.dump({
            'media_directory': '',
            'ip_addresses': [],
            'radio_type': 'hackrf',
            'radio_mode': 'single',
            'theme': 'slate',
        }, fh)
    with open(os.path.join(folder, 'config', 'amSineGenerator_config.json'),
              'w', encoding='utf-8') as fh:
        json.dump({'sentinel': 'preserve'}, fh)


def main():
    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as folder:
        prepare_workspace(folder)
        os.chdir(folder)
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        launcher = RFbenchToolkit(app)
        launcher.show()
        result = {'clicked_tile': False, 'clicked_cancel': False,
                  'error': None}

        def click_tile():
            tiles = [button for button in launcher.findChildren(QtWidgets.QPushButton)
                     if button.accessibleName() == 'AM Sine Generator']
            if len(tiles) != 1:
                result['error'] = f'expected one AM Sine tile, found {len(tiles)}'
                app.quit()
                return
            result['clicked_tile'] = True
            QTest.mouseClick(tiles[0], QtCore.Qt.LeftButton)

        def click_cancel():
            dialog = app.activeModalWidget()
            if dialog is None:
                QtCore.QTimer.singleShot(50, click_cancel)
                return
            buttons = dialog.findChild(QtWidgets.QDialogButtonBox)
            cancel = buttons.button(QtWidgets.QDialogButtonBox.Cancel)
            if cancel is None:
                result['error'] = 'configuration dialog has no Cancel button'
                app.quit()
                return
            result['clicked_cancel'] = True
            QTest.mouseClick(cancel, QtCore.Qt.LeftButton)

        def finish():
            if app.activeModalWidget() is not None:
                QtCore.QTimer.singleShot(50, finish)
                return
            launcher.close()
            app.quit()

        QtCore.QTimer.singleShot(100, click_tile)
        QtCore.QTimer.singleShot(250, click_cancel)
        QtCore.QTimer.singleShot(1000, finish)
        app.exec_()

        with open(os.path.join(folder, 'config',
                               'amSineGenerator_config.json'),
                  encoding='utf-8') as fh:
            saved = json.load(fh)

        checks = {
            'the launcher tile was clicked': result['clicked_tile'],
            'the dialog Cancel button was clicked': result['clicked_cancel'],
            'the cancel path returned without an error': result['error'] is None,
            'existing app settings were preserved': saved.get('sentinel') == 'preserve',
            'dialog position was saved after Cancel':
                isinstance(saved.get('dialog_position'), dict),
            'Cancel did not launch a flowgraph': 'flowgraph_position' not in saved,
        }
        os.chdir(old_cwd)
        for message, passed in checks.items():
            print(f"  {'ok  ' if passed else 'FAIL'} {message}")
        if not all(checks.values()):
            if result['error']:
                print(f"error: {result['error']}")
            return 1
    print("\nlauncher Cancel path passed")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
