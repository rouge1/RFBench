#!/usr/bin/env python3
"""Check every theme in ``apps/theme.py`` before anything is painted in it.

``apps/theme.py`` is the single source of the palette, the type scale and
the faces: the launcher, the config dialogs and the app windows take it as
Qt style sheets. This holds every theme to the contrast its colours are
there for, since a colour that reads on Slate can vanish on Reading Room's
paper, and nothing else would say so until somebody looked. It also checks
that each stylesheet comes out whole, that the faces ship with their
licences, and that the tile tables stay literals. No radio, no display, no
GNU Radio.

    python scripts/test_theme.py
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apps import theme  # noqa: E402

FAILURES = []

#: (colour, what it sits on, the least contrast it may have, why). Slate's
#: own figures set these; every theme has to meet them.
CONTRAST = [
    ('ink', ('ground', 'panel', 'panel_2', 'well'), 7.0, 'body text'),
    ('ink_2', ('ground', 'panel', 'panel_2', 'well'), 4.5, 'labels'),
    ('heading', ('ground',), 4.5, "a bank's name"),
    ('tag', ('panel',), 3.0, "a tile's TRANSMIT line"),
    ('ink_3', ('ground', 'panel'), 3.0,
     'the quietest thing still meant to be read'),
    ('live', ('ground', 'panel'), 4.5, 'the ON AIR heading'),
    ('warn', ('ground', 'panel'), 4.5, "a receiver's lock line"),
    ('good', ('ground', 'panel'), 4.5, "a receiver's lock line"),
    ('bad', ('ground', 'panel'), 4.5, "a receiver's lock line"),
    ('trace', ('well',), 3.0, 'a plotted signal'),
    ('ground', ('ink', 'ink_0'), 4.5, 'the OK button, and under the pointer'),
]


def lum(colour):
    """The WCAG relative luminance of a #rrggbb colour."""
    out = []
    for i in (1, 3, 5):
        v = int(colour[i:i + 2], 16) / 255
        out.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def contrast(a, b):
    """The WCAG contrast ratio of two #rrggbb colours."""
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def check(condition, message):
    print(('  ok   ' if condition else '  FAIL ') + message)
    if not condition:
        FAILURES.append(message)


def main():
    print('the themes')
    check(theme.DEFAULT == next(iter(theme.THEMES)),
          'the default is the first the disc shows')
    check(set(theme.NAMES) == set(theme.THEMES), 'every theme has a name')
    for key, palette in theme.THEMES.items():
        extra = (set(palette) - set(theme.PALETTE) - set(theme.EXTRAS)
                 - {'scheme', 'type'})
        missing = set(theme.PALETTE) - set(palette)
        check(not extra and not missing and palette.get('scheme') in
              ('dark', 'light'),
              f"{theme.NAMES[key]} defines every colour and nothing else"
              f"{'' if not missing else ': missing ' + ', '.join(sorted(missing))}"
              f"{'' if not extra else ': unknown ' + ', '.join(sorted(extra))}")
        if missing:
            continue
        low = []
        back, text, _edge = theme.ok_hover(palette)
        if contrast(text, back) < 4.5:
            low.append(f"OK under the pointer {contrast(text, back):.2f}:1")
        for fg, grounds, least, _why in CONTRAST:
            for bg in grounds:
                ratio = contrast(palette[fg], palette[bg])
                if ratio < least:
                    low.append(f"{fg} on {bg} {ratio:.2f}:1 < {least}")
        check(not low, f"{theme.NAMES[key]}'s colours read where they are used"
                       f"{'' if not low else ': ' + '; '.join(low)}")
        faces = palette.get('type', {})
        if faces:
            shipped = {family for family, _f in theme.FACES}
            check(set(faces) <= {'f_num', 'f_ui', 'qss_bold'} and
                  {faces.get('f_num', 'Barlow Semi Condensed'),
                   faces.get('f_ui', 'Barlow')} <= shipped,
                  f"{theme.NAMES[key]}'s faces are ones that ship in apps/fonts/")

    print('\ntokens -> Qt')
    for key in theme.THEMES:
        theme.use(key)
        sheets = (theme.launcher_qss(),
                  theme.dialog_qss('u.png', 'd.png', 'c.png'),
                  theme.flowgraph_qss('u.png', 'd.png', 'c.png'))
        check(all(theme.TOKENS['ground'] in s for s in sheets),
              f"all three stylesheets are painted in {theme.NAMES[key]} "
              f"when it is in force")
    theme.use(theme.DEFAULT)
    for name, sheet in (('launcher', theme.launcher_qss()),
                        ('dialog', theme.dialog_qss('u.png', 'd.png', 'c.png')),
                        ('flowgraph',
                         theme.flowgraph_qss('u.png', 'd.png', 'c.png'))):
        leftover = re.findall(r'%\([a-z_0-9]+\)s', sheet)
        check(not leftover,
              f"the {name} stylesheet has no unsubstituted token"
              f"{'' if not leftover else ': ' + ', '.join(sorted(set(leftover)))}")
        check(theme.TOKENS['ground'] in sheet or theme.TOKENS['panel'] in sheet,
              f"the {name} stylesheet carries the palette")

    print('\nthe faces')
    for _family, filename in theme.FACES:
        path = os.path.join(theme.FONT_DIR, filename)
        check(os.path.exists(path), f"{filename} is in apps/fonts/")
    for family in sorted({family for family, _f in theme.FACES}):
        licence = theme.LICENCES.get(family)
        check(bool(licence) and
              os.path.exists(os.path.join(theme.FONT_DIR, licence)),
              f"{family}'s licence ships beside it, as the OFL requires")

    print('\nthe tile tables')
    # scripts/test_launcher_gui.py and this script read these out of the
    # launcher's source with ast, so they have to stay plain literals.
    import ast
    source = open(os.path.join(ROOT, 'RFbenchToolkit.py')).read()
    found = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, 'id', None) in ('APP_TILES', 'BANK_NAMES'):
                    found[target.id] = ast.literal_eval(node.value)
    check(set(found) == {'APP_TILES', 'BANK_NAMES'},
          'APP_TILES and BANK_NAMES are literals ast can read')
    if len(found) == 2:
        rows = {row for row, _col, _faces in found['APP_TILES']}
        unnamed = sorted(rows - set(found['BANK_NAMES']))
        check(not unnamed,
              f"every row of tiles has a heading"
              f"{'' if not unnamed else ': ' + str(unnamed) + ' has none'}")

    print(f"\n{len(FAILURES)} failed" if FAILURES else '\nall checks passed')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
