#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rich answers, JSON builders and scenario helpers for the golden tests."""

import types
import urllib.parse

from gi.repository import Adw, GLib, Gtk

from src import linkrouting
from src import lists as _lists
from src import pagenav
from src import profile
from src.mastodon import MastodonClient
from src.statusactions import StatusActions
from src.statusmenu import StatusMenuButton
from src.timelinedata import StatusItem

from testkit.goldenkit import (INSTANCE, ME, TOKEN, FakeWindow, account,
                               scenario, status)

def menu_for(rec, own=False, info=None, **extra):
    client = rec.client(info)
    if own:
        extra.setdefault('account', account(ME, 'me'))
    item = StatusItem(status('100', **extra))
    actions = StatusActions()
    actions.bind(item, client)
    menu = StatusMenuButton(actions=actions)
    menu.bind(item, client)
    menu.prepare()
    return menu, item

def activate(menu, name, parameter=None):
    menu.activate_action('row.' + name, parameter)

class WithWindow:
    def __enter__(self):
        self.real = pagenav.window_of
        pagenav.window_of = lambda widget: FakeWindow()

    def __exit__(self, *_exc):
        pagenav.window_of = self.real

def page_for(rec, relationship=None, acct='ada', client=None, **extra):
    client = client or rec.client()
    page = profile.ProfilePage(
        client, account('7', acct, **extra), viewer_id=ME)
    rec.settle()
    page.relationship = relationship or {}
    page._relationship_known = True
    page._content = True
    page.update_relationship_ui()
    rec.clear()
    return page

def run_action(page, name, state=None):
    action = page.actions.lookup_action(name)
    if state is None:
        action.activate(None)
    else:
        action.change_state(GLib.Variant.new_boolean(state))

def api_wrapper(rec, name, *args, module='statuses', **kwargs):
    """A `src.api.<module>` function: the client is its first argument."""
    import importlib
    api = importlib.import_module('src.api.' + module)
    getattr(api, name)(rec.client(), *args, callback=lambda *_a: None,
                       **kwargs)

def fresh_wrapper(rec, name, *args, **kwargs):
    """A client that has not loaded its instance info, so that
    instance_info_async really sends."""
    client = MastodonClient(INSTANCE, token=TOKEN)
    getattr(client, name)(*args, callback=lambda *_a: None, **kwargs)

def row_for(page):
    row = Adw.ActionRow()
    page.list_box.append(row)
    page.rows.append(row)
    return row

def list_page(rec, factory):
    page = factory(rec.client())
    rec.settle()
    rec.clear()
    return page

def request_item(**extra):
    item = types.SimpleNamespace(actors=[account('7')],
                                 request_state=None, **extra)
    return item

def own_page(rec):
    page = profile.ProfilePage(rec.client(), account(ME, 'me'),
                               viewer_id=ME)
    rec.settle()
    rec.clear()
    return page

MISSING = object()

def announcement(announcement_id='1', **extra):
    data = {'id': announcement_id, 'content': '<p>maintenance</p>',
            'published_at': '2026-01-01T00:00:00.000Z',
            'updated_at': '2026-01-01T00:00:00.000Z', 'read': False,
            'all_day': False, 'mentions': [], 'emojis': [],
            'reactions': [{'name': '\U0001F44D', 'count': 1, 'me': True}]}
    data.update(extra)
    return data

def filter_json(filter_id='4', **extra):
    data = {'id': filter_id, 'title': 'Spoilers', 'context': ['home'],
            'filter_action': 'warn', 'expires_at': None, 'statuses': [],
            'keywords': [{'id': '9', 'keyword': 'spoiler',
                          'whole_word': True}]}
    data.update(extra)
    return data

def list_json(list_id='3', **extra):
    data = {'id': list_id, 'title': 'Friends', 'replies_policy': 'list',
            'exclusive': False}
    data.update(extra)
    return data

def scheduled_json(scheduled_id, when):
    return {'id': scheduled_id, 'scheduled_at': when,
            'params': {'text': 'later', 'visibility': 'public'},
            'media_attachments': []}

