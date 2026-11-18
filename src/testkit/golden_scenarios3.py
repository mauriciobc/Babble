#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Golden request scenarios, part 3 of 3.

Imported by test-api-golden.py for the side effect of registering every
scenario in SCENARIOS, in the order the golden file records.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk

from testkit.goldenkit import (
    FakeWindow,
    account,
    check,
    scenario,
    status)
from testkit.goldenanswers import (
    AsAccount,
    _profile_editor,
    _report_dialog,
    admin_account_json,
    admin_dialog,
    admin_report_json,
    collection_json,
    link_header,
    link_json,
    p2,
    poll_json)
import json
import types
from src import explore

@p2
def api_timeline_source_id_paging(rec):
    from src.timelinepage import ApiTimelineSource
    for path, params in (('/timelines/home', None),
                         ('/timelines/public', {'local': 'true'}),
                         ('/timelines/public', None),
                         ('/timelines/tag/golden', None),
                         ('/timelines/list/3', None),
                         ('/accounts/7/statuses', {'pinned': 'true'})):
        source = ApiTimelineSource(rec.client(), path, params)
        for page in ({'limit': 20}, {'limit': 20, 'max_id': '50'},
                     {'limit': 20, 'since_id': '60'}):
            source.fetch(page, lambda c, r, _d=None: None)

@p2
def api_timeline_source_link_and_none_paging(rec):
    from src.timelinepage import ApiTimelineSource
    path = '/api/v1/trends/statuses'
    rec.queue[('GET', path)] = [([status('100')], link_header(
        path, offset='20', limit='20')), [status('101')]]
    source = ApiTimelineSource(rec.client(), '/trends/statuses',
                               paging='link')

    def done(_c, result, _d=None):
        source.finish(result)

    source.fetch({'limit': 20}, done)
    rec.settle()
    source.fetch({'limit': 20, 'max_id': '100'}, done)
    rec.settle()
    source.fetch({'limit': 20, 'since_id': '101'}, done)
    rec.settle()
    ApiTimelineSource(rec.client(), '/timelines/link',
                      {'url': 'https://news.example/a'},
                      paging='none').fetch({'limit': 20}, done)
    rec.settle()

@p2
def window_timeline_places(rec):
    from src.window import BabbleWindow
    stub = types.SimpleNamespace(client=rec.client())
    expected = {'favourites': 'link', 'bookmarks': 'link', 'public': 'id'}
    for kind, params in (('favourites', None), ('bookmarks', None),
                         ('public', {'local': 'true'}),
                         ('public', None)):
        page = BabbleWindow._timeline_place(
            stub, kind, params, 'edit-find-symbolic', 'Title', 'Description')
        rec.keep(page)
        check('%s place pages by %s' % (kind, expected[kind]),
              page.source.paging == expected[kind],
              '    got %r' % (page.source.paging,))
    rec.settle()

@p2
def conversations_load_mark_read_and_delete(rec):
    from src import conversations
    page = conversations.ConversationsPage(rec.client())
    rec.settle()
    row = page.rows[0]
    row.mark_read()
    rec.settle()
    row._delete_confirmed()
    rec.settle()

@p2
def explore_pages_load(rec):
    page = explore.ExplorePage(rec.client())
    rec.settle()
    for name in ('hashtags', 'news', 'for-you'):
        if name in page.bins:
            page._populate(name)
            rec.settle()
    rec.keep(explore.NewsPage(rec.client()))
    rec.keep(explore.SuggestionsPage(rec.client()))
    rec.keep(explore.LinkTimelinePage(rec.client(), link_json()))
    rec.settle()

@p2
def suggestions_row_dismiss(rec):
    page = explore.SuggestionsPage(rec.client())
    rec.settle()
    page.paged.rows[0]._on_dismiss(None)
    rec.settle()

@p2
def emoji_chooser_loads_custom_emojis(rec):
    from src import emojichooser
    emojichooser._cache.clear()
    emojichooser.load_custom_emojis(rec.client(), lambda emojis: None)
    rec.settle()

