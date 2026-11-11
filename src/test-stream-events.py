# SPDX-License-Identifier: GPL-3.0-or-later

"""Every documented Streams event is decoded and routed, none silently
dropped. Needs no display and no server: it asks the stream router for
the events it declares and imports ``streaming`` for the payload
classification.

Two failures this guards against, and both shipped:

* ``streaming.JSON_EVENTS`` listed the events whose payload is a JSON
  document, and ``add`` / ``remove`` -- Mastodon 4.3's collection
  membership -- were missing. Their payloads were emitted to the owner
  as raw JSON *strings*, which no handler could read.
* the window's event handler (now the router's route table) handled
  seven of the documented events and dropped the rest without a word.
  ``follow_request`` was the one that hurt: the request arrived, the
  server wrote the notification, and the list the reader was looking at
  never learned of it until they pulled to refresh.
"""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src import streaming, streamrouter  # noqa: E402

failures = 0


def check(label, condition):
    global failures
    if not condition:
        failures += 1
        print('FAIL - %s' % label)
    else:
        print('ok - %s' % label)


# docs.joinmastodon.org/methods/streaming. Every event the API can send,
# and what its payload carries.
DOCUMENTED = {
    'update': 'json',
    'notification': 'json',
    'delete': 'id',
    'status.update': 'json',
    'filters_changed': 'empty',
    'notifications_merged': 'empty',
    'conversation': 'json',
    'follow_request': 'empty',
    'announcement': 'json',
    'announcement.delete': 'id',
    'announcement.reaction': 'json',
    'report': 'json',
    'encrypted_message': 'json',
    'add': 'json',
    'remove': 'json',
}

JSON_PAYLOAD = {name for name, kind in DOCUMENTED.items() if kind == 'json'}

for name in sorted(JSON_PAYLOAD - streaming.JSON_EVENTS):
    check('%s carries a JSON payload and is decoded' % name, False)
check('every JSON-payload event is decoded',
      JSON_PAYLOAD <= streaming.JSON_EVENTS)

# An event decoded but not routed is dead weight; an event routed but not
# documented is a typo. The two together mean the list above is complete.
#
# The stream router declares the events it routes as a table, so this asks
# it instead of reading source.
class _Host:
    """A host whose every callable would fail a test that used it."""

    def __getattr__(self, name):
        raise AssertionError('the table is read without a host call: %s'
                             % name)


def router_with(table):
    class Router(streamrouter.StreamRouter):
        ROUTES = table
    return Router(_Host())


def gaps(documented, table):
    """The rules the table is held to, as the labels it breaks."""
    routed = router_with(table).routed_events()
    broken = ['%s is documented but not routed' % name
              for name in sorted(set(documented) - routed)]
    broken += ['%s is routed but not documented, so it is a typo' % name
               for name in sorted(routed - set(documented))]
    # A misspelt handler cannot get this far: the table holds references,
    # so it raises when the module is imported. What can still go wrong is
    # an entry that is not one of the router's own handlers (a name left
    # as a string, or some other callable).
    broken += ['%s is not a router handler: %r' % (name, handler)
               for name, handler in sorted(table.items())
               if handler is not None
               and getattr(streamrouter.StreamRouter,
                           getattr(handler, '__name__', ''), None)
               is not handler]
    return broken


router = router_with(streamrouter.ROUTES)
routed = router.routed_events()
check('the router declares its routes', bool(routed))
for name in sorted(DOCUMENTED):
    check('%s reaches the router' % name, name in routed)
for name in sorted(routed - set(DOCUMENTED)):
    check('%s is not a documented event, so it is a typo' % name, False)
check('the route table covers the documented set exactly',
      routed == set(DOCUMENTED))
check('every route is one of the router\'s own handlers, or None',
      not [line for line in gaps(DOCUMENTED, streamrouter.ROUTES)
           if 'is not a router handler' in line])

# The rule must be able to fail: a synthetic table with an event decoded
# and documented but not routed, one routed but not documented, and an
# entry that is not a handler (a name left as a string).
synthetic = dict(streamrouter.ROUTES)
del synthetic['follow_request']
synthetic['follow-request'] = streamrouter.StreamRouter._notifications_refresh
synthetic['report'] = '_report'
broken = gaps(DOCUMENTED, synthetic)
check('an event documented but not routed is caught',
      'follow_request is documented but not routed' in broken)
check('an event routed but not documented is caught',
      'follow-request is routed but not documented, so it is a typo'
      in broken)
check('an entry that is not a handler is caught',
      "report is not a router handler: '_report'" in broken)
check('a complete table is not flagged by the same rule',
      gaps(DOCUMENTED, streamrouter.ROUTES) == [])

if failures:
    print('%d checks failed' % failures)
    sys.exit(1)
print('every documented stream event is decoded and routed')
