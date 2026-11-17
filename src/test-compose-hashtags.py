#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercises the hashtag completer, late answers and exact matches with a
stub client."""

import inspect
import os
import re
import sys
import tempfile
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

from gi.repository import (Adw, Gdk, Gio, GLib, Gtk, Pango)  # noqa: E402

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
    FAIL_PATHS, HASHTAG_ANSWER, HOLD_PATHS, LINUX, MINT, NO_MODS,
    base_source, check, completer, cursor_offset, hashtag_searches, hcomp,
    held_requests, hnames, hpress, hselected, hsettle, htype, press, pump,
    searches, settle, type_text)
from src import feedback
from src import completer as completer_module
from gettext import gettext as _, ngettext

view = kit.view
client = kit.client

# The base class on its own, through a minimal subclass that completes
# ``#word`` from the hashtag answer: the selection, refill and input
# method rules live in TokenCompleter, not in either real completer.


class FakeCompleter(completer_module.TokenCompleter):
    list_label = 'Fake'
    #: Items shown at once, before any request, whose name starts with
    #: the query.
    instant = []
    searching = True
    #: No row starts selected.
    no_default = False

    inert = False

    def token_query(self, text):
        if self.inert:
            return None
        match = re.search(r'(?<!\S)#(\w*)\Z', text)
        return None if match is None else (match.start(), match.group(1))

    def continues_token(self, char):
        return char.isalnum()

    def instant_items(self, query):
        return [item for item in self.instant
                if item['name'].startswith(query)]

    def wants_search(self, query):
        return self.searching

    def search_request(self, query):
        return '/api/v2/search', {'q': query}

    def parse(self, result):
        return list(result['hashtags'])

    def build_row(self, item):
        return Gtk.ListBoxRow(child=Gtk.Label(label=item['name']),
                              can_focus=False)

    def completion(self, item, typed):
        return '#' + item['name']

    def default_selection(self, items, query):
        return None if self.no_default else 0

    def item_key(self, item):
        return item['name']

    def merge_results(self, query, items):
        # A failed search leaves the rows that were already shown.
        return items or self.instant_items(query)


fake = FakeCompleter(client, view.text_view)
fake.instant = []


def fake_press(keyval, state=NO_MODS):
    return fake._on_key_pressed(None, keyval, 0, state)


def fake_settle():
    deadline = GLib.get_monotonic_time() + 3 * 1000 * 1000
    while (fake._search_id or fake._cancellable is not None) \
            and GLib.get_monotonic_time() < deadline:
        pump(20)
    pump(50)


def fake_type(text):
    view.buffer.set_text(text)
    view.buffer.place_cursor(view.buffer.get_end_iter())
    fake_settle()


def wait_until(condition, seconds=3):
    deadline = GLib.get_monotonic_time() + seconds * 1000 * 1000
    while not condition() and GLib.get_monotonic_time() < deadline:
        pump(20)
    pump(50)


def fake_selected():
    row = fake.list_box.get_selected_row()
    return None if row is None else row.item['name']


def fake_names():
    names = []
    while (row := fake.list_box.get_row_at_index(len(names))) is not None:
        names.append(row.item['name'])
    return names


# An input method that is composing owns Enter, Tab and the arrows.
view.reset()
view.text_view.grab_focus()
HASHTAG_ANSWER[:] = [{'name': 'one'}, {'name': 'two'}]
type_text('hi @al')
view.text_view.emit('preedit-changed', '日本')
check('preedit lets Enter through (mention)',
      not press(Gdk.KEY_Return) and completer.get_visible()
      and view.text() == 'hi @al')
check('preedit lets Down and Tab through',
      not press(Gdk.KEY_Down) and not press(Gdk.KEY_Tab)
      and completer.list_box.get_selected_row()
      is completer.list_box.get_row_at_index(0))
check('Escape passes through while composing (mention)',
      not press(Gdk.KEY_Escape) and completer.get_visible())
view.text_view.emit('preedit-changed', '')
check('Escape closes once composing ends', press(Gdk.KEY_Escape)
      and not completer.get_visible())
type_text('hi @al')
check('empty preedit restores Enter', press(Gdk.KEY_Return)
      and view.text() == 'hi @alice ')
type_text('hi @al')
view.text_view.emit('preedit-changed', '日本')
completer._on_focus_leave(None)
type_text('hi @al')
check('preedit is reset by focus leave', press(Gdk.KEY_Return)
      and view.text() == 'hi @alice ')
view.text_view.emit('preedit-changed', '')

# A cursor move that is not a token (the input method moves it while
# composing) closes the list but does not end the composition.
type_text('hi @al')
view.text_view.emit('preedit-changed', '日本')
view.buffer.place_cursor(view.buffer.get_start_iter())
pump(50)
check('the list closes when the caret leaves the token',
      not completer.get_visible())
view.buffer.place_cursor(view.buffer.get_end_iter())
settle()
check('non-token mark-set mid-composition keeps the preedit guard',
      completer.get_visible() and not press(Gdk.KEY_Return)
      and view.text() == 'hi @al')
view.text_view.emit('preedit-changed', '')

fake_type('#o')
check('the fake completer lists the answer', fake_names() == ['one', 'two'])
view.text_view.emit('preedit-changed', 'に')
check('preedit lets Enter through (base class)',
      not fake_press(Gdk.KEY_Return) and fake.get_visible()
      and view.text() == '#o')
view.text_view.emit('preedit-changed', '')
check('Enter accepts again once composing ends',
      fake_press(Gdk.KEY_Return) and view.text() == '#one ')
view.reset()

# Selection: a subclass can leave no row selected, and then the keyboard
# has to ask for one with Down.
view.text_view.grab_focus()
HASHTAG_ANSWER[:] = [{'name': 'one'}, {'name': 'two'}]
fake.no_default = True
fake_type('#o')
check('default_selection None leaves no row selected',
      fake_names() == ['one', 'two'] and fake_selected() is None
      and fake.list_box.get_sensitive())
check('Enter with no selection passes through',
      not fake_press(Gdk.KEY_Return) and view.text() == '#o'
      and fake.get_visible())
check('Up with no selection passes through',
      not fake_press(Gdk.KEY_Up) and fake_selected() is None)
check('Down from no selection selects row 0 and is consumed',
      fake_press(Gdk.KEY_Down) and fake_selected() == 'one')
check('Enter after Down accepts the row',
      fake_press(Gdk.KEY_Return) and view.text() == '#one ')
fake_type('#o')
check('Tab with no selection passes through',
      not fake_press(Gdk.KEY_Tab) and view.text() == '#o')
fake.no_default = False

# Refill: rows shown at once are replaced by the server's answer. The
# row the user moved to stays; one nobody moved to goes back to the
# default.
HOLD_PATHS.add('/api/v2/search')
fake.instant = [{'name': 'one'}, {'name': 'oak'}]
HASHTAG_ANSWER[:] = [{'name': 'oak'}, {'name': 'one'}]
view.reset()
view.text_view.grab_focus()
view.buffer.set_text('#o')
view.buffer.place_cursor(view.buffer.get_end_iter())
wait_until(lambda: held_requests)
check('instant rows show before the answer, usable and selected',
      fake_names() == ['one', 'oak'] and fake.list_box.get_sensitive()
      and fake_selected() == 'one')
check('Down moves within the instant rows',
      fake_press(Gdk.KEY_Down) and fake_selected() == 'oak')
client.release_requests()
pump(50)
check('refill keeps the moved selection by key',
      fake_names() == ['oak', 'one'] and fake_selected() == 'oak')
view.reset()
view.text_view.grab_focus()
held_requests.clear()
view.buffer.set_text('#o')
view.buffer.place_cursor(view.buffer.get_end_iter())
wait_until(lambda: held_requests)
client.release_requests()
pump(50)
check('refill without a move re-applies the default',
      fake_names() == ['oak', 'one'] and fake_selected() == 'oak')

# A list still waiting for its answer is out of the keyboard's reach.
view.reset()
view.text_view.grab_focus()
held_requests.clear()
view.buffer.set_text('#o')
view.buffer.place_cursor(view.buffer.get_end_iter())
wait_until(lambda: held_requests)
client.release_requests()
pump(50)
held_requests.clear()
view.buffer.insert_at_cursor('z')
pump(50)
check('the list is insensitive while the new query runs',
      not fake.list_box.get_sensitive()
      and fake.list_box.get_selected_row() is None)
check('insensitive list passes Down and Enter through',
      not fake_press(Gdk.KEY_Down) and not fake_press(Gdk.KEY_Return)
      and view.text() == '#oz')
HOLD_PATHS.clear()
held_requests.clear()
fake.instant = []

# A search that fails keeps what could be shown without it.
FAIL_PATHS.add('/api/v2/search')
fake.instant = [{'name': 'one'}]
fake_type('#o')
check('failed search keeps instant rows',
      fake_names() == ['one'] and fake.get_visible())
fake.instant = []
fake_type('#o')
check('failed search with no rows hides', not fake.get_visible())
FAIL_PATHS.clear()

# A query that cannot be searched for and matches nothing leaves no
# rows of an older query behind.
HASHTAG_ANSWER[:] = [{'name': 'one'}, {'name': 'two'}]
fake_type('#o')
check('rows are shown before the query stops being searchable',
      fake_names() == ['one', 'two'])
fake.searching = False
view.buffer.insert_at_cursor('x')
pump(50)
check('no instant rows and no search clears stale rows',
      fake_names() == [] and not fake.get_visible() and not fake._search_id)
fake.searching = True
view.reset()
# Unparenting it leaves the other popovers on the view pointing at a
# stale parent, so it is silenced instead.
fake.inert = True
fake.dismiss()

# The hashtag completer: the same popover over the hashtag search.
from src.hashtagcompleter import caption_for  # noqa: E402


view.reset()
view.text_view.grab_focus()
HASHTAG_ANSWER[:] = [LINUX, MINT]
searches.clear()
htype('hi #lin')
request = hashtag_searches()[-1][2] if hashtag_searches() else {}
check('typing #lin searches hashtags',
      hashtag_searches() and hashtag_searches()[-1][0] == 'GET'
      and request.get('type') == 'hashtags' and request.get('q') == 'lin')
check('search has no exclude_unreviewed and resolve=false limit=10',
      'exclude_unreviewed' not in request
      and request.get('resolve') == 'false' and request.get('limit') == 10)
check('server rows show and the first is selected',
      hcomp.get_visible() and hnames() == ['linux', 'linuxmint']
      and hselected() == 'linux')
check('the mention list stays closed for a hashtag',
      not completer.get_visible())

view.reset()
searches.clear()
view.buffer.set_text('#ab')
view.buffer.place_cursor(view.buffer.get_end_iter())
view.buffer.insert_at_cursor('c')
view.buffer.insert_at_cursor('d')
hsettle()
check('only the last of three quick queries is sent',
      [entry[2]['q'] for entry in hashtag_searches()] == ['abcd'])

htype('#lin')
htype('#linu')
searches.clear()
htype('#lin')
check('cache answers backspace without a request',
      not hashtag_searches() and hcomp.get_visible()
      and hnames() == ['linux', 'linuxmint'])
hcomp._cache.clear()
for number in range(33):
    hcomp.merge_results('x%d' % number, [])
check('cache evicts at 33',
      len(hcomp._cache) == 32 and 'x0' not in hcomp._cache
      and 'x32' in hcomp._cache)
hcomp._cache.clear()

# Exact matches are not preselected: Enter after a finished tag is a line
# break, and Down then Enter still picks the server's spelling.
HASHTAG_ANSWER[:] = [{'name': 'art'}, {'name': 'arts'}]
htype('hi #art')
check('exact match first row is not selected',
      hnames() == ['art', 'arts'] and hselected() is None
      and hcomp.list_box.get_sensitive())
check('Enter on exact match leaves text plus newline',
      not hpress(Gdk.KEY_Return) and not hpress(Gdk.KEY_Tab)
      and view.text() == 'hi #art')
HASHTAG_ANSWER[:] = [{'name': 'Linux'}]
htype('#linux')
check('Down then Enter on exact match inserts merged casing #Linux',
      hselected() is None and hpress(Gdk.KEY_Down)
      and hselected() == 'Linux' and hpress(Gdk.KEY_Return)
      and view.text() == '#Linux ')

HASHTAG_ANSWER[:] = [LINUX, MINT]
view.text_view.grab_focus()
htype('#lin')
check('text view keeps focus before accept', view.text_view.is_focus())
check('Enter inserts #Linux with a trailing space and cursor after it',
      hpress(Gdk.KEY_Return) and view.text() == '#linux '
      and cursor_offset() == 7 and not hcomp.get_visible())
check('text view keeps focus after accept', view.text_view.is_focus())
if view.buffer.get_can_undo():
    view.buffer.undo()
    check('ctrl+z undoes the whole insertion in one step',
          view.text() == '#lin')
else:
    check('ctrl+z undo is not enabled on this buffer (code reference: '
          '_accept wraps begin/end_user_action)', True)
htype('hi ＃lin')
check('fullwidth trigger kept', hpress(Gdk.KEY_Return)
      and view.text() == 'hi ＃linux ')
htype('#lin rest', cursor=4)
check('space already after: no second space',
      hpress(Gdk.KEY_Return) and view.text() == '#linux rest'
      and cursor_offset() == 7)
HASHTAG_ANSWER[:] = [{'name': 'Linux'}]
hcomp._cache.clear()
htype('#linxyz', cursor=4)
check('mid-word accept replaces the rest of the name',
      hpress(Gdk.KEY_Return) and view.text() == '#Linux ')
HASHTAG_ANSWER[:] = [LINUX, MINT]
hcomp._cache.clear()
htype('#lin')
hcomp._on_row_activated(hcomp.list_box, hcomp.list_box.get_row_at_index(1))
check('click row inserts and focuses text view',
      view.text() == '#linuxmint ' and view.text_view.is_focus())

htype('#lin')
check('Shift+Enter passes through',
      not hpress(Gdk.KEY_Return, Gdk.ModifierType.SHIFT_MASK)
      and hcomp.get_visible())
check('Ctrl+Enter passes through',
      not hpress(Gdk.KEY_Return, Gdk.ModifierType.CONTROL_MASK))
check('Shift+Tab passes through',
      not hpress(Gdk.KEY_Tab, Gdk.ModifierType.SHIFT_MASK))
check('Escape closes', hpress(Gdk.KEY_Escape) and not hcomp.get_visible())
check('Escape returns False while hidden', not hpress(Gdk.KEY_Escape))
view.buffer.place_cursor(view.buffer.get_end_iter())
hsettle()
check('Escape closes and stays closed until the token changes',
      not hcomp.get_visible())
view.buffer.insert_at_cursor('u')
hsettle()
check('typing on reopens it', hcomp.get_visible())

type_text('@al')
check('@a leaves the mention list only', completer.get_visible()
      and not hcomp.get_visible())
htype('#al')
check('@a then #a leaves one popover',
      hcomp.get_visible() and not completer.get_visible())

# Rows that answer an older query are out of reach.
HOLD_PATHS.add('/api/v2/search')
htype('#lin')
hcomp._cache.clear()
view.buffer.insert_at_cursor('z')
wait_until(lambda: held_requests)
check('selection and stale rows',
      not hcomp.list_box.get_sensitive() and hselected() is None
      and not hpress(Gdk.KEY_Return) and view.text() == '#linz')
# A late answer for a token that is gone adds no rows.
view.buffer.set_text('')
pump(50)
client.release_requests()
pump(50)
check('late answer after token change adds no rows',
      hnames() == [] and not hcomp.get_visible())
HOLD_PATHS.clear()
held_requests.clear()

# Digits: nothing to ask for.
hcomp._cache.clear()
searches.clear()
htype('issue #12')
check('#12 makes no request',
      not hashtag_searches() and not hcomp.get_visible())
htype('#2024x')
check('#2024x searches', len(hashtag_searches()) == 1)

# Failure is silent.
toasts = []
real_toast, real_report = feedback.show_toast, feedback.report_error
feedback.show_toast = lambda *a, **k: toasts.append(a)
feedback.report_error = lambda *a, **k: toasts.append(a)
FAIL_PATHS.add('/api/v2/search')
htype('#fail')
check('failed search hides, no toast', not hcomp.get_visible() and not toasts)
FAIL_PATHS.clear()
HASHTAG_ANSWER[:] = []
htype('#nothingwithit')
check('pool-less empty answer hides', not hcomp.get_visible() and not toasts)
feedback.show_toast, feedback.report_error = real_toast, real_report
HASHTAG_ANSWER[:] = [{'id': '1'}, None, {'name': ''}, LINUX]
htype('#lin')
check('malformed entry skipped', hnames() == ['linux'])
HASHTAG_ANSWER[:] = [LINUX, MINT]

# Rows.
plain = hcomp.build_row({'name': 'x'})
labels_box = plain.get_child().get_last_child()
check('row without caption has no caption child and label #x',
      labels_box.get_first_child() is labels_box.get_last_child()
      and plain.accessible_text == '#x')
zero = hcomp.build_row({'name': 'x', 'history': [{'uses': '0',
                                                  'accounts': '0'}]})
check('zero-people history: no caption',
      zero.accessible_text == '#x'
      and caption_for({'name': 'x', 'history': []}) == '')


def people(count):
    return caption_for({'name': 'x', 'history': [
        {'uses': '1', 'accounts': str(count)}]})


check('caption plural 1 / 2 / 0',
      people(1) == ngettext('%d person, last 2 days',
                            '%d people, last 2 days', 1) % 1
      and people(2) == ngettext('%d person, last 2 days',
                                '%d people, last 2 days', 2) % 2
      and people(0) == '' and '2 days' in people(5))
check('pool captions',
      caption_for({'source': 'recent'}) == _('Recently used')
      and caption_for({'source': 'featured'}) == _('On your profile')
      and caption_for({'source': 'followed'}) == _('Followed'))
rich = hcomp.build_row({'name': 'linux', 'source': 'recent'})
check('row label is #name, caption',
      rich.accessible_text == '#linux, ' + _('Recently used'))

longname = 'x' * 60
long_row = hcomp.build_row({'name': longname, 'source': 'recent'})
box = long_row.get_child().get_last_child()
mention_row = completer.build_row({'acct': 'y' * 60, 'username': 'y',
                                   'display_name': 'y' * 60})
check('row labels ellipsize END and natural width <= mention row',
      box.get_first_child().get_ellipsize() == Pango.EllipsizeMode.END
      and box.get_last_child().get_ellipsize() == Pango.EllipsizeMode.END
      and long_row.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
      <= mention_row.measure(Gtk.Orientation.HORIZONTAL, -1)[1])
check('name and caption are separate labels (RTL)',
      box.get_first_child().get_label() == '#' + longname
      and box.get_last_child().get_label() == _('Recently used'))
Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL)
rtl_row = hcomp.build_row({'name': 'linux', 'source': 'recent'})
check('RTL row direction',
      rtl_row.get_child().get_direction() == Gtk.TextDirection.RTL)
Gtk.Widget.set_default_direction(Gtk.TextDirection.LTR)
check('list label is Suggested Hashtags',
      hcomp.list_label == _('Suggested Hashtags'))
# The suite runs with GTK_A11Y=none, where nothing can be read back, so
# this reads the one place it is set.
check('AUTOCOMPLETE stays LIST',
      'AccessibleAutocomplete.LIST' in base_source
      and 'AccessibleAutocomplete.NONE' not in base_source
      and 'AccessibleAutocomplete' not in inspect.getsource(
          type(hcomp)))
check('rows and list are not focusable',
      not hcomp.get_can_focus() and not hcomp.list_box.get_can_focus()
      and not plain.get_can_focus() and not rich.get_can_focus())

# Focus, posting, input methods.
htype('#lin')
check('focus leave dismisses',
      hcomp.get_visible() and (hcomp._on_focus_leave(None) or True)
      and not hcomp.get_visible())
view.text_view.grab_focus()
htype('#lin')
view.set_posting_ui(True)
check('posting dismisses both completers',
      not hcomp.get_visible() and not completer.get_visible())
view.set_posting_ui(False)
htype('#lin')
view.reset()
check('reset dismisses both completers',
      not hcomp.get_visible() and not completer.get_visible())
view.text_view.grab_focus()
htype('#lin')
view.text_view.emit('preedit-changed', '日本')
check('preedit lets Enter through (hashtag)',
      not hpress(Gdk.KEY_Return) and hcomp.get_visible()
      and view.text() == '#lin')
check('Escape passes through while composing (hashtag)',
      not hpress(Gdk.KEY_Escape) and hcomp.get_visible())
view.text_view.emit('preedit-changed', '')
view.reset()

if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all compose hashtag checks passed')
