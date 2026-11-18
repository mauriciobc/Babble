#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Golden request scenarios, part 2 of 3.

Imported by test-api-golden.py for the side effect of registering every
scenario in SCENARIOS, in the order the golden file records.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, Gtk

from testkit.goldenkit import (
    INSTANCE,
    ME,
    account,
    check,
    scenario,
    status)
from testkit.goldenanswers import (
    Anything,
    WithWindow,
    _announcements_page,
    _lists_page,
    bind_stub,
    filter_json,
    link_header,
    list_json,
    own_page,
    p2,
    page_for)
import types
from src import explore, linkrouting
from src import notifications
from src import pagenav
from src import profilelookup
from src import statusactions
from src import statusviews

@scenario
def quote_card_resolves_the_quoted_post(rec):
    real = pagenav.open_status
    pagenav.open_status = lambda *args: None
    try:
        card = statusactions.QuoteCard(
            {'state': 'accepted', 'quoted_status_id': '55'}, rec.client())
        card.emit('clicked')
    finally:
        pagenav.open_status = real

@scenario
def profile_load_extras_for_another_account(rec):
    page = page_for(rec)
    page.load_extras()

@scenario
def profile_load_extras_for_the_own_account(rec):
    page = own_page(rec)
    page.load_extras()

@scenario
def profile_reload_account_another(rec):
    page_for(rec).reload_account()

@scenario
def profile_reload_account_own(rec):
    own_page(rec).reload_account()

@scenario
def profile_account_lists(rec):
    page = page_for(rec, followers_count=3, following_count=2)
    page.push_subpage = lambda title, subpage: None
    page.on_followers_clicked(None)
    page.on_following_clicked(None)
    page.on_familiar_clicked(None)
    page.on_endorsements_clicked(None)

@scenario
def status_edit_history_view(rec):
    view = statusviews.EditHistoryView(rec.client(), status('100'))
    rec.settle()
    view.refresh()

@scenario
def status_stats_view_tabs(rec):
    view = statusviews.StatusStatsView(
        rec.client(), status('100', quotes_count=1), 'favourites')
    rec.settle()
    for kind, _key in statusviews.STATS_TABS:
        view._make_tab(kind, '100')
        rec.settle()

@scenario
def mention_completer_searches_accounts(rec):
    from src.mentioncompleter import MentionCompleter
    view = Gtk.TextView()
    completer = MentionCompleter(rec.client(), view)
    for token in ('al', 'ada@remote.example'):
        completer._token = (0, token)
        completer._search()
        rec.settle()

@scenario
def fedicollections_fetch_accounts(rec):
    from src import collectionbase
    collectionbase._ACCOUNTS.clear()
    collectionbase.fetch_accounts(rec.client(), ['7', '8', '7'],
                                   lambda _accounts: None)

@scenario
def fedicollections_fetch_accounts_chunks_large_batches(rec):
    """GET /accounts?id[]= answers at most 40 ids (the server's
    DEFAULT_ACCOUNTS_LIMIT): 85 uncached ids go out as 40 + 40 + 5, and
    the merged answer keeps the order of the ids."""
    from src import collectionbase
    collectionbase._ACCOUNTS.clear()
    ids = [str(1000 + n) for n in range(85)]
    rec.queue[('GET', '/api/v1/accounts')] = [
        [account(i, 'u' + i) for i in ids[:40]],
        [account(i, 'u' + i) for i in ids[40:80]],
        [account(i, 'u' + i) for i in ids[80:]]]
    got = []
    collectionbase.fetch_accounts(rec.client(), ids, got.append)
    rec.settle()
    check('chunked lookup answers once with every account, in order',
          len(got) == 1 and list(got[0]) == ids, repr(got)[:200])
    check('chunked lookup asks at most 40 ids per request',
          [len([k for k, _v in e['query']]) for e in rec.log]
          == [40, 40, 5], repr([len(e['query']) for e in rec.log]))

