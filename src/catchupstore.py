# SPDX-License-Identifier: GPL-3.0-or-later
"""Catch Up snapshots on disk (CATCH-UP.md section 7).

No widgets: only ``GLib`` for the cache directory, so the account store can
delete an account's snapshots without importing the Catch Up page, and every
function here is safe to run on a worker thread.

A snapshot is ``<cache>/babble/catch-up/<account key>/<id>.json``. The
directory is ``0700`` and the files ``0600``: they hold followers-only and
direct posts. Files are written to a temporary name in the same directory and
renamed over the target, so a reader never sees half a file.

The file is plain JSON, laid out so that it can be read in pieces. The
metadata keys come first, then ``"posts"`` with one post per line, then the
rest. ``read_meta`` parses only the head (a few hundred bytes), which is what
lists the "Previous catch-ups" without loading three snapshots of up to a
thousand posts each, and ``read_snapshot`` parses a post at a time, so the
interpreter gets to run other threads (the main loop) between posts instead of
holding the GIL for one multi-megabyte ``json.loads``.
"""

import json
import os
import shutil
import urllib.parse
import uuid

from gi.repository import GLib

VERSION = 1
KEEP = 3

# Keys written first, in this order. ``count`` and ``oldest_at`` are
# derived; the others describe the run.
META_KEYS = ('version', 'id', 'account', 'start_at', 'end_at', 'period',
             'stopped', 'count', 'oldest_at')

_POSTS_OPEN = ', "posts": [\n'
_POSTS_CLOSE = '\n], '
_META_PEEK = 8192

_root = None


def set_root(path):
    """Use *path* instead of the user cache directory (tests)."""
    global _root
    _root = path


def cache_root():
    return _root or os.path.join(GLib.get_user_cache_dir(), 'babble',
                                 'catch-up')


def safe_name(account_key):
    """*account_key* as one file name: percent-encoded, so two keys never
    collide and nothing in it can climb out of the directory."""
    return urllib.parse.quote(account_key or '', safe='') or '_'


def account_dir(account_key):
    return os.path.join(cache_root(), safe_name(account_key))


def new_id():
    return str(uuid.uuid4())


def snapshot_path(account_key, snapshot_id):
    # An id comes from a file name or from this module; keep it a name.
    return os.path.join(account_dir(account_key),
                        os.path.basename(snapshot_id) + '.json')


def _dump(snapshot):
    meta = {key: snapshot.get(key) for key in META_KEYS if key in snapshot}
    rest = {key: value for key, value in snapshot.items()
            if key not in meta and key != 'posts'}
    head = json.dumps(meta)[:-1]
    tail = json.dumps(rest)[1:]
    lines = ',\n'.join(json.dumps(post) for post in snapshot.get('posts', ()))
    return head + _POSTS_OPEN + lines + _POSTS_CLOSE + tail


def _private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    # makedirs leaves an existing directory alone, and applies the umask
    # to a new one.
    os.chmod(path, 0o700)


def write_snapshot(snapshot):
    """Save *snapshot* (a dict with ``account`` and ``id``) atomically and
    return its path. Raises OSError."""
    root = cache_root()
    directory = account_dir(snapshot['account'])
    os.makedirs(root, mode=0o700, exist_ok=True)
    _private_dir(root)
    _private_dir(directory)
    path = snapshot_path(snapshot['account'], snapshot['id'])
    temporary = '%s.%s.tmp' % (path, uuid.uuid4().hex)
    text = _dump(snapshot)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def _split(text):
    head, found, rest = text.partition(_POSTS_OPEN)
    if not found:
        raise ValueError('not a Catch Up snapshot')
    return head, rest


def read_meta(path):
    """The metadata of the snapshot at *path* (a dict), without its posts.
    Raises OSError or ValueError."""
    with open(path, encoding='utf-8') as handle:
        text = handle.read(_META_PEEK)
    head, found, _rest = text.partition(_POSTS_OPEN)
    if found:
        meta = json.loads(head + '}')
    else:
        # Written by something else, or the head outgrew the peek.
        meta = read_snapshot(path)
        meta.pop('posts', None)
    return meta


def read_snapshot(path):
    """The whole snapshot at *path*. Raises OSError or ValueError."""
    with open(path, encoding='utf-8') as handle:
        text = handle.read()
    try:
        head, rest = _split(text)
        body, found, tail = rest.rpartition(_POSTS_CLOSE)
        if not found:
            raise ValueError('truncated')
        snapshot = json.loads(head + '}')
        snapshot.update(json.loads('{' + tail))
        # One post at a time: other threads run between the calls.
        snapshot['posts'] = ([json.loads(line.rstrip(','))
                              for line in body.split('\n')]
                             if body else [])
    except ValueError:
        snapshot = json.loads(text)
    if not isinstance(snapshot, dict) \
            or not isinstance(snapshot.get('posts'), list) \
            or snapshot.get('version') != VERSION:
        raise ValueError('unsupported snapshot')
    return snapshot


def list_snapshots(account_key):
    """``[(path, meta)]`` for the account, newest ``end_at`` first. A file
    that cannot be read is skipped."""
    directory = account_dir(account_key)
    try:
        names = [name for name in os.listdir(directory)
                 if name.endswith('.json')]
    except OSError:
        return []
    found = []
    for name in names:
        path = os.path.join(directory, name)
        try:
            meta = read_meta(path)
        except (OSError, ValueError):
            continue
        if meta.get('version') != VERSION or not meta.get('id'):
            continue
        found.append((path, meta))
    found.sort(key=lambda entry: (entry[1].get('end_at') or '',
                                  entry[1]['id']), reverse=True)
    return found


def prune(account_key, keep=KEEP):
    """Delete all but the *keep* newest snapshots; returns the metas kept."""
    found = list_snapshots(account_key)
    for path, _meta in found[keep:]:
        try:
            os.unlink(path)
        except OSError:
            pass
    return [meta for _path, meta in found[:keep]]


def delete_snapshot(account_key, snapshot_id):
    try:
        os.unlink(snapshot_path(account_key, snapshot_id))
    except OSError:
        pass


def delete_account(account_key):
    """Remove every snapshot of the account (it was removed)."""
    shutil.rmtree(account_dir(account_key), ignore_errors=True)
