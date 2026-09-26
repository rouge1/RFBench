#!/usr/bin/env python3
"""Check that settings writes merge and remain atomic.

This test uses only temporary files. It also guards the application boundary:
the launcher and Settings dialog may read JSON directly, but they must route
writes through ``apps.utils.update_app_config``.
"""

import ast
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apps import utils  # noqa: E402


FAILURES = []


def check(condition, message):
    if condition:
        print(f"  ok   {message}")
    else:
        print(f"  FAIL {message}")
        FAILURES.append(message)


def dump_calls(path):
    with open(path, encoding='utf-8') as fh:
        tree = ast.parse(fh.read(), filename=path)
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if (isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == 'json'
                and function.attr == 'dump'):
            calls.append(node.lineno)
    return calls


def main():
    print("settings write paths")
    check(not dump_calls(os.path.join(ROOT, 'RFbenchToolkit.py')),
          "the launcher has no direct JSON dumps")
    check(not dump_calls(os.path.join(ROOT, 'apps', 'settings_dialog.py')),
          "Settings has no direct JSON dumps")

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'nested', 'settings.json')
        os.makedirs(os.path.dirname(path))
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'keep': 'this', 'old': 7}, fh)

        result = utils.update_app_config(path, {'new': 9})
        check(result == {'keep': 'this', 'old': 7, 'new': 9},
              "new settings merge with existing keys")
        with open(path, encoding='utf-8') as fh:
            check(json.load(fh) == result, "merged settings are valid JSON")
        check(not os.path.exists(path + '.tmp'),
              "the temporary file is replaced after writing")

        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('{ not valid json')
        result = utils.update_app_config(path, {'recovered': True})
        check(result == {'recovered': True},
              "a corrupt settings file is replaced with the requested keys")

        migration = os.path.join(folder, 'migration')
        os.makedirs(migration)
        legacy = os.path.join(migration, 'legacy.json')
        current = os.path.join(migration, 'current.json')
        with open(legacy, 'w', encoding='utf-8') as fh:
            json.dump({'legacy': True, 'shared': 'legacy'}, fh)
        with open(current, 'w', encoding='utf-8') as fh:
            json.dump({'current': True, 'shared': 'current'}, fh)
        utils.adopt_legacy_config(migration, 'legacy.json', current)
        with open(current, encoding='utf-8') as fh:
            migrated = json.load(fh)
        check(migrated == {
            'legacy': True, 'current': True, 'shared': 'current'},
              "legacy settings merge with current settings")
        check(os.path.exists(legacy + '.migrated'),
              "legacy settings are renamed after migration")

        retired = os.path.join(folder, 'retired.json')
        with open(retired, 'w', encoding='utf-8') as fh:
            json.dump({'ip_addresses': ['192.168.10.2'], 'radio_mode': 'multi',
                       'theme': 'walnut'}, fh)
        result = utils.update_app_config(
            retired, {'usrp_ip': '192.168.10.2'},
            remove=('ip_addresses', 'radio_mode'))
        check(result == {'theme': 'walnut', 'usrp_ip': '192.168.10.2'},
              "a retired setting is dropped without taking another window's")

        real_config = utils.CONFIG_DIR
        try:
            utils.CONFIG_DIR = os.path.join(folder, 'config')
            os.makedirs(utils.CONFIG_DIR)
            settings_path = os.path.join(utils.CONFIG_DIR, 'window_settings.json')
            with open(settings_path, 'w', encoding='utf-8') as fh:
                json.dump({}, fh)
            before = os.stat(settings_path).st_mtime_ns
            settings = utils.read_settings()
            check(settings == {'media_directory': '', 'usrp_ip': '',
                               'radio_type': 'hackrf'},
                  "read_settings supplies missing defaults")
            check(os.stat(settings_path).st_mtime_ns == before,
                  "read_settings only reads, and never writes the file back")

            with open(settings_path, 'w', encoding='utf-8') as fh:
                json.dump({'ip_addresses': ['192.168.10.2', '192.168.10.3']}, fh)
            settings = utils.read_settings()
            check(settings['usrp_ip'] == '192.168.10.2',
                  "the first of a legacy ip_addresses list becomes usrp_ip")
            check('ip_addresses' not in settings and 'radio_mode' not in settings,
                  "the retired keys are not passed on to callers")
        finally:
            utils.CONFIG_DIR = real_config

    if FAILURES:
        print(f"\n{len(FAILURES)} checks failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
