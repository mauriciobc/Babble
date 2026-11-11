#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""``StreamRouter`` routes each Streams event to the page that owns it, and
to nothing else, and answers which streams the open places want.

``test-stream-events`` holds the router's declared table against the
documented events; a branch that called the wrong page, or the wrong method
on the right page, would pass it. This drives the router through its
interface with plain callables and recorders, and asserts the exact calls
per event, in order.

A plain recorder accepts any method name, so every method it records is
also checked to exist on the real class. The router imports no window, so
this needs no display."""

import os
# Never the user's real dconf.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src.conversations import ConversationsPage  # noqa: E402
from src.desktopnotify import DesktopNotifier  # noqa: E402
from src.navigation import StreamPlace  # noqa: E402
from src.notifications import NotificationsPage  # noqa: E402
from src.streamrouter import StreamHost, StreamRouter  # noqa: E402
from src.streaming import (DIRECT, FEDERATED, LOCAL, USER,  # noqa: E402
                           hashtag_stream, list_stream)
from src.timelinepage import TimelinePage  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


class Recorder:
    """Records (target, method, args) for any attribute called on it."""

    def __init__(self, name, log):
        self._name = name
        self._log = log

    def __getattr__(self, method):
        if method.startswith('__'):
            raise AttributeError(method)

        def call(*args):
            self._log.append((self._name, method, args))
        return call


class TimelineRecorder(Recorder):
    """A recorded page the host calls a timeline page."""


class World:
    """The window's side of the host, as plain values the test sets."""

    def __init__(self, with_pages=True, public=True):
        self.log = []
        rec = self.rec = {
            name: TimelineRecorder(name, self.log)
            for name in ('home', 'local', 'federated', 'list', 'tagpage')}
        for name in ('notifications', 'conversations', 'notifier'):
            rec[name] = Recorder(name, self.log)
        self.public = public
        self.home = rec['home'] if with_pages else None
        self.notifications = rec['notifications'] if with_pages else None
        # Shaped like the real navigation: local and federated are built
        # places (its ``children``), and each place's timeline is listed
        # once, in build order.
        self.places = {'local': rec['local'],
                       'federated': rec['federated'],
                       'conversations': rec['conversations']} \
            if with_pages else {}
        self.stream_places = [
            StreamPlace('list', '12', rec['list'], False),
            StreamPlace('tag', 'rust', rec['tagpage'], False)] \
            if with_pages else []
        self.timelines = [rec['local'], rec['federated'], rec['list'],
                          rec['tagpage']] if with_pages else []

    def host(self):
        log = self.log
        return StreamHost(
            home_page=lambda: self.home,
            notifications_page=lambda: self.notifications,
            place=self.places.get,
            stream_places=lambda: list(self.stream_places),
            place_timelines=lambda: list(self.timelines),
            is_timeline=lambda page: isinstance(page, TimelineRecorder),
            live_public=lambda: self.public,
            refresh_admin_tool=lambda tool: log.append(
                ('window', 'refresh_admin_tool', (tool,))),
            notifier_handle=lambda payload: log.append(
                ('notifier', 'handle', (payload,))),
            note_background_notification=lambda payload: log.append(
                ('window', 'note_background_notification', (payload,))))


def dispatch(name, stream=USER, with_pages=True):
    world = World(with_pages)
    payload = {'id': 'P-%s' % name}
    StreamRouter(world.host()).route(stream, name, payload)
    return world.log, payload


# In delivery order: home, then the built places' timelines.
TIMELINES = ('home', 'local', 'federated', 'list', 'tagpage')
# The events that fan out to every timeline page: the method each calls,
# and whether the conversations place hears it too (after the timelines).
FAN_OUT = {'delete': ('on_stream_delete', True),
           'status.update': ('on_stream_status_update', False)}


