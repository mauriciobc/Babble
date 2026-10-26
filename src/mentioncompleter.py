# SPDX-License-Identifier: GPL-3.0-or-later
"""Account suggestions for the ``@mention`` being typed in a text view.

:class:`MentionCompleter` is a :class:`~.completer.TokenCompleter`: while
the cursor ends an ``@word``, it searches the instance's accounts and
lists them in a popover under the mention."""

import re

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _

from gi.repository import Adw, Gtk, Pango  # noqa: E402

from . import pages  # noqa: E402
from .completer import (MAX_SUGGESTIONS, SEARCH_DELAY_MS,  # noqa: E402, F401
                        TokenCompleter)

# The mention being typed, ending at the cursor: an @ not glued to a
# word, a path or another @ (Mastodon's own mention rule), then the
# name typed so far, with the domain of a remote account if any.
_MENTION_RE = re.compile(r'(?<![\w/@])@([\w.-]*(?:@[\w.-]*)?)\Z')

# Characters a mention token continues with, past the cursor.
_TOKEN_CHARS = re.compile(r'[\w.@-]')


def mention_query(text):
    """``(start, query)`` for the mention *text* (the text before the
    cursor) ends with, *start* being the offset of its ``@``; None when
    the cursor is not at the end of a mention."""
    match = _MENTION_RE.search(text)
    if match is None:
        return None
    return match.start(), match.group(1)


def _wants_resolve(query):
    """A full ``user@domain`` is worth a WebFinger lookup, which finds
    accounts the instance has never seen; anything shorter is not."""
    _user, _at, domain = query.partition('@')
    return '.' in domain.strip('.')


class MentionCompleter(TokenCompleter):
    """Suggestion popover for *text_view*, parented to it. Call
    :meth:`prompt` after inserting a bare ``@`` to show a hint before
    anything is typed, :meth:`dismiss` to close it for good."""

    __gtype_name__ = 'BabbleMentionCompleter'

    @property
    def list_label(self):
        return _('Suggested Accounts')

    @property
    def hint_text(self):
        return _('Type a name to search')

    def prompt(self):
        """Show the hint for an ``@`` just inserted at the cursor."""
        self._hint = True
        self._suppressed = None
        # The insertion already looked at this token, hint off.
        self._token = None
        self.update()

    # Hooks

    def token_query(self, text):
        return mention_query(text)

    def continues_token(self, char):
        return _TOKEN_CHARS.match(char) is not None

    def search_request(self, query):
        params = {'q': query, 'limit': MAX_SUGGESTIONS}
        if _wants_resolve(query):
            params['resolve'] = 'true'
        return '/accounts/search', params

    def parse(self, result):
        return [account for account in (result or [])[:MAX_SUGGESTIONS]
                if account.get('acct') or account.get('username')]

    def build_row(self, account):
        from .mediacache import AvatarCache
        acct = account.get('acct') or account.get('username')
        name = re.sub(r':[A-Za-z0-9_+\-]+:', '',
                      pages.display_name(account)).strip() or acct
        avatar = Adw.Avatar(size=32, text=name, show_initials=True)
        AvatarCache.load(avatar, account)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                         valign=Gtk.Align.CENTER)
        labels.append(Gtk.Label(label=name, xalign=0, max_width_chars=28,
                                ellipsize=Pango.EllipsizeMode.END))
        handle = Gtk.Label(label='@' + acct, xalign=0, max_width_chars=28,
                           ellipsize=Pango.EllipsizeMode.END)
        handle.add_css_class('dimmed')
        handle.add_css_class('caption')
        labels.append(handle)
        box = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4)
        box.append(avatar)
        box.append(labels)
        row = Gtk.ListBoxRow(child=box, can_focus=False)
        row.update_property([Gtk.AccessibleProperty.LABEL],
                            ['%s, @%s' % (name, acct)])
        row.acct = acct
        return row

    def completion(self, account, typed):
        return '@' + (account.get('acct') or account.get('username'))

    def item_key(self, account):
        return account.get('acct') or account.get('username')