def conversation_json(conversation_id='20'):
    return {'id': conversation_id, 'unread': True,
            'accounts': [account('7')], 'last_status': status('300')}

def tag_json(name='golden', **extra):
    data = {'name': name, 'url': 'https://golden.example/tags/' + name,
            'history': [], 'following': False}
    data.update(extra)
    return data

def link_json():
    return {'url': 'https://news.example/a', 'title': 'News',
            'description': 'd', 'provider_name': 'News Example',
            'image': None, 'history': [], 'type': 'link'}

def policy_json():
    return {'for_not_following': 'accept', 'for_not_followers': 'accept',
            'for_new_accounts': 'accept', 'for_private_mentions': 'accept',
            'for_limited_accounts': 'accept',
            'summary': {'pending_requests_count': 2,
                        'pending_notifications_count': 3}}

def report_json(year=2025):
    return {'annual_reports': [{'year': year, 'data': {
        'top_statuses': {'by_favourites': '321'}}}],
        'accounts': [], 'statuses': []}

_RICH = {
    ('GET', '/api/v1/announcements'): lambda q: [announcement()],
    ('GET', '/api/v2/filters'): lambda q: [filter_json()],
    ('POST', '/api/v2/filters'): lambda q: filter_json('5'),
    ('PUT', '/api/v2/filters/4'): lambda q: filter_json(),
    ('GET', '/api/v1/lists'): lambda q: [list_json()],
    ('POST', '/api/v1/lists'): lambda q: list_json('6'),
    ('PUT', '/api/v1/lists/3'): lambda q: list_json(),
    ('GET', '/api/v1/lists/3/accounts'): lambda q: [account('7')],
    ('GET', '/api/v1/accounts/search'): lambda q: [account('8', 'bob')],
    ('GET', '/api/v1/accounts/7/lists'): lambda q: [{'id': '3'}],
    ('GET', '/api/v1/timelines/list/3'): lambda q: [status('100')],
    ('GET', '/api/v1/scheduled_statuses'): lambda q: [
        scheduled_json('70', '2030-06-01T10:00:00.000Z'),
        scheduled_json('71', '5000-01-01T00:00:00.000Z')],
    ('PUT', '/api/v1/scheduled_statuses/70'): lambda q: scheduled_json(
        '70', '2031-06-01T10:00:00.000Z'),
    ('GET', '/api/v2/search'): lambda q: {
        'accounts': [], 'hashtags': [], 'statuses': []},
    ('GET', '/api/v1/followed_tags'): lambda q: [tag_json()],
    ('GET', '/api/v1/trends/tags'): lambda q: [tag_json()],
    ('GET', '/api/v1/trends/links'): lambda q: [link_json()],
    ('GET', '/api/v1/trends/statuses'): lambda q: [status('100')],
    ('GET', '/api/v1/featured_tags'): lambda q: [],
    ('GET', '/api/v1/featured_tags/suggestions'): lambda q: [],
    ('GET', '/api/v1/tags/golden'): lambda q: tag_json(),
    ('GET', '/api/v2/notifications/policy'): lambda q: policy_json(),
    ('PATCH', '/api/v2/notifications/policy'): lambda q: policy_json(),
    ('GET', '/api/v1/notifications/requests'): lambda q: [
        {'id': '9', 'account': account('7'), 'notifications_count': 2}],
    ('GET', '/api/v1/notifications'): lambda q: [],
    ('GET', '/api/v1/conversations'): lambda q: [conversation_json()],
    ('POST', '/api/v1/conversations/20/read'): lambda q: dict(
        conversation_json(), unread=False),
    ('GET', '/api/v2/suggestions'): lambda q: [
        {'source': 'global', 'account': account('7')}],
    ('GET', '/api/v1/custom_emojis'): lambda q: [
        {'shortcode': 'blob', 'url': 'https://x/blob.png',
         'static_url': 'https://x/blob.png', 'visible_in_picker': True,
         'category': None}],
    ('GET', '/api/v1/annual_reports/2025'): lambda q: report_json(),
    ('GET', '/api/v1/annual_reports/2025/state'): lambda q: {
        'state': 'eligible'},
    ('GET', '/api/v1/timelines/home'): lambda q: [status('100')],
}

