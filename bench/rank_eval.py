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

`token`, `follow`, `engage` and `inspect` are the harness's half of the
managed accounts (D2). The maintainer creates three accounts by hand and
hands over their tokens; `token` verifies one against
``/accounts/verify_credentials`` and stores it in the keyring under the id
the server names, where ``collect --account-id`` reads it. It never prints
the token, on the success path or the failure path. `follow` follows
accounts for one managed account and reports the resulting follow count:
the candidates come from another account's following list or from ids
given on the command line, accounts already followed are skipped, and
``--limit`` plus ``--pause`` build an account over several runs without
tripping the rate limit. `engage` produces one of the four engagements
T4.2 credits -- ``favourite``, ``reblog``, ``bookmark``, ``reply`` -- and
`inspect` reports, per account, its follow counts and how many home
timeline posts it can see, which is the first thing to check before the
14-day run. All four fail with a line naming what was refused, never a
traceback.

`evaluate` reads that database back and judges it, one local day at a time
at 06:00 local (D3). A day is judged only when its 48-hour window was
sampled at most two hours apart; the others are reported ``no-coverage``
and left out of every count. For each judged day it builds the feed the
reader had at T -- posts created in [T - 24 h, T), not engaged by then,
their counters from the last observation at or before T -- labels the
posts first seen engaged in (T, T + 24 h], and compares five orderings of
the same feed: chronological, `catchuprank.rank()` without and with
affinity, engagement-only, and affinity-then-chronological. It prints
recall@20, recall@50, distinct authors, small-account share and overlap
per ordering, then the four gate verdicts and the H1 record. With
``--json`` it writes the same numbers, and no post, id or handle, to a
summary another machine can share.

`aggregate` folds those summaries into the H1 record: Gate A passes when
at least ``min_accounts_passing`` accounts pass it, and the ordering and
the affinity decision are the accounts' majority, ties falling back to
``rank`` without affinity. Both commands read
``bench/rank_eval_gates.json`` unless ``--gates`` names another file. With
no gates file they print the metrics and refuse to print a verdict: the
thresholds were frozen before the data came in (H0), so they cannot be
chosen after it.

`self-test` checks all of that against a world whose answer is known, in
a temporary directory and with no network: five days of hourly runs, more
candidates a day than the top 20 holds, and labels planted on one author's
posts after each day's evaluation time. It asserts recall@k on an ordering
worked out by hand, the temporal split (and, in a second database that
differs by one row, that reading past the day *would* change the top 20),
the whole-future rewrite twice over -- once from a chosen day's
evaluation time, and once from an hour into the compared day's own
future, so a bound one hour too far cannot hide behind the boundary --
the sign test's two anchors, the gate verdicts on a fixture gates file
(each of the four on the fixture where its own condition decides), the
summary's privacy in keys and in values, candidate counters that move,
a day with a hole in its coverage, and `collect` through a duck-typed
fake client that answers with the real endpoints' shapes.
`src.mastodon.MastodonClient` is replaced by a class whose ``__init__``
raises, so a run that reached for the real client fails here: patching
`socket.socket` would prove nothing, because libsoup does not use
Python's sockets.

`bench` reproduces the cost figure in `Docs/timeline-algorithm.md` §6.1:
`catchuprank.rank()` on 400 and on 800 synthetic posts, three runs each,
every run printed.
"""

import argparse
import contextlib
import datetime
import getpass
import io
import itertools
import json
import math
import os
import random
import shutil
import sqlite3
import stat
import statistics
import sys
import tempfile
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# `python3 -I` drops the environment and BABBLE_SRCDIR (src/meson.build:148)
# names src/, not the repository root, so the root is found from __file__.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import affinity, catchuprank, mastodon  # noqa: E402

from gi.repository import GLib  # noqa: E402

# The schema version in `PRAGMA user_version`. Bump it when a table changes.
SCHEMA_VERSION = 1

# Mastodon's per-request maximum and its cap on a home timeline.
PAGE_LIMIT = 40
HOME_MAX_PAGES = 20

# /accounts/relationships accepts at most this many ids per request.
RELATIONSHIP_BATCH = 40

TOKEN_VARIABLE = 'BABBLE_EVAL_TOKEN'

# D3: a day is evaluated at this hour, local time, for every account.
EVALUATION_HOUR = 6

DAY = datetime.timedelta(days=1)
HOUR = datetime.timedelta(hours=1)

# A day is covered when no two consecutive runs are further apart than
# this, in seconds.
COVERAGE_GAP = 2 * 60 * 60

# The same gap, with the tolerance that ``julianday``'s resolution
# needs. A gap of exactly two hours comes back as 7200.000013 or
# 7199.999973 depending on the date, and the plan allows a two-hour
# gap -- so the comparison needs a margin, or the same sampling pattern
# is covered on one day and not on the next.
GAP_TOLERANCE = 1e-3

# The orderings every day compares, in report order (plan item 5), and
# the metrics each day records (plan item 6).
ORDERINGS = ('chrono', 'rank_null', 'rank_affinity', 'engagement',
             'affinity_chrono')
METRICS = ('recall20', 'recall50', 'authors20', 'small20', 'overlap20')

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


def make_client(instance, token):
    """The real client. `collect` takes this factory as a parameter, so a
    test hands in a duck-typed fake and never constructs a Soup.Session."""
    return mastodon.MastodonClient(instance, token=token)


# The real class, held here because `self_test` swaps the module attribute
# for `_NoNetwork` (that swap is what fails a run which reaches for the
# network). A check that pins the write methods' verbs and paths calls
# them unbound on this one, so it exercises the shipped methods rather
# than the stub.
REAL_CLIENT = mastodon.MastodonClient


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
        except Exception as error:
            # A failure that is not an HTTP error is still a failure of
            # this run: stop, keep what was gathered, and record why.
            raise Stopped(str(error) or path) from error


def status_accounts(status):
    """The account ids a status names: its author, the boosted author, the
    reply target.

    A boost names two accounts: the booster, in the wrapper's ``account``,
    and the boosted author, in the *inner* post's ``account``. The inner
    post's own ``id`` is a post id, not an account id, and asking
    ``/accounts/relationships`` about it wastes a request and leaves the
    boosted author's relationship unknown -- which is the author the
    affinity maps credit.
    """
    found = []
    if not isinstance(status, dict):
        return found
    account = status.get('account')
    if isinstance(account, dict) and account.get('id'):
        found.append(str(account['id']))
    reblog = status.get('reblog')
    if isinstance(reblog, dict):
        inner = reblog.get('account')
        if isinstance(inner, dict) and inner.get('id'):
            found.append(str(inner['id']))
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


def gather(client, known_accounts=(), viewer=None):
    """Everything one run reads, or as much of it as it could read.

    Steps run in the plan's order. A rate limit or a failed request raises
    `Stopped` out of `Fetcher.get`; what was gathered before it stays in
    the returned dictionary, and `error` names why the run ended early.

    *viewer* is the account id, when the caller already asked the server
    and does not want the request made twice.
    """
    fetched = Fetcher(client)
    gathered = {'viewer': None, 'home': [], 'own': [], 'favourites': [],
                'bookmarks': [], 'relationships': [], 'error': ''}

    try:
        # ``get`` returns ``(data, next_params)``, and this endpoint's
        # body is an object -- the account itself, which ``lift`` makes a
        # dict subclass. So this is the account, not the first element of
        # a list: the app reads it the same way
        # (``account = client.verify_credentials_finish(result)`` then
        # ``account['instance'] = ...``, auth.py:535).
        if viewer:
            gathered['viewer'] = str(viewer)
        else:
            viewer = fetched.get('/accounts/verify_credentials')[0]
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


def collect(instance, make_client=make_client, account_id=None, token=None,
            now=None, root=None, out=None):
    """One collection run. Returns the process exit code.

    The token is *token* if given, else ``BABBLE_EVAL_TOKEN``, else the
    keyring entry for *account_id*. With none of them, the run stops with
    a message and exit code 2, before touching the database.

    **The store is keyed by the account the token belongs to, not by the
    one that was asked for.** The run verifies the token and reads the id
    the server names; a store named for one account and holding another's
    feed would file two accounts' summaries as one account's, and
    ``aggregate`` cannot tell. So a given *account_id* that the server
    disagrees with stops the run, and an absent one is filled in from
    the server's answer.
    """
    out = out or sys.stdout
    try:
        instance_url = mastodon.normalize_instance(instance)
    except ValueError as error:
        print('rank_eval: %s' % error, file=sys.stderr)
        return 2

    try:
        token = token_for(instance_url, account_id, token)
    except LookupError as error:
        print('rank_eval: %s' % error, file=sys.stderr)
        return 2

    run_ts = iso_utc(now)
    client = make_client(instance_url, token)
    # The account the token belongs to, from the server itself. One
    # request, and the rest of the run reuses it.
    try:
        account = invoke(client, 'verify_credentials')
        viewer = str(account['id']) if account and account.get('id') else None
    except Exception:
        viewer = None
    if viewer and account_id and str(account_id) != viewer:
        print('rank_eval: the token belongs to account %s, not %s;'
              ' nothing was collected' % (viewer, account_id),
              file=sys.stderr)
        return 2
    account_id = viewer or account_id

    connection = open_database(
        database_path(instance_url, root, account_id))
    try:
        known = {row[0] for row in
                 connection.execute('SELECT account_id FROM relationships')}
        gathered, requests = gather(client, known, viewer)
        posts, new = write_run(connection, gathered, run_ts, requests,
                               instance_url)
    finally:
        connection.close()

    print('%s posts=%d new=%d requests=%d partial=%s'
          % (run_ts, posts, new, requests, bool(gathered['error'])), file=out)
    return 0


# region managed accounts
#
# D2's three accounts are created by hand; the harness is handed their
# tokens. These are the harness's half: putting a token where `collect`
# looks for one, following accounts so a home timeline has something in
# it, producing the engagement the labels come from, and reporting the
# counts that say whether an account is worth collecting.
#
# Every server call goes through Babble's own client -- reads through
# `call`, writes through the client's named methods -- so the tree keeps
# one HTTP path, not two.

# The kinds of engagement T4.2 credits, and the ones `engage` produces.
ENGAGEMENTS = ('favourite', 'reblog', 'bookmark', 'reply')

# /accounts/:id/following caps a page at 80, and an account may follow far
# more than the home timeline's 800; 50 pages reach 4,000 candidates,
# enough for D2's over-1,000 account.
FOLLOW_PAGE_LIMIT = 80
FOLLOW_MAX_PAGES = 50


def http_status(error):
    """The HTTP status a client ``GLib.Error`` carries, or None.

    The client builds every response failure with the HTTP status as the
    error's code, so a caller can tell a refused token (401) from a
    refused write (403) without reading the message. PyGObject hands the
    domain back as its name, not its quark.
    """
    if isinstance(error, GLib.Error) \
            and error.domain == 'mastodon-http-error-quark':
        return error.code
    return None


def refused(what, error):
    """*what* refused, in one line: the status, and the server's reason."""
    status = http_status(error)
    reason = mastodon.summarize_error(error) or 'no reason given'
    return '%s refused%s: %s' % (what, ' (%d)' % status if status else '',
                                 reason)


def invoke(client, action, *args, **params):
    """Run one client ``<action>_async`` and return ``<action>_finish``'s.

    The client answers on the main loop, so a `GLib.MainLoop` runs until
    the callback fires -- and not when a fake answered before returning,
    the same allowance `call` makes. A failure is raised, which is what
    every ``_finish`` does.
    """
    loop = GLib.MainLoop()
    answer = {}

    def finished(_client, result, _data=None):
        try:
            answer['value'] = getattr(client, action + '_finish')(result)
        except Exception as error:
            answer['error'] = error
        finally:
            loop.quit()

    getattr(client, action + '_async')(*args, callback=finished, **params)
    if not answer:
        loop.run()
    if 'error' in answer:
        raise answer['error']
    return answer['value']


def write_secret(instance, account_id, token):
    """Store *token* in the keyring under *account_id*, waited for.

    `store_secret_async` calls back with None or the GLib.Error that
    stopped it, and the loop runs until it answers -- what `read_secret`
    does in the other direction. The self-test passes its own writer, so
    neither this nor `token` needs a secret service to be exercised.
    """
    loop = GLib.MainLoop()
    answer = {}

    def done(error=None):
        answer['error'] = error
        loop.quit()

    mastodon.store_secret_async(instance, account_id, token, done)
    if not answer:
        loop.run()
    if answer.get('error') is not None:
        raise answer['error']


def token_for(instance_url, account_id=None, token=None, reader=read_secret):
    """The token for one account, or a LookupError saying where it is not.

    *token* wins, then ``BABBLE_EVAL_TOKEN``, then the keyring entry for
    *account_id*. *reader* is `read_secret`, injected by the self-test so
    no secret service is needed.
    """
    if not token:
        token = os.environ.get(TOKEN_VARIABLE) or None
    if not token and account_id:
        try:
            token = reader(instance_url, account_id)
        except GLib.Error as error:
            raise LookupError('cannot read the keyring: %s'
                              % mastodon.summarize_error(error)) from error
        if not token:
            raise LookupError('no keyring entry for account %s on %s'
                              % (account_id, instance_url))
    if not token:
        raise LookupError('no token: set %s, or pass --account-id to read'
                          ' the keyring entry Babble stored'
                          % TOKEN_VARIABLE)
    return token


def read_token_line(stream=None):
    """One line from stdin, without echoing when a terminal can hide it."""
    if sys.stdin.isatty():
        try:
            return getpass.getpass('Token: ').strip()
        except (EOFError, KeyboardInterrupt):
            return ''
    return (stream or sys.stdin).readline().strip()


def store_token(instance, account_id=None, token=None,
                make_client=make_client, store_secret=None, out=None):
    """Verify *token* and store it where `collect --account-id` reads it.

    Returns the process exit code. The token is never printed, on either
    path: the success line names the account and the instance, and a
    failure names what the server or the keyring refused.
    """
    out = out or sys.stdout
    store_secret = store_secret or write_secret
    if not token:
        print('rank_eval: no token to store: pass --token, or pipe one in',
              file=sys.stderr)
        return 2
    try:
        instance_url = mastodon.normalize_instance(instance)
    except ValueError as error:
        print('rank_eval: %s' % error, file=sys.stderr)
        return 2
    try:
        account = invoke(make_client(instance_url, token),
                         'verify_credentials')
    except Exception as error:
        print('rank_eval: %s' % refused('token', error), file=sys.stderr)
        return 2
    if not isinstance(account, dict) or not account.get('id'):
        print('rank_eval: verify_credentials answered with no account, so'
              ' the token was not stored', file=sys.stderr)
        return 2
    verified = str(account['id'])
    if account_id and account_id != verified:
        # Stored under the id the server names, the entry `collect
        # --account-id` will look for; a different id would be an entry
        # nothing reads.
        print('rank_eval: the token belongs to account %s, not %s; nothing'
              ' was stored' % (verified, account_id), file=sys.stderr)
        return 2
    try:
        store_secret(instance_url, verified, token)
    except GLib.Error as error:
        print('rank_eval: cannot write the keyring: %s'
              % mastodon.summarize_error(error), file=sys.stderr)
        return 2
    print('token stored for account %s on %s' % (verified, instance_url),
          file=out)
    return 0


def engage(instance, target, kind, text=None, visibility=None, token=None,
           account_id=None, make_client=make_client, reader=read_secret,
           out=None):
    """Produce one ``favourite``, ``reblog``, ``bookmark`` or ``reply`` on
    *target*. Returns the process exit code.

    2 means the command could not start -- a bad instance, no token, a
    keyring that would not answer; 1 means the server refused the write,
    and the line says which write and why. Neither path prints the token.
    """
    out = out or sys.stdout
    if kind not in ENGAGEMENTS:
        print('rank_eval: unknown engagement %s' % kind, file=sys.stderr)
        return 2
    if kind == 'reply' and not text:
        print('rank_eval: a reply needs --text', file=sys.stderr)
        return 2
    try:
        instance_url = mastodon.normalize_instance(instance)
    except ValueError as error:
        print('rank_eval: %s' % error, file=sys.stderr)
        return 2
    try:
        client = make_client(
            instance_url, token_for(instance_url, account_id, token, reader))
    except LookupError as error:
        print('rank_eval: %s' % error, file=sys.stderr)
        return 2
    try:
        viewer = invoke(client, 'verify_credentials')
    except Exception as error:
        print('rank_eval: %s' % refused('token', error), file=sys.stderr)
        return 2
    if not isinstance(viewer, dict) or not viewer.get('id'):
        print('rank_eval: verify_credentials answered with no account',
              file=sys.stderr)
        return 2
    viewer_id = str(viewer['id'])
    try:
        if kind == 'reply':
            params = {'visibility': visibility} if visibility else None
            status = invoke(client, 'reply', str(target), text, params)
        elif kind == 'reblog' and visibility:
            status = invoke(client, 'reblog', str(target),
                            visibility=visibility)
        else:
            status = invoke(client, kind, str(target))
    except Exception as error:
        print('rank_eval: %s' % refused(kind, error), file=sys.stderr)
        return 1
    created = status.get('id') if isinstance(status, dict) else None
    print('%s %s target=%s account=%s status=%s'
          % (iso_utc(), kind, target, viewer_id, created or '-'), file=out)
    return 0


def already_following(client, account_ids):
    """The subset of *account_ids* the viewer follows or has asked to.

    A locked account answers a follow with ``requested: true`` and
    ``following: false``; treating only the second as done would send the
    same request again on every resumed run, which for D2's over-1,000
    account is a thousand wasted requests.
    """
    following = set()
    for batch in itertools.batched(account_ids, RELATIONSHIP_BATCH):
        relationships = call(client, '/accounts/relationships',
                             {'id[]': list(batch)})[0]
        for relationship in relationships if isinstance(
                relationships, list) else ():
            if not isinstance(relationship, dict):
                continue
            if relationship.get('following') or relationship.get('requested'):
                following.add(str(relationship.get('id')))
    return following


