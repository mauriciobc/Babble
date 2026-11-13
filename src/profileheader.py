# SPDX-License-Identifier: GPL-3.0-or-later

"""Profile header: banner cache, chips, stats, fields and badges.

:class:`ProfileHeader` rebuilds the Profile tab column for a
:class:`~.profile.ProfilePage`. Relationship commands stay in
:mod:`profileactions`; tabs, extras and note/mute dialogs stay on the
page.
"""

import gi

import locale
import urllib.parse

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

from . import imagecache  # noqa: E402
from . import pages  # noqa: E402
from .pages import connect_weak  # noqa: E402
from .mediacache import AvatarCache, decode_async, log_media_failure  # noqa: E402
from .textemoji import (EMOJI_RE, InlineEmojiText, content_segments,  # noqa: E402
                        emoji_bank)


def _decode_banner(data, cancellable, done):
    """Decode a profile banner whole, at whatever size it is."""
    decode_async(data, None, cancellable,
                  lambda texture, error: done(texture,
                                              error and error.message))


class HeaderCache:
    """Profile-banner cache: one request per URL, shared with the other
    image caches.

    Was the odd one out: its own unbounded dict, its own in-flight table
    and its own fetch, so a session over many profiles grew without limit
    while its siblings were capped. Now it is the same store, so a banner
    costs at most BANNER_CACHE_SIZE decoded textures like everything else.
    """

    store = imagecache.Store(imagecache.BANNER_CACHE_SIZE)
    fetcher = imagecache.Fetcher(
        store,
        # A banner fills a fixed-width strip, so it decodes whole rather
        # than being scaled to a box that never changes.
        lambda data, key, cancellable, done: _decode_banner(
            data, cancellable, done),
        on_failure=log_media_failure)

    @classmethod
    def apply(cls, picture, url):
        """Show *url*'s image in *picture*, fetching it when needed."""
        # A failure leaves the banner empty and uncached for a retry.
        cls.fetcher.fetch(
            url, lambda texture, _reason: None if texture is None
            else picture.set_paintable(texture))


VISIBLE_FIELDS = 4
BANNER_HEIGHT = 100


def format_count(count):
    """*count* with the locale's digit grouping."""
    return locale.format_string('%d', count or 0, grouping=True)


def plain_name(account):
    """Display name with custom-emoji shortcodes removed."""
    name = EMOJI_RE.sub('', account.get('display_name') or '').strip()
    return name or account.get('username') or account.get('acct', '')


def link_context(segments, instance_host):
    """The mentions payload for InlineEmojiText link routing, derived
    from the rendered segments of an account's HTML (accounts do not
    carry the ``mentions`` array statuses do). Hashtags need no payload:
    parse_content marks their anchors, so the visible name is exact."""
    mentions = []
    for segment in segments:
        href = segment.get('href')
        text = segment.get('text') or ''
        if not href or not text.startswith('@'):
            continue
        parts = urllib.parse.urlsplit(href)
        user = parts.path.rstrip('/').rsplit('/', 1)[-1].lstrip('@')
        if user and parts.hostname:
            acct = user if parts.hostname == instance_host \
                else '%s@%s' % (user, parts.hostname)
            mentions.append({'url': href, 'acct': acct})
    return mentions


def make_chip(text, tone=None):
    """Small bordered badge; the text carries the meaning, *tone*
    ('accent', 'danger' or None) only tints it."""
    label = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    label.add_css_class('caption-heading')
    label.add_css_class('profile-chip')
    if tone:
        label.add_css_class('profile-chip-%s' % tone)
    return label


