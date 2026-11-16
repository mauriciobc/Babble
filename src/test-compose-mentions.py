#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercises mention tokens, suggestions, refills and input-method
selection with a stub client."""

import os
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

from gi.repository import (Adw, Gdk, Gio, Gtk)  # noqa: E402

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
    HASHTAG_ANSWER, SEARCH_ACCOUNTS, StubMediaResult, base_source, check,
    completer, press, pump, searches, set_text, settle, type_text)
from src import completer as completer_module
from src.hashtagcompleter import MAX_SUGGESTIONS
from src.hashtagcompleter import (
    hashtag_query, is_name_char, merge_casing, rank_tags, wants_search)
from src.mentioncompleter import mention_query

view = kit.view
client = kit.client

# Mention suggestions: the token under the cursor follows Mastodon's
# mention rule, so addresses and URLs never open the list.
check('mention query at the start', mention_query('@al') == (0, 'al'))
check('mention query after a space', mention_query('hi @al') == (3, 'al'))
check('mention query after punctuation', mention_query('(@al') == (1, 'al'))
check('bare @ is an empty query', mention_query('hi @') == (3, ''))
check('remote mention keeps its domain',
      mention_query('@bob@remote.ex') == (0, 'bob@remote.ex'))
check('an email address is no mention',
      mention_query('mail me@example.com') is None)
check('a URL path is no mention',
      mention_query('https://example.com/@al') is None)
check('a finished mention is no query', mention_query('@al ') is None)

# Hashtag suggestions: the token follows Mastodon's own rule (a tag
# starts the line or follows whitespace, and a name is letters, marks,
# digits and connectors), checked with the same pure function the
# completer uses.
check('hashtag query at start', hashtag_query('#lin') == (0, 'lin'))
check('hashtag query after a space',
      hashtag_query('hi #lin') == (3, 'lin'))
check('hashtag query after a tab',
      hashtag_query('hi\t#lin') == (3, 'lin'))
check('hashtag query after a newline',
      hashtag_query('hi\n#lin') == (3, 'lin'))
check('fullwidth hash starts a hashtag',
      hashtag_query('hi \uff03lin') == (3, 'lin'))
check('C# is no query', hashtag_query('C#') is None)
check('(#x is no query', hashtag_query('(#x') is None)
check('example.com/#a is no query',
      hashtag_query('example.com/#a') is None)
check('abc#x is no query', hashtag_query('abc#x') is None)
check('## is no query', hashtag_query('##x') is None)
check('CJK names are queries',
      hashtag_query('x #日本語') == (2, '日本語'))
check('Devanagari marks stay in the query',
      hashtag_query('#हिन्दी') == (0, 'हिन्दी'))
check('middle dot, katakana middle dot and ZWNJ stay in the query',
      hashtag_query('#a\u00b7b') == (0, 'a\u00b7b')
      and hashtag_query('#a\u30fbb') == (0, 'a\u30fbb')
      and hashtag_query('#a\u200cb') == (0, 'a\u200cb'))
check('digits and underscores stay in the query',
      hashtag_query('#a_1') == (0, 'a_1'))
check('bare hash is an empty query', hashtag_query('hi #') == (3, ''))
check('empty text is no query', hashtag_query('') is None)
check('finished tag is no query', hashtag_query('#tag ') is None)
check('#foo, is no query', hashtag_query('#foo,') is None)
check('#foo. is no query', hashtag_query('#foo.') is None)
check('is_name_char knows names from punctuation',
      is_name_char('a') and is_name_char('_') and is_name_char('\u0947')
      and not is_name_char('-') and not is_name_char(' ')
      and not is_name_char(''))
check('wants_search digits only is False',
      not wants_search('12') and not wants_search('')
      and wants_search('2024x') and wants_search('日本'))

check('merge_casing GamingD',
      merge_casing('GamingD', 'gamingdeals') == 'GamingDeals')
check('merge_casing gaming',
      merge_casing('gaming', 'GamingDeals') == 'GamingDeals')
check('merge_casing İ unchanged',
      merge_casing('\u0130', '\u0130stanbul') == '\u0130stanbul'
      and merge_casing('i', '\u0130stanbul') == '\u0130stanbul')
check('merge_casing camel case kept',
      merge_casing('gamingDeals', 'gamingdealsnow') == 'gamingDealsnow')
check('merge_casing empty typed gives the name',
      merge_casing('', 'Linux') == 'Linux')
check('merge_casing falls back when the name does not begin with it',
      merge_casing('Lix', 'Linux') == 'Linux')


def mk_tag(name, source=None, people=0, following=False):
    entry = {'name': name, 'following': following}
    if source:
        entry['source'] = source
    if people:
        entry['history'] = [{'uses': '9', 'accounts': str(people)}]
    return entry


def tag_names(ranked):
    return [entry['name'] for entry in ranked]


check('rank_tags exact first',
      tag_names(rank_tags('lin', [mk_tag('lingo', 'recent')],
                      [mk_tag('linux', people=9), mk_tag('Lin')]))
      == ['Lin', 'lingo', 'linux'])
check('rank_tags pool before server',
      tag_names(rank_tags('l', [mk_tag('lab', 'followed'), mk_tag('lap', 'recent')],
                      [mk_tag('linux', people=99)]))
      == ['lap', 'lab', 'linux'])
check('rank_tags pool by source order',
      tag_names(rank_tags('l', [mk_tag('lc', 'followed'), mk_tag('lb', 'featured'),
                            mk_tag('la', 'recent')], []))
      == ['la', 'lb', 'lc'])
check('rank_tags following before people',
      tag_names(rank_tags('l', [], [mk_tag('big', people=99), mk_tag('mine', people=1,
                                                           following=True)]))
      == ['mine', 'big'])
check('rank_tags people descending',
      tag_names(rank_tags('l', [], [mk_tag('lone', people=1), mk_tag('lots', people=5),
                                mk_tag('lots2', people=3)]))
      == ['lots', 'lots2', 'lone'])
check('rank_tags dedups case-insensitively',
      tag_names(rank_tags('l', [mk_tag('Linux', 'recent')],
                      [mk_tag('linux'), mk_tag('LINUX'), mk_tag('lisp')]))
      == ['Linux', 'lisp'])
check('rank_tags caps at 6',
      len(rank_tags('l', [mk_tag('l%d' % i, 'recent') for i in range(4)],
                    [mk_tag('s%d' % i) for i in range(9)])) == MAX_SUGGESTIONS)
check('rank_tags ties keep server order',
      tag_names(rank_tags('l', [], [mk_tag('lb'), mk_tag('la'), mk_tag('lc')]))
      == ['lb', 'la', 'lc'])
check('rank_tags skips nameless',
      tag_names(rank_tags('l', [{'source': 'recent'}, mk_tag('la', 'recent')],
                      [{'name': ''}, {'id': '3'}, None, mk_tag('lb')]))
      == ['la', 'lb'])
ranked = rank_tags('l', [mk_tag('la', 'featured', people=4)], [mk_tag('lb')])
check('rank_tags adds source and people to copies',
      ranked[0]['source'] == 'featured' and ranked[0]['people'] == 4
      and ranked[1]['source'] is None and ranked[1]['people'] == 0)

from src import hashtags, statustext  # noqa: E402
check('history_totals still importable from hashtags',
      hashtags.history_totals is statustext.history_totals
      and hashtags.STATS_DAYS == statustext.STATS_DAYS)
check('STATS_DAYS == 2', statustext.STATS_DAYS == 2)







view.reset()
view.text_view.grab_focus()
searches.clear()
type_text('hi @al')
check('typing a mention searches accounts',
      searches and searches[-1][1] == '/accounts/search'
      and searches[-1][2].get('q') == 'al'
      and 'resolve' not in searches[-1][2])
rows = [completer.list_box.get_row_at_index(i) for i in range(3)]
check('suggestions pop up under the mention', completer.get_visible())
check('one row per account, the first selected',
      rows[0] is not None and rows[1] is not None and rows[2] is None
      and completer.list_box.get_selected_row() is rows[0])
check('custom emoji are dropped from the shown name',
      rows[0].acct == 'alice'
      and rows[0].get_child().get_last_child().get_first_child()
      .get_label() == 'Alice')
check('rows activate on a single click',
      completer.list_box.get_activate_on_single_click())
check('Shift+Enter is left to the text view for a line break',
      not press(Gdk.KEY_Return, Gdk.ModifierType.SHIFT_MASK)
      and completer.get_visible())
check('Down moves the selection', press(Gdk.KEY_Down)
      and completer.list_box.get_selected_row() is rows[1])
check('Ctrl+Enter is left to the composer',
      not press(Gdk.KEY_Return, Gdk.ModifierType.CONTROL_MASK))
check('Enter inserts the selected account', press(Gdk.KEY_Return)
      and view.text() == 'hi @bob@remote.example ')
check('the list closes after inserting', not completer.get_visible())
cursor = view.buffer.get_iter_at_mark(view.buffer.get_insert())
check('the cursor lands after the mention',
      cursor.get_offset() == len(view.text()))

# Results for an older query are never inserted into a newer one.
type_text('@al')
view.buffer.insert_at_cursor('x')
check('older results are out of reach while the new query runs',
      completer.list_box.get_selected_row() is None
      and not completer.list_box.get_sensitive()
      and not press(Gdk.KEY_Return) and view.text() == '@alx')
settle()
check('new results make the list usable again',
      completer.list_box.get_sensitive()
      and completer.list_box.get_selected_row() is not None)

type_text('@al rest', cursor=2)
check('a mention mid-word still suggests', completer.get_visible())
completer._on_row_activated(completer.list_box,
                            completer.list_box.get_row_at_index(0))
check('picking replaces the whole name and keeps the space',
      view.text() == '@alice rest'
      and view.buffer.get_iter_at_mark(
          view.buffer.get_insert()).get_offset() == len('@alice '))

type_text('@al')
check('Escape closes the list', press(Gdk.KEY_Escape)
      and not completer.get_visible())
view.buffer.place_cursor(view.buffer.get_end_iter())
settle()
check('a closed mention stays closed while it is unchanged',
      not completer.get_visible())
view.buffer.insert_at_cursor('i')
settle()
check('typing on reopens it', completer.get_visible()
      and searches[-1][2].get('q') == 'ali')

stale = completer._serial
type_text('@bob@remote.example')
check('a full remote address resolves',
      searches[-1][2].get('resolve') == 'true')
completer._clear_rows()
completer._on_search_done(client, StubMediaResult(), stale)
check('a stale reply is ignored',
      completer.list_box.get_row_at_index(0) is None)

view.reset()
check('reset closes the list', not completer.get_visible())

# The mention completer is the base class plus account hooks, and the
# base knows nothing of accounts.

check('mention completer is a TokenCompleter',
      isinstance(completer, completer_module.TokenCompleter))

check('base class has no account-specific import',
      'AvatarCache' not in base_source and 'acct' not in base_source)
searches.clear()
client.request_async('GET', '/api/v2/search', params={'q': 'x'},
                     callback=lambda *_: None)
stub_result = StubMediaResult()
stub_result.path = '/api/v2/search'
HASHTAG_ANSWER.append({'name': 'linux'})
check('stub routes /api/v2/search separately from accounts',
      client.request_finish(stub_result) == {'hashtags': [{'name': 'linux'}]}
      and client.request_finish(StubMediaResult()) is SEARCH_ACCOUNTS)
HASHTAG_ANSWER.clear()
searches.clear()

# The Mention button starts a mention set off from the word before it
# and shows the hint until a name is typed.
set_text('hello')
view.buffer.place_cursor(view.buffer.get_end_iter())
view.on_mention_clicked(view.mention_button)
pump(50)
check('mention button spaces the @ from a word', view.text() == 'hello @')
check('mention button shows the hint', completer.get_visible()
      and completer.hint_label.get_visible())
view.buffer.insert_at_cursor('al')
settle()
check('typing replaces the hint with suggestions',
      not completer.hint_label.get_visible()
      and completer.list_box.get_row_at_index(0) is not None)
view.reset()
view.on_mention_clicked(view.mention_button)
check('mention button on an empty draft adds a bare @', view.text() == '@')
view.reset()

if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all compose mention checks passed')
