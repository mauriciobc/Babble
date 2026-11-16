#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Golden request scenarios, part 1 of 3.

Imported by test-api-golden.py for the side effect of registering every
scenario in SCENARIOS, in the order the golden file records.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk

from testkit.goldenkit import (
    INSTANCE,
    ME,
    account,
    bar,
    check,
    scenario,
    status)
from testkit.goldenanswers import (
    WithWindow,
    activate,
    api_wrapper,
    fresh_wrapper,
    list_page,
    menu_for,
    page_for,
    request_item,
    row_for,
    run_action)
import types
from src.mastodon import MastodonClient
from src.timelinebase import QUOTE_POLICIES
from src import explore, hashtaglists
from src import hashtags
from src import notificationrow
from src import notifications
from src import relations
from src import statusactions

@scenario
def bar_favourite(rec):
    actions, _item, _client = bar(rec)
    actions.favourite_button.emit('clicked')

@scenario
def bar_unfavourite(rec):
    actions, _item, _client = bar(rec, favourited=True, favourites_count=1)
    actions.favourite_button.emit('clicked')

@scenario
def bar_favourite_fails_and_rolls_back(rec):
    actions, item, client = bar(rec)
    rec.fail[('POST', '/api/v1/statuses/100/favourite')] = [500]
    actions.favourite_button.emit('clicked')
    rec.settle()
    check('favourite rolled back after a 500', item.status['favourited']
          is False)

@scenario
def bar_boost(rec):
    actions, _item, _client = bar(rec)
    actions.boost_button.emit('clicked')

@scenario
def bar_unboost(rec):
    actions, _item, _client = bar(rec, reblogged=True, reblogs_count=1)
    actions.boost_button.emit('clicked')

@scenario
def bar_unboost_then_undo_reboosts(rec):
    actions, _item, _client = bar(rec, reblogged=True, reblogs_count=1)
    actions.boost_button.emit('clicked')
    rec.settle()
    rec.press_undo()

@scenario
def bar_boost_with_visibility_dialog(rec):
    statusactions._flag = lambda name: name == 'advanced-boost-dialog'
    shown = []
    real_present = Adw.AlertDialog.present
    Adw.AlertDialog.present = lambda self, parent=None: shown.append(self)
    try:
        actions, _item, _client = bar(rec, visibility='unlisted')
        actions.boost_button.emit('clicked')
        shown[-1].emit('response', 'boost')
    finally:
        Adw.AlertDialog.present = real_present
        statusactions._flag = lambda name: False

@scenario
def bar_bookmark(rec):
    actions, _item, _client = bar(rec)
    actions.bookmark_button.emit('clicked')

@scenario
def bar_unbookmark(rec):
    actions, _item, _client = bar(rec, bookmarked=True)
    actions.bookmark_button.emit('clicked')

@scenario
def menu_icon_row_favourite_boost_bookmark(rec):
    menu, _item = menu_for(rec)
    activate(menu, 'favourite')
    activate(menu, 'boost')
    activate(menu, 'bookmark')

@scenario
def menu_translate(rec):
    info = {'configuration': {'translation': {'enabled': True}}}
    menu, _item = menu_for(rec, info=info)
    activate(menu, 'translate')

@scenario
def menu_mute_conversation(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True)
        activate(menu, 'mute-conversation')

@scenario
def menu_unmute_conversation(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True, muted=True)
        activate(menu, 'mute-conversation')

@scenario
def menu_pin(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True)
        activate(menu, 'pin')

@scenario
def menu_unpin(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True, pinned=True)
        activate(menu, 'pin')

@scenario
def menu_quote_policy(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True, quote_approval={
            'automatic': [], 'manual': [], 'current_user': 'automatic'})
        activate(menu, 'quote-policy',
                 GLib.Variant.new_string(QUOTE_POLICIES[-1][0]))

@scenario
def menu_revoke_quote(rec):
    with WithWindow():
        quoted = status('55', account=account(ME, 'me'))
        menu, _item = menu_for(rec, quote={
            'state': 'accepted', 'quoted_status': quoted})
        activate(menu, 'revoke-quote')

