#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from gi.repository import Gio, GLib  # noqa: E402

from src import pages  # noqa: E402
from src.profile import ProfilePage, endorse_action_names  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


check('endorse modern', endorse_action_names(True, True) == ('endorse', 'pin'))
check('unendorse modern',
      endorse_action_names(False, True) == ('unendorse', 'unpin'))
check('endorse legacy', endorse_action_names(True, False) == ('pin', 'endorse'))
check('unendorse legacy',
      endorse_action_names(False, False) == ('unpin', 'unendorse'))


class _Client:
    def __init__(self, fail_codes):
        self.paths = []
        self.fail_codes = list(fail_codes)

    def is_supported(self, _version, when_unknown=False):
        return True

    def request_async(self, method, path, form=None, params=None,
                      callback=None, **_kwargs):
        self.paths.append((method, path))
        if callback is not None:
            callback(self, len(self.paths))

    def request_finish(self, result):
        code = self.fail_codes.pop(0) if self.fail_codes else 0
        if code:
            raise GLib.Error.new_literal(
                Gio.io_error_quark(), 'missing', code)
        return {'id': 'rel', 'endorsed': True}


class _Page:
    """Just enough of ProfilePage for the relationship requests."""

    def __init__(self, fail_codes):
        self.client = _Client(fail_codes)
        self.account = {'id': '9', 'acct': 'ada'}
        self.relationship = {}
        self.menu_button = None
        self._busy = False
        self.menu_updates = 0

    def get_root(self):
        return None

    def _acct(self):
        return 'ada'

    def _set_busy(self, busy):
        self._busy = busy

    def update_follow_button(self):
        pass

    def update_menu(self):
        self.menu_updates += 1

    def update_relationship_ui(self):
        pass

    def set_relationship(self, body):
        self.relationship = body

    def load_relationship(self):
        self.client.paths.append(('GET', '/accounts/relationships'))

    def _send_profile_action(self, *args, **kwargs):
        return ProfilePage._send_profile_action(self, *args, **kwargs)


toasts = []
pages.show_toast = lambda *_args, **_kwargs: toasts.append(True)

page = _Page([404, 0])
ProfilePage.set_endorsed(page, True)
check('a 404 on endorse retries with pin',
      page.client.paths == [
          ('POST', '/accounts/9/endorse'),
          ('POST', '/accounts/9/pin')])
check('the retry applies the relationship and clears busy',
      page.relationship.get('endorsed') is True and page._busy is False
      and toasts)

page = _Page([0])
ProfilePage.send_relationship(
    page, 'mute', form={'notifications': 'false'},
    done='Muted %s', undo=('unmute', None, 'Undo'))
check('mute posts the operation',
      page.client.paths == [('POST', '/accounts/9/mute')])
check('a successful relationship replaces the one on the page',
      page.relationship.get('id') == 'rel' and page._busy is False)

page = _Page([500])
ProfilePage.set_endorsed(page, False)
check('a non-404 stops the endorse ladder',
      page.client.paths == [('POST', '/accounts/9/unendorse')]
      and page.relationship == {} and page._busy is False
      and page.menu_updates == 1)

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
