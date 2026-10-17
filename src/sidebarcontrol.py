# SPDX-License-Identifier: GPL-3.0-or-later
"""The header controls the window shows while the sidebar is out of the
way: reveal/mode, the primary menu, compose, and optionally the
notifications bell.

The control owns no state and connects no signals. The window exposes
``sidebar-visible``, ``sidebar-collapsed``, ``unread-text`` and
``unread-visible``; property bindings mirror them onto the widget, so a
mode change, a collapse or an unread count needs no callback here. The
widget is only on screen when the sidebar is hidden, and the actions
that do not fit a 64 px rail are only shown while the sidebar is
expanded.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import GObject, Gtk  # noqa: E402

_SYNC = GObject.BindingFlags.SYNC_CREATE
_INVERTED = _SYNC | GObject.BindingFlags.INVERT_BOOLEAN


class SidebarControl(Gtk.Box):
    """Header controls for a hidden sidebar.

    ``mode_menu`` and ``primary_menu`` are menu models owned by the
    window; ``include_bell`` is false in views where the notifications
    bell would duplicate an existing control. The bell and its badge are
    not created at all in that case.
    """

    __gtype_name__ = 'BabbleSidebarControl'

    def __init__(self, window, mode_menu, primary_menu,
                 include_bell=True, **kwargs):
        kwargs.setdefault('orientation', Gtk.Orientation.HORIZONTAL)
        kwargs.setdefault('spacing', 6)
        kwargs.setdefault('valign', Gtk.Align.CENTER)
        super().__init__(**kwargs)
        self.add_css_class('sidebar-control')

        self.mode_button = Gtk.MenuButton(
            icon_name='sidebar-show-symbolic',
            always_show_arrow=True,
            tooltip_text=_('Sidebar'),
            menu_model=mode_menu)
        self.mode_button.add_css_class('flat')
        self.mode_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Sidebar')])
        self.append(self.mode_button)

        self.primary_button = Gtk.MenuButton(
            icon_name='open-menu-symbolic',
            primary=True,
            tooltip_text=_('Main Menu'),
            menu_model=primary_menu)
        self.primary_button.add_css_class('flat')
        self.primary_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Main Menu')])
        self.append(self.primary_button)

        self.compose_button = Gtk.Button(
            icon_name='document-edit-symbolic',
            action_name='win.compose',
            tooltip_text=_('New Post'))
        self.compose_button.add_css_class('flat')
        self.compose_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('New Post')])
        self.append(self.compose_button)

        self.bell_button = None
        self.badge = None
        self._bell_host = None
        if include_bell:
            self.bell_button = Gtk.Button(
                icon_name='babble-notifications-symbolic',
                action_name='win.show-notifications',
                tooltip_text=_('Notifications'))
            self.bell_button.add_css_class('flat')
            self.bell_button.update_property(
                [Gtk.AccessibleProperty.LABEL], [_('Notifications')])
            self.badge = Gtk.Label()
            self.badge.add_css_class('notifications-badge')
            self.badge.add_css_class('caption-heading')
            self.badge.add_css_class('numeric')
            self.badge.set_halign(Gtk.Align.END)
            self.badge.set_valign(Gtk.Align.START)
            self.badge.set_margin_top(2)
            self.badge.set_margin_end(2)
            # The badge is decoration over the button, which already
            # carries the accessible label and the action.
            self.badge.set_can_target(False)
            self.badge.set_visible(False)
            self._bell_host = Gtk.Overlay(child=self.bell_button)
            self._bell_host.add_overlay(self.badge)
            self.append(self._bell_host)

        # The widget is the inverse of the sidebar: visible exactly
        # while the sidebar is hidden (or the overlay is closed on a
        # collapsed window).
        window.bind_property('sidebar-visible', self, 'visible', _INVERTED)

        # Compose and notifications only fit the expanded layout; in
        # icon-rail mode they live in the rail instead.
        window.bind_property('sidebar-collapsed', self.compose_button,
                             'visible', _INVERTED)
        if self.bell_button is not None:
            # Hide the overlay host as well: the badge is an overlay
            # child and would otherwise keep requesting its own size.
            window.bind_property('sidebar-collapsed', self._bell_host,
                                 'visible', _INVERTED)
            window.bind_property('sidebar-collapsed', self.bell_button,
                                 'visible', _INVERTED)
            window.bind_property('unread-text', self.badge, 'label', _SYNC)
            window.bind_property('unread-visible', self.badge,
                                 'visible', _SYNC)
