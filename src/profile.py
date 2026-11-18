# SPDX-License-Identifier: GPL-3.0-or-later

"""Profile view: account header (counts, badges, bio, fields, familiar
followers, featured hashtags and profiles), the relationship actions of
Tuba's profile menu and the Posts / Posts & Replies / Media tabs rendered
with the shared timeline components."""

import gi

import urllib.parse

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from .i18n import gettext as _, ngettext

from gi.repository import (Adw, GLib, GObject, Gtk,  # noqa: E402
                           Pango)

from . import accountrows, feedback, pagenav, uihelpers  # noqa: E402
from .api import accounts as api_accounts  # noqa: E402
from .capabilities import ENDORSEMENTS_API_VERSION  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402
from .profileactions import (ProfileActions,  # noqa: E402
                              endorse_action_names)  # noqa: F401
from .profileheader import ProfileHeader, plain_name  # noqa: E402
from .mediacache import AvatarCache  # noqa: E402
from .timelinebase import COLUMN_WIDTH  # noqa: E402
from .profiledialogs import _request_result  # noqa: E402
from .profilefeed import (AccountStatuses,
    ProfileDialog,
    ProfileTimeline)  # noqa: E402
def push_profile(view, client, account, acct=None):
    """Show *account* (dict) or the *acct* handle in a profile sheet over
    the window that owns *view*."""
    from .profilelookup import LookupProfilePage
    root = view.get_root()
    viewer = getattr(root, 'account', None) or {}
    viewer_id = viewer.get('id')
    if account is None:
        page = LookupProfilePage(client, acct, viewer_id=viewer_id)
        title = '@%s' % (acct or '')
    else:
        page = ProfilePage(client, account, viewer_id=viewer_id)
        title = account.get('display_name') or account.get('acct', '')
    visible_dialog = getattr(root, 'get_visible_dialog', None)
    current = visible_dialog() if visible_dialog is not None else None
    if isinstance(current, ProfileDialog):
        current.close()
    ProfileDialog(view, page, title).present(root)


