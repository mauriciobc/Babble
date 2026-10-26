# SPDX-License-Identifier: GPL-3.0-or-later

"""Hashpanel's numbers on the pages of the hashtags it follows.

Hashpanel (https://github.com/mauriciobc/hashpanel) follows the daily
hashtags of the Brazilian fediverse, one or more per weekday, and serves
their stats. The banner leads with what the page's own usage line does
not say: whose day it is, today's favourites, boosts and replies, the
week against the one before, and the top post.

The plugin asks Hashpanel for its schedule once, and asks about a tag
only when the schedule has it: opening any other hashtag sends Hashpanel
nothing. Answers are kept for as long as they stay useful (the schedule
an hour, today's stats two minutes, the weekly totals ten), so going
back and forth between pages does not ask again.
"""

import time
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

from gi.repository import Adw, GLib, Gtk, Pango

from babble import plugin

_ = plugin.gettext
ngettext = plugin.ngettext

DEFAULT_SERVER = 'https://hashpanel.mauriciocastro.des.br'
LOCAL_HOSTS = ('localhost', '127.0.0.1', '::1')
SCHEDULE_TTL = 3600
STATS_TTL = 120
WEEKLY_TTL = 600
SNIPPET_LENGTH = 140


def normalize_server(text):
    """The server's address without a trailing slash, or None. Plain
    http only on this machine."""
    text = (text or '').strip().rstrip('/')
    if not text:
        return None
    if '://' not in text:
        text = 'https://' + text
    parts = urlsplit(text)
    if not parts.hostname or parts.scheme not in ('https', 'http'):
        return None
    if parts.scheme == 'http' and parts.hostname not in LOCAL_HOSTS:
        return None
    return text


def weekday_name(day):
    """The weekday *day* (0 is Sunday, as Hashpanel counts) in the
    user's language."""
    # 2026-10-25 was a Sunday.
    return GLib.DateTime.new_local(2026, 10, 25 + day, 12, 0, 0).format('%A')


class _Text(HTMLParser):
    """A post's HTML as one line of text."""

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, _attrs):
        if tag in ('br', 'p'):
            self.parts.append(' ')

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(html):
    parser = _Text()
    parser.feed(html or '')
    parser.close()
    text = ' '.join(''.join(parser.parts).split())
    if len(text) > SNIPPET_LENGTH:
        text = text[:SNIPPET_LENGTH - 1].rstrip() + '…'
    return text


def parse_schedule(data):
    """``{tag: {'days': [weekday, …], 'today': bool}}`` from
    ``/api/hashtag/daily``, or None when it is not what was expected."""
    if not isinstance(data, dict) or not isinstance(
            data.get('dailySchedule'), list):
        return None
    schedule = {}
    for entry in data['dailySchedule']:
        if not isinstance(entry, dict):
            continue
        for tag in entry.get('hashtags') or [entry.get('hashtag')]:
            if not isinstance(tag, str) or not tag:
                continue
            info = schedule.setdefault(tag.lower(),
                                       {'days': [], 'today': False})
            info['days'].append(entry.get('day'))
            info['today'] = info['today'] or bool(entry.get('isToday'))
    return schedule


class Source:
    """Hashpanel's answers, shared by every banner while the plugin is
    enabled: one request per address in flight, and each answer kept
    for its time to live."""

    def __init__(self, context):
        self.context = context
        self.cache = {}  # url -> (expires, data)
        self.pending = {}  # url -> [callback]

    def server(self):
        return normalize_server(self.context.settings.get('server')) \
            or DEFAULT_SERVER

    def get(self, path, ttl, callback):
        """``callback(data)``, the parsed answer, or None when there was
        none to be had."""
        url = self.server() + path
        cached = self.cache.get(url)
        if cached is not None and time.monotonic() < cached[0]:
            callback(cached[1])
            return
        if url in self.pending:
            self.pending[url].append(callback)
            return
        self.pending[url] = [callback]

        def done(response):
            callbacks = self.pending.pop(url, [])
            data = response.data if response.ok else None
            if data is not None:
                self.cache[url] = (time.monotonic() + ttl, data)
            else:
                self.context.log('%s: %s' % (url, response.error))
            for waiting in callbacks:
                waiting(data)
        self.context.request('GET', url, done)

    def schedule(self, callback):
        self.get('/api/hashtag/daily', SCHEDULE_TTL,
                 lambda data: callback(parse_schedule(data)))

    def forget(self):
        self.cache.clear()


def source_of(context):
    source = context.data.get('source')
    if source is None:
        source = context.data['source'] = Source(context)
    return source


