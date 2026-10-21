# SPDX-License-Identifier: GPL-3.0-or-later
"""The ranked timeline's evaluation harness.

`collect` records the reader's own feed on the reader's own machine, so the
ranker can later be judged on real posts without any of them leaving it.
Nothing here is installed; it runs from the source tree.

One run reads the instance through Babble's own
`src.mastodon.MastodonClient` -- the same client, and so the same JSON, the
app ranks -- and appends:

* the whole home timeline, `limit=40`, paged with the `Link` header's next
  parameters until a page is empty or 20 pages are done (Mastodon keeps at
  most 800 entries);
* the first page of the reader's own statuses (boosts and replies carry
  exact times, which the flags do not);
* the first page of favourites;
* the first page of bookmarks;
* the relationships of the authors, boosters and reply targets not stored
  yet, 40 ids per request.

The token comes from one of two places:

* ``BABBLE_EVAL_TOKEN``, when it is set;
* otherwise, with ``--account-id ID``, from the keyring entry Babble wrote
  for that account, read with ``mastodon.load_secret_async(instance, ID,
  callback)``. The routine runs on the main loop, so a `GLib.MainLoop`
  runs until it answers. That route only finds entries an uninstalled
  (host) Babble wrote: a Flatpak install keeps its secrets inside the
  sandbox, where this script cannot reach them. A volunteer using the
  Flatpak therefore creates a token by hand --
  Preferences -> Development -> New application, with only the ``read``
  scope -- and puts it in ``BABBLE_EVAL_TOKEN``.

Neither a post's text nor the token is ever printed. A run prints one line::

    <ts> posts=<n> new=<n> requests=<n> partial=<bool>

The data lives in
``${XDG_CACHE_HOME:-~/.cache}/babble-eval/i-<quoted instance>/eval.sqlite3``:
a ``0700`` directory holding a ``0600`` file, one snapshot of every post's
counters and flags per run. The ``i-`` prefix keeps ``.`` and ``..`` out of
the path, since ``urllib.parse.quote`` leaves ``.`` alone.
"""

import argparse
import datetime
import itertools
import json
import os
import sqlite3
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# `python3 -I` drops the environment and BABBLE_SRCDIR (src/meson.build:148)
# names src/, not the repository root, so the root is found from __file__.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import mastodon  # noqa: E402

from gi.repository import GLib  # noqa: E402

# The schema version in `PRAGMA user_version`. Bump it when a table changes.
SCHEMA_VERSION = 1

# Mastodon's per-request maximum and its cap on a home timeline.
PAGE_LIMIT = 40
HOME_MAX_PAGES = 20

# /accounts/relationships accepts at most this many ids per request.
RELATIONSHIP_BATCH = 40

TOKEN_VARIABLE = 'BABBLE_EVAL_TOKEN'

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


def data_dir(instance, root=None):
    """The harness data directory for *instance*.

    *root* is the ``babble-eval`` directory itself; tests pass a temporary
    one. By default it is ``${XDG_CACHE_HOME:-~/.cache}/babble-eval``,
    read here rather than at import time so a caller can point it
    elsewhere.
    """
    if root is None:
        base = os.environ.get('XDG_CACHE_HOME') or os.path.join(
            os.path.expanduser('~'), '.cache')
        root = os.path.join(base, 'babble-eval')
    return os.path.join(root, 'i-' + urllib.parse.quote(instance, safe=''))


def database_path(instance, root=None):
    """The one SQLite file a run appends to."""
    return os.path.join(data_dir(instance, root), 'eval.sqlite3')


def make_client(instance, token):
    """The real client. `collect` takes this factory as a parameter, so a
    test hands in a duck-typed fake and never constructs a Soup.Session."""
    return mastodon.MastodonClient(instance, token=token)