@scenario
def fedicollections_fetch_accounts_keeps_what_arrived_and_says_so(rec):
    """A failed chunk does not lose the other chunks, and the reader is
    told once, not silently shown anonymous avatars."""
    from src import collectionbase
    collectionbase._ACCOUNTS.clear()
    ids = [str(2000 + n) for n in range(85)]
    # The failing answer does not consume a queued one.
    rec.queue[('GET', '/api/v1/accounts')] = [
        [account(i, 'u' + i) for i in ids[:40]],
        [account(i, 'u' + i) for i in ids[80:]]]
    rec.fail[('GET', '/api/v1/accounts')] = [200, 500, 200]
    got = []
    collectionbase.fetch_accounts(rec.client(), ids, got.append,
                                   widget=Gtk.Box())
    rec.settle()
    check('a failed chunk keeps the accounts of the others',
          len(got) == 1 and list(got[0]) == ids[:40] + ids[80:],
          repr(got)[:200])
    check('a failed chunk is reported once',
          len(rec.toasts) == 1, repr(rec.toasts))

@p2
def announcements_page_loads(rec):
    _announcements_page(rec)
    rec.settle()

@p2
def announcements_mark_read(rec):
    page = _announcements_page(rec)
    page.rows[0].mark_read()
    rec.settle()

@p2
def announcements_reaction_toggled_off_and_on(rec):
    page = _announcements_page(rec)
    bar = page.rows[0].reactions
    bar.buttons['\U0001F44D'].set_active(False)
    rec.settle()
    bar._on_emoji_picked(None, '\U0001F389')
    rec.settle()

@p2
def notification_controller_loads_announcements(rec):
    from src.notificationctrl import NotificationController
    stub = types.SimpleNamespace(client=rec.client(), banner=Anything())
    bind_stub(NotificationController, stub, 'on_announcements_loaded')
    NotificationController.load_announcements(stub)
    rec.settle()

@p2
def filters_list_loads(rec):
    from src import filters
    rec.keep(filters.FiltersList(rec.client(), Adw.NavigationView()))

@p2
def filter_create_with_keywords(rec):
    from src import filters
    page = filters.FilterEditPage(rec.client(), on_saved=lambda item: None)
    page.title_row.set_text('Spoilers')
    page._add_keyword_row({'keyword': 'spoiler', 'whole_word': True})
    page._add_keyword_row({'keyword': 'leak', 'whole_word': False})
    page._context_rows['notifications'].set_active(True)
    page.action_row.set_selected(1)
    page.expiry_row.set_selected(3)
    page._on_save_clicked(None)
    rec.settle()

@p2
def filter_edit_replaces_a_keyword(rec):
    from src import filters
    item = filter_json(expires_at='2030-01-01T00:00:00.000Z')
    page = filters.FilterEditPage(rec.client(), item,
                                  on_saved=lambda saved: None)
    page.title_row.set_text('Spoilers and leaks')
    page._on_keyword_removed(page._keyword_rows[0])
    page._add_keyword_row({'keyword': 'leak', 'whole_word': False})
    page._on_save_clicked(None)
    rec.settle()

@p2
def filter_delete(rec):
    from src import filters
    page = filters.FilterEditPage(rec.client(), filter_json(),
                                  on_saved=lambda saved: None)
    page._on_delete_clicked(None)
    rec.settle()

@p2
def lists_page_loads(rec):
    with WithWindow():
        _lists_page(rec)
    rec.settle()

@p2
def list_editor_create(rec):
    from src import lists
    with WithWindow():
        editor = lists.ListEditor(rec.client(),
                                  on_saved=lambda data, created: None)
        editor.title_row.set_text('Friends')
        editor.policy_row.set_selected(1)
        editor.exclusive_row.set_active(True)
        editor._on_save()
    rec.settle()

@p2
def list_editor_members_search_add_remove_save_delete(rec):
    from src import lists
    with WithWindow():
        editor = lists.ListEditor(rec.client(), list_json(),
                                  on_saved=lambda data, created: None,
                                  on_deleted=lambda data: None)
        rec.settle()
        editor.search_row.set_text('bo')
        editor._run_search()
        rec.settle()
        row = editor.result_rows[0]
        editor._on_add_member(Gtk.Button(), row)
        rec.settle()
        editor._on_remove_member(Gtk.Button(), editor.member_rows[0])
        rec.settle()
        editor.title_row.set_text('Close friends')
        editor._on_save()
        rec.settle()
        editor._on_delete(None)
    rec.settle()

