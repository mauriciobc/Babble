"""Status detail views: who favourited / boosted / quoted a post, and
the edit history of a post."""

import datetime
from .i18n import gettext as _

import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import pages  # noqa: E402

STATS_TABS = (
    ('favourites', 'favourites_count', '/statuses/%s/favourited_by'),
    ('boosts', 'reblogs_count', '/statuses/%s/reblogged_by'),
    ('quotes', 'quotes_count', '/statuses/%s/quotes'),
)


def _tab_title(kind, count):
    label = {'favourites': _('Favorites'),
             'boosts': _('Boosts')}.get(kind, _('Quotes'))
    if count is None:
        return label
    return '%s · %d' % (label, count)


class StatusStatsView(Adw.Bin):
    """Tabbed Favorites / Boosts / Quotes lists for one post."""

    __gtype_name__ = 'BabbleStatusStatsView'

    def __init__(self, client, status, kind):
        super().__init__()
        self.client = client
        # Stats are always about the boosted post itself.
        status = status.get('reblog') or status
        status_id = status['id']

        self.stack = Adw.ViewStack()
        self._builders = {}
        self._built = set()
        self._kinds = []
        for tab_kind, count_key, path in STATS_TABS:
            if tab_kind == 'quotes' and 'quotes_count' not in status \
                    and kind != 'quotes':
                continue
            self._kinds.append(tab_kind)
            holder = Adw.Bin()
            page = self.stack.add_titled(
                holder, tab_kind, _tab_title(tab_kind, status.get(count_key)))
            page.set_icon_name({'favourites': 'starred-symbolic',
                                'boosts': 'media-playlist-repeat-symbolic',
                                'quotes': 'chat-bubble-text-symbolic'}[tab_kind])
            self._builders[tab_kind] = (
                lambda k=tab_kind, p=path % status_id: self._make_tab(k, p))

        if kind not in self._kinds:
            kind = self._kinds[0]
        self.stack.set_visible_child_name(kind)
        self._ensure_built(kind)
        pages.connect_weak(self.stack, 'notify::visible-child-name',
                           self._on_switched)

        self.switcher = Adw.ViewSwitcher(
            stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.title_widget = self.switcher
        self.set_child(self.stack)

    def _make_tab(self, kind, path):
        if kind == 'favourites':
            return pages.AccountListPage(
                self.client, path, empty_title=_('No Favorites Yet'))
        if kind == 'boosts':
            return pages.AccountListPage(
                self.client, path, empty_title=_('No Boosts Yet'))
        from . import timeline
        return timeline.TimelinePage(
            self.client, mode='home',
            source=timeline.ApiTimelineSource(self.client, path,
                                              paging='link'))

    def _ensure_built(self, kind):
        if kind in self._built or kind not in self._builders:
            return
        self._built.add(kind)
        self.stack.get_child_by_name(kind).set_child(self._builders[kind]())

    def _on_switched(self, *_args):
        name = self.stack.get_visible_child_name()
        if name:
            self._ensure_built(name)

    def refresh(self):
        name = self.stack.get_visible_child_name()
        holder = self.stack.get_child_by_name(name) if name else None
        child = holder.get_child() if holder is not None else None
        if child is not None and hasattr(child, 'refresh'):
            child.refresh()


def push_status_stats(widget, client, status, kind):
    """Open the favourites / boosts / quotes view for *status*."""
    target = status.get('reblog') or status
    view = StatusStatsView(client, status, kind)
    titles = {'favourites': _('Favorites'), 'boosts': _('Boosts'),
              'quotes': _('Quotes')}
    return pages.push_page(widget, titles.get(kind, _('Favorites')), view,
                           tag='stats-%s' % target['id'])


# Edit history

def _parse_time(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace('Z', '+00:00'))
    except (ValueError, AttributeError, TypeError):
        return None


def _absolute_time(iso):
    then = _parse_time(iso)
    if then is None:
        return ''
    return then.astimezone().strftime('%-d %b %Y, %H:%M')


def _attachment_ids(revision):
    return [(m.get('id'), m.get('description') or '')
            for m in revision.get('media_attachments') or []]


def _poll_titles(revision):
    poll = revision.get('poll') or {}
    return [o.get('title') for o in poll.get('options') or []]


def _changes(previous, revision):
    """Human list of what differs between two consecutive revisions."""
    changed = []
    if previous.get('content') != revision.get('content'):
        changed.append(_('text'))
    if _attachment_ids(previous) != _attachment_ids(revision):
        changed.append(_('media'))
    if _poll_titles(previous) != _poll_titles(revision):
        changed.append(_('poll'))
    if previous.get('spoiler_text') != revision.get('spoiler_text'):
        changed.append(_('content warning'))
    if previous.get('sensitive') != revision.get('sensitive') \
            and _('media') not in changed:
        changed.append(_('sensitivity'))
    return changed


class EditHistoryView(Adw.Bin):
    """Revisions of one post, newest first, one card per revision."""

    __gtype_name__ = 'BabbleEditHistoryView'

    def __init__(self, client, status):
        super().__init__()
        self.client = client
        self.status_id = (status.get('reblog') or status)['id']
        self.stack = Gtk.Stack()
        self.loading_page = pages.make_loading_page(_('Loading Edit History'))
        self.error_page = pages.make_error_page(
            _('Could Not Load Edit History'), self._on_retry)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                               spacing=12, margin_top=12, margin_bottom=12,
                               margin_start=12, margin_end=12)
        clamp = Adw.Clamp(maximum_size=660, tightening_threshold=500,
                          child=self.content)
        scrolled = Gtk.ScrolledWindow(vexpand=True, child=clamp)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.stack.add_named(self.loading_page, 'loading')
        self.stack.add_named(self.error_page, 'error')
        self.stack.add_named(scrolled, 'list')
        self.set_child(self.stack)
        self.refresh()

    def _on_retry(self, _button):
        self.refresh()

    def refresh(self):
        self.stack.set_visible_child_name('loading')
        self.client.request_async(
            'GET', '/statuses/%s/history' % self.status_id,
            callback=self._on_loaded)

    def _on_loaded(self, client, result, _data=None):
        try:
            revisions = client.request_finish(result)
        except GLib.Error as error:
            self.error_page.set_description(error.message)
            self.stack.set_visible_child_name('error')
            return
        while (child := self.content.get_first_child()) is not None:
            self.content.remove(child)
        revisions = revisions or []
        for index in reversed(range(len(revisions))):
            previous = revisions[index - 1] if index > 0 else None
            self.content.append(
                self._make_card(index, revisions[index], previous))
        self.stack.set_visible_child_name('list')

    def _make_card(self, index, revision, previous):
        from . import textemoji, timeline

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class('card')
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(card, 'set_margin_' + side)(0)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                        margin_top=12, margin_bottom=12,
                        margin_start=12, margin_end=12)
        card.append(inner)

        header = Gtk.Box(spacing=8)
        title = Gtk.Label(
            label=_('Original') if index == 0 else _('Edit %d') % index,
            xalign=0, hexpand=True)
        title.add_css_class('heading')
        header.append(title)
        created = revision.get('created_at')
        when = Gtk.Label(
            label='%s · %s' % (_absolute_time(created),
                               _('%s ago') % timeline.relative_time(created)),
            xalign=1)
        when.add_css_class('dimmed')
        when.add_css_class('caption')
        header.append(when)
        inner.append(header)

        if previous is not None:
            changed = _changes(previous, revision)
            if changed:
                hint = Gtk.Label(label=_('Changed: %s') % ', '.join(changed),
                                 xalign=0, wrap=True)
                hint.add_css_class('caption')
                hint.add_css_class('dimmed')
                inner.append(hint)

        spoiler = revision.get('spoiler_text')
        if spoiler:
            warning = Gtk.Label(label=spoiler, xalign=0, wrap=True,
                                selectable=True)
            warning.add_css_class('warning')
            warning.add_css_class('heading')
            inner.append(warning)

        body = timeline.StatusBody()
        body.set_segments(timeline.parse_content(revision.get('content')),
                          textemoji.status_emoji_bank(revision))
        inner.append(body)

        attachments = revision.get('media_attachments') or []
        if attachments:
            media = timeline.MediaBox()
            media.set_media(attachments, bool(revision.get('sensitive')))
            inner.append(media)

        titles = _poll_titles(revision)
        if titles:
            poll = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for option in titles:
                row = Gtk.Box(spacing=6)
                row.append(Gtk.Image.new_from_icon_name(
                    'emblem-ok-symbolic'))
                row.append(Gtk.Label(label=option, xalign=0, wrap=True,
                                     hexpand=True))
                poll.append(row)
            inner.append(poll)
        return card


def push_edit_history(widget, client, status):
    """Open the revision list of an edited post."""
    target = status.get('reblog') or status
    return pages.push_page(widget, _('Edit History'),
                           EditHistoryView(client, target),
                           tag='history-%s' % target['id'])
