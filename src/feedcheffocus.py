# SPDX-License-Identifier: GPL-3.0-or-later
"""More Like This and the focus banner of a recipe's timeline page.

A mixin of ``FeedChefTimelinePage`` (in ``feedchefpages``, which stays under
the file size limit by keeping the rest). It reads the page's ``recipe``,
``storage``, ``model``, ``source`` and ``banner``, and calls its ``_cook()``.
"""

from . import feedback
from .feedchef.textutil import extract_focus
from .i18n import gettext as _, ngettext

# Terms the focus banner names before "and N more".
BANNER_TERMS = 3


class FocusMixin:
    """The focus half of ``FeedChefTimelinePage``."""

    def _shown_statuses(self):
        found = []
        for position in range(self.model.get_n_items()):
            status = getattr(self.model.get_item(position), 'status', None)
            if isinstance(status, dict):
                found.append(status)
        return found

    def _more_like_this(self, status):
        tags, keywords = extract_focus(status, self._shown_statuses())
        if not tags and not keywords:
            feedback.show_toast(self, _('Nothing to focus on in this post'))
            return
        kitchen = self.recipe.kitchen
        kitchen.active_focus_hashtags = tags or None
        kitchen.active_focus_keywords = keywords or None
        kitchen.active_focus_weights = None
        kitchen.active_focus_strength = 1.0
        self._save_focus()
        feedback.show_toast(self, _('Focus set from this post'))

    def _clear_focus(self, *_args):
        kitchen = self.recipe.kitchen
        kitchen.active_focus_hashtags = None
        kitchen.active_focus_keywords = None
        kitchen.active_focus_weights = None
        kitchen.active_focus_strength = 1.0
        self._save_focus()

    def _save_focus(self):
        try:
            self.storage.save_recipe(self.recipe)
        except Exception as error:
            feedback.report_error(self, _('Could not save the recipe'),
                                  error)
            return
        self._update_focus_ui()
        self._cook()

    def _update_focus_ui(self):
        kitchen = self.recipe.kitchen
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
            last = getattr(getattr(self, 'source', None), 'last_audit', None)
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