@p2
def list_row_delete(rec):
    with WithWindow():
        page = _lists_page(rec)
        page.paged.rows[0]._on_delete(None)
    rec.settle()

@p2
def list_membership_dialog_toggles(rec):
    from src import lists
    with WithWindow():
        item = list_json()
        row = types.SimpleNamespace(handler=0)
        dialog = lists._MembershipDialog(rec.client(), account('7'))
        rec.settle()
        dialog._on_toggled(Gtk.CheckButton(active=True), item, row)
        rec.settle()
        dialog._on_toggled(Gtk.CheckButton(active=False), item, row)
    rec.settle()

@p2
def window_loads_pinned_list_titles(rec):
    from src.sidebarhost import SidebarHost
    stub = types.SimpleNamespace(
        window=types.SimpleNamespace(
            client=rec.client(), sidebar_rail=Anything(),
            navigation=types.SimpleNamespace(
                set_pinned_lists=lambda lists: None, pinned_lists={},
                pages={})),
        _items={})
    SidebarHost._load_pinned_list_titles(stub)
    rec.settle()

@p2
def list_timeline_first_page(rec):
    from src import lists
    with WithWindow():
        rec.keep(lists.ListTimelinePage(rec.client(), list_json()))
    rec.settle()

@p2
def scheduled_posts_reschedule_and_delete(rec):
    from src import scheduled
    page = scheduled.ScheduledPostsPage(rec.client())
    rec.settle()
    row = page.list.rows[0]
    row._on_rescheduled('2031-06-01T10:00:00.000Z')
    rec.settle()
    row._on_delete()

@p2
def draft_posts_load(rec):
    from src import scheduled
    rec.keep(scheduled.DraftPostsPage(rec.client()))
    rec.settle()

@p2
def compose_retries_the_scheduled_removal(rec):
    from src.compose import BabbleComposeView
    from src.composeschedule import _ScheduledReplacement
    stub = types.SimpleNamespace(
        client=rec.client(), _cleanup_busy=False, _cleanup_banner=None,
        _cleanup_cancellable=None,
        _replacement=_ScheduledReplacement('70', {}, None, 'Posted'),
        set_posting_ui=lambda busy: None,
        _complete_post=lambda status, message: status)
    stub.weak_ref = lambda: (lambda: stub)
    BabbleComposeView._retry_scheduled_removal(stub)
    rec.settle()

@p2
def search_page_requests(rec):
    from src import search
    page = search.SearchPage(rec.client())
    page.query = 'ada'
    page.search(resolve=True)
    rec.settle()
    page.search(resolve=False)
    rec.settle()
    page._resolve_url('https://remote.example/@ada/1', force=True)

@p2
def search_type_pages_load_with_offsets(rec):
    from src import search
    full = [account(str(1000 + n), 'user%d' % n) for n in range(20)]
    for kind in ('accounts', 'hashtags', 'statuses'):
        rec.queue[('GET', '/api/v2/search')] = [{kind: full}, {kind: []}] \
            if kind == 'accounts' else [{kind: []}]
        page = search.SearchTypePage(rec.client(), 'ada', kind)
        rec.settle()
        page._load()
        rec.settle()

@p2
def pages_resolve_uri(rec):
    linkrouting.resolve_uri(Gtk.Box(), rec.client(),
                      'https://remote.example/@ada/1')
    rec.settle()

@p2
def lookup_profile_resolves_a_remote_account(rec):
    rec.fail[('GET', '/api/v1/accounts/lookup')] = [404]
    rec.keep(profilelookup.LookupProfilePage(rec.client(), '@zed@remote.example'))
    rec.settle()

