# SPDX-License-Identifier: GPL-3.0-or-later

"""Derived sidebar state, kept free of GTK so it can be reasoned about alone.

The stored state is two GSettings keys, `sidebar-style` and
`sidebar-hidden`, plus the window's collapsed flag. Everything the
widgets need (whether the rail shows, whether text labels fit, how wide
the sidebar is, whether the sidebar is on screen at all) follows from
those three values here rather than being recomputed at each call site.
"""

SIDEBAR_STYLES = ('full', 'icons')


def icon_rail(style, collapsed):
    """Whether the sidebar is a narrow icon-only rail.

    A collapsed sidebar never is one: collapsed it is either absent or a
    full-width overlay, whatever style is stored."""
    return style == 'icons' and not collapsed


def sidebar_visible(hidden, collapsed, overlay_open):
    """Whether the sidebar should be on screen."""
    if collapsed:
        return overlay_open
    return not hidden


def widths(style, collapsed):
    """Preferred and maximum sidebar width in logical pixels."""
    if icon_rail(style, collapsed):
        return (64, 64)
    return (180, 280)


def label_visible(style, collapsed):
    """Whether sidebar rows show their text labels; the rail hides them."""
    return not icon_rail(style, collapsed)
