# SPDX-License-Identifier: GPL-3.0-or-later
"""The base of the controllers behind Feed Chef's pages and dialogs.

A page that grows a concern of its own (the peek, the quality layer, the
focus banner, the recipe list's health badges, the builder's modes) hands
it to a controller instead of inheriting it: the controller keeps its own
state, the page keeps the controller (an attribute, so it lives and dies
with the page) and forwards only the hooks the toolkit calls.

The controller is a GObject so that ``connect_weak`` and ``weak_method``
can hold it weakly, and it holds its page by a GObject weak reference: the
only strong reference between the two runs from the page to the controller,
so no reference cycle forms and both are freed together.
"""

import functools

from gi.repository import GObject


class Gone(Exception):
    """The page (or dialog) a controller belongs to was freed."""


def guarded(method):
    """Run *method* only while the owner lives: an answer from a worker, a
    timer or a signal that lands after the owner was freed does nothing
    (and returns None)."""
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Gone:
            return None
    return call


class PageController(GObject.Object):
    """A concern of a page, owned by the page and holding it weakly."""

    def __init__(self, owner):
        super().__init__()
        self._owner = owner.weak_ref()

    @property
    def owner(self):
        """The page (or dialog) this controller belongs to. Raises ``Gone``
        once it has been freed (an answer from a worker or a signal can
        still arrive in that window); a method that reads it is decorated
        with ``guarded``, so the answer is dropped."""
        owner = self._owner()
        if owner is None:
            raise Gone()
        return owner
