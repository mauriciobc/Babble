# SPDX-License-Identifier: GPL-3.0-or-later
"""The commands that act on the instance: tokens, engages, follows, inspect."""

import getpass
import itertools
import os
import sys
import time
from gi.repository import GLib
from rankeval.shared import (PAGE_LIMIT,
    RELATIONSHIP_BATCH,
    call,
    iso_utc,
    make_client,
    read_secret)
from src import keyringstore, mastodon, oauthpkce
from src.api import (accounts as api_accounts,
    relationships as api_relationships,
    statuses as api_statuses)



TOKEN_VARIABLE = 'BABBLE_EVAL_TOKEN'

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

STATUS_ACTIONS = ('favourite', 'unfavourite', 'reblog', 'unreblog',
                  'bookmark', 'unbookmark', 'reply')

# The other actions that live in `src.api`, and the module each is in.
API_ACTIONS = {'verify_credentials': api_accounts, 'follow': api_relationships,
               'unfollow': api_relationships}

def invoke(client, action, *args, **params):
    """Run one ``<action>_async`` and return ``<action>_finish``'s.

    The status, account and relationship actions live in `src.api` and
    take the client as their first argument; every other action is a
    method of the client.

    The client answers on the main loop, so a `GLib.MainLoop` runs until
    the callback fires -- and not when a fake answered before returning,
    the same allowance `call` makes. A failure is raised, which is what
    every ``_finish`` does.
    """
    loop = GLib.MainLoop()
    answer = {}

    module = api_statuses if action in STATUS_ACTIONS \
        else API_ACTIONS.get(action)
    if module is not None:
        start = getattr(module, action + '_async')
        finish = getattr(module, action + '_finish')
        args = (client,) + args
        finish_args = (client,)
    else:
        start = getattr(client, action + '_async')
        finish = getattr(client, action + '_finish')
        finish_args = ()

    def finished(_client, result, _data=None):
        try:
            answer['value'] = finish(*finish_args, result)
        except Exception as error:
            answer['error'] = error
        finally:
            loop.quit()

    start(*args, callback=finished, **params)
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

    keyringstore.store_secret_async(instance, account_id, token, done)
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
        instance_url = oauthpkce.normalize_instance(instance)
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
        instance_url = oauthpkce.normalize_instance(instance)
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
        instance_url = oauthpkce.normalize_instance(instance)
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
                relationship = invoke(client, 'follow', candidate, reblogs=True,
                                      notify=False)
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
        instance_url = oauthpkce.normalize_instance(instance)
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
