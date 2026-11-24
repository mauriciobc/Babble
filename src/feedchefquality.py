# SPDX-License-Identifier: GPL-3.0-or-later
"""The quality layer's plumbing for the Feed Chef screens: the worker the
analysis runs on, the cache of each recipe's health, the health badge and
the recipe list's use of both.

Nothing here computes anything on the main thread. A job is a function the
worker runs; its answer comes back through ``GLib.idle_add`` to a callback
that holds its owner weakly, so a page or a dialog that went away never gets
it, and a job that was superseded is never delivered. The worker starts the
first time a job is submitted (a recipe that never reaches the quality layer
never starts it), runs at a low priority and is one thread, so what it
persists is written in the order it was given.
"""

import logging
import os
import queue
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import accounts  # noqa: E402
from .appsettings import settings as app_settings  # noqa: E402
from .feedchef import health as healthmod  # noqa: E402
from .feedchef import history  # noqa: E402
from .feedchefcontroller import PageController, guarded  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .weakconnect import weak_method  # noqa: E402

log = logging.getLogger('babble.feedchef')

# The health of each recipe the session has computed, by (account, id), and
# the recipes whose alert was already shown this session.
HEALTH = {}
ALERTED = set()

# How much nicer than the UI thread the worker runs (Linux: per thread).
NICE = 10


def health_key(account, recipe_id):
    return (accounts.account_key(account), recipe_id)


def feed_chef_on():
    """Whether Feed Chef is on: an answer that lands after it was turned
    off is dropped, not cached."""
    return app_settings().get_boolean('feed-chef-enabled')


def forget_session():
    """Feed Chef was turned off: the cache and the alerts of the session
    go, so turning it on again starts from what the logs say."""
    HEALTH.clear()
    ALERTED.clear()


class Job:
    """One piece of work: ``fn()`` on the worker, then ``deliver(result)``
    on the main loop. ``cancel()`` stops it from running, if it has not
    started, and from being delivered."""

    __slots__ = ('fn', 'deliver', 'cancelled')

    def __init__(self, fn, deliver):
        self.fn = fn
        self.deliver = deliver
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def _finish(self, result):
        if not self.cancelled and self.deliver is not None:
            self.deliver(result)
        return GLib.SOURCE_REMOVE


class Worker:
    def __init__(self):
        self._queue = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()

    def submit(self, fn, deliver=None):
        """Run *fn* on the worker; the result (None when it raised) goes to
        *deliver* on the main loop. Returns the job."""
        job = Job(fn, deliver)
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name='feedchef-quality', daemon=True)
                self._thread.start()
        self._queue.put(job)
        return job

    def _run(self):
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), NICE)
        except (AttributeError, OSError):
            pass
        while True:
            job = self._queue.get()
            if job.cancelled:
                continue
            try:
                result = job.fn()
            except Exception:
                log.exception('feedchef: a quality job failed')
                result = None
            if job.deliver is not None and not job.cancelled:
                GLib.idle_add(job._finish, result)

    def idle(self):
        """Whether nothing is queued (tests)."""
        return self._queue.empty()


_worker = None


def worker():
    """The quality worker, made on first use."""
    global _worker
    if _worker is None:
        _worker = Worker()
    return _worker


# The badge ------------------------------------------------------------

ICONS = {healthmod.WORKING: 'object-select-symbolic',
         healthmod.ATTENTION: 'dialog-warning-symbolic'}


