# SPDX-License-Identifier: GPL-3.0-or-later

"""Profile view: account header (counts, badges, bio, fields, familiar
followers, featured hashtags and profiles), the relationship actions of
Tuba's profile menu and the Posts / Posts & Replies / Media tabs rendered
with the shared timeline components."""

import gi

import locale
import urllib.parse

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gettext import gettext as _, ngettext

from gi.repository import (Adw, Gdk, GLib, Gio, GObject, Gtk,  # noqa: E402
                           Pango, Soup)

from . import pages  # noqa: E402
from .mastodon import summarize_error  # noqa: E402
from .timeline import AvatarCache, TimelinePage, COLUMN_WIDTH  # noqa: E402


def push_profile(view, client, account, acct=None):
    """Show *account* (dict) or the *acct* handle in a profile sheet over
    the window that owns *view*."""
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


class ProfileDialog(Adw.Dialog):
    """Profile in a bottom sheet, like Phanpy's account sheet. Lists the
    profile opens (followers, following, familiar followers) stack inside
    the sheet; anything the profile opens in the window's navigation (a
    thread, a post, a hashtag) closes the sheet so it lands in view."""

    __gtype_name__ = 'BabbleProfileDialog'

    def __init__(self, view, page, title, **kwargs):
        navigation = Adw.NavigationView()
        navigation.push(Adw.NavigationPage(title=title, tag='profile',
                                           child=page))
        # The sheet covers the window's own toast overlay, so it needs
        # one of its own for the feedback raised from inside it.
        super().__init__(title=title,
                         child=Adw.ToastOverlay(child=navigation),
                         content_width=COLUMN_WIDTH, content_height=900,
                         presentation_mode=Adw.DialogPresentationMode.BOTTOM_SHEET,
                         **kwargs)
        self._view = view
        self._pushed = view.connect('pushed', lambda *_a: self.close())
        self.connect('closed', self._on_closed)

    def _on_closed(self, _dialog):
        self._view.disconnect(self._pushed)


class AccountStatuses:
    """TimelinePage source over /accounts/:id/statuses.

    ``include_pinned`` puts the account's pinned posts ahead of the first
    page (Tuba's Posts tab); they are flagged with ``pinned = True`` and
    their regular copies further down are dropped by the model."""

    def __init__(self, client, account_id, pinned=False, media=False,
                 exclude_replies=False, paginated=True,
                 exclude_reblogs=False, include_pinned=False):
        self.client = client
        self.account_id = account_id
        self.pinned = pinned
        self.media = media
        self.exclude_replies = exclude_replies
        self.exclude_reblogs = exclude_reblogs
        self.include_pinned = include_pinned
        self.paginated = paginated
        self.pinned_statuses = []
        self.oldest_regular_id = None

    def base_params(self):
        params = {}
        if self.pinned:
            params['pinned'] = 'true'
        if self.media:
            params['only_media'] = 'true'
        if self.exclude_replies:
            params['exclude_replies'] = 'true'
        if self.exclude_reblogs:
            params['exclude_reblogs'] = 'true'
        return params

    def fetch(self, params, callback):
        merged = self.base_params()
        merged.update(params or {})
        paged = any(key in merged for key in ('max_id', 'since_id', 'min_id'))
        if not self.include_pinned or paged:
            self.client.account_statuses_async(
                self.account_id, params=merged, callback=callback)
            return

        def pinned_loaded(client, result, _data=None):
            try:
                pinned = client.account_statuses_finish(result)
            except GLib.Error:
                pinned = []
            for status in pinned:
                status['pinned'] = True
            self.pinned_statuses = pinned
            client.account_statuses_async(self.account_id, params=merged,
                                          callback=callback)

        self.client.account_statuses_async(
            self.account_id, params={'pinned': 'true'},
            callback=pinned_loaded)

    def finish(self, result):
        statuses = self.client.account_statuses_finish(result)
        pinned, self.pinned_statuses = self.pinned_statuses, []
        if statuses:
            self.oldest_regular_id = min(s['id'] for s in statuses)
        return pinned + statuses


class ProfileTimeline(TimelinePage):
    """Timeline of one account tab. Pinned posts ahead of the first page
    must not drag the paging cursor back to the pin's age."""

    def on_page_loaded(self, client, result, _user_data=None):
        TimelinePage.on_page_loaded(self, client, result, _user_data)
        oldest = getattr(self.source, 'oldest_regular_id', None)
        if oldest is not None and self.model.get_n_items():
            self.model.oldest_id = oldest


class HeaderCache:
    """Profile-banner cache: downloads each header once per URL and hands
    the decoded texture to every picture waiting for it."""

    _textures = {}
    _inflight = {}

    @classmethod
    def apply(cls, picture, url):
        """Show *url*'s image in *picture*, fetching it when needed."""
        texture = cls._textures.get(url)
        if texture is not None:
            picture.set_paintable(texture)
            return
        if url in cls._inflight:
            cls._inflight[url].append(picture)
            return
        message = Soup.Message.new('GET', url)
        if message is None:
            return
        cls._inflight[url] = [picture]
        AvatarCache.session().send_and_read_async(
            message, GLib.PRIORITY_DEFAULT_IDLE, None, cls._on_loaded, url)

    @classmethod
    def _on_loaded(cls, session, result, url):
        waiting = cls._inflight.pop(url, [])
        try:
            data = session.send_and_read_finish(result)
            texture = Gdk.Texture.new_from_bytes(data)
        except (GLib.Error, TypeError):
            return
        cls._textures[url] = texture
        for picture in waiting:
            picture.set_paintable(texture)


from . import textemoji  # noqa: E402

# Shared machinery now lives in textemoji.py; the profile sheet keeps
# using the very same emoji bank, texture cache and renderer.
EMOJI_RE = textemoji.EMOJI_RE
emoji_bank = textemoji.emoji_bank
content_segments = textemoji.content_segments
EmojiTextures = textemoji.EmojiTextures
InlineEmojiText = textemoji.InlineEmojiText