def call(client, path, params=None):
    """GET *path* and return ``(data, next_params)``.

    The client answers on the main loop, so a `GLib.MainLoop` runs until
    the callback fires, and the first two items of `request_finish_paged`'s
    three are what the caller reads. List parameters go as
    ``{'id[]': [...]}`` (catchup.py:699's form); ``urlencode(doseq=True)``
    in the client expands them.
    """
    loop = GLib.MainLoop()
    answer = {}

    def finished(_client, result, _data=None):
        try:
            page, next_params, _previous = client.request_finish_paged(result)
        except GLib.Error as error:
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

    mastodon.load_secret_async(instance, account_id, done)
    if not answer:
        loop.run()
    if answer.get('error') is not None:
        raise answer['error']
    return answer.get('value')


class Fetcher:
    """One run's requests: counts them and stops on a 429 or a failure."""

    def __init__(self, client):
        self.client = client
        self.requests = 0

    def get(self, path, params=None):
        if self.client.rate_limited():
            after = int(self.client.retry_after_seconds())
            raise Stopped('rate limited' +
                          ('; retry after %ds' % after if after else ''))
        self.requests += 1
        try:
            return call(self.client, path, params)
        except GLib.Error as error:
            raise Stopped(error.message or path) from error


def status_accounts(status):
    """The account ids a status names: its author, the boosted author, the
    reply target."""
    found = []
    if not isinstance(status, dict):
        return found
    for field in ('account', 'reblog'):
        account = status.get(field)
        if isinstance(account, dict) and account.get('id'):
            found.append(str(account['id']))
    reply_to = status.get('in_reply_to_account_id')
    if reply_to:
        found.append(str(reply_to))
    return found


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


def gather(client, known_accounts=()):
    """Everything one run reads, or as much of it as it could read.

    Steps run in the plan's order. A rate limit or a failed request raises
    `Stopped` out of `Fetcher.get`; what was gathered before it stays in
    the returned dictionary, and `error` names why the run ended early.
    """
    fetched = Fetcher(client)
    gathered = {'viewer': None, 'home': [], 'own': [], 'favourites': [],
                'bookmarks': [], 'relationships': [], 'error': ''}

    try:
        # ``get`` returns ``(data, next_params)``, so this is the first
        # element of the account list -- the account itself.
        viewer = fetched.get('/accounts/verify_credentials')[0][0]
        if not isinstance(viewer, dict) or not viewer.get('id'):
            raise Stopped('verify_credentials returned no account')
        gathered['viewer'] = str(viewer['id'])

        params = {'limit': str(PAGE_LIMIT)}
        for _page in range(HOME_MAX_PAGES):
            page, next_params = fetched.get('/timelines/home', params)
            if not isinstance(page, list) or not page:
                break
            gathered['home'].extend(page)
            if not next_params:
                break
            params = dict(next_params)

        gathered['own'] = _page_of(fetched, '/accounts/%s/statuses'
                                   % gathered['viewer'])
        gathered['favourites'] = _page_of(fetched, '/favourites')
        gathered['bookmarks'] = _page_of(fetched, '/bookmarks')

        unknown = []
        seen = set(known_accounts)
        for table in ('home', 'own', 'favourites', 'bookmarks'):
            for status in gathered[table]:
                for account_id in status_accounts(status):
                    if account_id not in seen:
                        seen.add(account_id)
                        unknown.append(account_id)
        for batch in itertools.batched(unknown, RELATIONSHIP_BATCH):
            gathered['relationships'].extend(_page_of(
                fetched, '/accounts/relationships', {'id[]': list(batch)}))
    except Stopped as stopped:
        gathered['error'] = str(stopped)

    return gathered, fetched.requests


def _page_of(fetched, path, params=None):
    """The first page of *path*, as a list."""
    page, _next = fetched.get(path, params or {'limit': str(PAGE_LIMIT)})
    return page if isinstance(page, list) else []


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
    handle = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(handle)
    connection = sqlite3.connect(path, isolation_level=None)
    create_schema(connection)
    return connection