def follow(instance, from_account=None, accounts=(), limit=0, pause=0.0,
           token=None, account_id=None, make_client=make_client,
           reader=read_secret, sleep=time.sleep, out=None):
    """Follow accounts for one managed account, then report its counts.

    Candidates come from *from_account* (the accounts it follows) or from
    *accounts*. *limit* caps how many are followed in this run, which is
    how an over-1,000 account is built without tripping the rate limit in
    one go; accounts already followed are skipped, so running the command
    again continues where the last one stopped. *pause* seconds sleep
    between follows.

    Returns 0 when every follow was accepted, 1 when the server refused
    one or rate limiting stopped the run (the accepted follows stay), and
    2 when the command could not start. A locked account answers with a
    follow *request* rather than a follow; that is counted as
    ``requested``, not as followed, because the following count does not
    move until it is accepted.
    """
    out = out or sys.stdout
    try:
        instance_url = mastodon.normalize_instance(instance)
    except ValueError as error:
        print('rank_eval: %s' % error, file=sys.stderr)
        return 2
    if not from_account and not accounts:
        print('rank_eval: name candidates: --from-account ID, or --account ID'
              ' (repeatable)', file=sys.stderr)
        return 2
    try:
        client = make_client(
            instance_url, token_for(instance_url, account_id, token, reader))
    except LookupError as error:
        print('rank_eval: %s' % error, file=sys.stderr)
        return 2
    try:
        viewer = invoke(client, 'verify_credentials')
    except Exception as error:
        print('rank_eval: %s' % refused('token', error), file=sys.stderr)
        return 2
    if not isinstance(viewer, dict) or not viewer.get('id'):
        print('rank_eval: verify_credentials answered with no account',
              file=sys.stderr)
        return 2
    viewer_id = str(viewer['id'])

    followed = 0
    requested = 0
    skipped = 0
    refused_follows = 0
    failures = []

    def take(batch):
        """Follow the accounts in *batch* not followed yet. False stops
        the caller: the limit was reached, or the server refused or rate
        limited the run."""
        nonlocal followed, requested, skipped, refused_follows
        ids = [account for account in batch if account != viewer_id]
        if not ids:
            return True
        try:
            known = already_following(client, ids)
        except Exception as error:
            failures.append(refused('relationship lookup', error))
            return False
        for candidate in ids:
            if candidate in known:
                skipped += 1
                continue
            if limit and followed + requested >= limit:
                return False
            if client.rate_limited():
                failures.append('rate limited after %d follows; run again to'
                                ' continue' % (followed + requested))
                return False
            try:
                relationship = invoke(client, 'follow', candidate)
            except Exception as error:
                refused_follows += 1
                failures.append(
                    refused('follow of account %s' % candidate, error))
                continue
            if isinstance(relationship, dict) \
                    and relationship.get('following'):
                followed += 1
            else:
                requested += 1
            if pause:
                sleep(pause)
        return True

    if from_account:
        params = {'limit': str(FOLLOW_PAGE_LIMIT)}
        for _page in range(FOLLOW_MAX_PAGES):
            try:
                page, next_params = call(
                    client, '/accounts/%s/following' % from_account, params)
            except Exception as error:
                failures.append(refused(
                    'the following list of account %s' % from_account, error))
                break
            if not isinstance(page, list) or not page:
                break
            if not take([str(account['id']) for account in page
                         if isinstance(account, dict) and account.get('id')]):
                break
            if not next_params:
                break
            params = dict(next_params)
    else:
        take([str(account) for account in accounts])

    try:
        account = invoke(client, 'verify_credentials')
    except Exception as error:
        failures.append(refused('the follow counts', error))
        account = {}
    following_count = account.get('following_count', '-') \
        if isinstance(account, dict) else '-'
    try:
        home, next_params = call(client, '/timelines/home',
                                 {'limit': str(PAGE_LIMIT)})
    except Exception as error:
        failures.append(refused('the home timeline', error))
        home, next_params = [], None
    for line in failures:
        print('rank_eval: %s' % line, file=sys.stderr)
    print('%s account=%s following=%s followed=%d requested=%d already=%d'
          ' refused=%d home=%d older=%s'
          % (iso_utc(), viewer_id, following_count, followed, requested,
             skipped, refused_follows,
             len(home) if isinstance(home, list) else 0,
             bool(next_params)), file=out)
    return 1 if failures else 0


def inspect(instance, account_ids, token=None, make_client=make_client,
            reader=read_secret, out=None):
    """One line per managed account: its counts, and whether its home
    timeline has posts.

    Returns 0 when every account answered, 1 when one did not. ``home=``
    is how many posts the account's home timeline returned on its first
    page, and ``older=True`` says there are more: this is the command that
    answers whether a managed account has anything to rank, before the
    14-day run rather than after it.
    """
    out = out or sys.stdout
    try:
        instance_url = mastodon.normalize_instance(instance)
    except ValueError as error:
        print('rank_eval: %s' % error, file=sys.stderr)
        return 2
    if token and len(account_ids) != 1:
        print('rank_eval: --token names one account; pass one --account-id',
              file=sys.stderr)
        return 2
    failed = False
    for account_id in account_ids:
        try:
            client = make_client(instance_url, token_for(
                instance_url, account_id, token, reader))
        except LookupError as error:
            print('rank_eval: %s' % error, file=sys.stderr)
            failed = True
            continue
        try:
            account = invoke(client, 'verify_credentials')
        except Exception as error:
            print('rank_eval: %s' % refused('account %s' % account_id, error),
                  file=sys.stderr)
            failed = True
            continue
        try:
            home, next_params = call(client, '/timelines/home',
                                     {'limit': str(PAGE_LIMIT)})
        except Exception as error:
            print('rank_eval: %s'
                  % refused('the home timeline of account %s' % account_id,
                            error),
                  file=sys.stderr)
            failed = True
            continue
        if not isinstance(account, dict) or not account.get('id'):
            print('rank_eval: account %s: verify_credentials answered with no'
                  ' account' % account_id, file=sys.stderr)
            failed = True
            continue
        print('%s account=%s acct=%s following=%s followers=%s statuses=%s'
              ' home=%d older=%s'
              % (iso_utc(), account['id'], account.get('acct', '-'),
                 account.get('following_count', '-'),
                 account.get('followers_count', '-'),
                 account.get('statuses_count', '-'),
                 len(home) if isinstance(home, list) else 0,
                 bool(next_params)), file=out)
    return 1 if failed else 0


# endregion


def parse_moment(value):
    """An ISO-8601 stamp as an aware datetime, or None.

    Both timestamp families go through here before they are compared: a
    ``created_at`` has second precision and a ``run_ts`` microseconds, so
    their text order is not their time order. Only a ``run_ts`` is
    compared as text, and only with another ``run_ts``.
    """
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return (moment if moment.tzinfo
            else moment.replace(tzinfo=datetime.timezone.utc))


def day_moment(day):
    """*day* at the D3 hour, as UTC.

    The datetime is built naive, so ``astimezone`` reads it in the local
    zone the day's date was taken in, that day's DST rule included.
    """
    return datetime.datetime(day.year, day.month, day.day,
                             EVALUATION_HOUR).astimezone(
                                 datetime.timezone.utc)


def run_gaps(connection):
    """Each run's timestamp and the seconds since the run before it.

    The gap is `LAG`'s, computed in SQL as the plan requires; the first
    row's gap is None.
    """
    return connection.execute(
        'SELECT ts, (julianday(ts) - julianday(previous)) * 86400.0'
        ' FROM (SELECT ts, LAG(ts) OVER (ORDER BY ts) AS previous'
        ' FROM runs) ORDER BY ts').fetchall()


def covered(gaps, moment):
    """Whether the day at *moment* was sampled densely enough.

    The plan: "A day has coverage when no gap between consecutive runs
    in [T - 24 h, T + 24 h] exceeds 2 h."

    That is not only about the gaps *inside* the window. A window whose
    first twenty-four hours were never sampled has no internal gap to
    measure, and would pass a check that looked only at those -- and the
    day would be judged on half the context it is supposed to have. So
    the window must also be **spanned**: a run within the coverage gap
    of each end, and no interior gap over the limit.

    The gap is compared with a tolerance. ``julianday`` resolves to
    about ten microseconds, so an exactly two-hour gap comes back as
    7200.000013 or 7199.999973 depending on the date; without the
    tolerance the same sampling pattern would be covered on one day and
    not on the next.
    """
    low, high = iso_utc(moment - DAY), iso_utc(moment + DAY)
    inside = [row for row in gaps if low <= row[0] <= high]
    if len(inside) < 2:
        return False
    # the window's ends must be sampled, or the day has no context
    if inside[0][0] > iso_utc(moment - DAY + 2 * HOUR):
        return False
    if inside[-1][0] < iso_utc(moment + DAY - 2 * HOUR):
        return False
    return all(gap is not None and gap <= COVERAGE_GAP + GAP_TOLERANCE
               for _ts, gap in inside[1:])


def evaluated_days(gaps):
    """Every local day between the first run's date and the last one's.

    Returns ``(day, moment, covered)``: the date, its 06:00 local time as
    UTC, and whether that day can be judged.
    """
    if not gaps:
        return []
    day = parse_moment(gaps[0][0]).astimezone().date()
    last = parse_moment(gaps[-1][0]).astimezone().date()
    days = []
    while day <= last:
        moment = day_moment(day)
        days.append((day, moment, covered(gaps, moment)))
        day += DAY
    return days


def stored_maps(connection):
    """The viewer, the accounts they follow, and their mutuals.

    A mutual is an author the relationships say follows the viewer, the
    set Catch Up passes as ``mutual_author_ids`` (catchuprank.py:985-991).
    """
    row = connection.execute(
        "SELECT value FROM meta WHERE key = 'viewer_id'").fetchone()
    following, mutuals = set(), set()
    for account_id, follows, followed_by in connection.execute(
            'SELECT account_id, following, followed_by FROM relationships'):
        if follows:
            following.add(str(account_id))
        if followed_by:
            mutuals.add(str(account_id))
    return (row[0] if row else None), following, mutuals


def content(status):
    """The post a timeline entry shows: the boosted one, for a boost."""
    reblog = status.get('reblog')
    return reblog if isinstance(reblog, dict) else status


def author_id(status):
    return str((content(status).get('account') or {}).get('id') or '')


def created_ms(status):
    moment = parse_moment(status.get('created_at'))
    return int(moment.timestamp() * 1000) if moment else 0


def engagement_of(status):
    post = content(status)
    return (int(post.get('favourites_count') or 0)
            + int(post.get('reblogs_count') or 0))


def followers_of(status):
    return int((content(status).get('account') or {}).get('followers_count')
               or 0)


def observed(status, row):
    """*status* with its counters, in a copy, replaced by one observation.

    The counters live on the boosted post for a boost, which is where
    `collect` stored them, so they are written back there.
    """
    copy = dict(status)
    target = copy
    if isinstance(copy.get('reblog'), dict):
        target = copy['reblog'] = dict(copy['reblog'])
    target.update(favourites_count=row[0], reblogs_count=row[1],
                  replies_count=row[2], favourited=bool(row[3]),
                  reblogged=bool(row[4]), bookmarked=bool(row[5]))
    return copy


def candidates_at(connection, moment):
    """The feed at *moment*, with nothing it could not know yet.

    Candidates are posts created in [T - 24 h, T) that no observation at
    or before T shows engaged. Each one's counters are replaced, in a
    copy, by the last observation at or before T: one query, the row with
    the greatest ``run_ts <= T`` per post. A post first observed after T
    was not in the feed at T, so it is not a candidate.
    """
    before = iso_utc(moment)
    engaged = {row[0] for row in connection.execute(
        'SELECT DISTINCT post_id FROM observations WHERE run_ts <= ?'
        ' AND (favourited OR reblogged OR bookmarked)', (before,))}
    latest = {row[0]: row[1:] for row in connection.execute(
        'SELECT post_id, favourites, reblogs, replies, favourited,'
        ' reblogged, bookmarked FROM (SELECT *, ROW_NUMBER() OVER'
        ' (PARTITION BY post_id ORDER BY run_ts DESC) AS newest'
        ' FROM observations WHERE run_ts <= ?) WHERE newest = 1',
        (before,))}
    start, found = moment - DAY, []
    for post_id, raw in connection.execute('SELECT id, json FROM posts'):
        status = json.loads(raw)
        created = parse_moment(status.get('created_at'))
        if (created is not None and start <= created < moment
                and post_id not in engaged and post_id in latest):
            found.append(observed(status, latest[post_id]))
    return found


def labels_at(connection, moment, candidates):
    """The candidates first observed engaged in (T, T + 24 h], and how
    many of them fall within an hour of T.

    A flag is the server's state at the run that saw it, so a favourite
    made just before T cannot be told from one made just after; the near
    count makes that blur visible. Boosts and replies are exact, from the
    viewer's own statuses, and are matched by content id.
    """
    start, after = iso_utc(moment), iso_utc(moment + DAY)
    near_limit = iso_utc(moment + HOUR)
    first = {row[0]: row[1] for row in connection.execute(
        'SELECT post_id, MIN(run_ts) FROM observations'
        ' WHERE (favourited OR reblogged OR bookmarked) GROUP BY post_id')}
    canonical = {}
    for status in candidates:
        post_id = str(status.get('id'))
        canonical[post_id] = post_id
        reblog = status.get('reblog')
        if isinstance(reblog, dict) and reblog.get('id'):
            canonical[str(reblog['id'])] = post_id
    labelled, near = set(), 0
    for status in candidates:
        seen = first.get(str(status.get('id')))
        if seen is None or not start < seen <= after:
            continue
        labelled.add(str(status.get('id')))
        if seen <= near_limit:
            near += 1
    for (raw,) in connection.execute('SELECT json FROM own'):
        status = json.loads(raw)
        created = parse_moment(status.get('created_at'))
        reblog = status.get('reblog')
        target = str(reblog.get('id') if isinstance(reblog, dict)
                     else status.get('in_reply_to_id'))
        if (created is not None and moment < created <= moment + DAY
                and target in canonical):
            labelled.add(canonical[target])
    return labelled, near


def near_labels(connection, moment, candidates):
    """The candidates first observed engaged within an hour of T.

    The same rule `labels_at` counts, but returning which ones, so a
    check can name the post on the edge rather than only the count.
    """
    start, near_limit = iso_utc(moment), iso_utc(moment + HOUR)
    first = {row[0]: row[1] for row in connection.execute(
        'SELECT post_id, MIN(run_ts) FROM observations'
        ' WHERE (favourited OR reblogged OR bookmarked) GROUP BY post_id')}
    near = set()
    for status in candidates:
        seen = first.get(str(status.get('id')))
        if seen is not None and start < seen <= near_limit:
            near.add(str(status.get('id')))
    return near


def affinity_at(connection, moment, viewer, mutuals):
    """The affinity maps at T, from what was first seen *before* T only.

    ``now_ms`` is T, so the decay and the bootstrapped strengths are
    measured from the same clock the ranking uses.
    """
    before = iso_utc(moment)

    def listed(table):
        return [json.loads(row[0]) for row in connection.execute(
            'SELECT json FROM %s WHERE first_seen < ?' % table, (before,))]

    engaged = affinity.bootstrap_engaged(
        listed('favourites'), listed('bookmarks'), listed('own'), viewer,
        mutuals)
    return affinity.derive_affinities(engaged, (), (),
                                      int(moment.timestamp() * 1000))


def orderings_at(candidates, moment, resolver, signals, mutuals, following):
    """The five orderings of one day's candidates (plan item 5).

    ``chrono`` is Home's order. ``rank_null`` and ``rank_affinity`` are
    Catch Up's pipeline, without and with the affinity resolver.
    ``engagement`` is the popularity straw man: favourites plus reblogs.
    ``affinity_chrono`` reads the same author signal the resolver reads
    and falls back to time; its scale is not the ranker's, so the two are
    compared as orderings, never as numbers. ``in_network`` is false only
    for a non-boost whose author the viewer does not follow.
    """
    wrapped = [catchuprank.candidate_from_status(
        status, in_network=(isinstance(status.get('reblog'), dict)
                            or author_id(status) in following))
        for status in candidates]

    def ranked_with(affinity_resolver):
        context = catchuprank.RankingContext(
            now=moment, mutual_author_ids=mutuals,
            affinity=affinity_resolver)
        return [candidate.status
                for candidate in catchuprank.rank(wrapped, context)]

    def descending(key):
        # Primary key, then newest, then id: a total order, so the same
        # data always compares the same way.
        return sorted(candidates, key=lambda status: (
            key(status), created_ms(status), str(status.get('id'))),
            reverse=True)

    def by_signal(status):
        return catchuprank.affinity_signal(signals.author_affinity,
                                           author_id(status))

    return {
        'chrono': descending(lambda status: 0),
        'rank_null': ranked_with(catchuprank.NULL_AFFINITY),
        'rank_affinity': ranked_with(resolver),
        'engagement': descending(engagement_of),
        'affinity_chrono': descending(by_signal),
    }


def small_authors(candidates):
    """Author ids below the day's candidate median follower count.

    One value per candidate -- the median of what the feed offered, so an
    author with four of the day's posts counts four times.
    """
    if not candidates:
        return set()
    cutoff = statistics.median([followers_of(s) for s in candidates])
    return {author_id(status) for status in candidates
            if followers_of(status) < cutoff}


def metrics_at(ranked, labels, small, engagement_ids):
    """One ordering's metrics on one day (plan item 6)."""
    ids = [str(status.get('id')) for status in ranked]
    row = {}
    for k in (20, 50):
        row['recall%d' % k] = (
            len(labels & set(ids[:k])) / len(labels) if labels else None)
    top = ranked[:20]
    authors = [author_id(status) for status in top]
    row['authors20'] = len(set(authors))
    row['small20'] = (sum(1 for author in authors if author in small)
                      / len(top) if top else None)
    row['overlap20'] = len(set(ids[:20]) & engagement_ids)
    return row


def mean_of(rows, ordering, metric):
    """The mean of one metric over the days that produced it."""
    values = [row['orderings'][ordering][metric] for row in rows
              if row['orderings'][ordering].get(metric) is not None]
    return statistics.fmean(values) if values else None


def sign_test(wins, n):
    """The sign test §4 pins: p = the sum over i >= wins of C(n,i)/2**n.

    One-sided, H0: P(win) = 0.5 against H1: P(win) > 0.5. Ties are
    dropped by the caller, so *n* is the non-tied count.
    """
    if n <= 0:
        return 1.0
    return sum(math.comb(n, i) for i in range(wins, n + 1)) / 2 ** n


def sign_result(rows, candidate, baseline, gates):
    """The sign test of two orderings, scored by recall@``a1_k``.

    A day where both score the same is a tie and is dropped; a day
    either could not score is dropped too. Too few non-tied days is
    INCONCLUSIVE: not a pass, and not a fail, because a sample that small
    says nothing either way (§4).
    """
    k, wins, losses = gates['a1_k'], 0, 0
    for row in rows:
        candidate_recall = row['orderings'][candidate]['recall%d' % k]
        baseline_recall = row['orderings'][baseline]['recall%d' % k]
        if candidate_recall is None or baseline_recall is None:
            continue
        if candidate_recall > baseline_recall:
            wins += 1
        elif candidate_recall < baseline_recall:
            losses += 1
    n = wins + losses
    p = sign_test(wins, n)
    if n < gates['min_nontied_days']:
        verdict = 'INCONCLUSIVE'
    else:
        verdict = 'PASS' if p <= gates['alpha'] else 'FAIL'
    return {'verdict': verdict, 'nontied': n, 'wins': wins, 'p': p}


