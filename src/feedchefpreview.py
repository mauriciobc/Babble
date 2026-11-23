# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef preview: a recipe's timeline as it is right now, unsaved.

The dialog cooks a copy of the recipe the builder is editing, with an
engine of its own, in the same ``TimelinePage`` a saved recipe uses. It
has no focus banner, no edit button and no More Like This entry: it only
shows what the recipe would give, and it is read-only. A post in it cannot
be opened, and its action bar and menu cannot be used, so nothing the
reader does there reaches the window behind the dialog. Closing it stops
the engine."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw  # noqa: E402

from . import accounts  # noqa: E402
from .feedback import ToastDialog  # noqa: E402
from .feedchef.icons import icon_name  # noqa: E402
from .feedchefbridge import make_engine  # noqa: E402
from .feedchefsource import FeedChefSource  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .timelinepage import (TimelineFactory, TimelinePage,  # noqa: E402
                           filter_context_for)
from .weakconnect import connect_weak  # noqa: E402


class _ReadOnlyFactory(TimelineFactory):
    """Rows that are shown and scrolled, not used.

    A card that cannot be targeted passes every pointer event to what is
    behind it, the scrolled window, so the wheel and a touch drag still
    scroll, while a click on a post, an avatar, a link, an action or a
    menu reaches nothing. A card that cannot take focus keeps Tab and the
    keyboard from reaching them either, and the arrow keys scroll the
    window instead."""

    def on_setup(self, factory, list_item):
        TimelineFactory.on_setup(self, factory, list_item)
        card = list_item.get_child()
        card.set_can_target(False)
        card.set_can_focus(False)


class FeedChefPreviewPage(TimelinePage):
    """A TimelinePage over a FeedChefSource for one recipe, read-only."""

    __gtype_name__ = 'BabbleFeedChefPreviewPage'
    engine = None
    _shut_down = False

    def __init__(self, client, account, recipe):
        # An unsaved recipe leaves no audit record: the log of its page
        # would hold a recipe that was never kept, and fill the retention
        # of the saved ones.
        engine = make_engine(client, accounts.account_key(account),
                             str(account.get('id') or ''), recipe,
                             audit=False)
        engine.init_preload()
        source = FeedChefSource(engine, recipe)
        super().__init__(client, source=source, boosts_carousel=False,
                         autoload=False)
        self.engine = engine
        self.loading_page.set_title(_('Cooking Your Timeline'))
        self.loading_page.set_description(_('Gathering posts…'))
        self.list_view.set_factory(_ReadOnlyFactory(
            client, filter_context_for(source)))
        self.error_page.set_title(_('Could Not Load This Recipe'))
        self.empty_page.set_icon_name(icon_name(recipe.meta.icon))
        self.empty_page.set_title(_('No Matching Posts'))
        self.empty_page.set_description(
            _('Try fewer filters or a wider scope'))
        self.ensure_loaded()

    def on_row_activated(self, _view, _position):
        """A post does not open from here: the preview is read-only."""

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
        self.header.set_title_widget(Adw.WindowTitle(
            title=_('Preview'), subtitle=recipe.meta.name))
        self.timeline = FeedChefPreviewPage(client, account, recipe)
        self.set_content(self.timeline)
        connect_weak(self, 'closed', self._on_closed)

    def _on_closed(self, _dialog):
        self.timeline.shutdown()