@p2
def wrapped_loads_a_report_and_its_top_post(rec):
    from src import wrapped
    rec.keep(wrapped.WrappedDialog(Gtk.Box(), rec.client(), 2025))
    rec.settle()

@p2
def wrapped_without_a_year_loads_the_index(rec):
    from src import wrapped
    rec.keep(wrapped.WrappedDialog(Gtk.Box(), rec.client()))
    rec.settle()

@p2
def wrapped_generates_an_eligible_report(rec):
    from src import wrapped
    rec.fail[('GET', '/api/v1/annual_reports/2025')] = [404]
    dialog = wrapped.WrappedDialog(Gtk.Box(), rec.client(), 2025)
    rec.settle()
    dialog._generate(2025)
    rec.settle()

@p2
def admin_dialog_probes_scopes_and_loads_the_dashboard(rec):
    dialog = admin_dialog(rec)
    rec.settle()
    dialog.authorize_scopes(None)
    rec.settle()

@p2
def admin_reports_list_and_resolved_filter(rec):
    dialog = admin_dialog(rec)
    rec.settle()
    page = dialog.show_tool('reports').content
    rec.settle()
    page.resolved_button.set_active(True)
    rec.settle()

@p2
def admin_report_dialog_category_assignment_and_resolve(rec):
    from src import adminreports
    dialog = admin_dialog(rec)
    rec.settle()
    rec.queue[('POST', '/api/v1/admin/reports/12/assign_to_self')] = [
        {'assigned_account': admin_account_json('30')}]
    report = adminreports.ReportDialog(dialog, admin_report_json())
    rec.settle()
    report.category_checks['spam'].set_active(True)
    rec.settle()
    report.category_checks['violation'].set_active(True)
    report._rule_checks['2'].set_active(True)
    rec.settle()
    report.assign_row._on_clicked(None)
    rec.settle()
    report.assign_row._on_clicked(None)
    rec.settle()
    report._on_resolve(None)
    rec.settle()
    reopened = adminreports.ReportDialog(
        dialog, admin_report_json(action_taken=True,
                                  action_taken_by_account=admin_account_json(
                                      '30')))
    rec.settle()
    reopened._on_reopen(None)
    rec.settle()

@p2
def admin_take_action_from_a_report_and_an_account(rec):
    from src import adminreports
    dialog = admin_dialog(rec)
    rec.settle()
    from_report = adminreports.TakeActionDialog(dialog, '31', '@ada', '12')
    from_report.checks['suspend'].set_active(True)
    from_report.comment.set_text('spam')
    from_report._on_submit(None)
    rec.settle()
    plain = adminreports.TakeActionDialog(dialog, '31', '@ada')
    plain.email.set_active(False)
    plain._on_submit(None)
    rec.settle()

@p2
def admin_accounts_list_filters_review_and_undo(rec):
    from src import adminaccounts
    dialog = admin_dialog(rec)
    rec.settle()
    page = dialog.show_tool('accounts').content
    rec.settle()
    page.origin.set_selected(1)
    page.status.set_selected(2)
    page.entries['username'].set_text('ada')
    page.entries['ip'].set_text('192.0.2.1')
    page._on_search()
    rec.settle()
    account_dialog = adminaccounts.AdminAccountDialog(dialog, admin_account_json())
    rec.settle()
    account_dialog._on_review(None, 'approve', 'Approve %s?', '_Approve')
    rec.settle()
    account_dialog._on_review(None, 'reject', 'Reject %s?', '_Reject')
    rec.settle()
    for endpoint in ('unsuspend', 'unsilence', 'enable', 'unsensitive'):
        adminaccounts.UndoRow(account_dialog, 'Undo', endpoint)._undo()
        rec.settle()
    account_dialog._on_take_action(None)
    rec.settle()