@p2
def search_provider_index_and_network(rec):
    from src import searchprovider
    provider = searchprovider.SearchProvider(None)
    provider._ensure_index(rec.client(), {'id': ME, 'instance': INSTANCE},
                           lambda: None)
    rec.settle()
    search = types.SimpleNamespace(
        answered=False, timer=0, client=rec.client(), query='ada',
        cancellable=Gio.Cancellable(), network=None, pending=5,
        invocation=None)
    provider._fire_network(search)
    rec.settle()

@p2
def hashtag_completer_searches_and_loads_the_tag_pool(rec):
    from src.hashtagcompleter import HashtagCompleter, TagPool
    view = Gtk.TextView()
    completer = HashtagCompleter(rec.client(), view)
    completer._token = (0, 'gold')
    completer._search()
    rec.settle()
    # prepare() would fill the popover, which needs a toplevel; the pool
    # it loads is the same object.
    TagPool().ensure(rec.client())
    rec.settle()

@p2
def composer_pasted_link_resolves_a_quote(rec):
    from src.composequote import QuotePaste
    client = rec.client()

    class Stub(QuotePaste):
        _quote_state_generation = 0
        quoted_status_id = None
        editing_id = None

        def weak_ref(self):
            return lambda: self

        def _attach_quote(self, status_id, uri):
            pass

        def _restore_url(self, uri, offset):
            pass

    stub = Stub()
    stub.client = client
    stub._begin_quote_attach('https://golden.example/@ada/100', 0)
    rec.settle()

@p2
def explore_trending_tags_load(rec):
    rec.keep(explore.HashtagsPage(rec.client()))

@p2
def home_timeline_notes_followed_tags(rec):
    from src.timelinepage import TimelinePage
    rec.keep(TimelinePage(rec.client()))
    rec.settle()

@p2
def catchup_followed_tags_trends_and_relationships(rec):
    import datetime
    from src.catchup import fetch
    tagged = status('100', tags=[{'name': 'golden'}],
                    created_at='2026-06-01T00:00:00.000Z')
    rec.queue[('GET', '/api/v1/timelines/home')] = [[tagged], []]
    # Two pages of followed tags: the Link cursor kept under
    # ``_more['followed_tags']`` is what asks for the second.
    rec.queue[('GET', '/api/v1/followed_tags')] = [
        ([{'name': 'golden'}],
         link_header('/api/v1/followed_tags', limit=200, max_id='77')),
        ([{'name': 'other'}], '')]
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc)
    got = []
    run = fetch.CatchUpRun(rec.client(), start, end, [ME],
                           done=got.append, pause_seconds=0)
    run.start()
    rec.settle()
    check('catch up run finishes', bool(got))
    rec.settle()
    pages_asked = [dict(entry['query']) for entry in rec.log
                   if entry['path'] == '/api/v1/followed_tags']
    check('followed tags follow the Link cursor to a second page',
          len(pages_asked) == 2 and pages_asked[1].get('max_id') == '77',
          '    asked: %r' % (pages_asked,))

@p2
def notification_policy_dialog_loads_and_saves(rec):
    from src import notificationpolicy
    dialog = notificationpolicy.NotificationPolicyDialog(rec.client())
    rec.settle()
    dialog.filter_rows['for_not_following'].set_selected(1)
    dialog.save()

@p2
def notification_controller_fetches_the_policy(rec):
    from src.notificationctrl import NotificationController
    stub = types.SimpleNamespace(client=rec.client(),
                                 requests_banner=Anything())
    bind_stub(NotificationController, stub, 'on_policy_loaded')
    NotificationController.fetch_policy(stub)
    rec.settle()

@p2
def notification_requests_page_rows_answer(rec):
    page = notifications.NotificationRequestsPage(rec.client())
    rec.settle()
    page.rows[0].answer('accept')
    rec.settle()

@p2
def desktop_notifier_polls(rec):
    from src.desktopnotify import DesktopNotifier
    client = rec.client()
    stub = types.SimpleNamespace(_polling_client=client, _last_id=None,
                                 _on_polled=lambda c, r, initial: None)
    DesktopNotifier._poll(stub, initial=True)
    stub._last_id = '55'
    DesktopNotifier._poll(stub)
    rec.settle()
