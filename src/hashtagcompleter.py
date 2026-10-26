# SPDX-License-Identifier: GPL-3.0-or-later
"""Hashtag suggestions for the ``#tag`` being typed in a text view.

The first half of this module is plain functions with no widget in them:
the rule for what a hashtag being typed looks like (:func:`hashtag_query`),
how a suggestion takes the casing already typed (:func:`merge_casing`),
and the order suggestions are shown in (:func:`rank_tags`). The second
is :class:`HashtagCompleter`, a :class:`~.completer.TokenCompleter`."""

import time
import unicodedata
import weakref
from collections import OrderedDict

import gi

gi.require_version('Gtk', '4.0')

from .i18n import gettext as _, ngettext

from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import pages  # noqa: E402
from .completer import MAX_SUGGESTIONS, TokenCompleter  # noqa: E402
from .statustext import history_totals  # noqa: E402

# What starts a hashtag: both hash signs Mastodon accepts.
_TRIGGERS = ('#', '\uff03')

# Name characters besides the categories below: the separators Mastodon
# allows in a tag (middle dot, katakana middle dot, zero width non-joiner).
_SEPARATORS = ('\u00b7', '\u30fb', '\u200c')

# The order pool tags rank in.
_SOURCE_ORDER = {'recent': 0, 'featured': 1, 'followed': 2}


def is_name_char(char):
    """Whether *char* can be part of a hashtag name: a letter, a mark
    (Mastodon's ``[[:word:]]`` takes the marks Indic and Thai scripts
    spell with, which Python's ``\\w`` does not), a decimal digit, a
    connector such as ``_``, or one of the separators."""
    if not char or len(char) != 1:
        return False
    if char in _SEPARATORS:
        return True
    category = unicodedata.category(char)
    return category[0] in 'LM' or category in ('Nd', 'Pc')


def hashtag_query(text):
    """``(start, query)`` for the hashtag *text* (the text before the
    cursor, on one line) ends with, *start* being the offset of its
    ``#``; None when the cursor is not at the end of a hashtag. A tag
    starts at the beginning of the line or after whitespace (so ``C#``,
    ``(#x`` and ``example.com/#a`` are not tags) and the query is the
    name typed so far, empty for a bare ``#``."""
    start = len(text)
    while start > 0 and is_name_char(text[start - 1]):
        start -= 1
    if start == 0 or text[start - 1] not in _TRIGGERS:
        return None
    trigger = start - 1
    if trigger > 0 and not text[trigger - 1].isspace():
        return None
    return trigger, text[start:]


def wants_search(query):
    """Whether *query* is worth a request: Mastodon only knows a tag
    that has a letter in it, so ``#2024`` matches nothing."""
    # A name starts and ends on a word character: a separator at either
    # edge is a tag still being typed.
    if query[:1] in _SEPARATORS or query[-1:] in _SEPARATORS:
        return False
    return any(unicodedata.category(char).startswith('L') for char in query)


def merge_casing(typed, name):
    """*name* with the capitals already typed kept: ``GamingD`` and
    ``gamingdeals`` give ``GamingDeals``. Only when lower-casing keeps
    the lengths (``İ`` lower-cases to two characters) and *name* begins
    with *typed*; otherwise *name* as the server spells it."""
    lowered, folded = typed.lower(), name.lower()
    if len(lowered) != len(typed) or len(folded) != len(name) \
            or not folded.startswith(lowered):
        return name
    head = ''.join(have if have.isupper() else suggested
                   for have, suggested in zip(typed, name, strict=False))
    return head + name[len(typed):]


def _tag_name(tag):
    name = tag.get('name') if isinstance(tag, dict) else None
    return name if isinstance(name, str) and name else None


def rank_tags(typed, pool_hits, server_tags):
    """Up to ``MAX_SUGGESTIONS`` tags to suggest for *typed*, as copies
    with ``source`` (the pool's, or None for the server) and ``people``
    (over the window of ``history_totals``) added. In order: the exact
    match, the pool's hits (recent, then featured, then followed), the
    server's tags that are followed, then the others by people, the
    server's order breaking ties. One entry per name, whatever its
    casing; entries without a name are skipped."""
    seen = set()
    exact = None
    pool = []
    for tag in pool_hits:
        name = _tag_name(tag)
        if name is None or name.lower() in seen:
            continue
        seen.add(name.lower())
        pool.append(dict(tag, people=history_totals(tag.get('history'))[1]))
    server = []
    for tag in server_tags:
        name = _tag_name(tag)
        if name is None or name.lower() in seen:
            continue
        seen.add(name.lower())
        server.append(dict(tag, source=None,
                           people=history_totals(tag.get('history'))[1]))
    pool.sort(key=lambda tag: _SOURCE_ORDER.get(tag.get('source'), 3))
    # Both sorts are stable: the order the entries came in breaks ties.
    server.sort(key=lambda tag: (not tag.get('following'), -tag['people']))
    ranked = pool + server
    typed = typed.lower()
    for index, tag in enumerate(ranked):
        if tag['name'].lower() == typed:
            exact = ranked.pop(index)
            break
    if exact is not None:
        ranked.insert(0, exact)
    return ranked[:MAX_SUGGESTIONS]