def fans_out(log, name, payload):
    """Every timeline page got the event exactly once, in the order home,
    then the places as built, nothing else did, and the conversations
    place, where it hears it, came last."""
    method, conversations = FAN_OUT[name]
    p = (payload,)
    heard = [entry for entry in log if entry[0] != 'conversations']
    last = [entry for entry in log if entry[0] == 'conversations']
    return (all(entry[1:] == (method, p) for entry in heard)
            and [entry[0] for entry in heard] == list(TIMELINES)
            and last == ([('conversations', method, p)]
                         if conversations else [])
            and (not last or log[-1] == last[0]))


def expected(name, payload, stream=USER):
    p = (payload,)
    if name == 'update':
        target = {LOCAL: 'local', FEDERATED: 'federated'}.get(stream, 'home')
        return [(target, 'on_stream_update', p)]
    return {
        'filters_changed': [('home', 'refresh', ()),
                            ('notifications', 'refresh', ())],
        'notifications_merged': [('notifications', 'refresh', ())],
        'follow_request': [('notifications', 'refresh', ())],
        'announcement': [('notifications', 'load_announcements', ())],
        'announcement.delete': [('notifications', 'load_announcements', ())],
        'announcement.reaction': [],
        'add': [],
        'remove': [],
        'report': [('window', 'refresh_admin_tool', ('reports',))],
        'notification': [('notifications', 'on_stream_notification', p),
                         ('notifier', 'handle', p),
                         ('window', 'note_background_notification', p)],
        'conversation': [('conversations', 'on_stream_conversation', p)],
        'encrypted_message': [('conversations', 'refresh', ())],
    }[name]


EVENTS = ('update', 'delete', 'status.update', 'filters_changed',
          'notifications_merged', 'follow_request', 'announcement',
          'announcement.delete', 'announcement.reaction', 'add', 'remove',
          'report', 'notification', 'conversation', 'encrypted_message')

seen = set()
for event in EVENTS:
    log, payload = dispatch(event)
    check('%s routes to exactly its targets' % event,
          fans_out(log, event, payload) if event in FAN_OUT
          else log == expected(event, payload))
    seen.update((target, method) for target, method, _args in log)

for label, stream in (('the user stream', USER), ('local', LOCAL),
                      ('federated', FEDERATED)):
    log, payload = dispatch('update', stream)
    check('update on %s goes to its own page only' % label,
          log == expected('update', payload, stream))

for label, stream, target in (
        ('a list', ('list', '12'), 'list'),
        ('a hashtag', ('hashtag', 'rust'), 'tagpage')):
    log, payload = dispatch('update', stream)
    check('update on %s goes to its page only' % label,
          log == [(target, 'on_stream_update', (payload,))])
log, payload = dispatch('update', ('list', '99'))
check('update on a list nobody has open goes nowhere', log == [])

for event in EVENTS:
    try:
        log, _payload = dispatch(event, with_pages=False)
        ok = True
    except Exception as err:  # noqa: BLE001
        print('  %s raised %r' % (event, err))
        ok = False
    check('%s survives every page being None' % event, ok)

log, _payload = dispatch('no-such-event')
check('an unknown event does nothing', log == [])

real = {'home': TimelinePage, 'local': TimelinePage,
        'federated': TimelinePage, 'list': TimelinePage,
        'tagpage': TimelinePage, 'notifications': NotificationsPage,
        'conversations': ConversationsPage, 'notifier': DesktopNotifier}
for target, method in sorted(seen):
    if target == 'window':
        # Window-owned effects: `navigation window` follows them on the
        # real window.
        continue
    check('%s.%s exists on %s' % (target, method, real[target].__name__),
          callable(getattr(real[target], method, None)))

# What is looked up when: nothing is held between events.
world = World()
router = StreamRouter(world.host())
router.route(USER, 'update', {'id': 'first'})
world.home = world.rec['local']
del world.log[:]
router.route(USER, 'update', {'id': 'second'})
check('the router looks a page up at each event, holding none',
      world.log == [('local', 'on_stream_update', ({'id': 'second'},))])

