# SPDX-License-Identifier: GPL-3.0-or-later

"""The icon-only rail a collapsed sidebar shows.

The rail carries the account button and the composer at the top and
one 36px icon per sidebar section below. The sidebar-mode menu is the
window's own bottom-bar button, shared with the full sidebar. It knows
no page: it reports the tag of an activated row through the
``activated`` signal, and whoever built the sections maps that tag to
a page.
"""

import gi

gi.require_version('Gtk', '4.0')

from .i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import GObject, Gtk  # noqa: E402

from . import pages  # noqa: E402

_ROW_HEIGHT = 36
_ICON_SIZE = 16


def detach(widget):
    """Take *widget* out of whatever container holds it, so it can be
    adopted by another. The one detach path for the account button."""
    parent = widget.get_parent()
    if parent is None:
        return
    if hasattr(parent, 'remove'):
        parent.remove(widget)
    elif hasattr(parent, 'set_child'):
        parent.set_child(None)
    else:
        widget.unparent()


class SidebarRail(Gtk.Box):
    """64px icon rail: account, compose, one row per section.

    :meth:`set_sections` is the only source of rows, so a page that
    changes its sidebar layout can rebuild the rail in place without
    replacing the widget the split view holds.
    """

    __gtype_name__ = 'BabbleSidebarRail'
    __gsignals__ = {
        'activated': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         margin_top=6, margin_bottom=6,
                         margin_start=4, margin_end=4, **kwargs)
        self.set_hexpand(True)
        self.add_css_class('sidebar-rail')
        self._rows = {}
        self._badge = None
        self._badge_row = None
        self._badge_count = 0

        self.account_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                    halign=Gtk.Align.CENTER)
        self.append(self.account_host)

        self.compose_button = pages.icon_button('document-edit-symbolic',
                                                _('New Post'))
        self.compose_button.set_action_name('win.compose')
        self.append(self.compose_button)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE,
                                   valign=Gtk.Align.START)
        self.listbox.add_css_class('navigation-sidebar')
        self.listbox.update_property([Gtk.AccessibleProperty.LABEL],
                                     [_('Sidebar')])
        pages.connect_weak(self.listbox, 'row-activated',
                           self._on_row_activated)
        scrolled = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            child=self.listbox)
        self.append(scrolled)

    # Sections

    def set_sections(self, sections):
        """Replace the rows with one row per ``(tag, icon, title)``
        entry, separated by a line between non-empty sections."""
        while (child := self.listbox.get_first_child()) is not None:
            self.listbox.remove(child)
        self._rows = {}
        self._badge = None
        self._badge_row = None
        first = True
        for section in sections:
            if not section:
                continue
            if not first:
                self.listbox.append(self._make_separator())
            first = False
            for tag, icon, title in section:
                row = self._make_row(tag, icon, title)
                self._rows[tag] = row
                self.listbox.append(row)

    def _make_separator(self):
        line = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL,
                             margin_top=4, margin_bottom=4,
                             margin_start=8, margin_end=8)
        row = Gtk.ListBoxRow(child=line, selectable=False,
                             activatable=False)
        # A separator is not a 36px place row; without its own class the
        # rail's row height and selection styling would turn it into a
        # grey block.
        row.add_css_class('sidebar-separator')
        return row

    def _make_row(self, tag, icon, title):
        image = Gtk.Image(icon_name=icon, pixel_size=_ICON_SIZE,
                          halign=Gtk.Align.CENTER,
                          valign=Gtk.Align.CENTER,
                          accessible_role=Gtk.AccessibleRole.PRESENTATION)
        row = Gtk.ListBoxRow(height_request=_ROW_HEIGHT)
        row.tag = tag
        row.title = title
        if tag == 'notifications':
            badge = Gtk.Label(visible=False, can_target=False,
                              accessible_role=Gtk.AccessibleRole.PRESENTATION)
            badge.add_css_class('notifications-badge')
            badge.add_css_class('caption-heading')
            badge.add_css_class('numeric')
            badge.set_halign(Gtk.Align.END)
            badge.set_valign(Gtk.Align.START)
            badge.set_margin_top(2)
            badge.set_margin_end(2)
            overlay = Gtk.Overlay(child=image)
            overlay.add_overlay(badge)
            row.set_child(overlay)
            self._badge = badge
            self._badge_row = row
            if self._badge_count > 0:
                badge.set_text(str(self._badge_count))
                badge.set_visible(True)
        else:
            row.set_child(image)
        row.set_tooltip_text(title)
        self._set_row_label(row)
        return row

    def _set_row_label(self, row):
        title = row.title
        if row is self._badge_row and self._badge_count > 0:
            # Translators: accessible label of the Notifications rail
            # button; %d is the number of unread notifications.
            title = ngettext('Notifications, %d unread',
                             'Notifications, %d unread',
                             self._badge_count) % self._badge_count
        row.update_property([Gtk.AccessibleProperty.LABEL], [title])

    def _on_row_activated(self, _listbox, row):
        tag = getattr(row, 'tag', None)
        if tag is not None:
            self.emit('activated', tag)

    def set_selected(self, tag):
        """Highlight *tag*'s row without activating it."""
        row = self._rows.get(tag)
        if row is not None:
            self.listbox.select_row(row)

    def set_title(self, tag, title):
        """Rename *tag*'s row; the label is plain text."""
        row = self._rows.get(tag)
        if row is None:
            return
        row.title = title
        row.set_tooltip_text(title)
        self._set_row_label(row)

    def set_badge(self, count):
        """Show *count* on the notifications row, and read it out."""
        self._badge_count = count
        if self._badge is not None:
            self._badge.set_text('' if count <= 0 else str(count))
            self._badge.set_visible(count > 0)
        if self._badge_row is not None:
            self._set_row_label(self._badge_row)

    def set_account_button(self, widget):
        """Put *widget* above the composer, detaching it first when it
        already has a parent."""
        detach(widget)
        self.account_host.append(widget)
