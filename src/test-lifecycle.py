#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The application shell: its actions, its dialogs and its help.

Three things here are easy to get quietly wrong and hard to notice:

* ``app.shortcuts`` is not ours to register. AdwApplication builds it
  from the ``shortcuts-dialog.ui`` resource, so an action of that name
  added here replaces libadwaita's with one that behaves the same but is
  a second copy to keep in step (ARCH-14).
* A preferences dialog per Ctrl+, stacks windows, each with its own
  view of the same settings.
* F1 is Help, and Ctrl+? is the shortcuts dialog. Getting those the
  wrong way round means the help pages are unreachable.

The window needs its gresource (``window.ui``) registered first, which
the launcher does from the installed bundle; here it comes from the
build tree, so the test needs a configured build directory."""

import os
import sys
import xml.etree.ElementTree as ET

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gio  # noqa: E402

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
builddir = os.environ.get('BABBLE_BUILDDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        print('FAIL - %s%s' % (name, (' [%s]' % detail) if detail else ''))
        failures.append(name)


def load_bundle():
    """Register babble.gresource the way the installed launcher does."""
    for candidate in (os.path.join(builddir, 'src', 'babble.gresource'),
                      os.path.join(os.path.dirname(srcdir), 'build-review',
                                   'src', 'babble.gresource')):
        if os.path.exists(candidate):
            Gio.Resource.load(candidate)._register()
            return True
    return False


if not load_bundle():
    print('SKIP - no compiled babble.gresource; run meson compile first')
    sys.exit(77)

from src import main as babble_main  # noqa: E402
from src import preferences  # noqa: E402
from src.window import DOCS_URL, HELP_URI, BabbleWindow  # noqa: E402

app = babble_main.BabbleApplication(version='test')

# --- app.shortcuts belongs to libadwaita ------------------------------
# list_actions() only answers once the application is registered, which
# is also the moment libadwaita has added its own, so "did we add ours"
# cannot be read back off the app: the source is the thing to check.
# This is the same shape as the capability-gate call-sites test.
main_source = open(os.path.join(srcdir, 'main.py'), encoding='utf-8').read()
check('the app does not register app.shortcuts itself',
      "create_action('shortcuts'" not in main_source
      and 'on_shortcuts_action' not in main_source,
      'libadwaita provides it; a second one replaces it')

app.register(None)
app.emit('startup')
check('libadwaita provides app.shortcuts from the resource',
      app.lookup_action('shortcuts') is not None)
check('Ctrl+? opens the shortcuts dialog',
      'question' in ' '.join(app.get_accels_for_action('app.shortcuts')),
      repr(app.get_accels_for_action('app.shortcuts')))

# --- F1 is Help, Ctrl+? is the shortcuts dialog ------------------------
# Looked up rather than enumerated. A menu item and an accelerator both
# resolve an action name through lookup, and for this application
# list_actions() leaves app.help out even though lookup finds it and the
# accelerator is registered against it -- so lookup is the answer that
# says whether the menu item will work.
check('app.help is registered', app.lookup_action('help') is not None)
check('F1 opens the help', app.get_accels_for_action('app.help') == ['F1'],
      repr(app.get_accels_for_action('app.help')))
check('F1 is not also bound to the shortcuts dialog',
      'F1' not in ' '.join(app.get_accels_for_action('app.shortcuts')))

# --- the help pages ---------------------------------------------------
# The scheme is asserted, not the whole literal: a pinned string only
# says that some URI was chosen, and the one I first chose (`yelp://`)
# was handled by nothing at all -- Yelp registers ghelp/help/info/man
# and no yelp handler, so the launch always failed and the pages were
# dead weight while the suite stayed green. Naming the scheme is what
# makes that failure a test failure.
check('the help URI uses a scheme a viewer registers',
      HELP_URI.startswith('help:'), HELP_URI)
check('the help document id is the application id',
      HELP_URI.split(':', 1)[1].split('/')[0]
      == 'io.github.mauriciobc.Babble', HELP_URI)
check('the fallback is the online documentation',
      DOCS_URL.startswith('https://'), DOCS_URL)
page = os.path.join(srcdir, '..', 'data', 'help', 'C',
                    'io.github.mauriciobc.Babble', 'index.page')
check('the help page is in the source tree', os.path.exists(page), page)
if os.path.exists(page):
    root = ET.parse(page).getroot()
    sections = [s.get('id') for s in root.iter(
        '{http://projectmallard.org/1.0/}section')]
    check('the help page has sections', len(sections) >= 3, repr(sections))
    check('the help page covers signing in', 'start' in sections,
          repr(sections))

# --- the menus --------------------------------------------------------
ui = ET.parse(os.path.join(srcdir, 'window.ui')).getroot()
for menu in ui.iter('menu'):
    labels = []
    for item in menu.iter('item'):
        for attribute in item.iter('attribute'):
            if attribute.get('name') in ('label', 'action'):
                labels.append((attribute.get('name'), attribute.text))
    actions = [text for name, text in labels if name == 'action']
    if 'app.about' not in actions:
        continue
    check('%s lists Help before About' % menu.get('id'),
          'app.help' in actions
          and actions.index('app.help') < actions.index('app.about'),
          repr(actions))
    check('%s has no Quit item' % menu.get('id'), 'app.quit' not in actions)

# --- one preferences dialog, not one per activation -------------------
win = BabbleWindow(application=app)
first = preferences.present_preferences(win)
second = preferences.present_preferences(win)
check('preferences reuses the open dialog', first is second)
check('preferences is searchable', first.get_search_enabled())
check('the open preferences dialog is the visible one',
      win.get_visible_dialog() is first)
check('the window has an open_help for the help action',
      callable(getattr(win, 'open_help', None)))

# --- links open in a column without loading WebKit ---------------------
# The web column is opt-in and WebKit is large, so nothing on the
# startup path may import it; it is loaded on the first link opened in
# the column, not before.
check('WebKit is not loaded by the window or preferences',
      'gi.repository.WebKit' not in sys.modules)

if failures:
    print('\n%d lifecycle check(s) failed: %s'
          % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('\nall lifecycle checks passed')