def _insert_posts(connection, statuses, run_ts):
    """Store the first snapshot of every post; return how many are new."""
    new = 0
    for status in statuses:
        if not isinstance(status, dict) or not status.get('id'):
            continue
        post_id = str(status['id'])
        cursor = connection.execute(
            'INSERT INTO posts(id, first_seen, json) VALUES(?, ?, ?)'
            ' ON CONFLICT(id) DO NOTHING',
            (post_id, run_ts, json.dumps(status)))
        new += max(cursor.rowcount, 0)
        connection.execute(
            'INSERT INTO observations(run_ts, post_id, favourites, reblogs,'
            ' replies, favourited, reblogged, bookmarked)'
            ' VALUES(?, ?, ?, ?, ?, ?, ?, ?)'
            ' ON CONFLICT(run_ts, post_id) DO UPDATE SET'
            ' favourites=excluded.favourites, reblogs=excluded.reblogs,'
            ' replies=excluded.replies, favourited=excluded.favourited,'
            ' reblogged=excluded.reblogged, bookmarked=excluded.bookmarked',
            (run_ts, post_id) + counts(status))
    return new


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
        new = _insert_posts(connection, gathered['home'], run_ts)
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
    return len(gathered['home']), new


def collect(instance, make_client=make_client, account_id=None, token=None,
            now=None, root=None, out=None):
    """One collection run. Returns the process exit code.

    The token is *token* if given, else ``BABBLE_EVAL_TOKEN``, else the
    keyring entry for *account_id*. With none of them, the run stops with
    a message and exit code 2, before touching the database.
    """
    out = out or sys.stdout
    try:
        instance_url = mastodon.normalize_instance(instance)
    except ValueError as error:
        print('rank_eval: %s' % error, file=sys.stderr)
        return 2

    if not token:
        token = os.environ.get(TOKEN_VARIABLE) or None
    if not token and account_id:
        try:
            token = read_secret(instance_url, account_id)
        except GLib.Error as error:
            print('rank_eval: cannot read the keyring: %s' % error.message,
                  file=sys.stderr)
            return 2
        if not token:
            print('rank_eval: no keyring entry for account %s on %s'
                  % (account_id, instance_url), file=sys.stderr)
            return 2
    if not token:
        print('rank_eval: no token: set %s, or pass --account-id to read'
              ' the keyring entry Babble stored' % TOKEN_VARIABLE,
              file=sys.stderr)
        return 2

    run_ts = iso_utc(now)
    connection = open_database(database_path(instance_url, root))
    try:
        known = {row[0] for row in
                 connection.execute('SELECT account_id FROM relationships')}
        gathered, requests = gather(make_client(instance_url, token), known)
        posts, new = write_run(connection, gathered, run_ts, requests,
                               instance_url)
    finally:
        connection.close()

    print('%s posts=%d new=%d requests=%d partial=%s'
          % (run_ts, posts, new, requests, bool(gathered['error'])), file=out)
    return 0


def build_parser():
    """The command line. The five commands later tasks fill in still parse
    and answer, so a script can name them without a traceback."""
    parser = argparse.ArgumentParser(
        prog='rank_eval.py',
        description='Record and judge a Mastodon home timeline.')
    commands = parser.add_subparsers(dest='command', required=True)

    collect_command = commands.add_parser(
        'collect', help='append one snapshot of the home timeline')
    collect_command.add_argument(
        '--instance', required=True,
        help='the instance, e.g. https://mastodon.social')
    collect_command.add_argument(
        '--account-id',
        help='read the token Babble stored for this account from the'
             ' keyring (host installs only; a Flatpak keeps its secrets'
             ' inside the sandbox)')
    for name in ('evaluate', 'aggregate', 'credits', 'self-test', 'bench'):
        commands.add_parser(name, help='not implemented yet')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command != 'collect':
        print('%s is not implemented yet' % args.command, file=sys.stderr)
        return 2
    return collect(args.instance, account_id=args.account_id)


if __name__ == '__main__':
    sys.exit(main())
