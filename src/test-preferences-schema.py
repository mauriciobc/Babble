#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Smoke-test enum GSettings keys used by preference combo rows.

Schema enum values are 0-based so Adw.ComboRow.selected matches
get_enum/set_enum. Callers keep using get_string/set_string with nicks.
"""

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
os.environ.setdefault('GSETTINGS_SCHEMA_DIR',
                      os.path.join(os.path.dirname(srcdir), 'data'))

import gi  # noqa: E402

gi.require_version('Gio', '2.0')
from gi.repository import Gio  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' :: %s' % detail) if detail else ''))


# key -> nicks in schema / ComboRow order
ENUM_KEYS = {
    'color-scheme': ('system', 'light', 'dark'),
    'text-size': ('small', 'normal', 'large'),
    'media-viewer-mode': ('box', 'fullscreen'),
    'default-visibility': ('public', 'unlisted', 'private', 'direct'),
    'default-quote-policy': ('public', 'followers', 'nobody'),
}

settings = Gio.Settings.new('io.github.mauriciobc.Babble')

for key, nicks in ENUM_KEYS.items():
    settings.reset(key)
    for index, nick in enumerate(nicks):
        settings.set_string(key, nick)
        check('%s set_string(%s) → get_string' % (key, nick),
              settings.get_string(key) == nick)
        check('%s set_string(%s) → get_enum == %d' % (key, nick, index),
              settings.get_enum(key) == index)
        settings.set_enum(key, index)
        check('%s set_enum(%d) → get_string == %s' % (key, index, nick),
              settings.get_string(key) == nick)

prefs_path = os.path.join(srcdir, 'preferences.py')
with open(prefs_path, encoding='utf-8') as handle:
    prefs = handle.read()
check('preferences defines _combo_enum', 'def _combo_enum(' in prefs)
check('preferences syncs enums via get_enum/set_enum',
      'get_enum(key)' in prefs and 'set_enum(key, index)' in prefs)
check('preferences does not claim broken settings.bind for enums',
      "self.settings.bind(key, row, 'selected'," not in prefs)
for key in ENUM_KEYS:
    check('preferences uses _combo_enum for %s' % key,
          "_combo_enum(" in prefs and ("'%s'" % key) in prefs)
    check('preferences does not _combo_string %s' % key,
          ('_combo_string(appearance, %r' % key) not in prefs
          and ('_combo_string(posts, %r' % key) not in prefs
          and ('_combo_string(group, %r' % key) not in prefs)

# auto-refresh stays a string key (enum nick "0" is illegal).
settings.reset('auto-refresh')
settings.set_string('auto-refresh', '30')
check('auto-refresh string choice round-trips',
      settings.get_string('auto-refresh') == '30')
check('preferences keeps _combo_string for auto-refresh',
      "_combo_string(refresh, 'auto-refresh'" in prefs)

if os.environ.get('DISPLAY'):
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Adw, Gtk  # noqa: E402

    Adw.init()
    settings.reset('text-size')
    nicks = list(ENUM_KEYS['text-size'])
    row = Adw.ComboRow(model=Gtk.StringList.new(nicks))
    row.set_selected(settings.get_enum('text-size'))
    check('ComboRow selected matches default get_enum',
          row.get_selected() == nicks.index('normal'))
    row.set_selected(nicks.index('large'))
    settings.set_enum('text-size', row.get_selected())
    check('set_enum from ComboRow selected writes nick',
          settings.get_string('text-size') == 'large')
    settings.set_string('text-size', 'small')
    row.set_selected(settings.get_enum('text-size'))
    check('ComboRow selected follows get_enum after set_string',
          row.get_selected() == nicks.index('small'))
else:
    print('ok - skip ComboRow enum index (no DISPLAY)')

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