@scenario
def menu_edit_loads_source(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True)
        activate(menu, 'edit')

@scenario
def menu_delete_after_toast_times_out(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True)
        activate(menu, 'delete')
        rec.settle()
        check('delete sends nothing while the toast is up', not rec.log)
        rec.time_out()

@scenario
def menu_delete_undone(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True)
        activate(menu, 'delete')
        rec.press_undo()
        check('delete undone sends nothing', not rec.log)

@scenario
def menu_delete_and_redraft(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True)
        activate(menu, 'redraft')

@scenario
def menu_mute_author_then_undo(rec):
    with WithWindow():
        menu, _item = menu_for(rec)
        activate(menu, 'mute')
        rec.settle()
        rec.press_undo()

@scenario
def menu_block_author(rec):
    with WithWindow():
        menu, _item = menu_for(rec)
        activate(menu, 'block')

@scenario
def profile_follow(rec):
    page = page_for(rec)
    page.on_follow_clicked(None)

@scenario
def profile_unfollow_then_undo_follows(rec):
    page = page_for(rec, {'following': True})
    page.on_follow_clicked(None)
    rec.settle()
    rec.press_undo()

@scenario
def profile_unfollow_locked_account(rec):
    page = page_for(rec, {'following': True}, locked=True)
    page.on_follow_clicked(None)

@scenario
def profile_withdraw_follow_request(rec):
    page = page_for(rec, {'requested': True})
    page.on_follow_clicked(None)

@scenario
def profile_block(rec):
    page = page_for(rec)
    run_action(page, 'block')

@scenario
def profile_unblock_then_undo_blocks(rec):
    page = page_for(rec, {'blocking': True})
    page.on_follow_clicked(None)
    rec.settle()
    rec.press_undo()

@scenario
def profile_mute(rec):
    page = page_for(rec)
    run_action(page, 'mute')

@scenario
def profile_unmute(rec):
    page = page_for(rec, {'muting': True})
    run_action(page, 'unmute')

@scenario
def profile_hide_and_show_boosts(rec):
    page = page_for(rec, {'following': True})
    run_action(page, 'hide-boosts')
    rec.settle()
    run_action(page, 'show-boosts')

@scenario
def profile_notify_on_and_off(rec):
    page = page_for(rec, {'following': True})
    run_action(page, 'notify', True)
    rec.settle()
    run_action(page, 'notify', False)

@scenario
def profile_remove_follower(rec):
    page = page_for(rec, {'followed_by': True})
    run_action(page, 'remove-follower')

@scenario
def profile_endorse_and_unendorse(rec):
    page = page_for(rec, {'following': True})
    run_action(page, 'endorse', True)
    rec.settle()
    run_action(page, 'endorse', False)

@scenario
def profile_endorse_404_falls_back_to_pin(rec):
    page = page_for(rec, {'following': True})
    rec.fail[('POST', '/api/v1/accounts/7/endorse')] = [404]
    run_action(page, 'endorse', True)

@scenario
def profile_unendorse_404_falls_back_to_unpin(rec):
    page = page_for(rec, {'following': True})
    rec.fail[('POST', '/api/v1/accounts/7/unendorse')] = [404]
    run_action(page, 'endorse', False)

@scenario
def profile_endorse_on_a_legacy_server(rec):
    client = rec.client({'api_versions': {'mastodon': 5}})
    page = page_for(rec, {'following': True}, client=client)
    run_action(page, 'endorse', True)

@scenario
def profile_private_note(rec):
    page = page_for(rec)

    class Dialog:
        save_button = type('Button', (), {
            'set_sensitive': lambda self, value: None})()

        def close(self):
            pass

    page.save_private_note(Dialog(), 'a note')

@scenario
def profile_block_domain(rec):
    page = page_for(rec, acct='zed@remote.example')
    page.confirm_domain_block(True)

@scenario
def profile_unblock_domain(rec):
    page = page_for(rec, {'domain_blocking': True},
                    acct='zed@remote.example')
    page.confirm_domain_block(False)

