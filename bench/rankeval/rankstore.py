# SPDX-License-Identifier: GPL-3.0-or-later
"""Where a run is written: the SQLite schema, the inserts and the copies."""

import json
import os
import shutil
import sqlite3
import urllib.parse
from rankeval.shared import SYNTHETIC_INSTANCE, counts



# The schema version in `PRAGMA user_version`. Bump it when a table changes.
SCHEMA_VERSION = 1

def data_dir(instance, root=None, account_id=None):
    """The harness data directory for *instance*, and for one account on
    it when *account_id* is given.

    *root* is the ``babble-eval`` directory itself; tests pass a temporary
    one. By default it is ``${XDG_CACHE_HOME:-~/.cache}/babble-eval``,
    read here rather than at import time so a caller can point it
    elsewhere.

    **The account is part of the path.** H1 evaluates three accounts and
    aggregates three summaries, and one store cannot give three: the
    ``viewer_id`` is a single row, the runs of one account would be
    another's coverage, and the candidates would be the union of three
    feeds. So an account's data lives in its own directory.
    """
    if root is None:
        base = os.environ.get('XDG_CACHE_HOME') or os.path.join(
            os.path.expanduser('~'), '.cache')
        root = os.path.join(base, 'babble-eval')
    name = 'i-' + urllib.parse.quote(instance, safe='')
    if account_id:
        name += '-a-' + urllib.parse.quote(str(account_id), safe='')
    return os.path.join(root, name)

def sibling_accounts(instance, root=None):
    """The accounts this instance holds **runs** for.

    Each account's data is its own directory, so reading the wrong one
    finds nothing. This says which accounts there are, so the message can
    name them instead of only saying there are no runs.
    """
    parent = os.path.dirname(data_dir(instance, root))
    prefix = os.path.basename(data_dir(instance, root)) + '-a-'
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return []
    found = []
    for name in names:
        if not name.startswith(prefix):
            continue
        account = name[len(prefix):]
        path = os.path.join(parent, name, 'eval.sqlite3')
        if os.path.exists(path) and _run_count(path):
            found.append(account)
    return found

def _run_count(path):
    """How many runs a database holds, or 0 if it cannot be read."""
    try:
        connection = sqlite3.connect(path)
    except sqlite3.Error:
        return 0
    try:
        return connection.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        connection.close()

def database_path(instance, root=None, account_id=None):
    """The one SQLite file a run appends to."""
    return os.path.join(data_dir(instance, root, account_id), 'eval.sqlite3')

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)',
    'CREATE TABLE IF NOT EXISTS runs(ts TEXT PRIMARY KEY, requests INT,'
    ' partial INT, error TEXT)',
    'CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY,'
    ' first_seen TEXT, json TEXT)',
    'CREATE TABLE IF NOT EXISTS observations(run_ts TEXT, post_id TEXT,'
    ' favourites INT, reblogs INT, replies INT, favourited INT,'
    ' reblogged INT, bookmarked INT, PRIMARY KEY (run_ts, post_id))',
    'CREATE TABLE IF NOT EXISTS own(id TEXT PRIMARY KEY, first_seen TEXT,'
    ' json TEXT)',
    'CREATE TABLE IF NOT EXISTS favourites(id TEXT PRIMARY KEY,'
    ' first_seen TEXT, json TEXT)',
    'CREATE TABLE IF NOT EXISTS bookmarks(id TEXT PRIMARY KEY,'
    ' first_seen TEXT, json TEXT)',
    'CREATE TABLE IF NOT EXISTS relationships(account_id TEXT PRIMARY KEY,'
    ' following INT, followed_by INT, fetched TEXT)',
)

# The tables the schema creates, in the order the plan lists them.
TABLES = ('meta', 'runs', 'posts', 'observations', 'own', 'favourites',
          'bookmarks', 'relationships')

def create_schema(connection):
    """Create the tables once, under `PRAGMA user_version = 1`."""
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version:
        raise RuntimeError('eval.sqlite3 has schema version %d, this harness'
                           ' writes %d' % (version, SCHEMA_VERSION))
    for statement in SCHEMA:
        connection.execute(statement)
    connection.execute('PRAGMA user_version = %d' % SCHEMA_VERSION)

