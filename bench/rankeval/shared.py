# SPDX-License-Identifier: GPL-3.0-or-later
"""The pieces every part of the harness needs: the client, the ids, the stop.

``make_client``/``call``/``read_secret`` are the one way to the network;
the synthetic world's identity constants live here so the store and the
world cannot disagree about them; ``Stopped`` is the one exception the
run raises to stop.
"""

import datetime
from gi.repository import GLib
from src import keyringstore, mastodon
from src.api import core as api_core


# Mastodon's per-request maximum and its cap on a home timeline.
PAGE_LIMIT = 40

class Stopped(Exception):
    """The run must stop: the instance is rate limiting, or a call failed.

    `collect` catches it, keeps every row gathered before it, and records
    the run as partial.
    """

def iso_utc(moment=None):
    """*moment* (default: now) as UTC ISO-8601, with microseconds.

    The microseconds are not decoration. The timestamp is the identity of
    a run -- ``runs.ts`` is its PRIMARY KEY, and ``observations`` is keyed
    by ``(run_ts, post_id)`` -- so two runs in the same second would
    overwrite each other: one run lost, and its counters with it. The
    plan reads "the last observation at or before T" from that table, so
    a lost row is a wrong candidate counter and a wrong gate number.

    The width is fixed (``%f`` is always six digits), so the strings
    still sort chronologically as text -- which is how T1.2 compares them
    in SQL (``run_ts <= T``, ``ORDER BY ts``). A variable-width fraction
    would not: ``'...00.5Z' <= '...00Z'`` is true as text and false in
    time.
    """
    if moment is None:
        moment = datetime.datetime.now(datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%S.%fZ')
# /accounts/relationships accepts at most this many ids per request.
RELATIONSHIP_BATCH = 40

def make_client(instance, token):
    """The real client. `collect` takes this factory as a parameter, so a
    test hands in a duck-typed fake and never constructs a Soup.Session."""
    return mastodon.MastodonClient(instance, token=token)

def call(client, path, params=None):
    """GET *path* and return ``(data, next_params)``.

    The client answers on the main loop, so a `GLib.MainLoop` runs until
    the callback fires, and the first two items of `api.core.finish_paged`'s
    three are what the caller reads. List parameters go as
    ``{'id[]': [...]}`` (the form used by Catch Up's pipeline);
    ``urlencode(doseq=True)`` in the client expands them.
    """
    loop = GLib.MainLoop()
    answer = {}

    def finished(_client, result, _data=None):
        try:
            page, next_params, _previous = api_core.finish_paged(
                client, result)
        except GLib.Error as error:
            answer['error'] = error
        except Exception as error:
            # Anything else -- a malformed fixture in the self-test, a
            # client that raises something else -- would otherwise escape
            # the handler, and the KeyError below would replace the real
            # cause with a symptom.
            answer['error'] = error
        else:
            answer['value'] = (page, next_params)
        finally:
            loop.quit()

    client.request_async('GET', path, params=params, callback=finished)
    if not answer:                    # a fake may answer synchronously
        loop.run()
    if 'error' in answer:
        raise answer['error']
    return answer['value']

def read_secret(instance, account_id):
    """The token Babble stored for *account_id*, from the keyring.

    `load_secret_async` needs the loop, and calls back with
    ``(value, error)``.
    """
    loop = GLib.MainLoop()
    answer = {}

    def done(value, error=None):
        answer['value'] = value
        answer['error'] = error
        loop.quit()

    keyringstore.load_secret_async(instance, account_id, done)
    if not answer:
        loop.run()
    if answer.get('error') is not None:
        raise answer['error']
    return answer.get('value')

def counts(status):
    """The counters and flags stored for *status*.

    A boost is not the boosted post: the counts (and the reader's flags)
    that belong to the post are the ones `reblog` carries.
    """
    effective = status.get('reblog')
    if not isinstance(effective, dict):
        effective = status
    return (int(effective.get('favourites_count') or 0),
            int(effective.get('reblogs_count') or 0),
            int(effective.get('replies_count') or 0),
            int(bool(effective.get('favourited'))),
            int(bool(effective.get('reblogged'))),
            int(bool(effective.get('bookmarked'))))
# Everything about the synthetic world is a function of these, so the
# dates the checks name are the dates the world produces.
SYNTHETIC_INSTANCE = 'https://synth.example'

VIEWER_ID = 'synth-viewer'

# The acct the fake's account endpoint answers with, so the viewer's
# handle is one of the strings a summary must never carry.
VIEWER_ACCT = 'synth'