ENDORSEMENT_PREVIEW = 3
FAMILIAR_NAMES = 3
class ProfilePage(ProfileActions, Adw.Bin):
    """Profile / Posts / Pinned / Media tabs: the account header, then
    the account's timelines.

    The header bar carries the tab switcher; below 550sp the switcher
    moves to a bottom bar. A second bottom bar holds the relationship
    button (Follow, Following, Requested, Follow Back, Blocked or, on
    your own profile, Edit Profile) and the overflow menu."""

    __gtype_name__ = 'BabbleProfilePage'

    def __init__(self, client, account, viewer_id=None, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.account = account
        self.viewer_id = viewer_id
        self.relationship = {}
        self._relationship_known = False
        self.menu_button = None
        self._busy = False
        self._content = False
        self._generation = 0
        self._follow_mode = None
        self.badge_box = None
        self.note_list = None
        self.header = ProfileHeader(self)

        self.view_stack = Adw.ViewStack()
        self.switcher = Adw.ViewSwitcher(
            stack=self.view_stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.switcher_bar = Adw.ViewSwitcherBar(stack=self.view_stack)
        self.header_bar = Adw.HeaderBar(title_widget=self.switcher)

        self.follow_button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.follow_button.add_css_class('pill')
        connect_weak(self.follow_button, 'clicked', self.on_follow_clicked)
        # A plain box, not a GtkActionBar: the bar is only ever
        # constructed with revealed=self._signed_in() and never animated
        # after, and the switcher bar above goes into the same slot as a
        # bare widget. One bar type for one slot.
        self.action_bar = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        self.action_bar.append(self.follow_button)
        if self._signed_in():
            label = _('More Options')
            self.menu_button = Gtk.MenuButton(
                icon_name='view-more-symbolic', valign=Gtk.Align.CENTER)
            self.menu_button.add_css_class('flat')
            uihelpers.set_accessible_label(self.menu_button, label)
            self.action_bar.append(self.menu_button)

        self._build_actions()

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)

        self.loading_page = uihelpers.make_loading_page(_('Loading Profile'))
        self.stack.add_named(self.loading_page, 'loading')

        self.error_page = uihelpers.make_error_page(
            _('Could Not Load Profile'), self.on_retry)
        self.stack.add_named(self.error_page, 'error')

        self.scrolled = Gtk.ScrolledWindow(vexpand=True)
        self.scrolled.set_policy(Gtk.PolicyType.NEVER,
                                 Gtk.PolicyType.AUTOMATIC)
        self.scrolled.add_css_class('profile-page-scroller')
        # 660px content column, centered in the pane and narrowing with
        # it. The header is a tab of its own rather than a hero above the
        # timelines: each timeline scrolls itself, so under a shared
        # scroller a long bio or many fields left the posts a sliver
        # below the fold.
        self.holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                              spacing=12)
        self.scrolled.set_child(Adw.Clamp(maximum_size=COLUMN_WIDTH,
                                          tightening_threshold=COLUMN_WIDTH,
                                          child=self.holder))
        self.view_stack.add_titled(self.scrolled, 'profile', _('Profile'))
        self.view_stack.get_page(self.scrolled).set_icon_name(
            'avatar-default-symbolic')
        self.view_stack.set_vexpand(True)
        self.view_stack.set_hexpand(True)
        self.stack.add_named(self.view_stack, 'content')

        toolbar = Adw.ToolbarView(content=self.stack)
        toolbar.add_top_bar(self.header_bar)
        toolbar.add_bottom_bar(self.switcher_bar)
        toolbar.add_bottom_bar(self.action_bar)

        no_title = GObject.Value()
        no_title.init(Gtk.Widget)
        narrow = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse('max-width: 550sp'))
        narrow.add_setter(self.switcher_bar, 'reveal', True)
        narrow.add_setter(self.header_bar, 'title-widget', no_title)
        breakpoint_bin = Adw.BreakpointBin(child=toolbar)
        # The page must fit the 360 px window it is presented in, minus
        # the window's own padding: asking for 360 made the profile sheet
        # (and its AdwBottomSheet) overflow by 10 px.
        breakpoint_bin.set_size_request(340, 294)
        breakpoint_bin.add_breakpoint(narrow)
        self.set_child(breakpoint_bin)

        self.connect('notify::root', self._on_root_changed)
        if account.get('id') is not None:
            self.on_account_ready()
        else:
            self.set_stack_state('loading')

    # Identity helpers

    def _signed_in(self):
        return bool(getattr(self.client, 'token', None))

    def _viewer_id(self):
        if self.viewer_id is None:
            window = pagenav.window_of(self)
            self.viewer_id = (getattr(window, 'account', None)
                              or {}).get('id')
        return self.viewer_id

    def _is_self(self):
        mine = self._viewer_id()
        return mine is not None and mine == self.account.get('id')

    def _acct(self):
        return '@%s' % self.account.get('acct', '')

    def _instance_host(self):
        return urllib.parse.urlsplit(
            getattr(self.client, 'instance', '') or '').hostname

    def _full_handle(self):
        acct = self.account.get('acct', '')
        if '@' in acct:
            return '@' + acct
        return '@%s@%s' % (acct, self._instance_host() or '')

    def _remote_domain(self):
        """Domain of a remote account, None for accounts on our server."""
        acct = self.account.get('acct', '')
        if '@' not in acct:
            return None
        domain = acct.split('@', 1)[1]
        return None if domain == self._instance_host() else domain

    def _on_root_changed(self, *_args):
        if self.get_root() is not None and self.account.get('id') is not None:
            self.update_follow_button()
            self.update_menu()
            self.update_badges()

    def on_account_ready(self):
        """The account dict is complete: build everything that hangs off
        it (header, tabs, relationship, fresh copy of the account).

        Opening a profile currently costs about eight REST calls:
        relationship, account refresh, familiar followers (when signed in),
        featured tags, endorsements, and the three tab timelines each
        fetch their first page even when ``Adw.ViewStack`` is still on
        Posts (ARCH-10)."""
        self.refresh_header(self.account)
        self.open_tabs()
        self.load_relationship()
        self.reload_account()

    def push_subpage(self, title, child):
        """Push *child* inside the profile sheet's own navigation when
        there is one, else onto the window's."""
        view = self.get_ancestor(Adw.NavigationView)
        if view is None:
            pagenav.push_page(self, title, child)
            return
        view.push(pagenav.make_nav_page(title, child))

    # Header (forwards to ProfileHeader)

    def refresh_header(self, account):
        """(Re)build the header above the tab stack for *account*."""
        self.header.refresh(account)

    def update_badges(self):
        """Account-type and relationship chips plus the private note."""
        self.header.update_badges()

    def _on_note_activated(self, _listbox, _row):
        self.edit_private_note()

    @staticmethod
    def _on_account_row_activated(_listbox, row):
        callback = getattr(row, 'on_activate', None)
        if callback is not None:
            callback()

    def on_posts_clicked(self, _button):
        self.view_stack.set_visible_child_name('posts')

    def on_followers_clicked(self, _button):
        count = self.account.get('followers_count') or 0
        self._push_accounts(
            _('Followers'), 'followers',
            _('This user has chosen to not make this information available')
            if count else _('No Followers'))

    def on_following_clicked(self, _button):
        count = self.account.get('following_count') or 0
        self._push_accounts(
            _('Following'), 'following',
            _('This user has chosen to not make this information available')
            if count else _("This User Doesn't Follow Anyone Yet"))

    def _push_accounts(self, title, kind, empty_title):
        page = accountrows.AccountListPage(
            self.client, api_accounts.list_path(kind, self.account['id']),
            empty_title=empty_title)
        self.push_subpage(title, page)

    # Extras: familiar followers, featured hashtags, endorsed accounts

    def load_extras(self):
        account_id = self.account.get('id')
        if account_id is None:
            return
        generation = self._generation

        def handler(callback):
            def _on_loaded(client, result, _data=None):
                if generation != self._generation:
                    return
                callback(client, result)
            return _on_loaded

        if self._signed_in() and not self._is_self():
            api_accounts.familiar_followers_async(
                self.client, [account_id],
                callback=handler(self._on_familiar))
        api_accounts.featured_tags_async(
            self.client, account_id, callback=handler(self._on_featured_tags))
        # GET /accounts/:id/endorsements is Mastodon 4.4.0. Without the
        # gate every profile on an older server raised a "Could not load
        # featured profiles" toast from _request_result. A server that
        # says nothing is told no (when_unknown=False): the section is
        # decoration, and its absence is invisible.
        if self.client.is_supported(ENDORSEMENTS_API_VERSION):
            api_accounts.endorsements_async(
                self.client, account_id, params={'limit': 80},
                callback=handler(self._on_endorsements))

    def _on_familiar(self, client, result, _data=None):
        data = _request_result(client, result,
                               api_accounts.familiar_followers_finish, self,
                               _('Could not load familiar followers'))
        accounts = (data[0].get('accounts') or []) if data else []
        if not accounts:
            return
        button = self._familiar_button(accounts)
        self.familiar_box.append(button)
        self.familiar_box.set_visible(True)

    def _familiar_button(self, accounts):
        shown = accounts[:FAMILIAR_NAMES + (1 if len(accounts)
                                            == FAMILIAR_NAMES + 1 else 0)]
        names = [plain_name(account) for account in shown]
        others = len(accounts) - len(shown)
        if others > 0:
            text = ngettext('Followed by %(names)s and %(count)d other',
                            'Followed by %(names)s and %(count)d others',
                            others) % {'names': ', '.join(names),
                                       'count': others}
        elif len(names) == 1:
            text = _('Followed by %s') % names[0]
        else:
            text = _('Followed by %(names)s and %(last)s') % {
                'names': ', '.join(names[:-1]), 'last': names[-1]}
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        avatars = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=2, valign=Gtk.Align.CENTER)
        for account in shown:
            avatar = Adw.Avatar(size=24, text=plain_name(account),
                                show_initials=True)
            AvatarCache.load(avatar, account)
            avatars.append(avatar)
        row.append(avatars)
        label = Gtk.Label(label=text, xalign=0.0, wrap=True,
                          wrap_mode=Pango.WrapMode.WORD_CHAR, hexpand=True)
        label.add_css_class('caption')
        label.add_css_class('dimmed')
        row.append(label)
        button = Gtk.Button(child=row, halign=Gtk.Align.START)
        button.add_css_class('flat')
        button.update_property([Gtk.AccessibleProperty.LABEL], [text])
        connect_weak(button, 'clicked', self.on_familiar_clicked)
        return button

    def on_familiar_clicked(self, _button):
        page = accountrows.AccountListPage(
            self.client, api_accounts.familiar_followers_path(),
            params={'id[]': [self.account['id']]}, paginated=False,
            extract=lambda data: (data[0].get('accounts') or [])
            if data else [],
            empty_title=_('No Followers You Know'))
        self.push_subpage(_('Followers You Know'), page)

    def _on_featured_tags(self, client, result, _data=None):
        tags = _request_result(client, result,
                               api_accounts.featured_tags_finish, self,
                               _('Could not load featured hashtags'))
        if not tags:
            return
        self.tags_box.append(uihelpers.section_label(_('Featured Hashtags')))
        flow = Adw.WrapBox(child_spacing=6, line_spacing=6)
        for tag in tags:
            name = tag.get('name', '')
            count = tag.get('statuses_count')
            button = Gtk.Button(label='#%s' % name)
            button.add_css_class('pill')
            button.add_css_class('flat')
            if count is not None:
                button.set_tooltip_text(ngettext(
                    '%d post', '%d posts', int(count)) % int(count))
            connect_weak(button, 'clicked', self.on_tag_clicked, name)
            flow.append(button)
        self.tags_box.append(flow)
        self.tags_box.set_visible(True)

    def on_tag_clicked(self, _button, name):
        from .hashtags import push_hashtag
        push_hashtag(self, self.client, name)

    def _on_endorsements(self, client, result, _data=None):
        accounts = _request_result(client, result,
                                   api_accounts.endorsements_finish, self,
                                   _('Could not load featured profiles'))
        if not accounts:
            return
        self.endorse_box.append(uihelpers.section_label(_('Featured Profiles')))
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class('boxed-list')
        listbox.connect('row-activated', self._on_account_row_activated)
        for account in accounts[:ENDORSEMENT_PREVIEW]:
            listbox.append(accountrows.AccountRow(self.client, account))
        if len(accounts) > ENDORSEMENT_PREVIEW:
            more = Adw.ButtonRow(title=ngettext(
                'Show All %d Profile', 'Show All %d Profiles',
                len(accounts)) % len(accounts))
            connect_weak(more, 'activated', self.on_endorsements_clicked)
            listbox.append(more)
        self.endorse_box.append(listbox)
        self.endorse_box.set_visible(True)

    def on_endorsements_clicked(self, _row):
        page = accountrows.AccountListPage(
            self.client,
            api_accounts.list_path('endorsements', self.account['id']),
            empty_title=_('No Featured Profiles'))
        self.push_subpage(_('Featured Profiles'), page)

    # Tabs

    def open_tabs(self):
        account_id = self.account.get('id')
        if account_id is None:
            return
        show_media_replies = self.account.get('show_media_replies', True)
        tabs = (
            ('posts', _('Posts'), 'user-home-symbolic',
             AccountStatuses(self.client, account_id, exclude_replies=True)),
            ('pinned', _('Pinned'), 'view-pin-symbolic',
             AccountStatuses(self.client, account_id, pinned=True)),
            ('media', _('Media'), 'image-x-generic-symbolic',
             AccountStatuses(self.client, account_id, media=True,
                             exclude_replies=not show_media_replies)),
        )
        model = self.view_stack.get_pages()
        for page in [model.get_item(i).get_child()
                     for i in range(model.get_n_items())]:
            if isinstance(page, ProfileTimeline):
                self.view_stack.remove(page)
        timeline_pages = []
        for name, title, icon, source in tabs:
            page = ProfileTimeline(
                self.client, mode='account', source=source,
                boosts_carousel=not self._is_self(),
                autoload=(name == 'posts'))
            page.set_hexpand(True)
            page.set_vexpand(True)
            self.view_stack.add_titled(page, name, title)
            self.view_stack.get_page(page).set_icon_name(icon)
            timeline_pages.append(page)
        if self.account.get('show_media') is False:
            self.view_stack.get_page(timeline_pages[2]).set_visible(False)
        connect_weak(self.view_stack, 'notify::visible-child',
                           self._on_profile_tab_visible)
        self.set_stack_state('content')

    def _on_profile_tab_visible(self, stack, _pspec):
        page = stack.get_visible_child()
        if isinstance(page, ProfileTimeline):
            page.ensure_loaded()

    def refresh(self):
        """Reload account, relationship and the tabs (Ctrl+R)."""
        from .profilelookup import LookupProfilePage
        if self.account.get('id') is None:
            if isinstance(self, LookupProfilePage):
                self.lookup()
            return
        self.on_account_ready()

    def reload_account(self):
        """Fetch a fresh copy of the account (counts, fields, bio) and
        rebuild the header when it differs from what is shown."""
        account_id = self.account.get('id')
        generation = self._generation

        def loaded(client, result, _data=None):
            if generation != self._generation:
                return
            fresh = _request_result(
                client, result, finish, self,
                _('Could not reload the profile'))
            if isinstance(fresh, dict) and fresh.get('id') == account_id \
                    and fresh != self.account:
                self.account = dict(self.account, **fresh)
                self.refresh_header(self.account)

        if self._is_self():
            finish = api_accounts.verify_credentials_finish
            api_accounts.verify_credentials_async(self.client,
                                                  callback=loaded)
        else:
            finish = api_accounts.get_finish
            api_accounts.get_async(self.client, account_id, callback=loaded)

    # Relationship

    def load_relationship(self):
        if not self._signed_in() or self._is_self():
            self.update_follow_button()
            self.update_menu()
            return
        api_accounts.relationships_async(
            self.client, [self.account['id']], self.on_relationship_loaded)

    def on_relationship_loaded(self, client, result, _user_data=None):
        try:
            self.relationship = api_accounts.relationships_finish(
                client, result)
            self._relationship_known = True
        except GLib.Error as error:
            # Showing 'Follow' for an account the viewer already follows
            # would re-send a follow request, so hide the action instead.
            self._relationship_known = False
            feedback.report_error(self, _('Could not load relationship'),
                               error)
        self.update_relationship_ui()

    def set_relationship(self, relationship):
        from .entities import lift
        if isinstance(relationship, dict) and 'id' in relationship:
            self.relationship = lift(relationship)
        self.update_relationship_ui()

    def update_relationship_ui(self):
        self.update_badges()
        self.update_follow_button()
        self.update_menu()

    def update_follow_button(self):
        rs = self.relationship
        button = self.follow_button
        for css in ('suggested-action', 'destructive-action'):
            button.remove_css_class(css)
        button.set_sensitive(not self._busy)
        if self._is_self():
            mode, label = 'edit', _('Edit Profile')
            button.add_css_class('suggested-action')
        elif not self._relationship_known:
            # The relationship request failed: an unknown state must not
            # offer 'Follow' on an account that is already followed.
            mode, label = 'unknown', _('Follow')
            button.set_sensitive(False)
        elif rs.get('blocking'):
            mode, label = 'unblock', _('Unblock')
        elif rs.get('domain_blocking'):
            mode, label = 'unblock-domain', _('Unblock Domain')
        elif rs.get('requested'):
            mode, label = 'cancel-request', _('Withdraw Request')
        elif rs.get('following'):
            mode, label = 'unfollow', _('Unfollow')
        elif rs.get('followed_by'):
            mode, label = 'follow', _('Follow Back')
            button.add_css_class('suggested-action')
        else:
            mode, label = 'follow', _('Follow')
            button.add_css_class('suggested-action')
            if rs.get('blocked_by'):
                button.set_sensitive(False)
        self._follow_mode = mode
        button.set_label(label)
        button.set_visible(self._content and self._signed_in())

    def on_follow_clicked(self, _button):
        if self.account.get('id') is None:
            return
        mode = self._follow_mode
        if mode == 'edit':
            self.on_edit_profile()
        elif mode == 'follow':
            self.send_relationship('follow', done=_('Followed %s'),
                                   failed=_('Could not follow %s'))
        elif mode == 'unfollow':
            self.confirm_unfollow()
        elif mode == 'cancel-request':
            self.confirm_cancel_request()
        elif mode == 'unblock':
            self.confirm_block(False)
        elif mode == 'unblock-domain':
            self.confirm_domain_block(False)

    # Menu and actions

    def set_stack_state(self, name):
        self.stack.set_visible_child_name(name)
        self._content = name == 'content'
        self.switcher.set_visible(self._content)
        self.switcher_bar.set_visible(self._content)
        if self.menu_button is not None:
            self.menu_button.set_visible(self._content)
        self.update_follow_button()

    def on_retry(self, _button):
        from .profilelookup import LookupProfilePage
        if self.account.get('id') is not None:
            self.on_account_ready()
        elif isinstance(self, LookupProfilePage):
            self.lookup()