class ProfileHeader(GObject.Object):
    """Rebuilds the account header column the Profile tab scrolls.

    *page* is the ProfilePage whose holder, account and relationship
    this object reads. Call sites on the page stay as one-line forwards
    so relationship UI and root-notify keep calling the same names.

    A GObject so :func:`pages.connect_weak` / :func:`pages.weak_method`
    can target its methods (banner / avatar / fields toggles) without a
    C-level cycle through the widgets it builds.
    """

    def __init__(self, page):
        super().__init__()
        self.page = page

    def refresh(self, account):
        """(Re)build the header above the tab stack for *account*."""
        page = self.page
        page._generation += 1
        while True:
            old = page.holder.get_first_child()
            if old is None:
                break
            page.holder.remove(old)

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
        # A banner picture opens the viewer, so it is a button to
        # assistive tech; the role is fixed at construction.
        banner = Gtk.Overlay(
            overflow=Gtk.Overflow.HIDDEN,
            accessible_role=(Gtk.AccessibleRole.BUTTON if header_url
                             else Gtk.AccessibleRole.GENERIC))
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
                banner, header_url, account.get('header_description'),
                _('View Header Image'))
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
        display = account.get('display_name') or account.get('acct', '')
        bank = emoji_bank(account)
        name = InlineEmojiText(emoji_size=18)
        name.set_segments([{'text': display, 'href': None}], bank)
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
        avatar_url = account.get('avatar') or account.get('avatar_static')
        page.avatar = Adw.Avatar(
            size=64, show_initials=True, halign=Gtk.Align.START,
            valign=Gtk.Align.START,
            accessible_role=(Gtk.AccessibleRole.BUTTON if avatar_url
                             else Gtk.AccessibleRole.IMG))
        page.avatar.set_text(display or '?')
        description = account.get('avatar_description')
        if description:
            page.avatar.update_property(
                [Gtk.AccessibleProperty.DESCRIPTION], [description])
        page.avatar.set_margin_start(16)
        page.avatar.set_margin_top(BANNER_HEIGHT - 32)
        overlay.add_overlay(page.avatar)
        AvatarCache.load(page.avatar, account)
        if avatar_url:
            self._open_image_on_click(page.avatar, avatar_url, description,
                                      _('View Avatar'))
        header.append(overlay)

        # Badges (account type and relationship) and the private note.
        page.badge_box = Adw.WrapBox(child_spacing=6, line_spacing=6)
        page.badge_box.set_margin_start(4)
        header.append(page.badge_box)
        page.note_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                     visible=False)
        page.note_list.add_css_class('boxed-list')
        connect_weak(page.note_list, 'row-activated',
                     page._on_note_activated)
        header.append(page.note_list)

        header.append(self._build_stats(account))

        page.familiar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                    visible=False)
        header.append(page.familiar_box)

        segments = content_segments(account.get('note', ''))
        if segments:
            header.append(self._rich_text(
                segments, account, emoji_size=16, paragraph_gap=True))

        fields = account.get('fields') or []
        if fields:
            header.append(self._build_fields(fields, account))

        page.tags_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=6, visible=False)
        header.append(page.tags_box)
        page.endorse_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=6, visible=False)
        header.append(page.endorse_box)

        self.update_badges()
        page.update_follow_button()
        page.update_menu()

        # The header is the whole of the Profile tab's column.
        page.holder.prepend(header)
        page.load_extras()

    def update_badges(self):
        """Account-type and relationship chips plus the private note."""
        page = self.page
        if page.badge_box is None:
            return
        while (child := page.badge_box.get_first_child()) is not None:
            page.badge_box.remove(child)
        account, rs = page.account, page.relationship
        chips = []
        if account.get('memorial'):
            chips.append((_('In Memoriam'), None))
        if account.get('suspended'):
            chips.append((_('Suspended'), 'danger'))
        if account.get('invalid_handle'):
            # Mastodon 4.7: the handle cannot be verified.
            chips.append((_('Unverified Handle'), 'danger'))
        if account.get('locked'):
            chips.append((_('Locked'), None))
        if account.get('bot'):
            chips.append((_('Automated'), None))
        if account.get('group'):
            chips.append((_('Group'), None))
        for role in account.get('roles') or []:
            if role.get('name'):
                chips.append((role['name'], None))
        if not page._is_self():
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
            page.badge_box.append(make_chip(text, tone))
        page.badge_box.set_visible(bool(chips))

        if page.note_list is None:
            return
        while (child := page.note_list.get_first_child()) is not None:
            page.note_list.remove(child)
        note = rs.get('note') if not page._is_self() else None
        if note:
            row = Adw.ActionRow(title=_('Private Note'), subtitle=note,
                                use_markup=False, activatable=True)
            row.set_subtitle_lines(0)
            row.add_css_class('property')
            row.add_suffix(Gtk.Image(icon_name='document-edit-symbolic'))
            page.note_list.append(row)
        page.note_list.set_visible(bool(note))

    def _rich_text(self, segments, account, emoji_size=16,
                   paragraph_gap=False):
        """InlineEmojiText for *segments* with custom emoji from the
        account bank and mention/hashtag links routed in-app."""
        text = InlineEmojiText(emoji_size=emoji_size,
                               paragraph_gap=paragraph_gap)
        text.set_segments(segments, emoji_bank(account))
        text.link_mentions = link_context(segments,
                                          self.page._instance_host())
        return text

    def _open_image_on_click(self, widget, url, description, label=None):
        """Make *widget* open the image viewer: on click, and from the
        keyboard (Tab to it, Enter or space) with *label* as its name."""
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        connect_weak(click, 'released', self._on_image_clicked, url,
                     description)
        widget.add_controller(click)
        widget.set_cursor_from_name('pointer')
        widget.set_focusable(True)
        widget.add_css_class('profile-image-button')
        if label:
            widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
        pages.activate_on_key(widget, pages.weak_method(self._present_image),
                              url, description)

    def _on_image_clicked(self, _gesture, _n_press, _x, _y, url,
                          description):
        self._present_image(url, description)

    def _present_image(self, url, description):
        from .mediaviewer import present_media
        present_media(self.page, [{'type': 'image', 'url': url,
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
        listbox.append(pages.AccountRow(self.page.client, moved))
        listbox.connect('row-activated', self.page._on_account_row_activated)
        box.append(listbox)
        box.add_css_class('profile-moved')
        return box

    def _build_stats(self, account):
        """Posts / following / followers: the last two open the account
        lists, posts jumps to the Posts tab."""
        page = self.page
        stats = Adw.WrapBox(child_spacing=4, line_spacing=2)
        stats.set_margin_top(2)
        posts = account.get('statuses_count') or 0
        following = account.get('following_count') or 0
        followers = account.get('followers_count') or 0
        stats.append(self._stat_button(
            posts, ngettext('post', 'posts', posts), page.on_posts_clicked))
        stats.append(self._stat_button(
            following, _('following'), page.on_following_clicked))
        stats.append(self._stat_button(
            followers, ngettext('follower', 'followers', followers),
            page.on_followers_clicked))
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
        connect_weak(button, 'clicked', callback)
        return button

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
            if index >= VISIBLE_FIELDS:
                box = row.get_child()
                revealer = Gtk.Revealer(
                    transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
                row.set_child(None)
                revealer.set_child(box)
                row.set_child(revealer)
            row.set_visible(index < VISIBLE_FIELDS)
            listbox.append(row)
        if len(rows) > VISIBLE_FIELDS:
            toggle = Adw.ButtonRow(title=_('Show More'))
            connect_weak(toggle, 'activated', self.on_fields_toggle,
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
        # Wrapped text asks for no width of its own, so beside the check
        # in a horizontal box the name was allocated none and vanished.
        title.set_hexpand(True)
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
            revealer = row.get_child()
            if expand:
                row.set_visible(True)
                pages.reveal(revealer, True, animate=True)
            else:
                pages.reveal(revealer, False)
                row.set_visible(False)
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
