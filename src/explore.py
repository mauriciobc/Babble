# SPDX-License-Identifier: GPL-3.0-or-later

"""Explore: trending posts, hashtags and news, plus follow suggestions.

Tuba: Views/Explore.vala, Views/Link.vala, Widgets/PreviewCardExplore.vala,
Widgets/AccountSuggestions.vala. Phanpy: pages/trending.jsx.
"""

import re
import urllib.parse
from gettext import gettext as _, ngettext

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from . import pages  # noqa: E402

LINK_TIMELINE_MIN_VERSION = (4, 3)
PEOPLE_DAYS = 2

SOURCE_REASONS = {
    'staff': lambda: _('Featured by the server staff'),
    'featured': lambda: _('Featured by the server staff'),
    'past_interactions': lambda: _('You interacted with them before'),
    'most_interactions': lambda: _('Popular lately'),
    'global': lambda: _('Popular on the server'),
    'most_followed': lambda: _('Popular on the server'),
    'similar_to_recently_followed':
        lambda: _('Similar to people you followed'),
    'friends_of_friends': lambda: _('Followed by people you follow'),
}


# Helpers

def people_talking(link):
    """Distinct accounts that shared the link over the newest days."""
    return sum(int(entry.get('accounts') or 0)
               for entry in (link.get('history') or [])[:PEOPLE_DAYS])


def supports_link_timeline(client):
    """True when the server is Mastodon 4.3+ (GET /timelines/link). An
    unknown version is optimistically accepted."""
    version = (getattr(client, 'instance_info', None) or {}).get('version')
    match = re.match(r'^(\d+)\.(\d+)', version or '')
    if not match:
        return True
    return (int(match.group(1)), int(match.group(2))) \
        >= LINK_TIMELINE_MIN_VERSION


def open_link_timeline(widget, client, link):
    """Timeline of posts sharing *link* (a trending PreviewCard) when the
    server supports it, the browser otherwise."""
    url = link.get('url', '')
    if not supports_link_timeline(client):
        pages.launch_external(widget, url)
        return
    pages.push_page(widget, link.get('title') or _('Link'),
                    LinkTimelinePage(client, link),
                    tag='link-%s' % url)


def _domain(url):
    host = urllib.parse.urlsplit(url).netloc
    return host[4:] if host.startswith('www.') else host


class LinkTimelinePage(Adw.Bin):
    """Posts mentioning one link, under a summary row of the link."""

    __gtype_name__ = 'BabbleLinkTimelinePage'

    def __init__(self, client, link):
        super().__init__()
        from .timeline import ApiTimelineSource, TimelinePage
        self.link = link
        self.timeline = TimelinePage(client, source=ApiTimelineSource(
            client, '/timelines/link', params={'url': link['url']}))
        self.timeline.set_vexpand(True)
        self.timeline.empty_page.set_icon_name('edit-find-symbolic')
        self.timeline.empty_page.set_title(_('No Posts Share This Link'))
        self.timeline.empty_page.set_description(
            _('Posts mentioning the link will appear here'))

        summary = Adw.ActionRow(
            title=link.get('title') or link['url'], activatable=True,
            subtitle=link.get('provider_name') or _domain(link['url']),
            use_markup=False, title_lines=2, subtitle_lines=1)
        summary.add_suffix(Gtk.Image(icon_name='web-browser-symbolic'))
        summary.connect('activated', lambda *_a: self._open_browser())
        frame = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                            margin_top=12, margin_bottom=4,
                            margin_start=12, margin_end=12)
        frame.add_css_class('boxed-list')
        frame.append(summary)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Adw.Clamp(maximum_size=pages.COLUMN_WIDTH, child=frame))
        box.append(self.timeline)
        self.set_child(box)
        self.browser_button = pages.icon_button(
            'web-browser-symbolic', _('Open in Browser'),
            self._open_browser)
        self.header_widgets = [self.browser_button]

    def refresh(self):
        self.timeline.refresh()

    def _open_browser(self):
        pages.launch_external(self, self.link['url'])


