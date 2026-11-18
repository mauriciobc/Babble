#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercises the spell popover, cursor offsets and the pasted-image prune
with a stub client."""

import json
import os
import shutil
import sys
import tempfile
import time
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

# Pasted images are kept under the user cache: the test's go to a
# throwaway one. Before GLib is loaded, as it reads the variable once.
cache_home = tempfile.mkdtemp(prefix='babble-test-cache-')
os.environ['XDG_CACHE_HOME'] = cache_home

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GtkSource', '5')
gi.require_version('Spelling', '1')

from gi.repository import (Adw, Gdk, Gio, GLib, Gtk, GtkSource, Spelling)  # noqa: E402

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
srcroot = os.path.dirname(os.path.abspath(srcdir))
sys.path.insert(0, srcroot)
sys.path.insert(0, srcdir)

builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.dirname(
    os.environ.get('GSETTINGS_SCHEMA_DIR')
    or os.path.join(srcroot, 'build', 'data'))
Gio.Resource.load(os.path.join(builddir, 'src', 'babble.gresource'))._register()

Adw.init()
Gtk.init()

from testkit import composekit as kit  # noqa: E402
from testkit.composekit import (  # noqa: E402
    PNG_BYTES, check, cursor_offset, failures, local_settings, media_dir,
    pump, set_text, settle_until)
from src import composeattach
from src import compose
from src.compose import BabbleComposeView
app_window, dialog = kit.new_dialog()

view = kit.view
client = kit.client

# Spell checking: libspelling on the composer's buffer, in the language
# picked for the post.
adapter = view.spell_adapter
check('the composer buffer is a GtkSource.Buffer with no highlighting',
      isinstance(view.buffer, GtkSource.Buffer)
      and not view.buffer.get_highlight_syntax()
      and not view.buffer.get_highlight_matching_brackets()
      and view.buffer.get_style_scheme() is None)
check('the spell adapter checks the composer buffer',
      adapter.get_buffer() == view.buffer)
check('the corrections are the text view context menu extra',
      view.text_view.get_extra_menu() == adapter.get_menu_model())
spell_was_on = adapter.get_enabled()
check('the spelling actions reach the text view',
      view.text_view.activate_action('spelling.enabled', None)
      and adapter.get_enabled() != spell_was_on)
view.text_view.activate_action('spelling.enabled', None)
check('spell checking is back as it was',
      adapter.get_enabled() == spell_was_on)
check('each composer has a checker of its own',
      adapter.get_checker() != Spelling.Checker.get_default()
      and adapter.get_checker() != dialog.view.spell_adapter.get_checker())


def spell_click_at(offset):
    """Right-click the text view at character *offset*, through the
    composer's own capture-phase gesture."""
    controllers = view.text_view.observe_controllers()
    gesture = next(
        controllers.get_item(i) for i in range(controllers.get_n_items())
        if isinstance(controllers.get_item(i), Gtk.GestureClick)
        and controllers.get_item(i).get_button() == Gdk.BUTTON_SECONDARY)
    rect = view.text_view.get_iter_location(
        view.buffer.get_iter_at_offset(offset))
    x, y = view.text_view.buffer_to_window_coords(
        Gtk.TextWindowType.WIDGET, rect.x + 1, rect.y + rect.height // 2)
    gesture.emit('pressed', 1, float(x), float(y))





set_text('first tset last')
view.buffer.place_cursor(view.buffer.get_end_iter())
pump(100)
spell_click_at(7)
check('a right click puts the cursor on the word clicked',
      cursor_offset() == 7 and not view.buffer.get_has_selection())
view.buffer.select_range(view.buffer.get_iter_at_offset(6),
                         view.buffer.get_iter_at_offset(10))
spell_click_at(8)
check('a right click in the selection keeps it',
      view.buffer.get_has_selection() and cursor_offset() in (6, 10))
spell_click_at(1)
check('a right click out of the selection drops it',
      cursor_offset() == 1 and not view.buffer.get_has_selection())
set_text('')

check('a dictionary named as the language is taken as is',
      compose.spelling_language('pt', ['pt', 'pt_BR'], ['pt_BR']) == 'pt')
check('a regional dictionary of the user locale is preferred',
      compose.spelling_language(
          'pt', ['en_US', 'pt_BR', 'pt_PT'],
          ['pt_PT.UTF-8', 'pt_PT', 'pt.UTF-8', 'pt', 'C']) == 'pt_PT')
check('else the first regional dictionary',
      compose.spelling_language('pt', ['pt_PT', 'pt_BR', 'en_US'],
                                ['en_US', 'C']) == 'pt_BR')
check('a dictionary variant counts as the language',
      compose.spelling_language('pt', ['pt_PT-preao'], []) == 'pt_PT-preao')
check('a language without dictionary has none',
      compose.spelling_language('ja', ['en_US', 'jv'], ['ja_JP']) is None)

provider = Spelling.Provider.get_default()
dictionaries = provider.list_languages()
available = [dictionaries.get_item(i).get_code()
             for i in range(dictionaries.get_n_items())]
codes = [lang[0] for lang in compose.LANGUAGES]
covered = [code for code in codes
           if compose.spelling_language(code, available)]
uncovered = [code for code in codes if code not in covered]
if not covered:
    print('# no spelling dictionary installed: only turning spell checking '
          'off is exercised')
for code in covered[:2]:
    view.language_row.set_selected(codes.index(code))
    expected = compose.spelling_language(code, available,
                                         GLib.get_language_names())
    check('the spelling language follows the language row (%s: %s)'
          % (code, expected),
          adapter.get_language() == expected and adapter.get_enabled())
for code in uncovered[:1]:
    view.language_row.set_selected(codes.index(code))
    check('a language with no dictionary turns spell checking off (%s)'
          % code, not adapter.get_enabled())
if covered:
    view.language_row.set_selected(codes.index(covered[0]))
    check('a language with a dictionary turns it back on',
          adapter.get_enabled())
    # Checked on the plain Gtk.TextView: no GtkSource.View is needed.
    set_text('xqzvkj and more')
    view.buffer.place_cursor(view.buffer.get_end_iter())
    check('a misspelled word gets underlined', settle_until(
        3.0, lambda: view.buffer.get_start_iter().has_tag(
            adapter.get_tag())))
    set_text('')
# The drop-down always keeps an item selected; none selected is played
# by shadowing its getter.
view.language_row.get_selected = lambda: Gtk.INVALID_LIST_POSITION
view._update_spell_language()
del view.language_row.get_selected
default_code = provider.get_default_code()
check('with no language picked, the default dictionary is used',
      adapter.get_enabled() == bool(default_code)
      and (not default_code or adapter.get_language() == default_code))
view.reset()

# Text shared from outside the app starts a post, keeping the draft.
shared = BabbleComposeView(client)
shared.prefill_text('shared words')
check('prefill_text fills an empty draft',
      shared.text() == 'shared words'
      and not shared.reply_banner.get_revealed())
shared.prefill_text('another share')
check('prefill_text keeps the draft in progress',
      shared.text() == 'another share'
      and shared.reply_banner.get_revealed())
shared.pop_reply_state()
check('the kept draft comes back', shared.text() == 'shared words')
del shared

# Pasted images outlive their rows, for undo and saved drafts; the old
# ones no saved draft refers to are pruned.
pasted_dir = composeattach.pasted_dir()
week_ago = time.time() - composeattach.PASTED_MAX_AGE - 60
old_loose = os.path.join(pasted_dir, 'old loose.png')
old_saved = os.path.join(pasted_dir, 'old saved.png')
os.makedirs(pasted_dir, exist_ok=True)
for path in (old_loose, old_saved):
    with open(path, 'wb') as f:
        f.write(PNG_BYTES)
    os.utime(path, (week_ago, week_ago))
local_settings.set_strv('local-drafts', [json.dumps(
    {'attachments': [{'path': old_saved, 'alt': ''}]})])
recent_pasted = os.path.join(pasted_dir, 'recent.png')
with open(recent_pasted, 'wb') as f:
    f.write(PNG_BYTES)
composeattach.prune_pasted_images()
check('an old pasted image no draft refers to is pruned',
      not os.path.exists(old_loose))
check('an old pasted image a saved draft refers to is kept',
      os.path.isfile(old_saved))
check('a recent pasted image is kept', os.path.isfile(recent_pasted))
local_settings.set_strv('local-drafts', [])
shutil.rmtree(media_dir, ignore_errors=True)
shutil.rmtree(cache_home, ignore_errors=True)

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all compose flow checks passed')

if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all compose spell checks passed')
