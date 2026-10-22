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
"""

import argparse
import datetime
import itertools
import json
import math
import os
import sqlite3
import statistics
import sys
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
        # ``get`` returns ``(data, next_params)``, and this endpoint's
        # body is an object -- the account itself, which ``lift`` makes a
        # dict subclass. So this is the account, not the first element of
        # a list: the app reads it the same way
        # (``account = client.verify_credentials_finish(result)`` then
        # ``account['instance'] = ...``, auth.py:535).
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

    No gap between consecutive runs in [T - 24 h, T + 24 h] may exceed
    two hours. A window holding fewer than two runs has no gap to
    measure, and is not covered: an unsampled day is not a sampled one.
    """
    low, high = iso_utc(moment - DAY), iso_utc(moment + DAY)
    inside = [row for row in gaps if low <= row[0] <= high]
    return len(inside) > 1 and all(
        gap is not None and gap <= COVERAGE_GAP for _ts, gap in inside[1:])


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


def evaluate(instance, gates_path=None, json_path=None, root=None, out=None):
    """Judge the recorded feed, day by day. Returns an exit code.

    Every local day between the first and the last run is printed. A day
    whose 48-hour window was not sampled at most two hours apart is
    reported as ``no-coverage``, and left out of every count and mean.

    With a gates file the four decisions and the H1 record are printed,
    and the same numbers go to *json_path* as a summary carrying no post,
    no id and no handle. Without one, only the metrics are printed: the
    thresholds were frozen before the data came in (H0), so they cannot
    be chosen after it.
    """
    out = out or sys.stdout
    try:
        instance_url = mastodon.normalize_instance(instance)
    except ValueError as error:
        print('rank_eval: %s' % error, file=sys.stderr)
        return 2
    connection = open_database(database_path(instance_url, root))
    try:
        viewer, following, mutuals = stored_maps(connection)
        gaps = run_gaps(connection)
        if not gaps:
            print('rank_eval: no runs recorded', file=sys.stderr)
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
        print_gates(decision, out)
    if json_path:
        with open(json_path, 'w', encoding='utf-8') as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
            handle.write('\n')
    return 0


def aggregate(paths, gates_path=None, out=None):
    """The H1 record across accounts: the per-account summaries, judged.

    Gate A passes when at least ``min_accounts_passing`` accounts' records
    pass. A failure on any account that is not offset by enough passes is
    a FAIL; only accounts that ran out of days without failing make the
    whole INCONCLUSIVE. The ordering and the affinity decision are the
    accounts' majority, and a tie falls back to the more conservative
    answer: ``rank``, without affinity. Without a gates file nothing is
    decided.
    """
    out = out or sys.stdout
    gates = load_gates(gates_path)
    if gates is None:
        print('gates not frozen, no verdict', file=out)
        return 0
    records = []
    for path in paths:
        with open(path, encoding='utf-8') as handle:
            records.append(json.load(handle))
    verdicts = [record.get('gate_a') for record in records]
    if verdicts.count('PASS') >= gates['min_accounts_passing']:
        gate_a = 'PASS'
    elif 'FAIL' in verdicts:
        gate_a = 'FAIL'
    else:
        gate_a = 'INCONCLUSIVE'
    orderings = [record.get('ordering') for record in records]
    ordering = ('affinity_chrono'
                if orderings.count('affinity_chrono') > orderings.count('rank')
                else 'rank')
    uses = [record.get('use_affinity') for record in records]
    use_affinity = 2 * sum(1 for value in uses if value) > len(uses)
    print('Gate A: %s' % gate_a, file=out)
    print('ORDERING: %s' % ordering, file=out)
    print('USE_AFFINITY: %s' % ('true' if use_affinity else 'false'),
          file=out)
    return 0


def build_parser():
    """The command line. The three commands later tasks fill in still
    parse and answer, so a script can name them without a traceback."""
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

    aggregate_command = commands.add_parser(
        'aggregate', help='the H1 record over several summaries')
    aggregate_command.add_argument(
        'summaries', nargs='+', help='summary JSON files, one per account')
    aggregate_command.add_argument(
        '--gates', help='the frozen gates file (default: the one beside'
                        ' this script)')

    for name in ('credits', 'self-test', 'bench'):
        commands.add_parser(name, help='not implemented yet')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == 'collect':
        return collect(args.instance, account_id=args.account_id)
    if args.command == 'evaluate':
        return evaluate(args.instance, gates_path=args.gates,
                        json_path=args.json_path)
    if args.command == 'aggregate':
        return aggregate(args.summaries, gates_path=args.gates)
    print('%s is not implemented yet' % args.command, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
