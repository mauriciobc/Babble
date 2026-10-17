# SPDX-License-Identifier: GPL-3.0-or-later

"""The poll widget: the vote form, the result bars and the refresh.

Split out of ``mediabox`` (which owns the attachment surface) so the two
stay under the module size the tree keeps: a poll is a status body
widget, not an attachment one.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import pages  # noqa: E402
from .pages import connect_weak  # noqa: E402
from .textemoji import InlineEmojiText, poll_emoji_bank  # noqa: E402
from .timelinebase import time_left  # noqa: E402


class PollBox(Gtk.Box):
    """Renders status.poll. An open poll the reader has not answered is
    a vote form (radio buttons, or check buttons for a multiple-choice
    poll, plus Vote); an expired or answered one shows the result bars
    with the reader's own choices marked. ``set_poll`` takes the client
    and the post's view state ``ui`` so a vote or a refresh can update
    the post: ``on_update(new_poll)`` stores the server's answer."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class('status-poll')
        self._poll = None
        self._bank = {}
        self._client = None
        self._ui = {}
        self._on_update = None
        self._choices = []
        self._animate_bars = False
        self._bar_animations = []

    def set_poll(self, poll, bank=None, client=None, ui=None,
                 on_update=None):
        pages.clear_children(self)
        self._bar_animations = []
        self._poll = poll
        if not poll:
            self._animate_bars = False
            self.set_visible(False)
            return
        self.set_visible(True)
        # A poll declares its own emoji for the option titles; they win
        # over the post's bank, which only covers the body.
        self._bank = dict(bank or {})
        self._bank.update(poll_emoji_bank(poll))
        self._client = client
        self._ui = ui if ui is not None else {}
        self._on_update = on_update
        self._choices = []
        closed = bool(poll.get('expired') or poll.get('closed'))
        can_vote = (client is not None and bool(getattr(client, 'token', None))
                    and not closed and not poll.get('voted')
                    and not self._ui.get('poll_results'))
        if can_vote:
            self._build_form(poll)
        else:
            self._build_results(poll, closed)
        self._build_footer(poll, closed, can_vote)
        for animation in self._bar_animations:
            animation.play()
        self._animate_bars = False

    def _label(self, option):
        text = InlineEmojiText(emoji_size=15)
        text.set_segments([{'text': option.get('title') or '', 'href': None}],
                          self._bank)
        text.set_hexpand(True)
        return text

    def _build_form(self, poll):
        multiple = bool(poll.get('multiple'))
        first = None
        for index, option in enumerate(poll.get('options') or []):
            button = Gtk.CheckButton()
            if not multiple:
                if first is None:
                    first = button
                else:
                    button.set_group(first)
            button.set_child(self._label(option))
            connect_weak(button, 'toggled', self._on_choice_toggled)
            button.update_property([Gtk.AccessibleProperty.LABEL],
                                   [option.get('title') or ''])
            button.babble_index = index
            self._choices.append(button)
            self.append(button)

    def _selected(self):
        return [b.babble_index for b in self._choices if b.get_active()]

    def _on_choice_toggled(self, _button):
        self.vote_button.set_sensitive(bool(self._selected()))

    def _build_results(self, poll, closed):
        options = poll.get('options') or []
        own = set(poll.get('own_votes') or [])
        total = poll.get('votes_count') or 0
        counts = [(o.get('votes_count') or 0) for o in options]
        winner = counts.index(max(counts)) \
            if closed and counts and max(counts) > 0 else -1
        for index, option in enumerate(options):
            count = option.get('votes_count') or 0
            share = (count / total) if total else 0.0
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            line = Gtk.Box(spacing=8)
            if index in own:
                mark = Gtk.Image(icon_name='object-select-symbolic',
                                 accessible_role=Gtk.AccessibleRole.IMG)
                pages.set_accessible_label(mark, _('Your vote'))
                line.append(mark)
            label = self._label(option)
            if index == winner:
                label.add_css_class('heading')
            line.append(label)
            percent = Gtk.Label(label='%d%%' % round(share * 100))
            percent.add_css_class('numeric')
            percent.add_css_class('caption')
            line.append(percent)
            row.append(line)
            bar = Gtk.ProgressBar(
                fraction=0.0 if self._animate_bars else share)
            if self._animate_bars:
                self._bar_animations.append(Adw.TimedAnimation(
                    widget=bar, value_from=0.0, value_to=share,
                    duration=400, easing=Adw.Easing.EASE_OUT_CUBIC,
                    target=Adw.PropertyAnimationTarget.new(bar, 'fraction')))
            bar.add_css_class('poll-bar')
            if index in own:
                bar.add_css_class('accent')
            bar.update_property(
                [Gtk.AccessibleProperty.LABEL],
                ['%s, %d%%' % (option.get('title') or '', round(share * 100))])
            row.append(bar)
            self.append(row)

    def _build_footer(self, poll, closed, can_vote):
        footer = Gtk.Box(spacing=8)
        if can_vote:
            self.vote_button = Gtk.Button(label=_('_Vote'),
                                          use_underline=True)
            self.vote_button.add_css_class('suggested-action')
            self.vote_button.add_css_class('pill')
            self.vote_button.set_sensitive(False)
            connect_weak(self.vote_button, 'clicked', self._on_vote)
            footer.append(self.vote_button)
            show = Gtk.Button(label=_('Show _Results'), use_underline=True)
            show.add_css_class('flat')
            connect_weak(show, 'clicked', self._on_show_results)
            footer.append(show)
        elif not closed and not poll.get('voted') and self._ui.get(
                'poll_results'):
            back = Gtk.Button(label=_('_Back to Voting'), use_underline=True)
            back.add_css_class('flat')
            connect_weak(back, 'clicked', self._on_show_results)
            footer.append(back)
        votes = poll.get('votes_count') or 0
        voters = poll.get('voters_count')
        count = voters if voters is not None else votes
        detail = Gtk.Label(xalign=0.0, hexpand=True, wrap=True)
        detail.add_css_class('caption')
        detail.add_css_class('dimmed')
        if voters is not None:
            parts = [ngettext('%d voter', '%d voters', count) % count]
        else:
            parts = [ngettext('%d vote', '%d votes', votes) % votes]
        if closed:
            parts.append(_('Closed'))
        else:
            left = time_left(poll.get('expires_at'))
            parts.append(_('%s left') % left if left else _('Open'))
        detail.set_text(' · '.join(parts))
        footer.append(detail)
        if not closed:
            refresh = pages.icon_button('view-refresh-symbolic',
                                        _('Refresh Results'))
            connect_weak(refresh, 'clicked', self._on_refresh)
            footer.append(refresh)
        self.append(footer)

    def _on_show_results(self, _button):
        self._ui['poll_results'] = not self._ui.get('poll_results')
        self._animate_bars = True
        self.set_poll(self._poll, self._bank, self._client, self._ui,
                      self._on_update)

    def _apply(self, poll):
        if self._on_update is not None:
            self._on_update(poll)
        self._animate_bars = True
        self.set_poll(poll, self._bank, self._client, self._ui,
                      self._on_update)

    def _on_vote(self, _button):
        choices = self._selected()
        if not choices or self._poll is None:
            return
        self.vote_button.set_sensitive(False)
        pages.call(self, self._client, 'POST',
                   '/polls/%s/votes' % self._poll['id'],
                   on_done=self._apply, error_heading=_('Vote failed'),
                   json_body={'choices': choices})
        # A failed vote leaves the form usable again.
        GLib.timeout_add_seconds(
            3, lambda: self.vote_button.set_sensitive(
                bool(self._selected())) or GLib.SOURCE_REMOVE)

    def _on_refresh(self, _button):
        if self._poll is None:
            return
        pages.call(self, self._client, 'GET', '/polls/%s' % self._poll['id'],
                   on_done=self._apply, error_heading=_('Refresh failed'))
