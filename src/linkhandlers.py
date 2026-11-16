# SPDX-License-Identifier: GPL-3.0-or-later

"""Who may handle a link before the app opens it.

Link routing (:mod:`linkrouting`) sits in the layer every page builds on,
while the things that can take a link over -- a plugin that claims an
origin, the web column beside the timeline -- sit above it and import
the page toolkit themselves. Routing cannot import them without closing
a cycle, so they register here instead, at import, and routing asks.

* :func:`add_claimer` registers a function ``(widget, uri) -> bool`` that
  takes a link over completely (True) or leaves it alone (False);
  :func:`claim` asks the claimers in registration order.
* :func:`set_web_opener` registers the one function
  ``(widget, uri) -> bool`` that shows a web link inside the app;
  :func:`open_in_app` calls it, False meaning there is none or it
  declined.

This module uses the standard library only and holds no widget, client or
page. What it registers lives as long as the process (a registered
function is never released), so only a module-level function is accepted,
exactly as :func:`mediapolicy.connect_changes` does for its handlers.
"""

import types

_claimers = []
_web_opener = None


def _require_function(handler, what):
    """Raise :exc:`TypeError` unless *handler* is a module-level function:
    a ``types.FunctionType`` whose ``__qualname__`` equals its
    ``__name__`` (no ``Class.`` prefix, no ``<locals>``), that is not a
    lambda, has no closure, no default argument values and an empty
    ``__dict__`` (a ``functools.wraps`` wrapper records ``__wrapped__``
    there). Anything else would pin whatever it references for the whole
    process."""
    if not (isinstance(handler, types.FunctionType)
            and handler.__qualname__ == handler.__name__
            and handler.__name__ != '<lambda>'
            and handler.__closure__ is None
            and handler.__defaults__ is None
            and handler.__kwdefaults__ is None
            and not handler.__dict__):
        raise TypeError('%s takes a module-level function, not %r'
                        % (what, handler))


def add_claimer(func):
    """Register *func(widget, uri) -> bool* as a claimer. Registering the
    same function again does nothing. Returns *func*."""
    _require_function(func, 'add_claimer')
    if func not in _claimers:
        _claimers.append(func)
    return func


def claim(widget, uri):
    """Whether a claimer took *uri* over: the registered claimers are
    asked in registration order, and the first to return true wins."""
    for func in list(_claimers):
        if func(widget, uri):
            return True
    return False


def set_web_opener(func):
    """Register *func(widget, uri) -> bool* as the one opener of web
    links inside the app, replacing any earlier one. Returns *func*."""
    global _web_opener
    _require_function(func, 'set_web_opener')
    _web_opener = func
    return func


def open_in_app(widget, uri):
    """Show *uri* inside the app through the registered opener. False
    when none is registered or it declined (WebKit missing, say), and the
    caller sends the link to the browser."""
    opener = _web_opener
    if opener is None:
        return False
    return bool(opener(widget, uri))
