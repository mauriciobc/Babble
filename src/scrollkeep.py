# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep a scrolled list where it was while another page is pushed above it.

A page under the top of a navigation stack is given no size, and its scrolled
window clamps its position to the top. ``watch`` marks the scrolled window of a
page; ``remember`` reads its position before the push; the page puts it
back when it is mapped again.
"""

import gi

gi.require_version('GLib', '2.0')

from gi.repository import GLib  # noqa: E402


def watch(page, scrolled):
    """Restore *scrolled*'s position whenever *page* is mapped again after
    ``remember(page)`` (held weakly: the page owns the scrolled window)."""
    page._scrolled_ref = scrolled.weak_ref()
    page._scroll_saved = None
    page.connect('map', _on_map)


def remember(page):
    """The position of the page's scrolled window, to come back to."""
    scrolled = page._scrolled_ref()
    if scrolled is not None:
        page._scroll_saved = scrolled.get_vadjustment().get_value()


def _on_map(page):
    if page._scroll_saved is None:
        return

    def restore():
        scrolled = page._scrolled_ref()
        saved, page._scroll_saved = page._scroll_saved, None
        if scrolled is not None and saved is not None:
            scrolled.get_vadjustment().set_value(saved)
        return GLib.SOURCE_REMOVE

    GLib.idle_add(restore)