@scenario
def profile_unblock_domain_then_undo_blocks(rec):
    page = page_for(rec, {'domain_blocking': True},
                    acct='zed@remote.example')
    page.confirm_domain_block(False)
    rec.settle()
    rec.press_undo()

@scenario
def client_account_wrappers(rec):
    api_wrapper(rec, 'verify_credentials_async', module='accounts')
    api_wrapper(rec, 'lookup_async', 'ada@remote.example', module='accounts')
    api_wrapper(rec, 'statuses_async', '7', params={'limit': 20},
                module='accounts')
    api_wrapper(rec, 'relationships_async', ['7', '8'], module='accounts')
    api_wrapper(rec, 'follow_async', '7', reblogs=True, notify=False,
                module='relationships')
    api_wrapper(rec, 'follow_async', '7', reblogs=False, notify=True,
                module='relationships')
    api_wrapper(rec, 'unfollow_async', '7', module='relationships')

@scenario
def client_status_wrappers(rec):
    api_wrapper(rec, 'context_async', '100')
    api_wrapper(rec, 'favourite_async', '100')
    api_wrapper(rec, 'unfavourite_async', '100')
    api_wrapper(rec, 'reblog_async', '100')
    api_wrapper(rec, 'reblog_async', '100', visibility='private')
    api_wrapper(rec, 'unreblog_async', '100')
    api_wrapper(rec, 'bookmark_async', '100')
    api_wrapper(rec, 'unbookmark_async', '100')

@scenario
def client_post_reply_edit(rec):
    api_wrapper(rec, 'post_async', 'hello',
                params={'visibility': 'unlisted', 'sensitive': True,
                        'media_ids': ['1', '2'],
                        'poll': {'options': ['a', 'b'], 'expires_in': 300}})
    api_wrapper(rec, 'reply_async', 100, 'answer',
                params={'visibility': 'public'})
    # The composer hands over its whole draft; an edit keeps only the keys
    # PUT /statuses/:id permits (audit 5f), so the rest never reaches the wire.
    api_wrapper(rec, 'edit_async', '100', 'edited',
                params={'spoiler_text': 'cw', 'visibility': 'public',
                        'in_reply_to_id': '7', 'quoted_status_id': '8',
                        'scheduled_at': '2030-01-01T00:00:00Z',
                        'quote_approval_policy': 'nobody'})

@scenario
def client_media_multipart(rec):
    api_wrapper(rec, 'upload_async', b'\x89PNG-bytes', 'pic.png',
                'image/png', description='alt text', module='media')
    api_wrapper(rec, 'upload_async', GLib.Bytes.new(b'abc'), 'a.ogg',
                'audio/ogg', module='media')
    api_wrapper(rec, 'status_async', '500', module='media')

@scenario
def client_generic_request(rec):
    client = rec.client()
    client.request_async('GET', '/bookmarks', params={'limit': 20},
                         callback=lambda *_a: None)
    client.request_async('POST', '/api/v1/statuses/1/translate',
                         form={'lang': 'en'}, callback=lambda *_a: None)
    client.request_async('PUT', '/statuses/1/interaction_policy',
                         json_body={'x': [1, 2]}, callback=lambda *_a: None)

@scenario
def client_instance_markers_timelines_trends(rec):
    fresh_wrapper(rec, 'instance_info_async')
    api_wrapper(rec, 'get_async', ['home', 'notifications'],
                module='markers')
    api_wrapper(rec, 'home_async', params={'limit': 20}, module='timelines')
    api_wrapper(rec, 'public_async', params={'local': 'true', 'limit': 20},
                module='timelines')
    api_wrapper(rec, 'statuses_async', params={'limit': 10},
                module='trends')

@scenario
def client_notification_wrappers(rec):
    api_wrapper(rec, 'index_async', params={'limit': 30},
                module='notifications')
    api_wrapper(rec, 'unread_count_async', params={'limit': 100},
                module='notifications')
    api_wrapper(rec, 'clear_async', module='notifications')
    api_wrapper(rec, 'dismiss_async', '12', module='notifications')
    api_wrapper(rec, 'save_async', 'notifications', '34', module='markers')