def gate_a1(rows, ordering, gates):
    """A1: the sign test against chrono, plus the mean recall ratio."""
    result = sign_result(rows, ordering, 'chrono', gates)
    metric = 'recall%d' % gates['a1_k']
    candidate = mean_of(rows, ordering, metric)
    baseline = mean_of(rows, 'chrono', metric)
    result['mean_ratio'] = candidate / baseline if baseline else None
    if result['verdict'] == 'PASS':
        if baseline:
            enough = result['mean_ratio'] >= gates['a1_min_mean_ratio']
        else:
            # chrono recalled nothing on any day: any recall beats none.
            enough = bool(candidate)
        result['verdict'] = 'PASS' if enough else 'FAIL'
    return result


def gate_a2(rows, ordering, gates):
    """A2: breadth in the top ``a2_k``, against chrono's means.

    The harness records the top-20 metrics (plan item 6), so ``a2_k`` must
    be 20; the gates file pins it there.
    """
    k = gates['a2_k']
    if k != 20:
        raise ValueError('a2_k must be 20: only the top 20 is measured')
    authors = mean_of(rows, ordering, 'authors%d' % k)
    chrono_authors = mean_of(rows, 'chrono', 'authors%d' % k)
    share = mean_of(rows, ordering, 'small%d' % k)
    chrono_share = mean_of(rows, 'chrono', 'small%d' % k)
    result = {'authors': authors, 'chrono_authors': chrono_authors,
              'small_share': share, 'chrono_small_share': chrono_share}
    if None in (authors, chrono_authors, share, chrono_share):
        result['verdict'] = 'INCONCLUSIVE'
        return result
    enough = (authors >= chrono_authors - gates['a2_max_author_drop']
              and share
              >= gates['a2_min_small_share_ratio'] * chrono_share)
    result['verdict'] = 'PASS' if enough else 'FAIL'
    return result


def judge(rows, gates):
    """The four decisions and the H1 record (plan item 7).

    A4 asks whether ``rank()`` with affinity beats ``affinity_chrono``: if
    it does not, the simpler ordering ships. A3 asks whether affinity
    beats no affinity at all, and decides ``USE_AFFINITY``. The ordering
    they select is then run through A1 and A2 *itself*: Gate A is PASS
    only when that ordering passes both, so no ordering ships on another
    one's numbers (§4, H1).
    """
    a1 = gate_a1(rows, 'rank_affinity', gates)
    a2 = gate_a2(rows, 'rank_affinity', gates)
    a3 = sign_result(rows, 'rank_affinity', 'rank_null', gates)
    a4 = sign_result(rows, 'rank_affinity', 'affinity_chrono', gates)
    ordering = 'rank' if a4['verdict'] == 'PASS' else 'affinity_chrono'
    use_affinity = a3['verdict'] == 'PASS'
    if ordering == 'affinity_chrono':
        shipped = 'affinity_chrono'
    elif use_affinity:
        shipped = 'rank_affinity'
    else:
        shipped = 'rank_null'
    if shipped == 'rank_affinity':
        shipped_a1, shipped_a2 = a1, a2
    else:
        shipped_a1 = gate_a1(rows, shipped, gates)
        shipped_a2 = gate_a2(rows, shipped, gates)
    verdicts = (shipped_a1['verdict'], shipped_a2['verdict'])
    if 'FAIL' in verdicts:
        gate_a = 'FAIL'
    elif verdicts == ('PASS', 'PASS'):
        gate_a = 'PASS'
    else:
        gate_a = 'INCONCLUSIVE'
    return {'a1': a1, 'a2': a2, 'a3': a3, 'a4': a4, 'shipped': shipped,
            'shipped_a1': shipped_a1, 'shipped_a2': shipped_a2,
            'gate_a': gate_a, 'ordering': ordering,
            'use_affinity': use_affinity}


def load_gates(path=None):
    """The frozen gates, or None when there is no file.

    ``bench/rank_eval_gates.json`` is the maintainer's, written once under
    H0: this reads it and never writes it.
    """
    path = path or os.path.join(HERE, 'rank_eval_gates.json')
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None


def number(value):
    """A number as printed: three places if it has a fraction, or a dash."""
    if value is None:
        return '-'
    return '%.3f' % value if isinstance(value, float) else '%d' % value


def print_gates(decision, out):
    """The gate lines, ending in the H1 decision record (§4)."""
    for name in ('a1', 'a2', 'a3', 'a4'):
        result = decision[name]
        details = ' '.join('%s=%s' % (key, number(value))
                           for key, value in result.items()
                           if key != 'verdict')
        print('%s %s %s' % (name.upper(), result['verdict'], details),
              file=out)
    if decision['shipped'] != 'rank_affinity':
        print('%s: A1 %s A2 %s' % (decision['shipped'],
                                   decision['shipped_a1']['verdict'],
                                   decision['shipped_a2']['verdict']),
              file=out)
    print('Gate A: %s' % decision['gate_a'], file=out)
    print('ORDERING: %s' % decision['ordering'], file=out)
    print('USE_AFFINITY: %s' % ('true' if decision['use_affinity']
                                else 'false'), file=out)


def evaluate(instance, gates_path=None, json_path=None, root=None, out=None,
             account_id=None):
    """Judge the recorded feed, day by day. Returns an exit code.

    Every local day between the first and the last run is printed. A day
    whose 48-hour window was not sampled at most two hours apart is
    reported as ``no-coverage``, and left out of every count and mean.

    With a gates file the four decisions and the H1 record are printed,
    and the same numbers go to *json_path* as a summary carrying no post,
    no id and no handle. Without one, only the metrics are printed: the
    thresholds were frozen before the data came in (H0), so they cannot
    be chosen after it.

    *account_id* names the account whose data to read, and it must be the
    same one ``collect`` wrote under: H1 evaluates three accounts and
    aggregates three summaries, so each account's days must be its own.
    """
    out = out or sys.stdout
    try:
        instance_url = mastodon.normalize_instance(instance)
    except ValueError as error:
        print('rank_eval: %s' % error, file=sys.stderr)
        return 2
    connection = open_database(
        database_path(instance_url, root, account_id))
    try:
        viewer, following, mutuals = stored_maps(connection)
        gaps = run_gaps(connection)
        if not gaps:
            print('rank_eval: no runs recorded', file=sys.stderr)
            others = sibling_accounts(instance_url, root)
            if others:
                print('rank_eval: this instance has data for %s; pass the'
                      ' same --account-id that collect used'
                      % ', '.join(others), file=sys.stderr)
            return 1
        rows = []
        for day, moment, sampled in evaluated_days(gaps):
            if not sampled:
                print('%s 06:00 local no-coverage' % day, file=out)
                continue
            candidates = candidates_at(connection, moment)
            labels, near = labels_at(connection, moment, candidates)
            affinities = affinity_at(connection, moment, viewer, mutuals)
            signals = affinity.to_ranking_signals(affinities)
            now_ms = int(moment.timestamp() * 1000)
            ranked = orderings_at(
                candidates, moment,
                affinity.resolver(affinities, (), (), (), now_ms),
                signals, mutuals, following)
            small = small_authors(candidates)
            overlay = {str(status.get('id'))
                       for status in ranked['engagement'][:20]}
            row = {'date': day.isoformat(), 't': iso_utc(moment),
                   'candidates': len(candidates), 'labels': len(labels),
                   'labels_near_start': near, 'orderings': {}}
            for name in ORDERINGS:
                row['orderings'][name] = metrics_at(
                    ranked[name], labels, small, overlay)
            rows.append(row)
            print('%s 06:00 local candidates=%d labels=%d near=%d'
                  % (day, len(candidates), len(labels), near), file=out)
            for name in ORDERINGS:
                print('  %-16s %s' % (name, ' '.join(
                    '%s=%s' % (metric, number(row['orderings'][name][metric]))
                    for metric in METRICS)), file=out)
    finally:
        connection.close()

    means = {name: {metric: mean_of(rows, name, metric)
                    for metric in METRICS} for name in ORDERINGS}
    summary = {'version': 1, 'days': rows, 'means': means}
    gates = load_gates(gates_path)
    if gates is None:
        print('gates not frozen, no verdict', file=out)
    else:
        decision = judge(rows, gates)
        summary['gates'] = {name: decision[name]
                            for name in ('a1', 'a2', 'a3', 'a4')}
        summary['gate_a'] = decision['gate_a']
        summary['ordering'] = decision['ordering']
        summary['use_affinity'] = decision['use_affinity']
        # The ordering that shipped, and the verdicts it earned on its
        # own numbers. ``aggregate`` needs these: it must not name an
        # ordering that failed the plan's shipped-ordering rule on the
        # accounts that tested it, and it can only know that from the
        # records.
        summary['shipped'] = decision['shipped']
        summary['shipped_a1'] = decision['shipped_a1']['verdict']
        summary['shipped_a2'] = decision['shipped_a2']['verdict']
        print_gates(decision, out)
    if json_path:
        with open(json_path, 'w', encoding='utf-8') as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
            handle.write('\n')
    return 0


def aggregate(paths, gates_path=None, out=None):
    """The H1 record across accounts: the per-account summaries, judged.

    Gate A passes when at least ``min_accounts_passing`` accounts'
    records pass **and** those records shipped an ordering that passed
    A1 and A2 on its own numbers — the plan's shipped-ordering rule,
    which the accounts' majority cannot override. A failure on any
    account that is not offset by enough passes is a FAIL; only accounts
    that ran out of days without failing make the whole INCONCLUSIVE.
    The ordering and the affinity decision are the accounts' majority,
    and a tie falls back to the more conservative answer: ``rank``,
    without affinity. Without a gates file nothing is decided.
    """
    out = out or sys.stdout
    gates = load_gates(gates_path)
    if gates is None:
        print('gates not frozen, no verdict', file=out)
        return 0
    records = []
    seen = []
    for path in paths:
        # The same account twice is one account counted twice, and the
        # gate would read it as two. H1 folds three *different* accounts,
        # so a path named twice is a mistake, not a fold.
        key = os.path.abspath(path)
        if key in seen:
            print('rank_eval: %s was given twice; each account is one'
                  ' summary' % path, file=sys.stderr)
            return 2
        seen.append(key)
        with open(path, encoding='utf-8') as handle:
            records.append(json.load(handle))
    verdicts = [record.get('gate_a') for record in records]
    orderings = [record.get('ordering') for record in records]
    ordering = ('affinity_chrono'
                if orderings.count('affinity_chrono') > orderings.count('rank')
                else 'rank')
    uses = [record.get('use_affinity') for record in records]
    use_affinity = 2 * sum(1 for value in uses if value) > len(uses)

    # The ordering that ships is named by ``ordering`` and ``use_affinity``
    # together: 'rank' with affinity is ``rank_affinity``, 'rank' without
    # is ``rank_null``, and 'affinity_chrono' is itself. The plan says
    # that ordering "must pass A1 and A2 itself", so the records that
    # shipped *that* ordering and passed are the ones that carry the
    # decision. Without this, a majority could name an ordering that
    # failed on the only account that tested it.
    shipped = ('affinity_chrono' if ordering == 'affinity_chrono'
               else ('rank_affinity' if use_affinity else 'rank_null'))
    carried = [record for record in records
               if record.get('shipped') == shipped
               and record.get('shipped_a1') == 'PASS'
               and record.get('shipped_a2') == 'PASS']
    if len(carried) >= gates['min_accounts_passing']:
        gate_a = 'PASS'
    elif 'FAIL' in verdicts:
        gate_a = 'FAIL'
    else:
        gate_a = 'INCONCLUSIVE'
    print('Gate A: %s' % gate_a, file=out)
    print('ORDERING: %s' % ordering, file=out)
    print('USE_AFFINITY: %s' % ('true' if use_affinity else 'false'),
          file=out)
    return 0


# region self-test


class Checks:
    """`ok - …` lines, and `FAIL - …` for what did not hold."""

    def __init__(self):
        self.count = 0
        self.failures = 0

    def check(self, label, condition):
        self.count += 1
        if condition:
            print('ok - %s' % label)
        else:
            self.failures += 1
            print('FAIL - %s' % label)
        return bool(condition)

    def equal(self, label, got, want):
        same = got == want
        return self.check(label if same else
                          '%s: got %r, wanted %r' % (label, got, want), same)

    def close(self, label, got, want, tolerance=1e-9):
        near = got is not None and abs(got - want) <= tolerance
        return self.check(label if near else
                          '%s: got %r, wanted %r +/-%g'
                          % (label, got, want, tolerance), near)


class _NoNetwork:
    """Stands in for `mastodon.MastodonClient` while the self-test runs.

    Its ``__init__`` raises, so a run that reached for the real client
    fails here instead of opening a session. Patching `socket.socket`
    would prove nothing: libsoup does not use Python's sockets, so the
    test could pass while the network was used.
    """

    def __init__(self, *args, **kwargs):
        raise AssertionError('the self-test must not construct a client')


def pin_zone():
    """Judge days in UTC, for this helper on its own.

    D3 judges a day at 06:00 *local*, and the world's dates are written
    in UTC, so a check run under another zone compares the world's days
    against a 06:00 that has moved. `self_test` pins the zone for the
    whole run, but each helper calls this too: a helper that is only
    right because of the call order is a helper that fails when it is
    run alone. `time.tzset()` is how the C library is told.
    """
    os.environ['TZ'] = 'UTC'
    time.tzset()


# Everything about the synthetic world is a function of these, so the
# dates the checks name are the dates the world produces.
SYNTHETIC_INSTANCE = 'https://synth.example'
VIEWER_ID = 'synth-viewer'
# The acct the fake's account endpoint answers with, so the viewer's
# handle is one of the strings a summary must never carry.
VIEWER_ACCT = 'synth'
LIKED_AUTHOR = 'synth-liked'
WORLD_START = datetime.datetime(2026, 10, 5, tzinfo=datetime.timezone.utc)
WORLD_DAYS = 5
LIKED_PER_DAY = 5
FILLERS_PER_DAY = 26
MINUTE = datetime.timedelta(minutes=1)


def synthetic_status(post_id, author, created, followers=500):
    """One status, in the shape Mastodon answers with.

    Every post carries the same text, tag and language on purpose: the
    ranker's diversity reranker then has nothing to prefer, so what moves
    an ordering here is freshness and the author signal, which are the
    two things these checks are about.
    """
    return {
        'id': post_id,
        'created_at': created.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'account': {
            'id': author, 'acct': author, 'username': author,
            'display_name': author, 'followers_count': followers,
            'following_count': 300, 'statuses_count': 900,
            'note': 'a synthetic account', 'fields': [],
        },
        'content': '<p>a synthetic post</p>',
        'tags': [{'name': 'synth'}],
        'language': 'en',
        'favourites_count': 0, 'reblogs_count': 0, 'replies_count': 0,
    }


class SyntheticWorld:
    """A feed, and when the reader engaged with it.

    ``statuses`` maps a post id to the status as the server first sent
    it, and ``likes`` maps the posts the reader favourites to the moment
    the favourite appears. A run at or after that moment shows the post
    ``favourited``, and the harness learns both the flag (a label, later)
    and the author signal (a favourite in ``/favourites``) from it.

    ``counters`` maps a post id to a schedule of ``(moment, favourites,
    reblogs)``: the last entry at or before the run's moment is what the
    server reports. It changes the counters of posts the reader never
    engaged, which is the only way a *candidate* can carry a counter --
    an engaged post is not a candidate by definition, so a world that
    only moves liked posts' counters leaves every candidate at zero and
    cannot tell "the last observation" from "the first".
    """

    def __init__(self, statuses, likes, own=(), counters=None):
        self.statuses = statuses
        self.likes = likes
        self.own = list(own)
        self.counters = counters or {}

    def _view(self, post_id, moment):
        status = self.statuses[post_id]
        seen = status
        for at, favourites, reblogs in self.counters.get(post_id, ()):
            if at <= moment:
                if seen is status:
                    seen = dict(status)
                seen['favourites_count'] = favourites
                seen['reblogs_count'] = reblogs
        at = self.likes.get(post_id)
        if at is None or at > moment:
            return seen
        if seen is status:
            seen = dict(status)
        seen['favourited'] = True
        seen['favourites_count'] = 1
        return seen

    def visible(self, moment):
        """The home timeline at *moment*: the last 48 hours, the way
        Mastodon caps it."""
        start = moment - 2 * DAY
        return [self._view(post_id, moment)
                for post_id, status in sorted(self.statuses.items())
                if start < parse_moment(status['created_at']) <= moment]

    def favourites(self, moment):
        """The reader's favourites at *moment*."""
        return [self._view(post_id, moment)
                for post_id, at in sorted(self.likes.items())
                if at <= moment]

    def own_statuses(self, moment):
        """The reader's own posts, boosts and replies written by *moment*."""
        return [status for status in self.own
                if parse_moment(status['created_at']) <= moment]


class FakeClient:
    """A duck-typed `mastodon.MastodonClient`: the calls `collect` makes,
    answered from a `SyntheticWorld`, with no socket anywhere.

    ``request_async`` calls back before it returns, which `call` allows --
    it only runs the loop when nothing answered. The account endpoint
    answers with the account **object**, the shape the real one answers
    with and the harness reads; answering with a one-element list is what
    hid an off-by-one during T1.1's review, so `check_collect` asserts a
    list is *rejected*, not accepted.
    """

    def __init__(self, world, moment, fail_at=None, limited=False):
        self.world = world
        self.moment = moment
        self.fail_at = fail_at
        self.limited = limited
        self.asked = []

    def rate_limited(self):
        return self.limited

    def retry_after_seconds(self):
        return 30

    def request_async(self, method, path, params=None, callback=None):
        self.asked.append((path, params))
        callback(self, None)

    def request_finish_paged(self, result):
        path, params = self.asked[-1]
        if path == self.fail_at:
            raise GLib.Error.new_literal(
                GLib.quark_from_string('rank-eval'), 'the request failed', 1)
        return self._answer(path, params), None, None

    def _answer(self, path, params):
        if path == '/accounts/verify_credentials':
            return {'id': VIEWER_ID, 'acct': VIEWER_ACCT,
                    'username': VIEWER_ACCT}
        if path == '/timelines/home':
            return self.world.visible(self.moment)
        if path == '/favourites':
            return self.world.favourites(self.moment)
        if path == '/accounts/%s/statuses' % VIEWER_ID:
            return self.world.own_statuses(self.moment)
        if path == '/accounts/relationships':
            return [{'id': account_id, 'following': True,
                     'followed_by': False}
                    for account_id in (params or {}).get('id[]') or ()]
        return []


