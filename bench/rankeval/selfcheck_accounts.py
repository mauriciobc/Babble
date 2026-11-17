# SPDX-License-Identifier: GPL-3.0-or-later
"""The self-test of the accounts command and its keyring path."""

import contextlib
import datetime
import getpass
import io
import os
import shutil
import sqlite3
import sys
import tempfile
from gi.repository import GLib
from rankeval.collect import collect
from rankeval.commands import (STATUS_ACTIONS,
    TOKEN_VARIABLE,
    engage,
    follow,
    inspect,
    read_token_line,
    store_token,
    token_for,
    write_secret)
from rankeval.evaluate import evaluate
from rankeval.rankstore import _run_count, database_path, sibling_accounts
from rankeval.shared import SYNTHETIC_INSTANCE, VIEWER_ID
from rankeval.synthetic import FakeAccountClient, WORLD_START, _NoNetwork, pin_zone
from src import keyringstore
from src.api import relationships as api_relationships, statuses as api_statuses



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
        def _error(self, action):
            if action != 'verify':
                return super()._error(action)
            return GLib.Error.new_literal(
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

        # The status actions are `src.api.statuses` functions, which send
        # through the public request_async.
        request_async = _request_async

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
        name = action[:-len('_async')]
        module = api_statuses if name in STATUS_ACTIONS else api_relationships
        getattr(module, action)(sent, '7', **kwargs)
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
    api_relationships.follow_async(sent, '7', reblogs=True, notify=False)
    checks.equal('a follow sends both relationship switches',
                 sent.sent[2].get('form'),
                 {'reblogs': 'true', 'notify': 'false'})

    sent = Sent()
    api_statuses.reply_async(sent, '9', 'hello', {'visibility': 'private'})
    checks.equal('a reply posts a status with its parent in the body',
                 (sent.sent[0], sent.sent[1], sent.sent[2]['json_body']),
                 ('POST', '/statuses', {
                     'status': 'hello', 'in_reply_to_id': '9',
                     'visibility': 'private'}))

    # The forms and the finishes, which the verb-and-path check leaves
    # free: a reblog without its visibility, and a reply that finished as
    # something else, would both pass.
    sent = Sent()
    api_statuses.reblog_async(sent, '7', 'private')
    checks.equal('a reblog carries its visibility in the form',
                 sent.sent[2].get('form'), {'visibility': 'private'})
    sent = Sent()
    api_statuses.bookmark_async(sent, '7')
    checks.equal('a bookmark carries no form at all',
                 sent.sent[2].get('form'), None)

    class Finish:
        """A stand-in for the client, to see which _finish is called."""

        def _finish(self, result):
            return ('_finish', result)

        def request_finish(self, result):
            return ('request_finish', result)

    checks.equal('reply_finish finishes as a request',
                 api_statuses.reply_finish(Finish(), 'r'),
                 ('request_finish', 'r'))
    checks.equal('follow_finish finishes as a request',
                 api_relationships.follow_finish(Finish(), 'f'),
                 ('request_finish', 'f'))
    checks.equal('favourite_finish finishes as a request',
                 api_statuses.favourite_finish(Finish(), 'x'),
                 ('request_finish', 'x'))

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

        from rankeval.cli import main

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

    saved_store = keyringstore.store_secret_async
    keyringstore.store_secret_async = fake_store
    try:
        write_secret(SYNTHETIC_INSTANCE, VIEWER_ID, secret)
    finally:
        keyringstore.store_secret_async = saved_store
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

    saved_store = keyringstore.store_secret_async
    keyringstore.store_secret_async = refusing_store
    try:
        raised = None
        try:
            write_secret(SYNTHETIC_INSTANCE, VIEWER_ID, secret)
        except GLib.Error as error:
            raised = error
        checks.check('the keyring writer raises what the keyring said',
                     raised is not None and 'locked' in raised.message)
    finally:
        keyringstore.store_secret_async = saved_store

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
