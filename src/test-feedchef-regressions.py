#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef: promises an adversarial QA broke, each pinned by a case.

Display-free (GLib main loop only). Text extraction across line breaks,
the exact Hide the Least Engaging rule, a refresh that fails or is
cancelled keeping the session, a failed scope raising, the viewer's
filters not using a person's slots, a page one that loses no post to a
page-size cut."""

import copy
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))

import gi  # noqa: E402

gi.require_version('Gio', '2.0')

from gi.repository import Gio, GLib  # noqa: E402

from src.feedchef import contract  # noqa: E402
from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import kitchen as kitchenmod  # noqa: E402
from src.feedchef import network_reach as reachmod  # noqa: E402
from src.feedchef import textutil  # noqa: E402
from src.feedchef.engine import FeedEngine  # noqa: E402
from src.feedchef.recipe import Modifier, default_recipe  # noqa: E402
from src.feedchefsource import FeedChefSource, viewer_hides  # noqa: E402

failures = 0
NOW = datetime.now(timezone.utc)
TMP = tempfile.mkdtemp(prefix='feedchef-regressions-')
reachmod.set_root(os.path.join(TMP, 'reach'))
logmod.set_root(os.path.join(TMP, 'logs'))
_next = [1000]


def check(label, condition):
    global failures
    if condition:
        print('ok - %s' % label)
    else:
        failures += 1
        print('FAIL - %s' % label)


def post(html='<p>hello world</p>', author='f1', age=10, **extra):
    _next[0] += 1
    body = {
        'id': str(_next[0]),
        'created_at': (NOW - timedelta(minutes=age)).isoformat(),
        'content': html, 'spoiler_text': '', 'sensitive': False,
        'language': 'en', 'favourites_count': 0, 'reblogs_count': 0,
        'replies_count': 0, 'account': {'id': author}, 'tags': [],
        'media_attachments': [],
    }
    body.update(extra)
    return body


def recipe(**diet):
    result = default_recipe('t')
    result.kitchen.modifiers = []
    for key, value in diet.items():
        if key == 'min_length':
            result.diet.hard_filters.min_content_length = value
        else:
            setattr(result.diet, key, value)
    return result


def engine(pages=None, scope=None, **kw):
    pages = pages or [[]]
    if scope is None:
        def scope(max_id, count):
            return copy.deepcopy(pages[0])
    return FeedEngine(
        account_key='k%d' % _next[0], self_account_id='me',
        fetch_scope=scope, ui_idle=lambda cb: GLib.idle_add(cb), **kw)


def shown(recipe_, posts, **kw):
    return engine([posts], **kw.pop('eng', {})).process_feed_sync(
        recipe_, count=kw.pop('count', 80), **kw)


# --- 1. text across line breaks, and what the length counts --------------

def blocks(words, html, spoiler=''):
    return not shown(recipe(keyword_blocklist=words),
                     [post(html, spoiler_text=spoiler)]).items


check('text: a <br> separates words (bar<br>foo is not barfoo)',
      blocks(['foo'], '<p>bar<br>foo</p>'))
check('text: two paragraphs do not run together',
      blocks(['spam'], '<p>foo</p><p>spam</p>'))
check('text: list items, divs and blockquotes separate words',
      blocks(['spam'], '<ul><li>foo</li><li>spam</li></ul>')
      and blocks(['spam'], '<div>foo</div><div>spam</div>')
      and blocks(['spam'], '<blockquote>foo</blockquote>spam'))
check('text: an entry of several words matches across a line break',
      blocks(['bad word'], '<p>a bad<br>word here</p>'))
check('text: ... across a paragraph break and a no-break space',
      blocks(['bad word'], '<p>a bad</p><p>word</p>')
      and blocks(['bad word'], '<p>a bad&nbsp;word here</p>'))
check('text: ... and in the content warning, with extra spaces in the entry',
      blocks(['bad   word'], '<p>fine</p>', spoiler='so bad\nword'))
check('text: a word is still a whole word (cat is not in category)',
      not blocks(['cat'], '<p>category</p>'))
check('text: strip_html puts a newline for <br> and blank lines between '
      'paragraphs',
      textutil.strip_html('<p>a<br>b</p><p>c</p>').split()
      == ['a', 'b', 'c']
      and '\n' in textutil.strip_html('a<br>b'))


def length(html, min_length, **extra):
    return bool(shown(recipe(min_length=min_length),
                      [post(html, **extra)]).items)


check('minimum length: a paragraph break counts as one space (ab / cd = 5)',
      length('<p>ab</p><p>cd</p>', 5) and not length('<p>ab</p><p>cd</p>', 6))
check('minimum length: runs of whitespace and no-break spaces count once',
      contract.text_length(post('<p>a \n\n  b&nbsp;&nbsp;c</p>')) == 5)
bridged = ('<p class="quote-inline">RE: <a href="https://bsky.app/profile/'
           'alice.bsky.social/post/3kabc">https://bsky.app/profile/alice.'
           'bsky.social/post/3kabc</a></p><p>hi</p>')
quote_line = ('<p class="quote-inline">RE: <a href="https://x.test/p/1">'
              'https://x.test/p/1</a></p><p>hi</p>')
check('minimum length: the RE: line is left out when the row shows a '
      'Mastodon quote as a card (only "hi" counts)',
      contract.text_length(post(quote_line, quote={'id': '9'})) == 2
      and not length(quote_line, 5, quote={'id': '9'}))
check('minimum length: ... and counted when the row shows it',
      contract.text_length(post(quote_line)) > 10
      and length(quote_line, 5))
check('minimum length: media or a poll keep a Bluesky line the card would '
      'have replaced',
      contract.text_length(post(bridged, media_attachments=[
          {'type': 'image'}]), True) > 10
      and contract.text_length(post(bridged, poll={'id': '1'}), True) > 10)
check('minimum length: a Bluesky RE: line is left out only with the native '
      'setting on',
      contract.text_length(post(bridged), True) == 2
      and contract.text_length(post(bridged), False) > 10)
on = engine([[post(bridged)]], native_bluesky=lambda: True)
off = engine([[post(bridged)]])
check('minimum length: the engine asks the setting (on: hidden line is '
      'not counted; off: it is)',
      not on.process_feed_sync(recipe(min_length=5)).items
      and off.process_feed_sync(recipe(min_length=5)).items)
check('minimum length: the guard agrees with the diet (nothing dropped)',
      on.process_feed_sync(recipe(min_length=1)).guard['dropped'] == {})


# --- 2. Hide the Least Engaging hides exactly floor(P n / 100) ----------

def heat(percent, scores, ages=None):
    mod = Modifier(type='heat', percentile_threshold=percent)
    items = [post(age=(ages[i] if ages else i + 1), id=str(i + 1))
             for i in range(len(scores))]
    table = {item['id']: score for item, score in zip(items, scores, strict=True)}
    kept = kitchenmod.apply_heat(items, table, mod)
    return items, kept


def hid(percent, scores, ages=None):
    items, kept = heat(percent, scores, ages)
    return len(items) - len(kept)


check('heat: ties of zeros, P=50 of 10 hides exactly 5',
      hid(50, [0] * 10) == 5)
items, kept = heat(50, [0] * 10)
check('heat: ... the older ones first (the 5 newest stay)',
      [i['id'] for i in kept] == ['1', '2', '3', '4', '5'])
check('heat: P=1 of 40 hides none (floor 0.4), of 100 hides one',
      hid(1, list(range(40))) == 0 and hid(1, list(range(100))) == 1)
check('heat: P=10 with 30 of 40 posts at zero hides exactly 4',
      hid(10, [0] * 30 + list(range(1, 11))) == 4)
items, kept = heat(10, [0] * 30 + list(range(1, 11)))
check('heat: ... the 4 oldest of the zeros',
      {i['id'] for i in items} - {i['id'] for i in kept}
      == {'27', '28', '29', '30'})
check('heat: P=90 of 10 hides 9 and keeps the best',
      hid(90, list(range(10))) == 9
      and [i['id'] for i in heat(90, list(range(10)))[1]] == ['10'])
check('heat: one post is never hidden, even at 90',
      hid(90, [0]) == 0 and hid(100, [5]) == 0)
check('heat: two posts at 90 hide one (the lower)',
      hid(90, [1, 5]) == 1
      and [i['id'] for i in heat(90, [1, 5])[1]] == ['2'])
check('heat: never all of them, whatever the percent',
      hid(100, [1, 2, 3]) == 2)
check('heat: the lowest score goes first, then the older among equals',
      [i['id'] for i in heat(40, [2, 1, 1, 3, 3], ages=[1, 2, 9, 4, 5])[1]]
      == ['1', '4', '5'])
check('heat: percent 0 hides none', hid(0, [0] * 10) == 0)
hot = recipe()
hot.kitchen.modifiers = [Modifier(type='heat', percentile_threshold=50)]
res = shown(hot, [post(age=i + 1, favourites_count=0) for i in range(10)])
check('heat: through the engine, 10 equal posts at 50 leave 5 (%d)'
      % len(res.items), len(res.items) == 5)


# --- shared pager for the source cases -----------------------------------

loop = GLib.MainLoop()


def run_fetch(src, params, cancellable=None, timeout=10, cancel_after=None):
    out = {}

    def cb(_s, res, *_a):
        try:
            out['items'] = src.finish(res)
        except GLib.Error as error:
            out['error'] = error.message
        loop.quit()

    src.fetch(params, cb, cancellable)
    if cancel_after is not None:
        GLib.timeout_add(cancel_after,
                         lambda: (cancellable.cancel(), False)[-1])
    GLib.timeout_add(int(timeout * 1000),
                     lambda: (out.setdefault('timeout', True), loop.quit(),
                              False)[-1])
    loop.run()
    return out


class Pager:
    """A scope of pages of 40 posts, cursor = the smallest id of a page."""

    def __init__(self, pages):
        self.pages = pages
        self.cursors = {}
        for index, page in enumerate(pages[:-1]):
            self.cursors[str(min(int(p['id']) for p in page))] = index + 1
        self.mode = None
        self.delay = 0

    def __call__(self, max_id, count):
        if self.delay:
            time.sleep(self.delay)
        if self.mode == 'fail':
            raise RuntimeError('network down')
        index = 0 if max_id is None else self.cursors[max_id]
        return copy.deepcopy(self.pages[index])


def make_pages(authors_per_page, prefix='x'):
    pages = []
    for page_index, authors in enumerate(authors_per_page):
        base = page_index * 1000
        pages.append([post(author=a, age=base + i + 1)
                      for i, a in enumerate(authors)])
    return pages


def source_for(pages, recipe_, **kw):
    pager = Pager(pages)
    return pager, FeedChefSource(engine(scope=pager, **kw), recipe_)


def full(n, prefix):
    return ['%s%d' % (prefix, i) for i in range(n)]


# --- 3. a failed or cancelled refresh keeps the session ------------------

cap = default_recipe('cap')
cap.kitchen.modifiers = [Modifier(type='diversity', max_per_cluster=2,
                                  diversity_dimensions=['author'])]
pager, src = source_for(make_pages([full(40, 'a'), full(40, 'b'),
                                    full(5, 'c')]), cap)
first = run_fetch(src, {})
cursor, more, counts = src.cursor, src._has_more, dict(src._author_counts)
check('refresh: the first page lands with a cursor and more to come',
      len(first.get('items', [])) == 40 and cursor and more)
pager.mode = 'fail'
failed = run_fetch(src, {})
check('refresh: a refresh that fails is an error', 'error' in failed)
check('refresh: ... and leaves cursor, has_more and counts as they were',
      (src.cursor, src._has_more, src._author_counts)
      == (cursor, more, counts))
pager.mode = None
older = run_fetch(src, {'max_id': 'x'})
check('refresh: ... so the older page still loads (%d posts)'
      % len(older.get('items', [])), len(older.get('items', [])) == 40)

pager, src = source_for(make_pages([full(40, 'a'), full(40, 'b'),
                                    full(5, 'c')]), cap)
run_fetch(src, {})
cursor, more, counts = src.cursor, src._has_more, dict(src._author_counts)
pager.delay = 1.0
cancellable = Gio.Cancellable()
cancelled = run_fetch(src, {}, cancellable, cancel_after=200)
check('refresh: a cancelled refresh leaves the session intact',
      'error' in cancelled
      and (src.cursor, src._has_more, src._author_counts)
      == (cursor, more, counts))
pager.delay = 0
time.sleep(1.2)
older = run_fetch(src, {'max_id': 'x'})
check('refresh: ... and older pages continue after it',
      len(older.get('items', [])) == 40)
pager.mode = None
refreshed = run_fetch(src, {})
check('refresh: a refresh that lands starts the session again',
      len(refreshed.get('items', [])) == 40 and src.cursor == cursor
      and src._author_counts == counts)


# --- 4. a failed or late scope raises ------------------------------------

def sync_error(eng, **kw):
    try:
        eng.process_feed_sync(recipe(), **kw)
    except Exception as error:
        return error
    return None


def broken(max_id, count):
    raise RuntimeError('boom')


check('scope: a scope that fails raises on page one',
      isinstance(sync_error(engine(scope=broken)), RuntimeError))
check('scope: ... and on an older page',
      isinstance(sync_error(engine(scope=broken), max_id='5'),
                 RuntimeError))
slow = engine(scope=lambda m, c: (time.sleep(1.0), [post()])[1])
slow.pantry_timeout_base = 0.2
check('scope: a scope past its deadline raises (not an empty last page)',
      sync_error(slow) is not None)
check('scope: ... and so does an older page',
      sync_error(slow, max_id='5') is not None)
tagged = engine(scope=broken, fetch_hashtag=lambda t, c: [post(
    tags=[{'name': 'x'}])])
check('scope: with posts from a hashtag, a failed scope still raises '
      '(no cursor to continue from, so Try Again is the honest answer)',
      sync_error(tagged) is not None)
good = engine([[post(), post()]])
check('scope: a healthy scope still returns its page',
      len(good.process_feed_sync(recipe()).items) == 2)
pager, src = source_for([[post()]], recipe())
pager.mode = 'fail'
out = run_fetch(src, {})
check('scope: the source reports it as an error the page can show',
      'error' in out and out['error'])
pager.mode = None
src.engine.fetch_scope = slow.fetch_scope
src.engine.pantry_timeout_base = 0.2
out = run_fetch(src, {})
check('scope: a timeout reaches the page as an error with a reason',
      'error' in out and 'in time' in out['error'])


# --- 5. posts the viewer's filters hide use no slot ----------------------

def hidden_post(**kw):
    return post(filtered=[{'filter': {
        'context': ['home'], 'filter_action': 'hide', 'title': 'Hush'}}],
        **kw)


p1 = [hidden_post(author='a', age=1), post(author='a', age=2),
      post(author='a', age=3)] + [post(author='o%d' % i, age=4 + i)
                                  for i in range(37)]
p2 = [post(author='a', age=2000 + i) for i in range(2)] \
    + [post(author='q%d' % i, age=2010 + i) for i in range(38)]
p3 = [post(author='z', age=3000)]
cap2 = default_recipe('cap2')
cap2.kitchen.base_sort = 'chronological'
cap2.kitchen.modifiers = [Modifier(type='diversity', max_per_cluster=2,
                                   diversity_dimensions=['author'])]
pager, src = source_for([p1, p2, p3], cap2)
one = run_fetch(src, {})
ids1 = [i['id'] for i in one.get('items', [])]
check('viewer filters: the hidden post is not in the page',
      p1[0]['id'] not in ids1)
check('viewer filters: the two other posts of the capped person show '
      '(the hidden one used no slot)',
      p1[1]['id'] in ids1 and p1[2]['id'] in ids1)
two = run_fetch(src, {'max_id': 'x'})
ids2 = [i['id'] for i in two.get('items', [])]
check('viewer filters: on the next page the person is at the cap, 2 shown '
      'in all (%r)' % src._author_counts.get('a'),
      src._author_counts.get('a') == 2
      and not any(pid in ids2 for pid in (p2[0]['id'], p2[1]['id'])))
warn = post(author='w', filtered=[{'filter': {
    'context': ['home'], 'filter_action': 'warn', 'title': 'W'}}])
other = post(author='w', filtered=[{'filter': {
    'context': ['public'], 'filter_action': 'hide', 'title': 'P'}}])
res = engine([[warn, other]]).process_feed_sync(
    recipe(), hide=viewer_hides)
check('viewer filters: a warn rule and a rule of another context stay in '
      'the page, which shows them collapsed',
      len(res.items) == 2)


# --- 6. a page one that loses no post ------------------------------------

scope_page = [post(author='s%d' % i, age=100 + i) for i in range(5)]
tag_page = [post(author='t%d' % i, age=i + 1, tags=[{'name': 'x'}])
            for i in range(4)]
hashed = default_recipe('hashed')
hashed.kitchen.modifiers = []
hashed.pantry.hashtags_include = ['x']
scope_page = [dict(p, tags=[{'name': 'x'}]) for p in scope_page]
eng = engine(scope=lambda m, c: copy.deepcopy(scope_page),
             fetch_hashtag=lambda t, c: copy.deepcopy(tag_page))
res = eng.process_feed_sync(hashed, count=5)
have = {i['id'] for i in res.items}
check('page size: every post of the scope page is shown (%d of %d)'
      % (len([p for p in scope_page if p['id'] in have]), len(scope_page)),
      all(p['id'] in have for p in scope_page))
check('page size: the hashtag posts are shown too, none cut (%d)'
      % len(res.items), len(res.items) == 9)
check('page size: the cursor still points past the scope page',
      res.scope_oldest_id == min((p['id'] for p in scope_page), key=int)
      and res.has_more)

sys.exit(1 if failures else 0)