# Timeline pages: home, then each built place's, each once, and only
# timeline pages (the conversations page is not one).
world = World()
world.timelines.append(world.rec['conversations'])
found = StreamRouter(world.host()).timeline_pages()
check('the timeline pages are home then the places\' own, each once, in '
      'that order, and only timeline pages',
      [next(name for name, rec in world.rec.items() if rec is page)
       for page in found] == list(TIMELINES))
check('and none at all while nothing is built',
      StreamRouter(World(with_pages=False).host()).timeline_pages() == [])

# Local and federated are built places and are not listed a second time as
# public pages: a delete and a status update reach each page once.
for event in ('delete', 'status.update'):
    world = World()
    StreamRouter(world.host()).route(USER, event, {'id': 'D'})
    names = [entry[0] for entry in world.log]
    check('%s reaches local and federated once each, and home once' % event,
          all(names.count(name) == 1 for name in TIMELINES))


# The wanted streams, from every setting and every built place.
def wanted(public=True, conversations=False, local=False, federated=False,
           places=()):
    world = World(with_pages=False, public=public)
    rec = world.rec
    if conversations:
        world.places['conversations'] = rec['conversations']
    if local:
        world.places['local'] = rec['local']
    if federated:
        world.places['federated'] = rec['federated']
    world.stream_places = list(places)
    return StreamRouter(world.host()).wanted_streams()


def place(kind, key, multi=False, page=True):
    return StreamPlace(kind, key, TimelineRecorder('p', []) if page else None,
                       multi)


check('nothing built wants nothing', wanted() == set())
check('nothing built wants nothing with public updates off',
      wanted(public=False) == set())
check('the conversations place wants the direct stream',
      wanted(conversations=True) == {DIRECT})
check('and wants it whether or not public updates are on',
      wanted(public=False, conversations=True) == {DIRECT})
check('a built local page wants the local stream',
      wanted(local=True) == {LOCAL})
check('a built federated page wants the federated stream',
      wanted(federated=True) == {FEDERATED})
check('both public pages want both streams',
      wanted(local=True, federated=True) == {LOCAL, FEDERATED})
check('public-live-updates off drops both public streams',
      wanted(public=False, local=True, federated=True) == set())
check('a list place wants its list stream',
      wanted(places=[place('list', '12')]) == {list_stream('12')})
check('a list stream is not gated by public-live-updates',
      wanted(public=False, places=[place('list', '12')])
      == {list_stream('12')})
check('a hashtag place wants its tag stream, lower-cased',
      wanted(places=[place('tag', 'Rust')]) == {hashtag_stream('rust')})
check('public-live-updates off drops a hashtag stream',
      wanted(public=False, places=[place('tag', 'rust')]) == set())
check('a hashtag page over several tags wants no stream',
      wanted(places=[place('tag', 'rust', multi=True)]) == set())
check('a place without a timeline page wants no stream',
      wanted(places=[place('list', '12', page=False),
                     place('tag', 'rust', page=False)]) == set())
check('two hashtag places of one tag want one stream',
      wanted(places=[place('tag', 'rust'), place('tag', 'RUST')])
      == {hashtag_stream('rust')})
check('everything built at once adds up',
      wanted(conversations=True, local=True, federated=True,
             places=[place('list', '1'), place('list', '2'),
                     place('tag', 'a'), place('tag', 'b', multi=True)])
      == {DIRECT, LOCAL, FEDERATED, list_stream('1'), list_stream('2'),
          hashtag_stream('a')})
check('everything built with public updates off keeps the reader\'s own',
      wanted(public=False, conversations=True, local=True, federated=True,
             places=[place('list', '1'), place('tag', 'a')])
      == {DIRECT, list_stream('1')})

world = World()
streams = StreamRouter(world.host()).place_streams()
check('place_streams groups the pages by stream',
      streams == {('list', '12'): [world.rec['list']],
                  ('hashtag', 'rust'): [world.rec['tagpage']]})

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
