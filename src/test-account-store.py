# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the stored account list and the OAuth client helpers.

A Mastodon account id is server-local, so two instances both hand out
``42``. These pin the pairing that keeps one instance's account from
being read, switched to or deleted under another's id, plus the pieces
of the client those fixes rest on: the revoke request, the scope
ceiling and the keyring failure report.
"""

import os
import sys
import unittest

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
# The modules use relative imports, so they are loaded as a package.
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.accounts import (account_key, active_account, find_account,  # noqa: E402
                          find_account_by_key, forget_account,
                          read_accounts, save_account)
from src.mastodon import (ADMIN_SCOPES, OAUTH_SCOPES,  # noqa: E402
                          MastodonClient, normalize_instance)

ALICE = {'id': '42', 'acct': 'alice', 'username': 'alice',
         'instance': 'https://a.example'}
# Same id, different server: the collision the pairing exists for.
BOB = {'id': '42', 'acct': 'bob', 'username': 'bob',
       'instance': 'https://b.example'}
CAROL = {'id': '7', 'acct': 'carol', 'username': 'carol',
         'instance': 'https://a.example'}


class FakeSettings:
    """The two GSettings keys accounts.py touches."""

    def __init__(self):
        self.data = {'accounts': [], 'active-account': ''}

    def get_strv(self, key):
        return self.data[key]

    def set_strv(self, key, value):
        self.data[key] = list(value)

    def get_string(self, key):
        return self.data[key]

    def set_string(self, key, value):
        self.data[key] = value


class TestAccountIdentity(unittest.TestCase):
    def setUp(self):
        self.settings = FakeSettings()
        save_account(self.settings, ALICE)
        save_account(self.settings, BOB)
        save_account(self.settings, CAROL, activate=False)

    def handles(self):
        return {(a['acct'], a.get('instance')) for a in
                read_accounts(self.settings)}

    def test_key_pairs_the_instance_with_the_id(self):
        self.assertEqual(account_key(ALICE), 'https://a.example#42')
        self.assertEqual(account_key(BOB), 'https://b.example#42')

    def test_lookup_by_instance_separates_colliding_ids(self):
        found = find_account(self.settings, '42', 'https://b.example')
        self.assertIsNotNone(found)
        self.assertEqual(found['acct'], 'bob')

    def test_lookup_by_key_separates_colliding_ids(self):
        self.assertEqual(
            find_account_by_key(self.settings, account_key(BOB))['acct'],
            'bob')

    def test_bare_id_lookup_refuses_to_guess(self):
        # Two accounts answer this; picking the first would act on the
        # wrong server's account.
        self.assertIsNone(find_account(self.settings, '42'))
        self.assertIsNotNone(find_account(self.settings, '7'))

    def test_removing_one_instance_leaves_the_other(self):
        forget_account(self.settings, '42', 'https://a.example')
        self.assertEqual(self.handles(),
                         {('bob', 'https://b.example'),
                          ('carol', 'https://a.example')})

    def test_removing_the_active_account_activates_a_remaining_one(self):
        save_account(self.settings, ALICE)  # active: a.example#42
        self.assertEqual(active_account(self.settings)['acct'], 'alice')
        forget_account(self.settings, '42', 'https://a.example')
        self.assertEqual(active_account(self.settings)['acct'], 'bob')

    def test_removing_an_inactive_account_keeps_the_active_one(self):
        save_account(self.settings, ALICE)
        forget_account(self.settings, '42', 'https://b.example')
        self.assertEqual(active_account(self.settings)['acct'], 'alice')

    def test_the_last_removal_leaves_nothing_active(self):
        save_account(self.settings, ALICE)
        for account in (CAROL, ALICE, BOB):
            forget_account(self.settings, account['id'],
                           account['instance'])
        self.assertIsNone(active_account(self.settings))
        self.assertEqual(read_accounts(self.settings), [])

    def test_a_bare_id_from_before_the_pairing_still_resolves(self):
        # What the setting held before accounts carried their instance.
        settings = FakeSettings()
        save_account(settings, CAROL)
        settings.set_string('active-account', '7')
        self.assertEqual(active_account(settings)['acct'], 'carol')

    def test_an_unpaired_active_id_is_dropped_when_it_collides(self):
        # Legacy value, two accounts on file: refusing beats guessing.
        settings = FakeSettings()
        save_account(settings, ALICE, activate=False)
        save_account(settings, BOB, activate=False)
        settings.set_string('active-account', '42')
        self.assertIsNone(active_account(settings))


class TestRevocation(unittest.TestCase):
    def setUp(self):
        self.client = MastodonClient('https://a.example')
        self.sent = []
        self.client._request_async = (
            lambda method, path, **kwargs:
            self._record(method, path, kwargs))

    def _record(self, method, path, kwargs):
        self.sent.append((method, path, kwargs.get('form'),
                          kwargs.get('auth', True)))
        return object()

    def test_revoke_posts_the_client_credentials_and_the_token(self):
        self.client.client_id = 'cid'
        self.client.client_secret = 'csecret'
        self.client.revoke_token_async('tok')
        method, path, form, auth = self.sent[0]
        # POST only: a DELETE /oauth/revoke is not a route on Mastodon.
        self.assertEqual(method, 'POST')
        self.assertEqual(path, '/oauth/revoke')
        # The endpoint authenticates the client, so a bearer alone is
        # refused with a 403.
        self.assertEqual(form, {'client_id': 'cid',
                                'client_secret': 'csecret',
                                'token': 'tok'})
        self.assertFalse(auth)

    def test_revoke_without_client_credentials_fails_instead_of_lying(self):
        self.client.revoke_token_async('tok')
        self.assertEqual(self.sent, [])


class TestScopes(unittest.TestCase):
    def test_the_login_asks_for_no_admin_scope(self):
        # Every requested scope is listed on the consent screen, so the
        # default login must not put moderation in front of everyone.
        self.assertNotIn('admin', OAUTH_SCOPES)

    def test_the_admin_authorization_adds_both_admin_scopes(self):
        self.assertIn('admin:read', ADMIN_SCOPES)
        self.assertIn('admin:write', ADMIN_SCOPES)
        for scope in OAUTH_SCOPES.split():
            self.assertIn(scope, ADMIN_SCOPES.split())

    def test_the_app_website_is_not_the_instance(self):
        client = MastodonClient('https://some.server.example')
        sent = []
        client._request_async = (
            lambda method, path, **kwargs: sent.append(kwargs.get('form')))
        client.register_app_async()
        # The website is the application's own homepage, shown on the
        # instance's consent screen -- never the server being signed in
        # to, which would read as "this server made this app".
        self.assertNotEqual(sent[0]['website'], client.instance)
        self.assertTrue(sent[0]['website'].startswith('https://'))

    def test_registration_declares_the_scopes_it_will_authorize(self):
        client = MastodonClient('https://a.example')
        sent = []
        client._request_async = (
            lambda method, path, **kwargs: sent.append(kwargs.get('form')))
        client.register_app_async(scopes=ADMIN_SCOPES)
        # Mastodon refuses an authorization naming a scope the
        # registration did not declare, so the two must agree.
        self.assertEqual(sent[0]['scopes'], ADMIN_SCOPES)


class TestNormalizeInstance(unittest.TestCase):
    def test_a_plaintext_instance_is_refused(self):
        with self.assertRaises(ValueError):
            normalize_instance('http://a.example')


if __name__ == '__main__':
    unittest.main()