def rich(method, path, query):
    """The richer answer for a phase-2 scenario, or MISSING for the plain
    one."""
    found = _RICH.get((method, path))
    if found is not None:
        return found(query)
    return MISSING

def p2(function):
    """A scenario that runs with the richer answers."""
    import functools

    @functools.wraps(function)
    def run(rec):
        rec.rich = True
        return function(rec)
    return scenario(run)

def link_header(path, **params):
    return '<%s%s?%s>; rel="next"' % (
        INSTANCE, path, urllib.parse.urlencode(params))

class Anything:
    """A stand-in widget that accepts any call."""

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None

def bind_stub(cls, stub, *names):
    """Give *stub* the real methods *names* of *cls*."""
    for name in names:
        setattr(stub, name, types.MethodType(getattr(cls, name), stub))
    return stub


_lists.ListEditor.close = lambda self: None

linkrouting.launch_external = lambda *args: None

pagenav.open_status = lambda *args: None

pagenav.open_profile = lambda *args: None

def _announcements_page(rec):
    from src import announcements
    # An empty instance document, not the current-server default: the page
    # fetches it when there is none, and the golden records that request.
    page = announcements.AnnouncementsPage(rec.client(info={}))
    rec.settle()
    return page

def _lists_page(rec):
    from src import lists
    page = lists.ListsPage(rec.client())
    rec.settle()
    return page

def admin_account_json(account_id='31', **extra):
    data = {'id': account_id, 'username': 'ada', 'domain': None,
            'created_at': '2026-01-01T00:00:00.000Z', 'email': 'a@x.example',
            'ip': '192.0.2.1', 'ips': [], 'confirmed': True,
            'role': {'name': 'User', 'permissions': '0'},
            'suspended': True, 'silenced': True, 'disabled': True,
            'sensitized': True, 'approved': False,
            'invite_request': 'please', 'account': account('7')}
    data.update(extra)
    return data

def admin_report_json(report_id='12', **extra):
    data = {'id': report_id, 'action_taken': False, 'category': 'violation',
            'comment': '', 'forwarded': False,
            'created_at': '2026-01-02T00:00:00.000Z',
            'account': admin_account_json('30'),
            'target_account': admin_account_json('31'),
            'assigned_account': None, 'action_taken_by_account': None,
            'statuses': [], 'rules': [{'id': '1', 'text': 'Be kind'}]}
    data.update(extra)
    return data

_RICH.update({
    ('POST', '/api/v1/admin/measures'): lambda q: [],
    ('POST', '/api/v1/admin/dimensions'): lambda q: [],
    ('GET', '/api/v1/admin/reports'): lambda q: [admin_report_json()],
    ('GET', '/api/v2/admin/accounts'): lambda q: [admin_account_json()],
    ('GET', '/api/v1/admin/domain_blocks'): lambda q: [
        {'id': '41', 'domain': 'bad.example', 'severity': 'suspend'}],
    ('GET', '/api/v1/admin/domain_allows'): lambda q: [
        {'id': '51', 'domain': 'good.example'}],
    ('GET', '/api/v1/admin/email_domain_blocks'): lambda q: [
        {'id': '61', 'domain': 'spam.example', 'history': []}],
    ('GET', '/api/v1/admin/ip_blocks'): lambda q: [
        {'id': '71', 'ip': '192.0.2.0/24', 'severity': 'no_access',
         'comment': '', 'expires_at': None}],
})

