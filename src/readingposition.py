# SPDX-License-Identifier: GPL-3.0-or-later
"""Reopening the Home timeline where the reader stopped.

The position is the id of the post at the top of the screen, kept per
account in GSettings (``home-reading-position``, account key to id). On the
next launch the first page is asked for at that id with ``max_id``, which
puts the remembered post (or, when it was deleted, the nearest older one)
at the top of a normal page: no scrolling to a row that is not built yet,
and ``oldest_id``/``newest_id`` stay what they always are. What is newer
is fetched at once by the ordinary refresh and inserted above, while the
remembered post is held where it is on screen; the New Posts button goes
back up to them.

Only the local copy is used. The server's ``home`` marker would give a
second device the same place, but reading it costs a round trip before the
first post can be asked for, and writing it moves the place other clients
resume from.
"""

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import GLib, Gtk  # noqa: E402

from . import pages  # noqa: E402
from .timelinedata import BoostCarouselItem  # noqa: E402

SAVE_DELAY_MS = 2000
# Most frames a pinned post is nudged back into place while rows settle.
PIN_FRAMES = 30
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


def _pin_tick(page, _clock):
    return page._align_pin()


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
        self._catching = False
        self._pin_item = None
        self._pin_y = 0.0
        self._pin_ticks = 0
        self._pin_calm = 0
        self._pin_tick_id = 0
        self._save_source = 0
        self._last_top = None
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
        return False

    # Catching up above the remembered post

    def _resume_start_catchup(self):
        """The anchored page is in: fetch what is newer, with the post at
        its top held in place as those rows go in above it. Called from
        every path a first page can land by, so a page the filters emptied
        (and refilled by paging) starts it too."""
        if not self._resumed or self._catching:
            return
        if self.model.get_n_items() == 0:
            self._resumed = False
            return
        self._catching = True
        self._pin_item = self.model.get_item(0)
        self._pin_y = 0.0
        self.refresh()

    def _pin_prepare(self):
        """Before rows go in above: remember the post at the top of the
        screen and where it is, which is the post resumed on the first
        page and wherever the reader has scrolled to after that."""
        found = self._top_item()
        if found is not None:
            self._pin_item, self._pin_y = found

    def _chain_ended(self, sweep=True):
        """The refresh chain stopped, by a short page, the cap, a failure
        or because an older page took its request's place. Pinning ends,
        and one more refresh sweeps up whatever the catch-up missed: posts
        streamed meanwhile were held back, and the cursor continues
        contiguously from where the chain stopped. Not after a failure,
        which would only ask again at once."""
        self._refresh_chain = 0
        if self._catching:
            self._end_catchup()
            if sweep:
                self.refresh()

    def _end_catchup(self):
        """The last page's nudging still has frames to run, so the tick
        is left to finish and clear the pin itself."""
        self._catching = False
        self._resumed = False
        if not self._pin_tick_id:
            self._pin_item = None

    def _stop_pinning(self):
        self._pin_item = None
        self._pin_ticks = 0
        if self._pin_tick_id:
            self.remove_tick_callback(self._pin_tick_id)
            self._pin_tick_id = 0

    def resume_unpin(self):
        """The reader asked for the top: stop holding the post, and treat
        the rest of the catch-up as an ordinary refresh at the top."""
        self._resumed = False
        self._stop_pinning()

    def _top_item(self):
        """``(item, y)`` of the first row at least partly on screen: the
        one still below the top edge with the smallest y. Bounds are
        relative to the visible area, not to the content."""
        height = self._scrolled.get_height()
        best = None
        for y, h, run in self._row_rects():
            if y + h > 1 and y < height and (best is None or y < best[1]):
                best = (run.items[0], y)
        return best

    def _row_rects(self):
        """``(y, height, run)`` for every row drawn."""
        rects = []
        child = self.list_view.get_first_child()
        while child is not None:
            run = getattr(child.get_first_child(), '_run', None)
            ok, bounds = child.compute_bounds(self.list_view)
            if run is not None and ok and child.get_mapped():
                rects.append((bounds.get_y(), bounds.get_height(), run))
            child = child.get_next_sibling()
        return rects

    def _repin(self):
        """Rows went in above the pinned post. A list view at the top
        shows its first row, so scroll to the pinned one, then keep nudging
        it back to where it was while the rows above settle their heights."""
        if self._pin_item is None:
            return
        for position in range(self.run_model.get_n_items()):
            if self._pin_item in self.run_model.get_item(position).items:
                break
        else:
            return
        self.list_view.scroll_to(position, Gtk.ListScrollFlags.NONE, None)
        self._pin_ticks = PIN_FRAMES
        self._pin_calm = 0
        if not self._pin_tick_id:
            self._pin_tick_id = self.add_tick_callback(_pin_tick)

    def _align_pin(self):
        """One frame of nudging; stops once the post has stayed put for two
        frames, or after PIN_FRAMES."""
        if self._pin_item is None or self._pin_ticks <= 0:
            self._pin_tick_id = 0
            self._pin_item = None
            return GLib.SOURCE_REMOVE
        self._pin_ticks -= 1
        y = next((y for y, _h, run in self._row_rects()
                  if self._pin_item in run.items), None)
        if y is not None:
            if abs(y - self._pin_y) > 1:
                adjustment = self._scrolled.get_vadjustment()
                adjustment.set_value(adjustment.get_value() + y - self._pin_y)
                self._pin_calm = 0
            else:
                self._pin_calm += 1
        if self._pin_calm >= 2:
            self._pin_tick_id = 0
            self._pin_item = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # Saving

    def top_visible_status_id(self):
        """Timeline id of the first post at least partly on screen, or
        None. This is the id the server pages by (a boost's own id, not
        the id of the post it shares), so ``max_id`` can ask for it again.
        A carousel has no single id, so the post under it is the one kept."""
        found = self._top_item()
        if found is None or isinstance(found[0], BoostCarouselItem):
            return None
        raw = found[0].raw
        return str(raw['id']) if raw.get('id') else None

    def schedule_reading_position_save(self):
        """Save after the reader pauses, not on every scroll step."""
        if self._resume_key is None or self._resume_pending:
            return
        # What was last seen while scrolling: when the delay runs out the
        # window may be in the background, with no row mapped to look at.
        self._last_top = self.top_visible_status_id() or self._last_top
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
        status_id = self.top_visible_status_id() or self._last_top
        if status_id:
            set_position(pages.settings(), self._resume_key, status_id)

    def _on_resume_unmap(self, _widget):
        self.save_reading_position()
