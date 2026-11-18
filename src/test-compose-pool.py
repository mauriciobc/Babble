#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercises the tag and people pool requests and FakeTagPage with a stub
client."""

import gc
import os
import sys
import tempfile
import weakref
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

from gi.repository import (Adw, Gdk, Gio, GLib, Gtk)  # noqa: E402

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
    FAIL_PATHS, HASHTAG_ANSWER, HOLD_PATHS, LINUX, POOL_ANSWERS,
    StubClient, StubMediaResult, check, hashtag_searches, hcomp,
    held_requests, hnames, hpress, hselected, htype, pump, searches,
    set_text, wait_until)
from src import appsettings
from src import feedback
from src import pagenav
from src.compose import BabbleComposeView
from src import hashtags
from src.hashtagcompleter import wants_search

view = kit.view
client = kit.client

# The pool: the account's own tags, fetched once and shared.
from src import hashtagcompleter as hc_module  # noqa: E402

POOL_PATHS = ['/featured_tags/suggestions', '/featured_tags',
              '/followed_tags']
RECENT, FEATURED, FOLLOWED = POOL_PATHS


def reset_pool(**answers):
    hc_module._POOLS.clear()
    hcomp._cache.clear()
    view.reset()
    for path in POOL_PATHS:
        POOL_ANSWERS[path] = []
    for path, tags in answers.items():
        POOL_ANSWERS[path] = tags
    HASHTAG_ANSWER[:] = []
    held_requests.clear()
    searches.clear()
    view.text_view.grab_focus()


def pool_requests():
    return [entry[1] for entry in searches if entry[1] in POOL_PATHS]


def the_pool():
    return hc_module._POOLS.get(client)


reset_pool(**{RECENT: [{'name': 'linuxfan'}]})
HASHTAG_ANSWER[:] = [LINUX]
HOLD_PATHS.add('/api/v2/search')
view.buffer.set_text('#lin')
view.buffer.place_cursor(view.buffer.get_end_iter())
wait_until(lambda: hnames() == ['linuxfan'])
check('pool rows show before the search answers',
      hnames() == ['linuxfan'] and hcomp.list_box.get_sensitive()
      and hselected() == 'linuxfan' and hcomp.get_visible())
wait_until(lambda: held_requests)
client.release_requests()
pump(50)
check('the search answer joins the pool rows',
      hnames() == ['linuxfan', 'linux'])
HOLD_PATHS.clear()
check('pool fetches three endpoints once',
      sorted(pool_requests()) == sorted(POOL_PATHS)
      and [e[2] for e in searches if e[1] == FOLLOWED] == [{'limit': 200}])
htype('#lino')
extra = BabbleComposeView(client)
extra.hashtag_completer.prepare('lin')
pump(50)
check('second composer in 10 minutes makes no pool request',
      len(pool_requests()) == 3)
extra_ref = weakref.ref(extra)
del extra
gc.collect()

check('two composers of one client share the pool',
      len(hc_module._POOLS) == 1)
other = StubClient()
hc_module._POOLS.setdefault(other, hc_module.TagPool()).ensure(other)
pump(50)
check('other client has its own pool',
      len(pool_requests()) == 6 and len(hc_module._POOLS) == 2
      and hc_module._POOLS[other] is not the_pool())
other_ref = weakref.ref(other)
del other
gc.collect()
check('the pool is freed with its client',
      other_ref() is None and len(hc_module._POOLS) == 1)

clock = [1000.0]
real_now = hc_module._now
hc_module._now = lambda: clock[0]
reset_pool()
hcomp.prepare('lin')
pump(50)
clock[0] += hc_module.POOL_TTL_SECONDS - 1
hcomp.prepare('lin')
pump(50)
check('inside the TTL there is no refetch', len(pool_requests()) == 3)
clock[0] += 2
hcomp.prepare('lin')
pump(50)
check('TTL expiry refetches', len(pool_requests()) == 6)
hc_module._now = real_now

reset_pool()
htype('hello there')
pump(50)
check('composer without a hash makes no pool request',
      not pool_requests())

for failing, label in ((RECENT, 'recent'), (FEATURED, 'featured'),
                       (FOLLOWED, 'followed')):
    reset_pool(**{RECENT: [{'name': 'linr'}], FEATURED: [{'name': 'linf'}],
                  FOLLOWED: [{'name': 'link'}]})
    FAIL_PATHS.add(failing)
    htype('#lin')
    left = sorted(tag['name'] for source in the_pool().tags.values()
                  for tag in source)
    want = sorted({'linr', 'linf', 'link'} - {
        {RECENT: 'linr', FEATURED: 'linf', FOLLOWED: 'link'}[failing]})
    check('pool endpoint failing in turn: others still work (%s)' % label,
          left == want and hcomp.get_visible()
          and sorted(hnames()) == want)
    FAIL_PATHS.clear()
reset_pool()
HASHTAG_ANSWER[:] = [LINUX]
FAIL_PATHS.update(POOL_PATHS)
htype('#lin')
check('all pool endpoints failing: search still works',
      hnames() == ['linux'] and the_pool().loaded)
FAIL_PATHS.clear()

# remember() and forget_pool().
reset_pool()
hc_module.remember(client, [{'name': 'Foo'}, {'name': 'foo'}, {}])
hc_module.remember(client, [{'name': 'FOO'}, {'name': 'bar'}])
check('remember adds recent, dedups',
      [t['name'] for t in the_pool().tags['recent']] == ['bar', 'Foo']
      and not the_pool().loaded)
htype('#ba')
check('remembered tags survive the first load and are suggested',
      hnames() == ['bar'])
hc_module.remember(client, None)
hc_module.forget_pool(client)
check('forget_pool drops it', the_pool() is None)
reset_pool()
hcomp.prepare('a')
pump(50)
real_client_of = pagenav.client_of
pagenav.client_of = lambda _widget: client
hashtags._invalidate_home_tags(view)
pagenav.client_of = real_client_of
check('_invalidate_home_tags drops the pool', the_pool() is None)

reset_pool()
set_text('post #Gaming')
client.post_finish = lambda _result: {
    'id': '1', 'content': '<p>x</p>', 'tags': [{'name': 'Gaming'}]}
view.on_post_done(client, StubMediaResult(), (None, view.uploads.generation))
del client.post_finish
check('on_post_done remembers published tags',
      the_pool() is not None
      and [t['name'] for t in the_pool().tags['recent']] == ['Gaming'])

# Saving a draft or scheduled post can be refused with a 422 naming the
# limit (scheduled_status.rb: 25 per scheduled_at date, 300 per account).
# The reader must see the server's sentence, not a bare failure.
_limit_text = ('Validation failed: Daily limit of 25 scheduled posts '
               'for this date reached')
_toasts = []
_real_toast = view._toast
view._toast = _toasts.append
view.scheduled_at = '5026-11-05T12:00:00.000Z'
view._posting = True


def _refuse(_result):
    raise GLib.Error(_limit_text, 'mastodon-http-error-quark', 422)


client.post_finish = _refuse
view.on_post_done(client, StubMediaResult(), (None, view.uploads.generation))
del client.post_finish
view._toast = _real_toast
view.scheduled_at = None
check('a refused draft shows the server\'s limit message',
      len(_toasts) == 1 and 'Daily limit of 25' in _toasts[0]
      and not view._posting, repr(_toasts))

# The bare #.
reset_pool(**{RECENT: [{'name': 'r%d' % n} for n in range(5)],
              FEATURED: [{'name': 'f1'}, {'name': 'f2'}],
              FOLLOWED: [{'name': 'followedonly'}]})
the_pool_obj = hc_module._POOLS.setdefault(client, hc_module.TagPool())
the_pool_obj.ensure(client)
pump(100)
view.buffer.set_text('#')
view.buffer.place_cursor(view.buffer.get_end_iter())
pump(50)
check('bare hash shows nothing at once', not hcomp.get_visible())
wait_until(hcomp.get_visible)
check('bare hash after debounce lists recent and featured, max 6',
      hnames() == ['r0', 'r1', 'r2', 'r3', 'r4', 'f1']
      and hselected() is None)
check('bare hash list takes Down then Enter',
      hpress(Gdk.KEY_Down) and hpress(Gdk.KEY_Return)
      and view.text() == '#r0 ')
view.buffer.set_text('# heading')
view.buffer.place_cursor(view.buffer.get_end_iter())
pump(400)
check('# heading never shows a list', not hcomp.get_visible())
view.buffer.set_text('#')
view.buffer.place_cursor(view.buffer.get_end_iter())
view.buffer.insert_at_cursor(' ')
pump(400)
check('# then a space inside the debounce never shows a list',
      not hcomp.get_visible())

reset_pool()
searches.clear()
view.buffer.set_text('#')
view.buffer.place_cursor(view.buffer.get_end_iter())
pump(400)
check('bare hash with empty pool shows nothing and sends no search',
      not hcomp.get_visible() and not hashtag_searches())

# A pool that is still loading when the user pauses on # shows nothing,
# and its answer later does not pop a list for a token that moved on.
reset_pool(**{RECENT: [{'name': 'late'}]})
HOLD_PATHS.update(POOL_PATHS)
view.buffer.set_text('#lat')
view.buffer.place_cursor(view.buffer.get_end_iter())
pump(50)
view.buffer.set_text('hello')
pump(50)
HOLD_PATHS.clear()
client.release_requests()
pump(100)
check('pool answer after the token moved on shows nothing',
      not hcomp.get_visible() and hnames() == [])

reset_pool(**{FEATURED: [{'name': '123abc'}, {'name': 'alpha'}]})
htype('#12')
check('#12 matches a digit pool tag only',
      hnames() == ['123abc'] and not hashtag_searches())

pool_values = [value for source in the_pool().tags.values()
               for tag in source for value in tag.values()]
check('pool holds plain dicts',
      pool_values and not any(isinstance(v, Gtk.Widget)
                              for v in pool_values))

reset_pool()
HOLD_PATHS.update(POOL_PATHS)
extra = BabbleComposeView(client)
extra.hashtag_completer.prepare('lin')
pump(50)
extra_ref = weakref.ref(extra)
del extra
gc.collect()
check('pool fill does not keep the completer alive', extra_ref() is None)
HOLD_PATHS.clear()
client.release_requests()
pump(50)
check('a pool answer for a closed composer is harmless', True)
reset_pool()

# Fix round 1.

# F1: a bare # on a pool that is still cold shows the list when the pool
# arrives, if the user is still there. The pool is not warmed first.
WARM = {RECENT: [{'name': 'r%d' % n} for n in range(3)],
        FEATURED: [{'name': 'f1'}]}


def bare_hash_cold():
    reset_pool(**WARM)
    HOLD_PATHS.update(POOL_PATHS)
    view.buffer.set_text('#')
    view.buffer.place_cursor(view.buffer.get_end_iter())
    pump(400)


def arrive():
    HOLD_PATHS.clear()
    client.release_requests()
    pump(100)


bare_hash_cold()
check('bare hash on a cold pool shows nothing while it loads',
      not hcomp.get_visible() and len(held_requests) == 3)
arrive()
check('bare hash on a cold pool lists recent and featured when the pool '
      'arrives', hnames() == ['r0', 'r1', 'r2', 'f1'] and hselected() is None
      and hcomp.get_visible())
check('bare hash on a cold pool then takes Down then Enter',
      hpress(Gdk.KEY_Down) and hpress(Gdk.KEY_Return)
      and view.text() == '#r0 ')
bare_hash_cold()
view.buffer.insert_at_cursor('x')
arrive()
check('bare hash on a cold pool shows nothing if the user typed on',
      not hcomp.get_visible() and hnames() == [])
bare_hash_cold()
view.spoiler_entry.grab_focus()
arrive()
check('bare hash on a cold pool shows nothing without focus',
      not hcomp.get_visible() and hnames() == [])
view.text_view.grab_focus()
bare_hash_cold()
view.text_view.emit('preedit-changed', '日')
arrive()
check('bare hash on a cold pool shows nothing while composing',
      not hcomp.get_visible() and hnames() == [])
view.text_view.emit('preedit-changed', '')
reset_pool(**WARM)
hc_module._POOLS.setdefault(client, hc_module.TagPool()).ensure(client)
pump(100)
view.buffer.set_text('#')
view.buffer.place_cursor(view.buffer.get_end_iter())
view.text_view.emit('preedit-changed', '日')
pump(400)
check('composing right after a bare hash shows no list',
      not hcomp.get_visible())
view.text_view.emit('preedit-changed', '')
reset_pool()

# F2: accepting with the caret inside the name writes the completion in
# two edits; none of them may open a list.
reset_pool(**{FEATURED: [{'name': 'linux'}]})
hc_module._POOLS.setdefault(client, hc_module.TagPool()).ensure(client)
pump(100)
HASHTAG_ANSWER[:] = [{'name': 'Linux'}]
htype('#lin more', cursor=4)
popups, fills = [], []
hcomp.popup = lambda: popups.append(1)
hcomp._fill = lambda items: fills.append(1)
searches.clear()
first_row = hcomp.list_box.get_row_at_index(0)
if first_row is not None:
    hcomp._on_row_activated(hcomp.list_box, first_row)
del hcomp.popup, hcomp._fill
check('accepting a row with the caret mid-name makes no popup and no '
      'pool request',
      view.text() == '#linux more' and not popups and not fills
      and not pool_requests() and not hashtag_searches()
      and not hcomp.get_visible())
reset_pool()

# F3 is in test-a11y.py.

# F5: a pool whose three requests all failed is asked again soon.
clock = [5000.0]
real_now = hc_module._now
hc_module._now = lambda: clock[0]
reset_pool()
FAIL_PATHS.update(POOL_PATHS)
hcomp.prepare('a')
pump(50)
FAIL_PATHS.clear()
n = len(pool_requests())
clock[0] += hc_module.POOL_RETRY_SECONDS - 1
hcomp.prepare('a')
pump(50)
check('an all-failed pool is not asked again inside the retry interval',
      len(pool_requests()) == n == 3)
clock[0] += 2
hcomp.prepare('a')
pump(50)
check('an all-failed pool is asked again after the retry interval',
      len(pool_requests()) == 6)
reset_pool()
FAIL_PATHS.add(FEATURED)
hcomp.prepare('a')
pump(50)
FAIL_PATHS.clear()
clock[0] += hc_module.POOL_TTL_SECONDS - 1
hcomp.prepare('a')
pump(50)
check('a partly failed pool keeps the full TTL', len(pool_requests()) == 3)
clock[0] += 2
hcomp.prepare('a')
pump(50)
check('a partly failed pool is asked again after the TTL',
      len(pool_requests()) == 6)
reset_pool(**{RECENT: [{'name': 'linr'}], FEATURED: [{'name': 'linf'}]})
hcomp.prepare('a')
pump(50)
POOL_ANSWERS[FEATURED] = [{'name': 'linnew'}]
FAIL_PATHS.add(RECENT)
clock[0] += hc_module.POOL_TTL_SECONDS + 1
hcomp.prepare('a')
pump(50)
FAIL_PATHS.clear()
check('a refresh that fails keeps the tags the last load found',
      [tag['name'] for tag in the_pool().tags['recent']] == ['linr']
      and [tag['name'] for tag in the_pool().tags['featured']]
      == ['linnew'])
hc_module._now = real_now

# F6: no settings read per keystroke.
reads = []
real_settings = appsettings.settings
reset_pool()
appsettings.settings = lambda: (reads.append(1), real_settings())[1]
for char in 'linux':
    view.buffer.insert_at_cursor('#' if not view.text() else char)
pump(400)
appsettings.settings = real_settings
check('no GSettings read per keystroke', not reads)

# F7: one waiter per completer, however much is typed while the pool loads.
reset_pool()
calls, others = [], []
real_waiter = hcomp._pool_waiter
hcomp._pool_waiter = lambda: calls.append(1)
HOLD_PATHS.update(POOL_PATHS)
view.buffer.set_text('#')
view.buffer.place_cursor(view.buffer.get_end_iter())
for char in 'linux':
    view.buffer.insert_at_cursor(char)
pump(50)
the_pool().ensure(client, lambda: others.append(1))
HOLD_PATHS.clear()
client.release_requests()
pump(100)
hcomp._pool_waiter = real_waiter
check('typing six characters during a pool load runs the ready callback '
      'once', calls == [1])
check('a second completer still gets its own callback', others == [1])
reset_pool()

# F8: featuring or unfeaturing a tag changes what the pool lists.


class FakeTagPage:
    def __init__(self, featured_id):
        self.tags = ['linux']
        self.featured_id = featured_id
        self.client = client
        self.more_button = Gtk.Button()

    def _build_menu(self):
        pass


real_toast = feedback.show_toast
feedback.show_toast = lambda *a, **k: None
for featured_id, label in ((None, 'featuring'), ('7', 'unfeaturing')):
    reset_pool()
    hcomp.prepare('a')
    pump(50)
    page = FakeTagPage(featured_id)
    hashtags.HashtagPage._on_feature(page)
    pump(50)
    check('%s a tag drops the pool' % label, the_pool() is None)
feedback.show_toast = real_toast

# F9: one constant, no invisible character in the source.
from src import completer as completer_mod  # noqa: E402
check('MAX_SUGGESTIONS is one constant',
      hc_module.MAX_SUGGESTIONS is completer_mod.MAX_SUGGESTIONS)
with open(hc_module.__file__, encoding='utf-8') as source_file:
    hidden = [c for c in source_file.read()
              if c in '​‌‍⁠﻿']
check('no invisible characters in the source', not hidden)

# F10: the tag name is bold.
long_row = hcomp.build_row({'name': 'x' * 60, 'source': 'recent'})
check('tag name label is bold (heading class)',
      long_row.get_child().get_last_child().get_first_child()
      .has_css_class('heading'))

# F11: a separator at the edge of a name is a tag still being typed.
reset_pool()
htype('#a·')
check('#a· makes no request', not hashtag_searches())
htype('#·a')
check('#·a makes no request', not hashtag_searches())
htype('#a・')
check('#a・ makes no request', not hashtag_searches())
htype('#a·b')
check('#a·b searches', len(hashtag_searches()) == 1)
check('wants_search edges',
      not wants_search('a·') and not wants_search('‌a')
      and wants_search('a‌b') and wants_search('a'))

# F12: a failed search leaves a pool hit in the list.
reset_pool(**{FEATURED: [{'name': 'linux'}]})
toasts = []
real_toast, real_report = feedback.show_toast, feedback.report_error
feedback.show_toast = lambda *a, **k: toasts.append(a)
feedback.report_error = lambda *a, **k: toasts.append(a)
FAIL_PATHS.add('/api/v2/search')
htype('#lin')
check('a pool hit stays listed when the search fails',
      hnames() == ['linux'] and hcomp.list_box.get_sensitive()
      and hcomp.get_visible() and not toasts)
FAIL_PATHS.clear()
feedback.show_toast, feedback.report_error = real_toast, real_report
reset_pool()

extra = BabbleComposeView(client)
hashtag_ref = weakref.ref(extra.hashtag_completer)
extra_ref = weakref.ref(extra)
del extra
gc.collect()
check('hashtag completer is freed with the composer',
      extra_ref() is None and hashtag_ref() is None)
HASHTAG_ANSWER.clear()
searches.clear()

if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all compose pool checks passed')