VISIBLE_FIELDS = 4
BANNER_HEIGHT = 100
ENDORSEMENT_PREVIEW = 3
FAMILIAR_NAMES = 3
NOTE_LIMIT = 2000
# Seconds, as Mastodon's ``duration`` parameter wants them.
MUTE_DURATIONS = (0, 3600, 6 * 3600, 24 * 3600, 7 * 24 * 3600,
                  30 * 24 * 3600)


def format_count(count):
    """*count* with the locale's digit grouping."""
    return locale.format_string('%d', count or 0, grouping=True)


def plain_name(account):
    """Display name with custom-emoji shortcodes removed."""
    name = EMOJI_RE.sub('', account.get('display_name') or '').strip()
    return name or account.get('username') or account.get('acct', '')


def duration_label(seconds):
    if not seconds:
        return _('Never')
    if seconds < 24 * 3600:
        hours = seconds // 3600
        return ngettext('%d Hour', '%d Hours', hours) % hours
    days = seconds // (24 * 3600)
    return ngettext('%d Day', '%d Days', days) % days


def link_context(segments, instance_host):
    """``(mentions, tags)`` payloads for InlineEmojiText link routing,
    derived from the rendered segments of an account's HTML (accounts do
    not carry the ``mentions``/``tags`` arrays statuses do)."""
    mentions, tags = [], []
    for segment in segments:
        href = segment.get('href')
        text = segment.get('text') or ''
        if not href:
            continue
        if text.startswith('@'):
            parts = urllib.parse.urlsplit(href)
            user = parts.path.rstrip('/').rsplit('/', 1)[-1].lstrip('@')
            if user and parts.hostname:
                acct = user if parts.hostname == instance_host \
                    else '%s@%s' % (user, parts.hostname)
                mentions.append({'url': href, 'acct': acct})
        elif text.startswith('#') and len(text) > 1:
            tags.append({'name': text[1:]})
    return mentions, tags


def make_chip(text, tone=None):
    """Small bordered badge; the text carries the meaning, *tone*
    ('accent', 'danger' or None) only tints it."""
    label = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    label.add_css_class('caption-heading')
    label.add_css_class('profile-chip')
    if tone:
        label.add_css_class('profile-chip-%s' % tone)
    return label


def present_mute_dialog(parent, handle, on_confirm):
    """Mute confirmation with an expiry and the notifications switch;
    *on_confirm(duration_seconds, hide_notifications)* runs on Mute."""
    dialog = Adw.AlertDialog(
        heading=_('Mute %s?') % handle,
        body=_('Their posts will be hidden. They will not be told that '
               'you muted them.'))
    durations = Gtk.StringList.new([duration_label(s)
                                    for s in MUTE_DURATIONS])
    expire_row = Adw.ComboRow(title=_('Expire In'), model=durations)
    notifications_row = Adw.SwitchRow(title=_('Hide Notifications'),
                                      active=True)
    group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    group.add_css_class('boxed-list')
    group.append(expire_row)
    group.append(notifications_row)
    dialog.set_extra_child(group)
    dialog.add_response('cancel', _('_Cancel'))
    dialog.add_response('mute', _('_Mute'))
    dialog.set_response_appearance('mute',
                                   Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response('cancel')
    dialog.set_close_response('cancel')

    def on_response(_dialog, response):
        if response == 'mute':
            on_confirm(MUTE_DURATIONS[expire_row.get_selected()],
                       notifications_row.get_active())

    dialog.connect('response', on_response)
    dialog.present(parent)
    return dialog


class PrivateNoteDialog(Adw.Dialog):
    """Editor for the private note kept on another account (visible to
    the viewer only). *on_save(text)* is called with the new text; the
    caller closes the dialog by calling :meth:`close` on success."""

    __gtype_name__ = 'BabblePrivateNoteDialog'

    def __init__(self, handle, text, on_save, **kwargs):
        super().__init__(title=_('Private Note'), content_width=400,
                         content_height=360, **kwargs)
        self._on_save = on_save
        header = Adw.HeaderBar(show_start_title_buttons=False,
                               show_end_title_buttons=False)
        cancel = Gtk.Button(label=_('_Cancel'), use_underline=True)
        cancel.connect('clicked', lambda *_a: self.close())
        header.pack_start(cancel)
        self.save_button = Gtk.Button(label=_('_Save'), use_underline=True)
        self.save_button.add_css_class('suggested-action')
        self.save_button.connect('clicked', self._on_save_clicked)
        header.pack_end(self.save_button)
        self.counter = Gtk.Label(label=str(NOTE_LIMIT),
                                 tooltip_text=_('Remaining Characters'))
        self.counter.add_css_class('numeric')
        self.counter.add_css_class('dimmed')
        header.pack_end(self.counter)

        self.text_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
            top_margin=8, bottom_margin=8, left_margin=8, right_margin=8,
            vexpand=True, hexpand=True)
        self.text_view.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Private note about %s') % handle])
        self.buffer = self.text_view.get_buffer()
        self.buffer.set_text(text or '', -1)
        self.buffer.connect('changed', self._on_changed)
        scrolled = Gtk.ScrolledWindow(child=self.text_view, vexpand=True,
                                      margin_start=12, margin_end=12,
                                      margin_top=12, margin_bottom=12)
        scrolled.add_css_class('card')
        hint = Gtk.Label(
            label=_('Only you can see this note about %s.') % handle,
            xalign=0.0, wrap=True, margin_start=12, margin_end=12,
            margin_bottom=12)
        hint.add_css_class('dimmed')
        hint.add_css_class('caption')
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(scrolled)
        body.append(hint)
        toolbar = Adw.ToolbarView(content=body)
        toolbar.add_top_bar(header)
        self.set_child(Adw.ToastOverlay(child=toolbar))
        self.set_focus(self.text_view)
        self._on_changed()

    def text(self):
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, False)

    def _on_changed(self, *_args):
        remaining = NOTE_LIMIT - self.buffer.get_char_count()
        self.counter.set_label(str(remaining))
        if remaining < 0:
            self.counter.add_css_class('error')
        else:
            self.counter.remove_css_class('error')
        self.save_button.set_sensitive(remaining >= 0)

    def _on_save_clicked(self, _button):
        self.save_button.set_sensitive(False)
        self._on_save(self.text())


