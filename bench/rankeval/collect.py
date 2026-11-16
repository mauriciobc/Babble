# SPDX-License-Identifier: GPL-3.0-or-later
"""Collecting the reader's own feed into the store, through the real client."""

import itertools
import sys
from gi.repository import GLib
from rankeval.commands import invoke, token_for
from rankeval.rankstore import database_path, open_database, write_run
from rankeval.shared import (PAGE_LIMIT,
    RELATIONSHIP_BATCH,
    Stopped,
    call,
    iso_utc,
    make_client)
from src import oauthpkce



HOME_MAX_PAGES = 20

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
        # (``account = api_accounts.verify_credentials_finish(client,
        # result)`` then ``account['instance'] = ...``, auth.py).
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
        instance_url = oauthpkce.normalize_instance(instance)
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