class FakeAccountClient:
    """A duck-typed client for `token`, `follow`, `engage` and `inspect`.

    Reads answer from a small fixture and writes are recorded. The action
    names in *refuse* fail with a ``GLib.Error`` carrying the HTTP status
    a real server would answer with -- which is how the commands'
    refusal paths are seen without a server or a keyring. ``verify`` is a
    read; the rest are writes.

    The account body is the **object** the real endpoint answers with and
    `collect` reads (auth.py:535), never a one-element list.
    """

    def __init__(self, account_id=VIEWER_ID, following_count=0,
                 followers_count=0, statuses_count=0, follows=(),
                 following=(), requested=(), home=(), refuse=None,
                 limited_after=None, next_pages=()):
        self.account_id = str(account_id)
        self.following_count = following_count
        self.followers_count = followers_count
        self.statuses_count = statuses_count
        self.source_follows = [str(account) for account in follows]
        self.already = {str(account) for account in following}
        self.pending_requests = {str(account) for account in requested}
        self.home = list(home)
        self.refuse = dict(refuse or {})
        self.limited_after = limited_after
        # The parameters each page hands back for the next one, in order.
        self.next_pages = list(next_pages)
        self.asked = []
        self.asked_visibility = {}
        self.writes = 0
        self.followed = []
        self.requested = []
        self.pending = (None, None)

    def rate_limited(self):
        return (self.limited_after is not None
                and self.writes >= self.limited_after)

    def _account(self):
        return {'id': self.account_id, 'acct': 'managed',
                'username': 'managed',
                'following_count': self.following_count + len(self.followed),
                'followers_count': self.followers_count,
                'statuses_count': self.statuses_count}

    def _answer(self, path, params):
        if path == '/accounts/verify_credentials':
            return self._account()
        if path == '/timelines/home':
            return self.home
        if path == '/accounts/relationships':
            return [{'id': account_id,
                     'following': account_id in self.already,
                     'requested': account_id in self.pending_requests,
                     'followed_by': False}
                    for account_id in (params or {}).get('id[]') or ()]
        if path.startswith('/accounts/') and path.endswith('/following'):
            return [{'id': account_id} for account_id in self.source_follows]
        return []

    def _error(self, action):
        status = self.refuse.get(action)
        if status is None:
            return None
        return GLib.Error('%s refused by the test server' % action,
                          'mastodon-http-error-quark', status)

    def _finish(self, result):
        value, error = self.pending
        if error is not None:
            raise error
        return value

    def _read(self, action, path, params, callback):
        self.asked.append((action, path, params))
        self.pending = (self._answer(path, params), self._error(action))
        callback(self, None)

    def _write(self, action, argument, value):
        self.asked.append((action, argument))
        self.writes += 1
        self.pending = (value, self._error(action))

    def request_async(self, method, path, params=None, callback=None):
        self._read('request', path, params, callback)

    def request_finish_paged(self, result):
        # A page that has a next one hands back the parameters to ask
        # for it, which is how `inspect` knows the home timeline has
        # more than the first page and how `follow` walks the list. The
        # pages are per path, because one run asks for several.
        path = self.asked[-1][1] if self.asked else ''
        for index, (wanted, params) in enumerate(self.next_pages):
            if wanted == path:
                self.next_pages.pop(index)
                return self._finish(result), params, None
        return self._finish(result), None, None

    def verify_credentials_async(self, callback=None, cancellable=None):
        self._read('verify', '/accounts/verify_credentials', None, callback)

    def verify_credentials_finish(self, result):
        return self._finish(result)

    def favourite_async(self, status_id, callback=None, cancellable=None):
        self._write('favourite', str(status_id),
                    {'id': str(status_id), 'favourited': True})
        callback(self, None)

    def favourite_finish(self, result):
        return self._finish(result)

    def reblog_async(self, status_id, visibility=None, callback=None,
                     cancellable=None):
        # The visibility is kept beside the call, so a check can say the
        # harness passed it on rather than dropping it.
        self.asked_visibility['reblog'] = visibility
        self._write('reblog', str(status_id), {'id': str(status_id),
                                               'reblogged': True})
        callback(self, None)

    def reblog_finish(self, result):
        return self._finish(result)

    def bookmark_async(self, status_id, callback=None, cancellable=None):
        self._write('bookmark', str(status_id), {'id': str(status_id),
                                                 'bookmarked': True})
        callback(self, None)

    def bookmark_finish(self, result):
        return self._finish(result)

    def reply_async(self, status_id, text, params=None, callback=None,
                    cancellable=None):
        self.asked.append(('reply', (str(status_id), text,
                                     dict(params or {}))))
        self.writes += 1
        self.pending = ({'id': 'reply-1'}, self._error('reply'))
        callback(self, None)

    def reply_finish(self, result):
        return self._finish(result)

    def follow_async(self, account_id, reblogs=True, notify=False,
                     callback=None, cancellable=None):
        account_id = str(account_id)
        self.asked.append(('follow', account_id))
        self.writes += 1
        error = self._error('follow')
        if error is None:
            self.already.add(account_id)
            self.followed.append(account_id)
        self.pending = ({'id': account_id, 'following': error is None,
                         'requested': False}, error)
        callback(self, None)

    def follow_finish(self, result):
        return self._finish(result)


def record(root, world, moments):
    """Record *world* once at each of *moments*, through the fake.

    `collect` calls its factory with (instance, token) and nothing else,
    so the moment a client is built for lives in a cell the loop fills
    in.
    """
    clock = {'now': moments[0]}
    quiet = io.StringIO()

    def factory(_instance, _token):
        return FakeClient(world, clock['now'])

    for moment in moments:
        clock['now'] = moment
        collect(SYNTHETIC_INSTANCE, make_client=factory, token='synthetic',
                now=moment, root=root, out=quiet)


def synthetic_world():
    """Five days of a feed, and labels planted on one author's posts.

    Each day at T has LIKED_PER_DAY posts by LIKED_AUTHOR and
    FILLERS_PER_DAY by distinct authors. Every liked post is older than
    every filler, so "newest first" puts the liked ones past the
    twentieth place -- while an ordering that reads the author signal
    puts them first. The likes that *label* a day's posts happen two
    hours after its T, and the likes that teach the author signal are
    yesterday's, so a day is judged on evidence that existed before it.

    LIKED_AUTHOR is a small account (100 followers) against fillers with
    500, which keeps `small20` a number that can move.

    One filler a day carries counters that grow while nobody engages it.
    It is a *candidate* on its own day, so its counters are the only way
    the day's numbers can tell "the last observation at or before T"
    from "the first one": with a zero everywhere, `ORDER BY run_ts ASC`
    and `engagement_of` returning a constant would both go unnoticed.
    """
    statuses, likes, counters = {}, {}, {}
    for index in range(WORLD_DAYS):
        moment = WORLD_START + index * DAY + EVALUATION_HOUR * HOUR
        for slot in range(LIKED_PER_DAY):
            post_id = 'liked-%d-%d' % (index, slot)
            statuses[post_id] = synthetic_status(
                post_id, LIKED_AUTHOR,
                moment - 8 * HOUR + slot * 10 * MINUTE, followers=100)
            likes[post_id] = moment + 2 * HOUR
        for slot in range(FILLERS_PER_DAY):
            post_id = 'filler-%d-%d' % (index, slot)
            statuses[post_id] = synthetic_status(
                post_id, 'filler-%d-%d' % (index, slot),
                moment - 7 * HOUR + (slot + 1) * 13 * MINUTE)
        counters['filler-%d-0' % index] = [
            (moment - 7 * HOUR, 0, 0),
            (moment - 2 * HOUR, 3, 1),
            (moment, 6, 2),
            (moment + HOUR, 9, 4),
        ]
    return SyntheticWorld(statuses, likes, counters=counters)


PLANT_T = WORLD_START + 2 * DAY + EVALUATION_HOUR * HOUR
PLANT_LEAK = 'plant-leak'


def plant_world(evidence_before):
    """The leak plant: one day, and one author whose *only* engagement is
    a favourite that the two variants place either side of T.

    Nothing else differs between the two databases, so whatever the day's
    numbers do with that one row shows up as a difference between them --
    which is how the check proves that reading it would change the top
    20, and that the harness does not read it.
    """
    statuses = {}
    for slot in range(26):
        post_id = 'plant-filler-%02d' % slot
        statuses[post_id] = synthetic_status(
            post_id, 'plant-filler-%02d' % slot,
            PLANT_T - 6 * HOUR + slot * 11 * MINUTE)
    statuses['plant-leak'] = synthetic_status(
        'plant-leak', PLANT_LEAK, PLANT_T - 12 * HOUR, followers=100)
    statuses['plant-leak-old'] = synthetic_status(
        'plant-leak-old', PLANT_LEAK, PLANT_T - 3 * DAY, followers=100)
    statuses['plant-engaged'] = synthetic_status(
        'plant-engaged', 'plant-mine', PLANT_T - 10 * HOUR, followers=100)
    statuses['plant-at-t'] = synthetic_status(
        'plant-at-t', 'plant-at-t-author', PLANT_T - 5 * HOUR,
        followers=100)
    statuses['plant-reply-target'] = synthetic_status(
        'plant-reply-target', 'plant-reply-author', PLANT_T - 11 * HOUR,
        followers=100)
    statuses['plant-boost-target'] = synthetic_status(
        'plant-boost-target', 'plant-boost-author', PLANT_T - 9 * HOUR,
        followers=100)
    # The three edges the plan pins, each on the side nothing else
    # covers: a post created at exactly T is outside the candidate
    # window [T - 24 h, T); a post first seen engaged at exactly
    # T + 24 h is inside the label window (T, T + 24 h]; and one first
    # seen at exactly T + 1 h is outside the hour `labels_near_start`
    # counts.
    statuses['plant-created-at-t'] = synthetic_status(
        'plant-created-at-t', 'plant-created-at-t-author', PLANT_T,
        followers=100)
    statuses['plant-at-day-end'] = synthetic_status(
        'plant-at-day-end', 'plant-at-day-end-author', PLANT_T - 2 * HOUR,
        followers=100)
    statuses['plant-near-edge'] = synthetic_status(
        'plant-near-edge', 'plant-near-edge-author', PLANT_T - 3 * HOUR,
        followers=100)
    # And one just past the near hour, so the hour's upper edge is
    # covered too: a label first seen at T + 1 h + 1 min is outside the
    # hour, and a label at exactly T + 1 h is inside it.
    statuses['plant-past-near'] = synthetic_status(
        'plant-past-near', 'plant-past-near-author', PLANT_T - 3 * HOUR,
        followers=100)
    likes = {
        # favourited before T: not a candidate, and not a label either
        'plant-engaged': PLANT_T - 2 * HOUR,
        # favourited exactly at T. The bounds are asymmetric on purpose:
        # a candidate is what no observation *at or before* T shows
        # engaged, so this one is not a candidate; a label is what was
        # first seen *after* T, so it is not a label; and affinity reads
        # what was first seen *before* T, so it is not evidence either.
        'plant-at-t': PLANT_T,
        # first seen engaged half an hour after T: a label, inside the
        # hour `labels_near_start` counts, and inside the hour a one-hour
        # leak would read.
        'plant-leak': PLANT_T + 30 * MINUTE,
        # the evidence, which is the one thing the variants move
        'plant-leak-old': (PLANT_T - 2 * HOUR if evidence_before
                           else PLANT_T + 30 * MINUTE),
        # first seen engaged at exactly T + 24 h: the label window is
        # (T, T + 24 h], so this one is a label, and the last hour of
        # it is inside.
        'plant-at-day-end': PLANT_T + DAY,
        # first seen engaged at exactly T + 1 h: a label, and inside the
        # hour `labels_near_start` counts -- the bound is inclusive.
        'plant-near-edge': PLANT_T + HOUR,
        # first seen engaged one minute later: a label, and outside the
        # hour.
        'plant-past-near': PLANT_T + HOUR + MINUTE,
    }
    # Two of the reader's own statuses, on the two sides of T. The label
    # window is (T, T + 24 h]: a reply at exactly T is outside it, and a
    # boost an hour later is inside. Both are read from `own` and matched
    # by content id, which is the path a favourite never takes.
    own = [
        {'id': 'plant-reply-at-t',
         'created_at': PLANT_T.strftime('%Y-%m-%dT%H:%M:%SZ'),
         'in_reply_to_id': 'plant-reply-target',
         'in_reply_to_account_id': 'plant-reply-author'},
        {'id': 'plant-boost-after-t',
         'account': {'id': VIEWER_ID},
         'created_at': (PLANT_T + HOUR).strftime('%Y-%m-%dT%H:%M:%SZ'),
         'reblog': statuses['plant-boost-target']},
    ]
    return SyntheticWorld(statuses, likes, own)


REWRITE_LEAK_AUTHOR = 'rewrite-leak-author'
REWRITE_LEAK_POSTS = 6
# The chosen day, as an index into WORLD_START's days. Its date is
# 2026-10-07, so the days around it are judged too.
REWRITE_DAY = 2


def rewrite_world():
    """Five days where every post is liked half an hour after its T.

    The whole-future rewrite turns every observation after a chosen
    moment into an engaged one, counters and all. For the identity
    assertion to say anything, the compared day's *labels* must survive
    that: labels are each post's first engaged observation, so every
    candidate here is already engaged at T + 30 min, and the rewrite
    starts at T + 1 h. The run at T + 30 min survives, the minimum does
    not move, and the day's numbers cannot change -- unless a query reads
    one row too many, which now sees the run at T + 30 min where every
    candidate is engaged and loses its whole candidate list.

    One author, REWRITE_LEAK_AUTHOR, appears only on the chosen day and
    is liked only there. Its affinity exists only if the favourites
    written in (T, T + 1 h) are read, so the same leak shows up in
    `affinity_at` as six posts entering a top 20 they were outside of.
    """
    statuses, likes = {}, {}
    for index in range(WORLD_DAYS):
        moment = WORLD_START + index * DAY + EVALUATION_HOUR * HOUR
        liked_at = moment + 30 * MINUTE
        for slot in range(LIKED_PER_DAY):
            post_id = 'liked-%d-%d' % (index, slot)
            statuses[post_id] = synthetic_status(
                post_id, LIKED_AUTHOR,
                moment - 8 * HOUR + slot * 10 * MINUTE, followers=100)
            likes[post_id] = liked_at
        for slot in range(FILLERS_PER_DAY):
            post_id = 'filler-%d-%d' % (index, slot)
            statuses[post_id] = synthetic_status(
                post_id, 'filler-%d-%d' % (index, slot),
                moment - 7 * HOUR + (slot + 1) * 13 * MINUTE)
            likes[post_id] = liked_at
        if index == REWRITE_DAY:
            for slot in range(REWRITE_LEAK_POSTS):
                post_id = 'leak-%d' % slot
                statuses[post_id] = synthetic_status(
                    post_id, REWRITE_LEAK_AUTHOR,
                    moment - 9 * HOUR + slot * 5 * MINUTE)
                likes[post_id] = liked_at
    return SyntheticWorld(statuses, likes)


def day_orderings(connection, moment):
    """The affinity maps, the candidates and the five orderings at
    *moment* -- the same steps `evaluate` takes for a day, so a check that
    uses it checks the harness's own path, not a copy of it."""
    viewer, following, mutuals = stored_maps(connection)
    affinities = affinity_at(connection, moment, viewer, mutuals)
    candidates = candidates_at(connection, moment)
    signals = affinity.to_ranking_signals(affinities)
    ranked = orderings_at(
        candidates, moment,
        affinity.resolver(affinities, (), (), (),
                          int(moment.timestamp() * 1000)),
        signals, mutuals, following)
    return affinities, candidates, ranked


def evaluate_summary(root, path, gates_path):
    """`evaluate` on the database under *root*, as (summary, printed)."""
    printed = io.StringIO()
    evaluate(SYNTHETIC_INSTANCE, gates_path=gates_path, json_path=path,
             root=root, out=printed)
    with open(path, encoding='utf-8') as handle:
        return json.load(handle), printed.getvalue()


PRIVATE_KEYS = ('content', 'id', 'acct', 'url')