class LinkCard(Gtk.ListBoxRow):
    """Trending news card: image, provider, title, description and how
    many people are talking about it."""

    __gtype_name__ = 'BabbleLinkCard'

    def __init__(self, client, link):
        super().__init__(activatable=True)
        from .timeline import _TextureCache
        self.client = client
        self.link = link
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(box)

        image_url = link.get('image')
        if image_url:
            picture = Gtk.Picture(
                content_fit=Gtk.ContentFit.COVER, height_request=160,
                can_shrink=True, alternative_text=link.get(
                    'image_description') or '',
                accessible_role=Gtk.AccessibleRole.PRESENTATION)
            picture.add_css_class('link-card-image')
            box.append(picture)
            _TextureCache.load(image_url, picture)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       margin_top=10, margin_bottom=12, margin_start=14,
                       margin_end=14)
        box.append(text)
        provider = link.get('provider_name') or _domain(link.get('url', ''))
        meta = Gtk.Label(label=provider, xalign=0.0,
                         ellipsize=Pango.EllipsizeMode.END)
        meta.add_css_class('caption-heading')
        meta.add_css_class('dimmed')
        text.append(meta)
        title = Gtk.Label(label=link.get('title') or link.get('url', ''),
                          xalign=0.0, wrap=True, lines=3,
                          ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class('heading')
        text.append(title)
        if link.get('description'):
            description = Gtk.Label(
                label=link['description'], xalign=0.0, wrap=True, lines=2,
                ellipsize=Pango.EllipsizeMode.END)
            description.add_css_class('body')
            text.append(description)
        footer = []
        author = link.get('author_name')
        if author:
            footer.append(_('By %s') % author)
        people = people_talking(link)
        if people:
            footer.append(ngettext('%d person talking',
                                   '%d people talking', people) % people)
        if footer:
            line = Gtk.Label(label=' · '.join(footer), xalign=0.0, wrap=True)
            line.add_css_class('caption')
            line.add_css_class('dimmed')
            text.append(line)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ['%s, %s' % (title.get_label(), provider)])

    def on_activate(self):
        open_link_timeline(self, self.client, self.link)


class SuggestionRow(pages.AccountRow):
    """A suggested account: reason, Follow and dismiss buttons."""

    __gtype_name__ = 'BabbleSuggestionRow'

    def __init__(self, page, suggestion):
        account = suggestion['account']
        super().__init__(page.client, account)
        self.page = page
        sources = suggestion.get('sources') or [suggestion.get('source')]
        reasons = []
        for source in sources:
            reason = SOURCE_REASONS.get(source)
            if reason is not None and reason() not in reasons:
                reasons.append(reason())
        from .search import account_subtitle
        subtitle = account_subtitle(account)
        if reasons:
            subtitle += '\n' + ' · '.join(reasons)
        self.set_subtitle(subtitle)
        self.set_subtitle_lines(2)

        self.follow_button = Gtk.Button(label=_('_Follow'),
                                        use_underline=True,
                                        valign=Gtk.Align.CENTER)
        self.follow_button.add_css_class('suggested-action')
        self.follow_button.connect('clicked', self._on_follow)
        self.dismiss_button = pages.icon_button(
            'window-close-symbolic', _('Dismiss Suggestion'),
            self._on_dismiss)
        self.add_suffix(self.follow_button)
        self.add_suffix(self.dismiss_button)

    def _on_follow(self, button):
        button.set_sensitive(False)
        client = self.page.client

        def finished(c, result, _d=None):
            try:
                relationship = c.request_finish(result)
            except GLib.Error as error:
                button.set_sensitive(True)
                pages.report_error(self, _('Could not follow'), error)
                return
            button.remove_css_class('suggested-action')
            button.set_label(_('Requested') if relationship.get('requested')
                             else _('Following'))

        client.request_async(
            'POST', '/accounts/%s/follow' % self.account['id'],
            callback=finished)

    def _on_dismiss(self):
        client = self.page.client
        self.dismiss_button.set_sensitive(False)

        def finished(c, result, _d=None):
            try:
                c.request_finish(result)
            except GLib.Error as error:
                self.dismiss_button.set_sensitive(True)
                pages.report_error(self, _('Could not dismiss the '
                                           'suggestion'), error)
                return
            self.page.paged.remove_row(self)

        client.request_async(
            'DELETE', '/suggestions/%s' % self.account['id'],
            callback=finished)