class Banner(plugin.HashtagPageExtension):
    """The banner on a followed hashtag's page; hidden on any other."""

    def activate(self):
        self.alive = True

    def deactivate(self):
        self.alive = False

    def create_banner(self):
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                visible=False)
        self.list.add_css_class('boxed-list')

        self.header = Adw.ActionRow(title='#' + self.hashtag)
        self.open_button = Gtk.Button(
            icon_name='adw-external-link-symbolic',
            tooltip_text=_('Open Hashpanel'), valign=Gtk.Align.CENTER)
        self.open_button.add_css_class('flat')
        self.header.add_suffix(self.open_button)
        self.list.append(self.header)

        self.week = Adw.ActionRow(title=_('This Week'), visible=False)
        self.list.append(self.week)

        self.top = Adw.ActionRow(title=_('Top Post Today'), visible=False,
                                 activatable=True, subtitle_lines=2)
        self.top.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
        self.top_link = None
        self.list.append(self.top)

        self.connect(self.open_button, 'clicked', self.on_open)
        self.connect(self.top, 'activated', self.on_top)
        source_of(self.context).schedule(self.on_schedule)
        return self.list

    def on_schedule(self, schedule):
        if not self.alive or not schedule:
            return
        info = schedule.get(self.hashtag)
        if info is None:
            return  # not followed: Hashpanel is not asked about it
        if info['today']:
            self.header.set_title(_('Today’s Hashtag'))
        else:
            days = ', '.join(weekday_name(d) for d in info['days']
                             if isinstance(d, int))
            # Translators: %s is a weekday, or several, as in "Tuesday's
            # hashtag".
            self.header.set_title(_('%s’s Hashtag') % days)
        self.list.set_visible(True)
        source = source_of(self.context)
        tag = quote(self.hashtag, safe='')
        source.get('/api/hashtag/%s/stats' % tag, STATS_TTL, self.on_stats)
        source.get('/api/hashtag/%s/history/weekly' % tag, WEEKLY_TTL,
                   self.on_weekly)

    def on_stats(self, data):
        if not self.alive:
            return
        if not isinstance(data, dict):
            self.header.set_subtitle(_('Hashpanel could not be reached'))
            return
        engagement = data.get('engagement') or {}
        favourites = int(engagement.get('totalFavorites') or 0)
        boosts = int(engagement.get('totalBoosts') or 0)
        replies = int(engagement.get('totalReplies') or 0)
        counts = ' · '.join((
            ngettext('%d favorite', '%d favorites', favourites)
            % favourites,
            ngettext('%d boost', '%d boosts', boosts) % boosts,
            ngettext('%d reply', '%d replies', replies) % replies))
        # Translators: %s is a list of counts, as in "4 favorites ·
        # 7 boosts · 1 reply".
        self.header.set_subtitle(_('Today: %s') % counts)
        toots = data.get('topToots') or []
        top = toots[0] if toots and isinstance(toots[0], dict) else None
        if top and top.get('link'):
            self.top_link = top['link']
            snippet = plain_text(top.get('content'))
            author = top.get('author') or ''
            self.top.set_subtitle('@%s: %s' % (author, snippet) if snippet
                                  else '@' + author)
            self.top.set_visible(True)

    def on_weekly(self, data):
        if not self.alive or not isinstance(data, dict):
            return
        weeks = [w for w in data.get('weeklyData') or []
                 if isinstance(w, dict)]
        if not weeks:
            return
        this = int(weeks[-1].get('totalUses') or 0)
        text = ngettext('%d post this week', '%d posts this week',
                        this) % this
        if len(weeks) > 1:
            before = int(weeks[-2].get('totalUses') or 0)
            text += ' · ' + ngettext('%d the week before',
                                     '%d the week before', before) % before
        self.week.set_subtitle(text)
        self.week.set_visible(True)

    def on_open(self, button):
        self.context.open_link(button, source_of(self.context).server())

    def on_top(self, row):
        if self.top_link:
            self.context.open_link(row, self.top_link)


class HashpanelPreferences(plugin.PreferencesExtension):
    """Which Hashpanel to ask."""

    def create_preferences(self):
        group = Adw.PreferencesGroup(
            description=_('Hashpanel follows the daily hashtags of the '
                          'Brazilian fediverse. Babble asks it only about '
                          'the hashtags it follows.'))
        self.server = Adw.EntryRow(
            title=_('Server'), show_apply_button=True,
            text=self.context.settings.get('server') or DEFAULT_SERVER)
        self.error = Gtk.Label(wrap=True, xalign=0.0, visible=False,
                               ellipsize=Pango.EllipsizeMode.NONE)
        self.error.add_css_class('error')
        group.add(self.server)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(group)
        box.append(self.error)
        self.connect(self.server, 'apply', self.on_apply)
        return box

    def on_apply(self, row):
        server = normalize_server(row.get_text())
        if server is None:
            self.error.set_label(_('Use the server’s https:// address'))
            self.error.set_visible(True)
            return
        self.error.set_visible(False)
        settings = self.context.settings
        if server == DEFAULT_SERVER:
            settings.remove('server')
        else:
            settings.set('server', server)
        row.set_text(server)
        source_of(self.context).forget()
        self.context.toast(row, _('Hashpanel server saved'))