def private_keys(value, path=''):
    """Every key in *value* that names a post, an id, a handle or a URL."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PRIVATE_KEYS:
                found.append('%s/%s' % (path, key))
            found.extend(private_keys(item, '%s/%s' % (path, key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(private_keys(item, '%s[%d]' % (path, index)))
    return found


def private_values(value, forbidden, path=''):
    """Every string in *value* that names a post, an account or the host.

    The plan's privacy rule is written about key names; the design's
    (``Docs/timeline-algorithm.md`` §3.5 -- "shares only the metrics
    summary, never posts") is written about what a summary may carry at
    all. A key called ``first`` holding a post id satisfies the first
    rule and breaks the second, so both are checked.
    """
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(private_values(item, forbidden,
                                        '%s/%s' % (path, key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(private_values(item, forbidden,
                                        '%s[%d]' % (path, index)))
    elif isinstance(value, str):
        for token in forbidden:
            if token and token in value:
                found.append('%s: %r' % (path, value))
                break
    return found


def world_tokens(world):
    """Every post id, author id, handle and the URL of *world*'s host."""
    host = urllib.parse.urlsplit(SYNTHETIC_INSTANCE).hostname
    # The bare handle needs no token of its own: `synth` is a substring
    # of `@synth@synth.example`, and the check looks for substrings.
    tokens = {SYNTHETIC_INSTANCE, VIEWER_ID,
              '@%s@%s' % (VIEWER_ACCT, host)}
    for post_id, status in world.statuses.items():
        account = status['account']
        tokens.add(post_id)
        tokens.add(account['id'])
        tokens.add('@%s@%s' % (account['acct'], host))
    return tokens


# The gates of §4, as a fixture file. The harness reads them through
# `load_gates` like any other, so the checks exercise the path the real
# run takes. The frozen file is the maintainer's; this only pins the keys
# it must carry.
FIXTURE_GATES = {
    'version': 1,
    'alpha': 0.05,
    'min_nontied_days': 11,
    'a1_k': 20,
    'a1_min_mean_ratio': 1.25,
    'a2_k': 20,
    'a2_max_author_drop': 2,
    'a2_min_small_share_ratio': 0.8,
    'min_accounts_passing': 2,
}


def fixture_rows(scores, second=None, breadth=None):
    """Rows `judge` can read: one a day, each a mapping of ordering to
    recall@20.

    ``second`` overrides recall@50 per ordering. The gates must read
    ``a1_k``'s metric, and two metrics that always agreed would let them
    read either one.

    ``breadth`` overrides ``(authors20, small20)`` per ordering -- the
    two numbers A2 reads. ``None`` stands for a mean that is missing, so
    A2's INCONCLUSIVE branch can be reached.

    Every other metric is held equal, so a verdict that moves is the one
    the gate decided.
    """
    rows = []
    for index, score in enumerate(scores):
        orderings = {}
        for name in ORDERINGS:
            value = score.get(name, 0.0)
            authors, share = (breadth or {}).get(name, (20, 0.5))
            orderings[name] = {
                'recall20': value,
                'recall50': (second or {}).get(name, value),
                'authors20': authors, 'small20': share, 'overlap20': 20}
        rows.append({'date': '2026-09-%02d' % (index + 1),
                     't': '2026-09-%02dT09:00:00.000000Z' % (index + 1),
                     'candidates': 25, 'labels': 5, 'labels_near_start': 0,
                     'orderings': orderings})
    return rows


def summary_record(gate_a, ordering, use_affinity, shipped, a1, a2):
    """One account's record, as `evaluate --json` writes it."""
    return {'version': 1, 'days': [], 'means': {}, 'gates': {},
            'gate_a': gate_a, 'ordering': ordering,
            'use_affinity': use_affinity, 'shipped': shipped,
            'shipped_a1': a1, 'shipped_a2': a2}


def write_json(path, value):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(value, handle)
    return path


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


def check_metrics(checks):
    """Recall@k and the other four metrics, on an ordering worked out by
    hand.

    The labels sit on the boundaries themselves: index 19 is the last
    post inside the top 20 and 20 the first outside it, and index 49 is
    the last inside the top 50. A cut one short or one long moves a
    label, so `ids[:k-1]`, `ids[:k]` and `ids[:k+1]` give three different
    answers. The row is longer than 50 posts, or the top 50 would hold
    every post and its cut could not be reached.
    """
    pin_zone()
    posts = [synthetic_status('p%02d' % index, 'author-%02d' % index,
                              WORLD_START) for index in range(60)]
    posts[1]['account']['followers_count'] = 10
    posts[59]['account']['followers_count'] = 10
    small = small_authors(posts)
    checks.equal('the small-account set is the two authors below the median',
                 sorted(small), ['author-01', 'author-59'])
    row = metrics_at(posts, {'p02', 'p19', 'p20', 'p49'}, small,
                     {'p00', 'p01', 'p59'})
    checks.equal('recall@20 counts index 19 and not index 20, so 2 of 4',
                 row['recall20'], 2 / 4)
    checks.equal('recall@50 counts index 49, so all 4',
                 row['recall50'], 4 / 4)
    checks.equal('the top 20 holds 20 distinct authors', row['authors20'], 20)
    checks.equal('1 of 20 is a small account, so the share is 0.05',
                 row['small20'], 0.05)
    checks.equal('2 of the top 20 are in the engagement order',
                 row['overlap20'], 2)
    checks.equal('a day with no labels scores no recall',
                 metrics_at(posts, set(), small, set())['recall20'], None)


def check_sign_test(checks):
    """The anchors §4 pins, to five decimal places."""
    checks.close('the sign test gives p = 0.02869 for 11 of 14',
                 sign_test(11, 14), 0.02869, 1e-5)
    checks.close('and p = 0.08978 for 10 of 14',
                 sign_test(10, 14), 0.08978, 1e-5)
    checks.equal('no non-tied days at all is p = 1', sign_test(0, 0), 1.0)


def check_gates(checks, work):
    """PASS, FAIL and INCONCLUSIVE on a fixture gates file, and the rule
    that the ordering which ships must pass on its own numbers.

    Each gate is asserted on the fixture where *its own* condition is the
    only thing that can move the verdict. That is what separates the four
    baselines the plan names: a fixture where `chrono`, `rank_null` and
    `affinity_chrono` score alike cannot tell A3 from A4, and one where
    `recall20` and `recall50` agree cannot tell the metric the gates read.
    """
    pin_zone()
    gates_path = write_json(os.path.join(work, 'fixture-gates.json'),
                            FIXTURE_GATES)
    gates = load_gates(gates_path)
    checks.equal('the fixture gates file reads back as written',
                 gates, FIXTURE_GATES)
    frozen = load_gates()
    checks.check('the frozen gates file is there, with the same keys',
                 frozen is not None and set(frozen) == set(FIXTURE_GATES))

    winning = [{'rank_affinity': 0.8 if day < 11 else 0.4,
                'rank_null': 0.4, 'chrono': 0.4, 'affinity_chrono': 0.4}
               for day in range(14)]
    decision = judge(fixture_rows(winning), gates)
    checks.equal('A1 passes on 11 wins out of 14',
                 decision['a1']['verdict'], 'PASS')
    checks.equal('its sample is the 11 days that were not ties',
                 decision['a1']['nontied'], 11)
    checks.equal('and its p is the sign test of 11 wins in 11 days',
                 decision['a1']['p'], sign_test(11, 11))
    checks.equal('A2 passes when the top 20 keeps its authors and its'
                 ' small-account share', decision['a2']['verdict'], 'PASS')
    checks.equal('A3 passes: affinity beats no affinity',
                 decision['a3']['verdict'], 'PASS')
    checks.equal('A4 passes: the ranker beats affinity-then-chrono',
                 decision['a4']['verdict'], 'PASS')
    checks.equal('the ordering that was checked in its own right is rank'
                 ' with affinity', decision['shipped'], 'rank_affinity')
    checks.equal('Gate A passes, shipping rank with affinity',
                 (decision['gate_a'], decision['ordering'],
                  decision['use_affinity']), ('PASS', 'rank', True))

    # A2: the two numbers it reads, each its own way of failing, and the
    # missing mean it cannot decide. Without these, deleting A2's
    # condition leaves every verdict where it was.
    same_order = [{'rank_affinity': 0.8 if day < 11 else 0.4,
                   'rank_null': 0.4, 'chrono': 0.4, 'affinity_chrono': 0.4}
                  for day in range(14)]

    def breadth_of(rank_authors, rank_share):
        return judge(fixture_rows(
            same_order,
            breadth={'rank_affinity': (rank_authors, rank_share)}), gates)

    checks.equal('A2 allows a drop of exactly the two authors the gates'
                 ' name', breadth_of(18, 0.5)['a2']['verdict'], 'PASS')
    checks.equal('and a small-account share of exactly 0.8x',
                 breadth_of(20, 0.4)['a2']['verdict'], 'PASS')
    narrow = breadth_of(17, 0.5)
    checks.equal('A2 fails when the top 20 loses three authors',
                 narrow['a2']['verdict'], 'FAIL')
    checks.equal('and Gate A fails on A2 alone, A1 having passed',
                 (narrow['a1']['verdict'], narrow['gate_a']),
                 ('PASS', 'FAIL'))
    checks.equal('A2 fails when the small-account share is below 0.8x',
                 breadth_of(20, 0.3)['a2']['verdict'], 'FAIL')
    blind = breadth_of(None, None)
    checks.equal('A2 is INCONCLUSIVE when a mean is missing',
                 blind['a2']['verdict'], 'INCONCLUSIVE')
    checks.equal('and Gate A is INCONCLUSIVE, not a pass',
                 (blind['a1']['verdict'], blind['gate_a']),
                 ('PASS', 'INCONCLUSIVE'))

    # A2 is measured against *chrono*, not against rank_null. The two
    # must be told apart, or the gate could be scored on the wrong
    # baseline and nobody would know. So one fixture, with the two
    # baselines given different breadths: the verdict it reaches says
    # which one the gate read.
    apart = judge(fixture_rows(
        same_order,
        breadth={'rank_affinity': (18, 0.5), 'chrono': (15, 0.4),
                 'rank_null': (20, 0.5)}), gates)
    checks.equal('A2 measures against chrono, not rank_null: it passes on'
                 ' chrono\'s breadth',
                 apart['a2']['verdict'], 'PASS')
    checks.equal('and chrono\'s breadth is the one it read',
                 (apart['a2']['chrono_authors'], apart['a2']['chrono_small_share']),
                 (15, 0.4))

    # A1 is scored on recall@`a1_k`, and only on it. Here the ranker wins
    # every day on recall@20 and loses every day on recall@50.
    crossed = [{'rank_affinity': 0.8, 'rank_null': 0.0, 'chrono': 0.4,
                'affinity_chrono': 0.0} for _day in range(14)]
    decision = judge(fixture_rows(
        crossed, second={'rank_affinity': 0.2, 'chrono': 0.6}), gates)
    checks.equal('A1 reads recall@a1_k: the other metric turns every win'
                 ' into a loss',
                 (decision['a1']['verdict'], decision['a1']['wins']),
                 ('PASS', 14))

    # A1's two halves: the sign test alone can pass while the mean-ratio
    # floor fails, and chrono can recall nothing at all.
    thin_gain = judge(fixture_rows(
        [{'rank_affinity': 0.45, 'rank_null': 0.4, 'chrono': 0.4,
          'affinity_chrono': 0.4} for _day in range(14)]), gates)['a1']
    checks.check('A1 fails on the mean ratio alone: %r with %d wins, p=%r'
                 % (thin_gain['mean_ratio'], thin_gain['wins'],
                    thin_gain['p']),
                 thin_gain['verdict'] == 'FAIL'
                 and thin_gain['wins'] == 14
                 and thin_gain['p'] <= gates['alpha']
                 and thin_gain['mean_ratio'] < gates['a1_min_mean_ratio'])
    nothing = judge(fixture_rows(
        [{'rank_affinity': 0.2, 'rank_null': 0.0, 'chrono': 0.0,
          'affinity_chrono': 0.0} for _day in range(14)]), gates)['a1']
    checks.equal('A1 passes when chrono recalled nothing and rank did not',
                 nothing['verdict'], 'PASS')
    checks.equal('and its mean ratio is undefined, not a division by zero',
                 nothing['mean_ratio'], None)
    # The floor is a *minimum*: a ratio of exactly 1.25 passes. A gate
    # that asked for strictly more would ship a stricter rule than the
    # frozen file names.
    exactly = judge(fixture_rows(
        [{'rank_affinity': 0.5, 'rank_null': 0.4, 'chrono': 0.4,
          'affinity_chrono': 0.4} for _day in range(14)]), gates)['a1']
    checks.equal('A1 accepts a mean ratio of exactly the minimum',
                 (exactly['verdict'], exactly['mean_ratio']),
                 ('PASS', gates['a1_min_mean_ratio']))

    losing = [{'rank_affinity': 0.4, 'rank_null': 0.2, 'chrono': 0.5,
               'affinity_chrono': 0.2} for _day in range(14)]
    decision = judge(fixture_rows(losing), gates)
    checks.equal('A1 fails when the ranker loses to chrono',
                 decision['a1']['verdict'], 'FAIL')
    checks.equal('and Gate A fails with it', decision['gate_a'], 'FAIL')

    few = [{'rank_affinity': 0.8, 'rank_null': 0.4, 'chrono': 0.4,
            'affinity_chrono': 0.4} for _day in range(5)]
    decision = judge(fixture_rows(few), gates)
    checks.equal('A1 is INCONCLUSIVE below min_nontied_days',
                 decision['a1']['verdict'], 'INCONCLUSIVE')
    checks.equal('and Gate A is INCONCLUSIVE: not a pass, not a fail',
                 decision['gate_a'], 'INCONCLUSIVE')
    checks.equal('A4 below min_nontied_days is INCONCLUSIVE too',
                 decision['a4']['verdict'], 'INCONCLUSIVE')
    checks.equal('and an inconclusive A4 ships the simple ordering',
                 decision['ordering'], 'affinity_chrono')

    # A3 asks whether affinity beats *no affinity*; its baseline is
    # `rank_null`, not `chrono`. Level with rank_null is INCONCLUSIVE
    # however far behind chrono the ranker leaves it.
    level = [{'rank_affinity': 0.8, 'rank_null': 0.8, 'chrono': 0.2,
              'affinity_chrono': 0.2} for _day in range(14)]
    decision = judge(fixture_rows(level), gates)
    checks.equal('A3 compares against rank_null: level with it is'
                 ' INCONCLUSIVE', decision['a3']['verdict'], 'INCONCLUSIVE')
    checks.equal('so affinity is not used, though chrono trails by 0.6',
                 decision['use_affinity'], False)
    checks.equal('and the ordering that ships is rank without affinity',
                 decision['shipped'], 'rank_null')

    # A4 asks whether rank() beats *affinity, then chronological*; its
    # baseline is `affinity_chrono`, not `chrono`. Trailing
    # affinity_chrono while beating chrono is what tells them apart.
    overtaken = [{'rank_affinity': 0.5, 'rank_null': 0.1, 'chrono': 0.4,
                  'affinity_chrono': 0.9} for _day in range(14)]
    decision = judge(fixture_rows(overtaken), gates)
    checks.equal('A4 compares against affinity_chrono, so beating chrono'
                 ' is not enough', decision['a4']['verdict'], 'FAIL')
    checks.equal('and hands the ordering over', decision['ordering'],
                 'affinity_chrono')

    # A4 did not select `rank`, so `affinity_chrono` is what ships -- and
    # it fails A1 against chrono on its own numbers. Gate A must be FAIL
    # even though the ranker's A3 verdict was better.
    handed_over = [{'rank_affinity': 0.4, 'rank_null': 0.4, 'chrono': 1.0,
                    'affinity_chrono': 0.7} for _day in range(14)]
    decision = judge(fixture_rows(handed_over), gates)
    checks.equal('A4 hands the ordering to affinity-then-chrono',
                 (decision['a4']['verdict'], decision['ordering']),
                 ('FAIL', 'affinity_chrono'))
    checks.equal('which then fails A1 on its own numbers',
                 decision['shipped_a1']['verdict'], 'FAIL')
    checks.equal('so Gate A is FAIL, not a pass on another ordering',
                 decision['gate_a'], 'FAIL')
    record_text = io.StringIO()
    print_gates(decision, record_text)
    printed = record_text.getvalue()
    checks.check('the H1 record says Gate A: FAIL', 'Gate A: FAIL' in printed)
    checks.check('and names affinity-then-chrono as the ordering',
                 'ORDERING: affinity_chrono' in printed)
    checks.check('and leaves affinity off', 'USE_AFFINITY: false' in printed)

    # The mirror image: the ranker fails A1, affinity_chrono passes it,
    # and A4 hands the ordering over. Consulting the ranker's verdicts
    # instead of the shipped ordering's would fail Gate A.
    saved = [{'rank_affinity': 0.2, 'rank_null': 0.1, 'chrono': 0.4,
              'affinity_chrono': 0.9} for _day in range(14)]
    decision = judge(fixture_rows(saved), gates)
    checks.equal('the ranker fails A1 here', decision['a1']['verdict'],
                 'FAIL')
    checks.equal('while affinity-then-chrono passes it',
                 decision['shipped_a1']['verdict'], 'PASS')
    checks.equal('so Gate A is PASS on the shipped ordering\'s numbers',
                 (decision['shipped'], decision['gate_a']),
                 ('affinity_chrono', 'PASS'))

    # The same, for A2: the ordering that ships is too narrow in the top
    # 20, while the ranker is broad enough. Consulting the ranker's A2
    # instead of the shipped ordering's would pass Gate A on the wrong
    # ordering's breadth -- and `aggregate` counts an account by the
    # verdicts it shipped.
    saved_narrow = [{'rank_affinity': 0.2, 'rank_null': 0.1, 'chrono': 0.4,
                     'affinity_chrono': 0.9} for _day in range(14)]
    decision = judge(fixture_rows(
        saved_narrow, breadth={'affinity_chrono': (17, 0.5),
                               'rank_affinity': (20, 0.5)}), gates)
    checks.equal('the shipped ordering fails A2 on its own breadth',
                 decision['shipped_a2']['verdict'], 'FAIL')
    checks.equal('while the ranker passes it',
                 decision['a2']['verdict'], 'PASS')
    checks.equal('so Gate A is FAIL, not a pass on the rankers breadth',
                 decision['gate_a'], 'FAIL')

    passed = summary_record('PASS', 'rank', True, 'rank_affinity',
                            'PASS', 'PASS')
    failed = summary_record('FAIL', 'rank', True, 'rank_affinity',
                            'FAIL', 'PASS')
    # A record that says it shipped one ordering but was judged as another
    # is exactly the hole the review of T1.2 found: a majority must not
    # name an ordering that no account shipped and passed.
    stray = summary_record('PASS', 'rank', True, 'affinity_chrono',
                           'PASS', 'PASS')
    paths = {
        'one': write_json(os.path.join(work, 'account-one.json'), passed),
        'two': write_json(os.path.join(work, 'account-two.json'),
                          dict(passed)),
        'failing': write_json(os.path.join(work, 'account-failing.json'),
                              failed),
        'stray': write_json(os.path.join(work, 'account-stray.json'), stray),
    }
    printed = io.StringIO()
    aggregate([paths['one'], paths['two']], gates_path=gates_path, out=printed)
    checks.check('two accounts that shipped the ordering and passed make'
                 ' Gate A PASS', 'Gate A: PASS' in printed.getvalue())
    printed = io.StringIO()
    aggregate([paths['one'], paths['failing']], gates_path=gates_path,
              out=printed)
    checks.check('one account failing makes it FAIL',
                 'Gate A: FAIL' in printed.getvalue())
    printed = io.StringIO()
    aggregate([paths['stray']], gates_path=gates_path, out=printed)
    checks.check('a record that shipped another ordering cannot make it PASS',
                 'Gate A: INCONCLUSIVE' in printed.getvalue())

    # The same account twice is one account counted twice, and the gate
    # would read it as two.
    printed = io.StringIO()
    code = aggregate([paths['one'], paths['one']], gates_path=gates_path,
                     out=printed)
    checks.equal('the same summary given twice is refused',
                 (code, 'Gate A: PASS' in printed.getvalue()), (2, False))
    return gates_path


def check_split(checks, work):
    """A post engaged before T is not a candidate, an engagement after T
    does not reach the affinity at T -- and the pair of databases shows
    that reading it *would* change the top 20.

    The runs are placed on the boundaries themselves, not between them: a
    run at exactly T and another half an hour later are what tell `<= T`
    from `< T` and `< T` from `< T + 1 h`. A world that only sampled T -
    2 h and T + 2 h would leave all three of those cuts looking alike.
    """
    pin_zone()
    answers = {}
    moments = (PLANT_T - 13 * HOUR, PLANT_T - 2 * HOUR, PLANT_T,
               PLANT_T + 30 * MINUTE, PLANT_T + HOUR, PLANT_T + HOUR + MINUTE,
               PLANT_T + 2 * HOUR, PLANT_T + DAY)
    for name, evidence_before in (('bounded', False), ('control', True)):
        root = os.path.join(work, 'split-' + name)
        record(root, plant_world(evidence_before), moments)
        connection = open_database(database_path(SYNTHETIC_INSTANCE, root))
        try:
            affinities, candidates, ranked = day_orderings(connection, PLANT_T)
            labels, near = labels_at(connection, PLANT_T, candidates)
            seen = connection.execute(
                'SELECT post_id, MIN(run_ts) FROM observations'
                ' WHERE favourited OR reblogged OR bookmarked'
                ' GROUP BY post_id').fetchall()
            # The label window's lower cut on its own. At T the post
            # engaged exactly at T is already excluded from the
            # candidates, so its `seen == T` case is never reached there;
            # a candidate list taken half an hour earlier does include
            # it, and "after T" must still exclude it.
            earlier = labels_at(
                connection, PLANT_T,
                candidates_at(connection, PLANT_T - 30 * MINUTE))[0]
            near_set = near_labels(connection, PLANT_T, candidates)
        finally:
            connection.close()
        answers[name] = {
            'author': affinities.author,
            'candidates': [str(status.get('id')) for status in candidates],
            'top': [str(status.get('id'))
                    for status in ranked['rank_affinity'][:20]],
            'labels': labels,
            'near': near,
            'near_labels': near_set,
            'first': dict(seen),
            'earlier_labels': earlier,
        }

    bounded, control = answers['bounded'], answers['control']
    checks.check('a post engaged before T is not a candidate',
                 'plant-engaged' not in bounded['candidates'])
    checks.check('a post engaged only after T is a candidate',
                 'plant-leak' in bounded['candidates'])
    checks.check('a post first engaged exactly at T is not a candidate:'
                 ' "at or before T" includes T',
                 'plant-at-t' not in bounded['candidates'])
    checks.check('a favourite made after T labels the post',
                 'plant-leak' in bounded['labels'])
    checks.check('a favourite first seen exactly at T is not a label:'
                 ' the window opens after T',
                 'plant-at-t' not in bounded['earlier_labels'])
    checks.check('a boost after T labels the post it boosted',
                 'plant-boost-target' in bounded['labels'])
    checks.check('a reply at exactly T does not: the window opens after it',
                 'plant-reply-target' not in bounded['labels'])
    checks.equal('two labels fall within the hour that blurs a flag: the'
                 ' one liked half an hour after T, and the one liked at'
                 ' exactly T + 1 h, whose bound is inclusive',
                 bounded['near'], 2)
    # The three edges the plan pins, each on the side nothing else
    # covers. The candidate window is [T - 24 h, T), so a post created at
    # exactly T is outside it; the label window is (T, T + 24 h], so a
    # post first seen engaged at exactly T + 24 h is inside it; and the
    # near count is the first hour after T, so a post first seen at
    # exactly T + 1 h is outside that.
    checks.check('a post created at exactly T is not a candidate: the'
                 ' window is [T - 24 h, T)',
                 'plant-created-at-t' not in bounded['candidates'])
    checks.check('a post first seen engaged at exactly T + 24 h is a label:'
                 ' the window is (T, T + 24 h]',
                 'plant-at-day-end' in bounded['labels'])
    checks.check('a post first seen engaged at exactly T + 1 h is a label'
                 ' and inside the hour that blurs a flag: the bound is'
                 ' inclusive',
                 'plant-near-edge' in bounded['labels']
                 and 'plant-near-edge' in bounded['near_labels'])
    checks.check('and one first seen a minute later is a label but outside'
                 ' that hour',
                 'plant-past-near' in bounded['labels']
                 and 'plant-past-near' not in bounded['near_labels'])
    checks.check('an engagement after T does not reach the affinity at T',
                 PLANT_LEAK not in bounded['author'])
    checks.check('nor does one first seen exactly at T',
                 'plant-at-t-author' not in bounded['author'])
    checks.check('the same engagement before T does reach it',
                 PLANT_LEAK in control['author'])
    checks.check('so the leaked author\'s post stays outside the top 20',
                 'plant-leak' not in bounded['top'])
    checks.check("and enters it when the favourite is moved before T",
                 'plant-leak' in control['top'])
    checks.check('which is what a query that read one row too many would do',
                 bounded['top'] != control['top'])
    # The premise the cuts rest on: the leak's engagement is first seen
    # inside the hour after T, so a bound of T + 1 h reads it and a bound
    # of T does not.
    leaked_at = parse_moment(bounded['first'].get('plant-leak'))
    checks.check('the leak is first seen at %s, inside (T, T + 1 h]'
                 % iso_utc(leaked_at) if leaked_at else
                 'the leak was never seen engaged',
                 leaked_at is not None
                 and PLANT_T < leaked_at <= PLANT_T + HOUR)


def check_collect(checks, work):
    """`collect` through the fake: what a run writes, what a second run
    adds, and how a rate limit or a failed request stops it."""
    pin_zone()
    world = synthetic_world()
    root = os.path.join(work, 'collect')
    # The first day's 06:00: by then all 31 of its posts exist, and none
    # of them carries a favourite yet.
    moment = WORLD_START + EVALUATION_HOUR * HOUR

    def run(when, printed, where=None, **client):
        return collect(
            SYNTHETIC_INSTANCE,
            make_client=lambda _instance, _token: FakeClient(
                world, when, **client),
            token='synthetic', now=when, root=where or root, out=printed)

    printed = io.StringIO()
    checks.equal('a run exits 0', run(moment, printed), 0)
    checks.equal(
        'the run line counts the posts stored, the new ones, the requests,'
        ' and whether it is partial',
        printed.getvalue().strip(),
        '%s posts=31 new=31 requests=6 partial=False' % iso_utc(moment))
    checks.equal('the data directory is 0700',
                 stat.S_IMODE(os.stat(
                     data_dir(SYNTHETIC_INSTANCE, root)).st_mode), 0o700)
    checks.equal('and the database is 0600',
                 stat.S_IMODE(os.stat(database_path(
                     SYNTHETIC_INSTANCE, root)).st_mode), 0o600)

    same_second = moment + datetime.timedelta(microseconds=500000)
    printed = io.StringIO()
    checks.equal('a second run in the same second exits 0',
                 run(same_second, printed), 0)
    # Five requests, not six: every account is known now, so the run has
    # no relationship to ask about.
    checks.equal('and finds no new post or account to ask about',
                 printed.getvalue().strip(),
                 '%s posts=31 new=0 requests=5 partial=False'
                 % iso_utc(same_second))
    checks.equal('so there are two runs and one row per post',
                 stored_rows(root, 'SELECT (SELECT COUNT(*) FROM runs),'
                                  ' (SELECT COUNT(*) FROM posts)'), (2, 31))
    checks.equal('and each run keeps its own observations',
                 stored_rows(root, 'SELECT (SELECT COUNT(DISTINCT run_ts)'
                                  ' FROM observations),'
                                  ' (SELECT COUNT(*) FROM observations)'),
                 (2, 62))

    limited = os.path.join(work, 'collect-limited')
    printed = io.StringIO()
    checks.equal('a rate-limited run exits 0',
                 run(moment, printed, where=limited, limited=True), 0)
    checks.equal('it stops before the first request, and says why',
                 printed.getvalue().strip(),
                 '%s posts=0 new=0 requests=0 partial=True'
                 % iso_utc(moment))
    checks.equal('the run row records the rate limit',
                 stored_rows(limited, 'SELECT requests, partial, error'
                                     ' FROM runs'),
                 (0, 1, 'rate limited; retry after 30s'))

    failed = os.path.join(work, 'collect-failed')
    printed = io.StringIO()
    checks.equal('a run whose request fails exits 0',
                 run(moment, printed, where=failed,
                     fail_at='/favourites'), 0)
    checks.equal('and keeps what it gathered before the failure',
                 printed.getvalue().strip(),
                 '%s posts=31 new=31 requests=4 partial=True'
                 % iso_utc(moment))
    checks.equal('the run row names the failure',
                 stored_rows(failed, 'SELECT requests, partial, error'
                                    ' FROM runs'),
                 (4, 1, 'the request failed'))
    checks.equal('and the posts gathered before it are stored',
                 stored_rows(failed, 'SELECT COUNT(*) FROM posts')[0], 31)

    class ListBody(FakeClient):
        """Answers the account endpoint with a one-element list."""

        def _answer(self, path, params):
            if path == '/accounts/verify_credentials':
                return [{'id': VIEWER_ID}]
            return super()._answer(path, params)

    gathered, requests = gather(ListBody(world, WORLD_START))
    checks.equal('the account body is read as the object it is, not a list',
                 gathered['error'], 'verify_credentials returned no account')
    checks.equal('and the run stops on its first request', requests, 1)


def check_accounts(checks):
    """The managed-account commands, with no server and no keyring.

    Each command is run twice where it can fail: once accepted, once
    refused. The refusals are what a real server answers with, built as
    the client builds them -- a ``GLib.Error`` whose code is the HTTP
    status -- so a path that would print a traceback instead of a line
    fails here. Every check also asserts the token is absent from both
    streams, because a token in an error message is the leak that matters
    most.
    """
    pin_zone()
    secret = 'synthetic-secret-token'

    def run(function, *args, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = function(*args, **kwargs)
        printed, errors = out.getvalue(), err.getvalue()
        checks.check('no token reaches the streams of %s'
                     % getattr(function, '__name__', function),
                     secret not in printed + errors)
        return code, printed, errors

    # --- tokens in -----------------------------------------------------
    stored = {}

    def store(instance, account_id, token):
        stored.update(instance=instance, account=account_id, token=token)

    code, printed, errors = run(
        store_token, SYNTHETIC_INSTANCE, account_id=VIEWER_ID, token=secret,
        make_client=lambda _instance, _token: FakeAccountClient(),
        store_secret=store)
    checks.equal('a verified token is stored under the id the server names',
                 (code, stored.get('account')), (0, VIEWER_ID))
    checks.check('the success line names the account, not the token',
                 'token stored for account %s' % VIEWER_ID in printed)
    checks.equal('and the token is nowhere in the output',
                 secret in printed + errors, False)

    stored.clear()
    code, printed, errors = run(
        store_token, SYNTHETIC_INSTANCE, account_id='someone-else',
        token=secret,
        make_client=lambda _instance, _token: FakeAccountClient(),
        store_secret=store)
    checks.equal('a token under an id the server does not name is refused,'
                 ' and nothing is stored', (code, stored), (2, {}))
    checks.check('and the line names both ids',
                 'belongs to account %s' % VIEWER_ID in errors
                 and 'someone-else' in errors)
    checks.equal('and that path does not print the token either',
                 secret in printed + errors, False)

    stored.clear()
    code, printed, errors = run(
        store_token, SYNTHETIC_INSTANCE, account_id=VIEWER_ID, token=secret,
        make_client=lambda _instance, _token: FakeAccountClient(
            refuse={'verify': 401}),
        store_secret=store)
    checks.equal('a refused token exits 2 and stores nothing',
                 (code, stored), (2, {}))
    checks.check('and the line says the token was refused, with the status',
                 'token refused (401)' in errors)
    checks.equal('and the refused token is not printed',
                 secret in printed + errors, False)

    def no_keyring(_instance, _account_id, _token):
        raise GLib.Error('no secret service', 'secret-error-quark', 0)

    code, printed, errors = run(
        store_token, SYNTHETIC_INSTANCE, account_id=VIEWER_ID, token=secret,
        make_client=lambda _instance, _token: FakeAccountClient(),
        store_secret=no_keyring)
    checks.equal('a keyring that will not take the token exits 2', code, 2)
    checks.check('and says the keyring refused it',
                 'cannot write the keyring: no secret service' in errors)
    checks.equal('and that path does not print the token either',
                 secret in printed + errors, False)

    # --- engagement ----------------------------------------------------
    kinds = {}
    for kind in ('favourite', 'reblog', 'bookmark'):
        fake = FakeAccountClient()
        code, printed, errors = run(
            engage, SYNTHETIC_INSTANCE, 'post-1', kind, token=secret,
            make_client=lambda _instance, _token, fake=fake: fake)
        kinds[kind] = (code, [call for call in fake.asked
                              if call[0] != 'verify'],
                       secret in printed + errors)
    checks.equal('each engagement is one call of its own',
                 kinds, {kind: (0, [(kind, 'post-1')], False)
                         for kind in ('favourite', 'reblog', 'bookmark')})

    fake = FakeAccountClient()
    code, printed, errors = run(
        engage, SYNTHETIC_INSTANCE, 'post-2', 'reply', text='hello',
        visibility='private', token=secret,
        make_client=lambda _instance, _token: fake)
    checks.equal('a reply carries its parent, its text and its visibility',
                 [call for call in fake.asked if call[0] == 'reply'],
                 [('reply', ('post-2', 'hello',
                             {'visibility': 'private'}))])
    checks.check('and the line names the reply and the target',
                 code == 0 and 'reply target=post-2' in printed)

    fake = FakeAccountClient()
    code, printed, errors = run(
        engage, SYNTHETIC_INSTANCE, 'post-2', 'reply', token=secret,
        make_client=lambda _instance, _token: fake)
    checks.equal('a reply without text stops before the server',
                 (code, 'a reply needs --text' in errors,
                  [call for call in fake.asked if call[0] == 'reply']),
                 (2, True, []))

    fake = FakeAccountClient()
    code, printed, errors = run(
        engage, SYNTHETIC_INSTANCE, 'post-5', 'reblog', visibility='private',
        token=secret, make_client=lambda _instance, _token: fake)
    checks.equal('a reblog carries its visibility',
                 (fake.asked_visibility.get('reblog'),
                  [call for call in fake.asked if call[0] == 'reblog']),
                 ('private', [('reblog', 'post-5')]))

    fake = FakeAccountClient(refuse={'favourite': 403})
    code, printed, errors = run(
        engage, SYNTHETIC_INSTANCE, 'post-3', 'favourite', token=secret,
        make_client=lambda _instance, _token: fake)
    checks.equal('a refused favourite exits 1, not with a traceback',
                 code, 1)
    checks.check('and the line names what was refused, with the status',
                 'favourite refused (403): favourite refused by the test'
                 ' server' in errors)

    saved = os.environ.pop(TOKEN_VARIABLE, None)
    try:
        code, printed, errors = run(
            engage, SYNTHETIC_INSTANCE, 'post-4', 'favourite',
            make_client=_NoNetwork)
    finally:
        if saved is not None:
            os.environ[TOKEN_VARIABLE] = saved
    checks.equal('without a token the command stops before building a client',
                 code, 2)
    checks.check('and says where a token comes from', 'no token:' in errors)

    # --- following -----------------------------------------------------
    fake = FakeAccountClient(following_count=1,
                             follows=['a1', 'a2', 'a3', 'a4'],
                             following=['a1'], home=[{'id': 'p1'}])
    code, printed, errors = run(
        follow, SYNTHETIC_INSTANCE, from_account='source', token=secret,
        make_client=lambda _instance, _token: fake)
    checks.equal('a follow run follows only what is not followed yet',
                 [call for call in fake.asked if call[0] == 'follow'],
                 [('follow', 'a2'), ('follow', 'a3'), ('follow', 'a4')])
    checks.check('and reports the count the server then gives',
                 code == 0 and 'following=4' in printed
                 and 'followed=3' in printed and 'already=1' in printed)
    checks.check('and shows the home timeline it can now see',
                 'home=1' in printed)

    # `older=` says the home timeline has more than its first page, and
    # the follow command prints it too.
    paged = FakeAccountClient(following_count=1, follows=['a1'],
                              home=[{'id': 'p1'}],
                              next_pages=[('/timelines/home', {'max_id': 'p1'})])
    code, printed, errors = run(
        follow, SYNTHETIC_INSTANCE, from_account='source', token=secret,
        make_client=lambda _instance, _token: paged)
    checks.check('a follow run says when the home timeline has more',
                 code == 0 and 'older=True' in printed)

    fake = FakeAccountClient(follows=['b1', 'b2', 'b3', 'b4'])
    code, printed, errors = run(
        follow, SYNTHETIC_INSTANCE, from_account='source', limit=2,
        token=secret, make_client=lambda _instance, _token: fake)
    checks.equal('--limit stops after that many follows',
                 (code, [call for call in fake.asked if call[0] == 'follow']),
                 (0, [('follow', 'b1'), ('follow', 'b2')]))

    fake = FakeAccountClient(follows=['e1', 'e2'], requested=['e1'])
    code, printed, errors = run(
        follow, SYNTHETIC_INSTANCE, from_account='source', token=secret,
        make_client=lambda _instance, _token: fake)
    checks.equal('a follow request already pending is not sent again',
                 [call for call in fake.asked if call[0] == 'follow'],
                 [('follow', 'e2')])

    # The account must not follow itself. Mastodon refuses the request,
    # so the run would carry a refusal it had no reason to make.
    fake = FakeAccountClient(follows=[VIEWER_ID, 'f1'])
    code, printed, errors = run(
        follow, SYNTHETIC_INSTANCE, from_account='source', token=secret,
        make_client=lambda _instance, _token: fake)
    checks.equal('the account does not follow itself',
                 [call for call in fake.asked if call[0] == 'follow'],
                 [('follow', 'f1')])

    fake = FakeAccountClient(follows=['c1', 'c2'], refuse={'follow': 403})
    code, printed, errors = run(
        follow, SYNTHETIC_INSTANCE, from_account='source', token=secret,
        make_client=lambda _instance, _token: fake)
    checks.equal('a refused follow exits 1, after trying the rest', code, 1)
    checks.check('and every refusal names its account and the status',
                 errors.count('follow of account') == 2
                 and 'refused (403)' in errors)
    checks.check('and the report counts the refusals',
                 'refused=2' in printed)

    fake = FakeAccountClient(follows=['d1', 'd2', 'd3'], limited_after=1)
    code, printed, errors = run(
        follow, SYNTHETIC_INSTANCE, from_account='source', token=secret,
        make_client=lambda _instance, _token: fake)
    checks.equal('a rate limit stops the run and says how to continue',
                 (code, 'rate limited after 1 follows' in errors), (1, True))
    checks.equal('and only the first follow was sent',
                 [call for call in fake.asked if call[0] == 'follow'],
                 [('follow', 'd1')])

    code, printed, errors = run(
        follow, 'http://plain.test', accounts=['x'], token=secret,
        make_client=_NoNetwork)
    checks.equal('a plaintext instance is refused before any call', code, 2)
    checks.check('and says why', 'only connects over HTTPS' in errors)

    # --- the three accounts' numbers -----------------------------------
    one = FakeAccountClient(account_id='acct-a', following_count=5,
                            home=[{'id': 'p1'}, {'id': 'p2'}])
    two = FakeAccountClient(account_id='acct-b', following_count=1500)
    clients = {'acct-a': one, 'acct-b': two}
    tokens = {'acct-a': 'token-a', 'acct-b': 'token-b'}

    def reader(_instance, account_id):
        return tokens.get(account_id)

    def by_token(_instance, token):
        return clients['acct-a' if token == 'token-a' else 'acct-b']

    code, printed, errors = run(
        inspect, SYNTHETIC_INSTANCE, ['acct-a', 'acct-b'],
        make_client=by_token, reader=reader)
    lines = printed.strip().splitlines()
    checks.equal('inspect prints one line per account',
                 (code, len(lines)), (0, 2))
    checks.check('with its follow count and its home timeline size',
                 'following=5' in lines[0] and 'home=2' in lines[0]
                 and 'following=1500' in lines[1] and 'home=0' in lines[1])
    checks.equal('and no account token is printed', any(
        token in printed + errors for token in tokens.values()), False)

    # `older=` says the home timeline has more than its first page. It
    # comes from the parameters the server hands back for the next one,
    # so a page that has a next one must say so.
    paged = FakeAccountClient(following_count=7, home=[{'id': 'p1'}],
                              next_pages=[('/timelines/home', {'max_id': 'p1'})])
    code, printed, errors = run(
        inspect, SYNTHETIC_INSTANCE, [VIEWER_ID], token=secret,
        make_client=lambda _instance, _token: paged)
    checks.check('a home timeline with more than one page says so',
                 code == 0 and 'older=True' in printed)
    unpaged = FakeAccountClient(following_count=7, home=[{'id': 'p1'}])
    code, printed, errors = run(
        inspect, SYNTHETIC_INSTANCE, [VIEWER_ID], token=secret,
        make_client=lambda _instance, _token: unpaged)
    checks.check('and one with a single page does not',
                 code == 0 and 'older=False' in printed)

    code, printed, errors = run(
        inspect, SYNTHETIC_INSTANCE, ['acct-a', 'acct-missing'],
        make_client=by_token, reader=reader)
    checks.equal('an account with no keyring entry is reported, not crashed',
                 (code, 'no keyring entry for account acct-missing' in errors),
                 (1, True))

    # The home timeline failing is not the account failing, and the
    # message must say which it was.
    class NoHome(FakeAccountClient):
        def request_finish_paged(self, result):
            path = self.asked[-1][1] if self.asked else ''
            if path == '/timelines/home':
                raise GLib.Error.new_literal(
                    GLib.quark_from_string('rank-eval'),
                    'the home timeline refused', 500)
            return self._finish(result), None, None

    code, printed, errors = run(
        inspect, SYNTHETIC_INSTANCE, [VIEWER_ID], token=secret,
        make_client=lambda _instance, _token: NoHome())
    checks.check('a home timeline that fails is named as the timeline',
                 code == 1 and 'the home timeline of account' in errors)
    checks.check('and the token does not reach that message',
                 secret not in errors)

    # And the account itself failing, with a token in hand.
    class NoAccount(FakeAccountClient):
        def verify_credentials_finish(self, result):
            raise GLib.Error.new_literal(
                GLib.quark_from_string('rank-eval'), 'the token was refused',
                401)

    code, printed, errors = run(
        inspect, SYNTHETIC_INSTANCE, [VIEWER_ID], token=secret,
        make_client=lambda _instance, _token: NoAccount())
    checks.check('an account that cannot be verified is named',
                 code == 1 and 'refused' in errors)
    checks.check('and the token does not reach that message either',
                 secret not in errors)

    # --- the client's own write methods --------------------------------
    # `src/mastodon.py` owns the HTTP, and there is no server here to send
    # it to. Calling each method unbound, with a recorder as `self`, runs
    # the shipped body and pins the verb and path it would send -- so a
    # typo in a path is caught here rather than on the maintainer's
    # instance.
    class Sent:
        """Stands in for the client's transport."""

        def _request_async(self, method, path, **kwargs):
            self.sent = (method, path, kwargs)
            return 'task'

        def post_status_async(self, text, params=None, callback=None,
                              cancellable=None):
            self.sent = ('POST', '/statuses', {'json_body': dict(
                {'status': text}, **(params or {}))})
            return 'task'

    endpoints = []
    for action, kwargs in (
            ('favourite_async', {}),
            ('unfavourite_async', {}),
            ('reblog_async', {'visibility': 'private'}),
            ('unreblog_async', {}),
            ('bookmark_async', {}),
            ('unbookmark_async', {}),
            ('follow_async', {}),
            ('unfollow_async', {})):
        sent = Sent()
        getattr(REAL_CLIENT, action)(sent, '7', **kwargs)
        endpoints.append((action, sent.sent[0], sent.sent[1]))
    checks.equal(
        'the client sends each action to its own endpoint', endpoints,
        [('favourite_async', 'POST', '/statuses/7/favourite'),
         ('unfavourite_async', 'POST', '/statuses/7/unfavourite'),
         ('reblog_async', 'POST', '/statuses/7/reblog'),
         ('unreblog_async', 'POST', '/statuses/7/unreblog'),
         ('bookmark_async', 'POST', '/statuses/7/bookmark'),
         ('unbookmark_async', 'POST', '/statuses/7/unbookmark'),
         ('follow_async', 'POST', '/accounts/7/follow'),
         ('unfollow_async', 'POST', '/accounts/7/unfollow')])

    sent = Sent()
    REAL_CLIENT.follow_async(sent, '7')
    checks.equal('a follow sends both relationship switches',
                 sent.sent[2].get('form'),
                 {'reblogs': 'true', 'notify': 'false'})

    sent = Sent()
    REAL_CLIENT.reply_async(sent, '9', 'hello', {'visibility': 'private'})
    checks.equal('a reply posts a status with its parent in the body',
                 sent.sent,
                 ('POST', '/statuses', {'json_body': {
                     'status': 'hello', 'in_reply_to_id': '9',
                     'visibility': 'private'}}))

    # The forms and the finishes, which the verb-and-path check leaves
    # free: a reblog without its visibility, and a reply that finished as
    # something else, would both pass.
    sent = Sent()
    REAL_CLIENT.reblog_async(sent, '7', 'private')
    checks.equal('a reblog carries its visibility in the form',
                 sent.sent[2].get('form'), {'visibility': 'private'})
    sent = Sent()
    REAL_CLIENT.bookmark_async(sent, '7')
    checks.equal('a bookmark carries no form at all',
                 sent.sent[2].get('form'), None)

    class Finish:
        """A stand-in for the client, to see which _finish is called."""

        def _finish(self, result):
            return ('_finish', result)

        def post_status_finish(self, result):
            return ('post_status', result)

    checks.equal('reply_finish finishes as a posted status',
                 REAL_CLIENT.reply_finish(Finish(), 'r'), ('post_status', 'r'))
    checks.equal('follow_finish finishes as a request',
                 REAL_CLIENT.follow_finish(Finish(), 'f'), ('_finish', 'f'))
    checks.equal('favourite_finish finishes as a request',
                 REAL_CLIENT.favourite_finish(Finish(), 'x'), ('_finish', 'x'))

    # --- the three accounts keep their own data -------------------------
    # H1 evaluates three accounts and aggregates three summaries. One
    # store cannot give three: the viewer is a single row, and one
    # account's runs would be another's coverage.
    cache = os.path.join(tempfile.gettempdir(), 'rank-eval-separate-%d'
                         % os.getpid())
    root = os.path.join(cache, 'babble-eval')
    try:
        shutil.rmtree(cache, ignore_errors=True)
        for name in ('acct-one', 'acct-two'):
            fake = FakeAccountClient(account_id=name)
            run(collect, SYNTHETIC_INSTANCE, account_id=name, token=secret,
                make_client=lambda _instance, _token, f=fake: f,
                now=WORLD_START, root=root)
        # The store is named for the account the *server* named, not the
        # one that was asked for: a token for one account filed under
        # another's id would make two summaries of one feed.
        paths = [database_path(SYNTHETIC_INSTANCE, root, name)
                 for name in ('acct-one', 'acct-two')]
        checks.check('two accounts write to two databases',
                     paths[0] != paths[1]
                     and all(os.path.exists(path) for path in paths))
        viewers = []
        for path in paths:
            connection = sqlite3.connect(path)
            try:
                row = connection.execute(
                    "SELECT value FROM meta WHERE key = 'viewer_id'"
                ).fetchone()
                viewers.append(row[0] if row else None)
            finally:
                connection.close()
        checks.equal('and each keeps its own viewer', viewers,
                     ['acct-one', 'acct-two'])
        checks.equal('and the accounts it holds data for are named',
                     sibling_accounts(SYNTHETIC_INSTANCE, root),
                     ['acct-one', 'acct-two'])

        # An account id is quoted into the path, so an id with a slash in
        # it cannot climb out of the data directory.
        awkward = database_path(SYNTHETIC_INSTANCE, root, 'a/../../escape')
        checks.check('an account id cannot escape the data directory',
                     os.path.commonpath([os.path.abspath(root),
                                         os.path.abspath(awkward)])
                     == os.path.abspath(root))
        # Reading the account-less directory finds nothing, so the message
        # has to say which account to ask for.
        code, printed, errors = run(
            evaluate, SYNTHETIC_INSTANCE, root=root, gates_path=None)
        checks.check('and reading the wrong one says which to ask for',
                     code == 1 and 'no runs recorded' in errors
                     and 'acct-one' in errors)

        # A token for one account, asked for under another's id: the run
        # stops, because a store named for one account and holding
        # another's feed would make two summaries of one feed -- and
        # ``aggregate`` cannot tell.
        mismatched = FakeAccountClient(account_id='acct-two')
        before = _run_count(paths[0])
        code, printed, errors = run(
            collect, SYNTHETIC_INSTANCE, account_id='acct-one', token=secret,
            make_client=lambda _instance, _token: mismatched,
            now=WORLD_START + datetime.timedelta(hours=1), root=root)
        checks.equal('a token for another account stops the run',
                     (code, 'belongs to account acct-two' in errors), (2, True))
        checks.equal('and writes nothing under the id that was asked for',
                     _run_count(paths[0]), before)
        # With no account id at all, the store is named for the account
        # the server named.
        derived = FakeAccountClient(account_id='acct-three')
        code, printed, errors = run(
            collect, SYNTHETIC_INSTANCE, token=secret,
            make_client=lambda _instance, _token: derived,
            now=WORLD_START, root=root)
        checks.check('and with no account id the store is named for the'
                     ' account the server named',
                     code == 0
                     and os.path.exists(
                         database_path(SYNTHETIC_INSTANCE, root, 'acct-three')))

        # And the read half: `evaluate` must read the account it was
        # told to, and only that one. H1 folds three summaries, so a
        # read that ignored the id would fold one feed three times.
        for name in ('acct-one', 'acct-two'):
            code, printed, errors = run(
                evaluate, SYNTHETIC_INSTANCE, root=root, account_id=name,
                gates_path=None)
            checks.check('evaluate reads the account it was given (%s)'
                         % name, code == 0)
        code, printed, errors = run(
            evaluate, SYNTHETIC_INSTANCE, root=root, account_id='acct-none',
            gates_path=None)
        checks.equal('and an account with no runs says so',
                     (code, 'no runs recorded' in errors), (1, True))

        # And the command line itself, which is what the maintainer
        # types: the account id must reach `evaluate` from the
        # arguments, not only from a caller in this file.
        saved_cache = os.environ.get('XDG_CACHE_HOME')
        os.environ['XDG_CACHE_HOME'] = cache
        try:
            code, printed, errors = run(
                main, ['evaluate', '--instance', SYNTHETIC_INSTANCE,
                       '--account-id', 'acct-one'])
        finally:
            if saved_cache is None:
                os.environ.pop('XDG_CACHE_HOME', None)
            else:
                os.environ['XDG_CACHE_HOME'] = saved_cache
        checks.check('the command line passes the account id to evaluate',
                     code == 0)
    finally:
        shutil.rmtree(cache, ignore_errors=True)

    # --- where the token comes from ---------------------------------------
    # The order is the plan's: the one given, then the environment, then
    # the keyring. A change to it would pick up the wrong account's
    # token, and the store is keyed by whoever that token belongs to.
    saved_env = os.environ.get(TOKEN_VARIABLE)
    try:
        os.environ[TOKEN_VARIABLE] = 'from-the-environment'
        checks.equal('the environment token is used when none is given',
                     token_for(SYNTHETIC_INSTANCE, 'acct-a'),
                     'from-the-environment')
        checks.equal('and a token given wins over it',
                     token_for(SYNTHETIC_INSTANCE, 'acct-a', 'given'),
                     'given')
        os.environ.pop(TOKEN_VARIABLE, None)
        checks.equal('and the keyring is read when neither is there',
                     token_for(SYNTHETIC_INSTANCE, 'acct-a',
                               reader=lambda _i, _a: 'from-the-keyring'),
                     'from-the-keyring')
    finally:
        if saved_env is None:
            os.environ.pop(TOKEN_VARIABLE, None)
        else:
            os.environ[TOKEN_VARIABLE] = saved_env

    # --- the real keyring writer -----------------------------------------
    # Every test passes its own writer, so the one that calls the keyring
    # is never run -- and a writer that stored the token as the keyring's
    # *kind* and the account id as the secret would leave `collect`
    # holding an invalid token, silently.
    calls = []

    def fake_store(instance, kind, payload, callback=None, cancellable=None):
        calls.append((instance, kind, payload))
        callback(None)

    saved_store = mastodon.store_secret_async
    mastodon.store_secret_async = fake_store
    try:
        write_secret(SYNTHETIC_INSTANCE, VIEWER_ID, secret)
    finally:
        mastodon.store_secret_async = saved_store
    checks.equal('the real keyring writer stores the token as the payload'
                 ' and the account id as the kind',
                 calls, [(SYNTHETIC_INSTANCE, VIEWER_ID, secret)])

    # And that it raises when the keyring says no: a token that was
    # never stored, reported as stored, would leave `collect` without
    # one and the run would fail later, far from the cause.
    def refusing_store(instance, kind, payload, callback=None,
                       cancellable=None):
        callback(GLib.Error.new_literal(GLib.quark_from_string('g-secret'),
                                        'the keyring is locked', 1))

    saved_store = mastodon.store_secret_async
    mastodon.store_secret_async = refusing_store
    try:
        raised = None
        try:
            write_secret(SYNTHETIC_INSTANCE, VIEWER_ID, secret)
        except GLib.Error as error:
            raised = error
        checks.check('the keyring writer raises what the keyring said',
                     raised is not None and 'locked' in raised.message)
    finally:
        mastodon.store_secret_async = saved_store

    # --- the token is read without echo ------------------------------------
    # Nothing else here can see this: it is a property of the terminal,
    # not of the output.
    class Tty(io.StringIO):
        def isatty(self):
            return True

    saved_stdin, saved_getpass = sys.stdin, getpass.getpass
    sys.stdin = Tty()
    getpass.getpass = lambda prompt='': secret
    try:
        typed = read_token_line()
    finally:
        sys.stdin, getpass.getpass = saved_stdin, saved_getpass
    checks.equal('a terminal token is read without echo, through getpass',
                 typed, secret)

    # --- the inspect command with a token ----------------------------------
    # The one path that takes a token on the command line, and the one
    # whose output could have carried it.
    fake = FakeAccountClient(following_count=3, home=[{'id': 'p1'}])
    code, printed, errors = run(
        inspect, SYNTHETIC_INSTANCE, [VIEWER_ID], token=secret,
        make_client=lambda _instance, _token: fake)
    checks.check('inspect reports the account with a token given',
                 code == 0 and 'following=3' in printed)

    # A token names one account, so `inspect` must not take one and a
    # list of accounts at once: it would have no way to say which the
    # token belongs to.
    code, printed, errors = run(
        inspect, SYNTHETIC_INSTANCE, ['acct-a', 'acct-b'], token=secret,
        make_client=lambda _instance, _token: fake)
    checks.equal('a token with more than one account is refused',
                 (code, 'names one account' in errors), (2, True))


def check_world(checks, work, gates_path):
    """The five-day world: which days are judged, what the labels are,
    what a candidate's counters came from, and the whole-future rewrite."""
    pin_zone()
    world = synthetic_world()
    root = os.path.join(work, 'world')
    record(root, world,
           [WORLD_START + index * HOUR for index in range(WORLD_DAYS * 24)])
    summary, printed = evaluate_summary(
        root, os.path.join(work, 'world.json'), gates_path)
    days = summary['days']
    checks.equal('five days of hourly runs are judged on the three in the'
                 ' middle', [row['date'] for row in days],
                 ['2026-10-06', '2026-10-07', '2026-10-08'])
    checks.check('the first and last days have no coverage at one end',
                 '2026-10-05 06:00 local no-coverage' in printed
                 and '2026-10-09 06:00 local no-coverage' in printed)
    checks.equal('every judged day was labelled the same way',
                 sorted({row['labels'] for row in days}), [5])

    first = days[0]
    orderings = first['orderings']
    checks.equal('the day offered more candidates than the top 20 holds',
                 first['candidates'], 31)
    checks.equal("'newest first' leaves every label outside the top 20",
                 orderings['chrono']['recall20'], 0.0)
    checks.equal("but still recalls every label in the top 50",
                 orderings['chrono']['recall50'], 1.0)
    checks.equal('its top 20 is 20 distinct authors, none of them small',
                 (orderings['chrono']['authors20'],
                  orderings['chrono']['small20']), (20, 0.0))
    checks.equal('the affinity ordering puts every label in the top 20',
                 orderings['affinity_chrono']['recall20'], 1.0)
    checks.equal('as one author plus fifteen, five of them small',
                 (orderings['affinity_chrono']['authors20'],
                  orderings['affinity_chrono']['small20']), (16, 0.25))
    checks.equal('the popularity ordering finds none of them',
                 orderings['engagement']['recall20'], 0.0)
    checks.equal('the ranker without affinity finds none either',
                 orderings['rank_null']['recall20'], 0.0)
    checks.check('the ranker with affinity finds some, but not all: %r'
                 % orderings['rank_affinity']['recall20'],
                 0 < orderings['rank_affinity']['recall20'] < 1)
    # overlap20 is the popularity straw man's top 20 met by each ordering.
    # The engaged candidate is the oldest filler, so it is in the
    # popularity top 20 and outside chrono's.
    checks.equal('chrono overlaps the popularity top 20 in all but the'
                 ' candidate the counters lift into it',
                 orderings['chrono']['overlap20'], 19)
    checks.equal('and affinity-then-chrono in fifteen',
                 orderings['affinity_chrono']['overlap20'], 15)

    tokens = world_tokens(world)
    checks.equal('the summary carries no id, content, handle or url as a key',
                 private_keys(summary), [])
    checks.equal('nor as a value: no post id, handle or host',
                 private_values(summary, tokens), [])
    # The positive controls: each shape the reviewer added passed the
    # key-only rule, and each must be caught by the value rule.
    for key, value in (('first', 'liked-0-0'),
                       ('reader', '@synth@synth.example'),
                       ('where', SYNTHETIC_INSTANCE)):
        probe = json.loads(json.dumps(summary))
        probe['days'][0][key] = value
        checks.check('a %r value of %r is caught: %s'
                     % (key, value, private_values(probe, tokens)),
                     bool(private_values(probe, tokens)))

    no_gates, printed = evaluate_summary(
        root, os.path.join(work, 'nogates.json'),
        os.path.join(work, 'absent-gates.json'))
    checks.check('without a gates file it prints the metrics and refuses a'
                 ' verdict', 'gates not frozen, no verdict' in printed
                 and 'gates' not in no_gates)

    moment = datetime.datetime(2026, 10, 7, EVALUATION_HOUR,
                               tzinfo=datetime.timezone.utc)
    connection = open_database(database_path(SYNTHETIC_INSTANCE, root))
    try:
        affinities, candidates, ranked = day_orderings(connection, moment)
        observed_counters = {
            order: {row[0]: row[1:] for row in connection.execute(
                'SELECT post_id, favourites, reblogs FROM (SELECT *,'
                ' ROW_NUMBER() OVER (PARTITION BY post_id ORDER BY run_ts'
                ' %s) AS pick FROM observations WHERE run_ts <= ?)'
                ' WHERE pick = 1' % order, (iso_utc(moment),))}
            for order in ('DESC', 'ASC')}
    finally:
        connection.close()
    signals = affinity.to_ranking_signals(affinities)
    resolver = affinity.resolver(affinities, (), (), (),
                                 int(moment.timestamp() * 1000))
    strangers = set(affinities.author) | {'an author with no affinity'}
    checks.check('the liked author has a positive affinity: %r'
                 % affinities.author.get(LIKED_AUTHOR),
                 affinities.author.get(LIKED_AUTHOR, 0) > 0)
    checks.equal('affinity_chrono and rank_affinity read the same author'
                 ' signal, for every author',
                 {author: resolver.author(author) for author in strangers},
                 {author: catchuprank.affinity_signal(
                     signals.author_affinity, author) for author in strangers})

    # The one candidate whose counters move without anyone engaging it.
    popular = 'filler-%d-0' % (moment.date() - WORLD_START.date()).days
    by_id = {str(status.get('id')): status for status in candidates}
    checks.check('the day still offers the candidate whose counters move',
                 popular in by_id)
    checks.equal('its counters are the last observation at or before T',
                 (by_id[popular]['favourites_count'],
                  by_id[popular]['reblogs_count']),
                 observed_counters['DESC'][popular])
    checks.check('and the first observation differs (%r, then %r), so first'
                 ' and last are told apart'
                 % (observed_counters['ASC'][popular],
                    observed_counters['DESC'][popular]),
                 observed_counters['ASC'][popular]
                 != observed_counters['DESC'][popular])
    checks.equal('the popularity ordering puts the most engaged candidate'
                 ' first', str(ranked['engagement'][0].get('id')), popular)
    # The straw man is favourites *plus* reblogs. Either alone would put
    # the same candidate first, so the order alone cannot say which.
    checks.equal('engagement is favourites plus reblogs, not one of them',
                 engagement_of({'favourites_count': 6, 'reblogs_count': 2}),
                 8)

    # The interior-gap branch of `covered`: the world is sampled hourly,
    # so only a hole can fail it. Three hours is over the limit, two is
    # inside GAP_TOLERANCE.
    # Inside the first judged day's window and no other judged day's:
    # deleting runs at 10:00 and 11:00 leaves a three-hour gap there,
    # while 24-hour-spaced windows mean a hole further along would be
    # reported twice.
    hole = datetime.datetime(2026, 10, 5, 10, tzinfo=datetime.timezone.utc)
    for name, holes, covered_by_hand in (
            ('two-hour hole', (hole,), True),
            ('three-hour hole', (hole, hole + HOUR), False)):
        gapped = os.path.join(work, 'gap-' + name.replace(' ', '-'))
        copy_database(root, gapped)
        connection = sqlite3.connect(
            database_path(SYNTHETIC_INSTANCE, gapped), isolation_level=None)
        try:
            connection.executemany('DELETE FROM runs WHERE ts = ?',
                                   [(iso_utc(at),) for at in holes])
        finally:
            connection.close()
        gap_summary, gap_printed = evaluate_summary(
            gapped, os.path.join(work, 'gap-%s.json' % name.replace(' ', '-')),
            gates_path)
        lost = '2026-10-06 06:00 local no-coverage' in gap_printed
        remaining = [row['date'] for row in gap_summary['days']]
        checks.equal('a %s in a judged day\'s window leaves it'
                     ' %s' % (name, 'judged' if covered_by_hand
                              else 'no-coverage'),
                     (lost, remaining),
                     (not covered_by_hand,
                      ['2026-10-06', '2026-10-07', '2026-10-08']
                      if covered_by_hand else
                      ['2026-10-07', '2026-10-08']))

    # The whole-future rewrite: every observation after the chosen day's
    # evaluation time engaged, every counter moved. The days *before* it
    # may not notice. The chosen day itself may: its labels are the
    # engagements in (T, T + 24 h], which is past the boundary by design
    # and is not leakage. `check_rewrite` is the sharper form: it puts
    # the boundary inside the compared day's future.
    chosen = datetime.date(2026, 10, 7)
    copied = os.path.join(work, 'rewritten')
    copy_database(root, copied)
    first_day = day_moment(datetime.date(2026, 10, 6))
    connection = sqlite3.connect(database_path(SYNTHETIC_INSTANCE, copied),
                                 isolation_level=None)
    try:
        rewritten = connection.execute(
            'UPDATE observations SET favourites = favourites + 1000,'
            ' reblogs = reblogs + 1000, replies = replies + 1000,'
            ' favourited = 1, reblogged = 1, bookmarked = 1'
            ' WHERE run_ts > ?', (iso_utc(day_moment(chosen)),)).rowcount
        kept = candidates_at(connection, first_day)
        engaged_ever = {row[0] for row in connection.execute(
            'SELECT DISTINCT post_id FROM observations'
            ' WHERE favourited OR reblogged OR bookmarked')}
        window = [str(post_id) for post_id, raw in connection.execute(
            'SELECT id, json FROM posts')
            if first_day - DAY <= parse_moment(
                json.loads(raw).get('created_at')) < first_day]
    finally:
        connection.close()
    checks.check('the rewrite changed observations the earlier days must'
                 ' not read (%d rows)' % rewritten, rewritten > 0)
    checks.equal('the day before it still has every candidate it had',
                 len(kept), 31)
    checks.equal('while a query without the time bound would find none of'
                 ' them (%d posts in its window)' % len(window),
                 sorted(set(window) - engaged_ever), [])

    after, _printed = evaluate_summary(
        copied, os.path.join(work, 'rewritten.json'), gates_path)

    def by_day(summary_):
        return {row['date']: json.dumps(row, sort_keys=True)
                for row in summary_['days']}

    was, now = by_day(summary), by_day(after)
    checks.equal('every day before the rewrite is byte for byte identical,'
                 ' day for day and metric for metric',
                 [day for day in was
                  if day < chosen.isoformat() and was[day] != now[day]], [])
    shifted = [day for day in now
               if day >= chosen.isoformat() and now[day] != was.get(day)]
    checks.check('and the days it moved differ: %s' % ', '.join(shifted),
                 shifted)


def check_rewrite(checks, work, gates_path):
    """The whole-future rewrite, with the boundary *inside* the compared
    day's future.

    `check_world` rewrites everything after the chosen day's 06:00 and
    compares the day before it, whose future is the 24 hours that
    rewrite does not touch -- so a bound one hour too far is invisible
    there. Here every candidate is already engaged at T + 30 min and the
    rewrite starts at T + 1 h: the labels cannot move, the day cannot
    move, and a query that reads anything after T does.
    """
    pin_zone()
    root = os.path.join(work, 'rewrite')
    moments = {WORLD_START + index * HOUR
               for index in range(WORLD_DAYS * 24)}
    moments.update(WORLD_START + index * DAY + EVALUATION_HOUR * HOUR
                   + 30 * MINUTE for index in range(WORLD_DAYS))
    record(root, rewrite_world(), sorted(moments))
    summary, _printed = evaluate_summary(
        root, os.path.join(work, 'rewrite.json'), gates_path)
    checks.equal('the rewrite world is judged on the three middle days',
                 [row['date'] for row in summary['days']],
                 ['2026-10-06', '2026-10-07', '2026-10-08'])
    chosen = (WORLD_START + REWRITE_DAY * DAY).date()
    first = {row['date']: row for row in summary['days']}[chosen.isoformat()]
    checks.equal('every candidate of the chosen day is labelled inside the'
                 ' hour after T, so the rewrite cannot move a label',
                 (first['labels'], first['candidates'],
                  first['labels_near_start']), (37, 37, 37))

    copied = os.path.join(work, 'rewrite-after')
    copy_database(root, copied)
    moment = day_moment(chosen)
    connection = sqlite3.connect(database_path(SYNTHETIC_INSTANCE, copied),
                                 isolation_level=None)
    try:
        inside = connection.execute(
            'SELECT COUNT(*) FROM observations WHERE run_ts > ?'
            ' AND run_ts < ?', (iso_utc(moment),
                                iso_utc(moment + DAY))).fetchone()[0]
        rewritten = connection.execute(
            'UPDATE observations SET favourites = favourites + 1000,'
            ' reblogs = reblogs + 1000, replies = replies + 1000,'
            ' favourited = 1, reblogged = 1, bookmarked = 1'
            ' WHERE run_ts > ?', (iso_utc(moment + HOUR),)).rowcount
    finally:
        connection.close()
    checks.check('the rewrite reaches into the compared day\'s future:'
                 ' %d observations inside (T, T + 24 h)' % inside, inside > 0)
    checks.check('and rewrote %d of them' % rewritten, rewritten > 0)

    after, _printed = evaluate_summary(
        copied, os.path.join(work, 'rewrite-after.json'), gates_path)

    def by_day(summary_):
        return {row['date']: json.dumps(row, sort_keys=True)
                for row in summary_['days']}

    was, now = by_day(summary), by_day(after)
    checks.equal('the day before it and the chosen day itself are byte for'
                 ' byte identical, day for day and metric for metric',
                 [day for day in was
                  if day <= chosen.isoformat() and was[day] != now[day]], [])
    later = (chosen + DAY).isoformat()
    checks.check('while the day after the boundary moved: %s' % later,
                 now.get(later) != was.get(later))


def self_test():
    """Check the harness against a world whose answer is known.

    Prints an ``ok - …`` line per check and a ``FAIL - …`` line for each
    that did not hold, and returns 1 if any failed. Every run goes through
    `FakeClient`, and `mastodon.MastodonClient` is replaced here by a
    class whose ``__init__`` raises -- so no `Soup.Session` is ever built,
    and a run that reached for the real client would fail loudly instead
    of quietly touching the network.
    """
    checks = Checks()
    # D3 judges a day at 06:00 *local*, so the world pins the local zone
    # rather than guess the machine's, and the dates above are the dates
    # the harness then judges. Every check calls `pin_zone` itself too, so
    # each one is also right when it is run alone.
    pin_zone()
    real = mastodon.MastodonClient
    mastodon.MastodonClient = _NoNetwork
    try:
        checks.check('the real client is replaced for the whole run',
                     mastodon.MastodonClient is _NoNetwork)
        with tempfile.TemporaryDirectory(prefix='rank-eval-') as work:
            check_metrics(checks)
            check_sign_test(checks)
            gates_path = check_gates(checks, work)
            check_split(checks, work)
            check_collect(checks, work)
            check_accounts(checks)
            check_world(checks, work, gates_path)
            check_rewrite(checks, work, gates_path)
    finally:
        mastodon.MastodonClient = real
    if checks.failures:
        print('%d checks, %d failed' % (checks.count, checks.failures))
        return 1
    print('%d checks, all passed' % checks.count)
    return 0


# endregion

# region bench

# The clock the bench's posts are aged against, so two machines rank the
# same ages and not the same wall-clock offsets.
BENCH_NOW = datetime.datetime(2026, 10, 21, 12, tzinfo=datetime.timezone.utc)
BENCH_AUTHORS = 150
BENCH_RUNS = 3
BENCH_WORDS = ('alpha bravo charlie delta echo foxtrot golf hotel india'
               ' juliet kilo lima mike november oscar papa quebec romeo'
               ' sierra tango uniform victor whiskey xray yankee zulu'
               ' babble timeline ranked affinity timeline feed').split()


def bench_posts(count, seed=20261021):
    """*count* synthetic home posts, as Docs/timeline-algorithm.md §6.1
    describes them: random text of 8 to 60 words, one tag, one link, 150
    authors, 48 hours of ages, no media."""
    rng = random.Random(seed)
    statuses = []
    for index in range(count):
        link = 'https://example.test/%d' % index
        text = ' '.join(rng.choice(BENCH_WORDS)
                        for _word in range(rng.randint(8, 60)))
        created = BENCH_NOW - datetime.timedelta(
            seconds=rng.randint(0, 48 * 60 * 60))
        statuses.append({
            'id': 'bench-%d' % index,
            'created_at': created.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'account': {
                'id': 'bench-author-%d' % (index % BENCH_AUTHORS),
                'acct': 'author-%d' % (index % BENCH_AUTHORS),
                'username': 'author-%d' % (index % BENCH_AUTHORS),
                'followers_count': rng.randint(10, 20000),
                'following_count': rng.randint(30, 2000),
                'statuses_count': rng.randint(100, 20000),
                'note': 'a synthetic account',
            },
            'content': '<p>%s <a href="%s">%s</a></p>' % (text, link, link),
            'card': {'url': link},
            'tags': [{'name': 'synth'}],
            'language': 'en',
            'favourites_count': rng.randint(0, 3000),
            'reblogs_count': rng.randint(0, 500),
            'replies_count': rng.randint(0, 100),
        })
    return statuses


def bench(out=None):
    """Time `catchuprank.rank()` on 400 and 800 synthetic posts, three
    runs each, printing every run. §6.1 records the figure this
    reproduces; medians would hide the first-call warm-up it names."""
    out = out or sys.stdout
    context = catchuprank.RankingContext(now=BENCH_NOW)
    for count in (400, 800):
        candidates = [catchuprank.candidate_from_status(status)
                      for status in bench_posts(count)]
        for attempt in range(1, BENCH_RUNS + 1):
            started = time.perf_counter()
            catchuprank.rank(candidates, context)
            elapsed = (time.perf_counter() - started) * 1000
            print('%d posts, run %d: %.1f ms' % (count, attempt, elapsed),
                  file=out)
    return 0


# endregion


def build_parser():
    """The command line. `credits` is T4.3's; it parses and answers, so a
    script can name it without a traceback."""
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

    evaluate_command = commands.add_parser(
        'evaluate', help='judge the recorded feed, day by day')
    evaluate_command.add_argument(
        '--instance', required=True,
        help='the instance whose database is judged')
    evaluate_command.add_argument(
        '--gates', help='the frozen gates file (default: the one beside'
                        ' this script)')
    evaluate_command.add_argument(
        '--json', dest='json_path',
        help='write the summary here (numbers, dates and verdicts only)')
    evaluate_command.add_argument(
        '--account-id',
        help='the account whose data to judge; the same one collect wrote'
             ' under. Without it, the account-less directory is read,'
             ' which is where a single-account collection lands.')

    aggregate_command = commands.add_parser(
        'aggregate', help='the H1 record over several summaries')
    aggregate_command.add_argument(
        'summaries', nargs='+', help='summary JSON files, one per account')
    aggregate_command.add_argument(
        '--gates', help='the frozen gates file (default: the one beside'
                        ' this script)')

    token_command = commands.add_parser(
        'token', help='verify an account token and store it in the keyring')
    token_command.add_argument(
        '--instance', required=True,
        help='the instance the account lives on')
    token_command.add_argument(
        '--account-id',
        help='the id the server names; checked against verify_credentials')
    token_command.add_argument(
        '--token',
        help='the token, when it is not piped in on stdin (a shell keeps'
             ' it in its history)')

    follow_command = commands.add_parser(
        'follow', help='follow accounts for one managed account')
    follow_command.add_argument(
        '--instance', required=True, help='the instance to act on')
    follow_command.add_argument(
        '--account-id', help='the keyring account to act as')
    follow_command.add_argument(
        '--from-account',
        help='follow the accounts this account id follows')
    follow_command.add_argument(
        '--account', dest='accounts', action='append', default=[],
        metavar='ID', help='a candidate account id (repeatable)')
    follow_command.add_argument(
        '--limit', type=int, default=0,
        help='stop after this many follows this run (default: no cap)')
    follow_command.add_argument(
        '--pause', type=float, default=1.0,
        help='seconds to wait between follows (default: 1)')

    engage_command = commands.add_parser(
        'engage', help='favourite, reblog, bookmark or reply, as an account')
    engage_command.add_argument(
        '--instance', required=True, help='the instance to act on')
    engage_command.add_argument(
        '--account-id', help='the keyring account to act as')
    engage_command.add_argument(
        '--target', required=True, help='the status id to engage')
    engage_command.add_argument(
        '--kind', required=True, choices=ENGAGEMENTS)
    engage_command.add_argument(
        '--text', help='the reply body (reply only)')
    engage_command.add_argument(
        '--visibility', help='public or private (reblog and reply)')

    inspect_command = commands.add_parser(
        'inspect',
        help='report follow counts and the home timeline size per account')
    inspect_command.add_argument(
        '--instance', required=True, help='the instance the accounts live on')
    inspect_command.add_argument(
        '--account-id', action='append', required=True, metavar='ID',
        help='a keyring account to report on (repeatable)')
    inspect_command.add_argument(
        '--token', help='act as this token; one --account-id only')

    # `credits` belongs to T4.3, which reads the ranked store's counters
    # table; the store itself is T2.1's. Until then it parses and answers.
    commands.add_parser('credits', help='not implemented yet')
    commands.add_parser(
        'self-test',
        help='check the harness against a synthetic world (no network)')
    commands.add_parser(
        'bench',
        help='time catchuprank.rank() on synthetic posts')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == 'collect':
        return collect(args.instance, account_id=args.account_id)
    if args.command == 'evaluate':
        return evaluate(args.instance, gates_path=args.gates,
                        json_path=args.json_path,
                        account_id=args.account_id)
    if args.command == 'aggregate':
        return aggregate(args.summaries, gates_path=args.gates)
    if args.command == 'token':
        return store_token(args.instance, account_id=args.account_id,
                           token=args.token or read_token_line())
    if args.command == 'follow':
        return follow(args.instance, from_account=args.from_account,
                      accounts=args.accounts, limit=args.limit,
                      pause=args.pause, account_id=args.account_id)
    if args.command == 'engage':
        return engage(args.instance, args.target, args.kind, text=args.text,
                      visibility=args.visibility,
                      account_id=args.account_id)
    if args.command == 'inspect':
        return inspect(args.instance, args.account_id, token=args.token)
    if args.command == 'self-test':
        return self_test()
    if args.command == 'bench':
        return bench()
    print('%s is not implemented yet' % args.command, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
