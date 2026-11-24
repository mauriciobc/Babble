# SPDX-License-Identifier: GPL-3.0-or-later
"""More Like This and the focus banner of a recipe's timeline page.

A controller of ``FeedChefTimelinePage`` (the page owns it, see
``feedchefcontroller``). It reads the page's ``recipe``, ``storage``,
``model`` and ``source``, owns the banner, and asks the page to ``cook()``.
"""

import gi

gi.require_version('Adw', '1')

from gi.repository import Adw  # noqa: E402

from . import feedback  # noqa: E402
from .feedchef.textutil import extract_focus  # noqa: E402
from .feedchefcontroller import PageController, guarded  # noqa: E402
from .i18n import gettext as _, ngettext  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402

# Terms the focus banner names before "and N more".
BANNER_TERMS = 3


class FocusController(PageController):
    """The focus half of ``FeedChefTimelinePage``."""

    def __init__(self, page):
        super().__init__(page)
        # The focus banner sits above the timeline's own stack.
        self.banner = Adw.Banner(button_label=_('Clear Focus'),
                                 use_markup=False)
        connect_weak(self.banner, 'button-clicked', self.clear)

    @guarded
    def _shown_statuses(self):
        model = self.owner.model
        found = []
        for position in range(model.get_n_items()):
            status = getattr(model.get_item(position), 'status', None)
            if isinstance(status, dict):
                found.append(status)
        return found

    @guarded
    def more_like_this(self, status):
        page = self.owner
        tags, keywords = extract_focus(status, self._shown_statuses())
        if not tags and not keywords:
            feedback.show_toast(page, _('Nothing to focus on in this post'))
            return
        kitchen = page.recipe.kitchen
        kitchen.active_focus_hashtags = tags or None
        kitchen.active_focus_keywords = keywords or None
        kitchen.active_focus_weights = None
        kitchen.active_focus_strength = 1.0
        self._save()
        feedback.show_toast(page, _('Focus set from this post'))

    @guarded
    def clear(self, *_args):
        kitchen = self.owner.recipe.kitchen
        kitchen.active_focus_hashtags = None
        kitchen.active_focus_keywords = None
        kitchen.active_focus_weights = None
        kitchen.active_focus_strength = 1.0
        self._save()

    @guarded
    def _save(self):
        page = self.owner
        try:
            page.storage.save_recipe(page.recipe)
        except Exception as error:
            feedback.report_error(page, _('Could not save the recipe'),
                                  error)
            return
        self.update_ui()
        page.cook()

    @guarded
    def update_ui(self):
        page = self.owner
        kitchen = page.recipe.kitchen
        parts = ['#%s' % tag for tag in kitchen.active_focus_hashtags or []]
        parts += list(kitchen.active_focus_keywords or [])
        if parts:
            terms = ', '.join(parts[:BANNER_TERMS])
            extra = len(parts) - BANNER_TERMS
            if extra > 0:
                # Translators: %(terms)s lists the first hashtags and words
                # the timeline is focused on, such as "#a, #b, #c"; %(count)d
                # is how many more there are.
                terms = ngettext('%(terms)s and %(count)d more',
                                 '%(terms)s and %(count)d more',
                                 extra) % {'terms': terms, 'count': extra}
            last = getattr(getattr(page, 'source', None), 'last_audit', None)
            if isinstance(last, dict) and last.get('focus_unmatched'):
                # Translators: %s lists the hashtags and words the timeline
                # is focused on; none of the posts shown matches them, so
                # the posts are in the order the recipe asks for.
                self.banner.set_title(
                    _('No post here matches %s; showing your recipe’s '
                      'order') % terms)
            else:
                # Translators: %s lists the hashtags and words the timeline
                # is focused on, for example "#python, decorators".
                self.banner.set_title(_('Showing more like %s') % terms)
        # The whole list, for when the title had to leave some out.
        self.banner.set_tooltip_text(', '.join(parts) if parts else None)
        self.banner.set_revealed(bool(parts))
