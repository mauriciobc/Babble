# SPDX-License-Identifier: GPL-3.0-or-later
"""The words a text of Feed Chef is made of: the app's gettext and ngettext
(and a way to name a language), handed in by the caller, so that no module
under ``feedchef`` imports a translation module or GTK. ``ENGLISH`` is
the default of every function that takes one."""

from __future__ import annotations

from typing import Callable, NamedTuple


class Words(NamedTuple):
    gettext: Callable[[str], str]
    ngettext: Callable[[str, str, int], str]
    # Names a language code in the reader's language, or None when it
    # cannot: the dialogs pass one (it needs the desktop), this module
    # stays free of GTK.
    language: Callable[[str], 'str | None'] | None = None


def _plural(one: str, other: str, count: int) -> str:
    return one if count == 1 else other


ENGLISH = Words(lambda text: text, _plural)
