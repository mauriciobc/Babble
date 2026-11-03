# SPDX-License-Identifier: GPL-3.0-or-later

"""Links a plugin claims: :class:`plugin.LinkExtension` as the rest of
the app sees it.

A link is claimed only when its origin is one the plugin names (read on
every click, so a settings change takes effect), the first plugin to
claim it wins, and ``claims()`` can only narrow that, never widen it.
The origin of the signed-in account's instance is never claimed,
whatever a plugin says.
:func:`claim_link` also steps aside when the user chose to open links in
the browser; :func:`is_claimed` does not, since it answers a different
question (does a plugin own this origin, for a click inside its panel).

Nothing here imports libpeas or starts the host: with no plugin enabled
both functions are a lookup and a return.
"""

from .pluginpanel import origin_of


_opening = set()  # urls a plugin is opening now
_FAILED = object()  # what guard() gives back when open() raised


def _origins(context, extension):
    """The origins *extension* claims, normalised, or None when it
    raised (the plugin is then turned off)."""
    def read():
        return {origin_of(str(origin)) for origin in extension.origins()}
    found = context.guard(read, default=None)
    return None if found is None else found - {''}


def _own_origin():
    """The origin of the signed-in account's instance, which no plugin
    may claim, or '' with nobody signed in. Read on each call: an
    account switch moves it."""
    from . import accounts, pages
    account = accounts.active_account(pages.settings())
    return origin_of(account.get('instance')) if account else ''


def _claimer(url):
    """``(context, extension)`` of the first enabled plugin claiming
    *url*, or None."""
    from . import pluginhost
    host = pluginhost._host
    if host is None or not host.link_extensions:
        return None
    origin = origin_of(url)
    if not origin or origin == _own_origin():
        return None
    for plugin_id, extensions in list(host.link_extensions.items()):
        context = host.contexts.get(plugin_id)
        for extension in extensions:
            if context is None or not context.active:
                break
            if origin not in (_origins(context, extension) or ()):
                continue
            if context.guard(extension.claims, url, default=False):
                return context, extension
    return None


def is_claimed(url):
    """Whether an enabled plugin claims *url*, whatever the user chose
    for opening links."""
    return _claimer(url) is not None


def claim_link(widget, url):
    """Hand *url* to the plugin that claims it and return True, or
    return False and leave it alone: nobody claims it, the user opens
    links in the browser, or the plugin's open() raised."""
    from . import pages, pluginhost
    host = pluginhost._host
    if host is None or not host.link_extensions:
        return False
    if not pages.settings().get_boolean('open-links-in-app'):
        return False
    if url in _opening:
        return False  # its open() handed the link back: do not loop
    found = _claimer(url)
    if found is None:
        return False
    context, extension = found
    _opening.add(url)
    try:
        result = context.guard(extension.open, url, widget, widget=widget,
                               default=_FAILED)
    finally:
        _opening.discard(url)
    # A plugin that raised is turned off; the click still opens as it
    # would have, instead of being lost with it.
    return result is not _FAILED