@p2
def admin_rule_pages_load_add_and_delete(rec):
    from src import adminfederation
    dialog = admin_dialog(rec)
    rec.settle()
    for tag, item_id in (('domain-blocks', '41'), ('domain-allows', '51'),
                         ('email-blocks', '61'), ('ip-blocks', '71')):
        page = dialog.show_tool(tag).content
        rec.settle()
        page.delete(page.list.rows[0], item_id, 'Remove?', '_Remove')
        rec.settle()
    dialog.show_tool('domain-allows').content._on_add('allowed.example')
    rec.settle()
    dialog.show_tool('email-blocks').content._on_add('mail.example')
    rec.settle()
    block = adminfederation.AddFederationBlockDialog(dialog)
    block.domain.set_text('bad.example')
    block.severity.set_selected(0)
    block.reject_reports.set_active(True)
    block._on_save(None)
    rec.settle()
    block = adminfederation.AddFederationBlockDialog(dialog)
    block.domain.set_text('worse.example')
    block.severity.set_selected(1)
    block._on_save(None)
    rec.settle()
    rule = adminfederation.AddIPBlockDialog(dialog)
    rule.ip.set_text('198.51.100.0/24')
    rule.checks['sign_up_block'].set_active(True)
    rule._on_save(None)
    rec.settle()
    rule = adminfederation.AddIPBlockDialog(dialog)
    rule.ip.set_text('203.0.113.7')
    rule.expiration.set_selected(0)
    rule._on_save(None)
    rec.settle()

@p2
def collections_page_loads_both_lists_and_refreshes(rec):
    from src import collectionactions, collectionbase
    collectionbase._ACCOUNTS.clear()
    page = rec.keep(collectionactions.CollectionsPage(rec.client()))
    rec.settle()
    page.refresh()
    rec.settle()

@p2
def collection_page_loads(rec):
    from src import collectionpage
    rec.keep(collectionpage.CollectionPage(rec.client(), '5'))
    rec.settle()

@p2
def collection_owner_deletes(rec):
    from src import collectionactions
    with AsAccount():
        actions = collectionactions.CollectionActions(
            Gtk.Box(), rec.client(), collection_json('5'),
            lambda: None, lambda: None)
        actions.delete()
    rec.settle()

@p2
def collection_member_removes_themselves(rec):
    from src import collectionactions
    with AsAccount():
        actions = collectionactions.CollectionActions(
            Gtk.Box(), rec.client(), collection_json('6', owner='7'),
            lambda: None, lambda: None)
        actions.remove_me()
    rec.settle()

@p2
def collection_edit_members_invite_remove_and_save(rec):
    from src import collectionedit
    dialog = rec.keep(collectionedit.CollectionEditDialog(
        rec.client(), collection_json('5'), on_saved=lambda: None))
    dialog.close = lambda: None
    rec.settle()
    dialog.search_row.set_text('bo')
    dialog._search_id += 1  # cancel the debounce timer
    dialog._run_search(dialog._search_id)
    rec.settle()
    dialog._invite(account('8', 'bob'), dialog._search_results[0])
    rec.settle()
    dialog._remove(account('7'))
    rec.settle()
    dialog.name_row.set_text('Better picks')
    dialog.sensitive_row.set_active(True)
    dialog._on_save(None)
    rec.settle()

@p2
def collection_edit_clearing_the_topic_sends_a_null_tag_name(rec):
    """Collection.tag_name= with nothing leaves the tag unset (Tag
    .find_or_create_by_names(nil) is []), and the permit list takes a
    null scalar: clearing the topic is `tag_name: null`."""
    from src import collectionedit
    dialog = rec.keep(collectionedit.CollectionEditDialog(
        rec.client(), collection_json('5'), on_saved=lambda: None))
    dialog.close = lambda: None
    rec.settle()
    dialog.topic_row.set_text('')
    dialog._on_save(None)
    rec.settle()
    check('clearing the topic is what the dialog sends',
          rec.log[-1]['method'] == 'PATCH'
          and rec.log[-1]['body'] is not None
          and 'null' in json.dumps(rec.log[-1]['body']),
          repr(rec.log[-1:]))

@p2
def collection_edit_saving_without_changes_sends_nothing_more(rec):
    from src import collectionedit
    dialog = rec.keep(collectionedit.CollectionEditDialog(
        rec.client(), collection_json('5'), on_saved=lambda: None))
    dialog.close = lambda: None
    rec.settle()
    dialog._on_save(None)
    rec.settle()

