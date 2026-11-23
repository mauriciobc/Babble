# SPDX-License-Identifier: GPL-3.0-or-later
"""Chips a page adds to the posts it shows (the Hot badge of a Feed Chef
recipe), without the post row knowing the page.

A page that wants to decorate its rows offers ``row_decorations(status)``,
answering a list of ``Decoration``; the first ancestor of a row that has it
is asked, once per row (a row never leaves its list), and the chip of each
decoration is built the first time a post wears it, like the Bluesky
indicator. ``status`` is the post as the timeline holds it (the boost
wrapper for a boost).
"""

from typing import NamedTuple

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gtk  # noqa: E402


class Decoration(NamedTuple):
    """One chip: a stable *key*, its *label*, an optional symbolic *icon*
    name, and what a screen reader adds to the label (*description*, also its
    tooltip)."""

    key: str
    label: str
    icon: str | None
    description: str


def _page_hook(row):
    """The weak reference to the page that decorates *row*, False when no
    page does, or None while the row is in no window yet (a row is bound
    before it is attached); looked up once, when it can be."""
    found = getattr(row, '_decorator', None)
    if found is None:
        if row.get_root() is None:
            return None
        widget = row.get_parent()
        while widget is not None \
                and not callable(getattr(widget, 'row_decorations', None)):
            widget = widget.get_parent()
        found = widget.weak_ref() if widget is not None else False
        row._decorator = found
    return found


def _chip(row, decoration):
    box = Gtk.Box(spacing=3, valign=Gtk.Align.CENTER)
    box.add_css_class('status-chip')
    box.add_css_class('decoration-chip')
    box.add_css_class('decoration-%s' % decoration.key)
    if decoration.icon:
        # Decorative: the label says what the chip is.
        box.append(Gtk.Image(
            icon_name=decoration.icon, pixel_size=12,
            accessible_role=Gtk.AccessibleRole.PRESENTATION))
    label = Gtk.Label(label=decoration.label)
    label.add_css_class('caption')
    box.append(label)
    box.update_property([Gtk.AccessibleProperty.LABEL],
                        [decoration.label])
    box.update_property([Gtk.AccessibleProperty.DESCRIPTION],
                        [decoration.description])
    box.set_tooltip_text(decoration.description)
    row.add_decoration_chip(box)
    return box


def _wait_for_root(row, status):
    """Decorate *row* once it is attached to a window, with the post it is
    bound to by then (the row is held weakly)."""
    row._decor_pending = status
    if getattr(row, '_decor_waiting', False):
        return
    row._decor_waiting = True
    me = row.weak_ref()
    handler = []

    def attached(widget, _pspec):
        target = me()
        if target is None or target.get_root() is None:
            return
        target._decor_waiting = False
        widget.disconnect(handler[0])
        decorate(target, target._decor_pending)

    handler.append(row.connect('notify::root', attached))


def decorate(row, status):
    """Show the chips the page offers for *status* on *row*, and hide the
    ones it does not (a recycled row wears the post it is bound to)."""
    chips = getattr(row, 'decoration_chips', None)
    ref = _page_hook(row)
    if ref is None:
        _wait_for_root(row, status)
        return
    page = ref() if ref else None
    offered = page.row_decorations(status) if page is not None else ()
    if not offered and not chips:
        return
    if chips is None:
        chips = row.decoration_chips = {}
    wanted = set()
    for decoration in offered:
        wanted.add(decoration.key)
        if decoration.key not in chips:
            chips[decoration.key] = _chip(row, decoration)
        chips[decoration.key].set_visible(True)
    for key, chip in chips.items():
        if key not in wanted:
            chip.set_visible(False)