def admin_dialog(rec):
    from src import admin
    # The window is today's date: pin it so the body is deterministic.
    admin.DashboardPage._window = lambda self: ('2026-01-01', '2026-01-30')
    return rec.keep(admin.AdminDialog(
        Gtk.Box(), rec.client(info={'rules': [
            {'id': '1', 'text': 'Be kind'}, {'id': '2', 'text': 'No spam'}]}),
        None))

def collection_json(collection_id='5', owner=ME, **extra):
    data = {'id': collection_id, 'name': 'Picks', 'description': 'Good ones',
            'url': 'https://golden.example/collections/' + collection_id,
            'uri': 'https://golden.example/c/' + collection_id,
            'account_id': owner, 'language': 'en', 'sensitive': False,
            'discoverable': True, 'tag': {'name': 'books'}, 'item_count': 2,
            'updated_at': '2026-01-03T00:00:00.000Z',
            'items': [{'id': '91', 'account_id': '7', 'state': 'accepted'},
                      {'id': '92', 'account_id': ME, 'state': 'accepted'}]}
    data.update(extra)
    return data

def collection_answer(collection):
    return {'collection': collection,
            'accounts': [account('7'), account('8', 'bob')]}

def poll_json(poll_id='55', **extra):
    data = {'id': poll_id, 'expires_at': '2099-01-01T00:00:00.000Z',
            'expired': False, 'multiple': False, 'votes_count': 0,
            'voters_count': 0, 'voted': False, 'own_votes': [],
            'options': [{'title': 'a', 'votes_count': 0},
                        {'title': 'b', 'votes_count': 0},
                        {'title': 'c', 'votes_count': 0}], 'emojis': []}
    data.update(extra)
    return data

_RICH.update({
    ('GET', '/api/v1/accounts/1/collections'): lambda q: {
        'collections': [collection_json('5')]},
    ('GET', '/api/v1/accounts/1/in_collections'): lambda q: {
        'collections': [collection_json('6', owner='7')]},
    ('GET', '/api/v1/collections/5'): lambda q: collection_answer(
        collection_json('5')),
    ('GET', '/api/v1/collections/6'): lambda q: collection_answer(
        collection_json('6', owner='7')),
    ('POST', '/api/v1/collections'): lambda q: collection_answer(
        collection_json('9')),
    ('PATCH', '/api/v1/collections/5'): lambda q: collection_answer(
        collection_json('5')),
    ('POST', '/api/v1/collections/5/items'): lambda q: {
        'collection_item': {'id': '93', 'account_id': '8',
                            'state': 'accepted'}},
    ('GET', '/api/v1/polls/55'): lambda q: poll_json(votes_count=2),
    ('POST', '/api/v1/polls/55/votes'): lambda q: poll_json(
        voted=True, own_votes=[1], votes_count=1),
})

class AsAccount:
    """The signed-in account the collection pages read off the window."""

    def __enter__(self):
        from src import collectionbase
        self.module = collectionbase
        self.real = collectionbase._current_account
        collectionbase._current_account = \
            lambda widget=None: account(ME, 'me')

    def __exit__(self, *_exc):
        self.module._current_account = self.real

def _report_dialog(rec, acct, **kwargs):
    from src import report
    info = {'rules': [{'id': '1', 'text': 'Be kind'},
                      {'id': '2', 'text': 'No spam'}]}
    dialog = rec.keep(report.ReportDialog(
        rec.client(info=info), acct, parent_widget=Gtk.Box(), **kwargs))
    dialog.force_close = lambda: None
    return dialog

def _profile_editor(rec, **extra):
    from src import profileedit
    account_json = account(
        ME, 'me', display_name='Ada Lovelace',
        avatar_description='', header_description='',
        show_featured=True, show_media=True, show_media_replies=False,
        source={'note': '', 'fields': [], 'privacy': 'public',
                'sensitive': False, 'language': 'en'})
    account_json.update(extra)
    info = {'api_versions': {'mastodon': 9}}
    dialog = rec.keep(profileedit.ProfileEditDialog(
        rec.client(info=info), account_json, Gtk.Box()))
    dialog.force_close = lambda: None
    return dialog

recorded = {}