# The Pool

# How long the tags the pool fetched are used before it asks again, and
# how long when every one of the three requests failed.
POOL_TTL_SECONDS = 600
POOL_RETRY_SECONDS = 60

# Where each source comes from, relative to /api/v1. A server without one
# of them answers an error, which counts as an empty list.
_POOL_PATHS = (('recent', '/featured_tags/suggestions'),
               ('featured', '/featured_tags'),
               ('followed', '/followed_tags'))

# {client: TagPool}. A client is one account on one server, so its pool
# is the account's, shared by every composer of that window, and goes
# with the client when the account is removed. Plain dicts only: nothing
# in here may keep a widget alive.
_POOLS = weakref.WeakKeyDictionary()

# The clock, so a test can move it.
_now = time.monotonic


class TagPool:
    """The tags an account is likely to type again: the ones it used
    lately, the ones on its profile and the ones it follows."""

    def __init__(self):
        self.tags = {source: [] for source, _path in _POOL_PATHS}
        # Tags remembered from posts published here, kept over a reload.
        self.remembered = []
        self.loaded_at = None
        # Whether any of the three requests got an answer.
        self.answered = False
        self._waiting = None

    @property
    def loaded(self):
        return self.loaded_at is not None

    @property
    def fresh(self):
        if not self.loaded:
            return False
        ttl = POOL_TTL_SECONDS if self.answered else POOL_RETRY_SECONDS
        return _now() - self.loaded_at < ttl

    def ensure(self, client, callback=None):
        """Load the three lists unless they are fresh. *callback* is
        called once, with no arguments, when the load that was running or
        has been started ends; the same callback is kept only once."""
        if self.fresh:
            return
        if self._waiting is not None:
            if callback is not None and callback not in self._waiting:
                self._waiting.append(callback)
            return
        self._waiting = [] if callback is None else [callback]
        answers = {}
        failed = []
        for source, path in _POOL_PATHS:
            client.request_async(
                'GET', path, params={'limit': 200} if source == 'followed'
                else None,
                callback=lambda c, r, _d=None, _s=source:
                self._on_answer(c, r, _s, answers, failed))

    def _on_answer(self, client, result, source, answers, failed):
        try:
            found = client.request_finish(result)
        except GLib.Error:
            found = None
        if not isinstance(found, list):
            failed.append(source)
            found = []
        answers[source] = [dict(tag, source=source) for tag in found
                           if _tag_name(tag) is not None]
        if len(answers) < len(_POOL_PATHS):
            return
        self.tags = answers
        self.loaded_at = _now()
        self.answered = len(failed) < len(_POOL_PATHS)
        self.remember(self.remembered)
        waiting, self._waiting = self._waiting, None
        for callback in waiting:
            callback()

    def remember(self, tags):
        """Put *tags* (a status's ``tags``) first among the recent ones."""
        known = {tag['name'].lower() for tag in self.tags['recent']}
        fresh = []
        for tag in tags or []:
            name = _tag_name(tag)
            if name is None or name.lower() in known:
                continue
            known.add(name.lower())
            fresh.append({'name': name, 'source': 'recent'})
        self.tags['recent'] = fresh + self.tags['recent']
        self.remembered = fresh + [tag for tag in self.remembered
                                   if tag not in fresh]

    def hits(self, query):
        """The tags whose name begins with *query*, every source."""
        query = query.lower()
        return [tag for source, _path in _POOL_PATHS
                for tag in self.tags[source]
                if tag['name'].lower().startswith(query)]


def pool_of(client):
    """The pool of *client*'s account, loaded or not; None before any."""
    return _POOLS.get(client)


def remember(client, tags):
    """Add the hashtags of a status just published to the account's
    pool, so they are suggested at once."""
    if not isinstance(tags, list):
        return
    _POOLS.setdefault(client, TagPool()).remember(tags)


def forget_pool(client):
    """Drop the account's pool: the next ``#`` asks again."""
    _POOLS.pop(client, None)


# The Completer

# Answers kept per completer, so backspacing does not ask again.
CACHE_SIZE = 32
# Tags asked of the server: more than are shown, as the server's own order
# is by length when it has no search index, and rank_tags sorts them.
SEARCH_LIMIT = 10


def caption_for(tag):
    """The line under a suggestion's name: where the pool found it, or how
    many people used it lately; '' when there is nothing to say."""
    source = tag.get('source')
    if source == 'recent':
        # Translators: second line of a hashtag suggestion in the composer,
        # under the hashtag: you have used it in your posts recently.
        return _('Recently used')
    if source == 'featured':
        # Translators: second line of a hashtag suggestion in the composer,
        # under the hashtag: it is one of the hashtags on your profile.
        return _('On your profile')
    if source == 'followed':
        # Translators: second line of a hashtag suggestion in the composer,
        # under the hashtag: you follow it.
        return _('Followed')
    people = tag.get('people')
    if people is None:
        people = history_totals(tag.get('history'))[1]
    if people <= 0:
        return ''
    # Translators: second line of a hashtag suggestion in the composer,
    # under the hashtag: how many people posted with it in the last two
    # days. "2 days" is fixed: keep it as the number 2 in the sentence.
    return ngettext('%d person, last 2 days', '%d people, last 2 days',
                    people) % people