@p2
def collection_create_with_members(rec):
    from src import collectionedit
    dialog = rec.keep(collectionedit.CollectionEditDialog(
        rec.client(), None, on_saved=lambda: None))
    dialog.close = lambda: None
    # The default language follows the environment: pin it.
    dialog.language_row.set_selected(dialog.language_codes.index('en'))
    dialog.name_row.set_text('New picks')
    dialog.topic_row.set_text('books')
    dialog.unlisted_check.set_active(True)
    dialog._invite(account('8', 'bob'), Gtk.Button())
    dialog._on_save(None)
    rec.settle()
    bare = rec.keep(collectionedit.CollectionEditDialog(
        rec.client(), None, on_saved=lambda: None))
    bare.close = lambda: None
    bare.language_row.set_selected(bare.language_codes.index('en'))
    bare.name_row.set_text('Bare')
    bare._on_save(None)
    rec.settle()

@p2
def poll_refresh_and_vote(rec):
    from src import pollbox
    box = rec.keep(pollbox.PollBox())
    box.set_poll(poll_json(), client=rec.client())
    box._on_refresh(None)
    rec.settle()
    box.set_poll(poll_json(), client=rec.client())
    box._choices[1].set_active(True)
    box._on_vote(None)
    rec.settle()
    many = rec.keep(pollbox.PollBox())
    many.set_poll(poll_json(multiple=True), client=rec.client())
    many._choices[0].set_active(True)
    many._choices[2].set_active(True)
    many._on_vote(None)
    rec.settle()

@p2
def report_submit_spam_on_a_remote_account_with_a_post(rec):
    dialog = _report_dialog(rec, account('7', 'ada@remote.example'),
                            status=status('100'))
    dialog.category = 'spam'
    dialog._build_posts_page()
    rec.settle()
    dialog._build_comment_page()
    dialog.comment_buffer.set_text('  looks automated ')
    dialog.forward_row.set_active(False)
    dialog._submit()
    rec.settle()

@p2
def report_submit_violation_with_rules_and_a_collection(rec):
    dialog = _report_dialog(rec, account('7', 'ada'),
                            collection=collection_json('6', owner='7'))
    dialog.category = 'violation'
    dialog._build_rules_page()
    dialog.rule_checks['2'].set_active(True)
    dialog._build_comment_page()
    dialog._submit()
    rec.settle()

@scenario
def profile_edit_save_with_avatar_fields_and_tabs(rec):
    dialog = _profile_editor(rec)
    dialog.name_row.set_text('Ada King')
    dialog.add_field('Pronouns', 'they/them')
    dialog.avatar_alt.set_text('a portrait')
    dialog.tab_rows['show_media'].set_active(False)
    dialog.new_avatar = (b'\x89PNG-avatar', 'me.png', 'image/png')
    dialog.new_header = (b'\xff\xd8-header', 'banner.jpg', 'image/jpeg')
    dialog.on_save_clicked(None)
    rec.settle()

@scenario
def profile_edit_save_credentials_only(rec):
    dialog = _profile_editor(rec)
    dialog.name_row.set_text('Ada King')
    dialog.on_save_clicked(None)
    rec.settle()

@scenario
def profile_edit_save_profile_only(rec):
    dialog = _profile_editor(rec)
    dialog.tab_rows['show_featured'].set_active(False)
    dialog.on_save_clicked(None)
    rec.settle()

@scenario
def posting_defaults_sync(rec):
    from src import preferences

    class Settings:
        values = {'default-visibility': 'unlisted',
                  'default-language': 'pt',
                  'default-sensitive': True,
                  'default-quote-policy': 'followers'}

        def get_string(self, key):
            return self.values[key]

        def get_boolean(self, key):
            return self.values[key]

    class Window(FakeWindow):
        client = rec.client()

    settings = Settings()
    for key in settings.values:
        preferences.sync_posting_defaults(Window, key, settings)
    settings.values['default-visibility'] = 'direct'   # not a server default
    preferences.sync_posting_defaults(Window, 'default-visibility', settings)
    settings.values['default-sensitive'] = False
    preferences.sync_posting_defaults(Window, 'default-sensitive', settings)
    rec.settle()
