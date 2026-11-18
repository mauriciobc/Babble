# SPDX-License-Identifier: GPL-3.0-or-later

"""The home-filters tests' harness: post builders, a fake client that
answers the home timeline (and, in ``_RelationshipClient``, who the reader
follows) and the page builder. Test-only; never installed.
"""

from src import appsettings, timelinepage  # noqa: E402


failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def status(number):
    return {'id': '%05d' % number, 'content': '<p>post %d</p>' % number,
            'account': {'acct': 'user%d' % number, 'id': str(number)},
            'created_at': '2026-01-01T00:00:00.000Z'}


def boost(number, of):
    wrapper = status(number)
    wrapper['reblog'] = status(of)
    return wrapper


class _Client:
    token = 'token'
    instance_info = None
    instance = 'https://example.org'

    def __init__(self, pages_):
        self.pages = list(pages_)
        self.params = []

    def rate_limited(self):
        return False

    def request_async(self, _method, _path, params=None, callback=None,
                      **_kw):
        if _path != '/timelines/home':
            return None
        self.params.append(dict(params or {}))
        callback(self, self.pages.pop(0) if self.pages else [])

    def request_finish(self, result):
        return result

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def home_page(client, hide_boosts):
    appsettings.settings().set_boolean('hide-boosts-home', hide_boosts)
    page = timelinepage.TimelinePage(client, mode='home', autoload=False)
    page.ensure_loaded()
    return page


def ids(page):
    return [page.model.get_item(i).raw['id']
            for i in range(page.model.get_n_items())
            if hasattr(page.model.get_item(i), 'raw')]


# Hiding replies from non-follows asks the server who the authors are.
# The real api.accounts.relationships_finish returns the first entry only
# (the profile page asks about one account), so the page has to read the
# whole list with relationships_all_finish; reading the first entry raised
# and left the page on "loading" forever.
def reply(number, author, parent=1):
    data = status(number)
    data['in_reply_to_id'] = '%05d' % parent
    data['account'] = {'acct': 'user%d' % author, 'id': str(author)}
    return data


class _RelationshipClient(_Client):
    def request_async(self, method, path, params=None, callback=None, **_kw):
        if path != '/accounts/relationships':
            return super().request_async(method, path, params=params,
                                         callback=callback, **_kw)
        callback(self, [{'id': i, 'following': i == '21'}
                        for i in params['id[]']])

    def request_finish(self, result):
        return result
