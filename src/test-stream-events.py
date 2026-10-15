# SPDX-License-Identifier: GPL-3.0-or-later

"""Every documented Streams event is decoded and routed, none silently
dropped. Needs no display and no server: it reads ``window.py`` for the
branch names and imports ``streaming`` for the payload classification.

Two failures this guards against, and both shipped:

* ``streaming.JSON_EVENTS`` listed the events whose payload is a JSON
  document, and ``add`` / ``remove`` -- Mastodon 4.3's collection
  membership -- were missing. Their payloads were emitted to the owner
  as raw JSON *strings*, which no handler could read.
* ``window._on_stream_event`` handled seven of the documented events
  and dropped the rest without a word. ``follow_request`` was the one
  that hurt: the request arrived, the server wrote the notification, and
  the list the reader was looking at never learned of it until they
  pulled to refresh.
"""

import ast
import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src import streaming  # noqa: E402

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
window_path = os.path.join(srcdir, 'window.py')
with open(window_path, encoding='utf-8') as source:
    tree = ast.parse(source.read(), window_path)

handler = next((node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == '_on_stream_event'), None)
check('the window still routes stream events', handler is not None)

branched = set()
if handler is not None:
    for node in ast.walk(handler):
        # ``if name == 'update':`` and ``if name in ('a', 'b'):`` both.
        literals = []
        if isinstance(node, ast.Compare) and len(node.ops) == 1 \
                and isinstance(node.ops[0], (ast.Eq, ast.In)):
            right = node.comparators[0]
            literals = list(right.elts) if isinstance(
                right, (ast.Tuple, ast.List, ast.Set)) else [right]
        elif isinstance(node, ast.Assign) \
                and isinstance(node.value, (ast.Tuple, ast.List, ast.Set)):
            literals = list(node.value.elts)
        for literal in literals:
            if isinstance(literal, ast.Constant) \
                    and isinstance(literal.value, str):
                branched.add(literal.value)

for name in sorted(DOCUMENTED):
    check('%s reaches the window' % name, name in branched)
for name in sorted(branched - set(DOCUMENTED)):
    check('%s is not a documented event, so it is a typo' % name, False)
check('the branch list covers the documented set exactly',
      branched == set(DOCUMENTED))

if failures:
    print('%d checks failed' % failures)
    sys.exit(1)
print('every documented stream event is decoded and routed')
