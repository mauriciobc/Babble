# SPDX-License-Identifier: GPL-3.0-or-later
"""Reopening the Home timeline where the reader stopped.

The position is the id of the post at the top of the screen, kept per
account in GSettings (``home-reading-position``, account key to id). On the
next launch the first page is asked for at that id with ``max_id``, which
puts the remembered post (or, when it was deleted, the nearest older one)
at the top of a normal page: no scrolling to a row that is not built yet,
and ``oldest_id``/``newest_id`` stay what they always are. What is newer
is one button away, and it is the ordinary refresh that fetches it.

Only the local copy is used. The server's ``home`` marker would give a
second device the same place, but reading it costs a round trip before the
first post can be asked for, and writing it moves the place other clients
resume from.
"""

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import GLib  # noqa: E402

from .i18n import gettext as _  # noqa: E402
from . import pages  # noqa: E402

SAVE_DELAY_MS = 2000
SETTING = 'home-reading-position'
ENABLED = 'resume-reading-position'


def get_position(settings, account_key):
    """The remembered post id for *account_key*, or None."""
    if not account_key:
        return None
    return settings.get_value(SETTING).unpack().get(account_key) or None


def set_position(settings, account_key, status_id):
    """Remember *status_id* for *account_key*; None forgets it."""
    if not account_key:
        return
    positions = dict(settings.get_value(SETTING).unpack())
    if status_id:
        if positions.get(account_key) == status_id:
            return
        positions[account_key] = str(status_id)
    elif positions.pop(account_key, None) is None:
        return
    settings.set_value(SETTING, GLib.Variant('a{ss}', positions))


def anchor_params(status_id):
    """``max_id`` that returns *status_id* itself first, or None when the
    id is not a number (a server whose ids are not snowflakes)."""
    try:
        return str(int(status_id) + 1)
    except (TypeError, ValueError):
        return None


def _save_tick(page_ref):
    page = page_ref()
    if page is None:
        return GLib.SOURCE_REMOVE
    page._save_source = 0
    page.save_reading_position()
    return GLib.SOURCE_REMOVE


class ReadingPosition:
    """Home page mixin. Needs ``list_view``, ``_scrolled``, ``model``,
    ``new_pill``, ``new_posts_revealer`` and ``_pill_count``."""

    def _resume_init(self, home):
        settings = pages.settings()
        self._resume_key = None
        self._resume_anchor = None
        self._resume_pending = False
        self._resumed = False
        self._save_source = 0
        if home and settings.get_boolean(ENABLED):
            self._resume_key = settings.get_string('active-account')
            anchor = get_position(settings, self._resume_key)
            if anchor and anchor_params(anchor):
                self._resume_anchor = anchor
        pages.connect_weak(self, 'unmap', self._on_resume_unmap)

    def _first_page_params(self):
        params = super()._first_page_params()
        if self._resume_anchor:
            params['max_id'] = anchor_params(self._resume_anchor)
            self._resume_pending = True
        return params

    def _resume_page_arrived(self, statuses):
        """First page back. Returns True when it was empty and the page was
        asked for again from the top, so the caller stops."""
        if not self._resume_pending:
            return False
        self._resume_pending = False
        if not statuses:
            self._resume_anchor = None
            set_position(pages.settings(), self._resume_key, None)
            self.model.loading = False
            self.load()
            return True
        self._resumed = True
        self._resume_anchor = None
        self.new_pill.set_label(_('Newer Posts'))
        self.new_posts_revealer.set_reveal_child(True)
        return False

    def resume_pill_clicked(self):
        """The pill in its resumed state asks for what is newer and goes
        there. Returns True when it handled the click."""
        if not self._resumed:
            return False
        self._resumed = False
        self.new_posts_revealer.set_reveal_child(False)
        self.refresh()
        self.scroll_to_top()
        return True

    # Saving

    def top_visible_status_id(self):
        """Timeline id of the first post that is at least partly on
        screen, or None. This is the id the server pages by (a boost's own
        id, not the id of the post it shares), so ``max_id`` can ask for
        it again. The list view's children are its row shells, in no order,
        and their bounds are relative to the visible area: the first row
        is the one still below the top edge with the smallest y."""
        height = self._scrolled.get_height()
        best = None
        child = self.list_view.get_first_child()
        while child is not None:
            shell = child.get_first_child()
            item = getattr(shell, 'item', None)
            raw = getattr(item, 'raw', None)
            ok, bounds = child.compute_bounds(self.list_view)
            if ok and raw and raw.get('id') and child.get_mapped() \
                    and bounds.get_y() + bounds.get_height() > 1 \
                    and bounds.get_y() < height \
                    and (best is None or bounds.get_y() < best[0]):
                best = (bounds.get_y(), str(raw['id']))
            child = child.get_next_sibling()
        return best[1] if best else None

    def schedule_reading_position_save(self):
        """Save after the reader pauses, not on every scroll step."""
        if self._resume_key is None or self._resume_pending:
            return
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(
            SAVE_DELAY_MS, _save_tick, self.weak_ref())

    def save_reading_position(self):
        if self._resume_key is None or self._resume_pending:
            return
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        at_top = self._scrolled.get_vadjustment().get_value() <= 0.0
        if at_top and not self._resumed:
            # Reading from the newest post: the next launch starts there
            # too, with nothing to resume.
            set_position(pages.settings(), self._resume_key, None)
            return
        status_id = self.top_visible_status_id()
        if status_id:
            set_position(pages.settings(), self._resume_key, status_id)

    def _on_resume_unmap(self, _widget):
        self.save_reading_position()
