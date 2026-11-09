# SPDX-License-Identifier: GPL-3.0-or-later

"""A native, read-only view of a Bluesky thread: the parent chain, the
post itself with its counts, and its replies nested like the Mastodon
thread page.  The one place Babble contacts Bluesky, and only after the
reader opened a post; there are no action buttons."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext

from gi.repository import Adw, Gtk, Pango  # noqa: E402

from . import bluesky  # noqa: E402
from . import bskytext  # noqa: E402
from . import pages  # noqa: E402
from .blueskycard import BlueskyQuoteCard  # noqa: E402
from .mediabox import CardPreview, MediaBox, status_picture  # noqa: E402
from .mediacache import AvatarCache, _TextureCache  # noqa: E402
from .statustext import compact_count, relative_time  # noqa: E402
from .textemoji import InlineEmojiText  # noqa: E402
from .thread import INDENT, MAX_NESTING  # noqa: E402
from .timelinebase import COLUMN_WIDTH, absolute_time  # noqa: E402

MAX_REPLIES = 60
THREAD_POST = 'app.bsky.feed.defs#threadViewPost'
NOT_FOUND = 'app.bsky.feed.defs#notFoundPost'
BLOCKED = 'app.bsky.feed.defs#blockedPost'


def push_bluesky_thread(widget, actor, rkey):
    """Open the native thread page for the post *rkey* by *actor* (a DID or
    a handle); the web page when there is no window to push onto."""
    page = BlueskyThreadPage(actor, rkey)
    if pages.push_page(widget, _('Bluesky Post'), page,
                       tag='bsky-%s' % rkey) is None:
        pages.launch_external(widget, bskytext.web_url(actor, rkey))
    return page


def _notice(text):
    label = Gtk.Label(label=text, xalign=0.0, wrap=True,
                      wrap_mode=Pango.WrapMode.WORD_CHAR)
    label.add_css_class('dimmed')
    label.add_css_class('caption')
    label.set_margin_top(6)
    label.set_margin_bottom(6)
    return label


def _node_notice(node):
    kind = node.get('$type')
    if kind == BLOCKED:
        return _notice(_('Post by a blocked account'))
    return _notice(_('Post not found'))


class BlueskyPostView(Gtk.Box):
    """One Bluesky post: author, text, pictures, link card, quoted post
    and, on the focused post, the counts."""

    __gtype_name__ = 'BabbleBlueskyPostView'

    def __init__(self, post, focused=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class('bluesky-post')
        self.post = post
        author = post.get('author') or {}
        record = post.get('record') or {}
        moderation = bskytext.moderation(post)
        name = author.get('displayName') or author.get('handle') or '?'
        self.web_url = ''
        ref = bskytext.post_ref(post.get('uri') or '')
        if ref:
            self.web_url = bskytext.web_url(
                author.get('handle') or author.get('did') or ref[0], ref[1])
        head = Gtk.Box(spacing=8)
        avatar = Adw.Avatar(size=48 if focused else 36, show_initials=True,
                            text=name)
        if author.get('avatar'):
            AvatarCache.load(avatar, {'avatar': author['avatar']})
        head.append(avatar)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                        valign=Gtk.Align.CENTER)
        title = Gtk.Label(label=name, xalign=0.0,
                          ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class('heading')
        names.append(title)
        handle = Gtk.Label(label='@%s' % author.get('handle', ''),
                           xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        handle.add_css_class('caption')
        handle.add_css_class('dimmed')
        names.append(handle)
        head.append(names)
        created = record.get('createdAt') or post.get('indexedAt') or ''
        when = Gtk.Label(
            label=(absolute_time(created) if focused
                   else relative_time(created)),
            valign=Gtk.Align.START)
        when.add_css_class('caption')
        when.add_css_class('dimmed')
        head.append(when)
        self.append(head)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Bluesky post by %(author)s: %(text)s') % {
                'author': name, 'text': record.get('text') or ''}])
        if moderation['withheld']:
            self.append(_notice(_('Bluesky hides this post from people '
                                  'who are not signed in.')))
            if self.web_url:
                button = Gtk.Button(label=_('Open on Bluesky'),
                                    halign=Gtk.Align.START)
                button.add_css_class('pill')
                pages.connect_weak(button, 'clicked', self._on_open_web)
                self.append(button)
            return
        text = record.get('text') or ''
        if text:
            body = InlineEmojiText(emoji_size=20)
            body.set_segments(bskytext.segments(text, record.get('facets')),
                              {})
            self.append(body)
        embed = post.get('embed')
        images = bskytext.attachments(embed)
        if images:
            media = MediaBox()
            media.set_media(images, moderation['sensitive'], {})
            self.append(media)
        video = bskytext.video_poster(embed)
        if video:
            self.append(self._video(video))
        card = bskytext.external(embed)
        if card:
            self.append(CardPreview(card))
        self._quoted(embed)
        if focused:
            self.append(self._counts(post))

    def _quoted(self, embed):
        record = bskytext.quoted(embed)
        if record is not None:
            self.append(BlueskyQuoteCard(bskytext.quote_card_data(record)))
        elif bskytext.embed_kind(embed) in (
                'app.bsky.embed.record', 'app.bsky.embed.recordWithMedia'):
            self.append(_notice(_('Quoted post unavailable')))

    def _video(self, video):
        overlay = Gtk.Overlay()
        picture = status_picture(-1, 200, presentation=True, hexpand=True)
        if video.get('poster'):
            _TextureCache.load(video['poster'], picture,
                               (COLUMN_WIDTH, 200))
        overlay.set_child(picture)
        play = Gtk.Image(icon_name='media-playback-start-symbolic',
                         pixel_size=32, halign=Gtk.Align.CENTER,
                         valign=Gtk.Align.CENTER,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        play.add_css_class('osd')
        overlay.add_overlay(play)
        button = Gtk.Button(child=overlay)
        button.add_css_class('flat')
        pages.set_accessible_label(
            button, video.get('alt') or _('Open Video on Bluesky'))
        pages.connect_weak(button, 'clicked', self._on_open_web)
        return button

    @staticmethod
    def _counts(post):
        parts = []
        for key, singular, plural in (
                ('replyCount', '%s reply', '%s replies'),
                ('repostCount', '%s repost', '%s reposts'),
                ('quoteCount', '%s quote', '%s quotes'),
                ('likeCount', '%s like', '%s likes')):
            count = int(post.get(key) or 0)
            if count:
                parts.append(ngettext(singular, plural, count)
                             % compact_count(count))
        label = Gtk.Label(label=' · '.join(parts), xalign=0.0, wrap=True,
                          visible=bool(parts))
        label.add_css_class('caption')
        label.add_css_class('dimmed')
        return label

    def _on_open_web(self, _button):
        if self.web_url:
            pages.launch_external(self, self.web_url)


class BlueskyThreadPage(Adw.Bin):
    """The thread of one Bluesky post, loaded when the page opens."""

    __gtype_name__ = 'BabbleBlueskyThreadPage'

    def __init__(self, actor, rkey, **kwargs):
        super().__init__(**kwargs)
        self.actor = actor
        self.rkey = rkey
        self.web_url = bskytext.web_url(actor, rkey)
        self._request = None
        self._token = 0
        self._replies_shown = 0
        self._replies_hidden = False
        button = Gtk.Button(icon_name='web-browser-symbolic')
        pages.set_accessible_label(button, _('Open on Bluesky'))
        pages.connect_weak(button, 'clicked', self._on_open_web)
        self.header_widgets = [button]
        self.stack = pages.StateStack(
            _('Loading Post'), _('Could Not Load Post'), self.on_retry)
        self.error_page = self.stack.error_page
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add_css_class('timeline-list')
        self.thread_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        clamp = Adw.Clamp(maximum_size=COLUMN_WIDTH,
                          tightening_threshold=COLUMN_WIDTH)
        clamp.set_child(self.thread_box)
        scrolled.set_child(clamp)
        self.stack.add_named(scrolled, 'list')
        self.set_child(self.stack)
        self.load()

    def load(self):
        bluesky.cancel(self._request)
        self._token = self.stack.begin()
        self._request = bluesky.get_thread_async(
            self.actor, self.rkey, pages.weak_method(self._on_thread))

    def on_retry(self, _button):
        self.load()

    def _on_thread(self, thread, error):
        if not self.stack.is_current(self._token):
            return
        if thread is None or thread.get('$type') != THREAD_POST:
            self.stack.show_error(
                error or _('This post is unavailable on Bluesky.'))
            return
        self.build(thread)
        self.stack.show('list')

    def build(self, thread):
        pages.clear_children(self.thread_box)
        parents = []
        node = thread.get('parent')
        while isinstance(node, dict):
            parents.append(node)
            node = node.get('parent') if node.get('$type') == THREAD_POST \
                else None
        parents.reverse()
        chain = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        chain.add_css_class('thread-card')
        for node in parents:
            if node.get('$type') == THREAD_POST:
                chain.append(BlueskyPostView(node['post']))
            else:
                chain.append(_node_notice(node))
        focused = BlueskyPostView(thread['post'], focused=True)
        focused.add_css_class('thread-focused')
        focused.update_property([Gtk.AccessibleProperty.DESCRIPTION],
                                [_('Selected post')])
        chain.append(focused)
        self.thread_box.append(chain)
        self._replies_shown = 0
        self._replies_hidden = False
        self._append_replies(self.thread_box, thread.get('replies') or [], 0)
        if self._replies_hidden:
            more = Gtk.Button(label=_('More Replies on Bluesky'),
                              halign=Gtk.Align.CENTER, margin_top=8,
                              margin_bottom=12)
            more.add_css_class('pill')
            pages.connect_weak(more, 'clicked', self._on_open_web)
            self.thread_box.append(more)

    def _append_replies(self, parent_box, replies, depth):
        for node in replies:
            if self._replies_shown >= MAX_REPLIES:
                self._replies_hidden = True
                return
            self._replies_shown += 1
            if node.get('$type') != THREAD_POST:
                parent_box.append(_node_notice(node))
                continue
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            if depth == 0:
                column.add_css_class('thread-card')
            else:
                column.add_css_class('thread-nested')
                column.set_margin_start(
                    INDENT if depth < MAX_NESTING else 0)
            column.append(BlueskyPostView(node['post']))
            parent_box.append(column)
            self._append_replies(column, node.get('replies') or [],
                                 depth + 1)

    def _on_open_web(self, _button):
        pages.launch_external(self, self.web_url)
