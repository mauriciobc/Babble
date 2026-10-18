#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The home timeline's first paint from disk: the cache file (what it
keeps, who may read it, when it is ignored), the model swap that
replaces it with the live page, and the page that ties them together.
The page checks need a display; without one only the first two sets run.
"""

import json
import os
import stat
import sys
import tempfile
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib  # noqa: E402

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import accounts, homecache, timelinedata  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def status(number, text='post', **extra):
    data = {'id': '%05d' % number, 'content': '<p>%s</p>' % text,
            'account': {'acct': 'user%d' % number, 'id': str(number)},
            'created_at': '2026-01-01T00:00:00.000Z',
            'favourites_count': 0}
    data.update(extra)
    return data


def page_of(first, last):
    return [status(n) for n in range(first, last, -1)]


work = tempfile.mkdtemp(prefix='babble-homecache-')
homecache.set_root(os.path.join(work, 'home'))
KEY = 'https://one.example#1'
OTHER = 'https://two.example#1'

# -- The file -----------------------------------------------------------

posts = page_of(20, 0)
homecache.write(KEY, posts)
check('a written page reads back as it was', homecache.read(KEY) == posts)
check('another account has no page', homecache.read(OTHER) is None)

homecache.write(OTHER, [status(99, 'elsewhere')])
check('accounts keep separate pages',
      homecache.read(KEY) == posts
      and homecache.read(OTHER)[0]['id'] == '00099')

path = homecache.cache_path(KEY)
check('the file is private to its owner',
      stat.S_IMODE(os.stat(path).st_mode) == 0o600
      and stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700)
check('an account key cannot name a path outside the cache',
      os.path.dirname(homecache.cache_path('../../x#1'))
      == homecache.cache_root())

now = time.time()
homecache.write(KEY, posts, now=now - homecache.MAX_AGE_SECONDS + 60)
check('a page just inside its age is shown',
      homecache.read(KEY, now=now) == posts)
homecache.write(KEY, posts, now=now - homecache.MAX_AGE_SECONDS - 60)
check('a page past its age is ignored', homecache.read(KEY, now=now) is None)

with open(path, 'w') as handle:
    handle.write('{"version": 1, "saved_at": ')
check('a half-written file is ignored', homecache.read(KEY) is None)
with open(path, 'w') as handle:
    json.dump({'version': homecache.VERSION + 1, 'saved_at': time.time(),
               'statuses': posts}, handle)
check('another version of the file is ignored',
      homecache.read(KEY) is None)
with open(path, 'w') as handle:
    json.dump({'version': homecache.VERSION, 'saved_at': time.time(),
               'statuses': []}, handle)
check('an empty page is no first paint', homecache.read(KEY) is None)

homecache.write(KEY, posts)
homecache.delete(KEY)
check('delete forgets the page', homecache.read(KEY) is None)
homecache.delete(KEY)
check('deleting a page that is not there is fine', True)

# Removing the login takes the cached posts with it.
homecache.write(KEY, posts)
homecache.write(OTHER, posts)
schemas = Gio.SettingsSchemaSource.get_default()
settings = (Gio.Settings.new('io.github.mauriciobc.Babble')
            if schemas is not None and schemas.lookup(
                'io.github.mauriciobc.Babble', True) is not None else None)
if settings is not None:
    accounts.save_account(settings, {'id': '1',
                                     'instance': 'https://one.example'})
    accounts.save_account(settings, {'id': '1',
                                     'instance': 'https://two.example'},
                          activate=False)
    accounts.forget_account(settings, '1', 'https://one.example')
    check('forgetting an account deletes its cached page',
          homecache.read(KEY) is None)
    check('and leaves another account\'s alone',
          homecache.read(OTHER) == posts)
else:
    print('SKIP - no GSettings schema for the account removal check')

# -- Swapping the cache for the live page -------------------------------

model = timelinedata.TimelineModel()
model.group_boosts_enabled = False
cached = page_of(20, 10)                      # posts 20..11
model.append_old(cached)
kept_item = model.get_item(5)                  # post 15, in both pages
kept_item.ui['expanded'] = True
changes = []
model.connect('items-changed', lambda m, p, r, a: changes.append((p, r, a)))
edited = status(15, 'post', favourites_count=7)
# Post 11 was deleted, 21 is new, and 15 got a favourite.
live = [status(21)] + [edited if n == 15 else status(n)
                       for n in range(20, 11, -1)]
changed = []
kept_item.connect('changed', lambda _i: changed.append(True))
model.replace_all(live)

ids = [model.get_item(i).raw['id'] for i in range(model.get_n_items())]
check('the live page replaces the cached one',
      ids == [s['id'] for s in live])
check('a post that was deleted since is gone', '00011' not in ids
      and '00012' in ids)
check('a post in both pages keeps its item (and so its view state)',
      model.get_item(ids.index('00015')) is kept_item
      and kept_item.ui.get('expanded') is True)
check('a changed post updates in place', changed
      and kept_item.raw['favourites_count'] == 7)
check('the cursors follow the live page',
      model.newest_id == '00021' and model.oldest_id == '00012')
check('the whole list is swapped in one change', len(changes) == 1
      and changes[0][0] == 0 and changes[0][1] == len(cached))

model.replace_all([])
check('an empty live page empties the list',
      model.get_n_items() == 0 and model.oldest_id is None)

# Posts of the old page can be fetched again: they are not "seen".
model.replace_all(page_of(5, 0))
model.append_old([status(3), status(0)])
check('older pages append after a swap without duplicating',
      [model.get_item(i).raw['id'] for i in range(model.get_n_items())]
      == ['00005', '00004', '00003', '00002', '00001', '00000'])

# -- The page ----------------------------------------------------------

if not os.environ.get('DISPLAY'):
    print('SKIP - no display for the page checks')
else:
    from src import timelinepage  # noqa: E402

    Adw.init()

    class FakeClient:
        """Holds the home request until the test answers it."""

        def __init__(self):
            self.requests = []

        def rate_limited(self):
            return True                     # no followed-tags traffic

        def timelines_home_async(self, params=None, callback=None,
                                 cancellable=None):
            self.requests.append((dict(params or {}), callback))

        def timelines_home_finish(self, result):
            if isinstance(result, GLib.Error):
                raise result
            return result

        def answer(self, result, index=-1):
            _params, callback = self.requests[index]
            callback(self, result)

    def ids_of(page):
        return [page.model.get_item(i).raw['id']
                for i in range(page.model.get_n_items())]

    def pump():
        context = GLib.MainContext.default()
        for _ in range(50):
            context.iteration(False)

    def page_for(client, key=KEY):
        homecache.set_root(os.path.join(work, 'page'))
        page = timelinepage.TimelinePage(client, mode='home', cache_key=key)
        pump()
        return page

    # A launch with a cached page: posts before the network answers.
    homecache.set_root(os.path.join(work, 'page'))
    homecache.write(KEY, page_of(20, 10))
    client = FakeClient()
    page = page_for(client)
    check('the cached page is on screen before the server answers',
          ids_of(page) == [s['id'] for s in page_of(20, 10)]
          and page.stack.get_visible_child_name() == 'list')
    check('the live page is still requested', len(client.requests) == 1)

    page.load_older()
    check('no older page is requested while the cache shows',
          len(client.requests) == 1)

    client.answer([status(25), status(24)] + page_of(20, 14))
    pump()
    check('the live page replaces the cache',
          ids_of(page)[:3] == ['00025', '00024', '00020']
          and '00011' not in ids_of(page))
    check('the replaced list pages on normally',
          (page.load_older() or True) and len(client.requests) == 2)
    pump()
    saved = homecache.read(KEY)
    check('the live page is kept for the next launch',
          saved is not None and saved[0]['id'] == '00025')

    # Offline: the cache stays, as content, with the failure reported.
    homecache.write(KEY, page_of(20, 10))
    client = FakeClient()
    page = page_for(client)
    client.answer(GLib.Error('offline', 'g-io-error-quark', 0))
    pump()
    check('a failed load keeps the cached page instead of an error page',
          ids_of(page) == [s['id'] for s in page_of(20, 10)]
          and page.stack.get_visible_child_name() == 'list')

    # No cache: the spinner, then the page.
    homecache.delete(KEY)
    client = FakeClient()
    page = page_for(client)
    check('without a cache the loading page shows',
          page.stack.get_visible_child_name() == 'loading')
    client.answer(page_of(20, 10))
    pump()
    check('the first live page is shown and kept',
          ids_of(page)[0] == '00020'
          and homecache.read(KEY)[0]['id'] == '00020')

    # Another account's cache is not this account's first paint.
    homecache.write(OTHER, page_of(30, 25))
    client = FakeClient()
    page = page_for(client, key=KEY)
    check('a page shows only its own account\'s cache',
          ids_of(page)[0] == '00020')

    # No key: nothing is read or written.
    homecache.set_root(os.path.join(work, 'page'))
    before = sorted(os.listdir(homecache.cache_root()))
    client = FakeClient()
    page = timelinepage.TimelinePage(client, mode='home')
    pump()
    check('a timeline without an account key shows no cache',
          page.stack.get_visible_child_name() == 'loading')
    client.answer(page_of(9, 4))
    pump()
    check('and leaves no cache',
          sorted(os.listdir(homecache.cache_root())) == before)

if failures:
    print('%d failure(s): %s' % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('all home cache checks passed')
