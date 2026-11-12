# SPDX-License-Identifier: GPL-3.0-or-later

"""The requests one owner (a page) has in flight, and whether an answer
to one of them is still wanted.

A page that reloads must ignore the answer to the load it replaced, and a
page that is freed must stop what it started. Both are one question asked
when an answer arrives, and one cancel when the answer stops being wanted:
this module is that, so a page keeps no generation counter of its own.

Use, for READS only (a write cannot be undone by cancelling it: the server
may already have acted, so a write is never started through a scope)::

    scope = RequestScope()                 # one per owner; holds no page
    ticket = scope.begin()                 # a new generation: whatever was
                                           # started before is cancelled
    api.some_async(client, callback=ticket.callback(self._on_answer),
                   cancellable=ticket.cancellable)
    ...
    def _on_answer(self, client, result):  # only ever called when wanted
        data = api.some_finish(client, result)

* :meth:`RequestScope.begin` supersedes the current generation: its
  cancellable is cancelled and a fresh one is made (a cancelled cancellable
  stays cancelled, so a generation never reuses one).
* :meth:`RequestScope.start` makes a request in the CURRENT generation, for
  a follow-up of it (the next page of a list). Every ticket of one
  generation shares its cancellable, so the next ``begin`` cancels them
  together. A scope starts with a generation of its own, so ``start`` before
  any ``begin`` is valid.
* :meth:`Ticket.callback` is the callback to hand the api call. It asks
  :meth:`Ticket.wanted` FIRST, before the handler decodes anything, and
  drops the answer silently when it is not wanted. That order is built in
  rather than a rule to remember: a ``Gio.Task`` whose cancellable fired
  reports CANCELLED on propagate even when the real answer had already
  arrived, and a cancelled request still calls its callback, sometimes
  seconds later (the transport's retry back-off), so decoding such an answer
  raises.
* :meth:`Ticket.wanted` is scope state only: False when the scope was
  closed, a newer generation began, or the ticket's cancellable was
  cancelled. It never raises, and it does not look at the answer. The rule
  that follows: an answer is dropped silently only when the scope itself
  cancelled or superseded it. Any other error, a CANCELLED one included
  (one the scope did not cause), is the reader's to see.
* :meth:`RequestScope.close` cancels everything outstanding and makes every
  later answer unwanted. Closing twice is harmless. A closed scope never
  raises on ``begin`` or ``start``: it hands out a ticket that is never
  wanted and whose cancellable is already cancelled, so a request started
  by a late idle fails at once and is dropped silently. Check
  :attr:`RequestScope.closed` to not send it at all.

The scope holds an int, a flag and a ``Gio.Cancellable``: no page, widget,
window or client, and no global state. A ticket holds its scope and a
cancellable, and its callback holds the handler's owner WEAKLY (a GObject
weak reference, as ``pages.weak_method`` does, written out here because
``pages`` imports this module and a scope must stay free of widget code), so
a request in flight keeps neither the page nor the scope's owner alive."""

from gi.repository import Gio


class Ticket:
    """One request's place in a scope: the cancellable to hand the api
    call, the callback that drops an unwanted answer, and the question of
    whether the answer is still wanted."""

    __slots__ = ('_scope', '_generation', 'cancellable')

    def __init__(self, scope, generation, cancellable):
        self._scope = scope
        self._generation = generation
        self.cancellable = cancellable

    def wanted(self):
        """False when the answer must be dropped silently: the scope was
        closed, a newer generation began, or the request was cancelled."""
        scope = self._scope
        if scope._closed or scope._generation != self._generation:
            return False
        return not self.cancellable.is_cancelled()

    def callback(self, method, on_drop=None):
        """The callback for the api call: ``method(client, result)``, a
        bound method of a GObject, held weakly. It asks :meth:`wanted`
        first. When the answer is not wanted, ``on_drop(owner)`` is called
        if given (a page resetting its loading flags, say) and *method* is
        not; when the owner is already freed, nothing is. A third argument
        the transport may pass is ignored."""
        func = method.__func__
        target = method.__self__.weak_ref()

        def call(client, result, _data=None):
            owner = target()
            if owner is None:
                return
            if not self.wanted():
                if on_drop is not None:
                    on_drop(owner)
                return
            func(owner, client, result)
        return call


class RequestScope:
    """The generations of one owner's reads; see the module docstring."""

    def __init__(self):
        self._closed = False
        self._generation = 0
        self._cancellable = Gio.Cancellable()

    @property
    def closed(self):
        return self._closed

    def begin(self):
        """Start a new generation, cancelling the previous one, and return
        its first ticket."""
        if self._closed:
            return self._dead()
        self._cancellable.cancel()
        self._generation += 1
        self._cancellable = Gio.Cancellable()
        return Ticket(self, self._generation, self._cancellable)

    def start(self):
        """Return a ticket in the current generation."""
        if self._closed:
            return self._dead()
        return Ticket(self, self._generation, self._cancellable)

    def close(self):
        """Cancel what is outstanding and drop every later answer."""
        if self._closed:
            return
        self._closed = True
        self._cancellable.cancel()

    def _dead(self):
        cancellable = Gio.Cancellable()
        cancellable.cancel()
        return Ticket(self, -1, cancellable)
