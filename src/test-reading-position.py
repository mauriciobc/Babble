#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reopening Home where the reader stopped: the per-account store, the
anchored first page, a remembered post that is gone, the button for what
is newer, the preference, and what the page saves on the way out.
Exits 77 (skip) without a display."""

import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import pages, readingposition, timelinepage  # noqa: E402

Adw.init()
failures = []
settings = pages.settings()
ACCOUNT = 'https://example.org#1'


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))


def pump(count=40):
    context = GLib.MainContext.default()
    for _ in range(count):
        while context.iteration(False):
            pass


def status(number):
    return {'id': '%05d' % number, 'content': '<p>post %d</p>' % number,
            'account': {'acct': 'user%d' % number, 'id': str(number)},
            'created_at': '2026-01-01T00:00:00.000Z'}


class _Server:
    token = 'token'
    instance_info = None
    instance = 'https://example.org'

    def __init__(self, numbers):
        self.posts = [status(n) for n in numbers]      # ascending ids
        self.params = []

    def rate_limited(self):
        return False

    def timelines_home_async(self, params=None, callback=None, **_kw):
        params = dict(params or {})
        self.params.append(params)
        limit = int(params.get('limit', 20))
        if 'min_id' in params:
            above = [p for p in self.posts
                     if int(p['id']) > int(params['min_id'])]
            out = above[:limit][::-1]
        elif 'max_id' in params:
            below = [p for p in self.posts
                     if int(p['id']) < int(params['max_id'])]
            out = below[-limit:][::-1]
        else:
            out = self.posts[-limit:][::-1]
        callback(self, out)

    def timelines_home_finish(self, result):
        return result

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def ids(page):
    return [page.model.get_item(i).raw['id']
            for i in range(page.model.get_n_items())
            if hasattr(page.model.get_item(i), 'raw')]


def reset():
    settings.reset('home-reading-position')
    settings.reset('resume-reading-position')
    settings.set_string('active-account', ACCOUNT)


def home(server):
    page = timelinepage.TimelinePage(server, mode='home', autoload=False)
    page.ensure_loaded()
    return page


# The store

reset()
check('nothing is remembered at first',
      readingposition.get_position(settings, ACCOUNT) is None)
readingposition.set_position(settings, ACCOUNT, '00050')
readingposition.set_position(settings, 'https://other.example#1', '00007')
check('a position round-trips',
      readingposition.get_position(settings, ACCOUNT) == '00050')
check('the same id on another instance does not collide',
      readingposition.get_position(settings, 'https://other.example#1')
      == '00007')
readingposition.set_position(settings, ACCOUNT, None)
check('None forgets one account only',
      readingposition.get_position(settings, ACCOUNT) is None
      and readingposition.get_position(
          settings, 'https://other.example#1') == '00007')
check('a non-numeric id gives no anchor',
      readingposition.anchor_params('abc') is None
      and readingposition.anchor_params('00050') == '51')

# The anchored first page

reset()
readingposition.set_position(settings, ACCOUNT, '00050')
server = _Server(range(1, 101))
page = home(server)
check('the first page asks for the remembered post, inclusive',
      server.params[0].get('max_id') == '51')
check('the remembered post is first', ids(page)[0] == '00050')
check('the cursors are the page\'s own',
      page.model.newest_id == '00050' and page.model.oldest_id == '00031')
check('the pill offers newer posts',
      page.new_posts_revealer.get_reveal_child()
      and page.new_pill.get_label() == 'Newer Posts')
server.posts.append(status(101))
page.on_stream_update(status(101))
check('a streamed post does not push the resumed post away',
      ids(page)[0] == '00050' and page.model.newest_id == '00050')
page.new_pill.emit('clicked')
check('choosing it fetches what is newer, with min_id',
      server.params[1].get('min_id') == '00050')
check('and the newer posts are on top',
      ids(page)[0] == '00101' and '00051' in ids(page))
check('the pill goes away',
      not page.new_posts_revealer.get_reveal_child())

# A remembered post that was deleted opens at the nearest older one.
reset()
readingposition.set_position(settings, ACCOUNT, '00050')
server = _Server([n for n in range(1, 101) if n != 50])
page = home(server)
check('a deleted post opens at the nearest older', ids(page)[0] == '00049')

# Nothing at or below it (everything older is gone): start from the top.
reset()
readingposition.set_position(settings, ACCOUNT, '00001')
server = _Server(range(2, 41))
page = home(server)
check('an empty anchored page falls back to the newest',
      ids(page)[0] == '00040' and len(server.params) == 2
      and 'max_id' not in server.params[1])
check('and forgets the position',
      readingposition.get_position(settings, ACCOUNT) is None)

# Another account's position is not applied.
reset()
readingposition.set_position(settings, 'https://other.example#1', '00050')
server = _Server(range(1, 101))
page = home(server)
check('another account\'s position is ignored',
      'max_id' not in server.params[0] and ids(page)[0] == '00100')

# The preference.
reset()
readingposition.set_position(settings, ACCOUNT, '00050')
settings.set_boolean('resume-reading-position', False)
server = _Server(range(1, 101))
page = home(server)
check('with the preference off the page opens at the newest',
      'max_id' not in server.params[0] and ids(page)[0] == '00100')
page.save_reading_position()
check('and nothing is read or written',
      readingposition.get_position(settings, ACCOUNT) == '00050')

# Only Home resumes.
reset()
readingposition.set_position(settings, ACCOUNT, '00050')
server = _Server(range(1, 101))
public = timelinepage.TimelinePage(server, mode='public', autoload=False)
check('the public timeline does not resume', public._resume_key is None)

# Saving what is on screen. Every third entry is a boost of a post with
# an unrelated id, as on a real Home timeline: the id to remember is the
# entry's, the one max_id pages by.
reset()
server = _Server(range(1, 101))
for _post in server.posts:
    if int(_post['id']) % 3 == 0:
        _post['reblog'] = status(int(_post['id']) + 500)
page = home(server)
window = Gtk.Window(default_width=700, default_height=500)
window.set_child(page)
window.present()
pump()
top = page.top_visible_status_id()
check('the top of a fresh page is the newest post', top == '00100')
page.save_reading_position()
check('reading from the newest post leaves nothing to resume',
      readingposition.get_position(settings, ACCOUNT) is None)
adjustment = page._scrolled.get_vadjustment()


def id_under_top_edge():
    """What is really drawn at the top edge, found by picking the widget
    there rather than by the geometry top_visible_status_id reads."""
    widget = page.list_view.pick(page.list_view.get_width() / 2, 2,
                                 Gtk.PickFlags.DEFAULT)
    while widget is not None and not hasattr(widget, 'item'):
        widget = widget.get_parent()
    return None if widget is None or widget.item is None \
        else widget.item.raw['id']


# The row at the top edge, at the top, a little down and far down: the
# lookup used to read the scroll offset as part of the row's position and
# found nothing once the list was scrolled far.
for fraction in (0.0, 0.05, 0.3, 0.6, 0.9):
    adjustment.set_value(adjustment.get_upper() * fraction)
    pump()
    expected = id_under_top_edge()
    check('the top post is found at %d%% of the list' % (fraction * 100),
          expected is not None
          and page.top_visible_status_id() == expected,
          '%s vs %s' % (page.top_visible_status_id(), expected))

adjustment.set_value(adjustment.get_upper() / 4)
pump()
top = page.top_visible_status_id()
check('scrolled down, the top is an older post',
      top is not None and int(top) < 100)
page.save_reading_position()
check('it is saved', readingposition.get_position(settings, ACCOUNT) == top)
page.schedule_reading_position_save()
check('a pause schedules a save', page._save_source != 0)
window.destroy()
pump()
check('closing leaves no save timer', page._save_source == 0)

reset()
if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
