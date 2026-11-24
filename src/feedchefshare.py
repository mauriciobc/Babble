# SPDX-License-Identifier: GPL-3.0-or-later
"""The Share Recipe dialog: a recipe's share link, a button that copies it,
and the link as a QR code a phone can scan.

The code is made by segno on a worker, after the dialog opens (segno is
imported only then, and only here), and drawn as a texture of one pixel per
module that is scaled without smoothing, dark modules black on white
whatever the theme. Without segno, or for a link too long for any code, the
dialog is the link and Copy Link."""

import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')

from gi.repository import (Adw, Gdk, GLib, GObject, Graphene,  # noqa: E402
                           Gsk, Gtk)

from .feedchef import deps  # noqa: E402
from .feedchef import qrcode  # noqa: E402
from .feedchef.sharecodec import attributed, handle_of  # noqa: E402
from .feedchef.sharecodec import recipe_share_uri  # noqa: E402
from .feedchef.sentence import chef_line  # noqa: E402
from .feedback import ToastDialog  # noqa: E402
from .feedchefexplain import WORDS  # noqa: E402
from .feedchefpreview import CloseWhenFeedChefOff  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .uihelpers import set_accessible_label  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402

# The largest side of the code on screen, in pixels (a whole number of
# pixels per module is used, so every module is as wide as the others).
QR_PIXELS = 232
# The fewest pixels a module is drawn with (a phone camera needs them).
MIN_MODULE = 3


class NearestPaintable(GObject.Object, Gdk.Paintable):
    """A texture drawn with the nearest-pixel filter, so that a scaled QR
    code keeps hard edges."""

    def __init__(self, texture):
        super().__init__()
        self.texture = texture

    def do_snapshot(self, snapshot, width, height):
        snapshot.append_scaled_texture(
            self.texture, Gsk.ScalingFilter.NEAREST,
            Graphene.Rect().init(0, 0, width, height))

    def do_get_intrinsic_width(self):
        return self.texture.get_width()

    def do_get_intrinsic_height(self):
        return self.texture.get_height()

    def do_get_flags(self):
        return (Gdk.PaintableFlags.STATIC_SIZE
                | Gdk.PaintableFlags.STATIC_CONTENTS)


def qr_texture(side, pixels):
    """The texture of a code of *side* by *side* modules (see
    ``qrcode.render``): one grey byte per module."""
    return Gdk.MemoryTexture.new(side, side, Gdk.MemoryFormat.G8,
                                 GLib.Bytes.new(pixels), side)


class FeedChefShareDialog(CloseWhenFeedChefOff, ToastDialog):
    """Share Recipe: the link, Copy Link and the QR code of a recipe.
    The recipe is shared as it is, with the sharer as its chef when it has
    none."""

    __gtype_name__ = 'BabbleFeedChefShareDialog'

    def __init__(self, recipe, account):
        super().__init__(_('Share Recipe'), width=440, height=620)
        self.link = recipe_share_uri(recipe, author=handle_of(account))
        shared = attributed(recipe, handle_of(account))
        self.qr_picture = None
        # The pixels the code was drawn from (one byte a module), for a test.
        self.qr_pixels = None
        self._closed = False

        self.qr_group = Adw.PreferencesGroup()
        self.qr_stack = Gtk.Stack(halign=Gtk.Align.CENTER,
                                  margin_top=6, margin_bottom=6)
        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER,
                              valign=Gtk.Align.CENTER)
        spinner.set_size_request(48, 48)
        self.qr_stack.add_named(spinner, 'wait')
        self.qr_group.add(self.qr_stack)
        self.page.add(self.qr_group)

        # Markup is parsed in a group's description: the chef is a stranger's
        # text.
        group = Adw.PreferencesGroup(description=GLib.markup_escape_text(
            ' '.join(part for part in (
                _('Scan the code or copy the link to share this recipe.'),
                chef_line(shared, WORDS)) if part)))
        self.link_row = Adw.ActionRow(
            title=_('Share Link'), subtitle=GLib.markup_escape_text(self.link),
            subtitle_lines=1, subtitle_selectable=True)
        self.too_large_row = Adw.ActionRow(
            title=_('This recipe is too large for a QR code. Copy the link '
                    'instead.'), use_markup=False, title_lines=0,
            visible=False)
        group.add(self.too_large_row)
        group.add(self.link_row)
        self.copy_row = Adw.ButtonRow(
            title=_('_Copy Link'), use_underline=True,
            start_icon_name='edit-copy-symbolic')
        connect_weak(self.copy_row, 'activated', self._on_copy)
        group.add(self.copy_row)
        self.page.add(group)
        self.watch_feed_chef_key()
        connect_weak(self, 'closed', self._on_closed)
        self._start_qr()

    def _start_qr(self):
        """Make the code on a worker (segno is loaded there); a package
        that is not installed shows the dialog without one."""
        if deps.unavailable_reason('segno'):
            self.qr_group.set_visible(False)
            return
        ref = self.weak_ref()
        link = self.link

        def work():
            try:
                found = qrcode.render(link)
                error = None
            except Exception as failure:
                found, error = (0, b''), failure
            GLib.idle_add(_arrived, ref, found, error)

        threading.Thread(target=work, name='feedchef-qr',
                         daemon=True).start()

    def show_qr(self, side, pixels):
        """Show the code (on the main thread)."""
        picture = Gtk.Picture.new_for_paintable(
            NearestPaintable(qr_texture(side, pixels)))
        size = side * max(MIN_MODULE, QR_PIXELS // side)
        picture.set_size_request(size, size)
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        set_accessible_label(
            picture, _('QR code of the recipe’s share link'))
        self.qr_picture = picture
        self.qr_pixels = (side, pixels)
        self.qr_stack.add_named(picture, 'qr')
        self.qr_stack.set_visible_child_name('qr')

    def hide_qr(self, too_large=False):
        """No code: the link and Copy Link stay. A link too long for a code
        says so."""
        self.qr_group.set_visible(False)
        self.too_large_row.set_visible(too_large)

    def _on_copy(self, _row):
        display = self.get_display()
        if display is not None:
            display.get_clipboard().set(self.link)
        self.toast(_('Share link copied'))

    def _on_closed(self, _dialog):
        self._closed = True


def _arrived(ref, found, error):
    dialog = ref()
    if dialog is not None and not dialog._closed:
        if error is None:
            dialog.show_qr(*found)
        else:
            dialog.hide_qr(too_large=getattr(error, 'too_large', False))
    return GLib.SOURCE_REMOVE
