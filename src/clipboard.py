# SPDX-License-Identifier: GPL-3.0-or-later

"""The 'Open copied post?' banner.

The window watches its own clipboard while it has focus and offers to
open a single fediverse link sitting in it. Everything about whether to
offer, and what to offer, lives here: the window keeps only the banner
and where to go when it is opened.
"""

import gi

gi.require_version('Gtk', '4.0')

try:
    from .i18n import gettext as _  # noqa: E402
except ImportError:  # loaded as a top-level module by the standalone tests
    from i18n import gettext as _  # noqa: E402

from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from . import pages  # noqa: E402

# A clipboard holds more than links, and a URL someone cut out of a
# document can be short and still not what they meant. Only a single
# link, under this length, is offered.
COPIED_LINK_MAX = 2048


def copied_link_kind(text):
    """``'post'``, ``'profile'`` or ``'tag'`` when *text* (already
    stripped) is a single fediverse link, else None."""
    if not text or len(text) > COPIED_LINK_MAX or any(
            c.isspace() for c in text):
        return None
    return pages.fedi_link_kind(text)


class CopiedLinkBanner(Gtk.Box):
    """A banner that offers to open the fediverse link on the clipboard.

    It sits on the window and follows it weakly, so a closed window's
    pending clipboard read resolves to nothing.
    """

    __gtype_name__ = 'BabbleCopiedLinkBanner'

    def __init__(self, window, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self._window = window.weak_ref()
        self.banner = Adw.Banner(button_label=_('_Open'), use_markup=False)
        self.append(self.banner)
        pages.connect_weak(self.banner, 'button-clicked', self._on_opened)
        # Links already offered, opened or not: none is offered twice.
        self._offered = set()
        self._offering = None
        self._read_serial = 0
        pages.connect_weak(window, 'notify::is-active',
                           self._on_active_changed)
        pages.connect_weak(window.get_clipboard(), 'changed',
                           self._on_clipboard_changed)

    def _on_active_changed(self, *_args):
        if self._window().is_active():
            self._check()

    def _on_clipboard_changed(self, *_args):
        window = self._window()
        if window is None:
            return
        if window.is_active():
            self._check()
        elif self.banner.get_revealed():
            self._hide()

    def _hide(self):
        self._read_serial += 1
        self._offering = None
        self.banner.set_revealed(False)

    def _check(self):
        window = self._window()
        if window is None:
            return
        if not window.is_active() or window.client is None \
                or not window.settings.get_boolean('offer-copied-links'):
            self._hide()
            return
        clipboard = window.get_clipboard()
        formats = clipboard.get_formats()
        if not (formats.contain_gtype(GObject.TYPE_STRING)
                or formats.contain_mime_type('text/plain')
                or formats.contain_mime_type('text/plain;charset=utf-8')):
            self._hide()
            return
        self._read_serial += 1
        clipboard.read_text_async(None, pages.weak_method(
            self._on_text), self._read_serial)

    def _on_text(self, clipboard, result, serial):
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            text = None
        if serial != self._read_serial:
            return  # a newer read (or a hide) superseded this one
        window = self._window()
        if window is None:
            return
        if not window.is_active() or window.client is None \
                or not window.settings.get_boolean('offer-copied-links'):
            self._hide()
            return
        url = (text or '').strip()
        kind = copied_link_kind(url)
        if kind is None:
            self._hide()
            return
        if url in self._offered:
            if url != self._offering:
                self._hide()
            return  # already offered: never nag twice for the same link
        self._offered.add(url)
        self._offering = url
        self.banner.set_title({
            'post': _('Open copied post?'),
            'profile': _('Open copied profile?'),
            'tag': _('Open copied hashtag?'),
            'collection': _('Open copied collection?')}[kind])
        self.banner.set_revealed(True)

    def _on_opened(self, _banner):
        url = self._offering
        self._hide()
        window = self._window()
        if url and window is not None:
            pages.open_link(window, url, resolve=True)
