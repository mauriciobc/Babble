# SPDX-License-Identifier: GPL-3.0-or-later
"""Make a thumbnail anywhere in the app open the media viewer.

The timeline's :class:`mediabox.MediaBox` does this for its own tiles.
Notification rows and Catch Up draw their thumbnails themselves, to fit
their own layout, so they share this mixin instead: a click, or Enter or
Space with the thumbnail focused, calls ``present_media`` at that
attachment. The click is claimed, otherwise the list's single-click
activation opens the thread of the containing row as well.
"""

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, Gtk  # noqa: E402

from .i18n import gettext as _  # noqa: E402
from .pages import connect_weak  # noqa: E402


def processing_badge():
    """The label drawn over a tile whose media has no picture yet."""
    badge = Gtk.Label(label=_('Processing…'), halign=Gtk.Align.CENTER,
                      valign=Gtk.Align.CENTER,
                      accessible_role=Gtk.AccessibleRole.PRESENTATION)
    badge.add_css_class('osd')
    badge.add_css_class('caption-heading')
    return badge


class MediaOpener:
    """Mixin for a widget that owns thumbnails. It calls
    :meth:`set_media_source` whenever it shows a post's media."""

    _media_list = ()
    _media_post_url = None
    _media_covered = False

    def set_media_source(self, item, covered, attachments=None):
        """What the viewer pages through (the media of *item*, a
        StatusItem, in full unless *attachments* says otherwise), the
        post's address for Open in Browser, and whether the media is
        covered (blurred or sensitive), which keeps it from opening."""
        self._media_list = item.media if attachments is None \
            else attachments
        self._media_post_url = item.status.get('url')
        self._media_covered = covered

    def make_media_openable(self, widget, index, label):
        """Let *widget* open attachment *index*. It becomes a focusable
        button named *label*; create it with ``accessible_role=BUTTON``."""
        widget.set_focusable(True)
        widget.add_css_class('media-tile')
        widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        connect_weak(click, 'released', self._on_media_released, index)
        widget.add_controller(click)
        keys = Gtk.EventControllerKey()
        connect_weak(keys, 'key-pressed', self._on_media_key, index)
        widget.add_controller(keys)
        widget.set_cursor_from_name('pointer')

    def wrap_thumbnail(self, picture, index, attachment, url):
        """*picture* in an overlay that is a button for *attachment*
        (unless the media is covered) and says "Processing…" while the
        server has given the attachment no *url* to draw."""
        overlay = Gtk.Overlay(
            child=picture, hexpand=True, accessible_role=(
                Gtk.AccessibleRole.GENERIC if self._media_covered
                else Gtk.AccessibleRole.BUTTON))
        if not url:
            overlay.add_overlay(processing_badge())
        if not self._media_covered:
            self.make_media_openable(
                overlay, index,
                attachment.get('description') or _('Attachment'))
        return overlay

    def open_media(self, index):
        """Open the viewer at *index*; False when the media is covered
        or *index* is not there, so the caller's own click still works."""
        if self._media_covered or not 0 <= index < len(self._media_list):
            return False
        from .mediaviewer import present_media
        present_media(self, self._media_list, index,
                      status_url=self._media_post_url)
        return True

    def _on_media_released(self, gesture, _n_press, _x, _y, index):
        if self.open_media(index):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_media_key(self, _controller, keyval, _keycode, _state, index):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            return self.open_media(index)
        return False
