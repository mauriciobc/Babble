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
import tempfile
import xml.etree.ElementTree as ET

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gio  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
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
from src import window as babble_window  # noqa: E402
from src.window import APP_ID, DOCS_URL, HELP_URI, BabbleWindow  # noqa: E402

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
check('F9 toggles the sidebar',
      app.get_accels_for_action('win.toggle-sidebar') == ['F9'],
      repr(app.get_accels_for_action('win.toggle-sidebar')))

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

# Whether the pages are reachable is asked before launching, because a
# launch answers wrong: with no viewer installed the OpenURI portal puts
# up "no apps available" and reports success, so the error path never
# runs. The lookup has to find the document id under a language in some
# datadir, not merely a help/ directory.
with tempfile.TemporaryDirectory() as data_dir:
    check('an empty datadir has no help pages',
          babble_window.help_pages_installed([data_dir]) is False)
    os.makedirs(os.path.join(data_dir, 'help', 'C', APP_ID))
    check('a language directory alone is not a page',
          babble_window.help_pages_installed([data_dir]) is False)
    os.makedirs(os.path.join(data_dir, 'help', 'C', 'org.example.Other'))
    check('another application\'s pages are not ours',
          babble_window.help_pages_installed([data_dir]) is False)
    with open(os.path.join(data_dir, 'help', 'C', APP_ID, 'index.page'),
              'w', encoding='utf-8') as page_file:
        page_file.write('<page/>')
    check('the pages are found in a datadir that has them',
          babble_window.help_pages_installed([data_dir]) is True)

# Both halves are required, and neither is the other's stand-in: pages
# with no viewer are as dead as a viewer with no pages.
saved_viewer = babble_window.help_viewer
saved_pages = babble_window.help_pages_installed
try:
    babble_window.help_viewer = lambda: None
    babble_window.help_pages_installed = lambda *a: True
    check('pages without a viewer are not help',
          babble_window.help_available() is False)
    babble_window.help_viewer = lambda: object()
    babble_window.help_pages_installed = lambda *a: False
    check('a viewer without pages is not help',
          babble_window.help_available() is False)
    babble_window.help_pages_installed = lambda *a: True
    check('a viewer and the pages are help',
          babble_window.help_available() is True)
finally:
    babble_window.help_viewer = saved_viewer
    babble_window.help_pages_installed = saved_pages

page = os.path.join(srcdir, '..', 'data', 'help', 'C',
                    'io.github.mauriciobc.Babble', 'index.page')
check('the help page is in the source tree', os.path.exists(page), page)
if os.path.exists(page):
    help_dir = os.path.dirname(page)
    root = ET.parse(page).getroot()
    sections = [s.get('id') for s in root.iter(
        '{http://projectmallard.org/1.0/}section')]
    check('the help page has sections', len(sections) >= 3, repr(sections))
    check('the help page covers signing in', 'start' in sections,
          repr(sections))

# Each section on the contents page has its own topic page, which is
# where the section text lives after the split; otherwise Yelp shows an
# empty page for each section.
    for section in sections:
        check('section %r has a topic page' % section,
              os.path.isfile(os.path.join(help_dir, section + '.page')),
              os.path.join(help_dir, section + '.page'))
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

# The bug this guards: on a host without Yelp (or from a build tree,
# which has no installed pages) F1 put up the portal's "no apps
# available" dialog, and pressing Cancel was what opened the docs. The
# fallback has to be decided before the launch, not after it fails.
opened = []
saved_open_url = win.open_url
saved_help_available = babble_window.help_available
win.open_url = opened.append
babble_window.help_available = lambda: False
try:
    win.open_help()
finally:
    win.open_url = saved_open_url
    babble_window.help_available = saved_help_available
check('F1 opens the online docs when help cannot resolve',
      opened == [DOCS_URL], repr(opened))

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
