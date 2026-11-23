# SPDX-License-Identifier: GPL-3.0-or-later
"""Undo of Relax Filters, for the recipe page (kept apart from it to keep
that module small)."""

import gi

gi.require_version('Gtk', '4.0')

from . import feedback  # noqa: E402
from .feedchef import relax  # noqa: E402
from .feedchefexplain import WORDS  # noqa: E402


def undo_relax(ref, anchor, storage, account, before, rule):
    """Turn the rule that Relax Filters switched off back on, in the recipe
    as it is stored now (anything else edited since stays), and cook it again
    if its page (weak reference *ref*) is still there. A recipe that was
    deleted meanwhile stays deleted and the reader is told; the page being
    gone is no reason to do nothing else. *anchor* is a widget to toast on."""
    from .feedchefpages import recipe_stored
    current = storage.get_recipe_by_id(before.id)
    if current is None:
        feedback.show_toast(anchor, WORDS.gettext(
            'The recipe no longer exists, so nothing was restored'))
        return
    relax.restore(current, before, rule)
    try:
        storage.save_recipe(current)
    except Exception as error:
        feedback.report_error(anchor, WORDS.gettext(
            'Could not save the recipe'), error)
        return
    page = ref()
    if page is not None and not page._scope.closed:
        page.use_recipe(current)
    recipe_stored(anchor, account, current, skip=page)
