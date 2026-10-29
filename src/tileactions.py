# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared action and overflow-menu construction for status tiles."""


from . import statusactions, statusmenu, statustext
from .statusactions import WINDOW_CLIENT


def tile_action_bar(item, client=WINDOW_CLIENT, *, bar=None, standalone=True,
                    button_classes=('flat',), tinted=False, quote=True,
                    count_format=None, catchup_style=False):
    """Create or bind a status action bar for a reusable tile.

    count_format, standalone, tinted and button_classes apply only when the
    bar is created here; a bar passed in keeps the ones it was built with.
    An explicit client=None is passed through unchanged.
    """
    if bar is None:
        if count_format is None:
            count_format = (statustext.compact_count if catchup_style
                            else str)
        bar = statusactions.StatusActions(
            standalone=standalone, button_classes=button_classes,
            tinted=tinted, quote=quote, count_format=count_format)
    if item is not None:
        bar.bind(item, client)
    if catchup_style:
        bar.add_css_class('catchup-actions')
        bar.set_child_spacing(2)
        bar.set_line_spacing(0)
    return bar


def tile_menu_button(item, client=WINDOW_CLIENT, *, button=None, actions=None,
                     button_classes=('flat',)):
    """Create or bind an overflow menu associated with a status tile."""
    if button is None:
        button = statusmenu.StatusMenuButton(
            actions=actions, button_classes=button_classes)
    elif actions is not None:
        button.actions = actions
    if item is not None:
        button.bind(item, client)
    return button