class SuggestionsPage(Adw.Bin):
    """GET /api/v2/suggestions: accounts the server recommends."""

    __gtype_name__ = 'BabbleSuggestionsPage'

    def __init__(self, client):
        super().__init__()
        self.paged = pages.PagedList(
            client, '/api/v2/suggestions',
            lambda suggestion: SuggestionRow(self, suggestion)
            if suggestion.get('account') else None,
            params={'limit': 40}, paginated=False,
            empty_title=_('No Suggestions'),
            empty_icon='system-users-symbolic',
            empty_description=_('The server has no accounts to suggest '
                                'right now'))
        self.set_child(self.paged)

    def refresh(self):
        self.paged.refresh()


class HashtagsPage(Adw.Bin):
    """GET /trends/tags with a usage sparkline."""

    __gtype_name__ = 'BabbleTrendingTagsPage'

    def __init__(self, client):
        super().__init__()
        from .hashtags import HashtagRow
        self.paged = pages.PagedList(
            client, '/trends/tags',
            lambda tag: HashtagRow(client, tag, sparkline=True),
            params={'limit': 20}, empty_title=_('No Trending Hashtags'),
            empty_icon='edit-find-symbolic')
        self.set_child(self.paged)

    def refresh(self):
        self.paged.refresh()


class NewsPage(Adw.Bin):
    """GET /trends/links as preview cards."""

    __gtype_name__ = 'BabbleTrendingNewsPage'

    def __init__(self, client):
        super().__init__()
        self.paged = pages.PagedList(
            client, '/trends/links', lambda link: LinkCard(client, link),
            params={'limit': 20}, empty_title=_('No Trending News'),
            empty_icon='text-x-generic-symbolic')
        self.set_child(self.paged)

    def refresh(self):
        self.paged.refresh()


class ExplorePage(Adw.Bin):
    """Posts, Hashtags, News and For You tabs behind a view switcher."""

    __gtype_name__ = 'BabbleExplorePage'

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.stack = Adw.ViewStack()
        self.builders = {
            'posts': self._build_posts,
            'hashtags': lambda: HashtagsPage(client),
            'news': lambda: NewsPage(client),
            'suggestions': lambda: SuggestionsPage(client),
        }
        self.bins = {}
        for name, title, icon in (
                ('posts', _('Posts'), 'view-list-symbolic'),
                ('hashtags', _('Hashtags'), 'edit-find-symbolic'),
                ('news', _('News'), 'text-x-generic-symbolic'),
                ('suggestions', _('For You'), 'system-users-symbolic')):
            bin_ = Adw.Bin()
            self.bins[name] = bin_
            self.stack.add_titled_with_icon(bin_, name, title, icon)
        self.set_child(self.stack)
        self.stack.connect('notify::visible-child-name', self._on_tab)
        self.switcher = Adw.InlineViewSwitcher(
            stack=self.stack,
            display_mode=Adw.InlineViewSwitcherDisplayMode.LABELS)
        self.title_widget = self.switcher
        self._populate('posts')

    def _build_posts(self):
        from .timeline import ApiTimelineSource, TimelinePage
        page = TimelinePage(self.client, source=ApiTimelineSource(
            self.client, '/trends/statuses', paging='link'))
        page.empty_page.set_icon_name('edit-find-symbolic')
        page.empty_page.set_title(_('No Trending Posts'))
        page.empty_page.set_description(
            _('This server has no trending posts right now'))
        return page

    def _populate(self, name):
        bin_ = self.bins[name]
        if bin_.get_child() is None:
            bin_.set_child(self.builders[name]())

    def _on_tab(self, *_a):
        name = self.stack.get_visible_child_name()
        if name in self.bins:
            self._populate(name)

    def refresh(self):
        child = self.stack.get_visible_child()
        content = child.get_child() if child is not None else None
        if content is not None and hasattr(content, 'refresh'):
            content.refresh()