def open_database(path):
    """Open (and, the first time, create) the database at *path*.

    The file is made ``0600`` with `os.open` *before* the first
    `sqlite3.connect`, so SQLite never creates it with the umask's mode;
    its journal copies the database's mode.
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    # os.makedirs leaves an existing directory's mode alone, and os.open
    # does not chmod an existing file -- so a restored backup or a
    # directory made by an earlier version would keep whatever mode it
    # had. The plan says the data lives in a 0700 directory holding a
    # 0600 file, so both are set rather than only asked for.
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    handle = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.fchmod(handle, 0o600)
    except OSError:
        pass
    os.close(handle)
    connection = sqlite3.connect(path, isolation_level=None)
    create_schema(connection)
    return connection

def _insert_posts(connection, statuses, run_ts):
    """Store the first snapshot of every post.

    Returns ``(stored, new)``: how many distinct posts were written, and
    how many of them were seen for the first time.
    """
    new = 0
    stored = set()
    for status in statuses:
        if not isinstance(status, dict) or not status.get('id'):
            continue
        post_id = str(status['id'])
        cursor = connection.execute(
            'INSERT INTO posts(id, first_seen, json) VALUES(?, ?, ?)'
            ' ON CONFLICT(id) DO NOTHING',
            (post_id, run_ts, json.dumps(status)))
        new += max(cursor.rowcount, 0)
        stored.add(post_id)
        connection.execute(
            'INSERT INTO observations(run_ts, post_id, favourites, reblogs,'
            ' replies, favourited, reblogged, bookmarked)'
            ' VALUES(?, ?, ?, ?, ?, ?, ?, ?)'
            ' ON CONFLICT(run_ts, post_id) DO UPDATE SET'
            ' favourites=excluded.favourites, reblogs=excluded.reblogs,'
            ' replies=excluded.replies, favourited=excluded.favourited,'
            ' reblogged=excluded.reblogged, bookmarked=excluded.bookmarked',
            (run_ts, post_id) + counts(status))
    return len(stored), new

def _insert_list(connection, table, statuses, run_ts):
    """Store the first sighting of each listed status."""
    for status in statuses:
        if not isinstance(status, dict) or not status.get('id'):
            continue
        connection.execute(
            'INSERT INTO %s(id, first_seen, json) VALUES(?, ?, ?)'
            ' ON CONFLICT(id) DO NOTHING' % table,
            (str(status['id']), run_ts, json.dumps(status)))

def write_run(connection, gathered, run_ts, requests, instance):
    """Write one run, in one transaction. Returns ``(posts, new)``."""
    error = gathered.get('error') or ''
    connection.execute('BEGIN IMMEDIATE')
    try:
        stored_posts, new = _insert_posts(connection, gathered['home'], run_ts)
        _insert_list(connection, 'own', gathered['own'], run_ts)
        _insert_list(connection, 'favourites', gathered['favourites'], run_ts)
        _insert_list(connection, 'bookmarks', gathered['bookmarks'], run_ts)
        for relationship in gathered['relationships']:
            if not isinstance(relationship, dict) or not relationship.get('id'):
                continue
            connection.execute(
                'INSERT INTO relationships(account_id, following, followed_by,'
                ' fetched) VALUES(?, ?, ?, ?)'
                ' ON CONFLICT(account_id) DO UPDATE SET'
                ' following=excluded.following,'
                ' followed_by=excluded.followed_by, fetched=excluded.fetched',
                (str(relationship['id']),
                 int(bool(relationship.get('following'))),
                 int(bool(relationship.get('followed_by'))), run_ts))
        if gathered['viewer']:
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('viewer_id', ?)"
                ' ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (gathered['viewer'],))
        connection.execute(
            "INSERT INTO meta(key, value) VALUES('instance', ?)"
            ' ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (instance,))
        connection.execute(
            'INSERT INTO runs(ts, requests, partial, error) VALUES(?, ?, ?, ?)'
            ' ON CONFLICT(ts) DO UPDATE SET requests=excluded.requests,'
            ' partial=excluded.partial, error=excluded.error',
            (run_ts, requests, int(bool(error)), error))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    # The posts actually stored, not the entries the page carried: a
    # duplicate in one page is one post, and a page entry with no id is
    # none.
    return stored_posts, new

def stored_rows(root, sql):
    """Run one query against the database under *root*, read-only."""
    connection = sqlite3.connect(database_path(SYNTHETIC_INSTANCE, root))
    try:
        return connection.execute(sql).fetchone()
    finally:
        connection.close()

def copy_database(source, destination):
    """Copy the harness database -- and only that -- to *destination*."""
    target = database_path(SYNTHETIC_INSTANCE, destination)
    os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
    shutil.copyfile(database_path(SYNTHETIC_INSTANCE, source), target)
    os.chmod(target, 0o600)

def write_json(path, value):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(value, handle)
    return path