@scenario
def client_oauth_wrappers(rec):
    client = MastodonClient(INSTANCE)
    client.register_app_async(callback=lambda *_a: None)
    client.register_app_async('read', loopback='http://127.0.0.1:4242/cb',
                              callback=lambda *_a: None)
    client.client_id = 'cid'
    client.client_secret = 'csecret'
    client.request_token_async('the-code', callback=lambda *_a: None)
    client.revoke_token_async('tok', callback=lambda *_a: None)

@scenario
def relations_follow_request_authorize_and_reject(rec):
    page = list_page(rec, relations.FollowRequestsPage)
    page.answer(row_for(page), account('7'), 'authorize')
    rec.settle()
    page.answer(row_for(page), account('8', 'bob'), 'reject')

@scenario
def relations_unmute(rec):
    page = list_page(rec, relations.MutedAccountsPage)
    page.unmute(row_for(page), account('7'))

@scenario
def relations_unblock(rec):
    page = list_page(rec, relations.BlockedAccountsPage)
    page.unblock(row_for(page), account('7'))

@scenario
def relations_unblock_domain(rec):
    page = list_page(rec, relations.BlockedDomainsPage)
    page.unblock(row_for(page), 'remote.example')

@scenario
def relations_lists_load(rec):
    for factory in (relations.FollowRequestsPage,
                    relations.MutedAccountsPage,
                    relations.BlockedAccountsPage,
                    relations.BlockedDomainsPage):
        factory(rec.client())
        rec.settle()

@scenario
def notification_answer_follow_request(rec):
    for verb in ('authorize', 'reject'):
        row = notificationrow.NotificationRow()
        row.client = rec.client()
        row.item = None
        row._request_buttons = (Gtk.Button(), Gtk.Button())
        row._answer_request(request_item(), verb)
        rec.settle()

@scenario
def notification_follow_back(rec):
    row = notificationrow.NotificationRow()
    row.client = rec.client()
    row.item = None
    row._follow_button = Gtk.Button()
    row._follow_back(request_item(), account('7'), (INSTANCE, '7'))

@scenario
def notification_request_row_accept_and_dismiss(rec):
    page = list_page(rec, notifications.NotificationRequestsPage)
    request = {'id': '9', 'account': account('7'),
               'notifications_count': 2}
    for verb in ('accept', 'dismiss'):
        row = notifications.NotificationRequestRow(page, request)
        page.list_box.append(row)
        page.rows.append(row)
        row.answer(verb)
        rec.settle()

@scenario
def suggestion_row_follow_and_dismiss(rec):
    page = types.SimpleNamespace(client=rec.client())
    holder = Gtk.Box()
    holder.client = page.client
    holder.paged = types.SimpleNamespace(remove_row=lambda row: None)
    row = explore.SuggestionRow(holder, {'account': account('7'),
                                         'source': 'global'})
    row._on_follow(row.follow_button)
    rec.settle()
    row._on_dismiss(row.dismiss_button)

@scenario
def hashtag_page_follow_unfollow_feature(rec):
    with WithWindow():
        page = hashtags.HashtagPage(rec.client(), ['golden'])
        rec.settle()
        page._on_follow_clicked(page.follow_button)
        rec.settle()
        page._on_follow_clicked(page.follow_button)
        rec.settle()
        page._on_feature()
        rec.settle()
        page.featured_id = '5'
        page._on_feature()

@scenario
def hashtag_tag_row_unfollow_then_undo(rec):
    with WithWindow():
        holder = Gtk.Box()
        holder.client = rec.client()
        holder.paged = types.SimpleNamespace(remove_row=lambda row: None)
        holder.refresh = lambda: None
        row = hashtaglists.TagRow(holder, {'name': 'golden', 'history': []})
        row._on_unfollow(row.unfollow)
        rec.settle()
        rec.press_undo()

@scenario
def followed_hashtags_page_loads(rec):
    with WithWindow():
        hashtaglists.FollowedHashtagsPage(rec.client())
        rec.settle()
