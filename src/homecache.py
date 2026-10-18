# SPDX-License-Identifier: GPL-3.0-or-later
"""The last home timeline page on disk.

A cold first request to a remote server costs three round trips before a
post can be drawn (TCP, TLS, the request itself: ~800 ms to a server
230 ms away), and none of that can start before the window is up. So the
home timeline shows the page it last loaded the moment it is built, and
replaces it when the live page arrives. The cache is only ever a first
paint: the live page is authoritative and swaps it out whole.

One file per account, ``<cache>/babble/home/<account key>.json``. It
holds the account's followers-only and direct posts, so it is ``0700`` /
``0600`` like the Catch Up snapshots, written the same way (it shares
:func:`catchupstore.write_private`), and goes with the login when the
account is removed. A file that is unreadable, holds a status that cannot
be shown, comes from another version or is older than
:data:`MAX_AGE_SECONDS` reads as no cache: a stale first paint is worse
than the spinner it replaces.

The page it holds is the first page this launch loaded, not the latest
page the reader saw: later refreshes and streamed posts correct it on
screen, and will be in the live first page next launch.

No widgets: ``GLib`` for the cache directory only, like ``catchupstore``.
"""

import json
import os
import time

from gi.repository import GLib

from . import catchupstore

VERSION = 1
# A week-old first paint is mostly posts the reader has long moved past.
MAX_AGE_SECONDS = 7 * 24 * 3600

_root = None


def set_root(path):
    """Use *path* instead of the user cache directory (tests)."""
    global _root
    _root = path


def cache_root():
    return _root or os.path.join(GLib.get_user_cache_dir(), 'babble', 'home')


def cache_path(account_key):
    return os.path.join(cache_root(),
                        catchupstore.safe_name(account_key) + '.json')


def write(account_key, statuses, now=None):
    """Keep *statuses*, a page as the API returned it, for the next
    launch. Raises OSError."""
    document = {'version': VERSION,
                'saved_at': time.time() if now is None else now,
                'statuses': statuses}
    catchupstore.write_private(cache_path(account_key),
                               json.dumps(document), cache_root())


def read(account_key, now=None):
    """The page :func:`write` kept for *account_key*, or None when there
    is none worth showing."""
    try:
        with open(cache_path(account_key), encoding='utf-8') as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) \
            or document.get('version') != VERSION:
        return None
    saved_at = document.get('saved_at')
    statuses = document.get('statuses')
    if not isinstance(saved_at, (int, float)) \
            or not isinstance(statuses, list) or not statuses:
        return None
    if not all(_showable(status) for status in statuses):
        return None
    if (time.time() if now is None else now) - saved_at > MAX_AGE_SECONDS:
        return None
    return statuses


def _showable(status):
    """Whether *status* is enough of a Status entity for a row.

    The cache is best effort, so a file whose entries cannot be shown is
    treated like an unreadable one instead of raising during page
    construction, where it would keep the live page from ever loading.
    The checks are the keys rows read without a fallback: the id, the
    account, and a boost's reblog and its account."""
    if not isinstance(status, dict):
        return False
    if not isinstance(status.get('id'), str) or not status['id']:
        return False
    if not isinstance(status.get('account'), dict):
        return False
    reblog = status.get('reblog')
    if reblog is None:
        return True
    return isinstance(reblog, dict) \
        and isinstance(reblog.get('id'), str) and bool(reblog['id']) \
        and isinstance(reblog.get('account'), dict)


def delete(account_key):
    """Forget the page kept for *account_key* (its login was removed)."""
    try:
        os.unlink(cache_path(account_key))
    except OSError:
        pass