class ProfilePage(Adw.Bin):
    """Account header plus Posts / Posts & Replies / Media tabs.

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
        self._timeline_pages = []
        self.badge_box = None
        self.note_list = None

        self.view_stack = Adw.ViewStack()
        self.switcher = Adw.ViewSwitcher(
            stack=self.view_stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.switcher_bar = Adw.ViewSwitcherBar(stack=self.view_stack)
        self.header_bar = Adw.HeaderBar(title_widget=self.switcher)

        self.follow_button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.follow_button.add_css_class('pill')
        self.follow_button.connect('clicked', self.on_follow_clicked)
        self.action_bar = Gtk.ActionBar(revealed=self._signed_in())
        self.action_bar.pack_end(self.follow_button)
        if self._signed_in():
            label = _('More Options')
            self.menu_button = Gtk.MenuButton(
                icon_name='view-more-symbolic', tooltip_text=label,
                valign=Gtk.Align.CENTER)
            self.menu_button.add_css_class('flat')
            self.menu_button.update_property(
                [Gtk.AccessibleProperty.LABEL], [label])
            self.action_bar.pack_end(self.menu_button)

        self._build_actions()

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)

        self.loading_page = Adw.StatusPage(title=_('Loading Profile'))
        self.loading_page.set_paintable(
            Adw.SpinnerPaintable.new(self.loading_page))
        self.stack.add_named(self.loading_page, 'loading')

        self.error_page = Adw.StatusPage(
            icon_name='network-error-symbolic',
            title=_('Could Not Load Profile'))
        retry = Gtk.Button(label=_('_Try Again'), use_underline=True,
                           halign=Gtk.Align.CENTER)
        retry.add_css_class('pill')
        retry.add_css_class('suggested-action')
        retry.connect('clicked', self.on_retry)
        self.error_page.set_child(retry)
        self.stack.add_named(self.error_page, 'error')

        self.scrolled = Gtk.ScrolledWindow(vexpand=True)
        self.scrolled.set_policy(Gtk.PolicyType.NEVER,
                                 Gtk.PolicyType.AUTOMATIC)
        self.scrolled.add_css_class('profile-page-scroller')
        # 660px content column, centered in the pane and narrowing with
        # it; the header hero and the tab pages fill it.
        self.holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                              spacing=12)
        self.scrolled.set_child(Adw.Clamp(maximum_size=COLUMN_WIDTH,
                                          tightening_threshold=COLUMN_WIDTH,
                                          child=self.holder))
        self.view_stack.set_vexpand(True)
        self.view_stack.set_hexpand(True)
        self.holder.append(self.view_stack)
        self.stack.add_named(self.scrolled, 'content')

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
            window = pages.window_of(self)
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
        it (header, tabs, relationship, fresh copy of the account)."""
        self.refresh_header(self.account)
        self.open_tabs()
        self.load_relationship()
        self.reload_account()

    def push_subpage(self, title, child):
        """Push *child* inside the profile sheet's own navigation when
        there is one, else onto the window's."""
        view = self.get_ancestor(Adw.NavigationView)
        if view is None:
            pages.push_page(self, title, child)
            return
        view.push(pages.make_nav_page(title, child))

    # Header

    def refresh_header(self, account):
        """(Re)build the header above the tab stack for *account*."""
        self._generation += 1
        while True:
            old = self.holder.get_first_child()
            if old is None or old is self.view_stack:
                break
            self.holder.remove(old)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header.set_margin_top(12)
        header.set_margin_bottom(6)
        header.set_margin_start(12)
        header.set_margin_end(12)

        moved = account.get('moved')
        if moved:
            header.append(self._build_moved(account, moved))

        # Banner / cover image with the avatar straddling its lower
        # edge.  The gradient band holds the height; the picture covers
        # it as an overlay, so the artwork's aspect ratio never grows it.
        header_url = account.get('header') or account.get('header_static')
        if header_url and header_url.endswith('/headers/original/missing.png'):
            header_url = None
        overlay = Gtk.Overlay()
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        overlay.set_child(hero)
        banner = Gtk.Overlay(overflow=Gtk.Overflow.HIDDEN)
        banner.add_css_class('profile-banner')
        backdrop = Gtk.Box()
        backdrop.set_size_request(-1, BANNER_HEIGHT)
        backdrop.add_css_class('profile-banner-fallback')
        banner.set_child(backdrop)
        if header_url:
            picture = Gtk.Picture(
                content_fit=Gtk.ContentFit.COVER, can_shrink=True,
                accessible_role=Gtk.AccessibleRole.PRESENTATION)
            banner.add_overlay(picture)
            HeaderCache.apply(picture, header_url)
            self._open_image_on_click(
                banner, header_url, account.get('header_description'))
        hero.append(banner)

        # Identity row sits in the space under the banner, to the right
        # of the avatar's protruding lower half.
        identity_row = Adw.WrapBox(child_spacing=12, line_spacing=6)
        identity_row.set_margin_top(4)
        identity_row.set_margin_bottom(4)
        identity_row.set_margin_start(16)
        identity_row.set_margin_end(4)

        identity_block = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 spacing=12)
        spacer = Gtk.Box()
        spacer.set_size_request(64, -1)  # avatar size
        identity_block.append(spacer)

        identity = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        identity.set_valign(Gtk.Align.CENTER)

        # Display name keeps the title typography; custom emoji from the
        # account bank are rendered inline as images instead of literal
        # :shortcodes:.
        self.emoji_bank_map = emoji_bank(account)
        display = account.get('display_name') or account.get('acct', '')
        name = InlineEmojiText(emoji_size=18)
        name.set_segments([{'text': display, 'href': None}],
                          self.emoji_bank_map)
        name.set_wrap_mode(Gtk.WrapMode.WORD)
        name.add_css_class('title-2')
        identity.append(name)

        handle = Gtk.Label(label='@%s' % account.get('acct', ''),
                           xalign=0.0, ellipsize=Pango.EllipsizeMode.END,
                           selectable=True)
        handle.add_css_class('caption')
        handle.add_css_class('dimmed')
        identity.append(handle)

        dates = Adw.WrapBox(child_spacing=8, line_spacing=0)
        for text in (self._joined_text(account),
                     self._last_post_text(account)):
            if text:
                date = Gtk.Label(label=text, xalign=0.0)
                date.add_css_class('caption')
                date.add_css_class('dimmed')
                dates.append(date)
        identity.append(dates)
        identity_block.append(identity)
        identity_row.append(identity_block)

        hero.append(identity_row)

        # The Avatar overlaps the banner: half of it sticks up over the
        # picture, half hangs below.
        self.avatar = Adw.Avatar(size=64, show_initials=True,
                                 halign=Gtk.Align.START,
                                 valign=Gtk.Align.START)
        self.avatar.set_text(display or '?')
        description = account.get('avatar_description')
        if description:
            self.avatar.update_property(
                [Gtk.AccessibleProperty.DESCRIPTION], [description])
        self.avatar.set_margin_start(16)
        self.avatar.set_margin_top(BANNER_HEIGHT - 32)
        overlay.add_overlay(self.avatar)
        AvatarCache.load(self.avatar, account)
        avatar_url = account.get('avatar') or account.get('avatar_static')
        if avatar_url:
            self._open_image_on_click(self.avatar, avatar_url, description)
        header.append(overlay)

        # Badges (account type and relationship) and the private note.
        self.badge_box = Adw.WrapBox(child_spacing=6, line_spacing=6)
        self.badge_box.set_margin_start(4)
        header.append(self.badge_box)
        self.note_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                     visible=False)
        self.note_list.add_css_class('boxed-list')
        self.note_list.connect('row-activated',
                               lambda *_a: self.edit_private_note())
        header.append(self.note_list)

        header.append(self._build_stats(account))

        self.familiar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                    visible=False)
        header.append(self.familiar_box)

        segments = content_segments(account.get('note', ''))
        if segments:
            header.append(self._rich_text(
                segments, account, emoji_size=16, paragraph_gap=True))

        fields = account.get('fields') or []
        if fields:
            header.append(self._build_fields(fields, account))

        self.tags_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=6, visible=False)
        header.append(self.tags_box)
        self.endorse_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=6, visible=False)
        header.append(self.endorse_box)

        self.update_badges()
        self.update_follow_button()
        self.update_menu()

        # Header goes above the tab stack inside the scrolled viewport.
        self.holder.prepend(header)
        self.load_extras()

    def _rich_text(self, segments, account, emoji_size=16,
                   paragraph_gap=False):
        """InlineEmojiText for *segments* with custom emoji from the
        account bank and mention/hashtag links routed in-app."""
        text = InlineEmojiText(emoji_size=emoji_size,
                               paragraph_gap=paragraph_gap)
        text.set_segments(segments, emoji_bank(account))
        mentions, tags = link_context(segments, self._instance_host())
        text.link_mentions = mentions
        text.link_tags = tags
        return text

    def _open_image_on_click(self, widget, url, description):
        click = Gtk.GestureClick.new()
        click.set_button(1)
        click.connect('released', self._on_image_clicked, url, description)
        widget.add_controller(click)
        widget.set_cursor_from_name('pointer')

    def _on_image_clicked(self, _gesture, _n_press, _x, _y, url,
                          description):
        try:
            from .mediaviewer import present_media
        except ImportError:
            pages.launch_external(self, url)
            return
        present_media(self, [{'type': 'image', 'url': url,
                              'preview_url': url,
                              'description': description or ''}], 0)

    def _build_moved(self, account, moved):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        label = Gtk.Label(
            label=_('%s has indicated that their new account is now:')
            % plain_name(account), xalign=0.0, wrap=True)
        box.append(label)
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class('boxed-list')
        listbox.append(pages.AccountRow(self.client, moved))
        listbox.connect('row-activated', self._on_account_row_activated)
        box.append(listbox)
        box.add_css_class('profile-moved')
        return box

    @staticmethod
    def _on_account_row_activated(_listbox, row):
        callback = getattr(row, 'on_activate', None)
        if callback is not None:
            callback()

    def _build_stats(self, account):
        """Posts / following / followers: the last two open the account
        lists, posts jumps to the Posts tab."""
        stats = Adw.WrapBox(child_spacing=4, line_spacing=2)
        stats.set_margin_top(2)
        posts = account.get('statuses_count') or 0
        following = account.get('following_count') or 0
        followers = account.get('followers_count') or 0
        stats.append(self._stat_button(
            posts, ngettext('post', 'posts', posts), self.on_posts_clicked))
        stats.append(self._stat_button(
            following, _('following'), self.on_following_clicked))
        stats.append(self._stat_button(
            followers, ngettext('follower', 'followers', followers),
            self.on_followers_clicked))
        return stats

    @staticmethod
    def _stat_button(count, caption, callback):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        number = Gtk.Label(label=format_count(count))
        number.add_css_class('heading')
        number.add_css_class('numeric')
        box.append(number)
        label = Gtk.Label(label=caption)
        label.add_css_class('dimmed')
        box.append(label)
        button = Gtk.Button(child=box)
        button.add_css_class('flat')
        button.update_property([Gtk.AccessibleProperty.LABEL],
                               ['%s %s' % (format_count(count), caption)])
        button.connect('clicked', lambda *_a: callback())
        return button

    def on_posts_clicked(self):
        self.view_stack.set_visible_child_name('posts')

    def on_followers_clicked(self):
        count = self.account.get('followers_count') or 0
        self._push_accounts(
            _('Followers'), 'followers',
            _('This user has chosen to not make this information available')
            if count else _('No Followers'))

    def on_following_clicked(self):
        count = self.account.get('following_count') or 0
        self._push_accounts(
            _('Following'), 'following',
            _('This user has chosen to not make this information available')
            if count else _("This User Doesn't Follow Anyone Yet"))

    def _push_accounts(self, title, kind, empty_title):
        page = pages.AccountListPage(
            self.client, '/accounts/%s/%s' % (self.account['id'], kind),
            empty_title=empty_title)
        self.push_subpage(title, page)

    def _build_fields(self, fields, account):
        """Boxed list of the account's fields, links routed in-app; rows
        past the first few sit behind a Show More row so the posts stay
        near the fold."""
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class('boxed-list')
        listbox.update_property([Gtk.AccessibleProperty.LABEL],
                                [_('Profile Fields')])
        rows = [self._field_row(field, account) for field in fields]
        for index, row in enumerate(rows):
            row.set_visible(index < VISIBLE_FIELDS)
            listbox.append(row)
        if len(rows) > VISIBLE_FIELDS:
            toggle = Adw.ButtonRow(title=_('Show More'))
            toggle.connect('activated', self.on_fields_toggle,
                           rows[VISIBLE_FIELDS:])
            listbox.append(toggle)
        return listbox

    def _field_row(self, field, account):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      margin_top=8, margin_bottom=8, margin_start=12,
                      margin_end=12)
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                            spacing=6)
        title = self._rich_text(
            [{'text': field.get('name', ''), 'href': None}], account,
            emoji_size=14)
        title.add_css_class('caption-heading')
        title.add_css_class('dimmed')
        title_row.append(title)
        verified = field.get('verified_at')
        if verified:
            label = _('Ownership of this link was verified on %s') \
                % verified.split('T', 1)[0]
            check = Gtk.Image(icon_name='object-select-symbolic',
                              tooltip_text=label, valign=Gtk.Align.CENTER)
            check.add_css_class('success')
            check.update_property([Gtk.AccessibleProperty.LABEL],
                                  [_('Verified Link')])
            title_row.append(check)
        box.append(title_row)
        box.append(self._rich_text(
            content_segments(field.get('value', '')), account))
        row = Gtk.ListBoxRow(activatable=False, child=box)
        row.add_css_class('profile-field')
        if verified:
            row.add_css_class('profile-field-verified')
        return row

    def on_fields_toggle(self, toggle, hidden_rows):
        """Reveal/hide field rows beyond the first few."""
        expand = not hidden_rows[0].get_visible()
        for row in hidden_rows:
            row.set_visible(expand)
        toggle.set_title(_('Show Less') if expand else _('Show More'))

    @staticmethod
    def _parse_date(raw):
        if not raw:
            return None
        try:
            return GLib.DateTime.new_from_iso8601(
                raw if 'T' in raw else raw + 'T00:00:00Z')
        except TypeError:
            return None

    @classmethod
    def _joined_text(cls, account):
        """Localized 'Joined <Month Year>' from account.created_at."""
        dt = cls._parse_date(account.get('created_at'))
        if dt is None:
            return ''
        return _('Joined %s') % dt.format('%B %Y')

    @classmethod
    def _last_post_text(cls, account):
        """Localized 'Last post <date>' from account.last_status_at."""
        dt = cls._parse_date(account.get('last_status_at'))
        if dt is None:
            return ''
        return _('Last post %s') % dt.format('%x')

    # Badges

    def update_badges(self):
        """Account-type and relationship chips plus the private note."""
        if self.badge_box is None:
            return
        while (child := self.badge_box.get_first_child()) is not None:
            self.badge_box.remove(child)
        account, rs = self.account, self.relationship
        chips = []
        if account.get('memorial'):
            chips.append((_('In Memoriam'), None))
        if account.get('suspended'):
            chips.append((_('Suspended'), 'danger'))
        if account.get('locked'):
            chips.append((_('Locked'), None))
        if account.get('bot'):
            chips.append((_('Automated'), None))
        if account.get('group'):
            chips.append((_('Group'), None))
        for role in account.get('roles') or []:
            if role.get('name'):
                chips.append((role['name'], None))
        if not self._is_self():
            if rs.get('requested'):
                chips.append((_('Requested'), 'accent'))
            if rs.get('followed_by') and rs.get('following'):
                chips.append((_('Mutuals'), 'accent'))
            elif rs.get('followed_by'):
                chips.append((_('Follows you'), 'accent'))
            if rs.get('blocked_by'):
                chips.append((_('Blocks you'), 'danger'))
            if rs.get('blocking'):
                chips.append((_('Blocked'), 'danger'))
            if rs.get('domain_blocking'):
                chips.append((_('Domain Blocked'), 'danger'))
            if rs.get('muting'):
                chips.append((_('Muted'), 'danger'))
        for text, tone in chips:
            self.badge_box.append(make_chip(text, tone))
        self.badge_box.set_visible(bool(chips))

        if self.note_list is None:
            return
        while (child := self.note_list.get_first_child()) is not None:
            self.note_list.remove(child)
        note = rs.get('note') if not self._is_self() else None
        if note:
            row = Adw.ActionRow(title=_('Private Note'), subtitle=note,
                                use_markup=False, activatable=True)
            row.set_subtitle_lines(0)
            row.add_css_class('property')
            row.add_suffix(Gtk.Image(icon_name='document-edit-symbolic'))
            self.note_list.append(row)
        self.note_list.set_visible(bool(note))

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
            self.client.request_async(
                'GET', '/accounts/familiar_followers',
                params={'id[]': [account_id]},
                callback=handler(self._on_familiar))
        self.client.request_async(
            'GET', '/accounts/%s/featured_tags' % account_id,
            callback=handler(self._on_featured_tags))
        self.client.request_async(
            'GET', '/accounts/%s/endorsements' % account_id,
            params={'limit': 80}, callback=handler(self._on_endorsements))

    def _on_familiar(self, client, result, _data=None):
        try:
            data = client.request_finish(result)
        except GLib.Error:
            return
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
        button.connect('clicked', self.on_familiar_clicked)
        return button

    def on_familiar_clicked(self, _button):
        page = pages.AccountListPage(
            self.client, '/accounts/familiar_followers',
            params={'id[]': [self.account['id']]}, paginated=False,
            extract=lambda data: (data[0].get('accounts') or [])
            if data else [],
            empty_title=_('No Followers You Know'))
        self.push_subpage(_('Followers You Know'), page)

    def _on_featured_tags(self, client, result, _data=None):
        try:
            tags = client.request_finish(result)
        except GLib.Error:
            return
        if not tags:
            return
        self.tags_box.append(pages.section_label(_('Featured Hashtags')))
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
            button.connect('clicked', self.on_tag_clicked, name)
            flow.append(button)
        self.tags_box.append(flow)
        self.tags_box.set_visible(True)

    def on_tag_clicked(self, _button, name):
        from .hashtags import push_hashtag
        push_hashtag(self, self.client, name)

    def _on_endorsements(self, client, result, _data=None):
        try:
            accounts = client.request_finish(result)
        except GLib.Error:
            return
        if not accounts:
            return
        self.endorse_box.append(pages.section_label(_('Featured Profiles')))
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class('boxed-list')
        listbox.connect('row-activated', self._on_account_row_activated)
        for account in accounts[:ENDORSEMENT_PREVIEW]:
            listbox.append(pages.AccountRow(self.client, account))
        if len(accounts) > ENDORSEMENT_PREVIEW:
            more = Adw.ButtonRow(title=ngettext(
                'Show All %d Profile', 'Show All %d Profiles',
                len(accounts)) % len(accounts))
            more.connect('activated', self.on_endorsements_clicked)
            listbox.append(more)
        self.endorse_box.append(listbox)
        self.endorse_box.set_visible(True)

    def on_endorsements_clicked(self, _row):
        page = pages.AccountListPage(
            self.client, '/accounts/%s/endorsements' % self.account['id'],
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
        while (child := self.view_stack.get_first_child()) is not None:
            self.view_stack.remove(child)
        timeline_pages = []
        for name, title, icon, source in tabs:
            page = ProfileTimeline(self.client, mode='account',
                                   source=source)
            page.set_hexpand(True)
            page.set_vexpand(True)
            self.view_stack.add_titled(page, name, title)
            self.view_stack.get_page(page).set_icon_name(icon)
            timeline_pages.append(page)
        if self.account.get('show_media') is False:
            self.view_stack.get_page(timeline_pages[2]).set_visible(False)
        self._timeline_pages = timeline_pages
        self.set_stack_state('content')

    def refresh(self):
        """Reload account, relationship and the tabs (Ctrl+R)."""
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
        path = '/accounts/verify_credentials' if self._is_self() \
            else '/accounts/%s' % account_id

        def loaded(client, result, _data=None):
            if generation != self._generation:
                return
            try:
                fresh = client.request_finish(result)
            except GLib.Error:
                return
            if isinstance(fresh, dict) and fresh.get('id') == account_id \
                    and fresh != self.account:
                self.account = dict(self.account, **fresh)
                self.refresh_header(self.account)

        self.client.request_async('GET', path, callback=loaded)

    # Relationship

    def load_relationship(self):
        if not self._signed_in() or self._is_self():
            self.update_follow_button()
            self.update_menu()
            return
        self.client.relationships_async([self.account['id']],
                                        self.on_relationship_loaded)

    def on_relationship_loaded(self, client, result, _user_data=None):
        try:
            self.relationship = client.relationships_finish(result)
            self._relationship_known = True
        except GLib.Error as error:
            # Showing 'Follow' for an account the viewer already follows
            # would re-send a follow request, so hide the action instead.
            self._relationship_known = False
            pages.report_error(self, _('Could not load relationship'),
                               error)
        self.update_relationship_ui()

    def set_relationship(self, relationship):
        if isinstance(relationship, dict) and 'id' in relationship:
            self.relationship = relationship
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

    def _build_actions(self):
        self.actions = Gio.SimpleActionGroup()
        plain = {
            'mention': lambda: self.on_mention(False),
            'direct': lambda: self.on_mention(True),
            'copy-handle': self.on_copy_handle,
            'copy-link': self.on_copy_link,
            'open-browser': self.on_open_browser,
            'private-note': self.edit_private_note,
            'lists': self.on_lists,
            'hide-boosts': lambda: self.set_boosts(False),
            'show-boosts': lambda: self.set_boosts(True),
            'remove-follower': self.confirm_remove_follower,
            'mute': self.on_mute,
            'unmute': self.on_unmute,
            'block': lambda: self.confirm_block(True),
            'unblock': lambda: self.confirm_block(False),
            'block-domain': lambda: self.confirm_domain_block(True),
            'unblock-domain': lambda: self.confirm_domain_block(False),
            'report': self.on_report,
        }
        for name, callback in plain.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', lambda _a, _p, cb=callback: cb())
            self.actions.add_action(action)
        stateful = {
            'notify': self.set_notifying,
            'endorse': self.set_endorsed,
        }
        for name, callback in stateful.items():
            action = Gio.SimpleAction.new_stateful(
                name, None, GLib.Variant.new_boolean(False))
            action.connect('change-state',
                           lambda _a, value, cb=callback:
                           cb(value.get_boolean()))
            self.actions.add_action(action)
        self.insert_action_group('profile', self.actions)

    def update_menu(self):
        """Rebuild the overflow menu for the current relationship."""
        if self.menu_button is None:
            return
        rs = self.relationship
        mine = self._is_self()
        following = bool(rs.get('following'))
        menu = Gio.Menu()

        compose = Gio.Menu()
        compose.append(_('Mention'), 'profile.mention')
        if not mine:
            compose.append(_('Direct Message'), 'profile.direct')
        menu.append_section(None, compose)

        share = Gio.Menu()
        share.append(_('Copy Handle'), 'profile.copy-handle')
        share.append(_('Copy Link'), 'profile.copy-link')
        share.append(_('Open in Browser'), 'profile.open-browser')
        menu.append_section(None, share)

        if not mine:
            relation = Gio.Menu()
            if following:
                relation.append(_('Receive Post Notifications'),
                                'profile.notify')
                relation.append(_('Feature on Profile'), 'profile.endorse')
                relation.append(
                    _('Show Boosts') if rs.get('showing_reblogs') is False
                    else _('Hide Boosts'),
                    'profile.show-boosts'
                    if rs.get('showing_reblogs') is False
                    else 'profile.hide-boosts')
                relation.append(_('Add or Remove from Lists…'),
                                'profile.lists')
            relation.append(_('Private Note…'), 'profile.private-note')
            if rs.get('followed_by'):
                relation.append(_('Remove from Followers'),
                                'profile.remove-follower')
            menu.append_section(None, relation)

            moderation = Gio.Menu()
            moderation.append(
                _('Unmute') if rs.get('muting') else _('Mute…'),
                'profile.unmute' if rs.get('muting') else 'profile.mute')
            moderation.append(
                _('Unblock') if rs.get('blocking') else _('Block…'),
                'profile.unblock' if rs.get('blocking')
                else 'profile.block')
            if self._remote_domain():
                moderation.append(
                    _('Unblock Domain') if rs.get('domain_blocking')
                    else _('Block Domain…'),
                    'profile.unblock-domain' if rs.get('domain_blocking')
                    else 'profile.block-domain')
            moderation.append(_('Report…'), 'profile.report')
            menu.append_section(_('Moderation'), moderation)

        for name, key in (('notify', 'notifying'), ('endorse', 'endorsed')):
            self.actions.lookup_action(name).set_state(
                GLib.Variant.new_boolean(bool(rs.get(key))))
        self.menu_button.set_menu_model(menu)
        self.menu_button.set_sensitive(not self._busy)

    def _close_sheet(self):
        dialog = self.get_ancestor(Adw.Dialog)
        if dialog is not None:
            dialog.close()

    def on_mention(self, direct):
        window = pages.window_of(self)
        if window is None:
            return
        dialog = window.ensure_composer()
        if dialog is None:
            return
        self._close_sheet()
        dialog.start_mention(self.account, direct=direct)
        window.present_composer()

    def on_copy_handle(self):
        self.get_clipboard().set(self._full_handle())
        pages.show_toast(self, _('Copied handle to clipboard'))

    def on_copy_link(self):
        url = self.account.get('url') or ''
        self.get_clipboard().set(url)
        pages.show_toast(self, _('Copied link to clipboard'))

    def on_open_browser(self):
        url = self.account.get('url')
        if url:
            pages.launch_external(self, url)

    def on_lists(self):
        from .lists import present_list_membership
        present_list_membership(self, self.client, self.account)

    def on_report(self):
        from .report import present_report
        present_report(self, self.client, self.account)

    def on_edit_profile(self):
        from .profileedit import present_profile_edit
        present_profile_edit(self, self.client, self.account,
                             on_saved=self.on_profile_saved)

    def on_profile_saved(self, account):
        self.account = dict(self.account, **account)
        self.refresh_header(self.account)

    # Confirmations

    def confirm_unfollow(self):
        """Reversible: act at once and offer Undo, unless the account is
        locked (a fresh request would need approval again)."""
        if self.account.get('locked'):
            pages.confirm(
                self, _('Unfollow %s?') % self._acct(),
                _('Their posts will no longer appear in your home '
                  'timeline.'),
                _('_Unfollow'),
                lambda: self.send_relationship(
                    'unfollow', done=_('Unfollowed %s'),
                    failed=_('Could not unfollow %s')))
            return
        self.send_relationship(
            'unfollow', done=_('Unfollowed %s'),
            failed=_('Could not unfollow %s'),
            undo=('follow', None, _('_Undo')))

    def confirm_cancel_request(self):
        self.send_relationship(
            'unfollow', done=_('Withdrew follow request to %s'),
            failed=_('Could not withdraw follow request to %s'),
            undo=('follow', None, _('_Undo')))

    def confirm_remove_follower(self):
        pages.confirm(
            self, _('Remove %s from Followers?') % self._acct(),
            _('They will stop following you and will not be told.'),
            _('_Remove'),
            lambda: self.send_relationship(
                'remove_from_followers',
                done=_('Removed %s from your followers'),
                failed=_('Could not remove %s from your followers')))

    def confirm_block(self, block):
        if block:
            pages.confirm(
                self, _('Block %s?') % self._acct(),
                _('They will not be able to follow you or see your posts, '
                  'and their posts will be hidden from you.'),
                _('_Block'),
                lambda: self.send_relationship(
                    'block', done=_('Blocked %s'),
                    failed=_('Could not block %s')))
        else:
            self.send_relationship(
                'unblock', done=_('Unblocked %s'),
                failed=_('Could not unblock %s'),
                undo=('block', None, _('_Undo')))

    def confirm_domain_block(self, block):
        domain = self._remote_domain()
        if not domain:
            return
        if block:
            heading = _('Block Entire %s?') % domain
            body = _('Blocking a domain will:\n\n'
                     '• Remove its public posts and notifications from '
                     'your timelines\n'
                     '• Remove its followers from your account\n'
                     '• Prevent you from following its users')
            label = _('_Block')
        else:
            heading = _('Unblock Entire %s?') % domain
            body = _('You will be able to see and follow its users '
                     'again.')
            label = _('_Unblock')
        if block:
            pages.confirm(self, heading, body, label,
                          lambda: self.send_domain_block(block, domain),
                          destructive=True)
        else:
            self.send_domain_block(block, domain, undo=_('_Undo'))

    # Requests

    def _set_busy(self, busy):
        self._busy = busy
        self.update_follow_button()
        if self.menu_button is not None:
            self.menu_button.set_sensitive(not busy)

    def send_relationship(self, operation, form=None, done=None,
                          failed=None, on_failed=None, undo=None):
        """POST /accounts/:id/<operation>; the response is the new
        Relationship. *undo* is ``(operation, form, label)`` for a
        reversible action, offered as a button on the success toast."""
        self._set_busy(True)
        handle = self._acct()

        def finished(client, result, _data=None):
            try:
                relationship = client.request_finish(result)
            except GLib.Error as error:
                self._set_busy(False)
                pages.report_error(
                    self, (failed or _('Action failed: %s')) % handle,
                    error)
                if on_failed is not None:
                    on_failed(error)
                self.update_menu()
                return
            self._busy = False
            self.set_relationship(relationship)
            if not done:
                return
            undo_action = None
            if undo is not None:
                undo_op, undo_form, undo_label = undo
                undo_action = lambda: self.send_relationship(  # noqa: E731
                    undo_op, form=undo_form,
                    done=_('Restored %s'), failed=failed, undo=None)
            pages.show_toast(self, done % handle, undo=undo_action,
                             undo_label=undo[2] if undo else None)

        self.client.request_async(
            'POST', '/accounts/%s/%s' % (self.account['id'], operation),
            form=form, callback=finished)

    def send_domain_block(self, block, domain, undo_label=None):
        self._set_busy(True)

        def finished(client, result, _data=None):
            try:
                client.request_finish(result)
            except GLib.Error as error:
                self._set_busy(False)
                pages.report_error(
                    self, _('Could not block domain') if block
                    else _('Could not unblock domain'), error)
                return
            self._busy = False
            self.relationship = dict(self.relationship,
                                     domain_blocking=block)
            self.update_relationship_ui()
            undo_action = (lambda: self.send_domain_block(not block, domain)) \
                if undo_label else None
            pages.show_toast(
                self, (_('Blocked %s') if block else _('Unblocked %s'))
                % domain, undo=undo_action, undo_label=undo_label)
            self.load_relationship()

        if block:
            self.client.request_async('POST', '/domain_blocks',
                                      form={'domain': domain},
                                      callback=finished)
        else:
            self.client.request_async('DELETE', '/domain_blocks',
                                      params={'domain': domain},
                                      callback=finished)

    def on_mute(self):
        present_mute_dialog(
            self, self._acct(),
            lambda duration, notifications: self.send_relationship(
                'mute',
                form={'notifications': str(notifications).lower(),
                      'duration': str(duration)},
                done=_('Muted %s'), failed=_('Could not mute %s')))

    def on_unmute(self):
        self.send_relationship('unmute', done=_('Unmuted %s'),
                               failed=_('Could not unmute %s'))

    def set_boosts(self, show):
        self.send_relationship(
            'follow', form={'reblogs': str(show).lower()},
            done=_('Showing boosts from %s') if show
            else _('Hiding boosts from %s'),
            failed=_('Could not change boosts from %s'))

    def set_notifying(self, notify):
        self.send_relationship(
            'follow', form={'notify': str(notify).lower()},
            done=_('You will be notified when %s posts') if notify
            else _('You will no longer be notified when %s posts'),
            failed=_('Could not change notifications for %s'))

    def set_endorsed(self, endorse):
        """Feature or unfeature the account on your own profile. Servers
        that predate the ``endorse`` aliases only know ``pin``/``unpin``,
        so a 404 retries with the other spelling."""
        version = ((self.client.instance_info or {}).get('api_versions')
                   or {}).get('mastodon', 0)
        names = ('endorse', 'pin') if endorse else ('unendorse', 'unpin')
        if 0 < version < 6:
            names = names[::-1]
        done = _('Featured %s on your profile') if endorse \
            else _('Removed %s from your featured profiles')
        failed = _('Could not feature %s') if endorse \
            else _('Could not unfeature %s')

        self._set_busy(True)
        handle = self._acct()

        def finished(client, result, attempt):
            try:
                relationship = client.request_finish(result)
            except GLib.Error as error:
                if getattr(error, 'code', 0) == 404 and attempt == 0:
                    send(1)
                    return
                self._set_busy(False)
                pages.report_error(self, failed % handle, error)
                self.update_menu()
                return
            self._busy = False
            self.set_relationship(relationship)
            pages.show_toast(self, done % handle)

        def send(attempt):
            self.client.request_async(
                'POST', '/accounts/%s/%s' % (self.account['id'],
                                             names[attempt]),
                callback=lambda c, r, _d=None: finished(c, r, attempt))

        send(0)

    # Private note

    def edit_private_note(self, text=None):
        note = self.relationship.get('note') if text is None else text
        dialog = PrivateNoteDialog(
            self._acct(), note,
            lambda value: self.save_private_note(dialog, value))
        dialog.present(self)

    def save_private_note(self, dialog, text):
        def finished(client, result, _data=None):
            try:
                relationship = client.request_finish(result)
            except GLib.Error as error:
                dialog.save_button.set_sensitive(True)
                pages.report_error(self, _('Could not save note'), error)
                return
            dialog.close()
            self.set_relationship(relationship)
            pages.show_toast(self, _('Private note saved'))

        self.client.request_async(
            'POST', '/accounts/%s/note' % self.account['id'],
            form={'comment': text}, callback=finished)

    # State helpers

    def set_stack_state(self, name):
        self.stack.set_visible_child_name(name)
        self._content = name == 'content'
        self.switcher.set_visible(self._content)
        self.switcher_bar.set_visible(self._content)
        if self.menu_button is not None:
            self.menu_button.set_visible(self._content)
        self.update_follow_button()

    def on_retry(self, _button):
        if self.account.get('id') is not None:
            self.on_account_ready()
        elif isinstance(self, LookupProfilePage):
            self.lookup()


class LookupProfilePage(ProfilePage):
    """Profile page that resolves an @user@instance handle first: the
    instance's account lookup, then a resolving search for remote
    accounts it has not met yet."""

    def __init__(self, client, acct, **kwargs):
        acct = (acct or '').lstrip('@')
        super().__init__(client, {'acct': acct, 'note': ''}, **kwargs)
        self.lookup()

    def lookup(self):
        self.set_stack_state('loading')
        self.client.lookup_account_async(self.account['acct'],
                                         self.on_lookup)

    def on_lookup(self, client, result, _user_data=None):
        try:
            account = client.lookup_account_finish(result)
        except GLib.Error as e:
            if e.code == 404 and self._signed_in():
                self.resolve()
                return
            self.show_lookup_error(e)
            return
        self.show_account(account)

    def resolve(self):
        """Search with resolve=true: makes the server fetch a remote
        account it does not know yet."""
        self.client.request_async(
            'GET', '/api/v2/search',
            params={'q': self.account['acct'], 'type': 'accounts',
                    'resolve': 'true', 'limit': 5},
            callback=self.on_resolved)

    def on_resolved(self, client, result, _user_data=None):
        try:
            data = client.request_finish(result)
        except GLib.Error as e:
            self.show_lookup_error(e)
            return
        wanted = self.account['acct'].lower()
        found = data.get('accounts') or []
        account = next((a for a in found
                        if a.get('acct', '').lower() == wanted),
                       found[0] if found else None)
        if account is None:
            self.error_page.set_description(_('No account found for %s')
                                            % self._acct())
            self.set_stack_state('error')
            return
        self.show_account(account)

    def show_lookup_error(self, error):
        self.error_page.set_description(summarize_error(error.message))
        self.set_stack_state('error')

    def show_account(self, account):
        self.account = account
        self.on_account_ready()