class HealthBadge(Gtk.Box):
    """"Working Well" or "Needs Attention", as an icon and words (never a
    colour alone). Hidden until a health is known."""

    __gtype_name__ = 'BabbleFeedChefHealthBadge'

    def __init__(self):
        # An image with a name, so a screen reader reads the state even
        # when only the icon shows.
        super().__init__(spacing=4, valign=Gtk.Align.CENTER, visible=False,
                         accessible_role=Gtk.AccessibleRole.IMG)
        self.compact = False
        self.text = ''
        self.icon = Gtk.Image(
            accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.label = Gtk.Label()
        self.label.add_css_class('caption')
        self.append(self.icon)
        self.append(self.label)
        self.state = healthmod.UNKNOWN

    def show_health(self, found, words):
        """Show *found* (a health.Health, or None for unknown)."""
        state = healthmod.UNKNOWN if found is None else found.state
        self.state = state
        self.set_visible(state in ICONS)
        if state not in ICONS:
            return
        text = healthmod.state_text(state, words)
        self.text = text
        self.icon.set_from_icon_name(ICONS[state])
        self.label.set_label(text)
        self.label.set_visible(not self.compact)
        for name in ('success', 'warning'):
            self.remove_css_class(name)
        self.add_css_class('warning' if state == healthmod.ATTENTION
                           else 'success')
        why = '; '.join(healthmod.reason_text(r, words)
                        for r in found.reasons)
        # Icon only: the tooltip carries the words as well.
        self.set_tooltip_text('%s: %s' % (text, why) if why else text)
        # Translators: the accessible description of a recipe's health
        # badge; %s is "Working Well" or "Needs Attention".
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [_('Recipe health: %s') % text])


    def set_compact(self, compact):
        """Icon only (a narrow list); the accessible name stays."""
        self.compact = compact
        self.label.set_visible(not compact and self.state in ICONS)


class ListHealth(PageController):
    """The recipe list's badges: shown from the cache at once, and
    computed on the worker for the recipes the cache has not seen. A cook on
    a recipe's page updates the cache, and the list shows it when it is next
    shown. A controller of the list page: it reads the page's ``account``
    and ``compact``."""

    def __init__(self, page):
        super().__init__(page)
        self.badges = {}
        self.slots = {}
        self._health_job = None
        self._health_words = None

    def reset(self):
        """The list was rebuilt: its rows, and so its badges, are new."""
        self.badges = {}
        self.slots = {}

    @guarded
    def health_badge(self, recipe, words):
        """The slot a recipe's row keeps for its badge. The badge itself is
        made when there is a health to show, so a long list costs one
        empty widget a row until then."""
        slot = Adw.Bin(valign=Gtk.Align.CENTER)
        self.slots[recipe.id] = slot
        self._show_badge(recipe.id, HEALTH.get(
            health_key(self.owner.account, recipe.id)), words)
        return slot

    @guarded
    def _show_badge(self, recipe_id, found, words):
        badge = self.badges.get(recipe_id)
        if badge is None:
            slot = self.slots.get(recipe_id)
            if slot is None or found is None \
                    or found.state not in ICONS:
                return
            badge = self.badges[recipe_id] = HealthBadge()
            badge.set_compact(bool(getattr(self.owner, 'compact', False)))
            slot.set_child(badge)
        badge.show_health(found, words)

    @guarded
    def refresh_health(self, words, recipe_ids):
        """Compute the health of the recipes the cache lacks."""
        account = self.owner.account
        missing = [rid for rid in recipe_ids
                   if health_key(account, rid) not in HEALTH]
        if self._health_job is not None:
            self._health_job.cancel()
        self._health_job = None
        if missing:
            self._health_words = words
            self._health_job = worker().submit(
                lambda: history.health_map(missing),
                weak_method(self._on_health))

    @guarded
    def apply_health(self, words):
        """Show the cache on every badge (the list was shown again)."""
        account = self.owner.account
        for recipe_id in list(self.slots):
            self._show_badge(recipe_id, HEALTH.get(
                health_key(account, recipe_id)), words)

    @guarded
    def _on_health(self, found):
        page = self.owner
        self._health_job = None
        if not feed_chef_on():
            return
        for recipe_id, state in (found or {}).items():
            HEALTH[health_key(page.account, recipe_id)] = state
        self.apply_health(self._health_words)

    def set_compact(self, compact):
        for badge in self.badges.values():
            badge.set_compact(compact)
