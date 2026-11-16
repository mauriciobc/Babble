# SPDX-License-Identifier: GPL-3.0-or-later

"""Who may handle a link before the app opens it.

Link routing (:mod:`linkrouting`) sits in the layer every page builds on,
while the things that can take a link over -- a plugin that claims an
origin, the web column beside the timeline -- sit above it and import
the page toolkit themselves. Routing cannot import them without closing
a cycle, so they register here instead, at import, and routing asks.
The application's composition root (``main.py``) imports both, which is
what fills this registry.

* :func:`add_claimer` registers a function ``(widget, uri) -> bool`` that
  takes a link over completely (True) or leaves it alone (False);
  :func:`claim` asks the claimers in registration order.
* :func:`set_web_opener` registers the one function
  ``(widget, uri) -> bool`` that shows a web link inside the app;
  :func:`open_in_app` calls it, False meaning there is none or it
  declined.

A handler that raises must not take link routing down with it: a failing
claimer is logged and skipped, and a failing opener is logged and counts
as declining, so the link goes to the browser.

This module uses the standard library only and holds no widget, client or
page. What it registers lives as long as the process (a registered
function is never released), so only a module-level function is accepted,
by the same predicate as ``mediapolicy.connect_changes`` (which also
accepts a GObject's bound method; this module does not).
"""

import logging
import types

_log = logging.getLogger(__name__)

_claimers = []
_web_opener = None


def _require_function(handler, what):
    """Raise :exc:`TypeError` unless *handler* is a module-level function:
    a ``types.FunctionType`` whose ``__qualname__`` equals its
    ``__name__`` (no ``Class.`` prefix, no ``<locals>``), that is not a
    lambda, has no closure, no default argument values and an empty
    ``__dict__`` (a ``functools.wraps`` wrapper records ``__wrapped__``
    there). Anything else would pin whatever it references for the whole
    process.

    The same predicate is written out in ``mediapolicy.connect_changes``;
    it is duplicated, not imported, because ``module boundary`` (R10)
    keeps this module standard-library-only. Change one and change the
    other."""
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
    same function again does nothing. Returns *func*.

    A claimer must honour the reader's preferences itself. ``open_web``
    asks the claimers before it reads ``open-links-in-app``, so a claimer
    that ignores that setting would take a link the reader chose to open
    in the browser; ``pluginlinks.claim_link`` is the example, and it
    returns False when the setting is off."""
    _require_function(func, 'add_claimer')
    if func not in _claimers:
        _claimers.append(func)
    return func


def claim(widget, uri):
    """Whether a claimer took *uri* over: the registered claimers are
    asked in registration order, and the first to return true wins. A
    claimer that raises is logged and skipped; the next one is asked."""
    for func in list(_claimers):
        try:
            if func(widget, uri):
                return True
        except Exception:
            _log.exception('link claimer %s.%s failed; skipped',
                           func.__module__, func.__name__)
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
    when none is registered, it declined (WebKit missing, say) or it
    raised (logged), and the caller sends the link to the browser."""
    opener = _web_opener
    if opener is None:
        return False
    try:
        return bool(opener(widget, uri))
    except Exception:
        _log.exception('web opener %s.%s failed; using the browser',
                       opener.__module__, opener.__name__)
        return False
