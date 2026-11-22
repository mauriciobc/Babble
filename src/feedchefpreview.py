# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef recipe preview — own engine per sheet (mashdon PreviewSheet)."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk  # noqa: E402

from . import accounts, feedback  # noqa: E402
from .feedchef.textutil import account_of, content_of, strip_html  # noqa: E402
from .feedchefbridge import make_engine  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402


def _preview_line(status):
    content = content_of(status)
    account = account_of(status, wrapper=False)
    name = (account.get('display_name') or account.get('username')
            or _('Unknown'))
    handle = account.get('acct') or account.get('username') or ''
    text = ' '.join(strip_html(content.get('content') or '').split())
    if len(text) > 160:
        text = text[:157] + '…'
    title = '%s @%s' % (name, handle) if handle else name
    return title, text


class FeedChefPreviewDialog(feedback.ToastDialog):
    """Cook a recipe into a short list without saving — own ``FeedEngine``."""

    __gtype_name__ = 'BabbleFeedChefPreviewDialog'

    def __init__(self, client, account, recipe):
        super().__init__(_('Recipe preview'), width=520, height=640)
        self.client = client
        self.account = account
        self.recipe = recipe
        self.engine = make_engine(
            client, accounts.account_key(account),
            str(account.get('id') or ''), recipe)
        self.engine.init_preload()
        self._generation = None

        close_btn = Gtk.Button(label=_('Close'))
        close_btn.add_css_class('flat')
        connect_weak(close_btn, 'clicked', self._on_close)
        self.header.pack_start(close_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        self.subtitle = Gtk.Label(
            label=_('Cooking your feed… (Pantry → Diet → Kitchen)'),
            wrap=True, xalign=0.0)
        self.subtitle.add_css_class('dim-label')
        box.append(self.subtitle)

        self.stack = Gtk.Stack()
        self.spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER,
                                  valign=Gtk.Align.CENTER, hexpand=True,
                                  vexpand=True)
        self.stack.add_named(self.spinner, 'loading')

        scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class('boxed-list')
        scrolled.set_child(self.list)
        self.stack.add_named(scrolled, 'results')

        empty = Adw.StatusPage(
            icon_name='system-search-symbolic',
            title=_('No posts found'),
            description=_('Nothing matched this recipe right now.'))
        self.stack.add_named(empty, 'empty')
        box.append(self.stack)
        self.set_content(box)

        connect_weak(self, 'closed', self._on_closed)
        self._cook()

    def _cook(self):
        self.stack.set_visible_child_name('loading')
        self.spinner.start()
        self.subtitle.set_label(
            _('Cooking your feed… (Pantry → Diet → Kitchen)'))
        self._generation = self.engine.process_feed(
            self.recipe,
            max_id=None,
            count=40,
            on_stage=self._on_stage,
            on_done=self._on_done,
            on_error=self._on_error,
        )

    def _on_stage(self, stage, _items):
        self.subtitle.set_label(_('Cooking… Stage: %s') % stage)

    def _on_done(self, result):
        self.spinner.stop()
        while child := self.list.get_first_child():
            self.list.remove(child)
        items = result.items or []
        if not items:
            self.stack.set_visible_child_name('empty')
            self.subtitle.set_label(
                _('No posts found matching your recipe.'))
            return
        self.stack.set_visible_child_name('results')
        self.subtitle.set_label(
            _('Found %d posts served hot!') % len(items))
        for status in items:
            title, body = _preview_line(status)
            row = Adw.ActionRow(title=title, subtitle=body)
            self.list.append(row)

    def _on_error(self, error):
        self.spinner.stop()
        self.stack.set_visible_child_name('empty')
        self.subtitle.set_label(_('Could not cook this recipe'))
        self.error(_('Feed Chef'), error)

    def _on_close(self, *_args):
        self.close()

    def _on_closed(self, *_args):
        self.engine.cancel()
        self.engine.shutdown()