class HashtagCompleter(TokenCompleter):
    """Suggestion popover for the ``#hashtag`` being typed in *text_view*:
    the instance's hashtag search, ranked by :func:`rank_tags`."""

    __gtype_name__ = 'BabbleHashtagCompleter'

    def __init__(self, client, text_view, **kwargs):
        super().__init__(client, text_view, **kwargs)
        # {query: tags} as the server answered, oldest first.
        self._cache = OrderedDict()
        # One callback for the pool to keep, however many times the user
        # types while it loads.
        self._pool_waiter = pages.weak_method(self._on_pool_ready)

    @property
    def list_label(self):
        return _('Suggested Hashtags')

    # Hooks

    def token_query(self, text):
        return hashtag_query(text)

    def continues_token(self, char):
        return is_name_char(char)

    def wants_search(self, query):
        return wants_search(query)

    def search_request(self, query):
        # No exclude_unreviewed: it leaves only the exact name on an
        # instance whose admin has reviewed no tags.
        return '/api/v2/search', {'type': 'hashtags', 'q': query,
                                  'limit': SEARCH_LIMIT, 'resolve': 'false'}

    def parse(self, result):
        tags = result.get('hashtags') if isinstance(result, dict) else None
        return [tag for tag in tags or [] if _tag_name(tag) is not None]

    completes_bare = True

    def _pool_hits(self, query):
        pool = pool_of(self.client)
        return pool.hits(query) if pool is not None else []

    def prepare(self, query):
        pool = _POOLS.setdefault(self.client, TagPool())
        pool.ensure(self.client, self._pool_waiter)

    def _on_pool_ready(self):
        token = self._token
        if token is None:
            return
        if not token[1]:
            # A bare "#" the user paused on while the pool was loading;
            # while the pause is still running it will read the pool.
            if not self._search_id and self.text_view.is_focus() \
                    and not self._preedit:
                items = self.bare_items()
                if items:
                    self._fill(items)
            return
        if self._search_id or self._cancellable is not None:
            # The search has not answered: show what the pool knows.
            items = self.instant_items(token[1])
            if items:
                self._fill(items)
        else:
            self._show_answer(self._cache.get(token[1].lower()))

    def instant_items(self, query):
        return rank_tags(query, self._pool_hits(query), [])

    def bare_items(self):
        # Only a pool that is loaded; one still loading answers through
        # _on_pool_ready, if the token is still the bare "#".
        pool = pool_of(self.client)
        if pool is None or not pool.loaded:
            return []
        return rank_tags('', pool.tags['recent'] + pool.tags['featured'], [])

    def cached_items(self, query):
        return self._cache.get(query.lower())

    def merge_results(self, query, items):
        if items is not None:
            self._cache[query.lower()] = items
            self._cache.move_to_end(query.lower())
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return rank_tags(query, self._pool_hits(query), items or [])

    def default_selection(self, items, query):
        # A tag already typed in full is not selected: Enter after
        # "#art" is a line break, and Down then Enter still picks it.
        # Nor is any row for a bare "#" the user paused on.
        if not query or (items and items[0]['name'].lower() == query.lower()):
            return None
        return 0

    def item_key(self, tag):
        return tag['name'].lower()

    def completion(self, tag, typed):
        token = self._current_token()
        trigger = '#'
        if token is not None:
            found = self.buffer.get_iter_at_offset(token[0]).get_char()
            if found in _TRIGGERS:
                trigger = found
        return trigger + merge_casing(typed, tag['name'])

    def build_row(self, tag):
        name = tag['name']
        # The same glyph as the hashtag page's badge, without importing a
        # page module into the composer.
        badge = Gtk.Label(label='#', width_request=32, height_request=32,
                          accessible_role=Gtk.AccessibleRole.PRESENTATION)
        badge.add_css_class('title-3')
        badge.add_css_class('dimmed')
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                         valign=Gtk.Align.CENTER)
        title = Gtk.Label(label='#' + name, xalign=0, max_width_chars=20,
                          ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class('heading')
        labels.append(title)
        caption = caption_for(tag)
        if caption:
            line = Gtk.Label(label=caption, xalign=0, max_width_chars=20,
                             ellipsize=Pango.EllipsizeMode.END)
            line.add_css_class('dimmed')
            line.add_css_class('caption')
            labels.append(line)
        box = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4)
        box.append(badge)
        box.append(labels)
        row = Gtk.ListBoxRow(child=box, can_focus=False)
        row.tag_name = name
        row.accessible_text = '#%s, %s' % (name, caption) if caption \
            else '#' + name
        row.update_property([Gtk.AccessibleProperty.LABEL],
                            [row.accessible_text])
        return row
