# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading markers (audit rows NT-9 and NT-10), declared once and sent
through `api.core`.

The only marker the app keeps is the notifications one, so the unread badge
can drop to zero; `get_async` reads any named timelines and `save_async`
stores the last read id of one. The request on the wire is the one the
notification controller sent before: ``timeline[]=notifications`` in the
query and ``notifications[last_read_id]`` in the form.

`min_version` is None: the app does not gate on it (Mastodon 3.0.0 added
it), so the release is in ``mastodon_version``.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/markers/'

GET = core.declare('markers.get', 'GET', '/markers', 'read:statuses', None,
                   DOCS + '#get', note='query timeline[] (home, '
                   'notifications)', mastodon_version='3.0.0')
SAVE = core.declare('markers.save', 'POST', '/markers', 'write:statuses',
                    None, DOCS + '#create',
                    note='form <timeline>[last_read_id]',
                    mastodon_version='3.0.0')


def get_async(client, timelines, callback=None, cancellable=None):
    """The markers of the named *timelines* (``['notifications']``)."""
    params = [('timeline[]', name) for name in timelines]
    return core.send(client, GET, params=params, callback=callback,
                     cancellable=cancellable)


def get_finish(client, result):
    return core.finish(client, result)


def save_async(client, timeline, last_read_id, callback=None,
               cancellable=None):
    """Store *last_read_id* as the last read post of *timeline*."""
    return core.send(client, SAVE,
                     form={'%s[last_read_id]' % timeline: last_read_id},
                     callback=callback, cancellable=cancellable)


def save_finish(client, result):
    return core.finish(client, result)
