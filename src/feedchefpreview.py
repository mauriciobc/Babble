# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef preview: a recipe's timeline as it is right now, unsaved.

The dialog cooks a copy of the recipe the builder is editing, with an
engine of its own, in the same ``TimelinePage`` a saved recipe uses. It
has no focus banner, no edit button and no More Like This entry: it only
shows what the recipe would give. Closing it stops the engine."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from . import accounts  # noqa: E402
from .feedback import ToastDialog  # noqa: E402
from .feedchef.icons import DEFAULT_ICON  # noqa: E402
from .feedchefbridge import make_engine  # noqa: E402
from .feedchefsource import FeedChefSource  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .timelinepage import TimelinePage  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402


class FeedChefPreviewPage(TimelinePage):
    """A TimelinePage over a FeedChefSource for one recipe, read-only."""

    __gtype_name__ = 'BabbleFeedChefPreviewPage'
    engine = None
    _shut_down = False

    def __init__(self, client, account, recipe):
        engine = make_engine(client, accounts.account_key(account),
                             str(account.get('id') or ''), recipe)
        engine.init_preload()
        source = FeedChefSource(engine, recipe)
        super().__init__(client, source=source, boosts_carousel=False,
                         autoload=False)
        self.engine = engine
        self.loading_page.set_title(_('Cooking Your Timeline'))
        self.loading_page.set_description(_('Gathering posts…'))
        self.error_page.set_title(_('Could Not Load This Recipe'))
        self.empty_page.set_icon_name(DEFAULT_ICON)
        self.empty_page.set_title(_('No Matching Posts'))
        self.empty_page.set_description(
            _('Try fewer filters or a wider scope'))
        self.ensure_loaded()

    def shutdown(self):
        """Stop cooking. The scope first: it cancels the request this page
        waits on, which the page then drops without showing an error."""
        if self._shut_down:
            return
        self._shut_down = True
        self._close_paging_scope()
        self.source.close()
        self.engine.shutdown()

    def do_dispose(self):
        if self.engine is not None:
            self.shutdown()
        TimelinePage.do_dispose(self)


class FeedChefPreviewDialog(ToastDialog):
    """Shows what an unsaved recipe cooks. The caller hands over a copy:
    the dialog never changes it."""

    __gtype_name__ = 'BabbleFeedChefPreviewDialog'

    def __init__(self, client, account, recipe):
        super().__init__(_('Preview'), width=520, height=720)
        self.timeline = FeedChefPreviewPage(client, account, recipe)
        self.set_content(self.timeline)
        connect_weak(self, 'closed', self._on_closed)

    def _on_closed(self, _dialog):
        self.timeline.shutdown()
