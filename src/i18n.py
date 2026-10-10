# SPDX-License-Identifier: GPL-3.0-or-later

"""Message lookups through one cached catalogue.

:func:`gettext.gettext` resolves the catalogue on every call: it rebuilds
the language list from the environment and probes the filesystem for each
candidate ``.mo`` file before it looks the message up. A timeline row
translates a few dozen strings each time it is built or bound, so that
probing dominated binding. The domain and the locale are fixed once
babble has started (see ``babble.in``), so the catalogue is resolved on
first use and reused from then on.
"""

import gettext as _gettext

_catalogue = None


def _translations():
    global _catalogue
    if _catalogue is None:
        domain = _gettext.textdomain()
        _catalogue = _gettext.translation(
            domain, _gettext.bindtextdomain(domain), fallback=True)
    return _catalogue


def gettext(message):
    return _translations().gettext(message)


def ngettext(singular, plural, n):
    return _translations().ngettext(singular, plural, n)
